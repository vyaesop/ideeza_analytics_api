"""
Pre-calculate Analytics Command

Usage:
    python manage.py precalculate_stats           # All data
    python manage.py precalculate_stats --days=7  # Last 7 days

Scheduled in production via cron:
    0 1 * * * python manage.py precalculate_stats --days=1
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.conf import settings
from django.core.cache import cache

from analytics.models import BlogView, DailyAnalyticsSummary
import time
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Pre-calculate daily analytics summaries"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=None, help="Days to calculate (default: all)"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Don't persist changes, just report what would change",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignore an existing running lock and proceed",
        )

    def handle(self, *args, **options):
        self.stdout.write("Starting pre-calculation...")
        start_time = time.perf_counter()

        # Acquire a simple cache-backed lock to prevent concurrent runs.
        lock_key = "precalculate_stats_lock"
        lock_acquired = False
        advisory_lock = False
        if not options.get("force"):
            try:
                lock_acquired = cache.add(lock_key, "1", timeout=60 * 60)
            except Exception:
                # If cache is not available, we'll attempt a DB advisory lock later
                logger.warning("Cache unavailable for precalc lock; will try advisory lock fallback")
                lock_acquired = False
        else:
            # Forced run; don't attempt to acquire lock
            lock_acquired = True

        if not lock_acquired:
            # Try Postgres advisory lock as a fallback when cache isn't usable
            try:
                from django.db import connection

                engine = connection.settings_dict.get("ENGINE", "")
                if "postgresql" in engine:
                    with connection.cursor() as cur:
                        # Use a fixed bigint as the advisory lock key
                        cur.execute("SELECT pg_try_advisory_lock(1234567890)")
                        res = cur.fetchone()
                        if res and res[0]:
                            advisory_lock = True
                            lock_acquired = True
                else:
                    lock_acquired = False
            except Exception:
                logger.exception("Advisory lock attempt failed; not acquiring lock")
                lock_acquired = False

        if not lock_acquired:
            # If we couldn't acquire a cache lock or an advisory lock, allow
            # the run to proceed in local/dev environments but log a clear
            # warning. This makes the management command usable without a
            # cache/redis or Postgres in small setups (per README).
            self.stdout.write(
                self.style.WARNING(
                    "No lock backend available (cache/advisory). Proceeding anyway; ensure only one precalc runs at a time."
                )
            )

        # Determine date range
        days = options.get("days")
        if days:
            start_date = timezone.now().date() - timedelta(days=days)
            self.stdout.write(f"  Range: last {days} days")
        else:
            earliest = BlogView.objects.order_by("timestamp").first()
            if not earliest:
                self.stdout.write(self.style.WARNING("No data found."))
                return
            start_date = earliest.timestamp.date()
            self.stdout.write(f"  Range: {start_date} to today")

        # Aggregate by day + country + author
        aggregated = (
            BlogView.objects.filter(timestamp__date__gte=start_date)
            .annotate(view_date=TruncDate("timestamp"))
            .values("view_date", "country", "blog__author")
            .annotate(
                total_views=Count("id"), unique_blogs=Count("blog", distinct=True)
            )
            .order_by("view_date")
        )

        # Build per-day set of blog ids for each group (view_date,country,author)
        # so we can store deduplicated blog ids per day in the summary.
        blogid_rows = (
            BlogView.objects.filter(timestamp__date__gte=start_date)
            .annotate(view_date=TruncDate("timestamp"))
            .values("view_date", "country", "blog__author", "blog")
            .distinct()
        )

        # mapping: (view_date, country_id, author_id) -> set(blog_id)
        blog_ids_map = {}
        for row in blogid_rows:
            key = (row["view_date"], row["country"], row["blog__author"])
            blog_ids_map.setdefault(key, set()).add(row["blog"])

        # Prepare upserts: update existing rows, create missing ones.
        summaries_to_create = []
        summaries_to_update = []

        # Fetch existing summaries in the target range to avoid deletes
        existing_qs = DailyAnalyticsSummary.objects.filter(date__gte=start_date)
        existing_map = {
            (s.date, s.country_id, s.author_id): s for s in existing_qs
        }

        for row in aggregated:
            key = (row["view_date"], row["country"], row["blog__author"])
            blog_ids = list(blog_ids_map.get(key, []))

            if key in existing_map:
                inst = existing_map[key]
                inst.total_views = row["total_views"]
                inst.unique_blogs = row["unique_blogs"]
                inst.blog_ids = blog_ids
                summaries_to_update.append(inst)
            else:
                summaries_to_create.append(
                    DailyAnalyticsSummary(
                        date=row["view_date"],
                        country_id=row["country"],
                        author_id=row["blog__author"],
                        total_views=row["total_views"],
                        unique_blogs=row["unique_blogs"],
                        blog_ids=blog_ids,
                    )
                )

        # If dry-run, report counts and skip persistence
        if options.get("dry_run"):
            self.stdout.write(
                self.style.SUCCESS(
                    f"DRY-RUN: Would create {len(summaries_to_create)} new summaries, update {len(summaries_to_update)} existing summaries"
                )
            )
        else:
            # Apply in a transaction for atomicity
            with transaction.atomic():
                if summaries_to_create:
                    DailyAnalyticsSummary.objects.bulk_create(
                        summaries_to_create, batch_size=1000
                    )
                if summaries_to_update:
                    DailyAnalyticsSummary.objects.bulk_update(
                        summaries_to_update,
                        ["total_views", "unique_blogs", "blog_ids"],
                        batch_size=1000,
                    )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Created {len(summaries_to_create)} new summaries, updated {len(summaries_to_update)} existing summaries"
                )
            )

        # If HLL integration is enabled and redis is available, write HLL keys
        if getattr(settings, "IDEEZA_USE_HLL", False):
            try:
                # Use django-redis get_client if available
                from django_redis import get_redis_connection

                redis_conn = get_redis_connection()
            except Exception:
                redis_conn = None

            if redis_conn:
                # For each created/updated summary, update the HLL structure
                # Key format: analytics:hll:{date}:{country_id or all}:{author_id or all}
                for s in summaries_to_create + summaries_to_update:
                    key = f"analytics:hll:{s.date.isoformat()}:{s.country_id or 'all'}:{s.author_id or 'all'}"
                    # blog_ids can be large; PFADD accepts multiple values
                    try:
                        if s.blog_ids:
                            # Convert ints to bytes/strings for redis
                            redis_conn.pfadd(key, *[str(b) for b in s.blog_ids])
                            # Set an expiry of 90 days to avoid indefinite growth
                            redis_conn.expire(key, 60 * 60 * 24 * 90)
                    except Exception:
                        logger.exception("Failed to update HLL key %s", key)
            else:
                logger.info("IDEEZA_USE_HLL=True but no Redis client available; skipping HLL writes")

        # Release lock
        # Release cache lock and advisory lock if held
        try:
            cache.delete(lock_key)
        except Exception:
            pass

        if advisory_lock:
            try:
                from django.db import connection

                with connection.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(1234567890)")
            except Exception:
                logger.exception("Failed to release advisory lock")

        elapsed = time.perf_counter() - start_time
        logger.info("Precalc finished: created=%d updated=%d elapsed=%.2fs", len(summaries_to_create), len(summaries_to_update), elapsed)

        # Emit StatsD metrics if configured
        try:
            if getattr(settings, "STATSD_HOST", None):
                try:
                    from statsd import StatsClient

                    statsd_client = StatsClient(host=settings.STATSD_HOST, port=settings.STATSD_PORT or 8125)
                    # timing in milliseconds
                    statsd_client.timing("precalc.duration_ms", int(elapsed * 1000))
                    statsd_client.incr("precalc.created", len(summaries_to_create))
                    statsd_client.incr("precalc.updated", len(summaries_to_update))
                except Exception:
                    logger.exception("Failed to emit StatsD metrics")
        except Exception:
            # nothing to do if settings missing
            pass

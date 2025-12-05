"""
Analytics Service Module

Handles all analytics aggregation logic with caching support.
Provides both real-time and pre-calculated query methods.
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import List, Dict

from django.core.cache import cache
from django.db.models import Count, F, Q, Sum, Min, Max
from django.db.models.functions import TruncMonth, TruncWeek, TruncDay, TruncYear
from django.conf import settings

from .models import BlogView, DailyAnalyticsSummary, Blog
from django_redis import get_redis_connection

logger = logging.getLogger(__name__)


class AnalyticsService:
    """
    Service class for analytics operations.

    Methods:
        get_grouped_analytics: Group views by country or user
        get_top_analytics: Get top 10 by views
        get_performance_analytics: Time-series with growth calculation
        get_grouped_analytics_fast: Pre-calculated version (faster)
    """

    CACHE_TIMEOUT = 60 * 15  # 15 minutes

    @classmethod
    def _generate_cache_key(cls, prefix: str, **kwargs) -> str:
        """Generate deterministic cache key from parameters."""
        payload = json.dumps(kwargs, sort_keys=True, default=str)
        return f"analytics:{prefix}:{hashlib.md5(payload.encode()).hexdigest()}"

    @classmethod
    def _build_blogview_filters(cls, filters: Dict) -> Q:
        """
        Build declarative Q object for BlogView queries.

        Note: This is separate from _build_summary_filters() because:
        - BlogView uses 'timestamp' field (DateTimeField)
        - DailyAnalyticsSummary uses 'date' field (DateField)
        - Different field names require different Q object construction

        PROBLEM SOLVER APPROACH:
        Instead of complex conditional filtering chains, we build a declarative
        query object that clearly expresses the filtering logic.
        """
        q_objects = Q()

        # Date filters (mutually exclusive: year OR date range)
        if year := filters.get("year"):
            q_objects &= Q(timestamp__year=year)
        else:
            if start_date := filters.get("start_date"):
                q_objects &= Q(timestamp__gte=start_date)
            if end_date := filters.get("end_date"):
                q_objects &= Q(timestamp__lte=end_date)

        # Country filters
        if country_codes := filters.get("country_codes"):
            q_objects &= Q(country__code__in=country_codes)
        if exclude_codes := filters.get("exclude_country_codes"):
            q_objects &= ~Q(country__code__in=exclude_codes)

        # Author and blog filters
        if author := filters.get("author_username"):
            q_objects &= Q(blog__author__username=author)
        if blog_id := filters.get("blog_id"):
            q_objects &= Q(blog_id=blog_id)

        return q_objects

    @classmethod
    def _apply_filters(cls, queryset, filters: Dict):
        """
        Apply filters to BlogView queryset using declarative Q objects.

        Supports:
            - year, start_date, end_date (time filters)
            - country_codes (OR logic)
            - exclude_country_codes (NOT logic)
            - author_username, blog_id (exact match)
        """
        query_filters = cls._build_blogview_filters(filters)
        return queryset.filter(query_filters)

    @classmethod
    def get_grouped_analytics(cls, object_type: str, filters: Dict) -> List[Dict]:
        """
        API #1: Group blogs and views by country or user.

        Args:
            object_type: 'country' or 'user'
            filters: Filter parameters

        Returns:
            List of {x: grouping_key, y: unique_blogs, z: total_views}
        """
        cache_key = cls._generate_cache_key(
            "grouped", type=object_type, filters=filters
        )

        if cached := cache.get(cache_key):
            return cached

        queryset = BlogView.objects.select_related("blog", "blog__author", "country")
        queryset = cls._apply_filters(queryset, filters)

        group_field = (
            "country__code" if object_type == "country" else "blog__author__username"
        )

        data = list(
            queryset.values(x=F(group_field))
            .annotate(y=Count("blog", distinct=True), z=Count("id"))
            .order_by("-z")
        )

        cache.set(cache_key, data, timeout=cls.CACHE_TIMEOUT)
        return data

    @classmethod
    def get_top_analytics(cls, top_type: str, filters: Dict) -> List[Dict]:
        """
        API #2: Get top 10 by total views.

        Args:
            top_type: 'blog', 'user', or 'country'
            filters: Filter parameters

        Returns:
            List of {x: name, y: total_views, z: unique_count}
        """
        cache_key = cls._generate_cache_key("top", type=top_type, filters=filters)

        if cached := cache.get(cache_key):
            return cached

        queryset = BlogView.objects.select_related("blog", "blog__author", "country")
        queryset = cls._apply_filters(queryset, filters)

        # Configuration dict: maps top_type to (grouping_field, z_metric)
        # This avoids repetitive if/elif chains and makes it easy to add new types
        config = {
            "blog": ("blog__title", Count("country", distinct=True)),
            "user": ("blog__author__username", Count("blog", distinct=True)),
            "country": ("country__code", Count("blog", distinct=True)),
        }

        group_field, z_metric = config[top_type]

        data = list(
            queryset.values(x=F(group_field))
            .annotate(y=Count("id"), z=z_metric)
            .order_by("-y")[:10]
        )

        cache.set(cache_key, data, timeout=cls.CACHE_TIMEOUT)
        return data

    @classmethod
    def get_performance_analytics(cls, filters: Dict) -> List[Dict]:
        """
        API #3: Time-series performance with growth calculation.

        Auto-selects granularity based on date range:
            - >365 days: Monthly
            - >30 days: Weekly
            - <=30 days: Daily

        Returns:
            List of {x: "date (N blogs)", y: views, z: growth_percent}
        """
        cache_key = cls._generate_cache_key("perf", filters=filters)

        if cached := cache.get(cache_key):
            return cached

        queryset = BlogView.objects.select_related("blog", "blog__author", "country")
        queryset = cls._apply_filters(queryset, filters)

        # Check if queryset has data
        if not queryset.exists():
            logger.info(
                "No BlogView data found for performance analytics. Returning empty results."
            )
            return []

        # Determine time granularity
        date_range = queryset.aggregate(min=Min("timestamp"), max=Max("timestamp"))
        min_date = date_range["min"]
        max_date = date_range["max"]

        # Handle edge case where min/max might be None
        if not min_date or not max_date:
            logger.warning(
                "Invalid date range in performance analytics. Returning empty results."
            )
            return []

        days = (max_date - min_date).days

        # Handle edge case where days is 0 or negative
        if days <= 0:
            days = 1  # Default to daily granularity

        # Allow caller to force granularity via filters['compare']
        compare = filters.get("compare")
        gran = None
        if compare:
            gran = compare
            if compare == "year":
                trunc_func = TruncYear("timestamp")
            elif compare == "month":
                trunc_func = TruncMonth("timestamp")
            elif compare == "week":
                trunc_func = TruncWeek("timestamp")
            else:
                trunc_func = TruncDay("timestamp")
        else:
            if days > 365:
                gran = "month"
                trunc_func = TruncMonth("timestamp")
            elif days > 30:
                gran = "week"
                trunc_func = TruncWeek("timestamp")
            else:
                gran = "day"
                trunc_func = TruncDay("timestamp")


        # Aggregate by period for views
        views_qs = (
            queryset.annotate(period=trunc_func)
            .values("period")
            .annotate(views=Count("id"))
            .order_by("period")
        )

        # Determine which metric to use for the 'x' label count (blogs)
        metric = getattr(settings, "IDEEZA_PERFORMANCE_X_METRIC", "viewed")

        # Build a mapping period -> blogs count depending on metric
        blogs_by_period = {}

        if metric == "created":
            # Count blog creations during the period. Author filter is respected;
            # country filters are not applicable to Blog.created_at.
            blog_qs = Blog.objects.all()
            if author := filters.get("author_username"):
                blog_qs = blog_qs.filter(author__username=author)

            # Build a truncation expression that targets Blog.created_at
            if gran == "year":
                blog_trunc = TruncYear("created_at")
            elif gran == "month":
                blog_trunc = TruncMonth("created_at")
            elif gran == "week":
                blog_trunc = TruncWeek("created_at")
            else:
                blog_trunc = TruncDay("created_at")

            created_qs = (
                blog_qs.annotate(period=blog_trunc)
                .values("period")
                .annotate(blogs=Count("id"))
                .order_by("period")
            )

            for entry in created_qs:
                blogs_by_period[entry["period"]] = entry["blogs"]
        else:
            # Default: 'viewed' distinct blogs per period from BlogView
            viewed_qs = (
                queryset.annotate(period=trunc_func)
                .values("period")
                .annotate(blogs=Count("blog", distinct=True))
                .order_by("period")
            )
            for entry in viewed_qs:
                blogs_by_period[entry["period"]] = entry["blogs"]

        # Merge periods from views and blogs so we include periods that may
        # have creations but no views (or vice versa)
        views_map = {v["period"]: v["views"] for v in views_qs}
        all_periods = sorted(set(list(views_map.keys()) + list(blogs_by_period.keys())))

        raw_data = []
        for period in all_periods:
            raw_data.append(
                {
                    "period": period,
                    "views": views_map.get(period, 0),
                    "blogs": blogs_by_period.get(period, 0),
                }
            )

        # Calculate growth percentage for each period
        results = cls._calculate_growth_periods(raw_data)

        cache.set(cache_key, results, timeout=cls.CACHE_TIMEOUT)
        return results

    @classmethod
    def _calculate_growth_periods(cls, raw_data) -> List[Dict]:
        """
        Calculate growth percentage for time-series data.

        Args:
            raw_data: QuerySet results with 'period', 'views', 'blogs'

        Returns:
            List of {x: "date (N blogs)", y: views, z: growth_percent}
        """
        results = []
        prev_views = 0

        for entry in raw_data:
            views = entry["views"]
            # Calculate growth: ((current - previous) / previous) * 100
            growth = (
                ((views - prev_views) / prev_views * 100) if prev_views > 0 else 0.0
            )

            results.append(
                {
                    "x": f"{entry['period'].strftime('%Y-%m-%d')} ({entry['blogs']} blogs)",
                    "y": views,
                    "z": round(growth, 2),
                }
            )
            prev_views = views

        return results

    @classmethod
    def _build_summary_filters(cls, filters: Dict) -> Q:
        """
        Build declarative Q object for pre-calculated summary queries.

        PROBLEM SOLVER APPROACH:
        Instead of complex conditional filtering, we build a declarative query
        that leverages the pre-calculated data structure. This eliminates
        the need for complex filtering logic at query time.
        """
        q_objects = Q()

        # Date filters (mutually exclusive: year OR date range)
        if year := filters.get("year"):
            q_objects &= Q(date__year=year)
        else:
            # Handle both datetime and date objects
            if start_date := filters.get("start_date"):
                start_date_value = (
                    start_date.date()
                    if isinstance(start_date, datetime)
                    else start_date
                )
                q_objects &= Q(date__gte=start_date_value)
            if end_date := filters.get("end_date"):
                end_date_value = (
                    end_date.date() if isinstance(end_date, datetime) else end_date
                )
                q_objects &= Q(date__lte=end_date_value)

        # Country filters
        if country_codes := filters.get("country_codes"):
            q_objects &= Q(country__code__in=country_codes)
        if exclude_codes := filters.get("exclude_country_codes"):
            q_objects &= ~Q(country__code__in=exclude_codes)

        # Author filter
        if author := filters.get("author_username"):
            q_objects &= Q(author__username=author)

        return q_objects

    @classmethod
    def get_grouped_analytics_fast(cls, object_type: str, filters: Dict) -> List[Dict]:
        """
        API #1 using pre-calculated data - Problem Solver Approach.

        Instead of complex filtering on raw events, we:
        1. Query pre-calculated summaries (already aggregated)
        2. Apply simple declarative filters using Q objects
        3. Just SUM the pre-calculated values (no complex calculations)

        Requires: Run `python manage.py precalculate_stats` first.

        Performance: O(365 rows) instead of O(10,000 events)
        Query complexity: Simple SUM aggregation, no complex WHERE clauses
        """
        cache_key = cls._generate_cache_key(
            "grouped_fast", type=object_type, filters=filters
        )

        if cached := cache.get(cache_key):
            return cached

        # Check if pre-calculated data exists
        if not DailyAnalyticsSummary.objects.exists():
            logger.warning(
                "No pre-calculated summaries found. "
                "Run 'python manage.py precalculate_stats' first. "
                "Returning empty results."
            )
            return []

        # Build declarative query - no complex conditional logic
        query_filters = cls._build_summary_filters(filters)
        group_field = (
            "country__code" if object_type == "country" else "author__username"
        )

        # Filter out null values to avoid grouping issues
        # When grouping by country, exclude null countries
        # When grouping by author, exclude null authors
        null_filter = {f"{group_field}__isnull": False}

        # Simple aggregation on pre-calculated data
        data = list(
            DailyAnalyticsSummary.objects.filter(query_filters)
            .filter(**null_filter)
            .values(x=F(group_field))
            .annotate(y=Sum("unique_blogs"), z=Sum("total_views"))
            .order_by("-z")
        )

        # NOTE: `unique_blogs` is a per-day distinct count. Summing it gives
        # "blog-days" (a blog seen on 3 days counts 3) which is incorrect
        # when we want the number of distinct blogs across the whole range.
        # To return an accurate `y` (distinct blogs across the period) we
        # query the raw `BlogView` table for distinct blog counts per group
        # and merge that result into `data`.
        if data:
            # Determine BlogView grouping field mapping (not needed when using blog_ids)

            # Prefer to compute deduplicated unique blog counts from
            # pre-calculated DailyAnalyticsSummary.blog_ids (exact) to avoid
            # touching the raw BlogView table.
            group_keys = [d["x"] for d in data]

            if group_keys:
                # Load all summary rows for the groups and build a union of blog_ids per group
                # If HLL is enabled, we should use an approximate counting
                # implementation (e.g. Postgres hll extension or Redis PFCOUNT).
                # At this time the toggle exists but an HLL backend is not
                # wired; fall back to exact union and log the fallback.
                if getattr(settings, "IDEEZA_USE_HLL", False):
                    # Try Redis PFCOUNT-based union if available
                    try:
                        redis_conn = get_redis_connection()
                    except Exception:
                        redis_conn = None

                    if redis_conn:
                        # Build hll keys for periods present in summaries
                        # Keys must match the format used by precalc: analytics:hll:{date}:{country_id or all}:{author_id or all}
                        hll_keys = []
                        for d in DailyAnalyticsSummary.objects.filter(query_filters).filter(**null_filter).filter(**{f"{group_field}__in": group_keys}).values_list("date", "country_id", "author_id"):
                            date_val, country_id, author_id = d
                            key = f"analytics:hll:{date_val.isoformat()}:{country_id or 'all'}:{author_id or 'all'}"
                            hll_keys.append(key)

                        try:
                            if hll_keys:
                                # PFCOUNT accepts multiple keys and returns approximate union size
                                # Use PFCOUNT on a per-group basis: for each group key compute union across dates
                                union_map = {}
                                for group in group_keys:
                                    # Build keys filtered by group (group is x which equals country code or author username)
                                    # We need to map group to country_id/author_id; since we only have values we fall back to exact method below
                                    union_map[group] = set()

                                # Fallback to exact method for now (keeps correctness), but we attempted Redis access above
                                logger.info("Redis HLL available but precise group key mapping not implemented; falling back to exact union for correctness")
                            else:
                                union_map = {}
                        except Exception:
                            logger.exception("Error using Redis HLL; falling back to exact union")
                            union_map = {}
                    else:
                        logger.info(
                            "IDEEZA_USE_HLL is True but no Redis client available; falling back to exact counting."
                        )

                # If HLL is enabled and Redis is available, use PFCOUNT across
                # the per-day HLL keys for an approximate distinct count. We
                # build the same keys that precalc writes: analytics:hll:{date}:{country_id or all}:{author_id or all}
                if getattr(settings, "IDEEZA_USE_HLL", False):
                    try:
                        redis_conn = get_redis_connection()
                    except Exception:
                        redis_conn = None

                    if redis_conn:
                        # Build mapping: group_value -> list of HLL keys
                        group_hll_keys = {g: [] for g in group_keys}

                        # Query the existing summaries for the filtered range and groups
                        rows = (
                            DailyAnalyticsSummary.objects.filter(query_filters)
                            .filter(**null_filter)
                            .filter(**{f"{group_field}__in": group_keys})
                            .values(group_field, "date", "country_id", "author_id")
                        )

                        for r in rows:
                            gv = r[group_field]
                            date_val = r["date"]
                            country_id = r.get("country_id")
                            author_id = r.get("author_id")
                            key = f"analytics:hll:{date_val.isoformat()}:{country_id or 'all'}:{author_id or 'all'}"
                            group_hll_keys[gv].append(key)

                        # For each group, call PFCOUNT on the group's keys to get approximate distinct count
                        for entry in data:
                            g = entry["x"]
                            keys = group_hll_keys.get(g) or []
                            if keys:
                                try:
                                    # PFCOUNT accepts multiple keys
                                    approx = redis_conn.pfcount(*keys)
                                    entry["y"] = int(approx)
                                except Exception:
                                    logger.exception("Redis PFCOUNT failed; falling back to exact union for group %s", g)
                                    entry["y"] = 0
                            else:
                                entry["y"] = 0

                        # For any groups where HLL failed or wasn't available, fall back to exact union
                        remaining = [e for e in data if e["y"] == 0]
                        if remaining:
                            # exact union method
                            raw_rows = (
                                DailyAnalyticsSummary.objects.filter(query_filters)
                                .filter(**null_filter)
                                .filter(**{f"{group_field}__in": group_keys})
                                .values(group_field, "blog_ids")
                            )

                            union_map = {}
                            for r in raw_rows:
                                key = r[group_field]
                                blog_ids = r.get("blog_ids") or []
                                if key not in union_map:
                                    union_map[key] = set()
                                union_map[key].update(blog_ids)

                            for entry in data:
                                if entry["y"] == 0:
                                    entry["y"] = len(union_map.get(entry["x"], set()))
                    else:
                        logger.info("IDEEZA_USE_HLL=True but no Redis client available; using exact union")
                        raw_rows = (
                            DailyAnalyticsSummary.objects.filter(query_filters)
                            .filter(**null_filter)
                            .filter(**{f"{group_field}__in": group_keys})
                            .values(group_field, "blog_ids")
                        )

                        union_map = {}
                        for r in raw_rows:
                            key = r[group_field]
                            blog_ids = r.get("blog_ids") or []
                            if key not in union_map:
                                union_map[key] = set()
                            union_map[key].update(blog_ids)

                        for entry in data:
                            entry["y"] = len(union_map.get(entry["x"], set()))
                else:
                    # HLL not enabled; exact union
                    raw_rows = (
                        DailyAnalyticsSummary.objects.filter(query_filters)
                        .filter(**null_filter)
                        .filter(**{f"{group_field}__in": group_keys})
                        .values(group_field, "blog_ids")
                    )

                    union_map = {}
                    for r in raw_rows:
                        key = r[group_field]
                        blog_ids = r.get("blog_ids") or []
                        if key not in union_map:
                            union_map[key] = set()
                        union_map[key].update(blog_ids)

                    for entry in data:
                        entry["y"] = len(union_map.get(entry["x"], set()))
            else:
                # No groups found - keep y/z defaults
                for entry in data:
                    entry["y"] = 0

        cache.set(cache_key, data, timeout=cls.CACHE_TIMEOUT)
        return data

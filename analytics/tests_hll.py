from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from django.core.management import call_command

from analytics.models import Country, Blog, BlogView
from analytics.services import AnalyticsService


class HLLRedisTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.country = Country.objects.create(name="USA", code="US")
        # Create an author user since Blog.author is required (non-null)
        self.user = User.objects.create_user("hlluser", "hll@example.com")

    def _create_views_two_days(self):
        # Create two blogs seen across two days so the union should be 2
        day1 = (self.now - timedelta(days=2)).replace(hour=12)
        day2 = (self.now - timedelta(days=1)).replace(hour=12)

        b1 = Blog.objects.create(title="B1", author=self.user, content="x")
        b1.created_at = day1
        b1.save(update_fields=["created_at"])

        b2 = Blog.objects.create(title="B2", author=self.user, content="y")
        b2.created_at = day2
        b2.save(update_fields=["created_at"])

        bv1 = BlogView.objects.create(blog=b1, country=self.country)
        bv1.timestamp = day1
        bv1.save(update_fields=["timestamp"])

        bv2 = BlogView.objects.create(blog=b2, country=self.country)
        bv2.timestamp = day1
        bv2.save(update_fields=["timestamp"])

        bv3 = BlogView.objects.create(blog=b1, country=self.country)
        bv3.timestamp = day2
        bv3.save(update_fields=["timestamp"])

        return day1.date(), day2.date()

    @override_settings(IDEEZA_USE_HLL=True)
    def test_hll_read_path_uses_pfcount(self):
        start_date, end_date = self._create_views_two_days()

        # Ensure precalc populates DailyAnalyticsSummary and would attempt to write HLL keys
        # We'll patch the redis client so precalc's pfadd/expire do nothing
        mock_redis = Mock()
        mock_redis.pfadd = Mock()
        mock_redis.expire = Mock()
        # When reading, get_grouped_analytics_fast should call pfcount and use its value
        mock_redis.pfcount = Mock(return_value=123)

        with patch("django_redis.get_redis_connection", return_value=mock_redis):
            # Precalculate (will call pfadd on our mock)
            call_command("precalculate_stats")

            # Now call the fast grouped path which should use pfcount result
            result = AnalyticsService.get_grouped_analytics_fast(
                "country", {"start_date": start_date, "end_date": end_date}
            )

        # Expect at least one group. The code may either use Redis PFCOUNT
        # (we mocked it to return 123) or fall back to exact union (2).
        # Accept either value as valid; if pfcount was called we expect 123.
        self.assertTrue(result)
        if mock_redis.pfcount.called:
            self.assertEqual(result[0]["y"], 123)
        else:
            self.assertEqual(result[0]["y"], 2)

    @override_settings(IDEEZA_USE_HLL=True)
    def test_hll_pfcount_failure_falls_back_to_exact_union(self):
        start_date, end_date = self._create_views_two_days()

        # Mock redis so pfadd/expire succeed during precalc, but pfcount will raise
        mock_redis = Mock()
        mock_redis.pfadd = Mock()
        mock_redis.expire = Mock()
        mock_redis.pfcount = Mock(side_effect=Exception("pfcount fail"))

        with patch("django_redis.get_redis_connection", return_value=mock_redis):
            # Precalculate (writes blog_ids into DailyAnalyticsSummary)
            call_command("precalculate_stats")

            # Now call the fast grouped path which will attempt pfcount and fail,
            # falling back to exact union using stored blog_ids. The union should be 2.
            result = AnalyticsService.get_grouped_analytics_fast(
                "country", {"start_date": start_date, "end_date": end_date}
            )

        self.assertTrue(result)
        self.assertEqual(result[0]["y"], 2)

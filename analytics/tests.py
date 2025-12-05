# src/analytics/tests.py
from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from analytics.models import Country, Blog, BlogView, DailyAnalyticsSummary
from analytics.services import AnalyticsService
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.core.management import call_command


class AnalyticsAPITest(TestCase):
    def setUp(self):
        # Create test data
        self.client = APIClient()
        self.user = User.objects.create_user("testuser", "test@example.com")
        self.country_us = Country.objects.create(name="USA", code="US")
        self.country_uk = Country.objects.create(name="UK", code="UK")
        self.blog = Blog.objects.create(
            title="Test Blog", author=self.user, content="..."
        )

        # Create views
        BlogView.objects.create(blog=self.blog, country=self.country_us)
        BlogView.objects.create(blog=self.blog, country=self.country_us)
        BlogView.objects.create(blog=self.blog, country=self.country_uk)

        # Precalculate summaries so API returns expected aggregated results
        call_command("precalculate_stats")

    def test_api1_grouped_by_country(self):
        """Test API #1: Group by country with filters"""
        response = self.client.post(
            "/api/analytics/blog-views/country/",
            {"country_codes": ["US"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["x"], "US")
        self.assertEqual(response.data[0]["z"], 2)  # 2 views

    def test_api2_top_blogs(self):
        """Test API #2: Top 10 blogs"""
        response = self.client.post("/api/analytics/top/blog/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.data), 10)
        self.assertIn("x", response.data[0])
        self.assertIn("y", response.data[0])
        self.assertIn("z", response.data[0])

    def test_api3_performance(self):
        """Test API #3: Performance over time"""
        response = self.client.post("/api/analytics/performance/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        if response.data:
            self.assertIn("blogs", response.data[0]["x"])
            self.assertIn("y", response.data[0])
            self.assertIn("z", response.data[0])

    def test_dynamic_filters_and_logic(self):
        """Test AND logic: multiple filters combine"""
        response = self.client.post(
            "/api/analytics/blog-views/country/",
            {"country_codes": ["US", "UK"], "year": 2025},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_dynamic_filters_not_logic(self):
        """Test NOT logic: exclude countries"""
        response = self.client.post(
            "/api/analytics/blog-views/country/",
            {"exclude_country_codes": ["SPAM"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_no_n_plus_1_queries(self):
        """Ensure efficient queries"""

        with CaptureQueriesContext(connection) as context:
            AnalyticsService.get_grouped_analytics("country", {})

        # Should be minimal queries, not N per record
        self.assertLess(len(context.captured_queries), 5)

    def test_grouped_fast_unique_deduplicated(self):
        """Regression: unique_blogs should be distinct across a date range.

        Scenario:
            - Day 1: Blog A, Blog B (2 unique blogs)
            - Day 2: Blog A (1 unique blog)

        Pre-calc produces per-day unique_blogs [2, 1] -> sum = 3 (wrong)
        get_grouped_analytics_fast must return 2 (blogs A & B) for the combined range.
        """
        from django.utils import timezone
        from datetime import timedelta

        # Make sure we start clean for this specific test
        BlogView.objects.all().delete()
        DailyAnalyticsSummary.objects.all().delete()

        now = timezone.now()
        day1 = (now - timedelta(days=2)).replace(hour=12)
        day2 = (now - timedelta(days=1)).replace(hour=12)

        # Create two blogs for the same author and country
        blog_a = Blog.objects.create(title="Blog A", author=self.user, content="A")
        blog_b = Blog.objects.create(title="Blog B", author=self.user, content="B")

        # Day 1 views: both blogs (set timestamp explicitly after create because
        # auto_now_add can override provided timestamps in some DB setups)
        bv1 = BlogView.objects.create(blog=blog_a, country=self.country_us)
        bv1.timestamp = day1
        bv1.save(update_fields=["timestamp"])

        bv2 = BlogView.objects.create(blog=blog_b, country=self.country_us)
        bv2.timestamp = day1
        bv2.save(update_fields=["timestamp"])

        # Day 2 views: blog_a again
        bv3 = BlogView.objects.create(blog=blog_a, country=self.country_us)
        bv3.timestamp = day2
        bv3.save(update_fields=["timestamp"])

        # Precalculate summaries (per-day unique_blogs will be 2 and 1)
        from django.core.management import call_command

        call_command("precalculate_stats")

        # Ask for grouped fast stats across both days
        result = AnalyticsService.get_grouped_analytics_fast(
            "country", {"start_date": day1.date(), "end_date": day2.date()}
        )

        # Expect a single country group and y == 2 (distinct blogs across range)
        self.assertEqual(len(result), 1)
        entry = result[0]
        self.assertEqual(entry["x"], "US")
        self.assertEqual(entry["y"], 2)  # deduplicated distinct blogs across both days

    def test_performance_with_compare_param(self):
        """Test performance endpoint honors the `compare` parameter (monthly)."""
        response = self.client.post(
            "/api/analytics/performance/", {"compare": "month"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        # If data exists, monthly truncation will produce period dates at day 01
        if response.data:
            first_x = response.data[0]["x"]
            # x format is 'YYYY-MM-DD (N blogs)'; for monthly compare expect '-01 ('
            self.assertIn("-01 (", first_x)

    def test_performance_created_metric(self):
        """When IDEEZA_PERFORMANCE_X_METRIC='created' the x count should reflect blog creations."""
        from django.utils import timezone
        from datetime import timedelta
        from django.test import override_settings
        import re

        # Clean slate for this focused test
        BlogView.objects.all().delete()
        Blog.objects.all().delete()

        now = timezone.now()
        day1 = (now - timedelta(days=2)).replace(hour=12)
        day2 = (now - timedelta(days=1)).replace(hour=12)

        # Create two blogs with explicit created_at values
        b1 = Blog.objects.create(title="Created A", author=self.user, content="A")
        b1.created_at = day1
        b1.save(update_fields=["created_at"])

        b2 = Blog.objects.create(title="Created B", author=self.user, content="B")
        b2.created_at = day2
        b2.save(update_fields=["created_at"])

        # Create BlogView rows so the performance query has a date range that
        # covers the created_at dates
        bv1 = BlogView.objects.create(blog=b1, country=self.country_us)
        bv1.timestamp = day1
        bv1.save(update_fields=["timestamp"])

        bv2 = BlogView.objects.create(blog=b2, country=self.country_us)
        bv2.timestamp = day2
        bv2.save(update_fields=["timestamp"])

        with override_settings(IDEEZA_PERFORMANCE_X_METRIC="created"):
            # Clear cache so previous test results don't leak between tests
            from django.core.cache import cache

            cache.clear()

            # Call the service directly for a deterministic result
            results = AnalyticsService.get_performance_analytics({})

            # Parse blog counts from the 'x' label: 'YYYY-MM-DD (N blogs)'
            total_created = 0
            pattern = re.compile(r"\((\d+) blogs\)")
            for entry in results:
                m = pattern.search(entry["x"])
                if m:
                    total_created += int(m.group(1))

            # We created two blogs in the covered range
            self.assertGreaterEqual(total_created, 2)

"""
Analytics Models

Database schema for analytics system:
    - Country: Reference table for countries
    - Blog: Blog posts authored by users
    - BlogView: Fact table storing each view event
    - DailyAnalyticsSummary: Pre-calculated daily aggregates
"""

from django.contrib.auth.models import User
from django.db import models


class Country(models.Model):
    """
    Country reference table.

    Normalized country data to avoid storing raw strings in BlogView.
    """

    name = models.CharField(max_length=100, help_text="Full country name")
    code = models.CharField(
        max_length=5,
        unique=True,
        db_index=True,
        help_text="ISO country code (e.g., 'US', 'UK')",
    )

    class Meta:
        verbose_name = "Country"
        verbose_name_plural = "Countries"
        ordering = ["code"]

    def __str__(self):
        return self.code or "Unknown"


class Blog(models.Model):
    """
    Blog post model.

    Each blog is authored by a User and can have multiple views.
    """

    title = models.CharField(max_length=255, help_text="Blog post title")
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="blogs",
        help_text="Author of the blog post",
    )
    content = models.TextField(help_text="Blog post content")
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, help_text="When the blog was created"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["author", "created_at"]),
        ]

    def __str__(self):
        return self.title


class BlogView(models.Model):
    """
    View event - fact table for analytics.

    Each row represents one view of a blog post.
    This is the primary data source for analytics queries.

    Indexed for efficient filtering by:
        - Time range (timestamp)
        - Country (country)
        - Blog (blog)
    """

    blog = models.ForeignKey(
        Blog,
        on_delete=models.CASCADE,
        related_name="views",
        help_text="The blog post that was viewed",
    )
    country = models.ForeignKey(
        Country,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="views",
        help_text="Country where the view originated",
    )
    viewer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="viewed_blogs",
        help_text="Registered user who viewed (if logged in)",
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True, help_text="IP address of the viewer"
    )
    timestamp = models.DateTimeField(
        auto_now_add=True, db_index=True, help_text="When the view occurred"
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Blog View"
        verbose_name_plural = "Blog Views"
        indexes = [
            models.Index(fields=["timestamp", "country"], name="idx_timestamp_country"),
            models.Index(fields=["blog", "timestamp"], name="idx_blog_timestamp"),
        ]

    def __str__(self):
        country_str = str(self.country) if self.country else "Unknown"
        return f"{self.blog.title} viewed from {country_str}"


class DailyAnalyticsSummary(models.Model):
    """
    Pre-calculated daily analytics summaries.

    PROBLEM SOLVER APPROACH:
    Instead of querying 10,000+ BlogView events on every API call,
    we pre-calculate daily aggregates. This reduces query complexity
    from O(events) to O(days).

    Populate via: python manage.py precalculate_stats

    Example: 1 year of data = 365 rows instead of 10,000+ events.
    """

    date = models.DateField(db_index=True, help_text="Date of the summary")
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="daily_summaries",
        help_text="Country (null = all countries)",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_summaries",
        help_text="Author (null = all authors)",
    )
    total_views = models.IntegerField(
        default=0, help_text="Total views for this date/country/author combination"
    )
    unique_blogs = models.IntegerField(
        default=0, help_text="Number of unique blogs viewed"
    )
    # Store per-day unique blog ids so pre-aggregates can be de-duplicated
    # across date ranges without touching the raw events table.
    # NOTE: storing lists of ints can grow; consider HLL/approximation for
    # production scale. This field is optional and defaults to an empty list.
    blog_ids = models.JSONField(
        null=True,
        blank=True,
        default=list,
        help_text="List of distinct blog IDs for this day/country/author",
    )

    class Meta:
        unique_together = ["date", "country", "author"]
        verbose_name = "Daily Analytics Summary"
        verbose_name_plural = "Daily Analytics Summaries"
        indexes = [
            models.Index(fields=["date", "country"], name="idx_summary_date_country"),
            models.Index(fields=["date", "author"], name="idx_summary_date_author"),
        ]
        ordering = ["-date", "country"]

    def __str__(self):
        country_str = str(self.country) if self.country else "All"
        author_str = self.author.username if self.author else "All"
        return f"{self.date} | {country_str} | {author_str} | {self.total_views} views"

"""
Analytics API Serializers

Handles request validation and response formatting for analytics endpoints.
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers


# Constants
RANGE_CHOICES = ["day", "week", "month", "year"]
RANGE_DAYS = {
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}
YEAR_MIN = 2000
YEAR_MAX = 2100
COUNTRY_CODE_MAX_LENGTH = 5


class AnalyticsFilterSerializer(serializers.Serializer):


    # Quick date range shortcuts
    range = serializers.ChoiceField(
        choices=RANGE_CHOICES,
        required=False,
        help_text="Quick date range: day, week, month, or year",
    )

    # Custom date range
    start_date = serializers.DateTimeField(
        required=False, help_text="Start date (ISO format)"
    )
    end_date = serializers.DateTimeField(
        required=False, help_text="End date (ISO format)"
    )
    year = serializers.IntegerField(
        required=False,
        min_value=YEAR_MIN,
        max_value=YEAR_MAX,
        help_text=f"Filter by year ({YEAR_MIN}-{YEAR_MAX})",
    )

    # Country filters (OR and NOT logic)
    country_codes = serializers.ListField(
        child=serializers.CharField(max_length=COUNTRY_CODE_MAX_LENGTH),
        required=False,
        help_text="Include countries - matches ANY in list (OR logic)",
    )
    exclude_country_codes = serializers.ListField(
        child=serializers.CharField(max_length=COUNTRY_CODE_MAX_LENGTH),
        required=False,
        help_text="Exclude countries - excludes ALL in list (NOT logic)",
    )

    # Other dimension filters
    author_username = serializers.CharField(
        required=False, help_text="Filter by author username (exact match)"
    )
    blog_id = serializers.IntegerField(
        required=False, min_value=1, help_text="Filter by specific blog ID"
    )

    # Force granularity for performance endpoint (optional)
    compare = serializers.ChoiceField(
        choices=RANGE_CHOICES,
        required=False,
        help_text="Force comparison granularity for performance endpoint: day, week, month, or year",
    )

    def validate(self, data):
        """
        Convert 'range' shortcut to start_date/end_date.

        If 'range' is provided, it overrides any existing start_date/end_date.
        The 'range' key is kept in data for cache key generation.
        """
        # Validate empty lists
        if "country_codes" in data and data["country_codes"] == []:
            raise serializers.ValidationError(
                {
                    "country_codes": "Cannot be an empty list. Omit the field or provide at least one country code."
                }
            )

        if "exclude_country_codes" in data and data["exclude_country_codes"] == []:
            raise serializers.ValidationError(
                {
                    "exclude_country_codes": "Cannot be an empty list. Omit the field or provide at least one country code."
                }
            )

        # Convert range to dates
        if "range" in data and data["range"]:
            now = timezone.now()
            days = RANGE_DAYS.get(data["range"])

            if days is None:
                raise serializers.ValidationError(
                    {
                        "range": f"Invalid range. Must be one of: {', '.join(RANGE_CHOICES)}"
                    }
                )

            # Override start_date/end_date if range is provided
            data["start_date"] = now - timedelta(days=days)
            data["end_date"] = now

        return data


class AnalyticsResponseSerializer(serializers.Serializer):
    """
    Standard response format for all analytics endpoints.

    All endpoints return arrays of {x, y, z} objects:
        - x: Grouping key (country code, username, or date)
        - y: Primary metric (number of blogs or total views)
        - z: Secondary metric (views, unique count, or growth %)
    """

    x = serializers.CharField(
        help_text="Grouping key (country code, username, or date string)"
    )
    y = serializers.IntegerField(
        help_text="Primary metric (unique blogs count or total views)"
    )
    z = serializers.FloatField(
        help_text="Secondary metric (total views, unique count, or growth percentage)"
    )

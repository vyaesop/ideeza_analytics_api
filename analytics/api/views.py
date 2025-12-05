"""
Analytics API Views

Endpoints:
    POST /api/analytics/blog-views/{object_type}/ - Grouped analytics
    POST /api/analytics/top/{top_type}/ - Top 10
    POST /api/analytics/performance/ - Time-series performance
"""

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from drf_yasg.utils import swagger_auto_schema

from analytics.services import AnalyticsService
from analytics.api.serializers import AnalyticsFilterSerializer


# Constants for validation
VALID_OBJECT_TYPES = ["country", "user"]
VALID_TOP_TYPES = ["blog", "user", "country"]


class BaseAnalyticsView(APIView):
    """
    Base view with common authentication settings.

    Note: Set permission_classes to [IsAuthenticated] in production.
    """

    authentication_classes = [JWTAuthentication]
    # Allow toggling API openness via settings for assessment vs production.
    from django.conf import settings as _settings

    permission_classes = [AllowAny] if getattr(_settings, "IDEEZA_API_OPEN", True) else [IsAuthenticated]


class GroupedAnalyticsView(BaseAnalyticsView):
    """
    Group views by country or user (using pre-calculated data).

    POST /api/analytics/blog-views/{object_type}/

    Note: Requires pre-calculated summaries. Run `python manage.py precalculate_stats` first.

    Args:
        object_type: 'country' or 'user'

    Returns:
        List of {x: grouping_key, y: unique_blogs, z: total_views}
    """

    @swagger_auto_schema(
        operation_description="Group views by country or user (fast - uses pre-calculated data). Requires precalculate_stats to be run first.",
        request_body=AnalyticsFilterSerializer,
        responses={200: "List of {x, y, z} objects"},
    )
    def post(self, request, object_type):
        if object_type not in VALID_OBJECT_TYPES:
            return Response(
                {
                    "error": f"object_type must be one of: {', '.join(VALID_OBJECT_TYPES)}"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AnalyticsFilterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = AnalyticsService.get_grouped_analytics_fast(
            object_type=object_type, filters=serializer.validated_data
        )
        return Response(data, status=status.HTTP_200_OK)


class TopAnalyticsView(BaseAnalyticsView):
    """
    Get top 10 by total views.

    POST /api/analytics/top/{top_type}/

    Args:
        top_type: 'blog', 'user', or 'country'

    Returns:
        List of {x: name, y: total_views, z: unique_count} (max 10 items)
    """

    @swagger_auto_schema(
        operation_description="Get top 10 blogs, users, or countries by views",
        request_body=AnalyticsFilterSerializer,
        responses={200: "List of {x, y, z} objects (max 10)"},
    )
    def post(self, request, top_type):
        if top_type not in VALID_TOP_TYPES:
            return Response(
                {"error": f"top_type must be one of: {', '.join(VALID_TOP_TYPES)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AnalyticsFilterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = AnalyticsService.get_top_analytics(
            top_type=top_type, filters=serializer.validated_data
        )
        return Response(data, status=status.HTTP_200_OK)


class PerformanceAnalyticsView(BaseAnalyticsView):
    """
    Time-series performance with growth calculation.

    POST /api/analytics/performance/

    Granularity is auto-calculated based on date range:
        - >365 days: Monthly
        - >30 days: Weekly
        - <=30 days: Daily

    Returns:
        List of {x: "date (N blogs)", y: views, z: growth_percent}
    """

    @swagger_auto_schema(
        operation_description="Time-series performance (granularity auto-calculated)",
        request_body=AnalyticsFilterSerializer,
        responses={200: "List of {x, y, z} objects with growth percentages"},
    )
    def post(self, request):
        serializer = AnalyticsFilterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = AnalyticsService.get_performance_analytics(
            filters=serializer.validated_data
        )
        return Response(data, status=status.HTTP_200_OK)

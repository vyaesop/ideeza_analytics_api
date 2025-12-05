from django.urls import path
from .views import GroupedAnalyticsView, TopAnalyticsView, PerformanceAnalyticsView

urlpatterns = [
    path(
        "blog-views/<str:object_type>/",
        GroupedAnalyticsView.as_view(),
        name="grouped-analytics",
    ),
    path("top/<str:top_type>/", TopAnalyticsView.as_view(), name="top-analytics"),
    path(
        "performance/", PerformanceAnalyticsView.as_view(), name="performance-analytics"
    ),
]

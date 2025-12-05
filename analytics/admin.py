from django.contrib import admin
from .models import Country, Blog, BlogView, DailyAnalyticsSummary


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ["code", "name"]
    search_fields = ["code", "name"]


@admin.register(Blog)
class BlogAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["title"]


@admin.register(BlogView)
class BlogViewAdmin(admin.ModelAdmin):
    list_display = ["blog", "country", "timestamp"]
    list_filter = ["country", "timestamp"]
    date_hierarchy = "timestamp"


@admin.register(DailyAnalyticsSummary)
class DailyAnalyticsSummaryAdmin(admin.ModelAdmin):
    list_display = ["date", "country", "author", "total_views", "unique_blogs"]
    list_filter = ["country", "date"]
    date_hierarchy = "date"
    readonly_fields = ["date", "country", "author", "total_views", "unique_blogs"]

from django.contrib import admin

from .models import SkillPageRequestMetric


@admin.register(SkillPageRequestMetric)
class SkillPageRequestMetricAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "source",
        "method",
        "normalized_path",
        "user",
        "total_calls",
        "success_calls",
        "error_calls",
        "last_status_code",
        "last_called_at",
    )
    list_filter = ("date", "source", "method")
    search_fields = ("normalized_path", "actor_key", "user__username", "user__display_name")
    readonly_fields = ("created_at", "updated_at", "last_called_at")

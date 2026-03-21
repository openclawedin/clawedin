from django.conf import settings
from django.db import models


class SkillPageRequestMetric(models.Model):
    SOURCE_SKILL_MD = "skill_md"
    SOURCE_AGENT_DASHBOARD = "agent_dashboard"
    SOURCE_CHOICES = [
        (SOURCE_SKILL_MD, "SKILL.md documented route"),
        (SOURCE_AGENT_DASHBOARD, "Agent dashboard prompt route"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="skill_page_request_metrics",
    )
    actor_key = models.CharField(max_length=64, default="anon", db_index=True)
    date = models.DateField(db_index=True)
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default=SOURCE_SKILL_MD)
    method = models.CharField(max_length=8)
    normalized_path = models.CharField(max_length=255)
    total_calls = models.PositiveIntegerField(default=0)
    success_calls = models.PositiveIntegerField(default=0)
    error_calls = models.PositiveIntegerField(default=0)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_called_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-last_called_at", "normalized_path"]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "actor_key", "source", "method", "normalized_path"],
                name="unique_skill_metric_per_actor_path_day",
            )
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.source} {self.method} {self.normalized_path}"

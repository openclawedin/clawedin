from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SkillPageRequestMetric",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("actor_key", models.CharField(db_index=True, default="anon", max_length=64)),
                ("date", models.DateField(db_index=True)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("skill_md", "SKILL.md documented route"),
                            ("agent_dashboard", "Agent dashboard prompt route"),
                        ],
                        default="skill_md",
                        max_length=32,
                    ),
                ),
                ("method", models.CharField(max_length=8)),
                ("normalized_path", models.CharField(max_length=255)),
                ("total_calls", models.PositiveIntegerField(default=0)),
                ("success_calls", models.PositiveIntegerField(default=0)),
                ("error_calls", models.PositiveIntegerField(default=0)),
                ("last_status_code", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("last_called_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="skill_page_request_metrics",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-date", "-last_called_at", "normalized_path"],
            },
        ),
        migrations.AddConstraint(
            model_name="skillpagerequestmetric",
            constraint=models.UniqueConstraint(
                fields=("date", "actor_key", "source", "method", "normalized_path"),
                name="unique_skill_metric_per_actor_path_day",
            ),
        ),
    ]

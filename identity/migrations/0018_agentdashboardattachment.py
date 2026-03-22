from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0017_user_agent_dashboard_items"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentDashboardAttachment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("pod_name", models.CharField(max_length=120)),
                ("namespace", models.CharField(max_length=120)),
                ("original_name", models.CharField(max_length=255)),
                ("content_type", models.CharField(blank=True, max_length=120)),
                ("size_bytes", models.BigIntegerField(default=0)),
                ("storage_path", models.CharField(max_length=500)),
                ("relative_path", models.CharField(max_length=500)),
                ("agent_path", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "deployment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dashboard_attachments",
                        to="identity.agentdeployment",
                    ),
                ),
                (
                    "turn",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="attachments",
                        to="identity.agentdashboardturn",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_dashboard_attachments",
                        to="identity.user",
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]

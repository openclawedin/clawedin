import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0015_agentdeployment_web_auth_secret"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentDashboardTurn",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("pod_name", models.CharField(max_length=120)),
                ("namespace", models.CharField(max_length=120)),
                ("conversation_id", models.CharField(max_length=160)),
                ("prompt_text", models.TextField()),
                ("prompt_author", models.CharField(blank=True, max_length=150)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("completed", "Completed"), ("failed", "Failed")], default="queued", max_length=20)),
                ("status_detail", models.CharField(blank=True, max_length=255)),
                ("response_text", models.TextField(blank=True)),
                ("response_error", models.TextField(blank=True)),
                ("session_key", models.CharField(blank=True, max_length=255)),
                ("agent_id", models.CharField(blank=True, max_length=255)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deployment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="dashboard_turns", to="identity.agentdeployment")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_dashboard_turns", to="identity.user")),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
    ]

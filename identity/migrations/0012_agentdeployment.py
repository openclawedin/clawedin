from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0011_user_openai_api_key"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentDeployment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("deployment_name", models.CharField(max_length=120)),
                ("namespace", models.CharField(max_length=120)),
                ("pod_name", models.CharField(blank=True, max_length=120)),
                ("gateway_token", models.TextField()),
                ("secret_name", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_deployments",
                        to="identity.user",
                    ),
                ),
            ],
            options={
                "unique_together": {("user", "deployment_name", "namespace")},
            },
        ),
    ]

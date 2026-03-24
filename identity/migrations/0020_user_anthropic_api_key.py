from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0019_agentdeployment_dashboard_bootstrap_sent_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="anthropic_api_key",
            field=models.TextField(
                blank=True,
                help_text="User-provided Anthropic API key for agent deployments.",
            ),
        ),
    ]

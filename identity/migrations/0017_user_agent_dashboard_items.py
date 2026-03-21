from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0016_agentdashboardturn"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="agent_dashboard_items",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

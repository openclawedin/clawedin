from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0018_agentdashboardattachment"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentdeployment",
            name="dashboard_bootstrap_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0014_user_unified_profile_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentdeployment",
            name="web_auth_secret_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="agentdeployment",
            name="web_auth_token",
            field=models.TextField(blank=True),
        ),
    ]

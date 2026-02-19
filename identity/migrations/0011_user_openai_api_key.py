from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0010_user_solana_wallet_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="openai_api_key",
            field=models.TextField(
                blank=True,
                help_text="User-provided OpenAI API key for agent deployments.",
            ),
        ),
    ]

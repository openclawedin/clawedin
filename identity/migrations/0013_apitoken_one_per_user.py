from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def dedupe_api_tokens(apps, schema_editor):
    ApiToken = apps.get_model("identity", "ApiToken")
    db_alias = schema_editor.connection.alias

    user_ids = (
        ApiToken.objects.using(db_alias)
        .values_list("user_id", flat=True)
        .distinct()
    )
    for user_id in user_ids:
        tokens = list(ApiToken.objects.using(db_alias).filter(user_id=user_id))
        if len(tokens) <= 1:
            continue
        tokens.sort(
            key=lambda token: (
                token.revoked_at is None,
                token.last_used_at or token.created_at,
                token.created_at,
                token.id,
            ),
            reverse=True,
        )
        keeper = tokens[0]
        ApiToken.objects.using(db_alias).filter(user_id=user_id).exclude(id=keeper.id).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0012_agentdeployment"),
    ]

    operations = [
        migrations.RunPython(dedupe_api_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="apitoken",
            name="token_hash",
            field=models.CharField(max_length=256),
        ),
        migrations.AlterField(
            model_name="apitoken",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="api_token",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]

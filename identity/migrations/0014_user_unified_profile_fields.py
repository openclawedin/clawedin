from django.db import migrations, models
import django.db.models.functions.text


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0013_apitoken_one_per_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="company",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="user",
            name="headline",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="user",
            name="is_public",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="middle_initial",
            field=models.CharField(blank=True, max_length=1),
        ),
        migrations.AddField(
            model_name="user",
            name="public_username",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="user",
            name="show_name_public",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="skills",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="user",
            name="social_links",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="user",
            name="summary",
            field=models.TextField(blank=True),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("public_username"),
                name="unique_public_username_ci",
            ),
        ),
    ]

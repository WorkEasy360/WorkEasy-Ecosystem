# Phase 7 final audit: a staff grant may be time-boxed. Additive and nullable,
# so every existing grant keeps its meaning (no expiry).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("authz", "0005_staffgrant"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffgrant",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

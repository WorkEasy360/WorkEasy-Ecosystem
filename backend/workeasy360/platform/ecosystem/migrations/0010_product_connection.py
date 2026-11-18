# ADR-072: the durable record behind the Accounts Control Center's Connected
# Logins - one row per (person, product, organization), written at the token
# endpoint and touched by the entitlement endpoint.

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    overlap_safety = (
        "A new table only. The previous release neither reads nor writes it; "
        "the release that writes it tolerates it being empty."
    )
    dependencies = [
        ("ecosystem", "0009_product_last_sign_in"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductConnection",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("organization_id", models.UUIDField()),
                (
                    "first_sign_in_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "last_sign_in_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("sign_in_count", models.PositiveIntegerField(default=1)),
                ("last_activity_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "integration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="connections",
                        to="ecosystem.productintegration",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "ecosystem_product_connection",
                "indexes": [
                    models.Index(
                        fields=["last_seen_at"], name="ix_product_connection_seen"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "integration", "organization_id"),
                        name="uq_product_connection",
                    )
                ],
            },
        ),
    ]

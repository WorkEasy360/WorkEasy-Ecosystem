# ADR-074: automatic product sign-in setup - one row per catalog product,
# linked to its one integration, holding the result of the last setup check.

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    overlap_safety = (
        "A new table only. The previous release neither reads nor writes it; "
        "the release that writes it tolerates it being empty (a product with no "
        "row is shown as set up by hand)."
    )
    dependencies = [
        ("ecosystem", "0010_product_connection"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductSignInSetup",
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
                ("product_code", models.CharField(max_length=32, unique=True)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending_configuration", "Pending Configuration"),
                            ("configured", "Configured"),
                            ("verification_required", "Verification Required"),
                            ("active", "Active"),
                            ("failed", "Failed"),
                        ],
                        default="pending_configuration",
                        max_length=32,
                    ),
                ),
                ("checks", models.JSONField(blank=True, default=list)),
                (
                    "application_url",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                (
                    "connector_name",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "connector_version",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                ("key_absences", models.JSONField(blank=True, default=dict)),
                ("requested_by_id", models.UUIDField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("next_check_at", models.DateTimeField(blank=True, null=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "integration",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sign_in_setup",
                        to="ecosystem.productintegration",
                    ),
                ),
            ],
            options={
                "db_table": "ecosystem_product_sign_in_setup",
                "indexes": [
                    models.Index(
                        fields=["state", "next_check_at"], name="ix_sign_in_setup_due"
                    ),
                ],
            },
        ),
    ]

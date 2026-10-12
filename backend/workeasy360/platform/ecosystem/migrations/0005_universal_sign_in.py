# Universal, user-controlled product sign-in (ADR-044).
#
# Additive only: one new table and three nullable/defaulted columns on
# ecosystem_pending_authorization (a small, short-lived table whose rows live
# ten minutes), so the previous release keeps working against this schema.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ecosystem", "0004_authorizationcode_session_facts"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="pendingauthorization",
            name="awaiting_choice",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="pendingauthorization",
            name="user_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pendingauthorization",
            name="authenticated_after",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="UserSignInPreference",
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
                (
                    "product_code",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("auto_sso", "Auto Sso"),
                            ("ask", "Ask"),
                            ("product_login", "Product Login"),
                        ],
                        max_length=16,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
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
                "db_table": "ecosystem_user_sign_in_preference",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "product_code"), name="uq_sign_in_preference"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("mode__in", ["auto_sso", "ask", "product_login"])
                        ),
                        name="ck_sign_in_preference_mode",
                    ),
                ],
            },
        ),
    ]

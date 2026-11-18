# Unified login (ADR-066): "Ask once" becomes a storable sign-in preference.
#
# Schema only - no table, no column. The default ("no row") is decided in code
# (contracts.values.sign_in.DEFAULT_PREFERENCE), so nobody's stored choice
# changes: people who chose Automatic, Ask each time or the product's own
# sign-in keep it.
#
# Rollback: the previous release cannot read an ``ask_once`` row, and the old
# check constraint cannot be added back while one exists. Migrating back to
# 0005 therefore deletes those rows first (the last operation's reverse runs
# first); the people who had picked Ask once explicitly then get that
# release's default again.

from django.conf import settings
from django.db import migrations, models


def _forget_ask_once(apps, schema_editor):
    preference = apps.get_model("ecosystem", "UserSignInPreference")
    preference.objects.filter(mode="ask_once").delete()


class Migration(migrations.Migration):
    overlap_safety = (
        "AlterField only widens the choices of a column that is already NOT NULL "
        "(no null is ever written: every row has a mode), and the new check "
        "constraint admits every value the previous release writes. The previous "
        "release reads an ask_once row only if someone picks it during the "
        "overlap; the reverse migration deletes those rows (see the header)."
    )
    dependencies = [
        ("ecosystem", "0005_universal_sign_in"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="usersigninpreference",
            name="ck_sign_in_preference_mode",
        ),
        migrations.AlterField(
            model_name="usersigninpreference",
            name="mode",
            field=models.CharField(
                choices=[
                    ("ask_once", "Ask Once"),
                    ("auto_sso", "Auto Sso"),
                    ("ask", "Ask"),
                    ("product_login", "Product Login"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="usersigninpreference",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("mode__in", ["ask_once", "auto_sso", "ask", "product_login"])
                ),
                name="ck_sign_in_preference_mode",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, _forget_ask_once),
    ]

# ADR-072: the Accounts Control Center's Login History and profile editor.
#
# Additive only: two more choices on a column the database does not check.

from django.db import migrations, models


class Migration(migrations.Migration):
    overlap_safety = (
        "The AlterField only adds choices to a column that is already NOT NULL; "
        "choices are not enforced by the database, so nothing the previous release "
        "writes is refused, and it shows the three new account events by their value."
    )
    dependencies = [
        ("identity", "0009_email_change"),
    ]

    operations = [
        migrations.AlterField(
            model_name="accountevent",
            name="kind",
            field=models.CharField(
                choices=[
                    ("sign_in", "Signed in"),
                    ("sign_in_failed", "Sign-in failed"),
                    ("password_changed", "Password changed"),
                    ("session_revoked", "Session signed out"),
                    ("other_sessions_revoked", "Other sessions signed out"),
                    ("signed_out_everywhere", "Signed out everywhere"),
                    ("account_disabled", "Account disabled"),
                    ("account_enabled", "Account enabled"),
                    ("account_unlocked", "Account unlocked"),
                    ("email_verified", "Email address verified"),
                    ("password_reset", "Password reset by email"),
                    ("sign_in_preference_changed", "Product sign-in preference changed"),
                    ("email_change_requested", "Email change requested"),
                    ("email_changed", "Sign-in email changed"),
                    ("product_sign_in", "Signed in to a product"),
                    ("product_signed_out", "Signed out of a product"),
                    ("profile_updated", "Profile updated"),
                ],
                max_length=32,
            ),
        ),
    ]

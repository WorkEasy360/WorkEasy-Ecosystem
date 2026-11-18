# Changing the sign-in email (ADR-066).
#
# Additive only: one column with a database default (so the previous release,
# which does not know it, still inserts tokens) and two longer choice lists,
# which the database does not check. The previous release keeps working
# against this schema; it shows the two new account events by their value.

from django.db import migrations, models


class Migration(migrations.Migration):
    overlap_safety = (
        "Both AlterFields only add choices to columns that are already NOT NULL; "
        "choices are not enforced by the database, so nothing the previous release "
        "writes is refused. The new column has a database default, so the previous "
        "release's INSERTs (signup, reset) still succeed."
    )
    dependencies = [
        ("identity", "0008_password_change_required"),
    ]

    operations = [
        migrations.AddField(
            model_name="accounttoken",
            name="new_email",
            field=models.EmailField(
                blank=True, db_default="", default="", max_length=254
            ),
        ),
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
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="accounttoken",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("verify_email", "Verify email address"),
                    ("reset_password", "Reset password"),
                    ("change_email", "Change sign-in email"),
                ],
                max_length=16,
            ),
        ),
    ]

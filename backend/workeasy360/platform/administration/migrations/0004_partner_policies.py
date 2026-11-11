"""ADR-063: the two partner policies the Admin decides (legacy KYC, suspended
commission). Defaults keep today's behaviour, so applying this changes nothing."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0003_help_and_company"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformsettings",
            name="partner_legacy_kyc_policy",
            field=models.CharField(db_default="allow", default="allow", max_length=16),
        ),
        migrations.AddField(
            model_name="platformsettings",
            name="partner_suspended_commission_policy",
            field=models.CharField(db_default="accrue", default="accrue", max_length=16),
        ),
        migrations.AddConstraint(
            model_name="platformsettings",
            constraint=models.CheckConstraint(
                condition=models.Q(partner_legacy_kyc_policy__in=["allow", "restrict"]),
                name="ck_platform_settings_legacy_kyc",
            ),
        ),
        migrations.AddConstraint(
            model_name="platformsettings",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    partner_suspended_commission_policy__in=["accrue", "stop"]
                ),
                name="ck_platform_settings_suspended_commission",
            ),
        ),
    ]

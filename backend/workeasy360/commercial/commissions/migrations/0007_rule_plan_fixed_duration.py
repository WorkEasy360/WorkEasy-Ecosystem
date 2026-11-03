"""ADR-056: plan-specific, fixed-amount and limited-duration commission rules.

A rule version is a commercial fact, frozen by trigger once written (every
column but ``retired_at``). The new columns join the frozen set, and so does
``recurring``, which ADR-052 added without extending the trigger.
"""

from django.db import migrations, models

from workeasy360.platform.tenancy.migrations_helpers import freeze_columns

_FROZEN_BEFORE = [
    "code",
    "version",
    "partner_id",
    "product_code",
    "rate_bp",
    "effective_from",
    "reason",
    "created_by_id",
    "created_at",
]
_FROZEN_AFTER = [
    *_FROZEN_BEFORE,
    "recurring",
    "plan_code",
    "fixed_amount_minor",
    "fixed_currency",
    "recurring_months",
]


class Migration(migrations.Migration):

    dependencies = [
        ('commissions', '0006_affiliate_products'),
        ('tenancy', '0006_partner_channel_controls'),
    ]

    operations = [
        migrations.AddField(
            model_name='commissionrule',
            name='fixed_amount_minor',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commissionrule',
            name='fixed_currency',
            field=models.CharField(blank=True, db_default='', default='', max_length=3),
        ),
        migrations.AddField(
            model_name='commissionrule',
            name='plan_code',
            field=models.CharField(blank=True, db_default='', default='', max_length=64),
        ),
        migrations.AddField(
            model_name='commissionrule',
            name='recurring_months',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.RunSQL(
            sql=freeze_columns("commissions_rule", columns=_FROZEN_AFTER).sql,
            reverse_sql=freeze_columns("commissions_rule", columns=_FROZEN_BEFORE).sql,
            elidable=False,
        ),
    ]

"""ADR-056: a price row carries its promotional discount and set-up fee.

Both are commercial terms of a published price, so the immutability trigger
freezes them exactly like ``amount_minor``: a discount that starts or ends, or a
new set-up fee, is a new price row with its own window, and a past order stays
reproducible. ``db_default`` keeps old rows (and old code during a rolling
deploy) valid: no discount, no fee.
"""

from django.db import migrations, models

from workeasy360.platform.tenancy.migrations_helpers import (
    make_immutable_when_published,
)

_FROZEN_BEFORE = [
    "price_book_id",
    "plan_version_id",
    "bundle_version_id",
    "interval",
    "model",
    "amount_minor",
    "min_quantity",
    "max_quantity",
    "tax_behaviour",
    "valid_from",
]
_FROZEN_AFTER = [*_FROZEN_BEFORE, "discount_bp", "setup_fee_minor"]


class Migration(migrations.Migration):
    dependencies = [
        ("pricing", "0003_semi_annual_interval"),
        ("tenancy", "0003_commercial_isolation"),
    ]

    operations = [
        migrations.AddField(
            model_name="planprice",
            name="discount_bp",
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="planprice",
            name="setup_fee_minor",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="planprice",
            constraint=models.CheckConstraint(
                condition=models.Q(discount_bp__lte=10000),
                name="ck_price_discount_at_most_100pct",
            ),
        ),
        migrations.AddConstraint(
            model_name="planprice",
            constraint=models.CheckConstraint(
                condition=models.Q(setup_fee_minor__isnull=True)
                | models.Q(setup_fee_minor__gte=0),
                name="ck_price_setup_fee_non_negative",
            ),
        ),
        migrations.RunSQL(
            sql=make_immutable_when_published(
                "pricing_plan_price", columns=_FROZEN_AFTER
            ).sql,
            reverse_sql=make_immutable_when_published(
                "pricing_plan_price", columns=_FROZEN_BEFORE
            ).sql,
            elidable=False,
        ),
    ]

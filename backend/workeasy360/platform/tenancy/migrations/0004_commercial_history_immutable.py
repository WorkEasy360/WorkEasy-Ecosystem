"""F5, applied to the commercial record itself.

``tenancy.0003`` made published catalog and pricing rows immutable by trigger.
It did not protect the subscription rows that *quote* them, and the
denormalised snapshot on a subscription item is exactly the copy those
triggers exist to make redundant. A plain ``QuerySet.update()`` could rewrite
``unit_amount_minor``, ``currency`` or the pinned ``plan_version_id`` of a line
a customer agreed to months ago - retroactively changing what they bought,
with the immutable catalog row still sitting there proving something else.

The currency case is the sharpest. ``Subscription.currency`` is fixed at
creation from the billing account and every item's snapshot is denominated in
it. Changing one without the other leaves a subscription whose lines are
priced in a currency it does not settle in, which is a billing incident that
reconciliation cannot detect because both values look internally consistent.

Columns left mutable are the ones the lifecycle genuinely moves: a
subscription's status, period, trial and cancellation timestamps and its
optimistic ``version``; an item's ``removed_at``, which is how a line is
closed, and ``updated_at``.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import freeze_columns

#: The commercial facts a subscription line records. ``quantity`` is included
#: because ``total_amount_minor`` is derived from it: changing one without the
#: other produces a line whose arithmetic does not hold. A quantity change is
#: a new line, which is the mechanism the architecture already uses for every
#: other change to a line.
ITEM_COLUMNS = [
    "organization_id",
    "subscription_id",
    "parent_item_id",
    "plan_version_id",
    "bundle_version_id",
    "product_code",
    "plan_code",
    "plan_version_number",
    "bundle_code",
    "seat_pool",
    "quantity",
    "price_id",
    "price_source",
    "unit_amount_minor",
    "total_amount_minor",
    "currency",
    "interval",
    "price_model",
    "tax_behaviour",
    "added_at",
]

#: Currency is a settlement fact, not a preference. Mid-life currency
#: switching is a cancel-and-resubscribe, which the model docstring already
#: states and which nothing enforced.
SUBSCRIPTION_COLUMNS = [
    "organization_id",
    "currency",
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0003_commercial_isolation"),
    ]

    operations = [
        freeze_columns("subscriptions_item", columns=ITEM_COLUMNS),
        freeze_columns("subscriptions_subscription", columns=SUBSCRIPTION_COLUMNS),
    ]

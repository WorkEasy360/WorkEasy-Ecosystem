"""Isolation and immutability for the Phase 2 commercial tables.

Three jobs, in the same shape Phase 1 established:

**Layer 3 - RLS** on every new tenant table. A tenant table without a policy
has only application filtering, which is the layer that fails first.

**Layer 4 - composite foreign keys** on every new tenant-to-tenant reference.
RLS stops you *reading* another tenant's row; it does not stop you writing a
row in your own tenant that points at one, if you somehow obtained the id
(R-05).

**F5 - immutability** of published catalog and pricing records, enforced by
database triggers. The application guard raises a clear error; the trigger is
what survives a data-fix script, the admin and a bulk update. Editing a
published plan retroactively changes what every existing customer bought, and
editing a published price makes an existing invoice irreproducible.

Control-plane tables get DML grants but no policy: they carry no
``organization_id``, so there is nothing for a policy to match on, and
granting one would be security theatre.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    add_tenant_fk,
    enable_rls,
    freeze_children_of_published_parent,
    grant_app_role,
    make_immutable_when_published,
)

#: New ``TenantScopedModel`` tables.
TENANT_TABLES = [
    "billing_account",
    "idempotency_record",
    "pricing_contract_price",
    "subscriptions_item",
    "subscriptions_subscription",
]

#: New control-plane tables. Global reference data, no tenancy.
CONTROL_PLANE_TABLES = [
    "catalog_bundle",
    "catalog_bundle_item",
    "catalog_bundle_version",
    "catalog_feature",
    "catalog_plan",
    "catalog_plan_feature",
    "catalog_plan_version",
    "catalog_product",
    "pricing_plan_price",
    "pricing_price_book",
    "pricing_price_tier",
]

#: Tenant-to-tenant references. The parents already expose
#: ``UNIQUE (id, organization_id)`` as model constraints, which is what a
#: composite foreign key needs to point at.
COMPOSITE_FKS = [
    {
        "table": "subscriptions_item",
        "column": "subscription_id",
        "references_table": "subscriptions_subscription",
    },
    {
        # A bundle's derived lines hang off its priced line. Without the
        # composite constraint, a line could name a parent in another tenant.
        "table": "subscriptions_item",
        "column": "parent_item_id",
        "references_table": "subscriptions_item",
    },
]

#: Columns that may never change once the record is published. ``effective_to``
#: and ``valid_to`` are deliberately absent from every list: closing a record
#: is how supersession is recorded, and it is the one edit a published record
#: must still accept.
IMMUTABLE_AFTER_PUBLISH = [
    {
        "table": "catalog_plan_version",
        "columns": [
            "plan_id",
            "version",
            "effective_from",
            "included_seats",
            "max_seats",
            "trial_days",
        ],
    },
    {
        "table": "catalog_bundle_version",
        "columns": ["bundle_id", "version", "effective_from"],
    },
    {
        "table": "pricing_plan_price",
        "columns": [
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
        ],
    },
]

#: Children whose parent's publication freezes them too. A plan version whose
#: terms are immutable but whose feature rows are not is not immutable at all:
#: its identity is unchanged while what it grants is rewritten.
FROZEN_CHILDREN = [
    {
        "table": "catalog_plan_feature",
        "parent_table": "catalog_plan_version",
        "parent_fk_column": "plan_version_id",
    },
    {
        "table": "catalog_bundle_item",
        "parent_table": "catalog_bundle_version",
        "parent_fk_column": "bundle_version_id",
    },
    {
        "table": "pricing_price_tier",
        "parent_table": "pricing_plan_price",
        "parent_fk_column": "plan_price_id",
    },
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0002_composite_fk_coverage"),
        ("billing", "0001_initial"),
        ("catalog", "0001_initial"),
        ("idempotency", "0001_initial"),
        ("pricing", "0001_initial"),
        ("subscriptions", "0001_initial"),
    ]

    operations = [
        *[enable_rls(table) for table in TENANT_TABLES],
        *[grant_app_role(table) for table in CONTROL_PLANE_TABLES],
        *[add_tenant_fk(**fk) for fk in COMPOSITE_FKS],
        *[make_immutable_when_published(**spec) for spec in IMMUTABLE_AFTER_PUBLISH],
        *[freeze_children_of_published_parent(**spec) for spec in FROZEN_CHILDREN],
    ]

"""Isolation and immutability for the Phase 8 extension's commercial tables.

**Layer 3 - RLS** on an organization's invoices, their lines, its payment
attempts and refunds.

**Layer 4 - composite foreign keys**, so a line naming another organization's
invoice, an attempt paying another organization's invoice, a refund against
another organization's payment, or an invoice for another organization's
subscription is unrepresentable.

**Control plane** - grants, no policy - for the platform's payment provider
accounts and the log of provider callbacks, plus the invoice-number sequence.

**Immutability**: a published bundle version's new trial length joins the
columns that freeze on publication (the trigger is redefined with the longer
list; the reverse restores the Phase 2 definition).
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    add_tenant_fk,
    enable_rls,
    grant_app_role,
    make_immutable_when_published,
)

TENANT_TABLES = [
    "payments_invoice",
    "payments_invoice_line",
    "payments_attempt",
    "payments_refund",
]

CONTROL_PLANE_TABLES = ["payments_provider", "payments_webhook_event"]

COMPOSITE_FKS = [
    {
        "table": "payments_invoice",
        "column": "subscription_id",
        "references_table": "subscriptions_subscription",
        "on_delete": "RESTRICT",
    },
    {
        "table": "payments_invoice_line",
        "column": "invoice_id",
        "references_table": "payments_invoice",
    },
    {
        "table": "payments_attempt",
        "column": "invoice_id",
        "references_table": "payments_invoice",
        "on_delete": "RESTRICT",
    },
    {
        "table": "payments_refund",
        "column": "attempt_id",
        "references_table": "payments_attempt",
        "on_delete": "RESTRICT",
    },
]

_BUNDLE_VERSION_FROZEN_BEFORE = ["bundle_id", "version", "effective_from"]
_BUNDLE_VERSION_FROZEN_AFTER = [*_BUNDLE_VERSION_FROZEN_BEFORE, "trial_days"]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0009_console_isolation"),
        ("payments", "0001_payments"),
        ("catalog", "0002_plan_builder"),
    ]

    operations = [
        *[enable_rls(table) for table in TENANT_TABLES],
        *[grant_app_role(table) for table in CONTROL_PLANE_TABLES],
        migrations.RunSQL(
            sql="GRANT USAGE, SELECT ON SEQUENCE payments_invoice_number_seq TO w360_app;",
            reverse_sql="REVOKE ALL ON SEQUENCE payments_invoice_number_seq FROM w360_app;",
            elidable=False,
        ),
        *[add_tenant_fk(**fk) for fk in COMPOSITE_FKS],
        migrations.RunSQL(
            sql=make_immutable_when_published(
                "catalog_bundle_version", columns=_BUNDLE_VERSION_FROZEN_AFTER
            ).sql,
            reverse_sql=make_immutable_when_published(
                "catalog_bundle_version", columns=_BUNDLE_VERSION_FROZEN_BEFORE
            ).sql,
            elidable=False,
        ),
    ]

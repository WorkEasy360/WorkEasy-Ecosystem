"""Isolation for Phase 9's tables: orders, the staff order projection,
account links and platform settings.

**Layer 3 - RLS** on an organization's orders (FORCE, USING, WITH CHECK).

**Layer 4 - composite foreign keys**, so an order naming another
organization's subscription, or an invoice naming another organization's
order, is unrepresentable.

**Control plane** - grants, no policy - for the staff order projection
(``payments_order_summary``, debt register D-031), a person's one-time account
links (``identity_account_token``: global like the account itself, reached only
by the SHA-256 of the token) and the platform settings singleton; and the
order-number sequence.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    add_tenant_fk,
    enable_rls,
    grant_app_role,
)

CONTROL_PLANE_TABLES = [
    "payments_order_summary",
    "identity_account_token",
    "administration_platform_settings",
]

COMPOSITE_FKS = [
    {
        "table": "payments_order",
        "column": "subscription_id",
        "references_table": "subscriptions_subscription",
        "on_delete": "RESTRICT",
    },
    {
        "table": "payments_invoice",
        "column": "order_id",
        "references_table": "payments_order",
        "on_delete": "RESTRICT",
    },
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0010_payments_isolation"),
        ("payments", "0002_orders"),
        ("identity", "0004_self_service_accounts"),
        ("administration", "0001_platform_settings"),
    ]

    operations = [
        enable_rls("payments_order"),
        *[grant_app_role(table) for table in CONTROL_PLANE_TABLES],
        migrations.RunSQL(
            sql="GRANT USAGE, SELECT ON SEQUENCE payments_order_number_seq TO w360_app;",
            reverse_sql="REVOKE ALL ON SEQUENCE payments_order_number_seq FROM w360_app;",
            elidable=False,
        ),
        *[add_tenant_fk(**fk) for fk in COMPOSITE_FKS],
    ]

"""Database-level controls for the Phase 7 control-plane tables.

**Classification: every table below is control-plane, not tenant-scoped**
(ADR-027/028/029). None gets a customer-tenant RLS policy - see the module
docstrings of ``partners.models`` and ``commissions.models`` for why that would
be wrong - so this migration does the two jobs that *do* apply:

**Grants.** The application role receives DML on each new table. The one
exception is the commission ledger, which is made **append-only**: UPDATE,
DELETE and TRUNCATE are revoked from the role and a trigger raises on either,
so the ledger cannot be rewritten by a bug, a data-fix script or a compromised
application (F2, Task 41).

**Immutability of facts.** The columns whose whole value is that they cannot
change after the row exists are frozen by trigger, with only the deliberately
mutable ones left out:

- a commission rule is a version - only ``retired_at`` may move;
- a payout's snapshotted totals and identity never change, only its lifecycle;
- a payout line's amount and ledger reference never change, only its release;
- a grant's subject and terms never change, only its revocation stamps.

Deleting is not blocked for these (retention prunes some); the ledger's own
trigger covers deletion for the one table where it is never permitted.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    freeze_columns,
    grant_app_role,
    make_append_only,
)

CONTROL_PLANE_TABLES = [
    "authz_staff_grant",
    "partners_account",
    "partners_brand",
    "partners_capability_grant",
    "partners_domain",
    "partners_logo_upload",
    "partners_member",
    "partners_organization_link",
    "partners_owner_invite",
    "partners_product_grant",
    "provisioning_customer_summary",
    "commissions_payout",
    "commissions_payout_line",
    "commissions_referral",
    "commissions_referral_code",
    "commissions_rule",
]

FROZEN = [
    {
        "table": "commissions_rule",
        "columns": [
            "code",
            "version",
            "partner_id",
            "product_code",
            "rate_bp",
            "effective_from",
            "reason",
            "created_by_id",
            "created_at",
        ],
    },
    {
        "table": "commissions_payout",
        "columns": [
            "partner_id",
            "currency",
            "period_end",
            "gross_minor",
            "adjustments_minor",
            "payable_minor",
            "line_count",
            "created_by_id",
            "created_at",
            "idempotency_key",
        ],
    },
    {
        "table": "commissions_payout_line",
        "columns": ["payout_id", "accrual_id", "amount_minor"],
    },
    {
        "table": "authz_staff_grant",
        "columns": ["user_id", "permission", "granted_by_id", "granted_at", "reason"],
    },
    {
        "table": "partners_capability_grant",
        "columns": [
            "partner_id",
            "capability",
            "granted_by_id",
            "granted_at",
            "expires_at",
            "reason",
        ],
    },
    {
        "table": "partners_product_grant",
        "columns": ["partner_id", "product_id", "plan_id", "granted_by_id", "granted_at"],
    },
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0005_integration_isolation"),
        ("authz", "0005_staffgrant"),
        ("partners", "0001_initial"),
        ("commissions", "0002_initial"),
        ("provisioning", "0001_initial"),
        ("audit", "0003_alter_auditevent_actor_kind"),
        ("pricing", "0002_pricebook_partner_id_pricebook_ix_pricebook_partner_and_more"),
    ]

    operations = [
        *[grant_app_role(table) for table in CONTROL_PLANE_TABLES],
        make_append_only("commissions_accrual"),
        *[freeze_columns(spec["table"], columns=spec["columns"]) for spec in FROZEN],
    ]

"""Grants for the wallets and partner KYC tables (2026-11-09).

**Control plane** - grants, no policy: a wallet's owner is an organization or a
partner, and a partner's application belongs to no customer at all.

**Append-only by grant and trigger**, because tampering with these must be impossible
rather than merely difficult: a wallet's ledger, every KYC decision, and every time
staff opened a KYC document.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    grant_app_role,
    make_append_only,
)

MUTABLE = [
    "wallets_wallet",
    "wallets_usage_rate",
    "wallets_credit_pack",
    "wallets_usage_event",
    "partners_kyc_requirement",
    "partners_kyc",
    "partners_kyc_document",
]

APPEND_ONLY = [
    "wallets_entry",
    "partners_kyc_decision",
    "partners_kyc_document_access",
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0013_admin_projections"),
        ("wallets", "0001_admin_panel"),
        ("partners", "0017_admin_panel"),
    ]

    operations = [
        *[grant_app_role(table) for table in MUTABLE],
        *[make_append_only(table) for table in APPEND_ONLY],
    ]

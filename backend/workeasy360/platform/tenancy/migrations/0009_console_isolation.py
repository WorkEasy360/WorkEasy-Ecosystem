"""Isolation Layers 3 and 4 for the Phase 8 extension's console tables.

Same jobs, same helpers, as ``0001`` through ``0008``:

**Layer 3 - RLS** on the one new ``TenantScopedModel`` table: an
organization's sign-in activity (``organizations_member_sign_in``). An
administrator reading "most signed-in users" reads only their own tenant's
rows - enforced by the database, not by the query.

**Layer 4 - a composite foreign key** from that activity to its membership, so
a sign-in row naming another organization's member is unrepresentable.

The browser-session registry, a person's security trail and the platform's
communication settings are **control plane** - they belong to a global
account or to no organization - and get grants, no policy. Every access to
them is keyed by the authenticated user's own id or gated on a staff grant.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    add_tenant_fk,
    enable_rls,
    grant_app_role,
)

TENANT_TABLES = ["organizations_member_sign_in"]

CONTROL_PLANE_TABLES = [
    "identity_session",
    "identity_account_event",
    "notifications_template_override",
    "notifications_communication_settings",
]

COMPOSITE_FKS = [
    {
        "table": "organizations_member_sign_in",
        "column": "membership_id",
        "references_table": "organizations_membership",
    },
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0008_intelligence_isolation"),
        ("identity", "0003_session_registry_and_account_events"),
        ("organizations", "0005_console_access_policy_and_invitations"),
        ("notifications", "0002_sealed_context_and_communication"),
    ]

    operations = [
        *[enable_rls(table) for table in TENANT_TABLES],
        *[grant_app_role(table) for table in CONTROL_PLANE_TABLES],
        *[add_tenant_fk(**fk) for fk in COMPOSITE_FKS],
    ]

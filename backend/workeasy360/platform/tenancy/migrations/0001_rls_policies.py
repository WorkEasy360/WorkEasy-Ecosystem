"""Isolation Layers 3 and 4, applied to every tenant table.

This migration is what makes tenant isolation survive an application bug.
Everything above it - the scoped manager, the middleware, code review - can
fail; this cannot, because PostgreSQL enforces it below the application, for
raw SQL and ad-hoc scripts as well as for the ORM.

It runs last (it depends on every app's initial migration) because it must
see all the tables it protects.

Adding a tenant model without adding it here fails the W360.E002 system check,
which runs in CI and at container start.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    add_tenant_fk,
    add_tenant_unique_key,
    enable_rls,
    enable_system_template_rls,
    grant_app_role,
    make_append_only,
)

#: Every TenantScopedModel table. Order is irrelevant for policies, but the
#: list is kept alphabetical so a diff against the model inventory is easy.
TENANT_TABLES = [
    "audit_event",
    "authz_permission_set_version",
    "authz_role_assignment",
    "entitlements_entitlement",
    "entitlements_feature",
    "entitlements_grant",
    "entitlements_seat_assignment",
    "entitlements_seat_grant",
    "entitlements_snapshot",
    "files_file",
    "notifications_delivery",
    "notifications_notification",
    "notifications_preference",
    "organizations_invitation",
    "organizations_membership",
]

#: Control-plane tables: no organization_id, so RLS does not apply. They still
#: need DML grants because the app role owns nothing.
CONTROL_PLANE_TABLES = [
    "identity_user",
    "identity_auth_factor",
    "organizations_organization",
    "authz_permission",
    "authz_role_permission",
    "events_outbox",
    "events_processed",
    "events_dead_letter",
    "django_session",
    "django_content_type",
    "auth_permission",
    "auth_group",
    "auth_group_permissions",
    "django_migrations",
]

#: Composite foreign keys (Layer 4). Each makes a cross-tenant reference
#: structurally impossible rather than merely forbidden: the child's own
#: organization_id must equal the parent's, so the database rejects the write
#: regardless of what the application believes (R-05).
#:
#: Only FKs between two tenant tables need this. A FK to Organization or User
#: cannot be cross-tenant by construction.
COMPOSITE_FKS = [
    {
        "table": "authz_role_assignment",
        "column": "membership_id",
        "references_table": "organizations_membership",
    },
    {
        "table": "entitlements_seat_assignment",
        "column": "membership_id",
        "references_table": "organizations_membership",
    },
    {
        "table": "entitlements_seat_assignment",
        "column": "seat_grant_id",
        "references_table": "entitlements_seat_grant",
    },
    {
        "table": "notifications_notification",
        "column": "recipient_membership_id",
        "references_table": "organizations_membership",
    },
    {
        "table": "notifications_delivery",
        "column": "notification_id",
        "references_table": "notifications_notification",
    },
    {
        "table": "notifications_preference",
        "column": "membership_id",
        "references_table": "organizations_membership",
    },
    {
        "table": "files_file",
        "column": "uploaded_by_id",
        "references_table": "organizations_membership",
        "on_delete": "SET NULL",
    },
]

#: Parent tables that must expose UNIQUE (id, organization_id) so the
#: composite FKs above have a target to reference.
COMPOSITE_FK_TARGETS = [
    "notifications_notification",
]


class Migration(migrations.Migration):
    """Applied after every app that owns a tenant table."""

    initial = False
    atomic = True

    dependencies = [
        ("audit", "0001_initial"),
        ("authz", "0001_initial"),
        ("entitlements", "0001_initial"),
        ("files", "0001_initial"),
        ("identity", "0001_initial"),
        ("notifications", "0001_initial"),
        ("organizations", "0001_initial"),
        ("events", "0001_initial"),
    ]

    operations = [
        # --- Layer 3: RLS on every tenant table ---
        *[enable_rls(table) for table in TENANT_TABLES],

        # authz_role holds both global system templates and tenant-owned
        # custom roles (debt register D-001). An asymmetric policy lets every
        # tenant read system templates while forbidding the application role
        # from writing a NULL-tenant row at all.
        enable_system_template_rls("authz_role"),

        # --- Control-plane grants ---
        *[grant_app_role(table) for table in CONTROL_PLANE_TABLES],

        # --- Layer 4: composite foreign keys ---
        *[add_tenant_unique_key(table) for table in COMPOSITE_FK_TARGETS],
        *[add_tenant_fk(**fk) for fk in COMPOSITE_FKS],

        # --- Append-only enforcement, at the database ---
        # Audit is a business and legal record. UPDATE and DELETE are revoked
        # from the application role and a trigger backs that up, so an
        # ORM-level guard is not the only thing standing between a data-fix
        # script and a rewritten audit trail (11-observability.md section 9).
        make_append_only("audit_event"),
    ]

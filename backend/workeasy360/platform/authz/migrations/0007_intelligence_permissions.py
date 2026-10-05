"""Phase 8: grant the AI and analytics permissions to the owner and admin roles.

Identical mechanism, and identical reasons, to ``0004_integration_permissions``:
an upgrade that changes a system role's permissions ships a migration (Phase 5
frozen decision 5), the spec is frozen here rather than read from the live
registry, ``authz_role`` carries the D-001 asymmetric RLS policy so the
migrator briefly lifts ``FORCE`` on it, and re-running converges.

Only the three organization-scoped keys are granted. The two
``platform.staff_ai.*`` keys are control-plane permissions: no role holds them,
and they are reachable only through a ``StaffGrant`` (ADR-027).
"""

from __future__ import annotations

import uuid

from django.db import migrations

#: (key, label, category, dangerous, implies) as declared in Phase 8.
PERMISSIONS = [
    (
        "platform.ai.view",
        "View the organization's AI settings, limits and usage",
        "ai",
        False,
        [],
    ),
    (
        "platform.ai.manage",
        "Turn AI on or off, set its monthly budget and restrict its providers",
        "ai",
        True,
        ["platform.ai.view"],
    ),
    (
        "platform.analytics.view",
        "Query analytics for the products the organization holds",
        "analytics",
        False,
        [],
    ),
]

ROLES = ("owner", "admin")


def grant(apps, schema_editor):
    Permission = apps.get_model("authz", "Permission")
    Role = apps.get_model("authz", "Role")
    RolePermission = apps.get_model("authz", "RolePermission")

    for key, label, category, dangerous, implies in PERMISSIONS:
        Permission.objects.update_or_create(
            key=key,
            defaults={
                "label": label,
                "product": key.split(".", 1)[0],
                "category": category,
                "dangerous": dangerous,
                "implies": implies,
            },
        )

    for role in Role.objects.filter(key__in=ROLES, organization__isnull=True, is_system=True):
        existing = set(
            RolePermission.objects.filter(role=role).values_list("permission_id", flat=True)
        )
        for key, *_ in PERMISSIONS:
            if key not in existing:
                RolePermission.objects.create(id=uuid.uuid4(), role=role, permission_id=key)


def revoke(apps, schema_editor):
    RolePermission = apps.get_model("authz", "RolePermission")
    RolePermission.objects.filter(
        role__key__in=ROLES,
        role__organization__isnull=True,
        permission_id__in=[key for key, *_ in PERMISSIONS],
    ).delete()
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):
    dependencies = [("authz", "0006_staffgrant_expires_at")]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE authz_role NO FORCE ROW LEVEL SECURITY;",
            reverse_sql="ALTER TABLE authz_role FORCE ROW LEVEL SECURITY;",
            elidable=False,
        ),
        migrations.RunPython(grant, revoke, elidable=False),
        migrations.RunSQL(
            sql="SET CONSTRAINTS ALL IMMEDIATE;",
            reverse_sql=migrations.RunSQL.noop,
            elidable=False,
        ),
        migrations.RunSQL(
            sql="ALTER TABLE authz_role FORCE ROW LEVEL SECURITY;",
            reverse_sql="ALTER TABLE authz_role NO FORCE ROW LEVEL SECURITY;",
            elidable=False,
        ),
    ]

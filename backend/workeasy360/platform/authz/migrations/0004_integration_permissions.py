"""Phase 6: grant the integration permissions to the owner and admin roles.

An upgrade that changes a system role's permissions ships a migration (Phase 5
frozen decision 5), because the ``post_migrate`` sync only ever *adds* and a
database migrated before Phase 6 has system roles without these keys.

**The spec is frozen here**, not read from ``SYSTEM_ROLES`` or the live
permission registry - the Phase 5 audit recorded that ``0002`` imports both
and so breaks if either is renamed. This migration names exactly the five keys
it grants and their declarations as of Phase 6.

Same mechanism as ``0002``: ``authz_role`` carries the D-001 asymmetric RLS
policy, which no runtime role can write a system-role row through, so the
migrator briefly lifts ``FORCE`` (it owns the table), writes, and restores it.
Idempotent: re-running converges.

Cached permission sets need no bump here: the cache key carries a
fingerprint of the system role catalog (``authz.services._catalog_generation``),
so processes running this code never read a set cached under the old one.
"""

from __future__ import annotations

import uuid

from django.db import migrations

#: (key, label, category, dangerous, implies) as declared in Phase 6.
PERMISSIONS = [
    (
        "platform.connection.view",
        "View integrations, webhook subscriptions and deliveries",
        "integrations",
        False,
        [],
    ),
    (
        "platform.connection.manage",
        "Create, change, rotate and disable integrations and subscriptions",
        "integrations",
        True,
        ["platform.connection.view"],
    ),
    (
        "platform.webhook.replay",
        "Replay failed webhook deliveries",
        "integrations",
        False,
        ["platform.connection.view"],
    ),
    (
        "platform.automation.view",
        "View automation rules and their executions",
        "automation",
        False,
        [],
    ),
    (
        "platform.automation.manage",
        "Create, change, activate and pause automation rules",
        "automation",
        True,
        ["platform.automation.view"],
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
    dependencies = [("authz", "0003_backfill_owner_role_assignments")]

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

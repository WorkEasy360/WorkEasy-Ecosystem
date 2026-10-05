"""Phase 8 extension: grant ``platform.member.sign_out`` to the owner and admin
roles, and ``commercial.subscription.view`` to the billing manager.

The same mechanism, for the same reasons, as ``0004`` and ``0007``: a change to
what a system role grants ships as a migration with the spec frozen here,
``authz_role``'s D-001 policy is briefly lifted for the migrator, and re-running
converges.

The other permissions the console now enforces (``platform.member.invite``,
``.update``, ``platform.role.assign``, ``platform.audit.view``,
``platform.security.view``/``.update``, ``platform.organization.update``,
``platform.billing.*``) were declared - and granted to these roles - in Phase 1;
only their enforcement is new. The new ``platform.staff_*`` keys are
control-plane permissions held through ``StaffGrant`` rows, never by a role.

The billing manager ("billing and subscriptions only", 04-identity-authz.md)
pays invoices from the console's billing page, which shows the subscription
being paid for; until now the role could not read it. View only: buying,
changing and cancelling (``commercial.subscription.manage``) stay the owner's
unless an organization gives them to a custom role.
"""

from __future__ import annotations

import uuid

from django.db import migrations

#: (key, label, category, dangerous, implies) as declared by this extension.
PERMISSIONS = [
    (
        "platform.member.sign_out",
        "Sign a member out of the organization",
        "members",
        True,
        ["platform.member.view"],
    ),
]

#: role key -> the permissions this migration grants it.
GRANTS = {
    "owner": ("platform.member.sign_out",),
    "admin": ("platform.member.sign_out",),
    "billing_manager": ("commercial.subscription.view",),
}


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

    for role in Role.objects.filter(key__in=GRANTS, organization__isnull=True, is_system=True):
        existing = set(
            RolePermission.objects.filter(role=role).values_list("permission_id", flat=True)
        )
        for key in GRANTS[role.key]:
            if key not in existing:
                RolePermission.objects.create(id=uuid.uuid4(), role=role, permission_id=key)


def revoke(apps, schema_editor):
    RolePermission = apps.get_model("authz", "RolePermission")
    for role_key, keys in GRANTS.items():
        RolePermission.objects.filter(
            role__key=role_key,
            role__organization__isnull=True,
            permission_id__in=keys,
        ).delete()
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):
    dependencies = [("authz", "0007_intelligence_permissions")]

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

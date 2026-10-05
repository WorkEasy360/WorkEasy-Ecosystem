"""Give every existing organization owner the system ``owner`` role.

Phase 5 makes "the owner membership holds the owner role" an invariant of
organization creation (``platform.onboarding.services.create_organization``).
Organizations that existed before it have ``Membership.is_owner`` set and no
role assignment at all - and permissions come only from role assignments -
so after an upgrade their owners could not even list their own members.
Found by the Phase 5 upgrade rehearsal (every pre-existing owner: 403).

Grants exactly what creation grants, to exactly the membership already
flagged as owner (``uq_one_owner_per_org`` guarantees one per organization),
and only where no owner-role assignment exists - idempotent, never a second
grant. ``authz_role_assignment`` has FORCE ROW LEVEL SECURITY, so the tenant
variable is bound per organization inside this migration's transaction.

Reversible as a no-op: an owner keeping the owner role is the correct state
for a rolled-back Phase 5 too.
"""

from __future__ import annotations

from django.db import migrations

TENANT_SETTING = "app.organization_id"


def backfill(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Membership = apps.get_model("organizations", "Membership")
    Role = apps.get_model("authz", "Role")
    RoleAssignment = apps.get_model("authz", "RoleAssignment")
    PermissionSetVersion = apps.get_model("authz", "PermissionSetVersion")

    owner_role = Role.objects.filter(
        key="owner", organization__isnull=True, is_system=True
    ).first()
    if owner_role is None:  # pragma: no cover - 0002 seeds it
        raise RuntimeError("authz.0002_seed_system_roles must seed the owner role first.")

    with schema_editor.connection.cursor() as cur:
        for organization in Organization.objects.filter(deleted_at__isnull=True).iterator():
            cur.execute("SELECT set_config(%s, %s, true)", [TENANT_SETTING, str(organization.id)])
            owner = Membership.objects.filter(
                organization_id=organization.id, is_owner=True
            ).first()
            if owner is None:
                continue
            _assignment, granted = RoleAssignment.objects.get_or_create(
                organization_id=organization.id,
                membership_id=owner.id,
                role_id=owner_role.id,
                scope_type="organization",
                scope_product="",
                scope_ref="",
            )
            if granted:
                # A grant must invalidate cached permission sets, exactly as
                # authz.services.bump_permission_version does at runtime -
                # otherwise a set cached before the upgrade (up to its TTL)
                # still reads as "no permissions". An absent row reads as
                # version 1, so the first bump writes 2.
                version, created = PermissionSetVersion.objects.get_or_create(
                    organization_id=organization.id, defaults={"version": 2}
                )
                if not created:
                    PermissionSetVersion.objects.filter(pk=version.pk).update(
                        version=version.version + 1
                    )
        cur.execute("SELECT set_config(%s, '', true)", [TENANT_SETTING])


class Migration(migrations.Migration):
    dependencies = [
        ("authz", "0002_seed_system_roles"),
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop, elidable=False),
    ]

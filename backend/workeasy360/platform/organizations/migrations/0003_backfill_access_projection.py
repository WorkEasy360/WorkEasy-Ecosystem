"""Backfill the access-directory projection for existing memberships.

0002 creates ``organizations_user_access`` empty. Without this, every user
of a database that had organizations before Phase 5 sees an empty chooser
after the upgrade - and the frontend sends a user with no organizations to
onboarding. Found by the Phase 5 upgrade rehearsal (a Phase-4-shaped
database migrated forward: all five active memberships missing).

Historical models only, so this keeps working as the live models change.
``organizations_membership`` has FORCE ROW LEVEL SECURITY, which binds even
the migrator (its owner), so the tenant variable is set per organization -
transaction-local, inside this migration's own transaction - exactly as
``platform.tenancy.db.organization_db_scope`` does at runtime.

Idempotent (``get_or_create`` on the unique pair) and reversible as a no-op:
rolling back 0002 drops the table this fills.
"""

from __future__ import annotations

from django.db import migrations

TENANT_SETTING = "app.organization_id"


def backfill(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Membership = apps.get_model("organizations", "Membership")
    UserOrganizationAccess = apps.get_model("organizations", "UserOrganizationAccess")

    with schema_editor.connection.cursor() as cur:
        for organization in Organization.objects.filter(deleted_at__isnull=True).iterator():
            cur.execute("SELECT set_config(%s, %s, true)", [TENANT_SETTING, str(organization.id)])
            for membership in Membership.objects.filter(
                organization_id=organization.id, status="active"
            ).iterator():
                UserOrganizationAccess.objects.get_or_create(
                    user_id=membership.user_id,
                    organization_id=organization.id,
                    defaults={
                        "organization_slug": organization.slug,
                        "organization_display_name": organization.display_name,
                        "organization_status": organization.status,
                        "membership_id": membership.id,
                        "is_owner": membership.is_owner,
                    },
                )
        cur.execute("SELECT set_config(%s, '', true)", [TENANT_SETTING])


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0002_add_creation_idempotency_and_access_projection"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop, elidable=False),
    ]

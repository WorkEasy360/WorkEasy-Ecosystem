"""Seed the system role catalog: ``authz.services.SYSTEM_ROLES`` -> real rows.

**Why this is a migration and not a management command or a service call.**
``authz_role`` carries the D-001 asymmetric RLS policy
(``migrations_helpers.enable_system_template_rls``, applied by
``tenancy.0001_rls_policies``): reads see a tenant's own rows or system
templates, but *writes* only ever match ``organization_id = <the caller's own
tenant>`` - never ``NULL``. That is deliberate (see that policy's own
docstring: "An upgrade that adds a permission to a system role cannot be
performed by the application role at all"), and both database roles this
platform ever connects as are ``NOBYPASSRLS`` (``infra/postgres/init.sql``).
There is therefore no *runtime* code path, ever, through which a system role
row can be written - not a management command, not an admin action.

**Why this migration cannot simply run before RLS is enabled.** That was the
first approach (``run_before = [("tenancy", "0001_rls_policies")]``), and it
works for a database migrated from zero - but this platform has an existing
deployment where ``tenancy.0001_rls_policies`` is already applied
(``check_consistent_history`` refuses to reorder migration history that has
already run: Task 61's "any projection/new identity table migration must be
reviewed for ... existing users/memberships" applies here too, even though
this migration adds no table). The mechanism instead: Postgres's row-security
FORCE flag is independent of whether RLS is *enabled*, and it is a DDL
attribute (``ALTER TABLE``), never itself subject to a row-security policy.
``w360_migrator`` owns ``authz_role`` (``infra/postgres/init.sql``:
``ALTER SCHEMA public OWNER TO w360_migrator``), and a table owner bypasses
every row-security policy *unless* ``FORCE`` is set. So:

    ALTER TABLE authz_role NO FORCE ROW LEVEL SECURITY;   -- owner bypasses again
    -- seed rows, including organization_id IS NULL --
    ALTER TABLE authz_role FORCE ROW LEVEL SECURITY;      -- restored

``w360_app`` (the runtime role) is never the table owner and is always
subject to RLS regardless of ``FORCE`` - this toggle changes nothing about
what it can do, only what the migrator can do, and only for the seconds this
migration runs. This works identically whether ``tenancy.0001_rls_policies``
has already applied or not, so a **future** change to a system role (adding
a permission to ``admin``, say) needs a new migration written the same way -
bracketed by this same toggle - never a service call.

**Idempotent by construction.** ``update_or_create`` on the natural key
(``key``, ``organization=NULL``) and on ``(role, permission)`` for the join
table, so re-running this migration (a fresh install re-applying every
migration from zero, or a `migrate` re-run after a partial failure) converges
rather than duplicates (Task 31).
"""

from __future__ import annotations

import uuid

from django.db import migrations


def seed_system_roles(apps, schema_editor):
    Permission = apps.get_model("authz", "Permission")
    Role = apps.get_model("authz", "Role")
    RolePermission = apps.get_model("authz", "RolePermission")

    from workeasy360.platform.authz.registry import permission_registry
    from workeasy360.platform.authz.services import SYSTEM_ROLES

    # 1. Mirror the code-declared registry into the Permission table - "a
    #    deploy step in production" (conftest's sync_permissions fixture
    #    docstring), performed for real, here, for the first time.
    all_keys: list[str] = []
    for declaration in permission_registry.all():
        key = str(declaration.key)
        # Phase 7: control-plane permissions are mirrored but never granted
        # to a customer role (``getattr`` so this migration still reads a
        # registry written before the flag existed).
        if not getattr(declaration, "control_plane", False):
            all_keys.append(key)
        Permission.objects.update_or_create(
            key=key,
            defaults={
                "label": declaration.label,
                "product": declaration.product,
                "category": declaration.category,
                "dangerous": declaration.dangerous,
                "implies": list(declaration.implies),
            },
        )

    # 2. One global Role row per SYSTEM_ROLES entry, organization=NULL,
    #    is_system=True - readable by every tenant (system_row), writable by
    #    the application role never again after this migration.
    for role_key, spec in SYSTEM_ROLES.items():
        # Not update_or_create(): its defaults would include "id" only on
        # create, and there is no clean way to express that without risking
        # a re-run mutating an *existing* row's primary key mid-update
        # (Django's update_or_create sets every default attribute, including
        # id, before saving the found instance). Explicit get/create/update
        # instead - the same idempotence, no PK hazard.
        role = Role.objects.filter(key=role_key, organization__isnull=True).first()
        if role is None:
            role = Role.objects.create(
                id=uuid.uuid4(),
                key=role_key,
                organization=None,
                name=spec["name"],
                description=spec["description"],
                product="",
                is_system=True,
                is_assignable=True,
            )
        else:
            Role.objects.filter(pk=role.pk).update(
                name=spec["name"],
                description=spec["description"],
                is_system=True,
                is_assignable=True,
            )

        # "*" (owner) grants every permission declared as of this migration.
        # Safe by construction, not by trust: every control-plane-mutating
        # service additionally requires platform staff
        # (authz.services.require_platform_staff), independent of role - see
        # test_no_system_role_grants_catalog_or_pricing_management, which
        # already encodes this as an invariant.
        granted = all_keys if spec["permissions"] == "*" else list(spec["permissions"])

        existing = set(
            RolePermission.objects.filter(role=role).values_list("permission_id", flat=True)
        )
        for permission_key in granted:
            if permission_key in existing:
                continue
            RolePermission.objects.create(id=uuid.uuid4(), role=role, permission_id=permission_key)


def unseed_system_roles(apps, schema_editor):
    """Reverse: remove exactly what this migration adds, nothing else.

    Safe to run even after later code has added *new* permissions to the
    registry (their Permission rows are left - they were not this
    migration's alone to remove, and RolePermission rows referencing them
    will already be gone with their Role).
    """
    Role = apps.get_model("authz", "Role")

    from workeasy360.platform.authz.services import SYSTEM_ROLES

    Role.objects.filter(
        key__in=list(SYSTEM_ROLES.keys()), organization__isnull=True, is_system=True
    ).delete()
    # The reverse twin of the forward path's SET CONSTRAINTS below: the
    # delete queues deferred FK trigger events, and the next reverse step
    # (ALTER TABLE authz_role FORCE ...) is refused while any are pending -
    # found when an upgrade rehearsal first rolled this migration back.
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):
    dependencies = [("authz", "0001_initial")]

    operations = [
        # Harmless no-op if tenancy.0001_rls_policies has not applied yet
        # (ROW LEVEL SECURITY is not even enabled on the table at that
        # point, and this only ever sets a flag): correct on a fresh
        # install and on this platform's existing deployment alike.
        migrations.RunSQL(
            sql="ALTER TABLE authz_role NO FORCE ROW LEVEL SECURITY;",
            reverse_sql="ALTER TABLE authz_role FORCE ROW LEVEL SECURITY;",
            elidable=False,
        ),
        migrations.RunPython(seed_system_roles, unseed_system_roles, elidable=False),
        # Forces any deferred trigger events the seeding inserts queued (e.g.
        # a deferred constraint trigger on a table that references
        # authz_role) to fire now rather than at COMMIT. Without this,
        # PostgreSQL refuses the closing ALTER TABLE with "cannot ALTER
        # TABLE authz_role because it has pending trigger events" - the
        # same DEFERRABLE INITIALLY DEFERRED family Task 33 is about,
        # encountered here as a migration-ordering constraint rather than a
        # test gap.
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

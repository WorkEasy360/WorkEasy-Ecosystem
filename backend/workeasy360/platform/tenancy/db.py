"""The RLS session variable, set safely under connection pooling.

This is the single most dangerous operational detail in the design (R-01).

PgBouncer in transaction pooling mode returns a server connection to the pool
at COMMIT. A session-level ``SET app.organization_id = ...`` would persist on
that connection and be inherited by the *next* transaction, which may belong
to a different tenant. The result is a silent cross-tenant read with no error
anywhere.

The rule is therefore absolute: **only ``set_config(..., true)``, only inside
an explicit transaction.** ``is_local => true`` is the function form of
``SET LOCAL``; PostgreSQL reverts it at transaction end, so nothing can leak
to the next borrower of that server connection.

Every helper here is the only sanctioned way to set the variable. Application
code never touches ``app.organization_id`` directly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from django.db import connection, transaction

from workeasy360.contracts.values.tenant import TenantContext

logger = logging.getLogger(__name__)

#: The PostgreSQL run-time parameter RLS policies read. A custom GUC in a
#: namespaced form ("app.*") so it cannot collide with a real setting.
TENANT_SETTING = "app.organization_id"

#: Carried alongside so audit triggers and future policies can distinguish a
#: break-glass session at the database level, not only in application logs.
BREAK_GLASS_SETTING = "app.is_break_glass"


def set_tenant_variable(
    organization_id: UUID | None, *, is_break_glass: bool = False
) -> None:
    """Set the RLS variable for the *current transaction only*.

    Must be called inside an atomic block. Outside one, psycopg runs in
    autocommit and the setting would be discarded immediately after the
    statement - producing a policy that matches nothing and a very confusing
    "my query returns no rows" bug.
    """
    if not connection.in_atomic_block:
        raise RuntimeError(
            "set_tenant_variable() must run inside transaction.atomic(). "
            "Outside a transaction, set_config(..., true) is reverted at once "
            "and every tenant query would return zero rows. Use "
            "tenant_db_scope() instead of calling this directly."
        )
    with connection.cursor() as cur:
        cur.execute(
            "SELECT set_config(%s, %s, true), set_config(%s, %s, true)",
            [
                TENANT_SETTING,
                str(organization_id) if organization_id else "",
                BREAK_GLASS_SETTING,
                "on" if is_break_glass else "off",
            ],
        )


def current_tenant_variable() -> str | None:
    """Read the variable back. Diagnostics and leakage tests only.

    ``missing_ok => true`` returns NULL when unset rather than raising, which
    is the same behaviour the RLS policy relies on.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting(%s, true)", [TENANT_SETTING])
        row = cur.fetchone()
    value = row[0] if row else None
    return value or None


@contextmanager
def tenant_db_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    """Open a transaction and bind the tenant variable to it.

    This is the mechanism referenced by ADR-009. A consequence accepted
    deliberately: every tenant-scoped request runs inside a transaction.
    ``ATOMIC_REQUESTS`` is *not* used for this - it wraps too much and holds
    locks across slow serialisation - so the tenancy middleware opens the
    transaction explicitly and the request's writes share that span.
    """
    with transaction.atomic():
        set_tenant_variable(ctx.organization_id, is_break_glass=ctx.is_break_glass)
        yield ctx


@contextmanager
def organization_db_scope(organization_id: UUID) -> Iterator[UUID]:
    """Enter the database tenant scope for one organization, without a context.

    For cross-tenant writers - provisioning, reconciliation, purge, fixtures -
    that legitimately act on behalf of an organization but run outside a
    request.

    This exists because the two isolation layers are genuinely independent:

    - ``unscoped_access()`` relaxes **Layer 2**, the ORM manager.
    - RLS is **Layer 3** and does not care. Without a tenant variable, an
      INSERT is refused by the policy's WITH CHECK and a SELECT returns
      nothing.

    So a cross-tenant job must still enter this scope **per organization**. It
    must never try to write for many tenants in one transaction, which is the
    same rule 03-multi-tenancy.md states for Celery tasks: iterate
    organizations, entering context for each.
    """
    with transaction.atomic():
        set_tenant_variable(organization_id)
        yield organization_id


@contextmanager
def unscoped_db_scope() -> Iterator[None]:
    """A transaction with the tenant variable explicitly cleared.

    Used by migrations, reconciliation and the isolation tests. Clearing is
    explicit rather than assumed: inheriting a stale value from an enclosing
    scope is precisely the leak this module exists to prevent.
    """
    with transaction.atomic():
        set_tenant_variable(None)
        yield


def assert_connection_role_cannot_bypass_rls() -> list[str]:
    """Verify the runtime role is not superuser and not BYPASSRLS.

    R-02: PostgreSQL table owners bypass RLS unless FORCE ROW LEVEL SECURITY is
    set, and superusers bypass it regardless. If the application connects as
    such a role, every policy exists, every test of policy *definition* passes,
    and no policy is ever enforced.

    Returns a list of problems. The caller decides whether to warn or refuse to
    boot - the startup check refuses.
    """
    problems: list[str] = []
    with connection.cursor() as cur:
        cur.execute(
            "SELECT current_user, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        )
        row = cur.fetchone()

    if row is None:  # pragma: no cover - would mean a broken catalog
        return ["Could not resolve the current database role."]

    role, is_super, can_bypass = row
    if is_super:
        problems.append(
            f"Database role {role!r} is a SUPERUSER. Superusers bypass Row-Level "
            f"Security unconditionally, so every tenant isolation policy would "
            f"be inert. Connect as a dedicated non-superuser role (ADR-009)."
        )
    if can_bypass:
        problems.append(
            f"Database role {role!r} has BYPASSRLS. Tenant isolation policies "
            f"would not apply. Grant NOBYPASSRLS to this role (R-02)."
        )
    return problems


def tables_missing_rls(table_names: list[str]) -> list[str]:
    """Tenant tables that lack RLS, lack FORCE, or lack a complete policy.

    Checks all four properties that matter, because three of them produce a
    policy that looks present and does nothing:

    - ``relrowsecurity``  - RLS enabled at all
    - ``relforcerowsecurity`` - applies to the table owner too
    - a policy with ``USING``  - filters reads and the old row on UPDATE
    - a policy with ``WITH CHECK`` - validates the *new* row on INSERT/UPDATE

    Without WITH CHECK, code could insert a row belonging to another tenant
    even though it could not read it back.
    """
    if not table_names:
        return []

    problems: list[str] = []
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relname = ANY(%s)
            """,
            [table_names],
        )
        flags = {name: (rls, force) for name, rls, force in cur.fetchall()}

        cur.execute(
            """
            SELECT tablename, policyname, qual, with_check
            FROM pg_policies
            WHERE schemaname = current_schema() AND tablename = ANY(%s)
            """,
            [table_names],
        )
        policies: dict[str, list[tuple[str, str | None, str | None]]] = {}
        for table, policy, qual, with_check in cur.fetchall():
            policies.setdefault(table, []).append((policy, qual, with_check))

    for table in table_names:
        if table not in flags:
            problems.append(f"{table}: table not found in the current schema.")
            continue
        rls_enabled, rls_forced = flags[table]
        if not rls_enabled:
            problems.append(f"{table}: ROW LEVEL SECURITY is not enabled.")
        if not rls_forced:
            problems.append(
                f"{table}: FORCE ROW LEVEL SECURITY is not set, so the table "
                f"owner bypasses every policy (R-02)."
            )
        table_policies = policies.get(table, [])
        if not table_policies:
            problems.append(f"{table}: no RLS policy is defined.")
            continue
        if not any(qual for _, qual, _ in table_policies):
            problems.append(f"{table}: no policy defines USING (reads unfiltered).")
        if not any(check for _, _, check in table_policies):
            problems.append(
                f"{table}: no policy defines WITH CHECK, so a row belonging to "
                f"another tenant could be inserted."
            )
    return problems

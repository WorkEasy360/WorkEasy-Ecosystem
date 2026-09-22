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

**Transaction end, not block end.** ``set_config(..., true)`` is reverted when
the *transaction* ends. A ``SAVEPOINT`` that is released does **not** revert
it - PostgreSQL keeps the value for the remainder of the enclosing
transaction, and only a savepoint *rollback* undoes it. Django's
``transaction.atomic()`` is a savepoint whenever one is already open, so a
nested scope would otherwise leave its organization bound to the caller's
transaction after it exited: a silent cross-tenant read for every statement
that followed. The scope helpers below therefore restore what they found.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from django.db import connection, transaction

from workeasy360.contracts.errors import MissingTenantContext
from workeasy360.contracts.values.tenant import TenantContext

logger = logging.getLogger(__name__)

#: The PostgreSQL run-time parameter RLS policies read. A custom GUC in a
#: namespaced form ("app.*") so it cannot collide with a real setting.
TENANT_SETTING = "app.organization_id"

#: Carried alongside so audit triggers and future policies can distinguish a
#: break-glass session at the database level, not only in application logs.
BREAK_GLASS_SETTING = "app.is_break_glass"

#: The organization the innermost *scope helper* has bound, if any.
#:
#: A cheap, in-process mirror of the database variable, so that
#: :func:`assert_tenant_scope` costs nothing on the path that took a scope.
#: It is deliberately only written by the context managers here: a raw
#: :func:`set_tenant_variable` leaves it empty, which makes the assertion fall
#: back to asking the database rather than trusting a stale belief.
_bound_organization: ContextVar[UUID | None] = ContextVar(
    "w360_db_scope_organization", default=None
)


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


def _bind_tenant_variable(
    organization_id: UUID | None, *, is_break_glass: bool = False
) -> tuple[str, str]:
    """Set both variables and return what they held before, in one round trip.

    The previous values are read in the **same statement** as the write, from
    a ``MATERIALIZED`` common table expression. Materialising it is what makes
    the ordering defined: PostgreSQL may inline an ordinary CTE, and an
    inlined ``current_setting`` could be evaluated after the ``set_config``
    beside it, returning the value we were in the middle of writing.

    One statement rather than two because ``compute_entitlements`` opens three
    scopes per organization on an hourly, per-tenant job; a second round trip
    each would be six extra network hops to learn something the first hop
    could have told us.
    """
    if not connection.in_atomic_block:
        raise RuntimeError(
            "Tenant scopes must run inside transaction.atomic(). Outside a "
            "transaction, set_config(..., true) is reverted at once and every "
            "tenant query would return zero rows."
        )
    with connection.cursor() as cur:
        cur.execute(
            """
            WITH previous AS MATERIALIZED (
                SELECT current_setting(%s, true) AS organization,
                       current_setting(%s, true) AS break_glass
            )
            SELECT previous.organization,
                   previous.break_glass,
                   set_config(%s, %s, true),
                   set_config(%s, %s, true)
            FROM previous
            """,
            [
                TENANT_SETTING,
                BREAK_GLASS_SETTING,
                TENANT_SETTING,
                str(organization_id) if organization_id else "",
                BREAK_GLASS_SETTING,
                "on" if is_break_glass else "off",
            ],
        )
        row = cur.fetchone()
    return ((row[0] or "") if row else "", (row[1] or "") if row else "")


def _restore_tenant_variables(previous: tuple[str, str]) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT set_config(%s, %s, true), set_config(%s, %s, true)",
            [TENANT_SETTING, previous[0], BREAK_GLASS_SETTING, previous[1] or "off"],
        )


@contextmanager
def _db_scope(organization_id: UUID, *, is_break_glass: bool = False) -> Iterator[UUID]:
    """Bind the tenant variable for the block, and put back what was there.

    Restoring is not tidiness. ``set_config(..., true)`` survives the release
    of a savepoint, so a scope entered inside an existing transaction would
    otherwise leave its organization bound to every statement that follows it
    in that transaction - including the caller's, which believes it is still
    scoped to its own tenant. Proven by
    ``test_a_nested_scope_does_not_leak_its_tenant_to_the_caller``.

    Two cases are skipped deliberately, because each is provably a no-op:

    - **We opened the outermost transaction.** Its COMMIT or ROLLBACK
      discards the setting, so there is nothing to put back.
    - **The value did not change.** The common case - a request that
      reconciles its own organization opens three nested scopes for the
      tenant it is already bound to.
    """
    nested = connection.in_atomic_block
    desired = (str(organization_id), "on" if is_break_glass else "off")

    with transaction.atomic():
        previous = _bind_tenant_variable(organization_id, is_break_glass=is_break_glass)
        token = _bound_organization.set(organization_id)
        try:
            yield organization_id
        finally:
            _bound_organization.reset(token)

        # Only on the clean path. When the body raised, the savepoint (or the
        # transaction) is about to roll back, which reverts the setting by
        # itself - and issuing a statement on a connection whose last one
        # failed would raise a second error on top of the real one.
        if nested and previous != desired and not transaction.get_rollback():
            _restore_tenant_variables(previous)


@contextmanager
def tenant_db_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    """Open a transaction and bind the tenant variable to it.

    This is the mechanism referenced by ADR-009. A consequence accepted
    deliberately: every tenant-scoped request runs inside a transaction.
    ``ATOMIC_REQUESTS`` is *not* used for this - it wraps too much and holds
    locks across slow serialisation - so the tenancy middleware opens the
    transaction explicitly and the request's writes share that span.
    """
    with _db_scope(ctx.organization_id, is_break_glass=ctx.is_break_glass):
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
    with _db_scope(organization_id):
        yield organization_id


def assert_tenant_scope(organization_id: UUID, *, what: str) -> None:
    """Refuse a tenant-scoped query whose **database** scope does not match.

    This is the guard for the defect that cost Phase 2 its first P0, in its
    general form rather than at the three call sites where it was found:
    application scoping (Layer 2) satisfied, RLS scoping (Layer 3) absent, so
    the policy matches **zero rows** and the caller reads that as "no data".

    A Celery task is the canonical case. ``TenantTask`` re-establishes the
    tenant *context* across the queue boundary, which is Layer 2; nothing
    establishes Layer 3, because a task must choose its own transaction
    boundaries rather than inherit one from a base class. So a task that
    queries its own tenant's rows sees none, reports "missing", and neither
    fails nor retries.

    Cheap by construction: the scope helpers record what they bound, so the
    scoped path is a ``ContextVar`` read. Only a caller that has *not* taken a
    scope pays a round trip - and that caller is about to be told it is
    broken.
    """
    if _bound_organization.get() == organization_id:
        return

    active = current_tenant_variable()
    if active == str(organization_id):
        # Bound by a raw set_tenant_variable() - the isolation suite and the
        # migration helpers do this deliberately. Verified, so permitted.
        return

    logger.error(
        "tenancy.db_scope.missing",
        extra={
            "event": "tenancy.db_scope.missing",
            "model": what,
            "organization_id": str(organization_id),
            "bound_organization_id": active or "",
        },
    )
    raise MissingTenantContext(
        f"{what} is scoped to organization {organization_id}, but the "
        f"database tenant variable holds {active or 'nothing'}. Row-Level "
        f"Security would match zero rows and the result would look like "
        f"'no data'. Run inside tenant_db_scope()/organization_db_scope() - "
        f"a Celery task, a management command or a shell must enter one "
        f"itself, because only the tenancy middleware does it for you."
    )


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

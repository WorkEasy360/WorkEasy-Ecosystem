"""Database-level tenant isolation (Layer 3).

These are the tests that prove RLS is actually enforced rather than merely
configured. They run as the non-owner ``w360_app`` role - conftest.py swaps
the connection after migrations - because a suite running as the table owner
would pass while production was wide open (R-02).

Every case the brief requires is covered: correct tenant, wrong tenant,
missing tenant, connection reuse, transaction rollback, and nested
transactions.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection, transaction

from workeasy360.contracts.errors import MissingTenantContext
from workeasy360.platform.organizations.models import Membership
from workeasy360.platform.tenancy.db import (
    assert_connection_role_cannot_bypass_rls,
    current_tenant_variable,
    organization_db_scope,
    set_tenant_variable,
    tables_missing_rls,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.isolation]


def _raw_membership_count() -> int:
    """Count rows with raw SQL, bypassing the ORM entirely.

    Deliberately raw: the ORM's scoped manager is Layer 2, and this test is
    about Layer 3. If the assertion went through the manager, a passing result
    would prove nothing about the database.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM organizations_membership")
        return cur.fetchone()[0]


def _raw_membership_ids() -> set[str]:
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM organizations_membership")
        return {str(row[0]) for row in cur.fetchall()}


# --- role and policy configuration ---------------------------------------


def test_app_role_cannot_bypass_rls():
    """R-02: the whole scheme is inert if the role can bypass policies."""
    problems = assert_connection_role_cannot_bypass_rls()
    assert problems == [], "; ".join(problems)


def test_every_tenant_table_has_a_complete_rls_policy():
    """W360.E002: RLS enabled, FORCED, with both USING and WITH CHECK.

    Three of those four can be present while the policy does nothing, so all
    four are asserted rather than just the existence of a policy.
    """
    from django.apps import apps

    from workeasy360.platform.tenancy.models import TenantScopedModel

    tables = [
        m._meta.db_table
        for m in apps.get_models()
        if issubclass(m, TenantScopedModel) and not m._meta.abstract
    ]
    assert tables, "No tenant-scoped models found - the check would be vacuous."

    problems = tables_missing_rls(tables)
    assert problems == [], "\n".join(problems)


# --- correct / wrong / missing tenant ------------------------------------


def test_correct_tenant_sees_own_rows(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        assert _raw_membership_count() == 1
        assert str(a["membership"].id) in _raw_membership_ids()


def test_wrong_tenant_cannot_see_other_tenants_rows(two_tenants, in_tenant):
    """Tenant A cannot read tenant B, and vice versa."""
    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        visible = _raw_membership_ids()
    assert str(b["membership"].id) not in visible

    with in_tenant(b["ctx"]):
        visible = _raw_membership_ids()
    assert str(a["membership"].id) not in visible


def test_wrong_tenant_cannot_fetch_by_primary_key(two_tenants, in_tenant):
    """Knowing the id does not help. RLS filters, so the row is not found.

    This is why a cross-tenant miss is a 404 rather than a 403: the object
    genuinely does not exist in the caller's world, and returning 403 would
    disclose that it exists somewhere.
    """
    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM organizations_membership WHERE id = %s",
                [b["membership"].id],
            )
            assert cur.fetchone()[0] == 0


def test_missing_tenant_context_returns_zero_rows_not_all_rows(two_tenants):
    """The fail-closed property the entire design rests on.

    `current_setting(..., true)` is NULL when unset; `col = NULL` is NULL,
    which is not TRUE, so nothing matches. The dangerous alternative - a
    policy that degrades to "no filter" - would expose every tenant at once.
    """
    with transaction.atomic():
        set_tenant_variable(None)
        assert _raw_membership_count() == 0


def test_empty_tenant_variable_returns_zero_rows():
    """An empty string must behave like NULL, not raise.

    Once a variable has been set and reset within a session,
    `current_setting` returns '' rather than NULL, and `''::uuid` raises
    22P02. The policy uses NULLIF(..., '')::uuid so a reset connection
    returns zero rows instead of erroring on every query.
    """
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.organization_id', '', true)")
            cur.execute("SELECT count(*) FROM organizations_membership")
            assert cur.fetchone()[0] == 0


# --- write-side enforcement ----------------------------------------------


def test_with_check_blocks_writing_into_another_tenant(two_tenants, in_tenant):
    """USING filters reads; WITH CHECK validates the new row on write.

    Without WITH CHECK, code in tenant A could insert a row owned by tenant B
    even though it could not read one back.
    """
    from django.db.utils import ProgrammingError

    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        with pytest.raises(ProgrammingError, match="row-level security"):
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO organizations_membership "
                    "(id, organization_id, user_id, status, is_owner, "
                    " member_type, created_at, updated_at) "
                    "VALUES (%s,%s,%s,'active',false,'internal',now(),now())",
                    [uuid.uuid4(), b["org"].id, b["user"].id],
                )


def test_update_cannot_move_a_row_to_another_tenant(two_tenants, in_tenant):
    """A tenant cannot hand its own row to someone else either."""
    from django.db.utils import ProgrammingError

    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        with pytest.raises(ProgrammingError, match="row-level security"):
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE organizations_membership SET organization_id = %s "
                    "WHERE id = %s",
                    [b["org"].id, a["membership"].id],
                )


def test_delete_cannot_reach_another_tenants_row(two_tenants, in_tenant):
    """A cross-tenant DELETE affects zero rows rather than erroring.

    That is the correct shape: the row is invisible, so there is nothing to
    delete. An error would disclose that the row exists.
    """
    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM organizations_membership WHERE id = %s",
                [b["membership"].id],
            )
            assert cur.rowcount == 0

    with in_tenant(b["ctx"]):
        assert _raw_membership_count() == 1  # B's row survived


# --- transaction semantics ------------------------------------------------


def test_tenant_variable_does_not_survive_commit(two_tenants):
    """SET LOCAL is reverted at transaction end.

    This is the property that makes PgBouncer transaction pooling safe: the
    variable cannot outlive the transaction and therefore cannot be inherited
    by the next borrower of the server connection (R-01).
    """
    a = two_tenants["a"]

    with transaction.atomic():
        set_tenant_variable(a["org"].id)
        assert current_tenant_variable() == str(a["org"].id)

    with transaction.atomic():
        assert current_tenant_variable() is None
        assert _raw_membership_count() == 0


def test_tenant_variable_does_not_survive_rollback(two_tenants):
    """An errored request must not leave its tenant on the connection."""
    a = two_tenants["a"]

    try:
        with transaction.atomic():
            set_tenant_variable(a["org"].id)
            assert _raw_membership_count() == 1
            raise RuntimeError("simulated failure")
    except RuntimeError:
        pass

    with transaction.atomic():
        assert current_tenant_variable() is None
        assert _raw_membership_count() == 0


def test_nested_savepoint_rollback_restores_outer_tenant(two_tenants):
    """Django's nested atomic blocks are savepoints.

    A nested service call that fails and is caught must leave the outer
    tenant's context intact - not the inner one's, and not none.
    """
    a, b = two_tenants["a"], two_tenants["b"]

    with transaction.atomic():
        set_tenant_variable(a["org"].id)
        assert _raw_membership_count() == 1

        try:
            with transaction.atomic():  # SAVEPOINT
                set_tenant_variable(b["org"].id)
                assert _raw_membership_count() == 1
                assert current_tenant_variable() == str(b["org"].id)
                raise RuntimeError("inner failure")
        except RuntimeError:
            pass

        assert current_tenant_variable() == str(a["org"].id)
        assert _raw_membership_count() == 1


def test_connection_reuse_does_not_leak_between_tenants(two_tenants):
    """Sequential transactions on one connection, alternating tenants.

    The single-connection analogue of the R-01 concurrency spike: each
    transaction must see exactly its own tenant's rows, however many
    transactions preceded it on the same connection.
    """
    a, b = two_tenants["a"], two_tenants["b"]
    expected = {
        str(a["org"].id): {str(a["membership"].id)},
        str(b["org"].id): {str(b["membership"].id)},
    }

    for i in range(20):
        ctx = a["ctx"] if i % 2 == 0 else b["ctx"]
        with transaction.atomic():
            set_tenant_variable(ctx.organization_id)
            assert _raw_membership_ids() == expected[str(ctx.organization_id)]


def test_orm_manager_raises_without_context(two_tenants):
    """Layer 2 raises rather than returning an empty queryset.

    Silent emptiness looks like "no data", passes review and ships. An
    exception is loud and is caught in development.
    """
    with pytest.raises(MissingTenantContext):
        list(Membership.objects.all())


def test_orm_and_rls_agree(two_tenants, in_tenant):
    """The two layers must not disagree.

    If the ORM filter and the RLS policy ever produced different row sets,
    one of them would be wrong - and the quiet one would be the dangerous one.
    """
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        orm_ids = {str(m.id) for m in Membership.objects.all()}
        raw_ids = _raw_membership_ids()
        assert orm_ids == raw_ids == {str(a["membership"].id)}


def test_unscoped_manager_still_subject_to_rls(two_tenants, in_tenant):
    """The escape hatch relaxes Layer 2 only.

    `unscoped` bypasses application scoping; the database does not care. This
    is the defence-in-depth property: even a deliberate bypass of the ORM
    cannot read another tenant while a tenant variable is set.
    """
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        with unscoped_access(
            reason="Test asserts RLS still applies to the unscoped manager",
            module="tests.isolation",
        ):
            ids = {str(m.id) for m in Membership.unscoped.all()}

    assert ids == {str(a["membership"].id)}
    assert str(b["membership"].id) not in ids


def test_unscoped_manager_refuses_without_explicit_grant(two_tenants):
    """Using `unscoped` outside unscoped_access() is refused.

    Bypassing scoping is permitted, but never silently: the reason is audited
    and the call site is allowlisted.
    """
    with pytest.raises(MissingTenantContext, match="unscoped_access"):
        list(Membership.unscoped.all())


def test_organization_db_scope_confines_writes_to_one_tenant(two_tenants):
    """Cross-tenant writers enter one organization's scope at a time.

    A job that tried to write for many tenants in one transaction would be
    refused by WITH CHECK on the first foreign row, which is the intended
    shape: iterate organizations, entering scope for each.
    """
    from django.db.utils import ProgrammingError

    a, b = two_tenants["a"], two_tenants["b"]

    with organization_db_scope(a["org"].id):
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM organizations_membership")
            assert cur.fetchone()[0] == 1

            with pytest.raises(ProgrammingError):
                cur.execute(
                    "INSERT INTO organizations_membership "
                    "(id, organization_id, user_id, status, is_owner, "
                    " member_type, created_at, updated_at) "
                    "VALUES (%s,%s,%s,'active',false,'internal',now(),now())",
                    [uuid.uuid4(), b["org"].id, b["user"].id],
                )


# --- scope nesting (the savepoint leak) -----------------------------------
#
# ``set_config(..., true)`` is reverted at transaction end, not at block end.
# A savepoint that is *released* keeps it; only a savepoint rollback undoes
# it. Django's transaction.atomic() is a savepoint whenever one is already
# open, so every nested scope is one of these.
#
# Nothing in the suite asserted this, and the leak it produced was what made
# an earlier version of the authorization suite pass: a revocation that
# deleted zero rows looked like a revocation that worked.


def test_a_nested_scope_does_not_leak_its_tenant_to_the_caller(two_tenants):
    """The inner scope must put back what it found.

    Without this, every statement after the inner block - in the caller's own
    transaction, believing it is still scoped to its own tenant - reads the
    inner scope's organization instead. There is no error anywhere.
    """
    a, b = two_tenants["a"]["org"].id, two_tenants["b"]["org"].id

    with organization_db_scope(a):
        assert current_tenant_variable() == str(a)
        with organization_db_scope(b):
            assert current_tenant_variable() == str(b)
        assert current_tenant_variable() == str(a), (
            "the inner scope left its organization bound to the outer "
            "transaction: a silent cross-tenant read for everything after it"
        )


def test_a_nested_scope_does_not_widen_what_the_caller_can_read(two_tenants):
    """The leak, stated as the thing that actually matters.

    Asserted through the unscoped manager rather than by reading the variable
    back, so it fails if the mechanism changes but the outcome does not.
    """
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    a, b = two_tenants["a"], two_tenants["b"]

    with (
        unscoped_access(
            reason="Test reads across tenants to prove the scope was restored",
            module="tests.isolation",
        ),
        organization_db_scope(a["org"].id),
    ):
        with organization_db_scope(b["org"].id):
            pass
        visible = {m.organization_id for m in Membership.unscoped.all()}

    assert visible == {a["org"].id}, f"read {visible} while scoped to {a['org'].id}"


def test_a_failed_nested_scope_still_leaves_the_caller_scoped(two_tenants):
    """The error path. A savepoint rollback reverts the setting by itself, so
    the restore must not run - issuing a statement on a connection whose last
    one failed would raise a second error on top of the real one."""
    a, b = two_tenants["a"]["org"].id, two_tenants["b"]["org"].id

    with organization_db_scope(a):
        with pytest.raises(RuntimeError, match="deliberate"):
            with organization_db_scope(b):
                raise RuntimeError("deliberate failure inside the inner scope")
        assert current_tenant_variable() == str(a)


def test_an_outermost_scope_needs_no_restore(two_tenants):
    """Its transaction ends, which discards the setting.

    Asserted so that the skip in ``_db_scope`` is a tested decision rather
    than an untested optimisation.
    """
    a = two_tenants["a"]["org"].id
    assert current_tenant_variable() is None
    with organization_db_scope(a):
        assert current_tenant_variable() == str(a)
    assert current_tenant_variable() is None


# --- Layer 2 without Layer 3 ----------------------------------------------


def test_a_tenant_query_without_a_database_scope_raises(two_tenants):
    """The general form of the Phase 2 P0.

    Application scoping satisfied, RLS scoping absent: the query is well
    formed, the policy matches zero rows, and the caller reads that as "no
    data". This is the shape a Celery task has, because TenantTask
    re-establishes the context and nothing establishes the variable.

    It must raise. A fail-closed control that returns an empty answer is the
    most dangerous kind, because an empty answer looks like a result.
    """
    from workeasy360.platform.tenancy.context import tenant_context

    a = two_tenants["a"]

    with tenant_context(a["ctx"]):
        with pytest.raises(MissingTenantContext, match="Row-Level Security"):
            list(Membership.objects.all())


def test_a_raw_set_tenant_variable_still_satisfies_the_scope_assertion(two_tenants):
    """The assertion verifies the database, not only its own bookkeeping.

    The isolation suite and the migration helpers set the variable directly,
    without a scope helper. Those callers are correct and must not be
    refused, so a mismatch falls back to asking PostgreSQL.
    """
    from workeasy360.platform.tenancy.context import tenant_context

    a = two_tenants["a"]

    with tenant_context(a["ctx"]), transaction.atomic():
        set_tenant_variable(a["ctx"].organization_id)
        assert [m.id for m in Membership.objects.all()] == [a["membership"].id]

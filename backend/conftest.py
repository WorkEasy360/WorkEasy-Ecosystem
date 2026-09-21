"""Shared test fixtures for the whole suite.

Lives at ``backend/`` rather than ``backend/tests/`` so that module-level
tests (``workeasy360/platform/*/tests/``) and the cross-cutting suites
(``tests/isolation``, ``tests/architecture``) share one set of fixtures.
Duplicating tenant fixtures per module is how two subtly different
definitions of "a tenant" end up in one codebase.

The single most important thing in this file: **migrations run as the owner,
then the connection switches to the non-owner application role.**

A suite that runs as the table owner silently bypasses every RLS policy
(unless FORCE is set, and relying on that alone would leave the role
assumption untested). Isolation tests would pass while production was broken
- which is the exact failure mode R-02 describes
(13-deployment-evolution.md section 2.2).
"""

from __future__ import annotations

import uuid

import pytest
from django.conf import settings
from django.db import connection

from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus
from workeasy360.contracts.values.tenant import TenantContext


def _terminate_test_db_sessions() -> None:
    """Close any session holding a test database open.

    The suite connects through PgBouncer, whose pooled server connections
    keep the test database "in use" even when idle, so Django cannot drop and
    recreate it.
    """
    import os

    import psycopg

    host = os.environ.get("W360_DB_DIRECT_HOST", "127.0.0.1")
    port = os.environ.get("W360_DB_DIRECT_PORT", "5442")
    user = os.environ.get("W360_DB_MIGRATOR_USER", "w360_migrator")
    password = os.environ.get("W360_DB_MIGRATOR_PASSWORD", "w360_migrator_pw")

    try:
        with (
            psycopg.connect(
                f"host={host} port={port} dbname=postgres user={user} "
                f"password={password} connect_timeout=5",
                autocommit=True,
            ) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname LIKE 'test_workeasy360%%' AND pid <> pg_backend_pid()"
            )
    except Exception:  # noqa: S110
        # The stack may not be up yet; the run fails with a clearer message
        # when it tries to connect properly.
        pass


def pytest_configure(config) -> None:
    """Evict stale sessions from a previous run's test database.

    The suite connects through PgBouncer, which holds server connections in
    its pool. Those keep the test database "in use", so Django cannot drop
    and recreate it and the run fails before it starts. Terminating them here
    makes the suite self-healing rather than requiring a manual cleanup after
    any interrupted run.
    """
    _terminate_test_db_sessions()


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Create and migrate the test database, then drop to the app role.

    pytest-django's own ``django_db_setup`` has already run as the migrator
    (config/settings/test.py points the connection there). This wrapper
    rebinds the live connection to ``w360_app`` afterwards, so every
    assertion in the suite runs with RLS genuinely enforced.
    """
    app_user = settings.W360_TEST_APP_USER
    app_password = settings.W360_TEST_APP_PASSWORD

    with django_db_blocker.unblock():
        # Grants are applied by the RLS migration for tables that existed
        # then; anything created since (or by a third-party app) is covered
        # here so the app role can reach it at all.
        with connection.cursor() as cur:
            cur.execute(f"GRANT USAGE ON SCHEMA public TO {app_user}")
            cur.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                f"IN SCHEMA public TO {app_user}"
            )
            cur.execute(
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_user}"
            )
            # TRUNCATE, in the TEST DATABASE ONLY.
            #
            # Django's TransactionTestCase teardown flushes tables with
            # TRUNCATE, which the application role deliberately does not hold
            # in production: TRUNCATE is a table-level operation and is **not**
            # subject to RLS, so granting it would hand the application a
            # cross-tenant data-destruction primitive.
            #
            # The production grant model is asserted separately by
            # tests/security/test_database_grants.py, so relaxing it here does
            # not relax it anywhere that matters.
            cur.execute(f"GRANT TRUNCATE ON ALL TABLES IN SCHEMA public TO {app_user}")
            # Re-assert append-only after the blanket grant above, which would
            # otherwise hand back the UPDATE/DELETE the audit migration
            # deliberately revoked. TRUNCATE stays granted for teardown only.
            cur.execute(f"REVOKE UPDATE, DELETE ON audit_event FROM {app_user}")

    connection.close()

    migrator_user = settings.DATABASES["default"]["USER"]
    migrator_password = settings.DATABASES["default"]["PASSWORD"]
    migrator_host = settings.DATABASES["default"]["HOST"]
    migrator_port = settings.DATABASES["default"]["PORT"]

    settings.DATABASES["default"]["USER"] = app_user
    settings.DATABASES["default"]["PASSWORD"] = app_password
    connection.settings_dict["USER"] = app_user
    connection.settings_dict["PASSWORD"] = app_password

    if settings.W360_TEST_THROUGH_PGBOUNCER:
        host = settings.W360_TEST_PGBOUNCER_HOST
        port = settings.W360_TEST_PGBOUNCER_PORT
        settings.DATABASES["default"]["HOST"] = host
        settings.DATABASES["default"]["PORT"] = port
        connection.settings_dict["HOST"] = host
        connection.settings_dict["PORT"] = port

    yield

    # Hand the connection back to the owner so Django can drop the test
    # database. The app role deliberately cannot, which is the point of the
    # split - but it makes teardown fail noisily if not restored.
    connection.close()
    for target in (settings.DATABASES["default"], connection.settings_dict):
        target["USER"] = migrator_user
        target["PASSWORD"] = migrator_password
        target["HOST"] = migrator_host
        target["PORT"] = migrator_port

    # Evict PgBouncer's pooled server connections so Django can drop the test
    # database. They are idle but still count as sessions using it.
    _terminate_test_db_sessions()


@pytest.fixture(autouse=True)
def _assert_app_role(db):
    """Fail loudly if the suite is somehow running with RLS-bypassing rights.

    Autouse and cheap. Without it, a misconfigured environment would turn
    every isolation test into a test of nothing, and the suite would go green.
    """
    from workeasy360.platform.tenancy.db import (
        assert_connection_role_cannot_bypass_rls,
    )

    problems = assert_connection_role_cannot_bypass_rls()
    assert not problems, (
        "The test database role can bypass RLS, so isolation tests would "
        "pass without proving anything: " + "; ".join(problems)
    )


@pytest.fixture(autouse=True)
def _reset_registries():
    """Keep per-test state from leaking between tests."""
    from workeasy360.platform.observability.metrics import metrics

    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


# --- domain fixtures ------------------------------------------------------


@pytest.fixture
def user_factory(db):
    """Create users with unique normalized emails."""
    from workeasy360.platform.identity.models import User

    def make(email: str | None = None, **kwargs):
        from workeasy360.platform.tenancy.unscoped import unscoped_access

        with unscoped_access(
            reason="Test fixture creates a global identity",
            module="tests.conftest",
        ):
            return User.objects.create_user(
                email=email or f"user-{uuid.uuid4().hex[:10]}@example.com",
                password=kwargs.pop("password", None),
                **kwargs,
            )

    return make


@pytest.fixture
def organization_factory(db, user_factory):
    """Create an organization with an owner membership.

    Returns ``(organization, membership, user)``. Writes enter the
    organization's database scope, because RLS applies to fixtures exactly as
    it applies to production code.
    """
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    def make(
        slug: str | None = None,
        status: OrganizationStatus = OrganizationStatus.ACTIVE,
        owner=None,
        **kwargs,
    ):
        owner = owner or user_factory()
        slug = slug or f"org-{uuid.uuid4().hex[:10]}"

        with unscoped_access(
            reason="Test fixture creates a tenant before any context exists",
            module="tests.conftest",
        ):
            org = Organization.objects.create(
                slug=slug,
                legal_name=kwargs.pop("legal_name", slug.title()),
                display_name=kwargs.pop("display_name", slug.title()),
                status=status.value,
                created_by=owner,
                **kwargs,
            )
            with organization_db_scope(org.id):
                membership = Membership(
                    organization_id=org.id,
                    user_id=owner.id,
                    status=MembershipStatus.ACTIVE.value,
                    is_owner=True,
                    member_type="internal",
                )
                membership.save()
        return org, membership, owner

    return make


@pytest.fixture
def tenant_ctx_factory():
    """Build a TenantContext for an (organization, membership, user) triple."""

    def make(org, membership, user, **kwargs) -> TenantContext:
        return TenantContext(
            organization_id=org.id,
            membership_id=membership.id,
            user_id=user.id,
            organization_status=OrganizationStatus(org.status),
            **kwargs,
        )

    return make


@pytest.fixture
def two_tenants(organization_factory, tenant_ctx_factory):
    """Two fully-populated, unrelated organizations.

    The workhorse of the isolation suite: every cross-tenant assertion needs a
    real second tenant with real data, not an absence of one.
    """
    org_a, mem_a, user_a = organization_factory(slug=f"tenant-a-{uuid.uuid4().hex[:6]}")
    org_b, mem_b, user_b = organization_factory(slug=f"tenant-b-{uuid.uuid4().hex[:6]}")
    return {
        "a": {
            "org": org_a,
            "membership": mem_a,
            "user": user_a,
            "ctx": tenant_ctx_factory(org_a, mem_a, user_a),
        },
        "b": {
            "org": org_b,
            "membership": mem_b,
            "user": user_b,
            "ctx": tenant_ctx_factory(org_b, mem_b, user_b),
        },
    }


@pytest.fixture
def in_tenant():
    """Enter both isolation layers for an organization, as middleware does.

    Layer 2 (the ORM contextvar) and Layer 3 (the RLS variable) are
    independent, and a test that enters only one measures the wrong thing.
    """
    from contextlib import contextmanager

    from django.db import transaction

    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import set_tenant_variable

    @contextmanager
    def enter(ctx: TenantContext):
        # A transaction is mandatory, not incidental: set_config(..., true) is
        # transaction-local, so outside one it is reverted immediately and
        # every tenant query would return zero rows. This mirrors exactly what
        # the tenancy middleware does per request.
        with tenant_context(ctx), transaction.atomic():
            set_tenant_variable(ctx.organization_id, is_break_glass=ctx.is_break_glass)
            yield ctx

    return enter


@pytest.fixture
def entitled(db):
    """Grant an organization a product, through the sole sanctioned writer."""
    from workeasy360.commercial.provisioning.services import grant_product

    def make(org_id, product: str = "crm", **kwargs):
        return grant_product(org_id, product, **kwargs)

    return make

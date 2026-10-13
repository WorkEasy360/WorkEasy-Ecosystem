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

import os
import uuid
from datetime import timedelta

import pytest
from django.conf import settings
from django.db import connection
from django.utils import timezone

from tests.factories.manifests import PRODUCT_ORIGIN, TEST_ISSUER, manifest_for
from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus
from workeasy360.contracts.values.tenant import TenantContext


def _terminate_test_db_sessions() -> None:
    """Close any session holding a test database open.

    The suite connects through PgBouncer, whose pooled server connections
    keep the test database "in use" even when idle, so Django cannot drop and
    recreate it.

    Two steps, because neither alone reaches every session. The pooled
    connections belong to ``w360_app``, and ``pg_terminate_backend`` from the
    migrator is refused for another role's backend ("permission denied to
    terminate process" - the migrator deliberately does not hold
    ``pg_signal_backend``). So they are dropped by the process that owns
    them: PgBouncer's admin console, where the migrator is an ``admin_user``
    (infra/pgbouncer/pgbouncer.ini). ``KILL`` drops the pool's client and
    server connections; ``RESUME`` lets the next run connect again.
    Backends opened directly as the migrator are then terminated as before.
    """
    import os

    import psycopg

    host = os.environ.get("W360_DB_DIRECT_HOST", "127.0.0.1")
    port = os.environ.get("W360_DB_DIRECT_PORT", "5442")
    user = os.environ.get("W360_DB_MIGRATOR_USER", "w360_migrator")
    password = os.environ.get("W360_DB_MIGRATOR_PASSWORD", "w360_migrator_pw")
    pooler_host = os.environ.get("W360_DB_HOST", "127.0.0.1")
    pooler_port = os.environ.get("W360_DB_PORT", "6442")

    try:
        with psycopg.connect(
            f"host={pooler_host} port={pooler_port} dbname=pgbouncer user={user} "
            f"password={password} connect_timeout=5",
            autocommit=True,
        ) as admin:
            cur = admin.cursor()
            cur.execute("SHOW DATABASES")
            pools = [row[0] for row in cur.fetchall()]
            for pool in pools:
                if pool.startswith("test_workeasy360"):
                    # Names come from PgBouncer itself, not from input; the
                    # admin console accepts no bound parameters.
                    cur.execute(f"KILL {pool}")
                    cur.execute(f"RESUME {pool}")
    except Exception:  # noqa: S110
        # Not running behind PgBouncer (W360_TEST_THROUGH_PGBOUNCER=0), or
        # the stack is not up yet - the direct termination below still runs.
        pass

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


def pytest_sessionstart(session) -> None:
    """Single-writer ownership, enforced for every write the suite executes
    (``tests/write_ownership.py``)."""
    from tests import write_ownership

    session.config._w360_uninstall_write_guard = write_ownership.install()


def pytest_sessionfinish(session, exitstatus) -> None:
    """A write from the wrong module fails the run even if the code under test
    swallowed the exception it raised."""
    from tests import write_ownership

    uninstall = getattr(session.config, "_w360_uninstall_write_guard", None)
    if uninstall is not None:
        uninstall()
    if write_ownership.violations:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.section("single-writer violations", sep="=", red=True)
            for line in sorted(set(write_ownership.violations)):
                reporter.line(line)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


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
            # Phase 7: the commission ledger is append-only in production too.
            cur.execute(f"REVOKE UPDATE, DELETE ON commissions_accrual FROM {app_user}")
            # ...and refuses TRUNCATE for every role (tenancy 0007), which the
            # flush-based teardown above depends on. Disabled here, in the TEST
            # DATABASE ONLY, for the same reason the TRUNCATE grant exists; the
            # trigger itself is exercised by scripts/rehearse_phase7_upgrade.py
            # against a real migrated database.
            cur.execute(
                "ALTER TABLE commissions_accrual "
                "DISABLE TRIGGER commissions_accrual_reject_truncate"
            )

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
def _ensure_system_roles(db):
    """Phase 5: restore the system role catalog if a prior test's teardown
    flushed it away.

    ``authz.migrations.0002_seed_system_roles`` (and the ``post_migrate``
    receiver that mirrors it - ``authz.apps.AuthzConfig``) seed
    ``authz_role``/``authz_role_permission`` once, as the migrator, before
    any test runs. But ``pytest.mark.django_db(transaction=True)``'s
    ``TransactionTestCase``-style teardown flushes **every** table via
    TRUNCATE (granted to the app role for tests only - see the comment on
    that grant above), which is not RLS-scoped and does not distinguish
    migration-seeded reference data from a test's own rows. Nothing can
    restore it from the connection the suite normally runs on afterwards -
    that connection is deliberately ``w360_app``, which can never write a
    NULL-organization role row (D-001's entire point, re-asserted by
    ``ensure_system_role_catalog``'s own defensive check).

    Cheap on every test (one indexed existence query). On the rare test that
    runs immediately after such a flush, briefly swaps the connection to the
    migrator - the same technique ``django_db_setup`` above uses - reseeds,
    and swaps back before the test body runs, so the swap is invisible to
    the test and to ``_assert_app_role`` below it.
    """
    from django.db import connection

    from workeasy360.platform.authz.models import Role

    if Role.objects.filter(
        key="owner", organization__isnull=True, is_system=True
    ).exists():
        return

    if connection.in_atomic_block:
        # A rollback-based (`transaction=False`) test's wrapping transaction
        # is already open. Swapping credentials means closing the
        # connection, which would corrupt that transaction rather than
        # restore anything - never safe here. Only reachable at all if a
        # prior `transaction=True` test's flush wiped the roles and *this*
        # test happens to be next and also rollback-based; it will then fail
        # with a legible DoesNotExist/RuntimeError pointing at this comment,
        # rather than at a mysteriously corrupted connection.
        return

    app_user = settings.DATABASES["default"]["USER"]
    app_password = settings.DATABASES["default"]["PASSWORD"]
    app_host = settings.DATABASES["default"]["HOST"]
    app_port = settings.DATABASES["default"]["PORT"]

    migrator_user = os.environ.get("W360_DB_MIGRATOR_USER", "w360_migrator")
    migrator_password = os.environ.get("W360_DB_MIGRATOR_PASSWORD", "w360_migrator_pw")
    migrator_host = os.environ.get("W360_DB_DIRECT_HOST", "127.0.0.1")
    migrator_port = int(os.environ.get("W360_DB_DIRECT_PORT", "5442"))

    def _rebind(user: str, password: str, host: str, port: int) -> None:
        connection.close()
        for target in (settings.DATABASES["default"], connection.settings_dict):
            target["USER"] = user
            target["PASSWORD"] = password
            target["HOST"] = host
            target["PORT"] = port

    _rebind(migrator_user, migrator_password, migrator_host, migrator_port)
    try:
        from workeasy360.platform.authz.services import ensure_system_role_catalog

        ensure_system_role_catalog()
    finally:
        _rebind(app_user, app_password, app_host, app_port)


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

            # Phase 5: mirrors what platform.onboarding.services does for a
            # real creation, so this fixture's memberships behave like
            # production ones for the chooser (which now reads the
            # projection, not a live scan - ADR-022). Without this, every
            # test using this fixture would need to know to rebuild the
            # projection itself, and any test asserting "this organization
            # appears in the chooser" would be testing the fixture's gap,
            # not the product.
            from workeasy360.platform.organizations import projection

            projection.sync_access_for_membership(
                user_id=owner.id, membership=membership, organization=org
            )
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

    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    @contextmanager
    def enter(ctx: TenantContext):
        # Exactly what the tenancy middleware does per request, by calling the
        # same helper rather than a lookalike. A fixture that reimplements the
        # production mechanism tests the reimplementation - and this one used
        # to, which is how the savepoint leak in tenant scoping survived a
        # green suite.
        with tenant_context(ctx), tenant_db_scope(ctx):
            yield ctx

    return enter


@pytest.fixture
def entitled(db):
    """Grant an organization a product, through the sole sanctioned writer."""
    from workeasy360.commercial.provisioning.services import grant_product

    def make(org_id, product: str = "crm", **kwargs):
        return grant_product(org_id, product, **kwargs)

    return make


# --- commercial fixtures --------------------------------------------------
#
# Phase 2 adds a second kind of setup: the control-plane catalog, which is
# global rather than tenant-owned, and the privileged contexts that may write
# it. Both live here rather than in the commercial suites so that "what a
# catalog looks like" has one definition - two subtly different ones is how a
# test passes against a shape production never produces.


@pytest.fixture
def sync_permissions(db):
    """Mirror the code-declared permission registry into the database.

    A deploy step in production. The registry is the source of truth; this
    table is its projection, and exists so RolePermission can hold a foreign
    key.
    """
    from workeasy360.platform.authz.models import Permission
    from workeasy360.platform.authz.registry import permission_registry
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(
        reason="Permission sync is control-plane reference data",
        module="tests.conftest",
    ):
        for declaration in permission_registry.all():
            Permission.objects.update_or_create(
                key=str(declaration.key),
                defaults={
                    "label": declaration.label,
                    "product": declaration.product,
                    "category": declaration.category,
                    "dangerous": declaration.dangerous,
                    "implies": list(declaration.implies),
                },
            )
    return Permission.objects.count()


@pytest.fixture
def grant_permissions(db, sync_permissions):
    """Give a membership a role carrying specific permissions."""
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.authz.models import Role, RoleAssignment, RolePermission
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    counter = {"n": 0}

    def make(org, membership, keys):
        counter["n"] += 1
        role_key = f"commercial-{counter['n']}"
        # authz_role carries the D-001 asymmetric RLS policy: writes must
        # match the current tenant, so this runs inside the org's DB scope.
        with (
            unscoped_access(
                reason="Test fixture seeds a commercial role",
                module="tests.conftest",
            ),
            organization_db_scope(org.id),
        ):
            role = Role.objects.create(
                organization_id=org.id,
                key=role_key,
                name="Commercial",
                is_system=False,
            )
            for key in keys:
                RolePermission.objects.create(role=role, permission_id=str(key))
            assignment = RoleAssignment(
                organization_id=org.id, membership_id=membership.id, role=role
            )
            assignment.save()
        authz.bump_permission_version(org.id)
        return role

    return make


#: Everything a Phase 2 command surface can need. Granted as one set because
#: these tests are about commercial behaviour; the tests that assert *which*
#: permission gates *which* operation grant them individually.
COMMERCIAL_PERMISSIONS = (
    "commercial.catalog.view",
    "commercial.catalog.manage",
    "commercial.pricing.view",
    "commercial.pricing.manage",
    "commercial.subscription.view",
    "commercial.subscription.manage",
    # Phase 3. The same internal staff identity administers the product
    # integration registry, because in reality it is the same people: whoever
    # can publish a product as a SKU is whoever registers the application
    # behind it. Held together here for the same reason as above - the tests
    # that assert *which* permission gates *which* operation grant them
    # individually (tests/security/test_ecosystem_security.py).
    "platform.integration.view",
    "platform.integration.register",
    "platform.integration.update",
    "platform.integration.lifecycle",
    "platform.integration.credential",
)


#: The control-plane grants behind ``platform_admin``'s commercial power. The
#: organization role alone confers none of it (Phase 7 final audit): the
#: internal-identity check reads live ``StaffGrant`` rows, never ``is_staff``.
PLATFORM_ADMIN_GRANTS = (
    "platform.staff_catalog.manage",
    "platform.staff_pricing.manage",
    "platform.staff_subscription.manage",
    "platform.staff_integration.manage",
)


@pytest.fixture
def platform_admin(
    db, organization_factory, grant_permissions, tenant_ctx_factory, user_factory
):
    """An internal staff identity that may write the global catalog.

    Three things make it privileged and all are required: the permissions in
    its organization role, ``User.is_staff``, and live control-plane grants
    (``PLATFORM_ADMIN_GRANTS``) issued through the real ``authz.staff``
    mechanism. A customer administrator can be given the first by a
    misconfigured role; only Workeasy360 can provide the other two.

    ``aal=2`` because every ``manage`` permission is declared dangerous, and
    the decision function refuses those below AAL 2.
    """
    from workeasy360.platform.authz import staff
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    slug = "w360-internal-" + uuid.uuid4().hex[:6]
    org, membership, user = organization_factory(slug=slug)
    with unscoped_access(
        reason="Test fixture marks an identity as internal staff",
        module="tests.conftest",
    ):
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    root = user_factory(is_staff=True)
    for permission in PLATFORM_ADMIN_GRANTS:
        staff.create_grant(
            user_id=user.id,
            permission=permission,
            granted_by_id=root.id,
            reason="Test fixture: internal platform administrator",
        )

    grant_permissions(org, membership, COMMERCIAL_PERMISSIONS)
    ctx = tenant_ctx_factory(org, membership, user, aal=2)
    return {"org": org, "membership": membership, "user": user, "ctx": ctx}


@pytest.fixture
def customer(db, organization_factory, grant_permissions, tenant_ctx_factory):
    """A customer organization whose admin may manage its own subscription.

    Deliberately **not** granted catalog or pricing management: the whole
    point of the control-plane split is that a customer cannot hold those.
    """
    slug = "cust-" + uuid.uuid4().hex[:8]
    org, membership, user = organization_factory(slug=slug)
    grant_permissions(
        org,
        membership,
        ("commercial.subscription.view", "commercial.subscription.manage"),
    )
    ctx = tenant_ctx_factory(org, membership, user, aal=2)
    return {"org": org, "membership": membership, "user": user, "ctx": ctx}


@pytest.fixture
def catalog(db, platform_admin, in_tenant):
    """A small but complete catalog: two products, plans, prices, a bundle.

    Shaped like the example in the brief - CRM Starter / Professional, Books
    Professional, and a Sales Suite bundle - so the tests read as scenarios
    rather than as fixtures.
    """
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.pricing import services as pricing_services
    from workeasy360.contracts.enums import (
        BillingInterval,
        BundleKind,
        CatalogStatus,
        FeatureKind,
        LicensingMode,
        PriceBookChannel,
        ProductStatus,
    )
    from workeasy360.contracts.values.money import CurrencyCode

    ctx = platform_admin["ctx"]
    # The catalog has been live for a month. Backdated so that "what did this
    # cost yesterday?" is a question the fixture can actually answer - a
    # catalog created at `now` makes every historical assertion vacuous.
    established = timezone.now() - timedelta(days=30)

    with in_tenant(ctx):
        crm = catalog_services.create_product(
            ctx, code="crm", name="CRM", licensing_mode=LicensingMode.PER_SEAT
        )
        books = catalog_services.create_product(
            ctx,
            code="books",
            name="Books",
            licensing_mode=LicensingMode.PER_ORGANIZATION,
        )
        for product in (crm, books):
            catalog_services.set_product_status(
                ctx, product_id=product.id, status=ProductStatus.AVAILABLE
            )

        pipelines = catalog_services.create_feature(
            ctx,
            product_id=crm.id,
            key="crm.pipelines.max",
            name="Pipelines",
            kind=FeatureKind.QUOTA,
            unit="pipelines",
            default_value=0,
        )
        forecasting = catalog_services.create_feature(
            ctx,
            product_id=crm.id,
            key="crm.sales.forecast",
            name="Sales forecasting",
            kind=FeatureKind.BOOLEAN,
            default_value=False,
        )
        multi_currency = catalog_services.create_feature(
            ctx,
            product_id=books.id,
            key="books.ledger.multi_currency",
            name="Multi-currency ledger",
            kind=FeatureKind.BOOLEAN,
            default_value=False,
        )

        plans = {}
        versions = {}
        specs = [
            ("crm_starter", crm, "CRM Starter", {"crm.pipelines.max": 3}, 2, 14),
            (
                "crm_professional",
                crm,
                "CRM Professional",
                {"crm.pipelines.max": 25, "crm.sales.forecast": True},
                10,
                14,
            ),
            (
                "books_professional",
                books,
                "Books Professional",
                {"books.ledger.multi_currency": True},
                5,
                0,
            ),
        ]
        for code, product, name, features, seats, trial_days in specs:
            plan = catalog_services.create_plan(
                ctx, product_id=product.id, code=code, name=name
            )
            catalog_services.set_plan_status(
                ctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE
            )
            version = catalog_services.draft_plan_version(
                ctx,
                plan_id=plan.id,
                features=features,
                included_seats=seats,
                trial_days=trial_days,
                effective_from=established,
            )
            catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
            plans[code] = plan
            versions[code] = version

        book = pricing_services.create_price_book(
            ctx,
            code="direct_gbp",
            name="Direct GBP",
            currency=CurrencyCode.GBP,
            channel=PriceBookChannel.DIRECT,
            valid_from=established,
        )
        prices = {}
        for code, amount in (
            ("crm_starter", 1499),
            ("crm_professional", 4999),
            ("books_professional", 2999),
        ):
            prices[code] = pricing_services.publish_price(
                ctx,
                price_book_id=book.id,
                plan_version_id=versions[code].id,
                interval=BillingInterval.MONTHLY,
                amount_minor=amount,
                valid_from=established,
            )

        bundle = catalog_services.create_bundle(
            ctx, code="sales_suite", name="Sales Suite", kind=BundleKind.PREDEFINED
        )
        bundle_version = catalog_services.draft_bundle_version(
            ctx,
            bundle_id=bundle.id,
            items=[
                {"plan_version_id": versions["crm_professional"].id},
                {"plan_version_id": versions["books_professional"].id},
            ],
            effective_from=established,
        )
        catalog_services.publish_bundle_version(ctx, bundle_version_id=bundle_version.id)
        catalog_services.set_bundle_status(
            ctx, bundle_id=bundle.id, status=CatalogStatus.AVAILABLE
        )
        prices["sales_suite"] = pricing_services.publish_price(
            ctx,
            price_book_id=book.id,
            bundle_version_id=bundle_version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=6999,
            valid_from=established,
        )

    return {
        "products": {"crm": crm, "books": books},
        "features": {
            "pipelines": pipelines,
            "forecast": forecasting,
            "multi_currency": multi_currency,
        },
        "plans": plans,
        "versions": versions,
        "price_book": book,
        "prices": prices,
        "bundle": bundle,
        "bundle_version": bundle_version,
    }


@pytest.fixture
def billing_account(db, in_tenant):
    """Open a billing account, which fixes the organization's currency."""
    from workeasy360.commercial.billing import services as billing
    from workeasy360.contracts.values.money import CurrencyCode

    def make(ctx, currency=CurrencyCode.GBP, region=""):
        with in_tenant(ctx):
            return billing.open_billing_account(
                ctx, currency=currency, billing_region=region
            )

    return make


# --- remote product integration (Phase 3) ---------------------------------
#
# These live here rather than beside the module because two suites need them:
# the module's own tests under workeasy360/platform/ecosystem/tests/, and the
# acceptance proof under tests/integration/. Duplicating "what a registered
# product looks like" is how two subtly different definitions of a
# registration end up in one codebase - the same argument this file's header
# makes about tenants.
#
# Two of them carry most of the weight.
#
# `signing_key` puts a real key pair in place: a generated private half in the
# process environment, where EnvSigningKeyProvider will find it, and the
# public half in the database. Nothing is stubbed - tokens in these tests are
# genuinely signed and genuinely verified, because a fake signer would make
# every tampering assertion prove nothing.
#
# `register_product` runs the real registration path, through the real
# services, with real authorization - not ProductIntegration.objects.create.
# A fixture that wrote rows directly would let a test pass against a shape the
# registration service would never produce.


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Clear the limiter's in-process fallback between tests.

    The shared cache is already cleared by the root ``_clear_cache`` fixture,
    but the in-process window survives it - it exists precisely for when the
    cache is unavailable. Without this, a test that exhausts a bucket would
    leak that into the next one and fail it for the wrong reason.
    """
    from workeasy360.platform.ecosystem import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def frozen_throttle_window():
    """Pin the rate limiter's fixed window, so a test that sends N requests
    cannot have them land either side of a window boundary.

    Without this, any throttling assertion is a wall-clock race: the window
    is ``int(time.time()) // window_seconds``, so a test whose requests
    straddle a minute boundary sees the counter reset half way through and
    the limit not fire. It is rare, and it is exactly the kind of rare that
    fails once a fortnight in CI and gets rerun rather than fixed.

    Patches the module's reference to ``time`` rather than
    ``time.time`` itself: a global patch would reach Django, psycopg and
    every other library running inside the test.
    """
    from workeasy360.platform.observability import throttle

    class _FrozenClock:
        @staticmethod
        def time() -> float:
            # An arbitrary instant that is comfortably inside a window
            # rather than on its edge, so `retry_after` is also stable.
            return 1_700_000_020.0

    original = throttle.time
    throttle.time = _FrozenClock  # type: ignore[assignment]
    try:
        yield
    finally:
        throttle.time = original


@pytest.fixture
def signing_key(db, settings):
    """An active RS256 signing key, with its private half in the environment.

    Returns the ``SigningKey`` row. The private PEM is placed in
    ``os.environ`` rather than in a setting because that is where
    ``EnvSigningKeyProvider`` looks, and testing the provider through its real
    lookup is the point - a fixture that monkeypatched the provider would
    leave the resolution path untested.
    """
    from workeasy360.platform.ecosystem import keys
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    reference = f"test{uuid.uuid4().hex[:8]}"
    private_pem, public_pem = keys.generate_key_pair("RS256")
    variable = keys.EnvSigningKeyProvider().variable_name(reference)
    os.environ[variable] = private_pem

    key = SigningKey.objects.create(
        kid=keys.new_kid(),
        algorithm="RS256",
        public_key_pem=public_pem,
        private_key_ref=reference,
        status=SigningKeyStatus.ACTIVE.value,
    )
    key.private_pem = private_pem  # type: ignore[attr-defined]
    yield key
    os.environ.pop(variable, None)


@pytest.fixture
def new_signing_key(db):
    """A factory for additional keys, for the rotation tests."""
    from workeasy360.platform.ecosystem import keys
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    created: list[str] = []

    def make(status=SigningKeyStatus.PENDING, algorithm="RS256"):
        reference = f"test{uuid.uuid4().hex[:8]}"
        private_pem, public_pem = keys.generate_key_pair(algorithm)
        variable = keys.EnvSigningKeyProvider().variable_name(reference)
        os.environ[variable] = private_pem
        created.append(variable)

        key = SigningKey.objects.create(
            kid=keys.new_kid(),
            algorithm=algorithm,
            public_key_pem=public_pem,
            private_key_ref=reference,
            status=status.value,
        )
        key.private_pem = private_pem  # type: ignore[attr-defined]
        return key

    yield make
    for variable in created:
        os.environ.pop(variable, None)


@pytest.fixture
def register_product(db, platform_admin, catalog, in_tenant):
    """Register and (by default) activate an integration, through the services.

    Depends on ``catalog`` because registration verifies the product code
    against it through ``contracts/ports/catalog.py`` - a product must be a
    sellable SKU before an application can be registered against it. The
    dependency is the contract working, not fixture coupling.

    Returns a dict carrying the client id, the redirect URI, the credential
    and - for a ``public_key`` credential - the product's private key, which
    is what the reference client signs its assertions with.
    """
    from workeasy360.platform.ecosystem import keys, services

    ctx = platform_admin["ctx"]

    def make(
        product_code: str,
        *,
        origin: str = PRODUCT_ORIGIN,
        activate: bool = True,
        scopes: tuple[str, ...] = ("openid",),
        credential_kind: str = "public_key",
        manifest: dict | None = None,
    ) -> dict:
        payload = manifest or manifest_for(product_code, origin=origin, scopes=scopes)

        with in_tenant(ctx):
            integration = services.register_integration(ctx, manifest=payload)
            if activate:
                services.transition(
                    ctx, client_id=integration.client_id, to_status="active"
                )

            private_pem = ""
            if credential_kind == "public_key":
                private_pem, public_pem = keys.generate_key_pair("RS256")
                issued = services.create_credential(
                    ctx,
                    client_id=integration.client_id,
                    kind="public_key",
                    public_key_pem=public_pem,
                    algorithm="RS256",
                    label="test",
                )
            else:
                issued = services.create_credential(
                    ctx, client_id=integration.client_id, kind="client_secret"
                )

        return {
            "integration": integration,
            "client_id": integration.client_id,
            "product_code": product_code,
            "origin": origin,
            "redirect_uri": f"{origin}/auth/callback",
            "credential_id": issued.credential_id,
            "client_secret": issued.secret,
            "private_key_pem": private_pem,
        }

    return make


@pytest.fixture
def product_client(client):
    """Build a :class:`ReferenceProductClient` over a registration.

    ``client`` is Django's test client, used purely as transport. The
    reference client imports nothing from ``workeasy360`` - asserted by
    ``test_reference_client_imports_no_platform_module``.
    """
    from tests.factories.reference_product_client import ReferenceProductClient

    def make(registration: dict, *, http=None) -> ReferenceProductClient:
        return ReferenceProductClient(
            http=http or client,
            issuer=TEST_ISSUER,
            client_id=registration["client_id"],
            redirect_uri=registration["redirect_uri"],
            private_key_pem=registration["private_key_pem"],
            credential_kid=registration["credential_id"],
            client_secret=registration["client_secret"],
        )

    return make


@pytest.fixture
def signed_in(client, db):
    """Sign a user into the platform's own session, with an assurance level.

    The authorization endpoint is the one place in this phase that reads a
    browser session rather than a credential, so these suites need a real one.
    ``force_login`` establishes it exactly as the platform's login flow would;
    ``w360_aal`` is the claim that flow writes.
    """

    def make(
        user,
        *,
        aal: int = 1,
        mfa_verified: bool | None = None,
        sign_in_preference: str | None = "auto_sso",
    ):
        """``mfa_verified`` (default: staff only) marks an AAL 1 session as
        having verified a factor earlier - what a staff session must have
        since D-037 (Phase 12).

        ``sign_in_preference`` (ADR-044): the person's default product
        sign-in. These suites exercise the *silent* authorization path - a
        signed-in person entering a product with no question asked - which,
        since universal sign-in, is what a person who chose "automatically
        use my Workeasy360 account" gets. ``None`` leaves no stored
        preference, so the platform default applies (Automatic, since the
        one-login brief; ``tests/universal_sso`` passes ``"ask"`` where it
        needs the access chooser)."""
        from tests.factories.sessions import lapsed_step_up
        from workeasy360.platform.ecosystem.models import UserSignInPreference
        from workeasy360.platform.tenancy.resolution import (
            record_step_up,
            reset_session_aal,
        )

        if sign_in_preference is not None:
            UserSignInPreference.objects.update_or_create(
                user=user, product_code="", defaults={"mode": sign_in_preference}
            )
        client.force_login(user)
        session = client.session
        verified = user.is_staff if mfa_verified is None else mfa_verified
        if aal >= 2:
            record_step_up(session)
        elif verified:
            lapsed_step_up(session)
        else:
            reset_session_aal(session)
        session.save()
        return client

    return make


@pytest.fixture
def entitled_org(db, organization_factory, tenant_ctx_factory, entitled):
    """An organization holding a product, ready to authenticate into it.

    Granted through ``commercial/provisioning``, which is the sole sanctioned
    entitlement writer (A10). Writing ``Entitlement`` rows directly would test
    a state the system cannot actually reach.

    **Phase 4.** "Ready to authenticate into it" now includes a seat for a
    per-seat product (``platform.ecosystem.authorization.require_seat`` -
    Task 11 of the brief). Rather than push that setup onto every one of this
    fixture's ten callers, it is done here, once, exactly as entitlement
    itself already is - so a test written before Phase 4 that asked for "an
    org that can authenticate into crm" keeps meaning that. A test that
    specifically exercises the no-seat case passes ``seat=False`` and
    assigns (or does not) explicitly.

    The licensing mode is read through the same ``ProductDirectory`` port
    production code uses (never assumed, never duplicated). A product the
    catalog does not know - a test that granted entitlement without also
    registering a catalog product - is left exactly as before: no seat
    pool exists to assign from, which is also what ``require_seat`` does
    when the catalog has nothing to say about the product.
    """
    from workeasy360.contracts.enums import LicensingMode
    from workeasy360.contracts.ports.catalog import get_product_directory
    from workeasy360.contracts.values.identifiers import ProductCode

    def make(
        product_code: str = "crm", *, slug: str | None = None, seat: bool = True, **kwargs
    ):
        org, membership, user = organization_factory(
            slug=slug or f"org-{uuid.uuid4().hex[:10]}"
        )
        ctx = tenant_ctx_factory(org, membership, user)

        is_per_seat = False
        if seat:
            descriptor = get_product_directory().get(ProductCode(product_code))
            is_per_seat = (
                descriptor is not None
                and descriptor.licensing_mode == LicensingMode.PER_SEAT
            )
        if is_per_seat:
            kwargs.setdefault("seats", 5)
        entitled(org.id, product_code, **kwargs)

        if is_per_seat:
            from workeasy360.platform.entitlements.services.seats import assign_seat
            from workeasy360.platform.tenancy.context import tenant_context
            from workeasy360.platform.tenancy.db import tenant_db_scope

            # SeatAssignment is tenant-scoped (both isolation layers), and
            # ``assign_seat`` is written to be called from a live request or
            # task - exactly the two layers ``require_entitlement`` and
            # ``require_seat`` establish for the identical reason.
            with tenant_context(ctx), tenant_db_scope(ctx):
                assign_seat(org.id, pool_key=product_code, membership_id=membership.id)

        return {
            "org": org,
            "membership": membership,
            "user": user,
            "ctx": ctx,
            "organization_id": str(org.id),
        }

    return make


# --- integration platform (Phase 6) ----------------------------------------
#
# One product ("example") that declares events, one organization entitled to
# it with an administrator holding the integration permissions at AAL 2, a
# real loopback webhook receiver, and the one relaxation of the egress policy
# a loopback receiver needs. Shared here for the same reason as the fixtures
# above: "what a publishing product looks like" has one definition.

EXAMPLE_EVENTS = (
    {
        "type": "product.example.record.created",
        "version": 1,
        "description": "A record was created.",
    },
    {
        "type": "product.example.record.updated",
        "version": 1,
        "description": "A record changed.",
    },
)

#: Every Phase 6 permission. The tests that assert *which* permission gates
#: *which* operation grant them individually.
INTEGRATION_PERMISSIONS = (
    "platform.connection.view",
    "platform.connection.manage",
    "platform.webhook.replay",
    "platform.automation.view",
    "platform.automation.manage",
)


@pytest.fixture
def example_product(db, catalog, platform_admin, in_tenant, register_product):
    """The ``example`` product: sellable (a catalog SKU), registered, active,
    and declaring two events in a ``2026-10-01`` manifest."""
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.contracts.enums import LicensingMode, ProductStatus

    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        product = catalog_services.create_product(
            ctx,
            code="example",
            name="Example",
            licensing_mode=LicensingMode.PER_ORGANIZATION,
        )
        catalog_services.set_product_status(
            ctx, product_id=product.id, status=ProductStatus.AVAILABLE
        )
    return register_product(
        "example",
        manifest=manifest_for(
            "example", manifest_version="2026-10-01", events=list(EXAMPLE_EVENTS)
        ),
    )


@pytest.fixture
def integration_org(example_product, entitled_org, grant_permissions, tenant_ctx_factory):
    """An organization entitled to ``example`` whose owner holds the Phase 6
    permissions, with a context at AAL 2 (``manage`` is dangerous)."""

    def make(*, permissions=INTEGRATION_PERMISSIONS, aal: int = 2):
        org = entitled_org("example")
        if permissions:
            grant_permissions(org["org"], org["membership"], permissions)
        org["ctx"] = tenant_ctx_factory(
            org["org"], org["membership"], org["user"], aal=aal
        )
        return org

    return make


@pytest.fixture
def local_egress(settings):
    """Let the egress policy reach the loopback test receiver over plain
    http. Only these two flags move: private, link-local and metadata ranges
    stay refused, and production pins both flags off."""
    settings.INTEGRATION_EGRESS_ALLOW_HTTP = True
    settings.INTEGRATION_EGRESS_ALLOW_LOOPBACK = True
    return settings


@pytest.fixture
def webhook_receiver():
    from tests.factories.webhook_receiver import WebhookReceiver

    receiver = WebhookReceiver()
    receiver.url = receiver.start()
    yield receiver
    receiver.stop()


@pytest.fixture
def product_event():
    """A well-formed ``product.example.record.created.v1`` event body."""

    def make(**overrides):
        body = {
            "event_id": str(uuid.uuid4()),
            "event_type": "product.example.record.created",
            "event_version": 1,
            "occurred_at": timezone.now().isoformat(),
            "data": {"record_id": "rec_123", "amount": 250, "status": "open"},
        }
        body.update(overrides)
        return body

    return make


# --- Phase 7: control plane and partner channel ---------------------------


@pytest.fixture
def staff_factory(db, user_factory):
    """A staff identity holding exactly the control-plane permissions named.

    Returns ``(user, ctx)``; ``ctx`` is a ``STAFF`` control-plane context at
    AAL 2 unless told otherwise. Grants are created through the real
    ``authz.staff`` mechanism by a separate root identity, because a grant to
    oneself is refused by design.
    """
    from workeasy360.platform.authz import staff
    from workeasy360.platform.authz.control_plane import platform_context

    root = user_factory(is_staff=True)

    def make(*permissions: str, aal: int = 2, active: bool = True):
        user = user_factory(is_staff=True)
        for permission in permissions:
            staff.create_grant(
                user_id=user.id,
                permission=permission,
                granted_by_id=root.id,
                reason="Test fixture grants a control-plane permission",
            )
        ctx = platform_context(user_id=user.id, aal=aal)
        return user, ctx

    return make


@pytest.fixture
def partner_factory(db, user_factory, staff_factory):
    """An ACTIVE partner with one admin member and the capabilities named.

    Returns a dict with ``partner`` (row), ``user``, ``admin`` (partner-actor
    ctx factory taking ``aal``), ``staff_ctx``.
    """
    import uuid

    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.access import partner_actor
    from workeasy360.contracts.enums import PartnerStatus

    def make(
        *capabilities: str,
        role: str = "admin",
        currency: str = "GBP",
        code: str | None = None,
    ):
        _staff_user, sctx = staff_factory("platform.staff_partner.manage")
        partner = services.create_partner(
            sctx,
            code=code or f"p-{uuid.uuid4().hex[:8]}",
            display_name="Test Partner",
            payout_currency=currency,
            contact_email="ops@partner.example.com",
        )
        member_user = user_factory()
        services.add_member(sctx, partner.id, email=member_user.email, role=role)
        if role == "viewer":
            services.add_member(
                sctx, partner.id, email=user_factory().email, role="admin"
            )
        for capability in capabilities:
            services.grant_capability(
                sctx, partner.id, capability=capability, reason="Test fixture capability"
            )
        partner = services.set_partner_status(
            sctx,
            partner.id,
            target=PartnerStatus.ACTIVE,
            reason="Activated by test fixture",
        )

        def actor(aal: int = 2):
            return partner_actor(user_id=member_user.id, partner_id=partner.id, aal=aal)

        return {
            "partner": partner,
            "user": member_user,
            "actor": actor,
            "staff_ctx": sctx,
        }

    return make


# --- Phase 8: the shared AI, retrieval and analytics platform --------------
#
# A loopback stand-in for the AI vendors (tests/factories/fake_ai_provider.py)
# reached through the real adapters and the real egress client; a registry of
# test models that is the *only* active one, so selection is deterministic;
# and organizations entitled to a product *and* to AI through the ordinary
# entitlement path (the ``<product>.ai.monthly_budget_micros`` feature).

#: 5.00 in the accounting currency, in micro-units.
AI_TEST_BUDGET = 5_000_000
_AI_CREDENTIALS = {
    "W360_AI_CREDENTIAL_TEST_ANTHROPIC": "sk-ant-test-" + "a" * 24,
    "W360_AI_CREDENTIAL_TEST_OPENAI": "sk-test-" + "b" * 24,
}


@pytest.fixture
def fake_ai(settings, local_egress):
    """The fake vendor, its endpoints wired in, its API keys in the
    environment (where the credential resolver reads them), and retry delays
    shrunk so failure tests do not sleep for real."""
    from tests.factories.fake_ai_provider import FakeAIProvider

    provider = FakeAIProvider()
    url = provider.start()
    settings.AI_PROVIDER_ENDPOINTS = {"anthropic_messages": url, "openai": url}
    settings.AI_RETRY_BASE_S = 0.01
    settings.AI_RETRY_CAP_S = 0.05
    os.environ.update(_AI_CREDENTIALS)
    yield provider
    provider.stop()
    for name in _AI_CREDENTIALS:
        os.environ.pop(name, None)


@pytest.fixture
def ai_registry(db):
    """Test providers and models; every other model disabled, so which model
    a request selects is exactly what the test arranged.

    Returns a factory for extra models (``add(key, provider=..., ...)``) with
    the two defaults already created: ``test.primary`` (generation) and
    ``test.embed`` (embeddings, 16 dimensions - the fake vendor's size).
    """
    from django.utils import timezone

    from workeasy360.intelligence.ai.models import AIModel, AIModelPrice, AIProvider

    AIModel.objects.update(status="disabled")
    providers = {
        "anthropic": AIProvider.objects.update_or_create(
            key="anthropic",
            defaults={
                "adapter": "anthropic_messages",
                "display_name": "Anthropic",
                "status": "active",
                "credential_ref": "test-anthropic",
            },
        )[0],
        "openai": AIProvider.objects.update_or_create(
            key="openai",
            defaults={
                "adapter": "openai",
                "display_name": "OpenAI",
                "status": "active",
                "credential_ref": "test-openai",
            },
        )[0],
    }

    def add(
        key: str,
        *,
        provider: str = "anthropic",
        capabilities=("text", "structured_output"),
        priority: int = 50,
        tier: str = "standard",
        context_window: int = 200_000,
        max_output: int = 4_096,
        dimensions: int | None = None,
        input_price: int = 3_000_000,
        output_price: int = 15_000_000,
        status: str = "active",
    ):
        model = AIModel.objects.create(
            key=key,
            provider=providers[provider],
            model_id=key.split(".", 1)[-1],
            display_name=key,
            status=status,
            tier=tier,
            capabilities=sorted(capabilities),
            context_window_tokens=context_window,
            max_output_tokens=max_output,
            embedding_dimensions=dimensions,
            priority=priority,
        )
        AIModelPrice.objects.create(
            model=model,
            version=1,
            currency="USD",
            input_micros_per_mtok=input_price,
            output_micros_per_mtok=output_price,
            effective_from=timezone.now() - timezone.timedelta(days=1),
        )
        return model

    add("test.primary", priority=1)
    add(
        "test.embed",
        provider="openai",
        capabilities=("embeddings",),
        priority=1,
        dimensions=16,
        input_price=20_000,
        output_price=0,
    )
    add.providers = providers  # type: ignore[attr-defined]
    return add


@pytest.fixture
def ai_org(example_product, entitled_org):
    """An organization entitled to ``example`` with an AI budget for it."""

    def make(*, budget: int = AI_TEST_BUDGET, product: str = "example"):
        return entitled_org(
            product, features={f"{product}.ai.monthly_budget_micros": budget}
        )

    return make


@pytest.fixture
def ai_client(example_product, product_client):
    """The reference client for ``example``, speaking only discovered endpoints."""
    return product_client(example_product)


@pytest.fixture
def second_product(db, catalog, platform_admin, in_tenant, register_product):
    """A second registered product (``second``), for cross-product isolation."""
    from tests.factories.manifests import manifest_for
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.contracts.enums import LicensingMode, ProductStatus

    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        product = catalog_services.create_product(
            ctx,
            code="second",
            name="Second",
            licensing_mode=LicensingMode.PER_ORGANIZATION,
        )
        catalog_services.set_product_status(
            ctx, product_id=product.id, status=ProductStatus.AVAILABLE
        )
    return register_product(
        "second",
        manifest=manifest_for(
            "second",
            manifest_version="2026-10-01",
            events=[
                {
                    "type": "product.second.item.created",
                    "version": 1,
                    "description": "An item was created.",
                }
            ],
        ),
    )


@pytest.fixture
def entitle_products(db):
    """Grant an organization several products at once - one complete desired
    state, which is how the sole entitlement writer works (``grant_product``
    sets exactly one product and so replaces the rest).

    ``products`` maps a product code to its features, e.g.
    ``{"example": {"example.ai.monthly_budget_micros": 5_000_000}, "second": {}}``.
    """
    from django.utils import timezone

    from workeasy360.contracts.enums import EntitlementSourceKind, EntitlementStatus
    from workeasy360.platform.entitlements.services.write import (
        ComputedEntitlements,
        ComputedFeature,
        ComputedProduct,
        apply_entitlements,
    )

    def make(organization_id, products):
        now = timezone.now()
        return apply_entitlements(
            ComputedEntitlements(
                organization_id=organization_id,
                products=tuple(
                    ComputedProduct(
                        product_code=code,
                        status=EntitlementStatus.ACTIVE,
                        source_kind=EntitlementSourceKind.SUBSCRIPTION,
                        source_ref=f"test:{code}",
                        effective_from=now,
                    )
                    for code in products
                ),
                features=tuple(
                    ComputedFeature(
                        feature_key=key,
                        value=value,
                        kind="quota" if isinstance(value, int) else "boolean",
                        source_ref=f"test:{code}",
                        effective_from=now,
                    )
                    for code, features in products.items()
                    for key, value in features.items()
                ),
            )
        )

    return make

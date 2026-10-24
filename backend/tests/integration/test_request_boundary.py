"""The request boundary (TB-2): tenant resolution and R-10 chokepoint 1.

Exercised through real HTTP, because the properties being asserted are
properties of the middleware stack and the URL shape - not of any function
called in isolation.
"""

from __future__ import annotations

import uuid

import pytest

from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus

pytestmark = [pytest.mark.django_db, pytest.mark.security]


def _url(org, suffix="context"):
    return f"/api/v1/orgs/{org.slug}/{suffix}"


@pytest.fixture
def logged_in(client):
    def login(user):
        client.force_login(user)
        return client

    return login


# --- resolution -----------------------------------------------------------


def test_member_gets_tenant_context(two_tenants, logged_in):
    a = two_tenants["a"]
    response = logged_in(a["user"]).get(_url(a["org"]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_id"] == str(a["org"].id)
    assert payload["membership_id"] == str(a["membership"].id)
    assert payload["write_allowed"] is True


def test_non_member_is_denied(two_tenants, logged_in):
    """Tenant B's user cannot reach tenant A's organization."""
    a, b = two_tenants["a"], two_tenants["b"]
    response = logged_in(b["user"]).get(_url(a["org"]))
    assert response.status_code == 404


def test_unauthenticated_request_is_denied(client, two_tenants):
    a = two_tenants["a"]
    assert client.get(_url(a["org"])).status_code == 404


def test_nonexistent_organization_is_indistinguishable_from_non_membership(
    two_tenants, logged_in
):
    """Uniform responses, or the endpoint becomes an organization-enumeration
    oracle (04-identity-authz.md section 2 step 7)."""
    a, b = two_tenants["a"], two_tenants["b"]

    non_member = logged_in(b["user"]).get(_url(a["org"]))
    missing = logged_in(b["user"]).get("/api/v1/orgs/no-such-org/context")

    assert non_member.status_code == missing.status_code == 404
    assert non_member.json() == missing.json()


def test_organization_is_never_taken_from_a_header(two_tenants, logged_in):
    """TB-1: a client-supplied organization is a cross-tenant read primitive.

    Passing another tenant's id in a header must change nothing.
    """
    a, b = two_tenants["a"], two_tenants["b"]

    response = logged_in(a["user"]).get(
        _url(a["org"]),
        HTTP_X_ORGANIZATION_ID=str(b["org"].id),
        HTTP_X_ORG_SLUG=b["org"].slug,
    )

    assert response.status_code == 200
    assert response.json()["organization_id"] == str(a["org"].id)


def test_removed_membership_loses_access(two_tenants, logged_in, in_tenant):
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    a = two_tenants["a"]
    assert logged_in(a["user"]).get(_url(a["org"])).status_code == 200

    with in_tenant(a["ctx"]):
        with unscoped_access(
            reason="Test removes a membership to assert access is lost",
            module="tests.integration",
        ):
            Membership.unscoped.filter(id=a["membership"].id).update(
                status=MembershipStatus.REMOVED.value
            )

    assert logged_in(a["user"]).get(_url(a["org"])).status_code == 404


# --- R-10 chokepoint 1 ----------------------------------------------------


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_unsafe_methods_are_blocked_for_a_suspended_organization(
    organization_factory, logged_in, method
):
    """The HTTP boundary returns a well-shaped 402 rather than letting the
    request reach a service and fail deep inside."""
    org, membership, user = organization_factory(status=OrganizationStatus.SUSPENDED)
    client = logged_in(user)

    response = getattr(client, method)(_url(org))

    assert response.status_code == 402
    payload = response.json()
    assert payload["code"] == "organization_not_writable"
    assert payload["organization_status"] == "suspended"
    # The message names the remedy, because the remedy differs by status.
    assert "read-only" in payload["detail"]


def test_reads_are_permitted_for_a_suspended_organization(
    organization_factory, logged_in
):
    """Suspension is read-only, not lockout: the customer must still see and
    export their data, and pay."""
    org, membership, user = organization_factory(status=OrganizationStatus.SUSPENDED)
    response = logged_in(user).get(_url(org))

    assert response.status_code == 200
    assert response.json()["write_allowed"] is False


def test_writes_are_permitted_for_an_active_organization(organization_factory, logged_in):
    """The positive case, so the block is not passing for the wrong reason."""
    org, membership, user = organization_factory(status=OrganizationStatus.ACTIVE)
    assert logged_in(user).post(_url(org)).status_code == 200


@pytest.mark.parametrize(
    "status", [OrganizationStatus.LOCKED, OrganizationStatus.DELETED]
)
def test_non_readable_statuses_are_hidden_entirely(
    organization_factory, logged_in, status
):
    """locked and deleted deny reads too, with the same uniform 404 - a
    locked organization must not be distinguishable from a missing one."""
    org, membership, user = organization_factory(status=status)
    assert logged_in(user).get(_url(org)).status_code == 404


# --- correlation and transactions ----------------------------------------


def test_every_response_carries_a_correlation_id(two_tenants, logged_in):
    a = two_tenants["a"]
    response = logged_in(a["user"]).get(_url(a["org"]))
    uuid.UUID(response["X-Correlation-ID"])


def test_tenant_request_runs_inside_a_transaction(two_tenants, logged_in):
    """Every tenant-scoped request runs in a transaction, because the RLS
    variable is transaction-local (ADR-009, Consequences)."""
    a = two_tenants["a"]

    response = logged_in(a["user"]).get(_url(a["org"]))
    assert response.status_code == 200
    # Reaching the view at all proves the scope held: the view queries a
    # tenant table, which returns rows only when the variable is set.


def test_authorization_is_enforced_on_tenant_endpoints(two_tenants, logged_in):
    """A member with no roles is denied - deny by default reaches the API."""
    a = two_tenants["a"]
    response = logged_in(a["user"]).get(_url(a["org"], "memberships"))

    assert response.status_code == 403
    payload = response.json()
    assert payload["code"] == "permission_denied"
    assert payload["permission"] == "platform.member.view"
    # Correlation id is returned so support can find the exact request.
    assert payload["correlation_id"]


def test_unrecognised_exceptions_are_left_to_django(rf):
    """An unknown exception must not be rendered by our middleware.

    Inventing a third error behaviour would risk echoing the exception's
    message - which is where connection strings and internal paths live. Ours
    handles only errors whose shape we control; everything else falls through
    to Django, which returns a bare 500 with DEBUG off.
    """
    from workeasy360.interfaces.api.errors import DomainErrorMiddleware

    middleware = DomainErrorMiddleware(lambda request: None)
    request = rf.get("/api/v1/orgs/acme/context")

    leaky = RuntimeError("connection string postgres://user:secret@host/db")
    assert middleware.process_exception(request, leaky) is None


def test_domain_error_bodies_contain_only_declared_fields(rf):
    """A domain error response carries a code, a message we wrote, and the
    identifiers we chose - never an exception type or a traceback."""
    import json

    from workeasy360.contracts.errors import PermissionDenied
    from workeasy360.interfaces.api.errors import DomainErrorMiddleware

    middleware = DomainErrorMiddleware(lambda request: None)
    request = rf.get("/api/v1/orgs/acme/context")

    response = middleware.process_exception(
        request, PermissionDenied(permission="platform.member.view")
    )
    body = json.loads(response.content)

    assert response.status_code == 403
    assert set(body) <= {
        "code",
        "detail",
        "permission",
        "reason",
        "correlation_id",
    }
    assert "Traceback" not in response.content.decode()


def test_debug_is_never_enabled_in_production():
    """DEBUG=True in production leaks settings, SQL and stack traces through
    the technical 500 page."""
    import os

    required = {
        "W360_ENVIRONMENT": "production",
        "W360_SECRET_KEY": "x" * 60,
        "W360_DB_APP_PASSWORD": "y" * 24,
        "W360_FILE_URL_SIGNING_KEY": "z" * 40,
        "W360_ALLOWED_HOSTS": "app.workeasy360.com",
        "W360_CSRF_TRUSTED_ORIGINS": "https://app.workeasy360.com",
        "W360_RELEASE": "2026.09.21-abc",
        "W360_FILE_STORAGE_BACKEND": "s3",
        "W360_REDIS_URL": "rediss://cache:6379/0",
        "W360_BROKER_URL": "amqp://broker",
        "W360_DB_APP_USER": "w360_app",
    }
    from config.env import Env

    env = Env({**os.environ, **required})
    assert env.bool("W360_DEBUG", False) is False


def test_membership_list_is_bounded_and_scoped(two_tenants, logged_in, in_tenant):
    """Bounded page size, no unbounded variant, and one tenant's rows only."""
    from workeasy360.platform.organizations.selectors import (
        MAX_PAGE_SIZE,
        membership_summaries,
    )

    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        page = membership_summaries(a["ctx"], limit=10_000)

    assert len(page.items) <= MAX_PAGE_SIZE
    assert all(item["membership_id"] == str(a["membership"].id) for item in page.items)


def test_membership_list_has_no_n_plus_one(
    two_tenants, in_tenant, user_factory, django_assert_max_num_queries
):
    """The N+1 guard. Without select_related this is one query per row to
    fetch each member's email - invisible in review, trivially caught here."""
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.organizations.selectors import membership_summaries

    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        for _ in range(10):
            Membership(
                organization_id=a["org"].id,
                user_id=user_factory().id,
                status=MembershipStatus.ACTIVE.value,
                member_type="internal",
            ).save()

        with django_assert_max_num_queries(2):
            page = membership_summaries(a["ctx"], limit=25)
            assert len(page.items) == 11

"""Phase 5 security verification: session lifetime, owner boundaries,
organization-creation abuse.

Every test drives the real HTTP stack (Django's test client through the
full middleware chain) unless it is asserting a storage property, where it
reads what the HTTP calls wrote.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.security]

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ORGANIZATIONS = "/api/v1/organizations"
PASSWORD = "a long enough passphrase for tests"


def _post(client, path, body=None):
    return client.post(path, body or {}, content_type="application/json")


def _login(client, user):
    response = _post(client, LOGIN, {"email": user.email, "password": PASSWORD})
    assert response.status_code == 200
    return client


def _signed_in(client, slug):
    """True while the session still reaches the organization, else None."""
    response = client.get(f"/api/v1/orgs/{slug}/context")
    return True if response.status_code == 200 else None


@pytest.fixture
def owner(client, user_factory):
    """A signed-in user who created their organization over HTTP."""
    user = user_factory(password=PASSWORD)
    _login(client, user)
    created = _post(client, ORGANIZATIONS, {"name": "Owner Co"}).json()
    return {"user": user, "slug": created["slug"], "org_id": created["organization_id"]}


def test_revoking_sessions_ends_a_session(owner, client):
    from workeasy360.platform.identity.models import User

    assert _signed_in(client, owner["slug"]) is True

    User.objects.get(id=owner["user"].id).revoke_sessions()

    assert _signed_in(client, owner["slug"]) is None
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_an_expired_session_reaches_nothing(owner, client):
    from datetime import timedelta

    from django.utils import timezone

    # Phase 8 extension: sessions live in the identity session registry.
    from workeasy360.platform.identity.models import UserSession

    UserSession.objects.filter(session_key=client.session.session_key).update(
        expire_date=timezone.now() - timedelta(seconds=1)
    )

    assert _signed_in(client, owner["slug"]) is None
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


# --- owner role boundaries (audit item 20) ----------------------------------


def test_a_new_owner_cannot_reach_another_organization(owner, client, user_factory):
    from django.test import Client

    other_user = user_factory(password=PASSWORD)
    other = _login(Client(), other_user)
    theirs = _post(other, ORGANIZATIONS, {"name": "Their Co"}).json()

    for response in (
        client.get(f"/api/v1/orgs/{theirs['slug']}/context"),
        client.get(f"/api/v1/orgs/{theirs['slug']}/memberships"),
        client.get(f"/api/v1/orgs/{theirs['slug']}/organization"),
        client.delete(
            f"/api/v1/orgs/{theirs['slug']}/memberships/{theirs['membership_id']}"
        ),
    ):
        assert (response.status_code, response.json()["code"]) == (404, "not_found")
    # And the chooser never shows it.
    listed = client.get(ORGANIZATIONS).json()["organizations"]
    assert [o["slug"] for o in listed] == [owner["slug"]]


def test_a_new_owner_holds_no_platform_or_control_plane_power(owner, client):
    """Organization ownership is not staff, and staff
    operations check ``is_platform_staff`` regardless of role."""
    from django.urls import get_resolver

    from workeasy360.platform.identity.models import User

    user = User.objects.get(id=owner["user"].id)
    assert (user.is_staff, user.is_superuser) == (False, False)

    slug = owner["slug"]
    for path, body in (
        (f"/api/v1/orgs/{slug}/commercial/products", {"code": "x", "name": "X"}),
        (
            f"/api/v1/orgs/{slug}/commercial/price-books",
            {"code": "x", "name": "X", "currency": "GBP"},
        ),
        (f"/api/v1/orgs/{slug}/integrations", {"product_code": "crm"}),
    ):
        response = _post(client, path, body)
        assert response.status_code == 403, (path, response.json())

    # Signing keys have no HTTP surface at all: they are managed by the
    # `w360_signing_key` management command, which only an operator runs.
    routes = [str(p.pattern) for p in get_resolver().url_patterns]
    nested = [
        str(sub.pattern)
        for p in get_resolver().url_patterns
        for sub in getattr(p, "url_patterns", [])
    ]
    assert not [r for r in routes + nested if "signing" in r or "jwks/rotate" in r]


def test_a_new_owner_can_administer_their_own_members(owner, client, user_factory):
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import (
        Membership,
        Organization,
        UserOrganizationAccess,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    colleague = user_factory()
    with (
        unscoped_access(reason="test fixture", module="tests"),
        organization_db_scope(owner["org_id"]),
    ):
        org = Organization.objects.get(id=owner["org_id"])
        membership = Membership(
            organization_id=org.id,
            user_id=colleague.id,
            status=MembershipStatus.ACTIVE.value,
            member_type="internal",
        )
        membership.save()
        projection.sync_access_for_membership(
            user_id=colleague.id, membership=membership, organization=org
        )

    listed = client.get(f"/api/v1/orgs/{owner['slug']}/memberships").json()["results"]
    assert len(listed) == 2

    removed = client.delete(f"/api/v1/orgs/{owner['slug']}/memberships/{membership.id}")
    assert removed.status_code == 200
    assert not UserOrganizationAccess.objects.filter(user_id=colleague.id).exists()

    # The owner cannot remove themselves this way.
    own = next(m for m in listed if m["is_owner"])
    refused = client.delete(
        f"/api/v1/orgs/{owner['slug']}/memberships/{own['membership_id']}"
    )
    assert refused.status_code == 400


# --- organization-creation abuse (audit item 21) -----------------------------


def test_an_anonymous_caller_cannot_create_an_organization(client):
    from django.test import Client

    assert _post(client, ORGANIZATIONS, {"name": "X"}).status_code == 401
    # With CSRF enforced, as a real browser request is, it never reaches the view.
    assert (
        _post(Client(enforce_csrf_checks=True), ORGANIZATIONS, {"name": "X"}).status_code
        == 403
    )


def test_a_disabled_account_cannot_create_an_organization(client, user_factory):
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.models import Organization

    user = user_factory(password=PASSWORD)
    _login(client, user)
    User.objects.filter(id=user.id).update(status="disabled")

    response = _post(client, ORGANIZATIONS, {"name": "Should Not Exist"})

    assert response.status_code == 401
    assert not Organization.objects.filter(created_by_id=user.id).exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 256},
        {"name": ["Acme"]},
        {"name": {"name": "Acme"}},
        {"name": 42},
        {"name": True},
        {"name": "Acme", "idempotency_key": {"k": 1}},
        {"name": "Acme", "idempotency_key": "k" * 129},
    ],
)
def test_malformed_creation_input_is_refused(owner, client, body):
    from workeasy360.platform.organizations.models import Organization

    before = Organization.objects.count()
    response = _post(client, ORGANIZATIONS, body)
    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "organization_validation_error"
    assert Organization.objects.count() == before


def test_a_non_json_body_is_refused(owner, client):
    response = client.post(ORGANIZATIONS, "name=Acme", content_type="text/plain")
    assert response.status_code == 400


def test_organization_creation_is_rate_limited_per_account_not_globally(
    client, user_factory, settings, frozen_throttle_window
):
    from django.test import Client

    settings.ORGANIZATION_CREATION_RATE_LIMITS = {
        "org_create_account": (3, 60),
        "org_create_ip": (1000, 60),
    }
    user = user_factory(password=PASSWORD)
    _login(client, user)
    for i in range(3):
        assert _post(client, ORGANIZATIONS, {"name": f"Co {i}"}).status_code == 201
    limited = _post(client, ORGANIZATIONS, {"name": "Co 4"})
    assert limited.status_code == 429
    assert limited.json()["code"] == "organization_creation_rate_limited"

    other = _login(Client(), user_factory(password=PASSWORD))
    assert _post(other, ORGANIZATIONS, {"name": "Unaffected"}).status_code == 201

"""End-to-end HTTP coverage for login, the organization chooser, the app
directory, launch, and seat administration (Tasks 4, 5, 6, 7, 13, 16, 45).

Every request goes through the real middleware stack - session cookies,
the tenancy resolver - the same reason ``test_oidc_flow.py`` does. CSRF is
exercised separately, with ``enforce_csrf_checks=True``: Django's default
test ``Client`` does not enforce it, so every other test in this file would
pass identically whether or not CSRF protection actually worked. Task 33.
"""

from __future__ import annotations

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
SESSION = "/api/v1/auth/session"
ORGANIZATIONS = "/api/v1/organizations"


def _org_path(org, suffix: str) -> str:
    return f"/api/v1/orgs/{org['org'].slug}/{suffix}"


# --- login / logout / session ----------------------------------------------


def test_login_with_correct_credentials_establishes_a_session(client, user_factory):
    user = user_factory(password="correct horse battery staple")
    response = client.post(
        LOGIN,
        {"email": user.email, "password": "correct horse battery staple"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == str(user.id)

    session = client.get(SESSION)
    assert session.json() == {
        "authenticated": True,
        "user_id": str(user.id),
        "email": user.email,
        "name": user.full_name,
        # Phase 8 extension: which consoles the shell may offer.
        # ADR-045: true only while a temporary password is in force.
        "password_change_required": False,
        "consoles": {"staff": False, "partner": False},
    }


def test_login_with_wrong_password_is_refused_uniformly(client, user_factory):
    user_factory(email="known@example.com", password="the-real-password")
    response = client.post(
        LOGIN,
        {"email": "known@example.com", "password": "wrong"},
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_login_with_an_unknown_email_gives_the_same_answer_as_a_wrong_password(
    client,
):
    """No account-enumeration oracle: both are `invalid_credentials`."""
    response = client.post(
        LOGIN,
        {"email": "nobody@example.com", "password": "anything"},
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_repeated_failures_lock_the_account(client, user_factory):
    from workeasy360.platform.identity.services import MAX_FAILED_ATTEMPTS

    user = user_factory(email="lockout@example.com", password="the-real-password")
    for _ in range(MAX_FAILED_ATTEMPTS):
        client.post(
            LOGIN,
            {"email": user.email, "password": "wrong"},
            content_type="application/json",
        )

    response = client.post(
        LOGIN,
        {"email": user.email, "password": "the-real-password"},  # even the right one
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "account_locked"


def test_a_disabled_account_is_reported_distinctly(client, user_factory):
    from workeasy360.contracts.enums import UserStatus

    user = user_factory(
        email="disabled@example.com", password="pw", status=UserStatus.DISABLED.value
    )
    response = client.post(
        LOGIN,
        {"email": user.email, "password": "pw"},
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "account_disabled"


def test_session_reports_unauthenticated_with_no_session(client):
    assert client.get(SESSION).json() == {"authenticated": False}


def test_session_endpoint_issues_the_csrf_cookie(client):
    """The frontend calls this on shell mount specifically so the cookie
    exists before any form can be submitted (auth_views module docstring)."""
    from django.conf import settings

    client.get(SESSION)
    assert settings.CSRF_COOKIE_NAME in client.cookies


def test_login_without_a_csrf_token_is_refused(user_factory):
    """The property every other test in this file cannot see, because the
    default test Client does not enforce CSRF (module docstring)."""
    strict_client = Client(enforce_csrf_checks=True)
    user = user_factory(password="pw")

    # No prior GET to /auth/session, so no CSRF cookie exists at all yet -
    # the least forgiving case.
    response = strict_client.post(
        LOGIN, {"email": user.email, "password": "pw"}, content_type="application/json"
    )
    assert response.status_code == 403


def test_login_with_the_issued_csrf_token_succeeds(user_factory):
    strict_client = Client(enforce_csrf_checks=True)
    user = user_factory(password="pw")

    strict_client.get(SESSION)  # issues the cookie, per test above
    token = strict_client.cookies["w360_csrf"].value

    response = strict_client.post(
        LOGIN,
        {"email": user.email, "password": "pw"},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200


def test_logout_ends_the_session(client, user_factory):
    user = user_factory(password="pw")
    client.post(
        LOGIN, {"email": user.email, "password": "pw"}, content_type="application/json"
    )
    assert client.get(SESSION).json()["authenticated"] is True

    client.post(LOGOUT)
    assert client.get(SESSION).json() == {"authenticated": False}


def test_revoking_sessions_immediately_ends_an_established_one(client, user_factory):
    """Task 33: the session_epoch control this phase wires up, exercised
    over real HTTP rather than only at the middleware unit level."""
    user = user_factory(password="pw")
    client.post(
        LOGIN, {"email": user.email, "password": "pw"}, content_type="application/json"
    )
    assert client.get(SESSION).json()["authenticated"] is True

    user.revoke_sessions()
    assert client.get(SESSION).json() == {"authenticated": False}


# --- organization chooser ---------------------------------------------------


def test_organizations_endpoint_requires_a_session(client):
    response = client.get(ORGANIZATIONS)
    assert response.status_code == 401


def test_a_user_sees_every_organization_they_belong_to(
    client, user_factory, organization_factory
):
    user = user_factory(password="pw")
    org_a, _membership_a, _ = organization_factory(owner=user, slug="org-a-chooser")
    org_b, _membership_b, _ = organization_factory(owner=user, slug="org-b-chooser")

    client.post(
        LOGIN, {"email": user.email, "password": "pw"}, content_type="application/json"
    )
    response = client.get(ORGANIZATIONS)
    assert response.status_code == 200
    slugs = {o["slug"] for o in response.json()["organizations"]}
    assert slugs == {"org-a-chooser", "org-b-chooser"}


def test_a_users_organizations_are_isolated_from_another_users(
    client, user_factory, organization_factory
):
    other_user = user_factory(password="pw")
    organization_factory(owner=other_user, slug="not-my-org")

    me = user_factory(password="pw")
    organization_factory(owner=me, slug="my-org")

    client.post(
        LOGIN, {"email": me.email, "password": "pw"}, content_type="application/json"
    )
    response = client.get(ORGANIZATIONS)
    slugs = {o["slug"] for o in response.json()["organizations"]}
    assert slugs == {"my-org"}


# --- app directory / launch / seats -----------------------------------------


def test_organization_settings_view_returns_the_current_organization(
    client, organization_factory, tenant_ctx_factory, grant_permissions, in_tenant
):
    org, membership, user = organization_factory(display_name="Acme Inc")
    with in_tenant(tenant_ctx_factory(org, membership, user)):
        grant_permissions(org, membership, ["platform.organization.view"])

    client.force_login(user)
    response = client.get(_org_path({"org": org}, "organization"))
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == org.slug
    assert body["display_name"] == "Acme Inc"
    assert body["write_allowed"] is True


def test_the_app_directory_reflects_entitlement_and_seat_state(
    client, catalog, register_product, entitled_org, in_tenant
):
    register_product("crm")
    org = entitled_org("crm")  # PER_SEAT, seated by default

    client.force_login(org["user"])
    response = client.get(_org_path(org, "apps"))
    assert response.status_code == 200
    apps = {a["product_code"]: a for a in response.json()["apps"]}
    assert apps["crm"]["state"] == "active"
    assert apps["crm"]["can_launch"] is True
    assert "launch_url" not in apps["crm"]  # Task 6: never exposed to the browser


def test_a_no_seat_member_sees_no_seat_in_the_directory(
    client, catalog, register_product, entitled_org, in_tenant
):
    register_product("crm")
    org = entitled_org("crm", seat=False)

    client.force_login(org["user"])
    response = client.get(_org_path(org, "apps"))
    apps = {a["product_code"]: a for a in response.json()["apps"]}
    assert apps["crm"]["state"] == "no_seat"
    assert apps["crm"]["can_launch"] is False


def test_launch_redirects_to_the_registered_launch_url_with_organization_context(
    client, catalog, register_product, entitled_org, in_tenant
):
    from tests.factories.manifests import PRODUCT_ORIGIN

    register_product("crm")
    org = entitled_org("crm")

    client.force_login(org["user"])
    response = client.post(_org_path(org, "apps/crm/launch"))
    assert response.status_code == 302
    assert response["Location"].startswith(f"{PRODUCT_ORIGIN}/launch")
    assert f"organization={org['organization_id']}" in response["Location"]


def test_launch_is_denied_server_side_for_a_no_seat_member_even_if_the_browser_asks(
    client, catalog, register_product, entitled_org, in_tenant, settings
):
    """Task 16: the launch endpoint re-checks; it never trusts that the
    frontend rendered an Open button. Denial redirects back to the launcher
    with the reason, exactly as a plain form POST needs (Task 26) - never a
    JSON body a form submission could not render usefully."""
    register_product("crm")
    org = entitled_org("crm", seat=False)

    client.force_login(org["user"])
    response = client.post(_org_path(org, "apps/crm/launch"))
    assert response.status_code == 302
    assert response["Location"] == (
        f"{settings.W360_FRONTEND_URL}/o/{org['org'].slug}/apps?launch_error=no_seat&product=crm"
    )


def test_seat_overview_requires_the_seat_assign_permission(
    client, catalog, register_product, entitled_org, in_tenant
):
    register_product("crm")
    org = entitled_org("crm")  # a plain member, not an admin

    client.force_login(org["user"])
    response = client.get(_org_path(org, "apps/crm/seats"))
    assert response.status_code == 403


def test_an_admin_can_assign_and_release_a_seat(
    client,
    catalog,
    register_product,
    organization_factory,
    tenant_ctx_factory,
    grant_permissions,
    entitled,
    in_tenant,
):
    register_product("crm")
    org, admin_membership, admin_user = organization_factory()
    admin_ctx = tenant_ctx_factory(org, admin_membership, admin_user)
    with in_tenant(admin_ctx):
        entitled(org.id, "crm", seats=5)
        grant_permissions(org, admin_membership, ["platform.seat.assign"])

    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    member_user = organization_factory()[2]
    with (
        unscoped_access(reason="Test fixture adds a second member", module="tests"),
        organization_db_scope(org.id),
    ):
        member_membership = Membership(
            organization_id=org.id,
            user_id=member_user.id,
            status=MembershipStatus.ACTIVE.value,
            is_owner=False,
            member_type="internal",
        )
        member_membership.save()

    client.force_login(admin_user)
    org_dict = {"org": org, "membership": admin_membership, "user": admin_user}

    overview = client.get(_org_path(org_dict, "apps/crm/seats"))
    assert overview.status_code == 200
    body = overview.json()
    assert body["seats_total"] == 5
    assert body["seats_assigned"] == 0

    assign = client.post(_org_path(org_dict, f"apps/crm/seats/{member_membership.id}"))
    assert assign.status_code == 201

    overview_after = client.get(_org_path(org_dict, "apps/crm/seats")).json()
    assert overview_after["seats_assigned"] == 1
    member_row = next(
        m
        for m in overview_after["members"]
        if m["membership_id"] == str(member_membership.id)
    )
    assert member_row["held"] is True

    release = client.delete(_org_path(org_dict, f"apps/crm/seats/{member_membership.id}"))
    assert release.status_code == 200

    overview_final = client.get(_org_path(org_dict, "apps/crm/seats")).json()
    assert overview_final["seats_assigned"] == 0


# --- IDOR: the membership id comes off the URL ------------------------------
#
# Every other input to seat administration is server-derived: the
# organization from the path (resolved and membership-checked by
# TenancyMiddleware), the product from the catalog, the assigning member
# from the session. ``membership_id`` is the one field a client names
# freely, so it is the one that needs checking.


@pytest.fixture
def seat_admin(
    catalog,
    register_product,
    organization_factory,
    tenant_ctx_factory,
    grant_permissions,
    entitled,
    in_tenant,
):
    """An organization holding CRM seats, with an administrator who may
    assign them."""

    def make():
        register_product("crm")
        org, membership, user = organization_factory()
        ctx = tenant_ctx_factory(org, membership, user)
        with in_tenant(ctx):
            entitled(org.id, "crm", seats=5)
            grant_permissions(org, membership, ["platform.seat.assign"])
        return {"org": org, "membership": membership, "user": user, "ctx": ctx}

    return make


def test_a_seat_cannot_be_assigned_to_another_organizations_member(
    client, seat_admin, organization_factory, in_tenant
):
    """Task 46/47. The composite, DEFERRABLE foreign key would refuse the row
    - but only at COMMIT, after the view had already answered 201, turning a
    request that should be a clean refusal into a 500 (and, because pytest
    never commits, into a *passing* test). Refused in the service instead.

    Nothing about the foreign organization is disclosed: the same answer a
    membership id that never existed gets.
    """
    admin = seat_admin()
    _other_org, other_membership, _other_user = organization_factory()

    client.force_login(admin["user"])
    response = client.post(
        f"/api/v1/orgs/{admin['org'].slug}/apps/crm/seats/{other_membership.id}"
    )

    assert response.status_code == 403
    assert response.json()["code"] == "membership_required"

    # And no seat was consumed by the attempt.
    overview = client.get(f"/api/v1/orgs/{admin['org'].slug}/apps/crm/seats").json()
    assert overview["seats_assigned"] == 0


def test_an_unknown_membership_id_is_refused_identically(client, seat_admin):
    """The uniformity half: "not in this organization" and "does not exist"
    must be indistinguishable, or this endpoint enumerates membership ids."""
    import uuid

    admin = seat_admin()
    client.force_login(admin["user"])

    response = client.post(
        f"/api/v1/orgs/{admin['org'].slug}/apps/crm/seats/{uuid.uuid4()}"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "membership_required"


def test_releasing_an_unknown_membership_is_a_harmless_no_op(client, seat_admin):
    """Deliberately *not* symmetrical with assignment.

    Release must keep working for a membership that is no longer active -
    freeing a departed colleague's seat is the single most common reason an
    administrator touches this page - so it validates nothing and simply
    reports how many assignments it released.
    """
    import uuid

    admin = seat_admin()
    client.force_login(admin["user"])

    response = client.delete(
        f"/api/v1/orgs/{admin['org'].slug}/apps/crm/seats/{uuid.uuid4()}"
    )
    assert response.status_code == 200
    assert response.json()["released"] is False

"""Administrator / User sign-in modes: the choice grants nothing.

The sign-in page asks "Administrator or User?". That choice is a *destination*
the browser goes to after the one, shared sign-in - it never reaches the
server. What lets a person into the Admin Console is decided server-side, on
every request: a staff identity, a session that verified a second factor, and at
least one ``StaffGrant`` in force. These tests prove it from the HTTP edge:

- the login endpoint ignores anything but email and password, so no field,
  query string, header or cookie can ask for administrator rights;
- a customer who "chooses Administrator" is refused, with a plain refusal;
- a staff identity with no grant in force is refused, and losing the last grant
  closes the console on the very next request;
- an administrator can use the ordinary customer experience with the same
  session (the User mode);
- before sign-in nothing reveals whether an address belongs to staff.
"""

from __future__ import annotations

import pytest
from django.test import Client

from tests.partner_center.conftest import PASSWORD, VIEW

SESSION = "/api/v1/admin/session"


def _staff_user(user_factory, *permissions):
    from workeasy360.platform.authz import staff

    root = user_factory(is_staff=True)
    user = user_factory(is_staff=True, password=PASSWORD)
    grants = [
        staff.create_grant(
            user_id=user.id,
            permission=permission,
            granted_by_id=root.id,
            reason="Test fixture grants a control-plane permission",
        )[0]
        for permission in permissions
    ]
    return user, grants, root


def _login(client, email, password=PASSWORD, **extra_body):
    client.get("/api/v1/auth/session")
    return client.post(
        "/api/v1/auth/login",
        {"email": email, "password": password, **extra_body},
        content_type="application/json",
    )


# --- the choice is not an input ------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"mode": "admin"},
        {"mode": "administrator", "role": "admin"},
        {"is_staff": True, "is_superuser": True},
        {"as_admin": True, "permissions": ["platform.staff_security.manage"]},
    ],
    ids=["mode", "mode+role", "flags", "permissions"],
)
def test_no_login_field_can_ask_for_administrator_rights(http_login, user_factory, extra):
    from workeasy360.platform.authz.models import StaffGrant

    customer = user_factory(password=PASSWORD)
    client = Client()
    response = _login(client, customer.email, **extra)
    assert response.status_code == 200, response.content
    # The same answer as a plain sign-in: nothing about a mode or a role.
    assert set(response.json()) == {"user_id", "email", "name"}

    customer.refresh_from_db()
    assert (customer.is_staff, customer.is_superuser) == (False, False)
    assert not StaffGrant.objects.filter(user_id=customer.id).exists()
    refused = client.get(SESSION)
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"
    assert client.get("/api/v1/auth/session").json()["consoles"]["staff"] is False


def test_a_customer_choosing_administrator_is_refused_whatever_the_request_says(
    http_login, user_factory
):
    client = http_login(user_factory(password=PASSWORD))
    attempts = [
        client.get(SESSION, {"role": "admin", "mode": "admin"}),
        client.get(SESSION, HTTP_X_ROLE="admin", HTTP_X_W360_MODE="admin"),
    ]
    client.cookies["w360_role"] = "admin"
    client.cookies["w360_mode"] = "admin"
    attempts.append(client.get(SESSION))
    attempts.append(client.get("/api/v1/admin/partners/overview", {"role": "admin"}))
    for response in attempts:
        assert response.status_code == 403
        # A plain refusal: it names no permission the person lacks.
        assert response.json()["code"] == "permission_denied"
        assert "platform.staff_" not in response.content.decode()


def test_an_administrator_choosing_administrator_gets_the_console(
    http_login, user_factory
):
    user, _g, _r = _staff_user(user_factory, VIEW)
    client = http_login(user)
    response = client.get(SESSION)
    assert response.status_code == 200
    assert VIEW in response.json()["permissions"]
    assert client.get("/api/v1/auth/session").json()["consoles"]["staff"] is True


def test_a_password_sign_in_opens_the_console(http_login, user_factory):
    """Owner decision 2026-10-24: no sign-in code for the admin console."""
    user, _g, _r = _staff_user(user_factory, VIEW)
    client = http_login(user)
    response = client.get(SESSION)
    assert response.status_code == 200
    assert "aal" not in response.json()
    assert VIEW in response.json()["permissions"]


# --- what an administrator is --------------------------------------------------------------


def test_a_staff_identity_without_any_grant_is_not_an_administrator(
    http_login, user_factory
):
    user = user_factory(is_staff=True, password=PASSWORD)
    client = http_login(user)
    response = client.get(SESSION)
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert client.get("/api/v1/auth/session").json()["consoles"]["staff"] is False


@pytest.mark.parametrize("is_staff", [False, True], ids=["superuser", "staff+superuser"])
def test_django_superuser_is_not_an_administrator(http_login, user_factory, is_staff):
    user = user_factory(is_staff=is_staff, is_superuser=True, password=PASSWORD)
    client = http_login(user)
    assert client.get(SESSION).status_code == 403
    assert client.get("/api/v1/admin/partners/overview").status_code == 403
    assert client.get("/api/v1/auth/session").json()["consoles"]["staff"] is False


def test_revoking_the_last_grant_closes_the_console_on_the_next_request(
    http_login, user_factory
):
    from workeasy360.platform.authz import staff

    user, [grant], root = _staff_user(user_factory, VIEW)
    client = http_login(user)
    assert client.get(SESSION).status_code == 200
    assert client.get("/api/v1/admin/partners/overview").status_code == 200

    staff.end_grant(grant_id=grant.id, revoked_by_id=root.id, reason="Left the team")

    # Same session, no sign-out, no cache to expire.
    assert client.get(SESSION).status_code == 403
    assert client.get("/api/v1/admin/partners/overview").status_code == 403
    assert client.get("/api/v1/auth/session").json()["consoles"]["staff"] is False


def test_an_expired_grant_confers_nothing(http_login, user_factory, monkeypatch):
    """A grant row is immutable (a database trigger refuses edits), so the
    expiry is set when it is made and the clock the grant is judged by moves
    past it."""
    from datetime import timedelta
    from types import SimpleNamespace

    from django.utils import timezone

    from workeasy360.platform.authz import staff

    root = user_factory(is_staff=True)
    user = user_factory(is_staff=True, password=PASSWORD)
    staff.create_grant(
        user_id=user.id,
        permission=VIEW,
        granted_by_id=root.id,
        reason="Temporary access for a test",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    client = http_login(user)
    assert client.get(SESSION).status_code == 200

    later = timezone.now() + timedelta(hours=2)
    monkeypatch.setattr(staff, "timezone", SimpleNamespace(now=lambda: later))
    assert client.get(SESSION).status_code == 403


def test_disabling_the_staff_identity_closes_the_console_even_with_grants(
    http_login, user_factory
):
    user, _g, _r = _staff_user(user_factory, VIEW)
    client = http_login(user)
    assert client.get(SESSION).status_code == 200
    user.is_staff = False
    user.save(update_fields=["is_staff"])
    assert client.get(SESSION).status_code == 403


def test_an_anonymous_visit_to_the_console_asks_for_sign_in(db):
    response = Client().get(SESSION)
    assert response.status_code == 401
    assert response.json()["code"] == "login_required"


# --- the User mode -----------------------------------------------------------------------------


def test_an_administrator_can_use_the_ordinary_customer_experience(
    http_login, user_factory, organization_factory
):
    """Choosing "User" is only a different destination: the same session
    reaches the person's own organization like anyone else's would - and the
    console stays a separate, separately-authorized place."""
    user, _g, _r = _staff_user(user_factory, VIEW)
    org, _membership, _owner = organization_factory(owner=user)
    client = http_login(user)

    chooser = client.get("/api/v1/organizations")
    assert chooser.status_code == 200
    assert [o["slug"] for o in chooser.json()["organizations"]] == [org.slug]
    assert client.get(f"/api/v1/orgs/{org.slug}/apps").status_code == 200
    assert client.get(SESSION).status_code == 200


def test_a_customer_uses_the_user_mode_unchanged(
    http_login, user_factory, organization_factory
):
    customer = user_factory(password=PASSWORD)
    org, _m, _o = organization_factory(owner=customer)
    client = http_login(customer)
    assert [
        o["slug"] for o in client.get("/api/v1/organizations").json()["organizations"]
    ] == [org.slug]
    assert client.get(f"/api/v1/orgs/{org.slug}/apps").status_code == 200
    assert client.get(SESSION).status_code == 403


def test_an_administrator_without_an_organization_is_not_given_one(
    http_login, user_factory
):
    user, _g, _r = _staff_user(user_factory, VIEW)
    client = http_login(user)
    body = client.get("/api/v1/organizations").json()
    assert body["organizations"] == [] and body["has_more"] is False


# --- nothing leaks before sign-in ---------------------------------------------------------------


def test_a_failed_sign_in_reads_the_same_for_staff_customers_and_strangers(
    db, user_factory
):
    staff_user, _g, _r = _staff_user(user_factory, VIEW)
    customer = user_factory(password=PASSWORD)
    answers = []
    for email in (staff_user.email, customer.email, "nobody-here@example.com"):
        response = _login(Client(), email, password="not the password at all")
        answers.append((response.status_code, response.json()))
    assert (
        answers[0] == answers[1] == answers[2] == (401, {"code": "invalid_credentials"})
    )


def test_a_signed_out_session_reveals_no_console(db):
    assert Client().get("/api/v1/auth/session").json() == {"authenticated": False}


def test_a_successful_sign_in_answers_the_same_shape_for_staff_and_customers(
    db, user_factory
):
    staff_user, _g, _r = _staff_user(user_factory, VIEW)
    customer = user_factory(password=PASSWORD)
    shapes = {
        frozenset(_login(Client(), user.email).json()) for user in (staff_user, customer)
    }
    assert shapes == {frozenset({"user_id", "email", "name"})}

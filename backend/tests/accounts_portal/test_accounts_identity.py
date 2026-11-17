"""ADR-071: WorkEasy360 Accounts is where a person's identity lives.

With ``W360_ACCOUNTS_IDENTITY`` on, the customer portal's identity links -
email verification, password reset, sign-in email confirmation, the sign-in
after Switch Account - name ``accounts.workeasy360.com``. Off, ADR-059 exactly:
they name ``app.``. Either way the Admin and Partner portals keep their own,
and invitations (which join an organization on the customer portal) stay
there.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import Client

from tests.accounts_portal.conftest import ACCOUNTS, ADMIN, APP, PARTNER, host

pytestmark = pytest.mark.django_db


@pytest.fixture
def identity(stage, settings):
    """``identity(True)``: stage b of the move, with Accounts hosting identity."""

    def put(on: bool):
        stage("b")
        settings.W360_ACCOUNTS_IDENTITY = on
        return settings

    return put


def test_off_is_adr_059_exactly(identity):
    from workeasy360.platform.administration import invitations, self_service
    from workeasy360.platform.identity import portals

    identity(False)
    assert portals.identity_url() == APP
    assert portals.identity_url("app") == APP
    assert self_service._url("/verify-email", "t").startswith(f"{APP}/verify-email#")
    assert invitations._url("t").startswith(f"{APP}/invitations/accept#")


def test_on_the_customer_portals_identity_links_name_accounts(identity):
    from workeasy360.interfaces.api.v1 import auth_views
    from workeasy360.platform.administration import invitations, self_service
    from workeasy360.platform.identity import portals

    identity(True)
    assert portals.accounts_identity() is True
    assert portals.identity_url() == ACCOUNTS
    assert portals.identity_url("app") == ACCOUNTS
    for path in ("/verify-email", "/reset-password", "/confirm-email"):
        assert self_service._url(path, "t").startswith(f"{ACCOUNTS}{path}#token="), path
    assert self_service._url("/login") == f"{ACCOUNTS}/login"
    assert auth_views._switched_login("app") == f"{ACCOUNTS}/login?switched=1"
    # Invitations join an organization on the customer portal: not identity pages.
    assert portals.account_url() == APP
    assert invitations._url("t").startswith(f"{APP}/invitations/accept#")


def test_on_the_admin_and_partner_portals_keep_their_own(identity):
    from workeasy360.interfaces.api.v1 import auth_views
    from workeasy360.platform.administration import self_service
    from workeasy360.platform.identity import portals

    identity(True)
    assert portals.identity_url("admin") == ADMIN
    assert portals.identity_url("partner") == PARTNER
    assert self_service._url("/reset-password", "t", portal="partner").startswith(
        f"{PARTNER}/reset-password#"
    )
    assert auth_views._switched_login("partner") == f"{PARTNER}/login?switched=1"


def test_without_portals_it_is_never_on(settings):
    from workeasy360.platform.identity import portals

    settings.PORTALS_ENABLED = False
    settings.W360_ACCOUNTS_IDENTITY = True
    assert portals.accounts_identity() is False


def _forgot(origin: str, email: str) -> int:
    client = Client()
    netloc = host(origin)
    client.get("/api/v1/auth/session", HTTP_HOST=netloc, secure=True)
    from django.conf import settings

    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    response = client.post(
        "/api/v1/public/password/forgot",
        {"email": email},
        content_type="application/json",
        HTTP_HOST=netloc,
        HTTP_X_CSRFTOKEN=token,
        HTTP_ORIGIN=origin,
        HTTP_REFERER=f"{origin}/forgot-password",
        secure=True,
    )
    return response.status_code


@pytest.mark.parametrize(
    ("asked_on", "on", "link_on"),
    [
        (ACCOUNTS, True, ACCOUNTS),
        (APP, True, ACCOUNTS),  # an old app. page still sends people to Accounts
        (PARTNER, True, PARTNER),
        (APP, False, APP),
    ],
)
def test_a_reset_requested_anywhere_links_where_identity_lives(
    identity, user_factory, django_capture_on_commit_callbacks, asked_on, on, link_on
):
    identity(on)
    user = user_factory(email="reset-me@example.com")
    user.set_password("Correct-Horse-Battery-1!")
    user.save()
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert _forgot(asked_on, "reset-me@example.com") in (200, 202, 204)
    bodies = [m.body for m in mail.outbox if "reset-me@example.com" in m.to]
    assert bodies, [m.subject for m in mail.outbox]
    assert f"{link_on}/reset-password#token=" in bodies[-1]

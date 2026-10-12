"""Multi-portal fixtures (ADR-043, docs/portals/design.md).

The portals are switched on per test through pytest-django's ``settings``
fixture, exactly as production configures them: five https origins, the four
web origins trusted for CSRF, the five hosts allowed. Each host gets its own
``Client``, because a browser keeps cookies per host and Django's test client
keeps one jar.
"""

from __future__ import annotations

import json
import secrets

import pytest
from django.test import Client

PASSWORD = "correct horse battery staple 42"

ORIGINS = {
    "admin": "https://admin.workeasy360.com",
    "partner": "https://partner.workeasy360.com",
    "app": "https://app.workeasy360.com",
    "auth": "https://auth.workeasy360.com",
    "api": "https://api.workeasy360.com",
}
HOST = {name: origin.removeprefix("https://") for name, origin in ORIGINS.items()}
WEB = ("admin", "partner", "app", "auth")


@pytest.fixture
def portals_on(settings):
    settings.PORTALS_ENABLED = True
    settings.W360_PORTAL_ORIGINS = dict(ORIGINS)
    settings.ALLOWED_HOSTS = list(HOST.values())
    settings.CSRF_TRUSTED_ORIGINS = [ORIGINS[n] for n in WEB]
    settings.W360_FRONTEND_URL = ORIGINS["app"]
    return settings


def new_state() -> str:
    return secrets.token_urlsafe(32)


class HostClient:
    """A browser's view of one host: its own cookie jar, every request sent
    with that ``Host`` over https."""

    def __init__(self, portal: str, *, enforce_csrf: bool = False):
        self.portal = portal
        self.host = HOST[portal]
        self.client = Client(enforce_csrf_checks=enforce_csrf)

    def get(self, path, **extra):
        return self.client.get(path, HTTP_HOST=self.host, secure=True, **extra)

    def post(self, path, body=None, **extra):
        return self.client.post(
            path,
            json.dumps(body or {}),
            content_type="application/json",
            HTTP_HOST=self.host,
            secure=True,
            **extra,
        )

    @property
    def session(self):
        return self.client.session

    def set_cookie(self, name: str, value: str) -> None:
        self.client.cookies[name] = value


@pytest.fixture
def host_client():
    return HostClient


@pytest.fixture
def sign_in(portals_on):
    """Sign ``user`` in on the central sign-in host; returns that host's
    client. ``aal2`` records a completed step-up on that session."""
    from workeasy360.platform.tenancy.resolution import record_step_up

    def make(user, *, aal2: bool = False):
        auth = HostClient("auth")
        auth.get("/api/v1/auth/session")
        response = auth.post(
            "/api/v1/auth/login", {"email": user.email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.content
        if aal2:
            session = auth.session
            record_step_up(session)
            session.save()
        return auth

    return make


@pytest.fixture
def hand_off():
    """Issue on ``auth`` and redeem on a fresh client of the target portal,
    the way the browser does. Returns ``(portal_client, issue_response,
    redeem_response)``; the portal client is ``None`` when issuing failed."""

    def run(auth, mode, *, next_path=None, portal=None):
        state = new_state()
        issued = auth.post(
            "/api/v1/auth/handoff", {"mode": mode, "next": next_path, "state": state}
        )
        if issued.status_code != 200:
            return None, issued, None
        target = portal or {"admin": "admin", "partner": "partner", "user": "app"}[mode]
        client = HostClient(target)
        client.set_cookie("w360_portal_state", state)
        code = issued.json()["redirect_url"].split("#code=", 1)[1]
        redeemed = client.post("/api/v1/auth/handoff/redeem", {"code": code})
        return client, issued, redeemed

    return run


def code_of(issued) -> str:
    return issued.json()["redirect_url"].split("#code=", 1)[1]


@pytest.fixture
def customer(organization_factory):
    """An ordinary customer: an organization owner with a password."""
    org, membership, user = organization_factory()
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    return {"org": org, "membership": membership, "user": user}


@pytest.fixture
def staff_user(user_factory):
    """A staff identity holding one StaffGrant, with a password."""
    from workeasy360.platform.authz import staff

    root = user_factory(is_staff=True)

    def make(*permissions: str, mfa: bool = True):
        user = user_factory(is_staff=True, password=PASSWORD)
        for permission in permissions or ("platform.staff_customer.view",):
            staff.create_grant(
                user_id=user.id,
                permission=permission,
                granted_by_id=root.id,
                reason="Test fixture grants a control-plane permission",
            )
        if mfa:
            enrol_totp(user)
        return user

    return make


def enrol_totp(user) -> None:
    from django.utils import timezone

    from workeasy360.platform.identity.models import AuthFactor

    AuthFactor.objects.create(
        user=user,
        kind="totp",
        label="phone",
        secret_ref="test",
        confirmed_at=timezone.now(),
    )


@pytest.fixture
def partner_member(partner_factory):
    fx = partner_factory("customer.provision", "subscription.sell")
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    return fx

"""Login as Guest and the three partner categories (ADR-062): fixtures.

Partners are created the way the Admin creates them (``tests/partner_experience``:
Add Partner, Complete Your Profile, Activate), and the portals are switched on
afterwards, per test, exactly as production configures them
(``tests/portals``), because the partner fixtures speak to the single host.
"""

from __future__ import annotations

import json

import pytest

from tests.partner_experience import conftest as _pexp
from tests.portals import conftest as _portals

PASSWORD = _portals.PASSWORD
ORIGINS = _portals.ORIGINS
HOST = _portals.HOST
WEB = _portals.WEB
HostClient = _portals.HostClient

GUEST = "platform.staff_partner.guest"
VIEW = _pexp.VIEW
MANAGE = _pexp.MANAGE
PRICING = _pexp.PRICING
COMMISSION = _pexp.COMMISSION

send = _pexp.send
admin_path = _pexp.admin_path
make_body = _pexp.make_body
CREATE = _pexp.CREATE

staff_client = _pexp.staff_client
#: A staff identity with a password and grants, not signed in anywhere.
staff_user = _portals.staff_user
channel = _pexp.channel
reseller = _pexp.reseller
white_label = _pexp.white_label
affiliate = _pexp.affiliate
http_login = _pexp.http_login
partner_client = _pexp.partner_client
console = _pexp.console

START = "/guest-view"
ENTER = "/api/v1/partner/guest-view/enter"
END = "/api/v1/partner/guest-view/end"
REASON = "Checking the partner's dashboard for a support case"


@pytest.fixture
def admin(staff_client):
    """The Super Admin of these tests, holding Login as Guest's permission."""
    client, user = staff_client(GUEST, MANAGE, VIEW, PRICING, COMMISSION)
    client.staff_user = user  # type: ignore[attr-defined]
    return client


def enable_portals(settings) -> None:
    """The five production origins (``tests/portals``), switched on mid-test."""
    settings.PORTALS_ENABLED = True
    settings.W360_PORTAL_ORIGINS = dict(ORIGINS)
    settings.ALLOWED_HOSTS = list(HOST.values())
    settings.CSRF_TRUSTED_ORIGINS = [ORIGINS[n] for n in WEB]
    settings.W360_FRONTEND_URL = ORIGINS["app"]


def admin_host_sign_in(user) -> HostClient:
    """The staff member signing in on admin. itself (ADR-059: no hand-off)."""
    client = HostClient("admin")
    client.get("/api/v1/auth/session")
    response = client.post(
        "/api/v1/auth/login", {"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.content
    return client


def start_on_admin(client: HostClient, partner_id, **body):
    return client.post(
        f"/api/v1/admin/partners/{partner_id}{START}",
        {"reason": REASON, **body},
    )


def form_post(client: HostClient, path: str, data: dict, *, origin: str | None):
    """A top-level form post, the way the Admin page sends the ticket."""
    extra = {"HTTP_ORIGIN": origin} if origin is not None else {}
    return client.client.post(path, data, HTTP_HOST=client.host, secure=True, **extra)


def enter_partner_host(ticket: str, *, origin: str | None = None) -> tuple:
    partner = HostClient("partner")
    response = form_post(
        partner,
        ENTER,
        {"ticket": ticket},
        origin=ORIGINS["admin"] if origin is None else origin,
    )
    return partner, response


def put_json(client: HostClient, path: str, body: dict):
    return client.client.put(
        path,
        json.dumps(body),
        content_type="application/json",
        HTTP_HOST=client.host,
        secure=True,
    )

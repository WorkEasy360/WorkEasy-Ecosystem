"""Admin-created partner + type-specific panels (ADR-047): fixtures.

Everything is reused: the Phase 7 channel, the Phase 10 console, the Partner
Control Center fixtures and the real HTTP clients that sign in through
``/api/v1/auth/login``. Nothing here creates a partner, a user or a staff member
any other way than the commands under test do.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.partner_center import conftest as _center
from tests.phase10 import conftest as _phase10

# Re-exported by assignment (not import) so that using a fixture as a test
# argument is not a redefinition of an imported name.
PASSWORD = _center.PASSWORD
VIEW = _center.VIEW
MANAGE = _center.MANAGE
PRICING = "platform.staff_pricing.manage"
COMMISSION = "platform.staff_commission.manage"
channel = _center.channel
http_login = _center.http_login
partner_client = _center.partner_client
staff_client = _center.staff_client
console = _center.console
count_queries = _center.count_queries
post = _center.post
anonymous = _phase10.anonymous

#: Typed by Super Admin in these tests. Long, uncommon, not like the address.
ADMIN_PASSWORD = "Violet Harbor Lantern 2049"
ADMIN_EMAIL = "pat.owner@acme-partner.example.com"

CREATE = "/api/v1/admin/partners/create"
CHECK = "/api/v1/admin/partners/credentials-check"


def make_body(
    experience: str = "reseller",
    *,
    name: str = "Acme Partner",
    email: str = ADMIN_EMAIL,
    password: str | None = ADMIN_PASSWORD,
    confirm: str | None = None,
    mode: str = "create",
    country: str | None = "GB",
    **overrides: Any,
) -> dict[str, Any]:
    """The Add Partner form as the browser sends it (ADR-048): the partner's
    name, the administrator's email and password, the type, and a country."""
    administrator: dict[str, Any] = {"email": email, "mode": mode}
    if password is not None:
        administrator["password"] = password
        administrator["password_confirm"] = password if confirm is None else confirm
    partner: dict[str, Any] = {"display_name": name}
    if country is not None:
        partner["country"] = country
    body: dict[str, Any] = {
        "experience": experience,
        "partner": partner,
        "administrator": administrator,
    }
    body.update(overrides)
    return body


def send(client, url: str, body: dict[str, Any] | None = None):
    return client.post(url, json.dumps(body or {}), content_type="application/json")


@pytest.fixture
def admin(staff_client):
    """A signed-in staff member who may create partners, price and set commission."""
    client, user = staff_client(MANAGE, VIEW, PRICING, COMMISSION)
    client.staff_user = user  # type: ignore[attr-defined]
    return client


@pytest.fixture
def manager_only(staff_client):
    """May create partners but not price or set commission."""
    client, _user = staff_client(MANAGE, VIEW)
    return client


def sign_in(email: str, password: str):
    """A browser signing in through the real endpoint."""
    from django.test import Client

    client = Client()
    client.get("/api/v1/auth/session")
    response = client.post(
        "/api/v1/auth/login",
        {"email": email, "password": password},
        content_type="application/json",
    )
    return client, response


def admin_path(partner_id: str, tail: str = "") -> str:
    return f"/api/v1/admin/partners/{partner_id}{tail}"


def _version(admin, partner_id: str) -> int:
    response = admin.get(admin_path(partner_id))
    assert response.status_code == 200, response.content
    return response.json()["version"]


def complete_profile(
    admin,
    partner_id: str,
    *,
    business: bool = True,
    products=("crm",),
    commission: bool = True,
    brand: bool = True,
    referral: bool = True,
) -> None:
    """Fill in what the partner's capabilities make its profile need, through the
    same staff endpoints the Complete Your Profile wizard calls. Each step runs
    only when the partner holds what makes it apply."""
    summary = admin.get(admin_path(partner_id, "/summary")).json()
    held = {c["key"] for c in summary["capabilities"]}
    if business:
        response = admin.put(
            admin_path(partner_id, "/profile"),
            json.dumps(
                {
                    "expected_version": _version(admin, partner_id),
                    "business": {
                        "business_email": "hello@partner.example.com",
                        "country": "GB",
                    },
                    "contacts": {
                        "primary": {
                            "name": "Pat Owner",
                            "email": "pat@partner.example.com",
                        }
                    },
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
    sells = held & {
        "customer.provision",
        "customer.manage",
        "subscription.sell",
        "subscription.manage",
        "branding.manage",
        "domain.manage",
    }
    if sells:
        for code in products:
            response = send(
                admin, admin_path(partner_id, "/product-grants"), {"product_code": code}
            )
            assert response.status_code == 201, response.content
    if commission and "commission.earn" in held:
        response = send(
            admin,
            admin_path(partner_id, "/commission"),
            {"rate_bp": 1500, "reason": "Agreed partner rate", "expected_version": 0},
        )
        assert response.status_code in (200, 201), response.content
    if brand and held & {"branding.manage", "domain.manage"}:
        response = admin.put(
            admin_path(partner_id, "/brand"),
            json.dumps({"brand_name": "Partner Brand", "theme": {}}),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
    if referral and "referral.create" in held:
        response = send(
            admin, admin_path(partner_id, "/referral-codes"), {"label": "Launch"}
        )
        assert response.status_code == 201, response.content


def activate(admin, partner_id: str) -> Any:
    return send(
        admin,
        admin_path(partner_id, "/status"),
        {
            "status": "active",
            "reason": "Profile complete, activating",
            "expected_version": _version(admin, partner_id),
        },
    )


def provisioned(
    admin,
    experience: str,
    *,
    code: str = "",
    email: str,
    products=("crm",),
    name: str | None = None,
):
    """Create a partner through Add Partner, complete its profile and activate
    it as staff would, then sign its administrator in with the typed password."""
    response = send(
        admin,
        CREATE,
        make_body(
            experience,
            name=name or code.replace("-", " ").title() or "Partner",
            email=email,
        ),
    )
    assert response.status_code == 201, response.content
    partner_id = response.json()["id"]
    # The portal tests below count referral codes and brand progress from zero,
    # so those two are left for the tests themselves to create.
    complete_profile(admin, partner_id, products=products, brand=False, referral=False)
    activated = activate(admin, partner_id)
    assert activated.status_code == 200, activated.content
    client, login = sign_in(email, ADMIN_PASSWORD)
    assert login.status_code == 200, login.content
    return client, partner_id


@pytest.fixture
def reseller(catalog, admin):
    return provisioned(admin, "reseller", code="rs-one", email="owner@rs-one.example.com")


@pytest.fixture
def white_label(catalog, admin):
    return provisioned(
        admin, "white_label", code="wl-one", email="owner@wl-one.example.com"
    )


@pytest.fixture
def affiliate(catalog, admin):
    return provisioned(
        admin, "affiliate", code="af-one", email="owner@af-one.example.com"
    )

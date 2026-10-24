"""Partner upgrade (ADR-049): fixtures.

Everything is reused from the admin-created-partner suite: the same Add Partner
command creates the partner, the same staff endpoints complete and activate it,
and the same real sign-in endpoint opens its portal. Nothing here creates a
partner, a user or a grant any other way than the commands under test do - so a
test that says "the same login" means the very same ``identity.User`` row.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.partner_experience import conftest as _pe

PASSWORD = _pe.ADMIN_PASSWORD
admin = _pe.admin
manager_only = _pe.manager_only
sign_in = _pe.sign_in
send = _pe.send
admin_path = _pe.admin_path
provisioned = _pe.provisioned
staff_client = _pe.staff_client
http_login = _pe.http_login
count_queries = _pe.count_queries
post = _pe.post
channel = _pe.channel
partner_client = _pe.partner_client
console = _pe.console
anonymous = _pe.anonymous


def pp(partner_id: str, tail: str = "") -> str:
    """A partner-portal API path."""
    return f"/api/v1/partner/{partner_id}{tail}"


def put(client, url: str, body: dict[str, Any]):
    return client.put(url, json.dumps(body), content_type="application/json")


def make_affiliate(admin, *, email: str, code: str = "af-up", with_code: bool = True):
    """An active affiliate that has signed in, with the referral code its profile
    needs (the completeness rule asks for one while it holds the referral set)."""
    client, partner_id = provisioned(admin, "affiliate", code=code, email=email)
    if with_code:
        response = send(
            admin, admin_path(partner_id, "/referral-codes"), {"label": "Launch"}
        )
        assert response.status_code == 201, response.content
    return client, partner_id


def make_reseller(admin, *, email: str, code: str = "rs-up"):
    return provisioned(admin, "reseller", code=code, email=email)


def start(client, partner_id: str, target: str):
    return send(client, pp(partner_id, "/upgrade/requests"), {"experience": target})


def business_step(client, partner_id: str, *, phone: str = "+44 20 7946 0958") -> None:
    """Step 1 of the wizard: the business details go to the partner's own
    profile through the ordinary profile command."""
    profile = client.get(pp(partner_id, "/profile"))
    assert profile.status_code == 200, profile.content
    response = put(
        client,
        pp(partner_id, "/profile"),
        {
            "expected_version": profile.json()["version"],
            "business": {
                "phone": phone,
                "legal_name": "Acme Partner Ltd",
                "tax_number": "GB123456789",
                "website": "https://acme-partner.example.com",
            },
            "contacts": {
                "billing": {"name": "Bea Billing", "email": "billing@acme.example.com"}
            },
        },
    )
    assert response.status_code == 200, response.content


def save(client, partner_id: str, request: dict[str, Any], section: str, data: Any):
    """Save one wizard section; returns the response (the caller reads the new
    request, whose ``version`` the next save must send)."""
    return put(
        client,
        pp(partner_id, f"/upgrade/requests/{request['id']}/sections/{section}"),
        {"data": data, "expected_version": request["version"]},
    )


def fill(
    client, partner_id: str, request: dict[str, Any], target: str, *, products=("crm",)
):
    """Everything the wizard collects for ``target``, saved through the real
    endpoints. Returns the request as last saved."""
    business_step(client, partner_id)
    sections: list[tuple[str, Any]] = []
    if target == "white_label":
        sections.append(
            (
                "brand",
                {
                    "brand_name": "Acme Cloud",
                    "primary_color": "#1a73e8",
                    "secondary_color": "#34a853",
                    "support_email": "help@acme.example.com",
                },
            )
        )
    sections += [
        ("products", {"products": list(products)}),
        ("pricing", {"preference": "negotiated", "notes": "Volume discount please"}),
        (
            "commercial",
            {"expected_customers": 40, "team_size": 3, "customer_management": "CRM sync"},
        ),
    ]
    if target == "white_label":
        sections.append(("domain", {"hostname": "app.acme-partner.example.org"}))
    current = request
    for section, data in sections:
        response = save(client, partner_id, current, section, data)
        assert response.status_code == 200, (section, response.content)
        current = response.json()
    return current


def submit(client, partner_id: str, request: dict[str, Any]):
    return send(
        client,
        pp(partner_id, f"/upgrade/requests/{request['id']}/submit"),
        {"expected_version": request["version"]},
    )


def decide(admin, partner_id: str, request_id: str, action: str, **body: Any):
    return send(
        admin,
        admin_path(partner_id, f"/upgrades/{request_id}/decision"),
        {"action": action, **body},
    )


def request_upgrade(client, partner_id: str, target: str, **kw):
    """Start, fill and submit an upgrade through the partner's own endpoints."""
    started = start(client, partner_id, target)
    assert started.status_code == 201, started.content
    filled = fill(client, partner_id, started.json(), target, **kw)
    submitted = submit(client, partner_id, filled)
    assert submitted.status_code == 200, submitted.content
    return submitted.json()


def portal(client) -> list[dict[str, Any]]:
    response = client.get("/api/v1/partner/portal")
    assert response.status_code == 200, response.content
    return response.json()["partners"]


@pytest.fixture
def affiliate(catalog, admin):
    return make_affiliate(admin, email="owner@af-up.example.com")


@pytest.fixture
def reseller(catalog, admin):
    return make_reseller(admin, email="owner@rs-up.example.com")

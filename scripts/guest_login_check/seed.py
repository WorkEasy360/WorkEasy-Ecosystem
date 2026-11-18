"""Partners for the Login as Guest check, created the way the Super Admin creates them.

Add Partner (Partner Email + password), Complete Your Profile, Activate - all through
the Admin's own endpoints, on the Admin host. One partner of each type is activated;
one more is left a draft. Prints the ids the browser journeys need as JSON.

Scratch databases only. Every password is ``CHECK_PASSWORD``.
"""

# ruff: noqa: S105, S106

import json
import os

import django

django.setup()

from django.test import Client  # noqa: E402

PASSWORD = os.environ["CHECK_PASSWORD"]
STAFF = os.environ["PLATFORM_SUPERADMIN_EMAIL"]
ADMIN_HOST = "admin.workeasy360.com"
PRODUCT = "easy_crm"


def signed_in(email: str) -> Client:
    client = Client(HTTP_HOST=ADMIN_HOST)
    client.get("/api/v1/auth/session")
    response = client.post(
        "/api/v1/auth/login",
        {"email": email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 200, (email, response.content)
    return client


def send(client: Client, url: str, body=None, *, method="post", want=(200, 201)):
    response = getattr(client, method)(
        url, json.dumps(body or {}), content_type="application/json"
    )
    assert response.status_code in want, (url, response.status_code, response.content)
    return response.json()


admin = signed_in(STAFF)
A = "/api/v1/admin/partners"


def version(partner_id: str) -> int:
    return admin.get(f"{A}/{partner_id}").json()["version"]


def create(kind: str, name: str, email: str) -> str:
    created = send(
        admin,
        f"{A}/create",
        {
            "experience": kind,
            "partner": {"display_name": name, "country": "IN"},
            "administrator": {
                "email": email,
                "mode": "create",
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
        },
    )
    return created["id"]


def complete(partner_id: str) -> None:
    held = {c["key"] for c in admin.get(f"{A}/{partner_id}/summary").json()["capabilities"]}
    send(
        admin,
        f"{A}/{partner_id}/profile",
        {
            "expected_version": version(partner_id),
            "business": {"business_email": "hello@partner.example.com", "country": "IN"},
            "contacts": {"primary": {"name": "Pat Owner", "email": "pat@partner.example.com"}},
        },
        method="put",
    )
    if held & {"customer.provision", "subscription.sell", "branding.manage"}:
        send(admin, f"{A}/{partner_id}/product-grants", {"product_code": PRODUCT})
    if "commission.earn" in held:
        send(
            admin,
            f"{A}/{partner_id}/commission",
            {"rate_bp": 1500, "reason": "Agreed partner rate", "expected_version": 0},
        )
    if held & {"branding.manage", "domain.manage"}:
        send(admin, f"{A}/{partner_id}/brand", {"brand_name": "Guest WL Brand", "theme": {}}, method="put")
    if "referral.create" in held:
        send(admin, f"{A}/{partner_id}/referral-codes", {"label": "Launch"})


def activate(partner_id: str) -> None:
    send(
        admin,
        f"{A}/{partner_id}/status",
        {"status": "active", "reason": "Seeded for the guest check", "expected_version": version(partner_id)},
    )


ids = {}
for kind, name, email in (
    ("affiliate", "Guest Affiliate Co", "owner@guest-affiliate.dev"),
    ("reseller", "Guest Reseller Co", "owner@guest-reseller.dev"),
    ("white_label", "Guest WL Co", "owner@guest-wl.dev"),
):
    partner_id = create(kind, name, email)
    complete(partner_id)
    activate(partner_id)
    ids[kind] = partner_id
ids["draft"] = create("affiliate", "Guest Draft Co", "owner@guest-draft.dev")
print("SEED " + json.dumps(ids))

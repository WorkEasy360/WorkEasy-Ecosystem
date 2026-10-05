"""What a partner does NOT learn about its customers (Task 123).

A partner sees what it sold and whether it is healthy, not who works there.
"""

from __future__ import annotations

import json

import pytest

from tests.phase7.support import provision, sell

pytestmark = pytest.mark.django_db

ALLOWED_CUSTOMER_KEYS = {
    "organization_id",
    "name",
    "organization_status",
    "link_status",
    "started_at",
    "ended_at",
    "subscription_status",
    "products",
    # Phase 10e: the state of the owner's activation, its expiry and how many
    # links were sent - a partner may know whether its customer has activated,
    # never the address the link went to or the link.
    "owner_activation",
}
ALLOWED_OWNER_ACTIVATION_KEYS = {"activation", "expires_at", "send_count"}


def test_a_partners_customer_list_carries_no_personal_data(channel, http_login):
    from tests.phase7.conftest import PASSWORD

    fx = channel
    result, owner = provision(fx, name="Privacy Customer")
    sell(fx, result.organization_id)
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    client = http_login(fx["user"])

    body = client.get(f"/api/v1/partner/{fx['partner'].id}/customers").json()
    assert body["results"], "the fixture must produce a customer"
    for row in body["results"]:
        assert set(row) == ALLOWED_CUSTOMER_KEYS, set(row) ^ ALLOWED_CUSTOMER_KEYS
        assert set(row["owner_activation"]) == ALLOWED_OWNER_ACTIVATION_KEYS
    text = json.dumps(body).lower()
    assert owner.email.lower() not in text and "@" not in text


def test_a_partners_diagnostic_names_no_one_it_was_not_asked_about(
    channel, http_login, user_factory
):
    from tests.phase7.conftest import PASSWORD
    from workeasy360.commercial.partners import services

    fx = channel
    services.grant_capability(
        fx["staff_ctx"],
        fx["partner"].id,
        capability="support.access",
        reason="Fixture support access",
    )
    result, owner = provision(fx, name="Privacy Customer")
    sell(fx, result.organization_id)
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    client = http_login(fx["user"])

    response = client.post(
        f"/api/v1/partner/{fx['partner'].id}/customers/{result.organization_id}/diagnose",
        json.dumps({"product_code": "crm"}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    text = json.dumps(response.json()).lower()
    # No member list, no owner address: the partner asked about a product, not a person.
    assert owner.email.lower() not in text and "@" not in text


def test_the_public_referral_endpoints_reveal_nothing_about_partners(
    client, partner_factory
):
    from workeasy360.commercial.commissions import referrals

    fx = partner_factory("referral.create", "commission.earn")
    code = referrals.create_referral_code(fx["actor"](), label="private label")
    ok = client.post(
        "/api/v1/public/referrals",
        json.dumps({"code": code.code}),
        content_type="application/json",
    )
    assert ok.status_code == 201 and set(ok.json()) == {"token"}
    text = ok.content.decode().lower()
    assert str(fx["partner"].id) not in text and "private label" not in text
    missing = client.post(
        "/api/v1/public/referrals",
        json.dumps({"code": "ZZZZZZZZ"}),
        content_type="application/json",
    )
    assert missing.status_code == 404

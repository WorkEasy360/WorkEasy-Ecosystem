"""The new partner endpoints through the real HTTP stack (ADR-063): sign-in,
the partner id checked against the membership, CSRF, the permission, an online
top-up through the test provider's signed callback, and the Admin's policies."""

from __future__ import annotations

import json
import uuid

import pytest

from tests.phase7.support import CAPS, provision
from workeasy360.commercial.partners import capabilities, services

pytestmark = pytest.mark.django_db

TEST_COMPLETE = "/api/v1/public/payments/test-checkout/complete"


def pp(partner_id, path=""):
    return f"/api/v1/partner/{partner_id}{path}"


def post(client, path, body, **extra):
    return client.post(path, json.dumps(body), content_type="application/json", **extra)


@pytest.fixture
def reseller(partner_client, catalog, staff_factory, user_factory):
    """A signed-in reseller administrator with CRM granted, a 20% purchase
    discount and a customer."""
    from workeasy360.commercial.commissions import margins
    from workeasy360.commercial.partners.access import partner_actor

    client, fx = partner_client(*CAPS)
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    _u, margin_ctx = staff_factory("platform.staff_commission.manage")
    margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Launch terms"
    )
    fx.update(
        catalog=catalog,
        user_factory=user_factory,
        actor=lambda: partner_actor(user_id=fx["user"].id, partner_id=fx["partner"].id),
    )
    managed, _owner = provision(fx)
    fx["org"] = managed.organization_id
    return client, fx


def test_reads_answer_a_signed_in_reseller(reseller):
    client, fx = reseller
    pid = fx["partner"].id
    for path in (
        "/platform-wallet",
        "/platform-wallet/transactions",
        "/subscriptions?status=expiring",
        "/billing-records",
        "/help-articles",
        "/sales-resources",
        f"/customers/{fx['org']}/products-setup",
    ):
        response = client.get(pp(pid, path))
        assert response.status_code == 200, (path, response.content)
    wallet = client.get(pp(pid, "/platform-wallet")).json()
    assert wallet["currency"] == "GBP"
    assert wallet["wallets"][0]["balance_minor"] == 0
    assert wallet["top_up"]["allowed"] is True
    assert client.get(pp(pid, "/subscriptions?status=nonsense")).status_code == 400


def test_nobody_else_reads_them(reseller, client):
    _client, fx = reseller
    pid = fx["partner"].id
    # Anonymous.
    assert client.get(pp(pid, "/platform-wallet")).status_code in (401, 403)
    # Another partner's member: the partner does not exist for them.
    other_client, _other = _signed_in_other(reseller)
    assert other_client.get(pp(pid, "/platform-wallet")).status_code == 404
    assert (
        other_client.get(pp(pid, f"/customers/{fx['org']}/products-setup")).status_code
        == 404
    )


def _signed_in_other(reseller):
    client, fx = reseller
    del client
    from django.test import Client

    from tests.phase7.conftest import PASSWORD

    other = services.create_partner(
        fx["staff_ctx"],
        code=f"o-{uuid.uuid4().hex[:8]}",
        display_name="Other Partner",
        payout_currency="GBP",
        contact_email="ops@other.example.com",
    )
    user = fx["user_factory"](password=PASSWORD)
    services.add_member(fx["staff_ctx"], other.id, email=user.email, role="admin")
    web = Client()
    web.get("/api/v1/auth/session")
    assert (
        web.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": PASSWORD},
            content_type="application/json",
        ).status_code
        == 200
    )
    return web, other


def test_an_affiliate_has_no_platform_wallet(partner_client):
    client, fx = partner_client(capabilities.REFERRAL_CREATE, capabilities.REFERRAL_VIEW)
    assert client.get(pp(fx["partner"].id, "/platform-wallet")).status_code == 403
    assert (
        post(
            client,
            pp(fx["partner"].id, "/platform-wallet/top-up"),
            {"amount_minor": 10_000},
        ).status_code
        == 403
    )


def test_activation_over_http_needs_an_idempotency_key_and_the_amount(reseller):
    client, fx = reseller
    pid, org = fx["partner"].id, fx["org"]
    body = {"plan_version_id": str(fx["catalog"]["versions"]["crm_professional"].id)}
    quote = post(client, pp(pid, f"/customers/{org}/activation/quote"), body)
    assert quote.status_code == 200, quote.content
    assert quote.json()["charge_minor"] == 3999
    assert quote.json()["wallet"]["sufficient"] is False
    # No key, no activation.
    refused = post(
        client,
        pp(pid, f"/customers/{org}/activation"),
        {**body, "expected_charge_minor": 3999},
    )
    assert refused.status_code == 400
    # Insufficient balance is a 409 that changes nothing.
    short = post(
        client,
        pp(pid, f"/customers/{org}/activation"),
        {**body, "expected_charge_minor": 3999},
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert short.status_code == 409
    assert short.json()["code"] == "insufficient_balance"


def test_csrf_is_enforced_on_the_new_writes(partner_client):
    client, fx = partner_client(*CAPS, enforce_csrf=True)
    response = post(
        client, pp(fx["partner"].id, "/platform-wallet/top-up"), {"amount_minor": 10_000}
    )
    assert response.status_code == 403


def test_an_online_top_up_credits_the_wallet_once(
    reseller, fake_payments, django_capture_on_commit_callbacks
):
    from tests.partner_admin_panel.conftest import balance

    client, fx = reseller
    pid = fx["partner"].id
    started = post(client, pp(pid, "/platform-wallet/top-up"), {"amount_minor": 50_000})
    assert started.status_code == 201, started.content
    payment = started.json()
    assert payment["status"] == "awaiting_payment"
    reference = payment["checkout_url"].split("reference=", 1)[1].split("&", 1)[0]
    with django_capture_on_commit_callbacks(execute=True):
        done = post(
            client,
            TEST_COMPLETE,
            {"organization_id": "", "reference": reference, "outcome": "succeeded"},
        )
    assert done.status_code in (200, 201, 202), done.content
    assert balance(fx) == 50_000
    # The provider repeating its callback pays nothing twice.
    with django_capture_on_commit_callbacks(execute=True):
        post(
            client,
            TEST_COMPLETE,
            {"organization_id": "", "reference": reference, "outcome": "succeeded"},
        )
    assert balance(fx) == 50_000
    wallet = client.get(pp(pid, "/platform-wallet")).json()
    assert wallet["top_ups"][0]["status"] == "succeeded"
    entry = wallet["recent"][0]
    assert entry["entry_type"] == "top_up" and entry["amount_minor"] == 50_000
    records = client.get(pp(pid, "/billing-records")).json()
    assert records["partner_payments"][0]["label"] == "Platform Wallet top-up"


def test_the_admin_sets_the_partner_policies(staff_client):
    admin, _user = staff_client(
        "platform.staff_settings.view", "platform.staff_settings.manage"
    )
    current = admin.get("/api/v1/admin/settings").json()
    assert current["partner_legacy_kyc_policy"] == "allow"
    assert current["partner_suspended_commission_policy"] == "accrue"
    body = {
        **{k: current[k] for k in ("signup_enabled", "partner_applications_enabled")},
        "partner_legacy_kyc_policy": "restrict",
        "partner_suspended_commission_policy": "stop",
        "version": current["version"],
    }
    saved = admin.put(
        "/api/v1/admin/settings", json.dumps(body), content_type="application/json"
    )
    assert saved.status_code == 200, saved.content
    assert saved.json()["partner_legacy_kyc_policy"] == "restrict"
    from workeasy360.platform.administration.site import partner_policies

    assert partner_policies() == {
        "legacy_kyc": "restrict",
        "suspended_commission": "stop",
    }
    bad = admin.put(
        "/api/v1/admin/settings",
        json.dumps(
            {
                **body,
                "partner_legacy_kyc_policy": "maybe",
                "version": saved.json()["version"],
            }
        ),
        content_type="application/json",
    )
    assert bad.status_code == 400

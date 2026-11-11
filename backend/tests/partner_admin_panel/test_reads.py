"""What the partner reads (ADR-063, the specification's 4, 6.2 and 7.1): the
subscription filters, the dashboard's onboarding, referral and wallet sections,
and a customer's products and setup - separate facts, the partner's own only."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.partner_admin_panel.conftest import in_org, plan_id, top_up
from tests.phase7.support import provision, sell
from workeasy360.commercial.controlplane import (
    partner_billing,
    partner_portal,
    partner_wallet,
)

pytestmark = pytest.mark.django_db


def _wallet_customer(fx):
    managed, _owner = provision(fx, name="Wallet Customer")
    partner_wallet.activate(
        fx["actor"](),
        managed.organization_id,
        {"plan_version_id": plan_id(fx), "expected_charge_minor": 3999},
        idempotency_key=uuid.uuid4().hex,
    )
    return managed.organization_id


def _period_ends_in(org_id, days):
    from workeasy360.commercial.subscriptions.models import Subscription

    now = timezone.now()
    with in_org(org_id):
        Subscription.unscoped.filter(organization_id=org_id).update(
            current_period_start=now - timedelta(days=30 - days),
            current_period_end=now + timedelta(days=days),
        )


def test_the_subscription_filters(wallet_channel):
    fx = wallet_channel
    top_up(fx, 20_000)
    wallet_org = _wallet_customer(fx)
    direct, _owner = provision(fx, name="Card Customer")
    sell(fx, direct.organization_id)

    everything = partner_billing.subscriptions(fx["actor"]())
    assert everything["counts"]["all"] == 2
    by_customer = {row["customer"]: row for row in everything["items"]}
    assert by_customer["Wallet Customer"]["paid_by"] == "partner_wallet"
    assert by_customer["Card Customer"]["paid_by"] == "customer"
    line = by_customer["Wallet Customer"]["lines"][0]
    assert line["plan_name"] and line["order"]["number"].startswith("ORD-")

    # A wallet renewal due within 30 days and not yet paid is "expiring".
    _period_ends_in(wallet_org, 5)
    expiring = partner_billing.subscriptions(fx["actor"](), status="expiring")
    assert [r["customer"] for r in expiring["items"]] == ["Wallet Customer"]
    # Renewing it ahead takes it off the list.
    sub_id = expiring["items"][0]["subscription_id"]
    partner_wallet.renew(
        fx["actor"](), wallet_org, uuid.UUID(sub_id), expected_charge_minor=3999
    )
    assert partner_billing.subscriptions(fx["actor"](), status="expiring")["items"] == []
    assert (
        partner_billing.subscriptions(fx["actor"](), status="active")["counts"]["active"]
        == 2
    )
    assert partner_billing.subscriptions(fx["actor"](), q="card")["counts"]["all"] == 1


def test_another_partners_customers_are_not_listed(wallet_channel, partner_factory):
    from tests.phase7.support import CAPS

    fx = wallet_channel
    top_up(fx, 20_000)
    _wallet_customer(fx)
    other = partner_factory(*CAPS)
    assert partner_billing.subscriptions(other["actor"]())["items"] == []


def test_the_dashboard_keeps_kyc_account_wallet_and_earnings_apart(wallet_channel):
    fx = wallet_channel
    top_up(fx, 20_000)
    board = partner_portal.dashboard(fx["actor"]())
    assert board["onboarding"]["kyc_status"] == "draft"
    assert board["onboarding"]["account_status"] == "active"
    assert board["onboarding"]["cleared"] is True
    assert "reseller" in board["onboarding"]["permissions"]
    assert board["platform_wallet"][0] == {
        **board["platform_wallet"][0],
        "currency": "GBP",
        "balance_minor": 20_000,
    }
    # Earnings stay where they were, never added to the wallet.
    assert board["money"] is not None
    assert all(
        "balance_minor" not in m or m["currency"] for m in board["money"]["commission"]
    )


def test_a_customers_products_and_setup(wallet_channel):
    fx = wallet_channel
    top_up(fx, 20_000)
    org = _wallet_customer(fx)
    view = partner_billing.customer_products(fx["actor"](), org)
    assert [p["product_code"] for p in view["products"]] == ["crm"]
    crm = view["products"][0]
    assert crm["plans"][0]["subscription_status"] == "active"
    assert "answers" not in (crm["setup"] or {})

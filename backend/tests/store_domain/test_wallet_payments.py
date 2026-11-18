"""Paying a Store order from the Platform Wallet (ADR-065): in full, or part from the
wallet and the rest through the payment provider. The wallet's part is held - never
taken - until the provider confirms the rest; a payment that fails, expires or is
cancelled releases it; a retry never holds or charges twice; and nothing else may
spend money a hold reserves."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.extension.conftest import url
from tests.store_domain.conftest import (
    CRM_YEAR_TOTAL,
    attempts,
    balance,
    finish_at_provider,
    fund,
    holds,
    pay,
    place,
)

pytestmark = pytest.mark.django_db


def _order(shop, order_id):
    return shop["client"].get(url(shop["slug"], f"orders/{order_id}")).json()


class TestInFull:
    def test_the_wallet_pays_all_and_the_order_is_fulfilled_at_once(self, shop):
        wallet = fund(shop["id"], 1_500_000)
        order = place(shop)
        assert order["amount_due_minor"] == CRM_YEAR_TOTAL
        started = pay(shop, order["invoice_id"], wallet=CRM_YEAR_TOTAL)
        assert started.status_code == 202, started.content
        body = started.json()
        assert body["status"] == "succeeded" and body["provider"] == "wallet"
        assert body["wallet_amount_minor"] == CRM_YEAR_TOTAL
        assert _order(shop, order["order_id"])["status"] == "fulfilled"
        assert balance(wallet) == 1_500_000 - CRM_YEAR_TOTAL
        assert [a["provider_key"] for a in attempts(shop, order["invoice_id"])] == [
            "wallet"
        ]

    def test_a_retry_with_the_same_key_charges_once(self, shop):
        wallet = fund(shop["id"], 1_500_000)
        order = place(shop)
        key = uuid.uuid4().hex
        first = pay(shop, order["invoice_id"], wallet=CRM_YEAR_TOTAL, key=key)
        again = pay(shop, order["invoice_id"], wallet=CRM_YEAR_TOTAL, key=key)
        assert first.status_code == again.status_code == 202
        assert first.json()["attempt_id"] == again.json()["attempt_id"]
        assert balance(wallet) == 1_500_000 - CRM_YEAR_TOTAL
        assert len(attempts(shop, order["invoice_id"])) == 1

    def test_not_enough_in_the_wallet_is_refused_and_moves_nothing(self, shop):
        wallet = fund(shop["id"], 100_000)
        order = place(shop)
        refused = pay(shop, order["invoice_id"], wallet=CRM_YEAR_TOTAL)
        assert refused.status_code == 409, refused.content
        assert refused.json()["reason"] == "insufficient_balance"
        assert balance(wallet) == 100_000
        assert attempts(shop, order["invoice_id"]) == []
        assert _order(shop, order["order_id"])["status"] == "pending_payment"

    def test_more_than_the_invoice_owes_is_refused(self, shop):
        fund(shop["id"], 5_000_000)
        order = place(shop)
        refused = pay(shop, order["invoice_id"], wallet=CRM_YEAR_TOTAL + 1)
        assert refused.status_code == 400, refused.content

    @pytest.mark.parametrize("bad", [-1, "100", 1.5, True])
    def test_a_wallet_amount_that_is_not_a_whole_number_is_refused(self, shop, bad):
        fund(shop["id"], 5_000_000)
        order = place(shop)
        assert pay(shop, order["invoice_id"], wallet=bad).status_code == 400


class TestSplit:
    def test_part_from_the_wallet_part_from_the_provider(self, shop):
        wallet = fund(shop["id"], 500_000)
        order = place(shop)
        started = pay(shop, order["invoice_id"], wallet=500_000)
        assert started.status_code == 202, started.content
        body = started.json()
        # The provider is asked for the rest only; the wallet's part is held.
        assert body["amount_minor"] == CRM_YEAR_TOTAL - 500_000
        assert body["wallet_amount_minor"] == 500_000
        assert body["wallet_status"] == "held"
        assert holds(order["invoice_id"]) == [("held", 500_000)]
        assert balance(wallet) == 500_000  # held, not taken
        from workeasy360.commercial.wallets import ledger

        assert ledger.available(wallet.id) == 0

        finish_at_provider(shop, body["attempt_id"])
        assert _order(shop, order["order_id"])["status"] == "fulfilled"
        assert balance(wallet) == 0
        assert holds(order["invoice_id"]) == [("captured", 500_000)]
        paid = attempts(shop, order["invoice_id"])
        assert sorted(
            (a["provider_key"], a["status"], a["amount_minor"]) for a in paid
        ) == [
            ("test-provider", "succeeded", CRM_YEAR_TOTAL - 500_000),
            ("wallet", "succeeded", 500_000),
        ]
        invoice = (
            shop["client"]
            .get(url(shop["slug"], f"billing/invoices/{order['invoice_id']}"))
            .json()
        )
        assert invoice["status"] == "paid"
        assert invoice["amount_paid_minor"] == CRM_YEAR_TOTAL

    def test_a_failed_payment_releases_the_hold_and_takes_nothing(self, shop):
        wallet = fund(shop["id"], 500_000)
        order = place(shop)
        body = pay(shop, order["invoice_id"], wallet=500_000).json()
        finish_at_provider(shop, body["attempt_id"], outcome="failed")
        assert holds(order["invoice_id"]) == [("released", 500_000)]
        assert balance(wallet) == 500_000
        from workeasy360.commercial.wallets import ledger

        assert ledger.available(wallet.id) == 500_000
        assert _order(shop, order["order_id"])["status"] == "pending_payment"

    def test_after_a_failure_the_payer_can_try_again(self, shop):
        wallet = fund(shop["id"], 500_000)
        order = place(shop)
        first = pay(shop, order["invoice_id"], wallet=500_000).json()
        finish_at_provider(shop, first["attempt_id"], outcome="failed")
        second = pay(shop, order["invoice_id"], wallet=400_000).json()
        assert second["attempt_id"] != first["attempt_id"]
        assert second["amount_minor"] == CRM_YEAR_TOTAL - 400_000
        finish_at_provider(shop, second["attempt_id"])
        assert _order(shop, order["order_id"])["status"] == "fulfilled"
        assert balance(wallet) == 100_000

    def test_a_retry_holds_once(self, shop):
        fund(shop["id"], 500_000)
        order = place(shop)
        key = uuid.uuid4().hex
        a = pay(shop, order["invoice_id"], wallet=500_000, key=key).json()
        b = pay(shop, order["invoice_id"], wallet=500_000, key=key).json()
        # A second click with a new key while the first is in flight: the same payment.
        c = pay(shop, order["invoice_id"], wallet=500_000).json()
        assert a["attempt_id"] == b["attempt_id"] == c["attempt_id"]
        assert holds(order["invoice_id"]) == [("held", 500_000)]

    def test_cancelling_the_order_releases_the_hold(self, shop):
        wallet = fund(shop["id"], 500_000)
        order = place(shop)
        pay(shop, order["invoice_id"], wallet=500_000)
        cancelled = shop["client"].post(
            url(shop["slug"], f"orders/{order['order_id']}/cancel")
        )
        assert cancelled.status_code == 200, cancelled.content
        assert holds(order["invoice_id"]) == [("released", 500_000)]
        assert balance(wallet) == 500_000

    def test_an_expired_payment_releases_the_hold(self, shop):
        from workeasy360.commercial.payments import dunning
        from workeasy360.commercial.wallets import ledger

        wallet = fund(shop["id"], 500_000)
        order = place(shop)
        pay(shop, order["invoice_id"], wallet=500_000)
        dunning.process_due(at=timezone.now() + timedelta(hours=2))
        assert holds(order["invoice_id"]) == [("released", 500_000)]
        assert ledger.available(wallet.id) == 500_000

    def test_a_late_success_after_expiry_takes_the_wallet_part_again(self, shop):
        from workeasy360.commercial.payments import dunning

        wallet = fund(shop["id"], 500_000)
        order = place(shop)
        body = pay(shop, order["invoice_id"], wallet=500_000).json()
        from workeasy360.commercial.payments.checkout import create_checkout

        assert (
            create_checkout(shop["id"], uuid.UUID(body["attempt_id"]))
            == "awaiting_payment"
        )
        # The attempt (not the order) runs out of time, then the money arrives anyway.
        dunning.process_due(at=timezone.now() + timedelta(minutes=90))
        assert holds(order["invoice_id"]) == [("released", 500_000)]
        _complete_existing(shop, body["attempt_id"])
        assert _order(shop, order["order_id"])["status"] == "fulfilled"
        assert balance(wallet) == 0

    def test_the_held_money_cannot_be_spent_elsewhere(self, shop):
        from workeasy360.commercial.wallets import ledger

        wallet = fund(shop["id"], 600_000)
        order = place(shop)
        pay(shop, order["invoice_id"], wallet=500_000)
        with pytest.raises(ledger.InsufficientBalance):
            ledger.post(
                wallet.id,
                amount=-200_000,
                entry_type="subscription",
                reason="Another payment",
                idempotency_key=uuid.uuid4().hex,
            )
        # What is not held is still spendable.
        ledger.post(
            wallet.id,
            amount=-100_000,
            entry_type="subscription",
            reason="Another payment",
            idempotency_key=uuid.uuid4().hex,
        )
        assert balance(wallet) == 500_000

    def test_more_than_the_wallet_can_hold_is_refused(self, shop):
        wallet = fund(shop["id"], 100_000)
        order = place(shop)
        refused = pay(shop, order["invoice_id"], wallet=200_000)
        assert (
            refused.status_code == 409
            and refused.json()["reason"] == "insufficient_balance"
        )
        assert (
            attempts(shop, order["invoice_id"]) == [] and holds(order["invoice_id"]) == []
        )
        assert balance(wallet) == 100_000

    def test_paying_in_full_withdraws_a_payment_in_flight(self, shop):
        wallet = fund(shop["id"], 2_000_000)
        order = place(shop)
        split = pay(shop, order["invoice_id"], wallet=500_000).json()
        full = pay(shop, order["invoice_id"], wallet=CRM_YEAR_TOTAL)
        assert full.status_code == 202, full.content
        assert full.json()["status"] == "succeeded"
        assert _order(shop, order["order_id"])["status"] == "fulfilled"
        assert balance(wallet) == 2_000_000 - CRM_YEAR_TOTAL
        assert holds(order["invoice_id"]) == [("released", 500_000)]
        states = {
            a["provider_key"]: a["status"] for a in attempts(shop, order["invoice_id"])
        }
        assert states == {"test-provider": "cancelled", "wallet": "succeeded"}
        assert split["attempt_id"]


class TestEligibility:
    def test_a_wallet_top_up_is_never_paid_from_a_wallet(self, shop):
        fund(shop["id"], 1_000_000)
        top_up = shop["client"].post(
            url(shop["slug"], "wallets/top-up"),
            {"wallet_kind": "consumption", "amount_minor": 50_000},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        )
        assert top_up.status_code == 201, top_up.content
        invoice_id = top_up.json()["invoice_id"]
        for amount in (50_000, 10_000):
            refused = pay(shop, invoice_id, wallet=amount)
            assert refused.status_code == 409, refused.content
            assert refused.json()["reason"] == "wallet_not_eligible"
        # The provider alone still pays it.
        assert pay(shop, invoice_id).status_code == 202

    def test_a_member_without_billing_rights_cannot_pay(self, shop, login):
        fund(shop["id"], 1_500_000)
        order = place(shop)
        person = shop["add"]("member")
        refused = pay(
            shop, order["invoice_id"], wallet=CRM_YEAR_TOTAL, client=login(person["user"])
        )
        assert refused.status_code == 403

    def test_another_organizations_invoice_is_not_found(self, shop, console_org, login):
        from tests.store_checkout.conftest import open_shop

        fund(shop["id"], 1_500_000)
        order = place(shop)
        other = open_shop(console_org, login, shop["catalog"])
        fund(other["id"], 1_500_000)
        refused = other["client"].post(
            url(other["slug"], f"billing/invoices/{order['invoice_id']}/pay"),
            {"wallet_amount_minor": CRM_YEAR_TOTAL},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        )
        assert refused.status_code == 404


def _complete_existing(shop, attempt_id):
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt

    with organization_scope(shop["id"], reason="Test reads the checkout reference"):
        reference = PaymentAttempt.unscoped.get(
            id=uuid.UUID(attempt_id)
        ).provider_reference
    done = shop["client"].post(
        "/api/v1/public/payments/test-checkout/complete",
        {
            "reference": reference,
            "organization_id": str(shop["id"]),
            "outcome": "succeeded",
        },
        content_type="application/json",
    )
    assert done.status_code == 200, done.content

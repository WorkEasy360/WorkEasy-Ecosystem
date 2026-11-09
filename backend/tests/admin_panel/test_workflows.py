"""The brief's business workflows, each from the first request to the last, through the
real endpoints, the real checkout path and real PostgreSQL. Workflow 1 (partner
registration to activation) is in ``test_kyc_and_dashboard.py``."""

from __future__ import annotations

import uuid

import pytest
from django.test import Client

from tests.extension.conftest import PASSWORD, pay_order, place_and_pay, place_order, url

pytestmark = pytest.mark.django_db


def post(client, path, body=None, key=None):
    extra = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    return client.post(path, body or {}, content_type="application/json", **extra)


def version(shop, plan):
    return str(shop["catalog"]["versions"][plan].id)


MONEY = (
    "platform.staff_customer.view",
    "platform.staff_payment.view",
    "platform.staff_payment.manage",
    "platform.staff_wallet.view",
    "platform.staff_wallet.manage",
    "platform.staff_catalog.view",
    "platform.staff_pricing.manage",
)


def order_invoice(shop, order):
    invoices = shop["client"].get(url(shop["slug"], "billing/invoices")).json()["results"]
    return next(i for i in invoices if i["order_id"] == order["order_id"])


def staff_invoice(staff, shop, order):
    billing = staff.get(f"/api/v1/admin/customers/{shop['id']}/billing").json()
    number = order_invoice(shop, order)["number"]
    return next(i for i in billing["invoices"] if i["number"] == number)


def wallet_row(staff, shop, kind):
    rows = staff.get("/api/v1/admin/wallets", {"kind": kind}).json()["results"]
    return next((r for r in rows if r["owner_id"] == str(shop["id"])), None)


class TestWorkflow2CustomerToSubscription:
    def test_creation_to_active_subscription(
        self, staff_login, login, catalog, fake_payments, user_factory
    ):
        admin, _ = staff_login(
            "platform.staff_customer.manage",
            "platform.staff_subscription.manage",
            "platform.staff_payment.manage",
            "platform.staff_catalog.view",
        )
        made = post(
            admin,
            "/api/v1/admin/customers/create",
            {
                "name": "Northwind Traders",
                "owner_email": "owner@northwind.example.com",
                "owner_name": "Nia North",
                "reason": "Signed contract, set up by the sales team",
            },
        )
        assert made.status_code == 201, made.content
        created = made.json()
        assert created["owner_invited"] is True
        org_id = created["organization_id"]

        members = admin.get(f"/api/v1/admin/customers/{org_id}/members").json()["results"]
        assert [m["email"] for m in members] == ["owner@northwind.example.com"]
        assert members[0]["is_owner"] is True
        assert "password" not in str(members[0])

        command = uuid.uuid4().hex
        body = {
            "plan_version_id": str(catalog["versions"]["crm_professional"].id),
            "billing_interval": "monthly",
            "quantity": 1,
            "currency": "GBP",
            "reason": "Agreed on the sales call",
        }
        placed = post(
            admin, f"/api/v1/admin/customers/{org_id}/assign-plan", body, key=command
        )
        assert placed.status_code == 201, placed.content
        order = placed.json()
        assert order["requires_payment"] is True

        # The same command again is the same order, not a second one.
        again = post(
            admin, f"/api/v1/admin/customers/{org_id}/assign-plan", body, key=command
        )
        assert again.json()["order_id"] == order["order_id"]
        billing = admin.get(f"/api/v1/admin/customers/{org_id}/billing").json()
        assert len(billing["invoices"]) == 1
        invoice = billing["invoices"][0]

        # Nothing is granted before the money arrives.
        history = admin.get(
            f"/api/v1/admin/customers/{org_id}/assignment-history"
        ).json()["results"]
        assert history == []

        settled = post(
            admin,
            f"/api/v1/admin/customers/{org_id}/invoices/{invoice['invoice_id']}/mark-paid",
            {
                "reference": "NEFT-77",
                "reason": "Bank transfer checked",
                "version": invoice["version"],
            },
        )
        assert settled.status_code == 200, settled.content

        history = admin.get(
            f"/api/v1/admin/customers/{org_id}/assignment-history"
        ).json()["results"]
        assert [row["plan_code"] for row in history] == ["crm_professional"]
        running = admin.get("/api/v1/admin/subscriptions", {"status": "active"}).json()[
            "results"
        ]
        ours = next(r for r in running if r["organization_id"] == org_id)
        assert ours["status"] == "active"

    def test_an_unknown_currency_is_refused(self, staff_login, catalog):
        admin, _ = staff_login(
            "platform.staff_customer.manage", "platform.staff_subscription.manage"
        )
        made = post(
            admin,
            "/api/v1/admin/customers/create",
            {
                "name": "Southwind Traders",
                "owner_email": "owner@southwind.example.com",
                "owner_name": "Sia South",
                "reason": "Signed contract, set up by the sales team",
            },
        ).json()
        refused = post(
            admin,
            f"/api/v1/admin/customers/{made['organization_id']}/assign-plan",
            {
                "plan_version_id": str(catalog["versions"]["crm_professional"].id),
                "billing_interval": "monthly",
                "quantity": 1,
                "currency": "XXX",
                "reason": "Agreed on the sales call",
            },
            key=uuid.uuid4().hex,
        )
        assert refused.status_code == 400

    def test_an_address_in_use_becomes_owner_and_a_reason_is_required(
        self, staff_login, user_factory
    ):
        admin, _ = staff_login("platform.staff_customer.manage")
        person = user_factory(password=PASSWORD, email="existing-owner@example.com")
        refused = post(
            admin,
            "/api/v1/admin/customers/create",
            {
                "name": "Globex",
                "owner_email": person.email,
                "owner_name": "Gee",
                "reason": "no",
            },
        )
        assert refused.status_code == 400
        made = post(
            admin,
            "/api/v1/admin/customers/create",
            {
                "name": "Globex",
                "owner_email": person.email,
                "owner_name": "Gee",
                "reason": "Existing customer, second company",
            },
        )
        assert made.status_code == 201 and made.json()["owner_invited"] is False

    def test_it_needs_its_grant(self, staff_login):
        viewer, _ = staff_login("platform.staff_customer.view")
        assert (
            post(viewer, "/api/v1/admin/customers/create", {"name": "X"}).status_code
            == 403
        )


class TestWorkflow3OrderToActivation:
    def test_store_order_to_active_with_a_visible_trail(self, shop, staff_login):
        order = place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        assert order["status"] == "fulfilled"
        staff, _ = staff_login(*MONEY)
        invoices = staff.get("/api/v1/admin/invoices", {"status": "paid"}).json()[
            "results"
        ]
        assert any(i["organization_id"] == str(shop["id"]) for i in invoices)
        payments = staff.get(
            "/api/v1/admin/payments/all", {"status": "succeeded"}
        ).json()["results"]
        ours = [p for p in payments if p["organization_id"] == str(shop["id"])]
        assert len(ours) == 1 and ours[0]["provider"] == "test-provider"

    def test_subscriptions_list_shows_it_with_its_lines(self, shop, staff_login):
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        staff, _ = staff_login("platform.staff_customer.view")
        rows = staff.get("/api/v1/admin/subscriptions", {"status": "active"}).json()
        ours = [r for r in rows["results"] if r["organization_id"] == str(shop["id"])]
        assert ours[0]["lines"][0]["plan_code"] == "crm_professional"
        assert rows["counts"]["active"] >= 1
        history = staff.get(
            f"/api/v1/admin/customers/{shop['id']}/assignment-history"
        ).json()["results"]
        assert history[0]["plan_code"] == "crm_professional"


class TestWorkflow4FailedPaymentRetry:
    def test_retry_succeeds_with_one_charge(self, shop, staff_login):
        placed = place_order(
            shop["client"],
            shop["slug"],
            plan_version_id=version(shop, "crm_professional"),
        ).json()
        staff, _ = staff_login(*MONEY)
        failed = pay_order(
            shop["client"], shop["slug"], shop["id"], placed, outcome="failed"
        )
        assert failed["status"] == "pending_payment"
        waiting = staff.get("/api/v1/admin/payments/all", {"failed": "1"}).json()[
            "results"
        ]
        assert [p["organization_id"] for p in waiting] == [str(shop["id"])]

        paid = pay_order(shop["client"], shop["slug"], shop["id"], placed)
        assert paid["status"] == "fulfilled"
        # The failure no longer waits for anyone, and exactly one payment succeeded.
        assert (
            staff.get("/api/v1/admin/payments/all", {"failed": "1"}).json()["results"]
            == []
        )
        succeeded = [
            p
            for p in staff.get(
                "/api/v1/admin/payments/all", {"status": "succeeded"}
            ).json()["results"]
            if p["organization_id"] == str(shop["id"])
        ]
        assert len(succeeded) == 1

    def test_activation_retry_only_touches_a_paid_order_and_is_safe_twice(
        self, shop, staff_login
    ):
        order = place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        staff, _ = staff_login(*MONEY)
        done = post(staff, f"/api/v1/admin/orders/{order['order_id']}/retry-activation")
        assert done.status_code == 400  # already fulfilled: nothing to do, and it says so
        subs = shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"]
        assert len(subs) == 1

    def test_a_stuck_paid_order_activates_once(self, shop, staff_login):
        """Paid, not yet activated (the worker never ran): the retry finishes it, and a
        second press changes nothing."""
        from unittest import mock

        with mock.patch(
            "workeasy360.commercial.payments.orders.fulfil", return_value="noop"
        ):
            placed = place_and_pay(
                shop["client"],
                shop["slug"],
                shop["id"],
                plan_version_id=version(shop, "crm_professional"),
            )
        assert placed["status"] == "paid"
        staff, _ = staff_login(*MONEY)
        stuck = staff.get("/api/v1/admin/orders/stuck").json()["results"]
        assert [o["order_id"] for o in stuck] == [placed["order_id"]]
        first = post(staff, f"/api/v1/admin/orders/{placed['order_id']}/retry-activation")
        assert first.status_code == 200 and first.json()["outcome"] == "fulfilled"
        again = post(staff, f"/api/v1/admin/orders/{placed['order_id']}/retry-activation")
        assert again.status_code == 400
        subs = shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"]
        assert len(subs) == 1
        assert staff.get("/api/v1/admin/orders/stuck").json()["results"] == []


class TestWorkflow5WalletTopUpUsage:
    def test_top_up_usage_history(self, shop, staff_login):
        from workeasy360.commercial.wallets import usage
        from workeasy360.commercial.wallets.models import UsageRate

        staff, staff_user = staff_login(*MONEY)
        placed = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/wallet-orders",
            {"wallet_kind": "platform", "amount_minor": 50000, "currency": "GBP"},
            key=uuid.uuid4().hex,
        )
        assert placed.status_code == 201, placed.content
        order = placed.json()
        assert order["status"] == "pending_payment"
        # Nothing arrives before the money does.
        assert wallet_row(staff, shop, "platform") is None

        invoice = staff_invoice(staff, shop, order)
        settled = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/invoices/{invoice['invoice_id']}/mark-paid",
            {
                "reference": "NEFT-1",
                "reason": "Bank transfer checked",
                "version": invoice["version"],
            },
        )
        assert settled.status_code == 200, settled.content
        wallet = wallet_row(staff, shop, "platform")
        assert wallet["balance"] == 50000 and wallet["owner_name"]

        entries = staff.get(
            "/api/v1/admin/wallets/entries", {"wallet": wallet["id"]}
        ).json()["results"]
        assert [e["type"] for e in entries] == ["top_up"] and entries[0][
            "source_type"
        ] == "order"
        assert staff.get("/api/v1/admin/wallets/reconcile").json()["balanced"] is True

        # The commission ledger never earns on a top-up: its line is its own kind.
        lines = (
            shop["client"]
            .get(url(shop["slug"], f"billing/invoices/{invoice['invoice_id']}"))
            .json()
        )
        assert all(line["kind"] == "wallet" for line in lines["lines"])

        # A subscription deducted from it: the invoice is paid and the wallet is lighter.
        plan = place_order(
            shop["client"],
            shop["slug"],
            plan_version_id=version(shop, "crm_professional"),
        ).json()
        plan_invoice = staff_invoice(staff, shop, plan)
        paid = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/invoices/{plan_invoice['invoice_id']}/pay-from-wallet",
            {
                "reason": "Customer asked to use their balance",
                "expected_version": plan_invoice["version"],
            },
        )
        assert paid.status_code == 200, paid.content
        wallet = wallet_row(staff, shop, "platform")
        assert wallet["balance"] == 50000 - plan_invoice["total_minor"]
        subs = shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"]
        assert subs and subs[0]["status"] == "active"
        assert usage is not None and UsageRate is not None

    def test_not_enough_in_the_wallet_refuses_and_moves_nothing(self, shop, staff_login):
        from workeasy360.commercial.wallets import ledger

        wallet = ledger.ensure(
            owner_type="organization",
            owner_id=shop["id"],
            kind="platform",
            currency="GBP",
        )
        ledger.post(
            wallet.id, amount=1, entry_type="top_up", reason="t", idempotency_key="low-1"
        )
        staff, _ = staff_login(*MONEY)
        plan = place_order(
            shop["client"],
            shop["slug"],
            plan_version_id=version(shop, "crm_professional"),
        ).json()
        invoice = staff_invoice(staff, shop, plan)
        refused = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/invoices/{invoice['invoice_id']}/pay-from-wallet",
            {
                "reason": "Customer asked to use their balance",
                "expected_version": invoice["version"],
            },
        )
        assert refused.status_code == 409
        wallet.refresh_from_db()
        assert wallet.balance == 1
        assert (
            shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"] == []
        )


class TestWorkflow6AiCredits:
    def test_pack_purchase_consumption_balance(self, shop, staff_login):
        from workeasy360.commercial.wallets import usage

        staff, _ = staff_login(*MONEY)
        pack = post(
            staff,
            "/api/v1/admin/credit-packs",
            {"name": "Starter", "credits": 1000, "price_minor": 2500, "currency": "GBP"},
        )
        assert pack.status_code == 201, pack.content
        rate = post(
            staff,
            "/api/v1/admin/usage-rates",
            {
                "meter": "ai_tokens",
                "unit_amount": 1,
                "per_units": 1000,
                "included_units": 0,
                "reason": "Launch price agreed",
            },
        )
        assert rate.status_code == 201, rate.content
        placed = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/wallet-orders",
            {"pack_code": pack.json()["code"]},
            key=uuid.uuid4().hex,
        ).json()
        assert placed["amount_due_minor"] == 2500
        invoice = staff_invoice(staff, shop, placed)
        post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/invoices/{invoice['invoice_id']}/mark-paid",
            {
                "reference": "NEFT-2",
                "reason": "Bank transfer checked",
                "version": invoice["version"],
            },
        )
        wallet = wallet_row(staff, shop, "ai_credits")
        assert wallet["balance"] == 1000 and wallet["currency"] == ""

        event = usage.record(
            organization_id=shop["id"],
            product_code="crm",
            meter="ai_tokens",
            quantity=2500,
            idempotency_key="ai-run-1",
            currency="GBP",
        )
        assert event.outcome == "charged" and event.amount == 3
        assert wallet_row(staff, shop, "ai_credits")["balance"] == 997
        history = staff.get(
            "/api/v1/admin/wallets/entries", {"kind": "ai_credits"}
        ).json()["results"]
        assert {e["type"] for e in history} == {"credit_purchase", "usage"}
        assert (
            staff.get("/api/v1/admin/wallets/events", {"meter": "ai_tokens"}).json()[
                "results"
            ][0]["outcome"]
            == "charged"
        )


class TestWorkflow7Consumption:
    def test_recharge_messaging_api_deduction(self, shop, staff_login):
        from workeasy360.commercial.wallets import usage

        staff, _ = staff_login(*MONEY)
        for meter, amount in (("whatsapp", 40), ("api_trigger", 5)):
            assert (
                post(
                    staff,
                    "/api/v1/admin/usage-rates",
                    {
                        "meter": meter,
                        "currency": "GBP",
                        "unit_amount": amount,
                        "per_units": 1,
                        "included_units": 0,
                        "reason": "Rate card approved",
                    },
                ).status_code
                == 201
            )
        placed = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/wallet-orders",
            {"wallet_kind": "consumption", "amount_minor": 1000, "currency": "GBP"},
            key=uuid.uuid4().hex,
        ).json()
        invoice = staff_invoice(staff, shop, placed)
        post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/invoices/{invoice['invoice_id']}/mark-paid",
            {
                "reference": "NEFT-3",
                "reason": "Bank transfer checked",
                "version": invoice["version"],
            },
        )
        usage.record(
            organization_id=shop["id"],
            product_code="crm",
            meter="whatsapp",
            quantity=10,
            idempotency_key="m1",
            currency="GBP",
        )
        usage.record(
            organization_id=shop["id"],
            product_code="crm",
            meter="api_trigger",
            quantity=2,
            idempotency_key="a1",
            currency="GBP",
        )
        assert wallet_row(staff, shop, "consumption")["balance"] == 1000 - 400 - 10
        # A rate changed later never rewrites what was charged.
        post(
            staff,
            "/api/v1/admin/usage-rates",
            {
                "meter": "whatsapp",
                "currency": "GBP",
                "unit_amount": 99,
                "per_units": 1,
                "included_units": 0,
                "reason": "New rate card approved",
            },
        )
        rates = staff.get("/api/v1/admin/usage-rates", {"history": "1"}).json()["results"]
        assert len([r for r in rates if r["meter"] == "whatsapp"]) == 2
        assert wallet_row(staff, shop, "consumption")["balance"] == 590


class TestWorkflow9Refund:
    def test_refund_of_a_top_up_names_the_original_and_takes_it_back(
        self, shop, staff_login
    ):
        from workeasy360.commercial.payments.refunds import send_refund
        from workeasy360.commercial.wallets.models import WalletEntry

        staff, _ = staff_login(*MONEY)
        placed = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/wallet-orders",
            {"wallet_kind": "platform", "amount_minor": 10000, "currency": "GBP"},
            key=uuid.uuid4().hex,
        ).json()
        paid = pay_order(shop["client"], shop["slug"], shop["id"], placed)
        assert paid["status"] == "fulfilled"
        assert wallet_row(staff, shop, "platform")["balance"] == 10000

        attempt = next(
            p
            for p in staff.get(
                "/api/v1/admin/payments/all", {"status": "succeeded"}
            ).json()["results"]
            if p["organization_id"] == str(shop["id"])
        )
        refund = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/payments/{attempt['attempt_id']}/refund",
            {"amount_minor": 4000, "reason": "Customer overpaid by mistake"},
            key="refund-1",
        )
        assert refund.status_code == 202, refund.content
        send_refund(shop["id"], uuid.UUID(refund.json()["refund_id"]))

        rows = staff.get("/api/v1/admin/refunds").json()["results"]
        row = next(r for r in rows if r["refund_id"] == refund.json()["refund_id"])
        if row["status"] != "succeeded":
            # The test provider confirms by webhook; confirm it the way that path does.
            from django.db import transaction

            from workeasy360.commercial.payments import settlement
            from workeasy360.commercial.payments.invoicing import organization_scope
            from workeasy360.commercial.payments.models import Refund

            with (
                organization_scope(shop["id"], reason="Test confirms a refund"),
                transaction.atomic(),
            ):
                settlement.record_refund(
                    Refund.unscoped.select_for_update().get(id=row["refund_id"]),
                    succeeded=True,
                    reference="fake-refund",
                )
            rows = staff.get("/api/v1/admin/refunds").json()["results"]
            row = next(r for r in rows if r["refund_id"] == refund.json()["refund_id"])
        assert row["status"] == "succeeded"
        assert row["invoice_number"] and row["credit_note"].startswith("CN-")

        assert wallet_row(staff, shop, "platform")["balance"] == 6000
        original = WalletEntry.objects.get(idempotency_key=f"order:{placed['order_id']}")
        reversal = WalletEntry.objects.get(reverses_id=original.id)
        assert reversal.entry_type == "refund" and reversal.amount == -4000

        notes = staff.get("/api/v1/admin/credit-notes").json()["results"]
        assert (
            notes[0]["refund_id"] == row["refund_id"] and notes[0]["amount_minor"] == 4000
        )
        assert staff.get("/api/v1/admin/wallets/reconcile").json()["balanced"] is True

    def test_a_spent_top_up_cannot_be_refunded(self, shop, staff_login):
        from workeasy360.commercial.wallets import ledger

        staff, _ = staff_login(*MONEY)
        placed = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/wallet-orders",
            {"wallet_kind": "platform", "amount_minor": 10000, "currency": "GBP"},
            key=uuid.uuid4().hex,
        ).json()
        pay_order(shop["client"], shop["slug"], shop["id"], placed)
        wallet = wallet_row(staff, shop, "platform")
        ledger.post(
            wallet["id"],
            amount=-9000,
            entry_type="usage",
            reason="spent",
            idempotency_key="spent-1",
        )
        attempt = next(
            p
            for p in staff.get(
                "/api/v1/admin/payments/all", {"status": "succeeded"}
            ).json()["results"]
            if p["organization_id"] == str(shop["id"])
        )
        refused = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/payments/{attempt['attempt_id']}/refund",
            {"amount_minor": 5000, "reason": "Customer asked for it back"},
            key="refund-2",
        )
        assert refused.status_code == 409

    def test_refund_of_a_wallet_payment_goes_straight_back_to_the_wallet(
        self, shop, staff_login
    ):
        from workeasy360.commercial.wallets import ledger
        from workeasy360.commercial.wallets.models import WalletEntry

        wallet = ledger.ensure(
            owner_type="organization",
            owner_id=shop["id"],
            kind="platform",
            currency="GBP",
        )
        ledger.post(
            wallet.id,
            amount=20000,
            entry_type="top_up",
            reason="t",
            idempotency_key="fund-9",
        )
        staff, _ = staff_login(*MONEY)
        plan = place_order(
            shop["client"],
            shop["slug"],
            plan_version_id=version(shop, "crm_professional"),
        ).json()
        invoice = staff_invoice(staff, shop, plan)
        post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/invoices/{invoice['invoice_id']}/pay-from-wallet",
            {
                "reason": "Customer asked to use their balance",
                "expected_version": invoice["version"],
            },
        )
        attempt = next(
            p
            for p in staff.get(
                "/api/v1/admin/payments/all", {"status": "succeeded"}
            ).json()["results"]
            if p["organization_id"] == str(shop["id"])
        )
        assert attempt["provider"] == "wallet"
        refund = post(
            staff,
            f"/api/v1/admin/customers/{shop['id']}/payments/{attempt['attempt_id']}/refund",
            {"amount_minor": 1000, "reason": "Goodwill refund for downtime"},
            key="refund-3",
        )
        assert refund.status_code == 202, refund.content
        wallet.refresh_from_db()
        assert wallet.balance == 20000 - invoice["total_minor"] + 1000
        latest = WalletEntry.objects.filter(wallet=wallet).order_by("-created_at").first()
        assert latest.entry_type == "refund" and latest.reverses_id is not None


class TestManualControls:
    def test_adjustments_are_authorized_explained_and_replayable(self, staff_login):
        from workeasy360.commercial.wallets import ledger

        wallet = ledger.ensure(
            owner_type="organization",
            owner_id=uuid.uuid4(),
            kind="platform",
            currency="INR",
        )
        viewer, _ = staff_login("platform.staff_wallet.view")
        path = f"/api/v1/admin/wallets/{wallet.id}/adjust"
        assert (
            post(
                viewer,
                path,
                {"amount": 100, "reason": "Trying without the grant"},
                key="a",
            ).status_code
            == 403
        )
        manager, _ = staff_login("platform.staff_wallet.manage")
        assert (
            post(manager, path, {"amount": 100, "reason": "no"}, key="b").status_code
            == 400
        )
        first = post(
            manager,
            path,
            {"amount": 100, "reason": "Goodwill credit agreed by support"},
            key="c",
        )
        again = post(
            manager,
            path,
            {"amount": 100, "reason": "Goodwill credit agreed by support"},
            key="c",
        )
        assert first.status_code == again.status_code == 201
        assert first.json()["id"] == again.json()["id"]
        wallet.refresh_from_db()
        assert wallet.balance == 100
        over = post(
            manager, path, {"amount": -500, "reason": "Would take it below zero"}, key="d"
        )
        assert over.status_code == 409

    def test_a_partner_holds_only_a_platform_wallet_and_commission_is_not_one(
        self, staff_login, partner_factory
    ):
        partner = partner_factory()
        partner_id = getattr(partner, "id", None) or partner["partner"].id
        manager, _ = staff_login(
            "platform.staff_wallet.manage", "platform.staff_wallet.view"
        )
        credits = post(
            manager,
            "/api/v1/admin/wallets",
            {"owner_type": "partner", "owner_id": str(partner_id), "kind": "ai_credits"},
        )
        assert credits.status_code == 400
        made = post(
            manager,
            "/api/v1/admin/wallets",
            {
                "owner_type": "partner",
                "owner_id": str(partner_id),
                "kind": "platform",
                "currency": "INR",
            },
        )
        assert made.status_code == 201, made.content
        wallet_id = made.json()["id"]
        topped = post(
            manager,
            f"/api/v1/admin/wallets/{wallet_id}/top-up",
            {
                "amount": 5000,
                "reference": "NEFT-P1",
                "reason": "Partner paid by bank transfer",
            },
            key="p-1",
        )
        assert topped.status_code == 201, topped.content
        assert manager.get(f"/api/v1/admin/wallets/{wallet_id}").json()["balance"] == 5000

    def test_no_commission_model_holds_a_balance(self):
        from workeasy360.commercial.commissions.models import CommissionAccrual, Payout

        for model in (CommissionAccrual, Payout):
            assert not [f.name for f in model._meta.get_fields() if "balance" in f.name]


class TestWorkflow10PermissionChange:
    def test_change_access_audit(self, staff_login, user_factory, login):
        admin, _ = staff_login(
            "platform.staff_security.manage",
            "platform.staff_security.view",
            "platform.staff_audit.view",
        )
        made = post(
            admin,
            "/api/v1/admin/security/accounts",
            {
                "email": "new-admin@example.com",
                "full_name": "Nadia Admin",
                "role": "BILLING_ADMIN",
                "reason": "Joined the finance team today",
            },
        )
        assert made.status_code == 201, made.content
        assert made.json()["new_account"] is True
        from workeasy360.platform.identity.models import User

        person = User.objects.get(email="new-admin@example.com")
        assert person.is_staff is True
        assert not person.has_usable_password(), "nobody chose a password for them"
        person.set_password(PASSWORD)
        person.save()
        them = login(person)
        assert them.get("/api/v1/admin/invoices").status_code == 200
        assert them.get("/api/v1/admin/security/roles").status_code == 403

        revoked = post(
            admin,
            "/api/v1/admin/security/roles/revoke",
            {
                "user_id": str(person.id),
                "role": "BILLING_ADMIN",
                "reason": "Moved to another team",
            },
        )
        assert revoked.status_code == 200, revoked.content
        assert them.get("/api/v1/admin/invoices").status_code == 403
        actions = [
            e["action"]
            for e in admin.get("/api/v1/admin/audit", {"action": "staff."}).json()[
                "results"
            ]
        ]
        assert {
            "staff.account_created",
            "staff.role_granted",
            "staff.role_revoked",
        } <= set(actions)

    def test_sign_in_audit_lists_staff_failures_and_needs_its_grant(
        self, staff_login, user_factory
    ):
        target = user_factory(password=PASSWORD, is_staff=True)
        anonymous = Client()
        anonymous.get("/api/v1/auth/session")
        post(
            anonymous,
            "/api/v1/auth/login",
            {
                "email": target.email,
                "password": "wrong password here",
            },  # pragma: allowlist secret
        )
        reader, _ = staff_login("platform.staff_audit.view")
        rows = reader.get(
            "/api/v1/admin/security/sign-ins",
            {"q": target.email, "kind": "sign_in_failed"},
        ).json()
        assert rows["total"] == 1 and rows["results"][0]["email"] == target.email
        assert "password" not in str(rows["results"][0]).lower().replace("password_", "")
        nobody, _ = staff_login("platform.staff_customer.view")
        assert nobody.get("/api/v1/admin/security/sign-ins").status_code == 403


class TestReports:
    KINDS = (
        "sales",
        "payments",
        "renewals",
        "wallet-movements",
        "usage",
        "partner-conversions",
        "commissions",
        "payouts",
    )

    def test_every_report_opens_filters_pages_and_exports(self, shop, staff_login):
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        staff, _ = staff_login("platform.staff_report.view")
        assert len(staff.get("/api/v1/admin/reports").json()["results"]) == 8
        for kind in self.KINDS:
            page = staff.get(
                f"/api/v1/admin/reports/{kind}",
                {"limit": "5", "q": "x", "product": "crm"},
            )
            assert page.status_code == 200, (kind, page.content)
            assert page.json()["columns"]
            exported = staff.get(f"/api/v1/admin/reports/{kind}", {"format": "csv"})
            assert exported.status_code == 200, (kind, exported.content)
            assert exported["Content-Type"].startswith("text/csv")
            assert exported["Content-Disposition"].startswith("attachment")
        sales = staff.get("/api/v1/admin/reports/sales").json()
        assert sales["total"] >= 1 and sales["results"][0]["number"].startswith("INV-")
        filtered = staff.get(
            "/api/v1/admin/reports/sales", {"product": "no-such-product"}
        ).json()
        assert filtered["total"] == 0

    def test_it_needs_its_grant_and_refuses_an_unknown_report(self, staff_login):
        staff, _ = staff_login("platform.staff_report.view")
        assert staff.get("/api/v1/admin/reports/nonsense").status_code == 400
        nobody, _ = staff_login("platform.staff_customer.view")
        assert nobody.get("/api/v1/admin/reports/sales").status_code == 403

    def test_a_spreadsheet_formula_is_neutralised(self):
        from workeasy360.commercial.controlplane.reports import _safe_cell

        assert _safe_cell("=SUM(A1)") == "'=SUM(A1)"
        assert _safe_cell("@cmd") == "'@cmd"
        assert _safe_cell("plain") == "plain"

    def test_an_export_is_on_the_record(self, staff_login):
        staff, _ = staff_login("platform.staff_report.view", "platform.staff_audit.view")
        staff.get("/api/v1/admin/reports/sales", {"format": "csv"})
        actions = [
            e["action"]
            for e in staff.get("/api/v1/admin/audit", {"action": "report."}).json()[
                "results"
            ]
        ]
        assert actions == ["report.exported"]


class TestSupport:
    def test_customer_ticket_to_resolution(self, console_org, login, staff_login):
        org = console_org()
        owner = login(org["owner"])
        opened = post(
            owner,
            url(org["slug"], "support-tickets"),
            {
                "subject": "Cannot export invoices",
                "message": "The export button does nothing.",
                "category": "billing",
            },
        )
        assert opened.status_code == 201, opened.content
        ticket = opened.json()["id"]

        agent, agent_user = staff_login(
            "platform.staff_support.manage", "platform.staff_support.view"
        )
        rows = agent.get(
            "/api/v1/admin/tickets", {"audience": "customer", "status": "all"}
        ).json()
        assert [t["id"] for t in rows["items"]] == [ticket]
        assert rows["items"][0]["organization_name"]
        assert (
            agent.get(
                "/api/v1/admin/tickets", {"audience": "partner", "status": "all"}
            ).json()["items"]
            == []
        )

        assigned = post(
            agent,
            f"/api/v1/admin/tickets/{ticket}/assign",
            {"user_id": str(agent_user.id)},
        )
        assert assigned.json()["assigned_to_name"]
        replied = post(
            agent, f"/api/v1/admin/tickets/{ticket}", {"message": "Please try again now."}
        )
        assert replied.status_code == 200, replied.content
        post(
            agent,
            f"/api/v1/admin/tickets/{ticket}",
            {"message": "Check the logs first.", "internal": True},
        )
        seen = owner.get(url(org["slug"], f"support-tickets/{ticket}")).json()
        words = [m["body"] for m in seen["messages"]]
        assert "Please try again now." in words and "Check the logs first." not in words

        short = post(
            agent, f"/api/v1/admin/tickets/{ticket}/resolve", {"resolution": "ok"}
        )
        assert short.status_code == 400
        done = post(
            agent,
            f"/api/v1/admin/tickets/{ticket}/resolve",
            {"resolution": "Export fixed by the 12 Nov release."},
        )
        assert done.json()["status"] == "resolved" and done.json()["resolution"]
        assert owner.get(url(org["slug"], f"support-tickets/{ticket}")).json()[
            "resolution"
        ]

    def test_only_staff_can_be_assigned_and_it_needs_manage(
        self, console_org, login, staff_login, user_factory
    ):
        org = console_org()
        owner = login(org["owner"])
        ticket = post(
            owner,
            url(org["slug"], "support-tickets"),
            {"subject": "Hello there", "message": "A question."},
        ).json()["id"]
        agent, _ = staff_login("platform.staff_support.manage")
        customer = user_factory(password=PASSWORD)
        assert (
            post(
                agent,
                f"/api/v1/admin/tickets/{ticket}/assign",
                {"user_id": str(customer.id)},
            ).status_code
            == 400
        )
        reader, _ = staff_login("platform.staff_support.view")
        assert (
            post(reader, f"/api/v1/admin/tickets/{ticket}", {"message": "x"}).status_code
            == 403
        )

    def test_another_customer_cannot_read_it(self, console_org, login):
        first, second = console_org(), console_org()
        owner = login(first["owner"])
        ticket = post(
            owner,
            url(first["slug"], "support-tickets"),
            {"subject": "Hello there", "message": "A question."},
        ).json()["id"]
        other = login(second["owner"])
        assert (
            other.get(url(second["slug"], f"support-tickets/{ticket}")).status_code == 404
        )

    def test_help_content_reaches_readers_only_when_published(self, staff_login):
        editor, _ = staff_login("platform.staff_support.manage")
        made = post(
            editor,
            "/api/v1/admin/help-content",
            {
                "title": "How do I export?",
                "body": "Open Reports, then Export.",
                "kind": "faq",
                "audience": "customer",
            },
        )
        assert made.status_code == 201, made.content
        article = made.json()
        assert article["status"] == "draft"
        public = Client()
        assert (
            public.get("/api/v1/public/help", {"audience": "customer"}).json()["results"]
            == []
        )
        post(
            editor,
            f"/api/v1/admin/help-content/{article['id']}/status",
            {"status": "published"},
        )
        found = public.get("/api/v1/public/help", {"audience": "customer"}).json()[
            "results"
        ]
        assert [a["title"] for a in found] == ["How do I export?"]
        assert (
            public.get("/api/v1/public/help", {"audience": "partner"}).json()["results"]
            == []
        )
        post(
            editor,
            f"/api/v1/admin/help-content/{article['id']}/status",
            {"status": "archived"},
        )
        assert (
            public.get("/api/v1/public/help", {"audience": "customer"}).json()["results"]
            == []
        )


class TestSetup:
    def test_requirements_submission_correction_approval(self, shop, staff_login):
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        admin, _ = staff_login(
            "platform.staff_customer.view",
            "platform.staff_subscription.manage",
            "platform.staff_catalog.manage",
        )
        made = post(
            admin,
            "/api/v1/admin/setup/requirements",
            {"product_code": "crm", "label": "Your company's GST number"},
        )
        assert made.status_code == 201, made.content
        key = made.json()["key"]

        queue = admin.get("/api/v1/admin/setup", {"status": "pending"}).json()
        ours = [r for r in queue["results"] if r["organization_id"] == str(shop["id"])]
        assert ours and ours[0]["product_code"] == "crm"
        record = ours[0]["id"]

        client = shop["client"]
        view = client.get(url(shop["slug"], "setup")).json()["results"][0]
        assert view["editable"] is True and view["requirements"][0]["key"] == key
        blank = post(client, url(shop["slug"], f"setup/{record}/submit"), {"answers": {}})
        assert blank.status_code == 400
        sent = post(
            client,
            url(shop["slug"], f"setup/{record}/submit"),
            {"answers": {key: "29ABCDE1234F1Z5"}},
        )
        assert sent.status_code == 200, sent.content

        decide = f"/api/v1/admin/setup/{record}/decision"
        unexplained = post(
            admin, decide, {"decision": "request_correction", "reason": "no"}
        )
        assert unexplained.status_code == 400
        asked = post(
            admin,
            decide,
            {
                "decision": "request_correction",
                "reason": "That number has a typing mistake.",
            },
        )
        assert asked.json()["status"] == "correction_required"
        assert (
            "typing"
            in client.get(url(shop["slug"], "setup")).json()["results"][0][
                "decision_reason"
            ]
        )
        post(
            client,
            url(shop["slug"], f"setup/{record}/submit"),
            {"answers": {key: "29ABCDE1234F1Z6"}},
        )
        approved = post(admin, decide, {"decision": "approve"})
        assert (
            approved.json()["status"] == "approved" and approved.json()["decided_by_name"]
        )

        # Setup is its own state: the subscription never moved.
        subs = client.get(url(shop["slug"], "subscriptions")).json()["results"]
        assert subs[0]["status"] == "active"

    def test_another_customer_cannot_submit_this_record(
        self, shop, staff_login, console_org, login
    ):
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        admin, _ = staff_login(
            "platform.staff_customer.view", "platform.staff_catalog.manage"
        )
        post(
            admin,
            "/api/v1/admin/setup/requirements",
            {"product_code": "crm", "label": "A detail"},
        )
        record = admin.get("/api/v1/admin/setup").json()["results"][0]["id"]
        other = console_org()
        them = login(other["owner"])
        assert (
            post(
                them, url(other["slug"], f"setup/{record}/submit"), {"answers": {}}
            ).status_code
            == 404
        )


class TestSettings:
    def test_company_tax_and_notifications_save_and_tax_is_used(self, staff_login):
        from workeasy360.commercial.billing.tax import gst_rate_bp

        admin, _ = staff_login(
            "platform.staff_settings.view", "platform.staff_settings.manage"
        )
        current = admin.get("/api/v1/admin/settings").json()
        saved = admin.put(
            "/api/v1/admin/settings",
            {
                **{
                    k: current.get(k)
                    for k in (
                        "signup_enabled",
                        "support_email",
                        "sales_email",
                        "public_catalog_currency",
                    )
                },
                "company_name": "WorkEasy360",
                "company_legal_name": "WorkEasy360 Technologies Pvt Ltd",
                "company_tax_id": "29AAAAA0000A1Z5",
                "tax_rate_bp": 1200,
                "notifications_email": "ops@example.com",
                "partner_applications_enabled": False,
                "version": current["version"],
            },
            content_type="application/json",
        )
        assert saved.status_code == 200, saved.content
        after = admin.get("/api/v1/admin/settings").json()
        assert (
            after["company_legal_name"].endswith("Pvt Ltd")
            and after["tax_rate_bp"] == 1200
        )
        assert gst_rate_bp() == 1200
        refused = admin.put(
            "/api/v1/admin/settings",
            {"signup_enabled": True, "tax_rate_bp": 99999, "version": after["version"]},
            content_type="application/json",
        )
        assert refused.status_code == 400

    def test_domains_lists_what_exists_and_checks_only_known_hosts(self, staff_login):
        admin, _ = staff_login("platform.staff_settings.view")
        data = admin.get("/api/v1/admin/settings/domains").json()
        assert data["dns_provider_connected"] is False
        assert "apps" in data and "partner_domains" in data
        refused = post(
            admin, "/api/v1/admin/settings/domains/check", {"host": "evil.example.com"}
        )
        assert refused.status_code == 400

    def test_login_clients_never_show_a_credential(self, staff_login):
        admin, _ = staff_login("platform.staff_integration.view")
        response = admin.get("/api/v1/admin/settings/login-clients")
        assert response.status_code == 200
        assert "secret" not in response.content.decode().lower()


class TestProductRules:
    def test_plan_change_rule_is_enforced(self, shop, staff_login):
        from workeasy360.commercial.catalog.models import Plan

        order = place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        plan = Plan.objects.get(code="crm_professional")
        admin, _ = staff_login("platform.staff_pricing.manage")
        saved = admin.put(
            f"/api/v1/admin/catalog/plans/{plan.id}/rules",
            {
                "renewal_rule": "manual",
                "change_rule": "none",
                "included_usage": {"sms": 100},
            },
            content_type="application/json",
        )
        assert saved.status_code == 200, saved.content
        refused = admin.put(
            f"/api/v1/admin/catalog/plans/{plan.id}/rules",
            {"included_usage": {"carrier_pigeon": 1}},
            content_type="application/json",
        )
        assert refused.status_code == 400
        assert order["status"] == "fulfilled"
        subs = shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"]
        item = subs[0]["items"][0]
        changed = post(
            shop["client"],
            url(shop["slug"], f"subscription-items/{item['id']}/change-plan"),
            {"to_plan_version_id": version(shop, "crm_starter")},
            key=uuid.uuid4().hex,
        )
        assert changed.status_code == 400, changed.content
        assert "does not allow" in changed.json()["detail"]

    def test_included_usage_comes_off_before_the_wallet(self, shop, staff_login):
        from workeasy360.commercial.catalog.models import Plan
        from workeasy360.commercial.wallets import ledger, usage
        from workeasy360.commercial.wallets.models import UsageRate

        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        Plan.objects.filter(code="crm_professional").update(included_usage={"sms": 5})
        UsageRate.objects.create(
            meter="sms",
            currency="GBP",
            unit_amount=10,
            per_units=1,
            created_by_user_id=uuid.uuid4(),
        )
        wallet = ledger.ensure(
            owner_type="organization",
            owner_id=shop["id"],
            kind="consumption",
            currency="GBP",
        )
        ledger.post(
            wallet.id,
            amount=1000,
            entry_type="top_up",
            reason="t",
            idempotency_key="sms-fund",
        )
        first = usage.record(
            organization_id=shop["id"],
            product_code="crm",
            meter="sms",
            quantity=5,
            idempotency_key="s1",
            currency="GBP",
        )
        second = usage.record(
            organization_id=shop["id"],
            product_code="crm",
            meter="sms",
            quantity=2,
            idempotency_key="s2",
            currency="GBP",
        )
        assert first.outcome == "included"
        assert second.outcome == "charged" and second.amount == 20

    def test_app_links_must_be_secure(self, staff_login, catalog):
        from workeasy360.commercial.catalog.models import Product

        product = Product.objects.first()
        admin, _ = staff_login(
            "platform.staff_catalog.manage", "platform.staff_catalog.view"
        )
        path = f"/api/v1/admin/catalog/products/{product.id}/app-links"
        bad = admin.put(
            path,
            {"app_links": {"ios": "javascript:alert(1)"}},
            content_type="application/json",
        )
        assert bad.status_code == 400
        good = admin.put(
            path,
            {"app_links": {"ios": "https://apps.example.com/app"}},
            content_type="application/json",
        )
        assert good.status_code == 200 and good.json()["app_links"]["ios"].startswith(
            "https://"
        )


class TestAttention:
    def test_the_bell_counts_what_waits(self, staff_login, console_org, login):
        org = console_org()
        owner = login(org["owner"])
        post(
            owner,
            url(org["slug"], "support-tickets"),
            {"subject": "Hello there", "message": "A question."},
        )
        agent, _ = staff_login("platform.staff_support.view")
        items = {
            i["key"]: i["count"]
            for i in agent.get("/api/v1/admin/attention").json()["items"]
        }
        assert items.get("support_tickets", 0) >= 1

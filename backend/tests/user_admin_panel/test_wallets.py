"""The customer's own three wallets (User Admin Panel, 2026-11-11): what each role may
read and spend, that the three balances are never combined, that every read is the
caller's organization only, and that money arrives only when an order is paid."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.extension.conftest import pay_order, url

pytestmark = pytest.mark.django_db


def post(client, path, body=None, key=None):
    extra = {"HTTP_IDEMPOTENCY_KEY": key if key is not None else uuid.uuid4().hex}
    return client.post(path, body or {}, content_type="application/json", **extra)


def seed(org_id, kind, amount, currency="GBP", key=None, entry_type="top_up"):
    from workeasy360.commercial.wallets import ledger

    wallet = ledger.ensure(
        owner_type="organization", owner_id=org_id, kind=kind, currency=currency
    )
    ledger.post(
        wallet.id,
        amount=amount,
        entry_type=entry_type,
        reason="Test seeds a balance",
        idempotency_key=key or uuid.uuid4().hex,
    )
    return wallet


def rate(meter, unit_amount, *, currency="GBP", included=0, per_units=1):
    from workeasy360.commercial.wallets.models import UsageRate

    return UsageRate.objects.create(
        meter=meter,
        currency="" if meter == "ai_tokens" else currency,
        unit_amount=unit_amount,
        per_units=per_units,
        included_units=included,
        created_by_user_id=uuid.uuid4(),
        reason="Test rate",
    )


class TestWhoMayRead:
    @pytest.mark.parametrize("role", ["member", "readonly", "admin"])
    def test_roles_without_billing_view_are_refused(self, shop, login, role):
        person = shop["add"](role)
        client = login(person["user"])
        for suffix in ("wallets", "wallets/transactions", "wallets/usage"):
            assert client.get(url(shop["slug"], suffix)).status_code == 403, suffix

    def test_a_billing_manager_reads_and_may_spend(self, shop, login):
        person = shop["add"]("billing_manager")
        body = login(person["user"]).get(url(shop["slug"], "wallets")).json()
        assert body["can_spend"] is True and body["currency"] == "GBP"

    def test_nobody_signed_out_gets_anything(self, shop):
        from django.test import Client

        assert Client().get(url(shop["slug"], "wallets")).status_code in (401, 403, 404)


class TestOverview:
    def test_no_wallet_yet_is_zero_not_an_error(self, shop):
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        for kind in ("platform", "consumption"):
            assert body[kind]["exists"] is False
            assert body[kind]["balance_minor"] == 0
            assert body[kind]["currency"] == "GBP"
            assert body[kind]["recent"] == []
        assert (
            body["ai_credits"]["exists"] is False and body["ai_credits"]["balance"] == 0
        )
        meters = [m["meter"] for m in body["consumption"]["this_month"]]
        assert meters == ["whatsapp", "sms", "email", "api_trigger", "webhook"]

    def test_the_three_balances_stay_separate(self, shop):
        seed(shop["id"], "platform", 50_000)
        seed(shop["id"], "consumption", 7_000)
        seed(shop["id"], "ai_credits", 1_234, currency="", entry_type="credit_purchase")
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        assert body["platform"]["balance_minor"] == 50_000
        assert body["consumption"]["balance_minor"] == 7_000
        assert body["ai_credits"]["balance"] == 1_234
        assert body["ai_credits"]["purchased_total"] == 1_234
        # Nothing anywhere in the answer adds them up.
        flat = str(body)
        for total in (50_000 + 7_000, 50_000 + 7_000 + 1_234, 7_000 + 1_234):
            assert str(total) not in flat
        assert [e["entry_type"] for e in body["platform"]["recent"]] == ["top_up"]

    def test_another_currency_is_shown_beside_never_added(self, shop):
        seed(shop["id"], "platform", 1_000)
        seed(shop["id"], "platform", 9_999, currency="USD")
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        assert body["platform"]["balance_minor"] == 1_000
        assert body["platform"]["other_currencies"] == [
            {"currency": "USD", "balance_minor": 9_999}
        ]

    def test_low_balance_follows_the_admins_threshold_only(self, shop):
        wallet = seed(shop["id"], "consumption", 100)
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        assert body["consumption"]["is_low"] is False  # no threshold, no alert
        wallet.low_balance_threshold = 500
        wallet.save(update_fields=["low_balance_threshold"])
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        assert body["consumption"]["is_low"] is True
        assert body["consumption"]["low_balance_threshold"] == 500

    def test_allowance_and_usage_this_month(self, shop):
        from workeasy360.commercial.wallets import usage

        seed(shop["id"], "consumption", 10_000)
        rate("whatsapp", 40, included=10)
        rate("ai_tokens", 1, per_units=1000, included=500)
        seed(shop["id"], "ai_credits", 100, currency="", entry_type="credit_purchase")
        usage.record(
            organization_id=shop["id"],
            product_code="crm",
            meter="whatsapp",
            quantity=15,
            idempotency_key="wa-1",
            currency="GBP",
        )
        usage.record(
            organization_id=shop["id"],
            product_code="crm",
            meter="ai_tokens",
            quantity=2_500,
            idempotency_key="ai-1",
            currency="GBP",
        )
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        wa = next(
            m for m in body["consumption"]["this_month"] if m["meter"] == "whatsapp"
        )
        assert wa["quantity"] == 15 and wa["included_units"] == 10
        assert wa["charged_units"] == 5 and wa["amount_minor"] == 200
        assert body["consumption"]["balance_minor"] == 10_000 - 200
        sms = next(m for m in body["consumption"]["this_month"] if m["meter"] == "sms")
        assert sms["included_units"] is None  # no rate: not charged at all
        ai = body["ai_credits"]
        assert ai["included_per_month"] == 500 and ai["used_this_month"] == 2_500
        assert ai["credits_spent_this_month"] == 2 and ai["balance"] == 98
        assert ai["resets_at"] is not None

    def test_credit_packs_on_sale_in_the_billing_currency(self, shop):
        from workeasy360.commercial.wallets.models import CreditPack

        CreditPack.objects.create(
            code="starter", name="Starter", credits=1000, price_minor=2500, currency="GBP"
        )
        CreditPack.objects.create(
            code="usd", name="USD pack", credits=1000, price_minor=2500, currency="USD"
        )
        CreditPack.objects.create(
            code="old",
            name="Old",
            credits=10,
            price_minor=1,
            currency="GBP",
            status="archived",
        )
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        assert [p["code"] for p in body["credit_packs"]] == ["starter"]


class TestOnlyTheCallersOrganization:
    def test_another_organizations_balances_and_entries_are_invisible(
        self, shop, console_org, login
    ):
        other = console_org()
        seed(other["org"].id, "platform", 77_777)
        seed(shop["id"], "platform", 1_000)
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        assert body["platform"]["balance_minor"] == 1_000
        rows = shop["client"].get(url(shop["slug"], "wallets/transactions")).json()
        assert rows["total"] == 1
        assert "77777" not in str(rows)
        # The other organization's own owner cannot read this one's by its slug.
        stranger = login(other["owner"])
        assert stranger.get(url(shop["slug"], "wallets")).status_code in (403, 404)

    def test_a_query_parameter_cannot_name_another_owner(self, shop, console_org):
        other = console_org()
        seed(other["org"].id, "platform", 5_555)
        rows = (
            shop["client"]
            .get(
                url(shop["slug"], "wallets/transactions"),
                {"owner_id": str(other["org"].id), "organization": str(other["org"].id)},
            )
            .json()
        )
        assert rows["total"] == 0


class TestTransactions:
    def test_filters_and_pages(self, shop):
        seed(shop["id"], "platform", 100)
        seed(shop["id"], "platform", 200)
        seed(shop["id"], "consumption", 300)
        client = shop["client"]
        everything = client.get(url(shop["slug"], "wallets/transactions")).json()
        assert everything["total"] == 3
        platform = client.get(
            url(shop["slug"], "wallets/transactions"), {"kind": "platform", "limit": 1}
        ).json()
        assert platform["total"] == 2 and len(platform["items"]) == 1
        assert platform["items"][0]["kind"] == "platform"
        second = client.get(
            url(shop["slug"], "wallets/transactions"),
            {"kind": "platform", "limit": 1, "offset": 1},
        ).json()
        assert second["items"][0]["id"] != platform["items"][0]["id"]
        typed = client.get(
            url(shop["slug"], "wallets/transactions"), {"type": "usage"}
        ).json()
        assert typed["total"] == 0
        today = timezone.now().date()
        ranged = client.get(
            url(shop["slug"], "wallets/transactions"),
            {"from": (today - timedelta(days=1)).isoformat(), "to": today.isoformat()},
        ).json()
        assert ranged["total"] == 3
        future = client.get(
            url(shop["slug"], "wallets/transactions"),
            {"from": (today + timedelta(days=1)).isoformat()},
        ).json()
        assert future["total"] == 0

    @pytest.mark.parametrize(
        "query",
        [
            {"kind": "commission"},
            {"type": "free_money"},
            {"from": "yesterday"},
            {"from": "2026-11-10", "to": "2026-11-01"},
        ],
    )
    def test_nonsense_is_refused(self, shop, query):
        response = shop["client"].get(url(shop["slug"], "wallets/transactions"), query)
        assert response.status_code == 400


class TestUsage:
    def test_events_filter_by_meter_and_product(self, shop):
        from workeasy360.commercial.wallets import usage

        seed(shop["id"], "consumption", 10_000)
        rate("sms", 5)
        for i, product in enumerate(("crm", "books", "crm")):
            usage.record(
                organization_id=shop["id"],
                product_code=product,
                meter="sms",
                quantity=2,
                idempotency_key=f"sms-{i}",
                currency="GBP",
            )
        client = shop["client"]
        body = client.get(url(shop["slug"], "wallets/usage")).json()
        assert body["total"] == 3 and body["products"] == ["books", "crm"]
        assert {m["value"] for m in body["meters"]} >= {"sms", "whatsapp", "ai_tokens"}
        crm = client.get(url(shop["slug"], "wallets/usage"), {"product": "crm"}).json()
        assert crm["total"] == 2
        assert crm["items"][0]["meter_label"] and crm["items"][0]["amount"] == 10
        assert (
            client.get(url(shop["slug"], "wallets/usage"), {"meter": "whatsapp"}).json()[
                "total"
            ]
            == 0
        )
        assert (
            client.get(
                url(shop["slug"], "wallets/usage"), {"meter": "bitcoin"}
            ).status_code
            == 400
        )


class TestSpending:
    def test_a_top_up_arrives_once_when_paid(self, shop):
        placed = post(
            shop["client"],
            url(shop["slug"], "wallets/top-up"),
            {"wallet_kind": "platform", "amount_minor": 25_000, "currency": "GBP"},
        )
        assert placed.status_code == 201, placed.content
        order = placed.json()
        assert (
            order["status"] == "pending_payment" and order["amount_due_minor"] == 25_000
        )
        # Nothing before the money does.
        assert (
            shop["client"].get(url(shop["slug"], "wallets")).json()["platform"]["exists"]
            is False
        )
        paid = pay_order(shop["client"], shop["slug"], shop["id"], order)
        assert paid["status"] in ("paid", "fulfilled")
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        assert body["platform"]["balance_minor"] == 25_000
        assert body["consumption"]["balance_minor"] == 0
        history = shop["client"].get(url(shop["slug"], "wallets/transactions")).json()
        assert [(e["entry_type"], e["amount"]) for e in history["items"]] == [
            ("top_up", 25_000)
        ]
        assert history["items"][0]["source_type"] == "order"

    def test_a_failed_payment_moves_no_balance(self, shop):
        order = post(
            shop["client"],
            url(shop["slug"], "wallets/top-up"),
            {"wallet_kind": "consumption", "amount_minor": 9_000, "currency": "GBP"},
        ).json()
        after = pay_order(
            shop["client"], shop["slug"], shop["id"], order, outcome="failed"
        )
        assert after["status"] not in ("paid", "fulfilled")
        body = shop["client"].get(url(shop["slug"], "wallets")).json()
        assert body["consumption"]["balance_minor"] == 0
        assert (
            shop["client"].get(url(shop["slug"], "wallets/transactions")).json()["total"]
            == 0
        )

    def test_the_same_key_is_the_same_order(self, shop):
        key = uuid.uuid4().hex
        body = {"wallet_kind": "platform", "amount_minor": 100, "currency": "GBP"}
        first = post(shop["client"], url(shop["slug"], "wallets/top-up"), body, key=key)
        again = post(shop["client"], url(shop["slug"], "wallets/top-up"), body, key=key)
        assert first.status_code == again.status_code == 201
        assert first.json()["order_id"] == again.json()["order_id"]

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"wallet_kind": "ai_credits", "amount_minor": 100}, "wallet_kind"),
            ({"wallet_kind": "platform", "amount_minor": 0}, "amount_minor"),
            ({"wallet_kind": "platform", "amount_minor": -5}, "amount_minor"),
            ({"wallet_kind": "platform", "amount_minor": "100"}, "amount_minor"),
            ({"wallet_kind": "platform", "amount_minor": True}, "amount_minor"),
            (
                {"wallet_kind": "platform", "amount_minor": 100, "currency": "USD"},
                "currency",
            ),
        ],
    )
    def test_bad_top_ups_are_refused(self, shop, body, field):
        response = post(shop["client"], url(shop["slug"], "wallets/top-up"), body)
        assert response.status_code == 400, response.content
        assert field in response.content.decode()

    def test_a_missing_key_is_refused(self, shop):
        response = post(
            shop["client"],
            url(shop["slug"], "wallets/top-up"),
            {"wallet_kind": "platform", "amount_minor": 100},
            key="",
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("role", ["member", "readonly", "admin"])
    def test_only_billing_managers_and_owners_spend(self, shop, login, role):
        client = login(shop["add"](role)["user"])
        assert (
            post(
                client,
                url(shop["slug"], "wallets/top-up"),
                {"wallet_kind": "platform", "amount_minor": 100},
            ).status_code
            == 403
        )
        assert (
            post(
                client, url(shop["slug"], "wallets/credit-packs"), {"pack_code": "x"}
            ).status_code
            == 403
        )

    def test_without_a_billing_account_the_customer_is_told(self, console_org, login):
        org = console_org()
        client = login(org["owner"])
        response = post(
            client,
            url(org["slug"], "wallets/top-up"),
            {"wallet_kind": "platform", "amount_minor": 100},
        )
        assert response.status_code == 400
        assert "billing" in response.content.decode().lower()

    def test_a_credit_pack_is_bought_at_its_price(self, shop):
        from workeasy360.commercial.wallets.models import CreditPack

        CreditPack.objects.create(
            code="starter", name="Starter", credits=1000, price_minor=2500, currency="GBP"
        )
        placed = post(
            shop["client"],
            url(shop["slug"], "wallets/credit-packs"),
            {"pack_code": "starter"},
        )
        assert placed.status_code == 201, placed.content
        order = placed.json()
        assert order["amount_due_minor"] == 2500
        pay_order(shop["client"], shop["slug"], shop["id"], order)
        ai = shop["client"].get(url(shop["slug"], "wallets")).json()["ai_credits"]
        assert ai["balance"] == 1000 and ai["purchased_total"] == 1000
        unknown = post(
            shop["client"],
            url(shop["slug"], "wallets/credit-packs"),
            {"pack_code": "nope"},
        )
        assert unknown.status_code == 400

    def test_spending_is_on_the_record(self, shop):
        from workeasy360.platform.audit.models import AuditEvent
        from workeasy360.platform.tenancy.db import organization_db_scope
        from workeasy360.platform.tenancy.unscoped import unscoped_access

        post(
            shop["client"],
            url(shop["slug"], "wallets/top-up"),
            {"wallet_kind": "platform", "amount_minor": 4_200},
        )
        with (
            unscoped_access(reason="Test reads the audit trail", module="tests"),
            organization_db_scope(shop["id"]),
        ):
            rows = list(
                AuditEvent.unscoped.filter(
                    organization_id=shop["id"], action="wallet.order_placed"
                )
            )
        assert len(rows) == 1 and rows[0].actor_user_id == shop["owner"].id

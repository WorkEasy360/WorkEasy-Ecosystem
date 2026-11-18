"""The bell, the member's own alert choices and the Billing Profile (User Admin Panel,
2026-11-11)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.extension.conftest import url

pytestmark = pytest.mark.django_db


def bell(client, slug):
    response = client.get(url(slug, "notifications"))
    assert response.status_code == 200, response.content
    return response.json()


def categories(body):
    return {item["category"] for item in body["items"]}


def low_wallet(org_id, kind="platform", balance=100, threshold=500):
    from workeasy360.commercial.wallets import ledger

    wallet = ledger.ensure(
        owner_type="organization",
        owner_id=org_id,
        kind=kind,
        currency="" if kind == "ai_credits" else "GBP",
    )
    ledger.post(
        wallet.id,
        amount=balance,
        entry_type="top_up",
        reason="Test seeds a balance",
        idempotency_key=uuid.uuid4().hex,
    )
    wallet.low_balance_threshold = threshold
    wallet.save(update_fields=["low_balance_threshold"])
    return wallet


def pending_order(shop):
    placed = shop["client"].post(
        url(shop["slug"], "wallets/top-up"),
        {"wallet_kind": "platform", "amount_minor": 1_000},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert placed.status_code == 201, placed.content
    return placed.json()


class TestBell:
    def test_a_quiet_organization_has_nothing(self, shop):
        assert bell(shop["client"], shop["slug"]) == {"items": [], "count": 0}

    def test_payment_and_low_balance_for_those_who_see_billing(self, shop, login):
        order = pending_order(shop)
        low_wallet(shop["id"])
        body = bell(shop["client"], shop["slug"])
        assert categories(body) == {"payment", "low_balance"}
        payment = next(i for i in body["items"] if i["category"] == "payment")
        assert order["number"] in payment["title"] and payment["href"] == "orders"
        low = next(i for i in body["items"] if i["category"] == "low_balance")
        assert low["href"] == "wallets/platform" and low["tone"] in ("warning", "danger")
        assert body["count"] == len(body["items"])
        # A plain member sees neither: they cannot see billing.
        member = login(shop["add"]("member")["user"])
        assert bell(member, shop["slug"])["items"] == []

    def test_an_invoice_is_overdue_only_once_its_due_day_has_passed(self):
        """Immediate terms make an invoice due the moment it is issued: it is "due
        today" for the rest of that day, never "overdue" seconds later."""
        from workeasy360.commercial.controlplane.customer_alerts import invoice_item

        now = timezone.now().replace(hour=18, minute=0)
        row = {"id": "i1", "number": "INV-1"}
        today = invoice_item({**row, "due_at": now - timedelta(hours=2)}, now)
        assert today["title"] == "Invoice INV-1 is due today"
        assert today["tone"] == "warning"
        late = invoice_item({**row, "due_at": now - timedelta(days=1)}, now)
        assert late["title"] == "Invoice INV-1 is overdue" and late["tone"] == "danger"
        assert late["detail"].startswith("Was due on")
        later = invoice_item({**row, "due_at": now + timedelta(days=3)}, now)
        assert later["title"] == "Invoice INV-1 is due" and later["tone"] == "warning"

    def test_no_threshold_no_low_balance_alert(self, shop):
        low_wallet(shop["id"], threshold=None)
        assert bell(shop["client"], shop["slug"])["items"] == []

    def test_setup_needing_the_customer(self, shop, login):
        from workeasy360.commercial.provisioning.models import CustomerSetup

        CustomerSetup.objects.create(
            organization_id=shop["id"],
            product_code="crm",
            status="correction_required",
            decision_reason="The GST number does not match the business name.",
        )
        CustomerSetup.objects.create(
            organization_id=shop["id"], product_code="books", status="approved"
        )
        member = login(shop["add"]("member")["user"])
        body = bell(member, shop["slug"])
        assert [i["category"] for i in body["items"]] == ["setup"]
        item = body["items"][0]
        assert item["href"] == "apps?filter=setup" and "GST" in item["detail"]
        assert item["tone"] == "warning"

    def test_renewal_within_two_weeks(self, shop, login):
        from tests.extension.conftest import buy_directly
        from workeasy360.commercial.payments.invoicing import organization_scope
        from workeasy360.commercial.subscriptions.models import Subscription

        buy_directly(shop, shop["catalog"]["versions"]["crm_professional"].id)
        # Far off: nothing to say yet.
        assert "renewal" not in categories(bell(shop["client"], shop["slug"]))
        with organization_scope(shop["id"], reason="Test moves the period end"):
            Subscription.unscoped.filter(organization_id=shop["id"]).update(
                current_period_end=timezone.now() + timedelta(days=5)
            )
        body = bell(shop["client"], shop["slug"])
        renewal = [i for i in body["items"] if i["category"] == "renewal"]
        assert len(renewal) == 1 and renewal[0]["href"] == "settings/billing"
        # A plain member cannot see subscriptions.
        member = login(shop["add"]("member")["user"])
        assert "renewal" not in categories(bell(member, shop["slug"]))

    def test_a_support_reply_to_my_ticket(self, shop, login, user_factory):
        from workeasy360.commercial.partners.models import (
            PartnerTicket,
            PartnerTicketMessage,
        )

        person = shop["add"]("member")
        client = login(person["user"])
        opened = client.post(
            url(shop["slug"], "support-tickets"),
            {"subject": "Invoice question", "message": "Why was I charged twice?"},
            content_type="application/json",
        )
        assert opened.status_code == 201, opened.content
        ticket_id = opened.json()["id"]
        # Only my own message so far: nothing to tell me.
        assert "ticket" not in categories(bell(client, shop["slug"]))
        staff = user_factory(is_staff=True)
        ticket = PartnerTicket.objects.get(id=ticket_id)
        PartnerTicketMessage.objects.create(
            ticket=ticket,
            side="platform",
            author=staff,
            body="Looking into it.",
            internal=False,
            created_at=timezone.now() + timedelta(seconds=1),
        )
        body = bell(client, shop["slug"])
        item = next(i for i in body["items"] if i["category"] == "ticket")
        assert item["href"] == f"help?ticket={ticket_id}"
        # Someone else's ticket is not my alert.
        assert "ticket" not in categories(bell(shop["client"], shop["slug"]))
        # An internal note is never a reply.
        PartnerTicketMessage.objects.create(
            ticket=ticket,
            side="customer",
            author=person["user"],
            body="Thanks",
            internal=False,
            created_at=timezone.now() + timedelta(seconds=2),
        )
        PartnerTicketMessage.objects.create(
            ticket=ticket,
            side="platform",
            author=staff,
            body="note",
            internal=True,
            created_at=timezone.now() + timedelta(seconds=3),
        )
        assert "ticket" not in categories(bell(client, shop["slug"]))


class TestPreferences:
    def test_defaults_are_on_and_a_choice_hides_its_category(self, shop):
        prefs = shop["client"].get(url(shop["slug"], "notification-preferences")).json()
        assert [c["key"] for c in prefs["categories"]] == [
            "setup",
            "renewal",
            "payment",
            "low_balance",
            "ticket",
        ]
        assert all(c["in_app"] for c in prefs["categories"])
        pending_order(shop)
        assert "payment" in categories(bell(shop["client"], shop["slug"]))
        saved = shop["client"].put(
            url(shop["slug"], "notification-preferences"),
            {"categories": [{"key": "payment", "in_app": False}]},
            content_type="application/json",
        )
        assert saved.status_code == 200, saved.content
        by_key = {c["key"]: c["in_app"] for c in saved.json()["categories"]}
        assert by_key["payment"] is False and by_key["renewal"] is True
        assert "payment" not in categories(bell(shop["client"], shop["slug"]))

    def test_a_choice_is_mine_alone(self, shop, login):
        manager = login(shop["add"]("billing_manager")["user"])
        manager.put(
            url(shop["slug"], "notification-preferences"),
            {"categories": [{"key": "payment", "in_app": False}]},
            content_type="application/json",
        )
        pending_order(shop)
        assert "payment" in categories(bell(shop["client"], shop["slug"]))
        assert "payment" not in categories(bell(manager, shop["slug"]))

    @pytest.mark.parametrize(
        "body",
        [
            {"categories": [{"key": "marketing", "in_app": True}]},
            {"categories": [{"key": "payment", "in_app": "no"}]},
            {"categories": "payment"},
            {"categories": ["payment"]},
        ],
    )
    def test_nonsense_is_refused(self, shop, body):
        response = shop["client"].put(
            url(shop["slug"], "notification-preferences"),
            body,
            content_type="application/json",
        )
        assert response.status_code == 400


PROFILE = {
    "legal_name": "Acme Enterprises Pvt. Ltd.",
    "address_line1": "4th Floor, Tower B",
    "address_line2": "Sector 62",
    "city": "Noida",
    "state": "Uttar Pradesh",
    "postal_code": "201309",
    "country": "in",
    "tax_reference": "09aaacb1234f1z5",  # pragma: allowlist secret
    "billing_email": "Accounts@Acme.example",
}


def patch(client, slug, body):
    return client.patch(
        url(slug, "billing-account"), body, content_type="application/json"
    )


class TestBillingProfile:
    def test_owner_saves_it_and_everyone_with_billing_view_reads_it(self, shop, login):
        saved = patch(shop["client"], shop["slug"], PROFILE)
        assert saved.status_code == 200, saved.content
        account = saved.json()["billing_account"]
        assert account["legal_name"] == PROFILE["legal_name"]
        assert account["country"] == "IN"
        assert account["tax_reference"] == "09AAACB1234F1Z5"  # pragma: allowlist secret
        assert account["billing_email"] == "accounts@acme.example"
        assert account["currency"] == "GBP"  # untouched
        manager = login(shop["add"]("billing_manager")["user"])
        read = manager.get(url(shop["slug"], "billing-account")).json()["billing_account"]
        assert read["city"] == "Noida" and read["postal_code"] == "201309"

    def test_a_billing_manager_may_edit_others_may_not(self, shop, login):
        manager = login(shop["add"]("billing_manager")["user"])
        assert patch(manager, shop["slug"], {"city": "Pune"}).status_code == 200
        for role in ("admin", "member", "readonly"):
            client = login(shop["add"](role)["user"])
            assert patch(client, shop["slug"], {"city": "Delhi"}).status_code == 403, role

    @pytest.mark.parametrize(
        "body",
        [
            {"currency": "USD"},
            {"collection_mode": "invoice"},
            {"billing_region": "EU"},
            {"country": "India"},
            {"postal_code": "<script>"},
            {"billing_email": "not-an-email"},
            {"legal_name": "x" * 201},
            {"city": 42},
        ],
    )
    def test_only_profile_fields_and_only_valid_values(self, shop, body):
        response = patch(shop["client"], shop["slug"], body)
        assert response.status_code == 400, (body, response.content)
        account = (
            shop["client"]
            .get(url(shop["slug"], "billing-account"))
            .json()["billing_account"]
        )
        assert account["currency"] == "GBP"
        assert account["collection_mode"] == "automatic"

    def test_no_account_yet_says_open_one(self, console_org, login):
        org = console_org()
        response = patch(login(org["owner"]), org["slug"], {"city": "Noida"})
        assert response.status_code == 400

    def test_a_change_is_audited_by_field_name_only(self, shop):
        from workeasy360.platform.audit.models import AuditEvent
        from workeasy360.platform.tenancy.db import organization_db_scope
        from workeasy360.platform.tenancy.unscoped import unscoped_access

        patch(
            shop["client"],
            shop["slug"],
            {"city": "Noida", "billing_email": "a@b.example"},
        )
        with (
            unscoped_access(reason="Test reads the audit trail", module="tests"),
            organization_db_scope(shop["id"]),
        ):
            row = AuditEvent.unscoped.get(
                organization_id=shop["id"],
                action="commercial.billing_account.profile_changed",
            )
        assert row.changes == {"fields": ["billing_email", "city"]}
        assert "a@b.example" not in str(row.changes)

"""The rules an Admin sets reach the money: renewal, plan change, what waits for someone,
the names and terms on the earnings screens, and the address staff are told at."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from tests.admin_panel.test_workflows import post, version
from tests.extension.conftest import place_and_pay, url

pytestmark = pytest.mark.django_db


def _plan_rules(admin, code, **rules):
    from workeasy360.commercial.catalog.models import Plan

    plan = Plan.objects.get(code=code)
    saved = admin.put(
        f"/api/v1/admin/catalog/plans/{plan.id}/rules",
        rules,
        content_type="application/json",
    )
    assert saved.status_code == 200, saved.content


def _age(shop):
    """End the paid period now. A line's own dates are a commercial fact the database refuses
    to change, so it is the period that moves."""
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.subscriptions.models import Subscription

    now = timezone.now()
    with organization_scope(shop["id"], reason="Test ends a paid period"):
        Subscription.unscoped.filter(organization_id=shop["id"]).update(
            current_period_start=now - timedelta(days=30),
            current_period_end=now,
        )


def _lines(shop):
    """The lines still running. The API lists ended ones too, with their end date."""
    subscriptions = (
        shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"]
    )
    items = subscriptions[0]["items"] if subscriptions else []
    return [item for item in items if not item["removed_at"]]


def _owned(shop):
    """What the customer's access is computed from."""
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.subscriptions.selectors import (
        commercial_state,
        paid_product_codes,
    )

    with organization_scope(shop["id"], reason="Test reads what is held"):
        return set(paid_product_codes(commercial_state(shop["id"]), timezone.now()))


class TestRenewalRule:
    def test_a_plan_that_does_not_renew_ends_with_its_paid_period_and_others_carry_on(
        self, shop, staff_login
    ):
        from workeasy360.commercial.subscriptions import system

        admin, _ = staff_login("platform.staff_pricing.manage")
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "books_professional"),
        )
        assert len(_lines(shop)) == 2

        _plan_rules(admin, "crm_professional", renewal_rule="none")

        # Inside the paid period nothing changes.
        system.process_due_subscriptions(at=timezone.now())
        assert {line["plan_code"] for line in _lines(shop)} == {
            "crm_professional",
            "books_professional",
        }

        # After it the line ends, the period rolls, and the other plan carries on.
        _age(shop)
        counts = system.process_due_subscriptions(at=timezone.now())
        assert counts["periods_rolled"] == 1
        assert {line["plan_code"] for line in _lines(shop)} == {"books_professional"}
        # And it is access that ends, not only a line in a list.
        assert _owned(shop) == {"books"}

    def test_an_auto_renewing_plan_is_untouched(self, shop):
        from workeasy360.commercial.subscriptions import system

        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        _age(shop)
        counts = system.process_due_subscriptions(at=timezone.now())
        assert counts["periods_rolled"] == 1
        assert [line["plan_code"] for line in _lines(shop)] == ["crm_professional"]

    def test_the_customer_can_buy_again_after_a_line_ended(self, shop, staff_login):
        from workeasy360.commercial.subscriptions import system

        admin, _ = staff_login("platform.staff_pricing.manage")
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        _plan_rules(admin, "crm_professional", renewal_rule="manual")
        _age(shop)
        system.process_due_subscriptions(at=timezone.now())
        assert _lines(shop) == []
        again = place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        assert again["status"] == "fulfilled"
        assert [line["plan_code"] for line in _lines(shop)] == ["crm_professional"]


class TestPlanChangeRule:
    def _change(self, shop, target):
        item = _lines(shop)[0]
        return post(
            shop["client"],
            url(shop["slug"], f"subscription-items/{item['id']}/change-plan"),
            {"to_plan_version_id": version(shop, target)},
            key=uuid.uuid4().hex,
        )

    def test_no_changes_means_none_either_way(self, shop, staff_login):
        admin, _ = staff_login("platform.staff_pricing.manage")
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        _plan_rules(admin, "crm_professional", change_rule="none")
        refused = self._change(shop, "crm_starter")
        assert refused.status_code == 400, refused.content
        assert "does not allow" in refused.json()["detail"]

    def test_upgrade_only_refuses_a_downgrade_and_allows_an_upgrade(
        self, shop, staff_login
    ):
        admin, _ = staff_login("platform.staff_pricing.manage")
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        _plan_rules(admin, "crm_professional", change_rule="upgrade_only")
        refused = self._change(shop, "crm_starter")
        assert refused.status_code == 400, refused.content
        assert "only be upgraded" in refused.json()["detail"]

    def test_with_no_rule_a_downgrade_is_still_allowed(self, shop):
        place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=version(shop, "crm_professional"),
        )
        assert self._change(shop, "crm_starter").status_code in (200, 201), (
            "the default must not change"
        )


class TestWhatWaitsForSomeone:
    def _failed_order(self, shop):
        from workeasy360.commercial.payments.models import OrderSummary

        return OrderSummary.objects.create(
            order_id=uuid.uuid4(),
            organization_id=shop["id"],
            number=f"ORD-{uuid.uuid4().hex[:6]}",
            kind="plan",
            status="failed",
            description="Refunded automatically",
            currency="GBP",
            amount_due_minor=1000,
            created_at=timezone.now(),
        )

    def _refund(self, shop, attempt, status, *, minutes=0):
        from workeasy360.commercial.payments.models import RefundSummary

        return RefundSummary.objects.create(
            refund_id=uuid.uuid4(),
            organization_id=shop["id"],
            attempt_id=attempt,
            amount_minor=1000,
            currency="GBP",
            reason="Order could not be fulfilled",
            status=status,
            requested_by_user_id=shop["owner"].id,
            created_at=timezone.now() + timedelta(minutes=minutes),
        )

    def test_a_failed_order_waits_for_no_one_a_failed_refund_does(
        self, shop, staff_login
    ):
        from workeasy360.commercial.payments import admin_reads

        staff, _ = staff_login("platform.staff_payment.view")
        self._failed_order(shop)
        assert admin_reads.stuck_activation_count() == 0
        assert staff.get("/api/v1/admin/orders/stuck").json()["results"] == []

        attempt = uuid.uuid4()
        failed = self._refund(shop, attempt, "failed")
        assert admin_reads.refunds_waiting_count() == 1
        listed = staff.get("/api/v1/admin/refunds", {"waiting": "1"}).json()["results"]
        assert [row["refund_id"] for row in listed] == [str(failed.refund_id)]

        # A later refund of the same payment answers it.
        self._refund(shop, attempt, "succeeded", minutes=5)
        assert admin_reads.refunds_waiting_count() == 0
        assert (
            staff.get("/api/v1/admin/refunds", {"waiting": "1"}).json()["results"] == []
        )
        # ...but the page of all refunds still shows both.
        assert len(staff.get("/api/v1/admin/refunds").json()["results"]) == 2

    def test_needing_review_waits_and_reaches_the_dashboard(self, shop, staff_login):
        staff, _ = staff_login("platform.staff_payment.view")
        self._refund(shop, uuid.uuid4(), "needs_review")
        items = {
            i["key"]: i["count"]
            for i in staff.get("/api/v1/admin/attention").json()["items"]
        }
        assert items["failed_payments"] == 1


class TestEarningsScreens:
    def test_the_lists_name_the_partner_and_the_kind_of_earning(
        self, staff_login, partner_factory
    ):
        staff, _ = staff_login(
            "platform.staff_commission.view",
            "platform.staff_commission.manage",
            "platform.staff_payout.manage",
        )
        partner = partner_factory()["partner"]
        adjusted = post(
            staff,
            f"/api/v1/admin/partners/{partner.id}/adjustments",
            {
                "amount_minor": 12000,
                "currency": "GBP",
                "reason_code": "correction",
                "reason": "Agreed correction for October",
            },
            key=uuid.uuid4().hex,
        )
        assert adjusted.status_code in (200, 201), adjusted.content
        rows = staff.get(
            "/api/v1/admin/commission/accruals", {"partner_id": str(partner.id)}
        ).json()["results"]
        assert rows[0]["partner_name"] == "Test Partner"
        assert rows[0]["earning"]

        prepared = post(
            staff,
            "/api/v1/admin/payouts",
            {
                "partner_id": str(partner.id),
                "period_end": timezone.now().date().isoformat(),
            },
            key=uuid.uuid4().hex,
        )
        assert prepared.status_code in (200, 201), prepared.content
        payouts = staff.get(
            "/api/v1/admin/payouts", {"partner_id": str(partner.id)}
        ).json()["results"]
        assert payouts[0]["partner_name"] == "Test Partner"

    def test_the_terms_come_from_the_settings_and_need_their_grant(
        self, staff_login, settings
    ):
        settings.W360_COMMISSION_HOLDBACK_DAYS = 21
        settings.W360_PARTNER_MIN_WITHDRAWAL_MINOR = 250000
        settings.W360_PARTNER_TDS_RATE_BP = 300
        staff, _ = staff_login("platform.staff_commission.view")
        terms = staff.get("/api/v1/admin/commission/terms").json()
        assert terms["clearance_days"] == 21
        assert terms["min_withdrawal_minor"] == 250000
        assert terms["tds_rate_bp"] == 300
        assert "refund" in terms["reversal_reasons"]
        other, _ = staff_login("platform.staff_customer.view")
        assert other.get("/api/v1/admin/commission/terms").status_code == 403

    def test_a_rule_row_names_its_partner(self, staff_login, partner_factory):
        staff, _ = staff_login(
            "platform.staff_commission.manage", "platform.staff_commission.view"
        )
        partner = partner_factory()["partner"]
        made = post(
            staff,
            "/api/v1/admin/commission/rules",
            {
                "code": "northern",
                "rate_bp": 1000,
                "partner_id": str(partner.id),
                "reason": "Agreed with the partner",
            },
        )
        assert made.status_code == 201, made.content
        row = next(
            r
            for r in staff.get("/api/v1/admin/commission/rules").json()["results"]
            if r["code"] == "northern"
        )
        assert row["partner_name"] == "Test Partner"


class TestStaffNotifications:
    def _address(self, value):
        from workeasy360.platform.administration.models import PlatformSettings

        PlatformSettings.objects.update_or_create(
            id=1, defaults={"notifications_email": value}
        )

    def test_a_customer_ticket_tells_the_address_without_the_customers_words(self, shop):
        self._address("ops@example.com")
        with mock.patch(
            "workeasy360.platform.notifications.services.notify_account"
        ) as sent:
            made = post(
                shop["client"],
                url(shop["slug"], "support-tickets"),
                {"subject": "Private matter", "message": "A secret."},
            )
        assert made.status_code == 201, made.content
        assert sent.call_count == 1
        call = sent.call_args.kwargs
        assert call["recipient_email"] == "ops@example.com"
        assert (
            "secret" not in str(call["context"]).lower()
            and "private" not in str(call["context"]).lower()
        )

    def test_no_address_no_email_and_the_ticket_still_stands(self, shop):
        self._address("")
        with mock.patch(
            "workeasy360.platform.notifications.services.notify_account"
        ) as sent:
            made = post(
                shop["client"],
                url(shop["slug"], "support-tickets"),
                {"subject": "Hello there", "message": "A question."},
            )
        assert made.status_code == 201 and sent.call_count == 0

    def test_a_send_that_fails_does_not_lose_the_ticket(self, shop):
        self._address("ops@example.com")
        with mock.patch(
            "workeasy360.platform.notifications.services.notify_account",
            side_effect=RuntimeError("down"),
        ):
            made = post(
                shop["client"],
                url(shop["slug"], "support-tickets"),
                {"subject": "Hello there", "message": "A question."},
            )
        assert made.status_code == 201


class TestUsageReport:
    def test_charges_in_two_currencies_are_never_one_figure(self, console_org):
        from tests.admin_panel.test_usage_intake import _rate
        from workeasy360.commercial.wallets import admin as wallet_admin
        from workeasy360.commercial.wallets.models import UsageEvent

        org = console_org()["org"]
        now = timezone.now()
        for currency, amount in (("INR", 500), ("GBP", 7)):
            rate = _rate("sms", currency=currency, unit=1, per=1)
            UsageEvent.objects.create(
                organization_id=org.id,
                product_code="crm",
                meter="sms",
                quantity=1,
                idempotency_key=f"k-{currency}",
                occurred_at=now,
                outcome="charged",
                rate=rate,
                amount=amount,
            )
        rows = wallet_admin.usage_by_product(None, None)
        assert {(r["currency"], r["amount"]) for r in rows} == {("INR", 500), ("GBP", 7)}


class TestSearchAndProducts:
    def test_ticket_search_finds_the_customer_by_name(self, shop, staff_login):
        post(
            shop["client"],
            url(shop["slug"], "support-tickets"),
            {"subject": "Hello there", "message": "A question."},
        )
        staff, _ = staff_login("platform.staff_support.view")
        name = shop["org"].display_name
        found = staff.get("/api/v1/admin/tickets", {"q": name}).json()
        assert [t["subject"] for t in found["items"]] == ["Hello there"]
        none = staff.get("/api/v1/admin/tickets", {"q": "nobody by this name"}).json()
        assert none["items"] == []

    def test_the_product_console_carries_saved_app_links(self, staff_login, catalog):
        staff, _ = staff_login(
            "platform.staff_catalog.view", "platform.staff_catalog.manage"
        )
        product = next(
            p
            for p in staff.get("/api/v1/admin/products/console").json()["products"]
            if p["code"] == "crm"
        )
        assert product["app_links"] == {}
        saved = staff.put(
            f"/api/v1/admin/catalog/products/{product['id']}/app-links",
            {"app_links": {"android": "https://play.example.com/crm"}},
            content_type="application/json",
        )
        assert saved.status_code == 200, saved.content
        product = next(
            p
            for p in staff.get("/api/v1/admin/products/console").json()["products"]
            if p["code"] == "crm"
        )
        assert product["app_links"]["android"] == "https://play.example.com/crm"


class TestTheLedgerIsAppendOnly:
    def test_an_entry_cannot_be_changed_or_removed(self, console_org):
        from django.db import DatabaseError, transaction

        from tests.admin_panel.test_usage_intake import _fund
        from workeasy360.commercial.wallets.models import WalletEntry

        _fund(console_org()["org"].id)
        entry = WalletEntry.objects.first()
        with pytest.raises(DatabaseError), transaction.atomic():
            WalletEntry.objects.filter(id=entry.id).update(amount=entry.amount + 1)
        with pytest.raises(DatabaseError), transaction.atomic():
            WalletEntry.objects.filter(id=entry.id).delete()
        assert WalletEntry.objects.get(id=entry.id).amount == entry.amount


class TestReviewersCanReadDocuments:
    """Chrome's PDF viewer refuses to open inside a CSP sandbox, so a PDF has none. Anything
    else keeps it. Both stay private, uncached, unframed and unsniffed."""

    def _served(self, content_type):
        from workeasy360.interfaces.api.v1.admin_panel_views import _document_response

        return _document_response(b"x", content_type, "cert.pdf")

    def test_a_pdf_opens_in_the_viewer(self):
        policy = self._served("application/pdf")["Content-Security-Policy"]
        assert "sandbox" not in policy and "frame-ancestors 'none'" in policy

    def test_an_image_stays_sandboxed(self):
        policy = self._served("image/png")["Content-Security-Policy"]
        assert policy.startswith("sandbox;")

    def test_both_stay_private(self):
        for content_type in ("application/pdf", "image/png"):
            response = self._served(content_type)
            assert response["Cache-Control"] == "no-store, private"
            assert response["X-Content-Type-Options"] == "nosniff"

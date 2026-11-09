"""Partner KYC, the public application, wallets and the dashboard's definitions,
through the real endpoints on real PostgreSQL."""

from __future__ import annotations

import base64
import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from tests.extension.conftest import PASSWORD

pytestmark = pytest.mark.django_db

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
APPLY = "/api/v1/public/partner-applications"
REVIEW = "platform.staff_kyc.review"


@pytest.fixture(autouse=True)
def kyc_on(settings):
    settings.PARTNER_KYC_REQUIRED = True


def _application(**extra):
    return {
        "business_name": "Acme Traders",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": f"ada-{uuid.uuid4().hex[:8]}@example.com",
        "password": PASSWORD,
        "country": "IN",
        "phone": "+91 99999 00000",
        "interest": "affiliate",
        **extra,
    }


def _apply(**extra):
    client = Client()
    client.get("/api/v1/auth/session")
    body = _application(**extra)
    response = client.post(APPLY, body, content_type="application/json")
    return response, body


def _applicant(email):
    from workeasy360.commercial.partners.models import PartnerMember
    from workeasy360.platform.identity.models import User

    user = User.objects.get(email=email.lower())
    user.status = "active"
    user.email_verified_at = timezone.now()
    user.save(update_fields=["status", "email_verified_at"])
    member = PartnerMember.objects.get(user=user)
    return user, member.partner


def _signed_in(login, user):
    return login(user, password=PASSWORD)


def _requirement(key="id_proof", label="ID proof", required=True):
    from workeasy360.commercial.partners.models import KycRequirement

    return KycRequirement.objects.create(key=key, label=label, is_required=required)


def _json(client, method, url, body=None):
    return getattr(client, method)(url, body or {}, content_type="application/json")


class TestPublicApplication:
    def test_applying_grants_nothing(self):
        from workeasy360.commercial.partners import selectors
        from workeasy360.commercial.partners.models import PartnerAccount
        from workeasy360.platform.identity.models import User

        response, body = _apply()
        assert response.status_code == 202
        user = User.objects.get(email=body["email"])
        assert user.status == "pending"
        partner = PartnerAccount.objects.get(contact_email=body["email"])
        assert partner.status == "draft"
        assert selectors.active_capabilities(partner.id) == frozenset()
        assert partner.kyc.status == "draft"
        assert partner.kyc.requested_experience == "affiliate"
        assert partner.kyc.source == "application"

    def test_the_answer_does_not_say_whether_an_address_is_registered(self, user_factory):
        from workeasy360.commercial.partners.models import PartnerAccount

        existing = user_factory(password=PASSWORD, email="taken@example.com")
        first, _ = _apply(email=existing.email)
        second, _ = _apply(email="fresh-address@example.com")
        assert first.status_code == second.status_code == 202
        assert first.json() == second.json()
        assert not PartnerAccount.objects.filter(contact_email=existing.email).exists()

    def test_it_can_be_closed(self):
        from django.core.cache import cache

        from workeasy360.platform.administration.models import PlatformSettings
        from workeasy360.platform.administration.site import CACHE_KEY

        PlatformSettings.objects.update_or_create(
            id=1, defaults={"partner_applications_enabled": False}
        )
        cache.delete(CACHE_KEY)
        response, _ = _apply()
        assert response.status_code == 400
        cache.delete(CACHE_KEY)

    @pytest.mark.parametrize(
        "bad",
        [
            {"business_name": ""},
            {"email": "nope"},
            {"password": "short"},
            {"country": "India"},
            {"interest": "platinum"},
        ],
    )
    def test_bad_input_is_refused_and_creates_nothing(self, bad):
        from workeasy360.commercial.partners.models import PartnerAccount

        before = PartnerAccount.objects.count()
        response, _ = _apply(**bad)
        assert response.status_code == 400
        assert PartnerAccount.objects.count() == before


class TestWholeJourney:
    def test_registration_to_activation(self, login, staff_login):
        """Workflow 1: registration, KYC submission, review, approval, activation."""
        from workeasy360.commercial.partners import selectors
        from workeasy360.commercial.partners.models import (
            PartnerAccount,
            PartnerKycDocumentAccess,
        )

        requirement = _requirement()
        _, body = _apply()
        user, partner = _applicant(body["email"])
        client = _signed_in(login, user)
        base = f"/api/v1/partner/{partner.id}/kyc"

        state = client.get(base).json()
        assert state["status"] == "draft" and state["may_edit"] is True
        assert [r["key"] for r in state["requirements"]] == [requirement.key]

        # Not without what is required.
        refused = _json(client, "post", f"{base}/submit")
        assert refused.status_code == 400

        saved = _json(
            client,
            "put",
            base,
            {
                "details": {
                    "legal_name": "Acme Traders Pvt Ltd",
                    "country": "IN",
                    "city": "Pune",
                }
            },
        )
        assert saved.status_code == 200
        assert saved.json()["details"]["city"] == "Pune"

        upload = client.post(
            f"{base}/documents?requirement={requirement.key}&filename=id.png",
            PNG,
            content_type="image/png",
        )
        assert upload.status_code == 201, upload.content
        document_id = upload.json()["id"]

        submitted = _json(client, "post", f"{base}/submit")
        assert submitted.status_code == 200 and submitted.json()["status"] == "submitted"
        assert submitted.json()["editable"] is False

        # Still no rights while it waits.
        assert PartnerAccount.objects.get(id=partner.id).status == "draft"

        reviewer, _ = staff_login(REVIEW)
        queue = reviewer.get("/api/v1/admin/kyc/applications").json()
        assert [row["partner_id"] for row in queue["results"]] == [str(partner.id)]

        admin_base = f"/api/v1/admin/kyc/applications/{partner.id}"
        started = _json(reviewer, "post", f"{admin_base}/review")
        assert started.status_code == 200 and started.json()["status"] == "under_review"

        opened = reviewer.get(f"{admin_base}/documents/{document_id}/content")
        assert opened.status_code == 200
        assert opened["Cache-Control"].startswith("no-store")
        assert opened["X-Content-Type-Options"] == "nosniff"
        assert opened.content == PNG
        assert (
            PartnerKycDocumentAccess.objects.filter(document_id=document_id).count() == 1
        )

        approved = _json(
            reviewer,
            "post",
            f"{admin_base}/decision",
            {"decision": "approve", "expected_version": started.json()["version"]},
        )
        assert approved.status_code == 200, approved.content
        result = approved.json()
        assert result["status"] == "approved"
        assert result["decided_by_name"]
        assert result["decided_at"]
        assert result["document_access"], "the read is listed"

        partner.refresh_from_db()
        assert partner.status == "active"
        assert selectors.active_capabilities(partner.id), (
            "approval granted what was asked for"
        )

    def test_correction_then_resubmission(self, login, staff_login):
        requirement = _requirement()
        _, body = _apply()
        user, partner = _applicant(body["email"])
        client = _signed_in(login, user)
        base = f"/api/v1/partner/{partner.id}/kyc"
        _json(client, "put", base, {"details": {"legal_name": "Acme", "country": "IN"}})
        client.post(
            f"{base}/documents?requirement={requirement.key}&filename=a.png",
            PNG,
            content_type="image/png",
        )
        _json(client, "post", f"{base}/submit")

        reviewer, _ = staff_login(REVIEW)
        decision = f"/api/v1/admin/kyc/applications/{partner.id}/decision"
        too_short = _json(
            reviewer, "post", decision, {"decision": "request_correction", "reason": "no"}
        )
        assert too_short.status_code == 400
        asked = _json(
            reviewer,
            "post",
            decision,
            {
                "decision": "request_correction",
                "reason": "The ID proof photo is too blurred to read.",
                "flagged_requirements": [requirement.key],
            },
        )
        assert (
            asked.status_code == 200 and asked.json()["status"] == "correction_required"
        )
        state = client.get(base).json()
        assert state["editable"] is True
        assert state["correction"]["flagged_requirements"] == [requirement.key]
        assert "blurred" in state["correction"]["reason"]
        again = _json(client, "post", f"{base}/submit")
        assert again.json()["status"] == "submitted"
        partner.refresh_from_db()
        assert partner.status == "draft"

    def test_rejection_is_final_and_leaves_no_rights(self, login, staff_login):
        from workeasy360.commercial.partners import selectors

        _, body = _apply()
        user, partner = _applicant(body["email"])
        client = _signed_in(login, user)
        base = f"/api/v1/partner/{partner.id}/kyc"
        _json(client, "put", base, {"details": {"legal_name": "Acme", "country": "IN"}})
        _json(client, "post", f"{base}/submit")
        reviewer, _ = staff_login(REVIEW)
        decision = f"/api/v1/admin/kyc/applications/{partner.id}/decision"
        rejected = _json(
            reviewer,
            "post",
            decision,
            {"decision": "reject", "reason": "Could not verify this business."},
        )
        assert rejected.json()["status"] == "rejected"
        assert (
            rejected.json()["decisions"][0]["reason"] == "Could not verify this business."
        )
        partner.refresh_from_db()
        assert partner.status == "draft"
        assert selectors.active_capabilities(partner.id) == frozenset()
        # And it cannot be decided again.
        assert (
            _json(reviewer, "post", decision, {"decision": "approve"}).status_code == 409
        )


class TestTheGate:
    def test_a_draft_partner_cannot_be_activated_without_approval(self, staff_login):
        manager, _ = staff_login("platform.staff_partner.manage")
        from workeasy360.commercial.partners.kyc import assert_may_activate
        from workeasy360.commercial.partners.models import PartnerAccount, PartnerKyc
        from workeasy360.contracts.errors import PartnerValidationError

        _, body = _apply()
        row = PartnerAccount.objects.get(contact_email=body["email"])
        with pytest.raises(PartnerValidationError):
            assert_may_activate(row.id)
        response = _json(
            manager,
            "post",
            f"/api/v1/admin/partners/{row.id}/status",
            {"status": "active", "reason": "Trying to skip the KYC review."},
        )
        assert response.status_code == 400, response.content
        row.refresh_from_db()
        assert row.status == "draft"
        PartnerKyc.objects.filter(partner=row).update(status="approved")
        assert_may_activate(row.id)


class TestPermissions:
    def test_documents_need_review_not_just_view(self, login, staff_login):
        requirement = _requirement()
        _, body = _apply()
        user, partner = _applicant(body["email"])
        client = _signed_in(login, user)
        base = f"/api/v1/partner/{partner.id}/kyc"
        document = client.post(
            f"{base}/documents?requirement={requirement.key}&filename=a.png",
            PNG,
            content_type="image/png",
        ).json()["id"]
        viewer, _ = staff_login("platform.staff_kyc.view")
        admin_base = f"/api/v1/admin/kyc/applications/{partner.id}"
        assert viewer.get(admin_base).status_code == 200
        assert viewer.get(f"{admin_base}/documents/{document}/content").status_code == 403
        assert (
            _json(
                viewer, "post", f"{admin_base}/decision", {"decision": "approve"}
            ).status_code
            == 403
        )
        assert (
            _json(
                viewer, "post", "/api/v1/admin/kyc/requirements", {"label": "X"}
            ).status_code
            == 403
        )

    def test_another_partner_cannot_read_this_application(self, login):
        _, first = _apply()
        _, second = _apply()
        user, _partner = _applicant(first["email"])
        _other_user, other = _applicant(second["email"])
        client = _signed_in(login, user)
        assert client.get(f"/api/v1/partner/{other.id}/kyc").status_code == 404

    def test_a_non_administrator_member_cannot_change_it(self, login):
        from workeasy360.commercial.partners.models import PartnerMember

        _, body = _apply()
        user, partner = _applicant(body["email"])
        PartnerMember.objects.filter(user=user).update(role="viewer")
        client = _signed_in(login, user)
        assert client.get(f"/api/v1/partner/{partner.id}/kyc").status_code == 200
        response = _json(
            client,
            "put",
            f"/api/v1/partner/{partner.id}/kyc",
            {"details": {"legal_name": "X", "country": "IN"}},
        )
        assert response.status_code == 403

    def test_the_wrong_type_of_file_is_refused(self, login):
        requirement = _requirement()
        _, body = _apply()
        user, partner = _applicant(body["email"])
        client = _signed_in(login, user)
        base = f"/api/v1/partner/{partner.id}/kyc/documents"
        html = client.post(
            f"{base}?requirement={requirement.key}", b"<script>", content_type="text/html"
        )
        assert html.status_code == 400
        # A declared PNG that is not one: the bytes decide.
        fake = client.post(
            f"{base}?requirement={requirement.key}",
            b"not an image",
            content_type="image/png",
        )
        assert fake.status_code == 415


class TestLedger:
    def _wallet(self, kind="platform", currency="INR"):
        from workeasy360.commercial.wallets import ledger

        return ledger.ensure(
            owner_type="organization", owner_id=uuid.uuid4(), kind=kind, currency=currency
        )

    def test_a_retry_moves_money_once(self):
        from workeasy360.commercial.wallets import ledger

        wallet = self._wallet()
        first = ledger.post(
            wallet.id, amount=500, entry_type="top_up", reason="t", idempotency_key="k1"
        )
        second = ledger.post(
            wallet.id, amount=500, entry_type="top_up", reason="t", idempotency_key="k1"
        )
        assert first.id == second.id
        wallet.refresh_from_db()
        assert wallet.balance == 500
        assert ledger.reconcile() == []

    def test_the_same_key_cannot_mean_a_different_movement(self):
        from workeasy360.commercial.wallets import ledger

        wallet = self._wallet()
        ledger.post(
            wallet.id, amount=500, entry_type="top_up", reason="t", idempotency_key="k2"
        )
        with pytest.raises(ledger.WalletConflict):
            ledger.post(
                wallet.id,
                amount=900,
                entry_type="top_up",
                reason="t",
                idempotency_key="k2",
            )

    def test_no_overdraft(self):
        from workeasy360.commercial.wallets import ledger

        wallet = self._wallet()
        ledger.post(
            wallet.id, amount=100, entry_type="top_up", reason="t", idempotency_key="k3"
        )
        with pytest.raises(ledger.InsufficientBalance):
            ledger.post(
                wallet.id,
                amount=-101,
                entry_type="usage",
                reason="t",
                idempotency_key="k4",
            )
        wallet.refresh_from_db()
        assert wallet.balance == 100

    def test_reversal_names_the_original_and_cannot_exceed_it(self):
        from workeasy360.commercial.wallets import ledger

        wallet = self._wallet()
        entry = ledger.post(
            wallet.id, amount=100, entry_type="top_up", reason="t", idempotency_key="k5"
        )
        part = ledger.reverse(entry.id, reason="part", amount=40, idempotency_key="k6")
        assert part.reverses_id == entry.id and part.amount == -40
        with pytest.raises(ledger.WalletConflict):
            ledger.reverse(entry.id, reason="too much", amount=61, idempotency_key="k7")
        with pytest.raises(ledger.WalletConflict):
            ledger.reverse(part.id, reason="reversal of a reversal", idempotency_key="k8")
        rest = ledger.reverse(entry.id, reason="rest", idempotency_key="k9")
        assert rest.amount == -60
        wallet.refresh_from_db()
        assert wallet.balance == 0

    def test_an_entry_can_be_neither_changed_nor_deleted(self):
        from django.db import DatabaseError, transaction

        from workeasy360.commercial.wallets import ledger
        from workeasy360.commercial.wallets.models import WalletEntry

        wallet = self._wallet()
        entry = ledger.post(
            wallet.id, amount=100, entry_type="top_up", reason="t", idempotency_key="k10"
        )
        with pytest.raises(DatabaseError), transaction.atomic():
            WalletEntry.objects.filter(id=entry.id).update(amount=999)
        with pytest.raises(DatabaseError), transaction.atomic():
            WalletEntry.objects.filter(id=entry.id).delete()

    def test_the_database_refuses_a_negative_balance_or_a_partner_credit_wallet(self):
        from django.db import IntegrityError, transaction

        from workeasy360.commercial.wallets.models import Wallet

        wallet = self._wallet()
        with pytest.raises(IntegrityError), transaction.atomic():
            Wallet.objects.filter(id=wallet.id).update(balance=-1)
        with pytest.raises(IntegrityError), transaction.atomic():
            Wallet.objects.create(
                owner_type="partner", owner_id=uuid.uuid4(), kind="ai_credits"
            )


class TestUsage:
    def _rate(self, meter="sms", currency="INR", unit=50, per=1, included=0):
        from workeasy360.commercial.wallets.models import UsageRate

        return UsageRate.objects.create(
            meter=meter,
            currency=currency,
            unit_amount=unit,
            per_units=per,
            included_units=included,
            created_by_user_id=uuid.uuid4(),
        )

    def _funded(self, org, *, kind="consumption", currency="INR", amount=1000):
        from workeasy360.commercial.wallets import ledger

        wallet = ledger.ensure(
            owner_type="organization", owner_id=org, kind=kind, currency=currency
        )
        ledger.post(
            wallet.id,
            amount=amount,
            entry_type="top_up",
            reason="t",
            idempotency_key=f"fund:{org}:{kind}",
        )
        return wallet

    def test_no_rate_means_no_charge(self):
        from workeasy360.commercial.wallets import usage

        org = uuid.uuid4()
        wallet = self._funded(org)
        event = usage.record(
            organization_id=org,
            product_code="p",
            meter="sms",
            quantity=5,
            idempotency_key="e1",
            currency="INR",
        )
        assert event.outcome == "not_rated" and event.amount == 0
        wallet.refresh_from_db()
        assert wallet.balance == 1000

    def test_a_charge_comes_off_the_right_wallet_with_its_history(self):
        from workeasy360.commercial.wallets import usage
        from workeasy360.commercial.wallets.models import WalletEntry

        org = uuid.uuid4()
        wallet = self._funded(org)
        self._rate()
        event = usage.record(
            organization_id=org,
            product_code="p",
            meter="sms",
            quantity=3,
            idempotency_key="e2",
            currency="INR",
        )
        assert event.outcome == "charged" and event.amount == 150
        wallet.refresh_from_db()
        assert wallet.balance == 850
        entry = WalletEntry.objects.get(id=event.entry_id)
        assert entry.amount == -150 and entry.balance_after == 850

    def test_a_repeated_event_charges_once_and_is_counted_for_review(self):
        from workeasy360.commercial.wallets import usage

        org = uuid.uuid4()
        wallet = self._funded(org)
        self._rate()
        for _ in range(3):
            event = usage.record(
                organization_id=org,
                product_code="p",
                meter="sms",
                quantity=1,
                idempotency_key="e3",
                currency="INR",
            )
        wallet.refresh_from_db()
        assert wallet.balance == 950
        assert event.duplicate_count == 2

    def test_the_free_allowance_comes_off_first(self):
        from workeasy360.commercial.wallets import usage

        org = uuid.uuid4()
        wallet = self._funded(org)
        self._rate(included=2)
        first = usage.record(
            organization_id=org,
            product_code="p",
            meter="sms",
            quantity=2,
            idempotency_key="e4",
            currency="INR",
        )
        second = usage.record(
            organization_id=org,
            product_code="p",
            meter="sms",
            quantity=3,
            idempotency_key="e5",
            currency="INR",
        )
        assert first.outcome == "included" and first.amount == 0
        assert (
            second.outcome == "charged"
            and second.charged_units == 3
            and second.amount == 150
        )
        wallet.refresh_from_db()
        assert wallet.balance == 850

    def test_too_little_fails_is_kept_and_can_be_retried_once_funded(self):
        from workeasy360.commercial.wallets import ledger, usage

        org = uuid.uuid4()
        wallet = self._funded(org, amount=10)
        self._rate()
        event = usage.record(
            organization_id=org,
            product_code="p",
            meter="sms",
            quantity=1,
            idempotency_key="e6",
            currency="INR",
        )
        assert event.outcome == "failed" and event.failure_code == "insufficient_balance"
        wallet.refresh_from_db()
        assert wallet.balance == 10
        ledger.post(
            wallet.id, amount=100, entry_type="top_up", reason="t", idempotency_key="more"
        )
        done = usage.retry(event.id)
        assert done.outcome == "charged"
        wallet.refresh_from_db()
        assert wallet.balance == 60
        from workeasy360.contracts.errors import CatalogValidationError

        with pytest.raises(CatalogValidationError):
            usage.retry(event.id)
        wallet.refresh_from_db()
        assert wallet.balance == 60

    def test_ai_tokens_spend_credits_rounded_up(self):
        """Workflow 6: AI credit purchase, consumption, balance update."""
        from workeasy360.commercial.wallets import usage

        org = uuid.uuid4()
        wallet = self._funded(org, kind="ai_credits", currency="", amount=100)
        self._rate(meter="ai_tokens", currency="", unit=1, per=1000)
        event = usage.record(
            organization_id=org,
            product_code="p",
            meter="ai_tokens",
            quantity=1500,
            idempotency_key="ai-1",
            currency="INR",
        )
        assert event.amount == 2
        wallet.refresh_from_db()
        assert wallet.balance == 98
        assert usage.credits_block_ai(org) is False

    def test_ai_is_not_refused_until_a_rate_is_set(self):
        from workeasy360.commercial.wallets import usage

        org = uuid.uuid4()
        assert usage.credits_block_ai(org) is False
        self._rate(meter="ai_tokens", currency="", unit=1, per=1000)
        assert usage.credits_block_ai(org) is True


def _organization(organization_factory, user_factory, name="Acme"):
    owner = user_factory(password=PASSWORD)
    org, _membership, _ = organization_factory(
        slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}", owner=owner
    )
    return org


class TestDashboard:
    def _invoice(
        self, org, *, status="paid", total=1000, currency="INR", order=None, issued=None
    ):
        from workeasy360.commercial.payments.models import InvoiceSummary

        return InvoiceSummary.objects.create(
            invoice_id=uuid.uuid4(),
            organization_id=org.id,
            order_id=order,
            subscription_id=None if order else uuid.uuid4(),
            number=f"INV-{uuid.uuid4().hex[:8]}",
            status=status,
            currency=currency,
            total_minor=total,
            collection_mode="automatic",
            issued_at=issued or timezone.now(),
            due_at=timezone.now(),
        )

    def _payment(
        self,
        org,
        *,
        amount=1000,
        status="succeeded",
        currency="INR",
        when=None,
        invoice=None,
    ):
        from workeasy360.commercial.payments.models import PaymentSummary

        return PaymentSummary.objects.create(
            attempt_id=uuid.uuid4(),
            organization_id=org.id,
            invoice_id=invoice or uuid.uuid4(),
            provider_key="fake",
            status=status,
            amount_minor=amount,
            currency=currency,
            created_at=when or timezone.now(),
            completed_at=when or timezone.now(),
        )

    def test_definitions_of_sales_and_collections(
        self,
        staff_login,
        organization_factory,
        user_factory,
    ):
        from workeasy360.commercial.payments.models import RefundSummary

        org = _organization(organization_factory, user_factory)
        order = uuid.uuid4()
        self._invoice(org, total=1000)  # a renewal, whatever its status: billed
        self._invoice(org, total=500, status="open")  # billed, not yet collected
        self._invoice(org, total=700, status="void")  # never counted
        self._invoice(
            org, total=900, status="open", order=order
        )  # an unpaid checkout sold nothing
        self._invoice(org, total=300, status="paid", order=uuid.uuid4())  # a paid order
        self._invoice(org, total=9999, currency="USD")  # another currency: never added
        self._payment(org, amount=1000)
        self._payment(org, amount=250, status="failed")
        RefundSummary.objects.create(
            refund_id=uuid.uuid4(),
            organization_id=org.id,
            attempt_id=uuid.uuid4(),
            amount_minor=100,
            currency="INR",
            reason="r",
            status="succeeded",
            requested_by_user_id=uuid.uuid4(),
            created_at=timezone.now(),
            completed_at=timezone.now(),
        )
        client, _ = staff_login(
            "platform.staff_customer.view", "platform.staff_payment.view"
        )
        data = client.get("/api/v1/admin/dashboard/overview", {"currency": "INR"}).json()
        assert data["cards"]["sales"]["amount_minor"] == 1800
        assert data["cards"]["collections"]["amount_minor"] == 900
        assert data["currencies"] == ["INR", "USD"]
        usd = client.get("/api/v1/admin/dashboard/overview", {"currency": "USD"}).json()
        assert usd["cards"]["sales"]["amount_minor"] == 9999

    def test_change_compares_the_equally_long_period_before(
        self,
        staff_login,
        organization_factory,
        user_factory,
    ):
        org = _organization(organization_factory, user_factory)
        now = timezone.now()
        self._invoice(org, total=2000, issued=now - timedelta(days=2))
        self._invoice(org, total=1000, issued=now - timedelta(days=12))
        self._invoice(org, total=7777, issued=now - timedelta(days=40))
        client, _ = staff_login(
            "platform.staff_customer.view", "platform.staff_payment.view"
        )
        data = client.get(
            "/api/v1/admin/dashboard/overview", {"days": "10", "currency": "INR"}
        ).json()
        assert data["period"]["days"] == 10
        assert data["cards"]["sales"]["amount_minor"] == 2000
        assert data["cards"]["sales"]["previous_minor"] == 1000
        assert data["cards"]["sales"]["change_percent"] == 100.0

    def test_no_invented_percentage_when_there_was_nothing_before(self, staff_login):
        client, _ = staff_login(
            "platform.staff_customer.view", "platform.staff_payment.view"
        )
        data = client.get("/api/v1/admin/dashboard/overview").json()
        assert data["cards"]["sales"]["change_percent"] is None

    @pytest.mark.parametrize(
        "params",
        [
            {"from": "yesterday"},
            {"from": "2026-05-02", "to": "2026-05-01"},
            {"days": "0"},
            {"days": "9999"},
            {"to": "2099-01-01"},
            {"currency": "RUPEES"},
        ],
    )
    def test_a_bad_period_is_refused(self, staff_login, params):
        client, _ = staff_login(
            "platform.staff_customer.view", "platform.staff_payment.view"
        )
        assert client.get("/api/v1/admin/dashboard/overview", params).status_code == 400

    def test_sections_follow_the_grants(self, staff_login):
        client, _ = staff_login("platform.staff_customer.view")
        data = client.get("/api/v1/admin/dashboard/overview").json()
        assert set(data["cards"]) == {"customers", "active_subscriptions"}
        assert "wallets" not in data and "top_products" not in data
        assert data["pending"] == []
        assert client.get("/api/v1/admin/dashboard/money").status_code == 403

    def test_pending_actions_have_real_counts_and_a_failure_retried_is_not_one(
        self,
        staff_login,
        organization_factory,
        user_factory,
    ):
        from workeasy360.commercial.partners.models import PartnerKyc
        from workeasy360.commercial.wallets import ledger

        org = _organization(organization_factory, user_factory)
        still_open = self._invoice(org, status="open")
        since_paid = self._invoice(org, status="paid")
        self._payment(org, status="failed", invoice=still_open.invoice_id)
        self._payment(org, status="failed", invoice=since_paid.invoice_id)

        _, body = _apply()
        _, partner = _applicant(body["email"])
        PartnerKyc.objects.filter(partner=partner).update(status="submitted")

        wallet = ledger.ensure(
            owner_type="organization", owner_id=org.id, kind="platform", currency="INR"
        )
        wallet.low_balance_threshold = 100
        wallet.save()

        client, _ = staff_login(
            "platform.staff_customer.view",
            "platform.staff_payment.view",
            "platform.staff_kyc.view",
            "platform.staff_wallet.view",
        )
        pending = {
            p["key"]: p
            for p in client.get("/api/v1/admin/dashboard/overview").json()["pending"]
        }
        assert pending["kyc_reviews"]["count"] == 1
        assert pending["failed_payments"]["count"] == 1
        assert pending["low_balance"]["count"] == 1
        assert all(p["href"].startswith("/admin/") for p in pending.values())

    def test_wallet_summary_has_exactly_three_balances_and_no_commission(
        self, staff_login
    ):
        from workeasy360.commercial.wallets import ledger

        org_a, org_b = uuid.uuid4(), uuid.uuid4()
        for org, amount in ((org_a, 300), (org_b, 200)):
            wallet = ledger.ensure(
                owner_type="organization", owner_id=org, kind="platform", currency="INR"
            )
            ledger.post(
                wallet.id,
                amount=amount,
                entry_type="top_up",
                reason="t",
                idempotency_key=f"w:{org}",
            )
        client, _ = staff_login(
            "platform.staff_customer.view", "platform.staff_wallet.view"
        )
        wallets = client.get(
            "/api/v1/admin/dashboard/overview", {"currency": "INR"}
        ).json()["wallets"]
        assert set(wallets) == {"platform", "ai_credits", "consumption"}
        assert wallets["platform"]["balance"] == 500

    def test_recent_activity_and_trend_read_real_rows(
        self,
        staff_login,
        organization_factory,
        user_factory,
    ):
        org = _organization(organization_factory, user_factory, name="Globex")
        client, _ = staff_login(
            "platform.staff_customer.view", "platform.staff_partner.view"
        )
        data = client.get("/api/v1/admin/dashboard/overview").json()
        assert any(org.display_name in event["title"] for event in data["activity"])
        trend = client.get("/api/v1/admin/dashboard/growth", {"months": "6"}).json()
        assert len(trend["months"]) == 6 and trend["customers"][-1] >= 1


class TestProjections:
    def test_triggers_keep_summaries_in_step_with_tenant_rows(
        self, organization_factory, user_factory
    ):
        """A status change by ``.update()`` is exactly what a signal would miss."""
        from workeasy360.commercial.payments.models import Invoice, InvoiceSummary, Order
        from workeasy360.platform.tenancy.db import organization_db_scope
        from workeasy360.platform.tenancy.unscoped import unscoped_access

        org = _organization(organization_factory, user_factory)
        now = timezone.now()
        with (
            unscoped_access(reason="Test creates one tenant invoice", module="tests"),
            organization_db_scope(org.id),
        ):
            order = Order.unscoped.create(
                organization_id=org.id,
                number=f"ORD-{uuid.uuid4().hex[:8]}",
                kind="cart",
                currency="INR",
                amount_due_minor=500,
                description="Test order",
                idempotency_key=uuid.uuid4().hex,
                expires_at=now + timedelta(days=1),
            )
            invoice = Invoice.unscoped.create(
                organization_id=org.id,
                order=order,
                number=f"INV-{uuid.uuid4().hex[:8]}",
                currency="INR",
                total_minor=500,
                billing_interval="monthly",
                collection_mode="automatic",
                period_start=now,
                period_end=now + timedelta(days=30),
                due_at=now,
            )
            assert InvoiceSummary.objects.get(invoice_id=invoice.id).status == "open"
            Invoice.unscoped.filter(id=invoice.id).update(status="void", voided_at=now)
        assert InvoiceSummary.objects.get(invoice_id=invoice.id).status == "void"


class TestSearch:
    def test_it_returns_only_what_the_grants_cover(
        self,
        staff_login,
        organization_factory,
        user_factory,
    ):
        from workeasy360.commercial.payments.models import OrderSummary

        org = _organization(organization_factory, user_factory, name="Initech")
        OrderSummary.objects.create(
            order_id=uuid.uuid4(),
            organization_id=org.id,
            number="ORD-INITECH",
            kind="plan",
            status="paid",
            description="Initech order",
            currency="INR",
            created_at=timezone.now(),
        )
        limited, _ = staff_login("platform.staff_customer.view")
        kinds = {
            r["kind"]
            for r in limited.get("/api/v1/admin/search", {"q": "initech"}).json()[
                "results"
            ]
        }
        assert "customer" in kinds and "order" not in kinds
        wider, _ = staff_login(
            "platform.staff_customer.view", "platform.staff_payment.view"
        )
        kinds = {
            r["kind"]
            for r in wider.get("/api/v1/admin/search", {"q": "initech"}).json()["results"]
        }
        assert {"customer", "order"} <= kinds

    def test_too_short_a_query_searches_nothing_and_it_is_staff_only(
        self, staff_login, user_factory, login
    ):
        client, _ = staff_login("platform.staff_customer.view")
        assert client.get("/api/v1/admin/search", {"q": "a"}).json()["results"] == []
        customer = login(user_factory(password=PASSWORD))
        assert customer.get("/api/v1/admin/search", {"q": "acme"}).status_code == 403
        assert Client().get("/api/v1/admin/search", {"q": "acme"}).status_code in (
            401,
            403,
        )


class TestAdminCreatedPartner:
    def test_an_invited_administrator_gets_the_link_now_because_activation_waits_on_them(
        self, staff_login
    ):
        from workeasy360.commercial.partners.models import PartnerAccount, PartnerMember

        manager, _ = staff_login(
            "platform.staff_partner.manage", "platform.staff_partner.view"
        )
        response = _json(
            manager,
            "post",
            "/api/v1/admin/partners/create",
            {
                "experience": "affiliate",
                "partner": {"display_name": "Staff Added Partner", "country": "IN"},
                "administrator": {
                    "email": "invited-admin@example.com",
                    "first_name": "Ina",
                    "last_name": "Admin",
                    "mode": "invite",
                },
            },
        )
        assert response.status_code in (200, 201), response.content
        partner = PartnerAccount.objects.get(display_name="Staff Added Partner")
        member = PartnerMember.objects.get(partner=partner)
        assert member.invitation_deferred is False
        assert member.invitation_sent_at is not None
        assert partner.status == "draft"

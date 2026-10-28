"""Recurring commission on renewals (ADR-052; Store Working Flow PDF page 6:
"Renewal Payment -> Commission Rule: check if recurring commission applies ->
New Accrual"). Only a rule marked recurring earns on renewals; each paid
renewal earns exactly once, from the verified payment, and a renewal refunded
inside the holdback is reversed. A rule that is not recurring behaves exactly
as before: one commission per sale."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.extension.conftest import place_and_pay, url
from workeasy360.commercial.commissions import referrals

pytestmark = pytest.mark.django_db


@pytest.fixture
def referred(shop, partner_factory, staff_factory):
    """The shop's organization, referred by an affiliate who may earn."""
    affiliate = partner_factory(
        "referral.create", "commission.earn", "commission.view", "payout.view"
    )
    token = referrals.capture_referral(
        referrals.create_referral_code(affiliate["actor"]()).code
    )
    assert referrals.convert_referral(
        user_id=shop["owner"].id, token=token, organization_id=shop["id"]
    )
    _u, commission_ctx = staff_factory("platform.staff_commission.manage")
    return {**shop, "affiliate": affiliate, "commission_ctx": commission_ctx}


def _rule(fx, *, recurring):
    from workeasy360.commercial.commissions import rules

    rules.create_rule_version(
        fx["commission_ctx"],
        code="default",
        rate_bp=1000,
        reason="Programme default rate of ten percent",
        recurring=recurring,
    )


def _pay_invoice(fx, invoice_id, *, outcome="succeeded"):
    """Pay an invoice through the test provider's signed callback - the same
    verified-webhook path a real payment takes."""
    from workeasy360.commercial.payments.checkout import create_checkout
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt

    started = fx["client"].post(
        url(fx["slug"], f"billing/invoices/{invoice_id}/pay"),
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert started.status_code == 202, started.content
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    assert create_checkout(fx["id"], attempt_id) == "awaiting_payment"
    with organization_scope(fx["id"], reason="Test reads the checkout reference"):
        reference = PaymentAttempt.unscoped.get(id=attempt_id).provider_reference
    done = fx["client"].post(
        "/api/v1/public/payments/test-checkout/complete",
        {"reference": reference, "organization_id": str(fx["id"]), "outcome": outcome},
        content_type="application/json",
    )
    assert done.status_code == 200, done.content


def _renew(fx):
    """Buy CRM Professional, roll into the next period and pay its invoice.
    Returns the renewal invoice's id."""
    from workeasy360.commercial.payments.invoicing import ensure_invoices
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    order = place_and_pay(
        fx["client"],
        fx["slug"],
        fx["id"],
        plan_version_id=str(fx["catalog"]["versions"]["crm_professional"].id),
    )
    assert order["status"] == "fulfilled"
    subscription = fx["client"].get(url(fx["slug"], "subscriptions")).json()["results"][0]
    end = timezone.datetime.fromisoformat(subscription["current_period_end"])
    process_due_subscriptions(at=end + timedelta(minutes=1))
    ensure_invoices(fx["id"])
    invoices = fx["client"].get(url(fx["slug"], "billing/invoices")).json()["results"]
    renewal = next(i for i in invoices if i["status"] == "open" and not i["order_id"])
    _pay_invoice(fx, renewal["invoice_id"])
    return renewal["invoice_id"]


def _ledger(fx):
    from workeasy360.commercial.commissions import accrual
    from workeasy360.commercial.commissions.models import CommissionAccrual

    accrual.accrue_for_organization(fx["id"], now=timezone.now() + timedelta(days=60))
    return list(
        CommissionAccrual.objects.filter(organization_id=fx["id"]).order_by("created_at")
    )


def test_a_rule_that_is_not_recurring_earns_once_per_sale(referred):
    _rule(referred, recurring=False)
    _renew(referred)
    rows = _ledger(referred)
    assert [(r.kind, r.source_ref.split(":")[0]) for r in rows] == [
        ("accrual", "subscriptions.item")
    ]


def test_a_recurring_rule_earns_on_each_paid_renewal_exactly_once(referred):
    from workeasy360.commercial.commissions import accrual

    _rule(referred, recurring=True)
    renewal_invoice = _renew(referred)
    rows = _ledger(referred)
    kinds = sorted(r.source_ref.split(":")[0] for r in rows if r.kind == "accrual")
    assert kinds == ["payments.invoice_line", "subscriptions.item"]
    renewal = next(r for r in rows if r.source_ref.startswith("payments.invoice_line:"))
    assert renewal.partner_id == referred["affiliate"]["partner"].id
    assert renewal.attribution_kind == "referral"
    assert (renewal.basis_minor, renewal.amount_minor) == (4999, 500)
    assert renewal.event_at is not None and renewal.clears_at > renewal.event_at
    # Running it again changes nothing: one accrual per paid renewal line.
    assert len(_ledger(referred)) == len(rows)
    report = accrual.reconcile(organization_ids=[referred["id"]])
    assert report.missing == [] and report.unexpected == [] and report.wrong == []
    assert renewal_invoice  # the renewal was a real, separately paid invoice


def test_an_unpaid_renewal_earns_nothing(referred):
    from workeasy360.commercial.payments.invoicing import ensure_invoices
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    _rule(referred, recurring=True)
    place_and_pay(
        referred["client"],
        referred["slug"],
        referred["id"],
        plan_version_id=str(referred["catalog"]["versions"]["crm_professional"].id),
    )
    subscription = (
        referred["client"]
        .get(url(referred["slug"], "subscriptions"))
        .json()["results"][0]
    )
    end = timezone.datetime.fromisoformat(subscription["current_period_end"])
    process_due_subscriptions(at=end + timedelta(minutes=1))
    ensure_invoices(referred["id"])
    rows = _ledger(referred)
    assert not any(r.source_ref.startswith("payments.invoice_line:") for r in rows)


def test_a_renewal_refunded_inside_the_holdback_is_reversed(referred):
    from workeasy360.commercial.commissions import accrual
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import Invoice

    _rule(referred, recurring=True)
    renewal_invoice = _renew(referred)
    accrual.accrue_for_organization(referred["id"])
    with organization_scope(referred["id"], reason="Test records a refund"):
        invoice = Invoice.unscoped.get(id=renewal_invoice)
        Invoice.unscoped.filter(id=invoice.id).update(
            amount_refunded_minor=invoice.amount_paid_minor
        )
    stats = accrual.accrue_for_organization(referred["id"])
    assert stats.reversed == 1
    from workeasy360.commercial.commissions.models import CommissionAccrual

    renewal = CommissionAccrual.objects.get(
        organization_id=referred["id"],
        kind="accrual",
        source_ref__startswith="payments.invoice_line:",
    )
    reversal = CommissionAccrual.objects.get(reverses_id=renewal.id)
    assert reversal.amount_minor == -renewal.amount_minor
    assert reversal.reason_code == "refund"


def test_a_new_rate_for_a_partner_keeps_the_rules_recurring_choice(
    partner_factory, staff_factory
):
    from workeasy360.commercial.commissions import rules
    from workeasy360.commercial.commissions.models import CommissionRule

    fx = partner_factory("referral.create", "commission.earn")
    _u, ctx = staff_factory("platform.staff_commission.manage")
    first = rules.create_rule_version(
        ctx,
        code="pc-test",
        rate_bp=1000,
        reason="Partner rate for the recurring test",
        partner_id=fx["partner"].id,
        recurring=True,
    )
    rules.assign_partner_commission(
        ctx,
        fx["partner"].id,
        rate_bp=1500,
        reason="Raised rate for the test",
        expected_version=first.version,
    )
    latest = CommissionRule.objects.filter(code=first.code).order_by("-version").first()
    assert latest.rate_bp == 1500 and latest.recurring is True


def test_recurring_must_be_a_real_boolean(staff_factory):
    from workeasy360.commercial.commissions import rules
    from workeasy360.contracts.errors import PartnerValidationError

    _u, ctx = staff_factory("platform.staff_commission.manage")
    with pytest.raises(PartnerValidationError):
        rules.create_rule_version(
            ctx, code="x", rate_bp=100, reason="A reason long enough", recurring="yes"
        )

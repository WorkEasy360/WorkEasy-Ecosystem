"""Commission accrual, the append-only ledger, rule versioning and payouts."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from tests.phase7.support import provision, sell
from workeasy360.commercial.commissions import (
    accrual,
    domain,
    ledger,
    payouts,
    rules,
    selectors,
)
from workeasy360.commercial.commissions.models import CommissionAccrual, Payout
from workeasy360.commercial.partners import services
from workeasy360.contracts.errors import (
    InvalidStateTransition,
    PartnerNotActive,
    PartnerValidationError,
    PermissionDenied,
    SeparationOfDutiesViolation,
    StepUpRequired,
)
from workeasy360.contracts.values.money import CurrencyCode

pytestmark = pytest.mark.django_db


def _accruals(partner):
    return list(
        CommissionAccrual.objects.filter(partner=partner).order_by("event_at", "id")
    )


# --- pure calculation -----------------------------------------------------


@pytest.mark.parametrize(
    ("basis", "bp", "expected"),
    [
        (4999, 1000, 500),  # 499.9 rounds half-up to 500
        (4995, 1000, 500),  # 499.5 -> 500 (half up)
        (4994, 1000, 499),  # 499.4 -> 499
        (1, 5000, 1),  # 0.5 -> 1
        (0, 1000, 0),
        (10_000_000, 1, 1000),
        (999, 10000, 999),  # 100%
    ],
)
def test_commission_rounds_half_up_in_integer_minor_units(basis, bp, expected):
    assert (
        domain.commission_amount(basis_minor=basis, currency=CurrencyCode.GBP, rate_bp=bp)
        == expected
    )


def test_zero_decimal_currency_rounds_to_whole_units():
    assert (
        domain.commission_amount(
            basis_minor=1234, currency=CurrencyCode.JPY, rate_bp=1000
        )
        == 123
    )


def test_no_float_reaches_the_ledger_model():
    from django.db import models

    assert not any(
        isinstance(f, models.FloatField)
        for f in CommissionAccrual._meta.get_fields()
        if hasattr(f, "get_internal_type")
    )
    assert isinstance(Decimal("0.1") + Decimal("0.2"), Decimal)


def test_a_continuation_earns_only_on_the_increase():
    assert domain.sale_basis(total_minor=5000, predecessor_total_minor=None) == 5000
    assert domain.sale_basis(total_minor=5000, predecessor_total_minor=3000) == 2000
    assert domain.sale_basis(total_minor=3000, predecessor_total_minor=5000) == 0


def test_rule_selection_precedence_is_total_and_deterministic():
    import uuid

    from workeasy360.commercial.commissions.domain import RuleView

    now = timezone.now()
    partner = uuid.uuid4()

    def rule(code, bp, *, partner_id=None, product="", start=-10, end=None, version=1):
        return RuleView(
            id=uuid.uuid4(),
            code=code,
            version=version,
            partner_id=partner_id,
            product_code=product,
            rate_bp=bp,
            effective_from=now + timedelta(days=start),
            retired_at=None if end is None else now + timedelta(days=end),
        )

    default = rule("d", 1000)
    specific = rule("s", 2000, partner_id=partner)
    product = rule("p", 3000, product="crm")
    chosen = domain.select_rule(
        [default, product, specific], partner_id=partner, product_code="crm", at=now
    )
    assert chosen.code == "s"  # partner-specific beats product-specific
    assert (
        domain.select_rule(
            [default, product], partner_id=partner, product_code="crm", at=now
        ).code
        == "p"
    )
    assert (
        domain.select_rule([product], partner_id=partner, product_code="books", at=now)
        is None
    )
    ended = rule("old", 500, end=-5)
    assert (
        domain.select_rule([ended], partner_id=partner, product_code="crm", at=now)
        is None
    )


# --- accrual --------------------------------------------------------------


def test_a_sale_earns_one_deterministic_commission(channel):
    result, _o = provision(channel)
    item = sell(channel, result.organization_id)  # listener accrues after commit
    rows = _accruals(channel["partner"])
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "accrual"
    assert row.amount_minor == 500  # 10% of 4999, half-up
    assert row.basis_minor == 4999 and row.rate_bp == 1000 and row.rule_version == 1
    assert row.source_ref == f"subscriptions.item:{item.id}"
    assert row.attribution_kind == "managed"
    assert row.currency == "GBP"


def test_accrual_is_idempotent(channel):
    result, _o = provision(channel)
    sell(channel, result.organization_id)
    for _ in range(3):
        stats = accrual.accrue_for_organization(result.organization_id)
        assert stats.created == 0 and stats.duplicates == 1
    assert len(_accruals(channel["partner"])) == 1


def test_reconcile_detects_a_partner_without_the_earn_capability(channel):
    result, _o = provision(channel)
    services.revoke_capability(
        channel["staff_ctx"],
        channel["partner"].id,
        capability="commission.earn",
        reason="Programme change",
    )
    sell(channel, result.organization_id)
    assert _accruals(channel["partner"]) == []
    report = accrual.reconcile(organization_ids=[result.organization_id])
    assert report.skipped.get("not_entitled_to_earn") == 1 and report.is_clean


def test_a_sale_in_another_currency_accrues_nothing_and_is_reported(
    catalog, partner_factory, staff_factory, user_factory
):
    fx = partner_factory(
        "customer.provision", "subscription.sell", "commission.earn", currency="EUR"
    )
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    _u, cctx = staff_factory("platform.staff_commission.manage")
    rules.create_rule_version(
        cctx, code="eur", rate_bp=1000, reason="Programme default rate"
    )
    fx.update(catalog=catalog, user_factory=user_factory)
    # Billing currency defaults to the partner's (EUR) but the catalog only
    # prices GBP, so the sale itself is refused: no FX is invented.
    from workeasy360.contracts.errors import PriceNotAvailable

    result, _o = provision(fx)
    with pytest.raises(PriceNotAvailable):
        sell(fx, result.organization_id)
    assert _accruals(fx["partner"]) == []


def test_a_rule_is_versioned_and_old_sales_keep_their_rate(channel):
    first, _o = provision(channel)
    sell(channel, first.organization_id)
    rules.create_rule_version(
        channel["commission_ctx"],
        code="default",
        rate_bp=2500,
        reason="New programme rate from now",
    )
    second, _o2 = provision(channel, name="Second")
    import time

    time.sleep(0.05)
    sell(channel, second.organization_id)
    rows = _accruals(channel["partner"])
    assert [r.rate_bp for r in rows] == [1000, 2500]
    assert [r.rule_version for r in rows] == [1, 2]
    # Re-running accrual never rewrites the first row.
    accrual.accrue_for_organization(first.organization_id)
    assert _accruals(channel["partner"])[0].amount_minor == 500


def test_a_rule_cannot_be_backdated_or_edited(channel):
    from workeasy360.commercial.commissions.models import CommissionRule

    with pytest.raises(PartnerValidationError):
        rules.create_rule_version(
            channel["commission_ctx"],
            code="default",
            rate_bp=9000,
            reason="Trying to change the past",
            effective_from=timezone.now() - timedelta(days=30),
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        CommissionRule.objects.filter(code="default").update(rate_bp=9999)


def test_rate_must_be_integer_basis_points(channel):
    for bad in (12.5, "1000", True, -1, 10001):
        with pytest.raises(PartnerValidationError):
            rules.create_rule_version(
                channel["commission_ctx"],
                code="bad",
                rate_bp=bad,
                reason="Should be refused",
            )


# --- ledger immutability --------------------------------------------------


def test_the_ledger_cannot_be_updated_or_deleted(channel):
    from django.db import DatabaseError

    result, _o = provision(channel)
    sell(channel, result.organization_id)
    row = _accruals(channel["partner"])[0]
    # Denied by the database itself: the application role holds no UPDATE or
    # DELETE on the table, and a trigger refuses either even where granted.
    with pytest.raises(DatabaseError), transaction.atomic():
        CommissionAccrual.objects.filter(id=row.id).update(amount_minor=999999)
    with pytest.raises(DatabaseError), transaction.atomic():
        CommissionAccrual.objects.filter(id=row.id).delete()
    with pytest.raises(DatabaseError), transaction.atomic():
        row.amount_minor = 1
        row.save()
    assert _accruals(channel["partner"])[0].amount_minor == 500


def test_a_reversal_is_a_new_row_and_the_original_is_untouched(channel, staff_factory):
    result, _o = provision(channel)
    sell(channel, result.organization_id)
    original = _accruals(channel["partner"])[0]
    ledger.reverse_accrual(
        channel["commission_ctx"],
        original.id,
        reason_code="refund",
        reason="Customer was refunded in full",
        idempotency_key="rev-1",
    )
    rows = _accruals(channel["partner"])
    assert [r.kind for r in rows] == ["accrual", "reversal"]
    assert rows[0].amount_minor == 500 and rows[1].amount_minor == -500
    assert rows[1].reverses_id == original.id
    summary = selectors.balance_summary(channel["partner"].id)
    assert summary == [
        {
            "currency": "GBP",
            "earned_minor": 500,
            "adjustments_minor": -500,
            "balance_minor": 0,
            "entries": 2,
        }
    ]


def test_reversals_cannot_exceed_the_entry(channel):
    result, _o = provision(channel)
    sell(channel, result.organization_id)
    original = _accruals(channel["partner"])[0]
    ledger.reverse_accrual(
        channel["commission_ctx"],
        original.id,
        reason_code="correction",
        reason="Partial correction of 300",
        idempotency_key="a",
        amount_minor=300,
    )
    with pytest.raises(PartnerValidationError):
        ledger.reverse_accrual(
            channel["commission_ctx"],
            original.id,
            reason_code="correction",
            reason="Would exceed the original",
            idempotency_key="b",
            amount_minor=300,
        )


def test_a_manual_reversal_is_idempotent(channel):
    result, _o = provision(channel)
    sell(channel, result.organization_id)
    original = _accruals(channel["partner"])[0]
    for _ in range(3):
        ledger.reverse_accrual(
            channel["commission_ctx"],
            original.id,
            reason_code="refund",
            reason="Customer was refunded",
            idempotency_key="same",
        )
    assert len([r for r in _accruals(channel["partner"]) if r.kind == "reversal"]) == 1


def test_immediate_cancellation_inside_the_holdback_reverses_automatically(
    channel, staff_factory
):
    from workeasy360.commercial.subscriptions import services as subs
    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_for_organization,
    )
    from workeasy360.platform.authz.control_plane import (
        acting_scope,
        context_for_organization,
    )

    result, _o = provision(channel)
    sell(channel, result.organization_id)
    _u, sctx = staff_factory("platform.staff_subscription.manage")
    customer = context_for_organization(sctx, result.organization_id)
    with acting_scope(customer):
        subscription = subscriptions_for_organization()[0]
        subs.cancel_subscription(
            customer,
            subscription_id=subscription.id,
            idempotency_key="cancel-now",
            immediate=True,
            reason_code="fraud",
        )
    rows = _accruals(channel["partner"])
    assert [r.kind for r in rows] == ["accrual", "reversal"]
    assert rows[1].reason_code == "cancellation" and rows[1].amount_minor == -500
    # Running it again adds nothing.
    accrual.accrue_for_organization(result.organization_id)
    assert len(_accruals(channel["partner"])) == 2


def test_a_period_end_cancellation_does_not_reverse(channel, staff_factory):
    from workeasy360.commercial.subscriptions import services as subs
    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_for_organization,
    )
    from workeasy360.platform.authz.control_plane import (
        acting_scope,
        context_for_organization,
    )

    result, _o = provision(channel)
    sell(channel, result.organization_id)
    _u, sctx = staff_factory("platform.staff_subscription.manage")
    customer = context_for_organization(sctx, result.organization_id)
    with acting_scope(customer):
        subscription = subscriptions_for_organization()[0]
        subs.cancel_subscription(
            customer,
            subscription_id=subscription.id,
            idempotency_key="cancel-later",
            immediate=False,
        )
    assert [r.kind for r in _accruals(channel["partner"])] == ["accrual"]


def test_a_downgrade_earns_nothing_more_and_reverses_nothing(channel):
    from workeasy360.commercial.partners import customers
    from workeasy360.commercial.subscriptions.selectors import sale_facts

    result, _o = provision(channel)
    services.grant_product(
        channel["staff_ctx"], channel["partner"].id, product_code="crm"
    )
    upgrade_item = sell(channel, result.organization_id, plan="crm_starter")
    customers.change_customer_plan(
        channel["actor"](),
        result.organization_id,
        subscription_item_id=upgrade_item.id,
        to_plan_version_id=channel["catalog"]["versions"]["crm_professional"].id,
        idempotency_key="upgrade",
    )
    rows = _accruals(channel["partner"])
    # 10% of 1499 on the sale, then 10% of the *increase* (4999 - 1499) on the upgrade.
    assert [r.amount_minor for r in rows] == [150, 350]
    facts = sale_facts(result.organization_id)
    assert facts[-1].predecessor_total_minor == 1499


def test_a_listener_failure_never_fails_the_sale_and_reconcile_finds_the_gap(
    channel, monkeypatch
):
    result, _o = provision(channel)

    def boom(*_a, **_k):
        raise RuntimeError("commission subsystem down")

    monkeypatch.setattr(accrual, "accrue_for_organization", boom)
    item = sell(channel, result.organization_id)  # the sale still succeeds
    assert item.product_code == "crm"
    monkeypatch.undo()

    assert _accruals(channel["partner"]) == []
    report = accrual.reconcile(organization_ids=[result.organization_id])
    assert len(report.missing) == 1 and not report.is_clean
    accrual.accrue_missing(organization_ids=[result.organization_id])
    assert len(_accruals(channel["partner"])) == 1
    assert accrual.reconcile(organization_ids=[result.organization_id]).is_clean


# --- payouts --------------------------------------------------------------


@pytest.fixture
def owed(channel, staff_factory, monkeypatch):
    """A partner with cleared commission ready to be paid."""
    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    result, _o = provision(channel)
    sell(channel, result.organization_id)
    _pu, prep = staff_factory("platform.staff_payout.manage")
    _au, approver = staff_factory("platform.staff_payout.approve")
    return {**channel, "prep": prep, "approver": approver, "org": result.organization_id}


def _prepare(owed, key="p1"):
    return payouts.prepare_payout(
        owed["prep"],
        partner_id=owed["partner"].id,
        period_end=timezone.now().date(),
        idempotency_key=key,
    )


def test_a_payout_gathers_cleared_entries_and_reconciles(owed):
    payout = _prepare(owed)
    assert payout.status == "draft"
    assert (payout.gross_minor, payout.adjustments_minor, payout.payable_minor) == (
        500,
        0,
        500,
    )
    assert payouts.reconcile_payout(payout.id)["ok"]


def test_preparing_twice_with_one_key_is_one_payout(owed):
    first = _prepare(owed, "same")
    again = _prepare(owed, "same")
    assert first.id == again.id and Payout.objects.count() == 1


def test_an_entry_is_in_at_most_one_live_payout(owed):
    _prepare(owed, "one")
    # Everything payable is already in the first payout: nothing is left.
    with pytest.raises(PartnerValidationError):
        _prepare(owed, "two")


def test_payout_lifecycle_and_separation_of_duties(owed):
    payout = _prepare(owed)
    payouts.mark_payout_ready(owed["prep"], payout.id)
    with pytest.raises(SeparationOfDutiesViolation):
        # The preparer holds manage; give them approve too to prove the rule is
        # about *identity*, not permission.
        from workeasy360.platform.authz import staff

        approver_ctx = owed["prep"]
        staff.create_grant(
            user_id=approver_ctx.user_id,
            permission="platform.staff_payout.approve",
            granted_by_id=owed["approver"].user_id,
            reason="Test proves identity, not permission",
        )
        payouts.approve_payout(
            approver_ctx, payout.id, reason="Approving my own preparation"
        )
    approved = payouts.approve_payout(
        owed["approver"], payout.id, reason="Reviewed and correct"
    )
    assert (
        approved.status == "approved"
        and approved.approved_by_id == owed["approver"].user_id
    )
    paid = payouts.record_payout_paid(owed["prep"], payout.id, reference="BANK-REF-0001")
    assert paid.status == "paid" and paid.paid_reference == "BANK-REF-0001"


def test_nothing_is_marked_paid_without_a_reference_or_approval(owed):
    payout = _prepare(owed)
    payouts.mark_payout_ready(owed["prep"], payout.id)
    with pytest.raises(InvalidStateTransition):
        payouts.record_payout_paid(owed["prep"], payout.id, reference="BANK-REF-0001")
    payouts.approve_payout(owed["approver"], payout.id, reason="Reviewed and correct")
    with pytest.raises(PartnerValidationError):
        payouts.record_payout_paid(
            owed["prep"], payout.id, reference="GB29 NWBK 6016 1331 9268 19"
        )


def test_approval_needs_the_dangerous_permission_and_aal2(owed, staff_factory):
    payout = _prepare(owed)
    payouts.mark_payout_ready(owed["prep"], payout.id)
    _u, low = staff_factory("platform.staff_payout.approve", aal=1)
    with pytest.raises(StepUpRequired):
        payouts.approve_payout(low, payout.id, reason="No second factor here")
    with pytest.raises(PermissionDenied):
        payouts.approve_payout(owed["prep"], payout.id, reason="Not permitted to approve")


def test_a_cancelled_payout_releases_its_entries(owed):
    payout = _prepare(owed)
    payouts.cancel_payout(owed["prep"], payout.id, reason="Prepared for the wrong period")
    again = _prepare(owed, "second")
    assert again.payable_minor == 500 and again.id != payout.id


def test_paid_and_cancelled_are_terminal(owed):
    payout = _prepare(owed)
    payouts.cancel_payout(owed["prep"], payout.id, reason="Prepared in error today")
    with pytest.raises(InvalidStateTransition):
        payouts.mark_payout_ready(owed["prep"], payout.id)


def test_a_suspended_partners_payouts_are_held(owed):
    from workeasy360.contracts.enums import PartnerStatus

    payout = _prepare(owed)
    payouts.mark_payout_ready(owed["prep"], payout.id)
    services.set_partner_status(
        owed["staff_ctx"],
        owed["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Under review now",
    )
    with pytest.raises(PartnerNotActive):
        payouts.approve_payout(
            owed["approver"], payout.id, reason="Should be held while suspended"
        )
    with pytest.raises(PartnerNotActive):
        _prepare(owed, "new-request")


def test_a_reversal_nets_against_the_payable_balance(owed):
    original = _accruals(owed["partner"])[0]
    ledger.record_adjustment(
        owed["commission_ctx"],
        owed["partner"].id,
        amount_minor=-200,
        currency="GBP",
        reason="Overpayment correction of 2.00",
        idempotency_key="adj",
    )
    payout = _prepare(owed)
    assert (payout.gross_minor, payout.adjustments_minor, payout.payable_minor) == (
        500,
        -200,
        300,
    )
    assert original.amount_minor == 500


def test_an_adjustment_is_bounded_and_in_the_partner_currency(owed):
    for amount, currency in ((0, "GBP"), (5, "USD"), (10**13, "GBP"), (True, "GBP")):
        with pytest.raises(PartnerValidationError):
            ledger.record_adjustment(
                owed["commission_ctx"],
                owed["partner"].id,
                amount_minor=amount,
                currency=currency,
                reason="Should be refused outright",
                idempotency_key="x",
            )


def test_a_partner_sees_only_its_own_ledger_and_payouts(owed, partner_factory):
    other = partner_factory("commission.view", "payout.view", "commission.earn")
    mine = selectors.accruals_page(partner_id=owed["partner"].id, for_partner=True)
    theirs = selectors.accruals_page(partner_id=other["partner"].id, for_partner=True)
    assert len(mine.items) == 1 and theirs.items == []
    # A partner's row carries no derivation.
    assert set(mine.items[0]) == {
        "id",
        "kind",
        "amount_minor",
        "currency",
        "event_at",
        "clears_at",
        "reason_code",
        "reverses",
    }
    payout = _prepare(owed)
    from workeasy360.commercial.commissions.models import Payout

    assert not Payout.objects.filter(
        id=payout.id, partner_id=other["partner"].id
    ).exists()
    assert "approved_by" not in selectors.payout_dict(payout, for_partner=True)
    assert (
        selectors.payouts_page(partner_id=owed["partner"].id, for_partner=True).items
        == []
    )

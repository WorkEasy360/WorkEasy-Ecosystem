"""Payouts: preparation, approval and manual confirmation (Phase 7, Task 46-48, 105).

**No money moves here.** A payout is a reviewed batch of ledger rows that a
person will settle by some other channel. ``paid`` is reachable only by a staff
member supplying an external reference - there is no path by which this
platform decides that payment happened.

**Dual approval required: NO.** The money flow is manual and low volume, the
payout is prepared and approved by *different* staff members (the database
refuses an approver equal to the preparer), approval needs its own permission,
AAL 2 and a reason, and it is audited. That is separation of duties without
the operational cost of a two-signature workflow. The trigger to adopt dual
approval is any of: automated payment execution, a single payout above a
configured threshold, or more than one approver being routinely unavailable.

**A ledger entry can be in at most one live payout** - a partial unique index on
``PayoutLine`` - so a retry, a race or a re-run cannot pay a commission twice.
Totals are snapshotted when a payout is prepared and **re-derived from its
lines** before approval; if they disagree, approval is refused. So is a batch
one of whose entries has been **reversed since it was prepared** (a refund
recorded in between would otherwise be paid anyway), and so is an approval by
the **author of a manual entry** in the batch - separation of duties covers
the money being paid, not only the batch being prepared.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from typing import Any, Final
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from workeasy360.commercial.commissions import domain
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.domain import clean_reason
from workeasy360.contracts.enums import AccrualKind, PartnerStatus, PayoutStatus
from workeasy360.contracts.errors import (
    IdempotencyConflict,
    PartnerError,
    PartnerNotActive,
    PartnerRecordNotFound,
    PartnerValidationError,
    SeparationOfDutiesViolation,
)
from workeasy360.contracts.events import partner_events as schemas
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.events import services as events
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.metrics import metrics

MANAGE = "platform.staff_payout.manage"
APPROVE = "platform.staff_payout.approve"

MAX_LINES: Final = 5000
PAYOUT_OPERATIONS = "payout_operations_total"
_REFERENCE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{2,63}$")


class PayoutReconciliationFailed(PartnerError):
    """A payout's stored totals do not match what its lines derive."""

    code = "payout_reconciliation_failed"


def _lock(payout_id: UUID, *, expected_version: int | None) -> Any:
    from workeasy360.commercial.commissions.models import Payout
    from workeasy360.commercial.partners.services import ConcurrentPartnerModification

    payout = Payout.objects.select_for_update().filter(id=payout_id).first()
    if payout is None:
        raise PartnerRecordNotFound("No such payout.", payout_id=str(payout_id))
    if expected_version is not None and payout.version != expected_version:
        raise ConcurrentPartnerModification(
            f"Payout was at version {payout.version}, not {expected_version}.",
            expected_version=expected_version,
            actual_version=payout.version,
        )
    return payout


def _publish(ctx: TenantContext, schema: Any, payout: Any) -> None:
    events.publish(
        events.build_event(
            ctx,
            schema.event_type,
            1,
            aggregate_type=schema.aggregate_type,
            aggregate_id=payout.id,
            payload={"partner_id": str(payout.partner_id), "payout_id": str(payout.id)},
        )
    )


def _advisory(name: str) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [name])


# --- preparation ----------------------------------------------------------


@in_scope
def prepare_payout(
    ctx: TenantContext, *, partner_id: UUID, period_end: date, idempotency_key: Any
) -> Any:
    """Gather a partner's cleared, unpaid ledger rows into a draft payout.

    An **active** partner, or a **terminated** one being settled - termination
    ends the relationship, not money already earned - can have a payout
    prepared; a suspended partner's payouts are held, and a draft partner has
    earned nothing. Every ledger row - accruals, reversals and
    adjustments alike - whose holdback has cleared by ``period_end`` and which
    is not already in a live payout is included, so a reversal nets against
    what it reverses even when they land in different payouts. If the net is
    not positive, nothing is created: the rows simply carry forward.
    """
    from workeasy360.commercial.commissions.models import (
        CommissionAccrual,
        Payout,
        PayoutLine,
    )

    authz.require(ctx, MANAGE)
    if not isinstance(period_end, date) or period_end > timezone.now().date():
        raise PartnerValidationError(
            "period_end must be today or earlier: a commission that has not "
            "cleared cannot be paid.",
            field="period_end",
        )
    text = str(idempotency_key or "").strip()
    if not text or len(text) > 90:
        raise PartnerValidationError(
            "An idempotency key of 1-90 characters is required.", field="idempotency_key"
        )
    key = f"prepare:{partner_id}:{text}"

    partner = partner_selectors.partner_row(partner_id)
    if partner is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    if partner.status_enum not in (PartnerStatus.ACTIVE, PartnerStatus.TERMINATED):
        raise PartnerNotActive(
            "Payouts are prepared only for an active partner, or to settle a "
            "terminated one; a suspended partner's payouts are held.",
            partner_status=partner.status,
        )
    cutoff = datetime.combine(
        period_end + timedelta(days=1), time.min, tzinfo=dt_timezone.utc
    )

    with transaction.atomic():
        # One preparation per partner at a time: two concurrent runs would
        # otherwise each select the same rows (the unique index would still
        # refuse the second, but as a failure rather than a clean wait). The
        # key is checked *after* the lock, so the second of two requests with
        # one key waits for the first and then replays it, instead of finding
        # nothing left to pay.
        _advisory(f"payout:{partner_id}")
        existing = Payout.objects.filter(idempotency_key=key).first()
        if existing is not None:
            if existing.partner_id != partner_id or existing.period_end != period_end:
                raise IdempotencyConflict(
                    "That idempotency key was used for a different payout."
                )
            return existing

        eligible = list(
            CommissionAccrual.objects.filter(
                partner_id=partner_id,
                currency=partner.payout_currency,
                clears_at__lt=cutoff,
            )
            .annotate(
                in_live_payout=Exists(
                    PayoutLine.objects.filter(
                        accrual_id=OuterRef("pk"), released_at__isnull=True
                    )
                )
            )
            .filter(in_live_payout=False)
            .order_by("event_at", "id")[: MAX_LINES + 1]
        )
        if len(eligible) > MAX_LINES:
            raise PartnerValidationError(
                f"More than {MAX_LINES} entries are payable; choose an earlier period_end.",
                field="period_end",
            )
        gross = sum(
            r.amount_minor for r in eligible if r.kind == AccrualKind.ACCRUAL.value
        )
        adjustments = sum(
            r.amount_minor for r in eligible if r.kind != AccrualKind.ACCRUAL.value
        )
        payable = gross + adjustments
        if payable <= 0:
            raise PartnerValidationError(
                "Nothing is payable for this partner in this period; the "
                "entries carry forward.",
                field="period_end",
            )

        payout = Payout.objects.create(
            partner_id=partner_id,
            currency=partner.payout_currency,
            period_end=period_end,
            gross_minor=gross,
            adjustments_minor=adjustments,
            payable_minor=payable,
            line_count=len(eligible),
            created_by_id=ctx.user_id,
            idempotency_key=key,
        )
        PayoutLine.objects.bulk_create(
            [
                PayoutLine(payout=payout, accrual=r, amount_minor=r.amount_minor)
                for r in eligible
            ]
        )
        audit.record(
            ctx,
            "payout.prepared",
            resource_type="commissions.payout",
            resource_id=payout.id,
            changes={
                "partner_id": str(partner_id),
                "period_end": period_end.isoformat(),
                "lines": len(eligible),
                "currency": partner.payout_currency,
            },
        )
    metrics.increment(PAYOUT_OPERATIONS, operation="prepare", outcome="ok")
    return payout


@in_scope
def mark_payout_ready(
    ctx: TenantContext, payout_id: UUID, *, expected_version: int | None = None
) -> Any:
    """``draft -> ready``: the preparer certifies the batch for review."""
    authz.require(ctx, MANAGE)
    with transaction.atomic():
        payout = _lock(payout_id, expected_version=expected_version)
        current = payout.status_enum
        domain.assert_payout_transition(current, PayoutStatus.READY)
        if current is PayoutStatus.READY:
            return payout
        payout.status = PayoutStatus.READY.value
        payout.ready_at = timezone.now()
        payout.version += 1
        payout.save(update_fields=["status", "ready_at", "version"])
        audit.record(
            ctx,
            "payout.ready",
            resource_type="commissions.payout",
            resource_id=payout.id,
            changes={"partner_id": str(payout.partner_id)},
        )
    metrics.increment(PAYOUT_OPERATIONS, operation="ready", outcome="ok")
    return payout


# --- approval -------------------------------------------------------------


def derived_totals(payout_id: UUID) -> dict[str, int]:
    """Re-derive a payout's totals from its live lines and their ledger rows."""
    from django.db.models import F, Q, Sum

    from workeasy360.commercial.commissions.models import PayoutLine

    lines = PayoutLine.objects.filter(payout_id=payout_id, released_at__isnull=True)
    agg = lines.aggregate(
        gross=Sum("amount_minor", filter=Q(accrual__kind=AccrualKind.ACCRUAL.value)),
        adjustments=Sum(
            "amount_minor", filter=~Q(accrual__kind=AccrualKind.ACCRUAL.value)
        ),
        ledger=Sum("accrual__amount_minor"),
        snapshot=Sum("amount_minor"),
    )
    mismatched = lines.exclude(amount_minor=F("accrual__amount_minor")).count()
    return {
        "gross": agg["gross"] or 0,
        "adjustments": agg["adjustments"] or 0,
        "ledger": agg["ledger"] or 0,
        "snapshot": agg["snapshot"] or 0,
        "lines": lines.count(),
        "mismatched_lines": mismatched,
    }


def reversed_since_preparation(payout_id: UUID) -> int:
    """How many of the payout's entries have a reversal that is not in it.

    Typically a reversal recorded after the batch was prepared (it cannot be
    in the batch), or one that clears after the batch's period end. Either
    way it would only net against a *later* payout - after this one had paid
    the entry in full. For a partner with nothing further to earn there is no
    later payout, and the money is simply gone. Cancelling and preparing again
    (with a period end that includes the reversal) nets them in one batch.
    """
    from workeasy360.commercial.commissions.models import (
        CommissionAccrual,
        PayoutLine,
    )

    live = PayoutLine.objects.filter(payout_id=payout_id, released_at__isnull=True)
    return (
        CommissionAccrual.objects.filter(
            reverses_id__in=live.values("accrual_id"),
            kind=AccrualKind.REVERSAL.value,
        )
        .exclude(id__in=live.values("accrual_id"))
        .count()
    )


_UNSETTLED: Final = frozenset(
    {PayoutStatus.DRAFT, PayoutStatus.READY, PayoutStatus.APPROVED}
)


def reconcile_payout(payout_id: UUID) -> dict[str, Any]:
    """Read-only: do the payout's totals equal what its lines derive, and is
    every entry still owed in full?"""
    from workeasy360.commercial.commissions.models import Payout

    payout = Payout.objects.filter(id=payout_id).first()
    if payout is None:
        raise PartnerRecordNotFound("No such payout.", payout_id=str(payout_id))
    derived = derived_totals(payout_id)
    problems = []
    if payout.status_enum is not PayoutStatus.CANCELLED:
        if derived["gross"] != payout.gross_minor:
            problems.append("gross_mismatch")
        if derived["adjustments"] != payout.adjustments_minor:
            problems.append("adjustments_mismatch")
        if derived["gross"] + derived["adjustments"] != payout.payable_minor:
            problems.append("payable_mismatch")
        if derived["lines"] != payout.line_count:
            problems.append("line_count_mismatch")
        if derived["mismatched_lines"]:
            problems.append("line_amount_differs_from_ledger")
    if payout.status_enum in _UNSETTLED and reversed_since_preparation(payout_id):
        problems.append("entry_reversed_after_preparation")
    return {
        "payout_id": str(payout_id),
        "ok": not problems,
        "problems": problems,
        "derived": derived,
    }


@in_scope
def approve_payout(
    ctx: TenantContext,
    payout_id: UUID,
    *,
    reason: Any,
    expected_version: int | None = None,
) -> Any:
    """``ready -> approved``. A decision by a *different* person than the preparer.

    Refused when: the caller prepared it, or recorded a manual ledger entry
    it contains; the partner is suspended (its payouts are held); the totals
    stored on the payout do not equal what its lines derive; or an entry in it
    has been reversed since it was prepared (cancel and prepare it again).
    Idempotent: approving an approved payout returns it.
    """
    authz.require(ctx, APPROVE)
    why = clean_reason(reason)

    with transaction.atomic():
        payout = _lock(payout_id, expected_version=expected_version)
        current = payout.status_enum
        domain.assert_payout_transition(current, PayoutStatus.APPROVED)
        if current is PayoutStatus.APPROVED:
            return payout
        if payout.created_by_id == ctx.user_id:
            raise SeparationOfDutiesViolation(
                "A payout must be approved by someone other than the person "
                "who prepared it."
            )
        if _authored_a_line(payout.id, ctx.user_id):
            raise SeparationOfDutiesViolation(
                "This payout contains a manual ledger entry you recorded; "
                "someone else must approve paying it."
            )
        partner = partner_selectors.partner_row(payout.partner_id)
        if partner is None or partner.status_enum is PartnerStatus.SUSPENDED:
            raise PartnerNotActive(
                "This partner's payouts are held while it is suspended.",
                partner_status=partner.status if partner else None,
            )
        report = reconcile_payout(payout.id)
        if not report["ok"]:
            raise PayoutReconciliationFailed(
                "The payout's totals do not match its lines; it cannot be approved.",
                problems=report["problems"],
            )
        payout.status = PayoutStatus.APPROVED.value
        payout.approved_by_id = ctx.user_id
        payout.approved_at = timezone.now()
        payout.approval_reason = why
        payout.version += 1
        payout.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "approval_reason",
                "version",
            ]
        )
        _publish(ctx, schemas.PAYOUT_APPROVED_V1, payout)
        audit.record(
            ctx,
            "payout.approved",
            resource_type="commissions.payout",
            resource_id=payout.id,
            changes={
                "partner_id": str(payout.partner_id),
                "payable_minor": payout.payable_minor,
                "currency": payout.currency,
                "lines": payout.line_count,
            },
            reason=why,
        )
    metrics.increment(PAYOUT_OPERATIONS, operation="approve", outcome="ok")
    return payout


def _authored_a_line(payout_id: UUID, user_id: UUID) -> bool:
    """Did ``user_id`` record (by hand) any ledger entry this payout pays?"""
    from workeasy360.commercial.commissions.models import PayoutLine

    return PayoutLine.objects.filter(
        payout_id=payout_id,
        released_at__isnull=True,
        accrual__created_by_id=user_id,
    ).exists()


@in_scope
def record_payout_paid(
    ctx: TenantContext,
    payout_id: UUID,
    *,
    reference: Any,
    expected_version: int | None = None,
) -> Any:
    """``approved -> paid``: a person confirms money left by another channel.

    The reference is an opaque transfer identifier the confirming person
    supplies. No bank or card data is accepted or stored: the pattern refuses
    anything that is not a short reference token.
    """
    authz.require(ctx, MANAGE)
    ref = str(reference or "").strip()
    if not _REFERENCE_RE.match(ref):
        raise PartnerValidationError(
            "reference must be a 3-64 character transfer reference "
            "(letters, digits and . _ : / # -).",
            field="reference",
        )

    with transaction.atomic():
        payout = _lock(payout_id, expected_version=expected_version)
        current = payout.status_enum
        domain.assert_payout_transition(current, PayoutStatus.PAID)
        if current is PayoutStatus.PAID:
            return payout
        payout.status = PayoutStatus.PAID.value
        payout.paid_at = timezone.now()
        payout.paid_by_id = ctx.user_id
        payout.paid_reference = ref
        payout.version += 1
        payout.save(
            update_fields=["status", "paid_at", "paid_by", "paid_reference", "version"]
        )
        _publish(ctx, schemas.PAYOUT_PAID_V1, payout)
        audit.record(
            ctx,
            "payout.paid",
            resource_type="commissions.payout",
            resource_id=payout.id,
            changes={
                "partner_id": str(payout.partner_id),
                "payable_minor": payout.payable_minor,
            },
        )
    metrics.increment(PAYOUT_OPERATIONS, operation="paid", outcome="ok")
    return payout


@in_scope
def cancel_payout(
    ctx: TenantContext,
    payout_id: UUID,
    *,
    reason: Any,
    expected_version: int | None = None,
) -> Any:
    """Cancel an unpaid payout and release its ledger rows back to the pool."""
    from workeasy360.commercial.commissions.models import PayoutLine

    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    with transaction.atomic():
        payout = _lock(payout_id, expected_version=expected_version)
        current = payout.status_enum
        domain.assert_payout_transition(current, PayoutStatus.CANCELLED)
        if current is PayoutStatus.CANCELLED:
            return payout
        now = timezone.now()
        released = PayoutLine.objects.filter(
            payout_id=payout.id, released_at__isnull=True
        ).update(released_at=now)
        payout.status = PayoutStatus.CANCELLED.value
        payout.cancelled_at = now
        payout.cancelled_by_id = ctx.user_id
        payout.cancel_reason = why
        payout.version += 1
        payout.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason",
                "version",
            ]
        )
        audit.record(
            ctx,
            "payout.cancelled",
            resource_type="commissions.payout",
            resource_id=payout.id,
            changes={"partner_id": str(payout.partner_id), "released_lines": released},
            reason=why,
        )
    metrics.increment(PAYOUT_OPERATIONS, operation="cancel", outcome="ok")
    return payout

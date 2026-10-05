"""Manual ledger corrections: reversals and adjustments (Task 41, 45, 106).

A finalized ledger entry is never edited. When one is wrong - a refund, a
chargeback, a mistaken rate - the correction is a **new entry** that refers to
the one it corrects, so the partner's statement shows both the original and its
correction and the balance is the sum. Both commands are staff-only, dangerous
(AAL 2), require a reason a person will later read, are idempotent on the
caller's key and are audited.

Neither accepts an amount from a partner or a customer - only staff reach this
module, and the *sign*, currency and bounds are validated server-side.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction

from workeasy360.commercial.commissions import accrual, domain, selectors
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.domain import clean_reason
from workeasy360.contracts.enums import AccrualKind
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.idempotency import services as idempotency
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.metrics import metrics

MANAGE = "platform.staff_commission.manage"
MAX_ADJUSTMENT_MINOR = 10**12


def _key(ctx: TenantContext, client_key: Any) -> str:
    text = str(client_key or "").strip()
    if not text or len(text) > 90:
        raise PartnerValidationError(
            "An idempotency key of 1-90 characters is required.", field="idempotency_key"
        )
    return f"{ctx.user_id}:{text}"


@in_scope
def reverse_accrual(
    ctx: TenantContext,
    accrual_id: UUID,
    *,
    reason_code: Any,
    reason: Any,
    idempotency_key: Any,
    amount_minor: int | None = None,
) -> Any:
    """Offset all or part of an accrual with a reversal entry.

    ``amount_minor`` is a **positive** number of minor units to claw back
    (default: the whole entry); the stored entry is its negative. The running
    total of reversals of one entry can never exceed it.
    """
    authz.require(ctx, MANAGE)
    code = str(reason_code or "")
    if code not in domain.REVERSAL_REASONS:
        raise PartnerValidationError(
            f"reason_code must be one of {sorted(domain.REVERSAL_REASONS)}.",
            field="reason_code",
        )
    why = clean_reason(reason)
    key = _key(ctx, idempotency_key)

    original = selectors.get_accrual(accrual_id)
    if original is None or original.kind != AccrualKind.ACCRUAL.value:
        raise PartnerRecordNotFound(
            "No such commission entry.", accrual_id=str(accrual_id)
        )
    amount = original.amount_minor if amount_minor is None else amount_minor
    if (
        isinstance(amount, bool)
        or not isinstance(amount, int)
        or not 0 < amount <= original.amount_minor
    ):
        raise PartnerValidationError(
            "amount_minor must be a positive whole number no larger than the entry.",
            field="amount_minor",
        )

    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commissions.reverse",
            key=key,
            arguments={"accrual": str(accrual_id), "amount": amount, "reason_code": code},
        )
        if claim.is_replay:
            return original
        created = accrual.insert_reversal(
            original,
            amount_minor=-amount,
            reason_code=code,
            reason=why,
            created_by_id=ctx.user_id,
            idempotency_key=f"manual-reversal:{key}",
            audited=False,
        )
        audit.record(
            ctx,
            "commission.reversal_recorded",
            resource_type="commissions.accrual",
            resource_id=accrual_id,
            changes={"reason_code": code, "amount_minor": -amount, "created": created},
            reason=why,
        )
        idempotency.complete(claim, result_ref=f"commissions.accrual:{accrual_id}")
    metrics.increment("commission_manual_entries_total", kind="reversal")
    return original


@in_scope
def record_adjustment(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    amount_minor: Any,
    currency: Any,
    reason: Any,
    idempotency_key: Any,
) -> Any:
    """Append a signed adjustment to a partner's ledger.

    The currency must be the partner's payout currency - the platform never
    converts - and the amount is a non-zero whole number of minor units,
    bounded. The original entries are untouched.
    """
    from django.utils import timezone

    from workeasy360.commercial.commissions.models import CommissionAccrual

    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    key = _key(ctx, idempotency_key)
    partner = partner_selectors.partner_row(partner_id)
    if partner is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    if (
        isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or amount_minor == 0
        or abs(amount_minor) > MAX_ADJUSTMENT_MINOR
    ):
        raise PartnerValidationError(
            "amount_minor must be a non-zero whole number of minor units.",
            field="amount_minor",
        )
    if str(currency) != partner.payout_currency:
        raise PartnerValidationError(
            f"An adjustment must be in the partner's payout currency "
            f"({partner.payout_currency}); currencies are never converted.",
            field="currency",
        )

    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commissions.adjust",
            key=key,
            arguments={"partner": str(partner_id), "amount": amount_minor},
        )
        if claim.is_replay:
            ref = claim.result_ref.partition(":")[2]
            return CommissionAccrual.objects.get(id=ref)
        now = timezone.now()
        try:
            with transaction.atomic():
                row = CommissionAccrual.objects.create(
                    partner_id=partner_id,
                    kind=AccrualKind.ADJUSTMENT.value,
                    amount_minor=amount_minor,
                    currency=partner.payout_currency,
                    source_ref="manual",
                    event_at=now,
                    clears_at=now,
                    reason_code="correction",
                    reason=why,
                    created_by_id=ctx.user_id,
                    idempotency_key=f"manual-adjustment:{key}",
                )
        except IntegrityError as exc:
            raise PartnerValidationError("That adjustment was already recorded.") from exc
        audit.record(
            ctx,
            "commission.adjustment_recorded",
            resource_type="commissions.accrual",
            resource_id=row.id,
            changes={"partner_id": str(partner_id), "amount_minor": amount_minor},
            reason=why,
        )
        idempotency.complete(claim, result_ref=f"commissions.accrual:{row.id}")
    metrics.increment("commission_manual_entries_total", kind="adjustment")
    return row

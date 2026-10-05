"""The payments clock: invoices for rolled periods, stale checkouts, dunning,
and (Phase 9) orders unpaid past their time or paid but not yet fulfilled.

One pass per organization, each in its own transactions and database scope, so
one tenant's problem never stops the sweep for the rest (the same shape as
``subscriptions.system.process_due_subscriptions``). Idempotent throughout:
each step is decided by a durable timestamp that has passed and recorded on the
row it concerns, so running it twice does nothing the second time.

Dunning for an open invoice, measured from its due date:

====================================  ===================================
unpaid after ``due_at``                subscription ``active -> past_due``
unpaid ``PAYMENT_GRACE_AFTER_DAYS``    ``past_due -> grace``
unpaid ``PAYMENT_SUSPEND_AFTER_DAYS``  ``grace -> suspended`` (read-only;
                                       data kept, "pay now" still open)
====================================  ===================================

Payment in full at any stage returns it to ``active`` (``settlement``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

# Phase 12 (G-32): SoftTimeLimitExceeded subclasses Exception. Swallowed
# here, a pass that outran its budget kept going until the hard kill, with
# no summary, and the organizations enumerated last were never reached.
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.payments import settlement
from workeasy360.commercial.payments.invoicing import ensure_invoices, organization_scope
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


def process_due(*, at: datetime | None = None) -> dict[str, int]:
    from workeasy360.commercial.payments import orders
    from workeasy360.platform.organizations.selectors import all_organization_ids

    moment = at or timezone.now()
    totals = {
        "invoices": 0,
        "expired_attempts": 0,
        "dunning_steps": 0,
        "expired_orders": 0,
        "fulfilled_orders": 0,
        "failed": 0,
    }
    for organization_id in all_organization_ids():
        try:
            totals["invoices"] += len(ensure_invoices(organization_id, at=moment))
            totals["expired_attempts"] += _expire_attempts(organization_id, moment)
            totals["dunning_steps"] += _dun(organization_id, moment)
            for key, count in orders.process_due(organization_id, moment).items():
                totals[key] += count
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            totals["failed"] += 1
            metrics.increment("payments_clock_failures_total")
            logger.error(
                "payments.clock.organization_failed",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )
    return totals


def _expire_attempts(organization_id: UUID, moment: datetime) -> int:
    from workeasy360.commercial.payments.models import PaymentAttempt

    expired = 0
    after = settlement.AfterCommit()
    with (
        organization_scope(
            organization_id, reason="Expire payment attempts past their time"
        ),
        transaction.atomic(),
    ):
        stale = list(
            PaymentAttempt.unscoped.select_for_update(skip_locked=True).filter(
                organization_id=organization_id,
                status__in=("pending", "awaiting_payment"),
                expires_at__lte=moment,
            )[:200]
        )
        for attempt in stale:
            if (
                settlement.record_failure(
                    attempt,
                    status="expired",
                    failure_code="checkout_expired",
                    after=after,
                )
                == settlement.APPLIED
            ):
                expired += 1
    return expired


def _dun(organization_id: UUID, moment: datetime) -> int:
    from workeasy360.commercial.payments.models import Invoice
    from workeasy360.commercial.subscriptions import system

    grace_after = timedelta(days=int(settings.PAYMENT_GRACE_AFTER_DAYS))
    suspend_after = timedelta(days=int(settings.PAYMENT_SUSPEND_AFTER_DAYS))
    with organization_scope(organization_id, reason="Read overdue invoices for dunning"):
        # An order's invoice is never dunned: nothing was granted, so an
        # unpaid order simply expires (``orders.process_due``).
        overdue = list(
            Invoice.unscoped.filter(
                organization_id=organization_id,
                status=Invoice.Status.OPEN,
                due_at__lt=moment,
                order__isnull=True,
            ).values("id", "subscription_id", "due_at", "dunning_stage")
        )
    steps = 0
    for row in overdue:
        stage, target = _next_stage(row, moment, grace_after, suspend_after)
        if target is None:
            continue
        moved: Any
        if target == "past_due":
            moved = system.mark_past_due(
                organization_id, subscription_id=row["subscription_id"]
            )
        elif target == "grace":
            moved = system.enter_grace(
                organization_id, subscription_id=row["subscription_id"]
            )
        else:
            moved = _suspend(organization_id, row["subscription_id"])
        # Recorded on the invoice either way: if the subscription was already
        # further along (another overdue invoice moved it), this invoice has
        # still reached this stage.
        _record_stage(organization_id, row["id"], target)
        if moved is not None:
            steps += 1
    return steps


def _next_stage(
    row: dict[str, Any],
    moment: datetime,
    grace_after: timedelta,
    suspend_after: timedelta,
) -> tuple[str, str | None]:
    stage = row["dunning_stage"]
    due: datetime = row["due_at"]
    if stage == "":
        return stage, "past_due"
    if stage == "past_due" and moment >= due + grace_after:
        return stage, "grace"
    if stage == "grace" and moment >= due + suspend_after:
        return stage, "suspended"
    return stage, None


def _suspend(organization_id: UUID, subscription_id: UUID) -> Any:
    from workeasy360.commercial.subscriptions import system
    from workeasy360.contracts.errors import InvalidStateTransition

    try:
        return system.suspend_for_non_payment(
            organization_id, subscription_id=subscription_id, reason_code="invoice_unpaid"
        )
    except InvalidStateTransition:
        # Not in grace (staff intervened, or it was never active): leave it.
        return None


def _record_stage(organization_id: UUID, invoice_id: UUID, stage: str) -> None:
    from workeasy360.commercial.payments.models import Invoice
    from workeasy360.platform.audit import services as audit

    with (
        organization_scope(organization_id, reason="Record an invoice's dunning stage"),
        transaction.atomic(),
    ):
        updated = Invoice.unscoped.filter(
            id=invoice_id, organization_id=organization_id, status=Invoice.Status.OPEN
        ).update(dunning_stage=stage, updated_at=timezone.now())
        if updated:
            audit.record_system(
                organization_id,
                "billing.invoice_dunning",
                resource_type="payments.invoice",
                resource_id=invoice_id,
                changes={"stage": stage},
                reason="An invoice remained unpaid past a dunning threshold.",
            )

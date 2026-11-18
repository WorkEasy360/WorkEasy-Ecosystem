"""Applying a payment outcome - once - to an attempt, its invoice and, after
commit, the subscription.

Shared by the provider webhook (``webhooks.process``) and a Super Admin
recording a manual payment (``staff.mark_invoice_paid``), so "what paying an
invoice means" has one implementation.

Every function here runs **inside** the caller's transaction and one
organization's scope, takes row locks (attempt, then invoice - always that
order, so two settlements cannot deadlock), and is idempotent: a second
success for the same attempt changes nothing. What must happen *after* commit
(moving the subscription out of dunning, emailing a receipt) is returned as
``AfterCommit`` for the caller to run once its transaction has committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.utils import timezone

from workeasy360.platform.audit import services as audit
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

APPLIED = "applied"
DUPLICATE = "duplicate"
MISMATCH = "mismatch"
UNMATCHED = "unmatched"


@dataclass
class AfterCommit:
    """Follow-up work for once the settlement is durable."""

    settle_subscription: UUID | None = None
    lift_suspension: bool = False
    receipt: tuple[Any, Any] | None = None
    failure_notice: tuple[Any, Any] | None = None
    #: Phase 9: a paid order to fulfil, and money that arrived for an invoice
    #: that was no longer open (void, or already paid) - refunded in full.
    fulfil_order: UUID | None = None
    refund_attempt: UUID | None = None
    notes: list[str] = field(default_factory=list)

    def run(self, organization_id: UUID) -> None:
        from workeasy360.commercial.payments import notices
        from workeasy360.commercial.subscriptions import system

        if self.fulfil_order is not None:
            from workeasy360.commercial.payments.orders import fulfil

            fulfil(organization_id, self.fulfil_order)
        if self.refund_attempt is not None:
            from workeasy360.commercial.payments.refunds import refund_automatically

            refund_automatically(
                organization_id,
                self.refund_attempt,
                reason="Payment received for an invoice that was no longer open.",
            )
        if self.settle_subscription is not None:
            system.settle_after_payment(
                organization_id,
                subscription_id=self.settle_subscription,
                lift_suspension=self.lift_suspension,
            )
        if self.receipt is not None:
            notices.payment_received(organization_id, *self.receipt)
        if self.failure_notice is not None:
            notices.payment_failed(organization_id, *self.failure_notice)


def lock_attempt(organization_id: UUID, **lookup: Any) -> Any:
    from workeasy360.commercial.payments.models import PaymentAttempt

    return (
        PaymentAttempt.unscoped.select_for_update()
        .filter(organization_id=organization_id, **lookup)
        .first()
    )


def record_success(
    attempt: Any,
    *,
    amount_minor: int | None,
    currency: str,
    payment_id: str,
    after: AfterCommit,
) -> str:
    """The provider took the money. Accepted even for an attempt already marked
    expired or failed - a late success is still money received, and refusing
    to record it would lose track of it.

    Phase 9: an amount the provider did not report is a mismatch like a wrong
    one - nothing settles on an unverified figure. Money for an invoice that is
    no longer open (voided with its order, or already paid by another attempt)
    is recorded and refunded in full after commit, so nobody is charged twice.
    An order invoice becoming paid marks its order paid, and the order is
    fulfilled after commit."""
    from workeasy360.commercial.payments.models import Invoice

    if attempt.status == "succeeded":
        return DUPLICATE
    if (
        amount_minor is None
        or amount_minor != attempt.amount_minor
        or (currency and currency != attempt.currency)
    ):
        # Money arrived that is not what was asked for. Nothing is settled
        # automatically; the webhook log shows it for staff to reconcile.
        logger.error(
            "payments.settlement.mismatch",
            attempt_id=str(attempt.id),
            expected=attempt.amount_minor,
            received=amount_minor,
        )
        metrics.increment("payments_settlement_mismatch_total")
        return MISMATCH
    now = timezone.now()
    attempt.status = "succeeded"
    attempt.provider_payment_id = (payment_id or attempt.provider_payment_id)[:128]
    attempt.checkout_url = ""
    attempt.failure_code = ""
    attempt.completed_at = now
    attempt.save(
        update_fields=[
            "status",
            "provider_payment_id",
            "checkout_url",
            "failure_code",
            "completed_at",
            "updated_at",
        ]
    )
    invoice = (
        Invoice.unscoped.select_for_update()
        .filter(id=attempt.invoice_id, organization_id=attempt.organization_id)
        .first()
    )
    was_open = invoice.status == Invoice.Status.OPEN and invoice.amount_due_minor > 0
    invoice.amount_paid_minor += attempt.amount_minor
    fields = ["amount_paid_minor", "version", "updated_at"]
    invoice.version += 1
    from workeasy360.commercial.payments import wallet_orders

    if not was_open:
        after.refund_attempt = attempt.id
        metrics.increment("payments_unexpected_payments_total")
        wallet_orders.release_for_attempt(attempt, why="invoice_not_open")
    elif attempt.provider_key != wallet_orders.WALLET_PROVIDER:
        # ADR-065: the Platform Wallet's part of this payment, held while the
        # payer was at the provider, is taken now - in this same transaction.
        invoice.amount_paid_minor += wallet_orders.capture_for_attempt(
            attempt, invoice, now=now
        )
    if (
        invoice.status == Invoice.Status.OPEN
        and invoice.amount_paid_minor >= invoice.total_minor
    ):
        invoice.status = Invoice.Status.PAID
        invoice.paid_at = now
        fields += ["status", "paid_at"]
        if invoice.order_id is not None:
            from workeasy360.commercial.payments.orders import mark_paid

            after.fulfil_order = mark_paid(invoice, at=now)
        still_overdue = (
            Invoice.unscoped.filter(
                organization_id=invoice.organization_id,
                subscription_id=invoice.subscription_id,
                status=Invoice.Status.OPEN,
            )
            .exclude(id=invoice.id)
            .exclude(dunning_stage="")
            .exists()
        )
        if invoice.dunning_stage and not still_overdue:
            after.settle_subscription = invoice.subscription_id
            after.lift_suspension = invoice.dunning_stage == "suspended"
    invoice.save(update_fields=fields)
    audit.record_system(
        attempt.organization_id,
        "billing.payment_succeeded",
        resource_type="payments.attempt",
        resource_id=attempt.id,
        changes={
            "invoice": invoice.number,
            "amount_minor": attempt.amount_minor,
            "currency": attempt.currency,
            "provider": attempt.provider_key,
            "invoice_status": invoice.status,
        },
        reason="The payment provider confirmed the payment.",
    )
    after.receipt = (invoice, attempt)
    metrics.increment("payments_succeeded_total", provider=attempt.provider_key)
    return APPLIED


def record_failure(
    attempt: Any, *, status: str, failure_code: str, after: AfterCommit
) -> str:
    """A payment did not happen (failed, or the page expired). Only an attempt
    still in flight moves; a succeeded one never goes back."""
    from workeasy360.commercial.payments.models import Invoice

    if attempt.status not in ("pending", "awaiting_payment"):
        return DUPLICATE
    attempt.status = status
    attempt.failure_code = failure_code[:64]
    attempt.checkout_url = ""
    attempt.completed_at = timezone.now()
    attempt.save(
        update_fields=[
            "status",
            "failure_code",
            "checkout_url",
            "completed_at",
            "updated_at",
        ]
    )
    # ADR-065: what this payment held in the Platform Wallet is spendable again.
    from workeasy360.commercial.payments import wallet_orders

    wallet_orders.release_for_attempt(attempt, why=status)
    audit.record_system(
        attempt.organization_id,
        f"billing.payment_{status}",
        resource_type="payments.attempt",
        resource_id=attempt.id,
        changes={"failure_code": failure_code},
        reason="The payment provider reported that the payment did not complete.",
    )
    if status == "failed":
        invoice = Invoice.unscoped.filter(
            id=attempt.invoice_id, organization_id=attempt.organization_id
        ).first()
        after.failure_notice = (invoice, attempt)
    metrics.increment("payments_attempts_ended_total", status=status)
    return APPLIED


def record_refund(
    refund: Any, *, succeeded: bool, reference: str, failure_code: str = ""
) -> str:
    """A refund's final outcome. Amounts move only on success, and only once."""
    from workeasy360.commercial.payments.models import Invoice, PaymentAttempt, Refund

    if refund.status in (Refund.Status.SUCCEEDED, Refund.Status.FAILED):
        return DUPLICATE
    now = timezone.now()
    refund.provider_refund_id = (reference or refund.provider_refund_id)[:128]
    refund.completed_at = now
    if not succeeded:
        refund.status = Refund.Status.FAILED
        refund.failure_code = failure_code[:64] or "provider_failed"
        refund.save(
            update_fields=[
                "status",
                "failure_code",
                "provider_refund_id",
                "completed_at",
                "updated_at",
            ]
        )
        return APPLIED
    attempt = (
        PaymentAttempt.unscoped.select_for_update()
        .filter(id=refund.attempt_id, organization_id=refund.organization_id)
        .first()
    )
    invoice = (
        Invoice.unscoped.select_for_update()
        .filter(id=attempt.invoice_id, organization_id=refund.organization_id)
        .first()
    )
    refund.status = Refund.Status.SUCCEEDED
    refund.save(
        update_fields=["status", "provider_refund_id", "completed_at", "updated_at"]
    )
    attempt.amount_refunded_minor += refund.amount_minor
    attempt.save(update_fields=["amount_refunded_minor", "updated_at"])
    invoice.amount_refunded_minor += refund.amount_minor
    invoice.version += 1
    invoice.save(update_fields=["amount_refunded_minor", "version", "updated_at"])
    _issue_credit_note(refund, invoice)
    # A refund of a top-up or a credit pack takes it back out of the wallet. A refund
    # of a wallet payment already put it back, before asking this to confirm it.
    if attempt.provider_key != "wallet":
        from workeasy360.commercial.payments import wallet_orders

        wallet_orders.reverse_for_refund(refund, invoice)
    audit.record_system(
        refund.organization_id,
        "billing.refund_succeeded",
        resource_type="payments.refund",
        resource_id=refund.id,
        changes={
            "amount_minor": refund.amount_minor,
            "currency": refund.currency,
            "invoice": invoice.number,
        },
        reason="The payment provider confirmed the refund.",
    )
    metrics.increment("payments_refunds_succeeded_total")
    return APPLIED


def _issue_credit_note(refund: Any, invoice: Any) -> None:
    """The document a confirmed refund leaves, naming what it reversed. In the
    refund's own transaction; one per refund, whichever way it was confirmed."""
    from django.db import connection

    from workeasy360.commercial.payments.models import CreditNote

    if CreditNote.objects.filter(refund_id=refund.id).exists():
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('payments_credit_note_number_seq')")
        sequence = cursor.fetchone()[0]
    CreditNote.objects.create(
        number=f"CN-{sequence:06d}",
        organization_id=refund.organization_id,
        refund_id=refund.id,
        attempt_id=refund.attempt_id,
        invoice_id=invoice.id,
        invoice_number=invoice.number,
        amount_minor=refund.amount_minor,
        currency=refund.currency,
        reason=refund.reason,
    )

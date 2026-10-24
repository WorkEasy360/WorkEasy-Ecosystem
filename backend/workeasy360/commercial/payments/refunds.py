"""Refunds: requested by staff, sent to the provider outside any transaction.

``request_refund`` (``platform.staff_payment.manage`` - dangerous, a
reason, audited in the customer's own log) locks the attempt, refuses more than
was captured minus what is already refunded or in flight, records a
``requested`` refund and a timer.

``send_refund`` (the task) moves it to ``submitting`` in a committed
transaction *before* calling the provider, then records the provider's answer.
If a worker dies between the two, the refund is found still ``submitting``:
for a provider whose refunds carry an idempotency key (Stripe) it is simply
sent again - the provider returns the same refund; for one without (Razorpay)
it becomes ``needs_review``, because sending it twice would return the money
twice, and a person must check the provider's dashboard.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from workeasy360.commercial.payments import settlement
from workeasy360.commercial.payments.invoicing import organization_scope
from workeasy360.commercial.payments.providers import adapter_for
from workeasy360.commercial.payments.providers.contract import (
    ProviderFailure,
    RefundRequest,
)
from workeasy360.contracts.errors import (
    PaymentNotAllowed,
    PaymentRecordNotFound,
    PaymentValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MANAGE = "platform.staff_payment.manage"
REFUND_TIMER = "payments.refund"

_OPEN = ("requested", "submitting", "pending", "needs_review")


def request_refund(
    actor: TenantContext,
    *,
    organization_id: UUID,
    attempt_id: UUID,
    amount_minor: Any,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    from workeasy360.commercial.payments.models import Refund
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.authz.control_plane import context_for_organization
    from workeasy360.platform.tenancy.context import tenant_context

    authz.require(actor, MANAGE)
    if (
        isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or amount_minor <= 0
    ):
        raise PaymentValidationError(
            "amount_minor must be a positive integer.", field="amount_minor"
        )
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise PaymentValidationError(
            "Give a reason of at least 10 characters.", field="reason"
        )
    if not idempotency_key or len(idempotency_key) > 128:
        raise PaymentValidationError(
            "An Idempotency-Key of at most 128 characters is required.",
            field="idempotency_key",
        )
    rebound = context_for_organization(actor, organization_id)
    with (
        organization_scope(organization_id, reason="Staff requests a refund"),
        transaction.atomic(),
    ):
        replay = Refund.unscoped.filter(
            organization_id=organization_id, idempotency_key=idempotency_key
        ).first()
        if replay is not None:
            if replay.attempt_id != attempt_id:
                raise PaymentValidationError(
                    "This Idempotency-Key was used for another refund.",
                    field="idempotency_key",
                )
            return refund_view(replay)
        attempt = settlement.lock_attempt(organization_id, id=attempt_id)
        if attempt is None:
            raise PaymentRecordNotFound("No such payment.")
        if attempt.status != "succeeded" or attempt.provider_key == "manual":
            raise PaymentNotAllowed(
                "Only a payment collected through a provider can be refunded here."
            )
        in_flight = (
            Refund.unscoped.filter(
                organization_id=organization_id, attempt_id=attempt.id, status__in=_OPEN
            ).aggregate(total=Sum("amount_minor"))["total"]
            or 0
        )
        available = attempt.amount_minor - attempt.amount_refunded_minor - in_flight
        if amount_minor > available:
            raise PaymentNotAllowed(
                f"At most {available} can be refunded on this payment.",
                available_minor=available,
            )
        refund = Refund(
            organization_id=organization_id,
            attempt_id=attempt.id,
            amount_minor=amount_minor,
            currency=attempt.currency,
            reason=reason[:255],
            requested_by_user_id=actor.user_id,
            idempotency_key=idempotency_key,
        )
        refund.save()
        scheduling.schedule(
            REFUND_TIMER,
            organization_id=organization_id,
            target_id=refund.id,
            due_at=timezone.now(),
        )
        with tenant_context(rebound):
            audit.record(
                rebound,
                "billing.refund_requested",
                resource_type="payments.refund",
                resource_id=refund.id,
                changes={
                    "attempt": str(attempt.id),
                    "amount_minor": amount_minor,
                    "currency": attempt.currency,
                },
                reason=reason,
            )
        from workeasy360.commercial.payments import tasks

        scheduling.enqueue_after_commit(tasks.send_refund, [refund.id], ctx=rebound)
    metrics.increment("payments_refunds_requested_total")
    return refund_view(refund)


def refund_automatically(organization_id: UUID, attempt_id: UUID, *, reason: str) -> None:
    """Return everything still refundable on a payment the platform must not
    keep (Phase 9): money for an invoice that was no longer open, or for an
    order that could not be fulfilled. No actor - the system decided, and the
    audit entry says why. Idempotent on the attempt: a second call finds the
    refund already requested. Best effort: a failure here is logged and the
    payment stays visible to staff in the webhook log and the customer's
    billing, never lost."""
    from workeasy360.commercial.payments.models import Refund
    from workeasy360.contracts.values.tenant import NO_MEMBERSHIP
    from workeasy360.integration.scheduling import services as scheduling

    key = f"auto:{attempt_id}"
    try:
        with (
            organization_scope(organization_id, reason="Refund a payment automatically"),
            transaction.atomic(),
        ):
            if Refund.unscoped.filter(
                organization_id=organization_id, idempotency_key=key
            ).exists():
                return
            attempt = settlement.lock_attempt(organization_id, id=attempt_id)
            if (
                attempt is None
                or attempt.status != "succeeded"
                or attempt.provider_key == "manual"
            ):
                return
            in_flight = (
                Refund.unscoped.filter(
                    organization_id=organization_id,
                    attempt_id=attempt.id,
                    status__in=_OPEN,
                ).aggregate(total=Sum("amount_minor"))["total"]
                or 0
            )
            amount = attempt.amount_minor - attempt.amount_refunded_minor - in_flight
            if amount <= 0:
                return
            refund = Refund(
                organization_id=organization_id,
                attempt_id=attempt.id,
                amount_minor=amount,
                currency=attempt.currency,
                reason=reason[:255],
                requested_by_user_id=NO_MEMBERSHIP,
                idempotency_key=key,
            )
            refund.save()
            scheduling.schedule(
                REFUND_TIMER,
                organization_id=organization_id,
                target_id=refund.id,
                due_at=timezone.now(),
            )
            audit.record_system(
                organization_id,
                "billing.refund_requested",
                resource_type="payments.refund",
                resource_id=refund.id,
                changes={
                    "attempt": str(attempt.id),
                    "amount_minor": amount,
                    "currency": attempt.currency,
                    "automatic": True,
                },
                reason=reason,
            )
            from workeasy360.commercial.payments import tasks

            ctx = scheduling.routing_context(organization_id)
            if ctx is not None:
                scheduling.enqueue_after_commit(tasks.send_refund, [refund.id], ctx=ctx)
    except Exception as exc:
        metrics.increment("payments_automatic_refund_failures_total")
        logger.error(
            "payments.refund.automatic_failed",
            organization_id=str(organization_id),
            attempt_id=str(attempt_id),
            error_type=type(exc).__name__,
        )
        return
    metrics.increment("payments_refunds_requested_total")


def refund_view(refund: Any) -> dict[str, Any]:
    return {
        "refund_id": str(refund.id),
        "attempt_id": str(refund.attempt_id),
        "amount_minor": refund.amount_minor,
        "currency": refund.currency,
        "status": refund.status,
        "reason": refund.reason,
        "failure_code": refund.failure_code or None,
        "created_at": refund.created_at.isoformat(),
        "completed_at": refund.completed_at.isoformat() if refund.completed_at else None,
    }


def send_refund(organization_id: UUID, refund_id: UUID) -> str:
    from workeasy360.commercial.payments import credentials, transport
    from workeasy360.commercial.payments.models import (
        PaymentAttempt,
        PaymentProvider,
        Refund,
    )
    from workeasy360.integration.scheduling import services as scheduling

    with (
        organization_scope(organization_id, reason="Submit a refund to the provider"),
        transaction.atomic(),
    ):
        refund = (
            Refund.unscoped.select_for_update()
            .filter(id=refund_id, organization_id=organization_id)
            .first()
        )
        if refund is None or refund.status not in ("requested", "submitting"):
            scheduling.complete(REFUND_TIMER, refund_id, organization_id=organization_id)
            return "not_open"
        attempt = PaymentAttempt.unscoped.get(
            id=refund.attempt_id, organization_id=organization_id
        )
        provider = PaymentProvider.objects.filter(key=attempt.provider_key).first()
        adapter = adapter_for(provider.adapter) if provider is not None else None
        if adapter is None:
            refund.status = Refund.Status.NEEDS_REVIEW
            refund.failure_code = "provider_missing"
            refund.save(update_fields=["status", "failure_code", "updated_at"])
            scheduling.complete(REFUND_TIMER, refund_id, organization_id=organization_id)
            return "needs_review"
        if refund.status == Refund.Status.SUBMITTING and not adapter.idempotent_refunds:
            refund.status = Refund.Status.NEEDS_REVIEW
            refund.failure_code = "outcome_unknown"
            refund.save(update_fields=["status", "failure_code", "updated_at"])
            scheduling.complete(REFUND_TIMER, refund_id, organization_id=organization_id)
            logger.error("payments.refund.needs_review", refund_id=str(refund_id))
            return "needs_review"
        refund.status = Refund.Status.SUBMITTING
        refund.save(update_fields=["status", "updated_at"])
        request = RefundRequest(
            refund_id=str(refund.id),
            organization_id=str(organization_id),
            payment_id=attempt.provider_payment_id,
            amount_minor=refund.amount_minor,
            currency=refund.currency,
        )

    try:
        if getattr(adapter, "offline", False):
            result = adapter.offline_refund(request)  # type: ignore[attr-defined]
        else:
            api_key = credentials.api_key(
                provider.credential_ref, adapter=provider.adapter
            )
            call = adapter.refund_call(request, api_key=api_key)
            del api_key
            response = transport.send(adapter, call)
            result = adapter.parse_refund(response.status, response.body)
    except credentials.CredentialUnavailable:
        return _mark(organization_id, refund_id, status="requested", note="unavailable")
    except ProviderFailure as failure:
        if failure.retryable:
            # Nothing reached the provider (or it said "try later"): back to
            # requested, the timer retries.
            return _mark(
                organization_id, refund_id, status="requested", note=failure.category
            )
        if failure.category == "timeout":
            # It may have been processed. The next claim finds it
            # ``submitting`` and applies the idempotency rule above.
            return "unknown"
        return _finish(
            organization_id,
            refund_id,
            succeeded=False,
            reference="",
            code=failure.category,
        )

    if result.status == "pending":
        return _mark(
            organization_id,
            refund_id,
            status="pending",
            note="",
            reference=result.reference,
        )
    return _finish(
        organization_id,
        refund_id,
        succeeded=result.status == "succeeded",
        reference=result.reference,
        code="provider_failed",
    )


def _mark(
    organization_id: UUID, refund_id: UUID, *, status: str, note: str, reference: str = ""
) -> str:
    from workeasy360.commercial.payments.models import Refund
    from workeasy360.integration.scheduling import services as scheduling

    with (
        organization_scope(organization_id, reason="Record a refund's provider status"),
        transaction.atomic(),
    ):
        fields = {"status": status, "updated_at": timezone.now()}
        if reference:
            fields["provider_refund_id"] = reference[:128]
        Refund.unscoped.filter(
            id=refund_id, organization_id=organization_id, status="submitting"
        ).update(**fields)
        if status == "pending":
            # The provider's webhook completes it; no more sending.
            scheduling.complete(REFUND_TIMER, refund_id, organization_id=organization_id)
    if note:
        logger.warning("payments.refund.deferred", category=note)
    return status


def _finish(
    organization_id: UUID, refund_id: UUID, *, succeeded: bool, reference: str, code: str
) -> str:
    from workeasy360.commercial.payments.models import Refund
    from workeasy360.integration.scheduling import services as scheduling

    with (
        organization_scope(organization_id, reason="Record a refund's outcome"),
        transaction.atomic(),
    ):
        refund = (
            Refund.unscoped.select_for_update()
            .filter(id=refund_id, organization_id=organization_id)
            .first()
        )
        outcome = (
            settlement.record_refund(
                refund, succeeded=succeeded, reference=reference, failure_code=code
            )
            if refund is not None
            else settlement.UNMATCHED
        )
        scheduling.complete(REFUND_TIMER, refund_id, organization_id=organization_id)
    return outcome

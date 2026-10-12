"""Paying an invoice: an attempt, a hosted checkout page, and nothing else.

1. **Start** (``start_payment``, a member with ``platform.billing.manage`` on
   the console's billing or checkout page): lock the invoice,
   return the attempt already in flight if there is one (two clicks, two tabs,
   one checkout), otherwise choose a provider for the invoice's currency and
   record a ``pending`` attempt plus a due-work timer, audited. No network.
2. **Create the checkout** (``create_checkout``, a task): read the attempt,
   call the provider **outside any transaction** (E2), then record the hosted
   page's reference and address - ``awaiting_payment`` - in a second, short
   transaction that only succeeds if the attempt is still ``pending``. A
   retryable failure leaves the timer to try again; the rest fail the attempt.
3. **The browser** polls the attempt and, once it has an address, goes there.
   What the payer types happens on the provider's page.
4. **The webhook** (``webhooks.process``) - never the browser's return - decides
   whether the invoice was paid.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.payments.invoicing import organization_scope
from workeasy360.commercial.payments.providers import adapter_for
from workeasy360.commercial.payments.providers.contract import (
    CheckoutRequest,
    ProviderFailure,
)
from workeasy360.contracts.errors import (
    PaymentNotAllowed,
    PaymentProviderUnavailable,
    PaymentRecordNotFound,
    PaymentValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.identity import portals as portal_links
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MANAGE = "platform.billing.manage"
CHECKOUT_TIMER = "payments.checkout"

_IN_FLIGHT = ("pending", "awaiting_payment")


def choose_provider(currency: str) -> Any:
    """The enabled provider with the lowest priority that takes ``currency``.
    The test provider is never chosen unless development has enabled it."""
    from workeasy360.commercial.payments.models import PaymentProvider, ProviderAdapter

    for provider in PaymentProvider.objects.filter(
        status=PaymentProvider.Status.ENABLED
    ).order_by("priority", "key"):
        if (
            provider.adapter == ProviderAdapter.FAKE
            and not settings.PAYMENTS_FAKE_PROVIDER_ENABLED
        ):
            continue
        if currency in (provider.currencies or []):
            return provider
    return None


def start_payment(
    ctx: TenantContext, *, invoice_id: UUID, idempotency_key: str
) -> dict[str, Any]:
    from workeasy360.commercial.payments.models import Invoice, PaymentAttempt
    from workeasy360.integration.scheduling import services as scheduling

    authz.require(ctx, MANAGE)
    if not idempotency_key or len(idempotency_key) > 128:
        raise PaymentValidationError(
            "An Idempotency-Key of at most 128 characters is required.",
            field="idempotency_key",
        )
    now = timezone.now()
    with transaction.atomic():
        replay = PaymentAttempt.objects.filter(idempotency_key=idempotency_key).first()
        if replay is not None:
            if replay.invoice_id != invoice_id:
                raise PaymentValidationError(
                    "This Idempotency-Key was used for another invoice.",
                    field="idempotency_key",
                )
            return attempt_view(replay)
        invoice = Invoice.objects.select_for_update().filter(id=invoice_id).first()
        if invoice is None:
            raise PaymentRecordNotFound("No such invoice.")
        if invoice.status != Invoice.Status.OPEN or invoice.amount_due_minor <= 0:
            raise PaymentNotAllowed("This invoice has nothing to pay.")
        in_flight = (
            PaymentAttempt.objects.filter(
                invoice=invoice, status__in=_IN_FLIGHT, expires_at__gt=now
            )
            .order_by("-created_at")
            .first()
        )
        if in_flight is not None:
            return attempt_view(in_flight)
        provider = choose_provider(invoice.currency)
        if provider is None:
            raise PaymentProviderUnavailable(
                f"Online payment in {invoice.currency} is not available yet. "
                "Contact support to pay this invoice another way."
            )
        try:
            with transaction.atomic():
                attempt = PaymentAttempt.objects.create(
                    invoice=invoice,
                    provider_key=provider.key,
                    provider_mode=provider.mode,
                    amount_minor=invoice.amount_due_minor,
                    currency=invoice.currency,
                    idempotency_key=idempotency_key,
                    created_by_user_id=ctx.user_id,
                    expires_at=now
                    + timedelta(minutes=int(settings.PAYMENT_ATTEMPT_TTL_MINUTES)),
                )
        except IntegrityError as exc:
            raise PaymentValidationError(
                "This Idempotency-Key is already in use.", field="idempotency_key"
            ) from exc
        scheduling.schedule(
            CHECKOUT_TIMER,
            organization_id=ctx.organization_id,
            target_id=attempt.id,
            due_at=now,
        )
        audit.record(
            ctx,
            "billing.payment_started",
            resource_type="payments.attempt",
            resource_id=attempt.id,
            changes={
                "invoice": invoice.number,
                "amount_minor": attempt.amount_minor,
                "currency": attempt.currency,
                "provider": provider.key,
            },
        )
        from workeasy360.commercial.payments import tasks

        scheduling.enqueue_after_commit(tasks.create_checkout, [attempt.id], ctx=ctx)
    metrics.increment("payments_attempts_started_total", provider=provider.key)
    return attempt_view(attempt)


def attempt_view(attempt: Any) -> dict[str, Any]:
    """The browser's view of an attempt. The hosted page's address only while
    it is still the thing to do."""
    return {
        "attempt_id": str(attempt.id),
        "invoice_id": str(attempt.invoice_id),
        "status": attempt.status,
        "amount_minor": attempt.amount_minor,
        "currency": attempt.currency,
        "provider": attempt.provider_key,
        "checkout_url": attempt.checkout_url
        if attempt.status == "awaiting_payment"
        else None,
        "failure_code": attempt.failure_code or None,
        "expires_at": attempt.expires_at.isoformat(),
    }


def attempt_status(ctx: TenantContext, attempt_id: UUID) -> dict[str, Any]:
    from workeasy360.commercial.payments.models import PaymentAttempt

    authz.require(ctx, "platform.billing.view")
    attempt = PaymentAttempt.objects.filter(id=attempt_id).first()
    if attempt is None:
        raise PaymentRecordNotFound("No such payment.")
    return attempt_view(attempt)


def hosted_page_url(attempt: Any, reference: str) -> str:
    """The development test provider's "hosted page": the platform's own."""
    return (
        f"{portal_links.app_url()}/billing/test-checkout#reference={reference}"
        f"&org={attempt.organization_id}"
    )


# --- the task -------------------------------------------------------------------


def create_checkout(organization_id: UUID, attempt_id: UUID) -> str:
    """Obtain the hosted page for one pending attempt. Idempotent: anything but
    a still-pending, unexpired attempt is left alone and its timer dropped."""
    from workeasy360.commercial.payments import credentials
    from workeasy360.commercial.payments.models import (
        Invoice,
        PaymentAttempt,
        PaymentProvider,
    )
    from workeasy360.integration.scheduling import services as scheduling

    with organization_scope(
        organization_id, reason="Read a payment attempt to start checkout"
    ):
        attempt = PaymentAttempt.unscoped.filter(
            id=attempt_id, organization_id=organization_id
        ).first()
        if attempt is None or attempt.status != "pending":
            scheduling.complete(
                CHECKOUT_TIMER, attempt_id, organization_id=organization_id
            )
            return "not_pending"
        if attempt.expires_at <= timezone.now():
            _finish(attempt, "expired", failure_code="expired_before_checkout")
            scheduling.complete(
                CHECKOUT_TIMER, attempt_id, organization_id=organization_id
            )
            return "expired"
        invoice = Invoice.unscoped.get(
            id=attempt.invoice_id, organization_id=organization_id
        )
        provider = PaymentProvider.objects.filter(key=attempt.provider_key).first()
    adapter = adapter_for(provider.adapter) if provider is not None else None
    if provider is None or adapter is None:
        return _fail_checkout(organization_id, attempt_id, "unavailable")

    from workeasy360.commercial.payments.invoicing import billing_account

    account = billing_account(organization_id)
    request = CheckoutRequest(
        attempt_id=str(attempt.id),
        organization_id=str(organization_id),
        invoice_number=invoice.number,
        description=f"Workeasy360 invoice {invoice.number}",
        amount_minor=attempt.amount_minor,
        currency=attempt.currency,
        customer_email=(account.billing_email if account is not None else "") or "",
        success_url=_return_url(organization_id, attempt, "success"),
        cancel_url=_return_url(organization_id, attempt, "cancelled"),
        expires_at=int(max(attempt.expires_at.timestamp(), time.time() + 31 * 60)),
    )
    try:
        if getattr(adapter, "offline", False):
            if not settings.PAYMENTS_FAKE_PROVIDER_ENABLED:
                return _fail_checkout(organization_id, attempt_id, "unavailable")
            result = adapter.offline_checkout(request)  # type: ignore[attr-defined]
            url = hosted_page_url(attempt, result.reference)
            reference = result.reference
        else:
            from workeasy360.commercial.payments import transport

            api_key = credentials.api_key(
                provider.credential_ref, adapter=provider.adapter
            )
            call = adapter.checkout_call(request, api_key=api_key)
            del api_key
            response = transport.send(adapter, call)
            result = adapter.parse_checkout(response.status, response.body)
            url, reference = result.checkout_url, result.reference
    except credentials.CredentialUnavailable:
        return _fail_checkout(organization_id, attempt_id, "unavailable")
    except ProviderFailure as failure:
        if failure.retryable:
            # The timer stays; the next claim tries again with a longer lease.
            logger.warning("payments.checkout.retry", category=failure.category)
            return "retry"
        return _fail_checkout(organization_id, attempt_id, failure.category)

    with (
        organization_scope(organization_id, reason="Record a hosted checkout page"),
        transaction.atomic(),
    ):
        moved = PaymentAttempt.unscoped.filter(
            id=attempt_id, organization_id=organization_id, status="pending"
        ).update(
            status="awaiting_payment",
            provider_reference=reference[:128],
            checkout_url=url,
            updated_at=timezone.now(),
        )
        scheduling.complete(CHECKOUT_TIMER, attempt_id, organization_id=organization_id)
    metrics.increment("payments_checkouts_created_total", provider=provider.key)
    return "awaiting_payment" if moved else "superseded"


def _return_url(organization_id: UUID, attempt: Any, outcome: str) -> str:
    from workeasy360.platform.organizations.selectors import organization_slug

    slug = organization_slug(organization_id) or ""
    return (
        f"{portal_links.app_url()}/o/{slug}/settings/billing?payment={attempt.id}"
        f"&outcome={outcome}"
    )


def _fail_checkout(organization_id: UUID, attempt_id: UUID, category: str) -> str:
    from workeasy360.commercial.payments.models import PaymentAttempt
    from workeasy360.integration.scheduling import services as scheduling

    with (
        organization_scope(organization_id, reason="Record a failed checkout"),
        transaction.atomic(),
    ):
        attempt = (
            PaymentAttempt.unscoped.select_for_update()
            .filter(id=attempt_id, organization_id=organization_id, status="pending")
            .first()
        )
        if attempt is not None:
            _finish(attempt, "failed", failure_code=f"checkout_{category}"[:64])
        scheduling.complete(CHECKOUT_TIMER, attempt_id, organization_id=organization_id)
    metrics.increment("payments_checkouts_failed_total", category=category)
    return "failed"


def _finish(attempt: Any, status: str, *, failure_code: str = "") -> None:
    """Close an attempt that never took money. Clears the hosted address."""
    attempt.status = status
    attempt.failure_code = failure_code
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
    audit.record_system(
        attempt.organization_id,
        f"billing.payment_{status}",
        resource_type="payments.attempt",
        resource_id=attempt.id,
        changes={"failure_code": failure_code},
        reason="A payment attempt ended without taking a payment.",
    )

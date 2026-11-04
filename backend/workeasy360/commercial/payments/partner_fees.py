"""Fees a partner pays the platform, online (2026-11-03).

Today one fee: the set-up fee of a partner upgrade (ADR-053, priced by Super
Admin). A partner is not an organization, so this is not an invoice attempt:
it is a ``PartnerFeePayment`` (control plane) paid through the **same** hosted
checkout - the provider chosen by ``checkout.choose_provider``, the same
adapters, the same egress, the same signed webhook. The rules are the
invoice's:

- the browser only ever *starts* a payment and is sent to the provider's page;
  what the payer types never passes through Workeasy360;
- only a **verified provider callback** marks a payment paid (``apply_webhook``,
  reached from ``webhooks.process`` for a callback that names no organization),
  and only for the exact amount and currency asked - anything else is recorded
  as a mismatch and pays nothing;
- each callback applies once (``PaymentWebhookEvent``'s unique event id).

What a paid fee *does* is not decided here: the module that owns the subject
registers a handler for its purpose (``register``), called after the payment
commits. This module imports nothing above payments.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.payments.checkout import choose_provider
from workeasy360.commercial.payments.providers import adapter_for
from workeasy360.commercial.payments.providers.contract import (
    CHECKOUT_EXPIRED,
    PAYMENT_FAILED,
    PAYMENT_SUCCEEDED,
    CheckoutRequest,
    ProviderFailure,
    WebhookEvent,
)
from workeasy360.contracts.errors import PaymentValidationError
from workeasy360.platform.identity import portals as portal_links
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

PARTNER_UPGRADE: Final = "partner_upgrade"
PURPOSES: Final = (PARTNER_UPGRADE,)
#: How long a hosted page may take payment.
CHECKOUT_LIFETIME: Final = timedelta(hours=1)
_IN_FLIGHT: Final = ("pending", "awaiting_payment")

#: ``purpose -> on_paid(subject_id, payment_id)``, registered at app start.
_HANDLERS: dict[str, Callable[[UUID, UUID], None]] = {}


def register(purpose: str, on_paid: Callable[[UUID, UUID], None]) -> None:
    """Who acts when a fee for ``purpose`` is paid (once per payment, after it
    commits). Registered by the owning module's app config."""
    if purpose not in PURPOSES:
        raise ValueError(f"Unknown partner fee purpose: {purpose}")
    _HANDLERS[purpose] = on_paid


def _model() -> Any:
    from workeasy360.commercial.payments.models import PartnerFeePayment

    return PartnerFeePayment


def online_available(currency: str) -> bool:
    """Whether a payment provider takes ``currency`` now."""
    return bool(currency) and choose_provider(currency) is not None


def view(row: Any) -> dict[str, Any]:
    """A payment as a screen reads it. The hosted address only while it can
    still be paid; never a provider credential or payer detail."""
    expired = row.status in _IN_FLIGHT and row.expires_at <= timezone.now()
    status = "expired" if expired else row.status
    return {
        "id": str(row.id),
        "status": status,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "checkout_url": row.checkout_url if status == "awaiting_payment" else None,
        "test_mode": row.provider_mode != "live",
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "failure_code": row.failure_code,
    }


def get(payment_id: UUID | None) -> dict[str, Any] | None:
    if payment_id is None:
        return None
    row = _model().objects.filter(id=payment_id).first()
    return view(row) if row is not None else None


def start(
    *,
    purpose: str,
    subject_id: UUID,
    partner_id: UUID,
    amount_minor: int,
    currency: str,
    email: str,
    user_id: UUID | None,
    description: str,
    return_url: str,
) -> dict[str, Any]:
    """A hosted checkout for one fee. A payment already in flight for the same
    subject and amount is returned rather than a second one opened (two clicks,
    two tabs, one checkout). The provider is called **outside** any
    transaction; its page's address is recorded only if the row is still
    pending."""
    if purpose not in PURPOSES:
        raise PaymentValidationError("Unknown fee.", field="purpose")
    if not isinstance(amount_minor, int) or amount_minor <= 0:
        raise PaymentValidationError("There is nothing to pay.", field="amount_minor")
    provider = choose_provider(currency)
    if provider is None:
        raise PaymentValidationError(
            "Online payment is not available for this currency. Pay offline and "
            "enter the transfer's reference instead.",
            field="payment_method",
        )
    adapter = adapter_for(provider.adapter)
    if adapter is None:
        raise PaymentValidationError(
            "Online payment is not available.", field="payment_method"
        )

    now = timezone.now()
    with transaction.atomic():
        current = (
            _model()
            .objects.select_for_update()
            .filter(
                purpose=purpose,
                subject_id=subject_id,
                status="awaiting_payment",
                expires_at__gt=now + timedelta(minutes=5),
                amount_minor=amount_minor,
                currency=currency,
            )
            .order_by("-created_at")
            .first()
        )
        if current is not None and current.checkout_url:
            return view(current)
        row = _model().objects.create(
            purpose=purpose,
            subject_id=subject_id,
            partner_id=partner_id,
            provider_key=provider.key,
            provider_mode=provider.mode,
            amount_minor=amount_minor,
            currency=currency,
            created_by_user_id=user_id,
            expires_at=now + CHECKOUT_LIFETIME,
        )

    request = CheckoutRequest(
        attempt_id=str(row.id),
        # A partner fee belongs to no organization: the callback finds it by
        # the provider's reference or this attempt id (``apply_webhook``).
        organization_id="",
        invoice_number=f"PF-{row.id.hex[:12].upper()}",
        description=description[:120],
        amount_minor=amount_minor,
        currency=currency,
        customer_email=email or "",
        success_url=_return(return_url, row.id, "success"),
        cancel_url=_return(return_url, row.id, "cancelled"),
        expires_at=int(max(row.expires_at.timestamp(), time.time() + 31 * 60)),
    )
    try:
        if getattr(adapter, "offline", False):
            if not settings.PAYMENTS_FAKE_PROVIDER_ENABLED:
                raise ProviderFailure("unavailable")
            result = adapter.offline_checkout(request)  # type: ignore[attr-defined]
            reference = result.reference
            url = _test_page(reference, return_url, row.id)
        else:
            from workeasy360.commercial.payments import credentials, transport

            api_key = credentials.api_key(
                provider.credential_ref, adapter=provider.adapter
            )
            call = adapter.checkout_call(request, api_key=api_key)
            del api_key
            response = transport.send(adapter, call)
            result = adapter.parse_checkout(response.status, response.body)
            reference, url = result.reference, result.checkout_url
    except (
        Exception
    ) as exc:  # a provider or credential problem: say so, keep nothing open
        category = exc.category if isinstance(exc, ProviderFailure) else "unavailable"
        _model().objects.filter(id=row.id, status="pending").update(
            status="failed",
            failure_code=f"checkout_{category}"[:64],
            completed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        metrics.increment("payments_checkouts_failed_total", category=category)
        logger.warning("payments.partner_fee.checkout_failed", category=category)
        raise PaymentValidationError(
            "The payment page could not be opened. Try again in a moment, or pay "
            "offline and enter the transfer's reference.",
            field="payment_method",
        ) from None

    _model().objects.filter(id=row.id, status="pending").update(
        status="awaiting_payment",
        provider_reference=reference[:128],
        checkout_url=url,
        updated_at=timezone.now(),
    )
    metrics.increment("payments_checkouts_created_total", provider=provider.key)
    return view(_model().objects.get(id=row.id))


def _return(base: str, payment_id: UUID, outcome: str) -> str:
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}payment={payment_id}&outcome={outcome}"


def _test_page(reference: str, return_url: str, payment_id: UUID) -> str:
    """The development test provider's "hosted page": the platform's own. No
    organization - the page completes a partner fee by its reference."""
    from urllib.parse import quote

    back = quote(_return(return_url, payment_id, "success"), safe="")
    return f"{portal_links.app_url()}/billing/test-checkout#reference={reference}&org=&return={back}"


# --- the provider's callback --------------------------------------------------------


def apply_webhook(provider: Any, event: WebhookEvent, digest: str) -> str | None:
    """Apply a verified callback that names no organization, if it is about a
    partner fee; ``None`` when it is not (the caller records it as unmatched).
    Exactly once per event; a success pays only the amount and currency asked."""
    from workeasy360.commercial.payments.models import PaymentWebhookEvent

    row = _find(provider.key, event)
    if row is None:
        return None
    paid: tuple[str, UUID, UUID] | None = None
    with transaction.atomic():
        record = PaymentWebhookEvent(
            provider_key=provider.key,
            event_id=event.event_id[:128],
            kind=event.kind,
            payload_digest=digest,
            outcome="applied",
            attempt_id=row.id,
            amount_minor=event.amount_minor if event.kind == PAYMENT_SUCCEEDED else None,
            currency=event.currency[:3] if event.kind == PAYMENT_SUCCEEDED else "",
        )
        try:
            with transaction.atomic():
                record.save()
        except IntegrityError:
            metrics.increment("payments_webhooks_total", outcome="duplicate")
            return "duplicate"
        row = _model().objects.select_for_update().get(id=row.id)
        outcome = "applied"
        if event.kind == PAYMENT_SUCCEEDED:
            if event.amount_minor != row.amount_minor or event.currency != row.currency:
                outcome = "mismatch"
                logger.warning("payments.partner_fee.mismatch", payment_id=str(row.id))
            elif row.status != "succeeded":
                # A success wins over a failure or an expiry that came first.
                row.status = "succeeded"
                row.provider_payment_id = event.payment_id[:128]
                row.checkout_url = ""
                row.failure_code = ""
                row.completed_at = timezone.now()
                row.save()
                paid = (row.purpose, row.subject_id, row.id)
        elif event.kind in (PAYMENT_FAILED, CHECKOUT_EXPIRED):
            if row.status in _IN_FLIGHT:  # a failure never undoes a success
                row.status = "failed" if event.kind == PAYMENT_FAILED else "expired"
                row.failure_code = (
                    "provider_declined"
                    if event.kind == PAYMENT_FAILED
                    else "checkout_expired"
                )
                row.checkout_url = ""
                row.completed_at = timezone.now()
                row.save()
        else:
            outcome = "ignored"
        if outcome != "applied":
            record.outcome = outcome
            record.save(update_fields=["outcome"])
        if paid is not None:
            purpose, subject_id, payment_id = paid
            transaction.on_commit(lambda: _paid(purpose, subject_id, payment_id))
    metrics.increment("payments_webhooks_total", outcome=outcome)
    return outcome


def _find(provider_key: str, event: WebhookEvent) -> Any:
    """By the provider's own reference first (unforgeable once signed), then
    by the attempt id the platform attached - always for this provider."""
    rows = _model().objects.filter(provider_key=provider_key)
    if event.reference:
        row = rows.filter(provider_reference=event.reference[:128]).first()
        if row is not None:
            return row
    try:
        attempt_id = UUID(event.attempt_id) if event.attempt_id else None
    except ValueError:
        attempt_id = None
    return rows.filter(id=attempt_id).first() if attempt_id is not None else None


def _paid(purpose: str, subject_id: UUID, payment_id: UUID) -> None:
    handler = _HANDLERS.get(purpose)
    if handler is None:
        logger.error("payments.partner_fee.no_handler", purpose=purpose)
        return
    try:
        handler(subject_id, payment_id)
    except Exception as exc:  # the payment stands; staff see it as paid
        logger.error(
            "payments.partner_fee.handler_failed",
            purpose=purpose,
            payment_id=str(payment_id),
            error_type=type(exc).__name__,
        )

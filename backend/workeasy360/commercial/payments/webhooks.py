"""Provider webhooks: verify, de-duplicate, apply - once.

The only thing that marks an invoice paid online. The browser coming back from
the hosted page proves nothing (anyone can type a return URL); a callback
carrying the provider's signature does.

1. **Verify** before reading anything: the provider must exist, the body must
   be within ``PAYMENT_WEBHOOK_MAX_BYTES``, and the adapter must accept the
   signature under the provider's webhook secret (read from the secret manager
   per call). Every failure is the same ``webhook_rejected`` - which check
   failed is logged for operators, never told to the caller.
2. **Parse** into a platform event: which attempt (by the provider's reference
   and the metadata the platform attached), which organization, what happened.
3. **De-duplicate and apply in one transaction**, inside that organization's
   database scope: insert the event row (``(provider, event_id)`` is unique -
   a redelivery conflicts and changes nothing), then apply the outcome under
   row locks. A crash rolls both back and the provider's retry applies it.
4. **After commit**: move the subscription out of dunning, send the receipt.

Out-of-order delivery is safe: a success always wins over a failure or an
expiry that arrives later, and a failure never undoes a success.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction

from workeasy360.commercial.payments import settlement
from workeasy360.commercial.payments.invoicing import organization_scope
from workeasy360.commercial.payments.providers import adapter_for
from workeasy360.commercial.payments.providers.contract import (
    CHECKOUT_EXPIRED,
    IGNORED,
    PAYMENT_FAILED,
    PAYMENT_SUCCEEDED,
    REFUND_FAILED,
    REFUND_SUCCEEDED,
    InvalidWebhook,
    WebhookEvent,
)
from workeasy360.contracts.errors import WebhookRejected
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


def _reject(provider_key: str, reason: str) -> WebhookRejected:
    metrics.increment("payments_webhooks_rejected_total", reason=reason)
    logger.warning("payments.webhook.rejected", provider=provider_key[:32], reason=reason)
    return WebhookRejected("The webhook could not be verified.")


def process(provider_key: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    from workeasy360.commercial.payments import credentials
    from workeasy360.commercial.payments.models import PaymentProvider, ProviderAdapter

    if len(body) > int(settings.PAYMENT_WEBHOOK_MAX_BYTES):
        raise _reject(provider_key, "too_large")
    provider = PaymentProvider.objects.filter(key=provider_key).first()
    adapter = adapter_for(provider.adapter) if provider is not None else None
    if provider is None or adapter is None:
        raise _reject(provider_key, "unknown_provider")
    if (
        provider.adapter == ProviderAdapter.FAKE
        and not settings.PAYMENTS_FAKE_PROVIDER_ENABLED
    ):
        raise _reject(provider_key, "test_provider_disabled")
    lowered = {k.lower(): v for k, v in headers.items()}
    try:
        secret = credentials.webhook_secret(
            provider.webhook_secret_ref, adapter=provider.adapter
        )
    except credentials.CredentialUnavailable:
        raise _reject(provider_key, "secret_unavailable") from None
    verified = adapter.verify_webhook(lowered, body, secret=secret, now=time.time())
    del secret
    if not verified:
        raise _reject(provider_key, "bad_signature")
    try:
        event = adapter.parse_webhook(lowered, body)
    except InvalidWebhook:
        raise _reject(provider_key, "unparseable") from None

    digest = hashlib.sha256(body).hexdigest()
    organization_id = _uuid(event.organization_id)
    if event.kind == IGNORED or organization_id is None:
        outcome = _record_unscoped(provider_key, event, digest, organization_id)
        return {"received": True, "outcome": outcome}
    return _apply(provider, event, digest, organization_id)


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw) if raw else None
    except ValueError:
        return None


def _event_row(provider_key: str, event: WebhookEvent, digest: str, **extra: Any) -> Any:
    from workeasy360.commercial.payments.models import PaymentWebhookEvent

    return PaymentWebhookEvent(
        provider_key=provider_key,
        event_id=event.event_id[:128],
        kind=event.kind,
        payload_digest=digest,
        amount_minor=event.amount_minor if event.kind == PAYMENT_SUCCEEDED else None,
        currency=event.currency[:3] if event.kind == PAYMENT_SUCCEEDED else "",
        **extra,
    )


def _record_unscoped(
    provider_key: str, event: WebhookEvent, digest: str, organization_id: UUID | None
) -> str:
    outcome = "ignored" if event.kind == IGNORED else "unmatched"
    try:
        with transaction.atomic():
            _event_row(
                provider_key,
                event,
                digest,
                outcome=outcome,
                organization_id=organization_id,
            ).save()
    except IntegrityError:
        return "duplicate"
    metrics.increment("payments_webhooks_total", outcome=outcome)
    return outcome


def _apply(
    provider: Any, event: WebhookEvent, digest: str, organization_id: UUID
) -> dict[str, Any]:
    from workeasy360.platform.organizations.selectors import organization_status

    if organization_status(organization_id) is None:
        return {
            "received": True,
            "outcome": _record_unscoped(provider.key, event, digest, None),
        }
    after = settlement.AfterCommit()
    with (
        organization_scope(
            organization_id, reason="Apply a verified payment provider webhook"
        ),
        transaction.atomic(),
    ):
        row = _event_row(
            provider.key,
            event,
            digest,
            outcome="applied",
            organization_id=organization_id,
        )
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            metrics.increment("payments_webhooks_total", outcome="duplicate")
            return {"received": True, "outcome": "duplicate"}
        outcome, attempt_id = _dispatch(provider, event, organization_id, after)
        row.outcome = {
            settlement.APPLIED: "applied",
            settlement.DUPLICATE: "applied",
            settlement.MISMATCH: "mismatch",
            settlement.UNMATCHED: "unmatched",
        }[outcome]
        row.attempt_id = attempt_id
        row.save(update_fields=["outcome", "attempt_id"])
    after.run(organization_id)
    metrics.increment("payments_webhooks_total", outcome=row.outcome)
    return {"received": True, "outcome": row.outcome}


def _dispatch(
    provider: Any,
    event: WebhookEvent,
    organization_id: UUID,
    after: settlement.AfterCommit,
) -> tuple[str, UUID | None]:
    from workeasy360.commercial.payments.models import Refund

    if event.kind in (REFUND_SUCCEEDED, REFUND_FAILED):
        refund_id = _uuid(event.refund_id)
        refund = (
            Refund.unscoped.select_for_update()
            .filter(organization_id=organization_id, id=refund_id)
            .first()
            if refund_id is not None
            else None
        )
        if refund is None:
            return settlement.UNMATCHED, None
        return (
            settlement.record_refund(
                refund,
                succeeded=event.kind == REFUND_SUCCEEDED,
                reference=event.refund_reference,
                failure_code="provider_failed",
            ),
            refund.attempt_id,
        )

    attempt = _attempt_for(provider, event, organization_id)
    if attempt is None:
        return settlement.UNMATCHED, None
    if event.kind == PAYMENT_SUCCEEDED:
        return (
            settlement.record_success(
                attempt,
                amount_minor=event.amount_minor,
                currency=event.currency,
                payment_id=event.payment_id,
                after=after,
            ),
            attempt.id,
        )
    if event.kind == PAYMENT_FAILED:
        return (
            settlement.record_failure(
                attempt, status="failed", failure_code="provider_declined", after=after
            ),
            attempt.id,
        )
    if event.kind == CHECKOUT_EXPIRED:
        return (
            settlement.record_failure(
                attempt, status="expired", failure_code="checkout_expired", after=after
            ),
            attempt.id,
        )
    return settlement.UNMATCHED, attempt.id


def _attempt_for(provider: Any, event: WebhookEvent, organization_id: UUID) -> Any:
    """The attempt this event is about: by the provider's own reference first
    (unforgeable once signed), then by the metadata id - always within the
    event's organization and always for this provider."""
    if event.reference:
        attempt = settlement.lock_attempt(
            organization_id, provider_key=provider.key, provider_reference=event.reference
        )
        if attempt is not None:
            return attempt
    attempt_id = _uuid(event.attempt_id)
    if attempt_id is None:
        return None
    return settlement.lock_attempt(
        organization_id, id=attempt_id, provider_key=provider.key
    )

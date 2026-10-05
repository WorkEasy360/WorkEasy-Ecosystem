"""Stripe: Checkout Sessions (hosted), refunds, signed webhooks.

- Checkout: ``POST /v1/checkout/sessions`` (form-encoded), ``mode=payment`` with
  one inline ``price_data`` line for the invoice total. Metadata carries the
  platform's attempt and organization ids (on the session *and* the payment
  intent). ``Idempotency-Key`` is the attempt id, so a retried call returns the
  same session instead of creating a second one.
- Webhooks: ``Stripe-Signature: t=<ts>,v1=<hex>[,v1=...]``; HMAC-SHA256 with the
  endpoint's signing secret over ``"<ts>." + raw body``; only ``v1`` counts
  (downgrade protection); the timestamp must be within five minutes.
- Refunds: ``POST /v1/refunds`` against the payment intent, idempotent by the
  platform's refund id.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from typing import Any, Final

from workeasy360.commercial.payments.providers.contract import (
    CHECKOUT_EXPIRED,
    IGNORED,
    META_ATTEMPT,
    META_ORGANIZATION,
    META_REFUND,
    PAYMENT_FAILED,
    PAYMENT_SUCCEEDED,
    REFUND_FAILED,
    REFUND_SUCCEEDED,
    CheckoutRequest,
    CheckoutResult,
    HttpCall,
    InvalidWebhook,
    ProviderFailure,
    RefundRequest,
    RefundResult,
    WebhookEvent,
    as_int,
    failure_for_status,
    load_json,
    load_webhook_json,
    text,
)

#: Stripe's own libraries default to five minutes.
SIGNATURE_TOLERANCE_S: Final = 300


def _headers(api_key: str, idempotency_key: str) -> tuple[tuple[str, str], ...]:
    return (
        ("Authorization", f"Bearer {api_key}"),
        ("Content-Type", "application/x-www-form-urlencoded"),
        ("Idempotency-Key", idempotency_key),
    )


class StripeCheckoutAdapter:
    key = "stripe"
    default_endpoint = "https://api.stripe.com"
    idempotent_refunds = True

    def checkout_call(self, request: CheckoutRequest, *, api_key: str) -> HttpCall:
        params: list[tuple[str, str]] = [
            ("mode", "payment"),
            ("success_url", request.success_url),
            ("cancel_url", request.cancel_url),
            ("client_reference_id", request.attempt_id),
            ("line_items[0][quantity]", "1"),
            ("line_items[0][price_data][currency]", request.currency.lower()),
            ("line_items[0][price_data][unit_amount]", str(request.amount_minor)),
            ("line_items[0][price_data][product_data][name]", request.description[:250]),
            (f"metadata[{META_ATTEMPT}]", request.attempt_id),
            (f"metadata[{META_ORGANIZATION}]", request.organization_id),
            (f"payment_intent_data[metadata][{META_ATTEMPT}]", request.attempt_id),
            (
                f"payment_intent_data[metadata][{META_ORGANIZATION}]",
                request.organization_id,
            ),
            ("expires_at", str(request.expires_at)),
        ]
        if request.customer_email:
            params.append(("customer_email", request.customer_email))
        return HttpCall(
            method="POST",
            path="/v1/checkout/sessions",
            headers=_headers(api_key, f"w360-checkout-{request.attempt_id}"),
            body=urllib.parse.urlencode(params).encode("ascii"),
        )

    def parse_checkout(self, status: int, body: bytes) -> CheckoutResult:
        if not 200 <= status < 300:
            raise failure_for_status(status)
        data = load_json(body)
        reference, url = text(data.get("id")), data.get("url")
        if not reference or not isinstance(url, str) or not url.startswith("https://"):
            raise ProviderFailure("invalid_response")
        return CheckoutResult(reference=reference, checkout_url=url)

    def verify_webhook(
        self, headers: dict[str, str], body: bytes, *, secret: str, now: float
    ) -> bool:
        header = headers.get("stripe-signature", "")
        timestamp = ""
        signatures: list[str] = []
        for element in header.split(","):
            prefix, _, value = element.strip().partition("=")
            if prefix == "t":
                timestamp = value
            elif prefix == "v1":
                signatures.append(value)
        if not timestamp.isdigit() or not signatures or not secret:
            return False
        if abs(now - int(timestamp)) > SIGNATURE_TOLERANCE_S:
            return False
        expected = hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        return any(hmac.compare_digest(expected, candidate) for candidate in signatures)

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> WebhookEvent:
        event = load_webhook_json(body)
        event_id = text(event.get("id"))
        kind = text(event.get("type"), 64)
        data = event.get("data")
        obj: Any = data.get("object") if isinstance(data, dict) else None
        if not event_id or not isinstance(obj, dict):
            raise InvalidWebhook("missing event id or object")
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}

        if kind.startswith("checkout.session."):
            outcome = {
                "checkout.session.completed": PAYMENT_SUCCEEDED
                if obj.get("payment_status") == "paid"
                else IGNORED,
                "checkout.session.async_payment_succeeded": PAYMENT_SUCCEEDED,
                "checkout.session.async_payment_failed": PAYMENT_FAILED,
                "checkout.session.expired": CHECKOUT_EXPIRED,
            }.get(kind, IGNORED)
            return WebhookEvent(
                event_id=event_id,
                kind=outcome,
                reference=text(obj.get("id")),
                payment_id=text(obj.get("payment_intent")),
                amount_minor=as_int(obj.get("amount_total")),
                currency=text(obj.get("currency"), 3).upper(),
                attempt_id=text(metadata.get(META_ATTEMPT)),
                organization_id=text(metadata.get(META_ORGANIZATION)),
            )
        if kind in ("refund.created", "refund.updated", "refund.failed"):
            status = obj.get("status")
            outcome = (
                REFUND_SUCCEEDED
                if status == "succeeded"
                else REFUND_FAILED
                if status in ("failed", "canceled") or kind == "refund.failed"
                else IGNORED
            )
            return WebhookEvent(
                event_id=event_id,
                kind=outcome,
                payment_id=text(obj.get("payment_intent")),
                amount_minor=as_int(obj.get("amount")),
                currency=text(obj.get("currency"), 3).upper(),
                organization_id=text(metadata.get(META_ORGANIZATION)),
                refund_id=text(metadata.get(META_REFUND)),
                refund_reference=text(obj.get("id")),
            )
        return WebhookEvent(event_id=event_id, kind=IGNORED)

    def refund_call(self, request: RefundRequest, *, api_key: str) -> HttpCall:
        params = [
            ("payment_intent", request.payment_id),
            ("amount", str(request.amount_minor)),
            (f"metadata[{META_REFUND}]", request.refund_id),
            (f"metadata[{META_ORGANIZATION}]", request.organization_id),
        ]
        return HttpCall(
            method="POST",
            path="/v1/refunds",
            headers=_headers(api_key, f"w360-refund-{request.refund_id}"),
            body=urllib.parse.urlencode(params).encode("ascii"),
        )

    def parse_refund(self, status: int, body: bytes) -> RefundResult:
        if not 200 <= status < 300:
            raise failure_for_status(status)
        data = load_json(body)
        reference = text(data.get("id"))
        if not reference:
            raise ProviderFailure("invalid_response")
        state = data.get("status")
        return RefundResult(
            reference=reference,
            status="succeeded"
            if state == "succeeded"
            else "failed"
            if state in ("failed", "canceled")
            else "pending",
        )

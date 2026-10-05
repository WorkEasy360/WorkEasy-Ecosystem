"""Razorpay: Payment Links (hosted), refunds, signed webhooks.

- Checkout: ``POST /v1/payment_links`` (JSON, HTTP Basic ``key_id:key_secret`` -
  the credential reference holds that pair). ``reference_id`` is the attempt id
  (Razorpay refuses a second link with the same reference, which is the
  idempotency this API offers); ``notes`` carry the attempt and organization
  ids; Razorpay's own SMS/email reminders are off (the platform sends its own).
- Webhooks: ``X-Razorpay-Signature`` is the hex HMAC-SHA256 of the raw body
  under the webhook secret. There is no timestamp to bound replays, so the
  ``x-razorpay-event-id`` de-duplication is what makes a replay harmless.
- Refunds: ``POST /v1/payments/<payment id>/refund``. Not idempotent on
  Razorpay's side, so a refund whose outcome is unknown is never re-sent
  automatically (``idempotent_refunds = False``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from workeasy360.commercial.payments.providers.contract import (
    CHECKOUT_EXPIRED,
    IGNORED,
    META_ATTEMPT,
    META_ORGANIZATION,
    META_REFUND,
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


def _headers(api_key: str) -> tuple[tuple[str, str], ...]:
    token = base64.b64encode(api_key.encode("utf-8")).decode("ascii")
    return (("Authorization", f"Basic {token}"), ("Content-Type", "application/json"))


def _entity(payload: Any, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    wrapper = payload.get(name)
    entity = wrapper.get("entity") if isinstance(wrapper, dict) else None
    return entity if isinstance(entity, dict) else {}


class RazorpayPaymentLinkAdapter:
    key = "razorpay"
    default_endpoint = "https://api.razorpay.com"
    idempotent_refunds = False

    def checkout_call(self, request: CheckoutRequest, *, api_key: str) -> HttpCall:
        body: dict[str, Any] = {
            "amount": request.amount_minor,
            "currency": request.currency.upper(),
            "accept_partial": False,
            "expire_by": request.expires_at,
            "reference_id": request.attempt_id.replace("-", "")[:40],
            "description": request.description[:2048],
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": {
                META_ATTEMPT: request.attempt_id,
                META_ORGANIZATION: request.organization_id,
            },
            "callback_url": request.success_url,
            "callback_method": "get",
        }
        if request.customer_email:
            body["customer"] = {"email": request.customer_email}
        return HttpCall(
            method="POST",
            path="/v1/payment_links",
            headers=_headers(api_key),
            body=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        )

    def parse_checkout(self, status: int, body: bytes) -> CheckoutResult:
        if not 200 <= status < 300:
            raise failure_for_status(status)
        data = load_json(body)
        reference, url = text(data.get("id")), data.get("short_url")
        if not reference or not isinstance(url, str) or not url.startswith("https://"):
            raise ProviderFailure("invalid_response")
        return CheckoutResult(reference=reference, checkout_url=url)

    def verify_webhook(
        self, headers: dict[str, str], body: bytes, *, secret: str, now: float
    ) -> bool:
        signature = headers.get("x-razorpay-signature", "")
        if not signature or not secret:
            return False
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> WebhookEvent:
        event = load_webhook_json(body)
        kind = text(event.get("event"), 64)
        if not kind:
            raise InvalidWebhook("missing event type")
        event_id = text(headers.get("x-razorpay-event-id")) or (
            "sha256:" + hashlib.sha256(body).hexdigest()
        )
        payload = event.get("payload")
        if kind in (
            "payment_link.paid",
            "payment_link.expired",
            "payment_link.cancelled",
        ):
            link = _entity(payload, "payment_link")
            payment = _entity(payload, "payment")
            notes = link.get("notes") if isinstance(link.get("notes"), dict) else {}
            return WebhookEvent(
                event_id=event_id,
                kind=PAYMENT_SUCCEEDED
                if kind == "payment_link.paid"
                else CHECKOUT_EXPIRED,
                reference=text(link.get("id")),
                payment_id=text(payment.get("id")),
                amount_minor=as_int(payment.get("amount"))
                if payment
                else as_int(link.get("amount")),
                currency=text(payment.get("currency") or link.get("currency"), 3).upper(),
                attempt_id=text(notes.get(META_ATTEMPT)),
                organization_id=text(notes.get(META_ORGANIZATION)),
            )
        if kind in ("refund.processed", "refund.failed"):
            refund = _entity(payload, "refund")
            notes = refund.get("notes") if isinstance(refund.get("notes"), dict) else {}
            return WebhookEvent(
                event_id=event_id,
                kind=REFUND_SUCCEEDED if kind == "refund.processed" else REFUND_FAILED,
                payment_id=text(refund.get("payment_id")),
                amount_minor=as_int(refund.get("amount")),
                currency=text(refund.get("currency"), 3).upper(),
                organization_id=text(notes.get(META_ORGANIZATION)),
                refund_id=text(notes.get(META_REFUND)),
                refund_reference=text(refund.get("id")),
            )
        return WebhookEvent(event_id=event_id, kind=IGNORED)

    def refund_call(self, request: RefundRequest, *, api_key: str) -> HttpCall:
        body = {
            "amount": request.amount_minor,
            "notes": {
                META_REFUND: request.refund_id,
                META_ORGANIZATION: request.organization_id,
            },
        }
        payment = "".join(c for c in request.payment_id if c.isalnum() or c == "_")
        return HttpCall(
            method="POST",
            path=f"/v1/payments/{payment}/refund",
            headers=_headers(api_key),
            body=json.dumps(body, separators=(",", ":")).encode("utf-8"),
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
            if state == "processed"
            else "failed"
            if state == "failed"
            else "pending",
        )

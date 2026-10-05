"""The development test provider: the full payment flow with no network.

A "hosted page" served by the platform's own frontend, where a developer
chooses the outcome; the page reports it through the same signed-webhook path
a real provider uses (``webhooks.process``), so development exercises the real
pipeline. **Refused unless ``PAYMENTS_FAKE_PROVIDER_ENABLED``**, which the
production settings pin off - a provider whose "payment" is a button press can
never collect for a real invoice.

Signature: hex HMAC-SHA256 of the raw body under the provider's webhook
secret, in ``X-W360-Test-Signature``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from workeasy360.commercial.payments.providers.contract import (
    CHECKOUT_EXPIRED,
    IGNORED,
    PAYMENT_FAILED,
    PAYMENT_SUCCEEDED,
    REFUND_SUCCEEDED,
    CheckoutRequest,
    CheckoutResult,
    HttpCall,
    InvalidWebhook,
    RefundRequest,
    RefundResult,
    WebhookEvent,
    as_int,
    load_webhook_json,
    text,
)

SIGNATURE_HEADER = "x-w360-test-signature"

_KINDS = {
    "payment.succeeded": PAYMENT_SUCCEEDED,
    "payment.failed": PAYMENT_FAILED,
    "checkout.expired": CHECKOUT_EXPIRED,
    "refund.succeeded": REFUND_SUCCEEDED,
}


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def event_body(**fields: Any) -> bytes:
    return json.dumps(fields, separators=(",", ":"), sort_keys=True).encode("utf-8")


class FakeAdapter:
    key = "fake"
    default_endpoint = ""
    idempotent_refunds = True
    #: No request ever leaves the process.
    offline = True

    def checkout_call(self, request: CheckoutRequest, *, api_key: str) -> HttpCall:
        raise NotImplementedError("The test provider makes no network calls.")

    def parse_checkout(self, status: int, body: bytes) -> CheckoutResult:
        raise NotImplementedError("The test provider makes no network calls.")

    def offline_checkout(self, request: CheckoutRequest) -> CheckoutResult:
        """The page is the platform's own (``checkout.hosted_page_url``); only
        the reference is decided here."""
        return CheckoutResult(
            reference=f"fake_cs_{request.attempt_id.replace('-', '')}", checkout_url=""
        )

    def verify_webhook(
        self, headers: dict[str, str], body: bytes, *, secret: str, now: float
    ) -> bool:
        signature = headers.get(SIGNATURE_HEADER, "")
        return bool(signature and secret) and hmac.compare_digest(
            sign(body, secret), signature
        )

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> WebhookEvent:
        event = load_webhook_json(body)
        event_id = text(event.get("id"))
        if not event_id:
            raise InvalidWebhook("missing event id")
        return WebhookEvent(
            event_id=event_id,
            kind=_KINDS.get(text(event.get("type"), 32), IGNORED),
            reference=text(event.get("reference")),
            payment_id=text(event.get("payment_id")),
            amount_minor=as_int(event.get("amount_minor")),
            currency=text(event.get("currency"), 3).upper(),
            attempt_id=text(event.get("attempt_id")),
            organization_id=text(event.get("organization_id")),
            refund_id=text(event.get("refund_id")),
            refund_reference=text(event.get("refund_reference")),
        )

    def refund_call(self, request: RefundRequest, *, api_key: str) -> HttpCall:
        raise NotImplementedError("The test provider makes no network calls.")

    def parse_refund(self, status: int, body: bytes) -> RefundResult:
        raise NotImplementedError("The test provider makes no network calls.")

    def offline_refund(self, request: RefundRequest) -> RefundResult:
        return RefundResult(
            reference=f"fake_rf_{request.refund_id.replace('-', '')}", status="succeeded"
        )

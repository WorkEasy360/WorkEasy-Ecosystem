"""The payment provider contract: what an adapter builds, parses and verifies.

Pure standard library (``.importlinter``: ``payment-providers-are-sandboxed``).
An adapter never opens a socket, reads a setting, touches the database or looks
up a credential: the platform hands it one call's credential and sends what it
builds through ``integration/egress``.

**Hosted checkout only.** Every adapter creates a provider-hosted payment page
and returns its address. The payer's card (or UPI, or bank) details are typed
into the provider's page and never pass through Workeasy360 - so there is no
card field anywhere in this contract.

Failure categories (bounded, safe as metric labels):

=================  =========  ==================================================
category           retryable  typical cause
=================  =========  ==================================================
``rate_limited``   yes        429
``server_error``   yes        5xx
``connect_failed`` yes        DNS/TCP/TLS - nothing reached the provider
``timeout``        no         the provider may have acted; do not act twice
``auth_failed``    no         401/403 - a credential problem, an operator's
``bad_request``    no         400/404/422 - the request itself is wrong
``invalid_response`` no       an unparseable 2xx
``unavailable``    no         no credential configured, endpoint refused
=================  =========  ==================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final, Protocol

RETRYABLE: Final = frozenset({"rate_limited", "server_error", "connect_failed"})

# Webhook event kinds an adapter maps provider events onto.
PAYMENT_SUCCEEDED: Final = "payment_succeeded"
PAYMENT_FAILED: Final = "payment_failed"
CHECKOUT_EXPIRED: Final = "checkout_expired"
REFUND_SUCCEEDED: Final = "refund_succeeded"
REFUND_FAILED: Final = "refund_failed"
IGNORED: Final = "ignored"

#: Metadata keys the platform puts on every provider object and reads back
#: from every webhook - how a callback finds its organization and attempt.
META_ATTEMPT: Final = "w360_attempt"
META_ORGANIZATION: Final = "w360_org"
META_REFUND: Final = "w360_refund"


class ProviderFailure(Exception):
    """A provider call did not produce a usable answer. Never carries the
    provider's response body (it can echo payer details)."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE


class InvalidWebhook(Exception):
    """A callback that failed verification or could not be parsed."""


@dataclass(frozen=True, slots=True)
class HttpCall:
    method: str
    path: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass(frozen=True, slots=True)
class CheckoutRequest:
    attempt_id: str
    organization_id: str
    invoice_number: str
    description: str
    amount_minor: int
    currency: str
    customer_email: str
    success_url: str
    cancel_url: str
    #: Epoch seconds after which the hosted page must stop accepting payment.
    expires_at: int


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    reference: str
    checkout_url: str


@dataclass(frozen=True, slots=True)
class RefundRequest:
    refund_id: str
    organization_id: str
    payment_id: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class RefundResult:
    reference: str
    #: ``succeeded`` | ``pending`` | ``failed``
    status: str


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    event_id: str
    kind: str
    reference: str = ""
    payment_id: str = ""
    amount_minor: int | None = None
    currency: str = ""
    attempt_id: str = ""
    organization_id: str = ""
    refund_id: str = ""
    refund_reference: str = ""


class Adapter(Protocol):
    key: str
    default_endpoint: str
    #: Whether a refund call carries an idempotency key the provider honours,
    #: so a refund whose outcome is unknown may be re-sent safely.
    idempotent_refunds: bool

    def checkout_call(self, request: CheckoutRequest, *, api_key: str) -> HttpCall: ...

    def parse_checkout(self, status: int, body: bytes) -> CheckoutResult: ...

    def verify_webhook(
        self, headers: dict[str, str], body: bytes, *, secret: str, now: float
    ) -> bool: ...

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> WebhookEvent: ...

    def refund_call(self, request: RefundRequest, *, api_key: str) -> HttpCall: ...

    def parse_refund(self, status: int, body: bytes) -> RefundResult: ...


def failure_for_status(status: int) -> ProviderFailure:
    if status == 429:
        return ProviderFailure("rate_limited")
    if status >= 500:
        return ProviderFailure("server_error")
    if status in (401, 403):
        return ProviderFailure("auth_failed")
    return ProviderFailure("bad_request")


def load_json(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderFailure("invalid_response") from exc
    if not isinstance(parsed, dict):
        raise ProviderFailure("invalid_response")
    return parsed


def load_webhook_json(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidWebhook("unparseable") from exc
    if not isinstance(parsed, dict):
        raise InvalidWebhook("not an object")
    return parsed


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def text(value: Any, limit: int = 128) -> str:
    if isinstance(value, bool) or not isinstance(value, str | int) or value == "":
        return ""
    return str(value)[:limit]

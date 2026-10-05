"""The one place an AI provider request leaves the platform.

Every provider call - generation and embedding, every vendor - goes through
``send`` and from there through ``integration/egress``: the endpoint is
resolved once and pinned, TLS is verified against the hostname, redirects are
never followed, the response is read to a byte cap and the whole exchange
fits inside a deadline (Tasks 49, 103; ADR-025). The architecture test that
confines ``egress.client`` to its callers names this module and nothing else
in ``intelligence``.

**The endpoint is never caller-supplied** (Task 104). It is the adapter's
built-in vendor endpoint, or - outside production, where the override map is
pinned empty - a per-adapter override from settings for development and the
test suite's loopback provider. No request field, product manifest or staff
setting can name a URL.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from django.conf import settings

from workeasy360.integration.egress import client as egress
from workeasy360.integration.egress.policy import EgressRejected
from workeasy360.intelligence.ai.providers.contract import (
    Adapter,
    HttpCall,
    ProviderFailure,
)
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: Egress failure categories where nothing reached the provider, so a retry
#: cannot double-bill. Everything after the request was written is a
#: ``timeout``: the provider may have processed it.
_BEFORE_SEND = frozenset(
    {"dns_failure", "dns_timeout", "connect_failed", "connect_timeout", "tls_error"}
)
_REQUEST_ERRORS = frozenset({"request_too_large", "request_invalid"})


class Deadline:
    """One overall budget shared by every attempt, retry and fallback of one
    operation. Nothing waits past it (Task 49)."""

    def __init__(self, seconds: float) -> None:
        self._ends = time.monotonic() + max(0.0, seconds)

    def remaining(self) -> float:
        return self._ends - time.monotonic()

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0.05


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    duration_ms: int


def endpoint_for(adapter: Adapter) -> str:
    return str(
        settings.AI_PROVIDER_ENDPOINTS.get(adapter.key, adapter.default_endpoint)
    ).rstrip("/")


def send(
    adapter: Adapter, call: HttpCall, *, deadline: Deadline, max_response_bytes: int
) -> Response:
    """Send one provider request, or raise ``ProviderFailure``."""
    remaining = deadline.remaining()
    if remaining <= 0.05:
        raise ProviderFailure("timeout")
    timeouts = egress.Timeouts(
        connect=min(float(settings.AI_CONNECT_TIMEOUT_S), remaining),
        read=remaining,
        total=remaining,
        dns=min(float(settings.AI_DNS_TIMEOUT_S), remaining),
    )
    request = egress.OutboundRequest(
        url=endpoint_for(adapter) + call.path,
        body=call.body,
        headers=call.headers,
        method="POST",
    )
    try:
        response = egress.send(
            request,
            timeouts=timeouts,
            max_response_bytes=max_response_bytes,
            max_request_bytes=int(settings.AI_MAX_REQUEST_BYTES),
        )
    except EgressRejected as exc:
        # The configured endpoint itself is refused - an operator problem,
        # never something a retry or another request could fix.
        logger.error(
            "ai.provider.endpoint_refused", adapter=adapter.key, reason=exc.reason
        )
        raise ProviderFailure("unavailable") from None
    except egress.EgressFailure as exc:
        if exc.category in _BEFORE_SEND:
            raise ProviderFailure("connect_failed") from None
        if exc.category in _REQUEST_ERRORS:
            raise ProviderFailure("bad_request") from None
        raise ProviderFailure("timeout") from None

    if response.truncated:
        # An answer larger than the cap is not an answer the platform will
        # half-read and pretend is whole.
        raise ProviderFailure("invalid_response")
    return Response(
        status=response.status,
        headers=response.headers,
        body=response.body,
        duration_ms=int(response.duration_s * 1000),
    )

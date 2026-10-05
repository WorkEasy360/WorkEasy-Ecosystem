"""The one place a payment provider request leaves the platform.

Every call goes through ``integration/egress`` - the endpoint resolved once and
pinned, TLS verified against the host, redirects never followed, the response
read to a byte cap, the whole exchange inside a deadline - and **never inside a
database transaction**: callers commit their intent first and record the
outcome in a second, short transaction (E2).

The endpoint is the adapter's built-in vendor URL. ``PAYMENT_PROVIDER_ENDPOINTS``
may override it for development and the test suite's loopback provider; the
production settings pin it empty, so no configuration path - staff, customer or
environment - can point payments at another host.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from workeasy360.commercial.payments.providers.contract import (
    Adapter,
    HttpCall,
    ProviderFailure,
)
from workeasy360.integration.egress import client as egress
from workeasy360.integration.egress.policy import EgressRejected
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Egress failure categories where nothing reached the provider, so a retry
#: cannot create a second payment page or a second refund.
_BEFORE_SEND = frozenset(
    {"dns_failure", "dns_timeout", "connect_failed", "connect_timeout", "tls_error"}
)


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: bytes


def endpoint_for(adapter: Adapter) -> str:
    return str(
        settings.PAYMENT_PROVIDER_ENDPOINTS.get(adapter.key, adapter.default_endpoint)
    ).rstrip("/")


def send(adapter: Adapter, call: HttpCall) -> Response:
    """Send one provider request, or raise ``ProviderFailure``."""
    total = float(settings.PAYMENT_TOTAL_TIMEOUT_S)
    timeouts = egress.Timeouts(
        connect=float(settings.PAYMENT_CONNECT_TIMEOUT_S),
        read=total,
        total=total,
        dns=float(settings.PAYMENT_CONNECT_TIMEOUT_S),
    )
    request = egress.OutboundRequest(
        url=endpoint_for(adapter) + call.path,
        body=call.body,
        headers=call.headers,
        method=call.method,
    )
    try:
        response = egress.send(
            request,
            timeouts=timeouts,
            max_response_bytes=int(settings.PAYMENT_MAX_RESPONSE_BYTES),
            max_request_bytes=int(settings.PAYMENT_MAX_REQUEST_BYTES),
        )
    except EgressRejected as exc:
        logger.error(
            "payments.provider.endpoint_refused", adapter=adapter.key, reason=exc.reason
        )
        raise ProviderFailure("unavailable") from None
    except egress.EgressFailure as exc:
        category = "connect_failed" if exc.category in _BEFORE_SEND else "timeout"
        metrics.increment(
            "payments_provider_calls_total", adapter=adapter.key, outcome=category
        )
        raise ProviderFailure(category) from None
    metrics.increment(
        "payments_provider_calls_total",
        adapter=adapter.key,
        outcome=f"{response.status // 100}xx",
    )
    if response.truncated:
        raise ProviderFailure("invalid_response")
    return Response(status=response.status, body=response.body)

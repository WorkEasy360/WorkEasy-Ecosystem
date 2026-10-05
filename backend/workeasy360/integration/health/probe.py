"""A bounded reachability check of each registered product (Phase 11).

**Why this exists and what it is allowed to do.** A customer's dashboard should
say "temporarily unavailable" when a product's address is not answering, and
staff should see which product it is. It may **never** make a request on a
customer's behalf while their page renders: the dashboard reads the *stored*
result of the last check, and only the scheduler (or a staff member pressing
"check now") makes the network call.

Every call goes through the platform's one outbound HTTP boundary
(``integration/egress``): https only, no internal or metadata address (every
resolved IP is checked, then pinned), no redirect followed, tight timeouts, a
capped response. **TLS is never weakened**: a certificate that does not verify
is reported as ``tls_error`` and the product is ``unreachable`` - the check
does not retry with verification off, and nothing else in the platform does
either.

The result is a *status and a short category*, recorded by
``platform.ecosystem.health`` - not the response. It gates no access and no
readiness probe: an external product being down must not make Workeasy360
unavailable (E-6).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

# Phase 12 (G-32): SoftTimeLimitExceeded subclasses Exception. Swallowed
# here, a pass that outran its budget kept going until the hard kill, with
# no summary, and the organizations enumerated last were never reached.
from celery.exceptions import SoftTimeLimitExceeded

from workeasy360.integration.egress.client import (
    EgressFailure,
    OutboundRequest,
    Timeouts,
    send,
)
from workeasy360.integration.egress.policy import EgressRejected
from workeasy360.platform.ecosystem import health
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

# Not ``platform.ecosystem.models`` directly (F-6): the owning service
# module's own re-export is the contract; the model file is an implementation
# detail this package must not be welded to.
IntegrationHealth = health.IntegrationHealth

#: Tighter than webhook delivery: this is a liveness glance, not a delivery.
PROBE_TIMEOUTS = Timeouts(connect=3.0, read=5.0, total=8.0, dns=2.0)
#: A response body is never used, so almost none of it is read.
MAX_RESPONSE_BYTES = 4096
#: One sweep never checks more than this many products, and never more than
#: ``WORKERS`` at once - a registry that grew unexpectedly cannot turn the
#: probe into a load of its own.
MAX_TARGETS = 100
WORKERS = 8

USER_AGENT = "Workeasy360-Health/1"

PROBES_TOTAL = "ecosystem_health_probes_total"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    product_code: str
    status: IntegrationHealth
    detail: str


def classify(status_code: int) -> tuple[IntegrationHealth, str]:
    """A front door that answers (2xx) or sends the visitor onward (3xx - a
    product that redirects ``/`` to its login page is healthy) is healthy; any
    error status means it answered but is not well."""
    if 200 <= status_code < 400:
        return IntegrationHealth.HEALTHY, f"http_{status_code}"
    return IntegrationHealth.DEGRADED, f"http_{status_code}"


def probe_url(product_code: str, url: str) -> ProbeResult:
    """One GET, under every guarantee in the module docstring. Never raises:
    a failure is a *result*."""
    request = OutboundRequest(
        url=url,
        body=b"",
        method="GET",
        headers=(("user-agent", USER_AGENT), ("accept", "*/*")),
    )
    try:
        response = send(
            request, timeouts=PROBE_TIMEOUTS, max_response_bytes=MAX_RESPONSE_BYTES
        )
    except EgressRejected as exc:
        status, detail = IntegrationHealth.UNREACHABLE, f"rejected_{exc.reason}"
    except EgressFailure as exc:
        status, detail = IntegrationHealth.UNREACHABLE, exc.category
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # a probe must never take the sweep down with it
        logger.error(
            "ecosystem.health.probe_error",
            product_code=product_code,
            error_type=type(exc).__name__,
        )
        status, detail = IntegrationHealth.UNREACHABLE, "probe_error"
    else:
        status, detail = classify(response.status)
    metrics.increment(PROBES_TOTAL, outcome=status.value)
    return ProbeResult(product_code=product_code, status=status, detail=detail)


def check_product(product_code: str) -> ProbeResult | None:
    """Check one product now and record the result. ``None`` when the product
    has no launchable integration to check."""
    target = next(
        (t for t in health.probe_targets() if t.product_code == str(product_code)),
        None,
    )
    if target is None:
        return None
    result = probe_url(target.product_code, target.url)
    health.record_health(result.product_code, status=result.status, detail=result.detail)
    return result


def check_all() -> dict[str, int]:
    """Check every launchable product and record each result.

    The network calls run in a small pool; every database write happens here,
    in the calling thread, after the pool has finished.
    """
    targets = health.probe_targets()[:MAX_TARGETS]
    if not targets:
        return {"checked": 0, "healthy": 0, "degraded": 0, "unreachable": 0, "changed": 0}
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(targets))) as pool:
        results = list(pool.map(lambda t: probe_url(t.product_code, t.url), targets))
    counts = {
        "checked": len(results),
        "healthy": 0,
        "degraded": 0,
        "unreachable": 0,
        "changed": 0,
    }
    for result in results:
        counts[result.status.value] += 1
        if health.record_health(
            result.product_code, status=result.status, detail=result.detail
        ):
            counts["changed"] += 1
    return counts

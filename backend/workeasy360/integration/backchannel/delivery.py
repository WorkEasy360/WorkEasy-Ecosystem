"""POSTing logout tokens to products' ``backchannel_logout_uri`` (ADR-067).

OpenID Connect Back-Channel Logout 1.0 section 2.5: an
``application/x-www-form-urlencoded`` POST of ``logout_token``. The product
answers 200 once the named sessions are gone.

Every call goes through the platform's one outbound HTTP boundary
(``integration/egress``), like the product health probe: https only (loopback
http only where the egress policy allows it, i.e. development), no internal or
metadata address (each resolved IP checked, then pinned), no redirect
followed, tight timeouts, a capped response. TLS is never weakened.

Outcome classes:

* 2xx -> delivered;
* a 4xx other than 408/425/429 -> refused: the product rejected this token,
  and sending the same claims again would be rejected again;
* an egress policy rejection -> refused (the address is not callable);
* 408/425/429/5xx, timeouts, connection failures -> retried on the schedule
  in ``platform.ecosystem.backchannel``, then dead.

The token is minted per attempt and never stored, logged or kept in memory
past the request.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

from workeasy360.integration.egress.client import (
    EgressFailure,
    OutboundRequest,
    Timeouts,
    send,
)
from workeasy360.integration.egress.policy import EgressRejected
from workeasy360.platform.ecosystem import backchannel
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: A logout is a small, fast request; a product that cannot answer in this
#: time is retried, not waited for.
DELIVERY_TIMEOUTS = Timeouts(connect=3.0, read=5.0, total=8.0, dns=2.0)
MAX_RESPONSE_BYTES = 2048
USER_AGENT = "Workeasy360-Logout/1"
#: Deliveries per sweep, and how many run at once.
BATCH = 50
WORKERS = 8
RETRYABLE_4XX = frozenset({408, 425, 429})


def _request(item: backchannel.DueDelivery, token: str) -> OutboundRequest:
    return OutboundRequest(
        url=item.url,
        body=urlencode({"logout_token": token}).encode("ascii"),
        method="POST",
        headers=(
            ("content-type", "application/x-www-form-urlencoded"),
            ("accept", "application/json"),
            ("user-agent", USER_AGENT),
            ("cache-control", "no-store"),
        ),
    )


def _send(request: OutboundRequest) -> tuple[str, object]:
    """Network only - runs on a pool thread and touches no database."""
    try:
        return "response", send(
            request, timeouts=DELIVERY_TIMEOUTS, max_response_bytes=MAX_RESPONSE_BYTES
        )
    except EgressRejected as exc:
        return "rejected", exc
    except EgressFailure as exc:
        return "failure", exc
    except Exception as exc:  # one product must never take the sweep down
        return "error", exc


def _record(item: backchannel.DueDelivery, kind: str, result: object) -> str:
    """Classify one attempt and record it. Runs on the calling thread."""
    if kind == "rejected":
        backchannel.record_refused(item, outcome=f"rejected_{result.reason}")  # type: ignore[attr-defined]
        return "refused"
    if kind == "failure":
        backchannel.record_transient_failure(item, outcome=result.category)  # type: ignore[attr-defined]
        return "retry"
    if kind == "error":
        logger.error(
            "integration.backchannel.delivery_error",
            product_code=item.product_code,
            error_type=type(result).__name__,
        )
        backchannel.record_transient_failure(item, outcome="delivery_error")
        return "retry"
    status = result.status  # type: ignore[attr-defined]
    outcome = f"http_{status}"
    if 200 <= status < 300:
        backchannel.record_delivered(item, outcome=outcome)
        return "delivered"
    if 400 <= status < 500 and status not in RETRYABLE_4XX:
        backchannel.record_refused(item, outcome=outcome)
        return "refused"
    backchannel.record_transient_failure(item, outcome=outcome)
    return "retry"


def deliver_due(*, limit: int = BATCH) -> dict[str, int]:
    """Claim what is due and deliver it, a few at a time.

    Everything that touches the database - the lease, minting each token,
    recording each outcome - happens on the calling thread; the pool threads
    only wait on the network. So a sweep holds one database connection, not
    one per thread, and nothing is read through a connection that cannot see
    the caller's transaction.
    """
    due = backchannel.claim_due(limit=limit)
    summary = {"claimed": len(due), "delivered": 0, "refused": 0, "retry": 0}
    prepared: list[tuple[backchannel.DueDelivery, OutboundRequest]] = []
    for item in due:
        try:
            token = backchannel.logout_token_for(item)
        except Exception as exc:  # no active signing key: an outage, retried
            logger.error(
                "integration.backchannel.mint_failed",
                product_code=item.product_code,
                error_type=type(exc).__name__,
            )
            backchannel.record_transient_failure(item, outcome="mint_failed")
            summary["retry"] += 1
            continue
        prepared.append((item, _request(item, token)))
    if prepared:
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(prepared))) as pool:
            results = list(pool.map(_send, [request for _, request in prepared]))
        for (item, _), (kind, result) in zip(prepared, results, strict=False):
            outcome = _record(item, kind, result)
            summary[outcome] = summary.get(outcome, 0) + 1
    logger.info(
        "integration.backchannel.sweep", **{k: str(v) for k, v in summary.items()}
    )
    return summary

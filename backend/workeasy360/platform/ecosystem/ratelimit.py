"""Targeted limits on the externally reachable trust boundary.

**Scope is the design.** This limiter guards four buckets - authorize, token,
entitlement verification and failed client authentication - and nothing else.
A global throttle across the platform would mean one product's runaway retry
loop degrading an unrelated customer's invoice screen, which is precisely the
cascading failure the whole ecosystem boundary exists to prevent
(16-ecosystem-product-boundary.md section 10).

**What is here and what is not.** The counting itself lives in
``platform.observability.throttle`` - a fixed window, degrading to a
per-process approximation when Redis is unreachable, both explained there.
This module is the OAuth-shaped *facade* over it: the bucket names, their
configured limits, and the protocol error a product's client library can
parse. Phase 4 gave the platform its own public endpoints, which need the
same counter with a different denial shape; one counter, two facades, is
what stops the Redis-outage behaviour being got subtly differently in each.
"""

from __future__ import annotations

from typing import Final

from django.conf import settings

from workeasy360.platform.ecosystem import protocol
from workeasy360.platform.observability import throttle
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

_KEY_PREFIX: Final = "w360:eco:rl:"


class RateLimited(protocol.OAuthError):
    """429 with ``Retry-After``.

    An OAuth error rather than a bare HTTP response so it renders in the same
    shape as every other protocol failure - a client library that can parse
    ``invalid_client`` can parse this one, and 09-failure-resilience.md is
    explicit that a limit a client cannot see is a limit clients hammer.
    """

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            protocol.TEMPORARILY_UNAVAILABLE,
            "Too many requests. Retry after the interval in Retry-After.",
            http_status=429,
            headers={"Retry-After": str(retry_after)},
        )
        self.retry_after = retry_after


def consume(bucket: str, identity: str, *, endpoint: str = "") -> None:
    """Count one request against ``bucket`` for ``identity``, or raise.

    ``identity`` is whatever the bucket is scoped to - a client id, or an IP
    when no client has been established yet. It is **hashed into the key
    rather than used as a metric label**: metric labels are bounded by
    ``FORBIDDEN_LABELS`` for cardinality reasons, and a client id would be
    unbounded. The bucket name is the label; who was limited is in the log.
    """
    limit, window = _limits(bucket)
    if limit <= 0:
        return

    key = f"{_KEY_PREFIX}{bucket}:{identity}:{throttle.window_slot(window)}"

    if throttle.hit(key, window) <= limit:
        return

    metrics.increment("ecosystem_rate_limited_total", bucket=bucket)
    logger.warning(
        "ecosystem.rate_limited",
        bucket=bucket,
        endpoint=endpoint or bucket,
        limit=limit,
        window_s=window,
    )
    raise RateLimited(retry_after=throttle.retry_after(window))


def _limits(bucket: str) -> tuple[int, int]:
    configured = getattr(settings, "ECOSYSTEM_RATE_LIMITS", {})
    limit, window = configured.get(bucket, (0, 60))
    return int(limit), max(1, int(window))


def reset() -> None:
    """Test hook. Clears only the in-process fallback state."""
    throttle.reset()

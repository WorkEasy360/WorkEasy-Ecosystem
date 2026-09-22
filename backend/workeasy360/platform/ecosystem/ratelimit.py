"""Targeted limits on the externally reachable trust boundary.

**Scope is the design.** This limiter guards four buckets - authorize, token,
entitlement verification and failed client authentication - and nothing else.
A global throttle across the platform would mean one product's runaway retry
loop degrading an unrelated customer's invoice screen, which is precisely the
cascading failure the whole ecosystem boundary exists to prevent
(16-ecosystem-product-boundary.md section 10).

**Algorithm: fixed window, not token bucket.** 09-failure-resilience.md
section 4.6 specifies a token bucket for the general API surface because real
user traffic is bursty and a bucket absorbs that. These four endpoints are
not that traffic: they are machine-driven, evenly paced, and the burst
tolerance a bucket provides is exactly the burst an attacker wants. A fixed
window is also two cache operations instead of a read-modify-write, which
matters because it runs *before* authentication on every request. The general
token bucket remains the right answer for the user-facing API when that is
built; this is the right answer here.

**Redis down: approximate, biased toward allowing.** The counter degrades to
a per-process in-memory window, so a deployment with N workers allows up to
N times the limit. That is stated rather than hidden. The alternative -
failing closed - means a Redis blip takes down every product's ability to
authenticate, converting a cache outage into an ecosystem outage. Redis is
Tier 1 with a documented "degrades latency, never correctness" contract, and
rate limiting is the one place where degrading *capacity* to preserve
*availability* is the correct trade (docs/phase-3/14-failure-mode-review.md).
"""

from __future__ import annotations

import threading
import time
from typing import Final

from django.conf import settings
from django.core.cache import cache

from workeasy360.platform.ecosystem import protocol
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

_KEY_PREFIX: Final = "w360:eco:rl:"

#: Per-process fallback counters, used only when the shared cache is
#: unreachable. Guarded by a lock because the web tier is threaded.
_local_lock = threading.Lock()
_local_windows: dict[str, tuple[int, int]] = {}


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

    slot = int(time.time()) // window
    key = f"{_KEY_PREFIX}{bucket}:{identity}:{slot}"

    count = _increment(key, window)
    if count <= limit:
        return

    retry_after = window - (int(time.time()) % window)
    metrics.increment("ecosystem_rate_limited_total", bucket=bucket)
    logger.warning(
        "ecosystem.rate_limited",
        bucket=bucket,
        endpoint=endpoint or bucket,
        limit=limit,
        window_s=window,
    )
    raise RateLimited(retry_after=max(1, retry_after))


def _limits(bucket: str) -> tuple[int, int]:
    configured = getattr(settings, "ECOSYSTEM_RATE_LIMITS", {})
    limit, window = configured.get(bucket, (0, 60))
    return int(limit), max(1, int(window))


def _increment(key: str, window: int) -> int:
    """Increment a window counter, falling back in-process on a cache failure.

    ``cache.add`` then ``cache.incr`` rather than a get/set pair: ``incr`` is
    atomic on Redis, so two concurrent requests cannot both read 4 and both
    write 5. The ``add`` seeds the key with its expiry; ``incr`` on a missing
    key raises, which is the race between the two and is handled by seeding
    again.
    """
    try:
        if cache.add(key, 1, window):
            return 1
        return int(cache.incr(key))
    except ValueError:
        # The key expired between add() and incr(). Re-seed; losing a single
        # count at a window boundary is immaterial.
        try:
            cache.add(key, 1, window)
            return 1
        except Exception:  # pragma: no cover - cache backend down
            return _increment_locally(key, window)
    except Exception:  # pragma: no cover - cache backend down
        return _increment_locally(key, window)


def _increment_locally(key: str, window: int) -> int:
    """Per-process fallback. Approximate by design - see the module docstring."""
    now = int(time.time())
    with _local_lock:
        # Opportunistic sweep, so a long-lived process does not accumulate a
        # dictionary entry per window per identity forever. Cheap because the
        # map only ever holds entries from the current outage.
        if len(_local_windows) > 10_000:
            _local_windows.clear()
        expires, count = _local_windows.get(key, (0, 0))
        if expires <= now:
            _local_windows[key] = (now + window, 1)
            return 1
        _local_windows[key] = (expires, count + 1)
        return count + 1


def reset() -> None:
    """Test hook. Clears only the in-process fallback state."""
    with _local_lock:
        _local_windows.clear()

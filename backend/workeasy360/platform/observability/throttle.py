"""A fixed-window counter, shared by every limiter in the platform.

**Why this lives at P0 rather than inside the module that first needed it.**
Phase 3 put this counter inside ``platform/ecosystem/ratelimit.py``, where
the only callers were the four OIDC buckets. Phase 4 made the platform's own
login and organization-chooser endpoints publicly reachable, and they need
the identical primitive with a different denial shape (JSON, not an OAuth
error). Two copies of a counter are two places for the Redis-outage
behaviour below to be got subtly differently, so there is one, here, at the
lowest layer - and the OAuth-shaped facade stays where it was.

**Algorithm: fixed window, not token bucket.** 09-failure-resilience.md
section 4.6 specifies a token bucket for the general API surface because real
user traffic is bursty and a bucket absorbs that. The endpoints this guards
are not that traffic: they are the unauthenticated front door, and the burst
tolerance a bucket provides is exactly the burst an attacker wants. A fixed
window is also two cache operations instead of a read-modify-write, which
matters because it runs *before* authentication on every request.

**Redis down: approximate, biased toward allowing.** The counter degrades to
a per-process in-memory window, so a deployment with N workers allows up to
N times the limit. That is stated rather than hidden. The alternative -
failing closed - means a Redis blip takes down every product's ability to
authenticate and every customer's ability to sign in, converting a cache
outage into an ecosystem outage. Redis is Tier 1 with a documented "degrades
latency, never correctness" contract, and rate limiting is the one place
where degrading *capacity* to preserve *availability* is the correct trade
(docs/phase-3/14-failure-mode-review.md).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from django.core.cache import cache

#: Per-process fallback counters, used only when the shared cache is
#: unreachable. Guarded by a lock because the web tier is threaded. Ordered by
#: most recent hit, so eviction removes the identities nobody is using.
_local_lock = threading.Lock()
_local_windows: OrderedDict[str, tuple[int, int]] = OrderedDict()
_LOCAL_MAX_KEYS = 10_000


def window_slot(window: int) -> int:
    """Which fixed window the current instant falls in."""
    return int(time.time()) // max(1, window)


def retry_after(window: int) -> int:
    """Seconds until the current window rolls over. At least one, because a
    ``Retry-After: 0`` invites an immediate retry that is refused again."""
    window = max(1, window)
    return max(1, window - (int(time.time()) % window))


def hit(key: str, window: int) -> int:
    """Count one event against ``key``; return the count in this window.

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
            return _hit_locally(key, window)
    except Exception:  # pragma: no cover - cache backend down
        return _hit_locally(key, window)


def _hit_locally(key: str, window: int) -> int:
    """Per-process fallback. Approximate by design - see the module docstring."""
    now = int(time.time())
    with _local_lock:
        expires, count = _local_windows.pop(key, (0, 0))
        count = 1 if expires <= now else count + 1
        _local_windows[key] = (expires if count > 1 else now + window, count)
        # Bounded memory without a reset (Phase 12, G-13). This used to
        # ``clear()`` every counter once 10,000 keys existed - so spraying
        # 10,000 junk identities reset the limit on the one being attacked.
        # Least-recently-hit keys go first; an identity under attack is hit
        # constantly and is never the one evicted.
        while len(_local_windows) > _LOCAL_MAX_KEYS:
            _local_windows.popitem(last=False)
        return count


def reset() -> None:
    """Test hook. Clears only the in-process fallback state."""
    with _local_lock:
        _local_windows.clear()

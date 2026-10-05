"""A Redis cache that stops asking for a moment once Redis stops answering.

Phase 12, found by physically stopping Redis on the staging stack: every
correctness path already fell back to PostgreSQL when a cache call raised, but
*each* call still waited for its own failure first. With the host gone that is
not the 50 ms connect timeout the settings promise - resolving a name that no
longer exists costs seconds, and no socket timeout bounds it - so sign-in took
~8 s and My Products ~15 s while Redis was down. 09-failure-resilience.md
section 3.1 had specified a circuit breaker for exactly this; none existed.

The breaker is per process and shared by every thread: after a connection-type
failure it is *open* for ``BREAKER_COOLDOWN_S``, and every cache call during
that time fails immediately with the same ``ConnectionError`` a real outage
raises - so each caller's existing fallback runs at once instead of after a
timeout. After the cooldown the next call is let through (half-open): success
closes the breaker, failure opens it again. Only transport failures open it; a
bad value or a missing key is not an outage.
"""

from __future__ import annotations

import threading
import time
from functools import cached_property
from typing import Any

from django.core.cache.backends.redis import RedisCache
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

_TRANSPORT_ERRORS = (RedisConnectionError, RedisTimeoutError, OSError)


class _Breaker:
    def __init__(self, cooldown_s: float) -> None:
        self.cooldown_s = cooldown_s
        self.open_until = 0.0
        self.lock = threading.Lock()

    def allow(self) -> bool:
        return time.monotonic() >= self.open_until

    def trip(self) -> None:
        with self.lock:
            already_open = time.monotonic() < self.open_until
            self.open_until = time.monotonic() + self.cooldown_s
        if not already_open:
            metrics.increment("cache_breaker_opened_total")
            logger.warning("cache.breaker.opened", cooldown_s=self.cooldown_s)


#: One breaker per cache location, shared by every thread of the process
#: (Django gives each thread its own backend instance).
_breakers: dict[str, _Breaker] = {}
_breakers_lock = threading.Lock()


def _breaker_for(location: str, cooldown_s: float) -> _Breaker:
    with _breakers_lock:
        if location not in _breakers:
            _breakers[location] = _Breaker(cooldown_s)
        return _breakers[location]


class _GuardedClient:
    """Proxies ``RedisCacheClient``, failing fast while the breaker is open."""

    def __init__(self, inner: Any, breaker: _Breaker) -> None:
        self._inner = inner
        self._breaker = breaker

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr) or name.startswith("_"):
            return attr
        breaker = self._breaker

        def guarded(*args: Any, **kwargs: Any) -> Any:
            if not breaker.allow():
                raise RedisConnectionError("cache circuit open")
            try:
                return attr(*args, **kwargs)
            except _TRANSPORT_ERRORS:
                breaker.trip()
                raise

        return guarded


class ResilientRedisCache(RedisCache):
    """``django.core.cache.backends.redis.RedisCache`` plus the breaker.

    OPTIONS may carry ``breaker_cooldown_s`` (settings pass 30 s, W360_REDIS_BREAKER_COOLDOWN_S); it is removed before
    the rest reach the Redis client.
    """

    def __init__(self, server: Any, params: dict[str, Any]) -> None:
        options = dict(params.get("OPTIONS") or {})
        cooldown = float(options.pop("breaker_cooldown_s", 5.0))
        super().__init__(server, {**params, "OPTIONS": options})
        self._breaker = _breaker_for(",".join(self._servers), cooldown)

    @cached_property
    def _cache(self) -> Any:  # type: ignore[override]
        return _GuardedClient(self._class(self._servers, **self._options), self._breaker)

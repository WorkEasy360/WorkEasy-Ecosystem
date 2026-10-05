"""The Redis circuit breaker (Phase 12, found by the staging Redis outage)."""

from __future__ import annotations

import time

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from workeasy360.platform.observability import cache as breaker_module
from workeasy360.platform.observability.cache import _Breaker, _GuardedClient


class Inner:
    def __init__(self) -> None:
        self.calls = 0
        self.down = True

    def get(self, key, default=None):
        self.calls += 1
        if self.down:
            raise RedisConnectionError("Name or service not known")
        return "value"

    def add(self, key, value, timeout):
        self.calls += 1
        raise ValueError("not a transport problem")


def test_after_one_failure_calls_fail_fast_without_touching_redis():
    inner, breaker = Inner(), _Breaker(cooldown_s=60)
    client = _GuardedClient(inner, breaker)
    with pytest.raises(RedisConnectionError):
        client.get("k")
    started = time.perf_counter()
    for _ in range(100):
        with pytest.raises(RedisConnectionError):
            client.get("k")
    assert inner.calls == 1
    assert time.perf_counter() - started < 0.1


def test_half_open_after_the_cooldown_and_closes_on_success():
    inner, breaker = Inner(), _Breaker(cooldown_s=0.05)
    client = _GuardedClient(inner, breaker)
    with pytest.raises(RedisConnectionError):
        client.get("k")
    # Well past the cooldown: on Windows the monotonic clock ticks every
    # ~15.6 ms, so a 10 ms margin failed now and then (2026-10 audit).
    time.sleep(0.2)
    inner.down = False
    assert client.get("k") == "value"
    assert client.get("k") == "value"
    assert inner.calls == 3


def test_a_non_transport_error_does_not_open_the_breaker():
    inner, breaker = Inner(), _Breaker(cooldown_s=60)
    client = _GuardedClient(inner, breaker)
    with pytest.raises(ValueError):
        client.add("k", 1, 5)
    assert breaker.allow()


def test_every_deployed_environment_uses_the_guarded_backend():
    """Base settings - which production and staging inherit - select it. (The
    test settings swap in LocMemCache, so this reads the base module.)"""
    from config.settings import base

    assert base.CACHES["default"]["BACKEND"].endswith("ResilientRedisCache")


def _unreachable_cache():
    return breaker_module.ResilientRedisCache(
        "redis://127.0.0.1:1/0",
        {
            "OPTIONS": {
                "socket_connect_timeout": 0.05,
                "socket_timeout": 0.1,
                "breaker_cooldown_s": 30,
            }
        },
    )


def test_the_backend_strips_its_own_option_and_trips_on_a_real_refusal():
    backend = _unreachable_cache()
    assert "breaker_cooldown_s" not in backend._options
    # Refused or timed out depending on the platform - both are transport
    # failures, and both trip the breaker.
    with pytest.raises((RedisConnectionError, RedisTimeoutError)):
        backend.get("first")
    assert not backend._breaker.allow()
    started = time.perf_counter()
    for _ in range(200):
        with pytest.raises(RedisConnectionError):
            backend.get("again")
    assert time.perf_counter() - started < 0.2
    backend._breaker.open_until = 0.0


@pytest.mark.django_db
def test_the_rate_limiter_falls_back_immediately_while_the_breaker_is_open(monkeypatch):
    from workeasy360.platform.observability import throttle

    backend = _unreachable_cache()
    monkeypatch.setattr(throttle, "cache", backend)
    throttle.reset()
    try:
        started = time.perf_counter()
        counts = [throttle.hit("phase12:breaker:test", 60) for _ in range(50)]
        assert counts[-1] == 50
        assert time.perf_counter() - started < 1.0
    finally:
        backend._breaker.open_until = 0.0
        throttle.reset()

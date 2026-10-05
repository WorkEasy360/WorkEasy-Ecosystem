"""Rate-limit identities and the Redis-down fallback (Phase 12, G-12/G-13)."""

from __future__ import annotations

import pytest

from workeasy360.platform.observability import throttle
from workeasy360.platform.observability.client_ip import rate_limit_identity


@pytest.mark.parametrize(
    "address,bucket",
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff:ffff:ffff:fffe", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("", ""),
    ],
)
def test_ipv6_callers_share_one_bucket_per_64(address, bucket):
    assert rate_limit_identity(address) == bucket


def test_two_addresses_in_one_64_are_one_identity_and_the_next_64_is_another():
    one = rate_limit_identity("2001:db8:0:1::1")
    assert one == rate_limit_identity("2001:db8:0:1:dead:beef:0:7")
    assert one != rate_limit_identity("2001:db8:0:2::1")


def test_the_login_limit_counts_a_rotating_ipv6_caller_as_one(client, settings):
    """End to end: the per-address login limit is not reset by rotating
    through a /64 (trusted proxy count 1, so the forwarded address counts)."""
    settings.TRUSTED_PROXY_COUNT = 1
    settings.AUTH_RATE_LIMITS = {**settings.AUTH_RATE_LIMITS, "login_ip": (3, 60)}
    throttle.reset()
    statuses = []
    for i in range(6):
        response = client.post(
            "/api/v1/auth/login",
            {"email": f"nobody{i}@example.net", "password": "wrong-password-123"},
            content_type="application/json",
            HTTP_X_FORWARDED_FOR=f"2001:db8:77:1::{i + 1}",
        )
        statuses.append(response.status_code)
    assert 429 in statuses, statuses


def test_the_outage_fallback_never_resets_a_hot_identity(monkeypatch):
    """G-13: spraying junk identities used to clear every counter at 10,000
    keys - including the one under attack."""
    throttle.reset()
    monkeypatch.setattr(throttle, "_LOCAL_MAX_KEYS", 100)
    for _ in range(5):
        throttle._hit_locally("login_ip:victim", 60)
    for i in range(1_000):
        throttle._hit_locally(f"login_ip:junk-{i}", 60)
        if i % 50 == 0:
            throttle._hit_locally("login_ip:victim", 60)
    assert len(throttle._local_windows) <= 100
    assert throttle._hit_locally("login_ip:victim", 60) > 5
    throttle.reset()

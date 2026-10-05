"""What still has to be *correct* when the shared cache is gone.

Phase 4 reported "231 tests pass with Redis stopped", which is true and
proves less than it sounds like: the test settings use a ``LocMemCache``, so
stopping the Redis container changes nothing any of those tests touch. These
tests point the cache at a port nothing listens on, which is what a Redis
outage actually looks like to this process - every ``cache.get``/``add``/
``incr`` raising a connection error, on the request path, before
authentication.

The line being drawn is the one 09-failure-resilience.md draws: Redis is
Tier 1, "degrades latency, never correctness". A cache outage may make
things slower and may make a rate limit approximate. It may **not** sign
anybody out, and it may **not** read as "not entitled" - which is the
failure mode that would lock every paying customer out of every product
because a cache went down.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

#: A Redis backend pointing at a port nothing listens on. Deliberately not a
#: mock: the code under test catches broad exceptions from the cache client,
#: and a mock would let the test pass while a real client raised something
#: the ``except`` clause does not cover.
UNREACHABLE_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://127.0.0.1:6399/0",
        "KEY_PREFIX": "w360",
    }
}


def test_sign_in_still_works_with_an_unreachable_cache(client, user_factory, settings):
    """Sessions live in PostgreSQL, so an outage here must be invisible to a
    person signing in - including through the login throttle, which touches
    the cache before any credential is examined."""
    settings.CACHES = UNREACHABLE_CACHE
    user = user_factory(email="degraded@example.com", password="pw")

    response = client.post(
        "/api/v1/auth/login",
        {"email": user.email, "password": "pw"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True


def test_the_login_throttle_degrades_to_a_per_process_counter(
    client, user_factory, settings, frozen_throttle_window
):
    """Capacity degradation, not a hole.

    With N web workers the effective limit becomes N x the configured one,
    which is stated rather than hidden (debt D-012). What must not happen is
    the limit disappearing entirely, leaving an unauthenticated,
    Argon2-hashing endpoint with no ceiling at all for the duration of a
    cache outage.
    """
    settings.CACHES = UNREACHABLE_CACHE
    settings.AUTH_RATE_LIMITS = {**settings.AUTH_RATE_LIMITS, "login_ip": (2, 60)}

    codes = [
        client.post(
            "/api/v1/auth/login",
            {"email": "nobody@example.com", "password": "x"},
            content_type="application/json",
        ).status_code
        for _ in range(4)
    ]
    assert codes == [401, 401, 429, 429], codes


def test_an_entitlement_never_reads_as_denied_because_the_cache_is_down(
    client, catalog, register_product, entitled_org, settings
):
    """The failure this whole file exists for.

    ``_resolve_from_shared_cache`` swallows a cache failure and falls through
    to PostgreSQL. If it did not - if a cache miss-by-exception resolved to
    an empty snapshot - every product would report every organization
    unentitled for the duration of the outage, and the launcher would show a
    paying customer an empty app directory.
    """
    settings.CACHES = UNREACHABLE_CACHE
    register_product("crm")
    org = entitled_org("crm")
    client.force_login(org["user"])

    response = client.get(f"/api/v1/orgs/{org['org'].slug}/apps")
    assert response.status_code == 200
    apps = {a["product_code"]: a for a in response.json()["apps"]}
    assert apps["crm"]["state"] == "active"
    assert apps["crm"]["can_launch"] is True


def test_an_oidc_launch_still_completes_with_an_unreachable_cache(
    client, signing_key, register_product, entitled_org, signed_in, settings
):
    """The same rule at the product boundary: a cache outage must not deny a
    sign-in that a healthy platform would allow. The rate limiter in front of
    ``/authorize`` runs first and must not fail closed either."""
    import base64
    import hashlib
    import secrets
    from urllib.parse import parse_qs, urlsplit

    settings.CACHES = UNREACHABLE_CACHE
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    granted = client.get(
        "/api/v1/integrations/oauth/authorize",
        {
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": registration["redirect_uri"],
            "scope": "openid",
            "state": "s",
            "nonce": "n",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "organization": org["organization_id"],
        },
    )
    assert granted.status_code == 302, granted.content
    assert "code" in parse_qs(urlsplit(granted["Location"]).query)

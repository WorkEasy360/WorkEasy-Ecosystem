"""Targeted limits on the externally reachable trust boundary.

Two things must both hold, and the second is the one that gets lost:

1. The four external buckets are limited.
2. **Nothing else is.** A global throttle would make one product's runaway
   retry loop into an unrelated customer's outage, which is the cascading
   failure the whole ecosystem boundary exists to prevent.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from workeasy360.platform.ecosystem import ratelimit

pytestmark = pytest.mark.django_db

AUTHORIZE = "/api/v1/integrations/oauth/authorize"
TOKEN = "/api/v1/integrations/oauth/token"
VERIFY = "/api/v1/integrations/entitlements/verify"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _auth(registration) -> dict:
    now = int(time.time())
    return {
        "client_assertion_type": ASSERTION_TYPE,
        "client_assertion": jwt.encode(
            {
                "iss": registration["client_id"],
                "sub": registration["client_id"],
                "aud": "http://testserver" + TOKEN,
                "iat": now,
                "exp": now + 60,
                "jti": uuid.uuid4().hex,
            },
            registration["private_key_pem"],
            algorithm="RS256",
            headers={"kid": registration["credential_id"]},
        ),
    }


# --- the primitive --------------------------------------------------------


def test_a_bucket_allows_its_limit_then_refuses(settings):
    settings.ECOSYSTEM_RATE_LIMITS = {"verify": (3, 60)}
    for _ in range(3):
        ratelimit.consume("verify", "client-a")

    with pytest.raises(ratelimit.RateLimited) as exc:
        ratelimit.consume("verify", "client-a")

    assert exc.value.http_status == 429
    assert exc.value.error == "temporarily_unavailable"
    assert int(exc.value.headers["Retry-After"]) >= 1


def test_buckets_are_independent_per_identity(settings):
    """One product exhausting its budget must not affect another's.

    This is the difference between a targeted limit and a shared one, and a
    shared one would mean the noisiest product in the ecosystem sets everyone
    else's ceiling.
    """
    settings.ECOSYSTEM_RATE_LIMITS = {"verify": (2, 60)}
    ratelimit.consume("verify", "crm")
    ratelimit.consume("verify", "crm")
    with pytest.raises(ratelimit.RateLimited):
        ratelimit.consume("verify", "crm")

    # Books is unaffected.
    ratelimit.consume("verify", "books")
    ratelimit.consume("verify", "books")


def test_buckets_are_independent_of_each_other(settings):
    settings.ECOSYSTEM_RATE_LIMITS = {"verify": (1, 60), "token": (1, 60)}
    ratelimit.consume("verify", "crm")
    ratelimit.consume("token", "crm")
    with pytest.raises(ratelimit.RateLimited):
        ratelimit.consume("verify", "crm")


def test_an_unconfigured_bucket_does_not_limit(settings):
    """A limit nobody configured is not silently zero.

    Defaulting an unknown bucket to "refuse everything" would mean a typo in a
    settings key takes an endpoint offline.
    """
    settings.ECOSYSTEM_RATE_LIMITS = {}
    for _ in range(50):
        ratelimit.consume("verify", "crm")


def test_the_limiter_degrades_rather_than_failing_closed(settings, monkeypatch):
    """Redis down must not take the ecosystem's authentication down with it.

    Failing closed here would convert a cache outage - Tier 1, documented as
    "degrades latency, never correctness" - into every product being unable to
    authenticate anyone. The per-process fallback is approximate and says so.
    """
    settings.ECOSYSTEM_RATE_LIMITS = {"verify": (2, 60)}

    def unavailable(*args, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr("django.core.cache.cache.add", unavailable)
    monkeypatch.setattr("django.core.cache.cache.incr", unavailable)

    ratelimit.consume("verify", "crm")
    ratelimit.consume("verify", "crm")
    # Still enforced, in-process, at the same per-worker limit.
    with pytest.raises(ratelimit.RateLimited):
        ratelimit.consume("verify", "crm")


# --- the endpoints --------------------------------------------------------


def test_the_token_endpoint_is_limited(client, signing_key, register_product, settings):
    settings.ECOSYSTEM_RATE_LIMITS = {**settings.ECOSYSTEM_RATE_LIMITS, "token": (2, 60)}
    registration = register_product("crm")

    seen = [
        client.post(
            TOKEN,
            {"grant_type": "authorization_code", "code": "x", **_auth(registration)},
        ).status_code
        for _ in range(3)
    ]
    assert seen[-1] == 429


def test_the_entitlement_endpoint_is_limited(
    client, signing_key, register_product, entitled_org, settings
):
    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "verify": (2, 60),
    }
    registration = register_product("crm")
    org = entitled_org("crm")

    seen = [
        client.post(
            VERIFY,
            {"organization_id": org["organization_id"], **_auth(registration)},
        ).status_code
        for _ in range(3)
    ]
    assert seen[:2] == [200, 200]
    assert seen[-1] == 429


def test_the_authorization_endpoint_is_limited(
    client, signing_key, register_product, entitled_org, signed_in, settings
):
    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "authorize": (2, 60),
    }
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    params = {
        "response_type": "code",
        "client_id": registration["client_id"],
        "redirect_uri": registration["redirect_uri"],
        "scope": "openid",
        "state": "s",
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
        "organization": org["organization_id"],
    }
    seen = [client.get(AUTHORIZE, params).status_code for _ in range(3)]
    assert seen[-1] == 429


def test_repeated_credential_failures_are_limited_much_harder(
    client, signing_key, register_product, settings
):
    """The bucket credential brute force runs into.

    An order of magnitude tighter than the success path, because a caller
    failing authentication ten times a minute is not a caller with a
    configuration problem.
    """
    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "client_auth_failure": (2, 60),
    }
    registration = register_product("crm", credential_kind="client_secret")

    codes = [
        client.post(
            TOKEN,
            {
                "grant_type": "authorization_code",
                "client_id": registration["client_id"],
                "client_secret": "wrong",
            },
        ).status_code
        for _ in range(4)
    ]
    assert codes[0] == 401
    assert 429 in codes


def test_a_limited_response_tells_the_client_when_to_retry(
    client, signing_key, register_product, settings
):
    """A limit a client cannot see is a limit clients hammer
    (09-failure-resilience.md section 4.6)."""
    settings.ECOSYSTEM_RATE_LIMITS = {**settings.ECOSYSTEM_RATE_LIMITS, "token": (1, 60)}
    registration = register_product("crm")
    body = {"grant_type": "authorization_code", "code": "x"}

    client.post(TOKEN, {**body, **_auth(registration)})
    limited = client.post(TOKEN, {**body, **_auth(registration)})

    assert limited.status_code == 429
    assert int(limited["Retry-After"]) >= 1
    assert limited.json()["error"] == "temporarily_unavailable"


# --- and nothing else -----------------------------------------------------


def test_unrelated_platform_traffic_is_not_throttled(
    client,
    signing_key,
    register_product,
    entitled_org,
    settings,
    in_tenant,
    grant_permissions,
):
    """The rule that keeps one product's problem from becoming everyone's.

    The ecosystem buckets are set to one request each and then exhausted; an
    ordinary tenant-scoped platform endpoint must be entirely unaffected.
    """
    settings.ECOSYSTEM_RATE_LIMITS = {
        "authorize": (1, 60),
        "token": (1, 60),
        "verify": (1, 60),
        "client_auth_failure": (1, 60),
    }
    registration = register_product("crm")
    org = entitled_org("crm")
    grant_permissions(org["org"], org["membership"], ("platform.entitlement.view",))

    for _ in range(3):
        client.post(
            VERIFY,
            {"organization_id": org["organization_id"], **_auth(registration)},
        )

    client.force_login(org["user"])
    for _ in range(10):
        response = client.get(f"/api/v1/orgs/{org['org'].slug}/entitlements")
        assert response.status_code == 200

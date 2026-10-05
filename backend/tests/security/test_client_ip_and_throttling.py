"""D-009, closed: whose address a rate limit is actually keyed on, and what
happens at the platform's own front door when nobody has signed in yet.

Two properties, and the second only means anything because of the first:

1. ``X-Forwarded-For`` is a plain request header. Reading it without knowing
   how many proxies are in front means the caller names their own rate-limit
   bucket, which deletes the limit rather than weakening it.
2. Phase 4 made ``/auth/login`` publicly reachable and shipped it
   unthrottled, guarded only by a per-account lockout that is itself a
   denial-of-service lever.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from workeasy360.platform.observability.client_ip import UNKNOWN, client_ip

LOGIN = "/api/v1/auth/login"


def _request(remote_addr: str = "10.0.0.7", **headers: str):
    return RequestFactory().get("/", REMOTE_ADDR=remote_addr, **headers)


# --- trusted proxy resolution ---------------------------------------------


def test_with_no_proxy_configured_a_forwarded_header_is_ignored_entirely(settings):
    """The default, and the case that must never be forgeable: nothing is in
    front, so REMOTE_ADDR *is* the client and any X-Forwarded-For present is
    purely hostile."""
    settings.TRUSTED_PROXY_COUNT = 0
    request = _request("203.0.113.9", HTTP_X_FORWARDED_FOR="1.2.3.4")
    assert client_ip(request) == "203.0.113.9"


def test_one_trusted_proxy_reads_the_address_that_proxy_observed(settings):
    """An ALB appends the address it saw, so with one trusted hop the
    right-most entry is the client."""
    settings.TRUSTED_PROXY_COUNT = 1
    request = _request("10.0.0.7", HTTP_X_FORWARDED_FOR="198.51.100.4")
    assert client_ip(request) == "198.51.100.4"


def test_a_spoofed_chain_cannot_name_its_own_bucket(settings):
    """The attack the naive left-most read enables.

    The caller prepends whatever they like; with one trusted hop only the
    right-most entry - the one our own proxy wrote - is read, so the
    attacker's thirty fabricated addresses all still land in one bucket.
    """
    settings.TRUSTED_PROXY_COUNT = 1
    spoofed = ", ".join(f"1.1.1.{n}" for n in range(1, 31))
    request = _request("10.0.0.7", HTTP_X_FORWARDED_FOR=f"{spoofed}, 198.51.100.4")
    assert client_ip(request) == "198.51.100.4"


def test_two_trusted_proxies_read_two_from_the_right(settings):
    """CloudFront in front of an ALB: the ALB appends CloudFront's address,
    CloudFront appended the viewer's."""
    settings.TRUSTED_PROXY_COUNT = 2
    request = _request("10.0.0.7", HTTP_X_FORWARDED_FOR="198.51.100.4, 70.132.1.1")
    assert client_ip(request) == "198.51.100.4"


def test_a_chain_shorter_than_the_configured_depth_falls_back(settings):
    """Somebody reached the origin directly, or a proxy was reconfigured.
    Refused rather than salvaged: over-strict is safe, forgeable is not."""
    settings.TRUSTED_PROXY_COUNT = 2
    request = _request("10.0.0.7", HTTP_X_FORWARDED_FOR="198.51.100.4")
    assert client_ip(request) == "10.0.0.7"


def test_a_missing_forwarded_header_falls_back(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    assert client_ip(_request("10.0.0.7")) == "10.0.0.7"


@pytest.mark.parametrize(
    "forwarded",
    ["not-an-address", "", "   ", "999.999.999.999", "DROP TABLE seats"],
)
def test_an_unparseable_entry_is_discarded_rather_than_used_as_a_key(settings, forwarded):
    """These values become cache keys. A caller who can put arbitrary text in
    one can grow the keyspace at will."""
    settings.TRUSTED_PROXY_COUNT = 1
    request = _request("10.0.0.7", HTTP_X_FORWARDED_FOR=forwarded)
    assert client_ip(request) == "10.0.0.7"


def test_equivalent_ipv6_spellings_collapse_to_one_bucket(settings):
    """Otherwise one address is as many buckets as it has spellings."""
    settings.TRUSTED_PROXY_COUNT = 1
    long_form = _request("10.0.0.7", HTTP_X_FORWARDED_FOR="[0:0:0:0:0:0:0:1]")
    short_form = _request("10.0.0.7", HTTP_X_FORWARDED_FOR="[::1]")
    assert client_ip(long_form) == client_ip(short_form) == "::1"


def test_an_ipv4_address_with_a_port_is_normalised(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    request = _request("10.0.0.7", HTTP_X_FORWARDED_FOR="198.51.100.4:51234")
    assert client_ip(request) == "198.51.100.4"


def test_no_address_at_all_is_a_stable_bucket_not_an_empty_key(settings):
    settings.TRUSTED_PROXY_COUNT = 0
    assert client_ip(_request("")) == UNKNOWN


def test_the_forwarded_proto_header_is_trusted_only_behind_a_proxy():
    """The same header class, the same rule. Trusting X-Forwarded-Proto with
    nothing in front lets any caller tell Django "this arrived over TLS",
    which defeats SECURE_SSL_REDIRECT and makes request.is_secure() answer to
    the client rather than to the transport."""
    import importlib

    base = importlib.import_module("config.settings.base")
    assert (base.TRUSTED_PROXY_COUNT > 0) == (base.SECURE_PROXY_SSL_HEADER is not None)


# --- login throttling -----------------------------------------------------


@pytest.mark.django_db
def test_login_is_throttled_per_address_before_any_password_is_hashed(
    client, user_factory, settings, frozen_throttle_window
):
    """The credential-stuffing and CPU case: one address spraying one
    password across many accounts, which no per-account limit can see.

    Argon2id is deliberately memory-hard and Django hashes a dummy password
    even for an unknown email (its own timing-attack defence), so this
    endpoint is a workload anyone on the internet can schedule.
    """
    settings.AUTH_RATE_LIMITS = {**settings.AUTH_RATE_LIMITS, "login_ip": (3, 60)}
    user_factory(email="someone@example.com", password="pw")

    for attempt in range(3):
        response = client.post(
            LOGIN,
            {"email": f"victim{attempt}@example.com", "password": "guess"},
            content_type="application/json",
        )
        assert response.status_code == 401, attempt

    throttled = client.post(
        LOGIN,
        {"email": "yet-another@example.com", "password": "guess"},
        content_type="application/json",
    )
    assert throttled.status_code == 429
    assert throttled.json()["code"] == "rate_limited"
    assert int(throttled["Retry-After"]) >= 1


@pytest.mark.django_db
def test_login_is_throttled_per_account_independently_of_the_address(
    client, user_factory, settings, frozen_throttle_window
):
    """The bucket the address counter cannot stand in for.

    A botnet defeats a per-address limit outright - every request comes from
    somewhere new - so the only thing left bounding guesses against one
    person is a counter keyed on that person. Proven with the address bucket
    effectively disabled, so the refusal can only have come from the account
    one.
    """
    settings.AUTH_RATE_LIMITS = {
        **settings.AUTH_RATE_LIMITS,
        "login_ip": (10_000, 60),
        "login_account": (3, 60),
    }
    user = user_factory(email="target@example.com", password="the-real-password")

    for _ in range(3):
        client.post(
            LOGIN,
            {"email": user.email, "password": "wrong"},
            content_type="application/json",
        )

    throttled = client.post(
        LOGIN,
        {"email": user.email, "password": "wrong"},
        content_type="application/json",
    )
    assert throttled.status_code == 429


def test_the_account_throttle_does_not_mask_the_lockout():
    """Deliberately configured *above* MAX_FAILED_ATTEMPTS.

    Below it, the throttle would fire first and a genuinely locked-out user
    would be told "too many requests" instead of "your account is locked for
    fifteen minutes" - worse for them, and no harder for an attacker, who
    only needs ten requests per fifteen minutes to keep an account locked
    and is under any throttle worth having either way. The lockout being a
    denial-of-service lever is a property of the lockout policy, not
    something a limiter can tune away; it is time-boxed for exactly that
    reason (``platform.identity.services``).
    """
    import importlib

    from workeasy360.platform.identity.services import MAX_FAILED_ATTEMPTS

    base = importlib.import_module("config.settings.base")
    limit, _window = base.AUTH_RATE_LIMITS["login_account"]
    assert limit > MAX_FAILED_ATTEMPTS


@pytest.mark.django_db
def test_the_account_bucket_is_keyed_on_a_hash_not_on_the_email(
    client, user_factory, settings
):
    """Cache keys reach Redis, and Redis reaches backups and dashboards. An
    email address is PII (12-security.md section 5)."""
    from django.core.cache import cache

    settings.AUTH_RATE_LIMITS = {**settings.AUTH_RATE_LIMITS, "login_account": (5, 60)}
    user_factory(email="pii@example.com", password="pw")

    client.post(
        LOGIN,
        {"email": "pii@example.com", "password": "wrong"},
        content_type="application/json",
    )
    keys = list(getattr(cache, "_cache", {}))
    assert keys, "the throttle must have written a counter"
    assert not any("pii@example.com" in str(key) for key in keys)


@pytest.mark.django_db
def test_a_throttled_attempt_never_reports_a_credential_outcome(
    client, user_factory, settings, frozen_throttle_window
):
    """429 is not 401. Answering "wrong password" while throttled would make
    the limiter an oracle that costs the attacker nothing."""
    settings.AUTH_RATE_LIMITS = {**settings.AUTH_RATE_LIMITS, "login_ip": (1, 60)}
    user = user_factory(email="correct@example.com", password="right-password")

    first = client.post(
        LOGIN,
        {"email": user.email, "password": "right-password"},
        content_type="application/json",
    )
    assert first.status_code == 200

    blocked = client.post(
        LOGIN,
        {"email": user.email, "password": "right-password"},
        content_type="application/json",
    )
    assert blocked.status_code == 429
    assert "user_id" not in blocked.json()


@pytest.mark.django_db
def test_the_resume_endpoint_is_throttled(client, settings, frozen_throttle_window):
    """Phase 4 added it unthrottled. It is reached before any client is
    known, with a bearer-shaped token in a query string."""
    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "resume": (2, 60),
    }
    resume = "/api/v1/integrations/oauth/resume"

    for _ in range(2):
        assert client.get(resume, {"resume": "nope"}).status_code == 302

    throttled = client.get(resume, {"resume": "nope"})
    assert throttled.status_code == 429

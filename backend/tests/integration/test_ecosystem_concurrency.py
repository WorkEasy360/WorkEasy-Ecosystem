"""Races on the product integration boundary, against real PostgreSQL.

``TransactionTestCase`` semantics (``transactional_db``) throughout: the
default test transaction would serialise the threads and turn every one of
these into a test of nothing. What is being asserted is what the *database*
guarantees, so the database has to be genuinely involved.

The single-use authorization code is the one that matters most. Everything
else in the flow can be retried; a code redeemed twice is two sessions from
one authorization, which is the difference between "a user signed in" and "a
user and whoever stole their code both signed in".
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from django.db import connections
from django.test import Client

pytestmark = [pytest.mark.concurrency, pytest.mark.django_db(transaction=True)]

AUTHORIZE = "/api/v1/integrations/oauth/authorize"
TOKEN = "/api/v1/integrations/oauth/token"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

#: Enough threads to make a lost update overwhelmingly likely if the guard
#: were a read-then-write, and few enough to stay inside the connection pool.
THREADS = 8


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


def _in_parallel(work, count: int = THREADS) -> list:
    """Run ``work(i)`` on ``count`` threads, released together.

    A barrier rather than a stagger: threads that start at different moments
    do not collide, and a race test that does not collide passes for the
    wrong reason.
    """
    barrier = threading.Barrier(count)
    results: list = [None] * count

    def runner(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = work(index)
        except Exception as exc:  # recorded, not raised - the caller asserts
            results[index] = exc
        finally:
            # Each thread opens its own connection; leaving them behind makes
            # the suite's teardown fail to drop the test database.
            connections.close_all()

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def _grant_code(client, registration, org) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    granted = client.get(
        AUTHORIZE,
        {
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": registration["redirect_uri"],
            "scope": "openid",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "organization": org["organization_id"],
        },
    )
    assert granted.status_code == 302, granted.content
    return parse_qs(urlsplit(granted["Location"]).query)["code"][0], verifier


# --- the one that matters -------------------------------------------------


def test_one_authorization_code_yields_exactly_one_token_set(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Eight simultaneous redemptions of one code. Exactly one succeeds.

    The guard is a conditional UPDATE (``WHERE redeemed_at IS NULL``), which
    PostgreSQL applies atomically. A read-then-write would let several threads
    all observe an unredeemed code under READ COMMITTED and all issue tokens -
    and it would pass a sequential test every time.
    """
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _grant_code(client, registration, org)

    def redeem(_index: int):
        return (
            Client()
            .post(
                TOKEN,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": registration["redirect_uri"],
                    "code_verifier": verifier,
                    **_auth(registration),
                },
            )
            .status_code
        )

    results = _in_parallel(redeem)

    assert results.count(200) == 1, results
    assert results.count(400) == THREADS - 1, results


def test_concurrent_redemptions_leave_one_spent_code_and_no_duplicates(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The database's view of the same race: one row, redeemed once."""
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _grant_code(client, registration, org)

    _in_parallel(
        lambda _i: Client()
        .post(
            TOKEN,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": registration["redirect_uri"],
                "code_verifier": verifier,
                **_auth(registration),
            },
        )
        .status_code
    )

    rows = list(AuthorizationCode.objects.all())
    assert len(rows) == 1
    assert rows[0].redeemed_at is not None


# --- registration and lifecycle races -------------------------------------


def test_concurrent_registrations_for_one_product_produce_one_integration(
    platform_admin, catalog, in_tenant
):
    """The unique constraint on ``product_code``, not a check-then-insert.

    Two client ids both claiming to be CRM would make the entitlement answer
    depend on which one asked. An ``exists()`` guard would not stop it: both
    callers see "no row" and both insert.
    """
    from tests.factories.manifests import manifest_for
    from workeasy360.platform.ecosystem import services
    from workeasy360.platform.ecosystem.models import ProductIntegration

    ctx = platform_admin["ctx"]

    def register(index: int):
        try:
            with in_tenant(ctx):
                return services.register_integration(
                    ctx,
                    manifest=manifest_for("crm", origin=f"https://c{index}.example.com"),
                ).client_id
        except Exception as exc:
            return exc

    results = _in_parallel(register, count=4)

    succeeded = [r for r in results if isinstance(r, str)]
    assert len(succeeded) == 1, results
    assert ProductIntegration.objects.filter(product_code="crm").count() == 1


def test_concurrent_lifecycle_transitions_serialise(
    register_product, platform_admin, in_tenant
):
    """Row-locked, so two operators racing to suspend and reactivate cannot
    leave the integration in a state neither of them chose."""
    from workeasy360.platform.ecosystem import services
    from workeasy360.platform.ecosystem.models import (
        IntegrationStatus,
        ProductIntegration,
    )

    registration = register_product("crm")
    ctx = platform_admin["ctx"]

    def flip(index: int):
        target = "suspended" if index % 2 else "active"
        try:
            with in_tenant(ctx):
                return services.transition(
                    ctx,
                    client_id=registration["client_id"],
                    to_status=target,
                    reason="concurrent test",
                ).status
        except Exception as exc:
            return exc

    _in_parallel(flip, count=6)

    final = ProductIntegration.objects.get(client_id=registration["client_id"])
    # Whatever the interleaving, the row holds one of the two legal outcomes -
    # never a torn state, never a status outside the enum.
    assert final.status_enum in (IntegrationStatus.ACTIVE, IntegrationStatus.SUSPENDED)
    if final.status_enum is IntegrationStatus.SUSPENDED:
        assert final.suspension_reason
    else:
        assert final.suspended_at is None


def test_only_one_signing_key_survives_concurrent_promotion(
    db, signing_key, new_signing_key
):
    """Two operators promoting different keys at once.

    The partial unique index on ``status='active'`` decides it; the loser gets
    an integrity error rather than both keys ending up active, which would
    make "which key signed this?" depend on query ordering.
    """
    from workeasy360.platform.ecosystem import keys
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    candidates = [new_signing_key().kid for _ in range(4)]

    def promote(index: int):
        try:
            return keys.promote_key(candidates[index]).kid
        except Exception as exc:
            return exc

    _in_parallel(promote, count=len(candidates))

    active = SigningKey.objects.filter(status=SigningKeyStatus.ACTIVE.value)
    assert active.count() == 1


def test_a_credential_revoked_mid_flight_stops_working(
    client,
    signing_key,
    register_product,
    entitled_org,
    signed_in,
    platform_admin,
    in_tenant,
):
    """Revocation takes effect on the next request, with no cache to wait out.

    The code was issued while the credential was live; the exchange happens
    after revocation and must fail. This is the shape of an incident response,
    and the answer must not be "wait fifteen minutes".
    """
    from workeasy360.platform.ecosystem import services

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _grant_code(client, registration, org)

    with in_tenant(platform_admin["ctx"]):
        services.revoke_credential(
            platform_admin["ctx"],
            client_id=registration["client_id"],
            credential_id=registration["credential_id"],
            reason="incident",
        )

    response = Client().post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_auth(registration),
        },
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_an_integration_suspended_mid_flight_cannot_complete_the_exchange(
    client,
    signing_key,
    register_product,
    entitled_org,
    signed_in,
    platform_admin,
    in_tenant,
):
    """Suspension stops the product operating, including finishing a sign-in
    that was already under way. The window is sixty seconds, and sixty seconds
    of a compromised product still signing users in is sixty too many."""
    from workeasy360.platform.ecosystem import services

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _grant_code(client, registration, org)

    with in_tenant(platform_admin["ctx"]):
        services.transition(
            platform_admin["ctx"],
            client_id=registration["client_id"],
            to_status="suspended",
            reason="compromised",
        )

    response = Client().post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_auth(registration),
        },
    )
    assert response.status_code == 403
    assert response.json()["error"] == "integration_suspended"


def test_a_client_assertion_cannot_be_replayed_under_concurrency(
    client, signing_key, register_product, entitled_org, signed_in
):
    """``cache.add`` is atomic, so "first use wins" is a property of the cache
    rather than a read-then-write race between threads."""
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _grant_code(client, registration, org)
    assertion = _auth(registration)

    def redeem(_index: int):
        return (
            Client()
            .post(
                TOKEN,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": registration["redirect_uri"],
                    "code_verifier": verifier,
                    **assertion,
                },
            )
            .status_code
        )

    results = _in_parallel(redeem)

    # At most one request gets past client authentication, and therefore at
    # most one token set is ever issued.
    assert results.count(200) <= 1, results
    assert results.count(401) >= THREADS - 1, results

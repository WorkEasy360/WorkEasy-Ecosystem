"""Service-to-platform authentication: how a product proves it is itself.

The uniform-failure property is the one worth stating up front, because it is
the one a reviewer will want to check and the one a well-meaning change will
break: **every** authentication failure returns the identical
``invalid_client``, with the same status and the same description. Unknown
client, wrong secret, bad signature, revoked credential, expired credential,
wrong assertion audience, replayed assertion - all of them. Anything else is
a client-enumeration oracle.
"""

from __future__ import annotations

import base64
import time
import uuid

import jwt
import pytest

pytestmark = pytest.mark.django_db

TOKEN = "/api/v1/integrations/oauth/token"
VERIFY = "/api/v1/integrations/entitlements/verify"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _assertion(registration, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": registration["client_id"],
        "sub": registration["client_id"],
        "aud": "http://testserver" + TOKEN,
        "iat": now,
        "exp": now + 60,
        "jti": uuid.uuid4().hex,
    }
    claims.update(overrides.pop("claims", {}))
    return jwt.encode(
        claims,
        overrides.pop("key", registration["private_key_pem"]),
        algorithm=overrides.pop("algorithm", "RS256"),
        headers={"kid": overrides.pop("kid", registration["credential_id"])},
    )


def _post(client, registration, *, assertion=None, body=None):
    payload = {
        "grant_type": "authorization_code",
        "code": "irrelevant",
        "client_assertion_type": ASSERTION_TYPE,
        "client_assertion": assertion or _assertion(registration),
    }
    payload.update(body or {})
    return client.post(TOKEN, payload)


def _basic(client_id: str, secret: str) -> str:
    raw = f"{client_id}:{secret}".encode()
    return f"Basic {base64.b64encode(raw).decode()}"


# --- private_key_jwt ------------------------------------------------------


def test_a_valid_assertion_authenticates(client, signing_key, register_product):
    """Authentication succeeds, so the request fails on the *grant* instead.

    ``invalid_grant`` rather than ``invalid_client`` is the assertion: it
    proves the client was identified and the code was then rejected, which is
    exactly one step further than a client-authentication failure would get.
    """
    registration = register_product("crm")
    response = _post(client, registration)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.parametrize(
    "case",
    [
        "unknown_client",
        "wrong_key",
        "unknown_kid",
        "wrong_audience",
        "expired",
        "iss_sub_mismatch",
        "no_kid",
        "unsigned",
    ],
)
def test_every_assertion_failure_is_indistinguishable(
    client, signing_key, register_product, new_signing_key, case
):
    registration = register_product("crm")

    if case == "unknown_client":
        assertion = _assertion(registration, claims={"iss": "nope", "sub": "nope"})
    elif case == "wrong_key":
        other, _ = __import__(
            "workeasy360.platform.ecosystem.keys", fromlist=["keys"]
        ).generate_key_pair("RS256")
        assertion = _assertion(registration, key=other)
    elif case == "unknown_kid":
        assertion = _assertion(registration, kid="not-a-credential")
    elif case == "wrong_audience":
        assertion = _assertion(registration, claims={"aud": "https://evil.example"})
    elif case == "expired":
        now = int(time.time())
        assertion = _assertion(registration, claims={"iat": now - 600, "exp": now - 300})
    elif case == "iss_sub_mismatch":
        assertion = _assertion(registration, claims={"sub": "someone-else"})
    elif case == "no_kid":
        assertion = jwt.encode(
            {
                "iss": registration["client_id"],
                "sub": registration["client_id"],
                "aud": "http://testserver" + TOKEN,
                "exp": int(time.time()) + 60,
                "jti": uuid.uuid4().hex,
            },
            registration["private_key_pem"],
            algorithm="RS256",
        )
    else:  # unsigned - the alg=none attack
        assertion = jwt.encode(
            {
                "iss": registration["client_id"],
                "sub": registration["client_id"],
                "aud": "http://testserver" + TOKEN,
                "exp": int(time.time()) + 60,
                "jti": uuid.uuid4().hex,
            },
            key="",
            algorithm="none",
            headers={"kid": registration["credential_id"]},
        )

    response = _post(client, registration, assertion=assertion)
    body = response.json()
    assert response.status_code == 401, case
    assert body["error"] == "invalid_client", case
    # One description for every cause, so the response itself discloses
    # nothing about which part of the attempt was wrong.
    assert body["error_description"] == "Client authentication failed.", case
    assert response["WWW-Authenticate"].startswith("Basic ")


def test_an_assertion_cannot_be_replayed(client, signing_key, register_product):
    """Single use by ``jti`` inside its validity window.

    Without it, an assertion captured once - from a log, a proxy, a crash
    dump - authenticates as the product for its whole lifetime.
    """
    registration = register_product("crm")
    assertion = _assertion(registration)

    first = _post(client, registration, assertion=assertion)
    second = _post(client, registration, assertion=assertion)

    # The first gets past authentication and fails on the grant; the second
    # never gets that far.
    assert first.json()["error"] == "invalid_grant"
    assert second.status_code == 401
    assert second.json()["error"] == "invalid_client"


def test_the_algorithm_comes_from_the_credential_not_the_assertion(
    client, signing_key, register_product
):
    """The algorithm-confusion attack.

    The credential is registered as RS256, so the platform verifies with
    RS256 regardless of what the assertion's header claims. An attacker who
    could choose the algorithm could present the registered *public* key as an
    HMAC shared secret and sign their own assertions with it.
    """
    import hashlib
    import hmac
    import json

    registration = register_product("crm")
    public_pem = registration["integration"].credentials.first().public_key_pem

    # Hand-built, because PyJWT refuses to *create* this token - it raises
    # InvalidKeyError rather than let a caller use an asymmetric key as an
    # HMAC secret. That refusal is the library protecting its own users and
    # says nothing about what this platform does with a token that arrives
    # over the wire, which is what needs asserting.
    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(
        json.dumps(
            {"alg": "HS256", "typ": "JWT", "kid": registration["credential_id"]}
        ).encode()
    )
    payload = b64(
        json.dumps(
            {
                "iss": registration["client_id"],
                "sub": registration["client_id"],
                "aud": "http://testserver" + TOKEN,
                "exp": int(time.time()) + 60,
                "jti": uuid.uuid4().hex,
            }
        ).encode()
    )
    signing_input = header + b"." + payload
    signature = b64(hmac.new(public_pem.encode(), signing_input, hashlib.sha256).digest())
    forged = (signing_input + b"." + signature).decode()

    response = _post(client, registration, assertion=forged)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_a_revoked_credential_stops_working_immediately(
    client, signing_key, register_product, platform_admin, in_tenant
):
    """No propagation delay: client authentication reads the row every time,
    so there is no cached credential state to wait out. That is the deliberate
    difference from token revocation, which is bounded by token lifetime."""
    from workeasy360.platform.ecosystem import services

    registration = register_product("crm")
    assert _post(client, registration).json()["error"] == "invalid_grant"

    with in_tenant(platform_admin["ctx"]):
        services.revoke_credential(
            platform_admin["ctx"],
            client_id=registration["client_id"],
            credential_id=registration["credential_id"],
            reason="leaked",
        )

    after = _post(client, registration)
    assert after.status_code == 401
    assert after.json()["error"] == "invalid_client"


def test_an_expired_credential_is_refused(client, signing_key, register_product):
    """A credential with no end date outlives the person who created it."""
    from django.utils import timezone

    from workeasy360.platform.ecosystem.models import IntegrationCredential

    registration = register_product("crm")
    IntegrationCredential.objects.filter(
        credential_id=registration["credential_id"]
    ).update(expires_at=timezone.now() - timezone.timedelta(seconds=1))

    response = _post(client, registration)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


# --- client_secret --------------------------------------------------------


def test_a_client_secret_authenticates_via_basic_and_post(
    client, signing_key, register_product
):
    """Both placements are accepted: some HTTP clients cannot set an
    Authorization header, and refusing that pushes the secret somewhere
    worse."""
    registration = register_product("crm", credential_kind="client_secret")
    body = {"grant_type": "authorization_code", "code": "irrelevant"}

    via_header = client.post(
        TOKEN,
        body,
        HTTP_AUTHORIZATION=_basic(
            registration["client_id"], registration["client_secret"]
        ),
    )
    via_post = client.post(
        TOKEN,
        {
            **body,
            "client_id": registration["client_id"],
            "client_secret": registration["client_secret"],
        },
    )
    assert via_header.json()["error"] == "invalid_grant"
    assert via_post.json()["error"] == "invalid_grant"


def test_a_wrong_secret_is_refused(client, signing_key, register_product):
    registration = register_product("crm", credential_kind="client_secret")
    response = client.post(
        TOKEN,
        {"grant_type": "authorization_code", "code": "x"},
        HTTP_AUTHORIZATION=_basic(registration["client_id"], "wrong-secret-entirely"),
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_a_secret_belonging_to_another_client_is_refused(
    client, signing_key, register_product
):
    """The secret is verified against the credential *of the named client*,
    not against every credential in the table."""
    crm = register_product("crm", credential_kind="client_secret")
    books = register_product("books", credential_kind="client_secret")

    response = client.post(
        TOKEN,
        {"grant_type": "authorization_code", "code": "x"},
        HTTP_AUTHORIZATION=_basic(crm["client_id"], books["client_secret"]),
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_secrets_are_full_entropy_and_stored_only_as_a_digest(register_product):
    """256 bits from ``secrets.token_urlsafe``, which is why a fast hash is
    the correct choice - there is nothing to brute force, and a work factor
    would only hand an attacker a CPU-exhaustion lever against the token
    endpoint."""
    from workeasy360.platform.ecosystem import clientauth

    registration = register_product("crm", credential_kind="client_secret")
    secret = registration["client_secret"]

    # token_urlsafe(32) yields 43 url-safe characters.
    assert len(secret) >= 43
    assert registration["credential_id"] == secret[: clientauth.SECRET_PREFIX_LENGTH]


def test_missing_credentials_are_refused(client, signing_key, register_product):
    register_product("crm")
    response = client.post(TOKEN, {"grant_type": "authorization_code", "code": "x"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_a_malformed_basic_header_does_not_crash(client, signing_key, register_product):
    """Untrusted input on the unauthenticated path. A 500 here would be a
    denial-of-service primitive as well as a leak of the exception type."""
    register_product("crm")
    for header in (
        "Basic",
        "Basic !!!!",
        "Basic " + base64.b64encode(b"nocolon").decode(),
    ):
        response = client.post(
            TOKEN, {"grant_type": "authorization_code"}, HTTP_AUTHORIZATION=header
        )
        assert response.status_code == 401, header


def test_a_secret_containing_reserved_characters_round_trips(
    client, signing_key, register_product, platform_admin, in_tenant
):
    """RFC 6749 section 2.3.1 requires form-urlencoding both halves before
    Basic encoding. Not unquoting is a bug that only appears for some
    generated secrets, which makes it the worst kind to ship."""
    from urllib.parse import quote

    from workeasy360.platform.ecosystem import clientauth
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    registration = register_product("crm", credential_kind="client_secret")
    awkward = "abc:def%ghi jkl"
    IntegrationCredential.objects.filter(
        credential_id=registration["credential_id"]
    ).update(
        secret_hash=clientauth.hash_secret(awkward),
        credential_id=awkward[: clientauth.SECRET_PREFIX_LENGTH],
    )

    raw = f"{quote(registration['client_id'])}:{quote(awkward)}".encode()
    response = client.post(
        TOKEN,
        {"grant_type": "authorization_code", "code": "x"},
        HTTP_AUTHORIZATION=f"Basic {base64.b64encode(raw).decode()}",
    )
    assert response.json()["error"] == "invalid_grant"


# --- credential use tracking ---------------------------------------------


def test_successful_authentication_records_use_at_day_granularity(
    client, signing_key, register_product
):
    """Coarse deliberately: the question is "is anything still using this?"
    before revoking it, and a per-second timestamp would turn every token
    exchange into a write."""
    from django.utils import timezone

    from workeasy360.platform.ecosystem.models import IntegrationCredential

    registration = register_product("crm")
    _post(client, registration)

    row = IntegrationCredential.objects.get(credential_id=registration["credential_id"])
    assert row.last_used_on == timezone.now().date()


def test_a_failed_authentication_does_not_record_use(
    client, signing_key, register_product
):
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    registration = register_product("crm")
    _post(client, registration, assertion=_assertion(registration, kid="wrong"))

    row = IntegrationCredential.objects.get(credential_id=registration["credential_id"])
    assert row.last_used_on is None


# --- session authentication is never accepted -----------------------------


def test_credentialed_endpoints_ignore_session_authentication(
    client, signing_key, register_product, entitled_org, signed_in
):
    """This is what makes the CSRF exemption safe.

    CSRF protects requests carrying ambient credentials. These endpoints
    accept none: a fully signed-in browser session, which would be forgeable
    cross-origin, authenticates nothing here.
    """
    register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    for path, body in (
        (TOKEN, {"grant_type": "authorization_code", "code": "x"}),
        (VERIFY, {"organization_id": org["organization_id"]}),
    ):
        response = client.post(path, body)
        assert response.status_code == 401, path
        assert response.json()["error"] == "invalid_client", path


def test_an_assertion_may_not_outlive_the_replay_memory(
    client, signing_key, register_product
):
    """The bound the documented "maximum age 300 s" actually needs.

    A spent ``jti`` is remembered for ``OIDC_CLIENT_ASSERTION_MAX_AGE_S`` and
    no longer - a cache that remembered every assertion forever would grow
    without limit. So an assertion whose ``exp`` reaches further than that
    outlives the memory of having been used, and a captured one becomes
    replayable again the moment the entry expires. The product chooses its
    own ``exp``; the platform declines to honour one it cannot police.
    """
    now = int(time.time())
    registration = register_product("crm")

    long_lived = _assertion(registration, claims={"iat": now, "exp": now + 86_400})
    response = _post(client, registration, assertion=long_lived)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_an_assertion_without_iat_is_refused(client, signing_key, register_product):
    """Otherwise the maximum-age check has nothing to measure against and
    silently does not run - a bound that applies only to assertions which
    volunteered to be bounded."""
    now = int(time.time())
    registration = register_product("crm")

    no_iat = jwt.encode(
        {
            "iss": registration["client_id"],
            "sub": registration["client_id"],
            "aud": "http://testserver" + TOKEN,
            "exp": now + 60,
            "jti": uuid.uuid4().hex,
        },
        registration["private_key_pem"],
        algorithm="RS256",
        headers={"kid": registration["credential_id"]},
    )
    response = _post(client, registration, assertion=no_iat)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_a_normal_short_lived_assertion_still_authenticates(
    client, signing_key, register_product
):
    """The two bounds above would pass trivially if every assertion failed."""
    now = int(time.time())
    registration = register_product("crm")

    ordinary = _assertion(registration, claims={"iat": now, "exp": now + 60})
    response = _post(client, registration, assertion=ordinary)
    # The grant itself is nonsense ("irrelevant"), so the *client* authenticated
    # and the request failed later, on the code - which is exactly the boundary
    # being asserted.
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"

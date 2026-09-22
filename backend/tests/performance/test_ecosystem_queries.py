"""Query budgets and measured latency for the remote integration endpoints.

Budgets rather than timings, mostly. A query count is deterministic and
survives being run on a laptop, a CI runner and a loaded developer machine; a
wall-clock assertion on any of those is a flaky test that eventually gets
deleted, taking the protection with it.

The one latency measurement here is reported rather than asserted tightly,
for the same reason - and because the number that matters operationally is
the one in docs/phase-3/13-performance-review.md, measured deliberately
rather than incidentally during a test run.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest

pytestmark = pytest.mark.django_db

AUTHORIZE = "/api/v1/integrations/oauth/authorize"
TOKEN = "/api/v1/integrations/oauth/token"
VERIFY = "/api/v1/integrations/entitlements/verify"
JWKS = "/.well-known/jwks.json"
DISCOVERY = "/.well-known/openid-configuration"
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


def _pkce():
    verifier = secrets.token_urlsafe(48)
    return verifier, (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )


def _authorize_params(registration, org, challenge):
    return {
        "response_type": "code",
        "client_id": registration["client_id"],
        "redirect_uri": registration["redirect_uri"],
        "scope": "openid",
        "state": "s",
        "nonce": "n",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "organization": org["organization_id"],
    }


# --- query budgets --------------------------------------------------------


def test_the_jwks_costs_one_query(client, signing_key, django_assert_num_queries):
    """Fetched by every product on a cache miss, and by anything watching the
    rotation. One indexed read of the published keys, and no per-key query -
    which a naive implementation would produce."""
    with django_assert_num_queries(1):
        assert client.get(JWKS).status_code == 200


def test_discovery_costs_no_queries(client, signing_key, django_assert_num_queries):
    """It is derived from settings and from two module constants. A database
    read here would make the cheapest, most-fetched endpoint in the ecosystem
    depend on PostgreSQL being up."""
    with django_assert_num_queries(0):
        assert client.get(DISCOVERY).status_code == 200


def test_the_jwks_does_not_grow_a_query_per_key(
    client, signing_key, new_signing_key, django_assert_num_queries
):
    """The N+1 guard. Four published keys must cost what one costs."""
    for _ in range(3):
        new_signing_key()
    with django_assert_num_queries(1):
        body = client.get(JWKS).json()
    assert len(body["keys"]) == 4


def test_entitlement_verification_has_a_bounded_query_budget(
    client, signing_key, register_product, entitled_org, django_assert_max_num_queries
):
    """The hottest endpoint in the ecosystem: every product calls it on the
    documented revalidation cadence, for every organization it serves.

    Budget: the credential lookup, the credential-use stamp, the tenant-scope
    round trips and the snapshot read. Bounded rather than exact because the
    scope helpers' round trips are an implementation detail of tenancy, not of
    this endpoint - what must not happen is a count that grows with anything.
    """
    registration = register_product("crm")
    org = entitled_org("crm")

    with django_assert_max_num_queries(12):
        response = client.post(
            VERIFY,
            {"organization_id": org["organization_id"], **_auth(registration)},
        )
    assert response.json()["decision"] == "allow"


def test_verification_cost_does_not_grow_with_the_number_of_integrations(
    client, signing_key, register_product, entitled_org, django_assert_max_num_queries
):
    """The credential lookup is indexed on (integration, kind, credential_id),
    so a second registered product is not a second row scanned."""
    registration = register_product("crm")
    register_product("books")
    org = entitled_org("crm")

    with django_assert_max_num_queries(12):
        client.post(
            VERIFY, {"organization_id": org["organization_id"], **_auth(registration)}
        )


def test_the_authorization_endpoint_has_a_bounded_query_budget(
    client,
    signing_key,
    register_product,
    entitled_org,
    signed_in,
    django_assert_max_num_queries,
):
    """Session, integration lookup, organization, membership, entitlement
    snapshot, code insert - plus the tenant-scope round trips around the
    entitlement read.

    **Phase 4:** +5, from ``require_seat`` (Task 11/``authorization.py``
    step 9): a catalog lookup for the product's licensing mode, the seat
    read itself, and the tenant-scope round trips around that read - the
    identical shape ``require_entitlement`` already pays for the identical
    reason. ``crm`` in this fixture is per-seat, so every authorization
    against it pays this cost; an organization-wide product would not.
    """
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    _, challenge = _pkce()

    with django_assert_max_num_queries(25):
        response = client.get(AUTHORIZE, _authorize_params(registration, org, challenge))
    assert response.status_code == 302


def test_the_token_endpoint_has_a_bounded_query_budget(
    client,
    signing_key,
    register_product,
    entitled_org,
    signed_in,
    django_assert_max_num_queries,
):
    """Credential, use stamp, code lookup with its integration joined, the
    conditional spend, the signing key, and the profile read - **plus** the
    subject re-validation, which is most of this budget.

    The re-validation costs roughly what the authorization endpoint's own
    membership and entitlement checks cost, because it is literally the same
    two functions: an organization read, a membership read inside that
    organization's database scope, and the entitlement snapshot read with the
    scope round trips around it. It is bought deliberately
    (``authorization._revalidate_subject``): without it a membership revoked
    during the sixty seconds a code lives still yields a fresh five-minute
    identity token, and the documented revocation window is wrong by the code
    lifetime.

    It is affordable here and would not be on a per-request path. This runs
    once per product sign-in, alongside an RS256 signature that costs ~28 ms
    on its own - the database round trips are not what a user waits for.

    **Phase 4:** +4. +3 from ``_revalidate_subject`` now also re-running
    ``require_seat`` (a per-seat product's re-validation must re-check the
    seat, not only the entitlement, or an unassigned-mid-flight seat would
    still yield a token - the same argument that motivated re-validation at
    all, applied one check further). +1 from ``SessionEpochMiddleware``
    (Task 33): it reads ``request.user`` on every request to enforce
    immediate session revocation, and this specific test's client happens to
    carry a session cookie from ``signed_in`` even though the token endpoint
    authenticates by client credential and ignores it
    (``test_credentialed_endpoints_ignore_session_authentication``) - reading
    it costs one session-table lookup that nothing previously triggered.
    Real, small, and paid on every request rather than once per sign-in; not
    worth special-casing the middleware for credentialed endpoints, which
    would reintroduce exactly the kind of "does this endpoint check the
    session or not" ambiguity that test exists to rule out.
    """
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    verifier, challenge = _pkce()
    granted = client.get(AUTHORIZE, _authorize_params(registration, org, challenge))
    code = parse_qs(urlsplit(granted["Location"]).query)["code"][0]

    with django_assert_max_num_queries(30):
        response = client.post(
            TOKEN,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": registration["redirect_uri"],
                "code_verifier": verifier,
                **_auth(registration),
            },
        )
    assert response.status_code == 200


def test_the_code_lookup_joins_its_integration_rather_than_refetching(
    client, signing_key, register_product, entitled_org, signed_in
):
    """``select_related`` is not incidental here.

    Without it, redemption reads the code, then reads the integration to
    compare client ids, then reads it again for the product code on the token
    - three queries where one will do, on the endpoint a user waits for.

    Counted against the integration table specifically rather than as "one
    SELECT in total": redemption also re-validates the subject, which reads
    the organization, the membership and the entitlement snapshot. Those are
    a different cost with a different justification, and folding them into
    this assertion would mean this test failed the next time either changed
    while saying nothing about the join it exists to protect.
    """
    from workeasy360.platform.ecosystem import authorization

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    verifier, challenge = _pkce()
    granted = client.get(AUTHORIZE, _authorize_params(registration, org, challenge))
    code = parse_qs(urlsplit(granted["Location"]).query)["code"][0]

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    class _Client:
        integration = registration["integration"]
        product_code = "crm"

    with CaptureQueriesContext(connection) as captured:
        redeemed = authorization.redeem_code(
            code=code,
            client=_Client(),
            redirect_uri=registration["redirect_uri"],
            code_verifier=verifier,
        )
        # Reading the integration off the result must not trigger a query.
        assert redeemed.integration.product_code == "crm"

    integration_reads = [
        q
        for q in captured.captured_queries
        if "SELECT" in q["sql"].upper() and "ecosystem_product_integration" in q["sql"]
    ]
    assert len(integration_reads) == 1, [q["sql"] for q in integration_reads]
    # And it really is the join, not a second lookup that happens to be first.
    assert "ecosystem_authorization_code" in integration_reads[0]["sql"]


def test_listing_integrations_does_not_grow_a_query_per_row(
    register_product, platform_admin, in_tenant, django_assert_max_num_queries
):
    """The manifest is a JSON column on the row, so rendering a listing needs
    no second read per integration - which is the whole reason it is stored
    as one document rather than as related redirect-URI rows."""
    from workeasy360.platform.ecosystem import selectors

    register_product("crm")
    register_product("books")
    ctx = platform_admin["ctx"]

    with in_tenant(ctx), django_assert_max_num_queries(6):
        listed = selectors.integrations(ctx)
    assert len(listed) == 2


# --- measured, not asserted ----------------------------------------------


def test_report_entitlement_verification_latency(
    client, signing_key, register_product, entitled_org, capsys
):
    """Measure and print p50/p95/p99 for the remote entitlement check.

    Printed rather than asserted against a threshold: this runs on whatever
    machine the suite runs on, through PgBouncer, with a cold cache on the
    first iteration. A tight assertion would be a flaky test; an honest
    measurement is evidence. The numbers recorded in
    docs/phase-3/13-performance-review.md come from this, run deliberately.
    """
    registration = register_product("crm")
    org = entitled_org("crm")
    body = {"organization_id": org["organization_id"]}

    samples: list[float] = []
    for _ in range(40):
        payload = {**body, **_auth(registration)}
        started = time.perf_counter()
        response = client.post(VERIFY, payload)
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200

    samples.sort()

    def percentile(p: float) -> float:
        return samples[min(len(samples) - 1, int(len(samples) * p))]

    with capsys.disabled():
        print(
            f"\n  entitlement verify over {len(samples)} calls: "
            f"p50={percentile(0.50):.1f}ms "
            f"p95={percentile(0.95):.1f}ms "
            f"p99={percentile(0.99):.1f}ms "
            f"min={samples[0]:.1f}ms max={samples[-1]:.1f}ms"
        )

    # A ceiling loose enough never to be flaky and tight enough to catch a
    # regression that turned a bounded read into an unbounded one.
    assert percentile(0.95) < 500, samples[-5:]


@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_report_token_signing_cost(db, new_signing_key, capsys, algorithm):
    """Both algorithms, measured. RS256 signs more slowly and verifies faster;
    ES256 the reverse. The platform signs once per authentication and products
    verify once per session, so the default favours the verifier's
    compatibility rather than either cost."""
    from workeasy360.platform.ecosystem import tokens
    from workeasy360.platform.ecosystem.models import SigningKeyStatus

    new_signing_key(status=SigningKeyStatus.ACTIVE, algorithm=algorithm)
    subject = tokens.TokenSubject(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        product_code="crm",
        client_id="bench",
        scope="openid",
        aal=1,
    )

    samples = []
    for _ in range(20):
        started = time.perf_counter()
        tokens.issue_id_token(subject)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()

    with capsys.disabled():
        print(
            f"\n  {algorithm} id_token sign x{len(samples)}: "
            f"p50={samples[len(samples) // 2]:.2f}ms max={samples[-1]:.2f}ms"
        )


def test_report_token_size_and_bound_it(
    client, signing_key, register_product, entitled_org, signed_in, capsys
):
    """Tokens travel in headers and often end up in a cookie. Measure both.

    The ceiling matters because the failure is not gradual. An 8 KB request
    header is refused by nginx and by most ingress defaults, and a 4 KB cookie
    is silently dropped by the browser - so a product that stores the ID token
    in a session cookie starts failing for *some* users, the ones whose name
    or nonce pushed it over, with nothing in any log to explain it.

    Measured at the widest point this platform can produce: every scope
    granted, so `email`, `email_verified` and `name` are all present, and the
    longest nonce the authorization endpoint accepts.
    """
    registration = register_product("crm", scopes=("openid", "profile", "email"))
    org = entitled_org("crm")
    signed_in(org["user"])

    verifier, challenge = _pkce()
    params = _authorize_params(registration, org, challenge)
    params["scope"] = "openid profile email"
    params["nonce"] = "n" * 255
    granted = client.get(AUTHORIZE, params)
    payload = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": parse_qs(urlsplit(granted["Location"]).query)["code"][0],
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_auth(registration),
        },
    ).json()

    id_bytes = len(payload["id_token"].encode())
    access_bytes = len(payload["access_token"].encode())

    with capsys.disabled():
        print(
            f"\n  RS256 token size at maximum claims: "
            f"id_token={id_bytes}B access_token={access_bytes}B"
        )

    # Half a browser cookie, comfortably. Chosen against the constraint rather
    # than against the current number, so it says what it is protecting: the
    # 2 KB of headroom is what an added claim would eat into.
    assert id_bytes < 2048, id_bytes
    assert access_bytes < 2048, access_bytes

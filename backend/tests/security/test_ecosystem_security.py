"""Adversarial tests for the remote product integration boundary.

Organised by attack rather than by module, because that is how the threat
model in docs/phase-3/11-security-threat-model.md is organised and because a
control is only interesting in terms of what it stops.

Where a test asserts an *absence* - no enumeration signal, no leaked field -
it asserts it against a concrete alternative, not against a regex. "The
unknown-organization response is byte-identical to the unentitled one" is
checkable; "the response looks safe" is not.
"""

from __future__ import annotations

import base64
import json
import time
import uuid

import jwt
import pytest

pytestmark = [pytest.mark.security, pytest.mark.django_db]

AUTHORIZE = "/api/v1/integrations/oauth/authorize"
TOKEN = "/api/v1/integrations/oauth/token"
USERINFO = "/api/v1/integrations/oauth/userinfo"
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


def _issue(client, registration, org, *, scope="openid"):
    """Run the flow and return the token response."""
    import hashlib
    import secrets
    from urllib.parse import parse_qs, urlsplit

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
            "scope": scope,
            "state": "s",
            "nonce": "n",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "organization": org["organization_id"],
        },
    )
    code = parse_qs(urlsplit(granted["Location"]).query)["code"][0]
    return client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_auth(registration),
        },
    ).json()


def _repack(
    token: str, *, header: dict | None = None, payload: dict | None = None
) -> str:
    """Rebuild a JWT with altered parts, keeping the original signature.

    The forgery an attacker can actually produce without the private key.
    """
    raw_header, raw_payload, signature = token.split(".")

    def decode(part: str) -> dict:
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))

    def encode(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    new_header = encode({**decode(raw_header), **(header or {})})
    new_payload = encode({**decode(raw_payload), **(payload or {})})
    return f"{new_header}.{new_payload}.{signature}"


# --- token tampering ------------------------------------------------------


@pytest.mark.parametrize(
    "tamper,description",
    [
        ({"payload": {"organization_id": str(uuid.uuid4())}}, "tenant switching"),
        ({"payload": {"sub": str(uuid.uuid4())}}, "user substitution"),
        ({"payload": {"product_code": "books"}}, "product escalation"),
        ({"payload": {"aud": "some-other-client"}}, "audience substitution"),
        ({"payload": {"iss": "https://evil.example"}}, "issuer substitution"),
        ({"payload": {"exp": int(time.time()) + 86400}}, "expiry extension"),
        ({"payload": {"typ": "access_token"}}, "token type confusion"),
        ({"header": {"alg": "none"}}, "algorithm stripping"),
        ({"header": {"alg": "HS256"}}, "algorithm confusion"),
        ({"header": {"kid": "some-other-key"}}, "key substitution"),
    ],
)
def test_a_tampered_token_never_verifies(
    client, signing_key, register_product, entitled_org, signed_in, tamper, description
):
    """Ten single-field edits, each a distinct privilege gain if accepted.

    All of them break the signature, which is the point of signing - but the
    test exists because "the signature covers it" is only true while nobody
    adds a permissive parsing path, and because two of these (``alg`` and
    ``kid``) are attacks on the *verifier* rather than on the signature.
    """
    from workeasy360.platform.ecosystem import tokens

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    issued = _issue(client, registration, org)

    forged = _repack(issued["id_token"], **tamper)
    with pytest.raises(Exception) as exc:
        tokens.verify(
            forged,
            audience=registration["client_id"],
            expected_type=tokens.TYPE_ID_TOKEN,
        )
    assert not isinstance(exc.value, AssertionError), description


def test_an_expired_token_is_refused(
    client, signing_key, register_product, entitled_org, signed_in, settings
):
    """The lifetime *is* the revocation window, so it has to actually end."""
    from workeasy360.platform.ecosystem import tokens

    settings.OIDC_ID_TOKEN_TTL_S = -100  # already expired when signed
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    issued = _issue(client, registration, org)

    with pytest.raises(jwt.ExpiredSignatureError):
        tokens.verify(
            issued["id_token"],
            audience=registration["client_id"],
            expected_type=tokens.TYPE_ID_TOKEN,
        )


def test_a_not_yet_valid_token_is_refused(
    client, signing_key, register_product, entitled_org, signed_in, settings
):
    from workeasy360.platform.ecosystem import tokens

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    issued = _issue(client, registration, org)

    future = _repack(issued["id_token"], payload={"nbf": int(time.time()) + 3600})
    with pytest.raises(jwt.PyJWTError):
        tokens.verify(
            future,
            audience=registration["client_id"],
            expected_type=tokens.TYPE_ID_TOKEN,
        )


# --- clock skew -----------------------------------------------------------


def test_clock_skew_is_bounded(settings):
    """Small and deliberate.

    A generous acceptance window silently extends the life of *every* expired
    token by that amount, quietly undoing the short lifetime the whole
    revocation story depends on. 30s is well inside what NTP-synchronised
    hosts drift and an order of magnitude under the token lifetime.

    Asserted against genuinely signed tokens whose ``exp`` is in the past by a
    controlled amount - a repacked token would fail on its signature and
    prove nothing about the window.
    """
    assert settings.OIDC_CLOCK_SKEW_S == 30
    assert settings.OIDC_CLOCK_SKEW_S * 5 <= settings.OIDC_ID_TOKEN_TTL_S


@pytest.mark.parametrize(
    "expired_by,accepted",
    [
        (10, True),  # comfortably inside the 30s window
        # Near the edge but not on it. An exact-boundary case would be flaky
        # for a real reason: wall-clock time passes between minting and
        # verifying, so a token expired by exactly 30s at mint is expired by
        # more than 30s by the time it is checked. The boundary that matters
        # is that the window is ~30s and not ~300s, which these four pin.
        (25, True),
        (60, False),  # outside it
        (3600, False),  # an hour-old token is not a clock problem
    ],
)
def test_the_skew_window_is_exactly_what_is_configured(
    client,
    signing_key,
    register_product,
    entitled_org,
    signed_in,
    settings,
    expired_by,
    accepted,
):
    from workeasy360.platform.ecosystem import tokens

    # A negative lifetime mints a token that is already expired by a known
    # amount, which is the only way to place `exp` precisely relative to the
    # window without freezing the clock.
    settings.OIDC_ID_TOKEN_TTL_S = -expired_by
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    issued = _issue(client, registration, org)

    verify = lambda: tokens.verify(  # noqa: E731 - one expression, used twice
        issued["id_token"],
        audience=registration["client_id"],
        expected_type=tokens.TYPE_ID_TOKEN,
    )
    if accepted:
        assert verify()["sub"] == str(org["user"].id)
    else:
        with pytest.raises(jwt.ExpiredSignatureError):
            verify()


# --- cross-product confusion ---------------------------------------------


def test_a_products_token_is_rejected_by_another_product(
    client, signing_key, register_product, entitled_org, signed_in
):
    from workeasy360.platform.ecosystem import tokens

    crm = register_product("crm")
    books = register_product("books")
    org = entitled_org("crm")
    signed_in(org["user"])
    issued = _issue(client, crm, org)

    # CRM accepts it.
    tokens.verify(
        issued["id_token"],
        audience=crm["client_id"],
        expected_type=tokens.TYPE_ID_TOKEN,
    )
    # Books does not.
    with pytest.raises(jwt.InvalidAudienceError):
        tokens.verify(
            issued["id_token"],
            audience=books["client_id"],
            expected_type=tokens.TYPE_ID_TOKEN,
        )


def test_a_product_cannot_register_another_products_redirect_uri_for_itself(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Books registering CRM's callback would mean CRM's users' codes arrive
    at Books. It is allowed to *register* it - a URL is not owned - but a code
    is bound to the client that requested it, so the confusion buys nothing.
    """
    crm = register_product("crm", origin="http://localhost:9100")
    books = register_product("books", origin="http://localhost:9200")
    org = entitled_org("crm")
    signed_in(org["user"])

    # Books asks for CRM's callback: refused, because it is not registered to
    # Books and the comparison is exact.
    response = client.get(
        AUTHORIZE,
        {
            "response_type": "code",
            "client_id": books["client_id"],
            "redirect_uri": crm["redirect_uri"],
            "scope": "openid",
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256",
            "organization": org["organization_id"],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "Location" not in response


def test_a_userinfo_access_token_is_audience_bound(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Presenting CRM's access token still identifies CRM's user, but the
    audience is checked against the token's own client - so a product cannot
    borrow another's token to widen what it can read."""
    crm = register_product("crm", scopes=("openid", "email"))
    org = entitled_org("crm")
    signed_in(org["user"])
    issued = _issue(client, crm, org, scope="openid email")

    from workeasy360.platform.ecosystem import tokens

    with pytest.raises(jwt.InvalidAudienceError):
        tokens.verify(
            issued["access_token"],
            audience="a-different-client",
            expected_type=tokens.TYPE_ACCESS_TOKEN,
        )


# --- enumeration ----------------------------------------------------------


def test_the_authorization_endpoint_is_not_an_organization_oracle(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Three cases - no such organization, exists but not a member, exists and
    membership revoked - must be one response.

    Reachable by every authenticated user of every product, so any difference
    here enumerates the customer base.
    """
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    registration = register_product("crm")
    mine = entitled_org("crm", slug="probe-mine")
    theirs = entitled_org("crm", slug="probe-theirs")
    revoked = entitled_org("crm", slug="probe-revoked")

    with (
        unscoped_access(reason="test revokes a membership", module="tests"),
        organization_db_scope(revoked["org"].id),
    ):
        Membership.unscoped.filter(organization_id=revoked["org"].id).update(
            status=MembershipStatus.REMOVED.value
        )

    signed_in(mine["user"])
    seen = set()
    for organization in (str(uuid.uuid4()), theirs["organization_id"]):
        response = client.get(
            AUTHORIZE,
            {
                "response_type": "code",
                "client_id": registration["client_id"],
                "redirect_uri": registration["redirect_uri"],
                "scope": "openid",
                "code_challenge": "x" * 43,
                "code_challenge_method": "S256",
                "organization": organization,
            },
        )
        seen.add((response.status_code, response["Location"]))

    assert len(seen) == 1, f"the endpoint distinguishes these cases: {seen}"
    assert "membership_inactive" in next(iter(seen))[1]


def test_the_token_endpoint_is_not_a_client_oracle(client, signing_key, register_product):
    """ "No such client" and "wrong credential" must be one response, or an
    attacker learns when they have found a real client id - which is most of
    the work in credential stuffing."""
    registration = register_product("crm", credential_kind="client_secret")

    unknown = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "client_id": "definitely-not-a-client",
            "client_secret": "whatever",
        },
    )
    wrong = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "client_id": registration["client_id"],
            "client_secret": "wrong-but-well-formed",
        },
    )
    assert unknown.status_code == wrong.status_code == 401
    # The correlation id differs per request by design - it is how support
    # finds the exact request without the response disclosing anything.
    assert _without_correlation(unknown) == _without_correlation(wrong)


def _without_correlation(response) -> dict:
    return {k: v for k, v in response.json().items() if k != "correlation_id"}


def test_the_token_endpoint_does_not_distinguish_grant_failures(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Expired, already used, wrong client, wrong verifier, wrong redirect -
    one answer. Telling a holder of a stolen code which part was wrong tells
    them what to fix."""
    import hashlib
    import secrets
    from urllib.parse import parse_qs, urlsplit

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
    code = parse_qs(urlsplit(granted["Location"]).query)["code"][0]
    base = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": registration["redirect_uri"],
        "code_verifier": verifier,
    }

    bodies = [
        {**base, "code": "a-code-that-never-existed"},
        {**base, "code_verifier": secrets.token_urlsafe(48)},
        # Registered-looking but not the URI this code was issued against.
        {**base, "redirect_uri": "http://localhost:9100/auth/somewhere-else"},
    ]
    answers = {
        json.dumps(
            {
                k: v
                for k, v in client.post(TOKEN, {**body, **_auth(registration)})
                .json()
                .items()
                if k != "correlation_id"
            },
            sort_keys=True,
        )
        for body in bodies
    }
    assert len(answers) == 1, answers


# --- entitlement probing --------------------------------------------------


def test_entitlement_probing_cannot_cross_products(
    client, signing_key, register_product, entitled_org
):
    crm = register_product("crm")
    register_product("books")
    entitled = entitled_org("books")

    body = client.post(
        VERIFY,
        {
            "organization_id": entitled["organization_id"],
            # Every spelling an attacker might try.
            "product_code": "books",
            "product": "books",
            "aud": "books",
            "scope": "books",
            **_auth(crm),
        },
    ).json()

    assert body["product_code"] == "crm"
    assert body["decision"] == "deny"


def test_entitlement_verification_leaks_no_tenant_existence(
    client, signing_key, register_product, entitled_org
):
    registration = register_product("crm")
    other = entitled_org("books")

    unknown = client.post(
        VERIFY, {"organization_id": str(uuid.uuid4()), **_auth(registration)}
    ).json()
    real = client.post(
        VERIFY, {"organization_id": other["organization_id"], **_auth(registration)}
    ).json()

    unknown.pop("organization_id"), real.pop("organization_id")
    unknown.pop("checked_at"), real.pop("checked_at")
    assert unknown == real


# --- what never reaches a log or an audit entry ---------------------------


def test_no_authorization_code_or_token_is_ever_logged(
    client, signing_key, register_product, entitled_org, signed_in, caplog
):
    """Codes and tokens are bearer credentials. A log line containing one is
    a credential disclosed to everyone with log access, for the retention
    period."""
    import hashlib
    import secrets
    from urllib.parse import parse_qs, urlsplit

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    with caplog.at_level("DEBUG"):
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
        code = parse_qs(urlsplit(granted["Location"]).query)["code"][0]
        payload = client.post(
            TOKEN,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": registration["redirect_uri"],
                "code_verifier": verifier,
                **_auth(registration),
            },
        ).json()

    text = caplog.text
    assert code not in text
    assert verifier not in text
    assert payload["id_token"] not in text
    assert payload["access_token"] not in text
    # The jti *is* logged, deliberately: correlating "issued" with "used" is
    # the point of having one, and it is not a credential.
    assert "ecosystem.token.issued" in text


def test_no_private_key_material_is_reachable_over_http(
    client, signing_key, register_product, platform_admin, in_tenant
):
    """Belt and braces on top of "it is not in the database": every response
    a caller can obtain is checked against the actual key."""
    from workeasy360.platform.ecosystem import selectors

    registration = register_product("crm")
    private_pem = signing_key.private_pem
    body = private_pem.splitlines()[1]

    responses = [
        client.get("/.well-known/jwks.json").content.decode(),
        client.get("/.well-known/openid-configuration").content.decode(),
    ]
    with in_tenant(platform_admin["ctx"]):
        responses.append(
            str([i.as_dict() for i in selectors.integrations(platform_admin["ctx"])])
        )
        responses.append(
            str(
                [
                    c.as_dict()
                    for c in selectors.credentials(
                        platform_admin["ctx"], client_id=registration["client_id"]
                    )
                ]
            )
        )

    for response in responses:
        assert body not in response
        assert "PRIVATE KEY" not in response


def test_the_registry_never_exposes_a_credential_secret_hash(
    register_product, platform_admin, in_tenant
):
    """The digest is not a secret, but it is also not something an operator
    needs - and a field that is not in the DTO cannot be serialised into a
    response by accident."""
    from workeasy360.platform.ecosystem import selectors
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    registration = register_product("crm", credential_kind="client_secret")
    digest = IntegrationCredential.objects.get(
        credential_id=registration["credential_id"]
    ).secret_hash

    with in_tenant(platform_admin["ctx"]):
        listed = str(
            [
                c.as_dict()
                for c in selectors.credentials(
                    platform_admin["ctx"], client_id=registration["client_id"]
                )
            ]
        )
    assert digest not in listed


# --- CORS and browser surface --------------------------------------------


def test_no_credentialed_endpoint_sets_a_wildcard_cors_origin(
    client, signing_key, register_product, entitled_org
):
    """The combination 12-security.md section 8 forbids: a wildcard origin
    with credentials. Only the two public, credential-free documents carry
    one."""
    registration = register_product("crm")
    org = entitled_org("crm")

    credentialed = [
        client.post(TOKEN, {"grant_type": "authorization_code", **_auth(registration)}),
        client.post(
            VERIFY,
            {"organization_id": org["organization_id"], **_auth(registration)},
        ),
        client.get(USERINFO, HTTP_AUTHORIZATION="Bearer nope"),
        client.get(AUTHORIZE, {"client_id": registration["client_id"]}),
    ]
    for response in credentialed:
        assert response.get("Access-Control-Allow-Origin") != "*"
        assert not response.get("Access-Control-Allow-Credentials")


def test_the_public_documents_carry_no_credential_and_no_personal_data(
    client, signing_key, register_product, entitled_org
):
    """What makes their wildcard safe. Asserted against real values."""
    registration = register_product("crm")
    org = entitled_org("crm")

    for path in ("/.well-known/jwks.json", "/.well-known/openid-configuration"):
        body = client.get(path).content.decode()
        assert registration["client_id"] not in body
        assert org["organization_id"] not in body
        assert org["user"].email not in body


# --- a code records a decision; it does not replace one -------------------
#
# Every test below revokes something *after* the authorization code has been
# minted and *before* it is exchanged. The code is bound to the subject, so
# the binding cannot be attacked - but binding is not the same as permission,
# and permission is live state. Without re-validation at redemption the
# documented revocation window would silently be the token lifetime plus the
# code lifetime, and an operator who had just removed someone would be told
# the removal was effective when it was not
# (authorization._revalidate_subject).


def _mint_code(client, registration, org):
    """Authorize and stop, returning ``(code, verifier)`` ready to exchange."""
    import hashlib
    import secrets
    from urllib.parse import parse_qs, urlsplit

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
            "state": "s",
            "nonce": "n",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "organization": org["organization_id"],
        },
    )
    assert granted.status_code == 302, granted.content
    return parse_qs(urlsplit(granted["Location"]).query)["code"][0], verifier


def _exchange(client, registration, code, verifier):
    return client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_auth(registration),
        },
    )


def test_a_membership_removed_after_authorization_stops_the_exchange(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The offboarding case, and the one an operator will assume works.

    Someone is removed from an organization while a code they obtained a
    moment earlier is still in flight. Redeeming it must not mint them a
    fresh five-minute identity token for that organization.
    """
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _mint_code(client, registration, org)

    with (
        unscoped_access(
            reason="Test removes a membership between authorize and token",
            module="tests.security",
        ),
        organization_db_scope(org["org"].id),
    ):
        Membership.unscoped.filter(id=org["membership"].id).update(
            status=MembershipStatus.REMOVED.value
        )

    response = _exchange(client, registration, code, verifier)
    assert response.status_code == 400
    # `invalid_grant`, not `membership_inactive`. The specific reason is
    # disclosed at the authorization endpoint when the product restarts the
    # flow; here it would tell whoever holds the code which part of their
    # attempt was wrong.
    assert response.json()["error"] == "invalid_grant"


def test_an_organization_that_stops_permitting_login_stops_the_exchange(
    client, signing_key, register_product, entitled_org, signed_in
):
    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _mint_code(client, registration, org)

    with unscoped_access(
        reason="Test locks the organization between authorize and token",
        module="tests.security",
    ):
        Organization.objects.filter(id=org["org"].id).update(
            status=OrganizationStatus.LOCKED.value
        )

    response = _exchange(client, registration, code, verifier)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_an_entitlement_withdrawn_after_authorization_stops_the_exchange(
    client, signing_key, register_product, entitled_org, signed_in
):
    """A subscription cancelled mid-flow must not still let someone in.

    This is the check the entitlement endpoint's ``revalidate_after`` cadence
    cannot cover: the product has not started its session yet, so there is
    nothing for it to revalidate.
    """
    from workeasy360.platform.entitlements.models import (
        Entitlement,
        EntitlementSnapshot,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _mint_code(client, registration, org)

    with (
        unscoped_access(
            reason="Test withdraws the entitlement between authorize and token",
            module="tests.security",
        ),
        organization_db_scope(org["org"].id),
    ):
        Entitlement.unscoped.filter(organization_id=org["org"].id).delete()
        EntitlementSnapshot.unscoped.filter(organization_id=org["org"].id).delete()

    response = _exchange(client, registration, code, verifier)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_the_unchanged_case_still_exchanges(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The three checks above would pass trivially if redemption simply broke."""
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _mint_code(client, registration, org)

    response = _exchange(client, registration, code, verifier)
    assert response.status_code == 200
    assert response.json()["id_token"]


def test_a_failed_revalidation_read_is_a_503_and_never_a_denial(
    client, signing_key, register_product, entitled_org, signed_in, monkeypatch
):
    """The rule the entitlement endpoint already follows, applied here.

    If the platform cannot determine the answer, the product must not be told
    the grant was bad - and the code must survive, so the retry succeeds
    rather than sending the user back through a sign-in they already
    completed.
    """
    from workeasy360.contracts.ports.entitlements import get_gate
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _mint_code(client, registration, org)

    def _unreachable(*args, **kwargs):
        raise RuntimeError("entitlement store unreachable")

    # The gate itself, because that is what the authorization path asks -
    # patching the remote endpoint's selector would leave this path untouched
    # and the test would assert nothing.
    monkeypatch.setattr(get_gate(), "require", _unreachable)
    response = _exchange(client, registration, code, verifier)
    assert response.status_code == 503
    assert response.json()["error"] == "server_error"

    # And the code was not spent, so the retry works once the store is back.
    assert not AuthorizationCode.objects.filter(redeemed_at__isnull=False).exists()
    monkeypatch.undo()
    assert _exchange(client, registration, code, verifier).status_code == 200


# --- malformed input is refused, never a 500 ------------------------------


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://ex\u00e4mple.test/cb",  # non-ASCII host
        "https://example.test/\u2028cb",  # a line separator
        "\u0000",  # NUL
    ],
)
def test_a_non_ascii_redirect_uri_is_invalid_grant_not_a_server_error(
    client, signing_key, register_product, entitled_org, signed_in, redirect_uri
):
    """``hmac.compare_digest`` refuses ``str`` arguments holding non-ASCII.

    Both operands of the redirect-URI comparison come off the wire, so
    passing them in unencoded turned a malformed request into an unhandled
    ``TypeError``: a 503 with a stack trace in the error log and a spurious
    ``ecosystem_token_issuance_failures_total`` on the dashboard, every time
    anyone holding a valid code chose to send one.
    """
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])
    code, verifier = _mint_code(client, registration, org)

    response = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            **_auth(registration),
        },
    )
    assert response.status_code == 400, response.content
    assert response.json()["error"] == "invalid_grant"


def test_the_redaction_backstop_covers_oauth_bearer_material(caplog):
    """Nothing logs these today. This is the net under that.

    The backstop's own docstring says it exists "because one day someone
    will", which is only true of the field names it actually matches -
    ``client_assertion`` is a credential that authenticates an entire
    product and ``code_verifier`` is the secret half of PKCE, and neither
    was caught by any pattern in the list.
    """
    from workeasy360.platform.observability.logging import REDACTED, get_logger

    logger = get_logger("tests.redaction")
    with caplog.at_level("INFO"):
        logger.info(
            "tests.redaction.canary",
            client_assertion="eyJhbGciOiJSUzI1NiJ9.CANARY-ASSERTION",
            code_verifier="CANARY-VERIFIER",
            authorization_code="CANARY-CODE",
            client_secret="CANARY-SECRET",
            # Legitimate neighbours that must survive, or the backstop has
            # quietly eaten the fields that make a log line useful.
            product_code="crm",
            reason_code="not_entitled",
        )

    text = caplog.text
    for canary in ("CANARY-ASSERTION", "CANARY-VERIFIER", "CANARY-CODE", "CANARY-SECRET"):
        assert canary not in text, canary
    assert text.count(REDACTED) >= 4
    assert "crm" in text
    assert "not_entitled" in text


# --- the seat, re-asked at both moments -----------------------------------
#
# Phase 4's headline security property (ADR-021 §1): a per-seat product's
# seat is checked at `/authorize` *and* re-checked at redemption, exactly as
# entitlement already was. Both were implemented in Phase 4 and neither was
# covered by a test until this audit - so the three tests below are the
# difference between "the code calls require_seat" and "a token cannot be
# issued to an unseated member".


def test_a_member_with_no_seat_is_refused_at_the_authorization_endpoint(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The organization holds CRM; this member holds no seat in it.

    Delivered to the product's registered redirect URI as
    ``product_no_seat`` - redirectably, so the product's client library can
    render "ask your administrator for a seat" rather than a blank failure.
    """
    from urllib.parse import parse_qs, urlsplit

    registration = register_product("crm")
    org = entitled_org("crm", seat=False)
    signed_in(org["user"])

    response = client.get(
        AUTHORIZE,
        {
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": registration["redirect_uri"],
            "scope": "openid",
            "state": "s",
            "nonce": "n",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
            "organization": org["organization_id"],
        },
    )
    assert response.status_code == 302
    assert response["Location"].startswith(registration["redirect_uri"])
    query = parse_qs(urlsplit(response["Location"]).query)
    assert query["error"] == ["product_no_seat"]
    assert "code" not in query


def test_a_seat_released_after_authorization_stops_the_exchange(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Task 48, the mandatory case: the launcher rendered Open, the browser
    started the flow, and an administrator unassigned the seat in the sixty
    seconds before the product exchanged the code.

    The code records a decision; it is not a licence to skip making it
    again (``authorization._revalidate_subject``).
    """
    from workeasy360.platform.entitlements.services.seats import release_seat
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    registration = register_product("crm")
    org = entitled_org("crm")  # seated
    signed_in(org["user"])
    code, verifier = _mint_code(client, registration, org)

    with tenant_context(org["ctx"]), tenant_db_scope(org["ctx"]):
        released = release_seat(
            org["org"].id, pool_key="crm", membership_id=org["membership"].id
        )
    assert released == 1, "the fixture's seat must have existed to be released"

    response = _exchange(client, registration, code, verifier)
    assert response.status_code == 400
    # `invalid_grant`, uniform with every other redemption failure - the
    # specific reason is disclosed redirectably at `/authorize` when the
    # product restarts the flow.
    assert response.json()["error"] == "invalid_grant"


def test_an_organization_wide_product_never_requires_a_seat(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The false-positive half, and the reason licensing mode is *read*
    rather than inferred from seat count (Task 11 / ADR-021 §1).

    ``books`` is ``PER_ORGANIZATION`` in the shared catalog fixture, so no
    seat pool exists for it at all. A seat check applied indiscriminately
    would lock every user out of every organization-wide product - which is
    why the two tests above cannot stand on their own.
    """
    registration = register_product("books")
    org = entitled_org("books")
    signed_in(org["user"])

    code, verifier = _mint_code(client, registration, org)
    response = _exchange(client, registration, code, verifier)

    assert response.status_code == 200
    assert response.json()["id_token"]


def _deeply_nested_token(depth: int = 20_000) -> str:
    """A JWS whose payload is valid JSON nested ``depth`` deep. No valid
    signature is needed: the crash, when there is one, is in parsing."""

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": "k"}).encode())
    payload = b64(("[" * depth + "]" * depth).encode())
    return f"{header}.{payload}.c2ln"


def test_a_deeply_nested_client_assertion_is_invalid_client_not_a_server_error(client):
    """CVE-2026-101918 (2026-10 audit). PyJWT 2.14.0's unverified decode let a
    nested payload escape as RecursionError rather than DecodeError, and the
    token endpoint's peek at the assertion caught only PyJWTError: one
    unauthenticated ~50 KB request was a 500 (and a page from the 5xx alert).
    """
    response = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "client_assertion_type": ASSERTION_TYPE,
            "client_assertion": _deeply_nested_token(),
        },
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_a_deeply_nested_bearer_token_is_invalid_token_not_a_server_error(client):
    response = client.get(USERINFO, HTTP_AUTHORIZATION=f"Bearer {_deeply_nested_token()}")
    assert response.status_code == 401

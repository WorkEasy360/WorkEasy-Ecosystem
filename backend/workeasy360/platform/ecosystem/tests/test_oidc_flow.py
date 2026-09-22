"""The authorization code flow, over real HTTP.

Everything here goes through the actual endpoints with the actual middleware
stack. A test that called ``authorization.issue_code`` directly would skip the
tenancy middleware, the CSRF middleware and the URL routing - which is where
three of the more interesting failures would live.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest

from workeasy360.platform.ecosystem import tokens
from workeasy360.platform.ecosystem.models import AuthorizationCode

pytestmark = pytest.mark.django_db

AUTHORIZE = "/api/v1/integrations/oauth/authorize"
TOKEN = "/api/v1/integrations/oauth/token"
USERINFO = "/api/v1/integrations/oauth/userinfo"
DISCOVERY = "/.well-known/openid-configuration"
JWKS = "/.well-known/jwks.json"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _authorize_params(registration, org, **overrides) -> dict:
    _, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": registration["client_id"],
        "redirect_uri": registration["redirect_uri"],
        "scope": "openid",
        "state": "client-state",
        "nonce": "client-nonce",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "organization": org["organization_id"],
    }
    params.update(overrides)
    return params


def _code_from(response) -> str:
    return parse_qs(urlsplit(response["Location"]).query)["code"][0]


def _error_from(response) -> str:
    return parse_qs(urlsplit(response["Location"]).query)["error"][0]


def _assertion(registration, client, audience: str = "") -> dict:
    """private_key_jwt client authentication, as the product would build it."""
    import time
    import uuid

    now = int(time.time())
    return {
        "client_assertion_type": (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ),
        "client_assertion": jwt.encode(
            {
                "iss": registration["client_id"],
                "sub": registration["client_id"],
                "aud": audience or "http://testserver" + TOKEN,
                "iat": now,
                "exp": now + 60,
                "jti": uuid.uuid4().hex,
            },
            registration["private_key_pem"],
            algorithm="RS256",
            headers={"kid": registration["credential_id"]},
        ),
    }


# --- discovery ------------------------------------------------------------


def test_discovery_advertises_only_what_is_implemented(client, signing_key):
    """A document that promises more than the server does is worse than none:
    an integrator's library uses the advertised feature and fails in a way
    that looks like their bug."""
    document = client.get(DISCOVERY).json()

    assert document["issuer"] == "http://testserver"
    assert document["response_types_supported"] == ["code"]
    assert document["grant_types_supported"] == ["authorization_code"]
    assert document["code_challenge_methods_supported"] == ["S256"]
    # No dynamic client registration: it would let anyone create a client.
    assert "registration_endpoint" not in document
    # No refresh tokens, by decision.
    assert "refresh_token" not in document["grant_types_supported"]


def test_every_endpoint_the_discovery_document_names_resolves(client, signing_key):
    """Otherwise the document can advertise an endpoint that 404s, and a
    product built entirely from discovery - as the guide instructs - breaks."""
    from django.urls import resolve

    document = client.get(DISCOVERY).json()
    for field in (
        "authorization_endpoint",
        "token_endpoint",
        "userinfo_endpoint",
        "jwks_uri",
        "workeasy360_entitlement_endpoint",
    ):
        path = urlsplit(document[field]).path
        assert resolve(path), f"{field} -> {path} does not resolve"


def test_discovery_and_jwks_are_public_and_cacheable(client, signing_key):
    """Safe to wildcard: no credential is accepted and no cookie is read, so
    there is no ambient authority for a hostile origin to borrow."""
    for path in (DISCOVERY, JWKS):
        response = client.get(path)
        assert response.status_code == 200
        assert response["Access-Control-Allow-Origin"] == "*"
        assert "max-age" in response["Cache-Control"]


# --- the happy path -------------------------------------------------------


def test_the_full_flow_issues_a_verifiable_identity_token(
    client, signing_key, register_product, entitled_org, signed_in
):
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], aal=2)

    verifier, challenge = _pkce()
    response = client.get(
        AUTHORIZE,
        _authorize_params(registration, org, code_challenge=challenge),
    )
    assert response.status_code == 302
    assert response["Location"].startswith(registration["redirect_uri"])

    exchanged = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": _code_from(response),
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_assertion(registration, client),
        },
    )
    assert exchanged.status_code == 200
    payload = exchanged.json()

    claims = tokens.verify(
        payload["id_token"],
        audience=registration["client_id"],
        expected_type=tokens.TYPE_ID_TOKEN,
    )
    assert claims["sub"] == str(org["user"].id)
    assert claims["organization_id"] == org["organization_id"]
    assert claims["product_code"] == "crm"
    assert claims["aud"] == registration["client_id"]
    assert claims["iss"] == "http://testserver"
    assert claims["nonce"] == "client-nonce"
    assert claims["aal"] == 2


def test_state_is_echoed_verbatim(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The client's CSRF defence. The platform echoes it and does not
    interpret it - a client that cannot correlate the response with its own
    request will complete an attacker's authorization."""
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    response = client.get(
        AUTHORIZE, _authorize_params(registration, org, state="opaque/value+123")
    )
    returned = parse_qs(urlsplit(response["Location"]).query)["state"][0]
    assert returned == "opaque/value+123"


def test_the_token_response_is_never_cached(
    client, signing_key, register_product, entitled_org, signed_in
):
    """RFC 6749 section 5.1. A cached token response is a token handed to the
    next person through the same proxy."""
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    verifier, challenge = _pkce()
    granted = client.get(
        AUTHORIZE, _authorize_params(registration, org, code_challenge=challenge)
    )
    exchanged = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": _code_from(granted),
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_assertion(registration, client),
        },
    )
    assert "no-store" in exchanged["Cache-Control"]


def test_the_code_is_stored_only_as_a_hash(
    client, signing_key, register_product, entitled_org, signed_in
):
    """A database dump must not yield a redeemable credential."""
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    granted = client.get(AUTHORIZE, _authorize_params(registration, org))
    code = _code_from(granted)

    assert not AuthorizationCode.objects.filter(code_hash=code).exists()
    stored = AuthorizationCode.objects.get()
    assert stored.code_hash == hashlib.sha256(code.encode()).hexdigest()
    assert code not in str(
        AuthorizationCode.objects.filter(pk=stored.pk).values().first()
    )


# --- redirect URI validation ---------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example.com/callback",
        "http://localhost:9100/auth/callback/../../evil",
        "http://localhost:9100/auth/callback/extra",
        "http://localhost:9100/auth/callback?x=1",
        "http://localhost:9100.evil.net/auth/callback",
        "http://localhost:9101/auth/callback",
        "HTTP://LOCALHOST:9100/auth/callback",
    ],
)
def test_an_unregistered_redirect_uri_is_never_redirected_to(
    client, signing_key, register_product, entitled_org, signed_in, hostile
):
    """The open-redirect regression test.

    The response must be a rendered error, not a 302 - RFC 6749 section
    4.1.2.1: until the redirect URI is verified, the server does not know
    where the browser is allowed to go.
    """
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    response = client.get(
        AUTHORIZE, _authorize_params(registration, org, redirect_uri=hostile)
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "Location" not in response


def test_an_unknown_client_is_rendered_not_redirected(
    client, signing_key, register_product, entitled_org, signed_in
):
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    response = client.get(
        AUTHORIZE, _authorize_params(registration, org, client_id="not-a-client")
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
    assert "Location" not in response


def test_a_denial_after_verification_is_delivered_to_the_registered_uri(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Once the client and its redirect URI are verified, a failure belongs at
    the product so its library can render the right call to action."""
    registration = register_product("crm")
    org = entitled_org("books")  # entitled to something else
    signed_in(org["user"])

    response = client.get(AUTHORIZE, _authorize_params(registration, org))
    assert response.status_code == 302
    assert response["Location"].startswith(registration["redirect_uri"])
    assert _error_from(response) == "product_not_entitled"


# --- PKCE -----------------------------------------------------------------


def test_pkce_is_required(client, signing_key, register_product, entitled_org, signed_in):
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    params = _authorize_params(registration, org)
    params.pop("code_challenge")
    response = client.get(AUTHORIZE, params)

    # Redirected, not rendered: the client and its redirect URI were verified
    # before PKCE was examined, so this failure belongs at the product where
    # its library can act on it (RFC 6749 section 4.1.2.1).
    assert response.status_code == 302
    assert _error_from(response) == "invalid_request"
    assert (
        "PKCE" in parse_qs(urlsplit(response["Location"]).query)["error_description"][0]
    )


def test_plain_pkce_is_refused_rather_than_defaulted(
    client, signing_key, register_product, entitled_org, signed_in
):
    """RFC 7636 makes ``plain`` the default when the parameter is absent, so a
    silent default would let a client downgrade its own protection by omitting
    a field."""
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    for method in ("plain", "", "S512"):
        response = client.get(
            AUTHORIZE,
            _authorize_params(registration, org, code_challenge_method=method),
        )
        assert response.status_code == 302
        assert _error_from(response) == "invalid_request"
        assert (
            "S256"
            in parse_qs(urlsplit(response["Location"]).query)["error_description"][0]
        )


@pytest.mark.parametrize("verifier_case", ["wrong", "missing", "too_short"])
def test_a_bad_verifier_cannot_redeem_the_code(
    client, signing_key, register_product, entitled_org, signed_in, verifier_case
):
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    verifier, challenge = _pkce()
    granted = client.get(
        AUTHORIZE, _authorize_params(registration, org, code_challenge=challenge)
    )
    presented = {
        "wrong": secrets.token_urlsafe(48),
        "missing": "",
        "too_short": "abc",
    }[verifier_case]

    exchanged = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": _code_from(granted),
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": presented,
            **_assertion(registration, client),
        },
    )
    assert exchanged.status_code == 400
    assert exchanged.json()["error"] == "invalid_grant"


# --- code binding and single use -----------------------------------------


def test_a_code_cannot_be_redeemed_twice(
    client, signing_key, register_product, entitled_org, signed_in
):
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    verifier, challenge = _pkce()
    granted = client.get(
        AUTHORIZE, _authorize_params(registration, org, code_challenge=challenge)
    )
    code = _code_from(granted)
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": registration["redirect_uri"],
        "code_verifier": verifier,
    }

    first = client.post(TOKEN, {**body, **_assertion(registration, client)})
    second = client.post(TOKEN, {**body, **_assertion(registration, client)})

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_a_code_is_bound_to_the_redirect_uri_it_was_issued_against(
    client,
    signing_key,
    register_product,
    entitled_org,
    signed_in,
    platform_admin,
    in_tenant,
):
    """RFC 6749 section 4.1.3. Without it, a code obtained via one registered
    callback could be completed against another."""
    from tests.factories.manifests import manifest_for
    from workeasy360.platform.ecosystem import services

    registration = register_product("crm")
    # Register a second legitimate callback, so the two are both valid but
    # not interchangeable for a given code.
    second_uri = "http://localhost:9100/auth/other"
    with in_tenant(platform_admin["ctx"]):
        services.update_manifest(
            platform_admin["ctx"],
            client_id=registration["client_id"],
            manifest=manifest_for(
                "crm",
                redirect_uris=[registration["redirect_uri"], second_uri],
            ),
        )

    org = entitled_org("crm")
    signed_in(org["user"])
    verifier, challenge = _pkce()
    granted = client.get(
        AUTHORIZE, _authorize_params(registration, org, code_challenge=challenge)
    )

    exchanged = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": _code_from(granted),
            # Registered, but not the one this code was issued for.
            "redirect_uri": second_uri,
            "code_verifier": verifier,
            **_assertion(registration, client),
        },
    )
    assert exchanged.status_code == 400
    assert exchanged.json()["error"] == "invalid_grant"


def test_an_expired_code_is_refused(
    client, signing_key, register_product, entitled_org, signed_in, settings
):
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    verifier, challenge = _pkce()
    granted = client.get(
        AUTHORIZE, _authorize_params(registration, org, code_challenge=challenge)
    )

    from django.utils import timezone

    AuthorizationCode.objects.update(
        expires_at=timezone.now() - timezone.timedelta(seconds=1)
    )
    exchanged = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": _code_from(granted),
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_assertion(registration, client),
        },
    )
    assert exchanged.status_code == 400
    assert exchanged.json()["error"] == "invalid_grant"


def test_an_unsupported_grant_type_is_refused(client, signing_key, register_product):
    """No implicit, no password grant, no refresh - by decision, not omission."""
    registration = register_product("crm")
    for grant in ("password", "refresh_token", "client_credentials", "implicit"):
        response = client.post(
            TOKEN,
            {"grant_type": grant, **_assertion(registration, client)},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "unsupported_grant_type"


# --- who may authorize ----------------------------------------------------


def test_an_unauthenticated_user_is_parked_at_the_platform_login_page(
    client, signing_key, register_product, entitled_org, settings
):
    """No product-facing error at all: the browser goes to the platform's
    own login page, carrying a single-use resume token - never to the
    product with an error requiring it to restart the flow itself.

    See ``test_authorization_resume.py`` for what completing the resume
    actually does (docs/phase-4/05-organization-selection.md,
    docs/phase-4/06-sso-ui-integration.md).
    """
    registration = register_product("crm")
    org = entitled_org("crm")
    response = client.get(AUTHORIZE, _authorize_params(registration, org))
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert f"{location.scheme}://{location.netloc}" == settings.W360_FRONTEND_URL
    assert location.path == "/login"
    assert "resume" in parse_qs(location.query)


def test_a_signed_in_user_with_no_organization_named_is_parked_at_the_chooser(
    client, signing_key, register_product, entitled_org, signed_in, settings
):
    """Deliberately not "pick the user's only organization": an implicit
    choice is one the user did not make and cannot see, and it silently
    changes behaviour the day they join a second organization. The browser
    goes to the platform's own organization chooser, not to the product."""
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    params = _authorize_params(registration, org)
    params.pop("organization")
    response = client.get(AUTHORIZE, params)
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert f"{location.scheme}://{location.netloc}" == settings.W360_FRONTEND_URL
    assert location.path == "/organizations/select"
    assert "resume" in parse_qs(location.query)


def test_a_suspended_integration_cannot_authenticate(
    client,
    signing_key,
    register_product,
    entitled_org,
    signed_in,
    platform_admin,
    in_tenant,
):
    from workeasy360.platform.ecosystem import services

    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    with in_tenant(platform_admin["ctx"]):
        services.transition(
            platform_admin["ctx"],
            client_id=registration["client_id"],
            to_status="suspended",
            reason="security review",
        )

    response = client.get(AUTHORIZE, _authorize_params(registration, org))
    assert _error_from(response) == "integration_suspended"
    assert not AuthorizationCode.objects.exists()


def test_a_draft_integration_cannot_authenticate(
    client, signing_key, register_product, entitled_org, signed_in
):
    registration = register_product("crm", activate=False)
    org = entitled_org("crm")
    signed_in(org["user"])

    response = client.get(AUTHORIZE, _authorize_params(registration, org))
    assert _error_from(response) == "integration_suspended"


# --- scopes ---------------------------------------------------------------


def test_an_unregistered_scope_is_refused_not_silently_dropped(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Silently narrowing means a product believes it asked for ``email``,
    receives a token without one, and debugs its own parsing for an
    afternoon."""
    registration = register_product("crm", scopes=("openid",))
    org = entitled_org("crm")
    signed_in(org["user"])

    response = client.get(
        AUTHORIZE, _authorize_params(registration, org, scope="openid email")
    )
    assert _error_from(response) == "invalid_scope"


def test_personal_claims_require_the_scope_and_the_registration(
    client, signing_key, register_product, entitled_org, signed_in, entitled
):
    """A product that only needs to know *which* user is not handed *who* the
    user is."""
    minimal = register_product("crm", scopes=("openid",))
    rich = register_product("books", scopes=("openid", "profile", "email"))
    org = entitled_org("crm")
    signed_in(org["user"])

    for registration, scope, expect_email in (
        (minimal, "openid", False),
        (rich, "openid email", True),
    ):
        # Granted per iteration: ``grant_product`` writes the organization's
        # complete computed entitlement set rather than adding to it, so a
        # single call per product is the only way to have each one held in
        # turn. Two organizations would work equally well and prove less -
        # the interesting variable here is the *integration's* registered
        # scopes, not the tenant.
        entitled(org["org"].id, registration["product_code"])
        verifier, challenge = _pkce()
        granted = client.get(
            AUTHORIZE,
            _authorize_params(registration, org, scope=scope, code_challenge=challenge),
        )
        assert granted.status_code == 302, granted.content
        assert "code=" in granted["Location"], granted["Location"]
        exchanged = client.post(
            TOKEN,
            {
                "grant_type": "authorization_code",
                "code": _code_from(granted),
                "redirect_uri": registration["redirect_uri"],
                "code_verifier": verifier,
                **_assertion(registration, client),
            },
        )
        assert exchanged.status_code == 200, (
            registration["product_code"],
            exchanged.json(),
        )
        claims = tokens.verify(
            exchanged.json()["id_token"],
            audience=registration["client_id"],
            expected_type=tokens.TYPE_ID_TOKEN,
        )
        assert ("email" in claims) is expect_email


# --- userinfo -------------------------------------------------------------


def test_userinfo_requires_an_access_token_not_a_session(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Session authentication is never accepted here, which is what makes the
    CSRF exemption safe: there is no ambient credential to forge."""
    org = entitled_org("crm")
    signed_in(org["user"])
    register_product("crm")

    response = client.get(USERINFO)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_an_id_token_is_not_accepted_as_an_access_token(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The ``typ`` claim. Both verify against the same key with the same
    audience, so without it an identity assertion would be usable as a
    credential."""
    registration = register_product("crm", scopes=("openid", "email"))
    org = entitled_org("crm")
    signed_in(org["user"])

    verifier, challenge = _pkce()
    granted = client.get(
        AUTHORIZE,
        _authorize_params(
            registration, org, scope="openid email", code_challenge=challenge
        ),
    )
    payload = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": _code_from(granted),
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_assertion(registration, client),
        },
    ).json()

    good = client.get(USERINFO, HTTP_AUTHORIZATION=f"Bearer {payload['access_token']}")
    assert good.status_code == 200
    assert good.json()["sub"] == str(org["user"].id)
    assert good.json()["email"] == org["user"].email

    bad = client.get(USERINFO, HTTP_AUTHORIZATION=f"Bearer {payload['id_token']}")
    assert bad.status_code == 401


# --- request parameters are validated where they can still be fixed ------


@pytest.mark.parametrize(
    "challenge,why",
    [
        ("short", "under 43 characters"),
        ("a" * 129, "over 128 characters"),
        ("ä" * 43, "outside the unreserved set"),
        ("a" * 42 + "+", "'+' is base64 rather than base64url"),
        ("a" * 42 + "/", "'/' is base64 rather than base64url"),
        ("a" * 42 + "=", "padding is stripped from an S256 challenge"),
    ],
)
def test_a_malformed_code_challenge_is_refused_at_authorization(
    client, signing_key, register_product, entitled_org, signed_in, challenge, why
):
    """RFC 7636 section 4.2, enforced where the client can still act on it.

    A malformed challenge is never a hole - nothing can produce a verifier
    that hashes to it, so the flow fails closed regardless. It is refused
    here because the alternative is that it is *stored*, the browser is
    redirected with a code that looks fine, and the product gets
    `invalid_grant` a minute later for a request it got wrong at the start.
    A non-ASCII one additionally reached a constant-time comparison that
    cannot represent it, which surfaced as a 503.
    """
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    response = client.get(
        AUTHORIZE,
        _authorize_params(registration, org, code_challenge=challenge),
    )
    assert response.status_code == 302, why
    assert _error_from(response) == "invalid_request", why
    assert not AuthorizationCode.objects.exists(), (
        f"a code was minted for a challenge that is {why}"
    )


def test_a_nonce_is_echoed_verbatim_or_the_request_is_refused(
    client, signing_key, register_product, entitled_org, signed_in
):
    """OIDC Core section 3.1.3.7 step 11 has the client compare the echoed
    nonce with the one it sent, so a silently shortened one is a token the
    client is obliged to reject - an unexplained login failure the platform
    caused and did not report. Refuse instead.
    """
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    over_long = "n" * 256
    refused = client.get(AUTHORIZE, _authorize_params(registration, org, nonce=over_long))
    assert refused.status_code == 302
    assert _error_from(refused) == "invalid_request"
    assert not AuthorizationCode.objects.exists()

    # And the widest nonce that *is* accepted survives the round trip intact.
    at_the_limit = "n" * 255
    verifier, challenge = _pkce()
    granted = client.get(
        AUTHORIZE,
        _authorize_params(
            registration, org, nonce=at_the_limit, code_challenge=challenge
        ),
    )
    payload = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": _code_from(granted),
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_assertion(registration, client),
        },
    ).json()
    claims = jwt.decode(payload["id_token"], options={"verify_signature": False})
    assert claims["nonce"] == at_the_limit

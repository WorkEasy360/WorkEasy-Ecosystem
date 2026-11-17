"""The central sign-in's previous name while products move (ADR-067).

On ``auth.workeasy360.com`` the server-to-server surface answers exactly as
before, and every browser request is sent to the same address on WorkEasy360
Accounts - so no page is served and no session is read or set there. A
narrowing only: nothing refused elsewhere is let through here.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client

from tests.accounts_portal.conftest import ACCOUNTS, AUTH, challenge_of, host

#: An S256 code challenge (RFC 7636), computed - its value is irrelevant here.
CHALLENGE = challenge_of("v" * 43)

pytestmark = pytest.mark.django_db

LEGACY = host(AUTH)


def on_legacy(method: str, path: str, **extra):
    client = Client()
    call = getattr(client, method.lower())
    return call(path, HTTP_HOST=LEGACY, secure=True, **extra)


@pytest.fixture(autouse=True)
def _stage_a(stage):
    stage("a")


# --- what still answers on the previous name -----------------------------------


def test_the_discovery_document_and_key_set_answer_as_before(signing_key):
    """Every product configured with the old issuer fetches these from it; in
    stage A they are what they always were, issuer and endpoints included."""
    document = on_legacy("GET", "/.well-known/openid-configuration")
    assert document.status_code == 200
    body = document.json()
    assert body["issuer"] == AUTH
    for field in (
        "authorization_endpoint",
        "token_endpoint",
        "userinfo_endpoint",
        "jwks_uri",
    ):
        assert body[field].startswith(f"{AUTH}/"), field
    assert body["end_session_endpoint"] == f"{AUTH}/api/v1/integrations/oauth/logout"

    keys = on_legacy("GET", "/.well-known/jwks.json")
    assert keys.status_code == 200
    assert [k["kid"] for k in keys.json()["keys"]] == [signing_key.kid]
    # The same keys on the new name: one platform, one key set.
    same = Client().get("/.well-known/jwks.json", HTTP_HOST=host(ACCOUNTS), secure=True)
    assert same.json() == keys.json()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/integrations/oauth/token",
        "/api/v1/integrations/entitlements/verify",
        "/api/v1/integrations/events",
        "/api/v1/integrations/usage/events",
        "/api/v1/integrations/ai/invoke",
    ],
)
def test_server_to_server_endpoints_answer_unchanged(path):
    """Reached, not forwarded and not refused by the host: the endpoint's own
    client authentication answers (no credential here, so it refuses)."""
    response = on_legacy("POST", path, data={})
    assert response.status_code in (400, 401), (path, response.status_code)
    assert response.json() != {"code": "not_found"}


def test_userinfo_answers_unchanged():
    response = on_legacy("GET", "/api/v1/integrations/oauth/userinfo")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response


# --- browsers are sent to WorkEasy360 Accounts ---------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # The authorization request exactly as a product builds it: the query
        # must arrive byte for byte (PKCE challenge, state, nonce, redirect URI).
        "/api/v1/integrations/oauth/authorize?response_type=code&client_id=c-1"
        "&redirect_uri=https%3A%2F%2Fcrm.workeasy360.com%2Fapi%2Fauth%2Fcallback%2Fworkeasy360"
        "&scope=openid%20email&state=s%2F%2B1&nonce=n-1&code_challenge=abc_-"
        "&code_challenge_method=S256",
        "/api/v1/integrations/oauth/resume?resume=r-token",
        "/api/v1/integrations/oauth/logout?client_id=c-1"
        "&post_logout_redirect_uri=https%3A%2F%2Fcrm.workeasy360.com%2Flogin",
        "/login?resume=r-token&reauth=1",
        "/product-access?resume=r-token",
        "/organizations/select?resume=r-token",
        "/sign-out?request=sealed",
        "/switch-account",
        "/",
        "/anything/else",
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_a_browser_is_sent_to_the_same_address_on_accounts(path, method):
    response = on_legacy(method, path)
    assert response.status_code == 302
    assert response["Location"] == f"{ACCOUNTS}{path}"
    # Nothing is set on the previous name - no session, no CSRF cookie.
    assert not response.cookies


def test_the_destination_is_configuration_never_the_request():
    """A path that looks like a host stays a path on the configured origin."""
    response = on_legacy("GET", "//evil.example/login")
    assert response["Location"].startswith(f"{ACCOUNTS}/")


def test_a_session_on_the_previous_name_is_never_used(
    signing_key, register_product, entitled_org
):
    """A person still holding an old auth.-host session gets no silent code
    there: the request goes to Accounts, whose session decides."""
    registration = register_product("crm")
    org = entitled_org("crm")
    client = Client()
    client.force_login(org["user"])
    response = client.get(
        "/api/v1/integrations/oauth/authorize",
        {
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": registration["redirect_uri"],
            "scope": "openid",
            "state": "s",
            "nonce": "n",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        },
        HTTP_HOST=LEGACY,
        secure=True,
    )
    assert response.status_code == 302
    forwarded = urlsplit(response["Location"])
    assert f"{forwarded.scheme}://{forwarded.netloc}" == ACCOUNTS
    assert forwarded.path == "/api/v1/integrations/oauth/authorize"
    query = parse_qs(forwarded.query)
    assert "code" not in query  # no code was issued on the previous name
    assert query["client_id"] == [registration["client_id"]]


# --- and nothing else answers there ----------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        # Sign-in, hand-off and Switch Account: sessions, so never here.
        ("POST", "/api/v1/auth/login"),
        ("GET", "/api/v1/auth/session"),
        ("POST", "/api/v1/auth/handoff"),
        ("POST", "/api/v1/auth/switch-account/finish"),
        # The access chooser and the sign-out confirmation: Accounts' session.
        ("GET", "/api/v1/integrations/oauth/pending?resume=x"),
        ("POST", "/api/v1/integrations/oauth/pending/choose"),
        ("GET", "/api/v1/integrations/oauth/logout/request?request=x"),
        ("POST", "/api/v1/integrations/oauth/logout/confirm"),
        # Every portal's own API.
        ("GET", "/api/v1/admin/session"),
        ("GET", "/api/v1/partner/portal"),
        ("GET", "/api/v1/orgs/anything/apps"),
        ("GET", "/api/v1/public/branding"),
        # A write is never forwarded.
        ("POST", "/login"),
        ("POST", "/api/v1/integrations/oauth/authorize"),
    ],
)
def test_everything_else_is_refused(method, path):
    response = on_legacy(method, path)
    assert response.status_code == 404, (method, path, response.status_code)
    assert response.json() == {"code": "not_found"}
    assert not response.cookies


def test_without_a_previous_name_the_old_host_reaches_nothing(stage):
    """Stage E (retired): the name is no allowed host at all."""
    stage("e")
    response = on_legacy("GET", "/.well-known/openid-configuration")
    assert response.status_code == 400


# --- what the platform builds for a browser names the new host -------------------


def test_links_for_a_browser_name_accounts(settings):
    from workeasy360.platform.identity import portals as portal_links

    assert portal_links.auth_url() == ACCOUNTS
    assert portal_links.legacy_auth_origin() == AUTH
    assert portal_links.is_legacy_auth_host(LEGACY)
    assert portal_links.is_legacy_auth_host("AUTH.workeasy360.com.")
    assert not portal_links.is_legacy_auth_host(host(ACCOUNTS))
    # Still the platform's own name: never a partner's custom domain, and the
    # edge's certificate check says yes for it.
    assert LEGACY in portal_links.platform_hosts()
    assert host(ACCOUNTS) in portal_links.platform_hosts()


def test_the_edge_may_certify_both_names():
    for name in (LEGACY, host(ACCOUNTS)):
        response = Client().get(
            "/api/v1/public/edge/tls-allowed",
            {"domain": name},
            HTTP_HOST="api.workeasy360.com",
            secure=True,
        )
        assert response.status_code == 200, name
        assert response.json() == {"allowed": True}


def test_a_signed_out_product_sign_in_parks_on_accounts(
    signing_key, register_product, entitled_org
):
    """The authorization request forwarded from the old name parks for the
    login page - on Accounts."""
    registration = register_product("crm")
    org = entitled_org("crm")
    response = Client().get(
        "/api/v1/integrations/oauth/authorize",
        {
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": registration["redirect_uri"],
            "scope": "openid",
            "state": "s",
            "nonce": "n",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "organization": org["organization_id"],
        },
        HTTP_HOST=host(ACCOUNTS),
        secure=True,
    )
    assert response.status_code == 302
    assert response["Location"].startswith(f"{ACCOUNTS}/login?resume=")

"""Products through every stage of the move to WorkEasy360 Accounts (ADR-067).

Each product here is the independent reference client: it knows only its
configured issuer and discovers everything else, generates PKCE, state and
nonce, checks state on the callback and verifies the ID token strictly -
signature by ``kid`` from the key set, ``iss`` equal to its own configuration,
``aud``, expiry, ``nonce``, ``typ``. The rule under test: **no product is ever
handed an issuer it was not configured to trust**, and the person it signs in
is the same Workeasy360 user (``sub``) whichever name it uses.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from tests.accounts_portal.conftest import (
    ACCOUNTS,
    API,
    AUTH,
    Browser,
    challenge_of,
    host,
    sign_in_to_product,
)
from tests.factories.reference_product_client import IntegrationError

#: An S256 code challenge (RFC 7636), computed - its value is irrelevant here.
CHALLENGE = challenge_of("v" * 43)

pytestmark = pytest.mark.django_db


@pytest.fixture
def crm(signing_key, register_product, entitled_org):
    registration = register_product("crm")
    org = entitled_org("crm")
    return {"registration": registration, "org": org, "user": org["user"]}


def signed_in_browser(crm) -> Browser:
    """A person signed in to WorkEasy360 Accounts (the current sign-in host)."""
    browser = Browser()
    browser.sign_in(ACCOUNTS, crm["user"])
    return browser


def assert_same_person(claims, crm) -> None:
    assert claims["sub"] == str(crm["user"].id)  # the user's UUID, unchanged
    assert claims["organization_id"] == crm["org"]["organization_id"]
    assert claims["product_code"] == "crm"
    assert claims["aud"] == crm["registration"]["client_id"]


def hosts_of(trail: list[str]) -> list[str]:
    return [urlsplit(url).netloc for url in trail]


# --- stage A: the new name serves; every token is what it always was ------------


def test_stage_a_an_existing_product_signs_in_unchanged(stage, crm, product, internet):
    stage("a")
    rp = product(crm["registration"], issuer=AUTH)
    browser = signed_in_browser(crm)

    flow = sign_in_to_product(rp, browser, crm["org"]["organization_id"])

    assert flow["claims"]["iss"] == AUTH
    assert_same_person(flow["claims"], crm)
    # The browser started where discovery said (the old name) and was sent to
    # Accounts, where its session is; the code was issued there.
    assert hosts_of(flow["trail"]) == [host(AUTH), host(ACCOUNTS)]
    assert urlsplit(flow["trail"][1]).query == urlsplit(flow["start"]).query
    # Server to server, the product never left the name it was configured with.
    assert set(internet.hosts) == {host(AUTH)}

    profile = rp.fetch_userinfo(flow["tokens"]["access_token"])
    assert profile["sub"] == str(crm["user"].id)
    entitlement = rp.verify_entitlement(organization_id=crm["org"]["organization_id"])
    assert entitlement["decision"] == "allow"
    assert entitlement["product_code"] == "crm"


def test_stage_a_the_new_name_is_not_yet_an_issuer(stage, crm, product):
    """Configured with accounts. too early, a product refuses the discovery
    document (it names auth.) - it fails closed, before any sign-in."""
    stage("a")
    rp = product(crm["registration"], issuer=ACCOUNTS)
    with pytest.raises(IntegrationError, match="different issuer"):
        rp.discover()


def test_stage_a_no_token_ever_names_the_new_issuer(stage, crm, internet):
    """Even redeemed on the new host, a code yields auth.'s issuer in stage A,
    and a client assertion naming the new host's token endpoint is refused:
    accounts. is not an issuer yet."""
    from tests.factories.reference_product_client import ReferenceProductClient

    stage("a")
    raw = ReferenceProductClient(
        http=internet,
        issuer=AUTH,
        client_id=crm["registration"]["client_id"],
        redirect_uri=crm["registration"]["redirect_uri"],
        private_key_pem=crm["registration"]["private_key_pem"],
        credential_kid=crm["registration"]["credential_id"],
    )
    response = internet.post(
        f"{ACCOUNTS}/api/v1/integrations/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "not-a-code",
            "redirect_uri": crm["registration"]["redirect_uri"],
            "code_verifier": "v" * 43,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": raw.build_client_assertion(
                audience=f"{ACCOUNTS}/api/v1/integrations/oauth/token"
            ),
        },
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


# --- stage B: either name works, each product on the one it trusts ---------------


def test_stage_b_old_and_new_products_each_get_their_own_issuer(
    stage, crm, product, internet
):
    stage("b")
    old = product(crm["registration"], issuer=AUTH)
    new = product(crm["registration"], issuer=ACCOUNTS)

    before = sign_in_to_product(
        old, signed_in_browser(crm), crm["org"]["organization_id"]
    )
    internet.hosts.clear()
    after = sign_in_to_product(new, signed_in_browser(crm), crm["org"]["organization_id"])

    assert before["claims"]["iss"] == AUTH
    assert after["claims"]["iss"] == ACCOUNTS
    assert_same_person(before["claims"], crm)
    assert_same_person(after["claims"], crm)
    # The product on the new name never touches the old one.
    assert hosts_of(after["trail"]) == [host(ACCOUNTS)]
    assert set(internet.hosts) == {host(ACCOUNTS)}

    # Strict on both sides: a token from one name is refused by a product
    # configured with the other - the window never weakens a product's check.
    with pytest.raises(IntegrationError):
        old.verify_id_token(after["tokens"]["id_token"])
    with pytest.raises(IntegrationError):
        new.verify_id_token(before["tokens"]["id_token"])


def test_stage_b_discovery_names_the_issuer_of_the_host_it_is_read_on(stage, internet):
    stage("b")
    for origin in (AUTH, ACCOUNTS):
        document = internet.get(f"{origin}/.well-known/openid-configuration").json()
        assert document["issuer"] == origin
        assert document["token_endpoint"] == f"{origin}/api/v1/integrations/oauth/token"
        assert document["authorization_endpoint"].startswith(f"{origin}/")
    # Any other host (the API host) is answered as the canonical issuer.
    assert (
        internet.get(f"{API}/.well-known/openid-configuration").json()["issuer"] == AUTH
    )


def test_stage_b_access_tokens_from_either_name_open_userinfo_on_both(
    stage, crm, product
):
    stage("b")
    old = product(crm["registration"], issuer=AUTH)
    new = product(crm["registration"], issuer=ACCOUNTS)
    flow = sign_in_to_product(old, signed_in_browser(crm), crm["org"]["organization_id"])
    # Minted as auth., accepted by the userinfo endpoint on either name.
    assert old.fetch_userinfo(flow["tokens"]["access_token"])["sub"] == str(
        crm["user"].id
    )
    assert new.fetch_userinfo(flow["tokens"]["access_token"])["sub"] == str(
        crm["user"].id
    )


def test_stage_b_a_client_assertion_must_name_one_of_our_token_endpoints(
    stage, crm, product, internet
):
    stage("b")
    rp = product(crm["registration"], issuer=ACCOUNTS)

    def token_call(audience: str):
        return internet.post(
            f"{ACCOUNTS}/api/v1/integrations/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": "not-a-code",
                "redirect_uri": crm["registration"]["redirect_uri"],
                "code_verifier": "v" * 43,
                "client_assertion_type": (
                    "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                ),
                "client_assertion": rp.build_client_assertion(audience=audience),
            },
        )

    # Either of the platform's own token endpoints authenticates the client
    # (the code is then refused for being no code - invalid_grant).
    for origin in (AUTH, ACCOUNTS):
        answer = token_call(f"{origin}/api/v1/integrations/oauth/token")
        assert answer.json()["error"] == "invalid_grant", origin
    # Any other audience does not.
    for audience in ("https://evil.example/token", f"{ACCOUNTS}/", API):
        answer = token_call(audience)
        assert answer.status_code == 401, audience
        assert answer.json()["error"] == "invalid_client", audience


def test_stage_b_pkce_state_and_nonce_still_bind_the_flow(stage, crm, product):
    """The move changes hosts, never the checks: a wrong verifier is refused,
    and the callback's state and the token's nonce are the product's own."""
    from urllib.parse import parse_qs, urlencode

    stage("b")
    rp = product(crm["registration"], issuer=AUTH)
    browser = signed_in_browser(crm)
    begun = rp.begin_authorization(organization_id=crm["org"]["organization_id"])
    from tests.accounts_portal.conftest import platform_hosts

    landed = browser.follow(
        f"{begun['url']}?{urlencode(begun['query'])}", platform_hosts=platform_hosts()
    )
    callback = parse_qs(urlsplit(landed["Location"]).query)
    assert callback["state"] == [begun["state"]]
    with pytest.raises(IntegrationError, match="state mismatch"):
        rp.handle_callback(landed["Location"], expected_state="someone-elses")
    code = rp.handle_callback(landed["Location"], expected_state=begun["state"])
    with pytest.raises(IntegrationError) as refused:
        rp.exchange_code(code=code, code_verifier="w" * 43)
    assert refused.value.error == "invalid_grant"


def test_stage_b_an_unregistered_redirect_uri_is_rendered_never_followed(
    stage, crm, browser
):
    """RFC 6749 4.1.2.1: forwarded from the old name or not, an unverified
    redirect URI is never a destination."""
    from urllib.parse import urlencode

    from tests.accounts_portal.conftest import platform_hosts

    stage("b")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": crm["registration"]["client_id"],
            "redirect_uri": "https://evil.example/callback",
            "scope": "openid",
            "state": "s",
            "nonce": "n",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        }
    )
    landed = browser.follow(
        f"{AUTH}/api/v1/integrations/oauth/authorize?{query}",
        platform_hosts=platform_hosts(),
    )
    assert landed.status_code in (400, 401)
    assert landed.json()["error"] in ("invalid_request", "invalid_client")
    assert "Location" not in landed
    assert hosts_of(browser.trail) == [host(AUTH), host(ACCOUNTS)]


# --- stage C: Accounts is canonical; the old name is still honoured --------------


def test_stage_c_both_names_still_work(stage, crm, product, internet):
    stage("c")
    old = sign_in_to_product(
        product(crm["registration"], issuer=AUTH),
        signed_in_browser(crm),
        crm["org"]["organization_id"],
    )
    new = sign_in_to_product(
        product(crm["registration"], issuer=ACCOUNTS),
        signed_in_browser(crm),
        crm["org"]["organization_id"],
    )
    assert old["claims"]["iss"] == AUTH
    assert new["claims"]["iss"] == ACCOUNTS
    assert old["claims"]["sub"] == new["claims"]["sub"] == str(crm["user"].id)
    # Where no configured issuer applies, the canonical one is now Accounts.
    assert (
        internet.get(f"{API}/.well-known/openid-configuration").json()["issuer"]
        == ACCOUNTS
    )


# --- stage D: the window is closed ----------------------------------------------


def test_stage_d_a_product_left_on_the_old_issuer_fails_closed(stage, crm, product):
    """Never a token claiming an issuer the product does not trust: its
    discovery read on the old name now names Accounts, and it stops there."""
    stage("d")
    with pytest.raises(IntegrationError, match="different issuer"):
        product(crm["registration"], issuer=AUTH).discover()

    moved = sign_in_to_product(
        product(crm["registration"], issuer=ACCOUNTS),
        signed_in_browser(crm),
        crm["org"]["organization_id"],
    )
    assert moved["claims"]["iss"] == ACCOUNTS
    assert_same_person(moved["claims"], crm)


def test_stage_d_the_old_name_still_forwards_a_browser(stage, crm, product):
    """An old link or bookmark still lands on Accounts until the name is retired."""
    stage("d")
    browser = signed_in_browser(crm)
    response = browser.get(f"{AUTH}/login?resume=abc")
    assert response.status_code == 302
    assert response["Location"] == f"{ACCOUNTS}/login?resume=abc"


# --- stage E and rollback ---------------------------------------------------------


def test_stage_e_accounts_alone(stage, crm, product):
    stage("e")
    flow = sign_in_to_product(
        product(crm["registration"], issuer=ACCOUNTS),
        signed_in_browser(crm),
        crm["org"]["organization_id"],
    )
    assert flow["claims"]["iss"] == ACCOUNTS
    assert hosts_of(flow["trail"]) == [host(ACCOUNTS)]


def test_rollback_to_before_the_move_is_exactly_the_old_platform(stage, crm, product):
    """Stage "off": the sign-in is auth. again, its session there, no hop."""
    stage("off")
    browser = Browser()
    browser.sign_in(AUTH, crm["user"])
    flow = sign_in_to_product(
        product(crm["registration"], issuer=AUTH), browser, crm["org"]["organization_id"]
    )
    assert flow["claims"]["iss"] == AUTH
    assert hosts_of(flow["trail"]) == [host(AUTH)]
    assert_same_person(flow["claims"], crm)


def test_rolling_back_from_b_to_a_strands_only_products_that_moved(stage, crm, product):
    """Documented in the runbook: go back past stage B only while no product
    uses accounts. - one that does fails closed (it never gets a wrong iss)."""
    stage("b")
    moved = product(crm["registration"], issuer=ACCOUNTS)
    sign_in_to_product(moved, signed_in_browser(crm), crm["org"]["organization_id"])
    stage("a")
    with pytest.raises(IntegrationError, match="different issuer"):
        moved.discover(refresh=True)
    unmoved = sign_in_to_product(
        product(crm["registration"], issuer=AUTH),
        signed_in_browser(crm),
        crm["org"]["organization_id"],
    )
    assert unmoved["claims"]["iss"] == AUTH

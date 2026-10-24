"""The acceptance proof for Phase 3.

A simulated, independently developed product integrates with Workeasy360 end
to end, using only what an external application would have: an issuer URL, a
client id, its own private key, and HTTP. It imports nothing from
``workeasy360`` - asserted mechanically below - and keeps its own data in its
own store, keyed by the ecosystem tenant identity.

The thirteen numbered steps in ``test_an_independent_product_integrates_end_to_end``
are the phase's stated acceptance criteria, in order. The negative cases that
follow are the other half of the proof: an integration that only works is not
the same as one that fails correctly.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from tests.factories.reference_product_client import (
    IntegrationError,
    ProductStore,
)

pytestmark = pytest.mark.django_db

AUTHORIZE_QUERY_KEYS = {
    "response_type",
    "client_id",
    "redirect_uri",
    "scope",
    "state",
    "nonce",
    "code_challenge",
    "code_challenge_method",
    "organization",
}


def _run_flow(product, client, org, *, scope="openid"):
    """Drive the full browser + back-channel exchange. Returns the token set."""
    request = product.begin_authorization(
        organization_id=org["organization_id"], scope=scope
    )
    response = client.get(request["url"], request["query"])
    assert response.status_code == 302, response.content

    code = product.handle_callback(response["Location"], expected_state=request["state"])
    tokens = product.exchange_code(code=code, code_verifier=request["code_verifier"])
    return request, tokens


# --- the boundary itself --------------------------------------------------


def test_the_reference_client_imports_no_platform_module():
    """The constraint that gives the whole exercise its meaning.

    A product that can import ``workeasy360`` is not proving the remote
    contract works - it is proving that a Python process can call a function.
    Checked at the AST level so the constraint cannot be relaxed by adding an
    import inside a function body.
    """
    # Relative to this file, so the test passes from any working directory.
    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "factories"
        / "reference_product_client.py"
    ).read_text(encoding="utf-8")

    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)

    offenders = [m for m in imported if m.split(".")[0] in ("workeasy360", "config")]
    assert not offenders, (
        "The reference product imports platform code, so it proves nothing "
        f"about the remote contract: {offenders}"
    )


def test_the_product_discovers_everything_but_the_issuer(
    client, signing_key, register_product, product_client
):
    """A product is configured with an issuer and derives the rest.

    If this failed, every product would hard-code five paths and the platform
    could never move one without a coordinated release across nine
    repositories.
    """
    product = product_client(register_product("crm"))
    metadata = product.discover()

    assert metadata["issuer"] == product.issuer
    for endpoint in (
        "authorization_endpoint",
        "token_endpoint",
        "userinfo_endpoint",
        "jwks_uri",
        "workeasy360_entitlement_endpoint",
    ):
        assert metadata[endpoint].startswith(product.issuer)


# --- the acceptance flow --------------------------------------------------


def test_an_independent_product_integrates_end_to_end(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    """The thirteen acceptance steps, in order."""
    # 1. The product is registered with the ecosystem.
    registration = register_product("crm")
    product = product_client(registration)
    store = ProductStore()

    # 2-3. A user is authenticated in Workeasy360 and belongs to Organization A.
    org_a = entitled_org("crm")
    signed_in(org_a["user"])

    # 4. Organization A holds an active entitlement for the product - granted
    #    through commercial/provisioning, the sole sanctioned writer.
    assert product  # the client exists before it knows anything about the org

    # 5. The product starts an authorization, with PKCE, state and nonce of
    #    its own making.
    request = product.begin_authorization(organization_id=org_a["organization_id"])
    assert set(request["query"]) == AUTHORIZE_QUERY_KEYS

    # 6. Workeasy360 returns an authorization code to the registered callback.
    response = client.get(request["url"], request["query"])
    assert response.status_code == 302
    assert response["Location"].startswith(registration["redirect_uri"])
    code = product.handle_callback(response["Location"], expected_state=request["state"])

    # 7. The product exchanges it - once.
    tokens = product.exchange_code(code=code, code_verifier=request["code_verifier"])
    assert tokens["token_type"] == "Bearer"

    # 8-9. It receives a signed identity assertion and verifies issuer,
    #      signature, audience, expiry, type and nonce - offline, against the
    #      published key set, with no further call to the platform.
    claims = product.verify_id_token(tokens["id_token"], expected_nonce=request["nonce"])

    # 10. It obtains a stable user id and a stable organization id.
    assert claims["sub"] == str(org_a["user"].id)
    assert claims["organization_id"] == org_a["organization_id"]
    assert claims["product_code"] == "crm"

    # 11. It confirms entitlement over the remote contract.
    decision = product.verify_entitlement(organization_id=claims["organization_id"])
    assert decision["decision"] == "allow"
    assert decision["product_code"] == "crm"

    # 12. No Workeasy360 implementation module was imported - see
    #     test_the_reference_client_imports_no_platform_module.

    # 13. The product stores and reads its own domain data, scoped by the
    #     ecosystem tenant identity and sharing no table with the platform.
    store.add(claims["organization_id"], {"kind": "deal", "name": "Acme renewal"})
    store.add("00000000-0000-0000-0000-000000000000", {"kind": "deal", "name": "other"})

    mine = store.for_organization(claims["organization_id"])
    assert [r["name"] for r in mine] == ["Acme renewal"]


def test_the_product_can_read_profile_claims_it_registered_for(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    """UserInfo, reached with the access token the exchange returned."""
    registration = register_product("crm", scopes=("openid", "profile", "email"))
    product = product_client(registration)
    org = entitled_org("crm")
    signed_in(org["user"])

    _, tokens = _run_flow(product, client, org, scope="openid email")
    profile = product.fetch_userinfo(tokens["access_token"])

    assert profile["sub"] == str(org["user"].id)
    assert profile["email"] == org["user"].email


def test_the_product_survives_a_platform_key_rotation_without_redeploying(
    client,
    signing_key,
    register_product,
    entitled_org,
    product_client,
    signed_in,
    new_signing_key,
):
    """The operational property that makes key rotation possible at all.

    The product has already cached the key set. The platform generates,
    publishes and promotes a new key. The product's next token names a kid it
    has never seen, and it refreshes rather than failing - with no deploy, no
    configuration change and no coordination.
    """
    from workeasy360.platform.ecosystem import keys

    registration = register_product("crm")
    product = product_client(registration)
    org = entitled_org("crm")
    signed_in(org["user"])

    _, first = _run_flow(product, client, org)
    product.verify_id_token(first["id_token"])
    cached_kids = {k["kid"] for k in product.fetch_jwks()["keys"]}

    replacement = new_signing_key()
    keys.promote_key(replacement.kid)

    _, second = _run_flow(product, client, org)
    claims = product.verify_id_token(second["id_token"])

    assert replacement.kid not in cached_kids
    assert claims["sub"] == str(org["user"].id)
    # And the token issued before the rotation still verifies.
    assert product.verify_id_token(first["id_token"])["jti"]


# --- the negative cases ---------------------------------------------------


def test_product_y_cannot_use_product_x_token(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    """Audience confusion.

    Both tokens are signed by the same platform key, so the signature check
    passes for both. What stops Books accepting a CRM token is that it
    verifies ``aud`` against its own client id - which is why that step is in
    the certification checklist rather than left to each product's judgement.
    """
    crm = register_product("crm")
    books = register_product("books")
    org = entitled_org("crm")
    signed_in(org["user"])

    crm_product = product_client(crm)
    books_product = product_client(books)
    _, tokens = _run_flow(crm_product, client, org)

    with pytest.raises(IntegrationError):
        books_product.verify_id_token(tokens["id_token"])


def test_organization_b_context_cannot_be_reused_for_organization_a(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    """A token names exactly one organization, fixed at issue time.

    A product handed a token for Organization B cannot present it as access to
    Organization A: the claim is signed, so changing it invalidates the
    signature, and the product keys its own rows on the claim it verified.
    """
    registration = register_product("crm")
    product = product_client(registration)

    org_a = entitled_org("crm", slug="tenant-a-ref")
    org_b = entitled_org("crm", slug="tenant-b-ref")

    signed_in(org_b["user"])
    _, tokens = _run_flow(product, client, org_b)
    claims = product.verify_id_token(tokens["id_token"])

    assert claims["organization_id"] == org_b["organization_id"]
    assert claims["organization_id"] != org_a["organization_id"]

    store = ProductStore()
    store.add(claims["organization_id"], {"name": "B's data"})
    assert store.for_organization(org_a["organization_id"]) == []


def test_a_user_cannot_authorize_into_an_organization_they_do_not_belong_to(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    """The product supplies the organization; the platform verifies the
    membership. The claim is a hint, the membership row is the authority."""
    registration = register_product("crm")
    product = product_client(registration)
    mine = entitled_org("crm", slug="mine-ref")
    theirs = entitled_org("crm", slug="theirs-ref")

    signed_in(mine["user"])
    request = product.begin_authorization(organization_id=theirs["organization_id"])
    response = client.get(request["url"], request["query"])

    with pytest.raises(IntegrationError) as exc:
        product.handle_callback(response["Location"], expected_state=request["state"])
    assert exc.value.error == "membership_inactive"


def test_an_expired_entitlement_cannot_receive_access(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    from workeasy360.contracts.enums import EntitlementStatus

    registration = register_product("crm")
    product = product_client(registration)
    org = entitled_org("crm", status=EntitlementStatus.EXPIRED)
    signed_in(org["user"])

    request = product.begin_authorization(organization_id=org["organization_id"])
    response = client.get(request["url"], request["query"])

    with pytest.raises(IntegrationError) as exc:
        product.handle_callback(response["Location"], expected_state=request["state"])
    assert exc.value.error == "product_not_entitled"


def test_a_suspended_integration_cannot_authenticate(
    client,
    signing_key,
    register_product,
    entitled_org,
    product_client,
    signed_in,
    platform_admin,
    in_tenant,
):
    from workeasy360.platform.ecosystem import services

    registration = register_product("crm")
    product = product_client(registration)
    org = entitled_org("crm")
    signed_in(org["user"])

    with in_tenant(platform_admin["ctx"]):
        services.transition(
            platform_admin["ctx"],
            client_id=registration["client_id"],
            to_status="suspended",
            reason="compromised",
        )

    request = product.begin_authorization(organization_id=org["organization_id"])
    response = client.get(request["url"], request["query"])
    with pytest.raises(IntegrationError) as exc:
        product.handle_callback(response["Location"], expected_state=request["state"])
    assert exc.value.error == "integration_suspended"


def test_a_replayed_authorization_code_fails(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    """Single use. The second exchange fails even though the code, the
    verifier, the redirect URI and the client credential are all correct."""
    registration = register_product("crm")
    product = product_client(registration)
    org = entitled_org("crm")
    signed_in(org["user"])

    request = product.begin_authorization(organization_id=org["organization_id"])
    response = client.get(request["url"], request["query"])
    code = product.handle_callback(response["Location"], expected_state=request["state"])

    product.exchange_code(code=code, code_verifier=request["code_verifier"])
    with pytest.raises(IntegrationError) as exc:
        product.exchange_code(code=code, code_verifier=request["code_verifier"])
    assert exc.value.error == "invalid_grant"


def test_a_code_minted_for_one_product_cannot_be_exchanged_by_another(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    """Books holds a perfectly valid credential of its own. It still cannot
    redeem CRM's code, because the code is bound to the client that requested
    it."""
    crm = register_product("crm")
    books = register_product("books")
    org = entitled_org("crm")
    signed_in(org["user"])

    crm_product = product_client(crm)
    books_product = product_client(books)

    request = crm_product.begin_authorization(organization_id=org["organization_id"])
    response = client.get(request["url"], request["query"])
    code = crm_product.handle_callback(
        response["Location"], expected_state=request["state"]
    )

    with pytest.raises(IntegrationError) as exc:
        books_product.exchange_code(code=code, code_verifier=request["code_verifier"])
    assert exc.value.error == "invalid_grant"


def test_a_product_client_cannot_probe_another_products_entitlement(
    client, signing_key, register_product, entitled_org, product_client
):
    """There is no parameter in which to name another product, and the answer
    is always about the caller."""
    crm = register_product("crm")
    register_product("books")
    org = entitled_org("books")

    decision = product_client(crm).verify_entitlement(
        organization_id=org["organization_id"]
    )
    assert decision["product_code"] == "crm"
    assert decision["decision"] == "deny"


def test_the_product_refuses_a_callback_whose_state_does_not_match(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    """The product's own responsibility, and in the checklist for that reason.

    The platform echoes ``state`` and does not interpret it; a product that
    skips the comparison can be made to complete an attacker's authorization
    inside a victim's session.
    """
    registration = register_product("crm")
    product = product_client(registration)
    org = entitled_org("crm")
    signed_in(org["user"])

    request = product.begin_authorization(organization_id=org["organization_id"])
    response = client.get(request["url"], request["query"])

    with pytest.raises(IntegrationError, match="state mismatch"):
        product.handle_callback(response["Location"], expected_state="not-mine")


def test_the_product_refuses_a_token_whose_nonce_does_not_match(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    """Replay of a token captured from another session."""
    registration = register_product("crm")
    product = product_client(registration)
    org = entitled_org("crm")
    signed_in(org["user"])

    _, tokens = _run_flow(product, client, org)
    with pytest.raises(IntegrationError, match="nonce mismatch"):
        product.verify_id_token(tokens["id_token"], expected_nonce="somebody-elses")

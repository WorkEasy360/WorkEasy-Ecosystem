"""SSO security, per real product (Phase 12, Steps 2-5).

None of the four products can act as an OIDC client yet (their side: X-01 in
docs/phase-12/01-audit-gap-list.md), so single sign-on is **BLOCKED** for all
four and no production credential is issued - one would make the launcher
claim SSO works. What *can* be proven, and is here, is the Workeasy360 half
for each product's own registration: the exact manifest from
``infra/ecosystem/products.json`` (its launch URL, its redirect URI, its
product code), a test-only credential in the test database, and the reference
product client standing in for the product. Every attack in the brief's
Step 4 list must fail, for each product separately.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from tests.extension.conftest import staff_scope
from tests.factories.reference_product_client import IntegrationError
from workeasy360.platform.ecosystem import protocol

pytestmark = pytest.mark.django_db

REGISTRY = json.loads(
    (
        Path(__file__).resolve().parents[3] / "infra" / "ecosystem" / "products.json"
    ).read_text(encoding="utf-8")
)["products"]
CODES = [entry["code"] for entry in REGISTRY]
assert sorted(CODES) == ["easy_crm", "easy_helpdesk", "easy_project", "easybook"]


@pytest.fixture
def real_product(client, signing_key, product_client):
    """Register one real product exactly as ``register_products`` does, plus a
    test-only public-key credential."""
    from workeasy360.commercial.catalog import services as catalog
    from workeasy360.commercial.controlplane.management.commands.register_products import (
        _manifest,
    )
    from workeasy360.contracts.enums import FeatureKind, LicensingMode, ProductStatus
    from workeasy360.platform.ecosystem import keys
    from workeasy360.platform.ecosystem import services as ecosystem

    made: dict[str, dict] = {}

    def make(code: str) -> dict:
        if code in made:
            return made[code]
        entry = next(e for e in REGISTRY if e["code"] == code)
        with staff_scope(
            "platform.staff_catalog.manage", "platform.staff_integration.manage"
        ) as ctx:
            product = catalog.create_product(
                ctx, code=code, name=entry["name"], licensing_mode=LicensingMode.PER_SEAT
            )
            catalog.set_product_status(
                ctx, product_id=product.id, status=ProductStatus.AVAILABLE
            )
            catalog.create_feature(
                ctx,
                product_id=product.id,
                key=entry["features"][0]["key"],
                name=entry["features"][0]["name"],
                kind=FeatureKind.BOOLEAN,
            )
            integration = ecosystem.register_integration(ctx, manifest=_manifest(entry))
            ecosystem.transition(ctx, client_id=integration.client_id, to_status="active")
            private_pem, public_pem = keys.generate_key_pair("RS256")
            issued = ecosystem.create_credential(
                ctx,
                client_id=integration.client_id,
                kind="public_key",
                public_key_pem=public_pem,
                algorithm="RS256",
                label="phase-12 test only",
            )
        registration = {
            "client_id": integration.client_id,
            "redirect_uri": entry["integration"]["redirect_uris"][0],
            "credential_id": issued.credential_id,
            "client_secret": issued.secret,
            "private_key_pem": private_pem,
        }
        made[code] = {
            "entry": entry,
            "product": product_client(registration),
            **registration,
        }
        return made[code]

    return make


def _authorize(product, client, organization_id):
    request = product.begin_authorization(organization_id=str(organization_id))
    return request, client.get(request["url"], request["query"])


def _error_of(response) -> str:
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(response["Location"]).query).get("error", [""])[0]


@pytest.mark.parametrize("code", CODES)
def test_the_full_flow_binds_product_organization_and_nonce(
    code, client, real_product, entitled_org, signed_in
):
    p = real_product(code)
    org = entitled_org(code)
    signed_in(org["user"])
    request, response = _authorize(p["product"], client, org["organization_id"])
    assert response.status_code == 302
    assert response["Location"].startswith(p["redirect_uri"] + "?")
    code_value = p["product"].handle_callback(
        response["Location"], expected_state=request["state"]
    )
    tokens = p["product"].exchange_code(
        code=code_value, code_verifier=request["code_verifier"]
    )
    claims = p["product"].verify_id_token(
        tokens["id_token"], expected_nonce=request["nonce"]
    )
    assert claims["product_code"] == code
    assert claims["organization_id"] == str(org["organization_id"])
    assert claims["aud"] == p["client_id"]


@pytest.mark.parametrize("code", CODES)
def test_redirect_uri_tampering_is_rendered_never_redirected(
    code, client, real_product, entitled_org, signed_in
):
    """Wrong URI, wildcard-style and open-redirect variants: the platform
    renders an error page and sends the browser nowhere (RFC 6749 4.1.2.1)."""
    p = real_product(code)
    org = entitled_org(code)
    signed_in(org["user"])
    registered = p["redirect_uri"]
    origin = registered.split("/api/")[0]
    host = origin.split("//", 1)[1]
    for tampered in (
        registered + "/extra",
        registered + "?next=https://evil.example.net/",
        registered + "#x",
        registered.upper(),
        registered.replace("https://", "http://"),
        f"https://evil.{host}/api/auth/callback/workeasy360",
        f"https://{host}.evil.example.net/api/auth/callback/workeasy360",
        "https://evil.example.net/api/auth/callback/workeasy360",
        f"{origin}@evil.example.net/api/auth/callback/workeasy360",
        "//evil.example.net/cb",
        "",
    ):
        request = p["product"].begin_authorization(organization_id=org["organization_id"])
        query = {**request["query"], "redirect_uri": tampered}
        response = client.get(request["url"], query)
        assert response.status_code == 400, (tampered, response.status_code)
        assert "Location" not in response, tampered


@pytest.mark.parametrize("code", CODES)
def test_a_code_is_single_use_short_lived_and_bound_to_its_verifier(
    code, client, real_product, entitled_org, signed_in
):
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    p = real_product(code)
    org = entitled_org(code)
    signed_in(org["user"])

    # wrong PKCE verifier
    request, response = _authorize(p["product"], client, org["organization_id"])
    value = p["product"].handle_callback(
        response["Location"], expected_state=request["state"]
    )
    with pytest.raises(IntegrationError) as wrong:
        p["product"].exchange_code(code=value, code_verifier="x" * 64)
    assert wrong.value.error == protocol.INVALID_GRANT

    # reused code
    request, response = _authorize(p["product"], client, org["organization_id"])
    value = p["product"].handle_callback(
        response["Location"], expected_state=request["state"]
    )
    p["product"].exchange_code(code=value, code_verifier=request["code_verifier"])
    with pytest.raises(IntegrationError) as reused:
        p["product"].exchange_code(code=value, code_verifier=request["code_verifier"])
    assert reused.value.error == protocol.INVALID_GRANT

    # expired code
    request, response = _authorize(p["product"], client, org["organization_id"])
    value = p["product"].handle_callback(
        response["Location"], expected_state=request["state"]
    )
    AuthorizationCode.objects.filter(redeemed_at__isnull=True).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(IntegrationError) as expired:
        p["product"].exchange_code(code=value, code_verifier=request["code_verifier"])
    assert expired.value.error == protocol.INVALID_GRANT


@pytest.mark.parametrize("code", CODES)
def test_a_code_minted_for_this_product_is_useless_to_another_product(
    code, client, real_product, entitled_org, signed_in
):
    other_code = CODES[(CODES.index(code) + 1) % len(CODES)]
    p, other = real_product(code), real_product(other_code)
    org = entitled_org(code)
    signed_in(org["user"])
    request, response = _authorize(p["product"], client, org["organization_id"])
    value = p["product"].handle_callback(
        response["Location"], expected_state=request["state"]
    )
    with pytest.raises(IntegrationError) as refused:
        other["product"].exchange_code(code=value, code_verifier=request["code_verifier"])
    assert refused.value.error == protocol.INVALID_GRANT


@pytest.mark.parametrize("code", CODES)
def test_state_and_nonce_are_echoed_exactly_so_the_product_can_refuse_a_mismatch(
    code, client, real_product, entitled_org, signed_in
):
    """State and nonce are the *product's* checks; the platform's part is to
    echo them exactly and bind the nonce into the signed token. A missing
    state is not exploitable here: PKCE (S256, mandatory) binds the code to the
    browser that started the flow, so a code planted in someone else's session
    cannot be redeemed without that browser's verifier."""
    p = real_product(code)
    org = entitled_org(code)
    signed_in(org["user"])
    request, response = _authorize(p["product"], client, org["organization_id"])
    with pytest.raises(IntegrationError):
        p["product"].handle_callback(response["Location"], expected_state="not-the-state")
    value = p["product"].handle_callback(
        response["Location"], expected_state=request["state"]
    )
    tokens = p["product"].exchange_code(
        code=value, code_verifier=request["code_verifier"]
    )
    with pytest.raises(IntegrationError):
        p["product"].verify_id_token(tokens["id_token"], expected_nonce="not-the-nonce")

    # missing state: the code still cannot be redeemed without the verifier
    request = p["product"].begin_authorization(organization_id=org["organization_id"])
    query = {k: v for k, v in request["query"].items() if k != "state"}
    response = client.get(request["url"], query)
    assert response.status_code == 302 and "state=" not in response["Location"]
    from urllib.parse import parse_qs, urlsplit

    planted = parse_qs(urlsplit(response["Location"]).query)["code"][0]
    with pytest.raises(IntegrationError) as refused:
        p["product"].exchange_code(code=planted, code_verifier="attacker-" + "v" * 50)
    assert refused.value.error == protocol.INVALID_GRANT


@pytest.mark.parametrize("code", CODES)
def test_a_member_of_another_organization_cannot_sign_in_to_this_one(
    code, client, real_product, entitled_org, signed_in
):
    p = real_product(code)
    org_a, org_b = entitled_org(code), entitled_org(code)
    signed_in(org_b["user"])
    _request, response = _authorize(p["product"], client, org_a["organization_id"])
    assert response.status_code == 302
    assert _error_of(response) == protocol.MEMBERSHIP_INACTIVE


@pytest.mark.parametrize("code", CODES)
def test_removed_membership_missing_entitlement_and_missing_seat_are_refused(
    code, client, real_product, entitled_org, signed_in, organization_factory
):
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    p = real_product(code)

    # no entitlement at all
    bare_org, _membership, bare_user = organization_factory()
    signed_in(bare_user)
    _r, response = _authorize(p["product"], client, bare_org.id)
    assert _error_of(response) == protocol.PRODUCT_NOT_ENTITLED

    # entitled, but no seat for this member
    unseated = entitled_org(code, seat=False)
    signed_in(unseated["user"])
    _r, response = _authorize(p["product"], client, unseated["organization_id"])
    assert _error_of(response) == protocol.PRODUCT_NO_SEAT

    # removed membership (inside the organization's own scope: an unscoped
    # write with no tenant variable would match nothing under RLS)
    org = entitled_org(code)
    with tenant_context(org["ctx"]), tenant_db_scope(org["ctx"]):
        updated = Membership.objects.filter(id=org["membership"].id).update(
            status="removed", removed_at=timezone.now()
        )
    assert updated == 1
    signed_in(org["user"])
    _r, response = _authorize(p["product"], client, org["organization_id"])
    assert _error_of(response) == protocol.MEMBERSHIP_INACTIVE


@pytest.mark.parametrize("code", CODES)
def test_a_suspended_organization_signs_in_read_only(
    code, client, real_product, entitled_org, signed_in
):
    """By design (R-10): suspension is read-only, not a lock-out - customers
    can still see and export their data. The product learns it may not write."""
    from workeasy360.platform.organizations.models import Organization

    p = real_product(code)
    org = entitled_org(code)
    Organization.objects.filter(id=org["organization_id"]).update(status="suspended")
    signed_in(org["user"])
    request, response = _authorize(p["product"], client, org["organization_id"])
    value = p["product"].handle_callback(
        response["Location"], expected_state=request["state"]
    )
    p["product"].exchange_code(code=value, code_verifier=request["code_verifier"])
    read = p["product"].verify_entitlement(organization_id=org["organization_id"])
    write = p["product"].verify_entitlement(
        organization_id=org["organization_id"], write=True
    )
    assert read["decision"] == "allow", read
    assert write["decision"] == "deny" and write["reason"] == "suspended", write

"""The launcher honours the preference (ADR-044, design section 3.1): the SSO
path or the product's own sign-in page - after, never instead of, the access
decision."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from tests.factories.manifests import PRODUCT_ORIGIN
from tests.universal_sso.conftest import LOGIN_URL

pytestmark = pytest.mark.django_db


def _launch(client, slug, code="crm"):
    return client.post(
        f"/api/v1/orgs/{slug}/apps/{code}/launch", HTTP_ACCEPT="application/json"
    )


def _directory(client, slug):
    return {
        a["product_code"]: a
        for a in client.get(f"/api/v1/orgs/{slug}/apps").json()["apps"]
    }


@pytest.fixture
def member(client, entitled_org, signed_in):
    def make(product="crm", preference=None, **kwargs):
        org = entitled_org(product, **kwargs)
        signed_in(org["user"], sign_in_preference=preference)
        return org

    return make


@pytest.mark.parametrize("preference", ["auto_sso", "ask"])
def test_automatic_and_ask_both_take_the_workeasy360_path(
    client, sso_product, member, preference
):
    sso_product("crm", mode="sso_optional")
    org = member(preference=preference)
    body = _launch(client, org["org"].slug).json()
    assert body["sign_in"] == "workeasy360"
    parts = urlsplit(body["redirect_url"])
    assert f"{parts.scheme}://{parts.netloc}" == PRODUCT_ORIGIN
    assert parse_qs(parts.query)["organization"] == [org["organization_id"]]


def test_preferring_the_product_sign_in_opens_its_registered_page_without_an_sso_hint(
    client, sso_product, member
):
    sso_product("crm", mode="sso_optional")
    org = member(preference="product_login")
    body = _launch(client, org["org"].slug).json()
    assert body == {"redirect_url": LOGIN_URL, "state": "active", "sign_in": "product"}


def test_a_product_that_only_uses_workeasy360_cannot_be_opened_any_other_way(
    client, sso_product, member
):
    sso_product("crm", mode="sso_only")
    org = member(preference="product_login")
    body = _launch(client, org["org"].slug).json()
    assert body["sign_in"] == "workeasy360"  # it asks at the identity provider instead
    assert "organization=" in body["redirect_url"]


def test_a_change_takes_effect_on_the_next_launch_with_no_sign_out(
    client, sso_product, member, prefer
):
    sso_product("crm", mode="sso_optional")
    org = member(preference="auto_sso")
    slug = org["org"].slug
    assert _launch(client, slug).json()["sign_in"] == "workeasy360"
    response = client.put(
        "/api/v1/auth/sign-in-preferences",
        {"products": {"crm": "product_login"}},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert _launch(client, slug).json()["sign_in"] == "product"
    client.put(
        "/api/v1/auth/sign-in-preferences",
        {"products": {"crm": "inherit"}},
        content_type="application/json",
    )
    assert _launch(client, slug).json()["sign_in"] == "workeasy360"


def test_a_product_workeasy360_cannot_sign_in_to_says_so_and_opens_its_own_page(
    client, sso_product, member
):
    sso_product("crm", mode="local_only")
    org = member(preference="auto_sso")
    card = _directory(client, org["org"].slug)["crm"]
    assert card["sign_in"]["sso"] == "disabled"
    assert card["sign_in"]["effective"] == "product_login"
    body = _launch(client, org["org"].slug).json()
    assert body == {"redirect_url": LOGIN_URL, "state": "active", "sign_in": "product"}


def test_the_card_carries_the_servers_sign_in_answer_and_no_destination(
    client, sso_product, member
):
    sso_product("crm", mode="sso_optional")
    org = member(preference="ask")
    card = _directory(client, org["org"].slug)["crm"]
    assert card["sign_in"] == {
        "sso": "enabled",
        "mode": "sso_optional",
        "product_login_available": True,
        "override": "inherit",
        "preference": "ask",
        "effective": "ask",
        "constraint": "",
    }
    assert LOGIN_URL not in repr(card) and "launch" not in card


def test_a_preference_never_opens_a_product_the_organization_does_not_hold(
    client, sso_product, organization_factory, signed_in
):
    sso_product("crm")
    org, _membership, user = organization_factory()
    signed_in(user, sign_in_preference="auto_sso")
    refused = _launch(client, org.slug)
    assert refused.status_code == 409
    assert refused.json()["state"] == "no_entitlement"


def test_a_preference_never_opens_a_product_without_a_seat(client, sso_product, member):
    sso_product("crm")
    org = member(preference="auto_sso", seat=False)
    refused = _launch(client, org["org"].slug)
    assert refused.status_code == 409 and refused.json()["state"] == "no_seat"

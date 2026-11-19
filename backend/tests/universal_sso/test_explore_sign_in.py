"""Explore Products' "Sign in" (ADR-074).

A configured product can be signed in to from User Admin whether or not the
organization holds it: the route sends the browser to the product, WorkEasy360
asks "Continue as" / "Use another account", and only then does the
authorization endpoint deliver its answer. These tests pin both halves: the
chooser is reached for an unbought product, and nothing about that grants
access - the refusal is the same, no code is minted, and the launch route
(which *is* the access decision) still refuses.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from tests.factories.manifests import PRODUCT_ORIGIN
from tests.universal_sso.conftest import (
    AUTHORIZE,
    authorize_query,
    path_of,
    query_of,
    refused_after_chooser,
)
from workeasy360.platform.ecosystem import protocol

pytestmark = pytest.mark.django_db


def _sign_in(client, slug, code="crm"):
    return client.post(
        f"/api/v1/orgs/{slug}/apps/{code}/sign-in", HTTP_ACCEPT="application/json"
    )


def _launch(client, slug, code="crm"):
    return client.post(
        f"/api/v1/orgs/{slug}/apps/{code}/launch", HTTP_ACCEPT="application/json"
    )


def _codes(registration) -> int:
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    return AuthorizationCode.objects.filter(
        integration__client_id=registration["client_id"]
    ).count()


def test_an_unbought_product_reaches_the_chooser_and_is_still_refused(
    client, signing_key, sso_product, organization_factory, signed_in
):
    """The whole path for an organization with no purchase: launch refuses,
    sign-in sends the browser to the product with the organization, the
    product's own authorization request reaches the chooser, and "Continue
    as" ends at ``product_not_entitled`` with no code."""
    crm = sso_product("crm")
    org, _membership, user = organization_factory()
    signed_in(user, sign_in_preference=None)

    launch = _launch(client, org.slug)
    assert launch.status_code == 409
    assert launch.json()["state"] == "no_entitlement"

    answer = _sign_in(client, org.slug)
    assert answer.status_code == 200, answer.content
    body = answer.json()
    assert body["state"] == "available" and body["sign_in"] == "workeasy360"
    parts = urlsplit(body["redirect_url"])
    assert f"{parts.scheme}://{parts.netloc}" == PRODUCT_ORIGIN
    assert parse_qs(parts.query)["organization"] == [str(org.id)]

    # The product starts its own request with the organization it was given.
    _verifier, query = authorize_query(crm, str(org.id))
    asked = client.get(AUTHORIZE, query)
    assert path_of(asked) == "/product-access"
    refused_after_chooser(client, asked, protocol.PRODUCT_NOT_ENTITLED)
    assert _codes(crm) == 0


def test_a_held_product_signs_in_to_the_same_place_a_launch_goes(
    client, signing_key, sso_product, entitled_org, signed_in
):
    sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    launched = _launch(client, org["org"].slug).json()
    signed = _sign_in(client, org["org"].slug).json()
    assert signed["redirect_url"] == launched["redirect_url"]
    assert signed["sign_in"] == launched["sign_in"] == "workeasy360"


@pytest.mark.parametrize(
    ("kwargs", "state"),
    [
        ({"activate": False}, "unavailable"),  # a draft: nothing to enter yet
        ({"mode": "local_only"}, "unavailable"),  # no WorkEasy360 sign-in offered
    ],
)
def test_sign_in_is_refused_where_workeasy360_sign_in_cannot_reach(
    client, sso_product, organization_factory, signed_in, kwargs, state
):
    sso_product("crm", **kwargs)
    org, _membership, user = organization_factory()
    signed_in(user, sign_in_preference=None)
    answer = _sign_in(client, org.slug)
    assert answer.status_code == 409
    assert answer.json() == {
        "code": "sign_in_unavailable",
        "state": state,
        "product_code": "crm",
    }


def test_a_suspended_product_says_so_and_an_unknown_code_is_unavailable(
    client, sso_product, organization_factory, signed_in
):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    sso_product("crm")
    ProductIntegration.objects.filter(product_code="crm").update(status="suspended")
    org, _membership, user = organization_factory()
    signed_in(user, sign_in_preference=None)
    assert _sign_in(client, org.slug).json()["state"] == "suspended"
    assert (
        _sign_in(client, org.slug, code="nosuchproduct").json()["state"] == "unavailable"
    )
    assert _sign_in(client, org.slug, code="NOT A CODE").status_code == 409


def test_sign_in_is_a_post_for_members_only(
    client, sso_product, organization_factory, signed_in
):
    sso_product("crm")
    org, _membership, user = organization_factory()
    other, _m, _u = organization_factory()
    signed_in(user, sign_in_preference=None)
    assert client.get(f"/api/v1/orgs/{org.slug}/apps/crm/sign-in").status_code == 405
    # Another organization's slug: the tenancy middleware refuses before the view.
    assert _sign_in(client, other.slug).status_code in (403, 404)
    client.logout()
    # Signed out: refused, and the organization's existence is not confirmed.
    assert _sign_in(client, org.slug).status_code in (401, 403, 404)


def test_sign_in_states_never_read_an_entitlement(
    sso_product, organization_factory, entitled_org, monkeypatch
):
    """The registry answers; the entitlement gate is not consulted at all."""
    from workeasy360.contracts.ports import entitlements
    from workeasy360.platform.launcher.access import SignInState, sign_in_states

    sso_product("crm")
    sso_product("books", activate=False)
    org = entitled_org("crm")

    def refuse(*_a, **_k):  # pragma: no cover - the assertion is that it is not called
        raise AssertionError("sign-in availability must not read an entitlement")

    monkeypatch.setattr(entitlements, "get_gate", refuse)
    states = sign_in_states(org["ctx"], ["crm", "books", "none"])
    assert states == {
        "books": SignInState.UNAVAILABLE,
        "crm": SignInState.AVAILABLE,
        "none": SignInState.UNAVAILABLE,
    }


def test_explore_marks_each_product_with_its_sign_in(
    client, sso_product, organization_factory, grant_permissions, signed_in
):
    """Every Explore product carries ``sign_in`` from the registry - for a
    product the organization holds and for one it does not alike."""
    from workeasy360.interfaces.api.v1 import trial_views

    sso_product("crm")
    org, _membership, user = organization_factory()
    signed_in(user, sign_in_preference=None)
    listing = {"products": [{"code": "crm"}, {"code": "books"}]}

    class _Ctx:
        user_id = user.pk

    trial_views._mark_sign_in(_Ctx(), listing)
    assert {p["code"]: p["sign_in"] for p in listing["products"]} == {
        "crm": "available",
        "books": "unavailable",
    }
    # And the endpoint itself carries the field on every product it lists.
    grant_permissions(org, _membership, ["platform.organization.view"])
    response = client.get(f"/api/v1/orgs/{org.slug}/explore")
    assert response.status_code == 200
    assert all("sign_in" in p for p in response.json()["products"])


def test_signed_out_mid_flow_the_product_request_parks_for_sign_in(
    client, signing_key, sso_product, organization_factory
):
    """Direct product URL with no WorkEasy360 session: sign-in first, never a
    refusal or a code (the chooser and the answer follow at resume)."""
    crm = sso_product("crm")
    org, _membership, _user = organization_factory()
    _verifier, query = authorize_query(crm, str(org.id))
    parked = client.get(AUTHORIZE, query)
    assert path_of(parked) == "/login"
    assert "error" not in query_of(parked)
    assert _codes(crm) == 0

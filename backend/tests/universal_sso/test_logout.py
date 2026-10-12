"""RP-initiated sign-out (ADR-044, design section 7): a product's "sign out of
Workeasy360 too" - confirmed by the person, returned only to a registered URI."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client

from tests.universal_sso.conftest import (
    END_SESSION,
    END_SESSION_CONFIRM,
    END_SESSION_REQUEST,
    SIGNED_OUT_URL,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def product(sso_product):
    return sso_product("crm", post_logout=(SIGNED_OUT_URL,))


def _start(client, product, **overrides):
    params = {
        "client_id": product["client_id"],
        "post_logout_redirect_uri": SIGNED_OUT_URL,
        "state": "s-1",
    }
    params.update(overrides)
    return client.get(END_SESSION, {k: v for k, v in params.items() if v is not None})


def test_discovery_advertises_the_end_session_endpoint_and_prompt_values(
    client, signing_key
):
    doc = client.get("/.well-known/openid-configuration").json()
    assert doc["end_session_endpoint"].endswith(END_SESSION)
    assert doc["prompt_values_supported"] == [
        "none",
        "login",
        "consent",
        "select_account",
    ]
    assert "auth_time" in doc["claims_supported"]
    for code in ("access_denied", "interaction_required", "unauthorized_client"):
        assert code in doc["workeasy360_error_codes"]


def test_a_signed_out_browser_goes_straight_back_with_its_state(product):
    response = _start(Client(), product)
    assert response.status_code == 302
    assert response["Location"] == f"{SIGNED_OUT_URL}?state=s-1"


def test_opening_the_link_signs_no_one_out_it_asks_first(
    client, product, entitled_org, signed_in
):
    from workeasy360.platform.identity.models import UserSession

    org = entitled_org("crm")
    signed_in(org["user"])
    response = _start(client, product)
    assert urlsplit(response["Location"]).path == "/sign-out"
    assert UserSession.objects.filter(
        user=org["user"]
    ).exists()  # logout CSRF: nothing ended
    handle = parse_qs(urlsplit(response["Location"]).query)["request"][0]
    described = client.get(END_SESSION_REQUEST, {"request": handle}).json()
    assert described == {
        "product": {"name": "CRM"},
        "signed_in": True,
        "account": {"email": org["user"].email},
    }


def test_confirming_ends_the_whole_sign_in_and_returns_to_the_registered_uri(
    client, product, entitled_org, signed_in
):
    from workeasy360.platform.identity.models import UserSession

    org = entitled_org("crm")
    signed_in(org["user"])
    handle = parse_qs(urlsplit(_start(client, product)["Location"]).query)["request"][0]
    answered = client.post(
        END_SESSION_CONFIRM,
        {"request": handle, "sign_out": True},
        content_type="application/json",
    )
    assert answered.json() == {"redirect_url": f"{SIGNED_OUT_URL}?state=s-1"}
    assert not UserSession.objects.filter(user=org["user"]).exists()
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_staying_signed_in_keeps_the_session(client, product, entitled_org, signed_in):
    org = entitled_org("crm")
    signed_in(org["user"])
    handle = parse_qs(urlsplit(_start(client, product)["Location"]).query)["request"][0]
    answered = client.post(
        END_SESSION_CONFIRM,
        {"request": handle, "sign_out": False},
        content_type="application/json",
    )
    assert answered.json()["redirect_url"] == f"{SIGNED_OUT_URL}?state=s-1"
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "not-a-client"},
        {"post_logout_redirect_uri": "https://evil.example.com/"},
        {"post_logout_redirect_uri": SIGNED_OUT_URL + "/"},
        {"post_logout_redirect_uri": SIGNED_OUT_URL + "?next=https://evil.example.com"},
        {"post_logout_redirect_uri": None},
        {"client_id": None},
    ],
)
def test_an_unverified_client_or_uri_is_rendered_never_redirected(product, overrides):
    response = _start(Client(), product, **overrides)
    assert response.status_code == 400
    assert "Location" not in response
    assert response.json()["error"] == "invalid_request"


def test_a_tampered_or_stale_handle_is_refused(
    client, product, entitled_org, signed_in, platform_admin, in_tenant
):
    from workeasy360.platform.ecosystem import services

    org = entitled_org("crm")
    signed_in(org["user"])
    handle = parse_qs(urlsplit(_start(client, product)["Location"]).query)["request"][0]
    tampered = handle[:-2] + ("AA" if not handle.endswith("AA") else "BB")
    assert client.get(END_SESSION_REQUEST, {"request": tampered}).status_code == 400
    assert (
        client.post(
            END_SESSION_CONFIRM,
            {"request": tampered, "sign_out": True},
            content_type="application/json",
        ).status_code
        == 400
    )
    # The URI unregistered since the link was made: refused at confirmation.
    from tests.factories.manifests import manifest_for

    with in_tenant(platform_admin["ctx"]):
        services.update_manifest(
            platform_admin["ctx"],
            client_id=product["client_id"],
            manifest=manifest_for(
                "crm", manifest_version="2026-10-12", sign_in_mode="sso_only"
            ),
        )
    stale = client.post(
        END_SESSION_CONFIRM,
        {"request": handle, "sign_out": True},
        content_type="application/json",
    )
    assert stale.status_code == 400
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True


def test_confirmation_needs_the_csrf_token(product, entitled_org):
    org = entitled_org("crm")
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(org["user"])
    strict.get("/api/v1/auth/session")
    handle = parse_qs(urlsplit(_start(strict, product)["Location"]).query)["request"][0]
    body = {"request": handle, "sign_out": True}
    assert (
        strict.post(
            END_SESSION_CONFIRM, body, content_type="application/json"
        ).status_code
        == 403
    )
    assert strict.get("/api/v1/auth/session").json()["authenticated"] is True

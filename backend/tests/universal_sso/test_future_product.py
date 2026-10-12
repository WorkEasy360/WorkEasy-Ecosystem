"""A product added later joins universal sign-in with no code change
(ADR-044, the brief's "TEST FUTURE PRODUCT EXTENSIBILITY").

``test_future_product`` exists only inside this test: created through the
Super Admin console's own API (SKU -> integration -> credential -> activate),
used by a customer whose global preference it simply inherits, then retired.
Nothing in the platform names it - the architecture suite proves no source
file names any product.
"""

from __future__ import annotations

import pytest

from tests.factories.manifests import PRODUCT_ORIGIN
from tests.phase7 import conftest as _phase7
from tests.universal_sso.conftest import (
    AUTHORIZE,
    PREFS,
    authorize_query,
    path_of,
    query_of,
)

pytestmark = pytest.mark.django_db

# Fixtures shared with the Phase 7 suites: a signed-in staff member at AAL 2.
http_login = _phase7.http_login
staff_client = _phase7.staff_client

CODE = "test_future_product"
ADMIN = "/api/v1/admin"
GRANTS = (
    "platform.staff_catalog.view",
    "platform.staff_catalog.manage",
    "platform.staff_integration.view",
    "platform.staff_integration.manage",
)


@pytest.fixture
def future_product(staff_client, signing_key):
    """Register the product exactly as an operator would, in the console."""
    from workeasy360.platform.ecosystem import keys

    staff, _user = staff_client(*GRANTS)
    created = staff.post(
        f"{ADMIN}/catalog/products",
        {"code": CODE, "name": "Future Product"},
        content_type="application/json",
    )
    assert created.status_code in (200, 201), created.content
    product_id = created.json()["id"]
    available = staff.post(
        f"{ADMIN}/catalog/products/{product_id}/status",
        {"status": "available"},
        content_type="application/json",
    )
    assert available.status_code == 200, available.content

    registered = staff.post(
        f"{ADMIN}/products/{CODE}/integration",
        {
            "display_name": "Future Product",
            "launch_url": f"{PRODUCT_ORIGIN}/launch",
            "redirect_uris": [f"{PRODUCT_ORIGIN}/auth/callback"],
            "sign_in_mode": "sso_optional",
            "login_url": f"{PRODUCT_ORIGIN}/login",
            "scopes": ["openid", "email"],
        },
        content_type="application/json",
    )
    assert registered.status_code == 201, registered.content
    integration = registered.json()
    assert (
        integration["status"] == "draft"
        and integration["sso_state"] == "integration_required"
    )

    private_pem, public_pem = keys.generate_key_pair("RS256")
    credential = staff.post(
        f"{ADMIN}/products/{CODE}/integration/credentials",
        {"kind": "public_key", "public_key_pem": public_pem, "label": "future"},
        content_type="application/json",
    )
    assert credential.status_code == 201, credential.content
    assert "client_secret" not in credential.json()
    active = staff.post(
        f"{ADMIN}/products/{CODE}/integration/status",
        {"status": "active"},
        content_type="application/json",
    )
    assert active.status_code == 200, active.content
    assert active.json()["sso_state"] == "enabled"
    return {
        "staff": staff,
        "client_id": integration["client_id"],
        "redirect_uri": f"{PRODUCT_ORIGIN}/auth/callback",
        "credential_id": credential.json()["credential_id"],
        "client_secret": "",
        "private_key_pem": private_pem,
        "product_code": CODE,
    }


def test_a_product_added_later_joins_universal_sign_in_with_no_code_change(
    client, future_product, entitled_org, signed_in, product_client, prefer
):
    org = entitled_org(CODE)
    # The person chose "automatic" long before this product existed.
    signed_in(org["user"], sign_in_preference="auto_sso")

    # It appears, inheriting that choice.
    card = next(
        a
        for a in client.get(f"/api/v1/orgs/{org['org'].slug}/apps").json()["apps"]
        if a["product_code"] == CODE
    )
    assert card["state"] == "active"
    assert (
        card["sign_in"]["sso"] == "enabled" and card["sign_in"]["override"] == "inherit"
    )
    assert card["sign_in"]["effective"] == "auto_sso"
    row = next(
        p for p in client.get(PREFS).json()["products"] if p["product_code"] == CODE
    )
    assert row["override"] == "inherit" and row["choices"] == [
        "inherit",
        "auto_sso",
        "ask",
        "product_login",
    ]

    # Automatic: signed in silently, a verified identity for this product.
    reference = product_client(future_product)
    request = reference.begin_authorization(organization_id=org["organization_id"])
    granted = client.get(request["url"], request["query"])
    code = reference.handle_callback(granted["Location"], expected_state=request["state"])
    tokens = reference.exchange_code(code=code, code_verifier=request["code_verifier"])
    claims = reference.verify_id_token(
        tokens["id_token"], expected_nonce=request["nonce"]
    )
    assert claims["product_code"] == CODE and claims["sub"] == str(org["user"].id)

    # The person switches their global default to "ask": this product asks too.
    prefer(org["user"], "ask")
    _verifier, query = authorize_query(future_product, org["organization_id"])
    assert path_of(client.get(AUTHORIZE, query)) == "/product-access"

    # Prefer product sign-in: the launcher opens the page the console registered.
    prefer(org["user"], "product_login")
    launched = client.post(
        f"/api/v1/orgs/{org['org'].slug}/apps/{CODE}/launch",
        HTTP_ACCEPT="application/json",
    )
    assert launched.json()["redirect_url"] == f"{PRODUCT_ORIGIN}/login"


def test_removing_the_product_removes_it_everywhere(
    client, future_product, entitled_org, signed_in
):
    org = entitled_org(CODE)
    signed_in(org["user"], sign_in_preference="auto_sso")
    retired = future_product["staff"].post(
        f"{ADMIN}/products/{CODE}/integration/status",
        {"status": "retired"},
        content_type="application/json",
    )
    assert retired.status_code == 200, retired.content
    assert all(p["product_code"] != CODE for p in client.get(PREFS).json()["products"])
    launched = client.post(
        f"/api/v1/orgs/{org['org'].slug}/apps/{CODE}/launch",
        HTTP_ACCEPT="application/json",
    )
    assert (
        launched.status_code == 409 and launched.json()["state"] == "product_unavailable"
    )
    _verifier, query = authorize_query(future_product, org["organization_id"])
    response = client.get(AUTHORIZE, query)
    assert query_of(response)["error"] == ["integration_suspended"]


def test_the_console_never_returns_a_secret_after_it_was_shown_once(future_product):
    staff = future_product["staff"]
    issued = staff.post(
        f"{ADMIN}/products/{CODE}/integration/credentials",
        {"kind": "client_secret"},
        content_type="application/json",
    )
    assert issued.status_code == 201
    secret = issued.json()["client_secret"]
    assert len(secret) >= 32
    listed = staff.get(f"{ADMIN}/products/{CODE}/integration/credentials").json()
    console = staff.get(f"{ADMIN}/products/console").json()
    for body in (listed, console):
        assert secret not in repr(body)
        assert "secret_hash" not in repr(body)
    assert {c["kind"] for c in listed["credentials"]} == {"public_key", "client_secret"}

"""The Accounts Control Center (ADR-072): shared fixtures.

Product sign-ins are made the way the universal sign-in suites make them - in
single-host mode, through the real authorization and token endpoints - because
what is recorded must be recorded in every mode. The Control Center is then
read the way a browser reads it: on WorkEasy360 Accounts' own host, with
``W360_ACCOUNTS_IDENTITY`` on (stage b of the ADR-067 move, exactly as
``tests/accounts_portal`` configures it).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from tests.accounts_portal.conftest import ACCOUNTS, ADMIN, APP, configure, host
from tests.extension.conftest import console_org  # noqa: F401 - fixture
from tests.universal_sso.conftest import (  # noqa: F401 - fixtures
    AUTHORIZE,
    PASSWORD,
    authorize_query,
    password_login,
    prefer,
    query_of,
    sso_product,
    with_password,
)

ACCOUNT_API = "/api/v1/auth/account"
BROWSER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)


class OnHost:
    """A browser on one platform host: every request names that host over
    https, with one cookie jar - as a browser on accounts. (or admin.) is."""

    def __init__(self, origin: str, **defaults: Any) -> None:
        self.netloc = host(origin)
        self.client = Client(HTTP_USER_AGENT=BROWSER_AGENT, **defaults)

    def _extra(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {"HTTP_HOST": self.netloc, "secure": True, **extra}

    def get(self, path: str, data: Any = None, **extra: Any):
        return self.client.get(path, data, **self._extra(extra))

    def post(self, path: str, data: Any = None, **extra: Any):
        return self.client.post(
            path, data or {}, content_type="application/json", **self._extra(extra)
        )

    def patch(self, path: str, data: Any = None, **extra: Any):
        return self.client.patch(
            path, data or {}, content_type="application/json", **self._extra(extra)
        )

    def force_login(self, user: Any) -> OnHost:
        self.client.force_login(user)
        return self

    @property
    def session(self):
        return self.client.session


@pytest.fixture
def accounts_on(settings):
    """``accounts_on()``: WorkEasy360 Accounts hosts identity (ADR-071) - the
    switch the Control Center lives behind. ``accounts_on(False)``: the same
    hosts with the switch off, which is production today."""

    def put(on: bool = True):
        configure(settings, "b")
        settings.W360_ACCOUNTS_IDENTITY = on
        return settings

    return put


@pytest.fixture
def on_accounts():
    """A signed-in browser on WorkEasy360 Accounts."""

    def make(user: Any, **defaults: Any) -> OnHost:
        return OnHost(ACCOUNTS, **defaults).force_login(user)

    return make


@pytest.fixture
def on_app():
    def make(user: Any) -> OnHost:
        return OnHost(APP).force_login(user)

    return make


@pytest.fixture
def on_admin():
    def make(user: Any) -> OnHost:
        return OnHost(ADMIN).force_login(user)

    return make


def sign_in_to_product(
    browser: Client, registration: dict, organization_id: str, product_client
):
    """A real product sign-in from ``browser``'s live session (single-host
    mode): authorize, then the product redeems the code at the token
    endpoint. Returns the verified ID token claims."""
    verifier, query = authorize_query(registration, organization_id)
    granted = browser.get(AUTHORIZE, query)
    assert granted.status_code == 302, granted.content
    code = query_of(granted)["code"][0]
    reference = product_client(registration)
    issued = reference.exchange_code(code=code, code_verifier=verifier)
    return reference.verify_id_token(issued["id_token"], expected_nonce="client-nonce")


@pytest.fixture
def bcl_manifest():
    """A manifest that takes back-channel logout (ADR-069)."""
    from tests.factories.manifests import PRODUCT_ORIGIN, manifest_for

    def make(code: str):
        return manifest_for(
            code,
            manifest_version="2026-11-15",
            sign_in_mode="sso_only",
            backchannel_logout_uri=f"{PRODUCT_ORIGIN}/auth/backchannel-logout",
        )

    return make


@pytest.fixture
def person(catalog, entitled_org, with_password, prefer):  # noqa: F811 - fixtures above
    """A person who owns an organization entitled to CRM (with a seat), with a
    real password and Automatic product sign-in. After ``catalog``, so CRM is
    known to be per-seat when the seat is assigned."""
    org = entitled_org("crm")
    user = with_password(org["user"])
    prefer(user, "auto_sso")
    return {"org": org, "user": user, "organization_id": org["organization_id"]}


@pytest.fixture
def no_wait(monkeypatch):
    """Answer a per-product sign-out at once (the delivery worker does not run
    inside a test transaction)."""
    from workeasy360.interfaces.api.v1 import accounts_center_views

    monkeypatch.setattr(accounts_center_views, "PRODUCT_SIGN_OUT_WAIT_S", 0.0)


VERIFY = "/api/v1/integrations/entitlements/verify"
TOKEN = "/api/v1/integrations/oauth/token"


def product_assertion(registration: dict) -> dict:
    """``private_key_jwt`` client authentication, as a product's server sends it
    (single-host mode: the token endpoint is the audience)."""
    import time
    import uuid as _uuid

    import jwt

    now = int(time.time())
    return {
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": jwt.encode(
            {
                "iss": registration["client_id"],
                "sub": registration["client_id"],
                "aud": "http://testserver" + TOKEN,
                "iat": now,
                "exp": now + 60,
                "jti": _uuid.uuid4().hex,
            },
            registration["private_key_pem"],
            algorithm="RS256",
            headers={"kid": registration["credential_id"]},
        ),
    }


def ask_about(registration: dict, organization_id: str, subject: str | None) -> dict:
    """The product's entitlement question, with the person it is about."""
    fields = {"organization_id": organization_id, **product_assertion(registration)}
    if subject is not None:
        fields["subject"] = subject
    response = Client().post(VERIFY, fields)
    assert response.status_code == 200, response.content
    return response.json()

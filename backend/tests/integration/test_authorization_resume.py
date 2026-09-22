"""The authorization-resume flow: `/authorize` interrupted for login or
organization selection, completed at `/integrations/oauth/resume`.

Phase 4, Tasks 31/32. Everything here goes through the actual HTTP endpoints
with the actual middleware stack - the same reason ``test_oidc_flow.py``
does, and doubly so here: the property under test is what a browser can and
cannot do to the parked request between the park and the resume.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

AUTHORIZE = "/api/v1/integrations/oauth/authorize"
RESUME = "/api/v1/integrations/oauth/resume"
TOKEN = "/api/v1/integrations/oauth/token"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _authorize_params(registration, org=None, **overrides) -> dict:
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
    }
    if org is not None:
        params["organization"] = org["organization_id"]
    params.update(overrides)
    return params


def _resume_token(location_header: str) -> str:
    return parse_qs(urlsplit(location_header).query)["resume"][0]


def _assertion(registration) -> dict:
    import time
    import uuid

    import jwt

    now = int(time.time())
    return {
        "client_assertion_type": (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ),
        "client_assertion": jwt.encode(
            {
                "iss": registration["client_id"],
                "sub": registration["client_id"],
                "aud": "http://testserver" + TOKEN,
                "iat": now,
                "exp": now + 60,
                "jti": uuid.uuid4().hex,
            },
            registration["private_key_pem"],
            algorithm="RS256",
            headers={"kid": registration["credential_id"]},
        ),
    }


# --- the login leg ----------------------------------------------------------


def test_logging_in_then_resuming_completes_the_original_request(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The end-to-end shape a real browser follows: parked at login, signs
    in, resumes, lands back at the product's own redirect URI with a code.
    The PKCE-correct exchange itself is proven by
    ``test_a_full_browser_shaped_resume_round_trip``; this test is the
    park-then-resume mechanics in isolation."""
    registration = register_product("crm")
    org = entitled_org("crm")

    parked = client.get(AUTHORIZE, _authorize_params(registration, org))
    assert urlsplit(parked["Location"]).path == "/login"
    token = _resume_token(parked["Location"])

    signed_in(org["user"])  # the login page's own form submission, abstracted
    resumed = client.get(RESUME, {"resume": token})

    assert resumed.status_code == 302
    assert resumed["Location"].startswith(registration["redirect_uri"])
    code = parse_qs(urlsplit(resumed["Location"]).query)["code"][0]
    assert code


def test_a_full_browser_shaped_resume_round_trip(
    client, signing_key, register_product, entitled_org, signed_in
):
    """The complete, PKCE-correct path: not logged in, parked, logs in,
    resumes, exchanges the code, gets a verifiable token."""
    from workeasy360.platform.ecosystem import tokens

    verifier, challenge = _pkce()
    registration = register_product("crm")
    org = entitled_org("crm")

    parked = client.get(
        AUTHORIZE, _authorize_params(registration, org, code_challenge=challenge)
    )
    token = _resume_token(parked["Location"])

    signed_in(org["user"], aal=2)
    resumed = client.get(RESUME, {"resume": token})
    assert resumed.status_code == 302
    code = parse_qs(urlsplit(resumed["Location"]).query)["code"][0]

    exchanged = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_assertion(registration),
        },
    )
    assert exchanged.status_code == 200
    claims = tokens.verify(
        exchanged.json()["id_token"],
        audience=registration["client_id"],
        expected_type=tokens.TYPE_ID_TOKEN,
    )
    assert claims["sub"] == str(org["user"].id)
    assert claims["organization_id"] == org["organization_id"]
    assert claims["nonce"] == "client-nonce"


# --- the organization-chooser leg -------------------------------------------


def test_no_organization_named_parks_at_the_chooser_then_resumes(
    client, signing_key, register_product, entitled_org, signed_in
):
    """A request with no organization at all: signed in, still parked - at
    the chooser, not the product - then resumed with the user's choice."""
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    params = _authorize_params(registration)  # no organization
    parked = client.get(AUTHORIZE, params)
    assert urlsplit(parked["Location"]).path == "/organizations/select"
    token = _resume_token(parked["Location"])

    resumed = client.get(
        RESUME, {"resume": token, "organization_id": org["organization_id"]}
    )
    assert resumed.status_code == 302
    assert resumed["Location"].startswith(registration["redirect_uri"])
    assert "code" in parse_qs(urlsplit(resumed["Location"]).query)


def test_resuming_with_no_organization_choice_re_parks_at_the_chooser(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Resume reached with still no organization decided (e.g. the chooser
    page was reloaded): re-parked under a fresh token, not failed back to
    the product - the user is signed in, only the choice is missing."""
    registration = register_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"])

    parked = client.get(AUTHORIZE, _authorize_params(registration))
    token = _resume_token(parked["Location"])

    resumed = client.get(RESUME, {"resume": token})
    assert urlsplit(resumed["Location"]).path == "/organizations/select"
    assert _resume_token(resumed["Location"]) != token


# --- Task 32: tampering, replay, cross-client resume, wrong-org substitution


def test_a_resume_token_is_single_use(
    client, signing_key, register_product, entitled_org, signed_in
):
    """Clicked twice - a doubled tab, a replayed URL - resumes at most once."""
    registration = register_product("crm")
    org = entitled_org("crm")
    parked = client.get(AUTHORIZE, _authorize_params(registration, org))
    token = _resume_token(parked["Location"])

    signed_in(org["user"])
    first = client.get(RESUME, {"resume": token})
    assert first.status_code == 302
    assert first["Location"].startswith(registration["redirect_uri"])

    second = client.get(RESUME, {"resume": token})
    assert urlsplit(second["Location"]).path == "/login"
    assert second["Location"] != parked["Location"]  # a fresh, different park


def test_an_unknown_resume_token_is_sent_to_login_not_to_any_product(
    client, signing_key, register_product, entitled_org
):
    """No product-supplied redirect exists for a token nobody minted - the
    only safe destination is the platform's own login page."""
    response = client.get(RESUME, {"resume": "not-a-real-token"})
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == "/login"
    assert "error=resume_invalid" in response["Location"]


def test_an_expired_resume_token_cannot_be_completed(
    client, signing_key, register_product, entitled_org, signed_in
):
    from workeasy360.platform.ecosystem.models import PendingAuthorization

    registration = register_product("crm")
    org = entitled_org("crm")
    parked = client.get(AUTHORIZE, _authorize_params(registration, org))
    token = _resume_token(parked["Location"])

    PendingAuthorization.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    signed_in(org["user"])
    response = client.get(RESUME, {"resume": token})
    assert urlsplit(response["Location"]).path == "/login"
    assert "error=resume_invalid" in response["Location"]


def test_a_product_supplied_organization_cannot_be_substituted_at_resume(
    client, signing_key, register_product, entitled_org, two_orgs_one_user, signed_in
):
    """The original request named an organization. A browser-supplied
    ``organization_id`` at resume must not override it - the resumed request
    replays exactly what was verified at `/authorize`, nothing the browser
    adds at resume time (PendingAuthorization's docstring, Task 32)."""
    registration = register_product("crm")
    org_a, org_b, user = two_orgs_one_user("crm")

    parked = client.get(AUTHORIZE, _authorize_params(registration, org_a))
    token = _resume_token(parked["Location"])

    client.force_login(user)
    session = client.session
    session["w360_aal"] = 1
    session.save()

    # Attempt substitution: the request was parked for org_a, but the
    # resume call asks for org_b.
    resumed = client.get(
        RESUME, {"resume": token, "organization_id": org_b["organization_id"]}
    )
    assert resumed.status_code == 302
    assert resumed["Location"].startswith(registration["redirect_uri"])
    code = parse_qs(urlsplit(resumed["Location"]).query)["code"][0]

    # Redeem it and check the token names org_a, never the browser's org_b.

    verifier = "irrelevant-pkce-mismatch-only-matters-for-success-path"
    exchanged = client.post(
        TOKEN,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": registration["redirect_uri"],
            "code_verifier": verifier,
            **_assertion(registration),
        },
    )
    # PKCE will not match (this test does not thread the real verifier
    # through) - the point proven is upstream: the *code itself*, inspected
    # directly, is bound to org_a regardless of what the resume call asked
    # for.
    from workeasy360.platform.ecosystem.authorization import hash_code
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    row = AuthorizationCode.objects.get(code_hash=hash_code(code))
    assert str(row.organization_id) == org_a["organization_id"]
    assert str(row.organization_id) != org_b["organization_id"]
    assert exchanged.status_code == 400  # PKCE mismatch refuses the exchange


@pytest.fixture
def two_orgs_one_user(entitled_org, organization_factory, entitled, tenant_ctx_factory):
    """One user, member of two separately entitled organizations - the
    substitution scenario needs a user resume could plausibly authenticate
    into either of, not two unrelated users."""
    from workeasy360.platform.entitlements.services.seats import assign_seat
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    def make(product_code: str = "crm"):
        org_a = entitled_org(product_code)
        user = org_a["user"]

        org, membership_b, _owner = organization_factory(owner=user)
        entitled(org.id, product_code, seats=5)
        ctx_b = tenant_ctx_factory(org, membership_b, user)
        with tenant_context(ctx_b), tenant_db_scope(ctx_b):
            assign_seat(org.id, pool_key=product_code, membership_id=membership_b.id)

        org_b = {
            "org": org,
            "membership": membership_b,
            "user": user,
            "ctx": ctx_b,
            "organization_id": str(org.id),
        }
        return org_a, org_b, user

    return make

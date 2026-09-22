"""Remote entitlement verification, and what it refuses to tell a product.

Three properties, in order of how much damage their absence would do:

1. **A product cannot ask about another product.** There is no field in which
   to name one - the subject comes from the authenticated credential.
2. **A platform failure is never reported as a denial.** ``deny`` makes a
   product lock out a paying customer; 503 makes it retry.
3. **Nothing commercial crosses the boundary.** No plan, no price, no
   subscription, no renewal date. A product gets an access decision.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

pytestmark = pytest.mark.django_db

VERIFY = "/api/v1/integrations/entitlements/verify"
TOKEN = "/api/v1/integrations/oauth/token"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _auth(registration) -> dict:
    now = int(time.time())
    return {
        "client_assertion_type": ASSERTION_TYPE,
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


def _verify(client, registration, organization_id, **extra):
    return client.post(
        VERIFY,
        {"organization_id": str(organization_id), **extra, **_auth(registration)},
    )


# --- the decision ---------------------------------------------------------


def test_an_entitled_organization_is_allowed(
    client, signing_key, register_product, entitled_org
):
    registration = register_product("crm")
    org = entitled_org("crm")

    body = _verify(client, registration, org["organization_id"]).json()
    assert body["decision"] == "allow"
    assert body["reason"] == "entitled"
    assert body["product_code"] == "crm"
    assert body["organization_id"] == org["organization_id"]
    assert body["write_allowed"] is True
    assert body["revalidate_after"] > 0


def test_the_response_carries_no_commercial_detail(
    client, signing_key, register_product, entitled_org, catalog
):
    """A product gets an access decision, never "what plan are they on?"
    (ADR-005). Asserted against the response text so a field added later has
    to be deliberate."""
    registration = register_product("crm")
    org = entitled_org("crm")

    raw = _verify(client, registration, org["organization_id"]).content.decode()
    for leaked in (
        "plan",
        "price",
        "subscription",
        "amount",
        "currency",
        "invoice",
        "renew",
        "trial",
        "seats",
        "bundle",
    ):
        assert leaked not in raw.lower(), leaked


def test_an_unentitled_organization_is_denied(
    client, signing_key, register_product, entitled_org
):
    registration = register_product("crm")
    org = entitled_org("books")

    body = _verify(client, registration, org["organization_id"]).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "not_entitled"


def test_an_unknown_organization_is_indistinguishable_from_an_unentitled_one(
    client, signing_key, register_product, entitled_org
):
    """Any difference - reason, status, shape - makes this endpoint an
    organization-enumeration oracle for every product in the ecosystem."""
    registration = register_product("crm")
    known_but_unentitled = entitled_org("books")

    unknown = _verify(client, registration, uuid.uuid4()).json()
    unentitled = _verify(
        client, registration, known_but_unentitled["organization_id"]
    ).json()

    assert unknown["decision"] == unentitled["decision"] == "deny"
    assert unknown["reason"] == unentitled["reason"] == "not_entitled"
    assert set(unknown) == set(unentitled)


@pytest.mark.parametrize(
    "status,decision,reason,write_allowed",
    [
        ("active", "allow", "entitled", True),
        ("trial", "allow", "entitled", True),
        ("grace", "allow", "entitled", True),
        ("past_due", "allow", "entitled", True),
        # Read-only: the customer can still see and export their data, which
        # is what suspension is for. A product must be told it may not write.
        ("suspended", "allow", "entitled", False),
        ("expired", "deny", "expired", False),
    ],
)
def test_each_entitlement_status_maps_to_a_documented_answer(
    client,
    signing_key,
    register_product,
    entitled_org,
    status,
    decision,
    reason,
    write_allowed,
):
    """The reason codes are a versioned contract, mapped from the platform's
    internal enum rather than passed through - a product switching on a value
    that changed underneath would break with no deploy of its own."""
    from workeasy360.contracts.enums import EntitlementStatus

    registration = register_product("crm")
    org = entitled_org("crm", status=EntitlementStatus(status))

    body = _verify(client, registration, org["organization_id"]).json()
    assert body["decision"] == decision
    assert body["reason"] == reason
    assert body["write_allowed"] is write_allowed


def test_write_allowed_is_resolved_not_assumed(
    client, signing_key, register_product, entitled_org
):
    """A read-only answer does not imply a writable one.

    Telling a suspended organization's product ``write_allowed: true`` would
    have it offer a save button the platform then refuses, and the product
    would look broken for a condition it was never told about.
    """
    from workeasy360.contracts.enums import EntitlementStatus

    registration = register_product("crm")
    org = entitled_org("crm", status=EntitlementStatus.SUSPENDED)

    read = _verify(client, registration, org["organization_id"]).json()
    assert read["decision"] == "allow"
    assert read["write_allowed"] is False

    write = _verify(client, registration, org["organization_id"], write="true").json()
    assert write["decision"] == "deny"
    assert write["reason"] == "read_only"


# --- the product cannot choose the subject --------------------------------


def test_a_product_cannot_ask_about_another_product(
    client, signing_key, register_product, entitled_org
):
    """The subject comes from the credential, so a CRM client asking about
    Books cannot express the question.

    ``product_code`` is sent anyway, to prove it is ignored rather than
    merely undocumented.
    """
    crm = register_product("crm")
    register_product("books")
    org = entitled_org("books")

    body = _verify(
        client, crm, org["organization_id"], product_code="books", product="books"
    ).json()

    assert body["product_code"] == "crm"
    assert body["decision"] == "deny"


def test_two_products_get_independent_answers_for_one_organization(
    client, signing_key, register_product, entitled_org
):
    crm = register_product("crm")
    books = register_product("books")
    org = entitled_org("crm")

    assert _verify(client, crm, org["organization_id"]).json()["decision"] == "allow"
    assert _verify(client, books, org["organization_id"]).json()["decision"] == "deny"


def test_a_suspended_integration_cannot_verify_entitlement(
    client, signing_key, register_product, entitled_org, platform_admin, in_tenant
):
    """Suspension stops the product operating, not only signing users in.

    A suspended integration that could still read every organization's
    entitlement state would make suspension a half-measure - and the usual
    reason to suspend is that the product is compromised.
    """
    from workeasy360.platform.ecosystem import services

    registration = register_product("crm")
    org = entitled_org("crm")
    assert _verify(client, registration, org["organization_id"]).json()["decision"] == (
        "allow"
    )

    with in_tenant(platform_admin["ctx"]):
        services.transition(
            platform_admin["ctx"],
            client_id=registration["client_id"],
            to_status="suspended",
            reason="compromised",
        )

    response = _verify(client, registration, org["organization_id"])
    assert response.status_code == 403
    assert response.json()["error"] == "integration_suspended"


# --- failure is not denial ------------------------------------------------


def test_a_platform_failure_is_a_503_and_never_a_denial(
    client, signing_key, register_product, entitled_org, monkeypatch
):
    """The single most consequential behaviour in this module.

    A product that receives ``deny`` will tell a paying customer they have no
    licence and may tear down their session. A product that receives 503 will
    retry or honour its last known answer. Collapsing an outage into a denial
    converts a platform incident into an ecosystem-wide false lockout.
    """
    from workeasy360.platform.ecosystem import verification

    registration = register_product("crm")
    org = entitled_org("crm")

    def exploded(*args, **kwargs):
        raise RuntimeError("the database is on fire")

    monkeypatch.setattr(
        "workeasy360.platform.entitlements.selectors.decision_for_organization",
        exploded,
    )
    response = _verify(client, registration, org["organization_id"])

    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"
    assert response["Retry-After"]
    assert "deny" not in response.content.decode()
    assert verification.DENY not in response.content.decode()


def test_a_malformed_organization_id_is_a_request_error_not_a_denial(
    client, signing_key, register_product
):
    """ "That is not a UUID" is a client bug, and saying so costs nothing:
    the caller already knows what they sent."""
    registration = register_product("crm")
    response = client.post(
        VERIFY, {"organization_id": "not-a-uuid", **_auth(registration)}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_verification_requires_client_authentication(
    client, signing_key, register_product
):
    register_product("crm")
    response = client.post(VERIFY, {"organization_id": str(uuid.uuid4())})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_the_answer_is_never_cached(client, signing_key, register_product, entitled_org):
    """An entitlement answer cached by an intermediary outlives the
    revocation it was supposed to reflect."""
    registration = register_product("crm")
    org = entitled_org("crm")
    response = _verify(client, registration, org["organization_id"])
    assert "no-store" in response["Cache-Control"]


# --- revocation -----------------------------------------------------------


def test_revoking_an_entitlement_is_reflected_on_the_next_call(
    client, signing_key, register_product, entitled_org, entitled
):
    """No token lifetime bounds this one: the entitlement endpoint is a live
    read, which is precisely why the guide tells products to revalidate on it
    rather than trusting the token for its whole life.

    Re-granted through the ``entitled`` fixture rather than by importing
    ``commercial.provisioning`` here: this module is ``platform/`` and F-4
    forbids that edge, in tests as well as in source. The fixture lives in
    the root conftest, outside the package, which is exactly where a test
    helper that needs both sides belongs.
    """
    from workeasy360.contracts.enums import EntitlementStatus

    registration = register_product("crm")
    org = entitled_org("crm")
    assert _verify(client, registration, org["organization_id"]).json()["decision"] == (
        "allow"
    )

    entitled(org["org"].id, "crm", status=EntitlementStatus.EXPIRED)

    body = _verify(client, registration, org["organization_id"]).json()
    assert body["decision"] == "deny"
    assert body["reason"] == "expired"

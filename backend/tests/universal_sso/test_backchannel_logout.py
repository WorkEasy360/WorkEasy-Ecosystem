"""OpenID Connect Back-Channel Logout 1.0 for products (ADR-069).

What is proven here, against the real services and the real database:

* the manifest field, its version gate and its same-origin rule;
* ``sid`` in the ID token for a product that takes back-channel logout -
  and **no change at all** to the tokens of a product that does not;
* every way a Workeasy360 sign-in ends queues exactly the right logouts, in
  the same transaction: signing out (one sign-in), revoking one session,
  revoking the others (the caller's own sign-in is spared), "sign out
  everywhere", a password change, an account switch;
* the delivery state machine: delivered, refused, retried, dead, leased;
* the logout token itself, verified the way a product verifies it.

The real HTTP delivery to a real product is proven end to end by the
workeasy360-sso live suite (``tests/live`` in that repository).
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from django.test import Client

from tests.factories.manifests import PRODUCT_ORIGIN, TEST_ISSUER, manifest_for
from tests.universal_sso.conftest import AUTHORIZE, authorize_query, query_of

pytestmark = pytest.mark.django_db

BACKCHANNEL_URI = f"{PRODUCT_ORIGIN}/auth/backchannel-logout"


def _manifest(code: str, **extra):
    return manifest_for(
        code,
        manifest_version="2026-11-15",
        sign_in_mode="sso_only",
        backchannel_logout_uri=BACKCHANNEL_URI,
        **extra,
    )


@pytest.fixture
def bcl_product(register_product):
    def make(code: str = "crm"):
        return register_product(code, manifest=_manifest(code))

    return make


@pytest.fixture
def person(entitled_org, with_password, prefer):
    org = entitled_org("crm")
    user = with_password(org["user"])
    prefer(user, "auto_sso")
    return {"org": org, "user": user}


def _sign_in_to(browser, registration, organization_id, product_client):
    """A real authorization code flow through the browser's live session."""
    verifier, query = authorize_query(registration, organization_id)
    granted = browser.get(AUTHORIZE, query)
    assert granted.status_code == 302, granted.content
    code = query_of(granted)["code"][0]
    reference = product_client(registration)
    issued = reference.exchange_code(code=code, code_verifier=verifier)
    return reference.verify_id_token(issued["id_token"], expected_nonce="client-nonce")


def _sign_in_id(browser) -> uuid.UUID:
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    return uuid.UUID(str(browser.session[SESSION_SIGN_IN_ID_KEY]))


def _deliveries(user):
    from workeasy360.platform.ecosystem.models import BackchannelLogout

    return list(BackchannelLogout.objects.filter(user_id=user.pk).order_by("created_at"))


# --- the manifest -------------------------------------------------------------------


def test_the_manifest_takes_a_backchannel_uri_from_version_2026_11_15():
    from workeasy360.contracts.values.manifest import parse_manifest

    manifest = parse_manifest(_manifest("crm"), allow_insecure_urls=True)
    assert manifest.backchannel_logout_uri == BACKCHANNEL_URI
    assert manifest.as_dict()["backchannel_logout_uri"] == BACKCHANNEL_URI


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"manifest_version": "2026-10-12"}, "requires manifest_version"),
        ({"backchannel_logout_uri": "http://localhost:9200/logout"}, "same origin"),
        ({"backchannel_logout_uri": "https://evil.example/logout"}, "same origin"),
        ({"backchannel_logout_uri": f"{BACKCHANNEL_URI}?x=1"}, None),
        ({"backchannel_logout_uri": f"{BACKCHANNEL_URI}#f"}, None),
        ({"backchannel_logout_uri": f"{PRODUCT_ORIGIN}/*"}, "wildcard"),
        (
            {"sign_in_mode": "local_only", "login_url": f"{PRODUCT_ORIGIN}/login"},
            "local_only",
        ),
    ],
)
def test_the_backchannel_uri_is_validated(changes, message):
    from workeasy360.contracts.values.manifest import ManifestError, parse_manifest

    payload = {**_manifest("crm"), **changes}
    with pytest.raises(ManifestError, match=message):
        parse_manifest(payload, allow_insecure_urls=True)


def test_an_explicit_default_port_is_the_same_origin():
    """RFC 6454: ``https://host:443`` and ``https://host`` are one origin."""
    from workeasy360.contracts.values.manifest import parse_manifest

    payload = manifest_for(
        "crm",
        origin="https://crm.example.com",
        manifest_version="2026-11-15",
        sign_in_mode="sso_only",
        backchannel_logout_uri="https://crm.example.com:443/auth/backchannel-logout",
    )
    manifest = parse_manifest(payload, allow_insecure_urls=False)
    assert manifest.backchannel_logout_uri.endswith("/auth/backchannel-logout")


def test_older_manifests_are_unchanged():
    from workeasy360.contracts.values.manifest import parse_manifest

    old = manifest_for("crm", manifest_version="2026-10-12", sign_in_mode="sso_only")
    manifest = parse_manifest(old, allow_insecure_urls=True)
    assert manifest.backchannel_logout_uri == ""
    assert "backchannel_logout_uri" not in manifest.as_dict()


# --- discovery and the ID token ---------------------------------------------------------


def test_discovery_advertises_backchannel_logout(client, signing_key):
    doc = client.get("/.well-known/openid-configuration").json()
    assert doc["backchannel_logout_supported"] is True
    assert doc["backchannel_logout_session_supported"] is True
    assert "sid" in doc["claims_supported"]


def test_sid_is_the_sign_in_and_only_for_products_that_take_backchannel_logout(
    signing_key, bcl_product, sso_product, person, password_login, product_client
):
    crm = bcl_product("crm")
    browser = password_login(Client(), person["user"])
    claims = _sign_in_to(browser, crm, person["org"]["organization_id"], product_client)
    assert claims["sid"] == str(_sign_in_id(browser))

    from workeasy360.platform.ecosystem.models import ProductSignIn

    row = ProductSignIn.objects.get(user_id=person["user"].pk)
    assert row.sign_in_id == _sign_in_id(browser) and row.ended_at is None


def test_a_product_without_backchannel_logout_gets_exactly_the_old_token(
    signing_key, sso_product, person, password_login, product_client
):
    from workeasy360.platform.ecosystem.models import ProductSignIn

    crm = sso_product("crm")
    browser = password_login(Client(), person["user"])
    claims = _sign_in_to(browser, crm, person["org"]["organization_id"], product_client)
    assert "sid" not in claims
    assert not ProductSignIn.objects.exists()


def test_a_second_exchange_in_the_same_sign_in_records_one_row(
    signing_key, bcl_product, person, password_login, product_client
):
    from workeasy360.platform.ecosystem.models import ProductSignIn

    crm = bcl_product("crm")
    browser = password_login(Client(), person["user"])
    for _ in range(2):
        _sign_in_to(browser, crm, person["org"]["organization_id"], product_client)
    assert ProductSignIn.objects.filter(ended_at__isnull=True).count() == 1


# --- every way a sign-in ends -------------------------------------------------------------


@pytest.fixture
def two_devices(signing_key, bcl_product, person, password_login, product_client):
    """The same person signed in to CRM from two browsers (two sign-ins)."""
    crm = bcl_product("crm")
    laptop = password_login(Client(), person["user"])
    phone = password_login(Client(), person["user"])
    org_id = person["org"]["organization_id"]
    _sign_in_to(laptop, crm, org_id, product_client)
    _sign_in_to(phone, crm, org_id, product_client)
    return {"crm": crm, "laptop": laptop, "phone": phone, **person}


def test_signing_out_tells_the_products_of_that_sign_in_only(two_devices):
    laptop, phone, user = two_devices["laptop"], two_devices["phone"], two_devices["user"]
    laptop_sign_in = _sign_in_id(laptop)
    response = laptop.post("/api/v1/auth/logout")
    assert response.status_code in (200, 204), response.content
    (delivery,) = _deliveries(user)
    assert delivery.sign_in_id == laptop_sign_in
    assert delivery.reason == "signed_out"
    assert delivery.integration.client_id == two_devices["crm"]["client_id"]
    # The phone's sign-in, and its product sessions, are untouched.
    from workeasy360.platform.ecosystem.models import ProductSignIn

    assert ProductSignIn.objects.get(sign_in_id=_sign_in_id(phone)).ended_at is None


def test_sign_out_everywhere_names_the_person_not_a_sign_in(two_devices):
    from workeasy360.platform.identity import account

    account.sign_out_everywhere(two_devices["user"].pk)
    (delivery,) = _deliveries(two_devices["user"])  # one per product, not per device
    assert delivery.sign_in_id is None
    assert delivery.reason == "signed_out_everywhere"


def test_revoking_the_other_sessions_spares_the_callers_own_sign_in(two_devices):
    from workeasy360.platform.identity import account
    from workeasy360.platform.identity.models import UserSession

    laptop = two_devices["laptop"]
    phone_sign_in = _sign_in_id(two_devices["phone"])
    keep = UserSession.objects.get(session_key=laptop.session.session_key).public_id
    account.revoke_other_sessions(two_devices["user"].pk, keep_public_id=keep)
    (delivery,) = _deliveries(two_devices["user"])
    assert delivery.sign_in_id == phone_sign_in
    assert delivery.reason == "other_sessions_revoked"


def test_revoking_one_session_tells_that_sign_ins_products(two_devices):
    from workeasy360.platform.identity import account
    from workeasy360.platform.identity.models import UserSession

    phone = two_devices["phone"]
    phone_sign_in = _sign_in_id(phone)
    target = UserSession.objects.get(session_key=phone.session.session_key).public_id
    account.revoke_session(two_devices["user"].pk, target)
    (delivery,) = _deliveries(two_devices["user"])
    assert delivery.sign_in_id == phone_sign_in


def test_a_password_change_signs_every_product_session_out(two_devices):
    from tests.universal_sso.conftest import PASSWORD
    from workeasy360.platform.identity import account

    account.change_password(
        two_devices["user"].pk,
        current_password=PASSWORD,
        new_password="an entirely new passphrase 9917",  # pragma: allowlist secret
    )
    (delivery,) = _deliveries(two_devices["user"])
    assert delivery.sign_in_id is None and delivery.reason == "password_changed"


def test_a_sign_in_that_opened_no_backchannel_product_queues_nothing(
    signing_key, sso_product, person, password_login, product_client
):
    crm = sso_product("crm")
    browser = password_login(Client(), person["user"])
    _sign_in_to(browser, crm, person["org"]["organization_id"], product_client)
    browser.post("/api/v1/auth/logout")
    assert _deliveries(person["user"]) == []


def test_an_ended_sign_in_is_told_once(two_devices):
    from workeasy360.platform.identity import account

    two_devices["laptop"].post("/api/v1/auth/logout")
    account.sign_out_everywhere(two_devices["user"].pk)
    # The laptop's product sign-in already ended; everywhere covers the phone's.
    reasons = [d.reason for d in _deliveries(two_devices["user"])]
    assert reasons == ["signed_out", "signed_out_everywhere"]


# --- delivery --------------------------------------------------------------------------


@pytest.fixture
def queued(two_devices):
    two_devices["laptop"].post("/api/v1/auth/logout")
    return two_devices


@pytest.fixture
def product_answers(monkeypatch):
    """The product's HTTP answer, at the egress boundary (its own suite proves
    the socket layer). Captures what was sent."""
    from workeasy360.integration.backchannel import delivery
    from workeasy360.integration.egress.client import EgressResponse

    sent: list = []
    behaviour = {"status": 200, "raise": None}

    def fake_send(request, **kwargs):
        sent.append(request)
        if behaviour["raise"] is not None:
            raise behaviour["raise"]
        return EgressResponse(
            status=behaviour["status"],
            headers={},
            body=b"",
            truncated=False,
            duration_s=0.01,
        )

    monkeypatch.setattr(delivery, "send", fake_send)
    return {"sent": sent, "behaviour": behaviour}


def _verify_logout_token(token: str, client_id: str):
    from workeasy360.platform.ecosystem import keys

    header = jwt.get_unverified_header(token)
    key = next(k for k in keys.jwks()["keys"] if k["kid"] == header["kid"])
    claims = jwt.decode(
        token,
        jwt.PyJWK(key).key,
        algorithms=[key["alg"]],
        audience=client_id,
        issuer=TEST_ISSUER,
        options={"require": ["iss", "aud", "iat", "exp", "jti", "events"]},
    )
    return header, claims


def test_a_delivery_posts_a_valid_logout_token(queued, product_answers):
    from urllib.parse import parse_qs

    from workeasy360.integration.backchannel import delivery
    from workeasy360.platform.ecosystem.models import BackchannelLogoutStatus

    summary = delivery.deliver_due()
    assert summary["delivered"] == 1
    (request,) = product_answers["sent"]
    assert request.url == BACKCHANNEL_URI and request.method == "POST"
    assert ("content-type", "application/x-www-form-urlencoded") in request.headers
    token = parse_qs(request.body.decode())["logout_token"][0]
    header, claims = _verify_logout_token(token, queued["crm"]["client_id"])
    assert header["typ"] == "logout+jwt"
    assert claims["sub"] == str(queued["user"].pk)
    assert claims["sid"] == str(_deliveries(queued["user"])[0].sign_in_id)
    assert claims["events"] == {"http://schemas.openid.net/event/backchannel-logout": {}}
    assert claims["typ"] == "logout_token"
    assert "nonce" not in claims
    assert claims["exp"] - claims["iat"] == 120
    assert (
        _deliveries(queued["user"])[0].status == BackchannelLogoutStatus.DELIVERED.value
    )


def test_a_logout_token_is_never_an_identity(queued, product_answers):
    from urllib.parse import parse_qs

    from workeasy360.integration.backchannel import delivery
    from workeasy360.platform.ecosystem import tokens

    delivery.deliver_due()
    token = parse_qs(product_answers["sent"][0].body.decode())["logout_token"][0]
    with pytest.raises(jwt.InvalidTokenError):
        tokens.verify(
            token, audience=queued["crm"]["client_id"], expected_type="id_token"
        )


def test_a_refusal_is_final(queued, product_answers):
    from workeasy360.integration.backchannel import delivery

    product_answers["behaviour"]["status"] = 400
    assert delivery.deliver_due()["refused"] == 1
    row = _deliveries(queued["user"])[0]
    assert row.status == "failed" and row.last_outcome == "http_400"
    assert delivery.deliver_due()["claimed"] == 0


def test_an_outage_is_retried_on_the_schedule_then_given_up(queued, product_answers):
    from django.utils import timezone

    from workeasy360.integration.backchannel import delivery
    from workeasy360.platform.ecosystem import backchannel
    from workeasy360.platform.ecosystem.models import BackchannelLogout

    product_answers["behaviour"]["status"] = 503
    for attempt in range(1, backchannel.MAX_ATTEMPTS + 1):
        BackchannelLogout.objects.update(next_attempt_at=timezone.now())
        assert delivery.deliver_due()["claimed"] == 1
        row = _deliveries(queued["user"])[0]
        assert row.attempts == attempt
    assert row.status == "dead" and row.last_outcome == "http_503"


def test_a_transport_failure_is_retried_not_refused(queued, product_answers):
    from workeasy360.integration.backchannel import delivery
    from workeasy360.integration.egress.client import EgressFailure

    product_answers["behaviour"]["raise"] = EgressFailure("timeout", "read timed out")
    assert delivery.deliver_due()["retry"] == 1
    row = _deliveries(queued["user"])[0]
    assert row.status == "pending" and row.last_outcome == "timeout"
    assert row.next_attempt_at > row.created_at


def test_an_address_the_egress_policy_refuses_is_final(queued, product_answers):
    from workeasy360.integration.backchannel import delivery
    from workeasy360.integration.egress.policy import EgressRejected

    product_answers["behaviour"]["raise"] = EgressRejected(
        "private_address", "internal host"
    )
    assert delivery.deliver_due()["refused"] == 1
    assert _deliveries(queued["user"])[0].last_outcome == "rejected_private_address"


def test_a_claimed_delivery_is_leased_to_one_worker(queued, product_answers):
    from workeasy360.platform.ecosystem import backchannel

    first = backchannel.claim_due()
    second = backchannel.claim_due()
    assert len(first) == 1 and second == []


def test_operators_and_the_person_can_see_what_is_outstanding(queued, product_answers):
    from workeasy360.platform.ecosystem import backchannel

    pending = backchannel.pending_for_user(queued["user"].pk)
    assert pending[0]["product_code"] == "crm" and pending[0]["status"] == "pending"
    summary = backchannel.delivery_summary(queued["crm"]["integration"])
    assert summary["enabled"] is True and summary["counts"]["pending"] == 1


def test_staff_see_the_last_delivery_the_last_error_and_the_retry_state(
    queued, product_answers
):
    """What the Super Admin console shows: categories and times, never a body."""
    from django.utils import timezone

    from workeasy360.integration.backchannel import delivery
    from workeasy360.platform.ecosystem import backchannel
    from workeasy360.platform.ecosystem.models import BackchannelLogout

    integration = queued["crm"]["integration"]
    summary = backchannel.delivery_summary(integration)
    assert summary["last_delivery"]["status"] == "pending"
    assert summary["last_delivery"]["scope"] == "session"
    assert summary["last_error"] is None
    assert summary["retry"]["pending"] == 1 and summary["retry"]["retrying"] == 0
    assert summary["retry"]["attempt_limit"] == backchannel.MAX_ATTEMPTS

    product_answers["behaviour"]["status"] = 503
    delivery.deliver_due()
    summary = backchannel.delivery_summary(integration)
    assert summary["last_error"]["outcome"] == "http_503"
    assert summary["retry"]["retrying"] == 1 and summary["retry"]["max_attempts"] == 1
    assert summary["retry"]["next_attempt_at"] is not None

    product_answers["behaviour"]["status"] = 200
    BackchannelLogout.objects.update(next_attempt_at=timezone.now())
    delivery.deliver_due()
    summary = backchannel.delivery_summary(integration)
    assert summary["last_delivery"]["status"] == "delivered"
    assert summary["last_delivery"]["attempts"] == 2
    assert summary["retry"] == {
        "pending": 0,
        "retrying": 0,
        "next_attempt_at": None,
        "max_attempts": 0,
        "attempt_limit": backchannel.MAX_ATTEMPTS,
    }
    assert summary["last_error"] is None  # the earlier 503 was retried to success


def test_queueing_happens_in_the_callers_transaction(two_devices):
    """A sign-out that rolls back leaves no delivery behind (the outbox property)."""
    from django.db import transaction

    from workeasy360.platform.identity import account

    class Abort(Exception):
        pass

    with pytest.raises(Abort):
        with transaction.atomic():
            account.sign_out_everywhere(two_devices["user"].pk)
            raise Abort
    assert _deliveries(two_devices["user"]) == []


def test_the_token_lifetime_is_short(queued, product_answers):
    from urllib.parse import parse_qs

    from workeasy360.integration.backchannel import delivery

    before = int(time.time())
    delivery.deliver_due()
    token = parse_qs(product_answers["sent"][0].body.decode())["logout_token"][0]
    _, claims = _verify_logout_token(token, queued["crm"]["client_id"])
    assert before - 2 <= claims["iat"] <= int(time.time()) + 2

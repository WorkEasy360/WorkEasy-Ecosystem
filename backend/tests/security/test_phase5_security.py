"""Phase 5 security verification: MFA, session assurance, owner boundaries,
organization-creation abuse.

Every test drives the real HTTP stack (Django's test client through the
full middleware chain) unless it is asserting a storage property, where it
reads what the HTTP calls wrote.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.security]

ENROLL = "/api/v1/auth/mfa/totp/enroll"
CONFIRM = "/api/v1/auth/mfa/totp/confirm"
FACTORS = "/api/v1/auth/mfa/factors"
STEP_UP = "/api/v1/auth/mfa/step-up"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ORGANIZATIONS = "/api/v1/organizations"
PASSWORD = "a long enough passphrase for tests"


def _post(client, path, body=None):
    return client.post(path, body or {}, content_type="application/json")


def _login(client, user):
    response = _post(client, LOGIN, {"email": user.email, "password": PASSWORD})
    assert response.status_code == 200
    return client


def _raw_secret(factor_id):
    from workeasy360.platform.identity import mfa
    from workeasy360.platform.identity.models import AuthFactor

    return mfa.decrypt_totp_secret(AuthFactor.objects.get(id=factor_id).secret_ref)


def _code(raw_secret, *, steps_ahead=0):
    from workeasy360.platform.identity import mfa

    return mfa.current_totp(raw_secret, at=time.time() + 30 * steps_ahead)


def _enroll(client):
    """Enroll and confirm a TOTP factor over HTTP. Returns
    (factor_id, raw_secret, base32_secret, recovery_codes)."""
    started = _post(client, ENROLL).json()
    raw = _raw_secret(started["factor_id"])
    confirmed = _post(
        client, CONFIRM, {"factor_id": started["factor_id"], "code": _code(raw)}
    )
    assert confirmed.status_code == 200, confirmed.json()
    return (
        started["factor_id"],
        raw,
        started["secret"],
        confirmed.json()["recovery_codes"],
    )


def _step_up(client, raw, *, steps_ahead=1):
    response = _post(client, STEP_UP, {"code": _code(raw, steps_ahead=steps_ahead)})
    assert response.status_code == 200, response.json()


def _aal(client, slug):
    response = client.get(f"/api/v1/orgs/{slug}/context")
    return response.json().get("aal") if response.status_code == 200 else None


@pytest.fixture
def owner(client, user_factory):
    """A signed-in user who created their organization over HTTP."""
    user = user_factory(password=PASSWORD)
    _login(client, user)
    created = _post(client, ORGANIZATIONS, {"name": "Owner Co"}).json()
    return {"user": user, "slug": created["slug"], "org_id": created["organization_id"]}


# --- MFA: storage and generation (audit item 17) ---------------------------


def test_totp_secrets_are_random_160_bit_and_stored_encrypted(owner, client):
    from workeasy360.platform.identity import mfa
    from workeasy360.platform.identity.models import AuthFactor

    first = mfa.generate_totp_secret()
    assert len(first) == mfa.SECRET_BYTES == 20
    assert first != mfa.generate_totp_secret()

    factor_id, raw, base32_secret, _codes = _enroll(client)
    stored = AuthFactor.objects.get(id=factor_id).secret_ref
    assert base32_secret not in stored
    assert raw.hex() not in stored
    assert mfa.decrypt_totp_secret(stored) == raw  # Fernet round-trip


def test_recovery_codes_are_random_single_use_and_stored_as_keyed_hashes(owner, client):
    from workeasy360.platform.identity import mfa
    from workeasy360.platform.identity.models import AuthFactor

    _factor_id, _raw, _secret, codes = _enroll(client)

    assert len(set(codes)) == mfa.RECOVERY_CODE_COUNT
    assert all(len(c) == 11 and c[5] == "-" for c in codes)
    stored = set(
        AuthFactor.objects.filter(
            user_id=owner["user"].id, kind="recovery_code"
        ).values_list("secret_ref", flat=True)
    )
    assert stored == {mfa.hash_recovery_code(c) for c in codes}
    for code in codes:
        # Neither the plaintext nor its bare (unkeyed) SHA-256 is stored: a
        # database dump alone does not let anyone brute-force the codes.
        assert code not in stored
        assert hashlib.sha256(code.encode()).hexdigest() not in stored


def test_enrollment_needs_proof_of_possession(owner, client):
    from workeasy360.platform.identity.models import AuthFactor

    started = _post(client, ENROLL).json()
    raw = _raw_secret(started["factor_id"])
    wrong = "000000" if _code(raw) != "000000" else "111111"

    response = _post(client, CONFIRM, {"factor_id": started["factor_id"], "code": wrong})

    assert (response.status_code, response.json()["code"]) == (400, "mfa_code_invalid")
    factor = AuthFactor.objects.get(id=started["factor_id"])
    assert factor.confirmed_at is None
    assert not AuthFactor.objects.filter(
        user_id=owner["user"].id, kind="recovery_code"
    ).exists()


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", ENROLL),
        ("post", CONFIRM),
        ("get", FACTORS),
        ("post", STEP_UP),
        ("delete", f"{FACTORS}/{uuid.uuid4()}"),
    ],
)
def test_every_mfa_endpoint_requires_a_signed_in_user(client, method, path):
    response = getattr(client, method)(path, content_type="application/json")
    assert (response.status_code, response.json()["code"]) == (401, "login_required")


@pytest.mark.parametrize("factor_id", ["not-a-uuid", 7, None, ["x"]])
def test_a_malformed_factor_id_is_a_controlled_error(owner, client, factor_id):
    _post(client, ENROLL)
    response = _post(client, CONFIRM, {"factor_id": factor_id, "code": "123456"})
    assert (response.status_code, response.json()["code"]) == (
        404,
        "mfa_enrollment_not_found",
    )


def test_a_totp_code_is_accepted_once(owner, client):
    """RFC 6238 section 5.2. Including the code that confirmed enrollment."""
    _factor_id, raw, _secret, _codes = _enroll(client)

    replayed = _post(client, STEP_UP, {"code": _code(raw)})
    assert (replayed.status_code, replayed.json()["code"]) == (400, "mfa_code_invalid")

    fresh = _code(raw, steps_ahead=1)
    assert _post(client, STEP_UP, {"code": fresh}).status_code == 200
    assert _post(client, STEP_UP, {"code": fresh}).status_code == 400


def test_a_password_alone_cannot_add_a_factor_to_a_protected_account(
    owner, client, user_factory
):
    """The takeover path this closes: with only the victim's password, enroll
    an attacker-held authenticator, step up with it, and replace the
    victim's recovery codes."""
    from workeasy360.platform.identity.models import AuthFactor

    _enroll(client)
    before = set(AuthFactor.objects.filter(user_id=owner["user"].id).values_list("id"))

    _post(client, LOGOUT)
    _login(client, owner["user"])  # a fresh, password-only (AAL1) session

    for path, body in ((ENROLL, {}), (CONFIRM, {"factor_id": str(uuid.uuid4())})):
        response = _post(client, path, body)
        assert (response.status_code, response.json()["code"]) == (
            401,
            "step_up_required",
        )
    after = set(AuthFactor.objects.filter(user_id=owner["user"].id).values_list("id"))
    assert after == before


def test_adding_a_factor_after_step_up_replaces_the_recovery_codes(owner, client):
    _factor_id, raw, _secret, old_codes = _enroll(client)
    _step_up(client, raw)

    _second_id, _raw2, _secret2, new_codes = _enroll(client)

    assert set(new_codes).isdisjoint(old_codes)
    # Fresh session so the step-up itself is what is being tested.
    _post(client, LOGOUT)
    _login(client, owner["user"])
    assert _post(client, STEP_UP, {"code": old_codes[0]}).status_code == 400
    assert _post(client, STEP_UP, {"code": new_codes[0]}).status_code == 200


def test_removing_the_last_factor_removes_its_recovery_codes(owner, client):
    from workeasy360.platform.identity.models import AuthFactor

    factor_id, raw, _secret, _codes = _enroll(client)
    _step_up(client, raw)

    response = client.delete(f"{FACTORS}/{factor_id}")

    assert response.status_code == 200
    assert not AuthFactor.objects.filter(user_id=owner["user"].id).exists()


# --- MFA: rate limits (audit item 18) ---------------------------------------


def test_mfa_code_attempts_share_one_per_account_budget(
    owner, client, settings, frozen_throttle_window, user_factory, django_user_model
):
    """Step-up (TOTP or recovery code) and enrollment confirmation draw on
    the same per-account bucket, so alternating endpoints does not double a
    guesser's attempts - and it is per account, not global: another user is
    unaffected."""
    from django.test import Client

    settings.MFA_STEP_UP_RATE_LIMITS = {
        "step_up_account": (3, 60),
        "step_up_ip": (1000, 60),
    }
    started = _post(client, ENROLL).json()

    assert _post(client, STEP_UP, {"code": "000000"}).status_code == 400
    assert _post(
        client, CONFIRM, {"factor_id": started["factor_id"], "code": "x"}
    ).status_code in (400, 404)
    assert _post(client, STEP_UP, {"code": "aaaaa-bbbbb"}).status_code == 400

    for path, body in (
        (STEP_UP, {"code": "000000"}),
        (CONFIRM, {"factor_id": started["factor_id"], "code": "1"}),
    ):
        limited = _post(client, path, body)
        assert limited.status_code == 429
        assert limited.json()["code"] == "rate_limited"
        assert int(limited["Retry-After"]) > 0

    other_user = user_factory(password=PASSWORD)
    other = _login(Client(), other_user)
    assert _post(other, STEP_UP, {"code": "000000"}).status_code == 400  # not 429


# --- MFA: nothing sensitive is logged ---------------------------------------


def test_secrets_and_codes_never_reach_the_logs(
    owner, client, caplog, settings, frozen_throttle_window
):
    caplog.set_level(logging.DEBUG)
    settings.MFA_STEP_UP_RATE_LIMITS = {
        "step_up_account": (4, 60),
        "step_up_ip": (1000, 60),
    }

    started = _post(client, ENROLL).json()
    raw = _raw_secret(started["factor_id"])
    confirm_code = _code(raw)
    codes = _post(
        client, CONFIRM, {"factor_id": started["factor_id"], "code": confirm_code}
    ).json()["recovery_codes"]
    step_code = _code(raw, steps_ahead=1)
    _post(client, STEP_UP, {"code": step_code})
    _post(client, STEP_UP, {"code": codes[0]})
    _post(client, STEP_UP, {"code": "999999"})
    _post(client, STEP_UP, {"code": codes[1]})  # throttled: logs a warning

    logged = caplog.text
    assert "mfa.step_up.throttled" in logged  # the capture genuinely works
    for sensitive in (
        started["secret"],
        started["provisioning_uri"],
        confirm_code,
        step_code,
        *codes,
    ):
        assert sensitive not in logged


# --- session assurance (audit item 19) --------------------------------------


def test_aal2_lapses_after_the_step_up_window(owner, client, monkeypatch, settings):
    from workeasy360.platform.tenancy import resolution

    factor_id, raw, _secret, _codes = _enroll(client)
    _step_up(client, raw)
    assert _aal(client, owner["slug"]) == 2

    later = time.time() + settings.STEP_UP_MAX_AGE_SECONDS + 1
    monkeypatch.setattr(resolution.time, "time", lambda: later)

    assert _aal(client, owner["slug"]) == 1
    removal = client.delete(f"{FACTORS}/{factor_id}")
    assert (removal.status_code, removal.json()["code"]) == (401, "step_up_required")


def test_a_new_sign_in_starts_at_aal1_with_or_without_logout(owner, client):
    _factor_id, raw, _secret, _codes = _enroll(client)
    _step_up(client, raw)
    assert _aal(client, owner["slug"]) == 2

    _login(client, owner["user"])  # signing in again on the same session
    assert _aal(client, owner["slug"]) == 1

    _post(client, LOGOUT)
    assert _aal(client, owner["slug"]) is None  # signed out: uniform denial
    _login(client, owner["user"])
    assert _aal(client, owner["slug"]) == 1


def test_revoking_sessions_ends_an_aal2_session(owner, client):
    from workeasy360.platform.identity.models import User

    _factor_id, raw, _secret, _codes = _enroll(client)
    _step_up(client, raw)
    assert _aal(client, owner["slug"]) == 2

    User.objects.get(id=owner["user"].id).revoke_sessions()

    assert _aal(client, owner["slug"]) is None
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_an_expired_session_carries_no_assurance(owner, client):
    from datetime import timedelta

    from django.utils import timezone

    # Phase 8 extension: sessions live in the identity session registry.
    from workeasy360.platform.identity.models import UserSession

    _factor_id, raw, _secret, _codes = _enroll(client)
    _step_up(client, raw)

    UserSession.objects.filter(session_key=client.session.session_key).update(
        expire_date=timezone.now() - timedelta(seconds=1)
    )

    assert _aal(client, owner["slug"]) is None
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


# --- owner role boundaries (audit item 20) ----------------------------------


def test_a_new_owner_cannot_reach_another_organization(owner, client, user_factory):
    from django.test import Client

    other_user = user_factory(password=PASSWORD)
    other = _login(Client(), other_user)
    theirs = _post(other, ORGANIZATIONS, {"name": "Their Co"}).json()

    for response in (
        client.get(f"/api/v1/orgs/{theirs['slug']}/context"),
        client.get(f"/api/v1/orgs/{theirs['slug']}/memberships"),
        client.get(f"/api/v1/orgs/{theirs['slug']}/organization"),
        client.delete(
            f"/api/v1/orgs/{theirs['slug']}/memberships/{theirs['membership_id']}"
        ),
    ):
        assert (response.status_code, response.json()["code"]) == (404, "not_found")
    # And the chooser never shows it.
    listed = client.get(ORGANIZATIONS).json()["organizations"]
    assert [o["slug"] for o in listed] == [owner["slug"]]


def test_a_new_owner_holds_no_platform_or_control_plane_power(owner, client):
    """Even after step-up: organization ownership is not staff, and staff
    operations check ``is_platform_staff`` regardless of role."""
    from django.urls import get_resolver

    from workeasy360.platform.identity.models import User

    user = User.objects.get(id=owner["user"].id)
    assert (user.is_staff, user.is_superuser) == (False, False)

    _factor_id, raw, _secret, _codes = _enroll(client)
    _step_up(client, raw)

    slug = owner["slug"]
    for path, body in (
        (f"/api/v1/orgs/{slug}/commercial/products", {"code": "x", "name": "X"}),
        (
            f"/api/v1/orgs/{slug}/commercial/price-books",
            {"code": "x", "name": "X", "currency": "GBP"},
        ),
        (f"/api/v1/orgs/{slug}/integrations", {"product_code": "crm"}),
    ):
        response = _post(client, path, body)
        assert response.status_code == 403, (path, response.json())

    # Signing keys have no HTTP surface at all: they are managed by the
    # `w360_signing_key` management command, which only an operator runs.
    routes = [str(p.pattern) for p in get_resolver().url_patterns]
    nested = [
        str(sub.pattern)
        for p in get_resolver().url_patterns
        for sub in getattr(p, "url_patterns", [])
    ]
    assert not [r for r in routes + nested if "signing" in r or "jwks/rotate" in r]


def test_a_new_owner_can_administer_their_own_members(owner, client, user_factory):
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import (
        Membership,
        Organization,
        UserOrganizationAccess,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    colleague = user_factory()
    with (
        unscoped_access(reason="test fixture", module="tests"),
        organization_db_scope(owner["org_id"]),
    ):
        org = Organization.objects.get(id=owner["org_id"])
        membership = Membership(
            organization_id=org.id,
            user_id=colleague.id,
            status=MembershipStatus.ACTIVE.value,
            member_type="internal",
        )
        membership.save()
        projection.sync_access_for_membership(
            user_id=colleague.id, membership=membership, organization=org
        )

    listed = client.get(f"/api/v1/orgs/{owner['slug']}/memberships").json()["results"]
    assert len(listed) == 2

    removed = client.delete(f"/api/v1/orgs/{owner['slug']}/memberships/{membership.id}")
    assert removed.status_code == 200
    assert not UserOrganizationAccess.objects.filter(user_id=colleague.id).exists()

    # The owner cannot remove themselves this way.
    own = next(m for m in listed if m["is_owner"])
    refused = client.delete(
        f"/api/v1/orgs/{owner['slug']}/memberships/{own['membership_id']}"
    )
    assert refused.status_code == 400


# --- organization-creation abuse (audit item 21) -----------------------------


def test_an_anonymous_caller_cannot_create_an_organization(client):
    from django.test import Client

    assert _post(client, ORGANIZATIONS, {"name": "X"}).status_code == 401
    # With CSRF enforced, as a real browser request is, it never reaches the view.
    assert (
        _post(Client(enforce_csrf_checks=True), ORGANIZATIONS, {"name": "X"}).status_code
        == 403
    )


def test_a_disabled_account_cannot_create_an_organization(client, user_factory):
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.models import Organization

    user = user_factory(password=PASSWORD)
    _login(client, user)
    User.objects.filter(id=user.id).update(status="disabled")

    response = _post(client, ORGANIZATIONS, {"name": "Should Not Exist"})

    assert response.status_code == 401
    assert not Organization.objects.filter(created_by_id=user.id).exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 256},
        {"name": ["Acme"]},
        {"name": {"name": "Acme"}},
        {"name": 42},
        {"name": True},
        {"name": "Acme", "idempotency_key": {"k": 1}},
        {"name": "Acme", "idempotency_key": "k" * 129},
    ],
)
def test_malformed_creation_input_is_refused(owner, client, body):
    from workeasy360.platform.organizations.models import Organization

    before = Organization.objects.count()
    response = _post(client, ORGANIZATIONS, body)
    assert response.status_code == 400, response.json()
    assert response.json()["code"] == "organization_validation_error"
    assert Organization.objects.count() == before


def test_a_non_json_body_is_refused(owner, client):
    response = client.post(ORGANIZATIONS, "name=Acme", content_type="text/plain")
    assert response.status_code == 400


def test_organization_creation_is_rate_limited_per_account_not_globally(
    client, user_factory, settings, frozen_throttle_window
):
    from django.test import Client

    settings.ORGANIZATION_CREATION_RATE_LIMITS = {
        "org_create_account": (3, 60),
        "org_create_ip": (1000, 60),
    }
    user = user_factory(password=PASSWORD)
    _login(client, user)
    for i in range(3):
        assert _post(client, ORGANIZATIONS, {"name": f"Co {i}"}).status_code == 201
    limited = _post(client, ORGANIZATIONS, {"name": "Co 4"})
    assert limited.status_code == 429
    assert limited.json()["code"] == "organization_creation_rate_limited"

    other = _login(Client(), user_factory(password=PASSWORD))
    assert _post(other, ORGANIZATIONS, {"name": "Unaffected"}).status_code == 201

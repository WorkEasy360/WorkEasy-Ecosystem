"""There is no second factor anywhere in the platform (ADR-050).

The owner removed two-step verification from the whole software: nobody is
asked for a code, no action waits for one, no organization can require one, and
no token says how strongly anyone signed in. These tests prove the absence from
the outside (what an administrator, a partner and a product can do after a
password sign-in) and from the inside (the source names none of it), so a later
change cannot put a half of it back unnoticed.

What stays is deliberate and listed in ADR-050: the dormant ``AuthFactor``
table and the ``mfa_*`` / ``require_mfa`` columns (no migration, no data loss),
and everything that is *not* a second factor - the ``dangerous`` marker still
blocks a read-only organization and impersonation.
"""

from __future__ import annotations

import json
import pathlib
import re
import uuid

import pytest

from tests.extension import conftest as _ext
from tests.phase7 import conftest as _phase7
from tests.phase7.conftest import PASSWORD

pytestmark = [pytest.mark.django_db, pytest.mark.security]

# Re-exported by assignment so using a fixture as a test argument is not a
# redefinition of an imported name.
console_org = _ext.console_org
login = _ext.login
http_login = _phase7.http_login
staff_client = _phase7.staff_client
partner_client = _phase7.partner_client

PACKAGE = pathlib.Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (PACKAGE / "workeasy360", PACKAGE / "config")


def _post(client, url, body=None):
    return client.post(
        url,
        json.dumps(body or {}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )


# --- what a password sign-in can do -------------------------------------------------------


def test_a_staff_member_signed_in_by_password_only_performs_a_dangerous_action(
    staff_client,
):
    client, _user = staff_client("platform.staff_partner.manage")
    # The session holds nothing but the sign-in: no assurance level, no step-up.
    assert not [
        k for k in client.session.keys() if "aal" in k or "step_up" in k or "mfa" in k
    ]

    response = _post(
        client,
        "/api/v1/admin/partners",
        {
            "code": "no-second-factor",
            "display_name": "No Second Factor",
            "payout_currency": "GBP",
        },
    )
    assert response.status_code == 201, response.content


def test_a_partner_administrator_signed_in_by_password_only_performs_a_dangerous_action(
    partner_client,
):
    client, fx = partner_client("domain.manage")
    response = _post(
        client,
        f"/api/v1/partner/{fx['partner'].id}/domains",
        {"hostname": "app.no-second-factor.example.org"},
    )
    assert response.status_code == 201, response.content


def test_an_organization_owner_signed_in_by_password_only_changes_a_member_role(
    console_org, login
):
    org = console_org()
    member = org["add"]("member")
    owner = login(org["owner"])
    changed = owner.post(
        f"/api/v1/orgs/{org['slug']}/members/{member['membership'].id}/role",
        {"role": "admin"},
        content_type="application/json",
    )
    assert changed.status_code == 200, changed.content


# --- the endpoints and the payloads are gone ---------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/mfa/totp/enroll",
        "/api/v1/auth/mfa/totp/confirm",
        "/api/v1/auth/mfa/factors",
        f"/api/v1/auth/mfa/factors/{uuid.uuid4()}",
        "/api/v1/auth/mfa/step-up",
    ],
)
@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_every_mfa_route_answers_404_whoever_asks(path, method, client, staff_client):
    assert getattr(client, method)(path).status_code == 404  # anonymous
    signed_in, _user = staff_client()
    assert getattr(signed_in, method)(path).status_code == 404


def test_the_session_payload_carries_no_second_factor_field(client, user_factory):
    user = user_factory(password=PASSWORD)
    client.post(
        "/api/v1/auth/login",
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    body = client.get("/api/v1/auth/session").json()
    assert body["authenticated"] is True
    assert not [k for k in body if "mfa" in k or "aal" in k or "factor" in k]


def test_an_organization_cannot_require_a_second_factor(console_org, login):
    from workeasy360.platform.organizations.models import Organization

    org = console_org()
    member = org["add"]("member")
    owner = login(org["owner"])
    saved = owner.put(
        f"/api/v1/orgs/{org['slug']}/security/policy",
        {
            "require_mfa": True,  # a stale client still sends it; it is ignored
            "session_max_age_minutes": None,
            "password_min_length": None,
            "version": 1,
        },
        content_type="application/json",
    )
    assert saved.status_code == 200, saved.content
    assert "require_mfa" not in saved.json()
    read = owner.get(f"/api/v1/orgs/{org['slug']}/security/policy").json()
    assert "require_mfa" not in read
    assert Organization.objects.get(id=org["org"].id).require_mfa is False
    # And a member is never stopped at the door for want of a code.
    access = login(member["user"]).get(f"/api/v1/orgs/{org['slug']}/access")
    assert access.status_code == 200, access.content


def test_a_product_id_token_carries_no_assurance_claim(
    client, signing_key, register_product, entitled_org, product_client, signed_in
):
    from workeasy360.platform.ecosystem import discovery

    product = product_client(register_product("crm"))
    org = entitled_org("crm")
    signed_in(org["user"])
    request = product.begin_authorization(organization_id=org["organization_id"])
    response = client.get(request["url"], request["query"])
    assert response.status_code == 302
    code = product.handle_callback(response["Location"], expected_state=request["state"])
    tokens = product.exchange_code(code=code, code_verifier=request["code_verifier"])
    claims = product.verify_id_token(tokens["id_token"], expected_nonce=request["nonce"])
    assert claims["organization_id"] == str(org["organization_id"])
    assert "aal" not in claims
    assert "aal" not in discovery.document()["claims_supported"]


# --- the source names none of it ---------------------------------------------------------------

#: Names that existed only for the second factor. A hit in non-test, non-migration
#: source is a half-restored feature.
FORBIDDEN = re.compile(
    r"\b(?:StepUpRequired|MfaError|MfaCodeInvalid|MfaThrottled|MfaEnrollmentNotFound"
    r"|record_step_up|reset_session_aal|session_aal|session_mfa_verified"
    r"|SESSION_AAL_KEY|SESSION_STEP_UP_AT_KEY|SESSION_MFA_VERIFIED_KEY"
    r"|verify_step_up_code|begin_totp_enrollment|confirm_totp_enrollment"
    r"|list_mfa_factors|remove_mfa_factor|reset_second_factors"
    r"|users_with_second_factor|users_with_mfa|has_mfa"
    r"|STEP_UP_MAX_AGE_SECONDS|MFA_STEP_UP_RATE_LIMITS|MFA_ENCRYPTION_KEY"
    r"|W360_MFA_ENCRYPTION_KEY|MFA_REQUIRED|mfa_verified|mfa_enrolled|mfa_enabled"
    r"|with_second_factor|mfa_views)\b"
)

#: The dormant storage the removal deliberately leaves (ADR-050): no code reads
#: or writes these, so no behaviour hangs on them, and dropping them is a
#: separate, destructive decision.
DORMANT_STORAGE = {
    "platform/identity/models.py",
    "platform/organizations/models.py",
    "platform/ecosystem/models.py",
}


def _sources():
    for root in SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            rel = path.relative_to(PACKAGE).as_posix()
            if "/tests/" in rel or "/migrations/" in rel or "__pycache__" in rel:
                continue
            yield path, rel.removeprefix("workeasy360/")


def test_no_source_file_enforces_or_defines_a_second_factor():
    offenders = []
    for path, rel in _sources():
        if rel in DORMANT_STORAGE:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()[:100]}")
    assert offenders == [], "\n".join(offenders)


def test_the_organization_policy_has_no_second_factor_switch_in_source():
    """``require_mfa`` survives only as the dormant column on the model."""
    offenders = [
        rel
        for path, rel in _sources()
        if rel != "platform/organizations/models.py"
        and "require_mfa" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_dormant_storage_is_never_read_by_application_code():
    """The columns stay so nothing is lost; nothing may use them."""
    pattern = re.compile(
        r"\b(?:mfa_failed_attempts|mfa_locked_until|auth_factors|AuthFactor)\b"
    )
    allowed = DORMANT_STORAGE | {"platform/identity/lifecycle.py"}
    offenders = [
        rel
        for path, rel in _sources()
        if rel not in allowed and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_mfa_routes_and_module_files_do_not_exist():
    assert not (
        PACKAGE / "workeasy360" / "interfaces" / "api" / "v1" / "mfa_views.py"
    ).exists()
    assert not (PACKAGE / "workeasy360" / "platform" / "identity" / "mfa.py").exists()
    from django.urls import get_resolver

    names = []

    def walk(patterns):
        for p in patterns:
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns)
            elif p.name:
                names.append(p.name)

    walk(get_resolver().url_patterns)
    assert not [n for n in names if n.startswith("mfa-")]

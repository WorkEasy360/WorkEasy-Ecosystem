"""Phase 5 final (Opus) audit: regression tests for the defects it found and
the adversarial cases it added.

Each test names the audit item it covers. The ones marked "Reproduces" failed
on the code as it stood before the audit's fix; the rest pin behaviour the
audit verified and that no earlier test asserted.
"""

from __future__ import annotations

import base64
import json
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


def _wrong_code(raw_secret):
    """A six-digit code valid for no step anywhere near now."""
    near = {_code(raw_secret, steps_ahead=s) for s in range(-3, 4)}
    return next(c for c in (f"{n:06d}" for n in range(1000)) if c not in near)


def _enroll(client):
    started = _post(client, ENROLL).json()
    raw = _raw_secret(started["factor_id"])
    confirmed = _post(
        client, CONFIRM, {"factor_id": started["factor_id"], "code": _code(raw)}
    )
    assert confirmed.status_code == 200, confirmed.json()
    return started["factor_id"], raw, confirmed.json()["recovery_codes"]


def _aal(client, slug):
    response = client.get(f"/api/v1/orgs/{slug}/context")
    return response.json().get("aal") if response.status_code == 200 else None


@pytest.fixture
def owner(client, user_factory):
    user = user_factory(password=PASSWORD)
    _login(client, user)
    created = _post(client, ORGANIZATIONS, {"name": "Audit Co"}).json()
    return {
        "user": user,
        "slug": created["slug"],
        "org_id": created["organization_id"],
        "membership_id": created["membership_id"],
    }


@pytest.fixture
def generous_minute_bucket(settings):
    """Take the per-minute bucket out of the picture, which is what an
    attacker who spreads guesses over many minutes (or a Redis outage across
    N instances, D-012) sees. What is left is the durable limit."""
    settings.MFA_STEP_UP_RATE_LIMITS = {
        "step_up_account": (10_000, 60),
        "step_up_ip": (10_000, 60),
    }


# --- Audit 25: MFA guessing budget -----------------------------------------


def test_step_up_guessing_is_bounded_beyond_the_per_minute_bucket(
    owner, client, generous_minute_bucket
):
    """Reproduces P1-1. The only limit on step-up guesses was a fixed
    per-minute bucket (8/min/account), which resets every minute: ~11,500
    guesses a day against a 6-digit code, so an attacker holding only the
    password reached AAL2 in weeks. Consecutive failures now back off
    durably, in the database."""
    from workeasy360.platform.identity.services import MFA_FREE_FAILURES

    _factor_id, raw, _codes = _enroll(client)
    wrong = _wrong_code(raw)

    statuses = [
        _post(client, STEP_UP, {"code": wrong}).status_code
        for _ in range(MFA_FREE_FAILURES + 5)
    ]

    assert statuses[:MFA_FREE_FAILURES] == [400] * MFA_FREE_FAILURES
    assert statuses[-1] == 429
    # Locked means locked: not even the correct code is evaluated, so the
    # lock is not a guessing oracle.
    locked = _post(client, STEP_UP, {"code": _code(raw, steps_ahead=1)})
    assert (locked.status_code, locked.json()["code"]) == (429, "rate_limited")
    assert int(locked["Retry-After"]) > 0
    assert _aal(client, owner["slug"]) == 1


def test_the_step_up_lock_is_per_account_and_survives_a_new_session(
    owner, client, generous_minute_bucket, user_factory
):
    """The lock lives on the account in the database - not in a session, a
    process or the cache - so a fresh sign-in (or another instance) does not
    reset it, and another account is unaffected."""
    from django.test import Client

    from workeasy360.platform.identity.services import MFA_FREE_FAILURES

    _factor_id, raw, _codes = _enroll(client)
    for _ in range(MFA_FREE_FAILURES + 1):
        _post(client, STEP_UP, {"code": _wrong_code(raw)})

    fresh = _login(Client(), owner["user"])
    assert _post(fresh, STEP_UP, {"code": _code(raw, steps_ahead=1)}).status_code == 429

    other = _login(Client(), user_factory(password=PASSWORD))
    _factor2, raw2, _codes2 = _enroll(other)
    assert _post(other, STEP_UP, {"code": _code(raw2, steps_ahead=1)}).status_code == 200


def test_the_step_up_backoff_lapses_and_a_success_clears_it(
    owner, client, generous_minute_bucket
):
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.identity.services import MFA_FREE_FAILURES

    _factor_id, raw, _codes = _enroll(client)
    for _ in range(MFA_FREE_FAILURES + 1):
        _post(client, STEP_UP, {"code": _wrong_code(raw)})
    user = User.objects.get(pk=owner["user"].pk)
    assert user.mfa_failed_attempts == MFA_FREE_FAILURES + 1
    assert user.mfa_locked_until is not None

    # The lock lapses on its own (simulated: moved into the past).
    User.objects.filter(pk=user.pk).update(
        mfa_locked_until=user.mfa_locked_until.replace(year=2000)
    )
    assert _post(client, STEP_UP, {"code": _code(raw, steps_ahead=1)}).status_code == 200

    user.refresh_from_db()
    assert (user.mfa_failed_attempts, user.mfa_locked_until) == (0, None)
    assert _aal(client, owner["slug"]) == 2


def test_the_backoff_grows_and_is_capped():
    from datetime import timedelta

    from workeasy360.platform.identity.services import (
        MFA_FREE_FAILURES,
        MFA_MAX_LOCK,
        mfa_lock_duration,
    )

    assert mfa_lock_duration(MFA_FREE_FAILURES) is None
    first = mfa_lock_duration(MFA_FREE_FAILURES + 1)
    second = mfa_lock_duration(MFA_FREE_FAILURES + 2)
    assert first == timedelta(minutes=1)
    assert second == 2 * first
    assert mfa_lock_duration(MFA_FREE_FAILURES + 60) == MFA_MAX_LOCK


# --- Audit 50: MFA changes leave an account-security trail ------------------


def test_mfa_changes_are_recorded_against_the_account(owner, client, caplog):
    """Reproduces P2-1: enrolment, step-up and factor removal left no record
    naming the account - only aggregate counters."""
    import logging

    caplog.set_level(logging.INFO)
    factor_id, raw, _codes = _enroll(client)
    _post(client, STEP_UP, {"code": _wrong_code(raw)})
    assert _post(client, STEP_UP, {"code": _code(raw, steps_ahead=1)}).status_code == 200
    assert client.delete(f"{FACTORS}/{factor_id}").status_code == 200

    user_id = str(owner["user"].id)
    for event in (
        "identity.mfa.enrollment_started",
        "identity.mfa.factor_enrolled",
        "identity.mfa.step_up_failed",
        "identity.mfa.step_up_succeeded",
        "identity.mfa.factor_removed",
    ):
        lines = [r.getMessage() for r in caplog.records if event in r.getMessage()]
        assert lines, event
        assert any(user_id in line for line in lines), event


# --- Audit 26/33: recovery codes and secrets are not cacheable --------------


def test_mfa_responses_carrying_secrets_are_not_cacheable(owner, client):
    started = _post(client, ENROLL)
    raw = _raw_secret(started.json()["factor_id"])
    confirmed = _post(
        client, CONFIRM, {"factor_id": started.json()["factor_id"], "code": _code(raw)}
    )
    for response in (started, confirmed, client.get(FACTORS)):
        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]


# --- Audit 28: AAL2 lifetime boundaries -------------------------------------


@pytest.mark.parametrize(
    "elapsed,expected", [(14 * 60 + 59, 2), (15 * 60, 2), (15 * 60 + 1, 1)]
)
def test_aal2_lifetime_boundaries(owner, client, monkeypatch, elapsed, expected):
    """14:59 and exactly 15:00 are still AAL2; 15:01 is not. Nothing longer."""
    from workeasy360.platform.tenancy import resolution

    _factor_id, raw, _codes = _enroll(client)
    assert _post(client, STEP_UP, {"code": _code(raw, steps_ahead=1)}).status_code == 200
    stepped_up_at = client.session[resolution.SESSION_STEP_UP_AT_KEY]

    monkeypatch.setattr(resolution.time, "time", lambda: stepped_up_at + elapsed)

    assert _aal(client, owner["slug"]) == expected


def test_a_step_up_timestamp_from_the_future_is_not_trusted(owner, client, monkeypatch):
    """Clock skew between instances reads as AAL1 (fail closed), never as an
    AAL2 that lasts longer than the window."""
    from workeasy360.platform.tenancy import resolution

    _factor_id, raw, _codes = _enroll(client)
    assert _post(client, STEP_UP, {"code": _code(raw, steps_ahead=1)}).status_code == 200
    stepped_up_at = client.session[resolution.SESSION_STEP_UP_AT_KEY]
    monkeypatch.setattr(resolution.time, "time", lambda: stepped_up_at - 5)

    assert _aal(client, owner["slug"]) == 1


# --- Audit 31: one AAL source ------------------------------------------------


def test_the_oidc_code_carries_the_session_aal_not_a_default(owner, client, monkeypatch):
    """The authorization flow reads session_aal() (not a hardcoded 1 or 2):
    AAL2 inside the window, AAL1 once it lapses."""
    from workeasy360.interfaces.api.v1 import ecosystem_views
    from workeasy360.platform.tenancy import resolution

    _factor_id, raw, _codes = _enroll(client)
    assert _post(client, STEP_UP, {"code": _code(raw, steps_ahead=1)}).status_code == 200

    class _Req:
        session = client.session

    assert ecosystem_views._session_aal(_Req()) == 2
    stepped_up_at = client.session[resolution.SESSION_STEP_UP_AT_KEY]
    monkeypatch.setattr(resolution.time, "time", lambda: stepped_up_at + 15 * 60 + 1)
    assert ecosystem_views._session_aal(_Req()) == 1


# --- Audit 36: CSRF on every Phase 5 write ----------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", ORGANIZATIONS),
        ("post", ENROLL),
        ("post", CONFIRM),
        ("post", STEP_UP),
        ("delete", f"{FACTORS}/{uuid.uuid4()}"),
    ],
)
def test_phase5_writes_require_the_csrf_token(user_factory, method, path):
    from django.test import Client

    browser = Client(enforce_csrf_checks=True)
    user = user_factory(password=PASSWORD)
    browser.force_login(user)

    response = getattr(browser, method)(path, "{}", content_type="application/json")

    assert response.status_code == 403


def test_member_removal_requires_the_csrf_token(owner, user_factory):
    from django.test import Client

    browser = Client(enforce_csrf_checks=True)
    browser.force_login(owner["user"])

    response = browser.delete(f"/api/v1/orgs/{owner['slug']}/memberships/{uuid.uuid4()}")

    assert response.status_code == 403


# --- Audit 15/16: every generated slug is routable ---------------------------


def test_an_organization_named_with_an_underscore_is_usable_by_its_owner(
    client, user_factory
):
    """Reproduces P1-3. ``slugify`` keeps underscores, the tenancy router does
    not accept them: "Acme_Corp" became "acme_corp", created fine (201), and
    then every /orgs/acme_corp/... request ran with no tenant at all - 403
    or 500 - so the owner could never use the organization."""
    user = user_factory(password=PASSWORD)
    _login(client, user)

    created = _post(client, ORGANIZATIONS, {"name": "Acme_Corp"})

    assert created.status_code == 201
    slug = created.json()["slug"]
    assert "_" not in slug
    for path in ("context", "memberships", "organization"):
        assert client.get(f"/api/v1/orgs/{slug}/{path}").status_code == 200, path


@pytest.mark.parametrize(
    "name",
    [
        "Acme_Corp",
        "__init__",
        "a_b-c__d",
        "-_-",
        "Ünïcödé GmbH",
        "株式会社",
        "émoji 🚀 Co",
        "O'Reilly & Sons",
        "  Leading and trailing  ",
        "tab\tseparated\nlines",
        "API",
        "123",
        "x" * 255,
        "a" * 49 + "_" + "b" * 10,
        "a" * 49 + " -b",
    ],
)
def test_every_generated_slug_is_one_the_tenancy_router_resolves(user_factory, name):
    """The slug generator and the tenancy router must agree on the alphabet,
    for the first organization with a name and for a colliding second one
    (which takes a random suffix)."""
    from workeasy360.platform.onboarding.services import create_organization
    from workeasy360.platform.tenancy.resolution import organization_slug_from_path

    user = user_factory()
    for _ in range(2):
        slug = create_organization(user_id=user.id, name=name).slug
        assert organization_slug_from_path(f"/api/v1/orgs/{slug}/context") == slug, slug
        assert len(slug) <= 63


# --- Audit 59/61 (member removal): seats -------------------------------------


def test_removing_a_seated_member_frees_the_seat(owner, client, user_factory):
    """Reproduces P1-2. Removal left the member's seat assigned. The seat
    screen lists active members only, so the seat could never be freed from
    the UI: each removal of a seated member permanently shrank the
    organization's usable (and paid-for) seats, and the next member was
    refused with 402."""
    from uuid import UUID

    from workeasy360.commercial.provisioning.services import grant_product
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.entitlements.services import seats
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    org_id = UUID(owner["org_id"])
    ctx = TenantContext(
        organization_id=org_id,
        membership_id=UUID(owner["membership_id"]),
        user_id=owner["user"].id,
    )
    grant_product(org_id, "crm", seats=1)
    with tenant_context(ctx), tenant_db_scope(ctx):
        leaving, joining = (
            Membership(
                organization_id=org_id,
                user=user_factory(),
                status=MembershipStatus.ACTIVE.value,
            )
            for _ in range(2)
        )
        leaving.save()
        joining.save()
        seats.assign_seat(org_id, pool_key="crm", membership_id=leaving.id)

    removed = client.delete(f"/api/v1/orgs/{owner['slug']}/memberships/{leaving.id}")
    assert removed.status_code == 200

    with tenant_context(ctx), tenant_db_scope(ctx):
        assert seats.assigned_count(org_id, pool_key="crm") == 0
        seats.assign_seat(org_id, pool_key="crm", membership_id=joining.id)
        assert seats.held_by(org_id, pool_key="crm") == {joining.id}


# --- Audit 7/13: chooser cursor ----------------------------------------------


def _cursor(payload) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.parametrize(
    "payload",
    [
        {"t": "2026-01-01T00:00:00+00:00", "i": 123},
        {"t": "2026-01-01T00:00:00+00:00", "i": ["x"]},
        {"t": "2026-01-01T00:00:00+00:00", "i": {"a": 1}},
        {"t": 5, "i": str(uuid.uuid4())},
        ["t", "i"],
        "just a string",
    ],
)
def test_a_crafted_cursor_is_the_first_page_not_a_500(owner, client, payload):
    """Reproduces P3-1: a cursor whose id was a JSON number/list/object made
    ``UUID()`` raise AttributeError, which the parser did not catch - a 500
    on the chooser (and the member list) for any signed-in user."""
    cursor = _cursor(payload)

    chooser = client.get(ORGANIZATIONS, {"cursor": cursor})
    members = client.get(f"/api/v1/orgs/{owner['slug']}/memberships", {"cursor": cursor})

    assert chooser.status_code == 200
    assert [o["slug"] for o in chooser.json()["organizations"]] == [owner["slug"]]
    assert members.status_code == 200


def test_a_cursor_from_another_users_chooser_reveals_nothing(owner, client, user_factory):
    """Audit 7: a cursor only positions the caller's own keyset; it cannot
    widen the user filter."""
    from django.test import Client

    other = _login(Client(), user_factory(password=PASSWORD))
    for index in range(3):
        _post(other, ORGANIZATIONS, {"name": f"Other {index}"})
    foreign_cursor = other.get(ORGANIZATIONS, {"limit": 1}).json()["next_cursor"]
    assert foreign_cursor

    mine = client.get(ORGANIZATIONS, {"cursor": foreign_cursor, "limit": 100}).json()

    slugs = {o["slug"] for o in mine["organizations"]}
    assert slugs <= {owner["slug"]}

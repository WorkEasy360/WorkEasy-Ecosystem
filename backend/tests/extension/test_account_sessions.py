"""The session registry, the security trail, password change and session
revocation (Phase 8 extension, section 1 of docs/phase-8/extension/00-design.md)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.extension.conftest import PASSWORD, step_up

pytestmark = pytest.mark.django_db

SESSIONS = "/api/v1/auth/sessions"
PASSWORD_URL = "/api/v1/auth/password"
ACTIVITY = "/api/v1/auth/activity"


def _sessions(client):
    return client.get(SESSIONS).json()["sessions"]


def test_signing_in_registers_a_session_owned_by_the_user(user_factory, login):
    from workeasy360.platform.identity.models import UserSession

    user = user_factory(password=PASSWORD)
    client = login(user)

    row = UserSession.objects.get(session_key=client.session.session_key)
    assert row.user_id == user.id
    assert row.ip_prefix == "127.0.0.0/24"
    assert row.device_label == "Chrome on Windows"
    assert row.session_epoch == user.session_epoch

    listed = _sessions(client)
    assert len(listed) == 1 and listed[0]["current"] is True
    # The bearer secret never leaves the table.
    assert client.session.session_key not in str(client.get(SESSIONS).content)


def test_the_list_shows_only_my_own_live_sessions(user_factory, login):
    me = user_factory(password=PASSWORD)
    other = user_factory(password=PASSWORD)
    mine_a, mine_b = login(me), login(me)
    login(other)

    ids = {s["session_id"] for s in _sessions(mine_a)}
    assert len(ids) == 2
    assert {s["current"] for s in _sessions(mine_b)} == {True, False}


def test_revoking_a_session_needs_step_up_and_ends_only_that_session(user_factory, login):
    user = user_factory(password=PASSWORD)
    here, there = login(user), login(user)
    target = next(s for s in _sessions(here) if not s["current"])["session_id"]

    refused = here.post(f"{SESSIONS}/{target}/revoke")
    assert (refused.status_code, refused.json()["code"]) == (401, "step_up_required")
    assert there.get("/api/v1/auth/session").json()["authenticated"] is True

    step_up(here)
    assert here.post(f"{SESSIONS}/{target}/revoke").status_code == 200
    # The revoked browser is anonymous on its very next request.
    assert there.get("/api/v1/auth/session").json() == {"authenticated": False}
    assert here.get("/api/v1/auth/session").json()["authenticated"] is True
    kinds = [e["kind"] for e in here.get(ACTIVITY).json()["events"]]
    assert "session_revoked" in kinds


def test_nobody_can_revoke_another_users_session(user_factory, login):
    victim = user_factory(password=PASSWORD)
    attacker = user_factory(password=PASSWORD)
    victim_client = login(victim)
    victim_session = _sessions(victim_client)[0]["session_id"]
    attacker_client = login(attacker, aal2=True)

    response = attacker_client.post(f"{SESSIONS}/{victim_session}/revoke")
    assert (response.status_code, response.json()["code"]) == (404, "session_not_found")
    assert victim_client.get("/api/v1/auth/session").json()["authenticated"] is True


def test_revoke_others_keeps_the_current_session(user_factory, login):
    user = user_factory(password=PASSWORD)
    here = login(user, aal2=True)
    others = [login(user) for _ in range(3)]

    response = here.post(f"{SESSIONS}/revoke-others")
    assert response.status_code == 200 and response.json()["ended"] == 3
    assert all(
        c.get("/api/v1/auth/session").json() == {"authenticated": False} for c in others
    )
    assert [s["current"] for s in _sessions(here)] == [True]


def test_a_session_past_its_absolute_lifetime_no_longer_loads(
    user_factory, login, settings
):
    from workeasy360.platform.identity.models import UserSession

    user = user_factory(password=PASSWORD)
    client = login(user)
    UserSession.objects.filter(session_key=client.session.session_key).update(
        created_at=timezone.now()
        - timedelta(seconds=settings.SESSION_ABSOLUTE_TIMEOUT + 60)
    )
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_failed_and_successful_sign_ins_reach_the_owners_trail(
    user_factory, login, client
):
    user = user_factory(password=PASSWORD)
    client.post(
        "/api/v1/auth/login",
        {"email": user.email, "password": "wrong password here"},
        content_type="application/json",
    )
    signed_in = login(user)
    events = signed_in.get(ACTIVITY).json()["events"]
    assert [e["kind"] for e in events[:2]] == ["sign_in", "sign_in_failed"]
    assert events[1]["reason"] == "invalid_credentials"
    user.refresh_from_db()
    assert user.last_login_at is not None


def test_changing_the_password_ends_every_other_session(user_factory, login):
    user = user_factory(password=PASSWORD)
    here, elsewhere = login(user), login(user)
    new = "an entirely different passphrase 7"

    response = here.post(
        PASSWORD_URL,
        {"current_password": PASSWORD, "new_password": new},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert here.get("/api/v1/auth/session").json()["authenticated"] is True
    assert elsewhere.get("/api/v1/auth/session").json() == {"authenticated": False}
    user.refresh_from_db()
    assert user.check_password(new)


def test_a_wrong_current_password_is_refused_and_counted(user_factory, login):
    user = user_factory(password=PASSWORD)
    client = login(user)
    response = client.post(
        PASSWORD_URL,
        {
            "current_password": "not it at all 123",
            "new_password": "a brand new passphrase 9",
        },
        content_type="application/json",
    )
    assert (response.status_code, response.json()["code"]) == (
        400,
        "current_password_incorrect",
    )
    user.refresh_from_db()
    assert user.failed_login_count == 1
    assert user.check_password(PASSWORD)


def test_a_weak_new_password_lists_every_problem(user_factory, login):
    user = user_factory(password=PASSWORD)
    client = login(user)
    response = client.post(
        PASSWORD_URL,
        {"current_password": PASSWORD, "new_password": "12345"},
        content_type="application/json",
    )
    body = response.json()
    assert (response.status_code, body["code"]) == (400, "password_policy_violation")
    assert len(body["problems"]) >= 2
    assert "12345" not in response.content.decode()


def test_the_strictest_organization_minimum_applies_to_a_new_password(console_org, login):
    from workeasy360.platform.organizations.models import Organization

    org = console_org()
    Organization.objects.filter(id=org["org"].id).update(password_min_length=40)
    client = login(org["owner"])
    response = client.post(
        PASSWORD_URL,
        {
            "current_password": PASSWORD,
            "new_password": "a new passphrase of thirty chars",
        },
        content_type="application/json",
    )
    assert response.json()["code"] == "password_policy_violation"
    assert any("40" in p for p in response.json()["problems"])


def test_account_endpoints_need_a_session(client):
    for method, path in (
        ("get", SESSIONS),
        ("get", ACTIVITY),
        ("post", PASSWORD_URL),
        ("post", f"{SESSIONS}/revoke-others"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 401, path


def test_device_labels_and_prefixes_are_coarse():
    from workeasy360.platform.identity.devices import device_label, network_prefix

    assert (
        device_label(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Version/17.0 Mobile Safari/604.1"
        )
        == "Safari on iOS"
    )
    assert device_label("<script>alert(1)</script>") == "Unknown device"
    assert device_label("") == "Unknown device"
    assert network_prefix("203.0.113.77") == "203.0.113.0/24"
    assert network_prefix("2001:db8:1234:5678::1") == "2001:db8:1234::/48"
    assert network_prefix("::ffff:198.51.100.9") == "198.51.100.0/24"
    assert network_prefix("unknown") == ""

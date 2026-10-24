"""The Super Admin user directory, and cross-cutting security properties of the
Phase 8 extension (CSRF, dangerous permissions, production pins)."""

from __future__ import annotations

import pytest

from tests.extension.conftest import PASSWORD

pytestmark = pytest.mark.django_db

USERS = "/api/v1/admin/users"


def test_the_directory_shows_state_never_credentials(staff_login, user_factory, login):
    person = user_factory(
        email="person@example.com", password=PASSWORD, full_name="Pat Person"
    )
    login(person)
    viewer, _ = staff_login("platform.staff_user.view")
    listing = viewer.get(USERS, {"q": "person@"})
    rows = listing.json()["results"]
    assert [r["email"] for r in rows] == ["person@example.com"]
    assert rows[0]["last_sign_in_at"] is not None and "mfa_enrolled" not in rows[0]
    text = listing.content.decode()
    assert "argon2" not in text and "pbkdf2" not in text and "password" not in text
    detail = viewer.get(f"{USERS}/{person.id}").json()
    assert detail["sessions"] and "session_key" not in str(detail)
    assert detail["activity"][0]["kind"] == "sign_in"
    everyone = viewer.get(USERS).json()["results"]
    assert "platform-control-plane@workeasy360.invalid" not in [
        r["email"] for r in everyone
    ]


def test_account_actions_need_manage_and_a_reason(staff_login, user_factory, login):
    person = user_factory(password=PASSWORD)
    target = f"{USERS}/{person.id}/sign-out"
    viewer, _ = staff_login("platform.staff_user.view")
    assert (
        viewer.post(
            target,
            {"reason": "Suspicious activity seen"},
            content_type="application/json",
        ).status_code
        == 403
    )
    manager, _ = staff_login("platform.staff_user.manage")
    assert (
        manager.post(
            target, {"reason": "short"}, content_type="application/json"
        ).status_code
        == 400
    )


def test_signing_a_person_out_everywhere_and_disabling_them(
    staff_login, user_factory, login, client
):
    person = user_factory(password=PASSWORD)
    theirs = login(person)
    manager, me = staff_login("platform.staff_user.manage", "platform.staff_audit.view")

    out = manager.post(
        f"{USERS}/{person.id}/sign-out",
        {"reason": "Laptop reported stolen"},
        content_type="application/json",
    )
    assert out.status_code == 200
    assert theirs.get("/api/v1/auth/session").json() == {"authenticated": False}

    theirs = login(person)
    disabled = manager.post(
        f"{USERS}/{person.id}/disable",
        {"reason": "Left the company"},
        content_type="application/json",
    )
    assert disabled.json()["status"] == "disabled"
    assert theirs.get("/api/v1/auth/session").json() == {"authenticated": False}
    refused = client.post(
        "/api/v1/auth/login",
        {"email": person.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert refused.json()["code"] == "account_disabled"

    assert (
        manager.post(
            f"{USERS}/{me.id}/disable",
            {"reason": "Testing self disable"},
            content_type="application/json",
        ).status_code
        == 409
    )
    enabled = manager.post(
        f"{USERS}/{person.id}/enable",
        {"reason": "Returned to the company"},
        content_type="application/json",
    )
    assert enabled.json()["status"] == "active"
    audit = manager.get("/api/v1/admin/audit", {"action": "staff.user_"}).json()[
        "results"
    ]
    assert {e["action"] for e in audit} >= {
        "staff.user_sign_out",
        "staff.user_disable",
        "staff.user_enable",
    }
    assert all(e["reason"] for e in audit)


def test_unlock_clears_the_lockout(staff_login, user_factory):
    from datetime import timedelta

    from django.utils import timezone

    person = user_factory(password=PASSWORD)
    type(person).objects.filter(id=person.id).update(
        failed_login_count=10,
        locked_until=timezone.now() + timedelta(minutes=10),
    )
    manager, _ = staff_login("platform.staff_user.manage")
    response = manager.post(
        f"{USERS}/{person.id}/unlock",
        {"reason": "Verified the person by phone"},
        content_type="application/json",
    )
    assert response.status_code == 200 and response.json()["locked"] is False
    person.refresh_from_db()
    assert (person.failed_login_count, person.locked_until) == (0, None)


# --- cross-cutting -------------------------------------------------------------------------


def test_every_new_write_is_csrf_protected(user_factory, console_org):
    from django.test import Client

    org = console_org()
    client = Client(enforce_csrf_checks=True)
    client.force_login(org["owner"])
    for path in (
        "/api/v1/auth/password",
        "/api/v1/auth/sessions/revoke-others",
        f"/api/v1/orgs/{org['slug']}/invitations",
        f"/api/v1/orgs/{org['slug']}/members/{org['membership'].id}/suspend",
        "/api/v1/public/invitations/accept",
    ):
        response = client.post(path, {}, content_type="application/json")
        assert response.status_code == 403, path
        # Phase 12: the CSRF refusal is JSON in the API's error shape.
        assert response.json()["code"] == "csrf_failed", path


def test_the_new_permissions_are_dangerous_where_they_act():
    from workeasy360.platform.authz.registry import permission_registry

    for key in (
        "platform.member.sign_out",
        "platform.security.update",
        "platform.role.assign",
        "platform.staff_user.manage",
        "platform.staff_communication.manage",
        "platform.staff_payment.manage",
    ):
        assert permission_registry.get(key).dangerous, key
    for key in (
        "platform.staff_user.view",
        "platform.staff_communication.view",
        "platform.staff_payment.view",
    ):
        declaration = permission_registry.get(key)
        assert declaration.control_plane and not declaration.dangerous


def test_no_customer_role_carries_a_new_staff_permission(console_org, login):
    org = console_org()
    permissions = (
        login(org["owner"])
        .get(f"/api/v1/orgs/{org['slug']}/access")
        .json()["permissions"]
    )
    assert not [p for p in permissions if p.startswith("platform.staff_")]
    assert "platform.member.sign_out" in permissions


def test_production_pins_the_test_provider_off(monkeypatch):
    import importlib
    import sys

    from config.env import Env
    from tests.security.test_security_baseline import _production_env

    for key, value in _production_env(W360_PAYMENTS_FAKE_PROVIDER="true").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")
    monkeypatch.setattr(Env, "load_dotenv", lambda self, path: None)
    sys.modules.pop("config.settings.production", None)
    sys.modules.pop("config.settings.base", None)
    try:
        module = importlib.import_module("config.settings.production")
        assert module.PAYMENTS_FAKE_PROVIDER_ENABLED is False
        assert module.PAYMENT_PROVIDER_ENDPOINTS == {}
        assert module.SESSION_ENGINE == "workeasy360.platform.identity.sessions"
        assert len(module.NOTIFICATION_SEAL_KEYS) == 1
    finally:
        sys.modules.pop("config.settings.production", None)
        sys.modules.pop("config.settings.base", None)


def test_production_refuses_a_missing_or_reused_seal_key(monkeypatch):
    import importlib
    import sys

    from config.env import Env, ImproperlyConfigured
    from tests.security.test_security_baseline import _production_env

    monkeypatch.setattr(Env, "load_dotenv", lambda self, path: None)
    for overrides in (
        {"W360_NOTIFICATION_SEAL_KEYS": ""},
        {"W360_NOTIFICATION_SEAL_KEYS": "s" * 60},
    ):
        for key in list(__import__("os").environ):
            if key.startswith("W360_"):
                monkeypatch.delenv(key, raising=False)
        for key, value in _production_env(**overrides).items():
            if value:
                monkeypatch.setenv(key, value)
        monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")
        sys.modules.pop("config.settings.production", None)
        sys.modules.pop("config.settings.base", None)
        with pytest.raises(ImproperlyConfigured):
            importlib.import_module("config.settings.production")
    sys.modules.pop("config.settings.production", None)
    sys.modules.pop("config.settings.base", None)

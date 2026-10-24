"""Identity hardening found by the Phase 12 audit (G-15, G-16, G-20)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from workeasy360.platform.identity.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "Phase-12-Correct-Horse-7"
LOGIN = "/api/v1/auth/login"
SESSION = "/api/v1/auth/session"


def _signed_in(user_factory):
    user = user_factory(password=PASSWORD)
    browser = Client()
    browser.get(SESSION)
    response = browser.post(
        LOGIN,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return user, browser


def test_ten_wrong_passwords_by_someone_else_do_not_sign_the_owner_out(user_factory):
    """G-16: the lockout stops password guessing; it no longer ends the
    owner's live sessions."""
    user, owner_browser = _signed_in(user_factory)
    attacker = Client()
    for _ in range(12):
        attacker.post(
            LOGIN,
            {"email": user.email, "password": "wrong-guess-123456"},
            content_type="application/json",
        )
    user.refresh_from_db()
    assert user.is_locked_out
    assert owner_browser.get(SESSION).json()["authenticated"] is True
    # ...and the lockout still refuses the right password on a new sign-in.
    refused = Client().post(
        LOGIN,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert refused.status_code in (401, 423, 429)
    assert "account_locked" in refused.content.decode() or refused.status_code == 429


def test_an_administrators_lock_still_ends_every_session(user_factory):
    from workeasy360.contracts.enums import UserStatus

    user, browser = _signed_in(user_factory)
    User.objects.filter(id=user.id).update(status=UserStatus.LOCKED.value)
    assert browser.get(SESSION).json()["authenticated"] is False


def test_the_lockout_still_expires_by_itself(user_factory):
    user = user_factory(password=PASSWORD)
    User.objects.filter(id=user.id).update(
        locked_until=timezone.now() - timedelta(minutes=1)
    )
    ok = Client().post(
        LOGIN,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert ok.status_code == 200

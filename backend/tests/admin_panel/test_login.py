"""The Admin sign-in: "Remember me", brute-force limits and route protection."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import Client

from tests.extension.conftest import PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login"


def _sign_in(user, **extra):
    client = Client(HTTP_USER_AGENT="Mozilla/5.0 Chrome/126.0")
    client.get("/api/v1/auth/session")
    response = client.post(
        LOGIN,
        {"email": user.email, "password": PASSWORD, **extra},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return client, response


def _session_cookie(response):
    return response.cookies[settings.SESSION_COOKIE_NAME]


@pytest.fixture
def staff_user(user_factory):
    user = user_factory(password=PASSWORD)
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    return user


def _row(client):
    from workeasy360.platform.identity.models import UserSession

    return UserSession.objects.get(session_key=client.session.session_key)


def test_left_unticked_the_cookie_ends_with_the_browser_and_the_idle_window_is_normal(
    staff_user,
):
    client, response = _sign_in(staff_user, remember=False)
    cookie = _session_cookie(response)
    assert not cookie["max-age"] and not cookie["expires"]
    row = _row(client)
    remaining = (row.expire_date - row.last_activity_at).total_seconds()
    assert abs(remaining - settings.SESSION_COOKIE_AGE) < 60


def test_ticked_the_cookie_persists_for_the_remember_window(staff_user):
    client, response = _sign_in(staff_user, remember=True)
    cookie = _session_cookie(response)
    assert int(cookie["max-age"]) == settings.SESSION_REMEMBER_ME_AGE
    row = _row(client)
    remaining = (row.expire_date - row.last_activity_at).total_seconds()
    assert abs(remaining - settings.SESSION_REMEMBER_ME_AGE) < 60


def test_the_window_never_exceeds_the_absolute_lifetime():
    assert settings.SESSION_REMEMBER_ME_AGE <= settings.SESSION_ABSOLUTE_TIMEOUT


def test_a_remembered_session_still_ends_at_the_absolute_lifetime(staff_user):
    from datetime import timedelta

    from django.utils import timezone

    from workeasy360.platform.identity.models import UserSession

    client, _ = _sign_in(staff_user, remember=True)
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True
    UserSession.objects.filter(user_id=staff_user.id).update(
        created_at=timezone.now()
        - timedelta(seconds=settings.SESSION_ABSOLUTE_TIMEOUT + 1)
    )
    assert client.get("/api/v1/auth/session").json()["authenticated"] is False


def test_other_sign_ins_are_unchanged(user_factory, staff_user):
    customer = user_factory(password=PASSWORD)
    for user, extra in ((customer, {"remember": True}), (customer, {}), (staff_user, {})):
        _, response = _sign_in(user, **extra)
        assert int(_session_cookie(response)["max-age"]) == settings.SESSION_COOKIE_AGE


def test_a_non_boolean_remember_changes_nothing(staff_user):
    _, response = _sign_in(staff_user, remember="yes")
    assert int(_session_cookie(response)["max-age"]) == settings.SESSION_COOKIE_AGE


def test_wrong_passwords_lock_the_account_and_say_nothing_more(staff_user):
    client = Client()
    client.get("/api/v1/auth/session")
    codes = [
        client.post(
            LOGIN,
            {"email": staff_user.email, "password": "wrong password here"},
            content_type="application/json",
        ).json()["code"]
        for _ in range(11)
    ]
    assert codes[0] == "invalid_credentials"
    assert codes[-1] == "account_locked"
    unknown = client.post(
        LOGIN,
        {"email": "nobody-here@example.com", "password": "wrong password here"},
        content_type="application/json",
    )
    assert unknown.status_code == 401 and unknown.json()["code"] == "invalid_credentials"


def test_the_console_refuses_the_signed_out_and_the_non_staff(user_factory, staff_user):
    assert Client().get("/api/v1/admin/session").status_code in (401, 403)
    customer_client, _ = _sign_in(user_factory(password=PASSWORD))
    assert customer_client.get("/api/v1/admin/session").status_code == 403


def test_signing_out_ends_the_session(staff_user):
    client, _ = _sign_in(staff_user, remember=True)
    csrf = client.cookies[settings.CSRF_COOKIE_NAME].value
    assert client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=csrf).status_code == 200
    assert client.get("/api/v1/auth/session").json()["authenticated"] is False

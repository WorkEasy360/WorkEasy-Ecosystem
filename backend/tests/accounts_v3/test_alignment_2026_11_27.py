"""The 2026-11-27 Accounts V2/V3 alignment audit: the verified gaps it fixed, and
the behaviours it found implemented but untested.

- An email change needs the two-step code when two-step is on (V3 8.2, "reauthenticate"),
  and a wrong one spends the account changes' day budget, as closure does.
- A signed-in password change tells the verified address (V3 8.2, "notify the existing
  verified contact after sensitive changes").
- A reset link expires (V3 8.3, "expiring single-use recovery").
- Product sign-in setup only accepts addresses on the application's own origin -
  scheme, host and port (V3 9.1, "approved redirect destinations").
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from tests.accounts_v3.conftest import PASSWORD, delivered, next_code, post

pytestmark = pytest.mark.django_db

EMAIL = "/api/v1/auth/email"
PASSWORD_URL = "/api/v1/auth/password"
FORGOT = "/api/v1/public/password/forgot"
RESET = "/api/v1/public/password/reset"
NEW_PASSWORD = "an entirely different passphrase 2026"  # pragma: allowlist secret


@pytest.fixture
def member(with_password, user_factory):
    return with_password(user_factory(email="align.member@example.com", full_name="Asha Rao"))


def _signed_in(user) -> Client:
    browser = Client()
    browser.force_login(user)
    browser.get("/api/v1/auth/session")
    return browser


def _pending_changes(user) -> int:
    from workeasy360.platform.identity.models import AccountToken

    return AccountToken.objects.filter(
        user=user, purpose=AccountToken.Purpose.CHANGE_EMAIL
    ).count()


# --- email change behind the two-step code -------------------------------------------------


def test_without_two_step_an_email_change_still_needs_only_the_password(member):
    browser = _signed_in(member)
    asked = post(browser, EMAIL, {"new_email": "asha.new@example.com", "password": PASSWORD})
    assert asked.status_code == 202, asked.content
    assert _pending_changes(member) == 1


def test_with_two_step_on_an_email_change_needs_a_current_code(member, enroll):
    raw, codes = enroll(member)
    browser = _signed_in(member)
    body = {"new_email": "asha.new@example.com", "password": PASSWORD}

    no_code = post(browser, EMAIL, body)
    assert no_code.status_code == 400
    assert no_code.json()["code"] == "two_step_code_invalid"
    wrong = post(browser, EMAIL, {**body, "code": "000000"})
    assert wrong.json()["code"] == "two_step_code_invalid"
    # A refused request leaves nothing pending and mails nobody a link.
    assert _pending_changes(member) == 0
    assert not [m for m in delivered() if m.to == ["asha.new@example.com"]]

    asked = post(browser, EMAIL, {**body, "code": next_code(raw)})
    assert asked.status_code == 202, asked.content
    assert _pending_changes(member) == 1
    member.refresh_from_db()
    assert member.email == "align.member@example.com"  # unchanged until the link is used


def test_a_recovery_code_also_works_for_an_email_change_and_only_once(member, enroll):
    _, codes = enroll(member)
    browser = _signed_in(member)
    body = {"new_email": "asha.new@example.com", "password": PASSWORD, "code": codes[0]}
    assert post(browser, EMAIL, body).status_code == 202
    again = post(browser, EMAIL, {**body, "new_email": "asha.other@example.com"})
    assert again.json()["code"] == "two_step_code_invalid"


def test_the_password_is_still_checked_first_and_counted(member, enroll):
    raw, _ = enroll(member)
    browser = _signed_in(member)
    wrong = post(
        browser,
        EMAIL,
        {"new_email": "asha.new@example.com", "password": "not it", "code": next_code(raw)},
    )
    assert wrong.json()["code"] == "current_password_incorrect"
    member.refresh_from_db()
    assert member.failed_login_count == 1


def test_wrong_email_change_codes_spend_the_account_changes_day_budget(
    member, enroll, settings
):
    limits = dict(settings.CONTROL_PLANE_RATE_LIMITS)
    limits["two_step_daily_user"] = (3, 86400)
    limits["two_step_user"] = (100, 300)
    limits["email_change_user"] = (100, 300)
    limits["email_change_account"] = (100, 300)
    settings.CONTROL_PLANE_RATE_LIMITS = limits
    browser = Client()
    browser.force_login(member)
    raw, _ = enroll(member, browser)
    body = {"new_email": "asha.new@example.com", "password": PASSWORD, "code": "000000"}
    answers = [post(browser, EMAIL, body).status_code for _ in range(4)]
    assert answers == [400, 400, 400, 429]
    # The budget is the account's, so even the right code waits for tomorrow.
    right = post(browser, EMAIL, {**body, "code": next_code(raw)})
    assert right.status_code == 429


# --- the password change notice ---------------------------------------------------------------


def test_a_signed_in_password_change_tells_the_verified_address(member):
    browser = _signed_in(member)
    delivered()  # nothing earlier counts
    changed = post(
        browser,
        PASSWORD_URL,
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200, changed.content
    notices = [m for m in delivered() if m.subject == "Your WorkEasy360 password was changed"]
    assert [m.to for m in notices] == [["align.member@example.com"]]
    assert "signed out" in notices[0].body
    assert NEW_PASSWORD not in notices[0].body and PASSWORD not in notices[0].body


def test_a_refused_password_change_sends_no_notice(member):
    browser = _signed_in(member)
    delivered()
    refused = post(
        browser, PASSWORD_URL, {"current_password": "not it", "new_password": NEW_PASSWORD}
    )
    assert refused.status_code == 400
    assert not [m for m in delivered() if "password was changed" in m.subject]


# --- the reset link expires ---------------------------------------------------------------


def test_an_expired_reset_link_is_refused_and_the_password_stays(member):
    from tests.phase9.conftest import token_from
    from workeasy360.platform.identity.models import AccountToken

    delivered()
    assert post(Client(), FORGOT, {"email": member.email}).status_code == 202
    token = token_from(next(m for m in delivered() if "Reset your" in m.subject))
    AccountToken.objects.filter(user=member).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    refused = post(Client(), RESET, {"token": token, "password": NEW_PASSWORD})
    assert refused.status_code == 404
    assert refused.json()["code"] == "link_invalid"
    member.refresh_from_db()
    assert member.check_password(PASSWORD)


# --- sign-in setup addresses: the application's own origin ----------------------------------


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://crm.example.com/auth/callback", True),
        ("https://crm.example.com:443/auth/callback", True),
        ("https://crm.example.com:8443/auth/callback", False),
        ("http://crm.example.com/auth/callback", False),
        ("https://evil.example.com/auth/callback", False),
        ("https://crm.example.com:99999/auth/callback", False),
    ],
)
def test_setup_addresses_must_be_on_the_applications_scheme_host_and_port(url, ok):
    from workeasy360.commercial.controlplane import sso_setup

    problem = sso_setup._address_problem(
        url, host="crm.example.com", scheme="https", port=443
    )
    assert (problem == "") is ok, problem


def test_an_application_on_its_own_port_keeps_its_callbacks_on_that_port():
    from workeasy360.commercial.controlplane import sso_setup

    assert (
        sso_setup._address_problem(
            "http://127.0.0.1:8094/cb", host="127.0.0.1", scheme="http", port=8094
        )
        == ""
    )
    assert "port" in sso_setup._address_problem(
        "http://127.0.0.1:8095/cb", host="127.0.0.1", scheme="http", port=8094
    )

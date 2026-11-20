"""Personal two-step verification (ADR-075, Accounts V3 04 2.3 and 08.3):
setup verified before activation with recovery codes, the code step of every
password sign-in, replacement and turning off behind reauthentication, replay
and attempt limits, and the session revocation that follows a change."""

from __future__ import annotations

import time

import pytest
from django.core import mail
from django.test import Client

from tests.accounts_v3.conftest import (
    LOGIN,
    LOGIN_CODE,
    PASSWORD,
    TWO_STEP,
    delivered,
    next_code,
    post,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def member(with_password, user_factory):
    return with_password(user_factory(email="two.step@example.com", full_name="Asha Rao"))


def _password_step(client: Client, user, **extra):
    client.get("/api/v1/auth/session")
    return post(client, LOGIN, {"email": user.email, "password": PASSWORD, **extra})


# --- the primitives ------------------------------------------------------------------


def test_totp_matches_the_rfc_6238_vectors():
    """RFC 6238 Appendix B, SHA-1, truncated to six digits."""
    from workeasy360.platform.identity import two_step

    secret = b"12345678901234567890"
    for at, expected in (
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
    ):
        assert two_step.totp_now(secret, at=at) == expected
        assert two_step.match_step(secret, expected, at=at) == at // 30


def test_secrets_and_codes_are_never_stored_in_clear(member, enroll):
    from workeasy360.platform.identity.models import TwoStepMethod, TwoStepRecoveryCode

    raw, codes = enroll(member)
    method = TwoStepMethod.objects.get(user=member, confirmed_at__isnull=False)
    assert raw not in method.secret_ciphertext.encode()
    stored = set(
        TwoStepRecoveryCode.objects.filter(user=member).values_list(
            "code_hash", flat=True
        )
    )
    assert len(stored) == 10 and not stored & set(codes)


# --- setup -----------------------------------------------------------------------------


def test_setup_needs_the_password_and_is_off_until_a_code_confirms_it(member):
    from workeasy360.platform.identity import two_step

    browser = Client()
    browser.force_login(member)
    wrong = post(browser, f"{TWO_STEP}/setup", {"password": "not it"})
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "current_password_incorrect"

    started = post(browser, f"{TWO_STEP}/setup", {"password": PASSWORD})
    assert started.status_code == 200
    body = started.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/WorkEasy360%3A")
    assert "issuer=WorkEasy360" in body["otpauth_uri"]
    assert body["qr_svg_data_uri"].startswith("data:image/svg+xml;base64,")
    # Not on yet: a sign-in still needs only the password.
    assert browser.get(TWO_STEP).json()["enabled"] is False
    assert two_step.is_enabled(member.pk) is False

    bad = post(browser, f"{TWO_STEP}/setup/confirm", {"code": "000000"})
    assert bad.status_code == 400 and bad.json()["code"] == "two_step_code_invalid"
    assert browser.get(TWO_STEP).json()["enabled"] is False


def test_confirming_turns_it_on_shows_ten_codes_once_and_ends_other_sessions(
    member, enroll
):
    from workeasy360.platform.identity.models import AccountEvent, UserSession

    elsewhere = Client()
    elsewhere.force_login(member)
    here = Client()
    here.force_login(member)
    mail.outbox.clear()
    _raw, codes = enroll(member, here)

    assert len(codes) == 10 and len(set(codes)) == 10
    status = here.get(TWO_STEP).json()
    assert status["enabled"] is True and status["recovery_codes_remaining"] == 10
    # The other browser is signed out; this one is kept.
    assert elsewhere.get("/api/v1/auth/session").json().get("authenticated") is not True
    assert here.get("/api/v1/auth/session").json().get("authenticated") is True
    assert UserSession.objects.filter(user=member).count() == 1
    kinds = set(AccountEvent.objects.filter(user=member).values_list("kind", flat=True))
    assert {"two_step_enabled", "other_sessions_revoked"} <= kinds
    assert any("Two-step verification was turned on" in m.body for m in delivered())
    # The codes are in that one answer only.
    assert not any(code in str(here.get(TWO_STEP).content) for code in codes)


def test_without_a_key_setup_is_unavailable(member, settings):
    settings.TWO_STEP_KEY = b""
    browser = Client()
    browser.force_login(member)
    assert browser.get(TWO_STEP).json()["available"] is False
    refused = post(browser, f"{TWO_STEP}/setup", {"password": PASSWORD})
    assert refused.status_code == 503 and refused.json()["code"] == "two_step_unavailable"


# --- the code step of a sign-in ---------------------------------------------------------


def test_a_password_alone_no_longer_signs_in(member, enroll):
    enroll(member)
    browser = Client()
    answer = _password_step(browser, member)
    assert answer.status_code == 401
    body = answer.json()
    assert body["code"] == "two_step_required" and len(body["challenge"]) > 30
    # No session yet.
    assert browser.get("/api/v1/auth/session").json().get("authenticated") is not True


def test_the_code_completes_the_sign_in_and_the_same_code_never_works_twice(
    member, enroll
):
    raw, _codes = enroll(member)
    code = next_code(raw)
    first = Client()
    challenge = _password_step(first, member).json()["challenge"]
    done = post(first, LOGIN_CODE, {"challenge": challenge, "code": code})
    assert done.status_code == 200, done.content
    assert done.json()["user_id"] == str(member.pk)
    assert first.get("/api/v1/auth/session").json().get("authenticated") is True

    # Replay: the same code (same time step) on a second browser.
    second = Client()
    challenge = _password_step(second, member).json()["challenge"]
    replay = post(second, LOGIN_CODE, {"challenge": challenge, "code": code})
    assert replay.status_code == 400 and replay.json()["code"] == "two_step_code_invalid"
    # And the spent challenge of the first browser cannot be used again.
    again = post(first, LOGIN_CODE, {"challenge": challenge, "code": code})
    assert again.status_code in (400, 401)


def test_five_wrong_codes_end_the_challenge(member, enroll):
    raw, _codes = enroll(member)
    browser = Client()
    challenge = _password_step(browser, member).json()["challenge"]
    for _ in range(5):
        wrong = post(browser, LOGIN_CODE, {"challenge": challenge, "code": "123456"})
        assert wrong.status_code == 400
    late = post(browser, LOGIN_CODE, {"challenge": challenge, "code": next_code(raw)})
    assert late.status_code == 401 and late.json()["code"] == "two_step_challenge_expired"
    assert browser.get("/api/v1/auth/session").json().get("authenticated") is not True


def test_an_expired_challenge_is_refused(member, enroll):
    from datetime import timedelta

    from django.utils import timezone

    from workeasy360.platform.identity.models import TwoStepChallenge

    raw, _codes = enroll(member)
    browser = Client()
    challenge = _password_step(browser, member).json()["challenge"]
    TwoStepChallenge.objects.filter(user=member).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    late = post(browser, LOGIN_CODE, {"challenge": challenge, "code": next_code(raw)})
    assert late.status_code == 401 and late.json()["code"] == "two_step_challenge_expired"


def test_signing_out_everywhere_voids_a_challenge_in_flight(member, enroll):
    from workeasy360.platform.identity import account

    raw, _codes = enroll(member)
    browser = Client()
    challenge = _password_step(browser, member).json()["challenge"]
    account.sign_out_everywhere(member.pk)
    late = post(browser, LOGIN_CODE, {"challenge": challenge, "code": next_code(raw)})
    assert late.status_code == 401


def test_a_recovery_code_signs_in_once_and_is_reported(member, enroll):
    from workeasy360.platform.identity.models import AccountEvent

    _raw, codes = enroll(member)
    mail.outbox.clear()
    browser = Client()
    challenge = _password_step(browser, member).json()["challenge"]
    done = post(browser, LOGIN_CODE, {"challenge": challenge, "code": codes[0].upper()})
    assert done.status_code == 200
    assert AccountEvent.objects.filter(user=member, kind="recovery_code_used").exists()
    assert any("A recovery code was used to sign in" in m.body for m in delivered())

    other = Client()
    challenge = _password_step(other, member).json()["challenge"]
    reused = post(other, LOGIN_CODE, {"challenge": challenge, "code": codes[0]})
    assert reused.status_code == 400


def test_remember_me_from_the_password_step_is_kept(member, enroll):
    raw, _codes = enroll(member)
    browser = Client()
    challenge = _password_step(browser, member, remember=False).json()["challenge"]
    post(browser, LOGIN_CODE, {"challenge": challenge, "code": next_code(raw)})
    assert browser.session.get_expire_at_browser_close() is True


def test_the_code_step_is_rate_limited(member, enroll, settings):
    limits = dict(settings.CONTROL_PLANE_RATE_LIMITS)
    limits["two_step_login_user"] = (2, 300)
    settings.CONTROL_PLANE_RATE_LIMITS = limits
    enroll(member)
    browser = Client()
    challenge = _password_step(browser, member).json()["challenge"]
    answers = [
        post(browser, LOGIN_CODE, {"challenge": challenge, "code": "111111"}).status_code
        for _ in range(3)
    ]
    assert answers[-1] == 429


def test_a_missing_key_fails_closed_for_an_account_that_has_two_step(
    member, enroll, settings
):
    enroll(member)
    settings.TWO_STEP_KEY = b""
    answer = _password_step(Client(), member)
    assert answer.status_code == 503
    assert answer.json()["code"] == "two_step_unavailable"


# --- replacing, renewing, turning off -------------------------------------------------------


def test_replacing_needs_a_current_code_and_the_old_app_stops_working(member, enroll):
    import base64

    from workeasy360.platform.identity import two_step

    browser = Client()
    browser.force_login(member)
    old_raw, _codes = enroll(member, browser)
    refused = post(browser, f"{TWO_STEP}/setup", {"password": PASSWORD})
    assert (
        refused.status_code == 400 and refused.json()["code"] == "two_step_code_invalid"
    )

    started = post(
        browser, f"{TWO_STEP}/setup", {"password": PASSWORD, "code": next_code(old_raw)}
    )
    assert started.status_code == 200
    secret = started.json()["secret"]
    new_raw = base64.b32decode(secret + "=" * (-len(secret) % 8))
    done = post(
        browser, f"{TWO_STEP}/setup/confirm", {"code": two_step.totp_now(new_raw)}
    )
    assert done.status_code == 200 and done.json()["replaced"] is True

    signing_in = Client()
    challenge = _password_step(signing_in, member).json()["challenge"]
    later = time.time() + 2 * two_step.STEP_SECONDS
    stale = post(
        signing_in,
        LOGIN_CODE,
        {"challenge": challenge, "code": two_step.totp_now(old_raw, at=later)},
    )
    assert stale.status_code == 400


def test_new_recovery_codes_replace_the_old_ones(member, enroll):
    browser = Client()
    browser.force_login(member)
    raw, old = enroll(member, browser)
    renewed = post(
        browser,
        f"{TWO_STEP}/recovery-codes",
        {"password": PASSWORD, "code": next_code(raw)},
    )
    assert renewed.status_code == 200
    new = renewed.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)
    signing_in = Client()
    challenge = _password_step(signing_in, member).json()["challenge"]
    assert (
        post(signing_in, LOGIN_CODE, {"challenge": challenge, "code": old[1]}).status_code
        == 400
    )


def test_turning_off_needs_password_and_code_and_leaves_the_password_route(
    member, enroll
):
    browser = Client()
    browser.force_login(member)
    raw, codes = enroll(member, browser)
    no_code = post(browser, f"{TWO_STEP}/disable", {"password": PASSWORD})
    assert no_code.status_code == 400
    wrong_password = post(
        browser, f"{TWO_STEP}/disable", {"password": "nope", "code": codes[2]}
    )
    assert wrong_password.status_code == 400
    off = post(browser, f"{TWO_STEP}/disable", {"password": PASSWORD, "code": codes[2]})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert _password_step(Client(), member).status_code == 200
    # Turning off when off is a conflict, not a silent success.
    again = post(
        browser, f"{TWO_STEP}/disable", {"password": PASSWORD, "code": next_code(raw)}
    )
    assert again.status_code == 409


def test_two_step_writes_need_a_session_and_csrf(user_factory):
    """No automation, partner key or product credential reaches them: a
    session is required, and CSRF is enforced (Accounts V3 10.3)."""
    anonymous = Client()
    for path in ("setup", "setup/confirm", "recovery-codes", "disable"):
        assert post(anonymous, f"{TWO_STEP}/{path}", {}).status_code in (401, 403)
    strict = Client(enforce_csrf_checks=True)
    user = user_factory(email="csrf.two.step@example.com")
    strict.force_login(user)
    assert post(strict, f"{TWO_STEP}/setup", {"password": PASSWORD}).status_code == 403


def test_no_secret_reaches_the_logs(member, enroll, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    raw, codes = enroll(member)
    text = caplog.text
    assert raw.hex() not in text
    assert not any(code in text for code in codes)

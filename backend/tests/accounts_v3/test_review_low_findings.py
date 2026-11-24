"""Regression tests for the low findings of the independent review of ADR-075
(L-1, L-2, L-3). L-4 and L-5 are pinned in
tests/universal_sso/test_v3_own_organization_and_revocation.py; L-6 in the
frontend's self-service tests."""

from __future__ import annotations

import pytest
from django.test import Client

from tests.accounts_v3.conftest import (
    ACCOUNT_API,
    LOGIN,
    LOGIN_CODE,
    PASSWORD,
    delivered,
    post,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def member(with_password, user_factory):
    return with_password(
        user_factory(email="low.findings@example.com", full_name="Meera Iyer")
    )


def _limits(settings, **values):
    limits = dict(settings.CONTROL_PLANE_RATE_LIMITS)
    limits.update(values)
    settings.CONTROL_PLANE_RATE_LIMITS = limits


# --- L-1: closure's code check shares the account changes' budget and notice -----------------


def test_wrong_closure_codes_spend_the_day_budget(
    member, enroll, settings, accounts_on, on_accounts
):
    _limits(
        settings,
        two_step_daily_user=(2, 86400),
        two_step_user=(100, 300),
        account_closure_user=(100, 300),
    )
    enroll(member)
    accounts_on()
    here = on_accounts(member)
    answers = [
        here.post(
            f"{ACCOUNT_API}/closure",
            {"password": PASSWORD, "code": "000000", "confirmation": "CLOSE"},
        ).status_code
        for _ in range(3)
    ]
    assert answers == [400, 400, 429]
    member.refresh_from_db()
    assert member.status == "active"


def test_wrong_closure_codes_are_told_to_the_person(
    member, enroll, settings, accounts_on, on_accounts
):
    _limits(settings, account_closure_user=(100, 300))
    enroll(member)
    delivered()  # the "turned on" notice
    accounts_on()
    here = on_accounts(member)
    for _ in range(5):
        refused = here.post(
            f"{ACCOUNT_API}/closure",
            {"password": PASSWORD, "code": "000000", "confirmation": "CLOSE"},
        )
        assert refused.json()["code"] == "two_step_code_invalid"
    assert any("Several wrong two-step codes" in m.body for m in delivered())


# --- L-2: the day budget at sign-in counts wrong codes, never right ones ----------------------


def _password_step(client, user):
    client.get("/api/v1/auth/session")
    return post(client, LOGIN, {"email": user.email, "password": PASSWORD})


def test_signing_in_often_with_the_right_code_is_never_locked_out(
    member, enroll, settings
):
    _limits(
        settings,
        two_step_login_daily_user=(2, 86400),
        two_step_login_user=(100, 300),
    )
    _raw, recovery = enroll(member)
    for code in recovery[:4]:  # four right codes against a budget of two
        browser = Client()
        challenge = _password_step(browser, member).json()["challenge"]
        signed = post(browser, LOGIN_CODE, {"challenge": challenge, "code": code})
        assert signed.status_code == 200, signed.content


def test_wrong_sign_in_codes_still_meet_the_day_budget(member, enroll, settings):
    _limits(
        settings,
        two_step_login_daily_user=(2, 86400),
        two_step_login_user=(100, 300),
    )
    _raw, recovery = enroll(member)
    browser = Client()
    challenge = _password_step(browser, member).json()["challenge"]
    answers = [
        post(browser, LOGIN_CODE, {"challenge": challenge, "code": "111111"}).status_code
        for _ in range(2)
    ]
    assert answers == [400, 400]
    # The budget is spent: even the right code waits for tomorrow.
    browser = Client()
    challenge = _password_step(browser, member).json()["challenge"]
    late = post(browser, LOGIN_CODE, {"challenge": challenge, "code": recovery[0]})
    assert late.status_code == 429


# --- L-3: recovery codes are unique per person, not across everyone -----------------------------


def test_two_people_may_hold_the_same_recovery_code(user_factory, monkeypatch):
    from workeasy360.platform.identity import two_step
    from workeasy360.platform.identity.models import TwoStepRecoveryCode

    same = [f"aaaa{n}-bbbb{n}" for n in range(two_step.RECOVERY_CODE_COUNT)]
    monkeypatch.setattr(two_step, "generate_recovery_codes", lambda: list(same))
    first, second = user_factory(), user_factory()
    assert two_step._replace_recovery_codes(first.pk) == same
    assert two_step._replace_recovery_codes(second.pk) == same
    digest = two_step.hash_recovery_code(same[0])
    holders = set(
        TwoStepRecoveryCode.objects.filter(code_hash=digest).values_list(
            "user_id", flat=True
        )
    )
    assert holders == {first.pk, second.pk}


def test_one_persons_recovery_codes_are_distinct(monkeypatch):
    from workeasy360.platform.identity import two_step

    draws = iter(
        ["aaaaabbbbb", "aaaaabbbbb", "aaaaabbbbb"] + [f"{n:010x}" for n in range(20)]
    )
    monkeypatch.setattr(two_step.secrets, "token_hex", lambda _n: next(draws))
    codes = two_step.generate_recovery_codes()
    assert len(codes) == two_step.RECOVERY_CODE_COUNT
    assert len(set(codes)) == len(codes)

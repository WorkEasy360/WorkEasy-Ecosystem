"""Regression tests for the independent security review of ADR-075 (M1, M2,
L1, L2, L4): each pins the defect the review found."""

from __future__ import annotations

import struct
import zlib

import pytest
from django.test import Client

from tests.accounts_v3.conftest import (
    ACCOUNT_API,
    LOGIN,
    LOGIN_CODE,
    PASSWORD,
    TWO_STEP,
    add_member,
    delivered,
    post,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def member(with_password, user_factory):
    return with_password(
        user_factory(email="review.fix@example.com", full_name="Asha Rao")
    )


# --- M1: a wrong password on a security action counts toward the lockout ----------------


@pytest.mark.parametrize("path", ["setup", "recovery-codes", "disable"])
def test_a_wrong_password_on_a_two_step_action_is_counted(member, enroll, path):
    browser = Client()
    browser.force_login(member)
    if path != "setup":
        enroll(member, browser)
    member.refresh_from_db()
    before = member.failed_login_count
    refused = post(
        browser,
        f"{TWO_STEP}/{path}",
        {"password": "not it", "code": "000000"},  # pragma: allowlist secret
    )
    assert (
        refused.status_code == 400
        and refused.json()["code"] == "current_password_incorrect"
    )
    member.refresh_from_db()
    assert member.failed_login_count == before + 1


def test_a_wrong_password_on_closure_is_counted(person, accounts_on, on_accounts):
    accounts_on()
    user = person["user"]
    before = user.failed_login_count
    on_accounts(user).post(
        f"{ACCOUNT_API}/closure",
        {"password": "not it", "confirmation": "CLOSE"},  # pragma: allowlist secret
    )
    user.refresh_from_db()
    assert user.failed_login_count == before + 1


# --- M2: a closed co-owner does not count as an owner --------------------------------------


def test_closing_is_refused_when_the_only_other_owner_is_disabled(person, user_factory):
    from workeasy360.contracts.enums import UserStatus
    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.selectors import sole_owned_organizations

    co_owner = user_factory(email="co.owner@example.com")
    add_member(person["organization_id"], co_owner)
    UserOrganizationAccess.objects.filter(user=co_owner).update(is_owner=True)
    assert sole_owned_organizations(person["user"].pk) == []

    lifecycle.close(co_owner)
    assert co_owner.status == UserStatus.DISABLED.value
    blocking = sole_owned_organizations(person["user"].pk)
    assert [b["organization_id"] for b in blocking] == [str(person["organization_id"])]


# --- L1: wrong sign-in codes are recorded and limited per day ----------------------------------


def _password_step(client, user):
    client.get("/api/v1/auth/session")
    return post(client, LOGIN, {"email": user.email, "password": PASSWORD})


def test_a_wrong_sign_in_code_is_on_the_persons_trail(member, enroll):
    from workeasy360.platform.identity.models import AccountEvent

    enroll(member)
    browser = Client()
    challenge = _password_step(browser, member).json()["challenge"]
    post(browser, LOGIN_CODE, {"challenge": challenge, "code": "123456"})
    assert AccountEvent.objects.filter(
        user=member, kind="sign_in_failed", reason="two_step_code_invalid"
    ).exists()


def test_wrong_codes_have_a_daily_budget_per_person(member, enroll, settings):
    limits = dict(settings.CONTROL_PLANE_RATE_LIMITS)
    limits["two_step_login_daily_user"] = (3, 86400)
    limits["two_step_login_user"] = (100, 300)
    settings.CONTROL_PLANE_RATE_LIMITS = limits
    enroll(member)
    answers = []
    for _ in range(2):
        browser = Client()
        challenge = _password_step(browser, member).json()["challenge"]
        for _ in range(2):
            answers.append(
                post(
                    browser, LOGIN_CODE, {"challenge": challenge, "code": "111111"}
                ).status_code
            )
    assert answers[-1] == 429


# --- Second review (20 November), L1: code checks on account changes ------------------------


def test_account_two_step_changes_have_a_daily_budget_per_person(
    member, enroll, settings
):
    limits = dict(settings.CONTROL_PLANE_RATE_LIMITS)
    limits["two_step_daily_user"] = (4, 86400)
    limits["two_step_user"] = (100, 300)
    settings.CONTROL_PLANE_RATE_LIMITS = limits
    browser = Client()
    browser.force_login(member)
    # Setup and confirm succeed: the day's budget counts wrong codes only
    # (third review, L-2), so they spend none of the four.
    enroll(member, browser)
    answers = [
        post(
            browser, f"{TWO_STEP}/disable", {"password": PASSWORD, "code": "000000"}
        ).status_code
        for _ in range(5)
    ]
    assert answers == [400, 400, 400, 400, 429]


def test_wrong_codes_on_account_changes_are_told_to_the_person(member, enroll):
    from workeasy360.platform.identity.models import AccountEvent

    browser = Client()
    browser.force_login(member)
    enroll(member, browser)
    delivered()  # the "turned on" notice
    for _ in range(5):
        refused = post(
            browser,
            f"{TWO_STEP}/recovery-codes",
            {"password": PASSWORD, "code": "000000"},
        )
        assert refused.status_code == 400
        assert refused.json()["code"] == "two_step_code_invalid"
    assert any("Several wrong two-step codes" in m.body for m in delivered())
    # An account change, not a sign-in: nothing on the sign-in trail.
    assert not AccountEvent.objects.filter(user=member, kind="sign_in_failed").exists()


# --- L2: no path signs a two-step account in without its code ------------------------------------


def test_an_old_verification_link_does_not_sign_in_a_two_step_account(member, enroll):
    from workeasy360.platform.identity import tokens
    from workeasy360.platform.identity.models import AccountToken

    enroll(member)
    from datetime import timedelta

    raw = tokens.issue(
        member.pk, AccountToken.Purpose.VERIFY_EMAIL, ttl=timedelta(hours=1)
    )
    browser = Client()
    browser.get("/api/v1/auth/session")
    answer = post(
        browser, "/api/v1/public/signup/verify", {"token": raw, "password": PASSWORD}
    )
    if answer.status_code == 200:
        assert answer.json().get("sign_in_required") is True
    assert browser.get("/api/v1/auth/session").json().get("authenticated") is not True


def test_the_session_helper_refuses_a_two_step_account_without_the_code(
    member, enroll, rf
):
    from django.contrib.sessions.backends.db import SessionStore

    from workeasy360.contracts.errors import TwoStepChallengeExpired
    from workeasy360.interfaces.api.v1.auth_views import establish_session

    enroll(member)
    request = rf.post("/api/v1/auth/login")
    request.session = SessionStore()
    with pytest.raises(TwoStepChallengeExpired):
        establish_session(request, member, password=PASSWORD)


# --- L4: a photo keeps no camera metadata ------------------------------------------------------


def _jpeg_with_exif() -> bytes:
    exif = b"Exif\x00\x00" + b"GPS-LOCATION-SECRET" * 4
    app1 = b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    app0 = (
        b"\xff\xe0"
        + struct.pack(">H", 16)
        + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    )
    scan = (
        b"\xff\xda"
        + struct.pack(">H", 8)
        + b"\x01\x01\x00\x00\x3f\x00"
        + b"\x12\x34"
        + b"\xff\xd9"
    )
    return b"\xff\xd8" + app0 + app1 + scan


def _png_with_text() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"tEXt", b"Comment\x00GPS-LOCATION-SECRET")
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(
    ("make", "kind"), [(_jpeg_with_exif, "image/jpeg"), (_png_with_text, "image/png")]
)
def test_a_photo_is_stored_without_its_metadata(member, make, kind):
    from workeasy360.platform.identity import account
    from workeasy360.platform.identity.models import ProfilePhoto

    account.set_profile_photo(member.pk, make(), declared_type=kind)
    stored = bytes(ProfilePhoto.objects.get(user=member).data)
    assert b"GPS-LOCATION-SECRET" not in stored
    assert account.sniff_image(stored) == kind

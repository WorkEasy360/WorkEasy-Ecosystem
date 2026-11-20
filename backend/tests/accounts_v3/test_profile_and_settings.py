"""My Profile and Settings on WorkEasy360 Accounts (ADR-075, Accounts V3 04,
08.1, 08.2, 10.1, 10.2, 10.3): the profile fields and photo and what products
read of them, the pending email change, notification choices, account closure
and central account support."""

from __future__ import annotations

import struct
import zlib

import pytest
from django.core import mail
from django.test import Client

from tests.accounts_v3.conftest import ACCOUNT_API, PASSWORD, add_member, delivered, post

pytestmark = pytest.mark.django_db

PROFILE = f"{ACCOUNT_API}/profile"
PHOTO = f"{ACCOUNT_API}/profile/photo"


def _png(width: int = 2, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# --- My Profile -------------------------------------------------------------------------


def test_the_profile_fields_save_validate_and_clear(person, accounts_on, on_accounts):
    accounts_on()
    here = on_accounts(person["user"])
    saved = here.patch(
        PROFILE,
        {
            "full_name": "  Ravi   Kumar ",
            "country": "in",
            "language": "hi",
            "time_zone": "Asia/Kolkata",
        },
    )
    assert saved.status_code == 200, saved.content
    body = saved.json()
    assert (body["name"], body["country"], body["language"], body["time_zone"]) == (
        "Ravi Kumar",
        "IN",
        "hi",
        "Asia/Kolkata",
    )
    assert body["email_verified"] in (True, False) and body["user_id"] == str(
        person["user"].pk
    )
    for field, value in (
        ("country", "XX"),
        ("language", "klingon"),
        ("time_zone", "Mars/Base"),
    ):
        refused = here.patch(PROFILE, {field: value})
        assert refused.status_code == 400 and refused.json()["field"] == field
    # A refused field writes nothing of the rest of the request.
    mixed = here.patch(PROFILE, {"full_name": "Changed", "country": "XX"})
    assert mixed.status_code == 400
    assert here.get(PROFILE).json()["name"] == "Ravi Kumar"
    cleared = here.patch(PROFILE, {"country": "", "language": "", "time_zone": ""}).json()
    assert (cleared["country"], cleared["language"], cleared["time_zone"]) == ("", "", "")
    options = here.get(f"{ACCOUNT_API}/profile/options").json()
    assert "IN" in options["countries"] and "hi" in options["languages"]
    assert "Asia/Kolkata" in options["time_zones"]


def test_the_central_id_never_changes_with_the_profile(person, accounts_on, on_accounts):
    accounts_on()
    here = on_accounts(person["user"])
    before = here.get(PROFILE).json()["user_id"]
    here.patch(PROFILE, {"full_name": "Another Name", "country": "AE"})
    assert here.get(PROFILE).json()["user_id"] == before == str(person["user"].pk)


def test_a_photo_is_checked_by_its_bytes_and_served_only_as_that(
    person, accounts_on, on_accounts
):
    accounts_on()
    here = on_accounts(person["user"])
    png = _png()
    stored = here.client.put(
        PHOTO, png, content_type="image/png", HTTP_HOST=here.netloc, secure=True
    )
    assert stored.status_code == 200, stored.content
    url = stored.json()["photo_url"]
    assert url.startswith("/api/v1/public/profile-photos/")

    served = here.client.get(url, HTTP_HOST=here.netloc, secure=True)
    assert served.status_code == 200
    assert served["Content-Type"] == "image/png"
    assert served["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in served["Content-Security-Policy"]
    assert served.content == png

    for body, kind in (
        (b"<svg onload=alert(1)>", "image/png"),
        (b"GIF89a....", "image/gif"),
        (png, "image/jpeg"),  # the declared type must match the bytes
        (b"\x89PNG\r\n\x1a\n" + b"0" * 300_000, "image/png"),  # too large
    ):
        refused = here.client.put(
            PHOTO, body, content_type=kind, HTTP_HOST=here.netloc, secure=True
        )
        assert refused.status_code == 400 and refused.json()["code"] == "photo_invalid"

    # A new photo retires the old URL at once; removing it retires the new one.
    again = here.client.put(
        PHOTO, _png(3, 3), content_type="image/png", HTTP_HOST=here.netloc, secure=True
    )
    new_url = again.json()["photo_url"]
    assert new_url != url
    assert here.client.get(url, HTTP_HOST=here.netloc, secure=True).status_code == 404
    removed = here.client.delete(PHOTO, HTTP_HOST=here.netloc, secure=True)
    assert removed.json()["photo_url"] is None
    gone = here.client.get(new_url, HTTP_HOST=here.netloc, secure=True)
    assert gone.status_code == 404


def _product_sign_in(browser, registration, organization_id, product_client):
    """A product sign-in that asks for the profile: authorize, then redeem."""
    from tests.accounts_v3.conftest import AUTHORIZE, authorize_query, query_of

    verifier, query = authorize_query(
        registration, str(organization_id), scope="openid profile email"
    )
    granted = browser.get(AUTHORIZE, query)
    assert granted.status_code == 302, granted.content
    code = query_of(granted)["code"][0]
    reference = product_client(registration)
    issued = reference.exchange_code(code=code, code_verifier=verifier)
    claims = reference.verify_id_token(issued["id_token"], expected_nonce="client-nonce")
    return claims, issued["access_token"]


def test_products_read_the_new_profile_at_their_next_sign_in(
    signing_key, person, sso_product, product_client
):
    """Accounts is the authority for the profile (V3 8.1): a product that
    asked for ``profile`` gets the current name, language, time zone and photo
    at its next sign-in; userinfo answers the new values at once."""
    from workeasy360.platform.identity import account

    registration = sso_product("crm", scopes=("openid", "profile", "email"))
    user = person["user"]
    browser = Client()
    browser.force_login(user)
    first, _token = _product_sign_in(
        browser, registration, person["organization_id"], product_client
    )
    assert "locale" not in first and "picture" not in first

    account.update_profile(
        user.pk, full_name="Ravi K. Sharma", language="hi", time_zone="Asia/Dubai"
    )
    account.set_profile_photo(user.pk, _png(), declared_type="image/png")

    second, access_token = _product_sign_in(
        browser, registration, person["organization_id"], product_client
    )
    assert second["sub"] == first["sub"] == str(user.pk)
    assert second["name"] == "Ravi K. Sharma"
    assert second["locale"] == "hi" and second["zoneinfo"] == "Asia/Dubai"
    assert "/api/v1/public/profile-photos/" in second["picture"]
    assert second["updated_at"] >= first["updated_at"]

    account.update_profile(user.pk, full_name="Ravi Sharma")
    info = Client().get(
        "/api/v1/integrations/oauth/userinfo", HTTP_AUTHORIZATION=f"Bearer {access_token}"
    )
    assert info.status_code == 200 and info.json()["name"] == "Ravi Sharma"


# --- the pending email change ---------------------------------------------------------------


def test_a_pending_change_shows_resends_and_cancels_and_reads_the_same_for_a_taken_address(
    person, user_factory
):
    user = person["user"]
    taken = user_factory(email="already.here@example.com")
    browser = Client()
    browser.force_login(user)

    asked = post(
        browser, "/api/v1/auth/email", {"new_email": taken.email, "password": PASSWORD}
    )
    assert asked.status_code == 202
    pending = browser.get("/api/v1/auth/email/pending").json()["pending"]
    assert pending["new_email"] == taken.email
    # The same shape for a free address: no oracle.
    post(
        browser,
        "/api/v1/auth/email",
        {"new_email": "free.one@example.com", "password": PASSWORD},
    )
    free = browser.get("/api/v1/auth/email/pending").json()["pending"]
    assert set(free) == set(pending) and free["new_email"] == "free.one@example.com"

    delivered()
    mail.outbox.clear()
    resent = post(browser, "/api/v1/auth/email/pending/resend")
    assert resent.status_code == 202
    assert [m.to for m in delivered()] == [["free.one@example.com"]]

    # The old address still signs in while the change waits.
    signing_in = Client()
    signing_in.get("/api/v1/auth/session")
    assert (
        post(
            signing_in, "/api/v1/auth/login", {"email": user.email, "password": PASSWORD}
        ).status_code
        == 200
    )

    cancelled = browser.delete("/api/v1/auth/email/pending")
    assert cancelled.status_code == 200 and cancelled.json() == {"pending": None}
    assert browser.get("/api/v1/auth/email/pending").json()["pending"] is None
    assert post(browser, "/api/v1/auth/email/pending/resend").status_code == 404


def test_resending_is_rate_limited(person, settings):
    limits = dict(settings.CONTROL_PLANE_RATE_LIMITS)
    limits["email_change_resend_user"] = (1, 3600)
    settings.CONTROL_PLANE_RATE_LIMITS = limits
    browser = Client()
    browser.force_login(person["user"])
    post(
        browser,
        "/api/v1/auth/email",
        {"new_email": "new.addr@example.com", "password": PASSWORD},
    )
    assert post(browser, "/api/v1/auth/email/pending/resend").status_code == 202
    assert post(browser, "/api/v1/auth/email/pending/resend").status_code == 429


# --- Settings: notifications --------------------------------------------------------------------


def test_security_notices_are_locked_on_and_the_rest_are_the_persons(
    person, accounts_on, on_accounts
):
    accounts_on()
    here = on_accounts(person["user"])
    rows = {
        r["key"]: r
        for r in here.get(f"{ACCOUNT_API}/notifications").json()["preferences"]
    }
    assert (
        rows["security_notices"]["required"] is True
        and rows["security_notices"]["enabled"] is True
    )
    assert rows["new_sign_in_alerts"]["enabled"] is True
    assert rows["product_news"]["enabled"] is False

    refused = here.patch(f"{ACCOUNT_API}/notifications", {"security_notices": False})
    assert refused.status_code == 400
    unknown = here.patch(f"{ACCOUNT_API}/notifications", {"wallet_alerts": True})
    assert unknown.status_code == 400
    changed = here.patch(
        f"{ACCOUNT_API}/notifications",
        {"new_sign_in_alerts": False, "product_news": True},
    )
    rows = {r["key"]: r["enabled"] for r in changed.json()["preferences"]}
    assert rows == {
        "security_notices": True,
        "new_sign_in_alerts": False,
        "product_news": True,
    }


def test_a_sign_in_from_a_new_device_is_mailed_unless_turned_off(person):
    from workeasy360.platform.identity import preferences

    user = person["user"]

    def sign_in(agent: str):
        browser = Client(HTTP_USER_AGENT=agent)
        browser.get("/api/v1/auth/session")
        assert (
            post(
                browser, "/api/v1/auth/login", {"email": user.email, "password": PASSWORD}
            ).status_code
            == 200
        )

    chrome = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36"
    firefox = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    sign_in(chrome)
    delivered()
    mail.outbox.clear()
    sign_in(chrome)
    assert not [m for m in delivered() if "New sign-in" in m.subject]
    sign_in(firefox)
    assert [m for m in delivered() if "New sign-in" in m.subject]

    preferences.update_choices(user.pk, {"new_sign_in_alerts": False})
    mail.outbox.clear()
    sign_in("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/605.1.15")
    assert not [m for m in delivered() if "New sign-in" in m.subject]


def test_the_organization_links_name_only_the_persons_own(
    person, accounts_on, on_accounts
):
    accounts_on()
    rows = (
        on_accounts(person["user"])
        .get(f"{ACCOUNT_API}/organizations")
        .json()["organizations"]
    )
    assert [r["organization_id"] for r in rows] == [str(person["organization_id"])]
    assert rows[0]["is_owner"] is True and isinstance(rows[0]["can_view_billing"], bool)


# --- Settings: account closure ---------------------------------------------------------------------


def test_an_only_owner_cannot_close_and_is_told_which_organization(
    person, accounts_on, on_accounts
):
    accounts_on()
    here = on_accounts(person["user"])
    check = here.get(f"{ACCOUNT_API}/closure").json()
    assert check["can_close"] is False and check["blocking_organizations"]
    refused = here.post(
        f"{ACCOUNT_API}/closure", {"password": PASSWORD, "confirmation": "CLOSE"}
    )
    assert refused.status_code == 409 and refused.json()["code"] == "sole_owner"
    assert refused.json()["blocking_organizations"][0]["slug"]
    person["user"].refresh_from_db()
    assert person["user"].status == "active"


def test_a_member_closes_their_account_and_nothing_of_the_organization_goes(
    person, accounts_on, on_accounts, user_factory, with_password
):
    from workeasy360.platform.identity.models import UserSession

    employee = with_password(user_factory(email="employee.closes@example.com"))
    add_member(person["organization_id"], employee)
    elsewhere = Client()
    elsewhere.force_login(employee)
    accounts_on()
    here = on_accounts(employee)
    assert here.get(f"{ACCOUNT_API}/closure").json()["can_close"] is True

    no_word = here.post(
        f"{ACCOUNT_API}/closure", {"password": PASSWORD, "confirmation": "close"}
    )
    assert no_word.status_code == 400
    wrong = here.post(
        f"{ACCOUNT_API}/closure",
        {"password": "nope", "confirmation": "CLOSE"},  # pragma: allowlist secret
    )
    assert wrong.status_code == 400
    mail.outbox.clear()
    closed = here.post(
        f"{ACCOUNT_API}/closure", {"password": PASSWORD, "confirmation": "CLOSE"}
    )
    assert closed.status_code == 200 and closed.json() == {"closed": True}

    employee.refresh_from_db()
    assert employee.status == "disabled" and employee.closed_at is not None
    assert not UserSession.objects.filter(user=employee).exists()
    assert any("account is closed" in m.subject for m in delivered())
    from tests.accounts_center.conftest import OnHost
    from tests.accounts_portal.conftest import ACCOUNTS

    login = OnHost(ACCOUNTS)
    login.get("/api/v1/auth/session")
    refused = login.post(
        "/api/v1/auth/login", {"email": employee.email, "password": PASSWORD}
    )
    assert refused.status_code == 401
    # The organization and its owner are untouched.
    person["user"].refresh_from_db()
    assert person["user"].status == "active"
    owner = on_accounts(person["user"])
    assert owner.get(f"{ACCOUNT_API}/organizations").json()["organizations"]


def test_closing_with_two_step_on_needs_the_code(
    person, accounts_on, on_accounts, user_factory, with_password, enroll
):
    employee = with_password(user_factory(email="closer.two.step@example.com"))
    add_member(person["organization_id"], employee)
    raw, codes = enroll(employee)
    accounts_on()
    here = on_accounts(employee)
    assert here.get(f"{ACCOUNT_API}/closure").json()["two_step_required"] is True
    no_code = here.post(
        f"{ACCOUNT_API}/closure", {"password": PASSWORD, "confirmation": "CLOSE"}
    )
    assert (
        no_code.status_code == 400 and no_code.json()["code"] == "two_step_code_invalid"
    )
    done = here.post(
        f"{ACCOUNT_API}/closure",
        {"password": PASSWORD, "confirmation": "CLOSE", "code": codes[0]},
    )
    assert done.status_code == 200


# --- account-security support ---------------------------------------------------------------------


def test_support_answers_the_same_for_any_address_and_reaches_central_staff_only(
    person, settings
):
    from workeasy360.platform.identity.models import AccountSupportCase

    known = post(
        Client(),
        "/api/v1/public/account-support",
        {
            "email": person["user"].email,
            "name": "Ravi",
            "topic": "lost_two_step",
            "message": "Lost phone.",
        },
    )
    unknown = post(
        Client(),
        "/api/v1/public/account-support",
        {
            "email": "nobody.here@example.com",
            "topic": "cannot_sign_in",
            "message": "Help.",
        },
    )
    assert known.status_code == unknown.status_code == 202
    assert set(known.json()) == set(unknown.json()) == {"reference"}
    assert AccountSupportCase.objects.count() == 2
    bad = post(
        Client(),
        "/api/v1/public/account-support",
        {"email": "x@example.com", "topic": "nope", "message": "m"},
    )
    assert bad.status_code == 400
    # A customer session is not staff: the queue is not theirs to read.
    browser = Client()
    browser.force_login(person["user"])
    assert browser.get("/api/v1/admin/account-support").status_code in (403, 404)


def test_support_is_rate_limited_per_inbox(settings):
    limits = dict(settings.CONTROL_PLANE_RATE_LIMITS)
    limits["account_support_account"] = (1, 86400)
    settings.CONTROL_PLANE_RATE_LIMITS = limits
    body = {"email": "same.inbox@example.com", "topic": "other", "message": "Hello"}
    assert post(Client(), "/api/v1/public/account-support", body).status_code == 202
    assert post(Client(), "/api/v1/public/account-support", body).status_code == 429

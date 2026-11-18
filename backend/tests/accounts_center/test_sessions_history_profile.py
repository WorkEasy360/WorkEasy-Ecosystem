"""Active Sessions grouped per sign-in, Login History with paging and product
names, the profile editor and Switch Account on WorkEasy360 Accounts
(ADR-072)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from tests.accounts_center.conftest import ACCOUNT_API, BROWSER_AGENT, sign_in_to_product

pytestmark = pytest.mark.django_db

SESSIONS = f"{ACCOUNT_API}/sessions"
HISTORY = f"{ACCOUNT_API}/history"
PROFILE = f"{ACCOUNT_API}/profile"


# --- Active Sessions ------------------------------------------------------------------


def test_one_row_per_sign_in_not_per_portal(person, accounts_on, on_accounts):
    from workeasy360.platform.identity.models import UserSession
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    accounts_on()
    user = person["user"]
    here = on_accounts(user)
    sign_in = uuid.uuid4().hex
    session = here.session
    session[SESSION_SIGN_IN_ID_KEY] = sign_in
    session.save()
    # The same sign-in handed to two more hosts, and one other device.
    for _ in range(2):
        copy = Client()
        copy.force_login(user)
        s = copy.session
        s[SESSION_SIGN_IN_ID_KEY] = sign_in
        s.save()
    elsewhere = Client()
    elsewhere.force_login(user)
    assert UserSession.objects.filter(user_id=user.pk).count() == 4

    rows = here.get(SESSIONS).json()["sessions"]
    assert len(rows) == 2
    mine = next(r for r in rows if r["current"])
    assert mine["portal_sessions"] == 3
    other = next(r for r in rows if not r["current"])
    assert other["portal_sessions"] == 1
    assert "session_key" not in str(rows) and here.session.session_key not in str(rows)


# --- Login History --------------------------------------------------------------------


def test_history_pages_past_twenty_newest_first(person, accounts_on, on_accounts):
    from workeasy360.platform.identity.models import AccountEvent

    user = person["user"]
    now = timezone.now()
    for i in range(30):
        AccountEvent.objects.create(
            user=user,
            kind="sign_in" if i % 3 else "sign_in_failed",
            reason="" if i % 3 else "invalid_credentials",
            occurred_at=now - timedelta(minutes=i),
            ip_prefix="203.0.113.0/24",
            device_label="Firefox on Linux",
        )
    AccountEvent.objects.create(user=user, kind="password_changed", occurred_at=now)
    accounts_on()
    me = on_accounts(user)
    first = me.get(HISTORY).json()
    assert len(first["results"]) == 25 and first["has_more"] is True
    stamps = [r["occurred_at"] for r in first["results"]]
    assert stamps == sorted(stamps, reverse=True)
    second = me.get(HISTORY, {"cursor": first["next_cursor"]}).json()
    assert len(second["results"]) == 5 and second["has_more"] is False
    seen = {r["occurred_at"] for r in first["results"]} | {
        r["occurred_at"] for r in second["results"]
    }
    assert len(seen) == 30
    # Sign-ins only, unless every security event is asked for.
    assert {r["kind"] for r in first["results"] + second["results"]} == {
        "sign_in",
        "sign_in_failed",
    }
    everything = me.get(HISTORY, {"scope": "all", "limit": 100}).json()["results"]
    assert "password_changed" in {r["kind"] for r in everything}
    # Coarse only: a network prefix and a device label.
    assert first["results"][0]["ip_prefix"] == "203.0.113.0/24"


def test_history_names_the_product_of_an_sso_sign_in(
    signing_key,
    person,
    register_product,
    bcl_manifest,
    password_login,
    product_client,
    accounts_on,
    on_accounts,
):
    crm = register_product("crm", manifest=bcl_manifest("crm"))
    browser = password_login(Client(HTTP_USER_AGENT=BROWSER_AGENT), person["user"])
    sign_in_to_product(browser, crm, person["organization_id"], product_client)
    accounts_on()
    rows = on_accounts(person["user"]).get(HISTORY).json()["results"]
    product = next(r for r in rows if r["kind"] == "product_sign_in")
    assert product["product_name"] == "CRM" and product["reason"] == "crm"
    assert product["device_label"] == "Chrome on Windows"
    assert next(r for r in rows if r["kind"] == "sign_in")["product_name"] is None


def test_history_is_only_ever_my_own(person, user_factory, accounts_on, on_accounts):
    from workeasy360.platform.identity.models import AccountEvent

    other = user_factory()
    AccountEvent.objects.create(user=other, kind="sign_in", ip_prefix="198.51.100.0/24")
    accounts_on()
    body = on_accounts(person["user"]).get(HISTORY, {"scope": "all"}).json()
    assert "198.51.100.0/24" not in str(body)


def test_a_malformed_cursor_is_the_first_page(person, accounts_on, on_accounts):
    accounts_on()
    response = on_accounts(person["user"]).get(HISTORY, {"cursor": "%%%not-a-cursor"})
    assert response.status_code == 200 and response.json()["has_more"] is False


# --- Profile ----------------------------------------------------------------------


def test_the_name_can_be_changed_and_only_the_name(person, accounts_on, on_accounts):
    from workeasy360.platform.identity.models import AccountEvent

    user = person["user"]
    email = user.email
    accounts_on()
    me = on_accounts(user)
    profile = me.get(PROFILE).json()
    assert profile["user_id"] == str(user.pk) and profile["email"] == email
    changed = me.patch(
        PROFILE, {"full_name": "  Ravi   Kumar ", "email": "evil@example.com"}
    )
    assert changed.status_code == 200, changed.content
    assert changed.json()["name"] == "Ravi Kumar"
    user.refresh_from_db()
    assert (user.full_name, user.email) == ("Ravi Kumar", email)
    assert AccountEvent.objects.filter(user=user, kind="profile_updated").count() == 1
    # The same name again is no change, and no second trail entry.
    me.patch(PROFILE, {"full_name": "Ravi Kumar"})
    assert AccountEvent.objects.filter(user=user, kind="profile_updated").count() == 1
    assert "password" not in str(me.get(PROFILE).json())


@pytest.mark.parametrize(
    "bad", ["", "   ", "x" * 202, "Ravi\x00Kumar", "Ravi\x1bKumar", "Ravi\x9bKumar", 42]
)
def test_an_unusable_name_is_refused(person, accounts_on, on_accounts, bad):
    accounts_on()
    response = on_accounts(person["user"]).patch(PROFILE, {"full_name": bad})
    assert (response.status_code, response.json()["code"]) == (400, "profile_invalid")


# --- Switch Account ---------------------------------------------------------------


def test_switch_account_on_accounts_uses_the_adr_066_flow(
    person, accounts_on, on_accounts
):
    from tests.accounts_portal.conftest import ACCOUNTS

    accounts_on()
    me = on_accounts(person["user"])
    response = me.post("/api/v1/auth/switch-account")
    assert response.status_code == 200, response.content
    target = response.json()["redirect_url"]
    assert target.startswith(f"{ACCOUNTS}/switch-account#handle=")
    # This browser's sign-in on Accounts has ended; the next one is someone else's.
    assert me.get("/api/v1/auth/session").json() == {"authenticated": False}
    from urllib.parse import parse_qs

    handle = parse_qs(target.split("#", 1)[1])["handle"][0]
    finished = me.post("/api/v1/auth/switch-account/finish", {"handle": handle})
    assert finished.json()["redirect_url"] == f"{ACCOUNTS}/login?switched=1"


def test_with_identity_off_accounts_does_not_start_a_switch(
    person, accounts_on, on_accounts
):
    accounts_on(False)
    response = on_accounts(person["user"]).post("/api/v1/auth/switch-account")
    assert response.status_code == 404

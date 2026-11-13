"""Changing the sign-in email (the unified-login brief 5.6, ADR-066).

Using a different account is not the same as changing this one: the change
keeps the identity - same id, password, sessions, organizations and products -
and only the address it signs in with moves, once the new mailbox is proved.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import Client

from tests.unified_login.conftest import (
    PASSWORD,
    deliver_account_mail,
    log_in,
    signed_in_as,
    token_from,
)

pytestmark = pytest.mark.django_db

REQUEST = "/api/v1/auth/email"
CONFIRM = "/api/v1/public/email/confirm"
FORGOT = "/api/v1/public/password/forgot"


def _ask(client, new_email, password=PASSWORD):
    return client.post(
        REQUEST,
        {"new_email": new_email, "password": password},
        content_type="application/json",
    )


def _confirm(token):
    return Client().post(CONFIRM, {"token": token}, content_type="application/json")


def _mail_to(address):
    return [m for m in deliver_account_mail() if m.to == [address]]


@pytest.fixture
def rahul(person):
    user = person(email="rahul@abccompany.com")
    browser = Client()
    assert log_in(browser, user.email).status_code == 200
    mail.outbox.clear()
    return {"user": user, "browser": browser}


def test_nothing_changes_until_the_new_address_is_proved(rahul):
    from workeasy360.platform.identity.models import AccountEvent, AccountToken

    asked = _ask(rahul["browser"], "Rahul.New@ABCcompany.com")
    assert asked.status_code == 202, asked.content
    rahul["user"].refresh_from_db()
    assert rahul["user"].email == "rahul@abccompany.com"
    sent = _mail_to("Rahul.New@ABCcompany.com")
    assert sent and "/confirm-email#token=" in sent[-1].body
    assert "?token=" not in sent[-1].body
    token = token_from(sent[-1])
    row = AccountToken.objects.get(user=rahul["user"], purpose="change_email")
    assert token not in row.token_hash and row.new_email == "Rahul.New@ABCcompany.com"
    assert AccountEvent.objects.filter(
        user=rahul["user"], kind=AccountEvent.Kind.EMAIL_CHANGE_REQUESTED
    ).exists()


def test_the_link_moves_the_sign_in_email_and_keeps_the_identity(rahul):
    from workeasy360.platform.identity.models import AccountEvent

    user = rahul["user"]
    before_id, before_password = user.pk, user.password
    _ask(rahul["browser"], "Rahul.New@ABCcompany.com")
    token = token_from(_mail_to("Rahul.New@ABCcompany.com")[-1])
    mail.outbox.clear()

    confirmed = _confirm(token)
    assert confirmed.status_code == 200, confirmed.content
    user.refresh_from_db()
    assert user.pk == before_id and user.password == before_password
    assert user.email == "rahul.new@abccompany.com"
    assert user.email_original == "Rahul.New@ABCcompany.com"
    assert user.email_verified_at is not None
    assert AccountEvent.objects.filter(
        user=user, kind=AccountEvent.Kind.EMAIL_CHANGED
    ).exists()
    # The previous address is told, with the new one only partly shown.
    notice = _mail_to("rahul@abccompany.com")
    assert notice and "r***@abccompany.com" in notice[-1].body
    assert "rahul.new@" not in notice[-1].body.lower()
    # The session that asked is still signed in, as the same person.
    assert signed_in_as(rahul["browser"]) == "rahul.new@abccompany.com"
    # The new address signs in; the old one no longer does.
    assert log_in(Client(), "rahul.new@abccompany.com").status_code == 200
    assert log_in(Client(), "rahul@abccompany.com").status_code != 200


def test_a_link_works_once(rahul):
    _ask(rahul["browser"], "new@abccompany.com")
    token = token_from(_mail_to("new@abccompany.com")[-1])
    assert _confirm(token).status_code == 200
    again = _confirm(token)
    assert again.status_code == 404 and again.json()["code"] == "link_invalid"


def test_a_reset_link_sent_to_the_old_address_dies_with_the_change(rahul):
    anonymous = Client()
    anonymous.post(
        FORGOT, {"email": "rahul@abccompany.com"}, content_type="application/json"
    )
    reset_token = token_from(_mail_to("rahul@abccompany.com")[-1])
    _ask(rahul["browser"], "new@abccompany.com")
    assert _confirm(token_from(_mail_to("new@abccompany.com")[-1])).status_code == 200
    reset = anonymous.post(
        "/api/v1/public/password/reset",
        {"token": reset_token, "password": "a brand new passphrase 77"},
        content_type="application/json",
    )
    assert reset.status_code == 404


def test_only_the_latest_link_works(rahul):
    _ask(rahul["browser"], "first@abccompany.com")
    first = token_from(_mail_to("first@abccompany.com")[-1])
    _ask(rahul["browser"], "second@abccompany.com")
    second = token_from(_mail_to("second@abccompany.com")[-1])
    assert _confirm(first).status_code == 404
    assert _confirm(second).status_code == 200
    rahul["user"].refresh_from_db()
    assert rahul["user"].email == "second@abccompany.com"


# --- refusals ------------------------------------------------------------------------------------


def test_the_current_password_is_required_and_a_wrong_one_counts(rahul):
    refused = _ask(rahul["browser"], "new@abccompany.com", password="not it")
    assert refused.status_code == 400
    assert refused.json()["code"] == "current_password_incorrect"
    rahul["user"].refresh_from_db()
    assert rahul["user"].failed_login_count == 1
    assert not _mail_to("new@abccompany.com")


@pytest.mark.parametrize(
    "address", ["", "not-an-email", "rahul@abccompany.com", "RAHUL@abccompany.com"]
)
def test_an_unusable_address_is_refused_by_field(rahul, address):
    refused = _ask(rahul["browser"], address)
    assert refused.status_code == 400
    assert refused.json()["code"] == "email_change_invalid"


def test_an_address_another_account_holds_gets_the_same_answer_and_is_told(rahul, person):
    from workeasy360.platform.identity.models import AccountToken

    person(email="priya@abccompany.com")
    taken = _ask(rahul["browser"], "priya@abccompany.com")
    free = _ask(rahul["browser"], "nobody@abccompany.com")
    assert (taken.status_code, taken.json()) == (free.status_code, free.json())
    assert not AccountToken.objects.filter(new_email="priya@abccompany.com").exists()
    notice = _mail_to("priya@abccompany.com")
    assert notice and "already has a workeasy360 account" in notice[-1].subject.lower()
    assert "#token=" not in notice[-1].body


def test_an_address_taken_before_the_link_is_used_is_refused(rahul, person):
    _ask(rahul["browser"], "late@abccompany.com")
    token = token_from(_mail_to("late@abccompany.com")[-1])
    person(email="late@abccompany.com")
    refused = _confirm(token)
    assert refused.status_code == 404
    rahul["user"].refresh_from_db()
    assert rahul["user"].email == "rahul@abccompany.com"


def test_signed_out_nobody_can_ask(db):
    assert _ask(Client(), "new@example.com").status_code in (401, 403)


def test_a_signed_in_account_cannot_become_a_mail_cannon(rahul):
    answers = [
        _ask(rahul["browser"], f"n{i}@abccompany.com").status_code for i in range(6)
    ]
    assert answers[:5] == [202] * 5
    assert answers[5] == 429


def test_the_request_needs_the_csrf_token(person):
    user = person()
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(user)
    assert _ask(browser, "new@example.com").status_code == 403

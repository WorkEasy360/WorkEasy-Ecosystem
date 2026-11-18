"""ADR-072 security fix: revoking a session ends the whole sign-in.

In multi-portal mode one password sign-in has a session on WorkEasy360
Accounts and one on each portal it was handed to (ADR-043), all sharing its
``sign_in_id``. Revoking one of them used to delete that row only: the same
sign-in stayed alive on its other hosts while its products were told it had
ended. These tests fail on 4b183c6 and pass with the fix. They run in
single-host mode with the portal copies made directly - the fix must hold in
every mode, identity on or off.
"""

from __future__ import annotations

import uuid

import pytest
from django.test import Client

from tests.accounts_center.conftest import BROWSER_AGENT

pytestmark = pytest.mark.django_db

SESSIONS = "/api/v1/auth/sessions"


def _public_id(client: Client) -> uuid.UUID:
    from workeasy360.platform.identity.models import UserSession

    return UserSession.objects.get(session_key=client.session.session_key).public_id


def _sign_in_of(client: Client) -> uuid.UUID:
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    return uuid.UUID(str(client.session[SESSION_SIGN_IN_ID_KEY]))


def _portal_copy(user, sign_in_id: uuid.UUID) -> Client:
    """Another host's session of the same sign-in - what the ADR-043 hand-off
    leaves on app. for a sign-in made on accounts."""
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    copy = Client(HTTP_USER_AGENT=BROWSER_AGENT)
    copy.force_login(user)
    session = copy.session
    session[SESSION_SIGN_IN_ID_KEY] = str(sign_in_id)
    session.save()
    return copy


def _signed_in(client: Client) -> bool:
    return client.get("/api/v1/auth/session").json().get("authenticated") is True


@pytest.fixture
def two_sign_ins(person, password_login):
    """Laptop: one sign-in with a session on two hosts. Phone: another
    sign-in, also on two hosts. The phone does the revoking."""
    user = person["user"]
    laptop = password_login(Client(HTTP_USER_AGENT=BROWSER_AGENT), user)
    laptop_copy = _portal_copy(user, _sign_in_of(laptop))
    phone = password_login(Client(HTTP_USER_AGENT=BROWSER_AGENT), user)
    phone_copy = _portal_copy(user, _sign_in_of(phone))
    return {
        "user": user,
        "laptop": laptop,
        "laptop_copy": laptop_copy,
        "phone": phone,
        "phone_copy": phone_copy,
    }


def test_revoking_one_session_ends_every_session_of_that_sign_in(two_sign_ins):
    phone, laptop, laptop_copy = (
        two_sign_ins["phone"],
        two_sign_ins["laptop"],
        two_sign_ins["laptop_copy"],
    )
    assert _signed_in(laptop) and _signed_in(laptop_copy)

    response = phone.post(f"{SESSIONS}/{_public_id(laptop_copy)}/revoke")
    assert response.status_code == 200, response.content
    # Both hosts' sessions of the laptop's sign-in are gone - before the fix
    # the laptop's other session stayed signed in.
    assert not _signed_in(laptop_copy)
    assert not _signed_in(laptop)
    assert _signed_in(phone) and _signed_in(two_sign_ins["phone_copy"])
    assert response.json()["ended"] == 2


def test_products_of_the_revoked_sign_in_are_told_once(
    two_sign_ins, register_product, bcl_manifest
):
    from workeasy360.platform.ecosystem.models import BackchannelLogout, ProductSignIn

    crm = register_product("crm", manifest=bcl_manifest("crm"))
    user = two_sign_ins["user"]
    laptop_sign_in = _sign_in_of(two_sign_ins["laptop"])
    ProductSignIn.objects.create(
        integration=crm["integration"], user_id=user.pk, sign_in_id=laptop_sign_in
    )
    two_sign_ins["phone"].post(f"{SESSIONS}/{_public_id(two_sign_ins['laptop'])}/revoke")
    (delivery,) = BackchannelLogout.objects.filter(user_id=user.pk)
    assert (delivery.sign_in_id, delivery.reason) == (laptop_sign_in, "session_revoked")
    assert ProductSignIn.objects.get(sign_in_id=laptop_sign_in).ended_at is not None


def test_a_session_of_my_own_sign_in_is_refused_like_my_own(two_sign_ins):
    phone, phone_copy = two_sign_ins["phone"], two_sign_ins["phone_copy"]
    response = phone.post(f"{SESSIONS}/{_public_id(phone_copy)}/revoke")
    assert (response.status_code, response.json()["code"]) == (404, "session_not_found")
    assert _signed_in(phone) and _signed_in(phone_copy)


def test_revoking_the_others_keeps_my_whole_sign_in(two_sign_ins):
    phone = two_sign_ins["phone"]
    response = phone.post(f"{SESSIONS}/revoke-others")
    assert response.status_code == 200 and response.json()["ended"] == 2
    assert not _signed_in(two_sign_ins["laptop"])
    assert not _signed_in(two_sign_ins["laptop_copy"])
    # The phone's own sign-in on its other host is kept (it was deleted).
    assert _signed_in(phone) and _signed_in(two_sign_ins["phone_copy"])


def test_another_persons_session_is_still_not_found(
    two_sign_ins, user_factory, password_login
):
    from tests.accounts_center.conftest import PASSWORD

    stranger = user_factory(password=PASSWORD)
    theirs = password_login(Client(HTTP_USER_AGENT=BROWSER_AGENT), stranger)
    response = theirs.post(f"{SESSIONS}/{_public_id(two_sign_ins['laptop'])}/revoke")
    assert (response.status_code, response.json()["code"]) == (404, "session_not_found")
    assert _signed_in(two_sign_ins["laptop"])


def test_a_session_without_a_sign_in_id_ends_alone(person):
    """A session older than ADR-043's sign-in ids is its own sign-in."""
    from workeasy360.platform.identity import account
    from workeasy360.platform.identity.models import UserSession

    user = person["user"]
    old, other = Client(), Client()
    old.force_login(user)
    other.force_login(user)
    assert (
        UserSession.objects.filter(user_id=user.pk, sign_in_id__isnull=True).count() == 2
    )
    ended = account.revoke_session(user.pk, _public_id(old))
    assert not _signed_in(old) and _signed_in(other)
    assert ended == 1

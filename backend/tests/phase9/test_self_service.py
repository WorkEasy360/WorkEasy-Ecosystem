"""Self-service accounts (Phase 9): signup, email verification, password reset,
and every refusal - uniform answers, single-use links, pre-hijacking defence,
lockout accounting, CSRF and rate limits."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from tests.phase9.conftest import PASSWORD, deliver_account_mail, token_from

pytestmark = pytest.mark.django_db

SIGNUP = "/api/v1/public/signup"
VERIFY = "/api/v1/public/signup/verify"
RESEND = "/api/v1/public/signup/resend"
FORGOT = "/api/v1/public/password/forgot"
RESET = "/api/v1/public/password/reset"
LOGIN = "/api/v1/auth/login"
CHOSEN = "a long enough passphrase 9"
NEWER = "another long passphrase 10"


def _signup(client, email="Alice.Example@Example.COM", **extra):
    body = {
        "first_name": "Alice",
        "last_name": "Example",
        "email": email,
        "password": CHOSEN,
        **extra,
    }
    return client.post(SIGNUP, body, content_type="application/json")


def _user(email="alice.example@example.com"):
    from workeasy360.platform.identity.models import User

    return User.objects.filter(email=email).first()


def _verify_link():
    messages = [m for m in deliver_account_mail() if "Confirm your email" in m.subject]
    assert messages, [m.subject for m in mail.outbox]
    return token_from(messages[-1])


def _post(client, path, body):
    return client.post(path, body, content_type="application/json")


# --- signup ------------------------------------------------------------------


def test_signup_creates_one_pending_identity_and_emails_a_link(anonymous):
    response = _signup(anonymous())
    assert response.status_code == 202, response.content
    user = _user()
    assert user is not None
    assert (user.status, user.email_verified_at) == ("pending", None)
    assert user.full_name == "Alice Example"
    assert user.acquisition_source == "self_signup"
    assert user.email_original == "Alice.Example@Example.COM"
    assert user.check_password(CHOSEN)
    token = _verify_link()
    # Only the hash is stored.
    from workeasy360.platform.identity.models import AccountToken

    row = AccountToken.objects.get(user=user)
    assert token not in row.token_hash and len(row.token_hash) == 64
    # The link sits in the fragment, never in a query string.
    body = mail.outbox[-1].body
    assert "/verify-email#token=" in body and "?token=" not in body


def test_account_email_is_dispatched_when_the_signup_commits(
    anonymous, django_capture_on_commit_callbacks
):
    """Browser-check finding: the delivery task was dispatched after commit,
    when the platform routing context it was queued under had already ended,
    so the dispatch was refused (``MissingTenantContext``) and every account
    email waited for the five-minute sweeper. The email must go out at commit,
    with nothing but the ordinary worker (eager here) involved."""
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert _signup(anonymous()).status_code == 202
    assert [m.subject for m in mail.outbox if "Confirm your email" in m.subject]
    token_from(mail.outbox[-1])


def test_a_pending_account_cannot_sign_in_and_only_the_password_holder_learns_why(
    anonymous,
):
    client = anonymous()
    _signup(client)
    right = _post(
        client, LOGIN, {"email": "alice.example@example.com", "password": CHOSEN}
    )
    assert (right.status_code, right.json()["code"]) == (401, "email_not_verified")
    wrong = _post(
        client,
        LOGIN,
        {"email": "alice.example@example.com", "password": "nope nope nope"},
    )
    assert (wrong.status_code, wrong.json()["code"]) == (401, "invalid_credentials")
    assert _user().failed_login_count == 1


def test_verification_needs_the_link_and_the_password(anonymous):
    client = anonymous()
    _signup(client)
    token = _verify_link()
    wrong = _post(client, VERIFY, {"token": token, "password": "not the password at all"})
    assert (wrong.status_code, wrong.json()["code"]) == (401, "invalid_credentials")
    user = _user()
    assert user.status == "pending" and user.failed_login_count == 1
    # The link survives a wrong password.
    right = _post(client, VERIFY, {"token": token, "password": CHOSEN})
    assert right.status_code == 200, right.content
    assert right.json() == {"organization_slug": None, "organization_id": None}
    user.refresh_from_db()
    assert user.status == "active" and user.email_verified_at is not None
    assert user.failed_login_count == 0
    # Signed in by the verification itself.
    session = client.get("/api/v1/auth/session").json()
    assert session["authenticated"] and session["email"] == "alice.example@example.com"
    # Single use.
    again = _post(anonymous(), VERIFY, {"token": token, "password": CHOSEN})
    assert (again.status_code, again.json()["code"]) == (404, "link_invalid")


def test_verification_creates_the_requested_organization_with_the_person_as_owner(
    anonymous,
):
    from workeasy360.platform.organizations.selectors import organizations_for_user

    client = anonymous()
    _signup(client, company_name="Alice Trading Co")
    response = _post(client, VERIFY, {"token": _verify_link(), "password": CHOSEN})
    slug = response.json()["organization_slug"]
    assert slug
    page = organizations_for_user(_user().id, limit=10)
    assert [o["slug"] for o in page.items] == [slug]
    assert page.items[0]["display_name"] == "Alice Trading Co"
    # The owner can use the organization straight away.
    access = client.get(f"/api/v1/orgs/{slug}/access")
    assert access.status_code == 200, access.content


@pytest.mark.parametrize("token", ["", "x" * 500, "not-a-real-token"])
def test_bad_links_all_get_the_same_answer(anonymous, token):
    response = _post(anonymous(), VERIFY, {"token": token, "password": CHOSEN})
    assert (response.status_code, response.json()["code"]) == (404, "link_invalid")


def test_an_expired_link_is_refused(anonymous):
    from workeasy360.platform.identity.models import AccountToken

    client = anonymous()
    _signup(client)
    token = _verify_link()
    AccountToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    response = _post(client, VERIFY, {"token": token, "password": CHOSEN})
    assert response.json()["code"] == "link_invalid"


def test_signup_for_a_registered_address_answers_the_same_and_changes_nothing(
    anonymous, user_factory
):
    existing = user_factory(email="taken@example.com", password=PASSWORD)
    response = _signup(anonymous(), email="Taken@Example.com")
    assert response.status_code == 202
    assert response.json() == _signup(anonymous(), email="new@example.com").json()
    existing.refresh_from_db()
    assert existing.check_password(PASSWORD) and existing.status == "active"
    subjects = [m.subject for m in deliver_account_mail()]
    assert "You already have a Workeasy360 account" in subjects
    # The "existing" email carries no token of any kind.
    notice = next(m for m in mail.outbox if m.to == ["taken@example.com"])
    assert "#token=" not in notice.body


def test_repeating_a_pending_signup_resends_but_never_replaces_the_password(anonymous):
    client = anonymous()
    _signup(client)
    first = _verify_link()
    body = {
        "first_name": "Mallory",
        "last_name": "Other",
        "email": "alice.example@example.com",
        "password": "mallory chose this one 7",
    }
    assert _post(anonymous(), SIGNUP, body).status_code == 202
    second = _verify_link()
    assert second != first
    user = _user()
    assert user.check_password(CHOSEN) and user.full_name == "Alice Example"
    # Only the latest link works.
    assert _post(client, VERIFY, {"token": first, "password": CHOSEN}).status_code == 404
    assert _post(client, VERIFY, {"token": second, "password": CHOSEN}).status_code == 200


def test_a_link_clicked_by_the_wrong_person_cannot_activate_a_stranger_password(
    anonymous,
):
    """Pre-account-hijacking: someone signs up with another person's address.
    The link reaches the real owner, who does not know the stranger's password,
    so the account cannot be activated with it. A password reset from the same
    mailbox replaces it."""
    _signup(anonymous(), email="victim@example.com")
    link = _verify_link()
    refused = _post(
        anonymous(), VERIFY, {"token": link, "password": "victim guess one 11"}
    )
    assert refused.status_code == 401
    assert _post(anonymous(), FORGOT, {"email": "victim@example.com"}).status_code == 202
    reset = token_from(
        next(m for m in deliver_account_mail() if "Reset your" in m.subject)
    )
    done = _post(anonymous(), RESET, {"token": reset, "password": NEWER})
    assert done.status_code == 200, done.content
    user = _user("victim@example.com")
    assert user.status == "active" and user.email_verified_at is not None
    assert user.check_password(NEWER) and not user.check_password(CHOSEN)
    # The stranger's verification link is spent by the reset.
    assert (
        _post(anonymous(), VERIFY, {"token": link, "password": CHOSEN}).status_code == 404
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("first_name", "", "signup_invalid"),
        ("last_name", "x" * 101, "signup_invalid"),
        ("email", "not-an-address", "signup_invalid"),
        ("password", "short", "password_policy_violation"),
        ("company_name", "   ", None),
    ],
)
def test_signup_validation(anonymous, field, value, code):
    response = _signup(anonymous(), **{field: value})
    if code is None:
        assert response.status_code == 202
        return
    assert (response.status_code, response.json()["code"]) == (400, code)
    assert _user() is None


def test_a_weak_password_is_refused_whether_or_not_the_address_is_registered(
    anonymous, user_factory
):
    user_factory(email="taken@example.com", password=PASSWORD)
    for address in ("taken@example.com", "free@example.com"):
        response = _signup(anonymous(), email=address, password="short")
        assert (response.status_code, response.json()["code"]) == (
            400,
            "password_policy_violation",
        )


def test_signup_is_refused_while_closed(anonymous):
    from django.core.cache import cache

    from workeasy360.platform.administration.models import PlatformSettings

    PlatformSettings.objects.create(id=1, signup_enabled=False)
    cache.clear()
    response = _signup(anonymous())
    assert (response.status_code, response.json()["code"]) == (403, "signup_closed")
    assert _user() is None


def test_signup_is_rate_limited_per_inbox(anonymous, settings):
    from django.core.cache import cache

    cache.clear()
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "signup_account": (2, 3600),
    }
    statuses = [
        _signup(anonymous(ip=f"10.0.0.{i}"), email="target@example.com").status_code
        for i in range(3)
    ]
    assert statuses == [202, 202, 429]
    cache.clear()


def test_signup_without_a_csrf_token_is_refused():
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    response = _signup(client)
    assert response.status_code == 403
    assert _user() is None


def test_resend_is_silent_for_every_address_but_a_pending_one(anonymous, user_factory):
    user_factory(email="taken@example.com", password=PASSWORD)
    for address in ("taken@example.com", "nobody@example.com"):
        assert _post(anonymous(), RESEND, {"email": address}).status_code == 202
    assert deliver_account_mail() == []
    _signup(anonymous(), company_name="Kept Company")
    _verify_link()
    assert (
        _post(anonymous(), RESEND, {"email": "alice.example@example.com"}).status_code
        == 202
    )
    token = _verify_link()
    verified = _post(anonymous(), VERIFY, {"token": token, "password": CHOSEN})
    # The company asked for at signup survives the resend.
    assert verified.json()["organization_slug"]


# --- password reset -------------------------------------------------------------


def test_reset_sets_the_password_signs_out_everywhere_and_is_single_use(
    anonymous, user_factory, login
):
    from workeasy360.platform.identity.models import AccountEvent

    user = user_factory(email="reset@example.com", password=PASSWORD)
    signed_in = login(user)
    user.failed_login_count = 10
    user.locked_until = timezone.now() + timedelta(minutes=10)
    user.save()

    assert _post(anonymous(), FORGOT, {"email": "RESET@example.com"}).status_code == 202
    token = token_from(
        next(m for m in deliver_account_mail() if "Reset your" in m.subject)
    )
    done = _post(anonymous(), RESET, {"token": token, "password": NEWER})
    assert done.status_code == 200, done.content
    user.refresh_from_db()
    assert user.check_password(NEWER)
    assert (user.failed_login_count, user.locked_until) == (0, None)
    assert AccountEvent.objects.filter(user=user, kind="password_reset").exists()
    # The session opened before the reset is gone.
    assert signed_in.get("/api/v1/auth/session").json() == {"authenticated": False}
    # Single use, and the new password signs in.
    assert (
        _post(anonymous(), RESET, {"token": token, "password": NEWER}).status_code == 404
    )
    ok = _post(anonymous(), LOGIN, {"email": "reset@example.com", "password": NEWER})
    assert ok.status_code == 200
    # A notice went to the address after the reset.
    assert any("was reset" in m.subject for m in deliver_account_mail())


def test_reset_request_answers_the_same_for_unknown_and_disabled_addresses(
    anonymous, user_factory
):
    disabled = user_factory(email="gone@example.com", password=PASSWORD)
    disabled.status = "disabled"
    disabled.save()
    answers = {
        _post(anonymous(), FORGOT, {"email": address}).content
        for address in ("gone@example.com", "never@example.com", "")
    }
    assert len(answers) == 1
    assert deliver_account_mail() == []


def test_reset_applies_the_password_policy(anonymous, user_factory):
    user_factory(email="policy@example.com", password=PASSWORD)
    _post(anonymous(), FORGOT, {"email": "policy@example.com"})
    token = token_from(
        next(m for m in deliver_account_mail() if "Reset your" in m.subject)
    )
    weak = _post(anonymous(), RESET, {"token": token, "password": "short"})
    assert (weak.status_code, weak.json()["code"]) == (400, "password_policy_violation")
    # A refused password does not spend the link.
    assert (
        _post(anonymous(), RESET, {"token": token, "password": NEWER}).status_code == 200
    )


def test_reset_gives_a_partner_provisioned_owner_a_way_to_sign_in(anonymous):
    """D-020: an identity a partner named before its owner ever signed in has
    no password. The emailed reset proves the mailbox and sets one."""
    from workeasy360.platform.identity.services import ensure_identity_for_email

    user_id, created = ensure_identity_for_email("named@example.com")
    assert created
    assert _post(anonymous(), FORGOT, {"email": "named@example.com"}).status_code == 202
    token = token_from(
        next(m for m in deliver_account_mail() if "Reset your" in m.subject)
    )
    assert (
        _post(anonymous(), RESET, {"token": token, "password": NEWER}).status_code == 200
    )
    ok = _post(anonymous(), LOGIN, {"email": "named@example.com", "password": NEWER})
    assert ok.status_code == 200


def test_a_partner_can_name_an_address_that_only_has_a_pending_signup(anonymous):
    from workeasy360.platform.identity.services import ensure_identity_for_email

    _signup(anonymous(), email="squatted@example.com")
    user_id, created = ensure_identity_for_email("squatted@example.com")
    assert user_id == _user("squatted@example.com").id and not created


# --- invitations and retention -------------------------------------------------


def test_an_invitation_completes_a_pending_signup_with_the_invitee_password(
    anonymous, console_org, login
):
    from tests.extension.conftest import url

    org = console_org()
    _signup(anonymous(), email="joiner@example.com")
    owner = login(org["owner"])
    invited = owner.post(
        url(org["slug"], "invitations"),
        {"email": "joiner@example.com", "role": "member"},
        content_type="application/json",
    )
    assert invited.status_code == 201, invited.content
    from tests.phase9.conftest import deliver

    token = token_from(
        next(m for m in deliver(org["org"].id) if m.to == ["joiner@example.com"])
    )
    accepted = anonymous().post(
        "/api/v1/public/invitations/accept",
        {"token": token, "password": NEWER, "full_name": "Joiner Person"},
        content_type="application/json",
    )
    assert accepted.status_code == 200, accepted.content
    user = _user("joiner@example.com")
    assert user.status == "active" and user.check_password(NEWER)


def test_never_verified_signups_and_old_links_are_pruned(anonymous):
    from workeasy360.platform.administration.tasks import prune_activity
    from workeasy360.platform.identity.models import AccountToken, User

    _signup(anonymous(), email="stale@example.com")
    _signup(anonymous(), email="fresh@example.com")
    User.objects.filter(email="stale@example.com").update(
        created_at=timezone.now() - timedelta(days=30)
    )
    AccountToken.objects.filter(user__email="stale@example.com").update(
        expires_at=timezone.now() - timedelta(days=60)
    )
    result = prune_activity()
    assert result["pending_accounts"] == 1
    assert not User.objects.filter(email="stale@example.com").exists()
    assert User.objects.filter(email="fresh@example.com").exists()
    # The freed address can sign up again.
    assert _signup(anonymous(), email="stale@example.com").status_code == 202

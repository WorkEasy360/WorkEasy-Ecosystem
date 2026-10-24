"""Identity: normalisation, uniqueness, state, and authentication edges."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from workeasy360.contracts.enums import UserStatus
from workeasy360.platform.identity.email import is_plausible_email, normalize_email
from workeasy360.platform.identity.models import User

pytestmark = pytest.mark.django_db


# --- normalisation (pure, no database) -----------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Alice@Example.COM", "alice@example.com"),
        ("  bob@example.com  ", "bob@example.com"),
        ("CAROL@EXAMPLE.COM", "carol@example.com"),
        ("dave@Example.Com", "dave@example.com"),
        ("", ""),
    ],
)
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


def test_normalisation_preserves_plus_addressing():
    """``alice+admin@`` and ``alice@`` are different mailboxes.

    Stripping the tag would merge two identities, which on some providers
    belong to different people. Normalisation that is too aggressive is its
    own security bug.
    """
    assert normalize_email("Alice+Admin@Example.com") == "alice+admin@example.com"
    assert normalize_email("alice+admin@example.com") != normalize_email(
        "alice@example.com"
    )


def test_normalisation_preserves_dots():
    """Gmail treats dots as insignificant; the standard does not, and other
    providers do not. Collapsing them would merge distinct accounts."""
    assert normalize_email("First.Last@Example.com") == "first.last@example.com"
    assert normalize_email("firstlast@example.com") != normalize_email(
        "first.last@example.com"
    )


def test_normalisation_is_idempotent():
    once = normalize_email("Alice@Example.COM")
    assert normalize_email(once) == once


def test_unicode_confusables_are_normalised():
    """NFKC, so visually identical addresses written with different code
    points compare equal rather than creating two accounts."""
    assert normalize_email("ﬁrst@example.com") == "first@example.com"


def test_is_plausible_email():
    assert is_plausible_email("a@b.com")
    assert not is_plausible_email("no-at-sign")
    assert not is_plausible_email("two@@example.com")
    assert not is_plausible_email("@example.com")
    assert not is_plausible_email("a@nodot")


def test_is_plausible_email_agrees_with_what_the_model_will_accept():
    """An address this says yes to must not then fail model validation - that
    was a 500 in every flow that trusts it (invitations, sign-up, partner
    provisioning)."""
    for bad in (
        "a b@example.com",
        "<script>@example.com",
        "a,b@example.com",
        "a@b.com\r\nBcc: x@y.com",
        "a@b .com",
        "a@-b.com",
        "a\x00@b.com",
    ):
        assert not is_plausible_email(bad), bad
    for good in ("a@b.com", "first.last+tag@sub.example.co.uk", "o'brien@example.com"):
        assert is_plausible_email(good), good


# --- creation and uniqueness ---------------------------------------------


def test_create_user_normalises_and_keeps_original():
    user = User.objects.create_user(email="Alice@Example.COM")
    assert user.email == "alice@example.com"
    assert user.email_original == "Alice@Example.COM"


def test_email_uniqueness_is_case_insensitive():
    """The failure this prevents: two accounts for one human, differing only
    by case, with a unique constraint that looked adequate and did nothing.

    The manager validates before inserting, so this surfaces as a
    ValidationError with a usable message rather than a raw IntegrityError.
    The database constraint is the backstop and is covered by
    ``test_uniqueness_survives_direct_save``.
    """
    User.objects.create_user(email="alice@example.com")
    with pytest.raises(ValidationError, match="already exists"):
        User.objects.create_user(email="ALICE@EXAMPLE.COM")


def test_uniqueness_survives_direct_save():
    """save() normalises too, not just the manager.

    Without this, code calling save() directly bypasses validation and can
    insert a differently-cased duplicate.
    """
    User.objects.create_user(email="bob@example.com")
    duplicate = User(email="BOB@Example.com")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            duplicate.save()


def test_lookup_by_natural_key_is_case_insensitive():
    """A user who registered as Alice@ must be able to log in as alice@."""
    created = User.objects.create_user(email="Alice@Example.com")
    assert User.objects.get_by_natural_key("ALICE@EXAMPLE.COM") == created


def test_user_requires_an_email():
    with pytest.raises(ValueError, match="requires an email"):
        User.objects.create_user(email="")


def test_invalid_email_is_rejected():
    with pytest.raises(ValidationError):
        User.objects.create_user(email="not-an-email")


# --- password handling ----------------------------------------------------


def test_password_is_hashed_never_stored_plaintext():
    user = User.objects.create_user(
        email="a@example.com", password="correct-horse-battery"
    )
    assert user.password != "correct-horse-battery"
    assert "correct-horse-battery" not in user.password
    assert user.check_password("correct-horse-battery")


def test_user_without_password_cannot_authenticate():
    """An invited or SSO-only user must not be authenticable with a blank
    password. ``set_unusable_password`` rather than an empty string."""
    user = User.objects.create_user(email="sso@example.com", password=None)
    assert not user.has_usable_password()
    assert not user.check_password("")
    assert not user.check_password(None)


def test_production_uses_argon2():
    """The test suite hashes with MD5 for speed, so the production hasher is
    asserted separately - otherwise nothing would notice if it changed."""
    from config.settings import base

    assert base.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")


# --- state and authentication edges --------------------------------------


def test_active_user_is_active():
    user = User.objects.create_user(email="a@example.com")
    assert user.is_active


@pytest.mark.parametrize("status", [UserStatus.DISABLED, UserStatus.LOCKED])
def test_inactive_user_cannot_authenticate(status):
    user = User.objects.create_user(email="a@example.com", status=status.value)
    assert not user.is_active


def test_locked_until_blocks_authentication_even_when_active():
    """Lockout is time-boxed rather than a status change, so it expires by
    itself and an attacker cannot lock a victim out permanently.

    Phase 12 (G-16): it blocks *authentication* - the sign-in service refuses
    even the right password - but not the account's live sessions, which
    ``is_active`` decides. Before, ten wrong passwords typed by anyone signed
    the owner out of everything."""
    from datetime import timedelta

    from django.utils import timezone

    from workeasy360.platform.identity import services

    user = User.objects.create_user(
        email="a@example.com", password="Correct-Horse-9-Battery"
    )
    user.locked_until = timezone.now() + timedelta(minutes=15)
    user.save()

    assert user.is_locked_out
    assert user.is_active
    outcome = services.authenticate("a@example.com", "Correct-Horse-9-Battery")
    assert not outcome.success and outcome.reason == "account_locked"

    user.locked_until = timezone.now() - timedelta(minutes=1)
    user.save()
    assert not user.is_locked_out
    assert user.is_active


def test_authenticate_rejects_wrong_password():
    from django.contrib.auth import authenticate

    User.objects.create_user(email="a@example.com", password="right-password-here")
    assert authenticate(username="a@example.com", password="wrong-password") is None


def test_authenticate_rejects_disabled_user():
    """A disabled account must not authenticate even with the right password.

    Django's ModelBackend consults ``is_active``, which we derive from status.
    """
    from django.contrib.auth import authenticate

    User.objects.create_user(
        email="a@example.com",
        password="right-password-here",
        status=UserStatus.DISABLED.value,
    )
    assert authenticate(username="a@example.com", password="right-password-here") is None


def test_authenticate_succeeds_for_active_user():
    from django.contrib.auth import authenticate

    user = User.objects.create_user(email="a@example.com", password="right-password-here")
    assert authenticate(username="a@example.com", password="right-password-here") == user


def test_session_epoch_bump_is_the_revocation_mechanism():
    """Immediate revocation without enumerating sessions.

    Sessions carry the epoch they were issued under; bumping it invalidates
    all of them at once, which is why sessions are server-side rather than
    stateless tokens (04-identity-authz.md section 3.2).
    """
    user = User.objects.create_user(email="a@example.com")
    before = user.session_epoch
    user.revoke_sessions()
    assert user.session_epoch == before + 1


# --- identity is not membership ------------------------------------------


def test_user_has_no_organization_fields():
    """Identity and organization membership are separate concepts.

    Merging them is what makes multi-org impossible later: one person, one
    account, many organizations (ADR-002/ADR-003).
    """
    field_names = {f.name for f in User._meta.get_fields()}
    for forbidden in ("organization", "organization_id", "role", "is_owner", "tenant"):
        assert forbidden not in field_names, (
            f"User.{forbidden} would conflate identity with membership."
        )


def test_user_is_not_tenant_scoped():
    """A User is global; RLS cannot and should not protect it."""
    from workeasy360.platform.tenancy.models import TenantScopedModel

    assert not issubclass(User, TenantScopedModel)


# --- MFA readiness --------------------------------------------------------

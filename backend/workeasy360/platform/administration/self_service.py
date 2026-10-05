"""Self-service accounts: signup, email verification, password reset (Phase 9).

**One identity, from the first minute.** Signup creates the one ``User`` the
person will keep: an Explorer today, a Customer once their organization is
entitled to something. Nothing is ever moved between tables.

**What an anonymous caller can learn: nothing about who is registered.**

- **Signup always answers the same.** A new address gets a verification link.
  An address that already has an account gets a "you already have an account"
  email instead. A pending signup gets its link again.
- **The work is equalised.** Every path hashes the offered password once, so
  the response time does not say which path ran.
- **Password reset always answers the same too.**

**Verification needs the link and the password together**
(:func:`verify_email`). The link proves the mailbox; the password proves the
person who chose it. Suppose someone signs up with another person's address,
and that person clicks the link they never asked for. The account is still
not activated with the stranger's password, because the owner of the mailbox
does not know it (pre-account-hijacking). The mailbox owner's way in is a
password reset, which replaces that password.

**Links** are ``identity.tokens``:
- 256 random bits; only the SHA-256 is stored.
- Single use; the latest link of a purpose retires the earlier ones.
- Sent in a sealed notification, in the URL fragment.

**Accounts that never verify are pruned** after ``PENDING_ACCOUNT_TTL_DAYS``,
so an address cannot be squatted indefinitely.

Composes ``identity`` (P1), ``organizations`` (P1), ``onboarding`` (P3, the
organization a signup asked for) and ``notifications`` (P3), which is why it
lives at P4 beside the invitation flow it mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.errors import (
    AccountLinkInvalid,
    CredentialsInvalid,
    OrganizationValidationError,
    SignupClosed,
    SignupValidationError,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MAX_NAME = 100
MAX_COMPANY = 150

VERIFY_TEMPLATE = "platform.account.verify_email"
EXISTING_TEMPLATE = "platform.account.signup_existing"
RESET_TEMPLATE = "platform.account.password_reset"
RESET_DONE_TEMPLATE = "platform.account.password_changed"


def _verify_ttl() -> timedelta:
    return timedelta(hours=max(1, int(settings.ACCOUNT_VERIFY_TTL_HOURS)))


def _reset_ttl() -> timedelta:
    return timedelta(minutes=max(5, int(settings.PASSWORD_RESET_TTL_MINUTES)))


def _describe(ttl: timedelta) -> str:
    minutes = int(ttl.total_seconds() // 60)
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" + ("" if hours == 1 else "s")
    return f"{minutes} minutes"


def _url(path: str, token: str | None = None) -> str:
    base = f"{settings.W360_FRONTEND_URL}{path}"
    return f"{base}#token={token}" if token else base


def _clean_name(raw: Any, field: str, label: str) -> str:
    value = " ".join(str(raw or "").split())
    if not value:
        raise SignupValidationError(f"Enter your {label}.", field=field)
    if len(value) > MAX_NAME or any(ord(ch) < 32 for ch in value):
        raise SignupValidationError(
            f"Your {label} must be at most {MAX_NAME} characters.", field=field
        )
    return value


def _clean_company(raw: Any) -> str:
    from workeasy360.platform.organizations.services import validate_name

    value = " ".join(str(raw or "").split())
    if not value:
        return ""
    try:
        return str(validate_name(value))[:MAX_COMPANY]
    except OrganizationValidationError as exc:
        raise SignupValidationError(exc.message, field="company_name") from exc


def _clean_email(raw: Any) -> tuple[str, str]:
    """``(as typed, normalised)``."""
    from workeasy360.platform.identity.email import is_plausible_email, normalize_email

    typed = str(raw or "").strip()
    address = normalize_email(typed)
    if not typed or len(typed) > 254 or not is_plausible_email(address):
        raise SignupValidationError("Enter a valid email address.", field="email")
    return typed, address


# --- signup -------------------------------------------------------------------


def sign_up(
    *,
    first_name: Any,
    last_name: Any,
    email: Any,
    password: Any,
    company_name: Any = None,
) -> str:
    """Start a signup. Returns the outcome for metrics and tests only - the
    HTTP answer is the same for every one of them.

    Validation runs first and identically for every address (a weak password
    is refused whether or not the address is registered), so a validation
    answer is never a registration oracle either.
    """
    from workeasy360.platform.administration.site import signup_enabled
    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.identity.account import check_new_password
    from workeasy360.platform.identity.models import User

    if not signup_enabled():
        raise SignupClosed("Signup is closed at the moment. Try again later.")
    first = _clean_name(first_name, "first_name", "first name")
    last = _clean_name(last_name, "last_name", "last name")
    typed, address = _clean_email(email)
    company = _clean_company(company_name)
    secret = password if isinstance(password, str) else ""
    full_name = f"{first} {last}"
    check_new_password(secret, user=User(email=address, full_name=full_name))

    with transaction.atomic():
        existing = lifecycle.lock_by_email(address)
        if existing is None:
            try:
                with transaction.atomic():
                    user = lifecycle.create_account(
                        email=typed,
                        password=secret,
                        full_name=full_name,
                        status=UserStatus.PENDING,
                        source=User.AcquisitionSource.SELF_SIGNUP,
                    )
            except IntegrityError:
                # The same address signed up a moment ago in another request;
                # that request sent the link. Same answer to this one.
                make_password(secret)
                outcome = "race"
            else:
                _send_verification(user, organization_name=company)
                outcome = "created"
        elif existing.status == UserStatus.PENDING.value:
            # Not verified yet: send the link again. The password stays the one
            # chosen first - replacing it here would let anyone who knows the
            # address change what the real signer must type.
            make_password(secret)
            _send_verification(existing, organization_name=company)
            outcome = "resent"
        elif existing.status == UserStatus.ACTIVE.value:
            make_password(secret)
            _send_existing_notice(existing)
            outcome = "existing"
        else:
            make_password(secret)
            outcome = "ignored"
    metrics.increment("identity_signups_total", outcome=outcome)
    logger.info("identity.signup", outcome=outcome)
    return outcome


def _send_verification(user: Any, *, organization_name: str) -> None:
    from workeasy360.platform.identity import tokens
    from workeasy360.platform.identity.models import AccountToken
    from workeasy360.platform.notifications.services import notify_account

    token = tokens.issue(
        user.pk,
        AccountToken.Purpose.VERIFY_EMAIL,
        ttl=_verify_ttl(),
        organization_name=organization_name,
    )
    first_name = (user.full_name or "").split(" ", 1)[0] or "there"
    notify_account(
        template_key=VERIFY_TEMPLATE,
        recipient_email=user.email_original or user.email,
        context={"name": first_name, "expires_in": _describe(_verify_ttl())},
        sealed_context={"verify_url": _url("/verify-email", token)},
        idempotency_key=f"account:{user.pk}:verify:{_hash_prefix(token)}",
    )


def _send_existing_notice(user: Any) -> None:
    """At most one "you already have an account" email per account per hour,
    whatever the signup form is fed: the idempotency key is the hour."""
    from workeasy360.platform.notifications.services import notify_account

    hour = timezone.now().strftime("%Y%m%d%H")
    notify_account(
        template_key=EXISTING_TEMPLATE,
        recipient_email=user.email_original or user.email,
        context={
            "sign_in_url": _url("/login"),
            "reset_url": _url("/forgot-password"),
        },
        idempotency_key=f"account:{user.pk}:signup-existing:{hour}",
    )


def _hash_prefix(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def resend_verification(email: Any) -> None:
    """Send a pending account its verification link again. Silent for every
    other address, so it is not a registration oracle."""
    from workeasy360.platform.identity import lifecycle, tokens
    from workeasy360.platform.identity.email import normalize_email

    address = normalize_email(str(email or ""))
    if not address:
        return
    with transaction.atomic():
        user = lifecycle.lock_by_email(address, statuses=(UserStatus.PENDING.value,))
        if user is None:
            return
        # The company the person asked for survives a resend.
        _send_verification(user, organization_name=tokens.requested_organization(user.pk))
    metrics.increment("identity_verification_resent_total")


# --- verification ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Verified:
    user: Any
    organization_slug: str | None
    organization_id: str | None = None


def verify_email(token: Any, password: Any) -> Verified:
    """Redeem a verification link with the account's password.

    A wrong password counts toward the login lockout, like any failed sign-in,
    and leaves the link usable for another try. Success activates the account
    and verifies the address. If the signup asked for a company, it creates
    the organization with this person as owner (idempotently, keyed on the
    link). The view then signs the person in.
    """
    from workeasy360.platform.identity import lifecycle, tokens
    from workeasy360.platform.identity.account import record_account_event
    from workeasy360.platform.identity.models import AccountEvent, AccountToken
    from workeasy360.platform.identity.services import _record_failure

    secret = password if isinstance(password, str) else ""
    with transaction.atomic():
        row = tokens.open_token(token, AccountToken.Purpose.VERIFY_EMAIL)
        user = row.user
    if user.status not in (UserStatus.PENDING.value, UserStatus.ACTIVE.value):
        raise AccountLinkInvalid("This link is not valid.")
    if user.is_locked_out:
        raise CredentialsInvalid(
            "Too many attempts. Wait a few minutes, then try again.",
            reason="account_locked",
        )
    # Checked outside the transaction below, so the failure count it records
    # survives the error it raises.
    if not user.check_password(secret):
        _record_failure(user)
        raise CredentialsInvalid("That is not the password for this account.")

    organization_name = ""
    with transaction.atomic():
        row = tokens.open_token(token, AccountToken.Purpose.VERIFY_EMAIL)
        user = lifecycle.lock(row.user_id)
        tokens.consume(row)
        lifecycle.activate_verified(user)
        record_account_event(user.pk, AccountEvent.Kind.EMAIL_VERIFIED)
        organization_name = row.organization_name
        token_id = row.pk
    created = _create_requested_organization(user, organization_name, key=str(token_id))
    metrics.increment("identity_email_verified_total")
    return Verified(
        user=user,
        organization_slug=created[0] if created else None,
        organization_id=created[1] if created else None,
    )


def _create_requested_organization(
    user: Any, name: str, *, key: str
) -> tuple[str, str] | None:
    """The organization the signup asked for, or ``None``. Best effort: the
    account is already verified, and a failure here leaves the person on the
    create-organization page, never unverified."""
    from workeasy360.platform.onboarding.services import create_organization

    if not name:
        return None
    try:
        result = create_organization(
            user_id=user.pk, name=name, idempotency_key=f"signup:{key}"
        )
    except Exception as exc:
        logger.warning(
            "identity.signup.organization_failed", error_type=type(exc).__name__
        )
        return None
    return str(result.slug), str(result.organization_id)


# --- password reset -------------------------------------------------------------


def request_password_reset(email: Any) -> None:
    """Email a reset link if the address has an account that may use one.
    Silent otherwise - the HTTP answer is always the same."""
    from workeasy360.contracts.values.control_plane import PLATFORM_SYSTEM_USER_EMAIL
    from workeasy360.platform.identity import lifecycle, tokens
    from workeasy360.platform.identity.email import normalize_email
    from workeasy360.platform.identity.models import AccountToken
    from workeasy360.platform.notifications.services import notify_account

    address = normalize_email(str(email or ""))
    if not address or address == PLATFORM_SYSTEM_USER_EMAIL:
        return
    with transaction.atomic():
        user = lifecycle.lock_by_email(
            address, statuses=(UserStatus.ACTIVE.value, UserStatus.PENDING.value)
        )
        if user is None:
            metrics.increment("identity_password_reset_requests_total", outcome="none")
            return
        token = tokens.issue(
            user.pk, AccountToken.Purpose.RESET_PASSWORD, ttl=_reset_ttl()
        )
        notify_account(
            template_key=RESET_TEMPLATE,
            recipient_email=user.email_original or user.email,
            context={"expires_in": _describe(_reset_ttl())},
            sealed_context={"reset_url": _url("/reset-password", token)},
            idempotency_key=f"account:{user.pk}:reset:{_hash_prefix(token)}",
        )
    metrics.increment("identity_password_reset_requests_total", outcome="sent")


def reset_password(token: Any, new_password: Any) -> Any:
    """Set a new password from a reset link.

    The link proves the mailbox, so a successful reset also:
    - verifies the address;
    - activates a pending signup, replacing whatever password it was
      registered with;
    - clears the sign-in lockouts;
    - ends every session everywhere.

    The platform validators and the strictest password policy of the person's
    organizations apply. A notice goes to the address afterwards.
    """
    from workeasy360.platform.administration.site import support_email
    from workeasy360.platform.identity import lifecycle, tokens
    from workeasy360.platform.identity.account import (
        check_new_password,
        record_account_event,
    )
    from workeasy360.platform.identity.models import AccountEvent, AccountToken
    from workeasy360.platform.notifications.services import notify_account
    from workeasy360.platform.organizations.selectors import (
        password_min_lengths_for_user,
    )

    secret = new_password if isinstance(new_password, str) else ""
    with transaction.atomic():
        row = tokens.open_token(token, AccountToken.Purpose.RESET_PASSWORD)
        user = row.user
    if user.status not in (UserStatus.ACTIVE.value, UserStatus.PENDING.value):
        raise AccountLinkInvalid("This link is not valid.")
    minimum = max(password_min_lengths_for_user(user.pk).values(), default=None)
    check_new_password(secret, user=user, min_length=minimum)

    with transaction.atomic():
        row = tokens.open_token(token, AccountToken.Purpose.RESET_PASSWORD)
        user = lifecycle.lock(row.user_id)
        tokens.consume(row)
        tokens.spend_all(user.pk)
        lifecycle.reset_credentials(user, secret)
        record_account_event(user.pk, AccountEvent.Kind.PASSWORD_RESET)
        notify_account(
            template_key=RESET_DONE_TEMPLATE,
            recipient_email=user.email_original or user.email,
            context={"support_email": support_email()},
            idempotency_key=f"account:{user.pk}:reset-done:{row.pk}",
        )
    metrics.increment("identity_password_resets_total")
    logger.info("identity.password_reset", user_id=str(user.pk))
    return user


def prune_pending_accounts(
    *, older_than_days: int | None = None, batch: int = 500
) -> int:
    """Delete signups that never verified. They hold nothing (a pending
    account cannot sign in, join or buy), and deleting them frees the address.

    One savepoint per account: a pending address a partner has since named as
    an organization's owner is protected by that membership and is skipped,
    not allowed to stop the sweep."""
    from django.db.models import ProtectedError

    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.identity.models import User

    days = int(
        older_than_days
        if older_than_days is not None
        else settings.PENDING_ACCOUNT_TTL_DAYS
    )
    cutoff = timezone.now() - timedelta(days=max(1, days))
    candidates = list(
        User.objects.filter(
            status=UserStatus.PENDING.value, created_at__lt=cutoff
        ).values_list("id", flat=True)[:batch]
    )
    deleted = 0
    for user_id in candidates:
        try:
            with transaction.atomic():
                deleted += int(lifecycle.delete_pending(user_id))
        except (ProtectedError, IntegrityError):
            continue
    return deleted

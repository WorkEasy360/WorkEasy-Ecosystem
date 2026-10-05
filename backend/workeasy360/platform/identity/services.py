"""Session authentication: login and its failure accounting. Phase 4, Task 4.

**Pre-tenant, deliberately.** Every write function elsewhere in this
codebase takes a ``TenantContext`` first; nothing here can, because signing
in is how a ``TenantContext`` eventually comes to exist. This module knows
nothing about organizations, memberships or sessions - it answers exactly
one question, "are these credentials good, and if not, why not", and returns
a result the view applies to the actual Django session (which only the view
has - F-7's separation of a service from the transport it is reached
through, at its narrowest).

**Failure accounting reuses fields Phase 1 already modelled**
(``User.failed_login_count``, ``User.locked_until``) rather than inventing a
parallel counter. Lockout is time-boxed, matching the model's own
documented intent ("time-boxed with jitter rather than permanent, so an
attacker cannot lock a victim out at will") - jitter is not added here
because it would only matter against an attacker timing the *unlock*
precisely, which a fixed window already defeats by being minutes long.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

#: Failures before an account locks. Ten is generous enough that a genuine
#: user fumbling a password twice never sees it, and tight enough that an
#: offline-unthrottled guesser gains nothing - the real defence against
#: credential stuffing is Argon2id (12-security.md section 3), not this
#: counter; this counter exists for the *targeted* guesser.
MAX_FAILED_ATTEMPTS = 10

#: How long a lockout lasts. Short enough that a locked-out legitimate user
#: is not effectively told to contact support, long enough that it is not a
#: rate limit an automated guesser simply waits out and resumes.
LOCKOUT_MINUTES = 15


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """The answer to "may this email/password pair sign in".

    ``reason`` is a closed set an API can map to copy without inventing a
    branch per call site: ``invalid_credentials`` | ``account_locked`` |
    ``account_disabled`` | ``email_not_verified`` (Phase 9: right password, the
    address was never verified). Distinguishing them is Task 4's explicit
    requirement ("authentication errors, disabled account") - an accepted,
    deliberate trade against the stricter enumeration-resistance the OIDC
    boundary holds itself to (ecosystem/protocol.py's uniform failures),
    because a locked-out legitimate user who is only ever told "wrong
    password" has no path back in without contacting support, and Task 4
    asks for exactly the state this reveals.
    """

    success: bool
    user: Any = None
    reason: str = ""
    #: The account a *failed* attempt was made against, when one exists -
    #: for that person's own security trail (Phase 8 extension). Never
    #: returned to the caller of the login endpoint.
    subject_id: Any = None


def authenticate(email: str, password: str) -> LoginOutcome:
    """Verify credentials. Does not touch a session - see the module
    docstring."""
    from django.contrib.auth import authenticate as django_authenticate

    from workeasy360.contracts.enums import UserStatus
    from workeasy360.platform.identity.email import normalize_email
    from workeasy360.platform.identity.models import User

    normalized = normalize_email(email or "")
    row = User.objects.filter(email=normalized).first()

    # Checked, and refused, before a password comparison is attempted: a
    # locked or disabled account gains nothing from confirming whether the
    # password was even right, and this is the one place in the platform
    # where telling the person *why* is the explicit product requirement
    # rather than a leak to guard against.
    subject_id = row.pk if row is not None else None
    if row is not None and row.is_locked_out:
        return LoginOutcome(success=False, reason="account_locked", subject_id=subject_id)
    if row is not None and row.status == UserStatus.PENDING.value:
        # A self-service signup that has not verified its address (Phase 9).
        # Said only to someone who knows the password: to anyone else a
        # pending account is indistinguishable from a wrong password, so
        # "which addresses have started signing up" is not an oracle.
        if row.check_password(password or ""):
            return LoginOutcome(
                success=False, reason="email_not_verified", subject_id=subject_id
            )
        _record_failure(row)
        return LoginOutcome(
            success=False, reason="invalid_credentials", subject_id=subject_id
        )
    if row is not None and not UserStatus(row.status).can_authenticate:
        return LoginOutcome(
            success=False, reason="account_disabled", subject_id=subject_id
        )

    user = django_authenticate(username=normalized, password=password)
    if user is None:
        if row is not None:
            _record_failure(row)
        return LoginOutcome(
            success=False, reason="invalid_credentials", subject_id=subject_id
        )

    if row is not None and (row.failed_login_count or row.locked_until):
        row.failed_login_count = 0
        row.locked_until = None
        row.save(update_fields=["failed_login_count", "locked_until"])

    return LoginOutcome(success=True, user=user)


def _record_failure(user: Any) -> None:
    from django.utils import timezone

    user.failed_login_count += 1
    if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
        user.locked_until = timezone.now() + timedelta(minutes=LOCKOUT_MINUTES)
    user.save(update_fields=["failed_login_count", "locked_until"])


# --- MFA (Phase 5, Task 37-39) ---------------------------------------------
#
# Pre-tenant, like the rest of this module: AuthFactor belongs to User, a
# global identity, not to any organization. Step-up (turning a verified code
# into aal=2 on the *session*) is the view's job, exactly as establishing the
# session itself is - see interfaces/api/v1/mfa_views.py.


@dataclass(frozen=True, slots=True)
class TotpEnrollmentStart:
    factor_id: Any
    secret_base32: str
    provisioning_uri: str


def begin_totp_enrollment(user_id: Any) -> TotpEnrollmentStart:
    """Start enrolling a TOTP factor. The raw secret is returned exactly
    once, here - it is never retrievable again, including by this service."""
    from workeasy360.platform.identity import mfa
    from workeasy360.platform.identity.models import AuthFactor, User

    user = User.objects.get(pk=user_id)

    # Abandoned enrollments (started, never confirmed) do not accumulate -
    # each new attempt replaces the last unconfirmed one for the same kind.
    AuthFactor.objects.filter(
        user_id=user_id, kind=AuthFactor.Kind.TOTP, confirmed_at__isnull=True
    ).delete()

    label = "Authenticator app"
    if AuthFactor.objects.filter(user_id=user_id, kind=AuthFactor.Kind.TOTP).exists():
        # A confirmed factor already exists (re-enrollment, e.g. new phone).
        # Distinct label avoids uq_factor_user_kind_label; the old factor is
        # left alone until the caller explicitly removes it (Task 38 - no
        # implicit replacement of a working second factor).
        import uuid as _uuid

        label = f"Authenticator app ({_uuid.uuid4().hex[:4]})"

    raw_secret = mfa.generate_totp_secret()
    factor = AuthFactor.objects.create(
        user_id=user_id,
        kind=AuthFactor.Kind.TOTP,
        label=label,
        secret_ref=mfa.encrypt_totp_secret(raw_secret),
    )
    return TotpEnrollmentStart(
        factor_id=factor.id,
        secret_base32=mfa.base32_secret(raw_secret),
        provisioning_uri=mfa.provisioning_uri(
            secret=raw_secret, account_email=user.email
        ),
    )


def confirm_totp_enrollment(user_id: Any, factor_id: Any, code: str) -> list[str]:
    """Verify the first code and activate the factor.

    Returns freshly generated recovery codes in **plaintext, once** (Task
    38): only their hash is ever stored, so this is the only response that
    will ever contain them - the caller must show them to the user now.
    """
    from workeasy360.contracts.errors import MfaCodeInvalid, MfaEnrollmentNotFound
    from workeasy360.platform.identity import mfa
    from workeasy360.platform.identity.models import AuthFactor

    factor = AuthFactor.objects.filter(
        id=factor_id,
        user_id=user_id,
        kind=AuthFactor.Kind.TOTP,
        confirmed_at__isnull=True,
    ).first()
    if factor is None:
        raise MfaEnrollmentNotFound("No pending TOTP enrollment for this account.")

    raw_secret = mfa.decrypt_totp_secret(factor.secret_ref)
    step = mfa.match_totp_step(raw_secret, code)
    if step is None:
        raise MfaCodeInvalid("The code did not match. Check the time on your device.")

    from django.db import transaction
    from django.utils import timezone

    with transaction.atomic():
        # Conditional on still being unconfirmed: of two concurrent confirms,
        # exactly one activates the factor and issues recovery codes. The
        # step is recorded so the confirming code cannot then be replayed
        # for step-up.
        activated = AuthFactor.objects.filter(
            id=factor.id, confirmed_at__isnull=True
        ).update(confirmed_at=timezone.now(), sign_count=step)
        if not activated:
            raise MfaEnrollmentNotFound("No pending TOTP enrollment for this account.")
        codes = _issue_recovery_codes(user_id)
    _note_staff_enrollment(user_id, factor.id)
    return codes


def _note_staff_enrollment(user_id: Any, factor_id: Any) -> None:
    """A staff identity enrolling an authenticator is a security event (Phase
    12, D-037): whoever holds the factor holds the control plane. Logged at
    warning level for the log-based alert (08-observability-and-alerts.md);
    identifiers only."""
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.observability.logging import get_logger

    if User.objects.filter(id=user_id, is_staff=True).exists():
        get_logger(__name__).warning(
            "identity.mfa.staff_factor_enrolled",
            user_id=str(user_id),
            factor_id=str(factor_id),
        )


def _issue_recovery_codes(user_id: Any) -> list[str]:
    """Replace any existing recovery codes with a fresh set of 10.

    Called once, right after the first factor is confirmed. Re-issuing
    (rather than accumulating) means a user can always tell "how many do I
    have left" from a single count, and an old, possibly-exposed batch stops
    working the moment a new one is generated.
    """
    from workeasy360.platform.identity import mfa
    from workeasy360.platform.identity.models import AuthFactor

    AuthFactor.objects.filter(
        user_id=user_id, kind=AuthFactor.Kind.RECOVERY_CODE
    ).delete()

    plaintext_codes = mfa.generate_recovery_codes()
    from django.utils import timezone

    now = timezone.now()
    AuthFactor.objects.bulk_create(
        [
            AuthFactor(
                user_id=user_id,
                kind=AuthFactor.Kind.RECOVERY_CODE,
                label=f"Recovery code {index + 1}",
                secret_ref=mfa.hash_recovery_code(code),
                confirmed_at=now,
            )
            for index, code in enumerate(plaintext_codes)
        ]
    )
    return plaintext_codes


#: Consecutive failed step-up codes allowed before the account backs off.
#: Above the per-minute bucket (``MFA_STEP_UP_RATE_LIMITS``, 8), so ordinary
#: fumbling never meets it.
MFA_FREE_FAILURES = 10
#: The lock earned by the first failure past the allowance; it doubles with
#: each further consecutive failure, up to ``MFA_MAX_LOCK``.
MFA_FIRST_LOCK = timedelta(minutes=1)
MFA_MAX_LOCK = timedelta(hours=24)


def mfa_lock_duration(failures: int) -> timedelta | None:
    """How long step-up stays locked after ``failures`` consecutive failures.

    Why the per-minute bucket alone was not enough: it resets every minute,
    so it allowed ~11,500 guesses a day, indefinitely. Against a 6-digit code
    with a three-step window that is AAL2 in weeks for anyone holding only
    the password. With this backoff an attacker gets about 20 guesses on day
    one and about one a day after that; a success clears it. Capped rather
    than unbounded because only a password holder can trigger it, and the
    real owner must be able to step up again within a day.
    """
    over = failures - MFA_FREE_FAILURES
    if over <= 0:
        return None
    # The exponent is bounded before it is used, so a huge count cannot
    # overflow; 2**20 minutes is far past the cap anyway.
    return min(MFA_FIRST_LOCK * 2 ** min(over - 1, 20), MFA_MAX_LOCK)


def _reserve_step_up_attempt(user_id: Any) -> None:
    """Count this attempt before its code is looked at, or refuse it.

    One conditional ``UPDATE``, so the database decides (the same rule as
    code acceptance below): no attempt proceeds while the account is locked,
    whichever instance it reaches, and every attempt that does proceed is
    counted exactly once - also under concurrency, where each ``UPDATE``
    sees the count the previous one left. The new lock is computed in the
    same statement from the count being replaced, so there is no read that
    could go stale. Counting first also fails closed: a request that dies
    half-way stays counted. A success clears the count again.

    While locked, even a correct code is refused unevaluated, so the lock
    cannot be used as an oracle.
    """
    from django.db.models import Case, DateTimeField, F, Q, Value, When
    from django.utils import timezone

    from workeasy360.contracts.errors import MfaThrottled
    from workeasy360.platform.identity.models import User

    now = timezone.now()
    schedule = []
    failures = MFA_FREE_FAILURES + 1
    while (
        duration := mfa_lock_duration(failures)
    ) is not None and duration < MFA_MAX_LOCK:
        schedule.append(
            When(mfa_failed_attempts=failures - 1, then=Value(now + duration))
        )
        failures += 1
    lock_after_this_attempt = Case(
        *schedule,
        When(mfa_failed_attempts__gte=failures - 1, then=Value(now + MFA_MAX_LOCK)),
        default=Value(None),
        output_field=DateTimeField(),
    )

    reserved = (
        User.objects.filter(pk=user_id)
        .filter(Q(mfa_locked_until__isnull=True) | Q(mfa_locked_until__lte=now))
        .update(
            mfa_failed_attempts=F("mfa_failed_attempts") + 1,
            mfa_locked_until=lock_after_this_attempt,
        )
    )
    if reserved:
        return

    locked_until = (
        User.objects.filter(pk=user_id).values_list("mfa_locked_until", flat=True).first()
    )
    remaining = (locked_until - now).total_seconds() if locked_until else 1
    raise MfaThrottled(retry_after=int(remaining) + 1)


def _clear_step_up_failures(user_id: Any) -> None:
    from django.db.models import Q

    from workeasy360.platform.identity.models import User

    User.objects.filter(pk=user_id).filter(
        Q(mfa_failed_attempts__gt=0) | Q(mfa_locked_until__isnull=False)
    ).update(mfa_failed_attempts=0, mfa_locked_until=None)


def verify_step_up_code(user_id: Any, code: str) -> bool:
    """Does ``code`` satisfy AAL2 for this user - a confirmed TOTP factor,
    or an unused recovery code (consumed on success)?

    Raises ``MfaThrottled`` without evaluating the code while the account is
    locked by consecutive failures (``_reserve_step_up_attempt``).

    Tries every confirmed TOTP factor (there is normally one; re-enrollment
    can briefly leave two) before falling back to recovery codes, so a user
    is never asked which factor they meant.

    **Every acceptance is a conditional write, decided by the database.** A
    TOTP code is accepted only by moving the factor's last-accepted step
    (``sign_count``) forward, and a recovery code only by deleting its row;
    of two concurrent requests presenting the same code, the second's write
    matches zero rows, so it is refused (RFC 6238 section 5.2 - no OTP is
    accepted twice - and single use for recovery codes).
    """
    _reserve_step_up_attempt(user_id)
    if _code_verifies(user_id, code):
        _clear_step_up_failures(user_id)
        return True
    return False


def _code_verifies(user_id: Any, code: str) -> bool:
    from django.utils import timezone

    from workeasy360.platform.identity import mfa
    from workeasy360.platform.identity.models import AuthFactor

    code = (code or "").strip()
    if not code:
        return False

    for factor in AuthFactor.objects.filter(
        user_id=user_id, kind=AuthFactor.Kind.TOTP, confirmed_at__isnull=False
    ):
        raw_secret = mfa.decrypt_totp_secret(factor.secret_ref)
        step = mfa.match_totp_step(raw_secret, code)
        if step is None:
            continue
        accepted = AuthFactor.objects.filter(id=factor.id, sign_count__lt=step).update(
            sign_count=step, last_used_at=timezone.now()
        )
        if accepted:
            return True

    consumed, _ = AuthFactor.objects.filter(
        user_id=user_id,
        kind=AuthFactor.Kind.RECOVERY_CODE,
        secret_ref=mfa.hash_recovery_code(code),
    ).delete()
    return consumed > 0


def list_mfa_factors(user_id: Any) -> list[dict[str, Any]]:
    from workeasy360.platform.identity.models import AuthFactor

    factors = AuthFactor.objects.filter(user_id=user_id).order_by("kind", "created_at")
    return [
        {
            "factor_id": str(f.id),
            "kind": f.kind,
            "label": f.label,
            "confirmed": f.confirmed_at is not None,
            "created_at": f.created_at.isoformat(),
            "last_used_at": f.last_used_at.isoformat() if f.last_used_at else None,
        }
        for f in factors
        if f.kind != AuthFactor.Kind.RECOVERY_CODE  # counted, not listed individually
    ] + _recovery_code_summary(user_id)


def _recovery_code_summary(user_id: Any) -> list[dict[str, Any]]:
    from workeasy360.platform.identity.models import AuthFactor

    remaining = AuthFactor.objects.filter(
        user_id=user_id, kind=AuthFactor.Kind.RECOVERY_CODE
    ).count()
    if remaining == 0:
        return []
    return [
        {
            "factor_id": None,
            "kind": AuthFactor.Kind.RECOVERY_CODE.value,
            "label": f"{remaining} recovery code(s) remaining",
            "confirmed": True,
            "created_at": None,
            "last_used_at": None,
        }
    ]


def remove_mfa_factor(user_id: Any, factor_id: Any) -> None:
    """Remove one enrolled factor. The caller (mfa_views) requires the
    *current session* to already be AAL2 before reaching this - removing a
    factor is exactly the kind of action Task 38 means by "dangerous reset
    should require an appropriate trusted process", and "already completed
    step-up this session" is that process. There is no other recovery path
    (e.g. email-based reset) in this phase - see docs/phase-5/00-overview.md for the
    explicit statement of that limitation.
    """
    from django.db import transaction

    from workeasy360.platform.identity.models import AuthFactor

    with transaction.atomic():
        AuthFactor.objects.filter(id=factor_id, user_id=user_id).delete()
        # Recovery codes back up a factor; with no factor left they would be
        # a standing way to reach AAL2 with nothing to recover. Removing the
        # last factor therefore removes them too.
        if not AuthFactor.objects.filter(
            user_id=user_id,
            kind__in=[AuthFactor.Kind.TOTP, AuthFactor.Kind.WEBAUTHN],
            confirmed_at__isnull=False,
        ).exists():
            AuthFactor.objects.filter(
                user_id=user_id, kind=AuthFactor.Kind.RECOVERY_CODE
            ).delete()


def ensure_identity_for_email(email: str) -> tuple[Any, bool]:
    """The account for ``email``, creating an **uncredentialed** one if absent.
    Returns ``(user_id, created)``; ``None`` for the id if the address belongs
    to an account that may not be assigned anything (disabled or locked).

    For a privileged caller that must name a person - a partner provisioning a
    customer identifies its owner by address - before that person has ever
    signed in. The identity it creates has an **unusable password** and an
    unverified address: it cannot authenticate, and holds nothing, until its
    real owner sets a password through the emailed link, which proves the
    mailbox (a partner-provisioned customer owner: ``partners.activation``;
    a partner's own administrator: the setup link).
    Creating it is not granting anything.

    Race-safe: two callers naming a new address at once both end with the one
    row the unique index allows.
    """
    from django.db import IntegrityError, transaction

    from workeasy360.platform.identity.email import (
        is_plausible_email,
        normalize_email,
    )
    from workeasy360.platform.identity.models import User

    address = normalize_email(email or "")
    if not is_plausible_email(address):
        return None, False

    # A pending (unverified self-service) account is assignable too: it cannot
    # sign in, and its real owner completes it through a password reset or an
    # invitation, either of which proves the mailbox and replaces the password
    # it was registered with. Refusing it would let anyone block a partner
    # from naming a customer by pre-registering the address (Phase 9).
    assignable = ("active", "pending")
    existing = User.objects.filter(email=address).first()
    if existing is not None:
        return (existing.id if existing.status in assignable else None), False
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email.strip(),
                password=None,
                acquisition_source=User.AcquisitionSource.PARTNER,
            )
        return user.id, True
    except IntegrityError:
        existing = User.objects.filter(email=address).first()
        if existing is None:
            raise
        return (existing.id if existing.status in assignable else None), False

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


def ensure_identity_for_email(email: str, *, full_name: str = "") -> tuple[Any, bool]:
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

    ``full_name`` is the name the caller was told for this person. It is used
    **only when this call creates the account** - an existing account's name is
    its owner's to change and is never overwritten by whoever names them.

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
                full_name=full_name.strip()[:255],
            )
        return user.id, True
    except IntegrityError:
        existing = User.objects.filter(email=address).first()
        if existing is None:
            raise
        return (existing.id if existing.status in assignable else None), False

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
    ``account_disabled``. Distinguishing them is Task 4's explicit
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
    if row is not None and row.is_locked_out:
        return LoginOutcome(success=False, reason="account_locked")
    if row is not None and not UserStatus(row.status).can_authenticate:
        return LoginOutcome(success=False, reason="account_disabled")

    user = django_authenticate(username=normalized, password=password)
    if user is None:
        if row is not None:
            _record_failure(row)
        return LoginOutcome(success=False, reason="invalid_credentials")

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

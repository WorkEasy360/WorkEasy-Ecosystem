"""Account lifecycle writes: the one place outside migrations that creates a
``User``, activates or verifies it, sets a password on the person's behalf,
changes its status or staff designation, or deletes it (Phase 9).

``User`` is written by ``platform/identity`` only (08-data-ownership.md). The
flows that *decide* - signup and verification, invitations, the staff user
actions, the operator commands, the control-plane anchor - live in higher
layers and call these functions to *do*, so an account's invariants hold in
one place:

- a verified address stays verified;
- setting a password from a proof of the mailbox ends every session;
- a pending account becomes active only through such a proof;
- nothing here prints, logs or returns a password.

Pure writes: no authorization, no audit entry and no notification - the
callers own those, and each function runs inside the caller's transaction.
The ``lock*`` functions exist so a caller can take the row lock first and
check its own rules (for example "not the last security administrator")
before the write it then asks for.

Enforced by ``test_only_identity_writes_the_user_table``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone

from workeasy360.contracts.enums import UserStatus

_LOCKOUT_FIELDS = ["failed_login_count", "locked_until"]


def create_account(
    *,
    email: str,
    password: str | None,
    full_name: str = "",
    status: UserStatus = UserStatus.ACTIVE,
    source: str = "",
    verified: bool = False,
) -> Any:
    """A new account. ``password=None`` creates one that cannot sign in until
    its owner sets a password through a proof of the mailbox. Raises
    ``IntegrityError`` if the address was taken a moment ago - the caller
    decides what that means for its flow."""
    from workeasy360.platform.identity.models import User

    return User.objects.create_user(
        email=email,
        password=password,
        full_name=full_name,
        status=status.value,
        acquisition_source=source,
        email_verified_at=timezone.now() if verified else None,
    )


def lock(user_id: Any) -> Any:
    """The account, locked for the caller's transaction."""
    from workeasy360.platform.identity.models import User

    return User.objects.select_for_update().get(pk=user_id)


def lock_by_email(address: str, *, statuses: tuple[str, ...] | None = None) -> Any:
    """The account for a normalised address, locked; ``None`` if there is none
    (in one of ``statuses``, when given)."""
    from workeasy360.platform.identity.models import User

    rows = User.objects.select_for_update().filter(email=address)
    if statuses is not None:
        rows = rows.filter(status__in=statuses)
    return rows.first()


def activate_verified(user: Any, *, at: datetime | None = None) -> None:
    """The mailbox was proved and the password presented: active, verified,
    and the sign-in lockout counter cleared."""
    user.status = UserStatus.ACTIVE.value
    user.email_verified_at = user.email_verified_at or at or timezone.now()
    user.failed_login_count = 0
    user.locked_until = None
    user.save(
        update_fields=["status", "email_verified_at", *_LOCKOUT_FIELDS, "updated_at"]
    )


def complete_with_password(
    user: Any, password: str, *, full_name: str = "", activate: bool = False
) -> None:
    """Give an account its owner's first password after an emailed link proved
    the mailbox (an invitation). ``activate`` also turns a pending signup
    active - replacing the password it was registered with is exactly what
    defeats someone who pre-registered the address."""
    user.set_password(password)
    user.full_name = full_name or user.full_name
    user.email_verified_at = user.email_verified_at or timezone.now()
    fields = ["password", "full_name", "email_verified_at", "updated_at"]
    if activate:
        user.status = UserStatus.ACTIVE.value
        fields.append("status")
    user.save(update_fields=fields)


def set_temporary_password(user: Any, password: str) -> None:
    """A password someone *other than the owner* chose (an operator's demo
    account). It works once: the account is flagged so every API call but the
    password change is refused until its owner replaces it
    (``PasswordChangeRequiredMiddleware``), and every existing session ends."""
    user.set_password(password)
    user.password_change_required = True
    user.session_epoch += 1
    user.save(
        update_fields=[
            "password",
            "password_change_required",
            "session_epoch",
            "updated_at",
        ]
    )


def reset_credentials(user: Any, password: str, *, at: datetime | None = None) -> None:
    """A new password from a reset link. The link proved the mailbox, so the
    account is also verified and activated, both lockouts are cleared and
    every session everywhere ends (the epoch moves and the registry rows go)."""
    from workeasy360.platform.identity.models import UserSession

    user.set_password(password)
    user.status = UserStatus.ACTIVE.value
    user.email_verified_at = user.email_verified_at or at or timezone.now()
    user.failed_login_count = 0
    user.locked_until = None
    user.session_epoch += 1
    user.save(
        update_fields=[
            "password",
            "status",
            "email_verified_at",
            *_LOCKOUT_FIELDS,
            "session_epoch",
            "updated_at",
        ]
    )
    UserSession.objects.filter(user_id=user.pk).delete()


def clear_lockouts(user: Any) -> None:
    """Both lockouts - password and second factor - lifted."""
    user.failed_login_count = 0
    user.locked_until = None
    user.mfa_failed_attempts = 0
    user.mfa_locked_until = None
    user.save(
        update_fields=[
            *_LOCKOUT_FIELDS,
            "mfa_failed_attempts",
            "mfa_locked_until",
            "updated_at",
        ]
    )


def set_status(user: Any, status: UserStatus) -> None:
    user.status = status.value
    user.save(update_fields=["status", "updated_at"])


def reset_second_factors(user: Any) -> int:
    """Break glass (Phase 12, D-037 residual): remove every factor of the
    account - authenticator and recovery codes - and sign every session out.
    Returns the number of factor records removed. Callers decide what else a
    reset means (the staff command also removes the designation)."""
    from workeasy360.platform.identity.models import AuthFactor

    removed, _ = AuthFactor.objects.filter(user_id=user.id).delete()
    user.revoke_sessions()
    return removed


def set_staff(user: Any, enabled: bool) -> None:
    """The staff designation. It confers nothing by itself - control-plane
    power is a ``StaffGrant`` - but grants are refused to non-staff accounts."""
    if user.is_staff != enabled:
        user.is_staff = enabled
        user.save(update_fields=["is_staff", "updated_at"])


def delete_pending(user_id: Any) -> bool:
    """Delete an account that never verified; ``False`` if it is no longer
    pending. ``ProtectedError`` propagates when something now references the
    account (a partner named it an owner, say)."""
    from workeasy360.platform.identity.models import User

    _total, per_model = User.objects.filter(
        id=user_id, status=UserStatus.PENDING.value
    ).delete()
    return bool(per_model.get(User._meta.label))

"""A person's own account security: sessions, password, security trail.

Pre-tenant, like the rest of ``platform/identity``: these belong to the global
``User``. Whether a caller *may* do each thing is decided by the HTTP adapter
from the session itself (it is always the caller's own account) plus the
assurance level - revoking a session and changing a password are dangerous.

**What an organization can do to a member's sessions is not here.** An
organization administrator may end a member's access *to that organization*
(``Membership.sessions_valid_after``); only the person themselves, or Workeasy360
staff, can end a session outright, because a session is not the organization's.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.errors import (
    CurrentPasswordIncorrect,
    PasswordPolicyViolation,
    SessionNotFound,
)
from workeasy360.contracts.values.access_policy import password_meets
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: How many trail entries a person's security page shows.
DEFAULT_EVENT_LIMIT = 20


def record_account_event(
    user_id: Any,
    kind: str,
    *,
    reason: str = "",
    ip_prefix: str = "",
    device_label: str = "",
) -> None:
    """Append one entry to the person's security trail."""
    from workeasy360.platform.identity.models import AccountEvent

    AccountEvent.objects.create(
        user_id=user_id,
        kind=kind,
        reason=reason[:64],
        ip_prefix=ip_prefix[:64],
        device_label=device_label[:64],
    )


def record_sign_in(user: Any, *, ip_prefix: str, device_label: str) -> None:
    """A successful sign-in: the trail entry and ``User.last_login_at``.

    ``last_login_at`` has been on the model since Phase 1 and was never
    written; the staff user directory reads it."""
    from workeasy360.platform.identity.models import AccountEvent, User

    now = timezone.now()
    User.objects.filter(pk=user.pk).update(last_login_at=now)
    record_account_event(
        user.pk,
        AccountEvent.Kind.SIGN_IN,
        ip_prefix=ip_prefix,
        device_label=device_label,
    )


def recent_account_events(
    user_id: Any, *, limit: int = DEFAULT_EVENT_LIMIT
) -> list[dict]:
    from workeasy360.platform.identity.models import AccountEvent

    limit = max(1, min(int(limit or DEFAULT_EVENT_LIMIT), 100))
    rows = AccountEvent.objects.filter(user_id=user_id).order_by("-occurred_at", "-id")[
        :limit
    ]
    return [
        {
            "occurred_at": r.occurred_at.isoformat(),
            "kind": r.kind,
            "reason": r.reason,
            "ip_prefix": r.ip_prefix,
            "device_label": r.device_label,
        }
        for r in rows
    ]


def prune_account_events(*, older_than_days: int) -> int:
    from workeasy360.platform.identity.models import AccountEvent

    cutoff = timezone.now() - timedelta(days=max(1, int(older_than_days)))
    deleted, _ = AccountEvent.objects.filter(occurred_at__lt=cutoff).delete()
    return deleted


# --- sessions ----------------------------------------------------------------


def _live_sessions(user_id: Any) -> Any:
    from workeasy360.platform.identity.models import User, UserSession
    from workeasy360.platform.identity.sessions import absolute_cutoff

    epoch = (
        User.objects.filter(pk=user_id).values_list("session_epoch", flat=True).first()
    )
    rows = UserSession.objects.filter(
        user_id=user_id, expire_date__gt=timezone.now(), session_epoch=epoch or 0
    )
    cutoff = absolute_cutoff()
    return rows.filter(created_at__gt=cutoff) if cutoff is not None else rows


def list_sessions(user_id: Any, *, current_public_id: UUID | None) -> list[dict]:
    """Where this person is signed in, most recently active first."""
    rows = _live_sessions(user_id).order_by("-last_activity_at")[:50]
    return [
        {
            "session_id": str(r.public_id),
            "current": r.public_id == current_public_id,
            "signed_in_at": r.created_at.isoformat(),
            "last_active_at": r.last_activity_at.isoformat(),
            "expires_at": r.expire_date.isoformat(),
            "ip_prefix": r.ip_prefix,
            "device_label": r.device_label or "Unknown device",
        }
        for r in rows
    ]


def revoke_session(
    user_id: Any, public_id: UUID, *, ip_prefix: str = "", device_label: str = ""
) -> None:
    """End one of this person's sessions. Another user's id is not found."""
    from workeasy360.platform.identity.models import AccountEvent, UserSession

    with transaction.atomic():
        deleted, _ = UserSession.objects.filter(
            user_id=user_id, public_id=public_id
        ).delete()
        if not deleted:
            raise SessionNotFound("No such session.")
        record_account_event(
            user_id,
            AccountEvent.Kind.SESSION_REVOKED,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
    metrics.increment("identity_sessions_revoked_total", scope="one")
    logger.info("identity.session.revoked", user_id=str(user_id))


def revoke_other_sessions(
    user_id: Any,
    *,
    keep_public_id: UUID | None,
    ip_prefix: str = "",
    device_label: str = "",
) -> int:
    """End every session but the caller's own."""
    from workeasy360.platform.identity.models import AccountEvent, UserSession

    with transaction.atomic():
        rows = UserSession.objects.filter(user_id=user_id)
        if keep_public_id is not None:
            rows = rows.exclude(public_id=keep_public_id)
        deleted, _ = rows.delete()
        record_account_event(
            user_id,
            AccountEvent.Kind.OTHER_SESSIONS_REVOKED,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
    metrics.increment("identity_sessions_revoked_total", scope="others")
    logger.info("identity.sessions.revoked_others", user_id=str(user_id), count=deleted)
    return deleted


def sign_out_everywhere(user_id: Any) -> int:
    """End every session, including any that is mid-request: the epoch moves
    (so a session loaded before the delete is flushed on its next request) and
    the rows go. For staff and for account disablement."""
    from workeasy360.platform.identity.models import AccountEvent, User, UserSession

    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        user.revoke_sessions()
        deleted, _ = UserSession.objects.filter(user_id=user_id).delete()
        record_account_event(user_id, AccountEvent.Kind.SIGNED_OUT_EVERYWHERE)
    metrics.increment("identity_sessions_revoked_total", scope="everywhere")
    return deleted


# --- password ----------------------------------------------------------------


def check_new_password(
    password: str, *, user: Any = None, min_length: int | None = None
) -> None:
    """Platform validators, then the strictest organization minimum length.

    Raises ``PasswordPolicyViolation`` listing every problem at once, so a
    person fixes the password in one attempt rather than one rule at a time.
    """
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    problems: list[str] = []
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        problems.extend(str(m) for m in exc.messages)
    if not password_meets(password, min_length=min_length):
        problems.append(
            f"Your organization requires a password of at least {min_length} characters."
        )
    if problems:
        raise PasswordPolicyViolation(problems=problems)


def change_password(
    user_id: Any,
    *,
    current_password: str,
    new_password: str,
    min_length: int | None = None,
    ip_prefix: str = "",
    device_label: str = "",
) -> Any:
    """Change the password, ending every other session.

    A wrong current password counts towards the account lockout exactly as a
    failed sign-in does - this endpoint must not become a password-guessing
    oracle that sidesteps the one on the login form. Returns the user; the
    view re-stamps its own session so the caller stays signed in.
    """
    from workeasy360.platform.identity.models import AccountEvent, User, UserSession
    from workeasy360.platform.identity.services import _record_failure

    user = User.objects.get(pk=user_id)
    if not user.check_password(current_password or ""):
        _record_failure(user)
        raise CurrentPasswordIncorrect("The current password is not correct.")
    check_new_password(new_password, user=user, min_length=min_length)
    if user.check_password(new_password):
        raise PasswordPolicyViolation(
            problems=["The new password must be different from the current one."]
        )

    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        user.set_password(new_password)
        user.session_epoch += 1
        user.failed_login_count = 0
        user.locked_until = None
        user.save(
            update_fields=[
                "password",
                "session_epoch",
                "failed_login_count",
                "locked_until",
                "updated_at",
            ]
        )
        # Every other session dies now; the caller's own is re-issued by the
        # view (update_session_auth_hash rotates its key).
        UserSession.objects.filter(user_id=user_id).delete()
        record_account_event(
            user_id,
            AccountEvent.Kind.PASSWORD_CHANGED,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
    metrics.increment("identity_password_changed_total")
    logger.info("identity.password.changed", user_id=str(user_id))
    return user

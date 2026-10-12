"""Handing a sign-in from the central sign-in host to one portal host
(ADR-043, docs/portals/design.md section 3).

Mechanics only: issuing a one-time code, redeeming it once, and ending every
session of one sign-in together. *Whether* an account may enter a portal is
decided above this module (``interfaces/api/v1/auth_views.py``, from live
StaffGrants and partner memberships) and again by the portal on every
request; nothing here grants anything.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from workeasy360.contracts.values.portals import host_of
from workeasy360.platform.identity.models import SessionHandoff, User, UserSession
from workeasy360.platform.identity.sessions import absolute_cutoff

#: What a portal may send as its binding value: the frontend proxy generates
#: 32 random bytes, base64url-encoded (43 characters).
_STATE_RE: Final = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
#: A code as issued: 32 random bytes, base64url-encoded.
_CODE_RE: Final = re.compile(r"^[A-Za-z0-9_-]{40,64}$")

#: Expired rows older than this are deleted when a new code is issued.
_PURGE_AFTER: Final = timedelta(days=1)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def valid_state(state: object) -> bool:
    return isinstance(state, str) and bool(_STATE_RE.match(state))


def issue(
    *,
    user: Any,
    portal: str,
    target_origin: str,
    next_path: str,
    state: str,
    source_session: UUID,
    auth_state: dict[str, Any],
) -> str:
    """Store a single-use hand-off and return its code (shown once).

    ``next_path`` must already be validated for ``portal``
    (``contracts.values.portals.safe_next``); ``state`` must satisfy
    :func:`valid_state`.
    """
    if not valid_state(state):
        raise ValueError("invalid hand-off state")
    now = timezone.now()
    SessionHandoff.objects.filter(expires_at__lt=now - _PURGE_AFTER).delete()
    code = secrets.token_urlsafe(32)
    SessionHandoff.objects.create(
        code_hash=_digest(code),
        user_id=user.pk,
        portal=portal,
        target_origin=target_origin,
        next_path=next_path,
        state_hash=_digest(state),
        session_epoch=int(user.session_epoch or 0),
        source_session=source_session,
        auth_state=auth_state,
        created_at=now,
        expires_at=now + timedelta(seconds=int(settings.PORTAL_HANDOFF_TTL_S)),
    )
    return code


@dataclass(frozen=True, slots=True)
class Redemption:
    user: Any
    portal: str
    next_path: str
    auth_state: dict[str, Any]


def redeem(*, code: object, state: object, host: str) -> Redemption | None:
    """Spend ``code`` on this request, or ``None`` - one answer for every
    reason, so a caller learns nothing about *why*.

    The row is claimed **first** (one conditional UPDATE), so a code is spent
    by its first presentation whatever happens next: a stolen code tried on
    the wrong host, or without the right binding cookie, is burnt rather than
    left for a second guess.
    """
    if not isinstance(code, str) or not _CODE_RE.match(code):
        return None
    now = timezone.now()
    row = SessionHandoff.objects.filter(
        code_hash=_digest(code), consumed_at__isnull=True, expires_at__gt=now
    ).first()
    if row is None:
        return None
    claimed = SessionHandoff.objects.filter(pk=row.pk, consumed_at__isnull=True).update(
        consumed_at=now
    )
    if claimed != 1:
        return None

    if host_of(row.target_origin) != (host or "").strip().lower():
        return None
    if not isinstance(state, str) or not constant_time_compare(
        row.state_hash, _digest(state)
    ):
        return None

    user = User.objects.filter(pk=row.user_id).first()
    if (
        user is None
        or not user.is_active
        or int(user.session_epoch or 0) != row.session_epoch
    ):
        return None
    # The sign-in it came from must still be live: signing out on the
    # central host before the code is used voids it.
    live = UserSession.objects.filter(
        public_id=row.source_session, user_id=row.user_id, expire_date__gt=now
    )
    cutoff = absolute_cutoff()
    if cutoff is not None:
        live = live.filter(created_at__gt=cutoff)
    if not live.exists():
        return None
    return Redemption(
        user=user,
        portal=row.portal,
        next_path=row.next_path,
        auth_state=dict(row.auth_state or {}),
    )


def end_sign_in(user_id: Any, sign_in_id: object) -> int:
    """End every session of one sign-in - the central one and each portal's
    (ADR-043). Returns how many were ended."""
    try:
        wanted = UUID(str(sign_in_id))
    except (TypeError, ValueError):
        return 0
    deleted, _ = UserSession.objects.filter(user_id=user_id, sign_in_id=wanted).delete()
    return int(deleted)

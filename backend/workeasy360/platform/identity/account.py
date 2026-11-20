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
from workeasy360.contracts.ports.sign_ins import (
    REASON_OTHER_SESSIONS_REVOKED,
    REASON_PASSWORD_CHANGED,
    REASON_SESSION_REVOKED,
    REASON_SIGNED_OUT_EVERYWHERE,
    notify_sign_ins_ended,
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
    """The short security trail. ADR-072: product sign-ins are Login History
    only (:func:`account_event_page`), so this list - the security panels on
    every portal and the Super Admin's latest entries - reads as before."""
    from workeasy360.platform.identity.models import AccountEvent

    limit = max(1, min(int(limit or DEFAULT_EVENT_LIMIT), 100))
    rows = (
        AccountEvent.objects.filter(user_id=user_id)
        .exclude(kind=AccountEvent.Kind.PRODUCT_SIGN_IN)
        .order_by("-occurred_at", "-id")[:limit]
    )
    return [_event_json(r) for r in rows]


def _event_json(row: Any) -> dict[str, Any]:
    return {
        "occurred_at": row.occurred_at.isoformat(),
        "kind": row.kind,
        "reason": row.reason,
        "ip_prefix": row.ip_prefix,
        "device_label": row.device_label,
    }


#: ADR-072: what Login History shows by default - every sign-in, kept or
#: refused, to WorkEasy360 and (through it) to a product.
SIGN_IN_KINDS: tuple[str, ...] = ("sign_in", "sign_in_failed", "product_sign_in")
HISTORY_MAX_PAGE = 100


def _history_cursor(raw: str | None) -> tuple[Any, str] | None:
    import base64
    import json
    from datetime import datetime

    if not raw or len(raw) > 200:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
        return datetime.fromisoformat(str(data["t"])), str(UUID(str(data["i"])))
    except (ValueError, KeyError, TypeError):
        return None


def _encode_history_cursor(occurred_at: Any, event_id: Any) -> str:
    import base64
    import json

    payload = json.dumps({"t": occurred_at.isoformat(), "i": str(event_id)})
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii")


def account_event_page(
    user_id: Any,
    *,
    kinds: tuple[str, ...] | None = SIGN_IN_KINDS,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    """ADR-072: the person's Login History, newest first, keyset-paged past
    the security panel's 20 entries (the whole retention window,
    ``ACCOUNT_EVENT_RETENTION_DAYS``). ``kinds=None`` is every entry. Coarse
    network and device only, as written."""
    from django.db.models import Q

    from workeasy360.platform.identity.models import AccountEvent

    limit = max(1, min(int(limit or 25), HISTORY_MAX_PAGE))
    rows = AccountEvent.objects.filter(user_id=user_id)
    if kinds is not None:
        rows = rows.filter(kind__in=list(kinds))
    decoded = _history_cursor(cursor)
    if decoded is not None:
        moment, last = decoded
        rows = rows.filter(Q(occurred_at__lt=moment) | Q(occurred_at=moment, id__lt=last))
    page = list(rows.order_by("-occurred_at", "-id")[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    return {
        "results": [_event_json(r) for r in page],
        "next_cursor": _encode_history_cursor(page[-1].occurred_at, page[-1].id)
        if has_more and page
        else None,
        "has_more": has_more,
    }


def record_product_sign_in(
    user_id: Any, *, product_code: str, sign_in_id: Any = None
) -> None:
    """ADR-072: a product redeemed a WorkEasy360 sign-in for this person (the
    token endpoint). The network and device are those of the WorkEasy360
    sign-in that authorized it - the token endpoint itself is the product's
    server, not the person's browser - and empty when it is not known."""
    from workeasy360.platform.identity.models import AccountEvent, UserSession

    where = None
    if sign_in_id is not None:
        where = (
            UserSession.objects.filter(user_id=user_id, sign_in_id=sign_in_id)
            .order_by("-last_activity_at")
            .values("ip_prefix", "device_label")
            .first()
        )
    record_account_event(
        user_id,
        AccountEvent.Kind.PRODUCT_SIGN_IN,
        reason=str(product_code),
        ip_prefix=(where or {}).get("ip_prefix", ""),
        device_label=(where or {}).get("device_label", ""),
    )


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


def _uuid_or_none(raw: Any) -> UUID | None:
    """A sign-in id as the session stores it (hex) or the column does."""
    try:
        return UUID(str(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def live_sign_in_ids(user_id: Any) -> set[UUID]:
    """ADR-072: the person's WorkEasy360 sign-ins with a session still live -
    what Connected Logins means by "WorkEasy360 sign-in still open"."""
    return {
        s
        for s in _live_sessions(user_id).values_list("sign_in_id", flat=True)
        if s is not None
    }


def list_sign_ins(user_id: Any, *, current_public_id: UUID | None) -> list[dict]:
    """ADR-072: where this person is signed in, **one row per sign-in**.

    In multi-portal mode one password sign-in has a session on WorkEasy360
    Accounts and one on each portal it was handed to (ADR-043, the shared
    ``sign_in_id``); :func:`list_sessions` shows each of them, so one device
    appeared several times. Grouped here: the device and network of its most
    recently active session, when it signed in (its earliest), when it was
    last active (its latest) and how many portal sessions it holds.
    ``session_id`` is that most recent session's public id, which is what
    :func:`revoke_session` takes - and revoking it ends the whole sign-in.
    A session without a sign-in id (older than ADR-043) is its own row."""
    rows = _live_sessions(user_id).order_by("-last_activity_at")[:200]
    groups: dict[Any, dict[str, Any]] = {}
    for r in rows:
        key = r.sign_in_id or r.public_id
        found = groups.get(key)
        if found is None:
            groups[key] = {
                "session_id": str(r.public_id),
                "current": r.public_id == current_public_id,
                "signed_in_at": r.created_at,
                "last_active_at": r.last_activity_at,
                "expires_at": r.expire_date,
                "ip_prefix": r.ip_prefix,
                "device_label": r.device_label or "Unknown device",
                "portal_sessions": 1,
            }
            continue
        found["portal_sessions"] += 1
        found["current"] = found["current"] or r.public_id == current_public_id
        found["signed_in_at"] = min(found["signed_in_at"], r.created_at)
        found["expires_at"] = max(found["expires_at"], r.expire_date)
    listed = sorted(groups.values(), key=lambda g: g["last_active_at"], reverse=True)
    return [
        {
            **g,
            "signed_in_at": g["signed_in_at"].isoformat(),
            "last_active_at": g["last_active_at"].isoformat(),
            "expires_at": g["expires_at"].isoformat(),
        }
        for g in listed[:50]
    ]


def revoke_session(
    user_id: Any,
    public_id: UUID,
    *,
    keep_sign_in_id: Any = None,
    ip_prefix: str = "",
    device_label: str = "",
) -> int:
    """End one of this person's sign-ins, given any of its sessions. Another
    user's id is not found. Returns how many sessions ended.

    ADR-072 security fix: in multi-portal mode a sign-in has a session on
    WorkEasy360 Accounts and one on each portal it was handed to, all sharing
    its ``sign_in_id``. Revoking used to delete only the row named, so the
    same sign-in stayed alive on its other hosts (and its products were told
    it had ended). Every session of that sign-in now ends together, exactly
    as signing out of it does (``handoff.end_sign_in``).

    ``keep_sign_in_id`` is the caller's own sign-in: a session of it is
    refused here (signing out is how it ends), so revoking a sibling never
    signs the caller out by surprise."""
    from workeasy360.platform.identity.models import AccountEvent, UserSession

    with transaction.atomic():
        named = list(
            UserSession.objects.select_for_update()
            .filter(user_id=user_id, public_id=public_id)
            .values_list("sign_in_id", flat=True)
        )
        if not named:
            raise SessionNotFound("No such session.")
        sign_in_id = named[0]
        if sign_in_id is not None and sign_in_id == _uuid_or_none(keep_sign_in_id):
            raise SessionNotFound("Use sign out to end this session.")
        doomed = UserSession.objects.filter(user_id=user_id, public_id=public_id)
        if sign_in_id is not None:
            doomed = UserSession.objects.filter(user_id=user_id, sign_in_id=sign_in_id)
        sign_in_ids = [sign_in_id]
        deleted, _ = doomed.delete()
        # ADR-069: the products that sign-in opened are told, in this commit.
        notify_sign_ins_ended(
            user_id=user_id, sign_in_ids=sign_in_ids, reason=REASON_SESSION_REVOKED
        )
        record_account_event(
            user_id,
            AccountEvent.Kind.SESSION_REVOKED,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
    metrics.increment("identity_sessions_revoked_total", scope="one")
    logger.info("identity.session.revoked", user_id=str(user_id), count=deleted)
    return int(deleted)


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
        kept_sign_in = None
        if keep_public_id is not None:
            kept_sign_in = (
                UserSession.objects.filter(user_id=user_id, public_id=keep_public_id)
                .values_list("sign_in_id", flat=True)
                .first()
            )
            rows = rows.exclude(public_id=keep_public_id)
            if kept_sign_in is not None:
                # ADR-072: the caller's own sign-in is kept whole - its
                # sessions on the other hosts too, not only this row.
                rows = rows.exclude(sign_in_id=kept_sign_in)
        # The caller's own sign-in stays (its portal sessions included), so
        # products it opened are not signed out; every other sign-in's are.
        ended_sign_ins = {
            s for s in rows.values_list("sign_in_id", flat=True) if s != kept_sign_in
        }
        deleted, _ = rows.delete()
        notify_sign_ins_ended(
            user_id=user_id,
            sign_in_ids=ended_sign_ins,
            reason=REASON_OTHER_SESSIONS_REVOKED,
        )
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
        # ADR-069: every product the person is signed in to, from any device.
        notify_sign_ins_ended(
            user_id=user_id, everywhere=True, reason=REASON_SIGNED_OUT_EVERYWHERE
        )
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
        user.password_change_required = False
        user.session_epoch += 1
        user.failed_login_count = 0
        user.locked_until = None
        user.save(
            update_fields=[
                "password",
                "password_change_required",
                "session_epoch",
                "failed_login_count",
                "locked_until",
                "updated_at",
            ]
        )
        # Every other session dies now; the caller's own is re-issued by the
        # view (update_session_auth_hash rotates its key).
        UserSession.objects.filter(user_id=user_id).delete()
        # ADR-069: a new password ends every product session too, this
        # browser's included - SSO signs this person straight back in.
        notify_sign_ins_ended(
            user_id=user_id, everywhere=True, reason=REASON_PASSWORD_CHANGED
        )
        record_account_event(
            user_id,
            AccountEvent.Kind.PASSWORD_CHANGED,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
    metrics.increment("identity_password_changed_total")
    logger.info("identity.password.changed", user_id=str(user_id))
    return user


# --- profile (ADR-072) ----------------------------------------------------------

#: First and last name as signup takes them (100 each) and the space between.
MAX_FULL_NAME = 201


def clean_full_name(raw: Any) -> str:
    """The name as stored: inner whitespace collapsed, never empty, never a
    control character, never longer than signup allows."""
    from workeasy360.contracts.errors import ProfileInvalid

    if raw is not None and not isinstance(raw, str):
        raise ProfileInvalid("Enter your name.", field="full_name")
    value = " ".join((raw or "").split())
    if not value:
        raise ProfileInvalid("Enter your name.", field="full_name")
    if len(value) > MAX_FULL_NAME or any(
        ord(ch) < 32 or 0x7F <= ord(ch) < 0xA0 for ch in value
    ):
        raise ProfileInvalid(
            f"Your name must be at most {MAX_FULL_NAME} characters, without control "
            "characters.",
            field="full_name",
        )
    return value


#: "Not given" in a PATCH, as opposed to an empty value that clears the field.
UNSET: Any = object()


def clean_country(raw: Any) -> str:
    from workeasy360.contracts.errors import ProfileInvalid
    from workeasy360.contracts.values.countries import COUNTRY_CODES

    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ProfileInvalid("Choose a country from the list.", field="country")
    value = raw.strip().upper()
    if value and value not in COUNTRY_CODES:
        raise ProfileInvalid("Choose a country from the list.", field="country")
    return value


def clean_language(raw: Any) -> str:
    from workeasy360.contracts.errors import ProfileInvalid
    from workeasy360.contracts.values.languages import LANGUAGE_CODES

    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ProfileInvalid("Choose a language from the list.", field="language")
    value = raw.strip()
    if value and value not in LANGUAGE_CODES:
        raise ProfileInvalid("Choose a language from the list.", field="language")
    return value


def time_zone_names() -> list[str]:
    """The zones the profile form offers: the canonical Area/City names."""
    from zoneinfo import available_timezones

    names = sorted(
        name
        for name in available_timezones()
        if "/" in name
        and not name.startswith(("Etc/", "SystemV/", "US/", "posix/", "right/"))
    )
    return [*names, "UTC"]


def clean_time_zone(raw: Any) -> str:
    from zoneinfo import available_timezones

    from workeasy360.contracts.errors import ProfileInvalid

    if raw is None:
        return ""
    if not isinstance(raw, str) or len(raw) > 64:
        raise ProfileInvalid("Choose a time zone from the list.", field="time_zone")
    value = raw.strip()
    if value and value not in available_timezones():
        raise ProfileInvalid("Choose a time zone from the list.", field="time_zone")
    return value


def update_profile(
    user_id: Any,
    *,
    full_name: Any = UNSET,
    country: Any = UNSET,
    language: Any = UNSET,
    time_zone: Any = UNSET,
    ip_prefix: str = "",
    device_label: str = "",
) -> Any:
    """Change the person's own profile (ADR-072, ADR-075): name, country,
    language, time zone - only the fields given. The sign-in email has its own
    confirmed change (ADR-066) and the password its own. Products read the new
    values from the ID token and userinfo claims (``name``, ``picture``,
    ``locale``, ``zoneinfo``, ``updated_at``) at their next sign-in or
    userinfo call. Every field is validated before anything is written; a
    no-op (and no trail entry) when nothing changed."""
    from workeasy360.platform.identity.models import AccountEvent, User

    wanted: dict[str, str] = {}
    if full_name is not UNSET:
        wanted["full_name"] = clean_full_name(full_name)
    if country is not UNSET:
        wanted["country"] = clean_country(country)
    if language is not UNSET:
        wanted["language"] = clean_language(language)
    if time_zone is not UNSET:
        wanted["time_zone"] = clean_time_zone(time_zone)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        changed = [name for name, value in wanted.items() if getattr(user, name) != value]
        if not changed:
            return user
        for name in changed:
            setattr(user, name, wanted[name])
        user.save(update_fields=[*changed, "updated_at"])
        record_account_event(
            user_id,
            AccountEvent.Kind.PROFILE_UPDATED,
            reason=",".join(sorted(changed)),
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
    logger.info("identity.profile.updated", user_id=str(user_id), fields=sorted(changed))
    return user


# --- profile photo (ADR-075) ------------------------------------------------------


def sniff_image(data: bytes) -> str | None:
    """The image type ``data`` really is, by its bytes (never by the header the
    client sent), or ``None``: PNG, JPEG or WebP only."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def strip_image_metadata(data: bytes, kind: str) -> bytes:
    """The image without its metadata (camera, GPS, comments), by its
    container format alone - the pixels are untouched. A malformed container
    raises ``ValueError``.

    - JPEG: every APP1-APP15 and COM segment goes (APP0/JFIF stays).
    - PNG: every ancillary text/metadata chunk goes (tEXt, zTXt, iTXt, eXIf, tIME).
    - WebP: the EXIF and XMP chunks go (the VP8X flags are left; readers
      ignore a flag whose chunk is absent).
    """
    import struct

    if kind == "image/jpeg":
        out = bytearray(data[:2])
        i = 2
        while i < len(data):
            if data[i] != 0xFF:
                raise ValueError("not a JPEG marker")
            marker = data[i + 1]
            if marker == 0xDA:  # start of scan: the rest is image data
                out += data[i:]
                return bytes(out)
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                out += data[i : i + 2]
                i += 2
                continue
            (length,) = struct.unpack(">H", data[i + 2 : i + 4])
            segment = data[i : i + 2 + length]
            if len(segment) < 2 + length:
                raise ValueError("truncated JPEG segment")
            if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
                out += segment
            i += 2 + length
        raise ValueError("JPEG without image data")
    if kind == "image/png":
        out = bytearray(data[:8])
        i = 8
        while i < len(data):
            (length,) = struct.unpack(">I", data[i : i + 4])
            chunk_type = data[i + 4 : i + 8]
            chunk = data[i : i + 12 + length]
            if len(chunk) < 12 + length:
                raise ValueError("truncated PNG chunk")
            if chunk_type not in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"):
                out += chunk
            i += 12 + length
            if chunk_type == b"IEND":
                return bytes(out)
        raise ValueError("PNG without IEND")
    if kind == "image/webp":
        out = bytearray()
        i = 12
        while i + 8 <= len(data):
            chunk_type = data[i : i + 4]
            (length,) = struct.unpack("<I", data[i + 4 : i + 8])
            padded = length + (length & 1)
            chunk = data[i : i + 8 + padded]
            if len(chunk) < 8 + length:
                raise ValueError("truncated WebP chunk")
            if chunk_type not in (b"EXIF", b"XMP "):
                out += chunk
            i += 8 + padded
        return b"RIFF" + struct.pack("<I", 4 + len(out)) + b"WEBP" + bytes(out)
    raise ValueError("unsupported image type")


def set_profile_photo(
    user_id: Any,
    data: bytes,
    *,
    declared_type: str = "",
    ip_prefix: str = "",
    device_label: str = "",
) -> Any:
    """Store (or replace) the person's photo. The URL token is new every time,
    so the previous photo's URL stops working at once."""
    import hashlib
    import secrets

    from django.conf import settings
    from django.utils import timezone

    from workeasy360.contracts.errors import PhotoInvalid
    from workeasy360.platform.identity.models import AccountEvent, ProfilePhoto, User

    limit = int(getattr(settings, "PROFILE_PHOTO_MAX_BYTES", 262144))
    if not data or len(data) > limit:
        raise PhotoInvalid()
    kind = sniff_image(data)
    declared = (declared_type or "").split(";")[0].strip().lower()
    if kind is None or (declared and declared != kind):
        raise PhotoInvalid()
    try:
        data = strip_image_metadata(data, kind)
    except ValueError as exc:
        raise PhotoInvalid() from exc
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        ProfilePhoto.objects.update_or_create(
            user_id=user_id,
            defaults={
                "token": secrets.token_urlsafe(32),
                "content_type": kind,
                "data": data,
                "sha256": hashlib.sha256(data).hexdigest(),
                "updated_at": timezone.now(),
            },
        )
        user.save(update_fields=["updated_at"])
        record_account_event(
            user_id,
            AccountEvent.Kind.PROFILE_UPDATED,
            reason="photo",
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
    logger.info("identity.profile.photo_set", user_id=str(user_id), size=len(data))
    return user


def remove_profile_photo(
    user_id: Any, *, ip_prefix: str = "", device_label: str = ""
) -> Any:
    from workeasy360.platform.identity.models import AccountEvent, ProfilePhoto, User

    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        deleted, _ = ProfilePhoto.objects.filter(user_id=user_id).delete()
        if deleted:
            user.save(update_fields=["updated_at"])
            record_account_event(
                user_id,
                AccountEvent.Kind.PROFILE_UPDATED,
                reason="photo",
                ip_prefix=ip_prefix,
                device_label=device_label,
            )
    return user


def profile_photo_token(user_id: Any) -> str | None:
    from workeasy360.platform.identity.models import ProfilePhoto

    return (
        ProfilePhoto.objects.filter(user_id=user_id)
        .values_list("token", flat=True)
        .first()
    )


def profile_photo_by_token(token: Any) -> Any:
    """The photo a capability URL names, of an active account only."""
    from workeasy360.contracts.enums import UserStatus
    from workeasy360.platform.identity.models import ProfilePhoto

    if not isinstance(token, str) or not token or len(token) > 64:
        return None
    return (
        ProfilePhoto.objects.filter(token=token, user__status=UserStatus.ACTIVE.value)
        .only("content_type", "data", "sha256", "updated_at")
        .first()
    )

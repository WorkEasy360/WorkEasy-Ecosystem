"""The session engine: Django's database backend, writing ``UserSession`` rows.

``SESSION_ENGINE = "workeasy360.platform.identity.sessions"``. Everything
Django's ``db`` backend does is kept - the same key generation, the same
``cycle_key`` on sign-in (session fixation), the same expiry - and three things
are added:

1. **Ownership columns maintained from the session's own data** (the signed-in
   user, the epoch it was issued under, coarse network and device) on every
   save. No extra query: the columns ride on the existing ``UPDATE``. A save
   happens when the session changes, and otherwise at most once per
   ``SESSION_REFRESH_INTERVAL_S`` to slide the idle window
   (``platform.identity.middleware.SlidingSessionMiddleware``).
2. **Per-session revocation.** Deleting a row ends that session: the next
   request carrying its cookie loads nothing and is anonymous. Before this, the
   only revocation was ``User.session_epoch``, which ends *every* session.
3. **The absolute lifetime** ``SESSION_ABSOLUTE_TIMEOUT`` - declared since
   Phase 4 but never enforced; the sliding idle window alone let an active
   session live forever. A session older than the cap no longer loads.

Updates name their columns explicitly, so a save never rewrites ``created_at``
or ``public_id``.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta
from typing import Any, Final

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.sessions.backends.base import CreateError, UpdateError
from django.contrib.sessions.backends.db import SessionStore as DatabaseSessionStore
from django.core.exceptions import SuspiciousOperation
from django.db import DatabaseError, IntegrityError, router, transaction
from django.utils import timezone

#: Session-data keys the sign-in view writes (``auth_views.login_view``).
SESSION_IP_PREFIX_KEY: Final = "w360_ip_prefix"
SESSION_DEVICE_KEY: Final = "w360_device"
#: One password sign-in, shared with every portal session handed off from it
#: (ADR-043); kept in the indexed ``sign_in_id`` column so sign-out can end
#: them all together.
SESSION_SIGN_IN_ID_KEY: Final = "w360_sign_in_id"
#: A partner-portal session that exists only for a staff member's guest view
#: (``commercial.partners.guest``) carries the view's end (epoch seconds) and
#: ends with it: ``SessionStore.load`` refuses it afterwards, whatever sliding
#: did to its row's expiry.
SESSION_GUEST_UNTIL_KEY: Final = "w360_guest_until"

#: ``django.contrib.auth.SESSION_KEY`` - repeated rather than imported so this
#: module stays importable before the auth app is ready.
_AUTH_USER_KEY: Final = "_auth_user_id"
#: ``platform.identity.middleware.SESSION_EPOCH_KEY``, for the same reason.
_EPOCH_KEY: Final = "w360_session_epoch"
#: ``platform.tenancy.resolution.SESSION_AUTHENTICATED_AT_KEY``: when the
#: password was entered (epoch seconds). A portal session handed off from that
#: sign-in carries it verbatim (ADR-043).
_AUTHENTICATED_AT_KEY: Final = "w360_authenticated_at"

_UPDATE_FIELDS: Final = (
    "session_data",
    "expire_date",
    "user",
    "session_epoch",
    "last_activity_at",
    "ip_prefix",
    "device_label",
    "sign_in_id",
)


def absolute_cutoff() -> Any:
    """Sessions minted before this moment are past their absolute lifetime."""
    seconds = int(getattr(settings, "SESSION_ABSOLUTE_TIMEOUT", 0) or 0)
    if seconds <= 0:
        return None
    return timezone.now() - timedelta(seconds=seconds)


class SessionStore(DatabaseSessionStore):
    #: Set when the row is loaded or created; ``None`` for a session that has
    #: not touched the database yet.
    w360_public_id: uuid.UUID | None = None
    w360_created_at: Any = None
    #: When the loaded row was last saved: the start of its idle window.
    w360_last_activity_at: Any = None

    def refresh_due(self) -> bool:
        """Whether the sliding idle window should move forward on this request.

        Only for a row that was actually loaded, and only once
        ``SESSION_REFRESH_INTERVAL_S`` has passed since it was last saved. The
        expiry moves in steps of at most that interval, so an idle session can
        end up to one interval *early*, never late (2026-10 audit; it used to
        be saved on every request).
        """
        if self.w360_last_activity_at is None:
            return False
        interval = int(getattr(settings, "SESSION_REFRESH_INTERVAL_S", 0) or 0)
        return timezone.now() - self.w360_last_activity_at >= timedelta(seconds=interval)

    @classmethod
    def get_model_class(cls):  # type: ignore[no-untyped-def]
        from workeasy360.platform.identity.models import UserSession

        return UserSession

    def _live_rows(self) -> Any:
        rows = self.model.objects.filter(expire_date__gt=timezone.now())
        cutoff = absolute_cutoff()
        return rows.filter(created_at__gt=cutoff) if cutoff is not None else rows

    def _get_session_from_db(self):  # type: ignore[no-untyped-def]
        try:
            row = self._live_rows().get(session_key=self.session_key)
        except (self.model.DoesNotExist, SuspiciousOperation):
            self._session_key = None
            return None
        self.w360_public_id = row.public_id
        self.w360_created_at = row.created_at
        self.w360_last_activity_at = row.last_activity_at
        return row

    async def _aget_session_from_db(self):  # type: ignore[no-untyped-def]
        return await sync_to_async(self._get_session_from_db)()

    def load(self) -> dict[str, Any]:
        data = super().load()
        until = data.get(SESSION_GUEST_UNTIL_KEY)
        if until is None:
            return data
        try:
            live = float(until) > time.time()
        except (TypeError, ValueError):
            live = False
        if live:
            return data
        # The guest view this session existed for is over: the session ends
        # too (deleted, so nothing revives it), and this request is anonymous.
        if self.session_key:
            self.delete(self.session_key)
        self._session_key = None
        return {}

    async def aload(self) -> dict[str, Any]:
        return await sync_to_async(self.load)()

    def create_model_instance(self, data: dict[str, Any]) -> Any:
        now = timezone.now()
        return self.model(
            session_key=self._get_or_create_session_key(),
            session_data=self.encode(data),
            expire_date=self.get_expiry_date(),
            # The absolute lifetime runs from the sign-in, not from this key:
            # a portal session handed off on day 29 ends with its sign-in on
            # day 30 (ADR-043). Never later than now. Only read when the row
            # is inserted - updates never rewrite created_at.
            created_at=_signed_in_at(data, now),
            user_id=_uuid_or_none(data.get(_AUTH_USER_KEY)),
            session_epoch=_int_or_zero(data.get(_EPOCH_KEY)),
            last_activity_at=now,
            ip_prefix=str(data.get(SESSION_IP_PREFIX_KEY) or "")[:64],
            device_label=str(data.get(SESSION_DEVICE_KEY) or "")[:64],
            sign_in_id=_uuid_or_none(data.get(SESSION_SIGN_IN_ID_KEY)),
        )

    async def acreate_model_instance(self, data: dict[str, Any]) -> Any:
        return await sync_to_async(self.create_model_instance)(data)

    def save(self, must_create: bool = False) -> None:
        if self.session_key is None:
            return self.create()
        data = self._get_session(no_load=must_create)
        row = self.create_model_instance(data)
        using = router.db_for_write(self.model, instance=row)
        try:
            with transaction.atomic(using=using):
                if must_create:
                    row.save(force_insert=True, using=using)
                else:
                    row.save(force_update=True, using=using, update_fields=_UPDATE_FIELDS)
        except IntegrityError as exc:
            if must_create:
                raise CreateError from exc
            raise
        except DatabaseError as exc:
            # A forced update that matched nothing: the row was deleted
            # (revoked) while this request was in flight. Django turns this
            # into SessionInterrupted, which is the right outcome.
            if not must_create:
                raise UpdateError from exc
            raise
        if must_create:
            self.w360_public_id = row.public_id
            self.w360_created_at = row.created_at
        else:
            _slide_sign_in(row)
        return None

    async def asave(self, must_create: bool = False) -> None:
        await sync_to_async(self.save)(must_create)

    @classmethod
    def clear_expired(cls) -> None:
        from django.db.models import Q

        model = cls.get_model_class()
        expired = Q(expire_date__lt=timezone.now())
        cutoff = absolute_cutoff()
        if cutoff is not None:
            expired |= Q(created_at__lt=cutoff)
        model.objects.filter(expired).delete()


def _slide_sign_in(row: Any) -> None:
    """Keep every live session of one sign-in alive together (ADR-044, design
    section 8).

    In multi-portal mode a sign-in has a session on the central sign-in host
    and one on each portal it was handed to, each with its own idle window.
    The person works on one host - the customer portal, say - while the
    identity provider's session on the sign-in host sits untouched; after the
    idle window it would lapse, and the next product launch would ask for the
    password although the person never stopped using Workeasy360. So when one
    session slides its window, the others of the same sign-in slide with it.

    Only forward, only for sessions still live (an expired or revoked one is
    never revived), never past a session's absolute lifetime (enforced from
    its own ``created_at`` when it loads), and only in multi-portal mode: in
    single-host mode a sign-in has one session and this would be a wasted
    query. Sign-out still ends them all together (``handoff.end_sign_in``).
    """
    if not getattr(settings, "PORTALS_ENABLED", False):
        return
    if row.user_id is None or row.sign_in_id is None:
        return
    row.__class__.objects.filter(
        user_id=row.user_id,
        sign_in_id=row.sign_in_id,
        expire_date__gt=timezone.now(),
        expire_date__lt=row.expire_date,
    ).exclude(session_key=row.session_key).update(expire_date=row.expire_date)


def date_from_sign_in(session: Any) -> None:
    """Save ``session`` and date its row from the sign-in it carries
    (``w360_authenticated_at``), never later than now (ADR-043).

    A hand-off creates a new key (``cycle_key`` inserts the row at once, before
    the sign-in's facts are copied in), and the absolute lifetime is measured
    from the row's ``created_at``: without this, every hand-off would grant a
    fresh ``SESSION_ABSOLUTE_TIMEOUT``. Only ever moves ``created_at`` back.
    """
    if not isinstance(session, SessionStore):
        return
    session.save()
    from workeasy360.platform.identity.models import UserSession

    moment = _signed_in_at(dict(session.items()), timezone.now())
    UserSession.objects.filter(
        session_key=session.session_key, created_at__gt=moment
    ).update(created_at=moment)


def session_public_id(session: Any) -> uuid.UUID | None:
    """The public id of the request's session row, loading it if needed.

    ``None`` for a session that was never saved (an anonymous request) or for
    another engine (a test that swaps ``SESSION_ENGINE``)."""
    if session is None or not isinstance(session, SessionStore):
        return None
    # Any read loads the row, which is what sets the attribute.
    session.get(_AUTH_USER_KEY)
    return session.w360_public_id


def _signed_in_at(data: dict[str, Any], now: Any) -> Any:
    from datetime import datetime
    from datetime import timezone as dt_timezone

    try:
        moment = datetime.fromtimestamp(
            float(data[_AUTHENTICATED_AT_KEY]), tz=dt_timezone.utc
        )
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return now
    return min(moment, now)


def _uuid_or_none(raw: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _int_or_zero(raw: Any) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0

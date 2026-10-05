"""The session engine: Django's database backend, writing ``UserSession`` rows.

``SESSION_ENGINE = "workeasy360.platform.identity.sessions"``. Everything
Django's ``db`` backend does is kept - the same key generation, the same
``cycle_key`` on sign-in (session fixation), the same expiry - and three things
are added:

1. **Ownership columns maintained from the session's own data** (the signed-in
   user, the epoch it was issued under, coarse network and device) on the save
   Django already makes every request (``SESSION_SAVE_EVERY_REQUEST``). No extra
   query: the columns ride on the existing ``UPDATE``.
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

#: ``django.contrib.auth.SESSION_KEY`` - repeated rather than imported so this
#: module stays importable before the auth app is ready.
_AUTH_USER_KEY: Final = "_auth_user_id"
#: ``platform.identity.middleware.SESSION_EPOCH_KEY``, for the same reason.
_EPOCH_KEY: Final = "w360_session_epoch"

_UPDATE_FIELDS: Final = (
    "session_data",
    "expire_date",
    "user",
    "session_epoch",
    "last_activity_at",
    "ip_prefix",
    "device_label",
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
        return row

    async def _aget_session_from_db(self):  # type: ignore[no-untyped-def]
        return await sync_to_async(self._get_session_from_db)()

    def create_model_instance(self, data: dict[str, Any]) -> Any:
        now = timezone.now()
        return self.model(
            session_key=self._get_or_create_session_key(),
            session_data=self.encode(data),
            expire_date=self.get_expiry_date(),
            user_id=_uuid_or_none(data.get(_AUTH_USER_KEY)),
            session_epoch=_int_or_zero(data.get(_EPOCH_KEY)),
            last_activity_at=now,
            ip_prefix=str(data.get(SESSION_IP_PREFIX_KEY) or "")[:64],
            device_label=str(data.get(SESSION_DEVICE_KEY) or "")[:64],
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


def session_public_id(session: Any) -> uuid.UUID | None:
    """The public id of the request's session row, loading it if needed.

    ``None`` for a session that was never saved (an anonymous request) or for
    another engine (a test that swaps ``SESSION_ENGINE``)."""
    if session is None or not isinstance(session, SessionStore):
        return None
    # Any read loads the row, which is what sets the attribute.
    session.get(_AUTH_USER_KEY)
    return session.w360_public_id


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

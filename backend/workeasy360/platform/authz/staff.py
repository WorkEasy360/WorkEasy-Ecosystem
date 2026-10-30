"""Control-plane permission grants: the mechanism (ADR-027).

This module holds the *data* half of the Super Admin boundary - what a staff
member has been granted, and how a grant is created or ended - and nothing
else. The *use case* (authorizing the caller, requiring a reason, auditing,
publishing an event) lives in ``commercial/controlplane/security.py``, one
layer up, because ``authz`` and ``audit`` are siblings at P2 and may not
import each other (F-5).

Four rules are enforced here rather than left to callers, because they are
invariants of the data and not of any one use case:

1. **Only registered ``platform.staff_*`` permissions can be granted.** A key
   that is not declared in code with ``control_plane=True`` is refused, so the
   grantable surface is exactly the reviewable list in ``permissions.py``;
   partner keys, although control-plane too, belong to capability grants.
2. **Only an active staff identity holds a grant.** A grant on a
   non-staff or disabled account is inert - ``staff_permissions`` returns
   nothing for it - and creating one is refused so the table never
   accumulates rows that look meaningful and are not.
3. **A grant may be time-boxed, and an expired grant is over.** Expiry is read
   on every check, exactly like revocation: nothing has to run for it to take
   effect, and nothing can make an expired grant work again except a new one.
4. **The last permanent holder of ``platform.staff_security.manage`` cannot
   be removed.** Without that, two administrators revoking each other
   concurrently leaves a platform nobody can grant anything on. Enforced
   under row locks, so the race resolves to exactly one success. Only
   *permanent* holders count: a time-boxed administrator lapses on its own,
   so leaving only those would strand the control plane on a timer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.errors import (
    ControlPlaneInvariantViolation,
    PermissionDenied,
)
from workeasy360.platform.authz.registry import permission_registry

#: The permission whose last holder may not be removed.
SECURITY_ADMIN: Final = "platform.staff_security.manage"

#: Staff hold ``platform.staff_*`` keys and nothing else. Partner keys are
#: control-plane too, but they belong to capability grants: a staff grant of
#: ``platform.partner_subscription.sell`` would confer its ``commercial.*``
#: implications with none of the partner gates around them.
STAFF_KEY_PREFIX: Final = "platform.staff_"

MIN_REASON_LENGTH: Final = 10

EXPIRED_REASON: Final = "Expired; superseded by a new grant"


def _in_force(now: datetime) -> Q:
    """Not revoked, and either permanent or not yet expired at ``now``."""
    return Q(revoked_at__isnull=True) & (
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )


def _held_by_active_staff() -> Q:
    return Q(user__is_staff=True, user__status=UserStatus.ACTIVE.value)


def staff_permissions(user_id: UUID) -> frozenset[str]:
    """Everything this identity may do as staff, closed over ``implies``.

    Read from the database on every call - no cache. A revoked or expired
    grant must stop working on the very next check, and the control plane is
    a low-volume surface where one indexed query is not a cost worth trading
    that for.

    An identity that is not (or is no longer) an active staff member holds
    nothing, whatever rows exist for it.
    """
    from workeasy360.platform.authz.models import StaffGrant

    keys = list(
        StaffGrant.objects.filter(_in_force(timezone.now()), _held_by_active_staff())
        .filter(user_id=user_id)
        .values_list("permission", flat=True)
    )
    if not keys:
        return frozenset()
    # Only what the registry declares as a staff key can be exercised, so a
    # row carrying a stale or foreign key confers nothing.
    registered = [k for k in keys if _is_grantable(k)]
    return frozenset(
        key
        for key in permission_registry.expand(registered)
        if permission_registry.exists(key)
    )


def _is_grantable(key: str) -> bool:
    return (
        str(key).startswith(STAFF_KEY_PREFIX)
        and permission_registry.exists(key)
        and permission_registry.get(key).control_plane
    )


def validate_grantable(permission: str) -> None:
    if not _is_grantable(permission):
        raise ControlPlaneInvariantViolation(
            f"{permission!r} is not a control-plane staff permission and cannot "
            f"be granted.",
            permission=str(permission),
        )


def create_grant(
    *,
    user_id: UUID,
    permission: str,
    granted_by_id: UUID,
    reason: str,
    expires_at: datetime | None = None,
) -> tuple[Any, bool]:
    """Create an active grant. Returns ``(grant, created)``.

    A second active grant of the same permission to the same person is not an
    error - it is the same fact - so the grant in force is returned and
    ``created`` is False. One that has *expired* is not the same fact: it is
    closed (stamped as ended, never deleted) and the new grant takes its place.
    The partial unique index is what decides a race.
    """
    from workeasy360.platform.authz.models import StaffGrant
    from workeasy360.platform.identity.models import User

    validate_grantable(permission)
    if len((reason or "").strip()) < MIN_REASON_LENGTH:
        raise ControlPlaneInvariantViolation(
            "A reason of at least 10 characters is required to grant a "
            "control-plane permission.",
            field="reason",
        )
    now = timezone.now()
    if expires_at is not None and (
        not isinstance(expires_at, datetime)
        or expires_at.tzinfo is None
        or expires_at <= now
    ):
        raise ControlPlaneInvariantViolation(
            "expires_at must be a future moment with a timezone.", field="expires_at"
        )
    if user_id == granted_by_id:
        raise PermissionDenied(
            "A control-plane permission cannot be granted to yourself.",
            permission=SECURITY_ADMIN,
            reason="self_grant",
        )
    target = User.objects.filter(id=user_id).first()
    if target is None or not target.is_staff or not target.is_active:
        # One shape for "no such user", "not staff" and "disabled": the caller
        # must not learn which accounts exist.
        raise ControlPlaneInvariantViolation(
            "That identity cannot hold control-plane permissions.", field="user_id"
        )

    try:
        with transaction.atomic():
            existing = (
                StaffGrant.objects.select_for_update()
                .filter(user_id=user_id, permission=permission, revoked_at__isnull=True)
                .first()
            )
            if existing is not None:
                if existing.expires_at is None or existing.expires_at > now:
                    return existing, False
                existing.revoked_at = now
                existing.revoked_by_id = granted_by_id
                existing.revoke_reason = EXPIRED_REASON
                existing.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
            grant = StaffGrant.objects.create(
                user_id=user_id,
                permission=permission,
                granted_by_id=granted_by_id,
                granted_at=now,
                reason=reason.strip(),
                expires_at=expires_at,
            )
    except IntegrityError:
        # Lost the race to an identical grant; that grant is the answer.
        grant = StaffGrant.objects.get(
            user_id=user_id, permission=permission, revoked_at__isnull=True
        )
        return grant, False
    return grant, True


def end_grant(*, grant_id: UUID, revoked_by_id: UUID, reason: str) -> tuple[Any, bool]:
    """End a grant. Returns ``(grant, changed)``; ending an ended grant is a no-op.

    The row is locked first, and - for the security-administrator permission
    - so is every other live holder, which is what makes "never zero
    permanent administrators" true under concurrency rather than merely likely.
    """
    from workeasy360.platform.authz.models import StaffGrant

    if len((reason or "").strip()) < MIN_REASON_LENGTH:
        raise ControlPlaneInvariantViolation(
            "A reason of at least 10 characters is required to revoke a "
            "control-plane permission.",
            field="reason",
        )

    with transaction.atomic():
        # Lock order is the whole point. Two administrators removing each
        # other each hold their *own* row and want the other's: a deadlock if
        # the target is locked first. So the full set of live holders is
        # locked first, in id order, by every caller - they queue instead of
        # crossing - and only then the target row.
        permission = (
            StaffGrant.objects.filter(id=grant_id)
            .values_list("permission", flat=True)
            .first()
        )
        permanent_holders: list[UUID] = []
        if permission == SECURITY_ADMIN:
            holders = list(
                StaffGrant.objects.select_for_update()
                .filter(_in_force(timezone.now()), _held_by_active_staff())
                .filter(permission=SECURITY_ADMIN)
                .order_by("id")
                .values_list("id", "expires_at")
            )
            permanent_holders = [gid for gid, expires in holders if expires is None]
        grant = StaffGrant.objects.select_for_update().filter(id=grant_id).first()
        if grant is None:
            raise ControlPlaneInvariantViolation(
                "No such control-plane grant.", field="grant_id"
            )
        if grant.revoked_at is not None:
            return grant, False
        if (
            permission == SECURITY_ADMIN
            and grant.id in permanent_holders
            and len(permanent_holders) <= 1
        ):
            raise ControlPlaneInvariantViolation(
                "That is the last permanent holder of control-plane security "
                "administration. Grant it (without an expiry) to someone else first.",
                permission=SECURITY_ADMIN,
            )

        grant.revoked_at = timezone.now()
        grant.revoked_by_id = revoked_by_id
        grant.revoke_reason = reason.strip()
        grant.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
    return grant, True


def is_last_permanent_security_administrator(user_id: UUID) -> bool:
    """Would removing ``user_id``'s staff standing leave no *permanent* holder
    of ``platform.staff_security.manage``?

    Locks the live holders (the same rows ``end_grant`` locks) so a caller
    deciding inside its transaction cannot race a concurrent revocation.
    """
    from workeasy360.platform.authz.models import StaffGrant

    holders = set(
        StaffGrant.objects.select_for_update()
        .filter(_in_force(timezone.now()), _held_by_active_staff())
        .filter(permission=SECURITY_ADMIN, expires_at__isnull=True)
        .values_list("user_id", flat=True)
    )
    return holders == {user_id}


def security_administrator_exists() -> bool:
    """Is there an active holder of ``platform.staff_security.manage`` in force?

    Used by the one-time bootstrap command, which must refuse once the first
    administrator exists.
    """
    from workeasy360.platform.authz.models import StaffGrant

    return (
        StaffGrant.objects.filter(_in_force(timezone.now()), _held_by_active_staff())
        .filter(permission=SECURITY_ADMIN)
        .exists()
    )


def holders(permission: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """The active staff members who hold ``permission`` now - directly or
    through a key that implies it - as ``{user_id, email}``, by address.

    For routing a notice to the people who can act on it (ADR-055), never for
    deciding anything: the check stays :func:`staff_permissions` on the
    request itself. One query over the grants in force; staff are few.
    """
    from workeasy360.platform.authz.models import StaffGrant

    keys: dict[UUID, set[str]] = {}
    emails: dict[UUID, str] = {}
    rows = StaffGrant.objects.filter(
        _in_force(timezone.now()), _held_by_active_staff()
    ).values_list("user_id", "user__email", "permission")
    for user_id, email, key in rows:
        if _is_grantable(key):
            keys.setdefault(user_id, set()).add(key)
            emails[user_id] = email
    found = [
        {"user_id": user_id, "email": emails[user_id]}
        for user_id, held in keys.items()
        if permission in permission_registry.expand(held) and emails[user_id]
    ]
    return sorted(found, key=lambda h: h["email"])[: max(1, limit)]


def live_grants(*, user_id: UUID | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Grants in force, bounded, for the security screen. No secrets involved."""
    from workeasy360.platform.authz.models import StaffGrant

    queryset = StaffGrant.objects.filter(_in_force(timezone.now())).select_related("user")
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)
    return [
        {
            "id": str(g.id),
            "user_id": str(g.user_id),
            "email": g.user.email,
            "permission": g.permission,
            "granted_by_id": str(g.granted_by_id),
            "granted_at": g.granted_at.isoformat(),
            "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            "reason": g.reason,
        }
        for g in queryset.order_by("user__email", "permission")[: max(1, min(limit, 500))]
    ]

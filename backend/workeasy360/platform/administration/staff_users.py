"""The Super Admin's user directory and account actions (Phase 8 extension).

Accounts are global, so this is the one place an account is managed across
every organization at once: signing it out everywhere, unlocking it after the
login lockout, disabling it (a compromised or departed person) and enabling it
again.

``platform.staff_user.view`` reads: address, name, status, when it last signed
in, how many organizations it belongs to, whether it
is staff - never a password hash, a session key or a token.
``platform.staff_user.manage`` (dangerous: refused under impersonation)
acts, with a reason of at least ten characters, audited in the control-plane
log and in the person's own security trail.

Two refusals protect the platform from its own operators: nobody can disable
their own account, and the control-plane system user is not an account anyone
may act on.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Q

from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.errors import (
    MemberNotFound,
    MembershipConflict,
    MembershipValidationError,
)
from workeasy360.contracts.values import json_input
from workeasy360.contracts.values.control_plane import PLATFORM_SYSTEM_USER_EMAIL
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW = "platform.staff_user.view"
MANAGE = "platform.staff_user.manage"

MAX_PAGE = 100


def _cursor(raw: str | None) -> tuple[str, str] | None:
    if not raw:
        return None
    try:
        data = json_input.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
        return str(data["t"]), str(data["i"])
    except (ValueError, KeyError, TypeError):
        return None


def _encode(created_at: Any, user_id: Any) -> str:
    payload = json.dumps({"t": created_at.isoformat(), "i": str(user_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode("ascii")


#: The directory's segments (Phase 9). Lifecycle segments are built on the
#: organizations whose commercial access the caller passes in; account
#: segments on the account itself.
SEGMENTS = (
    "explorers",
    "trial",
    "customers",
    "free",
    "unverified",
    "suspended",
    "deactivated",
)


def _segment_filter(
    segment: str | None, *, customer_orgs: Any, trial_orgs: Any, now: Any
) -> Any:
    """The ``Q`` for a segment. ``customer_orgs``/``trial_orgs`` are lazy
    subqueries of organization ids, so the whole filter is one SQL statement.

    - explorers: an active account whose organizations hold nothing
    - trial: an active account with a trial somewhere, and nothing paid
    - customers: an active account in at least one customer organization
    - free: an active account in no customer organization (trial + explorers)
    - unverified: a signup that never verified its address
    - suspended: temporarily locked out (failed sign-ins or codes)
    - deactivated: disabled by Workeasy360 staff
    """
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    def members_of(org_ids: Any) -> Any:
        return UserOrganizationAccess.objects.filter(organization_id__in=org_ids).values(
            "user_id"
        )

    active = Q(status=UserStatus.ACTIVE.value)
    if segment is None:
        return Q()
    if segment not in SEGMENTS:
        raise MembershipValidationError("Unknown segment.", field="segment")
    if segment == "unverified":
        return Q(status=UserStatus.PENDING.value)
    if segment == "deactivated":
        return Q(status=UserStatus.DISABLED.value)
    if segment == "suspended":
        return active & Q(locked_until__gt=now)
    if customer_orgs is None or trial_orgs is None:
        raise ValueError("Lifecycle segments need the organizations' access classes.")
    if segment == "customers":
        return active & Q(id__in=members_of(customer_orgs))
    if segment == "free":
        return active & ~Q(id__in=members_of(customer_orgs))
    if segment == "trial":
        return (
            active
            & Q(id__in=members_of(trial_orgs))
            & ~Q(id__in=members_of(customer_orgs))
        )
    return (
        active & ~Q(id__in=members_of(customer_orgs)) & ~Q(id__in=members_of(trial_orgs))
    )


@in_scope
def directory(
    ctx: TenantContext,
    *,
    search: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
    segment: str | None = None,
    customer_orgs: Any = None,
    trial_orgs: Any = None,
) -> dict[str, Any]:
    from datetime import datetime

    from django.utils import timezone

    from workeasy360.platform.identity.models import User

    authz.require(ctx, VIEW)
    limit = max(1, min(int(limit or 25), MAX_PAGE))
    segment_q = _segment_filter(
        segment,
        customer_orgs=customer_orgs,
        trial_orgs=trial_orgs,
        now=timezone.now(),
    )
    rows = (
        User.objects.exclude(email=PLATFORM_SYSTEM_USER_EMAIL)
        .annotate(
            organizations=Count("organization_access_rows", distinct=True),
        )
        .filter(segment_q)
        .order_by("-created_at", "-id")
    )
    if search:
        term = search.strip()[:100]
        rows = rows.filter(Q(email__icontains=term) | Q(full_name__icontains=term))
    decoded = _cursor(cursor)
    if decoded is not None:
        stamp, last = decoded
        try:
            moment = datetime.fromisoformat(stamp)
            rows = rows.filter(
                Q(created_at__lt=moment) | Q(created_at=moment, id__lt=last)
            )
        except ValueError:
            pass
    page = list(rows[: limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    return {
        "results": [_user_row(u) for u in page],
        "next_cursor": _encode(page[-1].created_at, page[-1].id)
        if has_more and page
        else None,
        "has_more": has_more,
    }


def _user_row(user: Any) -> dict[str, Any]:
    return {
        "user_id": str(user.id),
        "email": user.email,
        "name": user.full_name,
        "status": user.status,
        "is_staff": user.is_staff,
        "locked": user.is_locked_out,
        "organizations": getattr(user, "organizations", None),
        "email_verified": user.email_verified_at is not None,
        "acquisition_source": user.acquisition_source,
        "created_at": user.created_at.isoformat(),
        "last_sign_in_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@in_scope
def detail(ctx: TenantContext, user_id: UUID) -> dict[str, Any]:
    from workeasy360.platform.identity.account import list_sessions, recent_account_events
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.selectors import access_rows_for_users

    authz.require(ctx, VIEW)
    user = _user(user_id)
    memberships = access_rows_for_users([user.id])[user.id][:100]
    row = _user_row(
        User.objects.filter(id=user.id)
        .annotate(organizations=Count("organization_access_rows", distinct=True))
        .first()
    )
    return {
        **row,
        "memberships": [
            {**m, "organization_id": str(m["organization_id"])} for m in memberships
        ],
        "sessions": list_sessions(user.id, current_public_id=None),
        "activity": recent_account_events(user.id, limit=20),
    }


def _user(user_id: UUID) -> Any:
    from workeasy360.platform.identity.models import User

    user = (
        User.objects.filter(id=user_id).exclude(email=PLATFORM_SYSTEM_USER_EMAIL).first()
    )
    if user is None:
        raise MemberNotFound("No such user.")
    return user


def _reason(raw: Any) -> str:
    reason = str(raw or "").strip()
    if len(reason) < 10:
        raise MembershipValidationError(
            "Give a reason of at least 10 characters.", field="reason"
        )
    return reason[:255]


@in_scope
def act(ctx: TenantContext, user_id: UUID, *, action: str, reason: Any) -> dict[str, Any]:
    """``sign_out`` | ``unlock`` | ``disable`` | ``enable``."""
    from workeasy360.platform.identity import account, lifecycle
    from workeasy360.platform.identity.models import AccountEvent

    authz.require(ctx, MANAGE)
    why = _reason(reason)
    if action not in ("sign_out", "unlock", "disable", "enable"):
        raise MembershipValidationError("Unknown action.", field="action")
    user = _user(user_id)
    if action == "disable" and user.id == ctx.user_id:
        raise MembershipConflict("You cannot disable your own account.")
    changes: dict[str, Any] = {"action": action}
    if action == "sign_out":
        changes["ended_count"] = account.sign_out_everywhere(user.id)
    else:
        with transaction.atomic():
            locked = lifecycle.lock(user.id)
            if action == "unlock":
                lifecycle.clear_lockouts(locked)
                kind = AccountEvent.Kind.ACCOUNT_UNLOCKED
            elif action == "disable":
                if locked.status == UserStatus.DISABLED.value:
                    raise MembershipConflict("The account is already disabled.")
                from workeasy360.platform.authz.staff import (
                    is_last_permanent_security_administrator,
                )

                # Phase 9 audit fix: the same rule ``w360_staff_identity``
                # applies. Checked inside this transaction, with the grant
                # holders locked, so two staff cannot each disable one of the
                # last two administrators at once.
                if is_last_permanent_security_administrator(locked.id):
                    raise MembershipConflict(
                        "This is the last permanent security administrator. "
                        "Grant platform.staff_security.manage to someone else first."
                    )
                lifecycle.set_status(locked, UserStatus.DISABLED)
                kind = AccountEvent.Kind.ACCOUNT_DISABLED
            else:
                if locked.status == UserStatus.ACTIVE.value:
                    raise MembershipConflict("The account is already active.")
                lifecycle.set_status(locked, UserStatus.ACTIVE)
                kind = AccountEvent.Kind.ACCOUNT_ENABLED
            account.record_account_event(user.id, kind)
        if action == "disable":
            # A disabled account must stop working now, not at its next login.
            changes["ended_count"] = account.sign_out_everywhere(user.id)
    audit.record(
        ctx,
        f"staff.user_{action}",
        resource_type="identity.user",
        resource_id=user.id,
        changes=changes,
        reason=why,
    )
    return detail(ctx, user.id)


def record_staff_session(user: Any, *, signed_in: bool, where: dict[str, str]) -> None:
    """Record that a staff identity signed in to (or out of) the platform, in
    the control-plane audit log (Phase 9) - the account's own security trail
    already has it; this puts it beside everything that person then does as
    staff. Coarse network and device only. Never fails the sign-in: an audit
    outage is logged, and the session is still established."""
    from workeasy360.platform.authz.control_plane import acting_scope, platform_context
    from workeasy360.platform.observability.logging import get_logger

    if not getattr(user, "is_staff", False):
        return
    ctx = platform_context(user_id=user.id)
    try:
        with acting_scope(ctx):
            audit.record(
                ctx,
                "staff.signed_in" if signed_in else "staff.signed_out",
                resource_type="identity.user",
                resource_id=user.id,
                changes={
                    "network": where.get("ip_prefix", ""),
                    "device": where.get("device_label", ""),
                },
            )
    except Exception as exc:
        get_logger(__name__).error(
            "staff.session_audit_failed", error_type=type(exc).__name__
        )

"""Read queries returning DTOs.

Selectors are how other modules read organization data. They never return
model instances: a queryset over another module's model welds the caller to
its schema, and when that schema changes the caller breaks without being in
the owning module's test suite (F-6).

Keyset pagination throughout. Offset pagination degrades linearly - ``OFFSET
100000`` makes PostgreSQL scan and discard 100,000 rows - and it skips or
duplicates rows when data changes between pages
(10-performance-concurrency.md section 8).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus
from workeasy360.contracts.values.tenant import TenantContext

#: Bounded, with no unbounded variant available to callers.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class Page:
    items: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool


def membership_summaries(
    ctx: TenantContext, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
) -> Page:
    """Active memberships for the current organization, keyset-paginated.

    ``select_related("user")`` is not incidental: without it this is an N+1,
    one query per row to fetch the email. The query-count test on this
    endpoint pins that.
    """
    from workeasy360.platform.organizations.models import Membership

    limit = max(1, min(limit, MAX_PAGE_SIZE))

    queryset = (
        Membership.objects.filter(status=MembershipStatus.ACTIVE.value)
        .select_related("user")
        .order_by("-created_at", "-id")
    )

    decoded = _decode_cursor(cursor)
    if decoded is not None:
        from django.db.models import Q

        created_at, last_id = decoded
        # Tie-break on id: with equal timestamps a cursor on created_at alone
        # overlaps or skips rows at the page boundary.
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=last_id)
        )

    # Fetch one extra to determine has_more without a COUNT. A COUNT(*) on a
    # large filtered table is expensive and the answer is not needed.
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        {
            "membership_id": str(row.id),
            "user_id": str(row.user_id),
            "email": row.user.email,
            "status": row.status,
            "member_type": row.member_type,
            "is_owner": row.is_owner,
            "joined_at": row.joined_at.isoformat() if row.joined_at else None,
        }
        for row in rows
    ]

    next_cursor = (
        _encode_cursor(rows[-1].created_at, rows[-1].id) if rows and has_more else None
    )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


def active_membership_exists(
    ctx: TenantContext, membership_id: Any, *, lock: bool = False
) -> bool:
    """Whether ``membership_id`` names an active member of *this* organization.

    The check a caller needs before writing a row that points at a
    membership it did not itself read - seat assignment being the one such
    caller today (``platform.launcher.services.assign_seat``).

    **Why an explicit check rather than leaving it to the database.** Every
    tenant-to-tenant foreign key in this schema is composite *and*
    ``DEFERRABLE INITIALLY DEFERRED`` (``tenancy/migrations_helpers.py``), so
    a row pointing at another organization's membership cannot survive - but
    it is only rejected at ``COMMIT``, long after the view has built a 201.
    The request then dies with an ``IntegrityError`` on the way out and the
    caller gets a 500 for input the endpoint should simply refuse. Worse, a
    test never sees it: pytest wraps each test in a transaction that is
    rolled back rather than committed, so the deferred constraint is never
    evaluated and the bad request looks like a success.

    Returns a bool rather than raising, so the caller chooses the denial
    shape. ``Membership.objects`` is the tenant-scoped manager, so "another
    organization's membership" and "no such membership" are the same answer
    here - which is the uniform, non-disclosing one (the same property
    ``TenancyMiddleware._deny_uniform`` preserves).

    ``lock=True`` (inside the caller's transaction) keeps the answer true
    until that transaction ends: the row is held ``FOR NO KEY UPDATE``, so
    a concurrent removal - which locks it ``FOR UPDATE`` - waits, and then
    sees (and releases) whatever the caller wrote. Without it a removal
    could commit between this check and the caller's write, leaving a seat
    assigned to a removed member (Phase 5 final audit).
    """
    from workeasy360.platform.organizations.models import Membership

    queryset = Membership.objects.filter(
        id=membership_id, status=MembershipStatus.ACTIVE.value
    )
    if lock:
        return bool(
            list(queryset.select_for_update(no_key=True).values_list("id", flat=True)[:1])
        )
    return queryset.exists()


def organization_summary(ctx: TenantContext) -> dict[str, Any] | None:
    """The current organization as a DTO."""
    from workeasy360.platform.organizations.models import Organization

    org = Organization.objects.filter(id=ctx.organization_id).first()
    if org is None:
        return None
    return {
        "organization_id": str(org.id),
        "slug": org.slug,
        "display_name": org.display_name,
        "status": org.status,
        "write_allowed": org.allows_write,
    }


def _encode_cursor(created_at: Any, last_id: UUID) -> str:
    payload = json.dumps({"t": created_at.isoformat(), "i": str(last_id)})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str | None) -> tuple[Any, UUID] | None:
    """Parse a cursor, returning None if it is unusable.

    A malformed cursor yields the first page rather than an error: cursors are
    opaque to clients, they end up truncated in URLs, and a 400 here is a
    worse experience than restarting the listing.
    """
    if not cursor:
        return None
    try:
        from datetime import datetime

        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        payload = json.loads(raw)
        timestamp, last_id = payload["t"], payload["i"]
        # Both must be strings: UUID() of a JSON number, list or object
        # raises AttributeError, which was a 500 on the chooser.
        if not isinstance(timestamp, str) or not isinstance(last_id, str):
            return None
        return datetime.fromisoformat(timestamp), UUID(last_id)
    except (ValueError, KeyError, TypeError):
        return None


def all_organization_ids(*, batch_size: int = 1000) -> list[UUID]:
    """Every live tenant, for jobs that must visit each one in turn.

    Read from ``organizations_organization``, which is **control plane** and
    carries no RLS policy. That is the only way a cross-tenant enumeration
    can work: every tenant table matches zero rows when no tenant variable is
    set, by design, so a job that tried to enumerate from one would quietly
    find nothing and conclude there was no work to do.

    Callers must then enter each organization's scope individually. Running a
    single query across every tenant is forbidden even for jobs that
    legitimately span them (03-multi-tenancy.md).

    Soft-deleted organizations are excluded: there is nothing to provision or
    bill for a tenant that is being purged.
    """
    from workeasy360.platform.organizations.models import Organization

    return list(
        Organization.objects.filter(deleted_at__isnull=True)
        .order_by("created_at", "id")
        .values_list("id", flat=True)[: batch_size * 1000]
    )


#: Bounded chooser search term length - long enough for any real
#: organization name, short enough that a pathological input costs nothing
#: (Task 57).
MAX_SEARCH_LENGTH = 100


def organizations_for_user(
    user_id: UUID,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    search: str | None = None,
) -> Page:
    """Every organization this user actively belongs to - the login/org-
    chooser bootstrap query (Phase 4 Task 5; rebuilt on the access-directory
    projection in Phase 5, replacing the O(organizations-on-the-platform)
    scan D-013 recorded - see ADR-022.

    Reads ``UserOrganizationAccess`` - denormalised, RLS-exempt (its own
    docstring states the trust model), synchronised transactionally by
    ``platform.organizations.services``/``projection`` alongside every
    membership write. A single indexed query, keyset-paginated exactly like
    ``membership_summaries`` above, with the same tie-break rationale: query
    count and latency no longer grow with the size of the platform, only
    with how many organizations *this user* belongs to (Task 25).

    ``user_id`` must be the authenticated caller's own, server-verified id -
    this function has no other access control, by design (Task 18/19): it
    answers "what should this user see", never "may this request act",
    which every tenant-scoped endpoint re-resolves from ``Membership``
    independently of anything this function returns.

    ``search`` is an optional case-insensitive substring match against the
    denormalised display name only - no raw SQL, no arbitrary ORM filter
    reaches this from the caller (Task 57).
    """
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    limit = max(1, min(limit, MAX_PAGE_SIZE))

    # Exactly the order of ix_user_org_access_user (user, -updated_at, id), so
    # the index serves the sort and a page reads limit + 1 rows. The id
    # tie-break used to run DESC against the index's ASC: PostgreSQL then
    # read every one of the user's rows and sorted them for each page (found
    # by the Phase 5 final audit's EXPLAIN).
    queryset = UserOrganizationAccess.objects.filter(user_id=user_id).order_by(
        "-updated_at", "id"
    )
    if search:
        queryset = queryset.filter(
            organization_display_name__icontains=search.strip()[:MAX_SEARCH_LENGTH]
        )

    decoded = _decode_cursor(cursor)
    if decoded is not None:
        from django.db.models import Q

        updated_at, last_id = decoded
        # The redundant upper bound is what the index can use: it turns the
        # cursor into a range scan starting at the cursor, with the OR below
        # only filtering out rows tied on updated_at.
        queryset = queryset.filter(updated_at__lte=updated_at).filter(
            Q(updated_at__lt=updated_at) | Q(updated_at=updated_at, id__gt=last_id)
        )

    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        {
            "organization_id": str(row.organization_id),
            "slug": row.organization_slug,
            "display_name": row.organization_display_name,
            "status": row.organization_status,
            "membership_id": str(row.membership_id),
            "is_owner": row.is_owner,
        }
        for row in rows
    ]
    next_cursor = (
        _encode_cursor(rows[-1].updated_at, rows[-1].id) if rows and has_more else None
    )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


def organization_status(organization_id: UUID) -> OrganizationStatus | None:
    """One organization's status, without a tenant context.

    For cross-tenant jobs that must build a context for an organization they
    are visiting. Read from the **control plane**, which carries no RLS
    policy, so it answers before any scope exists.

    Exists so a job cannot invent a status. ``OrganizationStatus`` drives the
    R-10 write block, and a job that assumed ``active`` would let a suspended
    organization be written to from a background path while the request path
    correctly refused - a write-block exemption created by accident rather
    than by an ADR.
    """
    from workeasy360.platform.organizations.models import Organization

    value = (
        Organization.objects.filter(id=organization_id, deleted_at__isnull=True)
        .values_list("status", flat=True)
        .first()
    )
    return OrganizationStatus(value) if value is not None else None


def organization_created_at(organization_id: UUID) -> Any:
    """When an organization was created, or ``None`` if there is no such one."""
    from workeasy360.platform.organizations.models import Organization

    return (
        Organization.objects.filter(id=organization_id, deleted_at__isnull=True)
        .values_list("created_at", flat=True)
        .first()
    )


def organization_lifecycle(organization_id: UUID) -> dict[str, Any] | None:
    """One organization's metadata and lifecycle, without a tenant context.

    Control-plane read of the (RLS-free) ``Organization`` table. Excludes the
    platform organization, which is not a customer.
    """
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.organizations.models import Organization

    if organization_id == PLATFORM_ORGANIZATION_ID:
        return None
    row = Organization.objects.filter(id=organization_id, deleted_at__isnull=True).first()
    if row is None:
        return None
    return {
        "organization_id": str(row.id),
        "slug": row.slug,
        "display_name": row.display_name,
        "legal_name": row.legal_name,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "suspended_at": row.suspended_at.isoformat() if row.suspended_at else None,
        "suspension_reason": row.suspension_reason,
    }


def organizations_directory_page(
    *,
    search: str | None = None,
    status: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    only: Any = None,
    exclude: Any = None,
) -> Page:
    """The staff customer directory: every customer, bounded, newest first.

    **Constant query count.** One query for the page, one for the owners of
    exactly those organizations (from the chooser projection, which holds the
    owner flag without a tenant scope). Never a query per row. The platform
    organization is excluded by id. Owner is shown as an address only - the
    directory is a staff surface, and staff hold ``platform.staff_customer.view``.
    """
    from django.db.models import Q

    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.organizations.models import (
        Organization,
        UserOrganizationAccess,
    )

    limit = max(1, min(limit, MAX_PAGE_SIZE))
    queryset = (
        Organization.objects.filter(deleted_at__isnull=True)
        .exclude(id=PLATFORM_ORGANIZATION_ID)
        .order_by("-created_at", "-id")
    )
    if status:
        queryset = queryset.filter(status=status)
    if only is not None:
        # Phase 9: a caller-composed subquery of organization ids (the staff
        # Subscriptions and Entitlements views filter on commercial state this
        # module cannot see). Composed into this one statement, never listed.
        queryset = queryset.filter(id__in=only)
    if exclude is not None:
        queryset = queryset.exclude(id__in=exclude)
    if search:
        term = search.strip()[:MAX_SEARCH_LENGTH]
        queryset = queryset.filter(
            Q(display_name__icontains=term) | Q(slug__icontains=term)
        )
    decoded = _decode_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]

    owners = {
        access.organization_id: access.user.email
        for access in UserOrganizationAccess.objects.filter(
            organization_id__in=[r.id for r in rows], is_owner=True
        ).select_related("user")
    }
    items = [
        {
            "organization_id": str(r.id),
            "slug": r.slug,
            "display_name": r.display_name,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "owner_email": owners.get(r.id, ""),
        }
        for r in rows
    ]
    next_cursor = (
        _encode_cursor(rows[-1].created_at, rows[-1].id) if rows and has_more else None
    )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


def membership_counts(ctx: TenantContext) -> dict[str, int]:
    """Members by status for the current organization - counts, no identities."""
    from django.db.models import Count

    from workeasy360.platform.organizations.models import Membership

    counts = {
        row["status"]: row["n"]
        for row in Membership.objects.values("status").annotate(n=Count("id"))
    }
    return {status.value: counts.get(status.value, 0) for status in MembershipStatus}


def membership_for_user(ctx: TenantContext, user_id: UUID) -> dict[str, Any] | None:
    """One user's membership of the current organization, or ``None``."""
    from workeasy360.platform.organizations.models import Membership

    row = (
        Membership.objects.filter(user_id=user_id)
        .values("id", "status", "is_owner", "member_type")
        .first()
    )
    return dict(row) if row is not None else None


def member_status_for_email(address: str) -> str | None:
    """The status of the current organization's membership for this
    (normalised) address, ignoring removed ones - or ``None``."""
    from workeasy360.platform.organizations.models import Membership

    return (
        Membership.objects.filter(user__email=address)
        .exclude(status=MembershipStatus.REMOVED.value)
        .values_list("status", flat=True)
        .first()
    )


def organization_status_counts() -> dict[str, int]:
    """Customers by lifecycle status (the platform organization excluded)."""
    from django.db.models import Count

    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.organizations.models import Organization

    return {
        row["status"]: row["n"]
        for row in Organization.objects.filter(deleted_at__isnull=True)
        .exclude(id=PLATFORM_ORGANIZATION_ID)
        .values("status")
        .annotate(n=Count("id"))
    }


# --- Phase 8 extension: the organization console ----------------------------
#
# Everything below runs inside the caller's tenant scope (the scoped manager
# plus RLS), so "another organization's member" and "no such member" are the
# same empty answer. Identities are read through the membership join only
# (F-6: identity is reached via organizations).


def password_min_lengths_for_user(user_id: UUID) -> dict[str, int]:
    """``{organization_id: min_length}`` for the organizations this user can
    open that set a password minimum - evaluated at sign-in, when the password
    is in hand, because it is never available again (it is only stored hashed).

    Both tables are control plane (no RLS) and the filter is the caller's own
    verified user id, exactly as the organization chooser reads them.
    """
    from workeasy360.platform.organizations.models import (
        Organization,
        UserOrganizationAccess,
    )

    organization_ids = UserOrganizationAccess.objects.filter(user_id=user_id).values(
        "organization_id"
    )
    return {
        str(row["id"]): int(row["password_min_length"])
        for row in Organization.objects.filter(
            id__in=organization_ids,
            deleted_at__isnull=True,
            password_min_length__isnull=False,
        ).values("id", "password_min_length")
    }


def security_policy(ctx: TenantContext) -> dict[str, Any]:
    from workeasy360.platform.organizations.models import Organization

    org = Organization.objects.get(id=ctx.organization_id)
    return {
        "require_mfa": org.require_mfa,
        "session_max_age_minutes": org.session_max_age_minutes,
        "password_min_length": org.password_min_length,
        "ip_allowlist_enabled": org.ip_allowlist_enabled,
        "ip_allowlist": list(org.ip_allowlist or []),
        "version": org.security_policy_version,
        "updated_at": org.security_policy_updated_at.isoformat()
        if org.security_policy_updated_at
        else None,
    }


#: Statuses the member directory lists by default. ``removed`` members are
#: history, not people who can do anything, and are shown only when asked for.
_DIRECTORY_STATUSES = (
    MembershipStatus.ACTIVE.value,
    MembershipStatus.INVITED.value,
    MembershipStatus.SUSPENDED.value,
)


def member_directory(
    ctx: TenantContext,
    *,
    status: str | None = None,
    search: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page:
    """Every member of the current organization with their last sign-in -
    one query (the last sign-in is a correlated subquery on an index that
    leads with the organization), keyset-paginated newest-first."""
    from django.db.models import Max, OuterRef, Q, Subquery

    from workeasy360.platform.organizations.models import Membership, MemberSignIn

    limit = max(1, min(limit, MAX_PAGE_SIZE))
    statuses = (
        (status,)
        if status in {s.value for s in MembershipStatus}
        else _DIRECTORY_STATUSES
    )
    last_sign_in = (
        MemberSignIn.objects.filter(membership_id=OuterRef("pk"))
        .values("membership_id")
        .annotate(last=Max("occurred_at"))
        .values("last")
    )
    queryset = (
        Membership.objects.filter(status__in=statuses)
        .select_related("user")
        .annotate(last_sign_in_at=Subquery(last_sign_in))
        .order_by("-created_at", "-id")
    )
    if search:
        term = search.strip()[:MAX_SEARCH_LENGTH]
        queryset = queryset.filter(
            Q(user__email__icontains=term) | Q(user__full_name__icontains=term)
        )
    decoded = _decode_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_member_row(row) for row in rows]
    next_cursor = (
        _encode_cursor(rows[-1].created_at, rows[-1].id) if rows and has_more else None
    )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


def member(ctx: TenantContext, membership_id: UUID) -> dict[str, Any] | None:
    from workeasy360.platform.organizations.models import Membership

    row = Membership.objects.filter(id=membership_id).select_related("user").first()
    return _member_row(row) if row is not None else None


def _member_row(row: Any) -> dict[str, Any]:
    last = getattr(row, "last_sign_in_at", None)
    return {
        "membership_id": str(row.id),
        "user_id": str(row.user_id),
        "email": row.user.email,
        "name": row.user.full_name,
        "status": row.status,
        "is_owner": row.is_owner,
        "member_type": row.member_type,
        "joined_at": row.joined_at.isoformat() if row.joined_at else None,
        "added_at": row.created_at.isoformat(),
        "last_sign_in_at": last.isoformat() if last else None,
    }


def member_statistics(ctx: TenantContext, *, since: Any, now: Any) -> dict[str, int]:
    """Counts for the dashboard tiles, in two queries."""
    from django.db.models import Count, Q

    from workeasy360.platform.organizations.models import Invitation, Membership

    members = Membership.objects.aggregate(
        active=Count("id", filter=Q(status=MembershipStatus.ACTIVE.value)),
        suspended=Count("id", filter=Q(status=MembershipStatus.SUSPENDED.value)),
        invited=Count("id", filter=Q(status=MembershipStatus.INVITED.value)),
        added=Count(
            "id",
            filter=Q(created_at__gte=since) & ~Q(status=MembershipStatus.REMOVED.value),
        ),
    )
    open_invites = Q(accepted_at__isnull=True, revoked_at__isnull=True)
    invitations = Invitation.objects.aggregate(
        pending=Count("id", filter=open_invites & Q(expires_at__gt=now)),
        expired=Count("id", filter=open_invites & Q(expires_at__lte=now)),
        accepted=Count("id", filter=Q(accepted_at__gte=since)),
    )
    return {
        "active_members": members["active"],
        "suspended_members": members["suspended"],
        # An invited *membership* is a partner-provisioned owner who has not
        # accepted; ordinary invitations have no membership until accepted.
        "invited_members": members["invited"],
        "members_added": members["added"],
        "pending_invitations": invitations["pending"],
        "expired_invitations": invitations["expired"],
        "invitations_accepted": invitations["accepted"],
    }


def sign_in_series(ctx: TenantContext, *, since: Any) -> list[dict[str, Any]]:
    """Distinct members who entered the organization, per UTC day."""
    from django.db.models import Count
    from django.db.models.functions import TruncDate

    from workeasy360.platform.organizations.models import MemberSignIn

    rows = (
        MemberSignIn.objects.filter(occurred_at__gte=since)
        .annotate(day=TruncDate("occurred_at"))
        .values("day")
        .annotate(members=Count("membership_id", distinct=True), sign_ins=Count("id"))
        .order_by("day")
    )
    return [
        {
            "date": r["day"].isoformat(),
            "members": r["members"],
            "sign_ins": r["sign_ins"],
        }
        for r in rows
    ]


def most_active_members(
    ctx: TenantContext, *, since: Any, limit: int = 10
) -> list[dict[str, Any]]:
    """Members by sign-in count in the window, with their last sign-in."""
    from django.db.models import Count, Max

    from workeasy360.platform.organizations.models import MemberSignIn

    rows = list(
        MemberSignIn.objects.filter(occurred_at__gte=since)
        .values("membership_id", "user__email", "user__full_name")
        .annotate(count=Count("id"), last=Max("occurred_at"))
        .order_by("-count", "-last")[: max(1, min(limit, 50))]
    )
    return [
        {
            "membership_id": str(r["membership_id"]),
            "email": r["user__email"],
            "name": r["user__full_name"],
            "sign_ins": r["count"],
            "last_sign_in_at": r["last"].isoformat(),
        }
        for r in rows
    ]


def sign_in_page(
    ctx: TenantContext, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
) -> Page:
    """Newest-first entries into this organization, for the audit section."""
    from django.db.models import Q

    from workeasy360.platform.organizations.models import MemberSignIn

    limit = max(1, min(limit, MAX_PAGE_SIZE))
    queryset = MemberSignIn.objects.select_related("user").order_by("-occurred_at", "-id")
    decoded = _decode_cursor(cursor)
    if decoded is not None:
        stamp, last_id = decoded
        queryset = queryset.filter(
            Q(occurred_at__lt=stamp) | Q(occurred_at=stamp, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "occurred_at": r.occurred_at.isoformat(),
            "membership_id": str(r.membership_id),
            "email": r.user.email,
            "name": r.user.full_name,
            "ip_prefix": r.ip_prefix,
            "device_label": r.device_label,
        }
        for r in rows
    ]
    next_cursor = (
        _encode_cursor(rows[-1].occurred_at, rows[-1].id) if rows and has_more else None
    )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


def invitation_page(
    ctx: TenantContext,
    *,
    state: str | None = None,
    now: Any,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page:
    """Invitations newest-first. ``state``: ``pending`` (default), ``expired``,
    ``accepted``, ``revoked`` or ``all``. Never the token or its hash."""
    from django.db.models import Q

    from workeasy360.platform.organizations.models import Invitation

    limit = max(1, min(limit, MAX_PAGE_SIZE))
    open_q = Q(accepted_at__isnull=True, revoked_at__isnull=True)
    filters = {
        "pending": open_q & Q(expires_at__gt=now),
        "expired": open_q & Q(expires_at__lte=now),
        "accepted": Q(accepted_at__isnull=False),
        "revoked": Q(revoked_at__isnull=False),
        "all": Q(),
    }
    queryset = (
        Invitation.objects.filter(filters.get(state or "pending", filters["pending"]))
        .select_related("invited_by__user")
        .order_by("-created_at", "-id")
    )
    decoded = _decode_cursor(cursor)
    if decoded is not None:
        created_at, last_id = decoded
        queryset = queryset.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [invitation_row(r, now=now) for r in rows]
    next_cursor = (
        _encode_cursor(rows[-1].created_at, rows[-1].id) if rows and has_more else None
    )
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


def invitation_row(row: Any, *, now: Any) -> dict[str, Any]:
    if row.accepted_at:
        state = "accepted"
    elif row.revoked_at:
        state = "revoked"
    elif row.expires_at <= now:
        state = "expired"
    else:
        state = "pending"
    inviter = getattr(getattr(row, "invited_by", None), "user", None)
    return {
        "invitation_id": str(row.id),
        "email": row.email,
        "role": row.role_key,
        "state": state,
        "invited_by": inviter.email if inviter is not None else None,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "last_sent_at": row.last_sent_at.isoformat() if row.last_sent_at else None,
        "send_count": row.send_count,
        "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
    }


def owner_email(ctx: TenantContext) -> str:
    """The active owner's address - where an organization's billing mail goes
    when its billing account names none."""
    from workeasy360.platform.organizations.models import Membership

    return (
        Membership.objects.filter(is_owner=True, status=MembershipStatus.ACTIVE.value)
        .values_list("user__email", flat=True)
        .first()
        or ""
    )


def organization_slug(organization_id: UUID) -> str | None:
    """One organization's slug without a tenant context (control plane) - for
    building links in background work, e.g. a payment's return address."""
    from workeasy360.platform.organizations.models import Organization

    return (
        Organization.objects.filter(id=organization_id)
        .values_list("slug", flat=True)
        .first()
    )


def organization_names(organization_ids: Any) -> dict[UUID, str]:
    """Display names for these organizations - one query, for staff lists
    that page through a control-plane projection (Phase 9)."""
    from workeasy360.platform.organizations.models import Organization

    ids = [i for i in organization_ids if i is not None]
    if not ids:
        return {}
    return dict(Organization.objects.filter(id__in=ids).values_list("id", "display_name"))


def access_rows_for_users(user_ids: Any) -> dict[UUID, list[dict[str, Any]]]:
    """Each user's organizations from the access-directory projection - one
    query for a page of users (Phase 9, the staff user directory). Display
    data only; never an authorization answer (ADR-022)."""
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    ids = [i for i in user_ids if i is not None]
    result: dict[UUID, list[dict[str, Any]]] = {i: [] for i in ids}
    if not ids:
        return result
    rows = (
        UserOrganizationAccess.objects.filter(user_id__in=ids)
        .order_by("organization_display_name")
        .values(
            "user_id",
            "organization_id",
            "organization_slug",
            "organization_display_name",
            "is_owner",
        )
    )
    for row in rows:
        result[row["user_id"]].append(
            {
                "organization_id": row["organization_id"],
                "slug": row["organization_slug"],
                "name": row["organization_display_name"],
                "is_owner": row["is_owner"],
            }
        )
    return result

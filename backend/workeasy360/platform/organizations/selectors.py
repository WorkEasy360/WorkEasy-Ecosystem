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


def active_member_ids(ctx: TenantContext) -> list[UUID]:
    """Membership ids for the current organization."""
    from workeasy360.platform.organizations.models import Membership

    return list(
        Membership.objects.filter(status=MembershipStatus.ACTIVE.value).values_list(
            "id", flat=True
        )
    )


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
        return datetime.fromisoformat(payload["t"]), UUID(payload["i"])
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


def organizations_for_user(
    user_id: UUID, *, max_organizations_scanned: int = 2000
) -> list[dict[str, Any]]:
    """Every organization this user actively belongs to - the login/org-
    chooser bootstrap query (Phase 4, Task 5).

    **A known, documented scale limit, not an oversight.** ``Membership``
    carries row-level security keyed on *one* organization at a time
    (``organization_id = current_setting('app.organization_id')``,
    ``FORCE ROW LEVEL SECURITY`` - ``migrations_helpers.enable_rls``), and the
    application role is deliberately ``NOBYPASSRLS`` (ADR-009). There is
    therefore no single query that answers "which organizations is this user
    in" - not because nobody wrote one, but because the isolation model that
    protects every tenant's data makes a cross-tenant *filter* impossible by
    construction, the same reason ``all_organization_ids`` documents for
    background jobs: "running a single query across every tenant is
    forbidden even for jobs that legitimately span them."

    This function takes the only path that pattern leaves open - the one
    ``all_organization_ids`` itself is built for: enumerate the (unprotected,
    control-plane) organization list and check membership inside each one's
    own scope. That is O(organizations on the platform) per login, bounded by
    ``max_organizations_scanned`` so a very large platform degrades rather
    than hangs, and is the reason ``ix_membership_user`` - an index leading
    with ``user_id`` rather than ``organization_id`` - already exists: it is
    what keeps each per-organization membership check to an index seek rather
    than a scan, not what makes the query cross-tenant in one call.

    **The scale fix, deliberately not built now:** a denormalised, RLS-exempt
    projection table (``(user_id, organization_id, status)``, control-plane
    like ``Organization`` itself), written alongside every membership change.
    Not built in Phase 4 because every membership write path would need
    auditing to keep it synchronised, which is real scope this phase's
    brief does not ask for. Recorded as a proposed Phase 5 decision
    (docs/phase-4/05-organization-selection.md) rather than shipped
    speculatively - a projection that can silently drift from ``Membership``
    is worse than an honestly-bounded scan.
    """
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.observability.metrics import metrics
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    results: list[dict[str, Any]] = []
    with unscoped_access(
        reason="Login/org-chooser bootstrap: no tenant context exists yet",
        module="platform.organizations.selectors",
    ):
        organizations = list(
            Organization.objects.filter(deleted_at__isnull=True)
            .order_by("created_at", "id")
            .values("id", "slug", "display_name", "status")[:max_organizations_scanned]
        )
        if len(organizations) == max_organizations_scanned:
            # Truncated. Metric, not an exception: a user missing from their
            # own chooser is a real incident, but this function's contract
            # is "the organizations found within the scanned window", stated
            # precisely rather than silently wrong.
            metrics.increment("organizations_for_user_scan_truncated_total")

        for org in organizations:
            with organization_db_scope(org["id"]):
                membership = (
                    Membership.unscoped.filter(
                        user_id=user_id, status=MembershipStatus.ACTIVE.value
                    )
                    .values("id", "is_owner")
                    .first()
                )
            if membership is not None:
                results.append(
                    {
                        "organization_id": str(org["id"]),
                        "slug": org["slug"],
                        "display_name": org["display_name"],
                        "status": org["status"],
                        "membership_id": str(membership["id"]),
                        "is_owner": membership["is_owner"],
                    }
                )
    return results


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

"""Reads for the partner business panel that touch the partners tables (ADR-045).

``controlplane`` composes these with other modules' selectors; it never imports a
partners model itself (the architecture test ``test_no_cross_module_orm_access``).
Every function takes the ``partner_id`` a verified membership established.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import Count, Exists, OuterRef, Q

from workeasy360.commercial.partners import capabilities
from workeasy360.contracts.enums import (
    GrantStatus,
    PartnerHierarchyStatus,
    PartnerLinkStatus,
)
from workeasy360.contracts.values.cursor import (
    Page,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)


def live_links(partner_id: UUID) -> Any:
    """The partner's relationships that are not ended (a queryset, so a caller can
    use ``.values("organization_id")`` as a subquery without importing the model)."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    return PartnerOrganizationLink.objects.filter(partner_id=partner_id).exclude(
        status=PartnerLinkStatus.ENDED.value
    )


def live_organization_ids(partner_id: UUID) -> Any:
    return live_links(partner_id).values("organization_id")


def link_counts(partner_id: UUID) -> dict[str, int]:
    """Customers by relationship state, ended included. Two grouped counts."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    live = {
        r["status"]: r["n"]
        for r in live_links(partner_id).values("status").annotate(n=Count("id"))
    }
    ended = PartnerOrganizationLink.objects.filter(
        partner_id=partner_id, status=PartnerLinkStatus.ENDED.value
    ).count()
    return {
        "total": sum(live.values()),
        "active": live.get(PartnerLinkStatus.ACTIVE.value, 0),
        "suspended": live.get(PartnerLinkStatus.SUSPENDED.value, 0),
        "ended": ended,
    }


def hierarchy_counts(partner_id: UUID) -> dict[str, int]:
    from workeasy360.commercial.partners.models import PartnerHierarchyLink

    counts = {
        r["status"]: r["n"]
        for r in PartnerHierarchyLink.objects.filter(parent_id=partner_id)
        .exclude(status=PartnerHierarchyStatus.ENDED.value)
        .values("status")
        .annotate(n=Count("id"))
    }
    return {"total": sum(counts.values()), **counts}


def children_page(parent_id: UUID, *, limit: Any = 25, cursor: str | None = None) -> Page:
    """A parent's live sub-partners, newest first: the facts a parent may see and
    nothing of their customers or secrets. Seven queries for any page size."""
    from workeasy360.commercial.partners.models import (
        PartnerCapabilityGrant,
        PartnerHierarchyLink,
        PartnerMember,
        PartnerOrganizationLink,
        PartnerProductGrant,
    )

    limit = clamp_limit(limit, default=25, maximum=100)
    queryset = (
        PartnerHierarchyLink.objects.filter(parent_id=parent_id)
        .exclude(status=PartnerHierarchyStatus.ENDED.value)
        .select_related("child", "child__profile")
        .order_by("-created_at", "-id")
    )
    decoded = decode_cursor(cursor)
    if decoded is not None:
        stamp, last_id = decoded
        queryset = queryset.filter(
            Q(created_at__lt=stamp) | Q(created_at=stamp, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    ids = [r.child_id for r in rows]

    admins: dict[UUID, Any] = {}
    for member in (
        PartnerMember.objects.filter(
            partner_id__in=ids,
            status="active",
            role__in=sorted(capabilities.ADMIN_ROLES),
        )
        .select_related("user")
        .order_by("partner_id", "added_at")
    ):
        admins.setdefault(member.partner_id, member)
    customers = {
        r["partner_id"]: r["n"]
        for r in PartnerOrganizationLink.objects.filter(partner_id__in=ids)
        .exclude(status="ended")
        .values("partner_id")
        .annotate(n=Count("id"))
    }
    products: dict[UUID, list[str]] = {}
    for partner_id, code in (
        PartnerProductGrant.objects.filter(
            partner_id__in=ids, status=GrantStatus.ACTIVE.value
        )
        .order_by("product__code")
        .values_list("partner_id", "product__code")
    ):
        if code not in products.setdefault(partner_id, []):
            products[partner_id].append(code)
    held: dict[UUID, set[str]] = {}
    for partner_id, capability in PartnerCapabilityGrant.objects.filter(
        partner_id__in=ids, status=GrantStatus.ACTIVE.value
    ).values_list("partner_id", "capability"):
        held.setdefault(partner_id, set()).add(capability)

    items = []
    for link in rows:
        child = link.child
        admin = admins.get(child.id)
        profile = getattr(child, "profile", None)
        items.append(
            {
                "id": str(child.id),
                "link_id": str(link.id),
                "name": child.display_name,
                "code": child.code,
                "status": child.status,
                "link_status": link.status,
                "currency": child.payout_currency,
                "country": profile.country if profile else "",
                "administrator": {
                    "name": admin.user.full_name if admin else "",
                    "email": (admin.user.email_original or admin.user.email)
                    if admin
                    else "",
                    "invited": bool(admin and admin.invitation_sent_at),
                    "last_login_at": (
                        admin.user.last_login_at.isoformat()
                        if admin and admin.user.last_login_at
                        else None
                    ),
                },
                "customers": customers.get(child.id, 0),
                "products": products.get(child.id, []),
                "capabilities": sorted(held.get(child.id, set())),
                "experiences": [
                    e["key"]
                    for e in capabilities.experience_labels(held.get(child.id, set()))
                ],
                "created_at": link.created_at.isoformat(),
            }
        )
    return Page(
        items=items,
        next_cursor=(
            encode_cursor(rows[-1].created_at, rows[-1].id) if has_more else None
        ),
        has_more=has_more,
    )


def team(partner_id: UUID) -> list[Any]:
    """The partner's active members, oldest first, with their accounts (at most 200)."""
    from workeasy360.commercial.partners.models import PartnerMember

    return list(
        PartnerMember.objects.filter(partner_id=partner_id, status="active")
        .select_related("user")
        .order_by("added_at", "id")[:200]
    )


def member_user_ids(partner_id: UUID) -> set[UUID]:
    """Every user that is, or ever was, a member - so an activity entry by one of
    them can be named, and one by staff cannot."""
    from workeasy360.commercial.partners.models import PartnerMember

    return set(
        PartnerMember.objects.filter(partner_id=partner_id).values_list(
            "user_id", flat=True
        )
    )


def active_member_user_ids(partner_id: UUID) -> list[UUID]:
    from workeasy360.commercial.partners.models import PartnerMember

    return list(
        PartnerMember.objects.filter(partner_id=partner_id, status="active").values_list(
            "user_id", flat=True
        )
    )


def has_active_member(partner_id: UUID, member_id: UUID) -> bool:
    from workeasy360.commercial.partners.models import PartnerMember

    return PartnerMember.objects.filter(
        id=member_id, partner_id=partner_id, status="active"
    ).exists()


def orders_page(
    partner_id: UUID, *, order_summaries: Any, limit: Any = 25, cursor: str | None = None
) -> Page:
    """Order summaries (a queryset the payments module hands over) placed by this
    partner's customers while they were its customers, newest first."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    limit = clamp_limit(limit, default=25, maximum=100)
    related = PartnerOrganizationLink.objects.filter(
        partner_id=partner_id,
        organization_id=OuterRef("organization_id"),
        started_at__lte=OuterRef("created_at"),
    ).filter(Q(ended_at__isnull=True) | Q(ended_at__gt=OuterRef("created_at")))
    queryset = (
        order_summaries.filter(Exists(related))
        .select_related("organization")
        .order_by("-created_at", "-order_id")
    )
    decoded = decode_cursor(cursor)
    if decoded is not None:
        stamp, last_id = decoded
        queryset = queryset.filter(
            Q(created_at__lt=stamp) | Q(created_at=stamp, order_id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    live = set(
        live_links(partner_id)
        .filter(organization_id__in=[r.organization_id for r in rows])
        .values_list("organization_id", flat=True)
    )
    items = [
        {
            "id": str(r.order_id),
            "number": r.number,
            "kind": r.kind,
            "status": r.status,
            "description": r.description,
            "products": list(r.product_codes or []),
            "currency": r.currency,
            "amount_due_minor": r.amount_due_minor,
            "customer": r.organization.display_name if r.organization_id in live else "",
            "created_at": r.created_at.isoformat(),
            "paid_at": r.paid_at.isoformat() if r.paid_at else None,
        }
        for r in rows
    ]
    return Page(
        items=items,
        next_cursor=(
            encode_cursor(rows[-1].created_at, rows[-1].order_id) if has_more else None
        ),
        has_more=has_more,
    )

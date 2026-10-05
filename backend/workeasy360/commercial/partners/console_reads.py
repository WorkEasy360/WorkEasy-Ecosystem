"""Partner-owned reads for the staff consoles (Phase 10).

``controlplane`` shows staff the partners, their customers, brands and domains
side by side with commission and payout figures. It sits at the top of the
commercial layering and **may not open another module's tables** (F-6), so each
table's owner answers the questions asked of it and hands back plain values:
this module for everything partners own, ``commissions.selectors`` for the
ledger and payouts, ``pricing.selectors`` for prices, ``provisioning.selectors``
for the customer summary projection.

Nothing here authorizes: the caller (``controlplane.partner_console``) has
already required the staff permission. Nothing here writes. Every function that
serves a list takes exactly the partners of the page and answers in a fixed
number of grouped queries - never one query per partner.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Count, Exists, Max, OuterRef, Q, Subquery

from workeasy360.commercial.partners.domain import (
    OWNER_ACTIVATION_STATES,
)
from workeasy360.commercial.provisioning import selectors as provisioning
from workeasy360.contracts.enums import (
    DomainStatus,
    GrantStatus,
    PartnerLinkStatus,
    PartnerStatus,
    SubscriptionStatus,
)
from workeasy360.contracts.values.cursor import decode_cursor

#: A domain request that has not been proven yet.
AWAITING_VERIFICATION = (
    DomainStatus.REQUESTED.value,
    DomainStatus.VERIFICATION_PENDING.value,
)
DOMAIN_FILTERS = (
    "awaiting",
    DomainStatus.REQUESTED.value,
    DomainStatus.VERIFICATION_PENDING.value,
    DomainStatus.VERIFIED.value,
    DomainStatus.ACTIVE.value,
    DomainStatus.FAILED.value,
    DomainStatus.DISABLED.value,
    "none",
)
#: A hostname's progress towards serving, most advanced first; a closed request
#: (failed, disabled) ranks below every live one.
_DOMAIN_RANK = {
    DomainStatus.ACTIVE.value: 0,
    DomainStatus.VERIFIED.value: 1,
    DomainStatus.VERIFICATION_PENDING.value: 2,
    DomainStatus.REQUESTED.value: 3,
    DomainStatus.FAILED.value: 4,
    DomainStatus.DISABLED.value: 5,
}
#: The most advanced domain state wins when one partner has several.
DOMAIN_ORDER = (
    DomainStatus.ACTIVE.value,
    DomainStatus.VERIFIED.value,
    DomainStatus.VERIFICATION_PENDING.value,
    DomainStatus.REQUESTED.value,
)
#: Filter values for the owner-activation state (``not_applicable`` = no invitation).
ACTIVATION_FILTERS = tuple(OWNER_ACTIVATION_STATES)
#: The stored relationship states, plus ``live`` (active or suspended: any that
#: is not history).
LINK_STATES = (*(s.value for s in PartnerLinkStatus), "live")
SUBSCRIPTION_FILTERS = frozenset({s.value for s in SubscriptionStatus} | {"none"})


# --- building blocks ----------------------------------------------------------


def _effective_grants(now: datetime) -> Any:
    """Capability grants in force at ``now``: active, and not yet expired."""
    from workeasy360.commercial.partners.models import PartnerCapabilityGrant

    return PartnerCapabilityGrant.objects.filter(status=GrantStatus.ACTIVE.value).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )


def _holds_any(now: datetime, keys: frozenset[str]) -> Any:
    """An ``Exists`` over the outer partner: it holds one of ``keys`` right now."""
    return Exists(
        _effective_grants(now).filter(partner=OuterRef("pk"), capability__in=sorted(keys))
    )


def _live_links() -> Any:
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    return PartnerOrganizationLink.objects.exclude(status=PartnerLinkStatus.ENDED.value)


def _after(queryset: Any, cursor: str | None, field: str) -> Any:
    """Keyset continuation: rows strictly older than the cursor's ``(stamp, id)``."""
    decoded = decode_cursor(cursor)
    if decoded is None:
        return queryset
    stamp, last_id = decoded
    return queryset.filter(
        Q(**{f"{field}__lt": stamp}) | Q(**{field: stamp}, id__lt=last_id)
    )


def held_capabilities(partner_ids: list[UUID], now: datetime) -> dict[UUID, set[str]]:
    """The capabilities each of these partners holds right now - one query."""
    held: dict[UUID, set[str]] = defaultdict(set)
    if partner_ids:
        for partner_id, capability in (
            _effective_grants(now)
            .filter(partner_id__in=partner_ids)
            .values_list("partner_id", "capability")
        ):
            held[partner_id].add(capability)
    return held


# --- overview -----------------------------------------------------------------


def overview_counts(
    now: datetime,
    *,
    reseller_keys: frozenset[str],
    white_label_keys: frozenset[str],
) -> dict[str, int]:
    """The counts the overview cards show: partners by label, live customers,
    the live customers holding an active subscription, and domains awaiting proof."""
    from workeasy360.commercial.partners.models import PartnerAccount, PartnerDomain

    active = PartnerAccount.objects.filter(status=PartnerStatus.ACTIVE.value)
    live = _live_links()
    return {
        "active_partners": active.count(),
        "active_resellers": active.filter(_holds_any(now, reseller_keys)).count(),
        "white_label_enabled": active.filter(_holds_any(now, white_label_keys)).count(),
        "partner_customers": live.count(),
        # One live link per organization (a partial unique index), so counting
        # links is counting customers.
        "active_partner_subscriptions": live.filter(
            organization_id__in=provisioning.organization_ids_matching(
                subscription_status=SubscriptionStatus.ACTIVE.value
            )
        ).count(),
        "domains_awaiting_verification": PartnerDomain.objects.filter(
            status__in=AWAITING_VERIFICATION
        ).count(),
    }


# --- the reseller list ---------------------------------------------------------


def partner_rows(
    now: datetime,
    *,
    experience_keys: frozenset[str] | None,
    query: str | None,
    status: str | None,
    product: str | None,
    capability: str | None,
    cursor: str | None,
    page_size: int,
) -> tuple[list[Any], bool]:
    """One page of partner rows, newest first, and whether there is another.

    ``experience_keys`` restricts the list to partners holding one of them (the
    Reseller label); ``None`` lists every partner. Filters compose as ``AND``.
    """
    from workeasy360.commercial.partners.models import PartnerAccount, PartnerProductGrant

    queryset = PartnerAccount.objects.order_by("-created_at", "-id")
    if experience_keys is not None:
        queryset = queryset.filter(_holds_any(now, experience_keys))
    if status:
        queryset = queryset.filter(status=status)
    if query:
        queryset = queryset.filter(
            Q(display_name__icontains=query)
            | Q(code__icontains=query)
            | Q(legal_name__icontains=query)
        )
    if capability:
        queryset = queryset.filter(_holds_any(now, frozenset({capability})))
    if product:
        queryset = queryset.filter(
            Exists(
                PartnerProductGrant.objects.filter(
                    partner=OuterRef("pk"),
                    status=GrantStatus.ACTIVE.value,
                    product__code=product,
                )
            )
        )
    rows = list(_after(queryset, cursor, "created_at")[: page_size + 1])
    return rows[:page_size], len(rows) > page_size


def page_facts(partner_ids: list[UUID], now: datetime) -> dict[str, Any]:
    """The partner-owned figures of a list page, for exactly these partners:
    capabilities held, live customers, granted products, customers with an
    active subscription, and when a relationship last started or ended. Five
    queries whatever the page size."""
    from workeasy360.commercial.partners.models import (
        PartnerOrganizationLink,
        PartnerProductGrant,
    )

    out: dict[str, Any] = {
        "capabilities": defaultdict(set),
        "customers": {},
        "products": defaultdict(list),
        "subscriptions": {},
        "link_at": {},
    }
    if not partner_ids:
        return out
    out["capabilities"] = held_capabilities(partner_ids, now)

    live = _live_links().filter(partner_id__in=partner_ids)
    out["customers"] = {
        row["partner_id"]: row["n"]
        for row in live.values("partner_id").annotate(n=Count("id"))
    }
    out["subscriptions"] = {
        row["partner_id"]: row["n"]
        for row in live.filter(
            organization_id__in=provisioning.organization_ids_matching(
                subscription_status=SubscriptionStatus.ACTIVE.value
            )
        )
        .values("partner_id")
        .annotate(n=Count("id"))
    }
    for grant in (
        PartnerProductGrant.objects.filter(
            partner_id__in=partner_ids, status=GrantStatus.ACTIVE.value
        )
        .select_related("product", "plan")
        .order_by("product__code", "plan__code")
    ):
        out["products"][grant.partner_id].append(
            {
                "product_code": grant.product.code,
                "plan_code": grant.plan.code if grant.plan_id else None,
            }
        )
    for row in (
        PartnerOrganizationLink.objects.filter(partner_id__in=partner_ids)
        .values("partner_id")
        .annotate(started=Max("started_at"), ended=Max("ended_at"))
    ):
        stamps = [t for t in (row["started"], row["ended"]) if t is not None]
        if stamps:
            out["link_at"][row["partner_id"]] = max(stamps)
    return out


# --- the white-label list --------------------------------------------------------


def white_label_rows(
    now: datetime,
    *,
    white_label_keys: frozenset[str],
    query: str | None,
    status: str | None,
    domain_status: str | None,
    brand: str | None,
    cursor: str | None,
    page_size: int,
) -> dict[str, Any]:
    """One page of partners that hold branding or domain capability, with what
    they set up: ``rows``, ``has_more``, and per partner the capabilities held,
    the brand, the most advanced domain and the live customer count. Four
    queries beyond the page whatever its size."""
    from workeasy360.commercial.partners.models import (
        PartnerAccount,
        PartnerBrand,
        PartnerDomain,
        PartnerOrganizationLink,
    )

    queryset = PartnerAccount.objects.filter(_holds_any(now, white_label_keys)).order_by(
        "-created_at", "-id"
    )
    if status:
        queryset = queryset.filter(status=status)
    if query:
        queryset = queryset.filter(
            Q(display_name__icontains=query)
            | Q(code__icontains=query)
            | Exists(
                PartnerBrand.objects.filter(
                    partner=OuterRef("pk"), brand_name__icontains=query
                )
            )
            | Exists(
                PartnerDomain.objects.filter(
                    partner=OuterRef("pk"), hostname__icontains=query
                )
            )
        )
    has_brand = Exists(PartnerBrand.objects.filter(partner=OuterRef("pk")))
    if brand == "configured":
        queryset = queryset.filter(has_brand)
    elif brand == "none":
        queryset = queryset.filter(~has_brand)
    if domain_status == "none":
        queryset = queryset.filter(
            ~Exists(
                PartnerDomain.objects.filter(
                    partner=OuterRef("pk"),
                    status__in=[
                        *AWAITING_VERIFICATION,
                        DomainStatus.VERIFIED.value,
                        DomainStatus.ACTIVE.value,
                    ],
                )
            )
        )
    elif domain_status:
        wanted = (
            list(AWAITING_VERIFICATION)
            if domain_status == "awaiting"
            else [domain_status]
        )
        queryset = queryset.filter(
            Exists(
                PartnerDomain.objects.filter(partner=OuterRef("pk"), status__in=wanted)
            )
        )
    rows = list(_after(queryset, cursor, "created_at")[: page_size + 1])
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    ids = [r.id for r in rows]

    brands: dict[UUID, dict[str, Any]] = {}
    domains: dict[UUID, dict[str, Any]] = {}
    customers: dict[UUID, int] = {}
    held = held_capabilities(ids, now)
    if ids:
        brands = {
            b.partner_id: {
                "name": b.brand_name,
                "has_logo": bool(b.logo_file_id),
                "updated_at": b.updated_at,
            }
            for b in PartnerBrand.objects.filter(partner_id__in=ids)
        }
        for domain in PartnerDomain.objects.filter(partner_id__in=ids).order_by(
            "-created_at"
        ):
            best = domains.get(domain.partner_id)
            if best is None or _DOMAIN_RANK[domain.status] < _DOMAIN_RANK[best["status"]]:
                domains[domain.partner_id] = {
                    "hostname": domain.hostname,
                    "status": domain.status,
                    "updated_at": domain.updated_at,
                }
        customers = {
            r["partner_id"]: r["n"]
            for r in PartnerOrganizationLink.objects.filter(partner_id__in=ids)
            .exclude(status=PartnerLinkStatus.ENDED.value)
            .values("partner_id")
            .annotate(n=Count("id"))
        }
    return {
        "rows": rows,
        "has_more": has_more,
        "capabilities": held,
        "brands": brands,
        "domains": domains,
        "customers": customers,
    }


# --- customers and their owner invitations -------------------------------------------


def _with_latest_invite(queryset: Any) -> Any:
    """Annotate links with the state of the newest owner invitation of their
    organization: ``inv_status`` and ``inv_expires`` (both null when there is
    none). One correlated subquery per column - never a query per row."""
    from workeasy360.commercial.partners.models import PartnerOwnerInvite

    latest = PartnerOwnerInvite.objects.filter(
        organization_id=OuterRef("organization_id")
    ).order_by("-created_at")
    return queryset.annotate(
        inv_status=Subquery(latest.values("status")[:1]),
        inv_expires=Subquery(latest.values("expires_at")[:1]),
    )


def _activation_filter(state: str, now: datetime) -> Q:
    if state == "not_applicable":
        return Q(inv_status__isnull=True)
    if state == "pending":
        return Q(inv_status="pending", inv_expires__gt=now)
    if state == "expired":
        return Q(inv_status="expired") | Q(inv_status="pending", inv_expires__lte=now)
    if state == "activated":
        return Q(inv_status="accepted")
    return Q(inv_status=state)  # revoked, declined


def customer_links(
    now: datetime,
    *,
    partner_id: UUID | None,
    query: str | None,
    status: str | None,
    activation: str | None,
    subscription: str | None,
    cursor: str | None,
    page_size: int,
) -> tuple[list[Any], bool]:
    """One page of relationships (with their organization and partner), newest
    first, and whether there is another. ``partner_id=None`` spans every partner."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    queryset = PartnerOrganizationLink.objects.select_related("organization", "partner")
    if partner_id is not None:
        queryset = queryset.filter(partner_id=partner_id)
    if status == "live":
        queryset = queryset.exclude(status=PartnerLinkStatus.ENDED.value)
    elif status:
        queryset = queryset.filter(status=status)
    if query:
        queryset = queryset.filter(organization__display_name__icontains=query)
    if subscription:
        # A subscription is the customer's *current* state, so only a live
        # relationship can be asked about it.
        queryset = queryset.exclude(status=PartnerLinkStatus.ENDED.value)
        if subscription == "none":
            queryset = queryset.exclude(
                organization_id__in=provisioning.organization_ids_with_any(
                    subscription=True
                )
            )
        else:
            queryset = queryset.filter(
                organization_id__in=provisioning.organization_ids_matching(
                    subscription_status=subscription
                )
            )
    if activation:
        queryset = _with_latest_invite(queryset).filter(
            _activation_filter(activation, now)
        )
    queryset = queryset.order_by("-started_at", "-id")
    rows = list(_after(queryset, cursor, "started_at")[: page_size + 1])
    return rows[:page_size], len(rows) > page_size


def latest_invites(organization_ids: list[UUID]) -> dict[UUID, Any]:
    """The newest owner invitation of each organization, with its invitee - one
    query. The rows carry the token *hash*; nothing that reads them returns it."""
    from workeasy360.commercial.partners.models import PartnerOwnerInvite

    if not organization_ids:
        return {}
    return {
        row.organization_id: row
        for row in PartnerOwnerInvite.objects.filter(organization_id__in=organization_ids)
        .select_related("user")
        .order_by("organization_id", "-created_at")
        .distinct("organization_id")
    }


# --- one partner ------------------------------------------------------------------


def summary_facts(partner_id: UUID, now: datetime) -> dict[str, Any]:
    """The partner-owned facts of the overview tab, in a fixed handful of
    queries: the grants in force, relationships by status, customers whose owner
    has not activated yet, domains by status, whether a brand is set, and the
    partner's active administrators."""
    from workeasy360.commercial.partners.models import (
        PartnerBrand,
        PartnerDomain,
        PartnerMember,
        PartnerOrganizationLink,
    )

    live = _with_latest_invite(
        PartnerOrganizationLink.objects.filter(partner_id=partner_id).exclude(
            status=PartnerLinkStatus.ENDED.value
        )
    )
    return {
        "grants": [
            {
                "key": g.capability,
                "granted_at": g.granted_at,
                "expires_at": g.expires_at,
            }
            for g in _effective_grants(now)
            .filter(partner_id=partner_id)
            .order_by("capability")
        ],
        "links_by_status": {
            r["status"]: r["n"]
            for r in PartnerOrganizationLink.objects.filter(partner_id=partner_id)
            .values("status")
            .annotate(n=Count("id"))
        },
        "owner_activation_pending": live.filter(
            _activation_filter("pending", now) | _activation_filter("expired", now)
        ).count(),
        "domains_by_status": {
            r["status"]: r["n"]
            for r in PartnerDomain.objects.filter(partner_id=partner_id)
            .values("status")
            .annotate(n=Count("id"))
        },
        "brand_configured": PartnerBrand.objects.filter(partner_id=partner_id).exists(),
        "administrators": [
            {"password_set": m.user.has_usable_password()}
            for m in PartnerMember.objects.filter(
                partner_id=partner_id,
                status="active",
                role="admin",
                user__status="active",
            ).select_related("user")
        ],
    }


def audit_resource_ids(partner_id: UUID) -> list[UUID]:
    """The ids an audit entry can name to be about this partner directly: the
    partner row and its brand."""
    from workeasy360.commercial.partners.models import PartnerBrand

    return [
        partner_id,
        *PartnerBrand.objects.filter(partner_id=partner_id).values_list("id", flat=True),
    ]


def product_grant_counts() -> dict[str, int]:
    """``{product_code: number of ACTIVE partners that may sell it}`` - the
    reseller channel's availability for the Super Admin product console. A
    grant held by a partner that is not itself active sells nothing, so it is
    not counted. One grouped query."""
    from workeasy360.commercial.partners.models import PartnerProductGrant

    rows = (
        PartnerProductGrant.objects.filter(
            status=GrantStatus.ACTIVE.value, partner__status=PartnerStatus.ACTIVE.value
        )
        .values("product__code")
        .annotate(partners=Count("partner_id", distinct=True))
    )
    return {row["product__code"]: int(row["partners"]) for row in rows}

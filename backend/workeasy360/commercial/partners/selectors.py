"""Partner-channel reads, returning DTOs.

Every function here that serves a *partner* takes a ``partner_id`` and filters
on it. That id is always the one a server-verified ``PartnerMember`` row
established (``access.partner_actor``), never a value from a request; there is
no selector that accepts "a customer" and infers which partner may see it.

Ids are looked up **scoped to the caller**: ``link_for(partner_id, org_id)``,
``brand_for(partner_id)``. A guessed identifier that belongs to another
partner therefore simply does not resolve - the isolation is in the query
predicate, so it cannot be forgotten in a caller. Whether "does not resolve"
is a 404 is the caller's job, and every partner-facing service makes it one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from workeasy360.commercial.partners import capabilities, portal
from workeasy360.contracts.enums import (
    DomainStatus,
    GrantStatus,
    PartnerLinkStatus,
    PartnerMemberRole,
    PartnerStatus,
)
from workeasy360.contracts.values.cursor import (
    Page,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)

DEFAULT_PAGE = 25
MAX_PAGE = 100


def _limit(limit: Any) -> int:
    return clamp_limit(limit, default=DEFAULT_PAGE, maximum=MAX_PAGE)


# --- partners -------------------------------------------------------------


def partner_row(partner_id: UUID) -> Any | None:
    """The model row, for the services in this module only."""
    from workeasy360.commercial.partners.models import PartnerAccount

    return PartnerAccount.objects.filter(id=partner_id).first()


def code_taken(code: str) -> bool:
    """Whether a partner already uses this code (a dry-run check: the command
    that creates the partner is what decides a race for it)."""
    from workeasy360.commercial.partners.models import PartnerAccount

    return PartnerAccount.objects.filter(code=code).exists()


def partner_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "display_name": row.display_name,
        "legal_name": row.legal_name,
        "contact_name": row.contact_name,
        "contact_email": row.contact_email,
        "payout_currency": row.payout_currency,
        "status": row.status,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "suspended_at": row.suspended_at.isoformat() if row.suspended_at else None,
        "terminated_at": row.terminated_at.isoformat() if row.terminated_at else None,
        "status_reason": row.status_reason,
    }


def partners_page(
    *,
    status: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_PAGE,
    cursor: str | None = None,
) -> Page:
    """Staff partner directory: keyset-paginated, one query for the page."""
    from workeasy360.commercial.partners.models import PartnerAccount

    limit = _limit(limit)
    queryset = PartnerAccount.objects.order_by("-created_at", "-id")
    if status:
        queryset = queryset.filter(status=status)
    if query:
        term = query.strip()[:80]
        queryset = queryset.filter(
            Q(display_name__icontains=term) | Q(code__icontains=term)
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
    return Page(
        items=[partner_dict(r) for r in rows],
        next_cursor=encode_cursor(rows[-1].created_at, rows[-1].id) if has_more else None,
        has_more=has_more,
    )


def partner_detail(partner_id: UUID) -> dict[str, Any] | None:
    """A partner with its live capabilities, members, products and counts."""
    from workeasy360.commercial.partners.models import (
        PartnerCapabilityGrant,
        PartnerMember,
        PartnerOrganizationLink,
        PartnerProductGrant,
    )
    from workeasy360.platform.identity.selectors import users_with_second_factor

    row = partner_row(partner_id)
    if row is None:
        return None
    now = timezone.now()
    caps = [
        {
            "capability": g.capability,
            "granted_at": g.granted_at.isoformat(),
            "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            "is_effective": g.expires_at is None or g.expires_at > now,
        }
        for g in PartnerCapabilityGrant.objects.filter(
            partner_id=partner_id, status=GrantStatus.ACTIVE.value
        ).order_by("capability")
    ]
    member_rows = list(
        PartnerMember.objects.filter(partner_id=partner_id, status="active")
        .select_related("user")
        .order_by("added_at")
    )
    members = [
        {
            "id": str(m.id),
            "user_id": str(m.user_id),
            "email": m.user.email,
            "role": m.role,
            "added_at": m.added_at.isoformat(),
            # A person named before they ever signed in has no password until
            # they follow the emailed link: "invitation pending", not an error.
            "password_set": m.user.has_usable_password(),
            "full_name": m.user.full_name,
            "last_login_at": m.user.last_login_at.isoformat()
            if m.user.last_login_at
            else None,
            "invitation_sent_at": m.invitation_sent_at.isoformat()
            if m.invitation_sent_at
            else None,
            "invitation_deferred": m.invitation_deferred,
        }
        for m in member_rows
    ]
    with_mfa = users_with_second_factor(m.user_id for m in member_rows)
    for entry in members:
        entry["mfa_enabled"] = UUID(entry["user_id"]) in with_mfa
    products = [
        {
            "id": str(g.id),
            "product_code": g.product.code,
            "plan_code": g.plan.code if g.plan_id else None,
            "trial_allowed": g.trial_allowed,
            "portal_visible": g.portal_visible,
        }
        for g in PartnerProductGrant.objects.filter(
            partner_id=partner_id, status=GrantStatus.ACTIVE.value
        )
        .select_related("product", "plan")
        .order_by("product__code")
    ]
    payload = partner_dict(row)
    payload.update(
        {
            "capabilities": caps,
            "members": members,
            "product_grants": products,
            "live_customers": PartnerOrganizationLink.objects.filter(
                partner_id=partner_id
            )
            .exclude(status=PartnerLinkStatus.ENDED.value)
            .count(),
        }
    )
    return payload


def active_capabilities(
    partner_id: UUID, *, at: datetime | None = None
) -> frozenset[str]:
    """Capabilities in force at ``at`` (default now). Read from the database
    every time: a revocation must apply to the next check (Task 61)."""
    from workeasy360.commercial.partners.models import PartnerCapabilityGrant

    moment = at or timezone.now()
    return frozenset(
        PartnerCapabilityGrant.objects.filter(
            partner_id=partner_id, status=GrantStatus.ACTIVE.value
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=moment))
        .values_list("capability", flat=True)
    )


def had_capability_at(partner_id: UUID, capability: str, moment: datetime) -> bool:
    """Whether ``capability`` was in force at ``moment`` - history-aware.

    Commission eligibility is decided as of the *sale*, not as of today, so a
    partner revoked later still earned on what it sold while entitled, and one
    granted later did not retroactively earn on earlier sales.
    """
    from workeasy360.commercial.partners.models import PartnerCapabilityGrant

    return (
        PartnerCapabilityGrant.objects.filter(
            partner_id=partner_id, capability=capability, granted_at__lte=moment
        )
        .filter(Q(revoked_at__isnull=True) | Q(revoked_at__gt=moment))
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=moment))
        .exists()
    )


# --- members --------------------------------------------------------------


def member_of(partner_id: UUID, user_id: UUID) -> Any | None:
    """The user's active membership of this partner, or None."""
    from workeasy360.commercial.partners.models import PartnerMember

    return PartnerMember.objects.filter(
        partner_id=partner_id,
        user_id=user_id,
        status="active",
        user__status="active",
    ).first()


def has_been_member(partner_id: UUID, user_id: UUID) -> bool:
    """Whether ``user_id`` is, or ever was, one of this partner's people.

    For the self-dealing guards: a partner's own member must not become the
    owner of a customer that partner earns on, and a removal just before the
    fact does not launder that.
    """
    from workeasy360.commercial.partners.models import PartnerMember

    return PartnerMember.objects.filter(partner_id=partner_id, user_id=user_id).exists()


def partners_for_user(user_id: UUID) -> list[dict[str, Any]]:
    """The partners this user works for, with what each lets them see.

    Feeds the portal's navigation. It returns the *capabilities*, so the UI is
    derived from capability rows and never from a partner type (Task 79).
    """
    from workeasy360.commercial.partners.models import PartnerMember

    memberships = list(
        PartnerMember.objects.filter(user_id=user_id, status="active")
        .select_related("partner")
        .order_by("partner__display_name")
    )
    if not memberships:
        return []
    result = []
    for m in memberships:
        result.append(
            {
                "partner_id": str(m.partner_id),
                "code": m.partner.code,
                "display_name": m.partner.display_name,
                "status": m.partner.status,
                "role": m.role,
                "is_admin": m.role == PartnerMemberRole.ADMIN.value,
                "capabilities": sorted(active_capabilities(m.partner_id)),
            }
        )
    return result


# --- relationships --------------------------------------------------------


def link_for(partner_id: UUID, organization_id: UUID) -> Any | None:
    """The *live* (active or suspended) link between exactly this pair."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    return (
        PartnerOrganizationLink.objects.filter(
            partner_id=partner_id, organization_id=organization_id
        )
        .exclude(status=PartnerLinkStatus.ENDED.value)
        .first()
    )


def live_link_for_organization(organization_id: UUID) -> Any | None:
    """The one live link an organization has, whichever partner it names."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    return (
        PartnerOrganizationLink.objects.filter(organization_id=organization_id)
        .exclude(status=PartnerLinkStatus.ENDED.value)
        .select_related("partner")
        .first()
    )


def link_at(organization_id: UUID, moment: datetime) -> Any | None:
    """The link that was in force at ``moment`` - history-aware, for commission."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    return (
        PartnerOrganizationLink.objects.filter(
            organization_id=organization_id, started_at__lte=moment
        )
        .filter(Q(ended_at__isnull=True) | Q(ended_at__gt=moment))
        .first()
    )


def partner_customers_page(
    partner_id: UUID,
    *,
    status: str | None = None,
    limit: int = DEFAULT_PAGE,
    cursor: str | None = None,
) -> Page:
    """One partner's portfolio: bounded fields, no member data, no PII.

    Two queries for any page size: the links with their organizations, then
    the summary projection for exactly those organizations (never a query per
    customer). Deliberately absent: owner and member details, billing
    contact, tax reference and anything from the customer's own audit trail
    - a partner sees what it sold and whether it is healthy, not who works
    there (Task 123).
    """
    from workeasy360.commercial.partners.models import PartnerOrganizationLink
    from workeasy360.commercial.provisioning.selectors import summaries_for_organizations

    limit = _limit(limit)
    queryset = PartnerOrganizationLink.objects.filter(partner_id=partner_id)
    if status:
        queryset = queryset.filter(status=status)
    queryset = queryset.select_related("organization").order_by("-started_at", "-id")
    decoded = decode_cursor(cursor)
    if decoded is not None:
        stamp, last_id = decoded
        queryset = queryset.filter(
            Q(started_at__lt=stamp) | Q(started_at=stamp, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    # An ended relationship is history: the partner keeps the fact that it
    # served this customer and when (commission on those sales stays
    # visible in its ledger), but none of the customer's *current* state -
    # name, status, subscription, products - which may by now belong to
    # another partner's relationship (Phase 7 final audit).
    live = [r for r in rows if r.status != PartnerLinkStatus.ENDED.value]
    summaries = summaries_for_organizations(r.organization_id for r in live)
    invites = _latest_owner_invites(partner_id, [r.organization_id for r in live])
    items = []
    for link in rows:
        is_live = link.status != PartnerLinkStatus.ENDED.value
        summary = summaries.get(link.organization_id) if is_live else None
        items.append(
            {
                "organization_id": str(link.organization_id),
                "name": link.organization.display_name if is_live else "",
                "organization_status": link.organization.status if is_live else "",
                "link_status": link.status,
                "started_at": link.started_at.isoformat(),
                "ended_at": link.ended_at.isoformat() if link.ended_at else None,
                "subscription_status": summary.subscription_status if summary else "",
                "products": list(summary.product_codes) if summary else [],
                "owner_activation": _activation_of(invites.get(link.organization_id)),
            }
        )
    return Page(
        items=items,
        next_cursor=encode_cursor(rows[-1].started_at, rows[-1].id) if has_more else None,
        has_more=has_more,
    )


def _latest_owner_invites(
    partner_id: UUID, organization_ids: list[UUID]
) -> dict[UUID, Any]:
    """The newest owner invitation *this partner* issued for each organization -
    one query. A partner sees the state of its own invitations and no one else's."""
    from workeasy360.commercial.partners.models import PartnerOwnerInvite

    if not organization_ids:
        return {}
    return {
        row.organization_id: row
        for row in PartnerOwnerInvite.objects.filter(
            partner_id=partner_id, organization_id__in=organization_ids
        )
        .order_by("organization_id", "-created_at")
        .distinct("organization_id")
    }


def _activation_of(invite: Any) -> dict[str, Any]:
    """What a partner may see of its customer's owner activation: the state and
    its dates - never the address it was sent to, the token or its hash."""
    from workeasy360.commercial.partners import activation

    if invite is None:
        return {"activation": "not_applicable", "expires_at": None, "send_count": 0}
    state = activation.public_state(invite)
    return {
        "activation": state["activation"],
        "expires_at": state["expires_at"],
        "send_count": state["send_count"],
    }


# --- product scope --------------------------------------------------------


def may_sell(partner_id: UUID, *, product_code: str, plan_code: str) -> bool:
    """Whether an active grant covers this product (and plan)."""
    from workeasy360.commercial.partners.models import PartnerProductGrant

    return (
        PartnerProductGrant.objects.filter(
            partner_id=partner_id,
            status=GrantStatus.ACTIVE.value,
            product__code=product_code,
        )
        .filter(Q(plan__isnull=True) | Q(plan__code=plan_code))
        .exists()
    )


def granted_products(partner_id: UUID) -> list[dict[str, Any]]:
    from workeasy360.commercial.partners.models import PartnerProductGrant

    return [
        {
            "grant_id": str(g.id),
            "product_code": g.product.code,
            "product_name": g.product.name,
            "plan_code": g.plan.code if g.plan_id else None,
            "trial_allowed": g.trial_allowed,
            "portal_visible": g.portal_visible,
        }
        for g in PartnerProductGrant.objects.filter(
            partner_id=partner_id, status=GrantStatus.ACTIVE.value
        )
        .select_related("product", "plan")
        .order_by("product__code", "plan__code")
    ]


def product_choices_for_organization(organization_id: UUID) -> dict[str, dict[str, bool]]:
    """What the partner this customer belongs to chose for each product it may
    sell: ``{product_code: {"trial_allowed": bool, "portal_visible": bool}}``.

    Empty when the organization has no live partner relationship, so a direct
    customer is never touched. A product the partner holds several plan-level
    grants for is allowed (or visible) when *any* of them is. Two queries.
    """
    from workeasy360.commercial.partners.models import PartnerProductGrant

    link = live_link_for_organization(organization_id)
    if link is None:
        return {}
    choices: dict[str, dict[str, bool]] = {}
    for grant in PartnerProductGrant.objects.filter(
        partner_id=link.partner_id, status=GrantStatus.ACTIVE.value
    ).select_related("product"):
        entry = choices.setdefault(
            grant.product.code, {"trial_allowed": False, "portal_visible": False}
        )
        entry["trial_allowed"] = entry["trial_allowed"] or grant.trial_allowed
        entry["portal_visible"] = entry["portal_visible"] or grant.portal_visible
    return choices


def trial_blocked_for_organization(organization_id: UUID, product_code: str) -> bool:
    """Whether the partner this customer belongs to turned the product's trial
    off for its customers. Only ever ``True`` for a product the partner sells
    *and* chose not to trial: a direct customer, or a product the partner holds
    no grant for, is never blocked here."""
    entry = product_choices_for_organization(organization_id).get(str(product_code))
    return entry is not None and not entry["trial_allowed"]


def hidden_product_codes_for_organization(organization_id: UUID) -> frozenset[str]:
    """Products the customer's white-label partner does not show on its portal.

    Only a partner that is actually white-label (it holds the pair and has a
    brand - the same test as :func:`brand_for_organization`) hides anything, and
    what it hides is *discovery*: the Explore page. A product the customer
    already holds still launches, and no entitlement is read or changed here."""
    if brand_for_organization(organization_id) is None:
        return frozenset()
    return frozenset(
        code
        for code, entry in product_choices_for_organization(organization_id).items()
        if not entry["portal_visible"]
    )


# --- branding and domains -------------------------------------------------


def brand_for(partner_id: UUID) -> Any | None:
    from workeasy360.commercial.partners.models import PartnerBrand

    return PartnerBrand.objects.filter(partner_id=partner_id).first()


def brand_dict(row: Any) -> dict[str, Any]:
    return {
        "brand_name": row.brand_name,
        "logo_file_id": str(row.logo_file_id) if row.logo_file_id else None,
        "accent_color": row.accent_color,
        "theme": row.theme,
        "support_email": row.support_email,
        "support_url": row.support_url,
        "portal": portal.view(row.portal),
        "version": row.version,
        "updated_at": row.updated_at.isoformat(),
    }


def domains_for(partner_id: UUID) -> list[Any]:
    from workeasy360.commercial.partners.models import PartnerDomain

    return list(
        PartnerDomain.objects.filter(partner_id=partner_id).order_by("-created_at")
    )


def domain_for(partner_id: UUID, domain_id: UUID) -> Any | None:
    """A domain **of this partner** - another partner's id does not resolve."""
    from workeasy360.commercial.partners.models import PartnerDomain

    return PartnerDomain.objects.filter(partner_id=partner_id, id=domain_id).first()


def domain_dict(row: Any, *, txt_name: str) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "hostname": row.hostname,
        "status": row.status,
        "verification_record": {
            "type": "TXT",
            "name": txt_name,
            "value": row.verification_token,
        },
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "last_checked_at": row.last_checked_at.isoformat()
        if row.last_checked_at
        else None,
        "failure_code": row.failure_code,
        "version": row.version,
    }


def active_brand_for_host(hostname: str) -> dict[str, Any] | None:
    """Presentation metadata for an ACTIVE domain, and nothing about who may
    do what. The result carries no partner id, no capability and no tenant:
    a host is not an identity (Task 35)."""
    from workeasy360.commercial.partners.models import PartnerDomain

    domain = (
        PartnerDomain.objects.filter(hostname=hostname, status=DomainStatus.ACTIVE.value)
        .select_related("partner", "partner__brand")
        .first()
    )
    if domain is None or domain.partner.status != PartnerStatus.ACTIVE.value:
        return None
    # Serving a white-label host is the exercise of two capabilities, not a
    # side effect of rows written while they were held: revoking either one
    # takes the host off air at once (the partner could no longer even
    # disable it itself, since that needs ``domain.manage``).
    if not {capabilities.BRANDING_MANAGE, capabilities.DOMAIN_MANAGE} <= (
        active_capabilities(domain.partner_id)
    ):
        return None
    brand = getattr(domain.partner, "brand", None)
    if brand is None:
        return None
    return {
        "brand_name": brand.brand_name,
        "logo_file_id": str(brand.logo_file_id) if brand.logo_file_id else None,
        "accent_color": brand.accent_color,
        "theme": brand.theme,
        "support_email": brand.support_email,
        "support_url": brand.support_url,
        "portal": portal.public_view(brand.portal),
    }


def brand_for_organization(organization_id: UUID) -> dict[str, Any] | None:
    """Presentation metadata for a customer's *own* organization (Phase 11,
    Step 30) - resolved through its live partner link, never a Host header.

    The same two capabilities as :func:`active_brand_for_host`
    (``branding.manage`` and ``domain.manage`` - "white-label" is exactly
    that pair, held together) and the same "no brand row, no answer" rule.
    Deliberately independent of any domain or its verification: this is the
    canonical Workeasy360 app showing a customer their *own* partner's brand,
    not a custom-domain sign-in, so none of D-035's concerns (CSRF trusted
    origins, TLS for an arbitrary hostname) apply here - the request never
    left workeasy360's own origin.
    """
    from workeasy360.contracts.enums import PartnerStatus

    link = live_link_for_organization(organization_id)
    if link is None or link.partner.status != PartnerStatus.ACTIVE.value:
        return None
    if not {
        capabilities.BRANDING_MANAGE,
        capabilities.DOMAIN_MANAGE,
    } <= active_capabilities(link.partner_id):
        return None
    brand = brand_for(link.partner_id)
    if brand is None:
        return None
    return {
        "brand_name": brand.brand_name,
        "logo_file_id": str(brand.logo_file_id) if brand.logo_file_id else None,
        "accent_color": brand.accent_color,
        "theme": brand.theme,
        "support_email": brand.support_email,
        "support_url": brand.support_url,
        "portal": portal.public_view(brand.portal),
    }


def invitation_identity(partner: Any) -> dict[str, Any]:
    """Who an email sent *in this partner's name* says it is from.

    The partner's own name, unless it is white-label (it holds the pair and has
    a brand) **and** chose "use my brand for invitations" - then its brand name,
    its sender display name and its reply-to address. Always text: the address
    the mail is sent from stays the deployment's, and nothing here is read by an
    access decision.
    """
    plain = {"partner_name": partner.display_name, "envelope": {}}
    brand = brand_for(partner.id)
    if brand is None:
        return plain
    if not {capabilities.BRANDING_MANAGE, capabilities.DOMAIN_MANAGE} <= (
        active_capabilities(partner.id)
    ):
        return plain
    settings = portal.view(brand.portal)
    if not settings["use_for_invitations"]:
        return plain
    return {
        "partner_name": brand.brand_name,
        "envelope": {
            "sender_name": settings["sender_name"] or brand.brand_name,
            "reply_to": settings["reply_to_email"] or brand.support_email,
        },
    }


def proven_hostnames(partner_id: UUID) -> list[str]:
    """The partner's verified or active hostnames (those a lookup can serve)."""
    from workeasy360.commercial.partners.models import PartnerDomain

    return list(
        PartnerDomain.objects.filter(
            partner_id=partner_id,
            status__in=[DomainStatus.VERIFIED.value, DomainStatus.ACTIVE.value],
        ).values_list("hostname", flat=True)
    )


def all_linked_organization_ids() -> list[UUID]:
    """Every organization that has ever had a partner relationship."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    return list(
        PartnerOrganizationLink.objects.values_list(
            "organization_id", flat=True
        ).distinct()
    )


def links_for_organizations(organization_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    """The live partner relationship of each organization - one query.

    For a *staff* surface (the customer directory). A partner-facing page never
    calls this: a partner sees only its own relationships.
    """
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    if not organization_ids:
        return {}
    return {
        link.organization_id: {
            "partner_id": str(link.partner_id),
            "partner_code": link.partner.code,
            "partner_name": link.partner.display_name,
            "link_status": link.status,
            "partner_status": link.partner.status,
            "started_at": link.started_at.isoformat(),
        }
        for link in PartnerOrganizationLink.objects.filter(
            organization_id__in=organization_ids
        )
        .exclude(status=PartnerLinkStatus.ENDED.value)
        .select_related("partner")
    }


def status_counts() -> dict[str, int]:
    """Partners by lifecycle status."""
    from django.db.models import Count

    from workeasy360.commercial.partners.models import PartnerAccount

    return {
        row["status"]: row["n"]
        for row in PartnerAccount.objects.values("status").annotate(n=Count("id"))
    }


def has_active_membership(user_id: UUID) -> bool:
    from workeasy360.commercial.partners.models import PartnerMember

    return PartnerMember.objects.filter(user_id=user_id, status="active").exists()

"""A partner's network: its sub-partners and its affiliate programme (ADR-045).

Read models for the Sub-partners, Affiliates and Users screens. The commands -
create, activate, suspend, equip (``partners.hierarchy``), invite and re-role
(``partners.services``) - are elsewhere; this module only answers "what do I
have", with a fixed number of queries whatever the page size.

**What a parent sees of a sub-partner**: its name, status, currency, country,
administrator (the person the parent invited), counts of customers and of
products, when its administrator last signed in and when it was created. Not
its customers, its profile details, its payout contact, its commission, its
members' other accounts or anything it was given by Workeasy360 alone.

**What the affiliate screens never show**: who a referred visitor was. A
referral row carries no name, email or address - the screens show a campaign
label, the dates and the commission it earned.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.utils import timezone

from workeasy360.commercial.commissions import referrals
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.controlplane.partner_portal import NOT_IMPLEMENTED
from workeasy360.commercial.partners import capabilities, hierarchy, panel_selectors
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.contracts.values.cursor import (
    Page,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.identity import portals as portal_links

SUBPARTNER_VIEW: Final = "platform.partner_subpartner.view"
SUBPARTNER_MANAGE: Final = "platform.partner_subpartner.manage"
REFERRAL_VIEW: Final = "platform.partner_referral.view"
MEMBER_VIEW: Final = "platform.partner_member.view"
COMMISSION_VIEW: Final = "platform.partner_commission.view"


# --- sub-partners ------------------------------------------------------------------


@in_scope
def sub_partners(
    ctx: TenantContext, *, limit: Any = 25, cursor: str | None = None
) -> Page:
    """The caller's sub-partners, newest first (see ``panel_selectors.children_page``)."""
    authz.require(ctx, SUBPARTNER_VIEW)
    return panel_selectors.children_page(ctx.partner_id, limit=limit, cursor=cursor)


@in_scope
def sub_partner_options(ctx: TenantContext) -> dict[str, Any]:
    """What the caller may hand a new sub-partner: only what it holds itself, and
    only what a parent may delegate (never money or custom domains)."""
    authz.require(ctx, SUBPARTNER_VIEW)
    held = partner_selectors.active_capabilities(ctx.partner_id)
    delegable = sorted(held & capabilities.DELEGABLE_CAPABILITIES)
    whole = [
        {"code": g["product_code"], "name": g["product_name"]}
        for g in partner_selectors.granted_products(ctx.partner_id)
        if g["plan_code"] is None
    ]
    return {
        "capabilities": [
            {
                "key": key,
                "label": capabilities.SHORT_LABELS.get(key, key),
                "description": capabilities.CAPABILITIES[key].label,
            }
            for key in delegable
        ],
        "products": whole,
        "depth": {
            "max": hierarchy.MAX_DEPTH,
            "can_have_children": hierarchy.depth_above(ctx.partner_id)
            < hierarchy.MAX_DEPTH,
        },
        "not_implemented": {
            "subpartner_pricing": NOT_IMPLEMENTED["subpartner_pricing"],
            "subpartner_commission": NOT_IMPLEMENTED["subpartner_commission"],
        },
    }


# --- affiliates ----------------------------------------------------------------------


def referral_url(code: str) -> str:
    """The link a visitor follows (ADR-053): ``/r/<code>`` on the customer
    portal, which records the visit and opens the link's product page (or
    sign-up). The origin comes from configuration, never from a request. A
    code is public by design and grants nothing."""
    return f"{portal_links.app_url()}/r/{code}"


@in_scope
def affiliates(ctx: TenantContext) -> dict[str, Any]:
    """The affiliate programme as the partner sees it.

    ``programme`` is the platform's settings (the attribution window, the
    commission rules naming this partner) - read, never changed from here.
    ``codes`` are the partner's own referral links. ``affiliates`` are its
    sub-partners that hold a referral capability, with counts only.
    """
    authz.require(ctx, REFERRAL_VIEW)
    now = timezone.now()
    permissions = authz.effective_permissions(ctx)
    partner_id = ctx.partner_id
    out: dict[str, Any] = {
        "programme": {
            "enabled": True,
            "attribution_window_days": int(referrals.capture_window().days),
            "visible_to_you": [
                "campaign label",
                "date captured",
                "date converted",
                "commission earned",
            ],
            "never_shown": ["name", "email", "phone", "country"],
            # Eligible offerings and payout settings, as the platform holds them:
            # a partner earns on the products it has been granted, and is paid in
            # its own payout currency by Workeasy360, outside this platform.
            "eligible_products": sorted(
                {
                    g["product_name"]
                    for g in partner_selectors.granted_products(partner_id)
                }
            ),
            "payout_currency": partner_selectors.partner_row(partner_id).payout_currency,
            "commission_rules": (
                commission_selectors.partner_rules(partner_id, now=now)
                if COMMISSION_VIEW in permissions
                else None
            ),
        },
        "summary": commission_selectors.referral_summary(partner_id),
        "codes": [
            {**c, "url": referral_url(c["code"])}
            for c in commission_selectors.referral_codes(partner_id)
        ],
        "affiliates": [],
        "not_implemented": {
            "affiliate_program_settings": NOT_IMPLEMENTED["affiliate_program_settings"]
        },
    }
    if SUBPARTNER_VIEW in permissions:
        children = sub_partners(ctx, limit=100).items
        affiliate_children = [
            c
            for c in children
            if {"referral.create", "referral.view"} & set(c["capabilities"])
        ]
        facts = commission_selectors.referral_facts(
            [UUID(c["id"]) for c in affiliate_children]
        )
        out["affiliates"] = [
            {
                "id": c["id"],
                "name": c["name"],
                "status": c["status"],
                "administrator_email": c["administrator"]["email"],
                "active_codes": facts.get(UUID(c["id"]), {}).get("active_codes", 0),
                "captured": facts.get(UUID(c["id"]), {}).get("captured", 0),
                "converted": facts.get(UUID(c["id"]), {}).get("converted", 0),
                "created_at": c["created_at"],
            }
            for c in affiliate_children
        ]
    return out


@in_scope
def conversions(
    ctx: TenantContext, *, limit: Any = 25, cursor: str | None = None
) -> Page:
    """Converted referrals of the caller, with the commission each earned (shown
    only to a role that may see commission)."""
    authz.require(ctx, REFERRAL_VIEW)
    page = commission_selectors.partner_conversions_page(
        ctx.partner_id, limit=limit, cursor=cursor
    )
    if COMMISSION_VIEW in authz.effective_permissions(ctx):
        return page
    return Page(
        items=[{**row, "commission_minor": None, "currency": ""} for row in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


# --- the team -------------------------------------------------------------------------


@in_scope
def members(ctx: TenantContext) -> dict[str, Any]:
    """The partner's own people. Names and addresses of its own users, a role,
    whether they have set a password, when they last signed in and joined."""
    authz.require(ctx, MEMBER_VIEW)
    permissions = authz.effective_permissions(ctx)
    rows = panel_selectors.team(ctx.partner_id)
    items = []
    for m in rows:
        spec = capabilities.ROLE_PRESETS.get(m.role)
        items.append(
            {
                "id": str(m.id),
                "name": m.user.full_name,
                "email": m.user.email_original or m.user.email,
                "role": m.role,
                "role_label": spec.label if spec else m.role,
                "status": (
                    "suspended"
                    if m.status == "suspended"
                    else "active"
                    if m.user.status == "active"
                    else "disabled"
                ),
                "setup_pending": not m.user.has_usable_password(),
                "invited_at": m.invitation_sent_at.isoformat()
                if m.invitation_sent_at
                else None,
                "last_login_at": m.user.last_login_at.isoformat()
                if m.user.last_login_at
                else None,
                "joined_at": m.added_at.isoformat(),
                "is_you": m.user_id == ctx.user_id,
            }
        )
    return {
        "members": items,
        "roles": [
            {"key": s.key, "label": s.label, "description": s.description}
            for s in capabilities.ROLE_PRESETS.values()
        ],
        "can_manage": "platform.partner_member.manage" in permissions,
    }


# --- roles and permissions ---------------------------------------------------------------

#: What every role gets for running the account, in words (``BASELINE_VIEW`` and
#: ``BASELINE_MANAGE`` in ``capabilities``; a test pins that these stay in step).
ACCOUNT_ACCESS_EVERYONE: Final = (
    "See the dashboard, the business profile, the team and the products",
)
ACCOUNT_ACCESS_ADMINISTRATORS: Final = (
    "Change the business profile",
    "Invite, re-role, pause and remove team members",
    "Arrange and switch products for sale",
    "See the activity trail and manage API keys",
)


@in_scope
def roles(ctx: TenantContext) -> dict[str, Any]:
    """The role presets as they apply to *this* partner.

    A role is a preset over the capabilities the partner holds (never a new
    permission): what a member in it can do is the role's list **intersected with
    the partner's own capabilities**, so a role can narrow a partner's powers and
    never widen them. There are no custom roles - the closed set below is the
    set - and nobody can give themselves, or anyone else, more than the partner
    holds (``services.change_member_role`` also refuses a role change to oneself).
    """
    authz.require(ctx, MEMBER_VIEW)
    permissions = authz.effective_permissions(ctx)
    held = partner_selectors.active_capabilities(ctx.partner_id)
    counts = panel_selectors.role_counts(ctx.partner_id)
    order = list(capabilities.CAPABILITIES)

    def listed(spec: capabilities.RoleSpec) -> list[dict[str, str]]:
        usable = held if spec.capabilities is None else held & spec.capabilities
        rows = []
        for key in sorted(usable, key=order.index):
            cap = capabilities.CAPABILITIES[key]
            if spec.manage and cap.manage:
                access = "manage"
            elif cap.view or cap.manage:
                access = "view"
            else:
                continue  # an earning-only capability opens no screen
            rows.append(
                {
                    "key": key,
                    "label": capabilities.SHORT_LABELS.get(key, key),
                    "access": access,
                }
            )
        return rows

    return {
        "roles": [
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "administrator": spec.admin,
                "can_change": spec.manage,
                "owner_only": spec.key == "owner",
                "members": counts.get(spec.key, 0),
                "capabilities": listed(spec),
            }
            for spec in capabilities.ROLE_PRESETS.values()
        ],
        "partner_capabilities": [
            {"key": k, "label": capabilities.SHORT_LABELS.get(k, k)}
            for k in sorted(held, key=order.index)
        ],
        "account_access": {
            "everyone": list(ACCOUNT_ACCESS_EVERYONE),
            "administrators": list(ACCOUNT_ACCESS_ADMINISTRATORS),
        },
        "can_manage": "platform.partner_member.manage" in permissions,
        "not_implemented": {"custom_roles": NOT_IMPLEMENTED["custom_roles"]},
    }

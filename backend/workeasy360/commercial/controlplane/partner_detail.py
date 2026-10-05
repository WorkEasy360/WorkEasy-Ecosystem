"""One partner's console pages, and the customer list across partners (Phase 10c).

The same discipline as ``partner_console``: read models over existing tables,
authorized here, bounded and keyset-paginated, with a fixed number of queries
whatever the page size. What this module adds is the *per-partner* view - the
overview with its setup progress, the customers with their owner-activation
state, the partner's activity from the one audit log, and its brand.

Nothing here mutates a subscription, an entitlement or a price. The console
*shows* a customer's subscriptions; changing one goes through the existing
staff or partner commercial services (``controlplane.subscriptions`` and
``partners.customers``), which are the only writers.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.utils import timezone

from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.controlplane import partner_console as console
from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners import console_reads as partner_reads
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.domain import owner_activation
from workeasy360.commercial.pricing import selectors as price_selectors
from workeasy360.commercial.provisioning.selectors import summaries_for_organizations
from workeasy360.contracts.enums import (
    DomainStatus,
    PartnerLinkStatus,
    PayoutStatus,
)
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values.cursor import Page, encode_cursor
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import selectors as audit_selectors
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

PARTNER_VIEW = console.PARTNER_VIEW
AUDIT_VIEW = "platform.staff_audit.view"

LINK_STATES = partner_reads.LINK_STATES
ACTIVATION_FILTERS = partner_reads.ACTIVATION_FILTERS


# --- owner activation ---------------------------------------------------------------


def _owner_payload(invite: Any, now: Any) -> dict[str, Any]:
    """What a screen may show about an invitation. **Never the token, its hash
    or anything that could redeem it** - only the facts a person acts on."""
    if invite is None:
        return {
            "activation": "not_applicable",
            "email": "",
            "invitation_id": None,
            "expires_at": None,
            "resolved_at": None,
            "last_sent_at": None,
            "send_count": 0,
        }
    return {
        "activation": owner_activation(invite.status, invite.expires_at, now),
        "email": invite.user.email_original or invite.user.email,
        "invitation_id": str(invite.id),
        "expires_at": invite.expires_at.isoformat(),
        "resolved_at": invite.resolved_at.isoformat() if invite.resolved_at else None,
        "last_sent_at": invite.last_sent_at.isoformat() if invite.last_sent_at else None,
        "send_count": invite.send_count,
    }


# --- the customers ------------------------------------------------------------------------


def customers_page(
    ctx: TenantContext,
    *,
    partner_id: UUID | None,
    query: str | None = None,
    status: str | None = None,
    activation: str | None = None,
    subscription: str | None = None,
    limit: Any = console.DEFAULT_PAGE,
    cursor: str | None = None,
) -> Page:
    """Customers of one partner (or of every partner), newest relationship first.

    Only a *live* relationship carries the customer's current state: an ended
    one is history - the partner served this customer, and when - and shows its
    name and the relationship, never a subscription or product that may now
    belong to another partner's relationship. The owner column is the factual
    state of the activation invitation, derived from the row and the clock.

    Four queries whatever the page size: the links (with their organizations
    and partners), the summary projection for exactly the live ones, the newest
    invitation of each organization, and the owner accounts those name.
    """
    authz.require(ctx, PARTNER_VIEW)
    page_size = console._limit(limit)
    if partner_id is not None:
        console.require_partner(partner_id)
    if status and status not in LINK_STATES:
        raise PartnerValidationError("Unknown status filter.", field="status")
    if activation and activation not in ACTIVATION_FILTERS:
        raise PartnerValidationError("Unknown activation filter.", field="activation")
    if subscription and subscription not in partner_reads.SUBSCRIPTION_FILTERS:
        raise PartnerValidationError("Unknown subscription filter.", field="subscription")
    needle = console._clean_query(query)
    now = timezone.now()

    rows, has_more = partner_reads.customer_links(
        now,
        partner_id=partner_id,
        query=needle,
        status=status or None,
        activation=activation or None,
        subscription=subscription or None,
        cursor=cursor,
        page_size=page_size,
    )
    live_ids = [
        r.organization_id for r in rows if r.status != PartnerLinkStatus.ENDED.value
    ]
    summaries = summaries_for_organizations(live_ids)
    invites = partner_reads.latest_invites([r.organization_id for r in rows])

    items = []
    for link in rows:
        live = link.status != PartnerLinkStatus.ENDED.value
        summary = summaries.get(link.organization_id) if live else None
        items.append(
            {
                "link_id": str(link.id),
                "organization_id": str(link.organization_id),
                "name": link.organization.display_name,
                "slug": link.organization.slug,
                "organization_status": link.organization.status if live else "",
                "partner_id": str(link.partner_id),
                "partner_code": link.partner.code,
                "partner_name": link.partner.display_name,
                "link_status": link.status,
                "started_at": link.started_at.isoformat(),
                "ended_at": link.ended_at.isoformat() if link.ended_at else None,
                "end_reason": link.end_reason,
                "subscription_status": summary.subscription_status if summary else "",
                "products": list(summary.product_codes) if summary else [],
                "seat_capacity": summary.seat_capacity if summary else None,
                "owner": _owner_payload(invites.get(link.organization_id), now),
            }
        )
    return Page(
        items=items,
        next_cursor=encode_cursor(rows[-1].started_at, rows[-1].id) if has_more else None,
        has_more=has_more,
    )


# --- the partner overview -----------------------------------------------------------------------


def summary(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    """The Overview tab: status, contact, capabilities, customers, product
    grants, subscriptions, commission, payouts, white-label state and setup
    progress. A fixed handful of queries for one partner."""
    authz.require(ctx, PARTNER_VIEW)
    row = console.require_partner(partner_id)
    now = timezone.now()

    own = partner_reads.summary_facts(partner_id, now)
    grants = own["grants"]
    held = {g["key"] for g in grants}
    group_of = {
        key: group_key
        for group_key, _label, keys in capabilities.CAPABILITY_GROUPS
        for key in keys
    }
    facts = console._aggregates([partner_id], now)
    by_link = own["links_by_status"]
    owner_waiting = own["owner_activation_pending"]
    products = partner_selectors.granted_products(partner_id)
    payout_counts = commission_selectors.payout_status_counts(partner_id)
    domain_counts = own["domains_by_status"]
    domain_state = next(
        (s for s in partner_reads.DOMAIN_ORDER if domain_counts.get(s)), "none"
    )
    brand_configured = own["brand_configured"]
    rule = commission_selectors.effective_rule(partner_id, product_code="", at=now)
    has_partner_price = price_selectors.has_partner_price(partner_id)
    admins = own["administrators"]

    enabled = bool(held & capabilities.experience_keys(capabilities.WHITE_LABEL))
    return {
        "partner": partner_selectors.partner_dict(row),
        "experience": capabilities.experience_labels(held),
        "capabilities": [
            {
                "key": g["key"],
                "label": capabilities.CAPABILITIES[g["key"]].label
                if g["key"] in capabilities.CAPABILITIES
                else g["key"],
                "group": group_of.get(g["key"], ""),
                "granted_at": g["granted_at"].isoformat(),
                "expires_at": g["expires_at"].isoformat() if g["expires_at"] else None,
            }
            for g in grants
        ],
        "customers": {
            "live": by_link.get("active", 0) + by_link.get("suspended", 0),
            "active": by_link.get("active", 0),
            "suspended": by_link.get("suspended", 0),
            "ended": by_link.get("ended", 0),
            "owner_activation_pending": owner_waiting,
        },
        "products": products,
        "active_subscriptions": facts["subscriptions"].get(partner_id, 0),
        "commission": facts["commission"].get(partner_id, []),
        "commission_rule": rule,
        "payouts": {
            "open": facts["open_payouts"].get(partner_id, 0),
            "by_status": {s.value: payout_counts.get(s.value, 0) for s in PayoutStatus},
            "latest": facts["latest_payout"].get(partner_id),
        },
        "white_label": {
            "enabled": enabled,
            "brand_configured": brand_configured,
            "domain_status": domain_state,
            "domains": {
                "active": domain_counts.get(DomainStatus.ACTIVE.value, 0),
                "verified": domain_counts.get(DomainStatus.VERIFIED.value, 0),
                "awaiting": sum(
                    domain_counts.get(s, 0) for s in partner_reads.AWAITING_VERIFICATION
                ),
            },
        },
        "setup_progress": _setup_progress(
            row,
            held,
            products,
            has_partner_price,
            rule,
            admins,
            brand_configured,
            domain_state,
        ),
    }


def _setup_progress(
    row: Any,
    held: set[str],
    products: list[dict[str, Any]],
    has_partner_price: bool,
    rule: dict[str, Any] | None,
    admins: list[dict[str, Any]],
    brand_configured: bool,
    domain_state: str,
) -> list[dict[str, str]]:
    """Each thing a partner needs to work, and where it stands. States:
    ``complete``, ``incomplete`` (needs doing), ``pending`` (done on our side,
    waiting for the person) and ``optional`` (not started, and need not be)."""

    def step(key: str, label: str, state: str, detail: str = "") -> dict[str, str]:
        return {"key": key, "label": label, "state": state, "detail": detail}

    if rule is None:
        commission = step(
            "commission",
            "Commission",
            "incomplete",
            "No rule applies: it would earn nothing.",
        )
    elif capabilities.COMMISSION_EARN not in held:
        commission = step(
            "commission",
            "Commission",
            "incomplete",
            "Does not hold the Earn commission capability.",
        )
    else:
        commission = step(
            "commission", "Commission", "complete", rule["scope"].replace("_", " ")
        )

    if any(a["password_set"] for a in admins):
        admin = step("administrator", "Administrator", "complete")
    elif admins:
        admin = step(
            "administrator",
            "Administrator",
            "pending",
            "Waiting for them to choose a password.",
        )
    else:
        admin = step(
            "administrator",
            "Administrator",
            "incomplete",
            "Nobody can act for this partner yet.",
        )

    return [
        step(
            "profile",
            "Profile",
            "complete" if row.contact_email else "incomplete",
            "" if row.contact_email else "Add a contact email.",
        ),
        step(
            "products",
            "Products",
            "complete" if products else "incomplete",
            "" if products else "It cannot sell anything.",
        ),
        step(
            "pricing",
            "Pricing",
            "complete" if has_partner_price else "incomplete",
            "" if has_partner_price else "Customers pay the list price.",
        ),
        commission,
        admin,
        step("branding", "Branding", "complete" if brand_configured else "optional"),
        step(
            "domain",
            "Domain",
            "complete"
            if domain_state == "active"
            else "pending"
            if domain_state != "none"
            else "optional",
            "" if domain_state in ("active", "none") else domain_state.replace("_", " "),
        ),
    ]


# --- pricing ------------------------------------------------------------------------------------------


def pricing(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    """What a customer of this partner pays for each plan it may sell, next to
    the list price, with the server's discount and commission figures.

    Prices come from ``resolve_price`` - the one function that decides one - once
    with no partner (the list price) and once as this partner (its own book if it
    has a price for that plan and cycle, else the list price, which the row says).
    Monthly is always shown; any other cycle appears when the partner has a price
    for it. Nothing is priced here and nothing is stored.
    """
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.pricing.resolution import resolve_price
    from workeasy360.contracts.enums import BillingInterval, PriceSource
    from workeasy360.contracts.errors import PriceNotAvailable
    from workeasy360.contracts.values.money import CurrencyCode

    authz.require(ctx, PARTNER_VIEW)
    row = console.require_partner(partner_id)
    now = timezone.now()
    currency = CurrencyCode(row.payout_currency)
    granted = partner_selectors.granted_products(partner_id)
    plans = [
        plan
        for plan in catalog.sellable_plans(
            sorted({g["product_code"] for g in granted}), at=now
        )
        if partner_selectors.may_sell(
            partner_id, product_code=plan["product_code"], plan_code=plan["plan_code"]
        )
    ]
    priced_cycles = price_selectors.partner_price_cycles(partner_id)

    def quote(plan_version_id: UUID, cycle: BillingInterval, as_partner: bool) -> Any:
        try:
            return resolve_price(
                plan_version_id=plan_version_id,
                organization_id=None,
                currency=currency,
                interval=cycle,
                quantity=1,
                at=now,
                partner_id=partner_id if as_partner else None,
            )
        except PriceNotAvailable:
            return None

    rules_by_product: dict[str, int | None] = {}
    rows: list[dict[str, Any]] = []
    for plan in plans:
        code = plan["product_code"]
        if code not in rules_by_product:
            rule = commission_selectors.effective_rule(
                partner_id, product_code=code, at=now
            )
            rules_by_product[code] = rule["rate_bp"] if rule else None
        cycles = {
            BillingInterval.MONTHLY.value,
            *priced_cycles.get(plan["plan_version_id"], set()),
        }
        for cycle_value in sorted(cycles):
            cycle = BillingInterval(cycle_value)
            listed = quote(plan["plan_version_id"], cycle, as_partner=False)
            actual = quote(plan["plan_version_id"], cycle, as_partner=True)
            list_minor = listed.total.minor_units if listed else None
            partner_minor = actual.total.minor_units if actual else None
            rows.append(
                {
                    "product_code": code,
                    "product_name": plan["product_name"],
                    "plan_version_id": str(plan["plan_version_id"]),
                    "plan_code": plan["plan_code"],
                    "plan_name": plan["plan_name"],
                    "interval": cycle.value,
                    "currency": currency.value,
                    "list_price_minor": list_minor,
                    "partner_price_minor": partner_minor,
                    "partner_price_source": (
                        "partner_book"
                        if actual is not None
                        and actual.price_source is PriceSource.PARTNER_BOOK
                        else "list"
                    ),
                    **console.price_figures(
                        list_minor=list_minor,
                        partner_minor=partner_minor,
                        rate_bp=rules_by_product[code],
                        currency=currency,
                    ),
                }
            )
    return {"currency": currency.value, "results": rows}


# --- activity --------------------------------------------------------------------------------------


@in_scope
def activity(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    limit: Any = console.DEFAULT_PAGE,
    cursor: str | None = None,
) -> Page:
    """What has been done to, and by, this partner - from the **one** audit log.

    There is no partner audit store. An entry belongs to the partner when it is
    about the partner row or its brand, or when its recorded ``changes`` name the
    partner (every partner, commission, payout and domain action does). Staff
    who may read the control-plane audit trail may read this.
    """
    authz.require(ctx, AUDIT_VIEW)
    console.require_partner(partner_id)
    return audit_selectors.audit_page(
        resource_ids=partner_reads.audit_resource_ids(partner_id),
        changes_match=("partner_id", str(partner_id)),
        limit=console._limit(limit),
        cursor=cursor,
    )


# --- the brand -----------------------------------------------------------------------------------------


def brand(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    """The partner's brand as it is stored (or ``None``), for staff."""
    authz.require(ctx, PARTNER_VIEW)
    console.require_partner(partner_id)
    row = partner_selectors.brand_for(partner_id)
    return {"brand": partner_selectors.brand_dict(row) if row is not None else None}

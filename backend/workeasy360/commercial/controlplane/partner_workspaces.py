"""What the three workspaces show that the shared panel does not (ADR-047).

The Reseller, White-label and Affiliate workspaces are compositions of the same
partner panel, so almost everything they show is already read by
``partner_portal`` and ``partner_network``. Three reads were missing, and they
live here, each a bounded read over data that already exists:

- :func:`affiliate_overview` - the affiliate's dashboard: clicks, conversions,
  the conversion rate, commission earned / pending / paid, the referral link and
  how each campaign is doing;
- :func:`my_products` - ADR-054's My Products: the products the partner may
  promote, their features and published updates, and every link's figures by
  product, update and campaign;
- :func:`reports` - Reports & Analytics for every workspace: factual counts of
  customers, subscriptions per product, sales and commission, and referrals;
- :func:`white_label_status` - the white-label dashboard's two facts: where the
  brand setup stands and where the custom domain stands.

Rules that hold for all three:

- **Every section follows a permission.** A section the caller's permissions do
  not allow is ``None``, never zero - so an affiliate's reports contain no
  customer, a reseller's contain no referral it was not given, and a missing
  number is never mistaken for an empty one.
- **The server computes the figures.** Money comes from the append-only ledger,
  a rate is integer basis points (half-up), and a "campaign" is a referral
  code's label: tracking metadata that changes nobody's commission.
- **Nothing identifies a visitor or a customer.** The referral rows carry no
  name or address, and the customer figures are counts.
- **Honest about gaps.** Anything the platform does not record (a separate
  sign-up step, marketing assets, discount codes) is stated from the
  ``NOT_IMPLEMENTED`` registry rather than shown as a zero.
"""

from __future__ import annotations

from typing import Any, Final

from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.commissions import policy as programme_policy
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.controlplane import partner_network, partner_portal
from workeasy360.commercial.controlplane.partner_portal import NOT_IMPLEMENTED
from workeasy360.commercial.partners import panel_selectors, promotion
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.provisioning import selectors as provisioning_selectors
from workeasy360.contracts.enums import DomainStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

REFERRAL_VIEW: Final = "platform.partner_referral.view"
COMMISSION_VIEW: Final = "platform.partner_commission.view"
PAYOUT_VIEW: Final = "platform.partner_payout.view"
CUSTOMER_VIEW: Final = "platform.partner_customer.view"
BRANDING_VIEW: Final = "platform.partner_branding.view"
DOMAIN_VIEW: Final = "platform.partner_domain.view"
DASHBOARD_VIEW: Final = partner_portal.DASHBOARD_VIEW

#: The campaign names the Referral Links screen offers. Tracking metadata only: a
#: code's label never reaches commission, attribution or authorization.
CAMPAIGN_PRESETS: Final = ("General", "LinkedIn", "YouTube", "Email", "Website")

#: A domain's progress, most advanced first.
_DOMAIN_RANK: Final = (
    DomainStatus.ACTIVE.value,
    DomainStatus.VERIFIED.value,
    DomainStatus.VERIFICATION_PENDING.value,
    DomainStatus.REQUESTED.value,
)


def rate_bp(part: int, whole: int) -> int | None:
    """``part / whole`` in whole basis points, rounded half up - integers only.
    ``None`` when there is nothing to divide by (no clicks is no rate, not 0%)."""
    if whole <= 0:
        return None
    return (part * 20_000 + whole) // (2 * whole)


def _amounts(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [
        {"currency": r["currency"], "amount_minor": r[key]}
        for r in rows
        if r[key] or r.get("entries")
    ]


def _commission_by_currency(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for point in series:
        totals[point["currency"]] = (
            totals.get(point["currency"], 0) + point["commission_minor"]
        )
    return [{"currency": c, "amount_minor": totals[c]} for c in sorted(totals)]


def without_money(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversion rows with the commission taken out (kept as ``None`` so a
    screen can say "needs commission access" instead of showing a zero)."""
    return [{**row, "commission_minor": None, "currency": ""} for row in rows]


def _by_currency(cells: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    """``{currency: {sales_minor, commission_minor, sales}}`` as sorted rows."""
    return [{"currency": c, **cells[c]} for c in sorted(cells)]


def _merge(
    a: list[dict[str, Any]], b: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, int]] = {}
    for row in [*a, *(b or [])]:
        cell = totals.setdefault(
            row["currency"], {"sales_minor": 0, "commission_minor": 0, "sales": 0}
        )
        for key in cell:
            cell[key] += row[key]
    return _by_currency(totals)


def _top_products(
    earnings: dict[str, Any], products: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The products the partner's referrals sold most, by sale count."""
    names = {p["code"]: p["name"] for p in products}
    rows = [
        {
            "product_code": code,
            "product_name": names.get(code, code),
            "sales": sum(v["sales"] for v in cells.values()),
            "earnings": _by_currency(cells),
        }
        for code, cells in earnings["by_product"].items()
    ]
    return sorted(rows, key=lambda r: (-r["sales"], r["product_name"]))[:5]


# --- the affiliate dashboard ----------------------------------------------------------------


@in_scope
def affiliate_overview(
    ctx: TenantContext,
    *,
    range_key: Any = "30d",
    date_from: Any = None,
    date_to: Any = None,
) -> dict[str, Any]:
    """The affiliate workspace's dashboard for one period.

    Clicks and conversions are one cohort (referrals captured in the period, and
    those that have become customers), so the rate is the share of visitors who
    followed a link in the period and are now customers. Commission figures need
    ``commission.view`` and are ``None`` without it; the payout list needs
    ``payout.view``.
    """
    authz.require(ctx, REFERRAL_VIEW)
    now = timezone.now()
    key, start, end = partner_portal.parse_range(range_key, date_from, date_to, now=now)
    partner_id = ctx.partner_id
    assert partner_id is not None
    permissions = authz.effective_permissions(ctx)
    row = partner_selectors.partner_row(partner_id)

    activity = commission_selectors.referral_activity(partner_id, start, end)
    codes = commission_selectors.referral_codes(partner_id)
    totals = activity["totals"]
    clicks, conversions = totals["clicks"], totals["conversions"]
    money_visible = COMMISSION_VIEW in permissions
    earnings = (
        commission_selectors.referral_earnings(partner_id, start, end)
        if money_visible
        else None
    )
    products = _promotable(partner_id, now)
    links = _link_rows(codes, activity, earnings)
    primary = next((link for link in links if link["status"] == "active"), None)

    campaigns: dict[str, dict[str, Any]] = {}
    for link in links:
        entry = campaigns.setdefault(
            link["campaign"],
            {
                "campaign": link["campaign"],
                "campaign_id": link["campaign_id"],
                "links": 0,
                **_ZERO,
                "earnings": [] if earnings is not None else None,
            },
        )
        entry["links"] += 1
        for k in _ZERO:
            entry[k] += link[k]
        if earnings is not None:
            entry["earnings"] = _merge(entry["earnings"], link["earnings"])

    out: dict[str, Any] = {
        "partner": {
            "id": str(partner_id),
            "name": row.display_name,
            "currency": row.payout_currency,
            "status": row.status,
        },
        "range": {"key": key, "from": start.isoformat(), "to": end.isoformat()},
        "kpis": {
            "clicks": clicks,
            "unique_visitors": totals["visitors"],
            # ADR-053: an account created by a visitor who followed a link.
            "signups": totals["registrations"],
            "conversions": conversions,
            "conversion_rate_bp": rate_bp(conversions, clicks),
        },
        "primary_link": primary,
        "links": links,
        "campaigns": [
            {**c, "conversion_rate_bp": rate_bp(c["conversions"], c["clicks"])}
            for c in sorted(campaigns.values(), key=lambda c: c["campaign"].lower())
        ],
        "campaign_list": commission_selectors.campaigns(partner_id),
        "campaign_presets": list(CAMPAIGN_PRESETS),
        "products": products,
        "top_products": _top_products(earnings, products)
        if earnings is not None
        else None,
        "series": activity["by_day"],
        "money": None,
        "recent": {"conversions": [], "payouts": None},
        "not_implemented": {
            "promo_discount_codes": NOT_IMPLEMENTED["promo_discount_codes"],
        },
    }
    out["recent"]["conversions"] = commission_selectors.partner_conversions_page(
        partner_id, limit=5
    ).items
    if COMMISSION_VIEW not in permissions:
        # Money is the commission view's: a role that may see referrals alone is
        # shown when and through which link, not what each earned.
        out["recent"]["conversions"] = without_money(out["recent"]["conversions"])
    if COMMISSION_VIEW in permissions:
        series = commission_selectors.partner_sales_by_day(partner_id, start, end)
        balances = commission_selectors.partner_money(partner_id, now)
        out["money"] = {
            "sales": [
                {"currency": c, "amount_minor": v["sales_minor"], "sales": v["sales"]}
                for c, v in sorted(earnings["totals"].items())
            ]
            if earnings is not None
            else [],
            "earned": _commission_by_currency(series),
            "pending": _amounts(balances, "pending_minor"),
            "available": _amounts(balances, "available_minor"),
            "paid": _amounts(balances, "paid_minor"),
            "balances": balances,
        }
    if PAYOUT_VIEW in permissions:
        out["recent"]["payouts"] = commission_selectors.payouts_page(
            partner_id=partner_id, for_partner=True, limit=5
        ).items
    return out


_ZERO: Final = {
    "clicks": 0,
    "visitors": 0,
    "registrations": 0,
    "trials": 0,
    "conversions": 0,
}


def _promotable(partner_id: Any, now: Any) -> list[dict[str, Any]]:
    """The Store products open to affiliates that this partner holds a
    promotion grant for (ADR-054) - its Marketing Materials and link targets."""
    granted = promotion.promotable_codes(partner_id)
    return [
        p for p in catalog_selectors.promotable_products(at=now) if p["code"] in granted
    ]


def _link_rows(
    codes: list[dict[str, Any]],
    activity: dict[str, Any],
    earnings: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Each link with its funnel for the period and (with commission view) its
    sales and commission by currency."""
    names = catalog_selectors.product_names(
        sorted({c["product_code"] for c in codes if c["product_code"]})
    )
    links = []
    for code in codes:
        counts = activity["by_code"].get(code["id"], _ZERO)
        links.append(
            {
                **code,
                "campaign": code["campaign_name"] or code["label"] or CAMPAIGN_PRESETS[0],
                "product_name": names.get(code["product_code"]) or None,
                # A link that no longer takes visitors has no address to copy.
                "url": partner_network.referral_url(code["code"])
                if code["status"] == "active"
                else None,
                **{k: counts.get(k, 0) for k in _ZERO},
                "earnings": _by_currency(earnings["by_code"].get(code["id"], {}))
                if earnings is not None
                else None,
            }
        )
    return links


def _rollup(links: list[dict[str, Any]], money: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "links": len(links),
        **_ZERO,
        "earnings": [] if money else None,
    }
    for link in links:
        for k in _ZERO:
            out[k] += link[k]
        if money:
            out["earnings"] = _merge(out["earnings"], link["earnings"])
    out["conversion_rate_bp"] = rate_bp(out["conversions"], out["clicks"])
    return out


# --- My Products (ADR-054) -------------------------------------------------------------------


@in_scope
def my_products(
    ctx: TenantContext,
    *,
    range_key: Any = "30d",
    date_from: Any = None,
    date_to: Any = None,
) -> dict[str, Any]:
    """The products this partner may promote, with what it can link to and how
    its links are doing in the period - per product, per update and per
    campaign. Products it once promoted and may no longer are listed apart, with
    their links and history, and the programme policy that now governs them.
    Figures are the server's: funnel counts from referrals, money from the
    ledger, and money only with ``commission.view``."""
    authz.require(ctx, REFERRAL_VIEW)
    now = timezone.now()
    key, start, end = partner_portal.parse_range(range_key, date_from, date_to, now=now)
    partner_id = ctx.partner_id
    assert partner_id is not None
    permissions = authz.effective_permissions(ctx)
    money = COMMISSION_VIEW in permissions

    activity = commission_selectors.referral_activity(partner_id, start, end)
    earnings = (
        commission_selectors.referral_earnings(partner_id, start, end) if money else None
    )
    links = _link_rows(
        commission_selectors.referral_codes(partner_id), activity, earnings
    )
    products = _promotable(partner_id, now)
    codes = [p["code"] for p in products]
    updates = catalog_selectors.published_updates(codes)
    features = catalog_selectors.public_features(codes)
    granted_at = {
        g["product_code"]: g["granted_at"]
        for g in promotion.grants(partner_id)
        if g["status"] == "active"
    }

    def product_card(p: dict[str, Any]) -> dict[str, Any]:
        mine = [link for link in links if link["product_code"] == p["code"]]
        by_update = []
        for u in updates.get(p["code"], []):
            for_update = [link for link in mine if link["update_id"] == u["id"]]
            by_update.append({**u, **_rollup(for_update, money)})
        return {
            **p,
            "granted_at": granted_at.get(p["code"]),
            "features": features.get(p["code"], []),
            "updates": by_update,
            "links": mine,
            "totals": _rollup(mine, money),
        }

    promotable = set(codes)
    withdrawn: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        if link["product_code"] and link["product_code"] not in promotable:
            withdrawn.setdefault(link["product_code"], []).append(link)
    campaigns: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        if link["campaign_id"]:
            campaigns.setdefault(link["campaign_id"], []).append(link)
    names = {c["id"]: c["name"] for c in commission_selectors.campaigns(partner_id)}

    return {
        "range": {"key": key, "from": start.isoformat(), "to": end.isoformat()},
        "can_create": "platform.partner_referral.create" in permissions,
        "products": [product_card(p) for p in products],
        "withdrawn": [
            {
                "product_code": code,
                "product_name": rows[0]["product_name"] or code,
                "links": rows,
                "totals": _rollup(rows, money),
            }
            for code, rows in sorted(withdrawn.items())
        ],
        "campaigns": [
            {"campaign_id": cid, "name": names.get(cid, ""), **_rollup(rows, money)}
            for cid, rows in campaigns.items()
        ],
        "campaign_list": commission_selectors.campaigns(partner_id),
        "policy": programme_policy.current().as_dict(),
    }


# --- Store Console (ADR-055) ------------------------------------------------------------------


@in_scope
def store_console(ctx: TenantContext) -> dict[str, Any]:
    """Explore Products and My Requests: every Store product open to
    affiliates, each with this partner's state for it (not requested, pending,
    approved, rejected, revoked), its listing content, its starting price as
    the public Store shows it and - only for a member who may see commission -
    the rate a sale of it would earn this partner. Then the partner's requests,
    newest first. The catalog is the one source: a product staff publish
    appears here, one they withdraw disappears, and an edit shows at once."""
    from workeasy360.commercial.partners import promotion_requests
    from workeasy360.commercial.subscriptions import marketplace

    authz.require(ctx, REFERRAL_VIEW)
    partner_id = ctx.partner_id
    assert partner_id is not None
    now = timezone.now()
    permissions = authz.effective_permissions(ctx)
    products = catalog_selectors.promotable_products(at=now)
    codes = [p["code"] for p in products]
    states = promotion_requests.product_states(partner_id)
    prices = {
        p["code"]: p.get("starting_price")
        for p in marketplace.public_catalog()["products"]
    }
    rates = (
        commission_selectors.effective_rates(partner_id, codes, at=now)
        if COMMISSION_VIEW in permissions
        else None
    )
    blocked = promotion_requests.may_request(partner_id, permissions)

    def card(p: dict[str, Any]) -> dict[str, Any]:
        state = states.get(
            p["code"], {"status": promotion_requests.NOT_REQUESTED, "request": None}
        )
        logo = next((m for m in p["media"] if m["kind"] == "logo"), None)
        return {
            "code": p["code"],
            "name": p["name"],
            "slug": p["slug"],
            "tagline": p["tagline"],
            "summary": p["summary"],
            "description": p["description"],
            "category": p["category"],
            "icon": p["icon"],
            "logo_url": logo["url"] if logo else None,
            "plans": p["plans"],
            "starting_price": prices.get(p["code"]),
            # None: not disclosed to this member. {"rate_bp": None}: no rule yet.
            "commission": (
                (rates.get(p["code"]) or {"rate_bp": None, "recurring": False})
                if rates is not None
                else None
            ),
            "status": state["status"],
            "request": state["request"],
        }

    cards = [card(p) for p in products]
    requests = promotion_requests.partner_requests(partner_id)
    return {
        "products": cards,
        "requests": requests,
        "counts": {
            "approved": sum(1 for c in cards if c["status"] == "approved"),
            "explore": sum(1 for c in cards if c["status"] != "approved"),
            "pending": sum(1 for r in requests if r["status"] == "pending"),
        },
        "can_request": blocked is None,
        "cannot_request_reason": blocked,
        "channels": promotion_requests.CHANNELS,
        "prefill": {"website": promotion_requests.profile_website(partner_id)},
    }


@in_scope
def referral_trail(ctx: TenantContext, referral_id: Any) -> dict[str, Any]:
    """ADR-054: the attribution trail of one of the partner's own referrals.
    Money only with ``commission.view``; never who the customer is."""
    from uuid import UUID

    from workeasy360.contracts.errors import PartnerRecordNotFound

    authz.require(ctx, REFERRAL_VIEW)
    assert ctx.partner_id is not None
    trail = commission_selectors.referral_trail(ctx.partner_id, UUID(str(referral_id)))
    if trail is None:
        raise PartnerRecordNotFound("No such referral.", referral_id=str(referral_id))
    if COMMISSION_VIEW not in authz.effective_permissions(ctx):
        trail["sales"] = [
            {**sale, "commission_minor": None, "sale_minor": None, "rate_bp": None}
            for sale in trail["sales"]
        ]
        trail["reversals"] = []
    return trail


# --- Reports & Analytics -----------------------------------------------------------------------


@in_scope
def reports(
    ctx: TenantContext,
    *,
    range_key: Any = "30d",
    date_from: Any = None,
    date_to: Any = None,
) -> dict[str, Any]:
    """Factual reports for one period. Four sections, each only for the caller
    who may see it: ``customers`` and ``products`` (customer view), ``sales``
    (commission view) and ``referrals`` (referral view). No derived score and no
    forecast: every number is a count or a ledger sum."""
    authz.require(ctx, DASHBOARD_VIEW)
    now = timezone.now()
    key, start, end = partner_portal.parse_range(range_key, date_from, date_to, now=now)
    partner_id = ctx.partner_id
    assert partner_id is not None
    permissions = authz.effective_permissions(ctx)
    row = partner_selectors.partner_row(partner_id)

    out: dict[str, Any] = {
        "partner": {
            "id": str(partner_id),
            "name": row.display_name,
            "currency": row.payout_currency,
            "status": row.status,
        },
        "range": {"key": key, "from": start.isoformat(), "to": end.isoformat()},
        "customers": None,
        "products": None,
        "sales": None,
        "referrals": None,
        "not_implemented": {
            "customer_last_activity": NOT_IMPLEMENTED["customer_last_activity"],
        },
    }
    if CUSTOMER_VIEW in permissions:
        out["customers"] = partner_portal.customer_counts(partner_id)
        organizations = panel_selectors.live_organization_ids(partner_id)
        seen: set[str] = set()
        products = []
        for grant in partner_selectors.granted_products(partner_id):
            code = grant["product_code"]
            if code in seen:
                continue
            seen.add(code)
            holding, active = provisioning_selectors.product_holders(organizations, code)
            products.append(
                {
                    "product_code": code,
                    "product_name": grant["product_name"],
                    "customers_holding": holding,
                    "active_subscriptions": active,
                }
            )
        out["products"] = products
    if COMMISSION_VIEW in permissions:
        series = commission_selectors.partner_sales_by_day(partner_id, start, end)
        out["sales"] = {
            "transactions": sum(p["sales"] for p in series),
            "gross": partner_portal.sales_by_currency(
                series, "gross_minor", "basis_currency"
            ),
            "commission_earned": _commission_by_currency(series),
            "charts": partner_portal.sales_charts(series),
            "source": "commission_ledger",
        }
    if REFERRAL_VIEW in permissions:
        activity = commission_selectors.referral_activity(partner_id, start, end)
        totals = activity["totals"]
        zero = {"clicks": 0, "visitors": 0, "registrations": 0, "conversions": 0}
        by_campaign: dict[str, dict[str, Any]] = {}
        for code in commission_selectors.referral_codes(partner_id):
            counts = activity["by_code"].get(code["id"], zero)
            name = code["campaign_name"] or code["label"] or CAMPAIGN_PRESETS[0]
            entry = by_campaign.setdefault(name, {"campaign": name, **zero})
            for k in zero:
                entry[k] += counts[k]
        out["referrals"] = {
            "clicks": totals["clicks"],
            "unique_visitors": totals["visitors"],
            "registrations": totals["registrations"],
            "conversions": totals["conversions"],
            "conversion_rate_bp": rate_bp(totals["conversions"], totals["clicks"]),
            "by_campaign": [
                {**c, "conversion_rate_bp": rate_bp(c["conversions"], c["clicks"])}
                for c in sorted(by_campaign.values(), key=lambda c: c["campaign"].lower())
            ],
            "series": activity["by_day"],
        }
    return out


# --- the white-label dashboard ---------------------------------------------------------------------


@in_scope
def white_label_status(ctx: TenantContext) -> dict[str, Any]:
    """Where the brand setup and the custom domain stand, for the white-label
    dashboard. Each half needs its own view permission and is ``None`` without
    it. Reads only; the commands are the brand and domain services."""
    authz.require(ctx, DASHBOARD_VIEW)
    permissions = authz.effective_permissions(ctx)
    partner_id = ctx.partner_id
    assert partner_id is not None
    out: dict[str, Any] = {"brand": None, "domain": None, "storefront": None}
    if BRANDING_VIEW in permissions:
        # ADR-053: the partner's branded Store - live once the partner holds
        # the white-label pair and the right to sell, with a brand set up.
        from workeasy360.commercial.partners import storefront
        from workeasy360.platform.identity import portals as portal_links

        row = partner_selectors.partner_row(partner_id)
        out["storefront"] = {
            "url": f"{portal_links.app_url()}/store/{row.code}",
            "live": storefront.storefront_partner(row.code) is not None,
        }
        brand = partner_selectors.brand_for(partner_id)
        state = (
            "not_started"
            if brand is None
            else ("complete" if brand.logo_file_id else "in_progress")
        )
        out["brand"] = {
            "state": state,
            "name": brand.brand_name if brand is not None else "",
            "logo": bool(brand and brand.logo_file_id),
        }
    if DOMAIN_VIEW in permissions:
        live = [
            d
            for d in partner_selectors.domains_for(partner_id)
            if d.status in _DOMAIN_RANK
        ]
        best = min(live, key=lambda d: _DOMAIN_RANK.index(d.status)) if live else None
        out["domain"] = (
            {"state": "none", "hostname": ""}
            if best is None
            else {"state": best.status, "hostname": best.hostname}
        )
    return out

"""The partner's own business panel: reads over the partner's own data (ADR-045).

Every function takes the partner context ``access.partner_actor`` established -
the partner id is the caller's own, verified membership, never a request
parameter - authorizes against a ``platform.partner_*`` permission the caller's
role and the partner's capabilities resolved, and returns plain bounded dicts.

Three rules hold for everything here:

- **The server computes the money.** A figure comes from the commission ledger
  (append-only, snapshotted), a price from the pricing resolver's own rows, a
  count from a grouped query. The browser sends no amount and does no
  arithmetic; currencies are reported side by side and never added together.
- **Nothing here writes.** The commands (sell, provision, grant, brand, team,
  product switches) are the existing services; this module reads.
- **Honesty about gaps.** Where the platform has no model for what a screen
  asks (a wholesale cost, a customer limit, a prepaid balance), the answer says
  so (``features``) instead of showing a zero that could be mistaken for a fact.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Final
from uuid import UUID

from django.utils import timezone as dj_timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.commissions import domain as commission_domain
from workeasy360.commercial.commissions import margins
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.controlplane import partner_detail
from workeasy360.commercial.partners import (
    capabilities,
    hierarchy,
    panel_selectors,
    product_settings,
)
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.payments import selectors as payment_selectors
from workeasy360.commercial.pricing import selectors as price_selectors
from workeasy360.commercial.provisioning import selectors as provisioning_selectors
from workeasy360.contracts.errors import PartnerValidationError, PermissionDenied
from workeasy360.contracts.values.cursor import Page
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import selectors as audit_selectors
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.identity import selectors as identity_selectors

DASHBOARD_VIEW: Final = "platform.partner_dashboard.view"
COMMISSION_VIEW: Final = "platform.partner_commission.view"
PRODUCT_VIEW: Final = "platform.partner_product.view"
PRICING_VIEW: Final = "platform.partner_pricing.view"
ACTIVITY_VIEW: Final = "platform.partner_activity.view"
CUSTOMER_VIEW: Final = "platform.partner_customer.view"
REFERRAL_VIEW: Final = "platform.partner_referral.view"
SUBPARTNER_VIEW: Final = "platform.partner_subpartner.view"

RANGES: Final = {"7d": 7, "30d": 30, "90d": 90}
MAX_CUSTOM_DAYS: Final = 366
ACTIVE_SUBSCRIPTION_STATES: Final = ("active", "trialing")

# --- what the platform does not have -------------------------------------------------
#
# One registry, read by the portal. A screen for a feature listed here shows the
# reason instead of a control, and the architecture tests pin that no endpoint
# exists for it. Remove an entry only when the capability is built and tested.

NOT_IMPLEMENTED: Final[dict[str, str]] = {
    "customer_limit": (
        "No customer or user limit exists for a partner in the commercial model, "
        "so none is shown. A limit needs a commercial rule first."
    ),
    "customer_country": "A customer organization carries no country.",
    "customer_owner_email": (
        "A customer's owner address is never shown to a partner: a partner sees "
        "whether its owner has activated, not who they are (the Phase 10e privacy "
        "contract)."
    ),
    "customer_last_activity": (
        "Last activity would come from inside the products, which the partner "
        "portal never reads."
    ),
    "recharge": (
        "There is no prepaid partner credit. Commission is a ledger the platform "
        "pays out; a recharge would need a prepaid-credit model and a payment "
        "flow the owner has not defined."
    ),
    "payment_links": (
        "Payment links exist only for a customer's own order. A link that pays a "
        "partner account, or an invoice a partner raises, has no model yet."
    ),
    "add_ons": (
        "The catalog has plans and bundles; it has no purchasable add-on. "
        "Included features are shown with each plan."
    ),
    "partner_packages": (
        "Packages are staff-defined bundles. A partner-authored package, and the "
        "sale of a bundle by a partner, are not built."
    ),
    "pricing_rules": (
        "A partner's price is a fixed price in its price book, set by Workeasy360 "
        "staff. Partner-authored discounts, customer groups and effective-dated "
        "rules need a pricing model that does not exist."
    ),
    "subpartner_pricing": (
        "A sub-partner sells at list price unless Workeasy360 sets a price book "
        "for it. There is no parent margin or wholesale chain."
    ),
    "subpartner_commission": (
        "Commission is earned by the partner that Workeasy360 enables for it. "
        "A parent sees no sub-partner commission and a sub-partner earns none "
        "until staff grant commission to it."
    ),
    "partner_smtp": (
        "Partner-owned mail servers are not supported: a partner-supplied host "
        "would let a partner make the platform connect to any address. Mail goes "
        "through the platform's provider, in the partner's name where the brand "
        "allows it."
    ),
    "whatsapp_sms_webhook": (
        "No WhatsApp, SMS or partner webhook channel exists on the platform."
    ),
    "partner_alerts": (
        "Only the platform's own notifications exist; a partner cannot yet route "
        "an event to a channel."
    ),
    "partner_templates": (
        "Email wording is the platform's. A partner changes its sender name and "
        "reply-to; per-partner template text is not built."
    ),
    "integrations": (
        "Connectors belong to a customer organization. The platform has no "
        "partner-scoped connector, and a partner cannot install code."
    ),
    "shared_resources": (
        "The files platform is organization-scoped. Sharing a file between "
        "partners or with customers would need an access model that does not exist."
    ),
    "custom_css": (
        "Custom CSS and JavaScript are deliberately not offered: they allow script "
        "injection, data exfiltration and cross-tenant style leakage. Use the "
        "brand colours and theme tokens."
    ),
    "brand_assets": (
        "One logo is supported. A separate dark logo and icon are not built."
    ),
    "affiliate_program_settings": (
        "The attribution window and the commission rates are platform settings. "
        "A partner reads them; it does not change them."
    ),
    "custom_roles": (
        "Roles are the fixed presets (Owner, Admin, Sales, Finance, Support, "
        "Marketing, Viewer), each a subset of what the partner holds. A "
        "partner-defined role would need a place to store it and a rule for who "
        "may define it; neither exists."
    ),
    "auth_policy": (
        "How your customers are authenticated is Workeasy360's policy, not a "
        "partner setting: a default sign-in destination, a stronger password "
        "rule, a session length and the single-sign-on mode would each change "
        "security for people who belong to their own organization. Only how the "
        "sign-in page is presented (your brand) is yours to choose."
    ),
    "appearance_layout": (
        "A custom sign-in background, landing cards, a preloader and a menu layout "
        "have no storage. The customer portal uses the standard layout with your "
        "brand name, logo and colours."
    ),
    "partner_tickets": (
        "Workeasy360 has no ticket system of its own, so open and resolved "
        "requests are not listed in the portal. Requests are handled in Easy Help "
        "Desk, where their status, priority and history live."
    ),
    # ADR-047: the affiliate workspace.
    "promo_discount_codes": (
        "A referral code identifies you when a visitor signs up. It gives the "
        "visitor no discount: Workeasy360 has no coupon or promo-code system, and "
        "one is not invented here."
    ),
    "affiliate_promotion_scope": (
        "The products an affiliate promotes are not a stored list: promoting "
        "grants no right to sell, so there is no product grant to record. The "
        "choice scopes the commission rate assigned to the affiliate; with the "
        "programme's default rate, every product a referred customer buys earns."
    ),
}


# --- the dashboard --------------------------------------------------------------------


def parse_range(
    key: Any, date_from: Any, date_to: Any, *, now: datetime | None = None
) -> tuple[str, datetime, datetime]:
    """``(key, start, end)`` for a dashboard range, in UTC, ``end`` exclusive.

    ``7d``/``30d``/``90d`` end now; ``custom`` takes two ISO dates (inclusive)
    spanning at most a year. Anything else is a 400, never a silent default."""
    now = now or dj_timezone.now()
    chosen = str(key or "30d")
    if chosen in RANGES:
        return chosen, now - timedelta(days=RANGES[chosen]), now
    if chosen != "custom":
        raise PartnerValidationError(
            "range must be 7d, 30d, 90d or custom.", field="range"
        )
    try:
        first = datetime.fromisoformat(str(date_from)).date()
        last = datetime.fromisoformat(str(date_to)).date()
    except ValueError as exc:
        raise PartnerValidationError(
            "from and to must be dates (YYYY-MM-DD).", field="from"
        ) from exc
    if last < first:
        raise PartnerValidationError("to must not be before from.", field="to")
    if (last - first).days >= MAX_CUSTOM_DAYS:
        raise PartnerValidationError(
            f"Choose at most {MAX_CUSTOM_DAYS} days.", field="to"
        )
    start = datetime.combine(first, time.min, tzinfo=timezone.utc)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return "custom", start, end


def customer_counts(partner_id: UUID) -> dict[str, int]:
    """Live customers, and how many of them hold an active or trialling
    subscription - three small queries."""
    counts = panel_selectors.link_counts(partner_id)
    counts["active_subscriptions"] = provisioning_selectors.active_subscription_count(
        panel_selectors.live_organization_ids(partner_id)
    )
    return counts


def _by_currency(series: list[dict[str, Any]], key: str, currency_key: str) -> list[Any]:
    totals: dict[str, int] = {}
    for point in series:
        currency = point[currency_key]
        if currency:
            totals[currency] = totals.get(currency, 0) + point[key]
    return [{"currency": c, "amount_minor": totals[c]} for c in sorted(totals)]


@in_scope
def dashboard(
    ctx: TenantContext,
    *,
    range_key: Any = "30d",
    date_from: Any = None,
    date_to: Any = None,
) -> dict[str, Any]:
    """The overview a partner opens first.

    Which sections are present follows the caller's permissions: customers and
    subscriptions need the customer view, sales and money need the commission
    view. A section the caller may not see is ``None``, not zero.
    """
    authz.require(ctx, DASHBOARD_VIEW)
    now = dj_timezone.now()
    key, start, end = parse_range(range_key, date_from, date_to, now=now)
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
        "sales": None,
        "money": None,
        "charts": None,
        "sub_partners": None,
        "recent": {"customers": None, "commissions": None, "payouts": None},
        # ADR-053: the margin rule that governs this partner's sales now, if any.
        "earning": _earning_model(partner_id, now),
        "not_implemented": {
            "customer_limit": NOT_IMPLEMENTED["customer_limit"],
        },
    }
    if CUSTOMER_VIEW in permissions:
        out["customers"] = customer_counts(partner_id)
        out["recent"]["customers"] = partner_selectors.partner_customers_page(
            partner_id, limit=5
        ).items
    if COMMISSION_VIEW in permissions:
        series = commission_selectors.partner_sales_by_day(partner_id, start, end)
        out["charts"] = _charts(series)
        out["sales"] = {
            "transactions": sum(p["sales"] for p in series),
            "gross": _by_currency(series, "gross_minor", "basis_currency"),
            "commission_earned": _by_currency(series, "commission_minor", "currency"),
            "source": "commission_ledger",
        }
        money = commission_selectors.partner_money(partner_id, now)
        out["money"] = {
            "balances": money,
            "pending_commission": [
                {"currency": m["currency"], "amount_minor": m["pending_minor"]}
                for m in money
            ],
            "pending_payout": [
                {
                    "currency": m["currency"],
                    "amount_minor": m["available_minor"] + m["in_payouts_minor"],
                }
                for m in money
            ],
        }
        out["recent"]["commissions"] = commission_selectors.accruals_page(
            partner_id=partner_id, for_partner=True, limit=5
        ).items
        out["recent"]["payouts"] = commission_selectors.payouts_page(
            partner_id=partner_id, for_partner=True, limit=5
        ).items
    if SUBPARTNER_VIEW in permissions:
        out["sub_partners"] = sub_partner_counts(partner_id)
    return out


def _earning_model(partner_id: UUID, now: Any) -> dict[str, Any]:
    """``margin`` with the all-products discount when a margin rule governs the
    partner's sales, else ``commission``."""
    found = margins.effective_discount(partner_id, product_code="", at=now)
    if found is None:
        return {"model": "commission", "discount_bp": None}
    return {"model": "margin", "discount_bp": found[0]}


def _charts(series: list[dict[str, Any]]) -> dict[str, Any]:
    """The per-day chart data, summed here (same currency, same day) so the
    browser draws bars and adds nothing: ``gross`` and ``commission`` are
    ``{currency: [{date, amount_minor}]}``, ``transactions`` is ``[{date, count}]``."""
    gross: dict[str, dict[str, int]] = {}
    commission: dict[str, dict[str, int]] = {}
    transactions: dict[str, int] = {}
    for p in series:
        if p["basis_currency"]:
            day = gross.setdefault(p["basis_currency"], {})
            day[p["date"]] = day.get(p["date"], 0) + p["gross_minor"]
        day = commission.setdefault(p["currency"], {})
        day[p["date"]] = day.get(p["date"], 0) + p["commission_minor"]
        transactions[p["date"]] = transactions.get(p["date"], 0) + p["sales"]

    def rows(by_day: dict[str, int], key: str) -> list[dict[str, Any]]:
        return [{"date": d, key: by_day[d]} for d in sorted(by_day)]

    return {
        "gross": {c: rows(v, "amount_minor") for c, v in sorted(gross.items())},
        "commission": {c: rows(v, "amount_minor") for c, v in sorted(commission.items())},
        "transactions": rows(transactions, "count"),
    }


#: The same two sums, for the other workspaces' reports (ADR-047): one place
#: decides how a day's ledger rows become a currency total and a chart.
sales_by_currency = _by_currency
sales_charts = _charts


def sub_partner_counts(partner_id: UUID) -> dict[str, int]:
    return panel_selectors.hierarchy_counts(partner_id)


# --- balance ----------------------------------------------------------------------------


@in_scope
def balance(ctx: TenantContext) -> dict[str, Any]:
    """The partner's balance: its commission ledger, by what can happen next.

    There is no mutable balance field anywhere. The ledger is append-only and
    the balance is its sum; a manual adjustment is staff's, with a reason.
    Recharge is not built (``features``)."""
    authz.require(ctx, COMMISSION_VIEW)
    now = dj_timezone.now()
    return {
        "balances": commission_selectors.partner_money(ctx.partner_id, now),
        "entries": commission_selectors.accruals_page(
            partner_id=ctx.partner_id, for_partner=True, limit=10
        ).items,
        "not_implemented": {"recharge": NOT_IMPLEMENTED["recharge"]},
    }


# --- products, plans, prices ------------------------------------------------------------


def _price_rows(plan_version_ids: list[Any], partner_id: UUID, currency: str) -> Any:
    """``{plan_version_id: {interval: {partner, list}}}`` in ``currency``, from the
    live published price rows - one query. Display only: the amount a customer is
    actually charged is the one ``resolve_price`` computes at sale time."""
    found: dict[str, dict[str, dict[str, int]]] = {}
    for p in price_selectors.current_prices(plan_version_ids=plan_version_ids):
        if p["currency"] != currency or not p["plan_version_id"]:
            continue
        slot = found.setdefault(p["plan_version_id"], {}).setdefault(p["interval"], {})
        if p["channel"] == "partner" and p["partner_id"] == str(partner_id):
            slot["partner"] = p["amount_minor"]
        elif p["channel"] == "direct":
            slot.setdefault("list", p["amount_minor"])
    return found


@in_scope
def products(ctx: TenantContext) -> dict[str, Any]:
    """The Product Manager / Product Billing read: every product the partner may
    sell, with its grant, its own switches and the commercial facts around it."""
    authz.require(ctx, PRODUCT_VIEW)
    partner_id = ctx.partner_id
    assert partner_id is not None
    permissions = authz.effective_permissions(ctx)
    partner = partner_selectors.partner_row(partner_id)
    grants = partner_selectors.granted_products(partner_id)
    switches = product_settings.settings_by_product_code(partner_id)
    codes = sorted({g["product_code"] for g in grants})
    now = dj_timezone.now()

    rows = catalog.product_facts(codes)
    presentation = catalog.product_presentations(codes)
    plans = catalog.sellable_plans(codes, at=now)
    # Prices are the pricing view's: a role (or a partner that no longer sells)
    # that may see products but not pricing is shown the plans without them.
    price_map = (
        _price_rows(
            [p["plan_version_id"] for p in plans], partner_id, partner.payout_currency
        )
        if PRICING_VIEW in permissions
        else {}
    )
    may_see_customers = CUSTOMER_VIEW in permissions
    live_orgs = panel_selectors.live_organization_ids(partner_id)

    items = []
    for code in codes:
        product = rows.get(code)
        if product is None:
            continue
        mine = [g for g in grants if g["product_code"] == code]
        whole = any(g["plan_code"] is None for g in mine)
        own = switches.get(code, product_settings.DEFAULTS)
        trial = product["trial"]
        product_plans = [p for p in plans if p["product_code"] == code]
        prices = []
        for plan in product_plans:
            if not whole and plan["plan_code"] not in {g["plan_code"] for g in mine}:
                continue
            by_interval = price_map.get(str(plan["plan_version_id"]), {})
            prices.append(
                {
                    "plan_code": plan["plan_code"],
                    "plan_name": plan["plan_name"],
                    "intervals": [
                        {
                            "interval": interval,
                            "partner_price_minor": slot.get("partner"),
                            "list_price_minor": slot.get("list"),
                        }
                        for interval, slot in sorted(by_interval.items())
                    ],
                }
            )
        customers = subscriptions = None
        if may_see_customers:
            customers, subscriptions = provisioning_selectors.product_holders(
                live_orgs, code
            )
        items.append(
            {
                "code": code,
                "name": product["name"],
                "description": product["description"],
                "tagline": presentation.get(code, {}).get("tagline", ""),
                "icon": presentation.get(code, {}).get("icon", ""),
                "category": presentation.get(code, {}).get("category", ""),
                "status": product["status"],
                "granted": True,
                "grant_scope": "all_plans" if whole else "some_plans",
                "sale_permitted": True,
                "enabled_for_sale": own["enabled_for_sale"],
                "show_in_explore": own["show_in_explore"],
                "show_in_my_products": own["show_in_my_products"],
                "display_order": own["display_order"],
                "trial": {
                    "offered_by_platform": bool(trial and trial["enabled"]),
                    "days": trial["days"] if trial and trial["enabled"] else None,
                    "allowed_for_partner": all(g["trial_allowed"] for g in mine),
                },
                "portal_visible_by_platform": any(g["portal_visible"] for g in mine),
                "plans": prices,
                "customers": customers,
                "active_subscriptions": subscriptions,
            }
        )
    items.sort(key=lambda i: (i["display_order"] or 10**6, i["name"]))
    available = [
        {"code": p["code"], "name": p["name"], "tagline": p["tagline"]}
        for p in catalog.public_products()
        if p["code"] not in codes and p["availability"] != "coming_soon"
    ]
    return {
        "currency": partner.payout_currency,
        "products": items,
        "available": available,
        "can_manage": "platform.partner_product.manage" in permissions,
        "not_implemented": {"custom_css": NOT_IMPLEMENTED["custom_css"]},
    }


@in_scope
def plans(ctx: TenantContext) -> dict[str, Any]:
    """Plans this partner may sell, with the partner's price and the public list
    price side by side, and what each plan includes."""
    authz.require(ctx, PRICING_VIEW)
    partner = partner_selectors.partner_row(ctx.partner_id)
    now = dj_timezone.now()
    grants = partner_selectors.granted_products(ctx.partner_id)
    codes = sorted({g["product_code"] for g in grants})
    all_plans = catalog.sellable_plans(codes, at=now)
    visible = [
        p
        for p in all_plans
        if partner_selectors.may_sell_ignoring_switch(
            ctx.partner_id, product_code=p["product_code"], plan_code=p["plan_code"]
        )
    ]
    price_map = _price_rows(
        [p["plan_version_id"] for p in visible], ctx.partner_id, partner.payout_currency
    )
    switches = product_settings.settings_by_product_code(ctx.partner_id)
    discounts = {
        code: margins.effective_discount(ctx.partner_id, product_code=code, at=now)
        for code in {p["product_code"] for p in visible}
    }
    currency = CurrencyCode(partner.payout_currency)
    items = []
    for plan in visible:
        by_interval = price_map.get(str(plan["plan_version_id"]), {})
        discount = discounts.get(plan["product_code"])
        items.append(
            {
                "plan_version_id": str(plan["plan_version_id"]),
                "plan_code": plan["plan_code"],
                "plan_name": plan["plan_name"],
                "product_code": plan["product_code"],
                "product_name": plan["product_name"],
                "included_seats": plan["included_seats"],
                "trial_days": plan["trial_days"],
                "currency": partner.payout_currency,
                "enabled_for_sale": switches.get(plan["product_code"], {}).get(
                    "enabled_for_sale", True
                ),
                "earning": (
                    {"model": "margin", "discount_bp": discount[0]}
                    if discount is not None
                    else {"model": "commission", "discount_bp": None}
                ),
                "prices": [
                    _price_line(interval, slot, discount, currency)
                    for interval, slot in sorted(by_interval.items())
                ],
            }
        )
    return {
        "currency": partner.payout_currency,
        "plans": items,
        "can_create_plan": False,
        "not_implemented": {
            "add_ons": NOT_IMPLEMENTED["add_ons"],
            "pricing_rules": NOT_IMPLEMENTED["pricing_rules"],
        },
    }


def _price_line(
    interval: str,
    slot: dict[str, int],
    discount: tuple[int, Any] | None,
    currency: CurrencyCode,
) -> dict[str, Any]:
    """One plan price as a partner sees it (ADR-053), every figure the
    server's: list price, the partner's cost under its margin rule (list less
    the discount), the customer's price (the partner book, else list) and the
    margin between them. Without a margin rule the partner earns commission and
    has no cost; the difference to list is still shown."""
    list_minor = slot.get("list")
    customer = slot["partner"] if slot.get("partner") is not None else list_minor
    cost = (
        commission_domain.partner_cost(
            list_minor=list_minor, currency=currency, discount_bp=discount[0]
        )
        if discount is not None and list_minor is not None
        else None
    )
    margin = customer - cost if customer is not None and cost is not None else None
    return {
        "interval": interval,
        "partner_price_minor": slot.get("partner"),
        "list_price_minor": list_minor,
        "customer_price_minor": customer,
        "partner_cost_minor": cost,
        "margin_minor": margin,
        "margin_bp": commission_domain.margin_bp(customer_minor=customer, cost_minor=cost)
        if margin is not None and customer
        else None,
        "difference_minor": (
            list_minor - slot["partner"]
            if list_minor is not None and slot.get("partner") is not None
            else None
        ),
        "source": "partner_price_book" if "partner" in slot else "list",
    }


@in_scope
def packages(ctx: TenantContext) -> dict[str, Any]:
    """Staff-defined bundles whose every product the partner is granted - shown
    for reference. Selling a bundle, and authoring one, are not built."""
    authz.require(ctx, PRICING_VIEW)
    held = {g["product_code"] for g in partner_selectors.granted_products(ctx.partner_id)}
    items = []
    for bundle in catalog.bundle_listing():
        if bundle["status"] != "available" or not bundle["is_public"]:
            continue
        latest = bundle["versions"][0] if bundle["versions"] else None
        if latest is None or latest["published_at"] is None:
            continue
        product_codes = {i["product_code"] for i in latest["items"]}
        if not product_codes or not product_codes <= held:
            continue
        items.append(
            {
                "code": bundle["code"],
                "name": bundle["name"],
                "caption": bundle["caption"],
                "products": sorted(product_codes),
                "items": [
                    {
                        "product_name": i["product_name"],
                        "plan_name": i["plan_name"],
                        "quantity": i["quantity"],
                        "is_optional": i["is_optional"],
                    }
                    for i in latest["items"]
                ],
            }
        )
    return {
        "packages": items,
        "can_create_package": False,
        "not_implemented": {"partner_packages": NOT_IMPLEMENTED["partner_packages"]},
    }


@in_scope
def currencies(ctx: TenantContext) -> dict[str, Any]:
    """The currency registry as it applies to this partner.

    ``supported`` is the platform's own list (nothing arbitrary can be added),
    ``default`` the partner's payout currency (fixed at creation: commission is
    denominated in it), ``priced`` the currencies the partner has a price book
    in. There is no exchange-rate service: an amount is never converted and two
    currencies are never added."""
    authz.require(ctx, PRICING_VIEW)
    partner = partner_selectors.partner_row(ctx.partner_id)
    priced = sorted(
        {
            b["currency"]
            for b in price_selectors.price_books()
            if b["partner_id"] == str(ctx.partner_id) and b["is_active"]
        }
    )
    direct = set(price_selectors.direct_currencies())
    return {
        "default": partner.payout_currency,
        "supported": [
            {
                "code": c.value,
                "exponent": c.exponent,
                "partner_priced": c.value in priced,
                "listed_publicly": c.value in direct,
                "is_default": c.value == partner.payout_currency,
            }
            for c in CurrencyCode
        ],
        "fx": "none",
    }


@in_scope
def price_book(ctx: TenantContext) -> dict[str, Any]:
    """The partner's own fixed prices (read-only): what Workeasy360 set for it."""
    authz.require(ctx, PRICING_VIEW)
    partner = partner_selectors.partner_row(ctx.partner_id)
    now = dj_timezone.now()
    grants = partner_selectors.granted_products(ctx.partner_id)
    plans_ = catalog.sellable_plans(sorted({g["product_code"] for g in grants}), at=now)
    by_id = {str(p["plan_version_id"]): p for p in plans_}
    rules = []
    for price in price_selectors.current_prices(plan_version_ids=list(by_id)):
        if price["channel"] != "partner" or price["partner_id"] != str(ctx.partner_id):
            continue
        plan = by_id.get(price["plan_version_id"])
        if plan is None:
            continue
        rules.append(
            {
                "kind": "fixed_partner_price",
                "plan_code": plan["plan_code"],
                "plan_name": plan["plan_name"],
                "product_name": plan["product_name"],
                "interval": price["interval"],
                "currency": price["currency"],
                "amount_minor": price["amount_minor"],
                "effective_from": price["valid_from"],
            }
        )
    return {
        "currency": partner.payout_currency,
        "rules": rules,
        "can_edit": False,
        "not_implemented": {"pricing_rules": NOT_IMPLEMENTED["pricing_rules"]},
    }


# --- activity ------------------------------------------------------------------------------


@in_scope
def activity(ctx: TenantContext, *, limit: Any = 25, cursor: str | None = None) -> Page:
    """What has been done to, and by, this partner - from the one audit log,
    shaped for the partner: a label, who, and what it concerned. The recorded
    ``changes`` and any staff reason stay in the log and are not echoed here."""
    from workeasy360.commercial.partners import console_reads as partner_reads

    authz.require(ctx, ACTIVITY_VIEW)
    partner_id = ctx.partner_id
    assert partner_id is not None
    page = audit_selectors.audit_page(
        resource_ids=partner_reads.audit_resource_ids(partner_id),
        changes_match=("partner_id", str(partner_id)),
        limit=limit,
        cursor=cursor,
    )
    members = panel_selectors.member_user_ids(partner_id)
    emails = identity_selectors.emails_for_users(
        UUID(i["actor_user_id"])
        for i in page.items
        if i["actor_user_id"] and UUID(i["actor_user_id"]) in members
    )
    items = []
    for raw in page.items:
        shaped = partner_detail._activity_item(raw, emails)
        actor_id = UUID(raw["actor_user_id"]) if raw["actor_user_id"] else None
        items.append(
            {
                "id": shaped["id"],
                "occurred_at": shaped["occurred_at"],
                "label": shaped["label"],
                "target": shaped["target"],
                "outcome": shaped["outcome"],
                "actor": (
                    emails.get(actor_id, "")
                    if actor_id in members
                    else ("Workeasy360" if actor_id else "System")
                ),
            }
        )
    return Page(items=items, next_cursor=page.next_cursor, has_more=page.has_more)


# --- the honest feature list ------------------------------------------------------------


def features(ctx: TenantContext) -> dict[str, Any]:
    """What this portal can and cannot do for the caller.

    The portal reads this rather than assuming, so a screen for something the
    platform has no model for shows the reason and no control. ``permissions``
    is the resolved set (every request is still authorized again server-side).
    """
    permissions = authz.effective_permissions(ctx)
    return {
        "permissions": sorted(
            p for p in permissions if p.startswith("platform.partner_")
        ),
        "not_implemented": NOT_IMPLEMENTED,
        "roles": [
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "owner_only": spec.key == "owner",
            }
            for spec in capabilities.ROLE_PRESETS.values()
        ],
        "hierarchy": {"max_depth": hierarchy.MAX_DEPTH},
    }


# --- customers: the export ------------------------------------------------------------

EXPORT_MAX_ROWS: Final = 5000
EXPORT_COLUMNS: Final = (
    "Organization",
    "Relationship",
    "Owner activation",
    "Subscription",
    "Access",
    "Products",
    "Customer since",
)
_FORMULA_LEADERS: Final = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: Any) -> str:
    """A cell that a spreadsheet will show as text, never run as a formula.

    A customer-controlled name such as ``=HYPERLINK(...)`` would otherwise
    execute in the partner's spreadsheet; a leading apostrophe makes it text."""
    text = str(value if value is not None else "")
    return "'" + text if text.startswith(_FORMULA_LEADERS) else text


@in_scope
def export_customers(
    ctx: TenantContext, *, status: str | None = None
) -> tuple[list[list[str]], bool]:
    """``(rows, truncated)`` for the partner's customer CSV.

    Only what the customer list already shows this partner (never an unrelated
    organization, never a member's details), at most ``EXPORT_MAX_ROWS`` rows,
    audited by count. The view rate-limits it."""
    from workeasy360.platform.audit import services as audit

    authz.require(ctx, "platform.partner_customer.export")
    if ctx.is_break_glass:
        # Bulk export is never available under impersonation
        # (04-identity-authz.md section 6), so not to a staff guest view.
        raise PermissionDenied(
            "Export is not available in a guest view.",
            permission="platform.partner_customer.export",
            reason="blocked_under_impersonation",
        )
    partner_id = ctx.partner_id
    assert partner_id is not None
    rows: list[list[str]] = []
    cursor: str | None = None
    truncated = False
    while True:
        page = partner_selectors.partner_customers_page(
            partner_id, status=status, limit=100, cursor=cursor
        )
        for c in page.items:
            if len(rows) >= EXPORT_MAX_ROWS:
                truncated = True
                break
            rows.append(
                [
                    csv_cell(c["name"]),
                    csv_cell(c["link_status"]),
                    csv_cell(c["owner_activation"]["activation"]),
                    csv_cell(c["subscription_status"]),
                    csv_cell(c["access_class"]),
                    csv_cell(" ".join(c["products"])),
                    csv_cell(c["started_at"][:10]),
                ]
            )
        if truncated or not page.has_more:
            break
        if len(rows) >= EXPORT_MAX_ROWS:
            truncated = True
            break
        cursor = page.next_cursor
    audit.record(
        ctx,
        "partner.customers_exported",
        resource_type="partners.partner",
        resource_id=partner_id,
        changes={
            "partner_id": str(partner_id),
            "rows": len(rows),
            "truncated": truncated,
        },
    )
    return rows, truncated


# --- authentication and communication (statements, not controls) -------------------------


@in_scope
def authentication(ctx: TenantContext) -> dict[str, Any]:
    """How sign-in works for this partner and its customers, as the platform
    states it. Nothing here is a setting: the issuer, the signing keys, the
    staff grants and the session lifetime are Workeasy360's, and a partner can neither read nor change them."""
    from workeasy360.commercial.controlplane.white_label_setup import AUTHENTICATION
    from workeasy360.platform.identity import portals as portal_links

    authz.require(ctx, "platform.partner_profile.view")
    partner_id = ctx.partner_id
    assert partner_id is not None
    domains = [
        {"hostname": d.hostname, "status": d.status}
        for d in partner_selectors.domains_for(partner_id)
    ]
    users = panel_selectors.active_member_user_ids(partner_id)
    return {
        **AUTHENTICATION,
        "sign_in_url": portal_links.auth_url(),
        "brand_domains": domains,
        "team": {"members": len(users)},
        "controlled_by_workeasy360": [
            "Where people sign in (the central sign-in page)",
            "How long a session lasts",
            "The issuer and signing keys other products trust",
            "Which Workeasy360 staff may do what",
        ],
        "partner_choices": [
            "Your brand (name, colours, logo) shown on that page",
            "Who on your team has access, and in which role",
        ],
    }


@in_scope
def communication(ctx: TenantContext) -> dict[str, Any]:
    """Channels, wording and sender identity as they exist today."""
    from workeasy360.commercial.partners import portal as brand_portal
    from workeasy360.platform.notifications import templates

    authz.require(ctx, "platform.partner_profile.view")
    permissions = authz.effective_permissions(ctx)
    brand = partner_selectors.brand_for(ctx.partner_id)
    sender = brand_portal.view(brand.portal if brand else {})
    wired = ("platform.customer.owner_invited", "platform.partner.member_invited")
    return {
        "channels": [
            {
                "key": "email",
                "label": "Email",
                "status": "active",
                "via": "Workeasy360's mail provider",
                "sender_name": sender["sender_name"],
                "reply_to": sender["reply_to_email"],
                "use_for_invitations": sender["use_for_invitations"],
                "editable": "platform.partner_branding.manage" in permissions,
            },
            *[
                {"key": key, "label": label, "status": "not_available"}
                for key, label in (
                    ("webhook", "Webhook"),
                    ("whatsapp", "WhatsApp"),
                    ("sms", "SMS"),
                )
            ],
        ],
        "templates": [
            {
                "key": t.key,
                "name": t.key.rsplit(".", 1)[-1].replace("_", " ").capitalize(),
                "description": t.description,
                "subject": t.subject,
                "variables": list(t.variables),
                "editable": False,
            }
            for t in (templates.get(key) for key in wired)
        ],
        "alerts": [],
        "logs": [],
        "not_implemented": {
            key: NOT_IMPLEMENTED[key]
            for key in (
                "partner_smtp",
                "whatsapp_sms_webhook",
                "partner_alerts",
                "partner_templates",
            )
        }
        | {
            "logs": (
                "Deliveries are recorded per recipient organization or account, "
                "not per partner, so there is no partner-scoped delivery log yet."
            )
        },
    }


#: What each connection state tells the person. The portal draws the state it
#: is given; ``disconnected`` and ``error`` are in the vocabulary for a channel
#: that has a real connection to lose, and no channel can report them today
#: because the only channel the platform sends through is its own mail provider.
CHANNEL_STATES: Final = (
    ("connected", "Connected", "Messages can be sent through this channel."),
    (
        "needs_setup",
        "Needs setup",
        "The channel works, but something of yours is not filled in yet.",
    ),
    ("disconnected", "Disconnected", "The channel was connected and is not any more."),
    ("error", "Error", "The channel is connected but its last use failed."),
    ("not_available", "Not available", "The platform has no provider for this channel."),
)


@in_scope
def channel_manager(ctx: TenantContext) -> dict[str, Any]:
    """The state of each outbound channel, honestly.

    Email goes through the platform's own mail provider and is the only channel
    that exists; it is ``connected``, or ``needs_setup`` when the partner has a
    white-label brand but has not yet named the sender. WhatsApp, SMS and a
    partner webhook have no provider, so they are ``not_available`` - not
    ``disconnected`` (there was never a connection) and not faked."""
    data = communication(ctx)
    channels = []
    for ch in data["channels"]:
        if ch["key"] == "email":
            white_label = (
                capabilities.BRANDING_MANAGE
                in partner_selectors.active_capabilities(ctx.partner_id)
            )
            needs = white_label and not ch["sender_name"]
            channels.append(
                {
                    "key": "email",
                    "label": ch["label"],
                    "state": "needs_setup" if needs else "connected",
                    "detail": (
                        "Name the sender so email sent in your name says who it is from."
                        if needs
                        else "Sent through Workeasy360's mail provider"
                        + (
                            f", in the name {ch['sender_name']}."
                            if ch["sender_name"]
                            else "."
                        )
                    ),
                    "editable": ch["editable"],
                }
            )
        else:
            channels.append(
                {
                    "key": ch["key"],
                    "label": ch["label"],
                    "state": "not_available",
                    "detail": "No provider exists for this channel on the platform.",
                    "editable": False,
                }
            )
    return {
        "channels": channels,
        "states": [
            {"key": key, "label": label, "meaning": meaning}
            for key, label, meaning in CHANNEL_STATES
        ],
        "not_implemented": {
            "whatsapp_sms_webhook": NOT_IMPLEMENTED["whatsapp_sms_webhook"],
            "partner_smtp": NOT_IMPLEMENTED["partner_smtp"],
        },
    }


# --- orders ----------------------------------------------------------------------------


@in_scope
def orders(ctx: TenantContext, *, limit: Any = 25, cursor: str | None = None) -> Page:
    """Orders placed by this partner's customers while they were its customers,
    newest first. Identifiers, a status, the product codes and the amount - no
    buyer detail beyond the customer's organization name (and only while the
    relationship is live), and nothing from any other partner's customers."""
    authz.require(ctx, CUSTOMER_VIEW)
    assert ctx.partner_id is not None
    page = panel_selectors.orders_page(
        ctx.partner_id,
        order_summaries=payment_selectors.order_summaries(),
        limit=limit,
        cursor=cursor,
    )
    # ADR-053: each order's earning, from the ledger - the margin (with the
    # partner's cost) or the commission its lines accrued. Money needs the
    # commission view; without it the figures are None, never zero.
    money = COMMISSION_VIEW in authz.effective_permissions(ctx)
    earned = (
        commission_selectors.earnings_by_item(
            ctx.partner_id,
            [i for item in page.items for i in item["subscription_item_ids"]],
        )
        if money
        else {}
    )
    for item in page.items:
        rows = [earned[i] for i in item.pop("subscription_item_ids") if i in earned]
        item["earning"] = (
            {
                "model": "margin"
                if any(r["earning"] == "margin" for r in rows)
                else "commission",
                "amount_minor": sum(r["amount_minor"] for r in rows),
                "partner_cost_minor": sum(r["cost_minor"] or 0 for r in rows)
                if any(r["cost_minor"] is not None for r in rows)
                else None,
                "currency": rows[0]["currency"],
            }
            if money and rows
            else None
        )
        item["payment_status"] = _payment_status(item)
    return page


def _payment_status(order: dict[str, Any]) -> str:
    """How the order stands for money: awaiting payment, paid, nothing due
    (a trial, a free plan, a partner-billed account) or not paid."""
    status = order["status"]
    if status == "pending_payment":
        return "awaiting_payment"
    if order["paid_at"]:
        return "paid"
    if status == "fulfilled":
        return "nothing_due"
    return "not_paid"

"""Super Admin -> Store: dashboard, product-wise commercial rules, plans and
pricing (ADR-056).

The use cases behind the Store management screens. **None owns a rule**: reads
come from the modules that own the data (catalog, pricing, commissions,
partners, payments, provisioning) and every figure a channel earns comes from
the central engine, ``commissions.channel_pricing``; writes go through the
existing services (``pricing.publish_price``, ``catalog.update_plan``,
``commissions.rules.create_rule_version``, ``margins.set_margin``,
``partners.price_ranges``), which validate, version and audit them. What this
module adds is the product-wise shape a non-developer can work with.

Analytics here are reads of the projections (``OrderSummary``, the commission
ledger, ``CustomerSummary``); nothing on this page is a source of truth for a
transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.commissions import channel_pricing, domain
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.partners import price_ranges
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.pricing import selectors as price_selectors
from workeasy360.contracts.enums import (
    BillingInterval,
    CatalogStatus,
    PlanBillingType,
    PlanKind,
)
from workeasy360.contracts.errors import CatalogValidationError, CommercialRecordNotFound
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

CATALOG_VIEW: Final = "platform.staff_catalog.view"
CATALOG_MANAGE: Final = "platform.staff_catalog.manage"
PRICING_MANAGE: Final = "platform.staff_pricing.manage"
COMMISSION_VIEW: Final = "platform.staff_commission.view"
COMMISSION_MANAGE: Final = "platform.staff_commission.manage"
PAYMENT_VIEW: Final = "platform.staff_payment.view"
PARTNER_VIEW: Final = "platform.staff_partner.view"
CUSTOMER_VIEW: Final = "platform.staff_customer.view"
MAX_DAYS: Final = 366


def _allowed(ctx: TenantContext, permission: str) -> bool:
    return authz.check(ctx, permission).allowed


def _currency(requested: Any) -> tuple[str | None, list[str]]:
    """The currency to show: the one asked for if it has direct prices, else
    the public site's catalog currency, else the first available."""
    from workeasy360.platform.administration.site import public_site

    available = price_selectors.direct_currencies()
    wanted = str(requested or "").strip().upper()
    if wanted:
        if wanted not in available:
            raise CatalogValidationError(
                "currency has no direct prices.", field="currency"
            )
        return wanted, available
    default = str(public_site().get("catalog_currency") or "")
    if default in available:
        return default, available
    return (available[0] if available else None), available


# --- dashboard -------------------------------------------------------------------


@in_scope
def dashboard(ctx: TenantContext, *, days: Any = 30) -> dict[str, Any]:
    """The Store at a glance: catalog, sales by channel, subscriptions, trials,
    pending partner requests and top products - each section behind the grant
    of the screen it summarises."""
    from workeasy360.commercial.partners import promotion_requests
    from workeasy360.commercial.payments import orders
    from workeasy360.commercial.payments.staff import collections_by_day
    from workeasy360.commercial.provisioning.selectors import status_counts

    authz.require(ctx, CATALOG_VIEW)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise CatalogValidationError(
            f"days is a whole number from 1 to {MAX_DAYS}.", field="days"
        )
    end = timezone.now()
    start = end - timedelta(days=days)
    result: dict[str, Any] = {
        "period": {"days": days, "start": start.isoformat(), "end": end.isoformat()},
        "catalog": catalog.store_counts(),
    }
    if _allowed(ctx, PAYMENT_VIEW):
        top = orders.paid_orders_by_product(start=start, end=end)
        names = catalog.product_names(list(top["by_product"]))
        result["sales"] = {
            "orders": top["orders"],
            "revenue": [
                {
                    "currency": row["currency"],
                    "amount_minor": row["amount_minor"],
                    "payments": row["payments"],
                }
                for row in collections_by_day(start=start, end=end)
            ],
            "by_channel": _sales_by_channel(
                orders.paid_orders_by_channel(start=start, end=end), at=end
            ),
            "top_products": sorted(
                (
                    {"code": code, "name": names.get(code, code), "orders": n}
                    for code, n in top["by_product"].items()
                ),
                key=lambda row: (-row["orders"], row["name"]),
            )[:5],
        }
    if _allowed(ctx, COMMISSION_VIEW):
        result["affiliate_sales"] = commission_selectors.referral_sales(
            start=start, end=end
        )
    if _allowed(ctx, CUSTOMER_VIEW):
        counts = status_counts()
        result["subscriptions"] = {
            "active": counts.get("active", 0),
            "trialing": counts.get("trialing", 0),
            "past_due": counts.get("past_due", 0),
            "cancelled": counts.get("cancelled", 0) + counts.get("canceled", 0),
        }
    if _allowed(ctx, PARTNER_VIEW):
        pending = promotion_requests.pending_request_count()
        result["partner_requests"] = {
            "promote": pending.get("promote", 0),
            "sell": pending.get("sell", 0),
        }
    return result


def _sales_by_channel(rows: list[dict[str, Any]], *, at: datetime) -> dict[str, Any]:
    """Paid orders split into the Store levels: direct, reseller (a partner
    ordering for its customer) and white-label (its branded store, or a
    partner holding the brand capability)."""
    out: dict[str, dict[str, Any]] = {
        key: {"orders": 0, "amounts": {}} for key in ("direct", "reseller", "white_label")
    }
    experience: dict[Any, str] = {}
    for row in rows:
        if row["channel"] == "storefront":
            key = "white_label"
        elif row["channel"] == "partner" and row["partner_id"]:
            pid = row["partner_id"]
            if pid not in experience:
                experience[pid] = partner_selectors.selling_experience_at(pid, at)
            key = experience[pid]
        else:
            key = "direct"
        bucket = out[key]
        bucket["orders"] += row["orders"]
        bucket["amounts"][row["currency"]] = (
            bucket["amounts"].get(row["currency"], 0) + row["amount_minor"]
        )
    return {
        key: {
            "orders": value["orders"],
            "amounts": [
                {"currency": c, "amount_minor": a}
                for c, a in sorted(value["amounts"].items())
            ],
        }
        for key, value in out.items()
    }


# --- commercial rules, product-wise --------------------------------------------------


def _priced_version(plan: dict[str, Any]) -> dict[str, Any] | None:
    """The version whose prices the board shows: the latest published one,
    else the latest draft."""
    versions = plan["versions"]
    return next(
        (v for v in versions if v["published_at"]), versions[0] if versions else None
    )


def _plan_rows(product_codes: list[str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for plan in catalog.plan_listing():
        if product_codes is not None and plan["product"]["code"] not in product_codes:
            continue
        if plan["status"] == CatalogStatus.RETIRED.value:
            continue
        version = _priced_version(plan)
        if version is None:
            continue
        rows.append(
            {
                "plan_id": plan["id"],
                "plan_version_id": version["id"],
                "product_code": plan["product"]["code"],
                "plan_code": plan["code"],
                "plan": plan,
                "version": version,
            }
        )
    return rows


def _rule_summary(rule: domain.RuleView | None) -> dict[str, Any] | None:
    if rule is None:
        return None
    return {
        "code": rule.code,
        "version": rule.version,
        "scope": "plan"
        if rule.plan_code
        else ("product" if rule.product_code else "all"),
        "rate_bp": rule.rate_bp,
        "fixed_amount_minor": rule.fixed_amount_minor,
        "fixed_currency": rule.fixed_currency,
        "recurring": rule.recurring,
        "recurring_months": rule.recurring_months,
        "effective_from": rule.effective_from.isoformat(),
    }


def _margin_summary(margin: domain.MarginView | None) -> dict[str, Any] | None:
    if margin is None:
        return None
    return {
        "code": margin.code,
        "version": margin.version,
        "scope": "product" if margin.product_code else "all",
        "discount_bp": margin.discount_bp,
        "recurring": margin.recurring,
        "effective_from": margin.effective_from.isoformat(),
    }


@in_scope
def commercial_rules(ctx: TenantContext, *, currency: Any = None) -> dict[str, Any]:
    """Every product with what each Store level gets: the customer price per
    plan, affiliate eligibility and commission, reseller eligibility and margin,
    white-label eligibility, margin and allowed price ranges - and, per plan,
    the central engine's breakdown for each channel (price, partner share,
    platform share). One screen; every value editable from it."""
    authz.require(ctx, CATALOG_VIEW)
    shown, available = _currency(currency)
    now = timezone.now()
    see_money = _allowed(ctx, COMMISSION_VIEW)
    products = [p for p in catalog.visible_products() if p["status"] != "retired"]
    plans = _plan_rows([p["code"] for p in products])
    schedule = (
        price_selectors.price_schedule(
            plan_version_ids=[UUID(r["plan_version_id"]) for r in plans], at=now
        )
        if plans
        else []
    )
    quotes: dict[str, dict[tuple[str, str], Any]] = {}
    if shown and see_money:
        quotes = {
            channel: channel_pricing.channel_prices(
                plans, currency=shown, channel=channel, at=now
            )
            for channel in channel_pricing.CHANNELS
        }
    ranges = (
        price_ranges.ranges(plan_ids=[UUID(r["plan_id"]) for r in plans]) if plans else []
    )
    rules = commission_selectors.rule_views() if see_money else []
    margin_rows = commission_selectors.margin_views() if see_money else []
    who = channel_pricing.NO_PARTNER

    items = []
    for product in products:
        code = product["code"]
        mine = [r for r in plans if r["product_code"] == code]
        plan_items = []
        for row in mine:
            pid = row["plan_version_id"]
            plan_items.append(
                {
                    "plan_id": row["plan_id"],
                    "plan_version_id": pid,
                    "code": row["plan_code"],
                    "name": row["plan"]["name"],
                    "status": row["plan"]["status"],
                    "kind": row["plan"]["kind"],
                    "billing_type": row["plan"]["billing_type"],
                    "recommended": row["plan"].get("recommended", False),
                    "published": row["version"]["published_at"] is not None,
                    "trial_days": row["version"]["trial_days"],
                    "prices": [
                        p
                        for p in schedule
                        if p["plan_version_id"] == pid
                        and (not shown or p["currency"] == shown)
                    ],
                    "ranges": [r for r in ranges if r["plan_id"] == row["plan_id"]],
                    "channels": {
                        channel: {
                            interval: q.as_dict()
                            for (target, interval), q in by_plan.items()
                            if target == pid
                        }
                        for channel, by_plan in quotes.items()
                    },
                }
            )
        entry: dict[str, Any] = {
            "id": str(product["id"]),
            "code": code,
            "name": product["name"],
            "icon": product["icon"],
            "category": product["category"],
            "status": product["manager_status"],
            "listing": product["listing"],
            "channels": {
                "customer_store": product["customer_store_enabled"],
                "affiliate": product["affiliate_enabled"],
                "reseller": product["reseller_enabled"],
                "white_label": product["white_label_enabled"],
            },
            "trial": product["trial"],
            "plans": plan_items,
        }
        if see_money:
            entry["affiliate_commission"] = _rule_summary(
                domain.select_rule(rules, partner_id=who, product_code=code, at=now)
            )
            entry["margins"] = {
                exp: _margin_summary(
                    domain.select_margin(
                        margin_rows,
                        partner_id=who,
                        experience=exp,
                        product_code=code,
                        at=now,
                    )
                )
                for exp in (channel_pricing.RESELLER, channel_pricing.WHITE_LABEL)
            }
        items.append(entry)
    return {
        "currency": shown,
        "currencies": available,
        "can_manage": {
            "catalog": _allowed(ctx, CATALOG_MANAGE),
            "pricing": _allowed(ctx, PRICING_MANAGE),
            "commission": _allowed(ctx, COMMISSION_MANAGE),
        },
        "shows_money": see_money,
        "products": items,
    }


def affiliate_rule_code(product_code: str, plan_code: str = "") -> str:
    """The programme-wide commission chain of one product (or one plan):
    ``cr-<product>[-<plan>]``, a slug of at most 48 characters."""
    raw = f"cr-{product_code}" + (f"-{plan_code}" if plan_code else "")
    return raw.replace("_", "-").lower()[:48].rstrip("-")


@in_scope
def set_product_commission(
    ctx: TenantContext,
    *,
    product_code: Any,
    plan_code: Any = "",
    rate_bp: Any = 0,
    fixed_amount_minor: Any = None,
    fixed_currency: Any = "",
    recurring: Any = False,
    recurring_months: Any = None,
    effective_from: datetime | None = None,
    reason: Any = None,
) -> dict[str, Any]:
    """Set the commission every partner earns on one product (or plan) - the
    programme default the Commercial Rules screen edits. A new version of the
    product's chain through ``rules.create_rule_version``: versioned, never
    backdated, audited with the version it replaces."""
    from workeasy360.commercial.commissions import rules

    authz.require(ctx, COMMISSION_MANAGE)
    product = str(product_code or "").strip()
    if not product or catalog.product_facts([product]).get(product) is None:
        raise CommercialRecordNotFound("No such product.", product_code=product)
    plan = str(plan_code or "").strip()
    rule = rules.create_rule_version(
        ctx,
        code=affiliate_rule_code(product, plan),
        rate_bp=rate_bp if fixed_amount_minor is None else 0,
        reason=reason or "Commission set on Store > Commercial rules.",
        product_code=product,
        plan_code=plan,
        effective_from=effective_from,
        recurring=recurring,
        fixed_amount_minor=fixed_amount_minor,
        fixed_currency=fixed_currency,
        recurring_months=recurring_months,
    )
    return {"code": rule.code, "version": rule.version, "id": str(rule.id)}


# --- plans and pricing -------------------------------------------------------------


@in_scope
def plan_board(ctx: TenantContext, *, product_id: UUID) -> dict[str, Any]:
    """Products -> one product -> Plans & Pricing: every plan of the product
    with its terms (seats, trial, features), its current and scheduled prices
    in every direct book, and the direct books a price can go into."""
    authz.require(ctx, CATALOG_VIEW)
    facts = catalog.product_facts_by_id(product_id)
    if facts is None:
        raise CommercialRecordNotFound("No such product.", product_id=str(product_id))
    code = facts["code"]
    plans = catalog.plan_listing(product_code=code)
    version_ids = [UUID(v["id"]) for p in plans for v in p["versions"][:2]]
    schedule = (
        price_selectors.price_schedule(plan_version_ids=version_ids)
        if version_ids
        else []
    )
    books = [
        b
        for b in price_selectors.price_books()
        if b["channel"] == "direct" and b["is_active"]
    ]
    out = []
    for plan in plans:
        published = next((v for v in plan["versions"] if v["published_at"]), None)
        draft = next((v for v in plan["versions"] if not v["published_at"]), None)
        priced = published or draft
        out.append(
            {
                **{
                    k: plan[k]
                    for k in (
                        "id",
                        "code",
                        "name",
                        "caption",
                        "description",
                        "status",
                        "is_public",
                        "sort_order",
                        "billing_type",
                        "kind",
                    )
                },
                "recommended": plan.get("recommended", False),
                "version": published,
                "draft_version": draft
                if draft
                and (published is None or draft["version"] > published["version"])
                else None,
                "prices": [
                    p for p in schedule if priced and p["plan_version_id"] == priced["id"]
                ],
            }
        )
    return {
        "product": {"id": str(product_id), "code": code, "name": facts["name"]},
        "books": books,
        "plans": out,
        "features": catalog.features_for_product(product_id),
        "can_manage": {
            "catalog": _allowed(ctx, CATALOG_MANAGE),
            "pricing": _allowed(ctx, PRICING_MANAGE),
        },
    }


@in_scope
def duplicate_plan(
    ctx: TenantContext, *, plan_id: UUID, code: Any, name: Any
) -> dict[str, Any]:
    """Copy a plan as a new **draft**: its presentation, its latest terms
    (seats, trial, features) as a draft version, and its current direct prices
    (amount, discount, set-up fee) on that version. Nothing is sold until staff
    publish the version and make the plan available - the ordinary path."""
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.pricing import services as pricing

    authz.require(ctx, CATALOG_MANAGE)
    authz.require(ctx, PRICING_MANAGE)
    source = next((p for p in catalog.plan_listing() if p["id"] == str(plan_id)), None)
    if source is None:
        raise CommercialRecordNotFound("No such plan.", plan_id=str(plan_id))
    terms = _priced_version(source)
    prices = (
        [
            p
            for p in price_selectors.price_schedule(plan_version_ids=[UUID(terms["id"])])
            if p["state"] == "current" and p["amount_minor"] is not None
        ]
        if terms
        else []
    )
    with transaction.atomic():
        plan = catalog_services.create_plan(
            ctx,
            product_id=UUID(source["product"]["id"]),
            code=str(code or "").strip(),
            name=str(name or "").strip(),
            description=source["description"],
            caption=source["caption"],
            is_public=source["is_public"],
            sort_order=source["sort_order"] + 1,
            billing_type=PlanBillingType(source["billing_type"]),
            kind=PlanKind(source["kind"]),
            addon_for_product_id=UUID(source["addon_for"]["id"])
            if source["addon_for"]
            else None,
        )
        version = catalog_services.draft_plan_version(
            ctx,
            plan_id=plan.id,
            features=dict(terms["features"]) if terms else {},
            included_seats=terms["included_seats"] if terms else 1,
            max_seats=terms["max_seats"] if terms else None,
            trial_days=terms["trial_days"] if terms else 0,
        )
        for price in prices:
            pricing.publish_price(
                ctx,
                price_book_id=UUID(price["price_book_id"]),
                interval=BillingInterval(price["interval"]),
                plan_version_id=version.id,
                amount_minor=price["amount_minor"],
                discount_bp=price["discount_bp"],
                setup_fee_minor=price["setup_fee_minor"],
            )
    return {
        "id": str(plan.id),
        "code": plan.code,
        "draft_version_id": str(version.id),
        "prices_copied": len(prices),
    }


@in_scope
def quote_preview(
    ctx: TenantContext,
    *,
    plan_version_id: Any,
    interval: Any,
    currency: Any,
    channel: Any,
    partner_id: Any = None,
) -> dict[str, Any]:
    """The central engine's answer for one plan, cycle and channel - optionally
    for one partner - exactly as checkout and the ledger would compute it."""
    authz.require(ctx, CATALOG_VIEW)
    authz.require(ctx, COMMISSION_VIEW)
    chosen = str(channel or "")
    if chosen not in channel_pricing.CHANNELS:
        raise CatalogValidationError(
            "channel is direct, affiliate, reseller or white_label.", field="channel"
        )
    try:
        version_id = UUID(str(plan_version_id))
        partner = UUID(str(partner_id)) if partner_id else None
        cycle = BillingInterval(str(interval or "monthly"))
    except ValueError as exc:
        raise CatalogValidationError("A value is not valid.", field="query") from exc
    shown, _available = _currency(currency)
    if shown is None:
        raise CatalogValidationError("No currency has direct prices.", field="currency")
    quote = channel_pricing.resolve_channel_price(
        plan_version_id=version_id,
        interval=cycle,
        currency=shown,
        channel=chosen,
        partner_id=partner,
    )
    if quote is None:
        raise CommercialRecordNotFound(
            "This plan has no price in that currency and billing cycle."
        )
    return quote.as_dict()

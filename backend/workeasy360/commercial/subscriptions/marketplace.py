"""What is for sale: the organization console's Explore page (Phase 8
extension) and the public site's catalog (Phase 9).

Both are built from the same two reads - the catalog's public, available,
listed offers and pricing's direct books - so the public site and Explore can
never disagree about what exists. Prices here are **informational**: what an
order commits to is resolved again, exactly (contract and regional rules
included), when it is placed.

- ``offers`` - for an organization: prices in its own billing currency and
  region, plus the products it already holds. Readable by every member
  (``platform.organization.view``): a price list is public information.
- ``public_catalog`` / ``public_product`` - for anyone: products, their plans
  and bundles, in a currency the visitor picks among those with direct prices.
  No organization, no authentication, cached for ``PUBLIC_CACHE_SECONDS``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.cache import cache
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.pricing import selectors as pricing
from workeasy360.contracts.enums import SubscriptionStatus
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

PUBLIC_CACHE_SECONDS = 60
_PUBLIC_CACHE_KEY = "w360:catalog:public:v1:{currency}"


def _attach_prices(
    listing: dict[str, list[dict[str, Any]]],
    *,
    currency: str | None,
    region: str,
    at: Any,
) -> None:
    """Give every plan and bundle in ``listing`` its direct list prices -
    one query for the lot."""
    prices: dict[tuple[str, str], dict[str, Any]] = {}
    if currency:
        prices = pricing.direct_list_prices(
            currency=currency,
            region=region,
            plan_version_ids=[UUID(p["plan_version_id"]) for p in listing["plans"]],
            bundle_version_ids=[UUID(b["bundle_version_id"]) for b in listing["bundles"]],
            at=at,
        )

    def priced(target: str) -> list[dict[str, Any]]:
        return [value for (tid, _interval), value in prices.items() if tid == target]

    for plan in listing["plans"]:
        plan["prices"] = priced(plan["plan_version_id"])
    for bundle in listing["bundles"]:
        bundle["prices"] = priced(bundle["bundle_version_id"])


def offers(ctx: TenantContext) -> dict[str, Any]:
    from workeasy360.commercial.billing.services import billing_account_or_none
    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_for_organization,
    )

    authz.require(ctx, "platform.organization.view")
    now = timezone.now()
    listing = catalog.marketplace_offers(at=now)
    account = billing_account_or_none(ctx.organization_id)
    currency = account.currency if account is not None else None
    region = account.billing_region if account is not None else ""
    _attach_prices(listing, currency=currency, region=region, at=now)

    held: set[str] = set()
    for subscription in subscriptions_for_organization():
        if subscription.status is SubscriptionStatus.EXPIRED:
            continue
        held.update(item.product_code for item in subscription.effective_items(now))
    return {
        "currency": currency,
        "billing_account": account is not None,
        "held_products": sorted(p for p in held if p),
        **listing,
    }


# --- the public catalog ----------------------------------------------------------


def _currency_for(requested: Any, available: list[str]) -> str | None:
    from workeasy360.platform.administration.site import public_site

    wanted = str(requested or "").strip().upper()
    if wanted in available:
        return wanted
    default = str(public_site()["catalog_currency"])
    if default in available:
        return default
    return available[0] if available else None


def _starting_price(plans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The lowest flat monthly price among a product's standard plans, else
    the lowest flat price of any interval - what "from X / month" shows."""
    flat = [
        price
        for plan in plans
        if plan["kind"] == "standard"
        for price in plan["prices"]
        if price.get("amount_minor") is not None
    ]
    monthly = [p for p in flat if p["interval"] == "monthly"]
    pool = monthly or flat
    if not pool:
        return None
    best = min(pool, key=lambda p: p["amount_minor"])
    return {
        "amount_minor": best["amount_minor"],
        "currency": best["currency"],
        "interval": best["interval"],
    }


def _trial_offer(
    config: dict[str, Any] | None, plans: list[dict[str, Any]], availability: str
) -> dict[str, Any]:
    """What a visitor may be told about a product's free trial (Phase 11).

    Offered only when staff configured and enabled it, the product is on sale
    and at least one eligible plan is itself on sale right now - the same
    conditions ``trials.start_product_trial`` re-checks, so the button never
    promises what the server would refuse. Nothing here is a default: with no
    configuration there is no trial.
    """
    none = {
        "available": False,
        "duration_days": None,
        "seat_limit": None,
        "payment_method_required": False,
        "plan_codes": [],
    }
    if not config or not config["enabled"] or availability != "available":
        return none
    eligible = set(config["eligible_plan_codes"])
    codes = [
        plan["plan_code"]
        for plan in plans
        if plan["kind"] == "standard" and plan["plan_code"] in eligible
    ]
    if not codes:
        return none
    return {
        "available": True,
        "duration_days": config["duration_days"],
        "seat_limit": config["seat_limit"],
        "payment_method_required": False,
        "plan_codes": codes,
    }


def _products_with_offers(
    currency: str | None, region: str, now: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The public products, each with its on-sale plans priced in ``currency``
    (and ``region``), its starting price and its trial offer - and the public
    bundles. The one assembly the public catalog and an organization's Explore
    page both use, so the two can never disagree about what is for sale."""
    products = catalog.public_products()
    listing = catalog.marketplace_offers(at=now)
    _attach_prices(listing, currency=currency, region=region, at=now)
    by_product: dict[str, list[dict[str, Any]]] = {}
    for plan in listing["plans"]:
        public_plan = {
            key: plan[key]
            for key in (
                "plan_version_id",
                "plan_code",
                "name",
                "caption",
                "description",
                "billing_type",
                "kind",
                "addon_for",
                "included_seats",
                "max_seats",
                "trial_days",
                "prices",
            )
        }
        by_product.setdefault(plan["product_code"], []).append(public_plan)
    for product in products:
        # A coming-soon product has nothing on sale, whatever its plans say.
        plans = (
            by_product.get(product["code"], [])
            if product["availability"] == "available"
            else []
        )
        product["plans"] = plans
        product["starting_price"] = _starting_price(plans)
        trial = _trial_offer(
            product.pop("trial_config", None), plans, product["availability"]
        )
        product["trial"] = trial
        for plan in plans:
            plan["trial_available"] = plan["plan_code"] in trial["plan_codes"]
    return products, listing["bundles"]


def _build_public_catalog(currency: str | None, available: list[str]) -> dict[str, Any]:
    products, bundles = _products_with_offers(currency, "", timezone.now())
    return {
        "currency": currency,
        "currencies": available,
        "products": products,
        "bundles": bundles,
    }


def explore(ctx: TenantContext) -> dict[str, Any]:
    """The products an organization can discover, with what *this*
    organization may do about each (Phase 11).

    Prices are in the organization's own billing currency and region. For each
    product the answer also says what the organization already has and whether
    a trial is still open to it - decided here, on the server, from the same
    facts ``start_product_trial`` re-checks - so the button a page shows is
    never one the server would refuse for a reason the page could have known:

    ``trial_state``: ``available`` (start one), ``active`` (running now),
    ``used`` (had one), ``owned`` (a paid line holds it), ``unavailable`` (no
    trial is offered).
    """
    from workeasy360.commercial.billing.services import billing_account_or_none
    from workeasy360.commercial.subscriptions import my_products
    from workeasy360.commercial.subscriptions.selectors import trialled_product_codes

    authz.require(ctx, "platform.organization.view")
    now = timezone.now()
    account = billing_account_or_none(ctx.organization_id)
    currency = account.currency if account is not None else None
    region = account.billing_region if account is not None else ""
    products, bundles = _products_with_offers(currency, region, now)

    held = my_products.holdings(at=now)
    trialled = trialled_product_codes(ctx.organization_id)
    for product in products:
        holding = held.get(product["code"])
        product["holding"] = holding.source if holding is not None else None
        if holding is not None and holding.source == "subscription":
            state = "owned"
        elif holding is not None:
            state = "active"
        elif product["code"] in trialled:
            state = "used"
        elif product["trial"]["available"]:
            state = "available"
        else:
            state = "unavailable"
        product["trial_state"] = state
    return {
        "currency": currency,
        "billing_account": account is not None,
        "products": products,
        "bundles": bundles,
    }


def public_catalog(*, currency: Any = None) -> dict[str, Any]:
    """The whole public catalog in one currency. Cached per currency for
    ``PUBLIC_CACHE_SECONDS``; a cache outage falls back to the database."""
    available = pricing.direct_currencies()
    chosen = _currency_for(currency, available)
    key = _PUBLIC_CACHE_KEY.format(currency=chosen or "none")
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
    if isinstance(cached, dict):
        return cached
    built = _build_public_catalog(chosen, available)
    try:
        cache.set(key, built, PUBLIC_CACHE_SECONDS)
    except Exception as exc:
        logger.warning("catalog.public.cache_unavailable", error_type=type(exc).__name__)
    return built


def public_product(slug: Any, *, currency: Any = None) -> dict[str, Any] | None:
    """One public product by slug, with the bundles that include it, or
    ``None`` - private, draft and withdrawn products are indistinguishable
    from ones that never existed."""
    catalog_view = public_catalog(currency=currency)
    wanted = str(slug or "")
    product = next((p for p in catalog_view["products"] if p["slug"] == wanted), None)
    if product is None:
        return None
    return {
        "currency": catalog_view["currency"],
        "currencies": catalog_view["currencies"],
        "product": product,
        "bundles": [
            b
            for b in catalog_view["bundles"]
            if any(i["product_code"] == product["code"] for i in b["includes"])
        ],
    }

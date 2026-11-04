"""The Super Admin Store (2026-10-31): the central catalog as a marketplace.

Super Admin -> Product Store -> Store shows every product as a store card -
banner, mark, category, summary, the price a customer would see it from, a
ribbon ("New!", "Coming soon", "Draft", ...) - with the filters, facets and
sorting a marketplace page has. Composed from reads that already exist, so it
can never disagree with the customer Store:

- the products are ``catalog.visible_products`` (drafts and hidden ones too -
  staff see the whole catalog, and each card says whether it is in the store);
- what a customer can buy is ``catalog.marketplace_offers`` priced from the
  direct books by ``pricing.direct_list_prices`` - the public catalog's own two
  reads. A product that is not on sale has no price, never an invented one;
- the "from" price is the public catalog's: the lowest flat monthly price of a
  standard plan, else the lowest flat price of any cycle.

Read only. Nothing here changes a product, and nothing shown is access: a
customer still uses a product only through an entitlement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.catalog.selectors import CHANNEL_FLAGS
from workeasy360.commercial.pricing import selectors as pricing
from workeasy360.contracts.enums import BillingInterval
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.administration.site import public_site
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW = "platform.staff_catalog.view"

#: A product activated this recently wears the "New!" ribbon.
NEW_FOR = timedelta(days=30)
MAX_QUERY = 100

#: The channel ("program") filter: every product, or those open to one channel.
PROGRAMS: dict[str, str] = {
    "all": "All programs",
    "customer_store": "Customer Store",
    "reseller": "Resellers",
    "affiliate": "Affiliates",
    "white_label": "White label",
}

#: Where a product stands in the customer Store.
STATUSES: dict[str, str] = {
    "live": "In the store",
    "coming_soon": "Coming soon",
    "not_listed": "Not in the store",
}

#: How a product may be bought: the billing cycles it is priced in, or a trial.
PLAN_TYPES: dict[str, str] = {
    BillingInterval.MONTHLY.value: "Monthly",
    BillingInterval.QUARTERLY.value: "Quarterly",
    BillingInterval.SEMI_ANNUAL.value: "Every six months",
    BillingInterval.ANNUAL.value: "Yearly",
    BillingInterval.ONE_TIME.value: "One-time",
    "free_trial": "Free trial",
}

SORTS: dict[str, str] = {
    "recommended": "Recommended",
    "newest": "Newest",
    "price_asc": "Price: low to high",
    "price_desc": "Price: high to low",
    "name": "Name: A to Z",
}


def _choice(raw: Any, allowed: dict[str, str], *, field: str, default: str) -> str:
    value = str(raw or "").strip() or default
    if value not in allowed:
        raise CatalogValidationError(
            f"{field} must be one of: {', '.join(allowed)}.", field=field
        )
    return value


def _choices(raw: list[str], allowed: dict[str, str], *, field: str) -> list[str]:
    """``a,b`` or a repeated parameter; unknown values are refused, not
    ignored, so a typo cannot look like an empty result."""
    values = [v.strip() for item in raw for v in str(item).split(",") if v.strip()]
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise CatalogValidationError(
            f"{field} must be among: {', '.join(allowed)}.", field=field
        )
    return [v for v in allowed if v in values]


def _bound(raw: Any, currency: str | None, *, field: str) -> int | None:
    """A price bound typed in major units (``49.99``), as minor units of the
    store's currency. The server converts; the browser never does."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise CatalogValidationError(f"{field} must be a number.", field=field) from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("100000000"):
        raise CatalogValidationError(f"{field} must be a number from 0.", field=field)
    exponent = CurrencyCode(currency).exponent if currency else 2
    return int(amount.scaleb(exponent).to_integral_value())


def _currency(requested: Any, available: list[str]) -> str | None:
    wanted = str(requested or "").strip().upper()
    if wanted and wanted not in available:
        raise CatalogValidationError(
            "There are no store prices in that currency.", field="currency"
        )
    if wanted:
        return wanted
    default = str(public_site()["catalog_currency"])
    if default in available:
        return default
    return available[0] if available else None


def _on_sale_plans(
    currency: str | None, now: datetime
) -> dict[str, list[dict[str, Any]]]:
    """Each product's plans a customer can buy now, with their direct list
    prices in ``currency`` - the public catalog's two reads, one query each."""
    listing = catalog.marketplace_offers(at=now)
    prices: dict[tuple[str, str], dict[str, Any]] = {}
    if currency:
        prices = pricing.direct_list_prices(
            currency=currency,
            plan_version_ids=[UUID(p["plan_version_id"]) for p in listing["plans"]],
            at=now,
        )
    by_target: dict[str, list[dict[str, Any]]] = {}
    for (target, _interval), value in prices.items():
        by_target.setdefault(target, []).append(value)
    out: dict[str, list[dict[str, Any]]] = {}
    for plan in listing["plans"]:
        out.setdefault(plan["product_code"], []).append(
            {
                "name": plan["name"],
                "kind": plan["kind"],
                "prices": by_target.get(plan["plan_version_id"], []),
            }
        )
    return out


def _flat(plans: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (plan, price)
        for plan in plans
        if plan["kind"] == "standard"
        for price in plan["prices"]
        if price.get("amount_minor") is not None
    ]


def price_from(
    plans: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The card's "from" price, and the yearly saving on that same plan when
    its annual price is below twelve monthly ones (the struck-through amount
    is that real twelve-month total, never a made-up list price)."""
    flat = _flat(plans)
    monthly = [
        pair for pair in flat if pair[1]["interval"] == BillingInterval.MONTHLY.value
    ]
    pool = monthly or flat
    if not pool:
        return None, None
    plan, best = min(pool, key=lambda pair: pair[1]["amount_minor"])
    price = {
        "amount_minor": best["amount_minor"],
        "currency": best["currency"],
        "interval": best["interval"],
        "plan_name": plan["name"],
        # ADR-056: the price before a promotional discount, when there is one.
        "list_amount_minor": best.get("list_amount_minor")
        if best.get("discount_bp")
        else None,
        "discount_bp": best.get("discount_bp") or 0,
    }
    if best["interval"] != BillingInterval.MONTHLY.value:
        return price, None
    annual = next(
        (
            p
            for p in plan["prices"]
            if p["interval"] == BillingInterval.ANNUAL.value
            and p.get("amount_minor") is not None
        ),
        None,
    )
    twelve = best["amount_minor"] * 12
    if annual is None or not 0 < annual["amount_minor"] < twelve:
        return price, None
    return price, {
        "amount_minor": annual["amount_minor"],
        "compare_at_minor": twelve,
        "currency": annual["currency"],
        "save_percent": round(100 * (twelve - annual["amount_minor"]) / twelve),
    }


def plan_types_of(plans: list[dict[str, Any]], *, trial: bool) -> list[str]:
    """The ``PLAN_TYPES`` a product is bought in: its priced billing cycles,
    and ``free_trial`` when a trial is offered."""
    types = sorted(
        {price["interval"] for _plan, price in _flat(plans)},
        key=list(PLAN_TYPES).index,
    )
    return [*types, "free_trial"] if trial else types


#: Curated collections (ADR-056): Super Admin's hand-set badges, plus "new"
#: for a product launched within ``NEW_FOR`` or marked new by hand.
COLLECTIONS: dict[str, str] = {
    "all": "All products",
    "best_seller": "Best Seller",
    "featured": "Featured",
    "popular": "Popular",
    "new": "New",
}


def badges_of(row: dict[str, Any] | None, now: datetime) -> list[str]:
    """The collections a product belongs to, from its listing row."""
    if row is None:
        return []
    badges = [key for key in ("best_seller", "featured", "popular") if row.get(key)]
    activated = row.get("activated_at")
    recent = bool(activated) and now - datetime.fromisoformat(str(activated)) <= NEW_FOR
    if row.get("badge_new") or recent:
        badges.append("new")
    return badges


def merchandising_of(row: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    """What a card shows of a product's ADR-056 store content."""
    return {
        "subcategory": (row or {}).get("subcategory", ""),
        "tags": list((row or {}).get("tags", [])),
        "badges": badges_of(row, now),
        # 2026-11-04 marketplace filters.
        "product_type": (row or {}).get("product_type") or "saas",
        "best_for": list((row or {}).get("best_for", [])),
        "integrations": list((row or {}).get("integrations", [])),
    }


#: The marketplace's "Product type" filter: every product, or one kind.
PRODUCT_TYPES: dict[str, str] = {"all": "All Products", **catalog.PRODUCT_TYPES}
#: The longest integration name a filter may ask for, and how many at once.
_MAX_INTEGRATION_FILTER = 32
_MAX_INTEGRATION_FILTERS = 12


def _store_status(product: dict[str, Any]) -> str:
    """The same test as ``catalog.store_products`` / ``public_products``."""
    if not product["customer_store_enabled"]:
        return "not_listed"
    if product["listing"] == "listed" and product["status"] == "available":
        return "live"
    if product["listing"] == "coming_soon" and product["status"] in (
        "draft",
        "available",
    ):
        return "coming_soon"
    return "not_listed"


def _ribbon(
    product: dict[str, Any], store_status: str, now: datetime
) -> dict[str, str] | None:
    """One ribbon per card, the most important fact first."""
    if product["manager_status"] == "archived":
        return {"kind": "archived", "label": "Archived"}
    if store_status == "coming_soon":
        return {"kind": "coming_soon", "label": "Coming soon"}
    if product["manager_status"] == "draft":
        return {"kind": "draft", "label": "Draft"}
    if store_status == "not_listed":
        return {"kind": "hidden", "label": "Not in the store"}
    activated = product["activated_at"]
    if activated and now - datetime.fromisoformat(activated) <= NEW_FOR:
        return {"kind": "new", "label": "New!"}
    trial = product["trial"]
    if trial and trial["enabled"]:
        return {"kind": "trial", "label": f"{trial['duration_days']}-day free trial"}
    return None


def _card(
    product: dict[str, Any],
    plans: list[dict[str, Any]],
    media: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    store_status = _store_status(product)
    # A product not on sale has nothing to buy, whatever its plans say.
    on_sale = plans if store_status == "live" else []
    price, yearly = price_from(on_sale)
    trial = product["trial"]
    trial_days = trial["duration_days"] if trial and trial["enabled"] else None
    logo = next((m for m in media if m["kind"] == "logo"), None)
    banner = next((m for m in media if m["kind"] == "banner"), None)
    plan_types = plan_types_of(on_sale, trial=bool(trial_days))
    return {
        "id": str(product["id"]),
        "code": product["code"],
        "slug": product["slug"],
        "name": product["name"],
        "title": product["store_title"] or product["name"],
        "summary": product["store_summary"] or product["tagline"],
        "category": product["category"],
        "icon": product["icon"],
        "logo_media_id": logo["id"] if logo else None,
        "banner_media_id": banner["id"] if banner else None,
        "manager_status": product["manager_status"],
        "listing": product["listing"],
        "store_status": store_status,
        "status": store_status,
        "ribbon": _ribbon(product, store_status, now),
        "price": price,
        "yearly_deal": yearly,
        "trial_days": trial_days,
        "plans_on_sale": len(on_sale),
        "plan_types": plan_types,
        "programs": [key for key, flag in CHANNEL_FLAGS.items() if product[flag]],
        **merchandising_of(product, now),
        "sort_order": product["sort_order"],
        "created_at": product["created_at"],
        "activated_at": product["activated_at"],
    }


def _sorted(cards: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    by_name = sorted(cards, key=lambda c: (c["title"].lower(), c["code"]))
    if sort == "name":
        return by_name
    if sort == "newest":
        return sorted(
            by_name,
            key=lambda c: c["activated_at"] or c["created_at"] or "",
            reverse=True,
        )
    if sort in ("price_asc", "price_desc"):
        priced = [c for c in by_name if c["price"]]
        unpriced = [c for c in by_name if not c["price"]]
        priced.sort(
            key=lambda c: c["price"]["amount_minor"], reverse=sort == "price_desc"
        )
        return priced + unpriced  # no price sorts last either way
    return sorted(by_name, key=lambda c: c["sort_order"])


Filter = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class Query:
    """What a Store page asked for, checked: every value is one the page offers."""

    text: str
    category: str
    statuses: list[str]
    plan_types: list[str]
    low: int | None
    high: int | None
    sort: str
    collection: str = "all"
    product_type: str = "all"
    best_for: tuple[str, ...] = ()
    #: Lower-cased integration names (matched ignoring case).
    integrations: tuple[str, ...] = ()


def _integrations(raw: list[str]) -> tuple[str, ...]:
    """Integration names to filter by: free text (the facet lists those the
    products name), so only their length and number are checked."""
    values = [v.strip() for item in raw for v in str(item).split(",") if v.strip()]
    if len(values) > _MAX_INTEGRATION_FILTERS or any(
        len(v) > _MAX_INTEGRATION_FILTER for v in values
    ):
        raise CatalogValidationError(
            f"integration is at most {_MAX_INTEGRATION_FILTERS} app names of "
            f"at most {_MAX_INTEGRATION_FILTER} characters.",
            field="integration",
        )
    return tuple(dict.fromkeys(v.lower() for v in values))


def parse_query(
    *,
    q: Any,
    category: Any,
    status: list[str] | None,
    plan_type: list[str] | None,
    price_min: Any,
    price_max: Any,
    sort: Any,
    currency: str | None,
    statuses: dict[str, str],
    collection: Any = "",
    product_type: Any = "",
    best_for: list[str] | None = None,
    integration: list[str] | None = None,
) -> Query:
    """The filters of any Store page (the Super Admin's, a customer's, a
    partner's), refused with a 400 when one is not on offer."""
    text = " ".join(str(q or "").split()).lower()
    if len(text) > MAX_QUERY:
        raise CatalogValidationError(
            f"Search is limited to {MAX_QUERY} characters.", field="q"
        )
    low = _bound(price_min, currency, field="price_min")
    high = _bound(price_max, currency, field="price_max")
    if low is not None and high is not None and low > high:
        raise CatalogValidationError(
            "The lowest price must not be above the highest.", field="price_min"
        )
    return Query(
        text=text,
        category=str(category or "").strip(),
        statuses=_choices(status or [], statuses, field="status"),
        plan_types=_choices(plan_type or [], PLAN_TYPES, field="plan_type"),
        low=low,
        high=high,
        sort=_choice(sort, SORTS, field="sort", default="recommended"),
        collection=_choice(collection, COLLECTIONS, field="collection", default="all"),
        product_type=_choice(
            product_type, PRODUCT_TYPES, field="product_type", default="all"
        ),
        best_for=tuple(_choices(best_for or [], catalog.BEST_FOR, field="best_for")),
        integrations=_integrations(integration or []),
    )


def browse(
    cards: list[dict[str, Any]],
    query: Query,
    *,
    statuses: dict[str, str],
    currency: str | None,
    currencies: list[str],
    extra: dict[str, Filter] | None = None,
) -> tuple[dict[str, Any], Callable[..., list[dict[str, Any]]]]:
    """One Store page over ``cards`` - each with ``title``, ``name``, ``code``,
    ``slug``, ``summary``, ``category``, ``status``, ``price``,
    ``plan_types``, ``sort_order`` and dates: those matching every filter, in
    order, and for each filter its options with how many products each would
    show given the *other* filters (so a count never promises an empty page).

    Also returns ``matching(skip=...)`` for a page's own extra facets."""
    text, low, high = query.text, query.low, query.high

    def priced_within(card: dict[str, Any]) -> bool:
        if low is None and high is None:
            return True
        if not card["price"]:
            return False
        amount = card["price"]["amount_minor"]
        return (low is None or amount >= low) and (high is None or amount <= high)

    filters: dict[str, Filter] = {
        "q": lambda c: not text
        or any(
            text in str(v).lower()
            for v in (
                c["title"],
                c["name"],
                c["code"],
                c["slug"],
                c["summary"],
                c["category"],
                c.get("subcategory", ""),
                " ".join(c.get("tags", [])),
            )
        ),
        "collection": lambda c: query.collection == "all"
        or query.collection in c.get("badges", []),
        "category": lambda c: not query.category or c["category"] == query.category,
        "status": lambda c: not query.statuses or c["status"] in query.statuses,
        "plan_type": lambda c: not query.plan_types
        or any(t in c["plan_types"] for t in query.plan_types),
        "price": priced_within,
        "product_type": lambda c: query.product_type == "all"
        or c.get("product_type", "saas") == query.product_type,
        "best_for": lambda c: not query.best_for
        or any(b in c.get("best_for", []) for b in query.best_for),
        "integration": lambda c: not query.integrations
        or any(i.lower() in query.integrations for i in c.get("integrations", [])),
        **(extra or {}),
    }

    def matching(*, skip: str | None = None) -> list[dict[str, Any]]:
        return [c for c in cards if all(f(c) for k, f in filters.items() if k != skip)]

    shown = _sorted(matching(), query.sort)
    by_category = matching(skip="category")
    by_status = matching(skip="status")
    by_plan_type = matching(skip="plan_type")
    by_collection = matching(skip="collection")
    by_type = matching(skip="product_type")
    by_best_for = matching(skip="best_for")
    by_integration = matching(skip="integration")
    # Integration names as the products spell them, one per name ignoring case.
    integration_names: dict[str, str] = {}
    for card in cards:
        for name in card.get("integrations", []):
            integration_names.setdefault(name.lower(), name)
    categories = sorted({c["category"] for c in cards if c["category"]})
    amounts = [c["price"]["amount_minor"] for c in cards if c["price"]]
    page = {
        "currency": currency,
        "currencies": currencies,
        "count": len(shown),
        "total": len(cards),
        "products": shown,
        "facets": {
            "categories": [
                {
                    "value": name,
                    "label": name,
                    "count": sum(1 for c in by_category if c["category"] == name),
                }
                for name in categories
            ],
            "statuses": [
                {
                    "value": key,
                    "label": label,
                    "count": sum(1 for c in by_status if c["status"] == key),
                }
                for key, label in statuses.items()
            ],
            "plan_types": [
                {
                    "value": key,
                    "label": label,
                    "count": sum(1 for c in by_plan_type if key in c["plan_types"]),
                }
                for key, label in PLAN_TYPES.items()
            ],
            "collections": [
                {
                    "value": key,
                    "label": label,
                    "count": len(by_collection)
                    if key == "all"
                    else sum(1 for c in by_collection if key in c.get("badges", [])),
                }
                for key, label in COLLECTIONS.items()
            ],
            "product_types": [
                {
                    "value": key,
                    "label": label,
                    "count": len(by_type)
                    if key == "all"
                    else sum(1 for c in by_type if c.get("product_type", "saas") == key),
                }
                for key, label in PRODUCT_TYPES.items()
            ],
            "best_for": [
                {
                    "value": key,
                    "label": label,
                    "count": sum(1 for c in by_best_for if key in c.get("best_for", [])),
                }
                for key, label in catalog.BEST_FOR.items()
            ],
            "integrations": [
                {
                    "value": name,
                    "label": name,
                    "count": sum(
                        1
                        for c in by_integration
                        if key in {i.lower() for i in c.get("integrations", [])}
                    ),
                }
                for key, name in sorted(
                    integration_names.items(), key=lambda kv: kv[1].lower()
                )
            ],
            "price": {
                "min_minor": min(amounts) if amounts else None,
                "max_minor": max(amounts) if amounts else None,
            },
        },
        "sorts": [{"value": k, "label": v} for k, v in SORTS.items()],
        "applied": {
            "q": text,
            "category": query.category,
            "status": query.statuses,
            "plan_type": query.plan_types,
            "sort": query.sort,
            "collection": query.collection,
            "product_type": query.product_type,
            "best_for": list(query.best_for),
            "integration": list(query.integrations),
        },
    }
    return page, matching


@in_scope
def store(
    ctx: TenantContext,
    *,
    q: Any = "",
    category: Any = "",
    program: Any = "",
    status: list[str] | None = None,
    plan_type: list[str] | None = None,
    price_min: Any = "",
    price_max: Any = "",
    currency: Any = "",
    sort: Any = "",
    collection: Any = "",
    product_type: Any = "",
    best_for: list[str] | None = None,
    integration: list[str] | None = None,
) -> dict[str, Any]:
    """The Super Admin Store page: every product, filtered by ``browse`` and
    by the channel ("program") it is open to."""
    authz.require(ctx, VIEW)
    chosen_program = _choice(program, PROGRAMS, field="program", default="all")
    available = pricing.direct_currencies()
    chosen_currency = _currency(currency, available)
    query = parse_query(
        q=q,
        category=category,
        status=status,
        plan_type=plan_type,
        price_min=price_min,
        price_max=price_max,
        sort=sort,
        currency=chosen_currency,
        statuses=STATUSES,
        collection=collection,
        product_type=product_type,
        best_for=best_for,
        integration=integration,
    )

    now = timezone.now()
    products = catalog.visible_products()
    plans = _on_sale_plans(chosen_currency, now)
    media = catalog.media_rows([p["id"] for p in products])
    cards = [
        _card(p, plans.get(p["code"], []), media[str(p["id"])], now) for p in products
    ]
    page, matching = browse(
        cards,
        query,
        statuses=STATUSES,
        currency=chosen_currency,
        currencies=available,
        extra={
            "program": lambda c: chosen_program == "all"
            or chosen_program in c["programs"]
        },
    )
    by_program = matching(skip="program")
    page["facets"]["programs"] = [
        {
            "value": key,
            "label": label,
            "count": len(by_program)
            if key == "all"
            else sum(1 for c in by_program if key in c["programs"]),
        }
        for key, label in PROGRAMS.items()
    ]
    page["applied"]["program"] = chosen_program
    return page

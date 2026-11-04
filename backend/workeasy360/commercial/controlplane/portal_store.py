"""The Store in every portal (2026-10-31): the Super Admin Store's marketplace
page - categories, filters, facet counts, sorting, product cards - for a
customer organization, an affiliate, a reseller and a white-label partner.

Each portal's Store is the page it already had, laid out as a marketplace,
never a wider one:

- a **customer** sees exactly its Explore listing (``marketplace.explore``
  after its partner's choices and presentation), priced in its own billing
  currency, each card with what this organization holds of it;
- a **partner** sees exactly its Store Console's Explore list
  (``partner_workspaces.store_console``): the Store products open to its
  channel - affiliate to promote, reseller or white-label to sell - each with
  its request status, and what a sale earns only for a member allowed to see it.

The filtering, counting and ordering is ``store.browse``, the Super Admin's
own, so the four pages behave alike. Read only: nothing here grants a product,
starts a trial or sends a request; those stay the existing, re-checked calls.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.controlplane import partner_workspaces, store
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

#: Where a product stands for a customer organization.
CUSTOMER_STATUSES: dict[str, str] = {
    "available": "Available",
    "trial": "On a free trial",
    "owned": "Your products",
    "coming_soon": "Coming soon",
}

#: Where a product stands for a partner: its request (ADR-055).
PARTNER_STATUSES: dict[str, str] = {
    "approved": "Approved",
    "pending": "Pending approval",
    "not_requested": "Not requested",
    "rejected": "Rejected",
    "revoked": "Revoked",
}


def _is_new(row: dict[str, Any] | None, now: datetime) -> bool:
    activated = row["activated_at"] if row else None
    if not activated:
        return False
    return now - datetime.fromisoformat(str(activated)) <= store.NEW_FOR


def _listing_rows(codes: list[str]) -> dict[str, dict[str, Any]]:
    """The listing content (store title and summary, dates) of these products."""
    wanted = set(codes)
    return {p["code"]: p for p in catalog.visible_products() if p["code"] in wanted}


def _params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: params.get(key)
        for key in (
            "q",
            "category",
            "status",
            "plan_type",
            "price_min",
            "price_max",
            "sort",
            "collection",
        )
    }


# --- customer ------------------------------------------------------------------------------


def _customer_status(product: dict[str, Any]) -> str:
    if product["trial_state"] == "owned":
        return "owned"
    if product["trial_state"] == "active":
        return "trial"
    if product["availability"] == "coming_soon":
        return "coming_soon"
    return "available"


def _customer_ribbon(
    product: dict[str, Any], status: str, row: dict[str, Any] | None, now: datetime
) -> dict[str, str] | None:
    """One ribbon per card, from the server's facts about this organization."""
    if status == "owned":
        return {"kind": "owned", "label": "Active"}
    if status == "trial":
        left = product.get("trial_days_left")
        label = (
            "Trial active"
            if left is None
            else f"Trial: {left} day{'' if left == 1 else 's'} left"
        )
        return {"kind": "trial_active", "label": label}
    if status == "coming_soon":
        return {"kind": "coming_soon", "label": "Coming soon"}
    if product["trial_state"] == "used" and product.get("trial_outcome") == "expired":
        return {"kind": "expired", "label": "Trial expired"}
    if _is_new(row, now):
        return {"kind": "new", "label": "New!"}
    trial = product["trial"]
    if trial["available"] and trial["duration_days"]:
        return {"kind": "trial", "label": f"{trial['duration_days']}-day free trial"}
    return None


def customer_store(
    ctx: TenantContext, listing: dict[str, Any], **params: Any
) -> dict[str, Any]:
    """An organization's Store: its Explore ``listing`` - already narrowed and
    ordered by its partner - as a marketplace page. The listing's order is the
    "Recommended" order, and its currency (the organization's billing
    currency, or none before billing details exist) the only one."""
    authz.require(ctx, "platform.organization.view")
    cards = _cards(
        listing["products"], status_of=_customer_status, ribbon_of=_customer_ribbon
    )
    currency = listing["currency"]
    query = store.parse_query(
        **_params(params), currency=currency, statuses=CUSTOMER_STATUSES
    )
    page, _matching = store.browse(
        cards,
        query,
        statuses=CUSTOMER_STATUSES,
        currency=currency,
        currencies=[currency] if currency else [],
    )
    page["billing_account"] = listing["billing_account"]
    return page


def _cards(
    products: list[dict[str, Any]],
    *,
    status_of: Callable[[dict[str, Any]], str],
    ribbon_of: Callable[..., dict[str, str] | None],
) -> list[dict[str, Any]]:
    """The Store cards of a customer-facing listing (an organization's Explore
    or the public catalog), in the listing's own order."""
    now = timezone.now()
    rows = _listing_rows([p["code"] for p in products])
    on_sale = [r["id"] for r in rows.values()]
    media = catalog.media_rows(on_sale) if on_sale else {}

    cards = []
    for position, product in enumerate(products):
        row = rows.get(product["code"])
        status = status_of(product)
        price, yearly = store.price_from(product["plans"])
        trial = product["trial"]
        offered = bool(trial["available"] and trial["duration_days"])
        # Listing images are public only while the product is on sale.
        images = (
            media.get(str(row["id"]), [])
            if row and product["availability"] == "available"
            else []
        )
        logo = next((m for m in images if m["kind"] == "logo"), None)
        banner = next((m for m in images if m["kind"] == "banner"), None)
        cards.append(
            {
                **product,
                "title": (row["store_title"] if row else "") or product["name"],
                "summary": (row["store_summary"] if row else "") or product["tagline"],
                "status": status,
                "ribbon": ribbon_of(product, status, row, now),
                "price": price,
                "yearly_deal": yearly,
                "plan_types": store.plan_types_of(product["plans"], trial=offered),
                "trial_days": trial["duration_days"] if offered else None,
                "logo_url": catalog.media_url(product["slug"], logo["id"])
                if logo
                else None,
                "banner_url": (
                    catalog.media_url(product["slug"], banner["id"]) if banner else None
                ),
                **store.merchandising_of(row, now),
                "sort_order": position,
                "created_at": row["created_at"] if row else None,
                "activated_at": row["activated_at"] if row else None,
            }
        )
    return cards


# --- the public Store -------------------------------------------------------------------

#: Where a product stands for a visitor: on sale, or announced.
PUBLIC_STATUSES: dict[str, str] = {
    "available": "Available now",
    "coming_soon": "Coming soon",
}


def _public_status(product: dict[str, Any]) -> str:
    return "coming_soon" if product["availability"] == "coming_soon" else "available"


def _public_ribbon(
    product: dict[str, Any], status: str, row: dict[str, Any] | None, now: datetime
) -> dict[str, str] | None:
    if status == "coming_soon":
        return {"kind": "coming_soon", "label": "Coming soon"}
    if _is_new(row, now):
        return {"kind": "new", "label": "New!"}
    trial = product["trial"]
    if trial["available"] and trial["duration_days"]:
        return {"kind": "trial", "label": f"{trial['duration_days']}-day free trial"}
    return None


def public_product(slug: Any, *, currency: Any = None) -> dict[str, Any] | None:
    """One product of the public Store (``/store/product/<slug>``): its full
    listing - overview, screenshots, key features, benefits, use cases - the
    plan comparison and related products, from the public catalog. Anonymous
    and read only; ``None`` for anything not in the public catalog."""
    from workeasy360.commercial.subscriptions.marketplace import public_catalog

    listing = public_catalog(currency=currency)
    wanted = str(slug or "")
    product = next((p for p in listing["products"] if p["slug"] == wanted), None)
    if product is None:
        return None
    page = _product_page(
        listing["products"],
        product["code"],
        status_of=_public_status,
        held={"held_plan_code": None, "renews_at": None},
    )
    if page is None:
        return None
    return {"currency": listing["currency"], "currencies": listing["currencies"], **page}


def public_store(*, currency: Any = None, **params: Any) -> dict[str, Any]:
    """The public Store (``/store``, 2026-11-02): every product a visitor may
    see - exactly the public catalog (listed and on the customer Store, or
    announced as coming soon) - as a marketplace page, in any currency the
    catalog has direct prices in. Anonymous and read only: nothing about any
    visitor, organization or partner; buying signs in and goes through the
    organization's own checkout, which prices the plan again."""
    from workeasy360.commercial.subscriptions.marketplace import public_catalog

    listing = public_catalog(currency=currency)
    cards = _cards(
        listing["products"], status_of=_public_status, ribbon_of=_public_ribbon
    )
    chosen = listing["currency"]
    query = store.parse_query(
        **_params(params), currency=chosen, statuses=PUBLIC_STATUSES
    )
    page, _matching = store.browse(
        cards,
        query,
        statuses=PUBLIC_STATUSES,
        currency=chosen,
        currencies=listing["currencies"],
    )
    return page


def _comparison(
    plans: list[dict[str, Any]], features: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The plan comparison's rows (ADR-056): each public feature with what
    every plan grants, plus seats - from the plan versions in force."""
    from uuid import UUID

    versions = catalog.plan_versions([UUID(p["plan_version_id"]) for p in plans])
    granted = {
        str(vid): {g.key: g.value for g in dto.features} for vid, dto in versions.items()
    }
    rows: list[dict[str, Any]] = [
        {
            "key": "seats",
            "label": "Users included",
            "kind": "seats",
            "unit": "",
            "values": {
                p["plan_version_id"]: {
                    "included": p["included_seats"],
                    "max": p["max_seats"],
                }
                for p in plans
            },
        }
    ]
    for feature in features:
        if not feature["is_public"]:
            continue
        values = {
            p["plan_version_id"]: granted.get(p["plan_version_id"], {}).get(
                feature["key"]
            )
            for p in plans
        }
        if all(v in (None, False) for v in values.values()):
            continue
        rows.append(
            {
                "key": feature["key"],
                "label": feature["name"],
                "description": feature.get("description", ""),
                "kind": feature["kind"],
                "unit": feature["unit"],
                "values": values,
            }
        )
    return rows


def customer_product(
    ctx: TenantContext, listing: dict[str, Any], *, product_code: str
) -> dict[str, Any] | None:
    """One product of an organization's Store (ADR-056): its listing content
    and screenshots, a plan comparison (price per cycle, seats, features,
    trial, the recommended plan), what this organization holds of it - so the
    page offers only what the server would accept - and related products.
    ``None`` when the product is not in this organization's Store."""
    authz.require(ctx, "platform.organization.view")
    holding = my_holding(product_code)
    page = _product_page(
        listing["products"],
        product_code,
        status_of=_customer_status,
        held={
            "held_plan_code": holding.plan_code if holding is not None else None,
            "renews_at": holding.renews_at.isoformat()
            if holding is not None and holding.renews_at
            else None,
        },
    )
    if page is None:
        return None
    return {
        "currency": listing["currency"],
        "billing_account": listing["billing_account"],
        **page,
    }


def _video_of(slug: str, row: dict[str, Any] | None) -> dict[str, Any] | None:
    """A product page's video: its uploaded file when it has one, else its
    YouTube or Vimeo link, else nothing."""
    if row is None:
        return None
    uploaded = catalog.product_videos([row["id"]]).get(str(row["id"]))
    if uploaded is not None:
        return {
            "provider": "upload",
            "id": uploaded["id"],
            "url": catalog.video_url(slug, uploaded),
            "content_type": uploaded["content_type"],
        }
    return catalog.video_embed(row.get("video_url", ""))


def _product_page(
    products: list[dict[str, Any]],
    product_code: str,
    *,
    status_of: Callable[[dict[str, Any]], str],
    held: dict[str, Any],
) -> dict[str, Any] | None:
    """One product's Store page from a customer-facing listing: content,
    screenshots, plan comparison and related products, plus ``held`` (what an
    organization holds of it, or nothing for a visitor)."""
    now = timezone.now()
    product = next((p for p in products if p["code"] == product_code), None)
    if product is None:
        return None
    rows = _listing_rows([p["code"] for p in products])
    row = rows.get(product_code)
    on_sale = product["availability"] == "available" and row is not None
    media = catalog.media_rows([row["id"]]).get(str(row["id"]), []) if on_sale else []

    def image(m: dict[str, Any]) -> dict[str, Any]:
        return {
            "url": catalog.media_url(product["slug"], m["id"]),
            "caption": m["caption"],
            "width": m["width"],
            "height": m["height"],
        }

    plans = sorted(
        product["plans"],
        key=lambda p: (p.get("kind") != "standard", p.get("sort_order", 100), p["name"]),
    )
    features = catalog.features_for_product(row["id"]) if row is not None else []
    price, yearly = store.price_from(plans)
    related = [
        {
            "code": p["code"],
            "slug": p["slug"],
            "name": p["name"],
            "title": (rows.get(p["code"], {}).get("store_title") or p["name"]),
            "summary": (rows.get(p["code"], {}).get("store_summary") or p["tagline"]),
            "icon": p.get("icon", ""),
            "category": p.get("category", ""),
            "price": store.price_from(p["plans"])[0],
        }
        for p in products
        if p["code"] != product_code
        and p["availability"] == "available"
        and (not product.get("category") or p.get("category") == product.get("category"))
    ][:4]
    return {
        "product": {
            **{
                k: product[k]
                for k in (
                    "code",
                    "slug",
                    "name",
                    "tagline",
                    "availability",
                    "trial",
                    "trial_state",
                    "holding",
                    "trial_ends_at",
                    "trial_days_left",
                    "trial_outcome",
                )
                if k in product
            },
            "category": product.get("category", ""),
            "icon": product.get("icon", ""),
            "title": (row["store_title"] if row else "") or product["name"],
            "summary": (row["store_summary"] if row else "") or product["tagline"],
            "description": (row["description"] if row else "") or "",
            "highlights": (row or {}).get("highlights", []),
            "benefits": (row or {}).get("benefits", []),
            "use_cases": (row or {}).get("use_cases", []),
            "video": _video_of(product["slug"], row if on_sale else None),
            "documentation_url": (row or {}).get("documentation_url", ""),
            "support_url": (row or {}).get("support_url", ""),
            **store.merchandising_of(row, now),
            "status": status_of(product),
            "logo": next((image(m) for m in media if m["kind"] == "logo"), None),
            "banner": next((image(m) for m in media if m["kind"] == "banner"), None),
            "screenshots": [image(m) for m in media if m["kind"] == "screenshot"],
            "price": price,
            "yearly_deal": yearly,
            **held,
        },
        "plans": plans,
        "comparison": _comparison(plans, features) if plans else [],
        "related": related,
    }


def my_holding(product_code: str) -> Any:
    """What the current tenant holds of one product, or ``None``."""
    from workeasy360.commercial.subscriptions import my_products

    return my_products.holdings().get(product_code)


# --- partner -------------------------------------------------------------------------------


@in_scope
def partner_store(ctx: TenantContext, *, kind: str, **params: Any) -> dict[str, Any]:
    """A partner's Store: its Store Console Explore list (``promote`` for an
    affiliate, ``sell`` for a reseller or white-label partner) as a
    marketplace page, priced as the public Store prices it. Who may read it,
    which products and what each discloses are ``store_console``'s answer."""
    from workeasy360.commercial.subscriptions import marketplace

    console = partner_workspaces.store_console(ctx, kind=kind)
    public = marketplace.public_catalog()
    offers = {p["code"]: p for p in public["products"]}
    rows = _listing_rows([p["code"] for p in console["products"]])
    now = timezone.now()

    cards = []
    for product in console["products"]:
        row = rows.get(product["code"])
        offer = offers.get(product["code"])
        plans = offer["plans"] if offer else []
        price, yearly = store.price_from(plans)
        trial = offer["trial"] if offer else None
        offered = bool(trial and trial["available"] and trial["duration_days"])
        ribbon = None
        if _is_new(row, now):
            ribbon = {"kind": "new", "label": "New!"}
        elif offered and trial is not None:
            ribbon = {
                "kind": "trial",
                "label": f"{trial['duration_days']}-day free trial",
            }
        cards.append(
            {
                **product,
                "title": (row["store_title"] if row else "") or product["name"],
                "summary": product["summary"] or product["tagline"],
                "ribbon": ribbon,
                "price": price,
                "yearly_deal": yearly,
                "plan_types": store.plan_types_of(plans, trial=offered),
                "trial_days": trial["duration_days"] if offered and trial else None,
                **store.merchandising_of(row, now),
                "sort_order": row["sort_order"] if row else 0,
                "created_at": row["created_at"] if row else None,
                "activated_at": row["activated_at"] if row else None,
            }
        )

    currency = public["currency"]
    query = store.parse_query(
        **_params(params), currency=currency, statuses=PARTNER_STATUSES
    )
    page, _matching = store.browse(
        cards,
        query,
        statuses=PARTNER_STATUSES,
        currency=currency,
        currencies=[currency] if currency else [],
    )
    page.update(
        {
            key: console[key]
            for key in (
                "kind",
                "counts",
                "can_request",
                "cannot_request_reason",
                "channels",
                "prefill",
            )
        }
    )
    return page

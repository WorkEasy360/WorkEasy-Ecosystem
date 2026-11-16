"""The one-page Product Publisher (ADR-068): a product, its Store listing and its
pricing plans, saved together.

No second product model, no second plan store, no second write path. The page
saves into the catalog's own ``Product`` row and the catalog's own ``Plan`` /
``PlanVersion`` / ``PlanPrice`` rows - the very records Products & Pricing >
Plans lists and edits - through their owners' authorized, audited services:

- **save** creates the product the way the Add Product wizard did
  (``product_manager.create``: catalog row, integration when a launch URL is
  given, trial) or updates it (``catalog.update_product``, only what changed),
  then applies the page's plans: a new plan is ``create_plan`` + a version +
  its prices; an existing plan's presentation is ``update_plan``, a change of
  seats or trial publishes a **new version** (the one in force is never edited:
  a subscriber keeps the terms they bought), and a change of price publishes a
  new price row that supersedes the old one for new purchases only. One
  transaction: a refused step leaves nothing behind.
- **publish** makes every visible, priced plan of the product available and
  publishes the product - the catalog's own publish checklist decides - in one
  transaction.
- **preview** is the Store product page exactly as the Store builds it
  (``portal_store._product_page``), for a draft too, with the images served by
  the staff media endpoint - nothing is made public to preview it.

Images and the video are uploaded against the saved product afterwards through
the existing media endpoint (files need the product to exist, and the upload
pipeline checks every byte); the page stages them until then.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.controlplane import product_manager
from workeasy360.commercial.pricing import selectors as price_selectors
from workeasy360.contracts.enums import (
    BillingInterval,
    CatalogStatus,
    PlanBillingType,
    PriceModel,
)
from workeasy360.contracts.errors import CatalogValidationError, CommercialRecordNotFound
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

CATALOG_VIEW: Final = "platform.staff_catalog.view"
CATALOG_MANAGE: Final = "platform.staff_catalog.manage"
PRICING_MANAGE: Final = "platform.staff_pricing.manage"
INTEGRATION_MANAGE: Final = "platform.staff_integration.manage"

#: The billing periods a plan may be priced for here - the Store sells each of
#: them. One-time is not offered: the checkout sells subscriptions only and
#: refuses a one-time price for one (``payments.orders``).
INTERVALS: Final = ("monthly", "quarterly", "semi_annual", "annual")
#: How many plans one product page may carry - the Store shows them side by side.
MAX_PLANS: Final = 12
MAX_SEATS: Final = 100_000

#: The product fields the page edits (beside the integration and the trial).
PRODUCT_FIELDS: Final = frozenset(
    {
        "name",
        "slug",
        "category",
        "tagline",
        "description",
        "application_url",
        "marketing_url",
        "documentation_url",
        "support_url",
        "store_title",
        "store_summary",
        "video_url",
        "video_title",
        "video_description",
        "highlights",
        "customer_store_enabled",
        "reseller_enabled",
        "affiliate_enabled",
        "white_label_enabled",
    }
)
#: What ``product_manager.create`` takes; the rest is applied right after.
_CREATE_FIELDS: Final = frozenset(
    {
        "name",
        "slug",
        "category",
        "tagline",
        "description",
        "marketing_url",
        *catalog_services.LISTING_DETAIL_FIELDS,
    }
)
_PLAN_FIELDS: Final = frozenset(
    {
        "id",
        "name",
        "caption",
        "description",
        "free",
        "price_book_id",
        "prices",
        "per_user",
        "setup_fee_minor",
        "discount_bp",
        "included_seats",
        "max_seats",
        "trial_days",
        "visible",
        "recommended",
    }
)
_BODY_FIELDS: Final = frozenset({"product", "integration", "trial", "plans"})


def _refuse_unknown(body: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise CatalogValidationError(
            f"These {where} fields are not accepted: {', '.join(unknown)}.",
            field=unknown[0],
        )


def _int(value: Any, *, field: str, low: int, high: int, allow_none: bool = False) -> Any:
    if value is None and allow_none:
        return None
    # ``bool`` is an ``int`` in Python; a checkbox is never a number here.
    if isinstance(value, bool) or not isinstance(value, int):
        raise CatalogValidationError(f"{field} is a whole number.", field=field)
    if not low <= value <= high:
        raise CatalogValidationError(
            f"{field} is between {low:,} and {high:,}.", field=field
        )
    return value


def _bool(value: Any, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CatalogValidationError(f"{field} is true or false.", field=field)
    return value


@dataclass(frozen=True)
class PlanInput:
    """One plan card of the page, checked."""

    id: UUID | None
    name: str
    caption: str
    description: str
    free: bool
    price_book_id: UUID | None
    #: interval -> amount in minor units (0 for a free plan).
    prices: dict[str, int]
    per_user: bool
    setup_fee_minor: int
    discount_bp: int
    included_seats: int
    max_seats: int | None
    trial_days: int
    visible: bool
    recommended: bool


def _plan_input(raw: Any, index: int) -> PlanInput:
    where = f"plans.{index}"
    if not isinstance(raw, dict):
        raise CatalogValidationError("Each plan is an object.", field=where)
    _refuse_unknown(raw, _PLAN_FIELDS, "plan")

    def field(name: str) -> str:
        return f"{where}.{name}"

    plan_id = raw.get("id")
    if plan_id is not None:
        try:
            plan_id = UUID(str(plan_id))
        except ValueError:
            raise CatalogValidationError("No such plan.", field=field("id")) from None
    name = " ".join(str(raw.get("name") or "").split())
    if not name or len(name) > catalog_services.MAX_NAME:
        raise CatalogValidationError(
            "Give the plan a name (up to 128 characters).", field=field("name")
        )
    caption = str(raw.get("caption") or "").strip()
    if len(caption) > catalog_services.MAX_CAPTION or "\n" in caption:
        raise CatalogValidationError(
            "The short plan description is one line of up to 160 characters.",
            field=field("caption"),
        )
    description = str(raw.get("description") or "").replace("\r\n", "\n").strip()
    if len(description) > catalog_services.MAX_DESCRIPTION or "\x00" in description:
        raise CatalogValidationError(
            "The plan's highlights may be at most 5,000 characters.",
            field=field("description"),
        )
    free = _bool(raw.get("free"), field=field("free"), default=False)
    raw_prices = raw.get("prices") or {}
    if not isinstance(raw_prices, dict):
        raise CatalogValidationError(
            "prices is a map of billing period to amount.", field=field("prices")
        )
    prices: dict[str, int] = {}
    for interval, amount in raw_prices.items():
        if interval not in INTERVALS:
            raise CatalogValidationError(
                f"{interval!r} is not a billing period the Store sells.",
                field=field("prices"),
            )
        if amount is None:
            continue
        prices[interval] = _int(
            amount,
            field=field(f"prices.{interval}"),
            low=0,
            high=10**12,
        )
    if free:
        # A free plan is offered at nothing for one period: the Store shows it
        # as "Free" and its checkout enrols with no payment.
        period = next(iter(prices), "monthly")
        prices = {period: 0}
    elif any(amount == 0 for amount in prices.values()):
        raise CatalogValidationError(
            "A paid plan's price is more than zero - mark the plan Free instead.",
            field=field("prices"),
        )
    book = raw.get("price_book_id")
    book_id: UUID | None = None
    if book not in (None, ""):
        try:
            book_id = UUID(str(book))
        except ValueError:
            raise CatalogValidationError(
                "Choose the currency (price book).", field=field("price_book_id")
            ) from None
    if prices and book_id is None:
        raise CatalogValidationError(
            "Choose the currency (price book) the plan is priced in.",
            field=field("price_book_id"),
        )
    included = _int(
        raw.get("included_seats", 1),
        field=field("included_seats"),
        low=1,
        high=MAX_SEATS,
    )
    max_seats = _int(
        raw.get("max_seats"),
        field=field("max_seats"),
        low=1,
        high=MAX_SEATS,
        allow_none=True,
    )
    if max_seats is not None and max_seats < included:
        raise CatalogValidationError(
            "The seat limit is at least the seats included.", field=field("max_seats")
        )
    return PlanInput(
        id=plan_id,
        name=name,
        caption=caption,
        description=description,
        free=free,
        price_book_id=book_id,
        prices=prices,
        per_user=_bool(raw.get("per_user"), field=field("per_user"), default=False),
        setup_fee_minor=_int(
            raw.get("setup_fee_minor") or 0,
            field=field("setup_fee_minor"),
            low=0,
            high=10**12,
        ),
        discount_bp=_int(
            raw.get("discount_bp") or 0,
            field=field("discount_bp"),
            low=0,
            high=9_900,
        ),
        included_seats=included,
        max_seats=max_seats,
        trial_days=_int(
            raw.get("trial_days") or 0, field=field("trial_days"), low=0, high=365
        ),
        visible=_bool(raw.get("visible"), field=field("visible"), default=True),
        recommended=_bool(
            raw.get("recommended"), field=field("recommended"), default=False
        ),
    )


def _plan_inputs(raw: Any) -> list[PlanInput]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CatalogValidationError("plans is a list.", field="plans")
    if len(raw) > MAX_PLANS:
        raise CatalogValidationError(
            f"A product page carries at most {MAX_PLANS} plans.", field="plans"
        )
    plans = [_plan_input(item, i) for i, item in enumerate(raw)]
    if sum(1 for p in plans if p.recommended) > 1:
        raise CatalogValidationError(
            "Only one plan can be marked Recommended.", field="plans"
        )
    ids = [p.id for p in plans if p.id is not None]
    if len(ids) != len(set(ids)):
        raise CatalogValidationError("A plan appears twice.", field="plans")
    return plans


# --- reading ---------------------------------------------------------------------


def _books() -> list[dict[str, Any]]:
    """The direct price books a plan can be priced in, as the plan board lists them."""
    return [
        {
            "id": b["id"],
            "code": b["code"],
            "name": b["name"],
            "currency": b["currency"],
            "region": b["region"],
        }
        for b in price_selectors.price_books()
        if b["channel"] == "direct" and b["is_active"]
    ]


def _current_prices(version_id: str | None) -> list[dict[str, Any]]:
    if not version_id:
        return []
    return [
        p
        for p in price_selectors.price_schedule(plan_version_ids=[UUID(version_id)])
        if p["state"] == "current"
    ]


def _latest(plan: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The plan's version in force (latest published) and a newer draft, if any."""
    versions = plan["versions"]  # newest first
    published = next((v for v in versions if v["published_at"]), None)
    draft = next((v for v in versions if not v["published_at"]), None)
    if draft and published and draft["version"] < published["version"]:
        draft = None
    return published, draft


def _plan_rows(product_code: str) -> list[dict[str, Any]]:
    """The product's own standard plans (not retired), in Store order, as the
    page edits them. Add-ons stay with Products & Pricing > Plans."""
    out = []
    plans = [
        p
        for p in catalog.plan_listing(product_code=product_code)
        if p["kind"] == "standard" and p["status"] != CatalogStatus.RETIRED.value
    ]
    plans.sort(key=lambda p: (p["sort_order"], p["name"]))
    for plan in plans:
        published, draft = _latest(plan)
        terms = published or draft
        prices = _current_prices(terms["id"] if terms else None)
        book = prices[0]["price_book_id"] if prices else None
        priced = {
            p["interval"]: p["amount_minor"]
            for p in prices
            if p["price_book_id"] == book and p["amount_minor"] is not None
        }
        first = next((p for p in prices if p["price_book_id"] == book), None)
        out.append(
            {
                "id": plan["id"],
                "code": plan["code"],
                "name": plan["name"],
                "caption": plan["caption"],
                "description": plan["description"],
                "status": plan["status"],
                "visible": plan["is_public"],
                "recommended": plan["recommended"],
                "billing_type": plan["billing_type"],
                "free": bool(priced) and all(a == 0 for a in priced.values()),
                "price_book_id": book,
                "currency": first["currency"] if first else None,
                "prices": priced,
                "per_user": bool(first and first["model"] == PriceModel.PER_SEAT.value),
                "setup_fee_minor": (first or {}).get("setup_fee_minor") or 0,
                "discount_bp": (first or {}).get("discount_bp") or 0,
                "included_seats": terms["included_seats"] if terms else 1,
                "max_seats": terms["max_seats"] if terms else None,
                "trial_days": terms["trial_days"] if terms else 0,
                "version": terms["version"] if terms else None,
                "published": published is not None,
                "features": dict(terms["features"]) if terms else {},
            }
        )
    return out


def _row_for(product_id: UUID) -> dict[str, Any] | None:
    """The catalog screens' row of one product (drafts included), or ``None``."""
    wanted = str(product_id)
    return next((p for p in catalog.visible_products() if str(p["id"]) == wanted), None)


def _can(ctx: TenantContext, permission: str) -> bool:
    return authz.check(ctx, permission).allowed and authz.is_platform_staff(
        ctx, permission
    )


@in_scope
def bootstrap(ctx: TenantContext) -> dict[str, Any]:
    """What an empty Add Product page needs: categories, the price books a plan
    can be priced in, the billing periods, and what this person may do."""
    authz.require(ctx, CATALOG_VIEW)
    from workeasy360.commercial.catalog.domain import PRODUCT_CATEGORIES

    return {
        "categories": list(PRODUCT_CATEGORIES),
        "books": _books(),
        "intervals": list(INTERVALS),
        "max_plans": MAX_PLANS,
        "can_manage": {
            "catalog": _can(ctx, CATALOG_MANAGE),
            "pricing": _can(ctx, PRICING_MANAGE),
            "integration": _can(ctx, INTEGRATION_MANAGE),
        },
    }


@in_scope
def state(ctx: TenantContext, product_id: UUID) -> dict[str, Any]:
    """One product as the page edits it: the product manager's detail (fields,
    images, publish checklist) plus its plans and the price books."""
    detail = product_manager.detail(ctx, product_id)
    code = detail["product"]["code"]
    row = next((p for p in catalog.visible_products() if p["code"] == code), None) or {}
    video = catalog.product_videos([product_id]).get(str(product_id))
    add_ons = [
        p
        for p in catalog.plan_listing()
        if p["kind"] == "add_on"
        and (p["addon_for"] or {}).get("code") == code
        and p["status"] != CatalogStatus.RETIRED.value
    ]
    return {
        **bootstrap(ctx),
        **detail,
        "listing": {
            key: row.get(key, default)
            for key, default in (
                ("description", ""),
                ("video_url", ""),
                ("video_title", ""),
                ("video_description", ""),
                ("highlights", []),
                ("store_title", ""),
                ("store_summary", ""),
                ("application_url", ""),
                ("documentation_url", ""),
                ("support_url", ""),
                ("marketing_url", ""),
            )
        },
        "video": (
            {
                "id": video["id"],
                "content_type": video["content_type"],
                "size_bytes": video["size_bytes"],
            }
            if video
            else None
        ),
        "plans": _plan_rows(code),
        "add_on_count": len(add_ons),
    }


# --- saving ----------------------------------------------------------------------


def _slugify(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower())).strip("_")


def _new_plan_code(product_code: str, name: str, taken: set[str]) -> str:
    """A plan code nobody holds: ``<product>_<plan name>``, numbered if needed."""
    base = f"{product_code}_{_slugify(name) or 'plan'}"[:56].rstrip("_")
    code, n = base, 2
    while code in taken:
        code = f"{base}_{n}"
        n += 1
    taken.add(code)
    return code


def _publish_prices(
    ctx: TenantContext,
    *,
    version_id: UUID,
    plan: PlanInput,
    current: dict[str, dict[str, Any]],
) -> None:
    """Make the version's prices what the card says: a new row for each period
    whose terms differ (it supersedes the running one for new purchases only),
    the running row withdrawn for a period the card no longer offers."""
    from workeasy360.commercial.pricing import services as pricing

    model = PriceModel.PER_SEAT if plan.per_user else PriceModel.FLAT
    for interval, amount in plan.prices.items():
        running = current.get(interval)
        discount = 0 if plan.free else plan.discount_bp
        setup = 0 if plan.free else plan.setup_fee_minor
        if (
            running is not None
            and running["price_book_id"] == str(plan.price_book_id)
            and running["amount_minor"] == amount
            and running["model"] == model.value
            and (running.get("discount_bp") or 0) == discount
            and (running.get("setup_fee_minor") or 0) == setup
        ):
            continue
        pricing.publish_price(
            ctx,
            price_book_id=plan.price_book_id,
            interval=BillingInterval(interval),
            plan_version_id=version_id,
            amount_minor=amount,
            price_model=model,
            discount_bp=discount,
            setup_fee_minor=setup,
        )
    for interval, running in current.items():
        if interval not in plan.prices or running["price_book_id"] != str(
            plan.price_book_id
        ):
            pricing.close_price(ctx, price_id=UUID(running["price_id"]))


def _apply_plan(
    ctx: TenantContext,
    *,
    product_id: UUID,
    product_code: str,
    plan: PlanInput,
    position: int,
    existing: dict[str, dict[str, Any]],
    taken: set[str],
) -> UUID:
    sort_order = (position + 1) * 10
    if plan.id is None:
        row = catalog_services.create_plan(
            ctx,
            product_id=product_id,
            code=_new_plan_code(product_code, plan.name, taken),
            name=plan.name,
            caption=plan.caption,
            description=plan.description,
            is_public=plan.visible,
            sort_order=sort_order,
            billing_type=PlanBillingType.PAID,
        )
        version = catalog_services.draft_plan_version(
            ctx,
            plan_id=row.id,
            features={},
            included_seats=plan.included_seats,
            max_seats=plan.max_seats,
            trial_days=plan.trial_days,
        )
        _publish_prices(ctx, version_id=version.id, plan=plan, current={})
        if plan.prices:
            catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
        return row.id

    current = existing.get(str(plan.id))
    if current is None:
        raise CommercialRecordNotFound(
            "That plan is not one of this product's plans.", plan_id=str(plan.id)
        )
    changes = {
        key: value
        for key, value, was in (
            ("name", plan.name, current["name"]),
            ("caption", plan.caption, current["caption"]),
            ("description", plan.description, current["description"]),
            ("is_public", plan.visible, current["is_public"]),
            ("sort_order", sort_order, current["sort_order"]),
        )
        if value != was
    }
    if changes:
        catalog_services.update_plan(ctx, plan_id=plan.id, **changes)
    published, draft = _latest(current)
    terms = published or draft
    same_terms = terms is not None and (
        terms["included_seats"],
        terms["max_seats"],
        terms["trial_days"],
    ) == (plan.included_seats, plan.max_seats, plan.trial_days)
    running = {p["interval"]: p for p in _current_prices(terms["id"] if terms else None)}
    if same_terms and terms is not None:
        _publish_prices(ctx, version_id=UUID(terms["id"]), plan=plan, current=running)
        if published is None and plan.prices:
            catalog_services.publish_plan_version(ctx, plan_version_id=UUID(terms["id"]))
        return plan.id
    # New terms: a new version, priced, then put in force. The version a
    # subscriber bought stays exactly as it was.
    version = catalog_services.draft_plan_version(
        ctx,
        plan_id=plan.id,
        features=dict(terms["features"]) if terms else {},
        included_seats=plan.included_seats,
        max_seats=plan.max_seats,
        trial_days=plan.trial_days,
    )
    _publish_prices(ctx, version_id=version.id, plan=plan, current={})
    if plan.prices:
        catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
    return plan.id


def _apply_plans(
    ctx: TenantContext, *, product_id: UUID, product_code: str, plans: list[PlanInput]
) -> None:
    listing = catalog.plan_listing()
    taken = {p["code"] for p in listing}
    existing = {
        p["id"]: p
        for p in listing
        if p["product"]["id"] == str(product_id) and p["kind"] == "standard"
    }
    recommended_now = next((pid for pid, p in existing.items() if p["recommended"]), None)
    wanted: UUID | None = None
    for position, plan in enumerate(plans):
        try:
            plan_id = _apply_plan(
                ctx,
                product_id=product_id,
                product_code=product_code,
                plan=plan,
                position=position,
                existing=existing,
                taken=taken,
            )
        except CatalogValidationError as exc:
            inner = str(exc.context.get("field") or "")
            raise CatalogValidationError(
                exc.message,
                field=f"plans.{position}{'.' + inner if inner else ''}",
            ) from exc
        if plan.recommended:
            wanted = plan_id
    if wanted is not None and str(wanted) != recommended_now:
        catalog_services.update_plan(ctx, plan_id=wanted, recommended=True)
    elif wanted is None and recommended_now is not None:
        catalog_services.update_plan(
            ctx, plan_id=UUID(recommended_now), recommended=False
        )


def _make_plans_available(ctx: TenantContext, *, product_code: str) -> None:
    """Every visible draft plan of the product that is priced and in force goes
    on sale (draft -> available). A hidden or unpriced plan is left as it is."""
    for plan in catalog.plan_listing(product_code=product_code):
        if (
            plan["kind"] != "standard"
            or plan["status"] != CatalogStatus.DRAFT.value
            or not plan["is_public"]
        ):
            continue
        published, _draft = _latest(plan)
        if published is None or not _current_prices(published["id"]):
            continue
        catalog_services.set_plan_status(
            ctx, plan_id=UUID(plan["id"]), status=CatalogStatus.AVAILABLE
        )


def _product_changes(body: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """The product fields the page sends that differ from the stored row."""
    out: dict[str, Any] = {}
    for key, value in body.items():
        stored = row.get(key)
        if key == "highlights":
            value = [str(v).strip() for v in (value or []) if str(v).strip()]
            stored = list(stored or [])
        if value != stored:
            out[key] = value
    return out


@in_scope
def save(ctx: TenantContext, product_id: UUID | None, body: Any) -> dict[str, Any]:
    """Save the page: the product (created when ``product_id`` is ``None``) and
    its plans, in one transaction. A product saved here is a draft until it is
    published; a live product's new, visible, priced plans go on sale at once."""
    authz.require(ctx, CATALOG_MANAGE)
    if not isinstance(body, dict):
        raise CatalogValidationError("The request body is an object.", field="body")
    _refuse_unknown(body, _BODY_FIELDS, "page")
    product_body = body.get("product") or {}
    if not isinstance(product_body, dict):
        raise CatalogValidationError("product is an object.", field="product")
    _refuse_unknown(product_body, PRODUCT_FIELDS, "product")
    plans = _plan_inputs(body.get("plans"))
    if any(p.prices for p in plans):
        authz.require(ctx, PRICING_MANAGE)
    books = {b["id"] for b in _books()}
    for i, plan in enumerate(plans):
        if plan.price_book_id is not None and str(plan.price_book_id) not in books:
            raise CatalogValidationError(
                "That price book is not an active direct price book.",
                field=f"plans.{i}.price_book_id",
            )

    with transaction.atomic():
        if product_id is None:
            created = product_manager.create(
                ctx,
                {
                    **{k: v for k, v in product_body.items() if k in _CREATE_FIELDS},
                    **(
                        {"integration": body["integration"]}
                        if body.get("integration")
                        else {}
                    ),
                    **({"trial": body["trial"]} if body.get("trial") else {}),
                },
            )
            product_id = UUID(created["product"]["id"])
            rest = {k: v for k, v in product_body.items() if k not in _CREATE_FIELDS}
            rest = {k: v for k, v in rest.items() if v not in ("", None, [])}
            if rest:
                catalog_services.update_product(ctx, product_id=product_id, changes=rest)
        else:
            facts = catalog.product_facts_by_id(product_id)
            if facts is None:
                raise CommercialRecordNotFound(
                    "No such product.", product_id=str(product_id)
                )
            row = _row_for(product_id) or {}
            changes = _product_changes(product_body, row)
            if changes:
                catalog_services.update_product(
                    ctx, product_id=product_id, changes=changes
                )
            if body.get("trial") is not None:
                trial = product_manager._trial_terms(body["trial"])
                if trial is not None:
                    current = row.get("trial") or {}
                    if (current.get("enabled"), current.get("duration_days")) != (
                        trial["enabled"],
                        trial["duration_days"],
                    ):
                        trial["eligible_plan_codes"] = list(
                            current.get("eligible_plan_codes") or []
                        )
                        catalog_services.configure_product_trial(
                            ctx, product_id=product_id, **trial
                        )
        facts = catalog.product_facts_by_id(product_id) or {}
        code = str(facts.get("code") or "")
        _apply_plans(ctx, product_id=product_id, product_code=code, plans=plans)
        if facts.get("status") == "available":
            _make_plans_available(ctx, product_code=code)
    return state(ctx, product_id)


@in_scope
def publish(ctx: TenantContext, product_id: UUID) -> dict[str, Any]:
    """Publish to the Store: the product's visible, priced plans go on sale and
    the product is published - or, if the catalog's publish checklist refuses,
    nothing changes (one transaction)."""
    authz.require(ctx, CATALOG_MANAGE)
    facts = catalog.product_facts_by_id(product_id)
    if facts is None:
        raise CommercialRecordNotFound("No such product.", product_id=str(product_id))
    with transaction.atomic():
        _make_plans_available(ctx, product_code=str(facts["code"]))
        catalog_services.publish_product(ctx, product_id=product_id)
    return state(ctx, product_id)


# --- previewing ------------------------------------------------------------------


def _admin_media_url(product_id: UUID) -> Any:
    def url(_slug: str, media_id: Any) -> str:
        return f"/api/v1/admin/products/{product_id}/media/{media_id}"

    return url


def _preview_plans(product_code: str, currency: str | None) -> list[dict[str, Any]]:
    """The product's visible standard plans - drafts too - in the Store's own
    plan shape, priced in ``currency`` exactly as the public catalog prices."""
    from workeasy360.commercial.pricing import selectors as pricing

    now = timezone.now()
    plans = []
    for plan in catalog.plan_listing(product_code=product_code):
        if (
            plan["kind"] != "standard"
            or not plan["is_public"]
            or plan["status"] == CatalogStatus.RETIRED.value
        ):
            continue
        published, draft = _latest(plan)
        terms = published or draft
        if terms is None:
            continue
        plans.append((plan, terms))
    prices: dict[tuple[str, str], dict[str, Any]] = {}
    if currency and plans:
        prices = pricing.direct_list_prices(
            currency=currency,
            region="",
            plan_version_ids=[UUID(t["id"]) for _p, t in plans],
            bundle_version_ids=[],
            at=now,
        )
    out = []
    for plan, terms in plans:
        out.append(
            {
                "plan_version_id": terms["id"],
                "plan_code": plan["code"],
                "name": plan["name"],
                "caption": plan["caption"],
                "description": plan["description"],
                "billing_type": plan["billing_type"],
                "kind": plan["kind"],
                "addon_for": None,
                "included_seats": terms["included_seats"],
                "max_seats": terms["max_seats"],
                "trial_days": terms["trial_days"],
                "prices": [v for (tid, _i), v in prices.items() if tid == terms["id"]],
                "recommended": plan["recommended"],
                "sort_order": plan["sort_order"],
                "renewal_rule": plan["renewal_rule"],
                "included_usage": plan["included_usage"],
            }
        )
    return out


@in_scope
def preview(ctx: TenantContext, product_id: UUID) -> dict[str, Any]:
    """The Store product page of this product exactly as the Store would build
    it once published - a draft included - for staff only. Its images and video
    are served by the staff media endpoint, so nothing becomes public to be
    previewed, and the page sells nothing (the Store's buttons are inert in
    the preview)."""
    from workeasy360.commercial.billing import tax
    from workeasy360.commercial.controlplane import portal_store
    from workeasy360.commercial.pricing import selectors as pricing
    from workeasy360.commercial.subscriptions import marketplace

    authz.require(ctx, CATALOG_VIEW)
    row = _row_for(product_id)
    if row is None:
        raise CommercialRecordNotFound("No such product.", product_id=str(product_id))
    available = pricing.direct_currencies()
    currency = marketplace._currency_for(None, available)
    plans = _preview_plans(row["code"], currency)
    trial = marketplace._trial_offer(row.get("trial"), plans, "available")
    for plan in plans:
        plan["trial_available"] = plan["plan_code"] in trial["plan_codes"]
    product = {
        "code": row["code"],
        "slug": row["slug"],
        "name": row["name"],
        "tagline": row.get("tagline", ""),
        # Shown as it will be once on sale, whatever its state today.
        "availability": "available",
        "trial": trial,
        "category": row.get("category", ""),
        "icon": row.get("icon", ""),
        "plans": plans,
    }
    page = portal_store._product_page(
        [product],
        row["code"],
        status_of=lambda _p: "available",
        held={"held_plan_code": None, "renews_at": None},
        media_url=_admin_media_url(product_id),
        video_url=lambda _slug, uploaded: (
            f"/api/v1/admin/products/{product_id}/media/{uploaded['id']}"
        ),
    )
    assert page is not None
    return {
        "preview": True,
        "currency": currency,
        "currencies": [currency] if currency else [],
        "tax": tax.terms(currency) if currency else None,
        **page,
    }

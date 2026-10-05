"""The Super Admin plan builder's reads (Phase 8 extension).

The builder writes through the existing catalog and pricing services (create a
plan, draft and publish a version, publish a price per billing cycle, change
status) - there is no second write path. What it lacked was a way to *see* the
catalog: until now only products could be listed. This composes the catalog's
and pricing's own listings, which is why it lives here, above both.

``platform.staff_catalog.view`` (implied by ``.manage`` and by
``platform.staff_pricing.manage``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.pricing import selectors as pricing
from workeasy360.contracts.enums import BillingInterval, PlanBillingType
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW = "platform.staff_catalog.view"

#: The cycles the plan builder offers, in display order. ``one_time`` is a
#: pricing concept the builder does not sell.
BUILDER_INTERVALS = (
    BillingInterval.MONTHLY,
    BillingInterval.QUARTERLY,
    BillingInterval.SEMI_ANNUAL,
    BillingInterval.ANNUAL,
)


@in_scope
def overview(ctx: TenantContext, *, product_code: str | None = None) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    plans = catalog.plan_listing(product_code=product_code)
    bundles = catalog.bundle_listing()
    plan_versions = [UUID(v["id"]) for p in plans for v in p["versions"]]
    bundle_versions = [UUID(v["id"]) for b in bundles for v in b["versions"]]
    prices = pricing.current_prices(
        plan_version_ids=plan_versions, bundle_version_ids=bundle_versions
    )
    by_target: dict[str, list[dict[str, Any]]] = {}
    for price in prices:
        target = price["plan_version_id"] or price["bundle_version_id"]
        by_target.setdefault(target, []).append(price)
    for plan in plans:
        for version in plan["versions"]:
            version["prices"] = by_target.get(version["id"], [])
    for bundle in bundles:
        for version in bundle["versions"]:
            version["prices"] = by_target.get(version["id"], [])
    return {
        "products": catalog.visible_products(),
        "plans": plans,
        "bundles": bundles,
        "price_books": pricing.price_books(),
        "currencies": [c.value for c in CurrencyCode],
        "intervals": [i.value for i in BUILDER_INTERVALS],
        "billing_types": [t.value for t in PlanBillingType],
    }


@in_scope
def product_features(ctx: TenantContext, product_id: UUID) -> list[dict[str, Any]]:
    authz.require(ctx, VIEW)
    return catalog.features_for_product(product_id)

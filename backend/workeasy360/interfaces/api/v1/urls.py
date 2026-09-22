"""API v1 URL assembly.

Each module owns its own API surface; this file only includes them. What is
genuinely cross-cutting - version routing, throttles, schema generation -
lives here and nowhere else.

Phase 1 exposes no product endpoints. The tenant-scoped path shape is fixed
now because the tenancy middleware resolves the organization from it:

    /api/v1/orgs/{org_slug}/...

Organization comes from the path and never from a header or body (TB-1/TB-2).
"""

from __future__ import annotations

from django.urls import path

from workeasy360.interfaces.api.v1 import commercial_views, views

urlpatterns = [
    # A minimal tenant-scoped surface, present so the tenancy middleware,
    # the suspension block and the isolation tests have a real HTTP path to
    # exercise rather than a synthetic one.
    path(
        "orgs/<slug:org_slug>/context",
        views.tenant_context_view,
        name="tenant-context",
    ),
    path(
        "orgs/<slug:org_slug>/memberships",
        views.memberships_view,
        name="memberships",
    ),
    path(
        "orgs/<slug:org_slug>/entitlements",
        views.entitlements_view,
        name="entitlements",
    ),
    # --- commercial: catalog and pricing administration ---
    #
    # Under the tenant path shape like everything else. These are gated by
    # commercial.catalog.manage / commercial.pricing.manage **and** by an
    # internal platform identity, both checked in the application service.
    path(
        "orgs/<slug:org_slug>/commercial/products",
        commercial_views.products_view,
        name="commercial-products",
    ),
    path(
        "orgs/<slug:org_slug>/commercial/products/<uuid:product_id>/status",
        commercial_views.product_status_view,
        name="commercial-product-status",
    ),
    path(
        "orgs/<slug:org_slug>/commercial/features",
        commercial_views.features_view,
        name="commercial-features",
    ),
    path(
        "orgs/<slug:org_slug>/commercial/plans",
        commercial_views.plans_view,
        name="commercial-plans",
    ),
    path(
        "orgs/<slug:org_slug>/commercial/plans/<uuid:plan_id>/status",
        commercial_views.plan_status_view,
        name="commercial-plan-status",
    ),
    path(
        "orgs/<slug:org_slug>/commercial/plan-versions",
        commercial_views.plan_versions_view,
        name="commercial-plan-versions",
    ),
    path(
        "orgs/<slug:org_slug>/commercial/bundles",
        commercial_views.bundles_view,
        name="commercial-bundles",
    ),
    path(
        "orgs/<slug:org_slug>/commercial/bundle-versions",
        commercial_views.bundle_versions_view,
        name="commercial-bundle-versions",
    ),
    path(
        "orgs/<slug:org_slug>/commercial/price-books",
        commercial_views.price_books_view,
        name="commercial-price-books",
    ),
    path(
        "orgs/<slug:org_slug>/commercial/prices",
        commercial_views.prices_view,
        name="commercial-prices",
    ),
    # --- commercial: the customer's own subscription ---
    path(
        "orgs/<slug:org_slug>/billing-account",
        commercial_views.billing_account_view,
        name="billing-account",
    ),
    path(
        "orgs/<slug:org_slug>/subscriptions",
        commercial_views.subscriptions_view,
        name="subscriptions",
    ),
    path(
        "orgs/<slug:org_slug>/subscriptions/purchase",
        commercial_views.purchase_view,
        name="subscription-purchase",
    ),
    path(
        "orgs/<slug:org_slug>/subscriptions/<uuid:subscription_id>",
        commercial_views.subscription_detail_view,
        name="subscription-detail",
    ),
    path(
        "orgs/<slug:org_slug>/subscriptions/<uuid:subscription_id>/activate",
        commercial_views.activate_view,
        name="subscription-activate",
    ),
    path(
        "orgs/<slug:org_slug>/subscriptions/<uuid:subscription_id>/cancel",
        commercial_views.cancel_view,
        name="subscription-cancel",
    ),
    path(
        "orgs/<slug:org_slug>/subscription-items/<uuid:subscription_item_id>/change-plan",
        commercial_views.change_plan_view,
        name="subscription-change-plan",
    ),
]

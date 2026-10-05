"""Query budgets for the launcher's HTTP surface (Task 59).

Pinned, not aspirational: each number is what the endpoint costs today,
with a fixed catalog of two products (docs/phase-4/09-performance.md).
A budget that grows without anyone noticing is exactly what these tests
exist to catch.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_the_app_directory_has_a_bounded_query_budget(
    client, catalog, register_product, entitled_org, django_assert_max_num_queries
):
    """One directory call composes: a tenant-context resolve, a catalog
    read, and per-product entitlement/seat/integration reads. Fixed at two
    products (crm, books) by the shared ``catalog`` fixture - the budget is
    stated per-catalog-size, not as a claim that scales to any N.

    **21 -> 27 in Phase 11.** A card now also carries presentation (category,
    icon, tagline), single-sign-on and health status, and - where the caller
    may see them - the plan, trial, seats and renewal the organization holds.
    Every one of those reads is batched over the whole product list rather
    than run per product (``launcher_views._card_extras``), so the increase is
    fixed (six queries: presentation, connection presence, two for holdings,
    two for seat usage) and does not appear in the per-product slope test
    below, which is unchanged.
    """
    register_product("crm")
    register_product("books")
    org = entitled_org("crm")

    client.force_login(org["user"])
    with django_assert_max_num_queries(27):
        response = client.get(f"/api/v1/orgs/{org['org'].slug}/apps")
    assert response.status_code == 200
    assert len(response.json()["apps"]) == 2


#: What one additional marketable product costs the directory, measured.
#:
#: **One query, and which one.** The entitlement snapshot is read once per
#: request whatever the catalog size (``EntitlementRequestScopeMiddleware``
#: memoises it), the catalog itself is one ``list_marketable()``, and the
#: seat check only runs for a product the organization actually holds. What
#: is left is ``ecosystem.selectors.launch_target()`` - "is there a
#: registered integration for this code, and where does it point" - asked
#: once per product.
#:
#: It was **two** until the Phase 4 audit: ``can_launch_product`` re-read
#: each product's catalog descriptor that ``app_directory`` had just
#: fetched in bulk. Measured at 50 products: 119 queries before, 69 after.
#:
#: The remaining one is a real read with a real batched fix
#: (``launch_targets(codes)``, one query for the set). Not built, because a
#: platform sells tens of products rather than thousands and this budget is
#: what makes that assumption falsifiable rather than assumed: the slope
#: test below fails the moment the cost per product goes back up, which is
#: the signal to build it.
QUERIES_PER_ADDITIONAL_PRODUCT = 1


def test_the_app_directory_cost_per_product_does_not_grow(
    client, catalog, register_product, entitled_org, platform_admin, in_tenant
):
    """A single budget pins the constant; this pins the **slope**.

    A budget alone cannot tell "23 queries for 2 products" from "23 queries
    for 2 products and 2,300 for 200" - and the second is the one that takes
    a launcher down. Measuring at two catalog sizes and asserting the
    difference is what makes the per-product cost above a fact rather than a
    comment.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.contracts.enums import LicensingMode, ProductStatus

    small, large = 5, 20
    register_product("crm")
    org = entitled_org("crm")
    client.force_login(org["user"])

    def add_products(count: int, offset: int) -> None:
        ctx = platform_admin["ctx"]
        with in_tenant(ctx):
            for n in range(offset, offset + count):
                product = catalog_services.create_product(
                    ctx,
                    code=f"probe{n:03d}",
                    name=f"Probe {n}",
                    licensing_mode=LicensingMode.PER_SEAT,
                )
                catalog_services.set_product_status(
                    ctx, product_id=product.id, status=ProductStatus.AVAILABLE
                )

    def directory_queries(expected_apps: int) -> int:
        with CaptureQueriesContext(connection) as captured:
            response = client.get(f"/api/v1/orgs/{org['org'].slug}/apps")
        assert response.status_code == 200
        assert len(response.json()["apps"]) == expected_apps
        return len(captured)

    # The shared catalog fixture already provides crm and books.
    add_products(small - 2, offset=0)
    at_small = directory_queries(small)

    add_products(large - small, offset=small)
    at_large = directory_queries(large)

    growth = at_large - at_small
    budget = (large - small) * QUERIES_PER_ADDITIONAL_PRODUCT
    assert growth <= budget, (
        f"The app directory costs {growth} extra queries for "
        f"{large - small} extra products ({growth / (large - small):.1f} "
        f"each), over the budgeted {QUERIES_PER_ADDITIONAL_PRODUCT}. "
        "Something in can_launch_product is reading per product that used "
        "to be read once - batch it (see QUERIES_PER_ADDITIONAL_PRODUCT)."
    )


def test_the_seat_overview_has_a_bounded_query_budget(
    client, catalog, register_product, entitled_org, django_assert_max_num_queries
):
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    register_product("crm")
    org = entitled_org("crm")
    ctx = TenantContext(
        organization_id=org["org"].id,
        membership_id=org["membership"].id,
        user_id=org["user"].id,
    )
    with tenant_context(ctx), tenant_db_scope(ctx):
        from workeasy360.platform.authz import services as authz
        from workeasy360.platform.authz.models import Role, RoleAssignment, RolePermission

        role = Role(
            organization_id=org["org"].id, key="perf-test", name="Perf", is_system=False
        )
        role.save()
        RolePermission.objects.create(role=role, permission_id="platform.seat.assign")
        RoleAssignment(
            organization_id=org["org"].id, membership_id=org["membership"].id, role=role
        ).save()
        authz.bump_permission_version(org["org"].id)

    client.force_login(org["user"])
    with django_assert_max_num_queries(23):
        response = client.get(f"/api/v1/orgs/{org['org'].slug}/apps/crm/seats")
    assert response.status_code == 200

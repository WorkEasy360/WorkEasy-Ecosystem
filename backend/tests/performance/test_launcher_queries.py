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
    stated per-catalog-size, not as a claim that scales to any N."""
    register_product("crm")
    register_product("books")
    org = entitled_org("crm")

    client.force_login(org["user"])
    with django_assert_max_num_queries(23):
        response = client.get(f"/api/v1/orgs/{org['org'].slug}/apps")
    assert response.status_code == 200
    assert len(response.json()["apps"]) == 2


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

"""The authoritative launch decision - one function, every state.

ADR-021, Task 9. Every test drives ``can_launch_product`` end to end,
through real ``platform/entitlements`` and ``platform/ecosystem`` state, not
through a mock of either - the property under test is that the *composition*
of the three modules produces the documented state, and a mock would assert
that the mock was called rather than that the answer is right.
"""

from __future__ import annotations

import pytest

from workeasy360.platform.launcher.access import AppAccessState, can_launch_product

pytestmark = pytest.mark.django_db


def test_a_product_with_no_registered_integration_is_unavailable(
    catalog, entitled_org, in_tenant
):
    """CRM is entitled and seated in this fixture, but nothing was
    registered against it in ``platform/ecosystem`` - there is nowhere to
    send a browser."""
    org = entitled_org("crm")
    with in_tenant(org["ctx"]):
        decision = can_launch_product(org["ctx"], "crm")
    assert decision.state is AppAccessState.PRODUCT_UNAVAILABLE
    assert not decision.state.can_launch
    assert decision.launch_url == ""


def test_registered_but_not_entitled_is_no_entitlement(
    catalog, organization_factory, tenant_ctx_factory, register_product, in_tenant
):
    register_product("crm")
    org, membership, user = organization_factory()
    ctx = tenant_ctx_factory(org, membership, user)

    with in_tenant(ctx):
        decision = can_launch_product(ctx, "crm")
    assert decision.state is AppAccessState.NO_ENTITLEMENT


def test_entitled_and_seated_is_active(
    catalog, register_product, entitled_org, in_tenant
):
    register_product("crm")
    org = entitled_org("crm")  # crm is PER_SEAT in the shared catalog fixture

    with in_tenant(org["ctx"]):
        decision = can_launch_product(org["ctx"], "crm")
    assert decision.state is AppAccessState.ACTIVE
    assert decision.state.can_launch
    assert decision.launch_url  # the registered launch_url, non-empty


def test_entitled_per_seat_product_with_no_seat_is_no_seat(
    catalog, register_product, entitled_org, in_tenant
):
    register_product("crm")
    org = entitled_org("crm", seat=False)  # entitled, deliberately no seat

    with in_tenant(org["ctx"]):
        decision = can_launch_product(org["ctx"], "crm")
    assert decision.state is AppAccessState.NO_SEAT
    assert not decision.state.can_launch


def test_organization_wide_product_needs_no_seat(
    catalog, register_product, entitled_org, in_tenant
):
    """Books is PER_ORGANIZATION in the shared catalog fixture: entitlement
    alone is sufficient, and ``entitled_org`` assigns no seat for it."""
    register_product("books")
    org = entitled_org("books")

    with in_tenant(org["ctx"]):
        decision = can_launch_product(org["ctx"], "books")
    assert decision.state is AppAccessState.ACTIVE


def test_a_suspended_integration_is_reported_distinctly_from_no_entitlement(
    catalog, register_product, entitled_org, platform_admin, in_tenant
):
    """Entitled and seated, but the platform suspended the integration - an
    operational fact, never confused with a commercial one (Task 48).

    The transition itself requires staff (``platform.integration.lifecycle``
    is ``dangerous``) - performed by ``platform_admin``, not by
    the entitled organization's own member, exactly as production requires.
    """
    from workeasy360.platform.ecosystem import services as ecosystem_services

    registration = register_product("crm")
    org = entitled_org("crm")

    with in_tenant(platform_admin["ctx"]):
        ecosystem_services.transition(
            platform_admin["ctx"],
            client_id=registration["client_id"],
            to_status="suspended",
            reason="test",
        )

    with in_tenant(org["ctx"]):
        decision = can_launch_product(org["ctx"], "crm")
    assert decision.state is AppAccessState.INTEGRATION_SUSPENDED


def test_an_unentitled_organization_never_learns_the_integration_is_suspended(
    catalog,
    register_product,
    organization_factory,
    tenant_ctx_factory,
    platform_admin,
    in_tenant,
):
    """Task 48: no commercial/security internal leaks beyond what is needed.
    An organization that never bought the product sees NO_ENTITLEMENT, not
    INTEGRATION_SUSPENDED, even while the integration actually is suspended -
    it is not this organization's business whether it is."""
    from workeasy360.platform.ecosystem import services as ecosystem_services

    registration = register_product("crm")
    with in_tenant(platform_admin["ctx"]):
        ecosystem_services.transition(
            platform_admin["ctx"],
            client_id=registration["client_id"],
            to_status="suspended",
            reason="test",
        )

    org, membership, user = organization_factory()
    ctx = tenant_ctx_factory(org, membership, user)
    with in_tenant(ctx):
        decision = can_launch_product(ctx, "crm")
    assert decision.state is AppAccessState.NO_ENTITLEMENT


def test_an_unknown_product_code_is_unavailable_not_an_error(
    catalog, entitled_org, in_tenant
):
    """The launch endpoint may be asked about any string a browser sends
    (Task 55) - this must never raise for one the catalog has never heard
    of."""
    org = entitled_org("crm")
    with in_tenant(org["ctx"]):
        decision = can_launch_product(org["ctx"], "no-such-product")
    assert decision.state is AppAccessState.PRODUCT_UNAVAILABLE


def test_the_state_enum_is_exactly_the_five_documented_states():
    """Task 8: an explicit, closed set. A sixth value appearing here without
    this test changing is exactly the kind of silent expansion Task 8 warns
    against."""
    assert {s.value for s in AppAccessState} == {
        "active",
        "no_seat",
        "no_entitlement",
        "integration_suspended",
        "product_unavailable",
    }

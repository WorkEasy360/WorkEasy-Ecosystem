"""The reseller flow end to end: provision, sell, isolate, terminate (Phase 7).

The scenario properties of the acceptance flow, one assertion each, against a
real database with RLS enforced.
"""

from __future__ import annotations

import uuid

import pytest

from workeasy360.commercial.partners import customers, selectors, services
from workeasy360.contracts.enums import PartnerLinkStatus, PartnerStatus
from workeasy360.contracts.errors import (
    PartnerNotActive,
    PartnerRecordNotFound,
    PartnerValidationError,
    PermissionDenied,
)
from workeasy360.platform.authz.control_plane import acting_scope

pytestmark = pytest.mark.django_db

from tests.phase7.support import CAPS, links_for
from tests.phase7.support import provision as _provision
from tests.phase7.support import sell as _sell

# --- provisioning ---------------------------------------------------------


def test_provisioning_creates_a_customer_with_its_own_pending_owner(channel):
    result, owner = _provision(channel)
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations.resolver import is_active_owner

    link = selectors.link_for(channel["partner"].id, result.organization_id)
    assert link is not None and link.status == "active"
    # The owner has been *invited*: nothing is granted until they accept.
    assert not is_active_owner(user_id=owner.id, organization_id=result.organization_id)
    # The partner is not a member of the customer organization.
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test inspects memberships", module="tests"),
        organization_db_scope(result.organization_id),
    ):
        members = list(
            Membership.unscoped.filter(
                organization_id=result.organization_id
            ).values_list("user_id", "status")
        )
    assert members == [(owner.id, MembershipStatus.INVITED.value)]


def test_provisioning_is_idempotent(channel):
    key = uuid.uuid4().hex
    first, owner = _provision(channel, key=key)
    again = customers.provision_customer(
        channel["actor"](),
        organization_name="Customer X",
        owner_email=owner.email,
        idempotency_key=key,
    )
    assert again.replayed and again.organization_id == first.organization_id
    assert len(links_for(channel["partner"].id)) == 1


def test_a_reused_key_with_different_input_is_refused(channel):
    from workeasy360.contracts.errors import IdempotencyConflict

    key = uuid.uuid4().hex
    _provision(channel, name="One", key=key)
    with pytest.raises(IdempotencyConflict):
        _provision(channel, name="Two", key=key)


def test_invalid_provisioning_leaves_nothing_behind(channel):
    from workeasy360.platform.organizations.selectors import all_organization_ids

    before = len(all_organization_ids())
    with pytest.raises(PartnerValidationError):
        customers.provision_customer(
            channel["actor"](),
            organization_name="   ",
            owner_email="someone@example.com",
            idempotency_key="k1",
        )
    with pytest.raises(PartnerValidationError):
        customers.provision_customer(
            channel["actor"](),
            organization_name="Fine Name",
            owner_email="not-an-address",
            idempotency_key="k2",
        )
    assert len(all_organization_ids()) == before
    assert links_for(channel["partner"].id) == []


def test_the_invited_owner_accepts_and_becomes_owner(channel):
    from workeasy360.platform.organizations.resolver import is_active_owner

    result, owner = _provision(channel)
    customers.accept_owner_invite(
        user_id=owner.id, organization_id=result.organization_id
    )
    assert is_active_owner(user_id=owner.id, organization_id=result.organization_id)
    # Accepting twice is harmless.
    customers.accept_owner_invite(
        user_id=owner.id, organization_id=result.organization_id
    )


def test_only_the_invited_person_can_accept(channel, user_factory):
    result, _owner = _provision(channel)
    with pytest.raises(PartnerRecordNotFound):
        customers.accept_owner_invite(
            user_id=user_factory().id, organization_id=result.organization_id
        )


def test_provisioning_needs_the_capability(partner_factory):
    fx = partner_factory("commission.earn")
    with pytest.raises(PermissionDenied):
        customers.provision_customer(
            fx["actor"](),
            organization_name="X",
            owner_email="a@b.com",
            idempotency_key="k",
        )


def test_a_suspended_partner_cannot_provision(channel):
    services.set_partner_status(
        channel["staff_ctx"],
        channel["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Under review for the test",
    )
    with pytest.raises((PermissionDenied, PartnerNotActive)):
        _provision(channel)


# --- selling --------------------------------------------------------------


def test_a_partner_sale_uses_the_ordinary_path_and_creates_entitlement(channel):
    from workeasy360.platform.entitlements.selectors import decision_for_organization

    result, _owner = _provision(channel)
    item = _sell(channel, result.organization_id)
    assert item.product_code == "crm"
    assert decision_for_organization(result.organization_id, "crm").allowed


def test_a_partner_cannot_sell_an_ungranted_product(channel):
    result, _owner = _provision(channel)
    with pytest.raises(PermissionDenied) as caught:
        _sell(channel, result.organization_id, plan="books_professional")
    assert caught.value.context.get("reason") == "product_not_granted"


def test_a_plan_grant_narrows_the_product(partner_factory, catalog):
    fx = partner_factory("customer.provision", "subscription.sell")
    services.grant_product(
        fx["staff_ctx"], fx["partner"].id, product_code="crm", plan_code="crm_starter"
    )
    assert selectors.may_sell(
        fx["partner"].id, product_code="crm", plan_code="crm_starter"
    )
    assert not selectors.may_sell(
        fx["partner"].id, product_code="crm", plan_code="crm_professional"
    )
    assert not selectors.may_sell(fx["partner"].id, product_code="books", plan_code="x")


def _partner_book(channel, staff_factory, amount):
    from datetime import timedelta

    from django.utils import timezone

    from workeasy360.commercial.pricing import services as pricing
    from workeasy360.contracts.enums import BillingInterval
    from workeasy360.contracts.values.money import CurrencyCode

    _u, pctx = staff_factory("platform.staff_pricing.manage")
    established = timezone.now() - timedelta(days=1)
    book = services.create_partner_price_book(
        pctx,
        channel["partner"].id,
        code=f"partner-{uuid.uuid4().hex[:6]}",
        name="Partner book",
        currency=CurrencyCode.GBP,
        valid_from=established,
    )
    with acting_scope(pctx):
        pricing.publish_price(
            pctx,
            price_book_id=book.id,
            plan_version_id=channel["catalog"]["versions"]["crm_professional"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=amount,
            valid_from=established,
        )


def test_partner_sale_is_priced_from_the_partner_book_when_one_exists(
    channel, staff_factory
):
    _partner_book(channel, staff_factory, 3999)
    result, _o = _provision(channel)
    item = _sell(channel, result.organization_id)
    assert item.price_source == "partner_book"
    assert item.unit_amount_minor == 3999


def test_without_a_partner_book_the_list_price_applies(channel):
    result, _o = _provision(channel)
    item = _sell(channel, result.organization_id)
    assert item.price_source == "default_book"
    assert item.unit_amount_minor == 4999


def test_a_partner_book_is_not_visible_to_another_partner(
    channel, partner_factory, staff_factory
):
    _partner_book(channel, staff_factory, 1)
    other = partner_factory(*CAPS)
    services.grant_product(other["staff_ctx"], other["partner"].id, product_code="crm")
    other["catalog"] = channel["catalog"]
    other["user_factory"] = channel["user_factory"]
    result, _o = _provision(other)
    item = _sell(other, result.organization_id)
    assert item.price_source == "default_book" and item.unit_amount_minor == 4999


def test_approved_catalog_shows_only_granted_plans_and_no_margin(channel):
    rows = customers.approved_catalog(channel["actor"]())
    assert {r["product_code"] for r in rows} == {"crm"}
    for row in rows:
        assert set(row) == {
            "plan_version_id",
            "plan_code",
            "plan_name",
            "product_code",
            "product_name",
            "included_seats",
            "trial_days",
            "price",
        }
        assert "commission" not in str(row).lower()


# --- isolation ------------------------------------------------------------


def test_a_partner_cannot_touch_a_customer_it_is_not_linked_to(
    channel, organization_factory
):
    org, _m, _owner = organization_factory()
    with pytest.raises(PartnerRecordNotFound):
        _sell(channel, org.id)
    with pytest.raises(PartnerRecordNotFound):
        customers.customer_subscription(channel["actor"](), org.id)


def test_a_partner_cannot_touch_another_partners_customer(channel, partner_factory):
    other = partner_factory(*CAPS)
    services.grant_product(other["staff_ctx"], other["partner"].id, product_code="crm")
    other["catalog"] = channel["catalog"]
    other["user_factory"] = channel["user_factory"]
    theirs, _o = _provision(other)
    with pytest.raises(PartnerRecordNotFound):
        _sell(channel, theirs.organization_id)
    with pytest.raises(PartnerRecordNotFound):
        customers.customer_subscription(channel["actor"](), theirs.organization_id)
    assert selectors.partner_customers_page(channel["partner"].id).items == []


def test_one_customer_cannot_be_linked_to_two_partners(channel, partner_factory):
    other = partner_factory(*CAPS)
    result, _o = _provision(channel)
    with pytest.raises(Exception) as caught:
        services.link_customer(
            other["staff_ctx"],
            other["partner"].id,
            result.organization_id,
            reason="Second partner",
        )
    assert "already has a live partner relationship" in str(caught.value)


def test_the_portfolio_shows_no_member_or_contact_data(channel):
    result, owner = _provision(channel)
    _sell(channel, result.organization_id)
    page = selectors.partner_customers_page(channel["partner"].id)
    row = page.items[0]
    assert set(row) == {
        "organization_id",
        "name",
        "organization_status",
        "link_status",
        "started_at",
        "ended_at",
        "subscription_status",
        "products",
        # ADR-045: trial, customer or nothing - a commercial state, not a person.
        "access_class",
        # Phase 10e: where the owner's activation stands - a state, its expiry
        # and how many times a link was sent. Never the address or the link.
        "owner_activation",
    }
    assert set(row["owner_activation"]) == {"activation", "expires_at", "send_count"}
    assert owner.email not in str(page.items)
    assert row["products"] == ["crm"]


# --- lifecycle of the relationship ---------------------------------------


def test_a_suspended_link_blocks_writes_but_not_reads(channel):
    result, _o = _provision(channel)
    link = selectors.link_for(channel["partner"].id, result.organization_id)
    services.change_link_status(
        channel["staff_ctx"],
        link.id,
        target=PartnerLinkStatus.SUSPENDED,
        reason="Investigating now",
    )
    with pytest.raises(PartnerNotActive):
        _sell(channel, result.organization_id)
    assert (
        customers.customer_subscription(channel["actor"](), result.organization_id) == []
    )


def test_ending_the_relationship_removes_partner_access_and_keeps_the_customer(channel):
    from workeasy360.platform.entitlements.selectors import decision_for_organization
    from workeasy360.platform.organizations.resolver import is_active_owner

    result, owner = _provision(channel)
    customers.accept_owner_invite(
        user_id=owner.id, organization_id=result.organization_id
    )
    _sell(channel, result.organization_id)

    link = selectors.link_for(channel["partner"].id, result.organization_id)
    services.change_link_status(
        channel["staff_ctx"],
        link.id,
        target=PartnerLinkStatus.ENDED,
        reason="Customer moved direct",
    )

    with pytest.raises(PartnerRecordNotFound):
        _sell(channel, result.organization_id, plan="crm_starter")
    with pytest.raises(PartnerRecordNotFound):
        customers.customer_subscription(channel["actor"](), result.organization_id)
    assert is_active_owner(user_id=owner.id, organization_id=result.organization_id)
    assert decision_for_organization(result.organization_id, "crm").allowed


def test_terminating_a_partner_ends_its_links_without_touching_customers(channel):
    from workeasy360.platform.entitlements.selectors import decision_for_organization

    result, _o = _provision(channel)
    _sell(channel, result.organization_id)
    services.set_partner_status(
        channel["staff_ctx"],
        channel["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Programme membership ended",
    )
    assert links_for(channel["partner"].id) == []
    assert decision_for_organization(result.organization_id, "crm").allowed


def test_a_partner_created_customer_can_be_rebuilt_from_subscriptions(channel):
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.platform.entitlements.models import Entitlement, EntitlementSnapshot
    from workeasy360.platform.entitlements.selectors import decision_for_organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    result, _o = _provision(channel)
    _sell(channel, result.organization_id)
    with (
        unscoped_access(reason="Test deletes the entitlement projection", module="tests"),
        organization_db_scope(result.organization_id),
    ):
        Entitlement.unscoped.filter(organization_id=result.organization_id).delete()
        EntitlementSnapshot.unscoped.filter(
            organization_id=result.organization_id
        ).delete()
    assert not decision_for_organization(result.organization_id, "crm").allowed
    reconcile(result.organization_id)
    assert decision_for_organization(result.organization_id, "crm").allowed


def _audit_rows(org_id):
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the customer's audit log", module="tests"),
        organization_db_scope(org_id),
    ):
        return list(
            AuditEvent.unscoped.filter(organization_id=org_id).values(
                "action", "actor_kind", "changes"
            )
        )


def test_partner_actions_are_visible_in_the_customers_own_audit_log(channel):
    result, _o = _provision(channel)
    _sell(channel, result.organization_id)
    rows = _audit_rows(result.organization_id)
    assert "partner" in {r["actor_kind"] for r in rows}
    actions = {r["action"] for r in rows}
    assert "partner.customer_provisioned" in actions
    assert "commercial.subscription.plan_purchased" in actions


def test_a_customer_cannot_see_that_a_partner_earns_on_it(channel):
    result, _o = _provision(channel)
    _sell(channel, result.organization_id)
    text = str(_audit_rows(result.organization_id))
    assert "commission" not in text.lower() and "rate_bp" not in text

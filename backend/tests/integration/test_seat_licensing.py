"""Seat capacity, assignment and the over-limit policy.

Capacity is a commercial fact written only by provisioning; occupancy is a
customer decision. This suite covers both sides of that split, and the
behaviour at the boundary between them: what happens when a plan change
leaves more people holding seats than the plan provides.

Lives in ``tests/integration`` rather than under ``platform/entitlements``
because it exercises both sides. A test file inside ``platform/`` that
imports ``commercial/`` is a genuine upward edge in the import graph - the
linter is right to refuse it, and moving the file is the honest fix rather
than widening the contract.
"""

from __future__ import annotations

import uuid

import pytest

from workeasy360.commercial.subscriptions import services
from workeasy360.contracts.errors import SeatLimitReached
from workeasy360.platform.entitlements.models import SeatAssignment, SeatGrant
from workeasy360.platform.entitlements.services import read as entitlements
from workeasy360.platform.entitlements.services import seats

pytestmark = pytest.mark.django_db


def key() -> str:
    return uuid.uuid4().hex


def _members(ctx, count):
    """Extra active memberships in the caller's organization."""
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.models import Membership

    created = []
    for _ in range(count):
        user = User.objects.create_user(email=f"seat-{uuid.uuid4().hex[:8]}@example.com")
        membership = Membership(
            organization_id=ctx.organization_id,
            user_id=user.id,
            status=MembershipStatus.ACTIVE.value,
            member_type="internal",
        )
        membership.save()
        created.append(membership.id)
    return created


@pytest.fixture
def seated(catalog, customer, billing_account, in_tenant):
    """CRM Starter: two seats, provisioned from the subscription."""
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
    return {"ctx": ctx, "item": item}


# --- capacity is derived from commercial state ---------------------------


def test_capacity_comes_from_the_purchased_plan(seated, in_tenant):
    ctx = seated["ctx"]
    with in_tenant(ctx):
        assert seats.capacity(ctx.organization_id, pool_key="crm") == 2
        grant = SeatGrant.objects.get(pool_key="crm")
    assert grant.source_ref.startswith("subscriptions.item:")


def test_a_bundle_seat_pool_is_shared(
    catalog, customer, billing_account, platform_admin, in_tenant
):
    """A bundle may grant seats usable across products rather than per
    product. Retrofitting this into a per-product model is a painful
    migration, which is why it is modelled now."""
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.pricing import services as pricing_services
    from workeasy360.contracts.enums import BillingInterval, CatalogStatus

    admin = platform_admin["ctx"]
    with in_tenant(admin):
        bundle = catalog_services.create_bundle(admin, code="suite_pool", name="Pooled")
        version = catalog_services.draft_bundle_version(
            admin,
            bundle_id=bundle.id,
            items=[
                {
                    "plan_version_id": catalog["versions"]["crm_professional"].id,
                    "seat_pool": "suite",
                },
                {
                    "plan_version_id": catalog["versions"]["books_professional"].id,
                    "seat_pool": "suite",
                },
            ],
        )
        catalog_services.publish_bundle_version(admin, bundle_version_id=version.id)
        catalog_services.set_bundle_status(
            admin, bundle_id=bundle.id, status=CatalogStatus.AVAILABLE
        )
        pricing_services.publish_price(
            admin,
            price_book_id=catalog["price_book"].id,
            bundle_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=6999,
        )

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_bundle(ctx, bundle_version_id=version.id, idempotency_key=key())
        pooled = seats.capacity(ctx.organization_id, pool_key="suite")
        per_product = seats.capacity(ctx.organization_id, pool_key="crm")

    # 10 (CRM Professional) + 5 (Books Professional) in one shared pool.
    assert pooled == 15
    assert per_product == 0


# --- assignment -----------------------------------------------------------


def test_assigning_a_seat_grants_it(seated, in_tenant):
    ctx = seated["ctx"]
    with in_tenant(ctx):
        seats.assign_seat(
            ctx.organization_id, pool_key="crm", membership_id=ctx.membership_id
        )
        assert entitlements.has_seat(ctx.organization_id, ctx.membership_id, "crm")
        remaining = seats.capacity(ctx.organization_id, pool_key="crm") - (
            seats.assigned_count(ctx.organization_id, pool_key="crm")
        )
        assert remaining == 1


def test_assignment_is_idempotent(seated, in_tenant):
    """A retried request must not consume a second seat."""
    ctx = seated["ctx"]
    with in_tenant(ctx):
        first = seats.assign_seat(
            ctx.organization_id, pool_key="crm", membership_id=ctx.membership_id
        )
        second = seats.assign_seat(
            ctx.organization_id, pool_key="crm", membership_id=ctx.membership_id
        )
        assert first.id == second.id
        assert seats.assigned_count(ctx.organization_id, pool_key="crm") == 1


def test_the_pool_cannot_be_overfilled(seated, in_tenant):
    ctx = seated["ctx"]
    with in_tenant(ctx):
        members = [ctx.membership_id, *_members(ctx, 2)]
        seats.assign_seat(ctx.organization_id, pool_key="crm", membership_id=members[0])
        seats.assign_seat(ctx.organization_id, pool_key="crm", membership_id=members[1])
        with pytest.raises(SeatLimitReached):
            seats.assign_seat(
                ctx.organization_id, pool_key="crm", membership_id=members[2]
            )


def test_a_pool_with_no_grant_refuses(customer, in_tenant):
    """Deny by default: an organization that bought nothing has no seats."""
    ctx = customer["ctx"]
    with in_tenant(ctx), pytest.raises(SeatLimitReached):
        seats.assign_seat(
            ctx.organization_id, pool_key="crm", membership_id=ctx.membership_id
        )


def test_releasing_a_seat_frees_it_and_keeps_the_history(seated, in_tenant):
    """Soft release, so "who had a seat in March?" stays answerable."""
    ctx = seated["ctx"]
    with in_tenant(ctx):
        seats.assign_seat(
            ctx.organization_id, pool_key="crm", membership_id=ctx.membership_id
        )
        released = seats.release_seat(
            ctx.organization_id, pool_key="crm", membership_id=ctx.membership_id
        )
        assert released == 1
        assert seats.assigned_count(ctx.organization_id, pool_key="crm") == 0
        assert SeatAssignment.objects.filter(
            membership_id=ctx.membership_id, released_at__isnull=False
        ).exists()
        # Idempotent.
        assert (
            seats.release_seat(
                ctx.organization_id, pool_key="crm", membership_id=ctx.membership_id
            )
            == 0
        )


def test_a_released_seat_can_be_reassigned(seated, in_tenant):
    ctx = seated["ctx"]
    with in_tenant(ctx):
        members = [ctx.membership_id, *_members(ctx, 2)]
        for member in members[:2]:
            seats.assign_seat(ctx.organization_id, pool_key="crm", membership_id=member)
        seats.release_seat(ctx.organization_id, pool_key="crm", membership_id=members[0])
        seats.assign_seat(ctx.organization_id, pool_key="crm", membership_id=members[2])
        assert seats.assigned_count(ctx.organization_id, pool_key="crm") == 2


def test_a_seat_does_not_substitute_for_a_product_entitlement(seated, in_tenant, catalog):
    """Seat checks are additive to the product check, never a replacement.

    A cancelled customer whose assignments survive must not keep access
    through them.
    """
    from workeasy360.commercial.subscriptions.models import Subscription

    ctx = seated["ctx"]
    with in_tenant(ctx):
        seats.assign_seat(
            ctx.organization_id, pool_key="crm", membership_id=ctx.membership_id
        )
        subscription = Subscription.objects.get()
        services.cancel_subscription(
            ctx,
            subscription_id=subscription.id,
            idempotency_key=key(),
            immediate=True,
        )
        assert not entitlements.check(ctx.organization_id, "crm").allowed


# --- the over-limit policy ------------------------------------------------


def test_a_seat_reduction_flags_rather_than_revokes(
    catalog, customer, billing_account, in_tenant
):
    """Silently locking someone out because of a billing change produces
    "I was locked out mid-invoice" incidents."""
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.models import Subscription

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        members = [ctx.membership_id, *_members(ctx, 3)]
        for member in members:
            seats.assign_seat(ctx.organization_id, pool_key="crm", membership_id=member)
        subscription = Subscription.objects.get()
        services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    # Let the scheduled downgrade take effect.
    from datetime import timedelta

    reconcile(
        ctx.organization_id, at=subscription.current_period_end + timedelta(minutes=1)
    )

    with in_tenant(ctx):
        stranded = seats.over_limit_assignments(ctx.organization_id, pool_key="crm")
        still_assigned = seats.assigned_count(ctx.organization_id, pool_key="crm")

    assert seats_capacity(ctx) == 2
    # Four assigned, two seats: two are flagged and nobody is revoked.
    assert len(stranded) == 2
    assert still_assigned == 4


def seats_capacity(ctx):
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason="Test reads provisioned seat capacity",
            module="tests.entitlements",
        ),
        organization_db_scope(ctx.organization_id),
    ):
        return sum(
            SeatGrant.unscoped.filter(
                organization_id=ctx.organization_id, pool_key="crm"
            ).values_list("seats_total", flat=True)
        )


def test_over_limit_selection_is_deterministic(
    catalog, customer, billing_account, in_tenant
):
    """The most recently assigned lose their seat first, and a retry flags
    the same members rather than shuffling who is affected."""
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.models import Subscription

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        members = [ctx.membership_id, *_members(ctx, 3)]
        for member in members:
            seats.assign_seat(ctx.organization_id, pool_key="crm", membership_id=member)
        subscription = Subscription.objects.get()
        services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    from datetime import timedelta

    at = subscription.current_period_end + timedelta(minutes=1)
    reconcile(ctx.organization_id, at=at)
    with in_tenant(ctx):
        first = {
            row.membership_id for row in seats.over_limit_assignments(ctx.organization_id)
        }
    reconcile(ctx.organization_id, at=at)
    with in_tenant(ctx):
        second = {
            row.membership_id for row in seats.over_limit_assignments(ctx.organization_id)
        }

    assert first == second
    # The two earliest keep their seats.
    assert members[0] not in first
    assert members[1] not in first


def test_restoring_capacity_clears_the_flag(
    catalog, customer, billing_account, in_tenant
):
    """A flag that only ever gets set becomes noise nobody trusts."""
    from datetime import timedelta

    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.models import Subscription

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        for member in [ctx.membership_id, *_members(ctx, 3)]:
            seats.assign_seat(ctx.organization_id, pool_key="crm", membership_id=member)
        subscription = Subscription.objects.get()
        services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    at = subscription.current_period_end + timedelta(minutes=1)
    reconcile(ctx.organization_id, at=at)
    with in_tenant(ctx):
        assert seats.over_limit_assignments(ctx.organization_id)

    # Recompute for a moment before the downgrade: capacity is back to 10.
    reconcile(
        ctx.organization_id, at=subscription.current_period_end - timedelta(minutes=1)
    )
    with in_tenant(ctx):
        assert not seats.over_limit_assignments(ctx.organization_id)


def test_provisioning_never_deletes_a_seat_grant(seated, in_tenant, catalog):
    """``SeatAssignment`` cascades from ``SeatGrant``.

    Deleting a stale grant would silently delete every assignment under it -
    revoking members' access as a side effect of a recomputation, with no
    record that it happened. Stale grants are zeroed instead.
    """
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.models import Subscription

    ctx = seated["ctx"]
    with in_tenant(ctx):
        seats.assign_seat(
            ctx.organization_id, pool_key="crm", membership_id=ctx.membership_id
        )
        subscription = Subscription.objects.get()
        services.cancel_subscription(
            ctx,
            subscription_id=subscription.id,
            idempotency_key=key(),
            immediate=True,
        )
    reconcile(ctx.organization_id)

    with in_tenant(ctx):
        grants = list(SeatGrant.objects.filter(pool_key="crm"))
        assignments = SeatAssignment.objects.filter(released_at__isnull=True).count()

    assert grants, "the grant row was deleted, taking its assignments with it"
    assert all(grant.seats_total == 0 for grant in grants)
    assert assignments == 1

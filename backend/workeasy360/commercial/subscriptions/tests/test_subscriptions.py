"""Subscription lifecycle, items, bundles, trials, upgrades and cancellation.

The questions this suite answers, in order of how expensive they are to get
wrong: can a bundle produce a duplicated product, can a duplicate command buy
twice, can a historical commercial fact change, and does access stop at the
right instant.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.pricing import services as pricing_services
from workeasy360.commercial.subscriptions import services, system
from workeasy360.commercial.subscriptions.domain import add_months, advance_period
from workeasy360.commercial.subscriptions.models import Subscription, SubscriptionItem
from workeasy360.contracts.enums import (
    BillingInterval,
    ChangeTiming,
    SubscriptionStatus,
)
from workeasy360.contracts.errors import (
    CatalogValidationError,
    CommercialError,
    CommercialRecordNotFound,
    IdempotencyConflict,
    InvalidStateTransition,
    PermissionDenied,
    SeatRemediationRequired,
)
from workeasy360.contracts.values.money import CurrencyCode

pytestmark = pytest.mark.django_db


def key() -> str:
    return uuid.uuid4().hex


# --- purchase -------------------------------------------------------------


def test_purchasing_a_plan_opens_a_subscription(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()

    assert subscription.status_enum is SubscriptionStatus.ACTIVE
    assert subscription.currency == CurrencyCode.GBP.value
    assert item.product_code == "crm"
    assert item.plan_code == "crm_professional"
    assert item.removed_at is None


def test_an_item_preserves_its_commercial_snapshot(
    catalog, customer, billing_account, in_tenant
):
    """Historical commercial facts must not be derivable *only* from mutable
    catalog tables."""
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )

    assert item.plan_version_id == catalog["versions"]["crm_professional"].id
    assert item.plan_version_number == 1
    assert item.price_id == catalog["prices"]["crm_professional"].id
    assert item.total_amount_minor == 4999
    assert item.unit_amount_minor == 4999
    assert item.currency == "GBP"
    assert item.interval == BillingInterval.MONTHLY.value
    assert item.price_source == "default_book"
    assert item.price_reason


def test_a_later_price_rise_does_not_change_an_existing_item(
    catalog, customer, billing_account, platform_admin, in_tenant
):
    """The customer must not discover a price change through their invoice."""
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    admin = platform_admin["ctx"]
    with in_tenant(admin):
        pricing_services.publish_price(
            admin,
            price_book_id=catalog["price_book"].id,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=1699,
        )

    with in_tenant(ctx):
        item.refresh_from_db()
    assert item.total_amount_minor == 1499


def test_a_later_plan_version_does_not_change_an_existing_item(
    catalog, customer, billing_account, platform_admin, in_tenant
):
    """Existing subscriptions keep the version they pinned until migrated."""
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    admin = platform_admin["ctx"]
    with in_tenant(admin):
        v2 = catalog_services.draft_plan_version(
            admin,
            plan_id=catalog["plans"]["crm_starter"].id,
            features={"crm.pipelines.max": 99},
            included_seats=50,
        )
        catalog_services.publish_plan_version(admin, plan_version_id=v2.id)

    from workeasy360.platform.entitlements.services import read as entitlements

    with in_tenant(ctx):
        assert entitlements.quota(ctx.organization_id, "crm.pipelines.max") == 3


def test_buying_the_same_product_twice_is_refused(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        with pytest.raises(CommercialError, match="already includes"):
            services.purchase_plan(
                ctx,
                plan_version_id=catalog["versions"]["crm_professional"].id,
                idempotency_key=key(),
            )


def test_one_open_item_per_product_is_enforced_by_the_database(
    catalog, customer, billing_account, in_tenant
):
    """The service check can be bypassed; the constraint cannot.

    This is the invariant that makes "can a bundle produce duplicated
    products?" answerable with a schema guarantee rather than with a review of
    expansion logic.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            SubscriptionItem.objects.create(
                organization_id=ctx.organization_id,
                subscription_id=item.subscription_id,
                plan_version_id=catalog["versions"]["crm_professional"].id,
                product_code="crm",
                plan_code="crm_professional",
                plan_version_number=1,
                added_at=timezone.now(),
            )


def test_purchase_of_a_draft_plan_is_refused(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    from workeasy360.commercial.catalog.models import PlanVersion

    draft = PlanVersion.objects.create(
        plan=catalog["plans"]["crm_starter"],
        version=99,
        effective_from=timezone.now(),
    )
    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="not published"):
        services.purchase_plan(ctx, plan_version_id=draft.id, idempotency_key=key())


def test_purchase_requires_a_billing_account(catalog, customer, in_tenant):
    """An implicit account would silently pick a billing currency, which is
    not recoverable once an invoice exists."""
    ctx = customer["ctx"]
    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="billing account"):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )


def test_viewing_a_subscription_does_not_allow_buying_one(
    catalog,
    customer,
    billing_account,
    in_tenant,
    tenant_ctx_factory,
    grant_permissions,
):
    """``view`` is not ``manage``.

    A second member of the *same* organization, holding only the read
    permission, must not be able to commit the organization to money.
    """
    owner = customer["ctx"]
    billing_account(owner)

    with in_tenant(owner):
        reader_membership = _extra_memberships(owner, 1)[0]

    from workeasy360.platform.organizations.models import Membership

    with in_tenant(owner):
        membership = Membership.objects.get(id=reader_membership)
    grant_permissions(customer["org"], membership, ("commercial.subscription.view",))
    reader = tenant_ctx_factory(customer["org"], membership, customer["user"], aal=2)

    with in_tenant(reader), pytest.raises(PermissionDenied):
        services.purchase_plan(
            reader,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )


# --- idempotency ----------------------------------------------------------


def test_a_repeated_purchase_command_does_not_buy_twice(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    command = key()

    with in_tenant(ctx):
        first = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=command,
        )
        second = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=command,
        )
        assert SubscriptionItem.objects.count() == 1

    assert first.id == second.id


def test_reusing_a_key_with_different_arguments_is_refused(
    catalog, customer, billing_account, in_tenant
):
    """One authorisation must not be made to perform two different
    operations."""
    ctx = customer["ctx"]
    billing_account(ctx)
    command = key()

    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=command,
        )
        with pytest.raises(IdempotencyConflict):
            services.purchase_plan(
                ctx,
                plan_version_id=catalog["versions"]["books_professional"].id,
                idempotency_key=command,
            )


def test_idempotency_keys_do_not_cross_tenants(
    catalog,
    billing_account,
    in_tenant,
    organization_factory,
    tenant_ctx_factory,
    grant_permissions,
):
    """A key issued by one organization must never resolve another's command."""
    command = "shared-key"
    contexts = []
    for _ in range(2):
        org, membership, user = organization_factory()
        grant_permissions(org, membership, ("commercial.subscription.manage",))
        ctx = tenant_ctx_factory(org, membership, user, aal=2)
        billing_account(ctx)
        contexts.append(ctx)

    items = []
    for ctx in contexts:
        with in_tenant(ctx):
            items.append(
                services.purchase_plan(
                    ctx,
                    plan_version_id=catalog["versions"]["crm_starter"].id,
                    idempotency_key=command,
                )
            )

    assert items[0].id != items[1].id
    assert items[0].organization_id != items[1].organization_id


# --- bundles --------------------------------------------------------------


def test_bundle_expands_into_one_item_per_plan(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        bundle_line = services.purchase_bundle(
            ctx,
            bundle_version_id=catalog["bundle_version"].id,
            idempotency_key=key(),
        )
        derived = list(
            SubscriptionItem.objects.filter(parent_item=bundle_line).order_by(
                "product_code"
            )
        )

    assert bundle_line.product_code == ""
    assert bundle_line.total_amount_minor == 6999
    assert [item.product_code for item in derived] == ["books", "crm"]
    assert all(item.plan_version_id is not None for item in derived)


def test_bundle_components_carry_no_price(catalog, customer, billing_account, in_tenant):
    """A bundle is priced as one thing. Pricing the parts invents revenue."""
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        bundle_line = services.purchase_bundle(
            ctx,
            bundle_version_id=catalog["bundle_version"].id,
            idempotency_key=key(),
        )
        derived = list(SubscriptionItem.objects.filter(parent_item=bundle_line))

        billed = sum(
            row.total_amount_minor or 0
            for row in SubscriptionItem.objects.filter(
                subscription=bundle_line.subscription
            )
        )

    assert all(item.price_id is None for item in derived)
    assert all(item.total_amount_minor is None for item in derived)
    # The bundle is billed once, at its own price - not as the sum of parts.
    assert billed == 6999


def test_a_repeated_bundle_expansion_does_not_duplicate_items(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    command = key()

    with in_tenant(ctx):
        first = services.purchase_bundle(
            ctx, bundle_version_id=catalog["bundle_version"].id, idempotency_key=command
        )
        second = services.purchase_bundle(
            ctx, bundle_version_id=catalog["bundle_version"].id, idempotency_key=command
        )
        # One bundle line + two derived lines, once.
        assert SubscriptionItem.objects.count() == 3

    assert first.id == second.id


def test_a_bundle_clashing_with_an_existing_plan_is_refused(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        with pytest.raises(CommercialError, match="already includes"):
            services.purchase_bundle(
                ctx,
                bundle_version_id=catalog["bundle_version"].id,
                idempotency_key=key(),
            )


def test_declining_a_mandatory_bundle_component_is_refused(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="mandatory"):
        services.purchase_bundle(
            ctx,
            bundle_version_id=catalog["bundle_version"].id,
            declined_plan_version_ids=[catalog["versions"]["books_professional"].id],
            idempotency_key=key(),
        )


def test_bundle_entitlements_use_the_ordinary_product_path(
    catalog, customer, billing_account, in_tenant
):
    """No entitlement shortcut for bundles.

    Every product entitlement comes from a plan line, whether that line was
    bought directly or produced by an expansion; a product asking "may I?"
    cannot tell the difference, which is the point.
    """
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_bundle(
            ctx, bundle_version_id=catalog["bundle_version"].id, idempotency_key=key()
        )
        assert entitlements.check(ctx.organization_id, "crm").allowed
        assert entitlements.check(ctx.organization_id, "books").allowed
        assert entitlements.quota(ctx.organization_id, "crm.pipelines.max") == 25


# --- trials ---------------------------------------------------------------


def test_a_trial_starts_trialing_and_ends_on_time(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )
        subscription = Subscription.objects.get()

    assert subscription.status_enum is SubscriptionStatus.TRIALING
    assert subscription.trial_ends_at is not None
    delta = subscription.trial_ends_at - subscription.current_period_start
    assert 13 <= delta.days <= 14


def test_a_trial_provisions_through_the_normal_path(
    catalog, customer, billing_account, in_tenant
):
    """A trial is a status, not a plan called Trial and not a flag on the
    organization. Every code path that handles entitlements handles it."""
    from workeasy360.contracts.enums import EntitlementStatus
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )
        decision = entitlements.check(ctx.organization_id, "crm", write=True)

    assert decision.allowed
    assert decision.status is EntitlementStatus.TRIAL
    assert decision.write_allowed


def test_a_second_trial_of_the_same_product_is_refused(
    catalog, customer, billing_account, in_tenant
):
    """Derived from durable history rather than a flag, which is one data-fix
    away from being cleared."""
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )
        subscription = Subscription.objects.get()
        subscription.status = SubscriptionStatus.EXPIRED.value
        subscription.save(update_fields=["status"])

        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )
        second = Subscription.objects.exclude(
            status=SubscriptionStatus.EXPIRED.value
        ).get()
    assert second.trial_ends_at is None
    assert second.status_enum is SubscriptionStatus.ACTIVE


def test_converting_a_trial_ends_it(catalog, customer, billing_account, in_tenant):
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )
        subscription = Subscription.objects.get()
        converted = services.activate_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )

    assert converted.status_enum is SubscriptionStatus.ACTIVE
    assert converted.activated_at is not None
    # The trial date must move to now, or access_ends_at() would still be
    # pinned to it and the paying customer would expire.
    assert converted.trial_ends_at <= timezone.now()


def test_an_expired_trial_removes_access(catalog, customer, billing_account, in_tenant):
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )
        subscription = Subscription.objects.get()

    # Move the clock rather than the data: the trial genuinely ends.
    later = timezone.now() + timedelta(days=15)
    system.process_due_subscriptions(at=later)

    with in_tenant(ctx):
        subscription.refresh_from_db()
        decision = entitlements.check(ctx.organization_id, "crm")

    assert subscription.status_enum is SubscriptionStatus.EXPIRED
    assert not decision.allowed


# --- upgrade and downgrade ------------------------------------------------


def test_an_upgrade_takes_effect_immediately(
    catalog, customer, billing_account, in_tenant
):
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        new_item = services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        item.refresh_from_db()
        quota = entitlements.quota(ctx.organization_id, "crm.pipelines.max")

    assert new_item.plan_code == "crm_professional"
    assert item.removed_at is not None
    assert item.removed_at <= timezone.now()
    assert quota == 25


def test_a_downgrade_defaults_to_the_period_end(
    catalog, customer, billing_account, in_tenant
):
    """The current period is already paid for at the higher tier."""
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        new_item = services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        item.refresh_from_db()
        quota_now = entitlements.quota(ctx.organization_id, "crm.pipelines.max")

    assert new_item.added_at == subscription.current_period_end
    assert item.removed_at == subscription.current_period_end
    # Nothing changes yet: the customer keeps what they paid for.
    assert quota_now == 25


def test_an_immediate_downgrade_that_strands_seats_is_refused(
    catalog, customer, billing_account, in_tenant
):
    """Deterministic policy: remediate, or schedule for the period end.

    Not a silent revocation and not a silent corruption of access state.
    """
    from workeasy360.platform.entitlements.services import seats

    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        # Professional grants 10 seats; assign three.
        memberships = [customer["membership"].id]
        for member in _extra_memberships(ctx, 2):
            memberships.append(member)
        for membership_id in memberships:
            seats.assign_seat(
                ctx.organization_id, pool_key="crm", membership_id=membership_id
            )

        with pytest.raises(SeatRemediationRequired) as excinfo:
            services.change_plan(
                ctx,
                subscription_item_id=item.id,
                to_plan_version_id=catalog["versions"]["crm_starter"].id,
                idempotency_key=key(),
                timing=ChangeTiming.IMMEDIATE,
            )
    assert excinfo.value.context["assigned"] == 3
    assert excinfo.value.context["capacity"] == 2


def _extra_memberships(ctx, count):
    """Create extra active memberships in the caller's organization."""
    import uuid as _uuid

    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.models import Membership

    created = []
    for _ in range(count):
        user = User.objects.create_user(email=f"seat-{_uuid.uuid4().hex[:8]}@example.com")
        membership = Membership(
            organization_id=ctx.organization_id,
            user_id=user.id,
            status=MembershipStatus.ACTIVE.value,
            member_type="internal",
        )
        membership.save()
        created.append(membership.id)
    return created


def test_changing_to_another_products_plan_is_refused(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        with pytest.raises(CatalogValidationError, match="not a plan change"):
            services.change_plan(
                ctx,
                subscription_item_id=item.id,
                to_plan_version_id=catalog["versions"]["books_professional"].id,
                idempotency_key=key(),
            )


def test_a_bundle_component_cannot_be_changed_alone(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        bundle_line = services.purchase_bundle(
            ctx, bundle_version_id=catalog["bundle_version"].id, idempotency_key=key()
        )
        component = SubscriptionItem.objects.get(
            parent_item=bundle_line, product_code="crm"
        )
        with pytest.raises(CommercialError, match="bundle component"):
            services.change_plan(
                ctx,
                subscription_item_id=component.id,
                to_plan_version_id=catalog["versions"]["crm_starter"].id,
                idempotency_key=key(),
            )


def test_a_plan_change_leaves_both_facts_in_the_record(
    catalog, customer, billing_account, in_tenant
):
    """A change is not an edit: the history contains what they had and what
    they moved to, with the instant it took effect."""
    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        history = list(
            SubscriptionItem.objects.filter(product_code="crm").order_by("added_at")
        )

    assert [row.plan_code for row in history] == ["crm_starter", "crm_professional"]
    assert history[0].total_amount_minor == 1499
    assert history[1].total_amount_minor == 4999


def test_activating_an_already_active_subscription_is_a_no_op(
    catalog, customer, billing_account, in_tenant
):
    """A legal self-transition that does nothing must actually do nothing.

    Re-applying it would publish an ``activated`` event for a change that did
    not happen, and bump the optimistic token - invalidating every concurrent
    caller's read for no reason.
    """
    from workeasy360.integration.events.models import OutboxEvent

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        assert subscription.status_enum is SubscriptionStatus.ACTIVE
        before_version = subscription.version

        # A *fresh* key, so the idempotency ledger does not mask the question.
        again = services.activate_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )

    assert again.version == before_version
    assert (
        OutboxEvent.objects.filter(
            organization_id=ctx.organization_id,
            event_type="commercial.subscription.activated",
        ).count()
        == 0
    )


def test_rescheduling_a_pending_plan_change_leaves_one_open_line(
    catalog, customer, billing_account, in_tenant
):
    """Two downgrades scheduled before either takes effect.

    A realistic support sequence, and the one that could plausibly break the
    one-open-line-per-product constraint: the first change closes the current
    line in the future and opens a replacement, and the second change then
    operates on that replacement.

    The middle line ends up with ``added_at == removed_at`` - a zero-width
    window - which ``is_effective_at`` never matches, so it is inert rather
    than a second grant.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        first = services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        second = services.change_plan(
            ctx,
            subscription_item_id=first.id,
            to_plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        # Asserted **inside** the scope. A queryset built here carries its
        # application filter but not the database tenant variable, so
        # evaluating it after the scope closes returns zero rows - RLS working
        # correctly, and indistinguishable from "there are none".
        open_lines = SubscriptionItem.objects.filter(
            product_code="crm", removed_at__isnull=True
        )
        history = list(
            SubscriptionItem.objects.filter(product_code="crm").order_by("added_at", "id")
        )

        assert open_lines.count() == 1
        assert open_lines.first().id == second.id
        assert len(history) == 3
        # The superseded middle line never grants anything.
        middle = next(row for row in history if row.id == first.id)
        assert middle.added_at == middle.removed_at


# --- cancellation ---------------------------------------------------------


def test_cancellation_keeps_access_until_the_period_end(
    catalog, customer, billing_account, in_tenant
):
    """Revoking on request is theft and generates chargebacks."""
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        cancelled = services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )
        decision = entitlements.check(ctx.organization_id, "crm", write=True)

    assert cancelled.status_enum is SubscriptionStatus.CANCELLED
    assert cancelled.cancel_at == subscription.current_period_end
    assert decision.allowed and decision.write_allowed


def test_immediate_cancellation_stops_access_now(
    catalog, customer, billing_account, in_tenant
):
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = customer["ctx"]
    billing_account(ctx)

    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        services.cancel_subscription(
            ctx,
            subscription_id=subscription.id,
            idempotency_key=key(),
            immediate=True,
            reason_code="fraud",
        )
        decision = entitlements.check(ctx.organization_id, "crm")

    assert not decision.allowed


def test_cancellation_stops_auto_renewal(catalog, customer, billing_account, in_tenant):
    """Otherwise the renewal sweep rolls the period past the cancellation date
    and quietly restores access."""
    from workeasy360.contracts.enums import RenewalPolicy

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        cancelled = services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )
    assert cancelled.renewal_policy == RenewalPolicy.NONE.value


def test_a_cancelled_subscription_expires_at_its_date(
    catalog, customer, billing_account, in_tenant
):
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )

    system.process_due_subscriptions(
        at=subscription.current_period_end + timedelta(minutes=1)
    )

    with in_tenant(ctx):
        subscription.refresh_from_db()
        assert subscription.status_enum is SubscriptionStatus.EXPIRED
        assert not entitlements.check(ctx.organization_id, "crm").allowed


def test_a_cancelled_subscription_accepts_no_new_commitments(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )
        with pytest.raises(InvalidStateTransition):
            services.purchase_plan(
                ctx,
                plan_version_id=catalog["versions"]["books_professional"].id,
                idempotency_key=key(),
            )


def test_cancelling_twice_returns_the_same_result(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    command = key()
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        first = services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=command
        )
        second = services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=command
        )
    assert first.cancel_at == second.cancel_at


def test_an_expired_subscription_cannot_transition(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        subscription.status = SubscriptionStatus.EXPIRED.value
        subscription.save(update_fields=["status"])
        with pytest.raises(InvalidStateTransition):
            services.activate_subscription(
                ctx, subscription_id=subscription.id, idempotency_key=key()
            )


# --- renewal --------------------------------------------------------------


def test_a_period_rolls_when_it_ends(catalog, customer, billing_account, in_tenant):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
    original_end = subscription.current_period_end

    system.process_due_subscriptions(at=original_end + timedelta(minutes=1))

    with in_tenant(ctx):
        subscription.refresh_from_db()
    assert subscription.current_period_start == original_end
    assert subscription.current_period_end > original_end
    assert subscription.status_enum is SubscriptionStatus.ACTIVE


def test_rolling_the_period_is_idempotent(catalog, customer, billing_account, in_tenant):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
    at = subscription.current_period_end + timedelta(minutes=1)

    system.process_due_subscriptions(at=at)
    with in_tenant(ctx):
        subscription.refresh_from_db()
        first = (subscription.current_period_start, subscription.current_period_end)

    system.process_due_subscriptions(at=at)
    with in_tenant(ctx):
        subscription.refresh_from_db()
        second = (subscription.current_period_start, subscription.current_period_end)

    assert first == second


def test_a_long_gap_catches_up_in_one_pass():
    """A renewal job that has not run for three months must produce the
    current period, not one three months stale."""
    start = timezone.now().replace(microsecond=0)
    end = add_months(start, 1)
    new_start, new_end = advance_period(
        start=start, end=end, interval=BillingInterval.MONTHLY, at=add_months(start, 5)
    )
    assert new_start == add_months(start, 5)
    assert new_end == add_months(start, 6)


def test_month_end_billing_dates_do_not_drift():
    """31 January plus one month is 28 February, not 3 March.

    Rolling over shifts every subsequent anniversary, and customers notice.
    """
    from datetime import datetime
    from datetime import timezone as dt_timezone

    jan31 = datetime(2026, 1, 31, 9, 0, tzinfo=dt_timezone.utc)
    assert add_months(jan31, 1).day == 28
    assert add_months(jan31, 12).day == 31


def test_a_one_time_interval_never_rolls():
    start = timezone.now()
    end = start + timedelta(days=365)
    assert advance_period(
        start=start,
        end=end,
        interval=BillingInterval.ONE_TIME,
        at=end + timedelta(days=1),
    ) == (start, end)


# --- suspension -----------------------------------------------------------


def test_suspension_makes_access_read_only(catalog, customer, billing_account, in_tenant):
    """Read-only, not lockout. The data stays visible and exportable and the
    pay-now path stays open."""
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()

    system.suspend_for_non_payment(
        ctx.organization_id,
        subscription_id=subscription.id,
        reason_code="dunning_exhausted",
    )

    with in_tenant(ctx):
        read_only = entitlements.check(ctx.organization_id, "crm")
        write = entitlements.check(ctx.organization_id, "crm", write=True)

    assert read_only.allowed
    assert not write.allowed
    assert write.reason_code.value == "read_only"


def test_system_operations_work_for_a_suspended_organization(
    catalog, customer, billing_account, in_tenant, organization_factory
):
    """The reason the system pathway exists at all.

    A suspended organization's writes are refused by R-10 under any tenant
    context, but its subscription must still be expirable - otherwise a
    non-paying customer's commercial record stays open forever.
    """
    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )

    with unscoped_access(
        reason="Test suspends the organization to exercise the system pathway",
        module="tests.subscriptions",
    ):
        Organization.objects.filter(id=ctx.organization_id).update(
            status=OrganizationStatus.SUSPENDED.value
        )

    counts = system.process_due_subscriptions(
        at=subscription.current_period_end + timedelta(minutes=1)
    )
    assert counts["failed"] == 0
    assert counts["cancellations_applied"] == 1


# --- ownership and isolation ---------------------------------------------


def test_another_tenants_subscription_is_not_addressable(
    catalog,
    customer,
    billing_account,
    in_tenant,
    organization_factory,
    tenant_ctx_factory,
    grant_permissions,
):
    """IDOR, closed by the scoped manager rather than by an ownership check
    the service could forget."""
    owner = customer["ctx"]
    billing_account(owner)
    with in_tenant(owner):
        services.purchase_plan(
            owner,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()

    org, membership, user = organization_factory()
    grant_permissions(org, membership, ("commercial.subscription.manage",))
    attacker = tenant_ctx_factory(org, membership, user, aal=2)

    with in_tenant(attacker), pytest.raises(CommercialRecordNotFound):
        services.cancel_subscription(
            attacker, subscription_id=subscription.id, idempotency_key=key()
        )


def test_the_optimistic_token_refuses_a_stale_write(
    catalog, customer, billing_account, in_tenant
):
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        stale = subscription.version - 1
        with pytest.raises(services.ConcurrentModification):
            services.cancel_subscription(
                ctx,
                subscription_id=subscription.id,
                idempotency_key=key(),
                expected_version=stale,
            )


# --- events and audit -----------------------------------------------------


def test_commercial_events_are_published_in_the_outbox(
    catalog, customer, billing_account, in_tenant
):
    from workeasy360.integration.events.models import OutboxEvent

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        # A trial, so activation is a real conversion rather than a
        # self-transition - which no longer publishes anything.
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )
        subscription = Subscription.objects.get()
        services.activate_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )

    published = list(
        OutboxEvent.objects.filter(organization_id=ctx.organization_id).values_list(
            "event_type", flat=True
        )
    )
    assert "commercial.subscription.item_added" in published
    assert "commercial.subscription.activated" in published
    assert "commercial.subscription.trial_ended" in published


def test_event_payloads_carry_identifiers_not_dumps(
    catalog, customer, billing_account, in_tenant
):
    """Stable identifiers, no money, no personal data.

    Events reach every subscribed consumer and are retained in the outbox and
    the dead-letter table; an amount in a payload is an amount disclosed
    downstream.
    """
    from workeasy360.integration.events.models import OutboxEvent

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )

    forbidden = {
        "amount",
        "amount_minor",
        "total_amount_minor",
        "unit_amount_minor",
        "price",
        "currency",
        "email",
        "billing_email",
    }
    for row in OutboxEvent.objects.filter(event_type__startswith="commercial."):
        payload = row.envelope["payload"]
        leaked = forbidden & set(payload)
        assert not leaked, f"{row.event_type} leaks {sorted(leaked)}"
        assert len(payload) < 12, f"{row.event_type} looks like an ORM dump"


def test_sensitive_operations_are_audited(catalog, customer, billing_account, in_tenant):
    from workeasy360.platform.audit.models import AuditEvent

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )
        actions = set(AuditEvent.objects.values_list("action", flat=True))

    assert {
        "commercial.subscription.plan_purchased",
        "commercial.subscription.plan_changed",
        "commercial.subscription.cancelled",
    } <= actions


def test_system_transitions_are_audited_without_an_actor(
    catalog, customer, billing_account, in_tenant
):
    from workeasy360.contracts.enums import ActorKind
    from workeasy360.platform.audit.models import AuditEvent

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
            with_trial=True,
        )

    system.process_due_subscriptions(at=timezone.now() + timedelta(days=15))

    with in_tenant(ctx):
        entry = AuditEvent.objects.get(action="commercial.subscription.trial_expired")

    assert entry.actor_kind == ActorKind.SYSTEM.value
    assert entry.actor_user_id is None
    assert entry.reason


# --- the commercial snapshot is history, not a cache ----------------------
#
# The pinned PlanVersion and PlanPrice are immutable by trigger. The
# denormalised copy on the item - the thing that exists so history survives a
# catalog row being superseded, closed, or restored from a different backup -
# was not, so a plain UPDATE could rewrite what a customer agreed to while
# the immutable catalog row sat beside it proving something else.


def test_a_subscription_items_commercial_snapshot_cannot_be_rewritten(
    catalog, customer, billing_account, in_tenant
):
    """``QuerySet.update()`` is the attack vector, as it is for pricing."""
    from django.db import transaction

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        original = SubscriptionItem.objects.get(id=item.id).unit_amount_minor

        for field, value in (
            ("unit_amount_minor", 1),
            ("total_amount_minor", 1),
            ("currency", "USD"),
            ("quantity", 99),
            ("plan_code", "something_else"),
            ("added_at", timezone.now()),
        ):
            with pytest.raises(Exception) as excinfo, transaction.atomic():
                SubscriptionItem.objects.filter(id=item.id).update(**{field: value})
            assert "cannot be changed" in str(excinfo.value).lower(), field

        assert SubscriptionItem.objects.get(id=item.id).unit_amount_minor == original


def test_closing_a_subscription_line_is_still_permitted(
    catalog, customer, billing_account, in_tenant
):
    """``removed_at`` is how a line is closed, so it stays mutable.

    The same shape as ``valid_to`` on a published price: freezing the column
    that records supersession would freeze supersession.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        item = services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        changed = services.change_plan(
            ctx,
            subscription_item_id=item.id,
            to_plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        assert changed.id != item.id
        assert SubscriptionItem.objects.get(id=item.id).removed_at is not None


def test_a_subscriptions_currency_cannot_drift_from_its_lines(
    catalog, customer, billing_account, in_tenant
):
    """Currency is a settlement fact fixed at creation.

    Changing it without every line's snapshot leaves a subscription priced in
    a currency it does not settle in - internally consistent on both sides,
    and detectable by nothing.
    """
    from django.db import transaction

    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        with pytest.raises(Exception) as excinfo, transaction.atomic():
            Subscription.objects.filter(id=subscription.id).update(currency="USD")
        assert "cannot be changed" in str(excinfo.value).lower()
        assert Subscription.objects.get(id=subscription.id).currency == "GBP"


def test_the_subscription_lifecycle_columns_stay_mutable(
    catalog, customer, billing_account, in_tenant
):
    """The freeze must not freeze the state machine it protects.

    Asserted explicitly, because a trigger that listed one column too many
    would break cancellation in a way only a lifecycle test would catch.
    """
    ctx = customer["ctx"]
    billing_account(ctx)
    with in_tenant(ctx):
        services.purchase_plan(
            ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()
        cancelled = services.cancel_subscription(
            ctx, subscription_id=subscription.id, idempotency_key=key()
        )
        assert cancelled.status == SubscriptionStatus.CANCELLED.value
        assert cancelled.cancel_at is not None
        assert cancelled.version > subscription.version

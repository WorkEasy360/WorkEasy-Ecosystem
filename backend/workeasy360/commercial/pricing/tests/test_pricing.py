"""Price books, price resolution, historical correctness and currency safety.

The invariant this suite exists to protect: **a historical price must never
change**. If today's CRM price moves from 1499 to 1699, everything that
referenced 1499 must keep saying 1499 - the subscription item, the
resolution for a past date, and the row itself.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.pricing import services
from workeasy360.commercial.pricing.domain import Tier, compute_amount
from workeasy360.commercial.pricing.models import ContractPrice, PlanPrice, PriceBook
from workeasy360.commercial.pricing.resolution import resolve_price
from workeasy360.contracts.enums import (
    BillingInterval,
    PriceBookChannel,
    PriceModel,
    PriceSource,
)
from workeasy360.contracts.errors import (
    CatalogValidationError,
    ImmutableRecord,
    PermissionDenied,
    PriceNotAvailable,
)
from workeasy360.contracts.values.money import CurrencyCode, CurrencyMismatch, Money

pytestmark = pytest.mark.django_db

GBP = CurrencyCode.GBP
INR = CurrencyCode.INR


# --- resolution order -----------------------------------------------------


def test_default_price_book_resolves(catalog, customer, in_tenant):
    ctx = customer["ctx"]
    with in_tenant(ctx):
        quote = resolve_price(
            plan_version_id=catalog["versions"]["crm_professional"].id,
            organization_id=ctx.organization_id,
            currency=GBP,
            interval=BillingInterval.MONTHLY,
            at=timezone.now(),
        )
    assert quote.total == Money(4999, GBP)
    assert quote.price_source is PriceSource.DEFAULT_BOOK
    assert "direct_gbp" in quote.reason


def test_regional_book_beats_the_default(catalog, platform_admin, customer, in_tenant):
    """Segmentation is additive: a new market is rows, not code."""
    admin = platform_admin["ctx"]
    with in_tenant(admin):
        book = services.create_price_book(
            admin,
            code="direct_gbp_ie",
            name="Ireland GBP",
            currency=GBP,
            region="IE",
            priority=50,
        )
        services.publish_price(
            admin,
            price_book_id=book.id,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=5499,
        )

    ctx = customer["ctx"]
    with in_tenant(ctx):
        quote = resolve_price(
            plan_version_id=catalog["versions"]["crm_professional"].id,
            organization_id=ctx.organization_id,
            currency=GBP,
            interval=BillingInterval.MONTHLY,
            region="IE",
            at=timezone.now(),
        )
    assert quote.total == Money(5499, GBP)
    assert quote.price_source is PriceSource.REGIONAL_BOOK


def test_contract_price_beats_every_book(catalog, platform_admin, customer, in_tenant):
    """Negotiated rates win, and are org-scoped rather than global rows."""
    admin_ctx = platform_admin["ctx"]
    customer_ctx = customer["ctx"]

    # Granted inside the customer's own tenant context: a ContractPrice is
    # data-plane and RLS applies. The acting identity carries the pricing
    # permission and internal staff status.
    from workeasy360.contracts.values.tenant import TenantContext

    acting = TenantContext(
        organization_id=customer_ctx.organization_id,
        membership_id=customer_ctx.membership_id,
        user_id=admin_ctx.user_id,
        aal=2,
    )
    grant_role_for(acting, customer)

    with in_tenant(acting):
        services.grant_contract_price(
            acting,
            organization_id=acting.organization_id,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=3500,
            currency=GBP,
            contract_ref="ACME-2026-01",
        )

    with in_tenant(customer_ctx):
        quote = resolve_price(
            plan_version_id=catalog["versions"]["crm_professional"].id,
            organization_id=customer_ctx.organization_id,
            currency=GBP,
            interval=BillingInterval.MONTHLY,
            at=timezone.now(),
        )
    assert quote.total == Money(3500, GBP)
    assert quote.price_source is PriceSource.CONTRACT
    assert "ACME-2026-01" in quote.reason


def grant_role_for(acting, customer_fixture):
    """Give the acting identity pricing rights inside the customer's org.

    A negotiated price is granted by internal staff but lives in the
    customer's tenant, so the acting context is a hybrid: the customer's
    organization, the staff member's user.
    """
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.authz.models import Role, RoleAssignment, RolePermission
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason="Test fixture seeds a pricing role for a staff actor",
            module="tests.pricing",
        ),
        organization_db_scope(acting.organization_id),
    ):
        role = Role.objects.create(
            organization_id=acting.organization_id,
            key="pricing-staff",
            name="Pricing",
            is_system=False,
        )
        RolePermission.objects.create(
            role=role, permission_id="commercial.pricing.manage"
        )
        RolePermission.objects.create(role=role, permission_id="commercial.pricing.view")
        assignment = RoleAssignment(
            organization_id=acting.organization_id,
            membership_id=acting.membership_id,
            role=role,
        )
        assignment.save()
    authz.bump_permission_version(acting.organization_id)


def test_a_missing_price_raises_rather_than_falling_back(catalog, customer, in_tenant):
    """Never a fallback currency, never a fallback region, never zero.

    A silent fallback bills someone incorrectly and nobody finds out until a
    customer complains.
    """
    ctx = customer["ctx"]
    with in_tenant(ctx), pytest.raises(PriceNotAvailable, match="catalog defect"):
        resolve_price(
            plan_version_id=catalog["versions"]["crm_professional"].id,
            organization_id=ctx.organization_id,
            currency=INR,
            interval=BillingInterval.MONTHLY,
            at=timezone.now(),
        )


def test_an_unpriced_interval_raises(catalog, customer, in_tenant):
    ctx = customer["ctx"]
    with in_tenant(ctx), pytest.raises(PriceNotAvailable):
        resolve_price(
            plan_version_id=catalog["versions"]["crm_professional"].id,
            organization_id=ctx.organization_id,
            currency=GBP,
            interval=BillingInterval.ANNUAL,
            at=timezone.now(),
        )


def test_partner_books_are_not_selectable_without_a_relationship(
    catalog, platform_admin, customer, in_tenant
):
    """A reseller rate must not reach a direct customer.

    ``commercial/partners`` does not exist yet, so the partner step of
    resolution is absent rather than stubbed. Selecting a partner book by
    region alone would be a revenue leak, not a missing feature.
    """
    admin = platform_admin["ctx"]
    with in_tenant(admin):
        book = services.create_price_book(
            admin,
            code="reseller_gold",
            name="Reseller Gold",
            currency=GBP,
            channel=PriceBookChannel.PARTNER,
            priority=1,
        )
        services.publish_price(
            admin,
            price_book_id=book.id,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=2500,
        )

    ctx = customer["ctx"]
    with in_tenant(ctx):
        quote = resolve_price(
            plan_version_id=catalog["versions"]["crm_professional"].id,
            organization_id=ctx.organization_id,
            currency=GBP,
            interval=BillingInterval.MONTHLY,
            at=timezone.now(),
        )
    assert quote.total == Money(4999, GBP), "partner pricing leaked to a direct customer"


# --- historical correctness ----------------------------------------------


def test_a_price_rise_does_not_rewrite_history(catalog, platform_admin, in_tenant):
    """1499 becomes 1699 today. Yesterday still resolves to 1499."""
    ctx = platform_admin["ctx"]
    now = timezone.now()
    yesterday = now - timedelta(days=1)
    tomorrow = now + timedelta(days=1)
    plan_version_id = catalog["versions"]["crm_starter"].id

    with in_tenant(ctx):
        original = PlanPrice.objects.get(id=catalog["prices"]["crm_starter"].id)
        services.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=plan_version_id,
            interval=BillingInterval.MONTHLY,
            amount_minor=1699,
            valid_from=tomorrow,
        )

        historical = resolve_price(
            plan_version_id=plan_version_id,
            organization_id=None,
            currency=GBP,
            interval=BillingInterval.MONTHLY,
            at=yesterday,
        )
        future = resolve_price(
            plan_version_id=plan_version_id,
            organization_id=None,
            currency=GBP,
            interval=BillingInterval.MONTHLY,
            at=tomorrow + timedelta(hours=1),
        )
        original.refresh_from_db()

    assert historical.total == Money(1499, GBP)
    assert historical.price_id == original.id
    assert future.total == Money(1699, GBP)
    # The original row is intact and merely closed.
    assert original.amount_minor == 1499
    assert original.valid_to == tomorrow


def test_a_published_price_cannot_be_edited_at_the_database(
    catalog, platform_admin, in_tenant
):
    """Editing a published amount makes an existing invoice irreproducible."""
    ctx = platform_admin["ctx"]
    price_id = catalog["prices"]["crm_starter"].id

    with in_tenant(ctx):
        with pytest.raises(Exception) as excinfo, transaction.atomic():
            PlanPrice.objects.filter(id=price_id).update(amount_minor=1)
        assert "immutable" in str(excinfo.value).lower()

        assert PlanPrice.objects.get(id=price_id).amount_minor == 1499


def test_closing_a_published_price_is_permitted(catalog, platform_admin, in_tenant):
    """``valid_to`` is the one edit a published price must still accept: it is
    how supersession is recorded."""
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        closed = services.close_price(ctx, price_id=catalog["prices"]["crm_starter"].id)
        assert closed.valid_to is not None
        with pytest.raises(ImmutableRecord):
            services.close_price(ctx, price_id=catalog["prices"]["crm_starter"].id)


def test_backdating_a_price_change_is_refused(catalog, platform_admin, in_tenant):
    """Retroactively changing what a past date resolves to is the one thing
    versioned pricing exists to prevent: an invoice raised last week would no
    longer be reproducible from the catalog."""
    ctx = platform_admin["ctx"]
    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="Backdating"):
        services.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=1,
            valid_from=timezone.now() - timedelta(days=1),
        )


def test_published_price_tiers_are_frozen(catalog, platform_admin, in_tenant):
    from workeasy360.commercial.pricing.models import PriceTier

    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        price = services.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=catalog["versions"]["books_professional"].id,
            interval=BillingInterval.ANNUAL,
            price_model=PriceModel.TIERED,
            tiers=[
                {"up_to": 10, "unit_amount_minor": 100},
                {"up_to": None, "unit_amount_minor": 80},
            ],
        )
        with pytest.raises(Exception) as excinfo, transaction.atomic():
            PriceTier.objects.filter(plan_price=price).update(unit_amount_minor=1)
        assert "published" in str(excinfo.value).lower()


# --- currency safety ------------------------------------------------------


def test_no_float_columns_exist_on_any_pricing_model():
    from django.db import models

    for model in (PriceBook, PlanPrice, ContractPrice):
        offenders = [
            field.name
            for field in model._meta.get_fields()
            if isinstance(field, models.FloatField | models.DecimalField)
        ]
        assert not offenders, f"{model.__name__} stores money imprecisely: {offenders}"


def test_amounts_must_be_integers(catalog, platform_admin, in_tenant):
    ctx = platform_admin["ctx"]
    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="integer"):
        services.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=catalog["versions"]["books_professional"].id,
            interval=BillingInterval.QUARTERLY,
            amount_minor=29.99,  # type: ignore[arg-type]
        )


def test_a_price_has_no_currency_of_its_own(catalog):
    """One source for currency, so there is nothing to disagree with.

    A price whose currency differs from its book's is a billing incident
    waiting for a customer to notice; the way to make that impossible is to
    have only one copy of the value.
    """
    columns = {
        field.attname
        for field in PlanPrice._meta.get_fields()
        if hasattr(field, "attname")
    }
    assert "currency" not in columns


def test_cross_currency_contract_price_is_refused_explicitly(
    catalog, platform_admin, customer, in_tenant
):
    """No unsupported FX. Refused loudly rather than converted silently."""
    from workeasy360.contracts.values.tenant import TenantContext

    admin_ctx = platform_admin["ctx"]
    customer_ctx = customer["ctx"]
    acting = TenantContext(
        organization_id=customer_ctx.organization_id,
        membership_id=customer_ctx.membership_id,
        user_id=admin_ctx.user_id,
        aal=2,
    )
    grant_role_for(acting, customer)

    with in_tenant(acting):
        services.grant_contract_price(
            acting,
            organization_id=acting.organization_id,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=250000,
            currency=INR,
        )

    with (
        in_tenant(customer_ctx),
        pytest.raises(PriceNotAvailable, match="Cross-currency"),
    ):
        resolve_price(
            plan_version_id=catalog["versions"]["crm_professional"].id,
            organization_id=customer_ctx.organization_id,
            currency=GBP,
            interval=BillingInterval.MONTHLY,
            at=timezone.now(),
        )


def test_money_refuses_to_mix_currencies():
    with pytest.raises(CurrencyMismatch):
        Money(1499, GBP) + Money(1499, INR)


def test_inr_amounts_stay_exact():
    """1499 rupees is 149900 paise, exactly, with no float in between."""
    assert Money.from_major(Decimal("1499"), INR).minor_units == 149900
    assert Money.from_major(Decimal("1699"), INR).minor_units == 169900


# --- price models ---------------------------------------------------------


def test_flat_and_per_seat_amounts():
    flat, _ = compute_amount(
        model=PriceModel.FLAT, currency=GBP, quantity=7, amount_minor=4999
    )
    per_seat, _ = compute_amount(
        model=PriceModel.PER_SEAT, currency=GBP, quantity=7, amount_minor=999
    )
    assert flat == Money(4999, GBP)
    assert per_seat == Money(6993, GBP)


def test_graduated_tiers_charge_each_band_at_its_own_rate():
    total, charges = compute_amount(
        model=PriceModel.TIERED,
        currency=GBP,
        quantity=25,
        amount_minor=None,
        tiers=(
            Tier(up_to=10, unit_amount_minor=100),
            Tier(up_to=20, unit_amount_minor=80),
            Tier(up_to=None, unit_amount_minor=50),
        ),
    )
    # 10 at 100 + 10 at 80 + 5 at 50
    assert total == Money(1000 + 800 + 250, GBP)
    assert [c.units for c in charges] == [10, 10, 5]


def test_volume_charges_every_unit_at_the_band_reached():
    """Distinct from graduated, and routinely confused. Conflating them
    produces invoices customers dispute."""
    total, charges = compute_amount(
        model=PriceModel.VOLUME,
        currency=GBP,
        quantity=25,
        amount_minor=None,
        tiers=(
            Tier(up_to=10, unit_amount_minor=100),
            Tier(up_to=None, unit_amount_minor=50),
        ),
    )
    assert total == Money(25 * 50, GBP)
    assert len(charges) == 1


def test_tiers_with_a_gap_are_refused():
    """A gap silently undercharges for the units nobody priced."""
    with pytest.raises(PriceNotAvailable, match="cover only"):
        compute_amount(
            model=PriceModel.TIERED,
            currency=GBP,
            quantity=50,
            amount_minor=None,
            tiers=(Tier(up_to=10, unit_amount_minor=100),),
        )


def test_usage_pricing_cannot_be_published(catalog, platform_admin, in_tenant):
    """A usage price with no metering resolves to an amount nobody can
    compute; returning zero would be an invoice that undercharges."""
    ctx = platform_admin["ctx"]
    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="metered"):
        services.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=catalog["versions"]["books_professional"].id,
            interval=BillingInterval.MONTHLY,
            price_model=PriceModel.USAGE,
            amount_minor=1,
        )


def test_a_quote_records_why_it_was_chosen(catalog, customer, in_tenant):
    """ "Why is this customer paying this?" must be answerable in production."""
    ctx = customer["ctx"]
    with in_tenant(ctx):
        quote = resolve_price(
            plan_version_id=catalog["versions"]["crm_starter"].id,
            organization_id=ctx.organization_id,
            currency=GBP,
            interval=BillingInterval.MONTHLY,
            at=timezone.now(),
        )
    snapshot = quote.as_snapshot()
    assert snapshot["price_id"] == str(quote.price_id)
    assert snapshot["price_source"] == PriceSource.DEFAULT_BOOK.value
    assert snapshot["currency"] == "GBP"
    assert snapshot["price_reason"]


# --- authorization --------------------------------------------------------


def test_a_customer_cannot_publish_prices(catalog, customer, in_tenant):
    ctx = customer["ctx"]
    with in_tenant(ctx), pytest.raises(PermissionDenied):
        services.publish_price(
            ctx,
            price_book_id=catalog["price_book"].id,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            interval=BillingInterval.QUARTERLY,
            amount_minor=1,
        )

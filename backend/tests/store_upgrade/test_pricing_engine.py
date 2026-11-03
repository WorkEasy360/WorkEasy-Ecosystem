"""ADR-056 pricing: promotional discount and set-up fee on the immutable price
row, effective dates, price versioning, and one discount function shared by
the resolver (what an order costs) and the list reads (what a Store shows)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, InternalError, ProgrammingError, transaction
from django.utils import timezone

from workeasy360.commercial.pricing import domain
from workeasy360.commercial.pricing import selectors as price_selectors
from workeasy360.commercial.pricing import services as pricing
from workeasy360.commercial.pricing.resolution import resolve_price
from workeasy360.contracts.enums import BillingInterval
from workeasy360.contracts.errors import CatalogValidationError, ImmutableRecord
from workeasy360.contracts.values.money import CurrencyCode, Money

pytestmark = pytest.mark.django_db

GBP = CurrencyCode.GBP
MONTHLY = BillingInterval.MONTHLY


def _resolve(catalog, plan="crm_professional", at=None):
    return resolve_price(
        plan_version_id=catalog["versions"][plan].id,
        organization_id=None,
        currency=GBP,
        interval=MONTHLY,
        at=at or timezone.now(),
    )


def _publish(ctx, catalog, plan="crm_professional", **terms):
    return pricing.publish_price(
        ctx,
        price_book_id=catalog["price_book"].id,
        plan_version_id=catalog["versions"][plan].id,
        interval=MONTHLY,
        **terms,
    )


def test_apply_discount_is_half_up_and_bounded():
    assert domain.apply_discount(Money(4999, GBP), 2000).minor_units == 3999
    assert domain.apply_discount(Money(4999, GBP), 0).minor_units == 4999
    assert domain.apply_discount(Money(4999, GBP), 10000).minor_units == 0
    assert domain.apply_discount(Money(1, GBP), 5000).minor_units == 1  # 0.5 half up
    with pytest.raises(Exception):
        domain.apply_discount(Money(100, GBP), 10001)


def test_a_discount_and_set_up_fee_are_resolved_and_snapshotted(catalog, pricing_scope):
    with pricing_scope() as ctx:
        _publish(ctx, catalog, amount_minor=4999, discount_bp=2000, setup_fee_minor=10000)
    quote = _resolve(catalog)
    assert quote.total.minor_units == 3999
    assert quote.list_minor == 4999
    snap = quote.as_snapshot()
    assert snap["list_total_minor"] == 4999
    assert snap["discount_bp"] == 2000
    assert snap["setup_fee_minor"] == 10000
    assert snap["total_amount_minor"] == 3999


def test_the_store_list_price_equals_the_checkout_price(catalog, pricing_scope):
    with pricing_scope() as ctx:
        _publish(ctx, catalog, amount_minor=4999, discount_bp=1500)
    version = catalog["versions"]["crm_professional"]
    listed = price_selectors.direct_list_prices(
        currency="GBP", plan_version_ids=[version.id]
    )
    shown = listed[(str(version.id), "monthly")]
    assert shown["amount_minor"] == _resolve(catalog).total.minor_units == 4249
    assert shown["list_amount_minor"] == 4999 and shown["discount_bp"] == 1500


def test_a_future_price_change_keeps_history_and_applies_from_its_date(
    catalog, pricing_scope
):
    now = timezone.now()
    starts = now + timedelta(days=7)
    with pricing_scope() as ctx:
        _publish(ctx, catalog, amount_minor=5999, valid_from=starts)
    # Before the change: the old price, after it: the new one. A month ago:
    # the original fact, untouched.
    assert _resolve(catalog, at=now + timedelta(days=1)).total.minor_units == 4999
    assert _resolve(catalog, at=starts + timedelta(hours=1)).total.minor_units == 5999
    assert _resolve(catalog, at=now - timedelta(days=10)).total.minor_units == 4999
    schedule = price_selectors.price_schedule(
        plan_version_ids=[catalog["versions"]["crm_professional"].id]
    )
    assert [(r["amount_minor"], r["state"]) for r in schedule] == [
        (4999, "current"),
        (5999, "scheduled"),
    ]


def test_cancelling_a_scheduled_change_restores_the_running_price(catalog, pricing_scope):
    starts = timezone.now() + timedelta(days=7)
    with pricing_scope() as ctx:
        scheduled = _publish(ctx, catalog, amount_minor=5999, valid_from=starts)
        pricing.cancel_scheduled_price(ctx, price_id=scheduled.id)
    assert _resolve(catalog, at=starts + timedelta(days=1)).total.minor_units == 4999
    current = catalog["prices"]["crm_professional"]
    current.refresh_from_db()
    assert current.valid_to is None


def test_a_started_price_cannot_be_withdrawn_only_closed(catalog, pricing_scope):
    with pricing_scope() as ctx, pytest.raises(ImmutableRecord):
        pricing.cancel_scheduled_price(ctx, price_id=catalog["prices"]["crm_starter"].id)


def test_effective_until_ends_a_promotion(catalog, pricing_scope):
    now = timezone.now()
    with pricing_scope() as ctx:
        _publish(
            ctx,
            catalog,
            amount_minor=4999,
            discount_bp=5000,
            valid_to=now + timedelta(days=3),
        )
    assert _resolve(catalog, at=now + timedelta(days=1)).total.minor_units == 2500
    # After the promotion nothing is on sale in that cycle until staff price it
    # again - never a guessed fallback.
    from workeasy360.contracts.errors import PriceNotAvailable

    with pytest.raises(PriceNotAvailable):
        _resolve(catalog, at=now + timedelta(days=4))


@pytest.mark.parametrize(
    "terms",
    [
        {"amount_minor": 100, "discount_bp": 10001},
        {"amount_minor": 100, "discount_bp": -1},
        {"amount_minor": 100, "setup_fee_minor": -5},
        {"amount_minor": 100, "discount_bp": True},
    ],
)
def test_bad_commercial_terms_are_refused(catalog, pricing_scope, terms):
    with pricing_scope() as ctx, pytest.raises(CatalogValidationError):
        _publish(ctx, catalog, **terms)


def test_a_price_change_cannot_be_backdated(catalog, pricing_scope):
    with pricing_scope() as ctx, pytest.raises(CatalogValidationError):
        _publish(
            ctx, catalog, amount_minor=5999, valid_from=timezone.now() - timedelta(days=2)
        )


def test_the_database_freezes_a_published_discount_and_fee(catalog, pricing_scope):
    with pricing_scope() as ctx:
        price = _publish(ctx, catalog, amount_minor=4999, discount_bp=1000)
    from workeasy360.commercial.pricing.models import PlanPrice

    for column, value in (("discount_bp", 9000), ("setup_fee_minor", 1)):
        with pytest.raises((IntegrityError, InternalError, ProgrammingError)):
            with transaction.atomic():
                PlanPrice.objects.filter(id=price.id).update(**{column: value})


def test_a_price_change_is_audited_with_the_previous_terms(catalog, pricing_scope):
    from tests.store_upgrade.conftest import audit_entry

    with pricing_scope() as ctx:
        price = _publish(ctx, catalog, amount_minor=5999, discount_bp=500)
    event = audit_entry("commercial.price.published", resource_id=price.id)
    assert event is not None
    assert event.changes["previous"] == [
        {"amount_minor": 4999, "discount_bp": 0, "setup_fee_minor": None}
    ]
    assert event.changes["amount_minor"] == 5999 and event.changes["discount_bp"] == 500

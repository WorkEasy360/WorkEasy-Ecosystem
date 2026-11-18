"""Fixtures for the Store's checkout (ADR-058): the catalog fixture with an
INR price book (CRM Professional at 8,399.00 a year, Books at 2,999.00), an
organization with an INR billing account, and staff who manage coupons."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.ecosystem.conftest import list_products
from tests.extension import conftest as _extension
from tests.extension.conftest import staff_scope, url

console_org = _extension.console_org
fake_payments = _extension.fake_payments
login = _extension.login
staff_login = _extension.staff_login

CRM_YEAR = 839900
BOOKS_YEAR = 299900


@pytest.fixture
def inr_catalog(catalog):
    from workeasy360.commercial.pricing import services as pricing
    from workeasy360.contracts.enums import BillingInterval, PriceBookChannel
    from workeasy360.contracts.values.money import CurrencyCode

    since = timezone.now() - timedelta(days=1)
    with staff_scope("platform.staff_pricing.manage") as ctx:
        book = pricing.create_price_book(
            ctx,
            code="direct_inr",
            name="Direct INR",
            currency=CurrencyCode.INR,
            channel=PriceBookChannel.DIRECT,
            valid_from=since,
        )
        for plan, amount in (
            ("crm_professional", CRM_YEAR),
            ("books_professional", BOOKS_YEAR),
        ):
            pricing.publish_price(
                ctx,
                price_book_id=book.id,
                plan_version_id=catalog["versions"][plan].id,
                interval=BillingInterval.ANNUAL,
                amount_minor=amount,
                valid_from=since,
            )
    list_products("crm", "books")
    return catalog


def open_shop(console_org, login, catalog, currency="INR"):
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": currency, "billing_email": "billing@example.com"},
        content_type="application/json",
    )
    assert opened.status_code == 200, opened.content
    return {**org, "client": owner, "catalog": catalog, "id": org["org"].id}


@pytest.fixture
def shop(console_org, login, inr_catalog, fake_payments):
    from django.core.cache import cache

    cache.clear()
    yield open_shop(console_org, login, inr_catalog)
    cache.clear()


@pytest.fixture
def coupon_admin(staff_login):
    client, _user = staff_login(
        "platform.staff_pricing.manage", "platform.staff_catalog.view"
    )
    return client

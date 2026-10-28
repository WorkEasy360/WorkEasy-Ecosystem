"""Fixtures for the complete Store + partner ecosystem (ADR-053)."""

from __future__ import annotations

import pytest

from tests.extension import conftest as _extension
from tests.phase7 import conftest as _phase7
from tests.store import conftest as _store

channel = _phase7.channel
console_org = _extension.console_org
fake_payments = _extension.fake_payments
login = _extension.login
staff_login = _extension.staff_login
shop = _store.shop


def list_products(*codes: str) -> None:
    """Put the catalog fixture's products on the customer Store (a listing and
    a slug), the way the Product Manager publishes one."""
    from workeasy360.commercial.catalog.models import Product

    for code in codes:
        Product.objects.filter(code=code).update(listing="listed", slug=code)


@pytest.fixture
def margin_ctx(staff_factory):
    _u, ctx = staff_factory("platform.staff_commission.manage")
    return ctx

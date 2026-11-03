"""Fixtures for the full-stack Store upgrade (ADR-056): the shared catalog
(CRM Starter 14.99, CRM Professional 49.99, Books Professional 29.99 GBP a
month), a customer organization ready to buy, a reseller channel with a 10%
programme commission, and staff contexts for catalog, pricing and commission."""

from __future__ import annotations

import pytest

from tests.ecosystem import conftest as _ecosystem
from tests.extension.conftest import staff_scope

channel = _ecosystem.channel
console_org = _ecosystem.console_org
fake_payments = _ecosystem.fake_payments
login = _ecosystem.login
staff_login = _ecosystem.staff_login
shop = _ecosystem.shop
list_products = _ecosystem.list_products

PRICING = ("platform.staff_catalog.manage", "platform.staff_pricing.manage")


@pytest.fixture
def pricing_scope():
    """Enter a staff scope that may publish prices: ``with pricing_scope() as ctx``."""
    return lambda: staff_scope(*PRICING)


@pytest.fixture
def commission_ctx(staff_factory):
    _u, ctx = staff_factory("platform.staff_commission.manage")
    return ctx


@pytest.fixture
def range_ctx(staff_factory):
    _u, ctx = staff_factory("platform.staff_pricing.manage")
    return ctx


def audit_entry(action: str, **match):
    """The latest audit entry for ``action`` (and ``resource_id`` when given)."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the commercial audit trail", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        rows = AuditEvent.unscoped.filter(action=action, **match)
        return rows.latest("occurred_at") if rows.exists() else None

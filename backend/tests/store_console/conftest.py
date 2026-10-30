"""Fixtures for the affiliate Store Console (ADR-055): CRM and Books on the
Store, an affiliate holding the referral capability but no promotion grant,
a 10% programme rule and a customer ready to buy."""

from __future__ import annotations

import pytest

from tests.affiliate_products import conftest as _affiliate
from tests.ecosystem.conftest import list_products

AFFILIATE = _affiliate.AFFILIATE
console_org = _affiliate.console_org
fake_payments = _affiliate.fake_payments
login = _affiliate.login
staff_login = _affiliate.staff_login
shop = _affiliate.shop
staff_ctx = _affiliate.staff_ctx

REQUEST = {
    "channel": "website",
    "website": "https://reviews.example.com",
    "audience": "Small agencies in India",
    "strategy": "A comparison article and a monthly newsletter feature.",
    "notes": "",
}


@pytest.fixture
def console(shop, partner_factory, staff_ctx):
    from workeasy360.commercial.commissions import rules

    list_products("crm", "books")
    affiliate = partner_factory(*AFFILIATE)
    rules.create_rule_version(
        staff_ctx, code="default", rate_bp=1000, reason="Programme default of ten percent"
    )
    return {**shop, "affiliate": affiliate, "staff": staff_ctx}


def ask(fx, product_code: str = "crm", **changes):
    from workeasy360.commercial.partners import promotion_requests

    return promotion_requests.submit(
        fx["affiliate"]["actor"](),
        body={**REQUEST, "product_code": product_code, **changes},
    )

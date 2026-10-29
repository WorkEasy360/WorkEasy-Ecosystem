"""Fixtures for product-aware affiliate links (ADR-054): a Store with CRM and
Books, an affiliate granted CRM only, a 10% programme rule and a customer
ready to buy."""

from __future__ import annotations

import uuid

import pytest

from tests.ecosystem.conftest import list_products
from tests.extension import conftest as _extension
from tests.extension.conftest import pay_order, place_order
from tests.store import conftest as _store

console_org = _extension.console_org
fake_payments = _extension.fake_payments
login = _extension.login
staff_login = _extension.staff_login
shop = _store.shop

AFFILIATE = (
    "referral.create",
    "referral.view",
    "commission.earn",
    "commission.view",
    "payout.view",
)


@pytest.fixture
def staff_ctx(staff_factory):
    _u, ctx = staff_factory(
        "platform.staff_partner.manage",
        "platform.staff_commission.manage",
        "platform.staff_catalog.manage",
    )
    return ctx


@pytest.fixture
def programme(shop, partner_factory, staff_ctx):
    """CRM and Books on the Store; an affiliate that may promote CRM only; a
    10% programme rule; the shop's organization as the customer."""
    from workeasy360.commercial.commissions import rules
    from workeasy360.commercial.partners import promotion

    list_products("crm", "books")
    affiliate = partner_factory(*AFFILIATE)
    grant = promotion.grant_promotion(
        staff_ctx, affiliate["partner"].id, product_code="crm"
    )
    rules.create_rule_version(
        staff_ctx, code="default", rate_bp=1000, reason="Programme default of ten percent"
    )
    return {**shop, "affiliate": affiliate, "grant": grant, "staff": staff_ctx}


def refer(fx, code_text: str) -> str:
    """The customer's owner follows ``code_text`` in their own browser and the
    referral converts their organization - the cookie path, no token in hand."""
    captured = fx["client"].post(
        "/api/v1/public/referrals", {"code": code_text}, content_type="application/json"
    )
    assert captured.status_code == 201, captured.content
    converted = fx["client"].post(
        "/api/v1/referrals/convert",
        {"organization_id": str(fx["id"])},
        content_type="application/json",
    )
    assert converted.json() == {"converted": True}
    return captured.json()["token"]


def buy(fx, plan: str = "crm_professional") -> dict:
    """Place an order for ``plan`` and pay it through the test provider's
    signed webhook. Returns the order and the provider reference paid."""
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt

    placed = place_order(
        fx["client"],
        fx["slug"],
        plan_version_id=str(fx["catalog"]["versions"][plan].id),
        _key=uuid.uuid4().hex,
    )
    assert placed.status_code == 201, placed.content
    order = pay_order(fx["client"], fx["slug"], fx["id"], placed.json())
    assert order["status"] == "fulfilled", order
    with organization_scope(fx["id"], reason="Test reads the paid attempt"):
        attempt = (
            PaymentAttempt.unscoped.filter(organization_id=fx["id"], status="succeeded")
            .order_by("-created_at")
            .first()
        )
    return {
        "order": order,
        "attempt_id": attempt.id,
        "reference": attempt.provider_reference,
    }


def sign_in(login, partner_fx):
    """A browser signed in as the partner's member (the factory gives no password)."""
    user = partner_fx["user"]
    user.set_password(_extension.PASSWORD)
    user.save(update_fields=["password"])
    return login(user)


def ledger(fx, **filters):
    from workeasy360.commercial.commissions.models import CommissionAccrual

    return list(
        CommissionAccrual.objects.filter(organization_id=fx["id"], **filters).order_by(
            "created_at"
        )
    )

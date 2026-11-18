"""Fixtures for the Digital Store on its own host (ADR-065): the Store checkout's
INR catalog and shop (``tests.store_checkout``), and helpers to pay an order the
way the Store's checkout does - in full or in part from the Platform Wallet."""

from __future__ import annotations

import uuid

import pytest

from tests.extension.conftest import url
from tests.store_checkout import conftest as _checkout

console_org = _checkout.console_org
fake_payments = _checkout.fake_payments
login = _checkout.login
staff_login = _checkout.staff_login
inr_catalog = _checkout.inr_catalog
shop = _checkout.shop

CRM_YEAR = _checkout.CRM_YEAR
#: CRM Professional for a year with 18% GST: 8,399.00 + 1,512.00.
CRM_YEAR_TOTAL = 991100


def fund(org_id, amount, *, currency="INR", kind="platform"):
    """Money in the organization's wallet, as a paid top-up would leave it."""
    from workeasy360.commercial.wallets import ledger

    wallet = ledger.ensure(
        owner_type="organization", owner_id=org_id, kind=kind, currency=currency
    )
    ledger.post(
        wallet.id,
        amount=amount,
        entry_type="top_up",
        reason="Test funds the wallet",
        idempotency_key=uuid.uuid4().hex,
    )
    wallet.refresh_from_db()
    return wallet


def balance(wallet):
    wallet.refresh_from_db()
    return wallet.balance


def place(shop, plan="crm_professional", **body):
    response = shop["client"].post(
        url(shop["slug"], "orders"),
        {
            "plan_version_id": str(shop["catalog"]["versions"][plan].id),
            "billing_interval": "annual",
            **body,
        },
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert response.status_code == 201, response.content
    return response.json()


def pay(shop, invoice_id, *, wallet=0, key=None, client=None, **extra):
    """Start paying the way the Store's checkout does."""
    return (client or shop["client"]).post(
        url(shop["slug"], f"billing/invoices/{invoice_id}/pay"),
        {"wallet_amount_minor": wallet},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
        **extra,
    )


def finish_at_provider(shop, attempt_id, outcome="succeeded"):
    """The provider's page: the hosted checkout is created, then completed (a
    signed callback through the real webhook path)."""
    from workeasy360.commercial.payments.checkout import create_checkout
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt

    attempt_id = uuid.UUID(str(attempt_id))
    assert create_checkout(shop["id"], attempt_id) == "awaiting_payment"
    with organization_scope(shop["id"], reason="Test reads the checkout reference"):
        reference = PaymentAttempt.unscoped.get(id=attempt_id).provider_reference
    done = shop["client"].post(
        "/api/v1/public/payments/test-checkout/complete",
        {"reference": reference, "organization_id": str(shop["id"]), "outcome": outcome},
        content_type="application/json",
    )
    assert done.status_code == 200, done.content


def attempts(shop, invoice_id):
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt

    with organization_scope(shop["id"], reason="Test reads the attempts"):
        return list(
            PaymentAttempt.unscoped.filter(invoice_id=invoice_id)
            .order_by("created_at")
            .values("provider_key", "status", "amount_minor", "return_portal")
        )


def holds(invoice_id):
    from workeasy360.commercial.wallets.models import WalletHold

    return list(
        WalletHold.objects.filter(invoice_id=invoice_id)
        .order_by("created_at")
        .values_list("status", "amount")
    )


@pytest.fixture
def store_host(settings):
    """Multi-portal mode with the Store's own host (ADR-065)."""
    origins = {
        "admin": "https://admin.w360.test",
        "partner": "https://partner.w360.test",
        "app": "https://app.w360.test",
        "auth": "https://auth.w360.test",
        "api": "https://api.w360.test",
        "store": "https://store.w360.test",
    }
    settings.W360_PORTAL_ORIGINS = origins
    settings.PORTALS_ENABLED = True
    settings.ALLOWED_HOSTS = [o.split("//", 1)[1] for o in origins.values()]
    settings.CSRF_TRUSTED_ORIGINS = [o for name, o in origins.items() if name != "api"]
    settings.W360_FRONTEND_URL = origins["app"]
    return origins

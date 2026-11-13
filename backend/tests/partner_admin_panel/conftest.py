"""Fixtures for the Partner Admin Panel of 08 November 2026 (ADR-063)."""

from __future__ import annotations

import uuid

import pytest

from tests.extension import conftest as _extension
from tests.phase7 import conftest as _phase7

channel = _phase7.channel
http_login = _phase7.http_login
partner_client = _phase7.partner_client
staff_client = _phase7.staff_client
fake_payments = _extension.fake_payments

PASSWORD = _phase7.PASSWORD


@pytest.fixture
def wallet_channel(channel, staff_factory):
    """The Phase 7 reseller with an approved purchase price: a 20% reseller
    margin rule, so its cost for a 49.99 plan is 39.99."""
    from workeasy360.commercial.commissions import margins

    _u, margin_ctx = staff_factory("platform.staff_commission.manage")
    margins.set_margin(
        margin_ctx, experience="reseller", discount_bp=2000, reason="Launch terms"
    )
    _u, wallet_ctx = staff_factory("platform.staff_wallet.manage")
    channel.update(margin_ctx=margin_ctx, wallet_ctx=wallet_ctx)
    return channel


def plan_id(fx, code: str = "crm_professional") -> str:
    return str(fx["catalog"]["versions"][code].id)


def top_up(fx, amount_minor: int, currency: str = "GBP") -> None:
    """Money into the reseller's Platform Wallet the way staff record a bank
    transfer (the only top-up before ADR-063, kept)."""
    from workeasy360.commercial.wallets import admin as wallet_admin
    from workeasy360.commercial.wallets import ledger

    row = ledger.ensure(
        owner_type="partner",
        owner_id=fx["partner"].id,
        kind="platform",
        currency=currency,
    )
    wallet_admin.record_offline_top_up(
        fx["wallet_ctx"],
        row.id,
        amount=amount_minor,
        reference=f"NEFT-{uuid.uuid4().hex[:8]}",
        reason="Bank transfer received",
        idempotency_key=uuid.uuid4().hex,
    )


def balance(fx, currency: str = "GBP") -> int:
    from workeasy360.commercial.wallets import api as wallets

    found = [
        w for w in wallets.partner_wallets(fx["partner"].id) if w["currency"] == currency
    ]
    return found[0]["balance_minor"] if found else 0


def in_org(org_id):
    """Read a customer's tenant rows in a test."""
    from contextlib import ExitStack

    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    stack = ExitStack()
    stack.enter_context(
        unscoped_access(reason="Test reads a customer's rows", module="tests")
    )
    stack.enter_context(organization_db_scope(org_id))
    return stack

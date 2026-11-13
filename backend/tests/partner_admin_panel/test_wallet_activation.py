"""Plan Activation paid from the reseller's Platform Wallet (ADR-063, the
specification's 6.3 and 8): the approved purchase price, the exact amount shown,
one transaction, no overdraft, no second charge, the customer never invoiced."""

from __future__ import annotations

import uuid

import pytest

from tests.partner_admin_panel.conftest import balance, in_org, plan_id, top_up
from tests.phase7.support import provision
from workeasy360.commercial.controlplane import partner_wallet
from workeasy360.commercial.partners import customers
from workeasy360.commercial.wallets.ledger import InsufficientBalance
from workeasy360.contracts.errors import (
    CommercialError,
    OrderNotAllowed,
    PartnerRecordNotFound,
    PartnerValidationError,
    PermissionDenied,
)

pytestmark = pytest.mark.django_db

COST = 3999  # 49.99 list, 20% reseller margin rule


def _body(fx, **extra):
    return {"plan_version_id": plan_id(fx), "billing_interval": "monthly", **extra}


def _lines(org_id):
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    with in_org(org_id):
        return list(
            SubscriptionItem.unscoped.filter(organization_id=org_id).values_list(
                "product_code", "removed_at"
            )
        )


def _orders(org_id):
    from workeasy360.commercial.payments.models import Order

    with in_org(org_id):
        return list(Order.unscoped.filter(organization_id=org_id))


def _collection(org_id):
    from workeasy360.commercial.billing.models import BillingAccount

    with in_org(org_id):
        return BillingAccount.unscoped.get(organization_id=org_id).collection_mode


def test_the_quote_shows_the_purchase_price_and_the_wallet_deduction(wallet_channel):
    fx = wallet_channel
    managed, _owner = provision(fx)
    top_up(fx, 10_000)
    quote = partner_wallet.activation_quote(
        fx["actor"](), managed.organization_id, _body(fx)
    )
    assert quote["charge_minor"] == COST
    assert quote["charge"]["partner_cost_minor"] == COST
    assert quote["charge"]["period"]["prorated"] is False
    assert quote["wallet"] == {
        "currency": "GBP",
        "balance_minor": 10_000,
        "charge_minor": COST,
        "balance_after_minor": 10_000 - COST,
        "sufficient": True,
    }
    assert quote["payer"]["usable"] is True
    # A quote writes nothing.
    assert _lines(managed.organization_id) == []
    assert balance(fx) == 10_000


def test_activation_debits_the_wallet_records_the_order_and_starts_the_plan(
    wallet_channel,
):
    from workeasy360.commercial.payments import invoicing

    fx = wallet_channel
    managed, _owner = provision(fx)
    top_up(fx, 10_000)
    result = partner_wallet.activate(
        fx["actor"](),
        managed.organization_id,
        _body(fx, expected_charge_minor=COST),
        idempotency_key=uuid.uuid4().hex,
    )
    order = result["order"]
    assert order["status"] == "fulfilled"
    assert order["paid_by"] == "partner_wallet"
    assert order["channel"] == "partner"
    assert result["wallet"] == {"currency": "GBP", "balance_minor": 10_000 - COST}
    assert balance(fx) == 10_000 - COST
    assert [code for code, _ in _lines(managed.organization_id)] == ["crm"]
    assert _collection(managed.organization_id) == "partner_wallet"
    # The customer is never invoiced for it.
    assert invoicing.ensure_invoices(managed.organization_id) == []
    # The order names the wallet transaction, and the transaction the order.
    row = _orders(managed.organization_id)[0]
    snapshot = row.price_snapshot["partner_wallet"]
    assert snapshot["charged_minor"] == COST
    page = partner_wallet.transactions(fx["actor"]())
    debit = page["items"][0]
    assert debit["entry_type"] == "subscription"
    assert debit["amount_minor"] == -COST
    assert debit["id"] == snapshot["wallet_entry_id"]
    assert debit["order"]["number"] == order["number"]
    assert debit["customer"]["organization_id"] == str(managed.organization_id)


def test_insufficient_balance_charges_nothing_and_grants_nothing(wallet_channel):
    fx = wallet_channel
    managed, _owner = provision(fx)
    top_up(fx, COST - 1)
    with pytest.raises(InsufficientBalance):
        partner_wallet.activate(
            fx["actor"](),
            managed.organization_id,
            _body(fx, expected_charge_minor=COST),
            idempotency_key=uuid.uuid4().hex,
        )
    assert balance(fx) == COST - 1
    assert _lines(managed.organization_id) == []
    assert _orders(managed.organization_id) == []


def test_a_retried_activation_returns_the_same_order_and_charges_once(wallet_channel):
    fx = wallet_channel
    managed, _owner = provision(fx)
    top_up(fx, 20_000)
    key = uuid.uuid4().hex
    first = partner_wallet.activate(
        fx["actor"](),
        managed.organization_id,
        _body(fx, expected_charge_minor=COST),
        idempotency_key=key,
    )
    second = partner_wallet.activate(
        fx["actor"](),
        managed.organization_id,
        _body(fx, expected_charge_minor=COST),
        idempotency_key=key,
    )
    assert first["order"]["order_id"] == second["order"]["order_id"]
    assert balance(fx) == 20_000 - COST
    assert len(_orders(managed.organization_id)) == 1


def test_a_different_amount_than_the_one_shown_is_refused(wallet_channel):
    fx = wallet_channel
    managed, _owner = provision(fx)
    top_up(fx, 20_000)
    with pytest.raises(CommercialError) as caught:
        partner_wallet.activate(
            fx["actor"](),
            managed.organization_id,
            _body(fx, expected_charge_minor=COST - 100),
            idempotency_key=uuid.uuid4().hex,
        )
    assert caught.value.code == "price_changed"
    assert balance(fx) == 20_000
    assert _lines(managed.organization_id) == []


def test_without_an_approved_purchase_price_the_wallet_is_not_offered(channel):
    # The Phase 7 channel has a commission rule but no margin rule: no price.
    managed, _owner = provision(channel)
    with pytest.raises(partner_wallet.NoPartnerPrice):
        partner_wallet.activation_quote(
            channel["actor"](), managed.organization_id, _body(channel)
        )


def test_a_customer_that_pays_directly_is_not_moved_to_the_wallet(wallet_channel):
    from tests.phase7.support import sell

    fx = wallet_channel
    managed, _owner = provision(fx)
    sell(fx, managed.organization_id)  # an automatic-collection subscription
    top_up(fx, 20_000)
    quote = partner_wallet.activation_quote(
        fx["actor"](), managed.organization_id, _body(fx)
    )
    assert quote["payer"] == {
        "usable": False,
        "reason": "pays_directly",
        "message": quote["payer"]["message"],
    }
    with pytest.raises(partner_wallet.WalletNotUsable):
        partner_wallet.activate(
            fx["actor"](),
            managed.organization_id,
            _body(fx, expected_charge_minor=COST),
            idempotency_key=uuid.uuid4().hex,
        )
    assert balance(fx) == 20_000


def test_another_partners_customer_cannot_be_activated(wallet_channel, partner_factory):
    from tests.phase7.support import CAPS

    fx = wallet_channel
    managed, _owner = provision(fx)
    other = partner_factory(*CAPS)
    with pytest.raises(PartnerRecordNotFound):
        partner_wallet.activation_quote(
            other["actor"](), managed.organization_id, _body(fx)
        )
    with pytest.raises(PartnerRecordNotFound):
        partner_wallet.activate(
            other["actor"](),
            managed.organization_id,
            _body(fx, expected_charge_minor=COST),
            idempotency_key=uuid.uuid4().hex,
        )


def test_a_role_without_selling_cannot_activate(wallet_channel):
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.access import partner_actor

    fx = wallet_channel
    managed, _owner = provision(fx)
    finance = fx["user_factory"]()
    services.add_member(
        fx["staff_ctx"], fx["partner"].id, email=finance.email, role="finance"
    )
    ctx = partner_actor(user_id=finance.id, partner_id=fx["partner"].id)
    with pytest.raises(PermissionDenied):
        partner_wallet.activate(
            ctx,
            managed.organization_id,
            _body(fx, expected_charge_minor=COST),
            idempotency_key=uuid.uuid4().hex,
        )
    # Finance reads the wallet, and may not add money to it.
    assert partner_wallet.wallet(ctx)["top_up"]["allowed"] is False


def test_a_suspended_partner_cannot_activate(wallet_channel):
    from workeasy360.commercial.partners import services
    from workeasy360.contracts.enums import PartnerStatus

    fx = wallet_channel
    managed, _owner = provision(fx)
    top_up(fx, 20_000)
    services.set_partner_status(
        fx["staff_ctx"],
        fx["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Under review",
    )
    with pytest.raises(PermissionDenied):
        partner_wallet.activate(
            fx["actor"](),
            managed.organization_id,
            _body(fx, expected_charge_minor=COST),
            idempotency_key=uuid.uuid4().hex,
        )
    assert balance(fx) == 20_000


def test_a_wallet_paid_customer_is_refused_every_unpaid_path(wallet_channel):
    from tests.phase7.support import sell

    fx = wallet_channel
    managed, _owner = provision(fx)
    top_up(fx, 20_000)
    partner_wallet.activate(
        fx["actor"](),
        managed.organization_id,
        _body(fx, expected_charge_minor=COST),
        idempotency_key=uuid.uuid4().hex,
    )
    # The ordinary sale, and a central order, would grant what nobody paid for.
    with pytest.raises(PartnerValidationError):
        sell(fx, managed.organization_id, plan="crm_professional")
    with pytest.raises(OrderNotAllowed):
        customers.place_customer_order(
            fx["actor"](),
            managed.organization_id,
            idempotency_key=uuid.uuid4().hex,
            plan_version_id=plan_id(fx),
        )


def test_the_customers_own_members_cannot_change_a_partner_paid_plan():
    """The subscription services' guard: a customer member is refused on a
    partner-wallet subscription; the partner and staff are not."""
    from types import SimpleNamespace

    from workeasy360.commercial.subscriptions import services as subscriptions
    from workeasy360.contracts.enums import PrincipalKind

    paid_by_partner = SimpleNamespace(collection_mode="partner_wallet")
    member = SimpleNamespace(principal=PrincipalKind.MEMBER)
    with pytest.raises(CommercialError):
        subscriptions._assert_member_may_change(
            member, paid_by_partner, operation="purchase_plan"
        )
    for principal in (PrincipalKind.PARTNER, PrincipalKind.STAFF):
        subscriptions._assert_member_may_change(
            SimpleNamespace(principal=principal), paid_by_partner, operation="change_plan"
        )
    subscriptions._assert_member_may_change(
        member, SimpleNamespace(collection_mode="automatic"), operation="change_plan"
    )


def test_a_wallet_paid_sale_earns_the_reseller_no_margin(wallet_channel):
    from workeasy360.commercial.commissions import accrual
    from workeasy360.commercial.commissions.models import CommissionAccrual

    fx = wallet_channel
    managed, _owner = provision(fx)
    top_up(fx, 20_000)
    partner_wallet.activate(
        fx["actor"](),
        managed.organization_id,
        _body(fx, expected_charge_minor=COST),
        idempotency_key=uuid.uuid4().hex,
    )
    accrual.accrue_for_organization(managed.organization_id)
    assert not CommissionAccrual.objects.filter(partner_id=fx["partner"].id).exists()

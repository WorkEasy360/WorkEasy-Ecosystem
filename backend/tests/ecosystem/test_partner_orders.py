"""A reseller's sale is a central Order (ADR-053, Store Working Flow page 5):
priced by the server from the partner's book, only for what the partner may
sell, and granting nothing before payment - or at once for an approved sale
(a partner-billed account). The partner sees the order with its earning."""

from __future__ import annotations

import uuid

import pytest

from tests.phase7.support import provision
from workeasy360.commercial.partners import customers
from workeasy360.contracts.errors import OrderNotAllowed

pytestmark = pytest.mark.django_db


def _plan(channel, code="crm_professional"):
    return str(channel["catalog"]["versions"][code].id)


def _subscription_items(org_id):
    from workeasy360.commercial.subscriptions.models import SubscriptionItem
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads a customer's lines", module="tests"),
        organization_db_scope(org_id),
    ):
        return list(
            SubscriptionItem.unscoped.filter(organization_id=org_id).values_list(
                "product_code", flat=True
            )
        )


def _partner_billed(org_id):
    from workeasy360.commercial.billing.models import BillingAccount
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test makes an account partner-billed", module="tests"),
        organization_db_scope(org_id),
    ):
        BillingAccount.unscoped.filter(organization_id=org_id).update(
            collection_mode="partner_billed"
        )


def test_the_quote_is_the_servers_price_from_the_partners_book(channel):
    managed, _owner = provision(channel)
    quote = customers.quote_customer_order(
        channel["actor"](), managed.organization_id, plan_version_id=_plan(channel)
    )
    assert quote["recurring_amount_minor"] == 4999
    assert quote["channel"] == "partner"
    assert quote["requires_payment"] is True


def test_a_card_customer_pays_before_anything_is_granted(channel):
    managed, _owner = provision(channel)
    order = customers.place_customer_order(
        channel["actor"](),
        managed.organization_id,
        idempotency_key=uuid.uuid4().hex,
        plan_version_id=_plan(channel),
    )
    assert order["status"] == "pending_payment"
    assert order["channel"] == "partner"
    assert _subscription_items(managed.organization_id) == []


def test_an_approved_sale_is_fulfilled_at_once_and_earns(channel):
    from workeasy360.commercial.commissions.models import CommissionAccrual

    managed, _owner = provision(channel)
    _partner_billed(managed.organization_id)
    order = customers.place_customer_order(
        channel["actor"](),
        managed.organization_id,
        idempotency_key=uuid.uuid4().hex,
        plan_version_id=_plan(channel),
    )
    assert order["status"] == "fulfilled"
    assert _subscription_items(managed.organization_id) == ["crm"]
    accrual = CommissionAccrual.objects.get(organization_id=managed.organization_id)
    assert accrual.partner_id == channel["partner"].id
    assert accrual.attribution_kind == "managed"


def test_a_partner_cannot_order_a_product_it_was_not_granted(channel):
    managed, _owner = provision(channel)
    with pytest.raises(OrderNotAllowed) as refused:
        customers.place_customer_order(
            channel["actor"](),
            managed.organization_id,
            idempotency_key=uuid.uuid4().hex,
            plan_version_id=_plan(channel, "books_professional"),
        )
    assert refused.value.context["reason"] == "not_for_sale"


def test_a_partner_cannot_order_for_another_partners_customer(channel, partner_factory):
    from tests.phase7.support import CAPS
    from workeasy360.contracts.errors import PartnerRecordNotFound

    other = partner_factory(*CAPS)
    managed, _owner = provision(channel)
    with pytest.raises(PartnerRecordNotFound):
        customers.place_customer_order(
            other["actor"](),
            managed.organization_id,
            idempotency_key=uuid.uuid4().hex,
            plan_version_id=_plan(channel),
        )


def test_the_partner_sees_the_order_with_what_it_earned(channel):
    from workeasy360.commercial.controlplane import partner_portal

    managed, _owner = provision(channel)
    _partner_billed(managed.organization_id)
    customers.place_customer_order(
        channel["actor"](),
        managed.organization_id,
        idempotency_key=uuid.uuid4().hex,
        plan_version_id=_plan(channel),
    )
    page = partner_portal.orders(channel["actor"]())
    (row,) = page.items
    assert row["sold_by_you"] is True and row["channel"] == "partner"
    assert row["plans"] == ["CRM Professional"]
    assert row["payment_status"] == "nothing_due"
    assert row["earning"]["model"] == "commission"
    assert row["earning"]["amount_minor"] == 500
    assert "subscription_item_ids" not in row


def test_the_browser_cannot_send_a_price(channel):
    """The order view's allowlist drops anything that is not an identifier."""
    from workeasy360.interfaces.api.v1 import partner_views

    body = {
        "plan_version_id": _plan(channel),
        "amount_due_minor": 1,
        "recurring_amount_minor": 1,
        "partner_id": str(uuid.uuid4()),
        "currency": "USD",
    }
    assert partner_views._order_request(body) == {"plan_version_id": _plan(channel)}

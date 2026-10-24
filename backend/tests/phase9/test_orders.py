"""Pay-first orders (Phase 9): nothing is granted before the money arrives,
the price is the server's, the first period is never billed twice, and every
race, replay and late payment ends with exactly one outcome."""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from tests.extension.conftest import (
    buy_directly,
    pay_order,
    place_and_pay,
    place_order,
    url,
)

pytestmark = pytest.mark.django_db


# --- fixtures and helpers ------------------------------------------------------------


@pytest.fixture
def shop(console_org, login, catalog, fake_payments):
    """An organization with a billing account (GBP) and its owner signed in."""
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP", "billing_email": "billing@example.com"},
        content_type="application/json",
    )
    assert opened.status_code == 200, opened.content
    return {**org, "client": owner, "catalog": catalog, "id": org["org"].id}


def _version(shop, plan):
    return str(shop["catalog"]["versions"][plan].id)


def _entitled(shop, product):
    """What a product asking the gate would be told - in the organization's
    scope, as the entitlement endpoint asks it."""
    from workeasy360.contracts.ports.entitlements import get_gate
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with tenant_context(routing_context(shop["id"])), organization_db_scope(shop["id"]):
        return get_gate().check(shop["id"], product).allowed


def _subscriptions(shop):
    return shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"]


def _invoices(shop):
    return shop["client"].get(url(shop["slug"], "billing/invoices")).json()["results"]


def _order(shop, order):
    return shop["client"].get(url(shop["slug"], f"orders/{order['order_id']}")).json()


# --- the journey --------------------------------------------------------------------


def test_nothing_is_granted_until_the_order_is_paid(shop):
    placed = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_professional")
    )
    assert placed.status_code == 201, placed.content
    order = placed.json()
    assert order["status"] == "pending_payment" and order["requires_payment"]
    assert (order["amount_due_minor"], order["currency"]) == (4999, "GBP")
    assert order["number"].startswith("ORD-")
    # No subscription, no entitlement - only the order and its invoice.
    assert _subscriptions(shop) == []
    assert not _entitled(shop, "crm")
    invoice = next(i for i in _invoices(shop) if i["order_id"] == order["order_id"])
    assert (invoice["status"], invoice["total_minor"]) == ("open", 4999)

    paid = pay_order(shop["client"], shop["slug"], shop["id"], order)
    assert paid["status"] == "fulfilled" and paid["paid_at"] and paid["fulfilled_at"]
    subscription = _subscriptions(shop)[0]
    assert subscription["status"] == "active"
    assert [i["plan_code"] for i in subscription["items"]] == ["crm_professional"]
    assert _entitled(shop, "crm")


def test_the_first_period_is_billed_once(shop):
    from workeasy360.commercial.payments.invoicing import ensure_invoices

    order = place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=_version(shop, "crm_professional"),
    )
    assert order["status"] == "fulfilled"
    # The order's invoice took the subscription's first period, so neither
    # the listener nor the clock issues another for it.
    assert ensure_invoices(shop["id"]) == []
    invoices = _invoices(shop)
    assert len(invoices) == 1 and invoices[0]["status"] == "paid"
    assert invoices[0]["period_start"] == _subscriptions(shop)[0]["current_period_start"]


def test_the_next_period_is_invoiced_in_full_from_the_subscription(shop):
    from workeasy360.commercial.payments.invoicing import ensure_invoices
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=_version(shop, "crm_professional"),
    )
    subscription = _subscriptions(shop)[0]
    after_period = timezone.datetime.fromisoformat(
        subscription["current_period_end"]
    ) + timedelta(minutes=1)
    process_due_subscriptions(at=after_period)
    # The roll notifies the invoicing listener; the clock finds nothing more.
    assert ensure_invoices(shop["id"], at=after_period) == []
    invoices = _invoices(shop)
    assert len(invoices) == 2
    newest = invoices[0]
    assert (newest["status"], newest["total_minor"], newest["order_id"]) == (
        "open",
        4999,
        None,
    )


def test_a_line_added_to_a_running_subscription_pays_the_prorated_rest(shop):
    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=_version(shop, "crm_professional"),
    )
    quote = (
        shop["client"]
        .post(
            url(shop["slug"], "orders/quote"),
            {"plan_version_id": _version(shop, "books_professional")},
            content_type="application/json",
        )
        .json()
    )
    assert quote["prorated"] and quote["requires_payment"]
    assert 0 < quote["amount_due_minor"] <= 2999
    assert quote["recurring_amount_minor"] == 2999
    order = place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=_version(shop, "books_professional"),
    )
    assert order["status"] == "fulfilled"
    assert order["amount_due_minor"] == quote["amount_due_minor"]
    items = {i["plan_code"] for i in _subscriptions(shop)[0]["items"]}
    assert items == {"crm_professional", "books_professional"}
    assert _entitled(shop, "books")


def test_a_bundle_order_grants_every_product_it_includes(shop):
    order = place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        bundle_version_id=str(shop["catalog"]["bundle_version"].id),
    )
    assert order["status"] == "fulfilled" and order["amount_due_minor"] == 6999
    assert sorted(order["product_codes"]) == ["books", "crm"]
    assert _entitled(shop, "crm") and _entitled(shop, "books")


# --- trials and activation -------------------------------------------------------------


def test_a_trial_needs_no_payment_and_activation_does(shop):
    trial = place_order(
        shop["client"],
        shop["slug"],
        plan_version_id=_version(shop, "crm_starter"),
        with_trial=True,
    )
    assert trial.status_code == 201, trial.content
    body = trial.json()
    assert (body["status"], body["requires_payment"], body["is_trial"]) == (
        "fulfilled",
        False,
        True,
    )
    subscription = _subscriptions(shop)[0]
    assert subscription["status"] == "trialing" and _entitled(shop, "crm")
    assert _invoices(shop) == []

    activation = place_order(
        shop["client"], shop["slug"], activate_subscription_id=subscription["id"]
    )
    assert activation.status_code == 201, activation.content
    pending = activation.json()
    assert pending["requires_payment"] and pending["amount_due_minor"] == 1499
    # Still a trial until the money arrives.
    assert _subscriptions(shop)[0]["status"] == "trialing"
    paid = pay_order(shop["client"], shop["slug"], shop["id"], pending)
    assert paid["status"] == "fulfilled"
    converted = _subscriptions(shop)[0]
    assert converted["status"] == "active"
    invoice = next(i for i in _invoices(shop) if i["order_id"] == pending["order_id"])
    assert invoice["period_start"] == converted["current_period_start"]


def test_a_second_trial_of_the_same_product_is_a_paid_order(shop, console_org):
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    place_order(
        shop["client"],
        shop["slug"],
        plan_version_id=_version(shop, "crm_starter"),
        with_trial=True,
    )
    process_due_subscriptions(at=timezone.now() + timedelta(days=30))  # the trial expires
    again = place_order(
        shop["client"],
        shop["slug"],
        plan_version_id=_version(shop, "crm_starter"),
        with_trial=True,
    )
    assert again.status_code == 201, again.content
    assert again.json()["is_trial"] is False and again.json()["requires_payment"] is True


# --- refusals ------------------------------------------------------------------------------


def test_the_price_currency_and_status_are_the_servers(shop):
    placed = place_order(
        shop["client"],
        shop["slug"],
        plan_version_id=_version(shop, "crm_professional"),
        amount_minor=1,
        amount_due_minor=1,
        currency="INR",
        status="fulfilled",
        price_snapshot={"total_amount_minor": 1},
    )
    body = placed.json()
    assert (body["amount_due_minor"], body["currency"], body["status"]) == (
        4999,
        "GBP",
        "pending_payment",
    )


def test_only_what_explore_lists_can_be_ordered(shop, staff_login):
    from workeasy360.commercial.catalog.models import Plan

    Plan.objects.filter(code="crm_starter").update(is_public=False)
    private = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_starter")
    )
    assert (private.status_code, private.json()["reason"]) == (409, "not_for_sale")
    staff, _ = staff_login("platform.staff_catalog.manage")
    staff.patch(
        f"/api/v1/admin/catalog/products/{shop['catalog']['products']['books'].id}",
        {"listing": "hidden"},
        content_type="application/json",
    )
    hidden = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "books_professional")
    )
    assert hidden.json()["reason"] == "not_for_sale"
    unknown = place_order(shop["client"], shop["slug"], plan_version_id=str(uuid.uuid4()))
    assert unknown.json()["reason"] == "not_for_sale"


def test_one_order_awaits_payment_at_a_time(shop):
    first = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_professional")
    ).json()
    second = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "books_professional")
    )
    assert (second.status_code, second.json()["reason"]) == (409, "order_pending")
    assert second.json()["order_id"] == first["order_id"]
    # Not even a trial while one is pending.
    trial = place_order(
        shop["client"],
        shop["slug"],
        plan_version_id=_version(shop, "crm_starter"),
        with_trial=True,
    )
    assert trial.json()["reason"] == "order_pending"
    cancelled = shop["client"].post(
        url(shop["slug"], f"orders/{first['order_id']}/cancel")
    )
    assert cancelled.json()["status"] == "cancelled"
    invoice = next(i for i in _invoices(shop) if i["order_id"] == first["order_id"])
    assert invoice["status"] == "void"
    assert (
        place_order(
            shop["client"],
            shop["slug"],
            plan_version_id=_version(shop, "books_professional"),
        ).status_code
        == 201
    )


def test_a_product_already_held_cannot_be_ordered_again(shop):
    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=_version(shop, "crm_starter"),
    )
    again = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_professional")
    )
    assert (again.status_code, again.json()["reason"]) == (409, "already_owned")


def test_nothing_can_be_bought_while_an_invoice_is_overdue(shop):
    from workeasy360.commercial.payments.dunning import process_due

    buy_directly(shop, shop["catalog"]["versions"]["crm_starter"].id)
    process_due(at=timezone.now() + timedelta(hours=1))  # the period invoice is overdue
    refused = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "books_professional")
    )
    assert (refused.status_code, refused.json()["reason"]) == (409, "outstanding_balance")


def test_billing_must_be_set_up_first(console_org, login, catalog):
    org = console_org()
    owner = login(org["owner"])
    refused = place_order(
        owner, org["slug"], plan_version_id=str(catalog["versions"]["crm_starter"].id)
    )
    assert (refused.status_code, refused.json()["reason"]) == (
        409,
        "billing_account_required",
    )


def test_placing_needs_the_buying_permission_and_reading_the_billing_one(shop, login):
    member = login(shop["add"]("member")["user"])
    assert (
        place_order(
            member, shop["slug"], plan_version_id=_version(shop, "crm_starter")
        ).status_code
        == 403
    )
    assert member.get(url(shop["slug"], "orders")).status_code == 403
    billing = login(shop["add"]("billing_manager")["user"])
    assert (
        place_order(
            billing, shop["slug"], plan_version_id=_version(shop, "crm_starter")
        ).status_code
        == 403
    )
    assert billing.get(url(shop["slug"], "orders")).status_code == 200


def test_another_organizations_order_is_a_404(shop, console_org, login):
    order = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_starter")
    ).json()
    other = console_org()
    stranger = login(other["owner"])
    for path in (f"orders/{order['order_id']}", f"orders/{order['order_id']}/cancel"):
        method = stranger.post if path.endswith("cancel") else stranger.get
        response = method(url(other["slug"], path))
        assert response.status_code == 404, path
    # And the invoice behind it cannot be paid from there either.
    pay = stranger.post(
        url(other["slug"], f"billing/invoices/{order['invoice_id']}/pay"),
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert pay.status_code == 404


def test_a_customer_cannot_choose_its_billing_terms(console_org, login):
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP", "collection_mode": "partner_billed", "billing_region": "IN"},
        content_type="application/json",
    )
    assert opened.status_code == 200
    account = owner.get(url(org["slug"], "billing-account")).json()["billing_account"]
    assert (account["collection_mode"], account["billing_region"]) == ("automatic", "")


def test_staff_set_sales_led_terms_and_such_orders_are_fulfilled_at_once(
    shop, staff_login
):
    staff, _ = staff_login("platform.staff_payment.manage")
    target = f"/api/v1/admin/customers/{shop['id']}/billing-terms"
    no_reason = staff.post(
        target, {"collection_mode": "invoice"}, content_type="application/json"
    )
    assert no_reason.status_code == 400
    set_terms = staff.post(
        target,
        {"collection_mode": "invoice", "reason": "Enterprise agreement, net 14 terms"},
        content_type="application/json",
    )
    assert set_terms.status_code == 200, set_terms.content
    order = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_professional")
    ).json()
    assert (order["status"], order["requires_payment"]) == ("fulfilled", False)
    assert _entitled(shop, "crm")
    invoice = _invoices(shop)[0]
    assert invoice["status"] == "open" and invoice["order_id"] is None


# --- idempotency, expiry, late money ---------------------------------------------------------


def test_a_replayed_key_returns_the_same_order_and_a_changed_one_conflicts(shop):
    key = uuid.uuid4().hex
    body = {"plan_version_id": _version(shop, "crm_professional")}
    first = place_order(shop["client"], shop["slug"], _key=key, **body).json()
    again = place_order(shop["client"], shop["slug"], _key=key, **body).json()
    assert again["order_id"] == first["order_id"]
    changed = place_order(
        shop["client"],
        shop["slug"],
        _key=key,
        plan_version_id=_version(shop, "books_professional"),
    )
    assert changed.status_code == 409


def test_an_unpaid_order_expires_and_late_money_is_refunded(shop, settings):
    from workeasy360.commercial.payments.dunning import process_due
    from workeasy360.commercial.payments.models import Refund
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    order = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_professional")
    ).json()
    started = (
        shop["client"]
        .post(
            url(shop["slug"], f"billing/invoices/{order['invoice_id']}/pay"),
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        )
        .json()
    )
    from workeasy360.commercial.payments.checkout import create_checkout

    create_checkout(shop["id"], uuid.UUID(started["attempt_id"]))
    process_due(at=timezone.now() + timedelta(hours=settings.ORDER_TTL_HOURS + 1))
    assert _order(shop, order)["status"] == "expired"
    # The customer finishes the provider's page anyway.
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt

    with organization_scope(shop["id"], reason="Test reads the checkout reference"):
        reference = PaymentAttempt.unscoped.get(
            id=started["attempt_id"]
        ).provider_reference
    done = shop["client"].post(
        "/api/v1/public/payments/test-checkout/complete",
        {
            "reference": reference,
            "organization_id": str(shop["id"]),
            "outcome": "succeeded",
        },
        content_type="application/json",
    )
    assert done.status_code == 200
    assert _order(shop, order)["status"] == "expired"
    assert not _entitled(shop, "crm")
    with (
        unscoped_access(reason="Test reads automatic refunds", module="tests"),
        organization_db_scope(shop["id"]),
    ):
        refund = Refund.unscoped.get(organization_id=shop["id"])
    assert (refund.amount_minor, refund.idempotency_key) == (
        4999,
        f"auto:{started['attempt_id']}",
    )


def test_a_payment_without_an_amount_never_settles():
    from types import SimpleNamespace

    from workeasy360.commercial.payments import settlement

    attempt = SimpleNamespace(
        id=uuid.uuid4(), status="awaiting_payment", amount_minor=4999, currency="GBP"
    )
    outcome = settlement.record_success(
        attempt,
        amount_minor=None,
        currency="GBP",
        payment_id="p",
        after=settlement.AfterCommit(),
    )
    assert outcome == settlement.MISMATCH


def test_an_order_that_cannot_be_fulfilled_after_payment_is_refunded(shop):
    from workeasy360.commercial.payments.models import Refund
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    order = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_professional")
    ).json()
    # Meanwhile the organization got CRM some other way (staff, a partner).
    buy_directly(shop, shop["catalog"]["versions"]["crm_starter"].id)
    paid = pay_order(shop["client"], shop["slug"], shop["id"], order)
    assert (paid["status"], paid["failure_reason"]) == ("failed", "already_owned")
    with (
        unscoped_access(reason="Test reads automatic refunds", module="tests"),
        organization_db_scope(shop["id"]),
    ):
        assert Refund.unscoped.filter(
            organization_id=shop["id"], amount_minor=4999
        ).exists()


def test_a_lost_fulfilment_is_redriven_by_the_clock(shop, monkeypatch):
    from workeasy360.commercial.payments import orders
    from workeasy360.commercial.payments.dunning import process_due

    real = orders.fulfil
    monkeypatch.setattr(orders, "fulfil", lambda *a, **k: "lost")
    order = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_professional")
    ).json()
    paid = pay_order(shop["client"], shop["slug"], shop["id"], order)
    assert paid["status"] == "paid" and not _entitled(shop, "crm")
    monkeypatch.setattr(orders, "fulfil", real)
    totals = process_due(at=timezone.now() + timedelta(minutes=5))
    assert totals["fulfilled_orders"] == 1
    assert _order(shop, order)["status"] == "fulfilled" and _entitled(shop, "crm")


def test_orders_are_listed_newest_first_with_a_cursor(shop):
    for plan in ("crm_starter",):
        place_and_pay(
            shop["client"], shop["slug"], shop["id"], plan_version_id=_version(shop, plan)
        )
    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=_version(shop, "books_professional"),
    )
    first = shop["client"].get(url(shop["slug"], "orders"), {"limit": 1}).json()
    assert len(first["results"]) == 1 and first["has_more"]
    second = (
        shop["client"]
        .get(url(shop["slug"], "orders"), {"limit": 1, "cursor": first["next_cursor"]})
        .json()
    )
    assert first["results"][0]["order_id"] != second["results"][0]["order_id"]
    assert first["results"][0]["created_at"] >= second["results"][0]["created_at"]


def test_staff_see_orders_across_customers(shop, staff_login, login):
    place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_starter")
    )
    staff, _ = staff_login("platform.staff_payment.view")
    listed = staff.get("/api/v1/admin/orders").json()["results"]
    assert listed[0]["organization_id"] == str(shop["id"])
    assert listed[0]["status"] == "pending_payment"
    assert "billing@example.com" not in str(listed)
    plain, _ = staff_login("platform.staff_customer.view")
    assert plain.get("/api/v1/admin/orders").status_code == 403
    assert login(shop["owner"]).get("/api/v1/admin/orders").status_code == 403


# --- races (real threads, real commits) -------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_simultaneous_orders_leave_exactly_one_awaiting_payment(shop, login):
    from workeasy360.commercial.payments.models import Order
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    clients = [login(shop["owner"]) for _ in range(4)]
    barrier = threading.Barrier(4)
    statuses: list[int] = []

    def place(c, plan):
        barrier.wait()
        statuses.append(
            place_order(c, shop["slug"], plan_version_id=_version(shop, plan)).status_code
        )
        connection.close()

    plans = ["crm_professional", "books_professional", "crm_starter", "crm_professional"]
    threads = [
        threading.Thread(target=place, args=(c, p))
        for c, p in zip(clients, plans, strict=True)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [201, 409, 409, 409]
    with (
        unscoped_access(reason="Test counts pending orders", module="tests"),
        organization_db_scope(shop["id"]),
    ):
        assert (
            Order.unscoped.filter(
                organization_id=shop["id"], status="pending_payment"
            ).count()
            == 1
        )


@pytest.mark.django_db(transaction=True)
def test_redelivered_payment_events_fulfil_once(shop):
    from django.test import Client

    from workeasy360.commercial.payments import credentials
    from workeasy360.commercial.payments.checkout import create_checkout
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt
    from workeasy360.commercial.payments.providers import fake
    from workeasy360.commercial.subscriptions.models import SubscriptionItem
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    order = place_order(
        shop["client"], shop["slug"], plan_version_id=_version(shop, "crm_professional")
    ).json()
    started = (
        shop["client"]
        .post(
            url(shop["slug"], f"billing/invoices/{order['invoice_id']}/pay"),
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        )
        .json()
    )
    create_checkout(shop["id"], uuid.UUID(started["attempt_id"]))
    with organization_scope(shop["id"], reason="Test reads the checkout reference"):
        attempt = PaymentAttempt.unscoped.get(id=started["attempt_id"])
    body = fake.event_body(
        id="evt_order_race",
        type="payment.succeeded",
        reference=attempt.provider_reference,
        payment_id="fake_pay_race",
        amount_minor=attempt.amount_minor,
        currency=attempt.currency,
        attempt_id=str(attempt.id),
        organization_id=str(shop["id"]),
    )
    signature = fake.sign(body, credentials.webhook_secret("TEST", adapter="fake"))
    barrier = threading.Barrier(4)

    def deliver():
        barrier.wait()
        Client().post(
            "/api/v1/public/payments/webhooks/test-provider",
            body,
            content_type="application/json",
            **{f"HTTP_{fake.SIGNATURE_HEADER.upper().replace('-', '_')}": signature},
        )
        connection.close()

    threads = [threading.Thread(target=deliver) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _order(shop, order)["status"] == "fulfilled"
    with (
        unscoped_access(reason="Test counts subscription lines", module="tests"),
        organization_db_scope(shop["id"]),
    ):
        assert SubscriptionItem.unscoped.filter(organization_id=shop["id"]).count() == 1

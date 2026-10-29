"""The Store's multi-product cart (ADR-052; Store Working Flow PDF pages 3, 9, 10):
several products, one order, one payment - then one subscription line and one
entitlement per product. Nothing is granted before the payment is verified, a
failed payment grants nothing, and a cart that cannot be fulfilled in full is
fulfilled not at all (and refunded)."""

from __future__ import annotations

import uuid

import pytest

from tests.extension.conftest import pay_order, place_and_pay, place_order, url

pytestmark = pytest.mark.django_db


def _v(shop, plan):
    return str(shop["catalog"]["versions"][plan].id)


def _cart(shop, *plans, **extra):
    return {"cart_items": [{"plan_version_id": _v(shop, p)} for p in plans], **extra}


def _quote(shop, body):
    return shop["client"].post(
        url(shop["slug"], "orders/quote"), body, content_type="application/json"
    )


def _entitled(shop, product):
    from workeasy360.contracts.ports.entitlements import get_gate
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with tenant_context(routing_context(shop["id"])), organization_db_scope(shop["id"]):
        return get_gate().check(shop["id"], product).allowed


def _subscriptions(shop):
    return shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"]


def _invoice(shop, order):
    return next(
        i
        for i in shop["client"]
        .get(url(shop["slug"], "billing/invoices"))
        .json()["results"]
        if i["order_id"] == order["order_id"]
    )


def _invoice_lines(shop, order):
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import InvoiceLine

    with organization_scope(shop["id"], reason="Test reads an order's invoice lines"):
        return list(
            InvoiceLine.unscoped.filter(
                organization_id=shop["id"], invoice__order_id=order["order_id"]
            )
            .order_by("product_code")
            .values("product_code", "plan_code", "amount_minor", "subscription_item_id")
        )


# --- the journey ------------------------------------------------------------------------


def test_two_products_one_order_one_payment_two_entitlements(shop):
    quoted = _quote(shop, _cart(shop, "crm_professional", "books_professional"))
    assert quoted.status_code == 200, quoted.content
    q = quoted.json()
    assert q["kind"] == "cart" and q["requires_payment"]
    assert q["amount_due_minor"] == 4999 + 2999  # each line priced by the server
    assert {(line["product_code"], line["amount_due_minor"]) for line in q["lines"]} == {
        ("crm", 4999),
        ("books", 2999),
    }

    placed = place_order(
        shop["client"],
        shop["slug"],
        **_cart(shop, "crm_professional", "books_professional"),
    )
    assert placed.status_code == 201, placed.content
    order = placed.json()
    assert order["status"] == "pending_payment" and order["amount_due_minor"] == 7998
    # Nothing granted before the money arrives.
    assert _subscriptions(shop) == []
    assert not _entitled(shop, "crm") and not _entitled(shop, "books")
    assert _invoice(shop, order)["total_minor"] == 7998
    assert [
        (r["product_code"], r["amount_minor"]) for r in _invoice_lines(shop, order)
    ] == [
        ("books", 2999),
        ("crm", 4999),
    ]

    paid = pay_order(shop["client"], shop["slug"], shop["id"], order)
    assert paid["status"] == "fulfilled"
    subscription = _subscriptions(shop)[0]
    assert sorted(i["plan_code"] for i in subscription["items"]) == [
        "books_professional",
        "crm_professional",
    ]
    assert _entitled(shop, "crm") and _entitled(shop, "books")
    # Each invoice line names its own subscription line; each order line too.
    items = {i["plan_code"]: i["id"] for i in subscription["items"]}
    lines = _invoice_lines(shop, order)
    assert {str(r["subscription_item_id"]) for r in lines} == set(items.values())
    assert {line["subscription_item_id"] for line in paid["lines"]} == set(items.values())
    assert "snapshot" not in paid["lines"][0]  # the price snapshot stays server-side


def test_a_failed_payment_grants_nothing(shop):
    order = place_order(
        shop["client"],
        shop["slug"],
        **_cart(shop, "crm_professional", "books_professional"),
    ).json()
    after = pay_order(shop["client"], shop["slug"], shop["id"], order, outcome="failed")
    assert after["status"] == "pending_payment"
    assert _subscriptions(shop) == []
    assert not _entitled(shop, "crm") and not _entitled(shop, "books")


def test_a_cart_that_cannot_be_fulfilled_in_full_is_fulfilled_not_at_all(
    shop, monkeypatch
):
    """The second line fails after payment: the first is rolled back with it,
    the order fails and the payment is refunded - never half a cart."""
    from workeasy360.commercial.payments import refunds
    from workeasy360.commercial.subscriptions import system
    from workeasy360.contracts.errors import CommercialError

    real = system.add_paid_line
    calls = []

    def second_fails(*args, **kwargs):
        calls.append(kwargs["plan_version_id"])
        if len(calls) == 2:
            raise CommercialError("Gone since the order.", reason="already_owned")
        return real(*args, **kwargs)

    refunded = []
    monkeypatch.setattr(system, "add_paid_line", second_fails)
    monkeypatch.setattr(
        refunds,
        "refund_automatically",
        lambda org, attempt, reason: refunded.append(attempt),
    )
    order = place_order(
        shop["client"],
        shop["slug"],
        **_cart(shop, "crm_professional", "books_professional"),
    ).json()
    after = pay_order(shop["client"], shop["slug"], shop["id"], order)
    assert after["status"] == "failed" and after["failure_reason"] == "already_owned"
    assert len(calls) == 2 and len(refunded) == 1
    assert _subscriptions(shop) == []
    assert not _entitled(shop, "crm") and not _entitled(shop, "books")


def test_fulfilment_runs_once(shop):
    from workeasy360.commercial.payments import orders

    order = place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        **_cart(shop, "crm_starter", "books_professional"),
    )
    assert order["status"] == "fulfilled"
    assert orders.fulfil(shop["id"], uuid.UUID(order["order_id"])) == "noop"
    assert len(_subscriptions(shop)[0]["items"]) == 2


def test_a_replayed_key_returns_the_same_cart_order(shop):
    key = uuid.uuid4().hex
    body = _cart(shop, "crm_professional", "books_professional")
    first = place_order(shop["client"], shop["slug"], _key=key, **body).json()
    again = place_order(shop["client"], shop["slug"], _key=key, **body).json()
    assert first["order_id"] == again["order_id"]
    other = place_order(
        shop["client"],
        shop["slug"],
        _key=key,
        **_cart(shop, "crm_starter", "books_professional"),
    )
    assert other.status_code in (400, 409)


def test_a_cart_joins_a_running_subscription_prorated_per_line(shop, third_product):
    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=_v(shop, "books_professional"),
    )
    body = {
        "cart_items": [
            {"plan_version_id": _v(shop, "crm_starter"), "quantity": 2},
            {"plan_version_id": str(third_product.id)},
        ]
    }
    q = _quote(shop, body).json()
    assert q["prorated"] is True
    by_code = {line["product_code"]: line for line in q["lines"]}
    # Each line is exactly what that plan alone would be quoted at.
    alone = _quote(
        shop, {"plan_version_id": _v(shop, "crm_starter"), "quantity": 2}
    ).json()
    assert by_code["crm"]["recurring_amount_minor"] == alone["recurring_amount_minor"]
    assert by_code["crm"]["amount_due_minor"] == alone["amount_due_minor"]
    assert by_code["notes"]["recurring_amount_minor"] == 999
    assert all(
        0 < line["amount_due_minor"] <= line["recurring_amount_minor"]
        for line in q["lines"]
    )
    assert q["amount_due_minor"] == sum(line["amount_due_minor"] for line in q["lines"])
    order = place_and_pay(shop["client"], shop["slug"], shop["id"], **body)
    assert order["status"] == "fulfilled"
    subscription = _subscriptions(shop)[0]
    assert sorted(i["plan_code"] for i in subscription["items"]) == [
        "books_professional",
        "crm_starter",
        "notes_team",
    ]
    assert _entitled(shop, "notes")


# --- what a cart refuses ------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"cart_items": []},
        {"cart_items": "crm"},
        {"cart_items": [{"plan_version_id": "not-an-id"}, {"plan_version_id": "x"}]},
        {"cart_items": [{"plan_version_id": str(uuid.uuid4()), "price": 1}] * 2},
        {"cart_items": [{"plan_version_id": str(uuid.uuid4()), "quantity": 0}] * 2},
    ],
)
def test_a_malformed_cart_is_refused(shop, body):
    assert _quote(shop, body).status_code == 400


def test_a_cart_needs_two_distinct_plans_of_two_products(shop):
    one = _quote(shop, _cart(shop, "crm_professional"))
    assert one.status_code == 400
    twice = _quote(shop, _cart(shop, "crm_professional", "crm_professional"))
    assert twice.status_code == 400
    same_product = _quote(shop, _cart(shop, "crm_starter", "crm_professional"))
    assert same_product.status_code in (400, 409)
    assert same_product.json()["reason"] == "duplicate_product"


def test_a_cart_never_buys_a_product_already_held(shop):
    place_and_pay(
        shop["client"], shop["slug"], shop["id"], plan_version_id=_v(shop, "crm_starter")
    )
    held = _quote(shop, _cart(shop, "crm_professional", "books_professional"))
    assert held.status_code in (400, 409)
    assert held.json()["reason"] == "already_owned"


def test_a_cart_starts_no_trial_and_names_its_plans_only_in_cart_items(shop):
    trial = _quote(
        shop, _cart(shop, "crm_starter", "books_professional", with_trial=True)
    )
    assert (
        trial.status_code in (400, 409) and trial.json()["reason"] == "trial_not_in_cart"
    )
    mixed = _quote(
        shop,
        _cart(
            shop,
            "crm_starter",
            "books_professional",
            plan_version_id=_v(shop, "crm_starter"),
        ),
    )
    assert mixed.status_code == 400


def test_a_plan_not_for_sale_is_refused(shop):
    body = {
        "cart_items": [
            {"plan_version_id": _v(shop, "crm_starter")},
            {"plan_version_id": str(uuid.uuid4())},
        ]
    }
    refused = _quote(shop, body)
    assert refused.status_code in (400, 403, 409)
    assert refused.json()["reason"] == "not_for_sale"


def test_the_browser_cannot_name_a_price(shop):
    """Unknown keys never reach the order service (the view's allowlist), and
    the total is the server's whatever the body says."""
    body = _cart(shop, "crm_professional", "books_professional")
    body.update({"amount_due_minor": 1, "currency": "USD"})
    placed = place_order(shop["client"], shop["slug"], **body)
    assert placed.status_code == 201, placed.content
    assert placed.json()["amount_due_minor"] == 7998
    assert placed.json()["currency"] == "GBP"


def test_only_someone_who_may_buy_can_place_a_cart(shop, login):
    client = login(shop["add"]("member")["user"])
    body = _cart(shop, "crm_professional", "books_professional")
    assert place_order(client, shop["slug"], **body).status_code == 403
    assert _quote({**shop, "client": client}, body).status_code == 403

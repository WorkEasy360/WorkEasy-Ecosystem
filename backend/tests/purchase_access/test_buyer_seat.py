"""ADR-073, G2: the member who pays for a per-seat product can open it.

Before, a paid order granted the organization the product and its seats, but
nobody held one: the buyer's own launch answered ``no_seat`` until an
administrator - usually the buyer - assigned a seat by hand. Fulfilment now
seats the buyer, exactly as a trial seats its starter: idempotently, never
beyond the pool, and never someone who is no longer a member.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from django.db import connection

from tests.purchase_access.conftest import (
    audit_actions,
    buy,
    launch,
    place_order,
    seat_holders,
    url,
    version_of,
)

pytestmark = pytest.mark.django_db


def test_buying_a_per_seat_product_lets_the_buyer_open_it(shop):
    assert seat_holders(shop["id"], "crm") == set()
    buy(shop, "crm_professional")

    assert seat_holders(shop["id"], "crm") == {shop["membership"].id}
    apps = {
        a["product_code"]: a
        for a in shop["client"].get(url(shop["slug"], "apps")).json()["apps"]
    }
    assert apps["crm"]["state"] == "active" and apps["crm"]["can_launch"] is True
    opened = launch(shop, "crm")
    assert opened.status_code == 200, opened.content
    assert opened.json()["redirect_url"].startswith("http://localhost:9101/launch")
    # Explained in the customer's own trail, as the system's doing.
    (entry,) = audit_actions(shop["id"], "commercial.order.buyer_seat_assigned")
    assert entry.changes["product_code"] == "crm" and entry.actor_kind == "system"


def test_a_second_product_bought_later_also_seats_the_buyer(shop):
    buy(shop, "crm_professional")
    buy(shop, "desk_team")

    assert seat_holders(shop["id"], "crm") == {shop["membership"].id}
    assert seat_holders(shop["id"], "desk") == {shop["membership"].id}
    assert launch(shop, "desk").status_code == 200
    assert launch(shop, "crm").status_code == 200


def test_a_product_licensed_per_organization_takes_no_seat(shop):
    buy(shop, "books_professional")
    assert seat_holders(shop["id"], "books") == set()
    assert launch(shop, "books").status_code == 200


def test_a_bundle_seats_the_buyer_in_its_per_seat_products_only(shop):
    from tests.extension.conftest import place_and_pay

    order = place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        bundle_version_id=str(shop["catalog"]["bundle_version"].id),
    )
    assert order["status"] == "fulfilled"
    assert seat_holders(shop["id"], "crm") == {shop["membership"].id}
    assert seat_holders(shop["id"], "books") == set()


def test_seating_is_idempotent_and_never_beyond_the_pool(shop):
    from workeasy360.commercial.payments.orders import seat_the_buyer
    from workeasy360.platform.entitlements.services import seats

    buy(shop, "crm_starter")  # two seats; the buyer takes one
    owner = shop["owner"].id
    assert seat_the_buyer(shop["id"], user_id=owner, product_codes=["crm"]) == []
    assert seat_holders(shop["id"], "crm") == {shop["membership"].id}

    # The buyer gives their seat away and the pool fills up: a re-run of the
    # seating (a re-driven fulfilment) takes nothing from anyone.
    first, second = shop["add"]("member"), shop["add"]("member")
    from tests.purchase_access.conftest import in_scope

    with in_scope(shop["id"]):
        seats.release_seat(
            shop["id"], pool_key="crm", membership_id=shop["membership"].id
        )
        seats.assign_seat(
            shop["id"], pool_key="crm", membership_id=first["membership"].id
        )
        seats.assign_seat(
            shop["id"], pool_key="crm", membership_id=second["membership"].id
        )
    assert seat_the_buyer(shop["id"], user_id=owner, product_codes=["crm"]) == []
    assert seat_holders(shop["id"], "crm") == {
        first["membership"].id,
        second["membership"].id,
    }
    # And the order is no less fulfilled for it: the full pool only means an
    # administrator decides who gets a seat.
    response = launch(shop, "crm")
    assert response.status_code == 409 and response.json()["state"] == "no_seat"


def test_a_buyer_who_is_no_longer_a_member_is_not_seated(shop):
    """The order names who placed it; by the time the payment is confirmed
    that person may have been removed or suspended. Neither gets a seat."""
    from workeasy360.commercial.payments.orders import seat_the_buyer

    buy(shop, "crm_professional")
    removed, suspended = shop["add"]("member"), shop["add"]("member")
    answer = shop["client"].delete(
        url(shop["slug"], f"memberships/{removed['membership'].id}")
    )
    assert answer.status_code == 200, answer.content
    answer = shop["client"].post(
        url(shop["slug"], f"members/{suspended['membership'].id}/suspend"),
        content_type="application/json",
    )
    assert answer.status_code == 200, answer.content

    for person in (removed, suspended):
        assert (
            seat_the_buyer(shop["id"], user_id=person["user"].id, product_codes=["crm"])
            == []
        )
    assert seat_holders(shop["id"], "crm") == {shop["membership"].id}


def test_a_member_who_already_holds_a_seat_keeps_it(shop):
    from tests.purchase_access.conftest import in_scope
    from workeasy360.commercial.payments.orders import seat_the_buyer
    from workeasy360.platform.entitlements.models import SeatAssignment

    buy(shop, "crm_professional")
    with in_scope(shop["id"]):
        before = SeatAssignment.unscoped.get(
            organization_id=shop["id"], membership_id=shop["membership"].id
        )
    seat_the_buyer(shop["id"], user_id=shop["owner"].id, product_codes=["crm", "crm"])
    with in_scope(shop["id"]):
        after = SeatAssignment.unscoped.filter(
            organization_id=shop["id"], membership_id=shop["membership"].id
        )
        assert [row.id for row in after] == [before.id]


@pytest.mark.django_db(transaction=True)
def test_redelivered_payment_events_seat_the_buyer_once(shop):
    """Four simultaneous deliveries of the same signed webhook: one fulfilment,
    one seat."""
    from django.test import Client

    from workeasy360.commercial.payments import credentials
    from workeasy360.commercial.payments.checkout import create_checkout
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt
    from workeasy360.commercial.payments.providers import fake

    order = place_order(
        shop["client"], shop["slug"], plan_version_id=version_of(shop, "crm_professional")
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
        id="evt_buyer_seat_race",
        type="payment.succeeded",
        reference=attempt.provider_reference,
        payment_id="fake_pay_buyer_seat",
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
    assert seat_holders(shop["id"], "crm") == {shop["membership"].id}
    assert len(audit_actions(shop["id"], "commercial.order.buyer_seat_assigned")) == 1


@pytest.mark.django_db(transaction=True)
def test_simultaneous_seatings_take_one_seat(shop):
    """A re-driven fulfilment racing the first: still one seat."""
    from tests.purchase_access.conftest import in_scope
    from workeasy360.commercial.payments.orders import seat_the_buyer
    from workeasy360.platform.entitlements.services import seats

    buy(shop, "crm_professional")
    with in_scope(shop["id"]):
        seats.release_seat(
            shop["id"], pool_key="crm", membership_id=shop["membership"].id
        )
    barrier = threading.Barrier(4)
    results: list[list[str]] = []

    def seat():
        barrier.wait()
        results.append(
            seat_the_buyer(shop["id"], user_id=shop["owner"].id, product_codes=["crm"])
        )
        connection.close()

    threads = [threading.Thread(target=seat) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seat_holders(shop["id"], "crm") == {shop["membership"].id}
    assert sum(len(r) for r in results) <= 1

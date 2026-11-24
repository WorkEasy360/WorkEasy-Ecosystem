"""ADR-073, G7: end one product, keep the others and the subscription.

Before, cancelling was of the whole subscription: a customer holding CRM and
Desk could not stop paying for Desk and keep CRM. The line-ending mechanics a
period-end downgrade already uses now answer that directly: the line closes at
the end of the period it was paid for, provisioning withdraws the product then,
and the next period's invoice no longer carries it.
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from tests.purchase_access.conftest import (
    audit_actions,
    buy,
    post,
    subscription_items,
    url,
)

pytestmark = pytest.mark.django_db


def _entitled(organization_id, code: str) -> bool:
    from workeasy360.platform.entitlements.selectors import decision_for_organization

    return decision_for_organization(organization_id, code).allowed


def _subscription(shop):
    return shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"][0]


def _line(shop, code: str) -> dict:
    return next(
        i
        for i in _subscription(shop)["items"]
        if i["product_code"] == code and i["removed_at"] is None
    )


def _end(client, shop, item_id, *, key=None, **body):
    return client.post(
        url(shop["slug"], f"subscription-items/{item_id}/end"),
        body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
    )


@pytest.fixture
def two_products(shop):
    buy(shop, "crm_professional")
    buy(shop, "desk_team")
    return shop


def test_ending_one_product_keeps_the_other_and_the_subscription(two_products):
    shop = two_products
    period_end = _subscription(shop)["current_period_end"]
    desk = _line(shop, "desk")

    ended = _end(shop["client"], shop, desk["id"])
    assert ended.status_code == 200, ended.content
    assert ended.json()["product_code"] == "desk"
    assert ended.json()["ends_at"] == period_end

    subscription = _subscription(shop)
    assert subscription["status"] == "active"
    # Paid for to the period end, so still usable until then.
    assert _entitled(shop["id"], "desk") and _entitled(shop["id"], "crm")
    (entry,) = audit_actions(shop["id"], "commercial.subscription.product_ended")
    assert entry.changes["product"] == "desk" and entry.changes["immediate"] is False

    # My Products says when it ends, and that it will not renew.
    apps = {
        a["product_code"]: a
        for a in shop["client"].get(url(shop["slug"], "apps")).json()["apps"]
    }
    assert apps["desk"]["cancel_at"] == period_end and apps["desk"]["renews_at"] is None
    assert apps["crm"]["cancel_at"] is None and apps["crm"]["renews_at"] == period_end


def test_at_the_period_end_the_product_goes_and_is_not_invoiced_again(
    two_products, monkeypatch
):
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    shop = two_products
    period_end = timezone.datetime.fromisoformat(
        _subscription(shop)["current_period_end"]
    )
    assert _end(shop["client"], shop, _line(shop, "desk")["id"]).status_code == 200

    # The clock's pass a minute after the period ends. Everything it notifies -
    # provisioning and the renewal invoice - reads the time, so the time is moved.
    after = period_end + timedelta(minutes=1)
    with monkeypatch.context() as clock:
        clock.setattr(timezone, "now", lambda: after)
        process_due_subscriptions()
        assert not _entitled(shop["id"], "desk")
        assert _entitled(shop["id"], "crm")
    assert _subscription(shop)["status"] == "active"
    newest = (
        shop["client"].get(url(shop["slug"], "billing/invoices")).json()["results"][0]
    )
    assert newest["order_id"] is None and newest["total_minor"] == 4999  # CRM only


def test_the_subscriptions_last_product_is_ended_by_cancelling_it(shop):
    buy(shop, "crm_professional")
    refused = _end(shop["client"], shop, _line(shop, "crm")["id"])
    assert refused.status_code == 409
    assert refused.json()["reason"] == "last_product"
    assert _line(shop, "crm")["removed_at"] is None


def test_a_bundled_product_ends_with_its_bundle(shop):
    from tests.extension.conftest import place_and_pay

    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        bundle_version_id=str(shop["catalog"]["bundle_version"].id),
    )
    buy(shop, "desk_team")
    items = subscription_items(shop["id"])
    component = next(i for i in items if i.product_code == "crm")
    refused = _end(shop["client"], shop, component.id)
    assert refused.status_code == 409 and refused.json()["reason"] == "bundle_component"

    bundle = next(i for i in items if i.bundle_version_id is not None)
    assert _end(shop["client"], shop, bundle.id).status_code == 200
    by_id = {i.id: i for i in subscription_items(shop["id"])}
    ended = [i for i in by_id.values() if i.removed_at is not None]
    assert {i.product_code for i in ended} == {
        "",
        "crm",
        "books",
    }  # the bundle and both parts
    assert by_id[next(i.id for i in items if i.product_code == "desk")].removed_at is None


def test_only_someone_who_may_cancel_may_end_a_product(two_products, login):
    shop = two_products
    member = login(shop["add"]("member")["user"])
    refused = _end(member, shop, _line(shop, "desk")["id"])
    assert refused.status_code == 403
    assert _line(shop, "desk")["removed_at"] is None


def test_another_organizations_line_is_a_404(two_products, console_org, login):
    shop = two_products
    other = console_org()
    stranger = login(other["owner"])
    response = stranger.post(
        url(other["slug"], f"subscription-items/{_line(shop, 'desk')['id']}/end"),
        {},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert response.status_code == 404


def test_ending_is_idempotent(two_products):
    shop = two_products
    desk = _line(shop, "desk")
    first = _end(shop["client"], shop, desk["id"], key="end-desk")
    replay = _end(shop["client"], shop, desk["id"], key="end-desk")
    again = _end(shop["client"], shop, desk["id"])  # a new key: already ending
    assert first.json() == replay.json() == again.json()
    assert len(audit_actions(shop["id"], "commercial.subscription.product_ended")) == 1


def test_a_scheduled_change_of_the_product_ends_with_it(two_products):
    """A quantity decrease waits for the period end in a new line; ending the
    product ends that line too, so nothing of it renews."""
    from tests.extension.conftest import owner_context
    from tests.purchase_access.conftest import in_scope
    from workeasy360.commercial.subscriptions import services as subscription_services
    from workeasy360.platform.tenancy.context import tenant_context

    shop = two_products
    ctx = owner_context(shop)
    with tenant_context(ctx), in_scope(shop["id"]):
        subscription_services.change_quantity(
            ctx,
            subscription_item_id=uuid.UUID(_line(shop, "desk")["id"]),
            quantity=2,
            idempotency_key=uuid.uuid4().hex,
        )
    future = next(
        i
        for i in subscription_items(shop["id"])
        if i.product_code == "desk" and i.removed_at is None
    )
    assert _end(shop["client"], shop, future.id).status_code == 200
    desk_lines = [i for i in subscription_items(shop["id"]) if i.product_code == "desk"]
    assert all(i.removed_at is not None for i in desk_lines)


def test_staff_end_a_product_now_with_a_reason(two_products, staff_login):
    shop = two_products
    staff, _user = staff_login("platform.staff_subscription.manage")
    desk = _line(shop, "desk")
    target = f"/api/v1/admin/customers/{shop['id']}/items/{desk['id']}/end"
    missing = post(staff, target, {"immediate": True})
    assert missing.status_code == 400  # a reason is required
    ended = post(
        staff, target, {"immediate": True, "reason": "Mistaken purchase, refunded"}
    )
    assert ended.status_code == 200, ended.content
    assert not _entitled(shop["id"], "desk") and _entitled(shop["id"], "crm")
    (entry,) = audit_actions(shop["id"], "commercial.subscription.product_ended")
    assert entry.changes["immediate"] is True
    assert entry.actor_kind == "staff"


@pytest.mark.django_db(transaction=True)
def test_simultaneous_requests_end_the_product_once(two_products, login):
    shop = two_products
    desk = _line(shop, "desk")
    clients = [login(shop["owner"]) for _ in range(4)]
    barrier = threading.Barrier(4)
    statuses: list[int] = []

    def end(client):
        barrier.wait()
        statuses.append(_end(client, shop, desk["id"]).status_code)
        connection.close()

    threads = [threading.Thread(target=end, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200, 200, 200, 200]
    assert len(audit_actions(shop["id"], "commercial.subscription.product_ended")) == 1
    desk_lines = [i for i in subscription_items(shop["id"]) if i.product_code == "desk"]
    assert len(desk_lines) == 1 and desk_lines[0].removed_at is not None

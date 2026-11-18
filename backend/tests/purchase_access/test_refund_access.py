"""ADR-073, G8: a refund can end the access it paid for - when staff say so.

A refund stays money only by default (``end_access: "keep"``). Staff may now
also choose ``period_end`` or ``now``: the product line(s) the refunded payment
paid for end through the ordinary line-ending command, in the refund's own
transaction, audited with the refund's reason. The subscription's last product
ends by cancelling the subscription, as the customer would.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from django.db import connection

from tests.purchase_access.conftest import (
    audit_actions,
    buy,
    in_scope,
    subscription_items,
    url,
)

pytestmark = pytest.mark.django_db

REASON = "Bought by mistake, refunded in full"


def _entitled(organization_id, code: str) -> bool:
    from workeasy360.platform.entitlements.selectors import decision_for_organization

    return decision_for_organization(organization_id, code).allowed


def _attempt_for(shop, order) -> str:
    from workeasy360.commercial.payments.models import PaymentAttempt

    with in_scope(shop["id"]):
        return str(
            PaymentAttempt.unscoped.get(
                organization_id=shop["id"],
                invoice_id=order["invoice_id"],
                status="succeeded",
            ).id
        )


def _refund(staff, shop, attempt_id, *, amount, key=None, **body):
    return staff.post(
        f"/api/v1/admin/customers/{shop['id']}/payments/{attempt_id}/refund",
        {"amount_minor": amount, "reason": REASON, **body},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
    )


def _open(shop, code: str):
    return [
        i
        for i in subscription_items(shop["id"])
        if i.product_code == code and i.removed_at is None
    ]


@pytest.fixture
def bought(shop):
    crm = buy(shop, "crm_professional")
    desk = buy(shop, "desk_team")
    return {**shop, "crm_order": crm, "desk_order": desk}


@pytest.fixture
def billing_staff(staff_login):
    staff, _user = staff_login(
        "platform.staff_payment.manage", "platform.staff_subscription.manage"
    )
    return staff


def test_by_default_a_refund_is_money_only(bought, billing_staff):
    attempt = _attempt_for(bought, bought["desk_order"])
    response = _refund(billing_staff, bought, attempt, amount=100)
    assert response.status_code == 202, response.content
    assert "access" not in response.json()
    assert _open(bought, "desk") and _entitled(bought["id"], "desk")
    assert audit_actions(bought["id"], "billing.refund_access_ended") == []


def test_ending_access_at_the_period_end_ends_only_the_refunded_product(
    bought, billing_staff
):
    subscription = (
        bought["client"].get(url(bought["slug"], "subscriptions")).json()["results"][0]
    )
    attempt = _attempt_for(bought, bought["desk_order"])
    response = _refund(
        billing_staff,
        bought,
        attempt,
        amount=bought["desk_order"]["amount_due_minor"],
        end_access="period_end",
    )
    assert response.status_code == 202, response.content
    access = response.json()["access"]
    assert access["end_access"] == "period_end" and len(access["ended_items"]) == 1
    (desk,) = [i for i in subscription_items(bought["id"]) if i.product_code == "desk"]
    assert desk.removed_at.isoformat() == subscription["current_period_end"]
    assert _open(bought, "crm")
    # Explained with the refund's own reason, in the customer's log.
    (entry,) = audit_actions(bought["id"], "billing.refund_access_ended")
    assert entry.reason == REASON and entry.changes["end_access"] == "period_end"


def test_ending_access_now_withdraws_the_product_at_once(bought, billing_staff):
    attempt = _attempt_for(bought, bought["desk_order"])
    response = _refund(billing_staff, bought, attempt, amount=100, end_access="now")
    assert response.status_code == 202, response.content
    assert not _entitled(bought["id"], "desk")
    assert _entitled(bought["id"], "crm")
    subscription = (
        bought["client"].get(url(bought["slug"], "subscriptions")).json()["results"][0]
    )
    assert subscription["status"] == "active"


def test_the_last_product_ends_by_cancelling_the_subscription(shop, billing_staff):
    order = buy(shop, "crm_professional")
    response = _refund(
        billing_staff, shop, _attempt_for(shop, order), amount=100, end_access="now"
    )
    assert response.status_code == 202, response.content
    assert response.json()["access"]["cancelled_subscriptions"]
    assert not _entitled(shop["id"], "crm")


def test_ending_access_needs_the_subscription_permission_too(bought, staff_login):
    from workeasy360.commercial.payments.models import Refund

    payments_only, _user = staff_login("platform.staff_payment.manage")
    attempt = _attempt_for(bought, bought["desk_order"])
    refused = _refund(payments_only, bought, attempt, amount=100, end_access="now")
    assert refused.status_code == 403
    with in_scope(bought["id"]):
        assert not Refund.unscoped.filter(organization_id=bought["id"]).exists()
    assert _entitled(bought["id"], "desk")


def test_an_unknown_choice_is_refused(bought, billing_staff):
    attempt = _attempt_for(bought, bought["desk_order"])
    response = _refund(billing_staff, bought, attempt, amount=100, end_access="later")
    assert response.status_code == 400
    assert response.json()["field"] == "end_access"


def test_a_replayed_refund_ends_access_once(bought, billing_staff):
    attempt = _attempt_for(bought, bought["desk_order"])
    first = _refund(
        billing_staff, bought, attempt, amount=100, key="refund-1", end_access="now"
    )
    replay = _refund(
        billing_staff, bought, attempt, amount=100, key="refund-1", end_access="now"
    )
    assert first.json()["refund_id"] == replay.json()["refund_id"]
    assert len(audit_actions(bought["id"], "billing.refund_access_ended")) == 1
    assert len(audit_actions(bought["id"], "commercial.subscription.product_ended")) == 1


def test_ending_access_now_signs_the_people_out_of_that_product(bought, billing_staff):
    from workeasy360.platform.ecosystem.models import BackchannelLogout, ProductSignIn

    for code in ("crm", "desk"):
        ProductSignIn.objects.create(
            integration=bought["products"][code]["integration"],
            user_id=bought["owner"].pk,
            sign_in_id=uuid.uuid4(),
        )
    attempt = _attempt_for(bought, bought["desk_order"])
    assert (
        _refund(billing_staff, bought, attempt, amount=100, end_access="now").status_code
        == 202
    )
    owed = set(
        BackchannelLogout.objects.filter(user_id=bought["owner"].pk).values_list(
            "integration__product_code", "reason"
        )
    )
    assert owed == {("desk", "entitlement_ended")}


@pytest.mark.django_db(transaction=True)
def test_simultaneous_refunds_end_the_line_once(bought, staff_login):
    attempt = _attempt_for(bought, bought["desk_order"])
    clients = [
        staff_login(
            "platform.staff_payment.manage", "platform.staff_subscription.manage"
        )[0]
        for _ in range(3)
    ]
    barrier = threading.Barrier(3)
    statuses: list[int] = []

    def refund(client):
        barrier.wait()
        statuses.append(
            _refund(client, bought, attempt, amount=100, end_access="now").status_code
        )
        connection.close()

    threads = [threading.Thread(target=refund, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [202, 202, 202]
    assert len(audit_actions(bought["id"], "commercial.subscription.product_ended")) == 1
    assert not _entitled(bought["id"], "desk") and _entitled(bought["id"], "crm")

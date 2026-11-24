"""ADR-073, G13: My Products shows a paid product that ended as Expired.

Before, a cancelled or expired *paid* product dropped out of My Products and
read as one the organization never had ("Explore products"); only an expired
trial was shown as expired. The app directory now names a paid product whose
access ended in the last 90 days (``lapsed``), so the page can show it as
Expired with a way to buy it again.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.purchase_access.conftest import buy, post, url, version_of

pytestmark = pytest.mark.django_db


def _apps(shop, client=None):
    listing = (client or shop["client"]).get(url(shop["slug"], "apps")).json()["apps"]
    return {a["product_code"]: a for a in listing}


def _subscription(shop):
    return shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"][0]


def test_a_held_product_is_not_lapsed(shop):
    buy(shop, "crm_professional")
    assert _apps(shop)["crm"]["lapsed"] is None
    assert _apps(shop)["desk"]["lapsed"] is None  # never held


def test_a_cancelled_paid_product_reads_as_expired_with_its_plan(shop, login):
    buy(shop, "crm_professional")
    cancelled = post(
        shop["client"],
        url(shop["slug"], f"subscriptions/{_subscription(shop)['id']}/cancel"),
        {"immediate": True, "reason_code": "customer_request"},
    )
    assert cancelled.status_code == 200
    crm = _apps(shop)["crm"]
    assert crm["state"] == "no_entitlement" and crm["entitlement_status"] is None
    assert crm["lapsed"]["plan"] == {
        "code": "crm_professional",
        "name": "CRM Professional",
    }
    ended = timezone.datetime.fromisoformat(crm["lapsed"]["ended_at"])
    assert abs((timezone.now() - ended).total_seconds()) < 60

    # A member who may not see billing sees that it expired, not the plan.
    member = login(shop["add"]("member")["user"])
    assert _apps(shop, member)["crm"]["lapsed"] == {
        "ended_at": crm["lapsed"]["ended_at"],
        "plan": None,
    }


def test_a_product_ended_on_its_own_lapses_at_its_period_end(shop):
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    buy(shop, "crm_professional")
    buy(shop, "desk_team")
    subscription = _subscription(shop)
    desk = next(i for i in subscription["items"] if i["product_code"] == "desk")
    ended = shop["client"].post(
        url(shop["slug"], f"subscription-items/{desk['id']}/end"),
        {},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert ended.status_code == 200
    assert _apps(shop)["desk"]["lapsed"] is None  # still running to the period end

    after = timezone.datetime.fromisoformat(
        subscription["current_period_end"]
    ) + timedelta(minutes=1)
    process_due_subscriptions(at=after)
    from tests.extension.conftest import owner_context
    from tests.purchase_access.conftest import in_scope
    from workeasy360.commercial.subscriptions import my_products
    from workeasy360.platform.tenancy.context import tenant_context

    ctx = owner_context(shop)
    with tenant_context(ctx), in_scope(shop["id"]):
        held, lapsed = my_products.directory_state(at=after)
    assert "crm" in held and "desk" not in held
    assert lapsed["desk"].ended_at.isoformat() == subscription["current_period_end"]


def test_a_paid_product_that_ended_long_ago_is_explores_again(shop):
    from tests.extension.conftest import owner_context
    from tests.purchase_access.conftest import in_scope
    from workeasy360.commercial.subscriptions import my_products
    from workeasy360.platform.tenancy.context import tenant_context

    buy(shop, "crm_professional")
    post(
        shop["client"],
        url(shop["slug"], f"subscriptions/{_subscription(shop)['id']}/cancel"),
        {"immediate": True, "reason_code": "customer_request"},
    )
    ctx = owner_context(shop)
    with tenant_context(ctx), in_scope(shop["id"]):
        _held, soon = my_products.directory_state(at=timezone.now() + timedelta(days=30))
        _held, later = my_products.directory_state(at=timezone.now() + timedelta(days=91))
    assert "crm" in soon and "crm" not in later


def test_an_expired_trial_is_not_an_expired_purchase(shop):
    """A plan-level trial that ran out unpaid is Explore's "trial expired", not
    a lapsed purchase."""
    from tests.extension.conftest import owner_context, place_order
    from tests.purchase_access.conftest import in_scope
    from workeasy360.commercial.subscriptions import my_products
    from workeasy360.platform.tenancy.context import tenant_context

    placed = place_order(
        shop["client"],
        shop["slug"],
        plan_version_id=version_of(shop, "crm_starter"),
        with_trial=True,
    )
    assert placed.status_code == 201, placed.content
    trial_end = timezone.datetime.fromisoformat(_subscription(shop)["trial_ends_at"])
    ctx = owner_context(shop)
    with tenant_context(ctx), in_scope(shop["id"]):
        _held, lapsed = my_products.directory_state(at=trial_end + timedelta(days=1))
    assert "crm" not in lapsed


def test_a_lapsed_product_can_be_bought_again(shop):
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    buy(shop, "crm_professional")
    post(
        shop["client"],
        url(shop["slug"], f"subscriptions/{_subscription(shop)['id']}/cancel"),
        {"immediate": True, "reason_code": "customer_request"},
    )
    process_due_subscriptions(at=timezone.now() + timedelta(minutes=1))
    assert _apps(shop)["crm"]["lapsed"] is not None

    buy(shop, "crm_professional")
    crm = _apps(shop)["crm"]
    assert crm["lapsed"] is None and crm["state"] == "active"

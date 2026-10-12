"""A 7-day free trial of each of the four products, proved separately for each
code (brief Parts 13-23 and 39 - "no PASS by association").

For one product at a time: Explore offers "Start 7-Day Free Trial"; starting is
idempotent; the trial entitlement is trial-derived and lasts exactly 7 days
by server time; launch works during it; at ``ends_at`` - not a second later,
and before any sweeper runs - launch is denied and Explore says the trial
expired and offers Buy; buying converts it to a paid entitlement without a
duplicate; the organization and its history stay.

The clock is the server's: ``django.utils.timezone.now`` is moved, which is
what every check reads.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.extension.conftest import place_and_pay, staff_scope, url
from tests.phase11 import conftest as _phase11
from tests.phase11 import test_11i_all_four_products as _four
from tests.phase11.conftest import CATALOG_STAFF, tenant_rows
from workeasy360.platform.entitlements.selectors import decision_for_organization

pytestmark = pytest.mark.django_db

console_org = _phase11.console_org
fake_payments = _phase11.fake_payments
login = _phase11.login
four_products = _four.four_products
shop_for = _four.shop_for

FOUR_PRODUCTS = _four.FOUR_PRODUCTS


@pytest.fixture
def seven_days(four_products):
    """The owner's decision: every product's trial is 7 days."""
    from workeasy360.commercial.catalog import services as catalog_services

    with staff_scope(*CATALOG_STAFF) as ctx:
        for code, made in four_products.items():
            catalog_services.configure_product_trial(
                ctx,
                product_id=made["product"].id,
                enabled=True,
                duration_days=7,
                eligible_plan_codes=[made["plan_code"]],
            )
    return four_products


def _explore(client, slug, code):
    listing = client.get(url(slug, "explore"))
    assert listing.status_code == 200, listing.content
    return next(p for p in listing.json()["products"] if p["code"] == code)


def _launch(client, slug, code):
    return client.post(url(slug, f"apps/{code}/launch"), HTTP_ACCEPT="application/json")


@pytest.mark.parametrize("code", FOUR_PRODUCTS)
def test_a_seven_day_trial_of_each_product(
    code, seven_days, shop_for, login, monkeypatch
):
    from workeasy360.commercial.subscriptions.models import ProductTrial
    from workeasy360.contracts.enums import ProductTrialStatus
    from workeasy360.platform.entitlements.models import Entitlement

    shop = shop_for()
    client, slug = shop["client"], shop["slug"]
    plan_code = seven_days[code]["plan_code"]

    # Explore: the trial is offered, for 7 days, decided by the server.
    card = _explore(client, slug, code)
    assert card["trial_state"] == "available"
    assert card["trial"]["available"] is True
    assert card["trial"]["duration_days"] == 7

    # Start: idempotent on the key - a double click or a retry is one trial.
    key = uuid.uuid4().hex
    body = {"product_code": code, "plan_code": plan_code}
    first = client.post(
        url(slug, "trials"),
        body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    again = client.post(
        url(slug, "trials"),
        body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert first.status_code == 201, first.content
    assert again.status_code in (200, 201) and again.json()["id"] == first.json()["id"]
    other_key = client.post(
        url(slug, "trials"),
        body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert other_key.status_code == 409
    trials = tenant_rows(ProductTrial, shop["id"], product_code=code)
    assert len(trials) == 1
    trial = trials[0]
    assert trial.ends_at - trial.started_at == timedelta(days=7)

    # The entitlement is trial-derived and ends exactly when the trial does.
    decision = decision_for_organization(shop["id"], code)
    assert decision.allowed and decision.status.value == "trial"
    rows = tenant_rows(Entitlement, shop["id"], product_code=code)
    assert [(r.source_kind, r.effective_to) for r in rows] == [("trial", trial.ends_at)]

    # During the trial: My Products shows it, launch goes to the product. The
    # person who started the trial holds one of its seats - nobody had to
    # assign it - and only that person: the other seats stay free.
    from workeasy360.platform.entitlements.models import SeatAssignment

    held = tenant_rows(
        SeatAssignment, shop["id"], seat_grant__pool_key=code, released_at__isnull=True
    )
    assert [s.membership_id for s in held] == [shop["membership"].id]
    entry = next(
        a
        for a in client.get(url(slug, "apps")).json()["apps"]
        if a["product_code"] == code
    )
    assert entry["entitlement_status"] == "trial" and entry["can_launch"] is True
    assert entry["trial"]["days_left"] == 7
    launched = _launch(client, slug, code)
    assert launched.status_code == 200, launched.content
    assert launched.json()["redirect_url"].startswith(f"https://{code}.example.com/")
    card = _explore(client, slug, code)
    assert card["trial_state"] == "active" and card["trial_days_left"] == 7

    # One second before the end (server time): still a trial.
    monkeypatch.setattr(timezone, "now", lambda: trial.ends_at - timedelta(seconds=1))
    assert decision_for_organization(shop["id"], code).allowed

    # At the end: denied - before any sweeper has run.
    monkeypatch.setattr(timezone, "now", lambda: trial.ends_at)
    assert not decision_for_organization(shop["id"], code).allowed
    owner = login(shop["owner"])  # the earlier session's idle window is long gone
    denied = _launch(owner, slug, code)
    assert denied.status_code in (402, 403, 409), denied.content
    card = _explore(owner, slug, code)
    assert card["trial_state"] == "used" and card["trial_outcome"] == "expired"
    assert card["trial"]["available"] is True  # still offered in general...
    # ...but not to this organization again: once per organization and product.
    replay = owner.post(
        url(slug, "trials"),
        body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert replay.status_code == 409 and replay.json()["reason"] == "already_used"

    # The sweeper settles the record; the organization and history stay.
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.system import settle_product_trials

    settle_product_trials(shop["id"], at=trial.ends_at)
    reconcile(shop["id"], at=trial.ends_at)
    trial = tenant_rows(ProductTrial, shop["id"], product_code=code)[0]
    assert trial.status_enum is ProductTrialStatus.EXPIRED
    assert _explore(owner, slug, code)["trial_outcome"] == "expired"

    # Buy, an hour after it expired: an ordinary paid order - one paid
    # entitlement, no second trial, no second organization.
    after = trial.ends_at + timedelta(hours=1)
    monkeypatch.setattr(timezone, "now", lambda: after)
    owner = login(shop["owner"])
    order = place_and_pay(
        owner, slug, shop["id"], plan_version_id=str(seven_days[code]["plan_version_id"])
    )
    assert order["status"] == "fulfilled", order
    decision = decision_for_organization(shop["id"], code)
    assert decision.allowed and decision.status.value == "active"
    now = timezone.now()
    live = [
        r
        for r in tenant_rows(Entitlement, shop["id"], product_code=code)
        if r.is_effective_at(now)
    ]
    assert len(tenant_rows(ProductTrial, shop["id"], product_code=code)) == 1
    assert [r.source_kind for r in live] == ["subscription"]
    assert _explore(owner, slug, code)["trial_state"] == "owned"
    assert _launch(owner, slug, code).status_code == 200


def test_the_four_trials_are_independent(seven_days, shop_for):
    """CRM paid, EasyBook on trial, Easy Project paid, Help Desk untouched -
    in one organization at once (brief Part 15)."""
    shop = shop_for()
    client, slug = shop["client"], shop["slug"]
    for code in ("easy_crm", "easy_project"):
        place_and_pay(
            client,
            slug,
            shop["id"],
            plan_version_id=str(seven_days[code]["plan_version_id"]),
        )
    started = client.post(
        url(slug, "trials"),
        {"product_code": "easybook", "plan_code": seven_days["easybook"]["plan_code"]},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert started.status_code == 201, started.content
    states = {
        code: getattr(decision_for_organization(shop["id"], code).status, "value", None)
        for code in FOUR_PRODUCTS
    }
    assert states["easy_helpdesk"] is None
    assert states["easy_crm"] == "active"
    assert states["easy_project"] == "active"
    assert states["easybook"] == "trial"
    assert not decision_for_organization(shop["id"], "easy_helpdesk").allowed
    cards = {
        p["code"]: p["trial_state"]
        for p in client.get(url(slug, "explore")).json()["products"]
        if p["code"] in FOUR_PRODUCTS
    }
    assert cards == {
        "easy_crm": "owned",
        "easy_project": "owned",
        "easybook": "active",
        "easy_helpdesk": "available",
    }


def test_another_organizations_trial_grants_this_one_nothing(seven_days, shop_for):
    first, second = shop_for(), shop_for()
    code = "easy_helpdesk"
    first["client"].post(
        url(first["slug"], "trials"),
        {"product_code": code, "plan_code": seven_days[code]["plan_code"]},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert decision_for_organization(first["id"], code).allowed
    assert not decision_for_organization(second["id"], code).allowed
    # And the second organization's own trial is still available to it.
    assert _explore(second["client"], second["slug"], code)["trial_state"] == "available"
    # A member of the first cannot read the second's explore page.
    assert first["client"].get(url(second["slug"], "explore")).status_code in (403, 404)

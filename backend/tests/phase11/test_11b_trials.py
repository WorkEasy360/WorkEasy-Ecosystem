"""11b - per-product free trials.

A trial is a durable fact that provisioning turns into a TRIAL entitlement: no
subscription, no invoice, no commission. These tests drive the real HTTP
surface (sessions, CSRF-free test client, idempotency keys) and read the answer
the way a product asks for it - through the entitlement decision."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase11.conftest import (
    CATALOG_STAFF,
    audit_of,
    place_and_pay,
    post,
    staff_scope,
    tenant_rows,
    url,
)
from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.contracts.enums import CatalogStatus, ProductStatus
from workeasy360.platform.entitlements.selectors import decision_for_organization

pytestmark = pytest.mark.django_db


# --- helpers ----------------------------------------------------------------------------


def configure(catalog, product="crm", plans=("crm_starter",), **overrides):
    terms = {
        "enabled": True,
        "duration_days": 14,
        "seat_limit": None,
        "payment_method_required": False,
        "eligible_plan_codes": list(plans),
    }
    terms.update(overrides)
    with staff_scope(*CATALOG_STAFF) as ctx:
        catalog_services.configure_product_trial(
            ctx, product_id=catalog["products"][product].id, **terms
        )


@pytest.fixture
def shop(console_org, login, catalog, fake_payments):
    """An organization whose owner is signed in, with the CRM trial configured
    (14 days, on ``crm_starter`` only) and a billing account for buying."""
    configure(catalog)
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP", "billing_email": "billing@example.com"},
        content_type="application/json",
    )
    assert opened.status_code == 200, opened.content
    return {**org, "client": owner, "catalog": catalog, "id": org["org"].id}


def start(shop, product="crm", plan="crm_starter", *, key=None, client=None):
    return (client or shop["client"]).post(
        url(shop["slug"], "trials"),
        {"product_code": product, "plan_code": plan},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
    )


def decision(shop, product="crm"):
    return decision_for_organization(shop["id"], product)


def trials_of(shop):
    from workeasy360.commercial.subscriptions.models import ProductTrial

    return sorted(tenant_rows(ProductTrial, shop["id"]), key=lambda t: t.id)


def as_of(shop, moment):
    """Reconcile and settle as the clock would at ``moment``."""
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.system import settle_product_trials

    settle_product_trials(shop["id"], at=moment)
    reconcile(shop["id"], at=moment)


# --- starting -----------------------------------------------------------------------------


def test_a_trial_grants_a_trial_entitlement_and_nothing_else(shop):
    from workeasy360.commercial.payments.models import Invoice
    from workeasy360.commercial.subscriptions.models import Subscription

    assert not decision(shop).allowed
    response = start(shop)
    assert response.status_code == 201, response.content
    body = response.json()
    assert (body["product_code"], body["plan_code"], body["status"]) == (
        "crm",
        "crm_starter",
        "active",
    )
    assert body["duration_days"] == 14
    started = timezone.datetime.fromisoformat(body["started_at"])
    ended = timezone.datetime.fromisoformat(body["ends_at"])
    assert ended - started == timedelta(days=14)
    assert body["seats"] == 2  # crm_starter includes two seats
    assert "plan_version_id" not in body

    granted = decision(shop)
    assert granted.allowed and granted.status.value == "trial"
    # Not a subscription, not an invoice, not a sale.
    assert tenant_rows(Subscription, shop["id"]) == []
    assert tenant_rows(Invoice, shop["id"]) == []
    # Another product is untouched.
    assert not decision(shop, "books").allowed


def test_the_trial_carries_the_plans_features_and_seats(shop):
    from workeasy360.platform.entitlements.selectors import feature_for_organization

    start(shop)
    assert feature_for_organization(shop["id"], "crm.pipelines.max") == 3
    assert not feature_for_organization(shop["id"], "crm.sales.forecast")


def test_a_replayed_start_returns_the_same_trial(shop):
    key = uuid.uuid4().hex
    first = start(shop, key=key).json()
    again = start(shop, key=key)
    assert again.status_code == 201
    assert again.json()["id"] == first["id"]
    assert len(trials_of(shop)) == 1


def test_a_second_start_with_another_key_is_refused(shop):
    assert start(shop).status_code == 201
    second = start(shop)
    assert second.status_code == 409
    assert second.json()["code"] == "trial_not_allowed"
    assert second.json()["reason"] == "already_used"
    assert len(trials_of(shop)) == 1


def test_the_database_decides_a_lost_race(shop, monkeypatch):
    """Two starts with different keys pass the checks together; the unique key
    lets exactly one row in and the other is told the trial is used."""
    from workeasy360.commercial.subscriptions import selectors

    assert start(shop).status_code == 201
    monkeypatch.setattr(selectors, "has_used_a_trial", lambda *a, **k: False)
    second = start(shop)
    assert second.status_code == 409
    assert second.json()["reason"] == "already_used"
    assert len(trials_of(shop)) == 1


def test_a_changed_plan_on_the_same_key_is_not_a_replay(shop):
    key = uuid.uuid4().hex
    assert start(shop, key=key).status_code == 201
    other = start(shop, plan="crm_professional", key=key)
    assert other.status_code >= 400


def test_an_idempotency_key_is_required(shop):
    response = shop["client"].post(
        url(shop["slug"], "trials"),
        {"product_code": "crm", "plan_code": "crm_starter"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert not trials_of(shop)


@pytest.mark.parametrize(
    ("product", "plan", "reason"),
    [
        ("books", "books_professional", "not_offered"),  # nothing configured
        ("crm", "crm_professional", "plan_not_eligible"),  # real plan, not eligible
        ("crm", "no_such_plan", "plan_not_eligible"),
        ("crm", "books_professional", "plan_not_eligible"),  # another product's plan
    ],
)
def test_a_trial_is_only_offered_where_staff_configured_it(shop, product, plan, reason):
    response = start(shop, product, plan)
    assert response.status_code == 409, response.content
    assert response.json()["reason"] == reason
    assert not trials_of(shop)


@pytest.mark.parametrize("product", ["", "CRM", "cr m", "crm;drop", "x" * 100, "no_such"])
def test_a_malformed_or_unknown_product_is_refused(shop, product):
    response = start(shop, product, "crm_starter")
    assert response.status_code in (400, 409)
    assert not trials_of(shop)


def test_a_switched_off_trial_cannot_be_started(shop):
    configure(shop["catalog"], enabled=False)
    response = start(shop)
    assert response.status_code == 409
    assert response.json()["reason"] == "not_offered"


def test_a_product_that_is_not_on_sale_has_no_trial(shop):
    from workeasy360.commercial.catalog.models import Product

    Product.objects.filter(code="crm").update(listing="hidden")
    assert start(shop).json()["reason"] == "product_unavailable"
    Product.objects.filter(code="crm").update(
        listing="listed", status=ProductStatus.DEPRECATED.value
    )
    assert start(shop).json()["reason"] == "product_unavailable"
    assert not trials_of(shop)


def test_a_plan_that_is_no_longer_on_sale_cannot_be_trialled(shop):
    with staff_scope(*CATALOG_STAFF) as ctx:
        catalog_services.set_plan_status(
            ctx,
            plan_id=shop["catalog"]["plans"]["crm_starter"].id,
            status=CatalogStatus.GRANDFATHERED,
        )
    assert start(shop).json()["reason"] == "plan_not_eligible"


def test_a_customer_who_already_owns_the_product_cannot_trial_it(shop):
    order = place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=str(shop["catalog"]["versions"]["crm_professional"].id),
    )
    assert order["status"] == "fulfilled"
    response = start(shop)
    assert response.status_code == 409
    assert response.json()["reason"] == "already_owned"


def test_the_seat_limit_caps_the_trial(shop):
    configure(shop["catalog"], seat_limit=1)
    assert start(shop).json()["seats"] == 1
    # A limit above the plan's own seats never raises it.
    assert decision(shop).allowed


def test_only_someone_who_may_buy_can_start_a_trial(shop, login):
    member = shop["add"]("member")
    client = login(member["user"])
    denied = start(shop, client=client)
    assert denied.status_code == 403
    assert not trials_of(shop)
    # ...but every member may look.
    listed = client.get(url(shop["slug"], "trials"))
    assert listed.status_code == 200 and listed.json()["results"] == []


def test_an_anonymous_visitor_cannot_start_or_list(shop):
    from django.test import Client

    anonymous = Client()
    assert start(shop, client=anonymous).status_code in (401, 403, 404)
    assert anonymous.get(url(shop["slug"], "trials")).status_code in (401, 403, 404)


def test_a_suspended_organization_cannot_start_a_trial(shop):
    from workeasy360.platform.organizations.models import Organization

    Organization.objects.filter(id=shop["id"]).update(status="suspended")
    response = start(shop)
    assert response.status_code in (402, 403, 404)
    assert not trials_of(shop)


# --- terms ---------------------------------------------------------------------------------


def test_reconfiguring_never_changes_a_running_trial(shop):
    started = start(shop).json()
    configure(shop["catalog"], duration_days=90, seat_limit=1)
    running = trials_of(shop)[0]
    assert running.duration_days == 14
    assert running.seats == 2
    assert running.ends_at.isoformat() == started["ends_at"]


# --- ending --------------------------------------------------------------------------------


def test_the_trial_ends_at_its_end_without_anyone_doing_anything(shop):
    from workeasy360.commercial.provisioning.services import compute_entitlements

    ends = timezone.datetime.fromisoformat(start(shop).json()["ends_at"])
    # Time alone removes it from the desired state - before any sweeper runs.
    assert [
        p.product_code
        for p in compute_entitlements(shop["id"], ends - timedelta(minutes=1)).products
    ] == ["crm"]
    assert compute_entitlements(shop["id"], ends + timedelta(minutes=1)).products == ()


def test_expiry_settles_the_record_and_revokes_through_provisioning(shop):
    from workeasy360.contracts.enums import ProductTrialStatus

    body = start(shop).json()
    ends = timezone.datetime.fromisoformat(body["ends_at"])
    assert decision(shop).allowed

    as_of(shop, ends + timedelta(minutes=1))

    trial = trials_of(shop)[0]
    assert trial.status_enum is ProductTrialStatus.EXPIRED and trial.ended_at
    assert not decision(shop).allowed
    audited = {
        r["action"] for r in audit_of(body["id"], "action", organization_id=shop["id"])
    }
    assert {
        "commercial.product_trial.started",
        "commercial.product_trial.expired",
    } <= audited


def test_settling_twice_changes_nothing(shop):
    from workeasy360.commercial.subscriptions.system import settle_product_trials

    ends = timezone.datetime.fromisoformat(start(shop).json()["ends_at"])
    late = ends + timedelta(minutes=1)
    assert settle_product_trials(shop["id"], at=late) == (1, 0)
    assert settle_product_trials(shop["id"], at=late) == (0, 0)


def test_a_trial_still_in_its_window_is_left_alone(shop):
    from workeasy360.commercial.subscriptions.system import settle_product_trials

    start(shop)
    assert settle_product_trials(shop["id"]) == (0, 0)
    assert decision(shop).allowed


def test_the_sweeper_expires_trials_across_organizations(shop):
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    ends = timezone.datetime.fromisoformat(start(shop).json()["ends_at"])
    counts = process_due_subscriptions(at=ends + timedelta(minutes=1))
    assert counts["product_trials_expired"] == 1
    assert counts["failed"] == 0
    assert not decision(shop).allowed


def test_an_expired_trial_cannot_be_started_again(shop):
    ends = timezone.datetime.fromisoformat(start(shop).json()["ends_at"])
    as_of(shop, ends + timedelta(minutes=1))
    again = start(shop)
    assert again.status_code == 409 and again.json()["reason"] == "already_used"
    assert len(trials_of(shop)) == 1


def test_cancelling_ends_it_now_and_it_cannot_return(shop):
    from workeasy360.contracts.enums import ProductTrialStatus

    body = start(shop).json()
    cancelled = shop["client"].post(url(shop["slug"], f"trials/{body['id']}/cancel"))
    assert cancelled.status_code == 200, cancelled.content
    assert cancelled.json()["status"] == "cancelled"
    assert not decision(shop).allowed
    assert trials_of(shop)[0].status_enum is ProductTrialStatus.CANCELLED
    # Again: a no-op that reports the state.
    assert (
        shop["client"]
        .post(url(shop["slug"], f"trials/{body['id']}/cancel"))
        .json()["status"]
        == "cancelled"
    )
    assert start(shop).json()["reason"] == "already_used"
    assert "commercial.product_trial.cancelled" in {
        r["action"] for r in audit_of(body["id"], "action", organization_id=shop["id"])
    }


def test_another_organization_cannot_see_or_cancel_my_trial(shop, console_org, login):
    body = start(shop).json()
    other = console_org()
    outsider = login(other["owner"])
    assert outsider.get(url(other["slug"], "trials")).json()["results"] == []
    # My id, their organization: indistinguishable from an id that never existed.
    theirs = outsider.post(url(other["slug"], f"trials/{body['id']}/cancel"))
    assert theirs.status_code == 404
    # ...and my own slug is closed to them entirely.
    assert outsider.post(
        url(shop["slug"], f"trials/{body['id']}/cancel")
    ).status_code in (
        403,
        404,
    )
    assert trials_of(shop)[0].status == "active"


# --- converting ----------------------------------------------------------------------------


def test_buying_during_a_trial_converts_it_without_a_second_entitlement(shop):
    from workeasy360.commercial.subscriptions.models import Subscription
    from workeasy360.commercial.subscriptions.system import settle_product_trials
    from workeasy360.contracts.enums import ProductTrialStatus
    from workeasy360.platform.entitlements.models import Entitlement

    trial = start(shop).json()
    assert decision(shop).status.value == "trial"

    order = place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=str(shop["catalog"]["versions"]["crm_professional"].id),
    )
    assert order["status"] == "fulfilled"

    # One subscription, paid; the product is now ACTIVE, not trial.
    assert len(tenant_rows(Subscription, shop["id"])) == 1
    assert decision(shop).status.value == "active"

    assert settle_product_trials(shop["id"]) == (0, 1)
    assert trials_of(shop)[0].status_enum is ProductTrialStatus.CONVERTED
    # Exactly one live entitlement for the product: the paid one.
    live = [
        e
        for e in tenant_rows(Entitlement, shop["id"], product_code="crm")
        if e.status != "expired"
    ]
    assert [e.source_kind for e in live] == ["subscription"]
    assert "commercial.product_trial.converted" in {
        r["action"] for r in audit_of(trial["id"], "action", organization_id=shop["id"])
    }


def test_buying_twice_or_retrying_the_payment_does_not_duplicate_anything(shop):
    from workeasy360.commercial.subscriptions.models import Subscription

    start(shop)
    version = str(shop["catalog"]["versions"]["crm_professional"].id)
    place_and_pay(shop["client"], shop["slug"], shop["id"], plan_version_id=version)
    again = shop["client"].post(
        url(shop["slug"], "orders"),
        {"plan_version_id": version},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert again.status_code >= 400  # already owned
    assert len(tenant_rows(Subscription, shop["id"])) == 1


def test_a_converted_trial_earns_commission_only_as_the_ordinary_sale(shop):
    """A trial is not a sale: it accrues nothing. The purchase is the sale."""
    from workeasy360.commercial.commissions.models import CommissionAccrual

    start(shop)
    assert not CommissionAccrual.objects.filter(organization_id=shop["id"]).exists()


# --- several products at once (the mandatory dashboard state) ------------------------------


def test_two_products_bought_one_on_trial_and_one_untouched(shop):
    """CRM active, Books active, a third product on trial, a fourth held by
    nobody - one organization, one subscription, three independent statuses."""
    from workeasy360.commercial.pricing import services as pricing
    from workeasy360.commercial.subscriptions.models import Subscription
    from workeasy360.contracts.enums import (
        BillingInterval,
        FeatureKind,
        LicensingMode,
    )
    from workeasy360.contracts.values.money import CurrencyCode

    catalog = shop["catalog"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        for code in ("desk", "vault"):
            product = catalog_services.create_product(
                ctx, code=code, name=code.title(), licensing_mode=LicensingMode.PER_SEAT
            )
            catalog_services.set_product_status(
                ctx, product_id=product.id, status=ProductStatus.AVAILABLE
            )
            catalog_services.create_feature(
                ctx,
                product_id=product.id,
                key=f"{code}.core.enabled",
                name="Core",
                kind=FeatureKind.BOOLEAN,
            )
            plan = catalog_services.create_plan(
                ctx, product_id=product.id, code=f"{code}_team", name="Team"
            )
            catalog_services.set_plan_status(
                ctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE
            )
            version = catalog_services.draft_plan_version(
                ctx,
                plan_id=plan.id,
                features={f"{code}.core.enabled": True},
                included_seats=3,
                effective_from=timezone.now() - timedelta(days=1),
            )
            catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
            pricing.publish_price(
                ctx,
                price_book_id=catalog["price_book"].id,
                plan_version_id=version.id,
                interval=BillingInterval.MONTHLY,
                amount_minor=999,
                valid_from=timezone.now() - timedelta(days=1),
            )
            catalog_services.configure_product_trial(
                ctx,
                product_id=product.id,
                enabled=True,
                duration_days=7,
                eligible_plan_codes=[f"{code}_team"],
            )
            catalog["versions"][f"{code}_team"] = version
    assert CurrencyCode.GBP  # priced in the fixture's currency

    for plan in ("crm_professional", "books_professional"):
        order = place_and_pay(
            shop["client"],
            shop["slug"],
            shop["id"],
            plan_version_id=str(catalog["versions"][plan].id),
        )
        assert order["status"] == "fulfilled"
    assert start(shop, "desk", "desk_team").status_code == 201

    statuses = {p: decision(shop, p) for p in ("crm", "books", "desk", "vault")}
    assert {p: (d.status.value if d.status else None) for p, d in statuses.items()} == {
        "crm": "active",
        "books": "active",
        "desk": "trial",
        "vault": None,
    }
    assert not statuses["vault"].allowed
    # The trial added no subscription: the two purchases share the one there is.
    assert len(tenant_rows(Subscription, shop["id"])) == 1

    # Trial of the fourth product is independent of the third.
    assert start(shop, "vault", "vault_team").status_code == 201
    assert decision(shop, "vault").status.value == "trial"
    # Ending the third leaves the others untouched.
    ends = next(t.ends_at for t in trials_of(shop) if t.product_code == "desk")
    as_of(shop, ends + timedelta(minutes=1))
    assert not decision(shop, "desk").allowed
    assert decision(shop, "crm").status.value == "active"
    assert decision(shop, "books").status.value == "active"


def test_a_trial_never_makes_a_customer_a_paying_customer(shop):
    from workeasy360.commercial.provisioning.models import CustomerSummary

    start(shop)
    summary = CustomerSummary.objects.get(organization_id=shop["id"])
    assert summary.access_class == "trial"
    assert "crm" in summary.product_codes


def test_starting_a_trial_writes_one_audit_entry_without_secrets(shop):
    body = start(shop).json()
    rows = [
        r
        for r in audit_of(body["id"], "action", "changes", organization_id=shop["id"])
        if r["action"] == "commercial.product_trial.started"
    ]
    assert len(rows) == 1
    changes = rows[0]["changes"]
    assert changes["product_code"] == "crm" and changes["seats"] == 2
    assert not any("key" in k or "token" in k or "secret" in k for k in changes)


def test_the_started_by_user_is_the_signed_in_user_not_the_request(shop):
    start(shop)
    assert trials_of(shop)[0].started_by_user_id == shop["owner"].id


def test_post_helper_is_the_tests_own_json_client(shop):
    """``post`` (conftest) is what later suites use; keep it honest here."""
    response = post(
        shop["client"],
        url(shop["slug"], "trials"),
        {"product_code": "crm", "plan_code": "crm_starter"},
    )
    assert response.status_code == 201

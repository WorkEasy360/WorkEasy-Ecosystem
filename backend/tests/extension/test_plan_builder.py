"""The plan builder and the marketplace (Phase 8 extension, section 5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone as dt_timezone

import pytest

from tests.extension.conftest import pay_order, url

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin/catalog"


def _post(client, path, body):
    return client.post(f"{ADMIN}/{path}", body, content_type="application/json")


@pytest.fixture
def builder(staff_login, catalog):
    client, _ = staff_login(
        "platform.staff_catalog.manage", "platform.staff_pricing.manage"
    )
    return {"client": client, "catalog": catalog}


def test_the_semi_annual_cycle_is_six_months():
    from workeasy360.commercial.subscriptions.domain import advance_period
    from workeasy360.contracts.enums import BillingInterval

    assert BillingInterval.SEMI_ANNUAL.months == 6
    start = datetime(2026, 1, 31, tzinfo=dt_timezone.utc)
    end = datetime(2026, 7, 31, tzinfo=dt_timezone.utc)
    rolled = advance_period(
        start=start,
        end=end,
        interval=BillingInterval.SEMI_ANNUAL,
        at=datetime(2026, 8, 1, tzinfo=dt_timezone.utc),
    )
    assert rolled == (end, datetime(2027, 1, 31, tzinfo=dt_timezone.utc))


def test_a_free_trial_plan_is_built_published_and_listed(builder):
    client, catalog = builder["client"], builder["catalog"]
    created = _post(
        client,
        "plans",
        {
            "product_id": str(catalog["products"]["crm"].id),
            "code": "crm_trial",
            "name": "CRM Trial",
            "caption": "Try it for two weeks",
            "billing_type": "free_trial",
            "description": "**Everything** in Professional.\n- No card needed",
        },
    )
    assert created.status_code == 201, created.content
    plan_id = created.json()["id"]

    no_trial = _post(
        client,
        "plan-versions",
        {"plan_id": plan_id, "included_seats": 3, "trial_days": 0, "publish": True},
    )
    assert (no_trial.status_code, no_trial.json()["field"]) == (400, "trial_days")
    version = _post(
        client,
        "plan-versions",
        {
            "plan_id": plan_id,
            "included_seats": 3,
            "max_seats": 3,
            "trial_days": 14,
            "publish": True,
        },
    )
    assert version.status_code == 201 and version.json()["published"] is True
    for interval in ("monthly", "semi_annual"):
        price = _post(
            client,
            "prices",
            {
                "price_book_id": str(catalog["price_book"].id),
                "plan_version_id": version.json()["id"],
                "interval": interval,
                "amount_minor": 0,
            },
        )
        assert price.status_code == 201, price.content
    assert (
        _post(client, f"plans/{plan_id}/status", {"status": "available"}).status_code
        == 200
    )

    overview = client.get(f"{ADMIN}/overview").json()
    listed = next(p for p in overview["plans"] if p["code"] == "crm_trial")
    assert (
        listed["caption"] == "Try it for two weeks"
        and listed["billing_type"] == "free_trial"
    )
    assert {p["interval"] for p in listed["versions"][0]["prices"]} == {
        "monthly",
        "semi_annual",
    }
    assert overview["intervals"] == ["monthly", "quarterly", "semi_annual", "annual"]
    assert "GBP" in overview["currencies"]


def test_presentation_edits_are_audited_and_the_type_freezes_once_on_sale(
    builder, staff_login
):
    client, catalog = builder["client"], builder["catalog"]
    plan_id = str(catalog["plans"]["crm_starter"].id)
    edited = client.patch(
        f"{ADMIN}/plans/{plan_id}",
        {"caption": "For small teams", "is_public": False},
        content_type="application/json",
    )
    assert edited.status_code == 200 and edited.json()["is_public"] is False
    frozen = client.patch(
        f"{ADMIN}/plans/{plan_id}",
        {"billing_type": "free_trial"},
        content_type="application/json",
    )
    assert (frozen.status_code, frozen.json()["field"]) == (400, "billing_type")
    too_long = client.patch(
        f"{ADMIN}/plans/{plan_id}",
        {"caption": "x" * 161},
        content_type="application/json",
    )
    assert too_long.status_code == 400
    two_lines = client.patch(
        f"{ADMIN}/plans/{plan_id}",
        {"caption": "one\ntwo"},
        content_type="application/json",
    )
    assert two_lines.status_code == 400

    viewer, _ = staff_login("platform.staff_catalog.view")
    assert viewer.get(f"{ADMIN}/overview").status_code == 200
    assert (
        viewer.patch(
            f"{ADMIN}/plans/{plan_id}", {"caption": "no"}, content_type="application/json"
        ).status_code
        == 403
    )


def _open_account(owner, slug, currency="GBP"):
    return owner.post(
        url(slug, "billing-account"),
        {"currency": currency},
        content_type="application/json",
    )


@pytest.fixture(autouse=True)
def _test_provider(fake_payments):
    """Paid purchases in this suite are paid on the development provider."""
    return fake_payments


def _purchase(owner, slug, **body):
    """Place an order; pay it on the test provider when it needs payment
    (Phase 9: buying is pay-first). Returns the placing response, or the paid
    order's final state as a response-shaped object."""
    placed = owner.post(
        url(slug, "orders"),
        body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    if placed.status_code != 201 or not placed.json()["requires_payment"]:
        return placed
    from workeasy360.platform.organizations.models import Organization

    organization_id = Organization.objects.get(slug=slug).id
    order = pay_order(owner, slug, organization_id, placed.json())
    assert order["status"] == "fulfilled", order
    return placed


def _addon(builder):
    client, catalog = builder["client"], builder["catalog"]
    created = _post(
        client,
        "plans",
        {
            "product_id": str(catalog["products"]["books"].id),
            "code": "books_extra_ledgers",
            "name": "Extra ledgers",
            "kind": "add_on",
            "addon_for_product_id": str(catalog["products"]["crm"].id),
        },
    )
    assert created.status_code == 201, created.content
    version = _post(
        client,
        "plan-versions",
        {"plan_id": created.json()["id"], "included_seats": 1, "publish": True},
    ).json()
    _post(
        client,
        "prices",
        {
            "price_book_id": str(catalog["price_book"].id),
            "plan_version_id": version["id"],
            "interval": "monthly",
            "amount_minor": 500,
        },
    )
    _post(client, f"plans/{created.json()['id']}/status", {"status": "available"})
    return version["id"]


def test_an_add_on_needs_its_product_and_never_joins_a_bundle(
    builder, console_org, login
):
    client, catalog = builder["client"], builder["catalog"]
    bad = _post(
        client,
        "plans",
        {
            "product_id": str(catalog["products"]["crm"].id),
            "code": "self_addon",
            "name": "x",
            "kind": "add_on",
            "addon_for_product_id": str(catalog["products"]["crm"].id),
        },
    )
    assert bad.status_code == 400
    orphan = _post(
        client,
        "plans",
        {
            "product_id": str(catalog["products"]["crm"].id),
            "code": "orphan",
            "name": "x",
            "kind": "add_on",
        },
    )
    assert orphan.status_code == 400
    addon_version = _addon(builder)

    bundle = _post(
        client, "bundles", {"code": "with_addon", "name": "With add-on"}
    ).json()
    refused = _post(
        client,
        "bundle-versions",
        {"bundle_id": bundle["id"], "items": [{"plan_version_id": addon_version}]},
    )
    assert refused.status_code == 400

    org = console_org()
    owner = login(org["owner"])
    _open_account(owner, org["slug"])
    alone = _purchase(owner, org["slug"], plan_version_id=addon_version)
    assert (alone.status_code, alone.json()["reason"]) == (409, "add_on_requires_base")
    assert _purchase(
        owner, org["slug"], plan_version_id=str(catalog["versions"]["crm_starter"].id)
    ).status_code in (200, 201)
    assert _purchase(owner, org["slug"], plan_version_id=addon_version).status_code in (
        200,
        201,
    )


def test_a_free_trial_opens_a_new_subscription_once(builder, console_org, login):
    client, catalog = builder["client"], builder["catalog"]
    plan = _post(
        client,
        "plans",
        {
            "product_id": str(catalog["products"]["books"].id),
            "code": "books_trial",
            "name": "Books Trial",
            "billing_type": "free_trial",
        },
    ).json()
    version = _post(
        client,
        "plan-versions",
        {"plan_id": plan["id"], "included_seats": 1, "trial_days": 7, "publish": True},
    ).json()
    _post(
        client,
        "prices",
        {
            "price_book_id": str(catalog["price_book"].id),
            "plan_version_id": version["id"],
            "interval": "monthly",
            "amount_minor": 0,
        },
    )
    _post(client, f"plans/{plan['id']}/status", {"status": "available"})

    org = console_org()
    owner = login(org["owner"])
    _open_account(owner, org["slug"])
    started = _purchase(owner, org["slug"], plan_version_id=version["id"])
    assert started.status_code in (200, 201), started.content
    subscription = owner.get(url(org["slug"], "subscriptions")).json()["results"][0]
    assert subscription["status"] == "trialing"
    again = _purchase(owner, org["slug"], plan_version_id=version["id"])
    assert again.status_code == 409  # already has a subscription (and the trial)

    paying = console_org()
    paying_owner = login(paying["owner"])
    _open_account(paying_owner, paying["slug"])
    _purchase(
        paying_owner,
        paying["slug"],
        plan_version_id=str(catalog["versions"]["crm_starter"].id),
    )
    late = _purchase(paying_owner, paying["slug"], plan_version_id=version["id"])
    assert (late.status_code, late.json()["reason"]) == (409, "subscription_exists")


def test_the_marketplace_lists_what_can_be_bought_in_the_organizations_currency(
    builder, console_org, login
):
    client, catalog = builder["client"], builder["catalog"]
    client.patch(
        f"{ADMIN}/plans/{catalog['plans']['books_professional'].id}",
        {"is_public": False},
        content_type="application/json",
    )
    draft = _post(
        client,
        "plans",
        {
            "product_id": str(catalog["products"]["crm"].id),
            "code": "crm_draft",
            "name": "Draft",
        },
    ).json()
    _post(
        client,
        "plan-versions",
        {"plan_id": draft["id"], "included_seats": 1, "publish": True},
    )
    _post(
        client,
        "prices",
        {
            "price_book_id": str(catalog["price_book"].id),
            "plan_version_id": str(catalog["versions"]["crm_starter"].id),
            "interval": "semi_annual",
            "amount_minor": 7999,
        },
    )

    org = console_org()
    member = org["add"]("member")
    owner = login(org["owner"])
    before = login(member["user"]).get(url(org["slug"], "marketplace")).json()
    assert before["currency"] is None and before["billing_account"] is False
    assert all(p["prices"] == [] for p in before["plans"])

    _open_account(owner, org["slug"])
    offers = login(member["user"]).get(url(org["slug"], "marketplace")).json()
    codes = {p["plan_code"] for p in offers["plans"]}
    assert {"crm_starter", "crm_professional"} <= codes
    assert "books_professional" not in codes  # private
    assert "crm_draft" not in codes  # never made available
    starter = next(p for p in offers["plans"] if p["plan_code"] == "crm_starter")
    assert {(p["interval"], p["amount_minor"]) for p in starter["prices"]} == {
        ("monthly", 1499),
        ("semi_annual", 7999),
    }
    suite = next(b for b in offers["bundles"] if b["bundle_code"] == "sales_suite")
    assert suite["prices"][0]["amount_minor"] == 6999
    assert {i["product_code"] for i in suite["includes"]} == {"crm", "books"}

    _purchase(
        owner, org["slug"], plan_version_id=str(catalog["versions"]["crm_starter"].id)
    )
    assert owner.get(url(org["slug"], "marketplace")).json()["held_products"] == ["crm"]


def test_bundle_status_and_late_publication(builder):
    client, catalog = builder["client"], builder["catalog"]
    bundle = _post(
        client,
        "bundles",
        {
            "code": "starter_suite",
            "name": "Starter suite",
            "caption": "Two apps",
            "billing_type": "paid",
        },
    ).json()
    draft = _post(
        client,
        "bundle-versions",
        {
            "bundle_id": bundle["id"],
            "items": [{"plan_version_id": str(catalog["versions"]["crm_starter"].id)}],
        },
    ).json()
    assert draft["published"] is False
    early = _post(client, f"bundles/{bundle['id']}/status", {"status": "available"})
    assert early.status_code == 400  # nothing published yet
    assert _post(client, f"bundle-versions/{draft['id']}/publish", {}).status_code == 200
    assert (
        _post(client, f"bundle-versions/{draft['id']}/publish", {}).status_code == 409
    )  # immutable
    assert (
        _post(
            client, f"bundles/{bundle['id']}/status", {"status": "available"}
        ).status_code
        == 200
    )
    listed = next(
        b
        for b in client.get(f"{ADMIN}/overview").json()["bundles"]
        if b["code"] == "starter_suite"
    )
    assert listed["status"] == "available" and listed["caption"] == "Two apps"


def test_a_published_bundle_versions_trial_is_frozen(builder):
    from django.db import IntegrityError, InternalError, transaction

    from workeasy360.commercial.catalog.models import BundleVersion

    version = BundleVersion.objects.get(id=builder["catalog"]["bundle_version"].id)
    with pytest.raises((IntegrityError, InternalError)), transaction.atomic():
        BundleVersion.objects.filter(id=version.id).update(trial_days=30)

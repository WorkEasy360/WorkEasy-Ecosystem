"""ADR-078 (with ADR-073's buyer seat): one activation for paid purchases, renewals and
trials; the Super Admin's trial eligibility and repeat rules; the central
default product-role policy delivered at sign-in.

Same fixtures as ``test_automatic_trials``: the four real product codes, each
with a priced per-seat plan, a trial and an active integration.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.auto_trial.test_automatic_trials import (
    _access,
    _activate,
    _launch,
    _seats,
    _trials,
)
from tests.extension.conftest import place_and_pay, staff_scope, url
from tests.phase11 import conftest as _phase11
from tests.phase11 import test_11i_all_four_products as _four
from tests.phase11.conftest import CATALOG_STAFF, audit_of
from workeasy360.platform.entitlements.selectors import decision_for_organization

pytestmark = pytest.mark.django_db

console_org = _phase11.console_org
fake_payments = _phase11.fake_payments
login = _phase11.login
staff_client = _phase11.staff_client
http_login = _phase11.http_login
four_products = _four.four_products
shop_for = _four.shop_for

FOUR_PRODUCTS = _four.FOUR_PRODUCTS


def _configure(made, **policy):
    from workeasy360.commercial.catalog import services as catalog_services

    with staff_scope(*CATALOG_STAFF) as ctx:
        catalog_services.configure_product_trial(
            ctx,
            product_id=made["product"].id,
            enabled=True,
            duration_days=14,
            eligible_plan_codes=[made["plan_code"]],
            **policy,
        )


# --- 1. a direct paid purchase opens with nobody else ---------------------------------------


@pytest.mark.parametrize("code", FOUR_PRODUCTS)
def test_a_paid_purchase_seats_the_buyer_and_opens(code, shop_for, four_products):
    shop = shop_for()
    client, slug = shop["client"], shop["slug"]
    assert _access(client, slug)[code]["action"] == "start_trial"

    order = place_and_pay(
        client,
        slug,
        shop["id"],
        plan_version_id=str(four_products[code]["plan_version_id"]),
    )
    assert order["status"] == "fulfilled", order

    assert decision_for_organization(shop["id"], code).status.value == "active"
    assert [s.membership_id for s in _seats(shop["id"], code)] == [shop["membership"].id]
    card = _access(client, slug)[code]
    assert card["action"] == "open" and card["holding"] == "subscription"
    assert _launch(client, slug, code).status_code == 200
    seated = [
        row
        for row in audit_of(
            _seats(shop["id"], code)[0].id,
            "action",
            "changes",
            organization_id=shop["id"],
        )
        if row["action"] == "commercial.order.buyer_seat_assigned"  # ADR-073 G2
    ]
    assert seated and seated[0]["changes"]["product_code"] == code


def test_a_purchase_seats_only_the_buyer(shop_for, four_products, login):
    shop = shop_for()
    other = shop["add"]("member")["membership"]
    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=str(four_products["easy_crm"]["plan_version_id"]),
    )
    held = {s.membership_id for s in _seats(shop["id"], "easy_crm")}
    assert held == {shop["membership"].id}
    assert other.id not in held


def test_buying_twice_never_seats_twice_or_duplicates(shop_for, four_products):
    shop = shop_for()
    plan = str(four_products["easybook"]["plan_version_id"])
    place_and_pay(shop["client"], shop["slug"], shop["id"], plan_version_id=plan)
    again = shop["client"].post(
        url(shop["slug"], "orders"),
        {"plan_version_id": plan, "billing_interval": "monthly"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert again.status_code >= 400  # already held: no second subscription line
    assert len(_seats(shop["id"], "easybook")) == 1


def test_a_renewal_keeps_access_and_the_seat(shop_for, four_products, monkeypatch):
    """The clock moves past the paid period: the sweep renews (or lets it fall
    due); the buyer's seat and the product's access stay - nothing to redo."""
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    shop = shop_for()
    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=str(four_products["easy_project"]["plan_version_id"]),
    )
    later = timezone.now() + timedelta(days=32)
    monkeypatch.setattr(timezone, "now", lambda: later)
    process_due_subscriptions(at=later)
    decision = decision_for_organization(shop["id"], "easy_project")
    assert decision.allowed, decision
    assert [s.membership_id for s in _seats(shop["id"], "easy_project")] == [
        shop["membership"].id
    ]


def test_a_free_plan_opens_like_a_paid_one(shop_for, four_products):
    """A zero-amount order (no payment step) seats the person who placed it,
    with the same rules as a paid order's buyer (ADR-073 G2, extended)."""
    from datetime import timedelta as _td

    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.pricing import services as pricing_services
    from workeasy360.commercial.pricing.models import PriceBook
    from workeasy360.contracts.enums import BillingInterval, CatalogStatus

    made = four_products["easybook"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        plan = catalog_services.create_plan(
            ctx, product_id=made["product"].id, code="easybook_free", name="Free"
        )
        catalog_services.set_plan_status(
            ctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE
        )
        version = catalog_services.draft_plan_version(
            ctx,
            plan_id=plan.id,
            features={"easybook.core.enabled": True},
            included_seats=2,
            effective_from=timezone.now() - _td(days=1),
        )
        catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
        pricing_services.publish_price(
            ctx,
            price_book_id=PriceBook.objects.get(code="direct_gbp_11i").id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=0,
            valid_from=timezone.now() - _td(days=1),
        )
    shop = shop_for()
    order = place_and_pay(
        shop["client"], shop["slug"], shop["id"], plan_version_id=str(version.id)
    )
    assert order["status"] == "fulfilled" and not order["requires_payment"], order
    assert [s.membership_id for s in _seats(shop["id"], "easybook")] == [
        shop["membership"].id
    ]
    assert _access(shop["client"], shop["slug"])["easybook"]["action"] == "open"


def test_a_failed_seat_never_fails_the_purchase(shop_for, four_products, monkeypatch):
    from workeasy360.platform.entitlements.services import seats

    def boom(*args, **kwargs):
        raise RuntimeError("seat store down")

    monkeypatch.setattr(seats, "assign_seat", boom)
    shop = shop_for()
    order = place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=str(four_products["easy_helpdesk"]["plan_version_id"]),
    )
    assert order["status"] == "fulfilled"
    assert _seats(shop["id"], "easy_helpdesk") == []
    monkeypatch.undo()
    # The Store says what is left, and one click finishes it (the owner may seat).
    card = _access(shop["client"], shop["slug"])["easy_helpdesk"]
    assert card["action"] == "retry_setup" and card["reason"] == "seat_missing"
    fixed = shop["client"].post(url(shop["slug"], "product-access/easy_helpdesk/repair"))
    assert fixed.json()["access"]["action"] == "open"


# --- 2. the trial policy ----------------------------------------------------------------------


def test_the_default_policy_is_one_trial_per_organization_and_product(
    shop_for, four_products
):
    shop = shop_for()
    from workeasy360.commercial.catalog.selectors import trial_offer

    offer = trial_offer("easy_crm")
    assert (offer.repeat_policy, offer.eligibility, offer.one_trial_per_person) == (
        "never",
        "all_organizations",
        False,
    )
    assert _activate(shop["client"], shop["slug"], "easy_crm").status_code == 200
    # A trial that ended is never repeated under the default rule.
    trial = _trials(shop["id"], "easy_crm")[0]
    from workeasy360.commercial.subscriptions.system import settle_product_trials

    settle_product_trials(shop["id"], at=trial.ends_at)
    again = shop["client"].post(
        url(shop["slug"], "trials"),
        {"product_code": "easy_crm", "plan_code": four_products["easy_crm"]["plan_code"]},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert again.status_code == 409 and again.json()["reason"] == "already_used"


def test_a_repeat_after_a_cooldown(shop_for, four_products, login, monkeypatch):
    _configure(
        four_products["easybook"], repeat_policy="after_cooldown", repeat_after_days=30
    )
    shop = shop_for()
    slug = shop["slug"]
    assert (
        _activate(shop["client"], slug, "easybook").json()["access"]["action"] == "open"
    )
    trial = _trials(shop["id"], "easybook")[0]

    # Never two at once.
    second = _activate(shop["client"], slug, "easybook")
    assert second.status_code == 200 and second.json()["trial"]["id"] == str(trial.id)

    # Ended, not swept, inside the cooldown: refused with the date it opens.
    within = trial.ends_at + timedelta(days=10)
    monkeypatch.setattr(timezone, "now", lambda: within)
    owner = login(shop["owner"])
    assert _access(owner, slug)["easybook"]["action"] == "upgrade"
    soon = _activate(owner, slug, "easybook")
    assert soon.status_code == 409 and soon.json()["reason"] == "repeat_too_soon"
    assert soon.json()["available_from"].startswith(
        (trial.ends_at + timedelta(days=30)).date().isoformat()
    )

    # After the cooldown: offered again, starts, the old one is settled first.
    after = trial.ends_at + timedelta(days=31)
    monkeypatch.setattr(timezone, "now", lambda: after)
    owner = login(shop["owner"])
    assert _access(owner, slug)["easybook"]["action"] == "start_trial"
    again = _activate(owner, slug, "easybook")
    assert again.status_code == 200, again.content
    assert again.json()["access"]["action"] == "open"
    statuses = sorted(t.status for t in _trials(shop["id"], "easybook"))
    assert statuses == ["active", "expired"]


def test_the_database_still_refuses_two_running_trials(
    shop_for, four_products, monkeypatch
):
    """Even if the policy check were bypassed (a race), one running trial per
    organization and product is the database's rule."""
    from workeasy360.commercial.subscriptions import trials

    _configure(
        four_products["easy_crm"], repeat_policy="after_cooldown", repeat_after_days=1
    )
    shop = shop_for()
    assert _activate(shop["client"], shop["slug"], "easy_crm").status_code == 200
    monkeypatch.setattr(trials, "trial_policy_refusal", lambda *a, **k: None)
    raced = shop["client"].post(
        url(shop["slug"], "trials"),
        {"product_code": "easy_crm", "plan_code": four_products["easy_crm"]["plan_code"]},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert raced.status_code == 409 and raced.json()["reason"] == "already_used"
    assert len(_trials(shop["id"], "easy_crm")) == 1


def test_one_trial_per_person_across_their_organizations(
    new_person_two_orgs, four_products
):
    first, second, client = new_person_two_orgs
    _configure(four_products["easy_project"], one_trial_per_person=True)
    assert _activate(client, first, "easy_project").status_code == 200
    refused = _activate(client, second, "easy_project")
    assert refused.status_code == 409
    assert refused.json()["reason"] == "already_used_elsewhere"
    # The default (off): a second organization may trial it.
    _configure(four_products["easy_project"], one_trial_per_person=False)
    assert _activate(client, second, "easy_project").status_code == 200


@pytest.fixture
def new_person_two_orgs(four_products, user_factory, login, fake_payments):
    from tests.extension.conftest import PASSWORD
    from workeasy360.platform.onboarding.services import create_organization

    person = user_factory(password=PASSWORD, full_name="Ravi Shah")
    one = create_organization(user_id=person.id, name="Ravi One")
    two = create_organization(user_id=person.id, name="Ravi Two")
    return one.slug, two.slug, login(person)


def test_new_customers_only_refuses_an_organization_that_paid(shop_for, four_products):
    from workeasy360.commercial.catalog.selectors import trial_offer
    from workeasy360.commercial.subscriptions import selectors, trials
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    _configure(four_products["easy_helpdesk"], eligibility="new_customers")
    shop = shop_for()
    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=str(four_products["easy_helpdesk"]["plan_version_id"]),
    )
    ctx = TenantContext(
        organization_id=shop["id"],
        membership_id=shop["membership"].id,
        user_id=shop["owner"].id,
    )
    with tenant_context(ctx), organization_db_scope(shop["id"]):
        assert selectors.has_paid_for(shop["id"], product_code="easy_helpdesk")
        refusal = trials.trial_policy_refusal(
            ctx, "easy_helpdesk", trial_offer("easy_helpdesk"), timezone.now()
        )
    assert refusal is not None and refusal.context["reason"] == "not_eligible"


# --- 3. Super Admin configures it ------------------------------------------------------------


def test_super_admin_sets_the_trial_policy(staff_client, four_products):
    client, _user = staff_client(
        "platform.staff_catalog.manage", "platform.staff_catalog.view"
    )
    product_id = four_products["easy_crm"]["product"].id
    path = f"/api/v1/admin/catalog/products/{product_id}/trial"
    body = {
        "enabled": True,
        "duration_days": 21,
        "eligible_plan_codes": [four_products["easy_crm"]["plan_code"]],
        "repeat_policy": "after_cooldown",
        "repeat_after_days": 90,
        "eligibility": "new_customers",
        "one_trial_per_person": True,
    }
    saved = client.put(path, body, content_type="application/json")
    assert saved.status_code == 200, saved.content
    assert {k: saved.json()[k] for k in body if k != "eligible_plan_codes"} == {
        k: v for k, v in body.items() if k != "eligible_plan_codes"
    }
    # A caller naming only the terms (the Product Publisher) keeps the policy.
    terms_only = {k: body[k] for k in ("enabled", "duration_days", "eligible_plan_codes")}
    kept = client.put(path, terms_only, content_type="application/json")
    assert kept.json()["repeat_policy"] == "after_cooldown"
    assert kept.json()["one_trial_per_person"] is True
    for bad in (
        {"repeat_policy": "sometimes"},
        {"repeat_policy": "after_cooldown", "repeat_after_days": 0},
        {"eligibility": "everyone"},
        {"one_trial_per_person": "yes"},
    ):
        refused = client.put(path, {**terms_only, **bad}, content_type="application/json")
        assert refused.status_code == 400, bad


def test_a_customer_cannot_set_a_trial_policy(shop_for, four_products):
    shop = shop_for()
    product_id = four_products["easy_crm"]["product"].id
    for path in (
        f"/api/v1/admin/catalog/products/{product_id}/trial",
        f"/api/v1/admin/catalog/products/{product_id}/role-policy",
    ):
        answer = shop["client"].put(
            path, {"repeat_policy": "after_cooldown"}, content_type="application/json"
        )
        assert answer.status_code in (401, 403, 404), (path, answer.status_code)


# --- 4. the default product-role policy --------------------------------------------------


def test_the_platform_default_role_policy():
    from workeasy360.commercial.catalog.domain import resolve_product_role

    assert resolve_product_role({}, ["owner"]) == "admin"
    assert resolve_product_role({}, ["admin", "member"]) == "admin"
    assert resolve_product_role({}, ["billing_manager"]) == "member"
    assert resolve_product_role({}, ["readonly"]) == "viewer"
    assert resolve_product_role({}, []) == "member"
    assert resolve_product_role({"member": "agent"}, ["member"]) == "agent"


def test_super_admin_sets_a_products_role_policy_but_never_a_platform_role(
    staff_client, four_products
):
    client, _user = staff_client(
        "platform.staff_catalog.manage", "platform.staff_catalog.view"
    )
    product_id = four_products["easy_helpdesk"]["product"].id
    path = f"/api/v1/admin/catalog/products/{product_id}/role-policy"
    shown = client.get(path)
    assert shown.status_code == 200, shown.content
    assert shown.json()["effective"]["owner"] == "admin" and shown.json()["custom"] == {}
    saved = client.put(
        path,
        {"policy": {"member": "agent", "readonly": "viewer"}},
        content_type="application/json",
    )
    assert saved.status_code == 200, saved.content
    assert saved.json()["effective"]["member"] == "agent"
    for bad in (
        {"owner": "super_admin"},
        {"owner": "platform_admin"},
        {"owner": "Staff"},
        {"ceo": "admin"},
    ):
        refused = client.put(path, {"policy": bad}, content_type="application/json")
        assert refused.status_code == 400, bad
    restored = client.put(path, {"policy": {}}, content_type="application/json")
    assert restored.json()["custom"] == {}


def test_the_id_token_carries_the_product_role(shop_for, four_products, login):
    """What the product reads at first sign-in: the person's organization roles
    and the product role from the policy - owner -> admin, a member -> the
    policy's member role, a removed member -> nothing, no scope -> nothing."""
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.interfaces.api.v1.ecosystem_views import _role_claims

    with staff_scope(*CATALOG_STAFF) as ctx:
        catalog_services.set_product_role_policy(
            ctx,
            product_id=four_products["easy_crm"]["product"].id,
            policy={"member": "sales_rep"},
        )
    shop = shop_for()
    member = shop["add"]("member")["user"]
    scope = ("openid", "organization")
    owner_claims = _role_claims(shop["owner"].id, shop["id"], "easy_crm", scope)
    assert owner_claims["product_role"] == "admin"
    assert "owner" in owner_claims["organization_roles"]
    assert (
        _role_claims(member.id, shop["id"], "easy_crm", scope)["product_role"]
        == "sales_rep"
    )
    assert (
        _role_claims(member.id, shop["id"], "easybook", scope)["product_role"] == "member"
    )
    assert _role_claims(shop["owner"].id, shop["id"], "easy_crm", ("openid",)) == {}
    stranger = shop_for()
    assert _role_claims(stranger["owner"].id, shop["id"], "easy_crm", scope) == {}

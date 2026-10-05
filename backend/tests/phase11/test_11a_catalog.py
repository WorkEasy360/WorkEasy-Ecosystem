"""11a - product presentation (category, icon), per-product trial configuration,
the public catalog's new fields, and the idempotent registration command."""

from __future__ import annotations

import json

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.phase11.conftest import CATALOG_STAFF, audit_of, put, staff_scope
from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.subscriptions import marketplace
from workeasy360.contracts.enums import CatalogStatus
from workeasy360.contracts.errors import CatalogValidationError

pytestmark = pytest.mark.django_db


def public_product(code):
    cache.clear()
    view = marketplace.public_catalog()
    return next(p for p in view["products"] if p["code"] == code)


def update(ctx, product_id, changes):
    return catalog_services.update_product(ctx, product_id=product_id, changes=changes)


# --- category and icon ----------------------------------------------------------------


def test_a_product_carries_a_category_and_an_allow_listed_icon(catalog):
    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        product = update(
            ctx, crm.id, {"category": "  Sales /   CRM ", "icon": "Customers"}
        )
    assert product.category == "Sales / CRM"
    assert product.icon == "customers"
    seen = public_product("crm")
    assert seen["category"] == "Sales / CRM"
    assert seen["icon"] == "customers"
    # licensing_mode is an internal billing detail (per-seat vs per-org), not
    # a marketing fact - the anonymous catalog never carries it (Phase 9's
    # own leak test guards this at the HTTP layer; this is the same rule at
    # the selector this endpoint is built from).
    assert "licensing_mode" not in seen


@pytest.mark.parametrize(
    "icon",
    [
        "https://evil.example/x.png",
        "javascript:alert(1)",
        "data:image/svg+xml;base64,AAAA",
        "<svg onload=alert(1)>",
        "../../etc/passwd",
        "not-in-the-list",
        "x" * 200,
    ],
)
def test_an_icon_is_never_a_url_or_markup(catalog, icon):
    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        with pytest.raises(CatalogValidationError):
            update(ctx, crm.id, {"icon": icon})
    crm.refresh_from_db()
    assert crm.icon == ""


@pytest.mark.parametrize(
    "category", ["x" * 65, "line" + chr(0) + "break", "bell" + chr(7) + "ring"]
)
def test_a_category_is_short_plain_text(catalog, category):
    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        with pytest.raises(CatalogValidationError):
            update(ctx, crm.id, {"category": category})


def test_editing_presentation_is_audited(catalog):
    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        update(ctx, crm.id, {"category": "Sales", "icon": "chart"})
        rows = audit_of(crm.id, "action", "changes")
    updated = [r for r in rows if r["action"] == "commercial.product.updated"]
    assert updated
    assert updated[-1]["changes"]["after"] == {"category": "Sales", "icon": "chart"}


# --- per-product trial configuration ---------------------------------------------------


def configure(ctx, product, **overrides):
    terms = {
        "enabled": True,
        "duration_days": 14,
        "seat_limit": None,
        "payment_method_required": False,
        "eligible_plan_codes": ["crm_starter"],
    }
    terms.update(overrides)
    return catalog_services.configure_product_trial(ctx, product_id=product.id, **terms)


def test_there_is_no_trial_until_staff_configure_one(catalog):
    trial = public_product("crm")["trial"]
    assert trial["available"] is False
    assert trial["duration_days"] is None
    assert trial["plan_codes"] == []
    assert all(r["trial"] is None for r in catalog_selectors.visible_products())


def test_a_configured_trial_is_offered_only_on_its_eligible_plans(catalog):
    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        configure(ctx, crm, duration_days=21, seat_limit=3)
    seen = public_product("crm")
    assert seen["trial"] == {
        "available": True,
        "duration_days": 21,
        "seat_limit": 3,
        "payment_method_required": False,
        "plan_codes": ["crm_starter"],
    }
    by_code = {p["plan_code"]: p for p in seen["plans"]}
    assert by_code["crm_starter"]["trial_available"] is True
    assert by_code["crm_professional"]["trial_available"] is False
    # Another product is untouched.
    assert public_product("books")["trial"]["available"] is False


def test_a_switched_off_trial_is_not_offered_but_is_remembered(catalog):
    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        configure(ctx, crm, enabled=False)
    assert public_product("crm")["trial"]["available"] is False
    row = next(r for r in catalog_selectors.visible_products() if r["code"] == "crm")
    assert row["trial"]["enabled"] is False
    assert row["trial"]["eligible_plan_codes"] == ["crm_starter"]


def test_a_trial_whose_plan_is_not_on_sale_is_not_offered(catalog):
    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        configure(ctx, crm)
        catalog_services.set_plan_status(
            ctx,
            plan_id=catalog["plans"]["crm_starter"].id,
            status=CatalogStatus.GRANDFATHERED,
        )
    assert public_product("crm")["trial"]["available"] is False


@pytest.mark.parametrize(
    ("terms", "field"),
    [
        ({"enabled": "yes"}, "enabled"),
        ({"duration_days": 0}, "duration_days"),
        ({"duration_days": 366}, "duration_days"),
        ({"duration_days": True}, "duration_days"),
        ({"duration_days": "14"}, "duration_days"),
        ({"seat_limit": 0}, "seat_limit"),
        ({"seat_limit": -1}, "seat_limit"),
        ({"seat_limit": True}, "seat_limit"),
        ({"seat_limit": 10**9}, "seat_limit"),
        ({"payment_method_required": True}, "payment_method_required"),
        ({"eligible_plan_codes": []}, "eligible_plan_codes"),
        ({"eligible_plan_codes": "crm_starter"}, "eligible_plan_codes"),
        ({"eligible_plan_codes": [1]}, "eligible_plan_codes"),
        ({"eligible_plan_codes": ["nope"]}, "eligible_plan_codes"),
        # a real plan, but of another product
        ({"eligible_plan_codes": ["books_professional"]}, "eligible_plan_codes"),
    ],
)
def test_bad_trial_terms_are_refused_and_nothing_is_stored(catalog, terms, field):
    from workeasy360.commercial.catalog.models import ProductTrialConfig

    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        with pytest.raises(CatalogValidationError) as raised:
            configure(ctx, crm, **terms)
    assert field in repr(raised.value.__dict__) or field in str(raised.value)
    assert not ProductTrialConfig.objects.exists()


def test_a_payment_method_requirement_is_refused_honestly(catalog):
    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        with pytest.raises(CatalogValidationError) as raised:
            configure(ctx, crm, payment_method_required=True)
    assert "stores none" in str(raised.value)


def test_configuring_a_trial_is_audited_with_before_and_after(catalog):
    crm = catalog["products"]["crm"]
    with staff_scope(*CATALOG_STAFF) as ctx:
        configure(ctx, crm)
        configure(ctx, crm, duration_days=30)
        configure(ctx, crm, duration_days=30)  # unchanged: no third entry
        rows = [
            r
            for r in audit_of(crm.id, "action", "changes")
            if r["action"] == "commercial.product.trial_configured"
        ]
    assert len(rows) == 2
    assert rows[0]["changes"]["before"] is None
    assert rows[1]["changes"]["before"]["duration_days"] == 14
    assert rows[1]["changes"]["after"]["duration_days"] == 30


def test_only_staff_holding_the_catalog_grant_may_configure(catalog, staff_login):
    crm = catalog["products"]["crm"]
    path = f"/api/v1/admin/catalog/products/{crm.id}/trial"
    body = {
        "enabled": True,
        "duration_days": 14,
        "eligible_plan_codes": ["crm_starter"],
    }
    outsider, _ = staff_login("platform.staff_customer.view")
    assert put(outsider, path, body).status_code == 403
    manager, _ = staff_login(*CATALOG_STAFF)
    response = put(manager, path, body)
    assert response.status_code == 200, response.content
    assert response.json()["duration_days"] == 14
    assert put(manager, path, {**body, "duration_days": 0}).status_code == 400


def test_a_customer_cannot_configure_a_trial(catalog, console_org, login):
    crm = catalog["products"]["crm"]
    client = login(console_org()["owner"], aal2=True)
    response = put(
        client,
        f"/api/v1/admin/catalog/products/{crm.id}/trial",
        {"enabled": True, "duration_days": 14, "eligible_plan_codes": ["crm_starter"]},
    )
    assert response.status_code in (401, 403, 404)


# --- the registration command ----------------------------------------------------------


def _actor(staff_factory):
    user, _ = staff_factory(
        "platform.staff_catalog.manage", "platform.staff_integration.manage"
    )
    return user.email


def _state():
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.platform.ecosystem.models import ProductIntegration

    return (
        Product.objects.count(),
        ProductIntegration.objects.count(),
        list(
            ProductIntegration.objects.order_by("id").values_list("client_id", flat=True)
        ),
    )


def test_the_command_registers_the_four_products_and_is_idempotent(staff_factory):
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.platform.ecosystem.models import ProductIntegration

    actor = _actor(staff_factory)
    call_command("register_products", actor=actor)
    codes = set(Product.objects.values_list("code", flat=True))
    assert {"easy_crm", "easybook", "easy_project", "easy_helpdesk"} <= codes

    slugs = dict(Product.objects.values_list("code", "slug"))
    assert slugs["easy_crm"] == "easy-crm"
    assert slugs["easy_helpdesk"] == "easy-helpdesk"
    assert slugs["easy_project"] == "easy-project"
    assert slugs["easybook"] == "easybook"

    easy_crm = Product.objects.get(code="easy_crm")
    assert (easy_crm.category, easy_crm.icon) == ("Sales / CRM", "customers")
    assert easy_crm.status == "available"
    # Registered, never active by itself, and never with a credential.
    assert set(ProductIntegration.objects.values_list("status", flat=True)) == {"draft"}
    assert not any(i.credentials.exists() for i in ProductIntegration.objects.all())

    before = _state()
    call_command("register_products", actor=actor)
    assert _state() == before


def test_the_command_invents_no_plan_price_trial_or_credential(staff_factory):
    from workeasy360.commercial.catalog.models import Plan, ProductTrialConfig
    from workeasy360.commercial.pricing.models import PlanPrice

    call_command("register_products", actor=_actor(staff_factory))
    assert not Plan.objects.exists()
    assert not PlanPrice.objects.exists()
    assert not ProductTrialConfig.objects.exists()
    seen = public_product("easy_crm")
    assert seen["plans"] == []
    assert seen["starting_price"] is None
    assert seen["trial"]["available"] is False
    assert [f["name"] for f in seen["features"]] == ["Customer records", "Sales pipeline"]


def test_activation_is_explicit_and_leaves_no_credential(staff_factory):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    call_command(
        "register_products", actor=_actor(staff_factory), activate_integrations=True
    )
    assert set(ProductIntegration.objects.values_list("status", flat=True)) == {"active"}
    assert not any(i.credentials.exists() for i in ProductIntegration.objects.all())
    assert {
        i.product_code: i.manifest["launch_url"] for i in ProductIntegration.objects.all()
    } == {
        "easy_crm": "https://crm.workeasy360.com/",
        "easybook": "https://book.workeasy360.com/",
        "easy_project": "https://ops.workeasy360.com/",
        "easy_helpdesk": "https://help.workeasy360.com/",
    }


def test_the_command_leaves_staff_edits_alone(staff_factory):
    from workeasy360.commercial.catalog.models import Product

    actor = _actor(staff_factory)
    call_command("register_products", actor=actor)
    Product.objects.filter(code="easy_crm").update(name="Renamed", category="Mine")
    call_command("register_products", actor=actor)
    crm = Product.objects.get(code="easy_crm")
    assert (crm.name, crm.category) == ("Renamed", "Mine")


def test_a_dry_run_writes_nothing(staff_factory):
    call_command("register_products", actor=_actor(staff_factory), dry_run=True)
    assert _state()[:2] == (0, 0)


def test_the_command_needs_a_real_staff_actor_holding_the_grants(
    staff_factory, user_factory
):
    with pytest.raises(CommandError):
        call_command("register_products", actor="nobody@example.com")
    plain = user_factory()
    with pytest.raises(CommandError):
        call_command("register_products", actor=plain.email)
    # A staff identity WITHOUT the grants is refused by the service itself.
    bare, _ = staff_factory("platform.staff_customer.view")
    with pytest.raises(Exception):
        call_command("register_products", actor=bare.email)
    assert _state()[:2] == (0, 0)


def test_an_unknown_key_in_the_definitions_is_refused(staff_factory, tmp_path):
    path = tmp_path / "products.json"
    path.write_text(
        json.dumps({"products": [{"code": "easy_x", "name": "X", "prise": 5}]}),
        encoding="utf-8",
    )
    with pytest.raises(CommandError, match="Unknown keys"):
        call_command("register_products", actor=_actor(staff_factory), file=str(path))

"""The Super Admin's product categories (2026-11-16), over HTTP.

What these pin:

- staff add, rename, order and delete categories, and the Product Publisher,
  the Listing tab and the catalog filter offer exactly that list;
- a category is one name whatever its case; an empty or over-long name is
  refused, and so is a list past its limit;
- renaming renames it on every product that has it, in one transaction;
- deleting one that products use needs another category to move them to, and
  a refused delete changes nothing;
- a category a product carried before the list existed is still offered, and
  the migration seeds the list from the old defaults and every category in use;
- reading needs the catalog view grant, every change the catalog manage grant,
  and customers get nothing.

Each test starts from an empty list: a transactional test elsewhere flushes
every table, so the migration's seed rows cannot be relied on here.
"""

from __future__ import annotations

import importlib
import json
import uuid

import pytest

pytestmark = pytest.mark.django_db

API = "/api/v1/admin/products/categories"
VIEW = "platform.staff_catalog.view"
MANAGE = "platform.staff_catalog.manage"


def call(client, method, path, payload=None):
    return getattr(client, method)(
        path, json.dumps(payload or {}), content_type="application/json"
    )


def product(code: str, category: str):
    from workeasy360.commercial.catalog.models import Product

    return Product.objects.create(
        code=code, name=code.title(), slug=code.replace("_", "-"), category=category
    )


@pytest.fixture(autouse=True)
def empty_list(db):
    from workeasy360.commercial.catalog.models import ProductCategory

    ProductCategory.objects.all().delete()


@pytest.fixture
def staff(staff_login):
    client, _user = staff_login(VIEW, MANAGE)
    return client


def add(staff, name: str) -> dict:
    response = call(staff, "post", API, {"name": name})
    assert response.status_code == 201, response.content
    return response.json()


def audit(action: str) -> list:
    """The platform audit trail's entries for ``action``."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the commercial audit trail", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        return list(AuditEvent.unscoped.filter(action=action).order_by("-occurred_at"))


def names(staff) -> list[str]:
    return [c["name"] for c in staff.get(API).json()["categories"]]


def test_staff_add_categories_and_every_category_field_offers_them(staff):
    add(staff, "Sales")
    row = add(staff, "  Field   Service ")
    assert row["name"] == "Field Service" and row["products"] == 0

    listed = staff.get(API).json()
    assert [c["name"] for c in listed["categories"]] == ["Sales", "Field Service"]
    assert listed["can_manage"] is True and listed["unlisted"] == []

    # The Publisher's field and the Product Catalog's filter read the same list.
    assert staff.get("/api/v1/admin/products/publisher").json()["categories"] == [
        "Sales",
        "Field Service",
    ]
    console = staff.get("/api/v1/admin/products/console")
    assert console.status_code == 200, console.content
    assert console.json()["categories"][:2] == ["Sales", "Field Service"]

    created = audit("commercial.product_category.created")
    assert len(created) == 2
    assert {e.resource_type for e in created} == {"catalog.product_category"}


@pytest.mark.parametrize(
    ("name", "why"),
    [("sales", "case"), ("", "empty"), ("   ", "blank"), ("x" * 65, "long")],
)
def test_a_category_is_one_name_and_a_real_one(staff, name, why):
    add(staff, "Sales")
    refused = call(staff, "post", API, {"name": name})
    assert refused.status_code == 400, why
    assert refused.json()["field"] == "name"
    assert names(staff) == ["Sales"]


def test_the_list_has_a_limit(staff):
    from workeasy360.commercial.catalog.categories import MAX_CATEGORIES
    from workeasy360.commercial.catalog.models import ProductCategory

    ProductCategory.objects.bulk_create(
        ProductCategory(name=f"Category {n}", sort_order=n) for n in range(MAX_CATEGORIES)
    )
    refused = call(staff, "post", API, {"name": "One more"})
    assert refused.status_code == 400 and "at most" in refused.json()["detail"]


def test_renaming_renames_it_on_every_product_in_it(staff):
    from workeasy360.commercial.catalog.models import Product

    sales = add(staff, "Sales")
    add(staff, "Finance")
    product("crm_one", "Sales")
    product("crm_two", "Sales")
    product("books_one", "Finance")

    renamed = call(staff, "patch", f"{API}/{sales['id']}", {"name": "Sales & CRM"})
    assert renamed.status_code == 200, renamed.content
    assert renamed.json() == {"id": sales["id"], "name": "Sales & CRM", "products": 2}
    assert sorted(
        Product.objects.filter(category="Sales & CRM").values_list("code", flat=True)
    ) == ["crm_one", "crm_two"]
    assert Product.objects.get(code="books_one").category == "Finance"

    # A change of case alone is a rename, not a clash with itself...
    assert (
        call(staff, "patch", f"{API}/{sales['id']}", {"name": "SALES & CRM"}).status_code
        == 200
    )
    # ...but another category's name is refused, and nothing moves.
    clash = call(staff, "patch", f"{API}/{sales['id']}", {"name": "finance"})
    assert clash.status_code == 400 and clash.json()["field"] == "name"
    assert Product.objects.filter(category="SALES & CRM").count() == 2

    first = audit("commercial.product_category.renamed")[-1]
    assert first.changes == {
        "name": {"from": "Sales", "to": "Sales & CRM"},
        "products": 2,
    }


def test_deleting_a_category_in_use_moves_its_products_or_is_refused(staff):
    from workeasy360.commercial.catalog.models import Product

    old = add(staff, "Old")
    new = add(staff, "New")
    unused = add(staff, "Unused")
    product("crm_one", "Old")

    refused = call(staff, "delete", f"{API}/{old['id']}")
    assert refused.status_code == 400
    assert refused.json()["field"] == "move_to"
    assert "1 product is in 'Old'" in refused.json()["detail"]
    assert names(staff) == ["Old", "New", "Unused"]
    assert Product.objects.get(code="crm_one").category == "Old"

    itself = call(staff, "delete", f"{API}/{old['id']}?move_to={old['id']}")
    assert itself.status_code == 400 and itself.json()["field"] == "move_to"
    missing = call(staff, "delete", f"{API}/{old['id']}?move_to={uuid.uuid4()}")
    assert missing.status_code == 404

    moved = call(staff, "delete", f"{API}/{old['id']}?move_to={new['id']}")
    assert moved.status_code == 200, moved.content
    assert [c["name"] for c in moved.json()["categories"]] == ["New", "Unused"]
    assert Product.objects.get(code="crm_one").category == "New"

    # One nothing uses goes without a destination.
    assert call(staff, "delete", f"{API}/{unused['id']}").status_code == 200
    assert names(staff) == ["New"]


def test_the_list_is_put_in_exactly_the_order_given(staff):
    a, b, c = (add(staff, n) for n in ("A", "B", "C"))
    ordered = call(staff, "post", f"{API}/order", {"order": [c["id"], a["id"], b["id"]]})
    assert ordered.status_code == 200, ordered.content
    assert [x["name"] for x in ordered.json()["categories"]] == ["C", "A", "B"]
    assert staff.get("/api/v1/admin/products/publisher").json()["categories"] == [
        "C",
        "A",
        "B",
    ]

    for partial in ([c["id"], a["id"]], [c["id"], a["id"], a["id"]], "C,A,B"):
        refused = call(staff, "post", f"{API}/order", {"order": partial})
        assert refused.status_code == 400 and refused.json()["field"] == "order"
    assert names(staff) == ["C", "A", "B"]


def test_a_category_a_product_already_has_is_still_offered(staff):
    add(staff, "Sales")
    product("legacy_one", "Field Service")

    listed = staff.get(API).json()
    assert listed["unlisted"] == [{"name": "Field Service", "products": 1}]
    assert staff.get("/api/v1/admin/products/publisher").json()["categories"] == [
        "Sales",
        "Field Service",
    ]


def test_reading_needs_the_view_grant_and_changes_the_manage_grant(
    staff_login, staff, client
):
    row = add(staff, "Sales")
    viewer, _user = staff_login(VIEW)
    seen = viewer.get(API)
    assert seen.status_code == 200 and seen.json()["can_manage"] is False
    for method, path, payload in (
        ("post", API, {"name": "Marketing"}),
        ("patch", f"{API}/{row['id']}", {"name": "Selling"}),
        ("delete", f"{API}/{row['id']}", None),
        ("post", f"{API}/order", {"order": [row["id"]]}),
    ):
        assert call(viewer, method, path, payload).status_code == 403, (method, path)
        assert call(client, method, path, payload).status_code in (401, 403), (
            method,
            path,
        )
    assert client.get(API).status_code in (401, 403)
    assert names(staff) == ["Sales"]


def test_the_migration_seeds_the_old_defaults_then_every_category_in_use():
    from django.apps import apps

    from workeasy360.commercial.catalog.models import ProductCategory

    seed = importlib.import_module(
        "workeasy360.commercial.catalog.migrations.0015_product_categories"
    ).seed
    product("legacy_one", "Field Service")
    product("legacy_two", "sales")  # the default "Sales" already covers it
    seed(apps, None)
    assert list(ProductCategory.objects.values_list("name", flat=True)) == [
        "Sales",
        "Finance",
        "Project Management",
        "Customer Support",
        "Marketing",
        "Communication",
        "AI",
        "HR",
        "Other",
        "Field Service",
    ]
    seed(apps, None)  # running it again adds nothing
    assert ProductCategory.objects.count() == 10

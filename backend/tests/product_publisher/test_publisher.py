"""The one-page Product Publisher (ADR-068), over HTTP.

What the owner's brief requires and these pin:

- one request saves a product and several pricing plans, and those plans ARE
  the Plans section's records (the same rows the plan board lists);
- a refused step leaves nothing behind (one transaction), and the refusal names
  its field inside the plan it belongs to;
- a second save updates the same product and plans - never a duplicate;
- a price change supersedes the running price for new purchases only; a change
  of seats or trial publishes a new version and keeps the one sold;
- a draft is private until published; Preview shows the real Store page to
  staff only; Publish goes through the catalog's own checklist, in one
  transaction, and the product then sells in the public Store;
- every write needs its own grant (catalog, and pricing for prices).
"""

from __future__ import annotations

import json

import pytest

from tests.product_manager.conftest import png

pytestmark = pytest.mark.django_db

API = "/api/v1/admin/products"
STORE = "/api/v1/admin/store"
FULL = (
    "platform.staff_catalog.view",
    "platform.staff_catalog.manage",
    "platform.staff_pricing.manage",
    "platform.staff_integration.manage",
)


def call(client, method, path, payload=None):
    return getattr(client, method)(
        path, json.dumps(payload or {}), content_type="application/json"
    )


def page(book_id: str, **product) -> dict:
    """What the page sends for a product with two plans."""
    return {
        "product": {
            "name": "Acme Desk",
            "slug": "acme-desk",
            "category": "Customer Support",
            "tagline": "Support tickets, sorted.",
            "description": "## Why teams pick it\n- Fast\n- Friendly",
            "application_url": "https://desk.workeasy360.com",
            "highlights": ["Ticket management", "Email notifications"],
            "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "video_title": "A two-minute tour",
            "video_description": "See a ticket from first email to solved.",
            "customer_store_enabled": True,
            "reseller_enabled": False,
            "affiliate_enabled": False,
            "white_label_enabled": False,
            **product,
        },
        "trial": {"enabled": False, "duration_days": 14},
        "plans": [
            {
                "id": None,
                "name": "Starter",
                "caption": "For small teams",
                "description": "- 5 users\n- Email support",
                "price_book_id": book_id,
                "prices": {"monthly": 49900},
                "included_seats": 5,
            },
            {
                "id": None,
                "name": "Pro",
                "caption": "Automation included",
                "description": "- 20 users\n- Automation",
                "price_book_id": book_id,
                "prices": {"monthly": 99900, "annual": 999000},
                "included_seats": 20,
                "recommended": True,
            },
        ],
    }


@pytest.fixture
def book(catalog):
    return str(catalog["price_book"].id)


@pytest.fixture
def staff(staff_login):
    client, _user = staff_login(*FULL)
    return client


def created(staff, book, **product):
    response = call(staff, "post", f"{API}/publisher", page(book, **product))
    assert response.status_code == 201, response.content
    return response.json()


def test_bootstrap_lists_the_direct_books_and_what_this_person_may_do(staff, book):
    # The category field offers the Super Admin's own list (tests/product_categories).
    added = call(staff, "post", f"{API}/categories", {"name": "Field Service"})
    assert added.status_code == 201, added.content
    boot = staff.get(f"{API}/publisher").json()
    assert book in [b["id"] for b in boot["books"]]
    assert boot["intervals"] == ["monthly", "quarterly", "semi_annual", "annual"]
    assert boot["can_manage"] == {"catalog": True, "pricing": True, "integration": True}
    assert "Field Service" in boot["categories"]


def test_one_request_saves_the_product_and_its_plans_as_the_plans_sections_records(
    staff, book
):
    state = created(staff, book)
    product = state["product"]
    assert product["name"] == "Acme Desk"
    assert product["manager_status"] == "draft"
    assert product["listing"] == "hidden"
    assert state["listing"]["highlights"] == ["Ticket management", "Email notifications"]
    assert state["listing"]["video_title"] == "A two-minute tour"
    plans = state["plans"]
    assert [p["name"] for p in plans] == ["Starter", "Pro"]
    assert plans[0]["prices"] == {"monthly": 49900}
    assert plans[1]["prices"] == {"monthly": 99900, "annual": 999000}
    assert [p["recommended"] for p in plans] == [False, True]
    assert plans[0]["included_seats"] == 5 and plans[0]["published"] is True

    # The very same rows the Plans section's board lists - no second copy.
    board = staff.get(f"{STORE}/products/{product['id']}/plans").json()
    assert {p["id"] for p in board["plans"]} == {p["id"] for p in plans}
    pro = next(p for p in board["plans"] if p["name"] == "Pro")
    assert pro["recommended"] is True
    assert sorted(r["amount_minor"] for r in pro["prices"]) == [99900, 999000]


def test_a_refused_plan_leaves_nothing_behind_and_names_its_field(staff, book):
    body = page(book)
    body["plans"][1]["prices"] = {"monthly": 0}  # a paid plan at nothing
    response = call(staff, "post", f"{API}/publisher", body)
    assert response.status_code == 400
    assert response.json()["field"] == "plans.1.prices"
    # Nothing was created: the same slug is still free.
    assert created(staff, book)["product"]["slug"] == "acme-desk"


def test_a_second_save_updates_the_same_records_and_never_duplicates(
    staff, book, catalog
):
    first = created(staff, book)
    pid = first["product"]["id"]
    again = call(staff, "post", f"{API}/publisher", page(book))
    assert again.status_code == 400  # the slug is taken: no second product

    body = page(book)
    body["product"].pop("slug")
    for plan, saved in zip(body["plans"], first["plans"]):
        plan["id"] = saved["id"]
    body["plans"][0]["prices"] = {"monthly": 59900}  # a price change
    body["plans"][1]["included_seats"] = 25  # a change of terms
    response = call(staff, "put", f"{API}/{pid}/publisher", body)
    assert response.status_code == 200, response.content
    state = response.json()
    assert [p["id"] for p in state["plans"]] == [p["id"] for p in first["plans"]]
    assert state["plans"][0]["prices"] == {"monthly": 59900}
    assert state["plans"][0]["version"] == first["plans"][0]["version"]
    assert state["plans"][1]["included_seats"] == 25
    assert state["plans"][1]["version"] == first["plans"][1]["version"] + 1

    # The superseded price is kept (ended), never edited - what a subscriber
    # bought stays reproducible.
    from workeasy360.commercial.pricing.models import PlanPrice

    rows = PlanPrice.objects.filter(
        plan_version__plan_id=first["plans"][0]["id"], interval="monthly"
    )
    assert sorted(r.amount_minor for r in rows) == [49900, 59900]
    assert rows.get(amount_minor=49900).valid_to is not None


def test_a_draft_is_private_and_preview_shows_the_store_page_to_staff_only(
    staff, book, client
):
    state = created(staff, book)
    pid = state["product"]["id"]
    staff.post(f"{API}/{pid}/media?kind=logo", png(256, 256), content_type="image/png")
    assert client.get("/api/v1/public/store/products/acme-desk").status_code == 404

    preview = staff.get(f"{API}/{pid}/store-preview")
    assert preview.status_code == 200
    data = preview.json()
    assert data["preview"] is True
    assert data["product"]["title"] == "Acme Desk"
    assert data["product"]["highlights"] == ["Ticket management", "Email notifications"]
    assert data["product"]["logo"]["url"].startswith(f"{API}/{pid}/media/")
    assert data["product"]["video"]["title"] == "A two-minute tour"
    assert [p["name"] for p in data["plans"]] == ["Starter", "Pro"]
    assert data["comparison"][0]["key"] == "seats"
    # Not for anyone else.
    assert client.get(f"{API}/{pid}/store-preview").status_code in (401, 403)


def test_publish_goes_through_the_checklist_in_one_transaction(staff, book, client):
    state = created(staff, book)
    pid = state["product"]["id"]
    refused = call(staff, "post", f"{API}/{pid}/publisher/publish")
    assert refused.status_code in (400, 409)  # no logo yet
    # Nothing changed: the plans are still drafts.
    still = staff.get(f"{API}/{pid}/publisher").json()
    assert {p["status"] for p in still["plans"]} == {"draft"}

    staff.post(f"{API}/{pid}/media?kind=logo", png(256, 256), content_type="image/png")
    published = call(staff, "post", f"{API}/{pid}/publisher/publish")
    assert published.status_code == 200, published.content
    live = published.json()
    assert live["product"]["manager_status"] == "active"
    assert {p["status"] for p in live["plans"]} == {"available"}

    from workeasy360.commercial.catalog import services as catalog_services

    catalog_services.refresh_public_catalog()
    public = client.get("/api/v1/public/store/products/acme-desk")
    assert public.status_code == 200, public.content
    page_data = public.json()
    assert [p["name"] for p in page_data["plans"]] == ["Starter", "Pro"]
    assert page_data["product"]["video"]["title"] == "A two-minute tour"
    pro = next(p for p in page_data["plans"] if p["name"] == "Pro")
    assert pro["recommended"] is True
    assert sorted(r["interval"] for r in pro["prices"]) == ["annual", "monthly"]


def test_a_free_plan_is_one_price_of_nothing(staff, book):
    body = page(book)
    body["plans"] = [
        {
            "id": None,
            "name": "Free",
            "free": True,
            "price_book_id": book,
            "prices": {"monthly": 0},
        }
    ]
    response = call(staff, "post", f"{API}/publisher", body)
    assert response.status_code == 201, response.content
    plan = response.json()["plans"][0]
    assert plan["free"] is True and plan["prices"] == {"monthly": 0}


def test_prices_need_the_pricing_grant_and_customers_get_nothing(
    staff_login, book, client
):
    catalog_only, _user = staff_login(
        "platform.staff_catalog.view", "platform.staff_catalog.manage"
    )
    refused = call(catalog_only, "post", f"{API}/publisher", page(book))
    assert refused.status_code == 403
    assert call(client, "post", f"{API}/publisher", page(book)).status_code in (401, 403)


def test_only_one_recommended_plan_and_only_this_products_plans(staff, book, catalog):
    body = page(book)
    body["plans"][0]["recommended"] = True
    two = call(staff, "post", f"{API}/publisher", body)
    assert two.status_code == 400 and two.json()["field"] == "plans"

    state = created(staff, book)
    other = str(catalog["plans"]["crm_starter"].id)
    body = page(book)
    body["product"].pop("slug")
    body["plans"] = [{**body["plans"][0], "id": other}]
    response = call(staff, "put", f"{API}/{state['product']['id']}/publisher", body)
    assert response.status_code == 404


def test_video_details_are_shown_only_when_set():
    """The public video object keeps its exact shape for a product with no
    title, description or thumbnail - older consumers compare it whole."""
    from workeasy360.commercial.controlplane import portal_store

    assert (
        portal_store._video_extras({"video_title": "", "video_description": ""}, None)
        == {}
    )
    assert portal_store._video_extras(
        {"video_title": "Tour", "video_description": ""}, "/x/poster"
    ) == {"title": "Tour", "poster": "/x/poster"}

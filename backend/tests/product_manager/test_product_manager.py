"""The Super Admin Product Manager (ADR-051): one central catalog.

Create through the wizard, edit, upload listing images, publish behind a
checklist, unpublish, archive, restore and - only for a product nothing ever
used - delete; every write authorized by a staff grant and audited, every URL
and file validated on the server, and publishing never granting anyone access.
"""

from __future__ import annotations

import pathlib

import pytest
from django.core.cache import cache

from tests.product_manager.conftest import (
    API,
    MANAGE,
    body,
    jpeg,
    patch,
    png,
    post,
    upload,
)

pytestmark = pytest.mark.django_db

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _create(client, **overrides):
    response = post(client, API, body(**overrides))
    assert response.status_code == 201, response.content
    return response.json()["product"]


def _detail(client, product_id):
    response = client.get(f"{API}/{product_id}")
    assert response.status_code == 200, response.content
    return response.json()["product"]


def _action(client, product_id, action):
    return post(client, f"{API}/{product_id}/{action}")


def _ready(client, product_id):
    """Give a wizard-made product the one thing it lacks to publish: a logo."""
    response = upload(client, product_id, png(256, 256))
    assert response.status_code == 201, response.content


def _store_codes():
    from workeasy360.commercial.catalog import selectors

    return {p["code"] for p in selectors.store_products()}


def _public_codes():
    from workeasy360.commercial.subscriptions import marketplace

    cache.clear()
    return {p["code"] for p in marketplace.public_catalog()["products"]}


# --- create -----------------------------------------------------------------------------


def test_a_super_admin_adds_a_draft_hidden_product_with_its_integration_and_trial(
    manager,
):
    product = _create(manager)
    assert product["code"] == "orchard_planner"  # derived from the slug, once
    assert product["slug"] == "orchard-planner"
    assert product["status"] == "draft" and product["manager_status"] == "draft"
    assert product["listing"] == "hidden"
    assert product["category"] == "Project Management"
    assert product["application_url"] == "https://orchard.workeasy360.com"
    assert (product["affiliate_enabled"], product["white_label_enabled"]) == (
        False,
        False,
    )
    assert (product["customer_store_enabled"], product["reseller_enabled"]) == (
        True,
        True,
    )
    # The launch URL lives in the one integration registry - registered, as
    # every registration is, in draft and with no credential.
    assert product["launch_url"] == "https://orchard.workeasy360.com/"
    assert product["integration"]["status"] == "draft"
    assert product["integration"]["sign_in_mode"] == "sso_only"
    assert product["integration"]["redirect_uris"] == [
        "https://orchard.workeasy360.com/api/auth/callback"
    ]
    # The trial is the product's own configuration; no plan is invented for it.
    assert product["channels"]["trial"]["enabled"] is True
    assert product["channels"]["trial"]["duration_days"] == 7
    assert product["channels"]["trial"]["eligible_plan_codes"] == []
    assert product["plans"] == []
    # Not published: not in the store, not on the public site.
    assert "orchard_planner" not in _store_codes()
    assert "orchard_planner" not in _public_codes()


def test_a_draft_needs_only_a_name_and_a_slug(manager):
    response = post(manager, API, {"name": "Seedling", "slug": "seedling"})
    assert response.status_code == 201, response.content
    product = response.json()["product"]
    assert product["status"] == "draft" and product["integration"] is None
    assert product["channels"]["trial"] is None
    missing = {i["key"] for i in product["readiness"] if not i["ok"]}
    assert missing == {"category", "tagline", "logo", "application_url", "launch_url"}


def test_product_sso_switched_off_registers_a_launch_url_without_a_callback(manager):
    product = _create(
        manager,
        integration={
            "launch_url": "https://orchard.workeasy360.com/",
            "sso_enabled": False,
        },
    )
    assert product["integration"]["sign_in_mode"] == "local_only"
    assert product["integration"]["redirect_uris"] == []


def test_sso_switched_on_needs_its_callback_and_nothing_is_created_without_it(manager):
    from workeasy360.commercial.catalog.models import Product

    response = post(
        manager,
        API,
        body(
            integration={
                "launch_url": "https://orchard.workeasy360.com/",
                "sso_enabled": True,
            }
        ),
    )
    assert response.status_code == 400
    assert response.json()["field"] == "redirect_uri"
    assert not Product.objects.filter(code="orchard_planner").exists()


def test_staff_without_the_catalog_grant_cannot_add_a_product(staff_login):
    from workeasy360.commercial.catalog.models import Product

    viewer, _ = staff_login(
        "platform.staff_catalog.view", "platform.staff_integration.manage"
    )
    assert post(viewer, API, body()).status_code == 403
    assert not Product.objects.filter(code="orchard_planner").exists()


def test_the_wizard_is_one_transaction_a_refused_step_leaves_nothing(staff_login):
    """Catalog grant but no integration grant: the integration step is refused,
    so the product row it would have hung off is rolled back too."""
    from workeasy360.commercial.catalog.models import Product

    catalog_only, _ = staff_login("platform.staff_catalog.manage")
    assert post(catalog_only, API, body()).status_code == 403
    assert not Product.objects.filter(code="orchard_planner").exists()


def test_a_customer_or_a_visitor_cannot_reach_the_product_manager(console_org, login):
    from django.test import Client

    owner = login(console_org()["owner"])
    assert post(owner, API, body()).status_code in (401, 403, 404)
    assert post(Client(), API, body()).status_code in (401, 403)


def test_django_is_staff_alone_confers_nothing(staff_login):
    """The flag is not an authority (final audit A-01): only a StaffGrant is."""
    staff_flag_only, _ = staff_login()
    assert post(staff_flag_only, API, body()).status_code in (401, 403)


@pytest.mark.parametrize(
    "slug",
    [
        "Not A Slug",
        "-leading",
        "trailing-",
        "double--hyphen",
        "under_score",
        "x" * 65,
        "",
    ],
)
def test_a_slug_is_lowercase_and_hyphen_safe(manager, slug):
    response = post(manager, API, body(slug=slug))
    assert response.status_code == 400
    assert response.json()["field"] == "slug"


def test_a_slug_too_long_for_a_product_code_is_refused_plainly(manager):
    response = post(manager, API, body(slug="a-very-long-product-name-for-the-store"))
    assert response.status_code == 400
    assert "32 characters" in response.json()["detail"]


def test_slugs_and_the_codes_they_imply_are_unique(manager, catalog):
    _create(manager)
    again = post(manager, API, body(name="Orchard Planner 2"))
    assert again.status_code == 400
    # "crm" is taken as a code by the catalog fixture.
    clash = post(manager, API, body(slug="crm", integration=None))
    assert clash.status_code == 400


# --- URL security -----------------------------------------------------------------------


BAD_URLS = [
    "javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "file:///etc/passwd",
    "http://orchard.workeasy360.com",
    "https://localhost/",
    "https://127.0.0.1/",
    "https://169.254.169.254/latest/meta-data/",
    "https://10.0.0.5/",
    "https://orchard.internal/",
    "https://user:pass@orchard.workeasy360.com/",
    "https://orchard.workeasy360.com\\@evil.example.net/",
    "https://%6cocalhost/",
    "not a url",
]


@pytest.mark.parametrize("field", ["application_url", "documentation_url", "support_url"])
@pytest.mark.parametrize("url", BAD_URLS)
def test_listing_urls_are_validated_on_the_server(manager, field, url):
    from workeasy360.commercial.catalog.models import Product

    response = post(manager, API, body(**{field: url}))
    assert response.status_code == 400, (field, url)
    assert response.json()["field"] == field
    assert not Product.objects.filter(code="orchard_planner").exists()


@pytest.mark.parametrize("url", BAD_URLS)
def test_the_launch_url_passes_the_registry_gate(manager, url):
    from workeasy360.commercial.catalog.models import Product

    response = post(
        manager,
        API,
        body(integration={"launch_url": url, "sso_enabled": False}),
    )
    assert response.status_code == 400, url
    assert not Product.objects.filter(code="orchard_planner").exists()


def test_an_edit_validates_urls_too(manager):
    product = _create(manager)
    response = patch(
        manager,
        f"/api/v1/admin/catalog/products/{product['id']}",
        {"support_url": "https://169.254.169.254/"},
    )
    assert response.status_code == 400
    assert (
        _detail(manager, product["id"])["support_url"]
        == "https://support.workeasy360.com/orchard"
    )


# --- images -----------------------------------------------------------------------------


def test_a_logo_is_stored_scanned_and_served_same_origin(manager):
    product = _create(manager)
    response = upload(manager, product["id"], png(256, 256))
    assert response.status_code == 201, response.content
    media = response.json()
    assert (media["kind"], media["width"], media["height"]) == ("logo", 256, 256)
    served = manager.get(f"{API}/{product['id']}/media/{media['id']}")
    assert served.status_code == 200
    assert served["Content-Type"] == "image/png"
    assert served["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in served["Content-Security-Policy"]
    assert served.content.startswith(b"\x89PNG")
    assert _detail(manager, product["id"])["logo_media_id"] == media["id"]


def test_a_new_logo_replaces_the_old_one(manager):
    product = _create(manager)
    first = upload(manager, product["id"], png(128, 128)).json()
    second = upload(
        manager, product["id"], jpeg(300, 300), content_type="image/jpeg"
    ).json()
    logos = [m for m in _detail(manager, product["id"])["media"] if m["kind"] == "logo"]
    assert [m["id"] for m in logos] == [second["id"]]
    assert manager.get(f"{API}/{product['id']}/media/{first['id']}").status_code == 404


@pytest.mark.parametrize(
    ("data", "content_type", "why"),
    [
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
            "image/svg+xml",
            "svg",
        ),
        (b"<html><script>alert(1)</script></html>", "image/png", "html posing as png"),
        (png(256, 256), "image/jpeg", "png posing as jpeg"),
        (b"MZ\x90\x00executable", "image/png", "executable"),
        (png(32, 32), "image/png", "too few pixels"),
        (png(4000, 10), "image/png", "too many pixels"),
        (b"", "image/png", "empty"),
        (png(256, 256), "image/gif", "unsupported type"),
    ],
)
def test_a_bad_image_is_refused_and_nothing_is_attached(manager, data, content_type, why):
    product = _create(manager)
    response = upload(manager, product["id"], data, content_type=content_type)
    assert response.status_code == 400, why
    assert _detail(manager, product["id"])["media"] == []


def test_an_oversized_logo_is_refused(manager):
    import os

    product = _create(manager)
    # A valid PNG header followed by incompressible bytes: over the logo's 512 KB.
    big = png(256, 256) + os.urandom(600 * 1024)
    assert upload(manager, product["id"], big).status_code == 400


def test_screenshots_append_reorder_caption_delete_and_are_capped(manager):
    product = _create(manager)
    ids = []
    for n in range(8):
        response = upload(
            manager, product["id"], png(640, 400), kind="screenshot", caption=f"Shot{n}"
        )
        assert response.status_code == 201, response.content
        ids.append(response.json()["id"])
    assert (
        upload(manager, product["id"], png(640, 400), kind="screenshot").status_code
        == 400
    )

    reordered = post(
        manager, f"{API}/{product['id']}/media/reorder", {"order": list(reversed(ids))}
    )
    assert reordered.status_code == 200, reordered.content
    shots = [m["id"] for m in reordered.json()["media"] if m["kind"] == "screenshot"]
    assert shots == list(reversed(ids))
    # A partial or duplicated order is refused rather than silently dropping one.
    assert (
        post(
            manager, f"{API}/{product['id']}/media/reorder", {"order": ids[:3]}
        ).status_code
        == 400
    )
    assert (
        post(
            manager, f"{API}/{product['id']}/media/reorder", {"order": [ids[0]] * 8}
        ).status_code
        == 400
    )

    captioned = patch(
        manager, f"{API}/{product['id']}/media/{ids[0]}", {"caption": "The board"}
    )
    assert captioned.status_code == 200 and captioned.json()["caption"] == "The board"
    assert manager.delete(f"{API}/{product['id']}/media/{ids[0]}").status_code == 200
    assert (
        len(
            [
                m
                for m in _detail(manager, product["id"])["media"]
                if m["kind"] == "screenshot"
            ]
        )
        == 7
    )


def test_one_products_image_cannot_be_read_or_deleted_through_another(manager):
    first = _create(manager)
    second = _create(manager, name="Second", slug="second-app", integration=None)
    logo = upload(manager, first["id"], png(256, 256)).json()
    assert manager.get(f"{API}/{second['id']}/media/{logo['id']}").status_code == 404
    assert manager.delete(f"{API}/{second['id']}/media/{logo['id']}").status_code == 404
    assert manager.get(f"{API}/{first['id']}/media/{logo['id']}").status_code == 200


def test_uploading_needs_the_catalog_manage_grant(manager, staff_login):
    product = _create(manager)
    viewer, _ = staff_login("platform.staff_catalog.view")
    assert upload(viewer, product["id"], png(256, 256)).status_code == 403
    assert viewer.get(f"{API}/{product['id']}").status_code == 200


# --- publish, unpublish, archive, restore -----------------------------------------------


def test_publishing_is_refused_until_the_checklist_is_met(manager):
    product = _create(manager)
    refused = _action(manager, product["id"], "publish")
    assert refused.status_code == 400
    assert refused.json()["missing"] == ["Logo"]
    detail = _detail(manager, product["id"])
    assert detail["listing"] == "hidden" and detail["status"] == "draft"


def test_publishing_through_an_edit_is_checked_the_same_way(manager):
    product = _create(manager)
    response = patch(
        manager, f"/api/v1/admin/catalog/products/{product['id']}", {"listing": "listed"}
    )
    assert response.status_code == 400
    assert "Logo" in response.json()["detail"]


def test_publish_makes_it_active_and_visible_and_grants_nobody_anything(manager):
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    product = _create(manager)
    _ready(manager, product["id"])

    def entries():
        with (
            unscoped_access(reason="test reads the whole audit log", module="tests"),
            organization_db_scope(PLATFORM_ORGANIZATION_ID),
        ):
            return dict(AuditEvent.unscoped.values_list("id", "action"))

    before = entries()
    published = _action(manager, product["id"], "publish")
    assert published.status_code == 200, published.content
    after = published.json()["product"]
    assert (after["status"], after["listing"], after["manager_status"]) == (
        "available",
        "listed",
        "active",
    )
    assert "orchard_planner" in _store_codes()
    assert "orchard_planner" in _public_codes()
    # The one thing that happened is the publication itself: no entitlement,
    # trial, subscription or provisioning record was written by it.
    new = [action for key, action in entries().items() if key not in before]
    assert new == ["commercial.product.published"]


def test_the_public_catalog_reflects_publish_and_unpublish_at_once(
    manager, django_capture_on_commit_callbacks
):
    """Found by the real-browser check: the public catalog is cached, and a
    product published within the cache's lifetime stayed invisible. Catalog
    writes now move the cache to a new generation once they commit - asserted
    here with the cache left warm (no ``cache.clear()``)."""
    from workeasy360.commercial.subscriptions import marketplace

    def public_slugs():
        return {p["slug"] for p in marketplace.public_catalog()["products"]}

    cache.clear()
    product = _create(manager)
    _ready(manager, product["id"])
    assert "orchard-planner" not in public_slugs()  # warms the cache
    with django_capture_on_commit_callbacks(execute=True):
        assert _action(manager, product["id"], "publish").status_code == 200
    assert "orchard-planner" in public_slugs()
    with django_capture_on_commit_callbacks(execute=True):
        assert _action(manager, product["id"], "unpublish").status_code == 200
    assert "orchard-planner" not in public_slugs()


def test_unpublish_hides_it_and_keeps_it_active(manager):
    product = _create(manager)
    _ready(manager, product["id"])
    _action(manager, product["id"], "publish")
    hidden = _action(manager, product["id"], "unpublish").json()["product"]
    assert (hidden["status"], hidden["listing"]) == ("available", "hidden")
    assert "orchard_planner" not in _store_codes()
    assert "orchard_planner" not in _public_codes()


def test_archive_and_restore(manager):
    product = _create(manager)
    archived = _action(manager, product["id"], "archive").json()["product"]
    assert (archived["status"], archived["listing"], archived["manager_status"]) == (
        "deprecated",
        "hidden",
        "archived",
    )
    assert _action(manager, product["id"], "publish").status_code == 400
    restored = _action(manager, product["id"], "restore").json()["product"]
    assert (restored["status"], restored["listing"]) == ("available", "hidden")
    assert _action(manager, product["id"], "restore").status_code == 400
    assert _action(manager, product["id"], "explode").status_code == 404


def test_lifecycle_actions_need_the_catalog_manage_grant(manager, staff_login):
    product = _create(manager)
    viewer, _ = staff_login("platform.staff_catalog.view")
    for action in ("publish", "unpublish", "archive", "restore"):
        assert _action(viewer, product["id"], action).status_code == 403


def test_the_slug_is_fixed_once_the_product_has_been_on_sale(manager):
    product = _create(manager)
    renamed = patch(
        manager, f"/api/v1/admin/catalog/products/{product['id']}", {"slug": "orchard"}
    )
    assert renamed.status_code == 200, renamed.content  # a draft may still change it
    _ready(manager, product["id"])
    assert _action(manager, product["id"], "publish").status_code == 200
    locked = patch(
        manager, f"/api/v1/admin/catalog/products/{product['id']}", {"slug": "orchard-2"}
    )
    assert locked.status_code == 400 and locked.json()["field"] == "slug"


# --- availability flags -----------------------------------------------------------------


def test_customer_store_not_allowed_keeps_a_published_product_off_customer_listings(
    manager,
):
    product = _create(manager, customer_store_enabled=False)
    _ready(manager, product["id"])
    assert _action(manager, product["id"], "publish").status_code == 200
    assert "orchard_planner" not in _store_codes()
    assert "orchard_planner" not in _public_codes()
    patch(
        manager,
        f"/api/v1/admin/catalog/products/{product['id']}",
        {"customer_store_enabled": True},
    )
    assert "orchard_planner" in _store_codes()


@pytest.mark.parametrize(
    "flag",
    [
        "customer_store_enabled",
        "reseller_enabled",
        "affiliate_enabled",
        "white_label_enabled",
    ],
)
def test_each_flag_is_stored_and_must_be_a_real_boolean(manager, flag):
    product = _create(manager)
    path = f"/api/v1/admin/catalog/products/{product['id']}"
    assert patch(manager, path, {flag: "false"}).status_code == 400
    assert patch(manager, path, {flag: False}).status_code == 200
    assert _detail(manager, product["id"])[flag] is False


def test_existing_products_keep_selling_where_they_did(manager, catalog):
    """The migration's defaults: every flag on, so nothing that sold before
    the Product Manager stops selling because of it."""
    crm = _detail(manager, catalog["products"]["crm"].id)
    assert all(
        crm[f]
        for f in (
            "customer_store_enabled",
            "reseller_enabled",
            "affiliate_enabled",
            "white_label_enabled",
        )
    )
    assert crm["manager_status"] == "active"
    assert "crm" in _public_codes()


# --- trial ------------------------------------------------------------------------------


@pytest.mark.parametrize("days", [0, 366, "7", True, None])
def test_trial_duration_is_validated(manager, days):
    from workeasy360.commercial.catalog.models import Product

    response = post(manager, API, body(trial={"enabled": True, "duration_days": days}))
    assert response.status_code == 400
    assert response.json()["field"] == "duration_days"
    assert not Product.objects.filter(code="orchard_planner").exists()


def test_the_trial_length_is_per_product_not_global(manager):
    one = _create(manager)
    two = _create(
        manager,
        name="Other",
        slug="other-app",
        integration=None,
        trial={"enabled": True, "duration_days": 30},
    )
    assert _detail(manager, one["id"])["channels"]["trial"]["duration_days"] == 7
    assert _detail(manager, two["id"])["channels"]["trial"]["duration_days"] == 30


# --- delete -----------------------------------------------------------------------------


def test_a_product_nothing_ever_used_can_be_deleted(manager):
    from workeasy360.commercial.catalog.models import Product

    response = post(manager, API, {"name": "Scratch", "slug": "scratch-app"})
    product = response.json()["product"]
    upload(manager, product["id"], png(128, 128))
    deleted = manager.delete(f"{API}/{product['id']}")
    assert deleted.status_code == 200, deleted.content
    assert not Product.objects.filter(code="scratch_app").exists()


def test_a_product_with_an_integration_is_archived_not_deleted(manager):
    product = _create(manager)
    refused = manager.delete(f"{API}/{product['id']}")
    assert refused.status_code == 400
    assert "Archive it instead" in refused.json()["detail"]
    assert _detail(manager, product["id"])["status"] == "draft"


def test_a_product_that_was_ever_active_or_has_plans_cannot_be_deleted(manager, catalog):
    crm = catalog["products"]["crm"]
    refused = manager.delete(f"{API}/{crm.id}")
    assert refused.status_code == 400
    detail = refused.json()["detail"]
    assert "has been active" in detail and "has plans" in detail


def test_deleting_needs_the_catalog_manage_grant(manager, staff_login):
    product = post(manager, API, {"name": "Scratch", "slug": "scratch-app"}).json()[
        "product"
    ]
    viewer, _ = staff_login("platform.staff_catalog.view")
    assert viewer.delete(f"{API}/{product['id']}").status_code == 403


# --- audit ------------------------------------------------------------------------------


def test_every_change_is_on_the_products_activity_with_who_did_it(manager):
    product = _create(manager)
    _ready(manager, product["id"])
    _action(manager, product["id"], "publish")
    _action(manager, product["id"], "unpublish")
    _action(manager, product["id"], "archive")
    patch(
        manager,
        f"/api/v1/admin/catalog/products/{product['id']}",
        {
            "application_url": "https://app.orchard.workeasy360.com",
            "reseller_enabled": False,
        },
    )
    page = manager.get(f"{API}/{product['id']}/activity")
    assert page.status_code == 200, page.content
    rows = page.json()["results"]
    labels = [r["label"] for r in rows]
    for expected in (
        "Created",
        "Integration registered",
        "Trial changed",
        "Image added",
        "Published",
        "Unpublished",
        "Archived",
        "Edited",
    ):
        assert expected in labels, (expected, labels)
    edited = next(r for r in rows if r["label"] == "Edited")
    assert "URLs" in edited["summary"] and "availability" in edited["summary"]
    assert all(r["actor"] for r in rows)
    assert "secret" not in page.content.decode().lower()


def test_activity_needs_the_audit_grant(manager, staff_login):
    product = _create(manager)
    catalog_staff, _ = staff_login(*MANAGE)
    assert catalog_staff.get(f"{API}/{product['id']}/activity").status_code == 403
    assert catalog_staff.get(f"{API}/{product['id']}").json()["can_see_activity"] is False


def test_no_secret_appears_in_any_product_manager_response(manager):
    product = _create(manager)
    detail = manager.get(f"{API}/{product['id']}").content.decode().lower()
    listing = manager.get(f"{API}/console").content.decode().lower()
    for text in (detail, listing):
        assert "client_secret" not in text
        assert "private key" not in text and "secret_hash" not in text


# --- the future-product rule ------------------------------------------------------------


def test_a_future_product_needs_no_code_change():
    """Nothing in the platform or the shell knows ``orchard_planner``: every
    test above added, published and managed it through data alone."""
    sources = [
        *(ROOT / "backend" / "workeasy360").rglob("*.py"),
        *(ROOT / "frontend" / "src").rglob("*.ts"),
        *(ROOT / "frontend" / "src").rglob("*.tsx"),
    ]
    assert len(sources) > 100
    mentions = [
        str(p.relative_to(ROOT))
        for p in sources
        if "orchard" in p.read_text(encoding="utf-8", errors="ignore").lower()
    ]
    assert mentions == []


def test_the_product_manager_branches_on_no_product_code():
    """No ``if code == "easy_crm"``: the four current products and any future
    one go through exactly the same code."""
    files = [
        ROOT / "backend/workeasy360/commercial/controlplane/product_manager.py",
        ROOT / "backend/workeasy360/interfaces/api/v1/admin_product_manager_views.py",
        *(ROOT / "frontend/src/features/controlplane/productmanager").glob("*.tsx"),
        *(ROOT / "frontend/src/features/controlplane/productmanager").glob("*.ts"),
    ]
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        for code in (
            "easy_crm",
            "easybook",
            "easy_project",
            "easy_helpdesk",
            "help desk",
        ):
            assert code not in text, (path.name, code)

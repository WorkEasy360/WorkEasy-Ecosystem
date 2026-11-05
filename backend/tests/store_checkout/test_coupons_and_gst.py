"""The Store's checkout to the owner's mock-up (ADR-058): 18% GST added on
top of every INR price - on the product page, the checkout's quote, the
order's invoice and every renewal - a coupon the Super Admin issues, priced by
the server and counted once per placed order, and the refund promise the
Super Admin sets on a product."""

from __future__ import annotations

import uuid

import pytest
from django.test import Client

from tests.extension.conftest import pay_order, place_order, url
from tests.store_checkout.conftest import CRM_YEAR, open_shop

pytestmark = pytest.mark.django_db

ADMIN_COUPONS = "/api/v1/admin/store/coupons"


def _version(shop, plan="crm_professional"):
    return str(shop["catalog"]["versions"][plan].id)


def _quote(shop, **body):
    response = shop["client"].post(
        url(shop["slug"], "orders/quote"),
        {"plan_version_id": _version(shop), "billing_interval": "annual", **body},
        content_type="application/json",
    )
    return response


def _place(shop, **body):
    return place_order(
        shop["client"],
        shop["slug"],
        plan_version_id=body.pop("plan_version_id", _version(shop)),
        billing_interval="annual",
        **body,
    )


def _invoice_lines(shop, invoice_id):
    detail = shop["client"].get(url(shop["slug"], f"billing/invoices/{invoice_id}"))
    assert detail.status_code == 200, detail.content
    body = detail.json()
    return body, [(line["kind"], line["amount_minor"]) for line in body["lines"]]


def _coupon(admin, **body):
    response = admin.post(ADMIN_COUPONS, body, content_type="application/json")
    assert response.status_code == 201, response.content
    return response.json()


# --- GST ------------------------------------------------------------------------------


def test_gst_is_18_percent_of_inr_rounded_to_whole_rupees(settings):
    from workeasy360.commercial.billing.tax import tax_for, terms

    # The owner's mock-up: 8,399 + 1,511.82 -> 1,512.
    assert tax_for("INR", 839900).amount_minor == 151200
    assert tax_for("INR", 839900).description == "GST (18%)"
    assert tax_for("INR", 50).amount_minor == 0  # 0.09 rupees rounds to nothing
    assert tax_for("GBP", 839900) is None
    assert tax_for("INR", 0) is None
    assert terms("INR") == {"label": "GST", "rate_bp": 1800, "description": "GST (18%)"}
    assert terms("USD") is None
    settings.GST_RATE_BP = 0
    assert tax_for("INR", 839900) is None and terms("INR") is None


def test_an_inr_order_adds_gst_as_its_own_invoice_line(shop):
    quoted = _quote(shop)
    assert quoted.status_code == 200, quoted.content
    quote = quoted.json()
    assert quote["subtotal_minor"] == CRM_YEAR
    assert quote["tax"] == {
        "label": "GST",
        "rate_bp": 1800,
        "amount_minor": 151200,
        "description": "GST (18%)",
    }
    assert quote["amount_due_minor"] == 991100  # the mock-up's ₹9,911

    placed = _place(shop)
    assert placed.status_code == 201, placed.content
    order = placed.json()
    assert (order["amount_due_minor"], order["tax_minor"]) == (991100, 151200)
    invoice, lines = _invoice_lines(shop, order["invoice_id"])
    assert invoice["total_minor"] == 991100
    assert sorted(lines) == [("charge", CRM_YEAR), ("tax", 151200)]

    paid = pay_order(shop["client"], shop["slug"], shop["id"], order)
    assert paid["status"] == "fulfilled"
    # Only the charge pays for the subscription line - commission never earns on GST.
    from workeasy360.commercial.payments.selectors import paid_item_lines

    earned = [line for rows in paid_item_lines(shop["id"]).values() for line in rows]
    assert [line["amount_minor"] for line in earned] == [CRM_YEAR]


def test_a_renewal_invoice_carries_gst_too(shop):
    from django.utils import timezone

    from workeasy360.commercial.payments.invoicing import ensure_invoices
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    order = _place(shop).json()
    pay_order(shop["client"], shop["slug"], shop["id"], order)
    subscription = (
        shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"][0]
    )
    after = timezone.datetime.fromisoformat(subscription["current_period_end"])
    after += timezone.timedelta(minutes=1)
    process_due_subscriptions(at=after)
    ensure_invoices(shop["id"], at=after)
    newest = (
        shop["client"].get(url(shop["slug"], "billing/invoices")).json()["results"][0]
    )
    assert newest["order_id"] is None
    assert newest["total_minor"] == 991100
    _invoice, lines = _invoice_lines(shop, newest["invoice_id"])
    assert sorted(lines) == [("charge", CRM_YEAR), ("tax", 151200)]


def test_another_currency_has_no_tax_line(console_org, login, catalog, fake_payments):
    gbp = open_shop(console_org, login, catalog, currency="GBP")
    order = place_order(
        gbp["client"],
        gbp["slug"],
        plan_version_id=str(catalog["versions"]["crm_professional"].id),
    ).json()
    assert (order["amount_due_minor"], order["tax_minor"]) == (4999, 0)
    _invoice, lines = _invoice_lines(gbp, order["invoice_id"])
    assert lines == [("charge", 4999)]


# --- coupons --------------------------------------------------------------------------


def test_staff_issue_coupons_and_bad_terms_are_refused(coupon_admin):
    made = _coupon(coupon_admin, code="save10", kind="percent", percent_bp=1000)
    assert (made["code"], made["label"], made["status"], made["redemptions"]) == (
        "SAVE10",
        "10% off",
        "active",
        0,
    )
    for body in (
        {"code": "SAVE10", "kind": "percent", "percent_bp": 500},  # taken
        {"code": "FREE", "kind": "percent", "percent_bp": 10000},  # 100% off
        {"code": "X", "kind": "percent", "percent_bp": 500},  # too short
        {"code": "FLAT", "kind": "fixed", "amount_minor": 50000},  # no currency
        {"code": "SOON", "kind": "percent", "percent_bp": 500, "product_codes": ["nope"]},
    ):
        refused = coupon_admin.post(ADMIN_COUPONS, body, content_type="application/json")
        assert refused.status_code == 400, (body, refused.content)
    listed = coupon_admin.get(ADMIN_COUPONS).json()["results"]
    assert [c["code"] for c in listed] == ["SAVE10"]


def test_coupons_need_the_pricing_grant(staff_login):
    viewer, _user = staff_login("platform.staff_catalog.view")
    assert viewer.get(ADMIN_COUPONS).status_code == 200
    refused = viewer.post(
        ADMIN_COUPONS,
        {"code": "SAVE10", "kind": "percent", "percent_bp": 1000},
        content_type="application/json",
    )
    assert refused.status_code == 403


def test_the_product_page_previews_a_coupon_and_the_checkout_charges_it(
    shop, coupon_admin
):
    _coupon(coupon_admin, code="SAVE10", kind="percent", percent_bp=1000)
    page = Client().get("/api/v1/public/store/products/crm?currency=INR").json()
    assert page["tax"]["description"] == "GST (18%)"
    preview = (
        Client()
        .get(
            "/api/v1/public/store/products/crm/coupon",
            {"code": "save10", "currency": "INR", "amount_minor": CRM_YEAR},
        )
        .json()
    )
    assert preview == {
        "valid": True,
        "code": "SAVE10",
        "label": "10% off",
        "discount_minor": 83990,
    }
    unknown = (
        Client()
        .get(
            "/api/v1/public/store/products/crm/coupon",
            {"code": "NOPE10", "currency": "INR", "amount_minor": CRM_YEAR},
        )
        .json()
    )
    assert unknown["valid"] is False and unknown["reason"] == "unknown"

    quote = _quote(shop, coupon_code="save10").json()
    # 8,399 - 839.90 = 7,559.10; GST 1,360.64 -> 1,361.
    assert quote["coupon"] == {
        "code": "SAVE10",
        "label": "10% off",
        "discount_minor": 83990,
    }
    assert (quote["tax_minor"], quote["amount_due_minor"]) == (136100, 892010)
    order = _place(shop, coupon_code="SAVE10").json()
    assert (order["coupon_code"], order["discount_minor"], order["amount_due_minor"]) == (
        "SAVE10",
        83990,
        892010,
    )
    _invoice, lines = _invoice_lines(shop, order["invoice_id"])
    assert sorted(lines) == [("charge", CRM_YEAR - 83990), ("tax", 136100)]
    # The renewal price is not discounted: a coupon lowers the first payment only.
    assert order["recurring_amount_minor"] == CRM_YEAR
    assert coupon_admin.get(ADMIN_COUPONS).json()["results"][0]["redemptions"] == 1


def test_a_cancelled_order_gives_its_coupon_use_back(shop, coupon_admin):
    _coupon(coupon_admin, code="ONCE", kind="percent", percent_bp=2000, max_redemptions=1)
    order = _place(shop, coupon_code="ONCE").json()
    cancelled = shop["client"].post(
        url(shop["slug"], f"orders/{order['order_id']}/cancel")
    )
    assert cancelled.status_code == 200, cancelled.content
    assert coupon_admin.get(ADMIN_COUPONS).json()["results"][0]["redemptions"] == 0
    again = _place(shop, coupon_code="ONCE")
    assert again.status_code == 201, again.content


def test_a_used_up_coupon_is_refused_to_the_next_customer(
    shop, coupon_admin, console_org, login, inr_catalog
):
    _coupon(
        coupon_admin, code="FIRST1", kind="percent", percent_bp=2000, max_redemptions=1
    )
    assert _place(shop, coupon_code="FIRST1").status_code == 201
    other = open_shop(console_org, login, inr_catalog)
    refused = _place(other, coupon_code="FIRST1")
    assert refused.status_code == 409, refused.content
    body = refused.json()
    assert (body["reason"], body["coupon_reason"]) == ("coupon_invalid", "used_up")
    # Without the coupon the same customer may still buy.
    assert _place(other).status_code == 201


def test_once_per_customer_and_product_limits(shop, coupon_admin):
    _coupon(
        coupon_admin,
        code="BOOKS20",
        kind="percent",
        percent_bp=2000,
        product_codes=["books"],
    )
    refused = _quote(shop, coupon_code="BOOKS20")
    assert refused.status_code == 409
    assert refused.json()["coupon_reason"] == "not_for_these_products"

    _coupon(coupon_admin, code="WELCOME", kind="percent", percent_bp=1000)
    order = _place(shop, coupon_code="WELCOME").json()
    pay_order(shop["client"], shop["slug"], shop["id"], order)
    again = _quote(
        shop, plan_version_id=_version(shop, "books_professional"), coupon_code="WELCOME"
    )
    assert again.status_code == 409
    assert again.json()["coupon_reason"] == "already_used"


def test_a_fixed_coupon_applies_in_its_currency_and_never_takes_everything(
    shop, coupon_admin
):
    _coupon(coupon_admin, code="USD5", kind="fixed", amount_minor=500, currency="USD")
    assert _quote(shop, coupon_code="USD5").json()["coupon_reason"] == "wrong_currency"
    _coupon(coupon_admin, code="HUGE", kind="fixed", amount_minor=10**9, currency="INR")
    quote = _quote(shop, coupon_code="HUGE").json()
    # At most 99% off: something is always left to pay, so payment still happens.
    assert quote["discount_minor"] == CRM_YEAR * 99 // 100
    assert quote["requires_payment"] is True


def test_a_disabled_coupon_stops_working(shop, coupon_admin):
    made = _coupon(coupon_admin, code="SUMMER", kind="percent", percent_bp=1500)
    changed = coupon_admin.patch(
        f"{ADMIN_COUPONS}/{made['id']}",
        {"status": "disabled"},
        content_type="application/json",
    )
    assert changed.status_code == 200 and changed.json()["status"] == "disabled"
    assert _quote(shop, coupon_code="SUMMER").json()["coupon_reason"] == "disabled"
    missing = coupon_admin.patch(
        f"{ADMIN_COUPONS}/{uuid.uuid4()}",
        {"status": "active"},
        content_type="application/json",
    )
    assert missing.status_code == 404


# --- refund promise -------------------------------------------------------------------


def test_the_refund_promise_is_the_admins_and_shows_on_the_product_page(
    shop, django_capture_on_commit_callbacks
):
    from django.core.cache import cache

    from tests.extension.conftest import staff_scope
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.contracts.errors import CatalogValidationError

    page = Client().get("/api/v1/public/store/products/crm?currency=INR").json()
    assert page["product"]["refund_days"] is None
    crm = Product.objects.get(code="crm")
    with (
        django_capture_on_commit_callbacks(execute=True),
        staff_scope("platform.staff_catalog.manage") as ctx,
    ):
        catalog_services.update_product(
            ctx, product_id=crm.id, changes={"refund_days": 60}
        )
        for bad in (0.5, 400, -1, "30", True):
            with pytest.raises(CatalogValidationError):
                catalog_services.update_product(
                    ctx, product_id=crm.id, changes={"refund_days": bad}
                )
    cache.clear()
    page = Client().get("/api/v1/public/store/products/crm?currency=INR").json()
    assert page["product"]["refund_days"] == 60

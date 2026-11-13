"""What the Digital Store shows anyone (ADR-065): a plan's recommended flag, renewal
rule and included usage; a product's setup requirements and the ways to reach it
(official links only); the Recharge page's live rates and credit packs exactly as an
Admin entered them (nothing assumed); whether online payment is open; a return
address only a product's own registration approves; footer policies only once the
Super Admin publishes them; and no partner code that would hide a Store page."""

from __future__ import annotations

import uuid

import pytest
from django.core.cache import cache
from django.test import Client

pytestmark = pytest.mark.django_db

STORE = "/api/v1/public/store"


@pytest.fixture
def public(shop):
    cache.clear()
    yield shop
    cache.clear()


def _get(path, **query):
    response = Client().get(path, query)
    assert response.status_code == 200, response.content
    return response.json()


# --- the product page ------------------------------------------------------------------


def test_plans_carry_recommended_renewal_and_included_usage(public):
    from workeasy360.commercial.catalog.models import Plan, Product

    crm = Product.objects.get(code="crm")
    pro = Plan.objects.get(code="crm_professional")
    pro.included_usage = {"sms": 100, "ai_tokens": 50_000}
    pro.save(update_fields=["included_usage"])
    crm.recommended_plan = pro
    crm.save(update_fields=["recommended_plan"])
    cache.clear()
    page = _get(f"{STORE}/products/crm")
    plans = {p["plan_code"]: p for p in page["plans"]}
    assert plans["crm_professional"]["recommended"] is True
    assert plans["crm_professional"]["included_usage"] == {
        "sms": 100,
        "ai_tokens": 50_000,
    }
    assert plans["crm_professional"]["renewal_rule"] in ("auto_renew", "manual", "none")
    assert all(
        p["recommended"] is False for c, p in plans.items() if c != "crm_professional"
    )


def test_setup_requirements_and_access_are_the_admins_only(public):
    from workeasy360.commercial.catalog.models import Product

    page = _get(f"{STORE}/products/crm")
    assert page["product"]["setup_requirements"] == []
    assert page["product"]["access"]["apps"] == {}
    Product.objects.filter(code="crm").update(
        setup_requirements=["A WhatsApp Business number"],
        application_url="https://crm.example.com/app",
        app_links={
            "android": "https://play.google.com/store/apps/details?id=x",
            "ios": "https://apps.apple.com/app/id1",
        },
    )
    cache.clear()
    product = _get(f"{STORE}/products/crm")["product"]
    assert product["setup_requirements"] == ["A WhatsApp Business number"]
    assert product["access"] == {
        "web": True,
        "apps": {
            "android": "https://play.google.com/store/apps/details?id=x",
            "ios": "https://apps.apple.com/app/id1",
        },
    }


def test_setup_requirements_are_validated_like_the_other_lists(public):
    from workeasy360.commercial.catalog import domain

    assert "setup_requirements" in domain.MERCHANDISING_LISTS


# --- Recharge ---------------------------------------------------------------------------


def test_recharge_shows_only_what_an_admin_entered(public):
    page = _get(f"{STORE}/recharge", currency="INR")
    assert page["currency"] == "INR"
    assert page["rates"] == [] and page["credit_packs"] == []
    assert page["payment"]["available"] is True  # the test provider takes INR

    from workeasy360.commercial.wallets.models import CreditPack, UsageRate

    UsageRate.objects.create(
        meter="whatsapp",
        currency="INR",
        unit_amount=80,
        per_units=1,
        included_units=10,
        created_by_user_id=uuid.uuid4(),
    )
    UsageRate.objects.create(
        meter="sms",
        currency="GBP",
        unit_amount=4,
        per_units=1,
        created_by_user_id=uuid.uuid4(),
    )
    UsageRate.objects.create(
        meter="ai_tokens",
        currency="",
        unit_amount=1,
        per_units=1000,
        created_by_user_id=uuid.uuid4(),
    )
    CreditPack.objects.create(
        code="starter", name="Starter", credits=1000, price_minor=49900, currency="INR"
    )
    CreditPack.objects.create(
        code="old",
        name="Old",
        credits=10,
        price_minor=100,
        currency="INR",
        status="archived",
    )
    CreditPack.objects.create(
        code="gbp", name="GBP", credits=10, price_minor=100, currency="GBP"
    )
    page = _get(f"{STORE}/recharge", currency="INR")
    assert [(r["meter"], r["wallet"], r["unit_amount"]) for r in page["rates"]] == [
        ("ai_tokens", "ai_credits", 1),
        ("whatsapp", "consumption", 80),
    ]
    assert [p["code"] for p in page["credit_packs"]] == ["starter"]


def test_payment_option_names_the_provider_kind_only(public, fake_payments):
    option = _get(f"{STORE}/payment-option", currency="INR")
    assert option["available"] is True and option["provider"] == "fake"
    assert set(option) == {"available", "provider", "label", "methods", "test_mode"}
    fake_payments.status = "disabled"
    fake_payments.save(update_fields=["status"])
    assert _get(f"{STORE}/payment-option", currency="INR")["available"] is False


def test_razorpay_is_named_with_its_methods():
    from workeasy360.commercial.payments import checkout

    label, methods = checkout._PROVIDER_LABEL["razorpay"]
    assert label == "Razorpay" and "UPI" in methods


# --- a product's return address -----------------------------------------------------------


def test_a_return_address_is_allowed_only_on_the_products_registered_origins(public):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    def allowed(url, product="crm"):
        return _get(f"{STORE}/return-check", product=product, url=url)["allowed"]

    assert allowed("https://crm.example.com/billing") is False  # nothing registered
    ProductIntegration.objects.update_or_create(
        product_code="crm",
        defaults={
            "manifest": {
                "launch_url": "https://crm.example.com/launch",
                "redirect_uris": ["https://crm.example.com/oidc/callback"],
            },
        },
    )
    assert allowed("https://crm.example.com/billing?from=store") is True
    assert allowed("http://crm.example.com/billing") is False
    assert allowed("https://evil.example/billing") is False
    # A URL carrying credentials is refused (test data, not a secret).
    # pragma: allowlist nextline secret
    assert allowed("https://user:pw@crm.example.com/") is False
    assert allowed("javascript:alert(1)") is False
    assert allowed("https://crm.example.com/x", product="no_such") is False
    # The answer never echoes the address.
    body = _get(f"{STORE}/return-check", product="crm", url="https://evil.example/")
    assert body == {"allowed": False}


# --- footer policies ----------------------------------------------------------------------


def test_a_policy_reaches_the_store_only_once_published(staff_login):
    editor, _ = staff_login("platform.staff_support.manage")

    def post(path, body):
        return editor.post(path, body, content_type="application/json")

    made = post(
        "/api/v1/admin/help-content",
        {
            "title": "Refund & Cancellation Policy",
            "body": "Approved text.",
            "kind": "policy",
            "policy_key": "refund-cancellation",
        },
    )
    assert made.status_code == 201, made.content
    article = made.json()
    assert article["slug"] == "refund-cancellation" and article["audience"] == "all"
    assert _get("/api/v1/public/help", kind="policy")["results"] == []
    post(f"/api/v1/admin/help-content/{article['id']}/status", {"status": "published"})
    found = _get("/api/v1/public/help", kind="policy")["results"]
    assert [(a["slug"], a["body"]) for a in found] == [
        ("refund-cancellation", "Approved text.")
    ]
    # Never mixed into the FAQs.
    assert _get("/api/v1/public/help", audience="customer")["results"] == []
    # One article per policy.
    again = post(
        "/api/v1/admin/help-content",
        {
            "title": "Another",
            "body": "x",
            "kind": "policy",
            "policy_key": "refund-cancellation",
        },
    )
    assert again.status_code == 400
    unknown = post(
        "/api/v1/admin/help-content",
        {"title": "Made up", "body": "x", "kind": "policy", "policy_key": "anything"},
    )
    assert unknown.status_code == 400


# --- partner codes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    ["checkout", "recharge", "help", "partners", "policies", "product", "products"],
)
def test_a_store_page_is_never_a_partner_code(code):
    from workeasy360.commercial.partners.domain import validate_partner_code
    from workeasy360.contracts.errors import PartnerValidationError

    with pytest.raises(PartnerValidationError):
        validate_partner_code(code)
    assert validate_partner_code(f"{code}-co") == f"{code}-co"


def test_a_partner_named_after_a_store_page_gets_the_next_code(db):
    from workeasy360.commercial.controlplane import partner_onboarding

    assert partner_onboarding._free_code("Help") == "help-2"
    assert partner_onboarding._free_code("Acme Help") == "acme-help"

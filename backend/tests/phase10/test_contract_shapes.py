"""The Phase 10 client types match what the API really returns.

Grows with each sub-phase: every new endpoint's payload is added here as its
screen is added, so a renamed field fails a test rather than blanking a cell.
"""

from __future__ import annotations

import pytest

from tests.phase10 import ts_contract as ts

pytestmark = pytest.mark.django_db


def test_the_interface_reader_reads_the_phase10_client():
    assert "ResellerRow" in ts.INTERFACES and "experience" in ts.all_fields("ResellerRow")
    assert "PartnersOverview" in ts.INTERFACES
    # It reads the Phase 7 client too, so a shared type is one definition.
    assert "CapabilityInfo" in ts.INTERFACES and "group" in ts.all_fields(
        "CapabilityInfo"
    )


def test_the_overview_matches_its_type(console, viewer):
    body = viewer.get("/api/v1/admin/partners/overview").json()
    ts.assert_matches(body, "PartnersOverview", "overview")
    with_amounts = [c for c in body["cards"] if c.get("amounts")]
    assert with_amounts, "the fixture must produce a card with money on it"
    ts.assert_matches(body["cards"][0], "OverviewCard", "cards[0]")


def test_the_reseller_list_matches_its_type(console, viewer):
    body = viewer.get("/api/v1/admin/partners/resellers").json()
    ts.assert_page(body, "ResellerRow", "resellers")
    row = body["results"][0]
    assert row["commission"], "the fixture must produce ledger figures"
    ts.assert_matches(row["commission"][0], "ResellerCommission", "commission[0]")
    ts.assert_matches(row["payouts"], "ResellerPayouts", "payouts")
    ts.assert_matches(row["experience"][0], "ExperienceLabel", "experience[0]")


def test_the_capability_catalog_and_products_match_their_types(console, viewer, catalog):
    body = viewer.get("/api/v1/admin/partners/capabilities").json()
    ts.assert_matches(body, "CapabilityCatalog", "capabilities")
    ts.assert_matches(body["groups"][0], "CapabilityGroup", "groups[0]")
    ts.assert_matches(body["presets"][0], "CapabilityPreset", "presets[0]")
    products = viewer.get("/api/v1/admin/partners/products").json()
    assert any(p["plans"] for p in products["results"]), (
        "the fixture needs a sellable plan"
    )
    ts.assert_matches(products["results"][0], "ConsoleProduct", "products[0]")
    with_plans = next(p for p in products["results"] if p["plans"])
    ts.assert_matches(with_plans["plans"][0], "ConsolePlan", "plans[0]")


def test_the_new_list_filters_reach_the_existing_money_endpoints(console, staff_client):
    client, _u = staff_client("platform.staff_commission.view")
    unpaid = client.get("/api/v1/admin/commission/accruals?unpaid=1").json()
    assert len(unpaid["results"]) == 1  # the one cleared, unpaid commission
    open_payouts = client.get("/api/v1/admin/payouts?status=open").json()
    assert open_payouts["results"] == []
    assert client.get("/api/v1/admin/payouts?status=paid").json()["results"] == []


def test_the_review_and_the_commission_state_match_their_types(
    console, staff_client, catalog
):
    import json

    client, _u = staff_client(
        "platform.staff_partner.view", "platform.staff_commission.view"
    )
    professional = str(catalog["versions"]["crm_professional"].id)
    draft = {
        "partner": {
            "code": "shape-resell",
            "display_name": "Shape Resell",
            "payout_currency": "GBP",
        },
        "capabilities": ["customer.provision", "commission.earn"],
        "products": ["crm"],
        "partner_prices": [
            {"plan_version_id": professional, "interval": "monthly", "amount_minor": 4500}
        ],
        "commission": {"mode": "custom", "rate_bp": 1500},
        "administrator": {"email": "boss@shape.example", "role": "admin"},
    }
    body = client.post(
        "/api/v1/admin/partners/reseller-review",
        json.dumps(draft),
        content_type="application/json",
    ).json()
    ts.assert_matches(body, "ResellerReview", "review")
    ts.assert_matches(body["pricing"][0], "ReviewPricingRow", "pricing[0]")
    ts.assert_matches(body["commission"], "ReviewCommission", "commission")
    ts.assert_matches(body["partner"], "ReviewPartner", "partner")

    # A reviewed problem has the declared shape too.
    bad = client.post(
        "/api/v1/admin/partners/reseller-review",
        json.dumps({"partner": {"code": "X"}}),
        content_type="application/json",
    ).json()
    assert bad["problems"]
    ts.assert_matches(bad["problems"][0], "ReviewProblem", "problems[0]")

    partner_id = console["reseller"]["partner"].id
    state = client.get(f"/api/v1/admin/partners/{partner_id}/commission").json()
    ts.assert_matches(state, "PartnerCommissionState", "commission state")


def test_the_partner_pages_match_their_types(
    console, staff_client, staff_factory, catalog
):
    from workeasy360.commercial.partners import branding, services

    client, _u = staff_client(
        "platform.staff_partner.view",
        "platform.staff_commission.view",
        "platform.staff_audit.view",
    )
    partner = console["reseller"]["partner"]
    _p, pricer = staff_factory("platform.staff_pricing.manage")
    services.set_partner_price(
        pricer,
        partner.id,
        plan_version_id=catalog["versions"]["crm_professional"].id,
        interval="monthly",
        amount_minor=4500,
    )
    base = f"/api/v1/admin/partners/{partner.id}"

    body = client.get(f"{base}/summary").json()
    ts.assert_matches(body, "PartnerSummaryView", "summary")
    ts.assert_matches(body["setup_progress"][0], "SetupStep", "setup_progress[0]")
    ts.assert_matches(body["payouts"], "SummaryPayouts", "payouts")
    ts.assert_matches(body["white_label"], "SummaryWhiteLabel", "white_label")
    ts.assert_matches(body["customers"], "SummaryCustomers", "customers")

    page = client.get(f"{base}/customers").json()
    ts.assert_page(page, "PartnerCustomerAdminRow", "customers")
    ts.assert_matches(page["results"][0]["owner"], "OwnerActivation", "owner")
    everyone = client.get("/api/v1/admin/partners/customers").json()
    ts.assert_page(everyone, "PartnerCustomerAdminRow", "all customers")

    pricing = client.get(f"{base}/pricing").json()
    assert pricing["results"]
    ts.assert_matches(pricing["results"][0], "PartnerPricingRow", "pricing[0]")

    ts.assert_page(client.get(f"{base}/activity").json(), "AuditEntry", "activity")

    wl = console["white_label"]
    branding.update_brand(wl["actor"](), brand_name="Shape Brand", accent_color="#1a56db")
    brand = client.get(f"/api/v1/admin/partners/{wl['partner'].id}/brand").json()
    ts.assert_matches(brand["brand"], "StoredBrand", "brand")


def test_the_staff_ledger_rows_match_their_type(console, staff_client):
    client, _u = staff_client("platform.staff_commission.view")
    page = client.get(
        f"/api/v1/admin/commission/accruals?partner_id={console['reseller']['partner'].id}"
    ).json()
    from tests.phase10 import ts_contract as contract

    contract.assert_page(page, "AccrualRow", "ledger")
    row = page["results"][0]
    assert {"organization_name", "source_ref", "rate_bp", "payout"} <= set(row)

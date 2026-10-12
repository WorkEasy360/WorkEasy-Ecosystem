"""The new client types match what the API really returns.

Every payload the setup wizards and the partner tabs read is declared as a
TypeScript interface, one field per line; TypeScript trusts those declarations,
so a field renamed on the server would show up as a blank cell, not an error.
This checks them against responses produced by the real stack.
"""

from __future__ import annotations

import json

import pytest

from tests.partner_setup.conftest import MANAGE, VIEW, profile_url, put
from tests.phase10 import ts_contract as ts
from tests.phase10.test_10b_review_invite_price import draft, review

pytestmark = pytest.mark.django_db


def test_the_reader_sees_the_new_interfaces():
    for name in (
        "PartnerProfile",
        "PartnerBusiness",
        "PartnerContacts",
        "WhiteLabelSetup",
        "WhiteLabelProductRow",
        "ReviewTrial",
        "ActivationReadiness",
        "CommissionExample",
        "PartnerMemberRow",
        "BrandPortalFields",
        "PublicPortalFields",
    ):
        assert name in ts.INTERFACES, name


def test_the_profile_matches_its_type(manager, console):
    from workeasy360.commercial.partners import profile

    partner = console["draft"]
    put(
        manager,
        profile_url(partner.id),
        {
            "business": {
                "business_email": "a@example.com",
                "country": "IN",
                "address": {"city": "Pune"},
            },
            "contacts": {
                "primary": {"name": "P", "email": "p@example.com"},
                "billing": {"name": "B"},
            },
            "internal_notes": "x",
            "expected_version": partner.version,
        },
    )
    body = manager.get(profile_url(partner.id)).json()
    ts.assert_matches(body, "PartnerProfile", "profile")
    ts.assert_matches(body["business"], "PartnerBusiness", "business")
    ts.assert_matches(body["business"]["address"], "PartnerAddress", "address")
    ts.assert_matches(body["contacts"], "PartnerContacts", "contacts")
    ts.assert_matches(body["contacts"]["primary"], "PartnerContactView", "primary")
    ts.assert_matches(body["support"], "PartnerSupportDetails", "support")
    ts.assert_matches(body["payout"], "PartnerPayoutPrefs", "payout")
    assert profile.profile_view(partner.id) == body


def test_the_white_label_setup_matches_its_type(manager, channel):
    from workeasy360.commercial.partners import branding, domains, services

    ctx, pid = channel["staff_ctx"], channel["partner"].id
    services.enable_white_label(ctx, pid, reason="Enabled for the shape test")
    branding.update_brand(
        ctx, partner_id=pid, brand_name="Shape", portal={"short_name": "S"}
    )
    domains.request_domain(ctx, hostname="app.shape-example.com", partner_id=pid)
    body = manager.get(f"/api/v1/admin/partners/{pid}/white-label").json()
    ts.assert_matches(body, "WhiteLabelSetup", "setup")
    ts.assert_matches(body["products"][0], "WhiteLabelProductRow", "products[0]")
    ts.assert_matches(
        body["authentication"], "WhiteLabelAuthentication", "authentication"
    )
    ts.assert_matches(body["checks"], "WhiteLabelChecks", "checks")
    ts.assert_matches(body["brand"], "StoredBrand", "brand")
    ts.assert_matches(body["brand"]["portal"], "BrandPortalFields", "brand.portal")
    ts.assert_matches(body["domain"], "DomainRow", "domain")
    assert body["experience"][0]["key"] and set(body["experience"][0]) == {"key", "label"}


def test_the_review_matches_its_type(staff_client, catalog, channel):
    client, _u = staff_client(VIEW)
    result = review(
        client,
        draft(
            catalog,
            white_label=True,
            profile={
                "business": {"business_email": "b@example.com"},
                "contacts": {"primary": {"name": "P", "email": "p@example.com"}},
            },
            product_options={"crm": {"trial_allowed": True}},
            commission={"mode": "custom", "rate_bp": 2000},
            administrator={
                "email": "boss@acme-resell.example",
                "role": "admin",
                "first_name": "A",
                "send_invitation": "on_activation",
            },
        ),
    )
    ts.assert_matches(result, "ResellerReview", "review")
    ts.assert_matches(result["profile"], "ReviewProfile", "profile")
    ts.assert_matches(result["trials"][0], "ReviewTrial", "trials[0]")
    ts.assert_matches(result["commission"], "ReviewCommission", "commission")
    ts.assert_matches(result["commission"]["example"], "CommissionExample", "example")
    ts.assert_matches(result["readiness"], "ActivationReadiness", "readiness")
    ts.assert_matches(result["white_label"], "ReviewWhiteLabel", "white_label")
    ts.assert_matches(result["administrator"], "ReviewAdministrator", "administrator")
    if result["readiness"]["missing"]:
        ts.assert_matches(
            result["readiness"]["missing"][0], "ReadinessItem", "missing[0]"
        )
    bare = review(
        client,
        {
            "partner": {
                "code": "bare-shape",
                "display_name": "Bare",
                "payout_currency": "GBP",
            }
        },
    )
    ts.assert_matches(bare, "ResellerReview", "bare")
    ts.assert_matches(bare["missing_optional"][0], "OptionalItem", "missing_optional[0]")
    assert bare["administrator"] is None


def test_the_products_trials_and_capabilities_match_their_types(staff_client, catalog):
    from tests.phase11.conftest import configure_trial

    configure_trial(catalog)
    client, _u = staff_client(VIEW)
    products = client.get("/api/v1/admin/partners/products").json()
    crm = next(p for p in products["results"] if p["code"] == "crm")
    ts.assert_matches(crm, "ConsoleProduct", "crm")
    ts.assert_matches(crm["trial"], "ConsoleTrial", "crm.trial")
    capabilities = client.get("/api/v1/admin/partners/capabilities").json()
    ts.assert_matches(capabilities["results"][0], "CapabilityInfo", "capability")
    assert all(c["short_label"] for c in capabilities["results"])


def test_the_partner_detail_summary_and_activity_match_their_types(staff_client, console):
    client, _u = staff_client(VIEW, "platform.staff_audit.view")
    pid = console["reseller"]["partner"].id
    detail = client.get(f"/api/v1/admin/partners/{pid}").json()
    ts.assert_matches(detail, "PartnerDetail", "detail")
    ts.assert_matches(detail["members"][0], "PartnerMemberRow", "members[0]")
    summary = client.get(f"/api/v1/admin/partners/{pid}/summary").json()
    ts.assert_matches(summary, "PartnerSummaryView", "summary")
    ts.assert_matches(summary["products"][0], "SummaryProduct", "products[0]")
    ts.assert_matches(summary["activation"], "ActivationReadiness", "activation")
    ts.assert_page(
        client.get(f"/api/v1/admin/partners/{pid}/activity").json(),
        "AuditEntry",
        "activity",
    )


def test_the_stored_brand_and_the_public_portal_match_their_types(
    manager, partner_factory, console_org, login
):
    from workeasy360.commercial.partners import services

    fx = partner_factory("branding.manage", "domain.manage")
    response = put(
        manager,
        f"/api/v1/admin/partners/{fx['partner'].id}/brand",
        {"brand_name": "Shape", "portal": {"welcome_message": "Hello"}},
    )
    ts.assert_matches(response.json(), "Brand", "brand")
    ts.assert_matches(response.json()["portal"], "BrandPortalFields", "portal")
    org = console_org()
    services.link_customer(
        fx["staff_ctx"],
        fx["partner"].id,
        org["org"].id,
        reason="A customer for the shape test",
    )
    own = login(org["owner"]).get(f"/api/v1/orgs/{org['slug']}/brand").json()["brand"]
    ts.assert_matches(own, "OwnBrand", "own")
    ts.assert_matches(own["portal"], "PublicPortalFields", "own.portal")
    assert json.dumps(own).count("sender_name") == 0
    assert MANAGE

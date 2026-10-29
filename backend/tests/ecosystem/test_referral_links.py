"""An affiliate's own referral links (ADR-053): a code of its choosing, a
product (and plan) to open, a campaign; the destination is the server's, never
a URL; visits count clicks, unique visitors and registrations; commission still
comes only from a paid sale."""

from __future__ import annotations

import pytest

from tests.ecosystem.conftest import list_products
from workeasy360.commercial.commissions import referrals, selectors
from workeasy360.contracts.errors import PartnerValidationError

pytestmark = pytest.mark.django_db

AFFILIATE = (
    "referral.create",
    "referral.view",
    "commission.earn",
    "commission.view",
    "payout.view",
)


@pytest.fixture
def affiliate(partner_factory, catalog, staff_factory):
    from workeasy360.commercial.partners import promotion

    list_products("crm", "books")
    fx = partner_factory(*AFFILIATE)
    fx["catalog"] = catalog
    # ADR-054: a product link needs the product's promotion grant.
    _u, staff = staff_factory("platform.staff_partner.manage")
    for code in ("crm", "books"):
        promotion.grant_promotion(staff, fx["partner"].id, product_code=code)
    return fx


def test_an_affiliate_chooses_its_own_code(affiliate):
    code = referrals.create_referral_code(affiliate["actor"](), code="aman360")
    assert code.code == "AMAN360"
    with pytest.raises(PartnerValidationError) as taken:
        referrals.create_referral_code(affiliate["actor"](), code="AMAN360")
    assert taken.value.context["field"] == "code"


@pytest.mark.parametrize("bad", ["AB", "1234567", "has space", "x" * 17, "<script>"])
def test_a_chosen_code_is_letters_and_digits_only(affiliate, bad):
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(affiliate["actor"](), code=bad)


def test_a_product_link_opens_that_products_store_page(affiliate):
    code = referrals.create_referral_code(
        affiliate["actor"](), product_code="crm", plan_code="crm_professional"
    )
    assert referrals.destination(code) == "/products/crm?plan=crm_professional"
    general = referrals.create_referral_code(affiliate["actor"]())
    assert referrals.destination(general) == "/signup"


def test_a_link_cannot_promote_a_product_closed_to_affiliates(affiliate):
    from workeasy360.commercial.catalog.models import Product

    Product.objects.filter(code="books").update(affiliate_enabled=False)
    with pytest.raises(PartnerValidationError) as refused:
        referrals.create_referral_code(affiliate["actor"](), product_code="books")
    assert refused.value.context["field"] == "product_code"
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(
            affiliate["actor"](), product_code="crm", plan_code="books_professional"
        )


def test_campaigns_group_links_and_stay_the_partners_own(affiliate, partner_factory):
    campaign = referrals.create_campaign(
        affiliate["actor"](), name="CRM October Campaign"
    )
    with pytest.raises(PartnerValidationError):
        referrals.create_campaign(affiliate["actor"](), name="crm october campaign")
    code = referrals.create_referral_code(
        affiliate["actor"](), product_code="crm", campaign_id=str(campaign.id)
    )
    assert code.campaign_id == campaign.id and code.label == "CRM October Campaign"
    other = partner_factory(*AFFILIATE)
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(other["actor"](), campaign_id=str(campaign.id))
    archived = referrals.set_campaign_status(
        affiliate["actor"](), campaign.id, active=False
    )
    assert archived.status == "revoked"
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(affiliate["actor"](), campaign_id=str(campaign.id))


def test_visits_count_clicks_unique_visitors_and_registrations(affiliate):
    from datetime import timedelta

    from django.utils import timezone

    code = referrals.create_referral_code(affiliate["actor"](), code="TRACK1")
    first = referrals.capture_referral("TRACK1", visitor_id="v" * 24)
    referrals.capture_referral("track1", visitor_id="v" * 24)  # same browser again
    referrals.capture_referral("TRACK1", visitor_id="w" * 24)
    assert referrals.record_registration(first) is True
    assert referrals.record_registration(first) is False  # once
    now = timezone.now()
    activity = selectors.referral_activity(
        affiliate["partner"].id, now - timedelta(days=1), now + timedelta(minutes=1)
    )
    assert activity["totals"] == {
        "clicks": 3,
        "visitors": 2,
        "registrations": 1,
        "trials": 0,
        "conversions": 0,
    }
    assert activity["by_code"][str(code.id)]["visitors"] == 2


def test_the_public_link_records_the_visit_and_answers_a_path(
    affiliate, client, settings
):
    referrals.create_referral_code(
        affiliate["actor"](), code="CRMLINK", product_code="crm"
    )
    response = client.post(
        "/api/v1/public/referrals", {"code": "CRMLINK"}, content_type="application/json"
    )
    assert response.status_code == 201, response.content
    assert response.json()["destination"] == "/products/crm"
    assert settings.REFERRAL_COOKIE_NAME in response.cookies
    visitor = response.cookies[settings.REFERRAL_VISITOR_COOKIE_NAME]
    assert visitor["httponly"] and visitor.value
    unknown = client.post(
        "/api/v1/public/referrals", {"code": "NOPE404"}, content_type="application/json"
    )
    assert unknown.status_code == 404


def test_a_click_alone_never_earns(affiliate, margin_ctx):
    from workeasy360.commercial.commissions import rules
    from workeasy360.commercial.commissions.models import CommissionAccrual

    rules.create_rule_version(
        margin_ctx, code="default", rate_bp=1000, reason="Programme default"
    )
    referrals.create_referral_code(affiliate["actor"](), code="NOEARN1")
    referrals.capture_referral("NOEARN1", visitor_id="z" * 24)
    assert not CommissionAccrual.objects.filter(
        partner_id=affiliate["partner"].id
    ).exists()


def test_the_affiliate_dashboard_reads_products_and_the_funnel(affiliate):
    from workeasy360.commercial.controlplane import partner_workspaces

    referrals.create_referral_code(
        affiliate["actor"](), code="DASH360", product_code="crm"
    )
    overview = partner_workspaces.affiliate_overview(affiliate["actor"]())
    assert {p["code"] for p in overview["products"]} == {"crm", "books"}
    assert overview["kpis"]["signups"] == 0 and overview["kpis"]["unique_visitors"] == 0
    link = next(link for link in overview["links"] if link["code"] == "DASH360")
    assert link["url"].endswith("/r/DASH360")
    assert link["product_name"] == "CRM"
    assert set(overview["not_implemented"]) == {"promo_discount_codes"}
    assert overview["top_products"] == []


def test_a_listing_image_is_public_only_for_a_store_product(client, catalog):
    import uuid

    response = client.get(f"/api/v1/public/products/crm/media/{uuid.uuid4()}")
    assert response.status_code == 404

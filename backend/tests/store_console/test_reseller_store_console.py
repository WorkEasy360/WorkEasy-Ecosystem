"""The reseller Store Console (ADR-055, kind ``sell``), through the real
catalog, request, product-grant, customer-provisioning and sale code.

Staff publish a product once -> a reseller finds it in Explore -> asks to sell
it -> staff approve -> the product grant exists and the reseller can sell it
to a customer; rejection and revocation leave it unable to; a revocation never
touches a customer's existing subscription.
"""

from __future__ import annotations

import pytest

from tests.phase7.support import CAPS, provision, sell
from tests.store_console.conftest import REQUEST
from workeasy360.commercial.controlplane import partner_workspaces
from workeasy360.commercial.partners import promotion, promotion_requests
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerRequestConflict,
    PartnerValidationError,
    PermissionDenied,
)

pytestmark = pytest.mark.django_db

SELLING = {
    **REQUEST,
    "channel": "existing_customers",
    "strategy": "Offer it to our 40 accounting clients.",
}


@pytest.fixture
def reseller(console, partner_factory, user_factory):
    fx = partner_factory(*CAPS)
    return {**fx, "user_factory": user_factory, "catalog": console["catalog"]}


def ask(fx, product_code="crm", **changes):
    return promotion_requests.submit(
        fx["actor"](),
        body={**SELLING, "product_code": product_code, **changes},
        kind="sell",
    )


def states(fx) -> dict[str, str]:
    view = partner_workspaces.store_console(fx["actor"](), kind="sell")
    return {p["code"]: p["status"] for p in view["products"]}


def may_sell(fx, code="crm", plan="crm_professional") -> bool:
    return partner_selectors.may_sell(fx["partner"].id, product_code=code, plan_code=plan)


def test_explore_lists_the_store_with_margin_and_commission_disclosed(reseller):
    from workeasy360.commercial.catalog.models import Product

    view = partner_workspaces.store_console(reseller["actor"](), kind="sell")
    assert view["kind"] == "sell" and view["can_request"] is True
    assert {p["code"]: p["status"] for p in view["products"]} == {
        "crm": "not_requested",
        "books": "not_requested",
    }
    card = view["products"][0]
    assert card["commission"] == {"rate_bp": 1000, "recurring": False}
    assert card["margin"] == {"discount_bp": None}  # no margin rule: earns commission
    assert set(view["channels"]) >= {"existing_customers", "direct_sales"}
    # A product closed to resellers is not offered; the affiliate flag is irrelevant.
    Product.objects.filter(code="books").update(
        reseller_enabled=False, affiliate_enabled=True
    )
    assert "books" not in states(reseller)
    with pytest.raises(PartnerValidationError):
        ask(reseller, "books")


def test_the_margin_a_sale_would_earn_is_shown_with_pricing_view(reseller, staff_factory):
    from workeasy360.commercial.commissions import margins

    _u, ctx = staff_factory("platform.staff_commission.manage")
    margins.set_margin(
        ctx, experience="reseller", discount_bp=2000, reason="Reseller terms"
    )
    cards = partner_workspaces.store_console(reseller["actor"](), kind="sell")["products"]
    assert {c["code"]: c["margin"] for c in cards}["crm"] == {"discount_bp": 2000}


def test_asking_grants_nothing_and_a_sale_is_still_refused(reseller):
    row = ask(reseller)
    assert (row["status"], row["kind"]) == ("pending", "sell")
    assert states(reseller)["crm"] == "pending"
    assert not may_sell(reseller)
    result, _owner = provision(reseller)
    with pytest.raises(PermissionDenied):
        sell(reseller, result.organization_id)
    with pytest.raises(PartnerRequestConflict):
        ask(reseller)


def test_approval_lets_the_reseller_sell_that_product_only(reseller, console):
    row = ask(reseller)
    approved = promotion_requests.approve(console["staff"], row["id"])
    assert approved["status"] == "approved" and approved["grant_id"]
    assert states(reseller)["crm"] == "approved"
    assert may_sell(reseller)
    result, _owner = provision(reseller)
    item = sell(reseller, result.organization_id)
    assert item.product_code == "crm"
    assert not may_sell(reseller, "books", "books_professional")
    # The product grant is the ordinary one: Partner Products lists it.
    granted = [
        g["product_code"]
        for g in partner_selectors.granted_products(reseller["partner"].id)
    ]
    assert granted == ["crm"]
    with pytest.raises(PartnerRequestConflict):
        ask(reseller)  # already sells it


def test_rejection_keeps_it_unsellable_and_the_reseller_may_ask_again(reseller, console):
    row = ask(reseller)
    promotion_requests.reject(
        console["staff"], row["id"], reason="Finish onboarding first."
    )
    [card] = [
        p
        for p in partner_workspaces.store_console(reseller["actor"](), kind="sell")[
            "products"
        ]
        if p["code"] == "crm"
    ]
    assert card["status"] == "rejected"
    assert card["request"]["decision_reason"] == "Finish onboarding first."
    assert not may_sell(reseller)
    assert ask(reseller)["id"] != row["id"]


def test_revocation_stops_new_sales_and_keeps_existing_subscriptions(reseller, console):
    from workeasy360.commercial.partners import services

    row = ask(reseller)
    approved = promotion_requests.approve(console["staff"], row["id"])
    result, _owner = provision(reseller)
    sold = sell(reseller, result.organization_id)

    revoked = promotion_requests.revoke(
        console["staff"], row["id"], reason="Contract ended."
    )
    assert (
        revoked["status"] == "revoked" and revoked["revoke_reason"] == "Contract ended."
    )
    assert states(reseller)["crm"] == "revoked"
    assert not may_sell(reseller)
    second, _ = provision(reseller, name="Customer Y")
    with pytest.raises(PermissionDenied):
        sell(reseller, second.organization_id)
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    with organization_scope(result.organization_id, reason="Test reads the sold item"):
        assert SubscriptionItem.unscoped.filter(id=sold.id).exists()

    # Revoking on the partner's page marks the request too.
    again = ask(reseller)
    second_grant = promotion_requests.approve(console["staff"], again["id"])["grant_id"]
    services.revoke_product_grant(
        console["staff"], reseller["partner"].id, grant_id=second_grant
    )
    detail = promotion_requests.staff_detail(console["staff"], again["id"])
    assert detail["request"]["status"] == "revoked"
    assert approved["grant_id"] != second_grant


def test_the_two_kinds_are_separate_permissions(console, partner_factory):
    """A partner that both promotes and sells asks for each separately, and
    approving one grants nothing of the other."""
    both = partner_factory(*CAPS, "referral.create", "referral.view")
    sell_row = promotion_requests.submit(
        both["actor"](), body={**SELLING, "product_code": "crm"}, kind="sell"
    )
    promote_row = promotion_requests.submit(
        both["actor"](), body={**REQUEST, "product_code": "crm"}, kind="promote"
    )
    promotion_requests.approve(console["staff"], sell_row["id"])
    assert partner_selectors.may_sell(
        both["partner"].id, product_code="crm", plan_code="crm_professional"
    )
    assert not promotion.may_promote(both["partner"].id, "crm")
    assert (
        promotion_requests.staff_detail(console["staff"], promote_row["id"])["request"][
            "status"
        ]
        == "pending"
    )
    queue = promotion_requests.staff_queue(console["staff"], kind="sell", status="all")
    assert [r["id"] for r in queue["items"]] == [sell_row["id"]]


def test_only_sellers_ask_to_sell_and_only_affiliates_ask_to_promote(
    console, partner_factory
):
    affiliate = console["affiliate"]
    with pytest.raises((PartnerValidationError, PermissionDenied)):
        promotion_requests.submit(
            affiliate["actor"](), body={**SELLING, "product_code": "crm"}, kind="sell"
        )
    assert partner_workspaces.store_console(affiliate["actor"](), kind="promote")[
        "can_request"
    ]
    seller = partner_factory(*CAPS)
    with pytest.raises((PartnerValidationError, PermissionDenied)):
        promotion_requests.submit(
            seller["actor"](), body={**REQUEST, "product_code": "crm"}, kind="promote"
        )
    viewer = partner_factory(*CAPS, role="viewer")
    with pytest.raises(PermissionDenied):
        promotion_requests.submit(
            viewer["actor"](), body={**SELLING, "product_code": "crm"}, kind="sell"
        )
    with pytest.raises(PartnerValidationError):
        promotion_requests.submit(
            seller["actor"](), body={**SELLING, "product_code": "crm"}, kind="everything"
        )


def test_the_journey_through_the_api(
    reseller, console, login, staff_login, partner_factory
):
    from tests.affiliate_products.conftest import sign_in

    client = sign_in(login, reseller)
    base = f"/api/v1/partner/{reseller['partner'].id}/reseller"
    explore = client.get(f"{base}/store").json()
    assert explore["kind"] == "sell" and explore["can_request"] is True
    asked = client.post(
        f"{base}/product-requests",
        {**SELLING, "product_code": "crm"},
        content_type="application/json",
    )
    assert asked.status_code == 201, asked.content
    twice = client.post(
        f"{base}/product-requests",
        {**SELLING, "product_code": "crm"},
        content_type="application/json",
    )
    assert twice.status_code == 409

    staff, _ = staff_login("platform.staff_partner.manage", "platform.staff_partner.view")
    queue = staff.get(
        "/api/v1/admin/partner-product-requests?status=pending&kind=sell"
    ).json()
    assert [r["id"] for r in queue["items"]] == [asked.json()["id"]]
    assert queue["items"][0]["kind"] == "sell"
    done = staff.post(
        f"/api/v1/admin/partner-product-requests/{asked.json()['id']}/approve",
        {},
        content_type="application/json",
    )
    assert done.status_code == 200, done.content
    assert client.get(f"{base}/store").json()["counts"]["approved"] == 1

    other = partner_factory(*CAPS)
    stranger = sign_in(login, other)
    assert stranger.get(f"{base}/store").status_code in (403, 404)
    forged = stranger.post(
        f"{base}/product-requests",
        {**SELLING, "product_code": "books"},
        content_type="application/json",
    )
    assert forged.status_code in (403, 404)
    with pytest.raises(PartnerRecordNotFound):
        promotion_requests.staff_detail(console["staff"], other["partner"].id)

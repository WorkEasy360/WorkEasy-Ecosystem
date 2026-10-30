"""The affiliate Store Console (ADR-055), end to end through the real catalog,
request, grant, referral, order, payment-webhook and accrual code.

The journey is the brief's: staff publish a product once -> it appears in the
affiliate's Explore -> the affiliate asks -> staff approve -> it is in My
Products and links, codes and campaigns work -> conversions earn exactly once.
Around it: duplicates, refusals, revocation, other partners, races, history.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from tests.affiliate_products.conftest import buy, ledger, refer, sign_in
from tests.extension.conftest import staff_scope
from tests.store.conftest import CATALOG_STAFF
from tests.store_console.conftest import REQUEST, ask
from workeasy360.commercial.commissions import accrual, referrals
from workeasy360.commercial.controlplane import partner_workspaces
from workeasy360.commercial.partners import promotion, promotion_requests
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerRequestConflict,
    PartnerValidationError,
    PermissionDenied,
)

pytestmark = pytest.mark.django_db


def _actor(fx):
    return fx["affiliate"]["actor"]()


def _partner_id(fx):
    return fx["affiliate"]["partner"].id


def _states(fx) -> dict[str, str]:
    return {
        p["code"]: p["status"]
        for p in partner_workspaces.store_console(_actor(fx))["products"]
    }


# --- 1. the catalog is the one source ----------------------------------------------------------


def test_a_published_product_appears_in_explore_and_edits_follow(console):
    from workeasy360.commercial.catalog import services as catalog_services
    from workeasy360.commercial.catalog.models import Product

    assert _states(console) == {"crm": "not_requested", "books": "not_requested"}

    crm = Product.objects.get(code="crm")
    with staff_scope(*CATALOG_STAFF) as ctx:
        catalog_services.update_product(
            ctx,
            product_id=crm.id,
            changes={"name": "Easy CRM Pro", "tagline": "Sell more"},
        )
    [card] = [
        p
        for p in partner_workspaces.store_console(_actor(console))["products"]
        if p["code"] == "crm"
    ]
    assert (card["name"], card["tagline"]) == ("Easy CRM Pro", "Sell more")
    # The member may see commission, so the rate a sale would earn is disclosed.
    assert card["commission"] == {"rate_bp": 1000, "recurring": False}

    # Withdrawn from the Store, closed to affiliates or not listed: not promotable.
    Product.objects.filter(code="books").update(listing="hidden")
    assert "books" not in _states(console)
    Product.objects.filter(code="books").update(listing="listed", affiliate_enabled=False)
    assert "books" not in _states(console)
    with pytest.raises(PartnerValidationError):
        ask(console, "books")


def test_commission_is_not_disclosed_without_commission_view(console, partner_factory):
    quiet = partner_factory("referral.create", "referral.view")
    cards = partner_workspaces.store_console(quiet["actor"]())["products"]
    assert cards and all(c["commission"] is None for c in cards)


# --- 2. asking --------------------------------------------------------------------------------


def test_asking_records_a_pending_request_and_notifies_the_deciders(
    console, staff_factory, django_capture_on_commit_callbacks
):
    from workeasy360.platform.notifications.models import Notification

    decider, _ = staff_factory("platform.staff_partner.manage")
    onlooker, _ = staff_factory("platform.staff_partner.view")
    with django_capture_on_commit_callbacks(execute=True):
        row = ask(console)
    assert row["status"] == "pending" and row["product_code"] == "crm"
    assert _states(console)["crm"] == "pending"
    # Nothing is granted by asking.
    assert not promotion.may_promote(_partner_id(console), "crm")
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(_actor(console), product_code="crm")

    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the queued staff notices", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        sent = set(
            Notification.unscoped.filter(
                template_key=promotion_requests.TEMPLATE_KEY
            ).values_list("recipient_email", flat=True)
        )
        # The audit entry names the product and channel, never the partner's words.
        entry = AuditEvent.unscoped.filter(action="partner.promotion_requested").latest(
            "occurred_at"
        )
    assert decider.email in sent and onlooker.email not in sent
    assert entry.changes["product_code"] == "crm"
    assert "comparison article" not in str(entry.changes)


def test_a_second_request_for_the_same_product_is_refused(console):
    ask(console)
    with pytest.raises(PartnerRequestConflict):
        ask(console)
    ask(console, "books")  # another product is its own request
    assert sorted(
        r["product_code"]
        for r in partner_workspaces.store_console(_actor(console))["requests"]
    ) == [
        "books",
        "crm",
    ]


def test_the_database_holds_one_pending_request_whatever_slips_past(console):
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.commercial.partners.models import PartnerPromotionRequest

    ask(console)
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerPromotionRequest.objects.create(
            partner_id=_partner_id(console),
            product=Product.objects.get(code="crm"),
            channel="website",
            audience="x" * 5,
            strategy="y" * 20,
            requested_by=console["affiliate"]["user"],
            requested_at=timezone.now(),
        )


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"channel": "carrier pigeon"}, "channel"),
        ({"audience": ""}, "audience"),
        ({"strategy": "short"}, "strategy"),
        ({"website": "http://plain.example.com"}, "website"),
        ({"website": "javascript:alert(1)"}, "website"),
        ({"phone": "+91 99999"}, "phone"),
    ],
)
def test_the_request_form_is_validated(console, changes, field):
    with pytest.raises(PartnerValidationError) as refused:
        ask(console, **changes)
    assert refused.value.context["field"] == field


def test_only_a_member_who_may_create_links_can_ask(console, partner_factory):
    viewer = partner_factory("referral.create", "referral.view", role="viewer")
    with pytest.raises(PermissionDenied):
        promotion_requests.submit(
            viewer["actor"](), body={**REQUEST, "product_code": "crm"}
        )
    seller = partner_factory("customer.manage", "referral.view")
    with pytest.raises((PermissionDenied, PartnerValidationError)):
        promotion_requests.submit(
            seller["actor"](), body={**REQUEST, "product_code": "crm"}
        )
    blocked = partner_workspaces.store_console(seller["actor"]())
    assert blocked["can_request"] is False and blocked["cannot_request_reason"]


# --- 3. deciding -----------------------------------------------------------------------------


def test_approval_moves_the_product_to_my_products_and_links_work(console):
    row = ask(console)
    approved = promotion_requests.approve(console["staff"], row["id"])
    assert approved["status"] == "approved" and approved["grant_id"]
    assert _states(console)["crm"] == "approved"
    assert [
        p["code"] for p in partner_workspaces.my_products(_actor(console))["products"]
    ] == ["crm"]

    campaign = referrals.create_campaign(_actor(console), name="CRM launch")
    link = referrals.create_referral_code(
        _actor(console),
        product_code="crm",
        campaign_id=str(campaign.id),
        code="CRMLAUNCH",
    )
    assert (
        link.campaign_id == campaign.id and referrals.destination(link) == "/products/crm"
    )
    # Approval is product-specific: Books is still closed.
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(_actor(console), product_code="books")


def test_a_decided_request_cannot_be_decided_again(console):
    row = ask(console)
    promotion_requests.approve(console["staff"], row["id"])
    with pytest.raises(PartnerRequestConflict):
        promotion_requests.approve(console["staff"], row["id"])
    with pytest.raises(PartnerRequestConflict):
        promotion_requests.reject(console["staff"], row["id"], reason="Too late now")
    from workeasy360.commercial.partners.models import PartnerPromotionGrant

    assert (
        PartnerPromotionGrant.objects.filter(partner_id=_partner_id(console)).count() == 1
    )


def test_rejection_needs_a_reason_the_partner_reads_and_they_may_ask_again(console):
    row = ask(console)
    with pytest.raises(PartnerValidationError):
        promotion_requests.reject(console["staff"], row["id"], reason="")
    promotion_requests.reject(
        console["staff"], row["id"], reason="Your site does not cover CRM buyers."
    )
    view = partner_workspaces.store_console(_actor(console))
    [card] = [p for p in view["products"] if p["code"] == "crm"]
    assert card["status"] == "rejected"
    assert card["request"]["decision_reason"] == "Your site does not cover CRM buyers."
    assert "decided_by" not in card["request"]  # the partner is not told who
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(_actor(console), product_code="crm")
    again = ask(console)
    assert again["status"] == "pending" and again["id"] != row["id"]


def test_approval_is_refused_when_the_product_left_the_store(console):
    from workeasy360.commercial.catalog.models import Product

    row = ask(console)
    Product.objects.filter(code="crm").update(affiliate_enabled=False)
    with pytest.raises(PartnerValidationError):
        promotion_requests.approve(console["staff"], row["id"])
    assert not promotion.may_promote(_partner_id(console), "crm")


def test_only_staff_holding_partner_manage_decide(
    console, staff_factory, staff_login, login
):
    row = ask(console)
    _u, viewer = staff_factory("platform.staff_partner.view")
    assert promotion_requests.staff_queue(viewer)["items"][0]["id"] == row["id"]
    for decide in (
        lambda: promotion_requests.approve(viewer, row["id"]),
        lambda: promotion_requests.reject(viewer, row["id"], reason="Not for you"),
    ):
        with pytest.raises(PermissionDenied):
            decide()

    # The affiliate cannot reach the staff API at all.
    affiliate = sign_in(login, console["affiliate"])
    for method, path in (
        ("get", "/api/v1/admin/partner-product-requests"),
        ("post", f"/api/v1/admin/partner-product-requests/{row['id']}/approve"),
    ):
        response = getattr(affiliate, method)(path, content_type="application/json")
        assert response.status_code in (401, 403, 404), response.content
    assert not promotion.may_promote(_partner_id(console), "crm")

    staff, _ = staff_login("platform.staff_partner.manage", "platform.staff_partner.view")
    queue = staff.get("/api/v1/admin/partner-product-requests?status=pending")
    assert queue.status_code == 200 and queue.json()["counts"]["pending"] == 1
    detail = staff.get(f"/api/v1/admin/partner-product-requests/{row['id']}").json()
    assert detail["product"]["eligible"] is True and len(detail["history"]) == 1
    done = staff.post(
        f"/api/v1/admin/partner-product-requests/{row['id']}/approve",
        {},
        content_type="application/json",
    )
    assert done.status_code == 200, done.content
    again = staff.post(
        f"/api/v1/admin/partner-product-requests/{row['id']}/approve",
        {},
        content_type="application/json",
    )
    assert again.status_code == 409
    nonsense = staff.post(
        f"/api/v1/admin/partner-product-requests/{row['id']}/grant-everything",
        {},
        content_type="application/json",
    )
    assert nonsense.status_code == 404


# --- 4. revocation ------------------------------------------------------------------------------


def test_revocation_stops_new_promotion_and_keeps_what_was_earned(console):
    from workeasy360.commercial.commissions import selectors

    row = ask(console)
    promotion_requests.approve(console["staff"], row["id"])
    link = referrals.create_referral_code(_actor(console), product_code="crm")
    refer(console, link.code)
    buy(console)
    accrual.accrue_for_organization(console["id"])
    before = selectors.partner_money(_partner_id(console), timezone.now())
    assert len(ledger(console)) == 1

    revoked = promotion_requests.revoke(
        console["staff"], row["id"], reason="Campaign ended"
    )
    assert revoked["status"] == "revoked" and revoked["revoke_reason"] == "Campaign ended"
    assert _states(console)["crm"] == "revoked"
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(_actor(console), product_code="crm")
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(
            _actor(console), link_kind="pricing", product_code="crm"
        )
    # The default policy: existing links take no new visitors.
    assert referrals.capture_referral(link.code) is None
    # Earned commission is untouched, and accruing again changes nothing.
    accrual.accrue_for_organization(console["id"])
    assert len(ledger(console)) == 1
    assert selectors.partner_money(_partner_id(console), timezone.now()) == before
    # A general link still works: revocation is product-specific.
    assert referrals.create_referral_code(_actor(console)).link_kind == "general"

    with pytest.raises(PartnerRequestConflict):
        promotion_requests.revoke(console["staff"], row["id"])
    # The partner may ask again, as a new request.
    assert ask(console)["status"] == "pending"


def test_revoking_on_the_partner_page_marks_the_request_too(console):
    row = ask(console)
    approved = promotion_requests.approve(console["staff"], row["id"])
    promotion.revoke_promotion(
        console["staff"], _partner_id(console), grant_id=approved["grant_id"]
    )
    detail = promotion_requests.staff_detail(console["staff"], row["id"])
    assert detail["request"]["status"] == "revoked"
    assert [g["status"] for g in detail["grants"]] == ["revoked"]


def test_a_hand_made_grant_shows_as_approved_and_its_revocation_as_revoked(console):
    grant = promotion.grant_promotion(
        console["staff"], _partner_id(console), product_code="books"
    )
    assert _states(console)["books"] == "approved"
    with pytest.raises(PartnerRequestConflict):
        ask(console, "books")
    promotion.revoke_promotion(console["staff"], _partner_id(console), grant_id=grant.id)
    assert _states(console)["books"] == "revoked"


# --- 5. partners stay apart -----------------------------------------------------------------------


def test_partners_cannot_see_or_touch_each_others_requests(
    console, partner_factory, login
):
    other = partner_factory(*("referral.create", "referral.view", "commission.view"))
    mine = ask(console)
    view = partner_workspaces.store_console(other["actor"]())
    assert view["requests"] == []
    assert {p["status"] for p in view["products"]} == {"not_requested"}

    client = sign_in(login, other)
    theirs = client.get(f"/api/v1/partner/{_partner_id(console)}/affiliate/store")
    assert theirs.status_code in (403, 404)
    forged = client.post(
        f"/api/v1/partner/{_partner_id(console)}/affiliate/product-requests",
        {**REQUEST, "product_code": "books"},
        content_type="application/json",
    )
    assert forged.status_code in (403, 404)
    own = client.post(
        f"/api/v1/partner/{other['partner'].id}/affiliate/product-requests",
        {**REQUEST, "product_code": "crm"},
        content_type="application/json",
    )
    assert own.status_code == 201, own.content
    # Approving theirs grants nothing to mine.
    promotion_requests.approve(console["staff"], own.json()["id"])
    assert not promotion.may_promote(_partner_id(console), "crm")
    assert (
        promotion_requests.staff_detail(console["staff"], mine["id"])["request"]["status"]
        == "pending"
    )
    with pytest.raises(PartnerRecordNotFound):
        promotion_requests.staff_detail(console["staff"], other["partner"].id)


# --- 6. races --------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_administrators_approving_at_once_make_one_grant(
    shop, partner_factory, staff_factory
):
    """Two real transactions on two connections: the partner-row lock puts
    the second approval behind the first, which then finds it decided."""
    import threading

    from django.db import connection

    from tests.affiliate_products.conftest import AFFILIATE
    from tests.ecosystem.conftest import list_products
    from workeasy360.commercial.partners.models import PartnerPromotionGrant

    list_products("crm")
    affiliate = partner_factory(*AFFILIATE)
    _u, staff_a = staff_factory("platform.staff_partner.manage")
    _u, staff_b = staff_factory("platform.staff_partner.manage")
    row = promotion_requests.submit(
        affiliate["actor"](), body={**REQUEST, "product_code": "crm"}
    )
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def decide(ctx):
        try:
            barrier.wait(timeout=10)
            promotion_requests.approve(ctx, row["id"])
            outcomes.append("approved")
        except PartnerRequestConflict:
            outcomes.append("conflict")
        finally:
            connection.close()

    threads = [threading.Thread(target=decide, args=(c,)) for c in (staff_a, staff_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(outcomes) == ["approved", "conflict"]
    assert (
        PartnerPromotionGrant.objects.filter(
            partner_id=affiliate["partner"].id, status="active"
        ).count()
        == 1
    )


# --- 7. the whole journey, through the browser's API ---------------------------------------------


def test_the_journey_publish_request_approve_promote_earn(console, staff_login, login):
    client = sign_in(login, console["affiliate"])
    base = f"/api/v1/partner/{_partner_id(console)}/affiliate"
    explore = client.get(f"{base}/store").json()
    assert explore["can_request"] is True
    assert explore["prefill"] == {"website": ""}
    assert {p["code"]: p["status"] for p in explore["products"]} == {
        "crm": "not_requested",
        "books": "not_requested",
    }
    asked = client.post(
        f"{base}/product-requests",
        {**REQUEST, "product_code": "crm"},
        content_type="application/json",
    )
    assert asked.status_code == 201, asked.content
    twice = client.post(
        f"{base}/product-requests",
        {**REQUEST, "product_code": "crm"},
        content_type="application/json",
    )
    assert twice.status_code == 409

    staff, _ = staff_login("platform.staff_partner.manage", "platform.staff_partner.view")
    approved = staff.post(
        f"/api/v1/admin/partner-product-requests/{asked.json()['id']}/approve",
        {},
        content_type="application/json",
    )
    assert approved.status_code == 200, approved.content

    after = client.get(f"{base}/store").json()
    assert after["counts"] == {"approved": 1, "explore": 1, "pending": 0}
    made = client.post(
        f"/api/v1/partner/{_partner_id(console)}/referrals",
        {"product_code": "crm", "link_kind": "product"},
        content_type="application/json",
    )
    assert made.status_code == 201, made.content
    refer(console, made.json()["code"])
    buy(console)
    accrual.accrue_for_organization(console["id"])
    accrual.accrue_for_organization(console["id"])  # a retried task
    [row] = ledger(console)
    assert (row.rate_bp, row.product_code) == (1000, "crm")
    assert row.partner_id == _partner_id(console)

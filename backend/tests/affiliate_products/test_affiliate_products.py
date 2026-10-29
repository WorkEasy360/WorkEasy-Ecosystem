"""Product-aware affiliate links (ADR-054), end to end through the real
referral, order, payment-webhook, accrual and payout code.

The flows are the brief's: a granted product gets many links; an ungranted one
gets none; an update gets its own link; a referred customer who pays earns
exactly one commission, through checkout and a replayed webhook; a refund
reverses it; a rule change leaves history alone; a revocation erases nothing;
partners stay apart.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.affiliate_products.conftest import buy, ledger, refer, sign_in
from workeasy360.commercial.commissions import accrual, referrals
from workeasy360.contracts.errors import PartnerValidationError

pytestmark = pytest.mark.django_db


def _actor(fx):
    return fx["affiliate"]["actor"]()


def _publish_update(
    fx, *, kind="release", slug="ai-lead-scoring", title="AI Lead Scoring"
):
    from workeasy360.commercial.catalog import updates
    from workeasy360.commercial.catalog.models import Product

    product = Product.objects.get(code="crm")
    row = updates.create_update(
        fx["staff"],
        product_id=product.id,
        body={
            "kind": kind,
            "slug": slug,
            "title": title,
            "summary": "Rank every lead by how likely it is to buy.",
            "body": "Easy CRM now scores your leads.\nTry it today.",
        },
    )
    return updates.publish_update(fx["staff"], update_id=row.id)


# --- 1. a granted product gets many links ---------------------------------------------------


def test_a_granted_product_gets_many_links_each_its_own_tracking_id(programme):
    from workeasy360.commercial.controlplane import partner_workspaces

    campaign = referrals.create_campaign(_actor(programme), name="CRM October")
    page = referrals.create_referral_code(
        _actor(programme), product_code="crm", plan_code="crm_professional"
    )
    pricing = referrals.create_referral_code(
        _actor(programme), link_kind="pricing", product_code="crm"
    )
    custom = referrals.create_referral_code(
        _actor(programme),
        link_kind="product",
        product_code="crm",
        campaign_id=str(campaign.id),
        code="CRMOCT",
    )
    assert len({page.code, pricing.code, custom.code}) == 3
    assert (page.link_kind, pricing.link_kind, custom.link_kind) == (
        "product",
        "pricing",
        "product",
    )
    assert referrals.destination(page) == "/products/crm?plan=crm_professional"
    assert referrals.destination(pricing) == "/products/crm?view=pricing"
    assert custom.campaign_id == campaign.id and custom.created_by_id is not None

    mine = partner_workspaces.my_products(_actor(programme))
    assert [p["code"] for p in mine["products"]] == ["crm"]
    assert {link["code"] for link in mine["products"][0]["links"]} == {
        page.code,
        pricing.code,
        custom.code,
    }
    assert mine["policy"]["attribution_rule"] == "first_touch"


# --- 2. an ungranted product gets none -------------------------------------------------------


def test_an_ungranted_product_can_be_neither_linked_nor_seen(programme, login):
    from workeasy360.commercial.controlplane import partner_workspaces

    with pytest.raises(PartnerValidationError) as refused:
        referrals.create_referral_code(_actor(programme), product_code="books")
    assert refused.value.context["field"] == "product_code"
    for kind in ("pricing", "update"):
        with pytest.raises(PartnerValidationError):
            referrals.create_referral_code(
                _actor(programme), link_kind=kind, product_code="books"
            )
    # The screen is not the gate: the API refuses too.
    client = sign_in(login, programme["affiliate"])
    partner_id = programme["affiliate"]["partner"].id
    posted = client.post(
        f"/api/v1/partner/{partner_id}/referrals",
        {"product_code": "books", "link_kind": "product"},
        content_type="application/json",
    )
    assert posted.status_code == 400, posted.content
    assert "books" not in [
        p["code"] for p in partner_workspaces.my_products(_actor(programme))["products"]
    ]
    # Nor can the affiliate grant itself anything.
    granted = client.post(
        f"/api/v1/admin/partners/{partner_id}/promotions",
        {"product_code": "books"},
        content_type="application/json",
    )
    assert granted.status_code in (401, 403, 404)


def test_only_staff_holding_partner_manage_assign_promotions(
    programme, staff_factory, staff_login
):
    from workeasy360.commercial.partners import promotion
    from workeasy360.contracts.errors import PermissionDenied

    _u, viewer = staff_factory("platform.staff_partner.view")
    partner_id = programme["affiliate"]["partner"].id
    with pytest.raises(PermissionDenied):
        promotion.grant_promotion(viewer, partner_id, product_code="books")
    assert [g["product_code"] for g in promotion.list_grants(viewer, partner_id)] == [
        "crm"
    ]

    staff, _ = staff_login("platform.staff_partner.manage")
    response = staff.post(
        f"/api/v1/admin/partners/{partner_id}/promotions",
        {"product_code": "books"},
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    assert promotion.may_promote(partner_id, "books")


def test_a_promotion_needs_the_referral_capability_and_an_affiliate_product(
    programme, partner_factory
):
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.commercial.partners import promotion

    seller = partner_factory("customer.manage")
    with pytest.raises(PartnerValidationError):
        promotion.grant_promotion(
            programme["staff"], seller["partner"].id, product_code="crm"
        )
    Product.objects.filter(code="books").update(affiliate_enabled=False)
    with pytest.raises(PartnerValidationError):
        promotion.grant_promotion(
            programme["staff"], programme["affiliate"]["partner"].id, product_code="books"
        )


# --- 3. an update gets its own link -------------------------------------------------------------


def test_a_product_update_gets_its_own_trackable_link(programme, client):
    from workeasy360.commercial.catalog import updates
    from workeasy360.commercial.controlplane import partner_workspaces

    update = _publish_update(programme)
    link = referrals.create_referral_code(
        _actor(programme),
        link_kind="update",
        product_code="crm",
        update_id=str(update.id),
        label="AI Lead Scoring launch",
    )
    assert link.update_id == update.id
    assert referrals.destination(link) == "/products/crm/updates/ai-lead-scoring"
    page = client.get("/api/v1/public/products/crm/updates/ai-lead-scoring")
    assert page.status_code == 200 and page.json()["title"] == "AI Lead Scoring"

    captured = client.post(
        "/api/v1/public/referrals", {"code": link.code}, content_type="application/json"
    )
    assert captured.json()["destination"] == "/products/crm/updates/ai-lead-scoring"
    mine = partner_workspaces.my_products(_actor(programme))
    per_update = mine["products"][0]["updates"]
    assert [(u["title"], u["links"], u["clicks"]) for u in per_update] == [
        ("AI Lead Scoring", 1, 1)
    ]

    # A feature link needs a feature; a release is not one.
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(
            _actor(programme),
            link_kind="feature",
            product_code="crm",
            update_id=str(update.id),
        )

    # Archiving changes where the link lands, never whose click it is.
    updates.archive_update(programme["staff"], update_id=update.id)
    link.refresh_from_db()
    assert referrals.destination(link) == "/products/crm"
    assert referrals.capture_referral(link.code) is not None
    assert (
        client.get("/api/v1/public/products/crm/updates/ai-lead-scoring").status_code
        == 404
    )
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(
            _actor(programme),
            link_kind="update",
            product_code="crm",
            update_id=str(update.id),
        )


def test_a_draft_update_cannot_be_linked_and_its_landing_page_is_approved(programme):
    from workeasy360.commercial.catalog import updates
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.contracts.errors import CatalogValidationError

    product = Product.objects.get(code="crm")
    draft = updates.create_update(
        programme["staff"],
        product_id=product.id,
        body={"kind": "feature", "slug": "pipelines", "title": "Pipelines"},
    )
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(
            _actor(programme),
            link_kind="feature",
            product_code="crm",
            update_id=str(draft.id),
        )
    for bad in (
        "https://evil.example.com/crm",
        "https://workeasy360.com.evil.example/crm",
        "https://evilworkeasy360.com/crm",
        "http://workeasy360.com/crm",
        "javascript:alert(1)",
    ):
        with pytest.raises(CatalogValidationError):
            updates.edit_update(
                programme["staff"], update_id=draft.id, body={"landing_url": bad}
            )
    ok = updates.edit_update(
        programme["staff"],
        update_id=draft.id,
        body={"landing_url": "https://www.workeasy360.com/crm/pipelines"},
    )
    assert ok.landing_url == "https://www.workeasy360.com/crm/pipelines"


# --- 4-6. one paid sale, one commission, through checkout and a replayed webhook -------


def test_a_referred_customer_who_pays_earns_exactly_one_commission(programme):
    link = referrals.create_referral_code(_actor(programme), product_code="crm")
    refer(programme, link.code)
    paid = buy(programme)
    accrual.accrue_for_organization(programme["id"])
    accrual.accrue_for_organization(programme["id"])
    accrual.sweep()

    rows = ledger(programme)
    assert len(rows) == 1
    row = rows[0]
    assert row.partner_id == programme["affiliate"]["partner"].id
    assert (row.kind, row.attribution_kind, row.product_code) == (
        "accrual",
        "referral",
        "crm",
    )
    assert (row.basis_minor, row.rate_bp, row.amount_minor) == (4999, 1000, 500)
    assert row.payment_ref.startswith("payments.invoice_line:")
    assert paid["order"]["status"] == "fulfilled"


def test_the_referral_survives_checkout_and_a_replayed_payment_webhook(programme):
    link = referrals.create_referral_code(_actor(programme), product_code="crm")
    refer(programme, link.code)
    paid = buy(programme)
    # The provider delivers the same success again, twice.
    for _ in range(2):
        again = programme["client"].post(
            "/api/v1/public/payments/test-checkout/complete",
            {
                "reference": paid["reference"],
                "organization_id": str(programme["id"]),
                "outcome": "succeeded",
            },
            content_type="application/json",
        )
        assert again.status_code in (200, 409), again.content
    accrual.accrue_for_organization(programme["id"])
    assert len(ledger(programme, kind="accrual")) == 1


def test_an_unpaid_referred_sale_earns_nothing_until_it_is_paid(programme):
    from workeasy360.commercial.commissions.models import Referral

    link = referrals.create_referral_code(_actor(programme), product_code="crm")
    refer(programme, link.code)
    referral = Referral.objects.get(organization_id=programme["id"])
    fact = type(
        "Fact",
        (),
        {
            "organization_id": programme["id"],
            "product_code": "crm",
            "added_at": timezone.now(),
            "subscription_activated_at": timezone.now(),
        },
    )()
    evaluation = accrual.evaluate_sale(
        fact, rules=[], now=timezone.now() + timedelta(minutes=1), payment=None
    )
    assert (evaluation.accrues, evaluation.reason) == (False, "awaiting_payment")
    assert referral.status == "converted"


def test_a_product_link_earns_only_on_the_product_it_promoted(programme):
    link = referrals.create_referral_code(_actor(programme), product_code="crm")
    refer(programme, link.code)
    buy(programme, "books_professional")
    stats = accrual.accrue_for_organization(programme["id"])
    assert ledger(programme) == []
    assert stats.skipped.get("not_the_promoted_product") == 1


def test_first_touch_a_later_click_never_steals_the_referral(programme, partner_factory):
    from workeasy360.commercial.commissions.models import Referral
    from workeasy360.commercial.partners import promotion

    rival = partner_factory(
        "referral.create", "referral.view", "commission.earn", "commission.view"
    )
    promotion.grant_promotion(programme["staff"], rival["partner"].id, product_code="crm")
    first = referrals.create_referral_code(_actor(programme), product_code="crm")
    second = referrals.create_referral_code(rival["actor"](), product_code="crm")
    client = programme["client"]
    a = client.post(
        "/api/v1/public/referrals", {"code": first.code}, content_type="application/json"
    ).json()["token"]
    b = client.post(
        "/api/v1/public/referrals", {"code": second.code}, content_type="application/json"
    ).json()["token"]
    assert a == b  # the browser keeps the first referral
    converted = client.post(
        "/api/v1/referrals/convert",
        {"organization_id": str(programme["id"])},
        content_type="application/json",
    ).json()
    assert converted == {"converted": True}
    assert Referral.objects.get(organization_id=programme["id"]).partner_id == (
        programme["affiliate"]["partner"].id
    )
    # The rival's click still counts as a click on its own link.
    assert Referral.objects.filter(code=second).count() == 1


# --- 7. refunds reverse ------------------------------------------------------------------------------


def _refund(fx, staff_login, attempt_id, amount, key):
    from workeasy360.commercial.payments.refunds import send_refund

    staff, _ = staff_login("platform.staff_payment.manage")
    asked = staff.post(
        f"/api/v1/admin/customers/{fx['id']}/payments/{attempt_id}/refund",
        {"amount_minor": amount, "reason": "Customer asked for a refund"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert asked.status_code == 202, asked.content
    assert send_refund(fx["id"], uuid.UUID(asked.json()["refund_id"])) == "applied"


def test_a_refund_reverses_its_share_of_the_commission(programme, staff_login):
    from workeasy360.commercial.commissions import selectors

    link = referrals.create_referral_code(_actor(programme), product_code="crm")
    refer(programme, link.code)
    paid = buy(programme)
    accrual.accrue_for_organization(programme["id"])
    [earned] = ledger(programme, kind="accrual")

    _refund(programme, staff_login, paid["attempt_id"], 2500, "half")
    accrual.accrue_for_organization(programme["id"])
    accrual.accrue_for_organization(programme["id"])  # idempotent
    reversals = ledger(programme, kind="reversal")
    assert [(r.amount_minor, r.reason_code, r.reverses_id) for r in reversals] == [
        (-250, "refund", earned.id)
    ]

    _refund(programme, staff_login, paid["attempt_id"], 2499, "rest")
    accrual.accrue_for_organization(programme["id"])
    assert sum(r.amount_minor for r in ledger(programme)) == 0
    status = selectors.entry_statuses([earned])[earned.id]
    assert status == {"status": "reversed", "reversed_minor": 500}


# --- 8. a rule change keeps history ------------------------------------------------------------


def test_changing_the_rule_leaves_earned_commission_as_it_was(programme):
    from workeasy360.commercial.commissions import rules

    link = referrals.create_referral_code(_actor(programme), product_code="crm")
    refer(programme, link.code)
    buy(programme)
    accrual.accrue_for_organization(programme["id"])
    rules.create_rule_version(
        programme["staff"], code="default", rate_bp=2000, reason="Programme raised to 20%"
    )
    accrual.accrue_for_organization(programme["id"])
    [row] = ledger(programme)
    assert (row.rate_bp, row.rule_version, row.amount_minor) == (1000, 1, 500)
    assert accrual.reconcile(organization_ids=[programme["id"]]).is_clean


# --- 9. revocation erases nothing ---------------------------------------------------------------


def test_revoking_a_promotion_stops_new_links_and_keeps_what_was_earned(programme):
    from workeasy360.commercial.commissions import policy, selectors
    from workeasy360.commercial.partners import promotion

    partner_id = programme["affiliate"]["partner"].id
    link = referrals.create_referral_code(_actor(programme), product_code="crm")
    refer(programme, link.code)
    buy(programme)
    accrual.accrue_for_organization(programme["id"])
    before = selectors.partner_money(partner_id, timezone.now())

    promotion.revoke_promotion(
        programme["staff"], partner_id, grant_id=programme["grant"].id
    )
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(_actor(programme), product_code="crm")
    # The default policy: the old link takes no new visitors...
    assert referrals.capture_referral(link.code) is None
    # ...unless Super Admin says existing links continue.
    policy.update(programme["staff"], attribution_days=30, revoked_links="continue")
    assert referrals.capture_referral(link.code) is not None
    # A switched-off link cannot be switched back on without the grant.
    referrals.update_referral_code(_actor(programme), link.id, active=False)
    with pytest.raises(PartnerValidationError):
        referrals.update_referral_code(_actor(programme), link.id, active=True)

    accrual.accrue_for_organization(programme["id"])
    assert len(ledger(programme)) == 1
    assert selectors.partner_money(partner_id, timezone.now()) == before
    from workeasy360.commercial.controlplane import partner_workspaces

    withdrawn = partner_workspaces.my_products(_actor(programme))["withdrawn"]
    assert [w["product_code"] for w in withdrawn] == ["crm"]


def test_the_attribution_window_is_the_programme_policy(programme):
    from workeasy360.commercial.commissions import policy
    from workeasy360.commercial.commissions.models import Referral

    with pytest.raises(PartnerValidationError):
        policy.update(programme["staff"], attribution_days=0, revoked_links="stop")
    policy.update(programme["staff"], attribution_days=30, revoked_links="stop")
    link = referrals.create_referral_code(_actor(programme), product_code="crm")
    token = referrals.capture_referral(link.code)
    row = Referral.objects.get(token_hash=referrals._hash(token))
    assert timedelta(days=29) < row.expires_at - row.captured_at <= timedelta(days=30)


# --- 10. partners and tenants stay apart -----------------------------------------------------


def test_partners_cannot_touch_each_others_links_or_trails(
    programme, partner_factory, login
):
    from workeasy360.commercial.commissions.models import Referral
    from workeasy360.commercial.controlplane import partner_workspaces
    from workeasy360.commercial.partners import promotion
    from workeasy360.contracts.errors import PartnerRecordNotFound

    other = partner_factory(*("referral.create", "referral.view", "commission.view"))
    promotion.grant_promotion(programme["staff"], other["partner"].id, product_code="crm")
    mine = referrals.create_referral_code(_actor(programme), product_code="crm")
    refer(programme, mine.code)
    referral = Referral.objects.get(organization_id=programme["id"])

    with pytest.raises(PartnerRecordNotFound):
        referrals.update_referral_code(other["actor"](), mine.id, label="Stolen")
    with pytest.raises(PartnerRecordNotFound):
        partner_workspaces.referral_trail(other["actor"](), referral.id)
    assert partner_workspaces.my_products(other["actor"]())["products"][0]["links"] == []

    client = sign_in(login, other)
    response = client.get(
        f"/api/v1/partner/{programme['affiliate']['partner'].id}/affiliate/products"
    )
    assert response.status_code in (403, 404)


def test_the_trail_tells_the_story_without_naming_the_customer(programme):
    from workeasy360.commercial.commissions.models import Referral
    from workeasy360.commercial.controlplane import partner_workspaces

    link = referrals.create_referral_code(_actor(programme), product_code="crm")
    refer(programme, link.code)
    buy(programme)
    accrual.accrue_for_organization(programme["id"])
    referral = Referral.objects.get(organization_id=programme["id"])

    trail = partner_workspaces.referral_trail(_actor(programme), referral.id)
    assert trail["link"]["code"] == link.code and trail["link"]["link_kind"] == "product"
    steps = {s["step"]: s["at"] for s in trail["steps"]}
    assert steps["clicked"] and steps["customer"] and steps["purchased"]
    [sale] = trail["sales"]
    assert (sale["product_code"], sale["commission_minor"], sale["status"]) == (
        "crm",
        500,
        "pending",
    )
    assert sale["payment_verified"] is True
    text = str(trail)
    assert str(programme["id"]) not in text and programme["slug"] not in text


# --- 11. the existing flows still work ---------------------------------------------------------


def test_general_links_and_the_dashboard_work_as_before(programme):
    from workeasy360.commercial.controlplane import partner_workspaces

    general = referrals.create_referral_code(_actor(programme), label="Newsletter")
    assert (general.link_kind, referrals.destination(general)) == ("general", "/signup")
    refer(programme, general.code)
    buy(programme, "books_professional")
    accrual.accrue_for_organization(programme["id"])
    # A general link earns on any product open to affiliates, as before.
    assert [r.product_code for r in ledger(programme)] == ["books"]
    overview = partner_workspaces.affiliate_overview(_actor(programme))
    assert overview["kpis"]["clicks"] == 1 and overview["kpis"]["conversions"] == 1
    assert [p["code"] for p in overview["products"]] == ["crm"]
    renamed = referrals.update_referral_code(_actor(programme), general.id, label="Blog")
    assert renamed.label == "Blog"

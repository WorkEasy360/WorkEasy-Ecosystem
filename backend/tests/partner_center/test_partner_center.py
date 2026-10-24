"""The Partner Control Center's reads: one PartnerAccount architecture, many views.

Every screen of the Control Center is a filtered or aggregated read of the same
partner accounts. These tests pin what is new - the Total partners and Active
affiliates cards, the recent-activity panel, the ``affiliates`` list scope with
its referral and paid figures, the derived badges on a partner's page, and the
staff Referrals tab - and what must stay true around it: one account wearing
several labels is one row, money stays behind its permission, a partner cannot
read the control plane, and suspending a partner does not touch its customers.
"""

from __future__ import annotations

import uuid

from django.test import Client
from django.utils import timezone

from tests.partner_center.conftest import (
    AFFILIATE_CAPS,
    COMMISSION_VIEW,
    MANAGE,
    VIEW,
    count_queries,
    post,
)

BASE = "/api/v1/admin/partners"


def _cards(client):
    response = client.get(f"{BASE}/overview")
    assert response.status_code == 200, response.content
    return {card["key"]: card for card in response.json()["cards"]}


def _list(client, scope, **params):
    response = client.get(f"{BASE}/resellers", {"scope": scope, "limit": 100, **params})
    assert response.status_code == 200, response.content
    return response.json()["results"]


def _labels(row):
    return [label["key"] for label in row["experience"]]


def _pay_out(console, staff_factory):
    """Take the reseller's cleared commission through the whole payout
    lifecycle with the existing commands: prepare, ready, approve, paid."""
    from workeasy360.commercial.commissions import payouts

    _p, prep = staff_factory("platform.staff_payout.manage")
    _a, approver = staff_factory("platform.staff_payout.approve")
    payout = payouts.prepare_payout(
        prep,
        partner_id=console["reseller"]["partner"].id,
        period_end=timezone.now().date(),
        idempotency_key=f"center-{uuid.uuid4().hex}",
    )
    payouts.mark_payout_ready(prep, payout.id)
    payouts.approve_payout(approver, payout.id, reason="Reviewed and correct")
    return payouts.record_payout_paid(prep, payout.id, reference="BANK-REF-0042")


def _convert_one_referral(affiliate, organization_factory):
    from workeasy360.commercial.commissions import referrals

    code = referrals.create_referral_code(affiliate["actor"](), label="Spring")
    token = referrals.capture_referral(code.code)
    referrals.capture_referral(code.code)  # a second visitor who never signs up
    org, _membership, owner = organization_factory()
    assert referrals.convert_referral(
        user_id=owner.id, token=token, organization_id=org.id
    )
    return code


# --- the dashboard --------------------------------------------------------------------------


def test_the_dashboard_counts_every_partner_and_the_affiliates(
    console, viewer, affiliate
):
    cards = _cards(viewer)
    # The reseller, the white-label partner, the draft and the affiliate. The
    # label cards (ADR-048) count every partner holding the grants, in any
    # state - each card is the size of the All Partners list it opens.
    assert cards["total_partners"]["count"] == 4
    assert cards["active_partners"]["count"] == 3
    assert cards["affiliates"]["count"] == 1
    for key, label in (
        ("resellers", "reseller"),
        ("white_label", "white_label"),
        ("affiliates", "affiliate"),
    ):
        listed = admin_list_count(viewer, label)
        assert cards[key]["count"] == listed, (key, cards[key]["count"], listed)
    assert cards["suspended_partners"]["count"] == 0


def test_a_suspended_partner_still_counts_as_a_partner_but_not_as_active(
    console, viewer, affiliate, staff_factory
):
    from workeasy360.commercial.partners import services
    from workeasy360.contracts.enums import PartnerStatus

    _u, sctx = staff_factory(MANAGE)
    services.set_partner_status(
        sctx,
        affiliate["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Under review",
    )
    cards = _cards(viewer)
    assert cards["total_partners"]["count"] == 4
    assert cards["active_partners"]["count"] == 2
    assert cards["suspended_partners"]["count"] == 1
    # Still counted as an affiliate: the label follows the grants, not the status.
    assert cards["affiliates"]["count"] == 1
    row = next(r for r in _list(viewer, "all") if r["id"] == str(affiliate["partner"].id))
    assert row["status"] == "suspended"
    # Still an affiliate by its grants - the status is a separate fact.
    assert _labels(row) == ["affiliate"]


def test_recent_activity_reads_existing_data_for_a_staff_member_with_money_access(
    console, money_viewer, affiliate
):
    response = money_viewer.get(f"{BASE}/recent")
    assert response.status_code == 200, response.content
    body = response.json()

    newest = body["partners"][0]
    assert newest["id"] == str(affiliate["partner"].id)
    assert newest["experience"] == [{"key": "affiliate", "label": "Affiliate"}]
    assert {p["id"] for p in body["partners"]} >= {
        str(console["reseller"]["partner"].id),
        str(console["draft"].id),
    }

    [customer] = body["provisioning"]
    assert customer["organization_id"] == str(console["org"])
    assert customer["partner_id"] == str(console["reseller"]["partner"].id)
    assert customer["partner_name"] and customer["link_status"] == "active"

    # The one sale the console made earned commission: it is a sale and a
    # ledger entry, with the partner named and the staff derivation present.
    [sale] = body["sales"]
    assert sale["kind"] == "accrual" and sale["amount_minor"] == 500
    assert sale["partner_name"] == console["reseller"]["partner"].display_name
    assert body["commission"][0]["id"] == sale["id"]
    assert body["payouts"] == []

    [domain] = body["domains"]
    assert domain["hostname"] == "brand.console-example.com"
    assert domain["partner_id"] == str(console["white_label"]["partner"].id)
    # The proof a partner publishes in DNS is never part of a listing.
    assert "verification_token" not in domain and "token" not in str(domain).lower()


def test_recent_activity_hides_money_from_a_staff_member_without_the_permission(
    console, viewer
):
    body = viewer.get(f"{BASE}/recent").json()
    # Absent, not empty: an empty list would claim there were no sales.
    assert body["sales"] is None
    assert body["commission"] is None
    assert body["payouts"] is None
    assert body["partners"] and body["provisioning"] and body["domains"]


def test_recent_activity_lists_open_payouts(console, money_viewer, staff_factory):
    from workeasy360.commercial.commissions import payouts

    _p, prep = staff_factory("platform.staff_payout.manage")
    payout = payouts.prepare_payout(
        prep,
        partner_id=console["reseller"]["partner"].id,
        period_end=timezone.now().date(),
        idempotency_key="center-open-payout",
    )
    [row] = money_viewer.get(f"{BASE}/recent").json()["payouts"]
    assert row["id"] == str(payout.id) and row["status"] == "draft"
    assert row["partner_name"] == console["reseller"]["partner"].display_name


def test_recent_activity_needs_the_partner_permission(
    console, staff_client, http_login, user_factory, partner_client
):
    from tests.partner_center.conftest import PASSWORD

    only_money, _u = staff_client(COMMISSION_VIEW)
    assert only_money.get(f"{BASE}/recent").status_code == 403
    customer = http_login(user_factory(password=PASSWORD))
    assert customer.get(f"{BASE}/recent").status_code == 403
    partner, _fx = partner_client("commission.view", "referral.view")
    assert partner.get(f"{BASE}/recent").status_code == 403
    assert Client().get(f"{BASE}/recent").status_code == 401


def test_recent_activity_costs_the_same_however_many_partners_exist(
    console, money_viewer, partner_factory
):
    money_viewer.get(f"{BASE}/recent")  # warm the session
    before, _r = count_queries(lambda: money_viewer.get(f"{BASE}/recent"))
    for _ in range(6):
        partner_factory(*AFFILIATE_CAPS)
    after, response = count_queries(lambda: money_viewer.get(f"{BASE}/recent"))
    assert len(response.json()["partners"]) == 5  # bounded
    assert before == after, f"recent activity grows with the data: {before} -> {after}"


# --- the lists: one account, several labels ---------------------------------------------------


def test_the_affiliate_list_is_a_filter_over_grants(console, viewer, affiliate):
    rows = _list(viewer, "affiliates")
    assert [r["id"] for r in rows] == [str(affiliate["partner"].id)]
    assert _labels(rows[0]) == ["affiliate"]


def test_one_partner_with_three_labels_is_one_row_in_every_view(
    console, viewer, combined
):
    pid = str(combined["partner"].id)
    for scope in ("all", "resellers", "affiliates"):
        rows = _list(viewer, scope)
        assert [r["id"] for r in rows].count(pid) == 1, scope
        row = next(r for r in rows if r["id"] == pid)
        assert _labels(row) == ["reseller", "white_label", "affiliate"], scope
    white_label = viewer.get(f"{BASE}/white-label", {"limit": 100}).json()["results"]
    assert [r["id"] for r in white_label].count(pid) == 1

    from workeasy360.commercial.partners.models import PartnerAccount

    # Three views, one record.
    assert PartnerAccount.objects.filter(code="all-three").count() == 1


def test_all_partners_includes_a_partner_with_no_capabilities(console, viewer):
    rows = _list(viewer, "all")
    draft = next(r for r in rows if r["id"] == str(console["draft"].id))
    assert draft["experience"] == [] and draft["capability_count"] == 0
    assert str(console["draft"].id) not in {r["id"] for r in _list(viewer, "affiliates")}


def test_an_affiliate_row_shows_codes_conversions_and_paid_figures(
    console, viewer, affiliate, organization_factory
):
    code = _convert_one_referral(affiliate, organization_factory)
    [row] = _list(viewer, "affiliates")
    assert row["referrals"] == {
        "codes": [code.code],
        "active_codes": 1,
        "captured": 2,
        "converted": 1,
    }
    assert row["payouts"]["paid"] == []


def test_paid_payouts_are_reported_per_currency(console, viewer, staff_factory):
    _pay_out(console, staff_factory)
    row = next(
        r
        for r in _list(viewer, "resellers")
        if r["id"] == str(console["reseller"]["partner"].id)
    )
    assert row["payouts"]["paid"] == [
        {"currency": "GBP", "amount_minor": 500, "payouts": 1}
    ]
    assert row["payouts"]["open"] == 0
    # Paid commission is no longer unpaid.
    assert row["commission"][0]["unpaid_minor"] == 0


def test_a_partner_without_referrals_has_zero_figures_not_missing_ones(console, viewer):
    row = next(
        r
        for r in _list(viewer, "all")
        if r["id"] == str(console["reseller"]["partner"].id)
    )
    assert row["referrals"] == {
        "codes": [],
        "active_codes": 0,
        "captured": 0,
        "converted": 0,
    }


def test_an_unknown_scope_is_refused(console, viewer):
    response = viewer.get(f"{BASE}/resellers", {"scope": "partner_type"})
    assert response.status_code == 400


def test_the_affiliate_list_costs_the_same_at_any_page_size(
    console, viewer, partner_factory
):
    for _ in range(4):
        partner_factory(*AFFILIATE_CAPS)
    viewer.get(f"{BASE}/resellers?scope=affiliates&limit=1")

    def cost(limit):
        count, response = count_queries(
            lambda: viewer.get(f"{BASE}/resellers?scope=affiliates&limit={limit}")
        )
        return count, len(response.json()["results"])

    small, small_rows = cost(1)
    large, large_rows = cost(4)
    assert (small_rows, large_rows) == (1, 4)
    assert small == large, f"query count grows with the page: {small} vs {large}"


# --- one partner ------------------------------------------------------------------------------------


def test_the_partner_page_carries_badges_derived_from_grants(console, viewer, combined):
    body = viewer.get(f"{BASE}/{combined['partner'].id}").json()
    assert [e["key"] for e in body["experience"]] == [
        "reseller",
        "white_label",
        "affiliate",
    ]


def test_revoking_a_capability_removes_its_badge_at_once(
    console, viewer, combined, staff_client
):
    manager, _u = staff_client(MANAGE)
    pid = combined["partner"].id
    for capability in ("referral.create", "referral.view"):
        response = post(
            manager,
            f"{BASE}/{pid}/capabilities/revoke",
            {"capability": capability, "reason": "No longer an affiliate"},
        )
        assert response.status_code == 200, response.content
    body = viewer.get(f"{BASE}/{pid}").json()
    assert [e["key"] for e in body["experience"]] == ["reseller", "white_label"]
    assert str(pid) not in {r["id"] for r in _list(viewer, "affiliates")}


def test_the_referrals_tab_shows_codes_and_counts_and_no_prospect(
    console, viewer, affiliate, organization_factory
):
    code = _convert_one_referral(affiliate, organization_factory)
    response = viewer.get(f"{BASE}/{affiliate['partner'].id}/referrals")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["summary"] == {"captured": 1, "converted": 1, "expired": 0}
    assert [c["code"] for c in body["codes"]] == [code.code]
    assert len(body["results"]) == 2 and body["has_more"] is False
    for row in body["results"]:
        # Nothing that identifies who was referred.
        assert set(row) == {
            "id",
            "code",
            "label",
            "status",
            "captured_at",
            "converted_at",
        }


def test_the_referrals_tab_needs_staff_and_a_real_partner(
    console, viewer, affiliate, staff_client, partner_client
):
    assert viewer.get(f"{BASE}/{uuid.uuid4()}/referrals").status_code == 404
    unrelated, _u = staff_client("platform.staff_catalog.view")
    assert unrelated.get(f"{BASE}/{affiliate['partner'].id}/referrals").status_code == 403
    partner, _fx = partner_client("referral.create", "referral.view")
    assert partner.get(f"{BASE}/{affiliate['partner'].id}/referrals").status_code == 403
    assert Client().get(f"{BASE}/{affiliate['partner'].id}/referrals").status_code == 401


def test_a_partner_cannot_read_another_partners_referrals_or_the_console(
    console, affiliate, partner_client, organization_factory
):
    _convert_one_referral(affiliate, organization_factory)
    other, fx = partner_client("referral.create", "referral.view")
    # Its own: fine. Another partner's id: indistinguishable from a missing one.
    assert other.get(f"/api/v1/partner/{fx['partner'].id}/referrals").status_code == 200
    stranger = other.get(f"/api/v1/partner/{affiliate['partner'].id}/referrals")
    assert stranger.status_code == 404
    for path in (
        "overview",
        "recent",
        "resellers?scope=affiliates",
        f"{affiliate['partner'].id}",
    ):
        assert other.get(f"{BASE}/{path}").status_code == 403, path


def test_suspending_a_partner_leaves_its_customer_untouched(
    console, viewer, staff_client
):
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    def org_status():
        with unscoped_access(reason="Test reads the customer's status", module="tests"):
            return Organization.objects.get(id=console["org"]).status

    before = org_status()
    manager, _u = staff_client(MANAGE)
    partner = console["reseller"]["partner"]
    partner.refresh_from_db()
    response = post(
        manager,
        f"{BASE}/{partner.id}/status",
        {
            "status": "suspended",
            "reason": "Under review",
            "expected_version": partner.version,
        },
    )
    assert response.status_code == 200, response.content
    assert org_status() == before
    # The relationship and the subscription it carries are still there.
    [customer] = viewer.get(f"{BASE}/customers").json()["results"]
    assert customer["organization_id"] == str(console["org"])
    assert customer["subscription_status"] == "active"


# --- the Add Partner presets -----------------------------------------------------------------------


def test_each_purpose_preset_earns_exactly_its_label(viewer):
    from workeasy360.commercial.partners import capabilities

    body = viewer.get(f"{BASE}/capabilities").json()
    presets = {p["key"]: p for p in body["presets"]}
    assert set(presets) == {"standard_reseller", "white_label", "affiliate"}
    expected = {
        "standard_reseller": "reseller",
        "white_label": "white_label",
        "affiliate": "affiliate",
    }
    for key, label in expected.items():
        keys = set(presets[key]["capabilities"])
        assert keys <= set(capabilities.CAPABILITIES)
        assert [e["key"] for e in capabilities.experience_labels(keys)] == [label], key


def test_creating_an_affiliate_is_an_ordinary_partner_with_grants(staff_client):
    """What the Add Partner flow does for "Affiliate": create, then grant the
    preset's keys through the ordinary command. No purpose is stored."""
    from workeasy360.commercial.partners.models import PartnerAccount

    manager, _u = staff_client(MANAGE)
    created = post(
        manager,
        BASE,
        {
            "code": "new-affiliate",
            "display_name": "New Affiliate",
            "payout_currency": "GBP",
        },
    )
    assert created.status_code == 201, created.content
    pid = created.json()["id"]
    for capability in AFFILIATE_CAPS:
        granted = post(
            manager,
            f"{BASE}/{pid}/capabilities",
            {"capability": capability, "reason": "Set up through the Add Partner flow"},
        )
        assert granted.status_code in (200, 201), granted.content
    body = manager.get(f"{BASE}/{pid}").json()
    assert [e["key"] for e in body["experience"]] == ["affiliate"]
    assert set(body) >= {"capabilities", "experience"} and "type" not in body
    assert PartnerAccount.objects.filter(code="new-affiliate").count() == 1


def test_changing_capabilities_still_needs_a_fresh_second_factor(console, staff_client):
    """Dangerous Control Center actions keep the existing step-up rule."""
    manager, _u = staff_client(MANAGE, aal2=False)
    response = post(
        manager,
        f"{BASE}/{console['draft'].id}/capabilities",
        {"capability": "referral.view", "reason": "Make it an affiliate"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "step_up_required"
    # Viewing does not.
    assert manager.get(f"{BASE}/overview").status_code == 200


def test_the_staff_money_lists_still_need_the_commission_permission(console, viewer):
    assert viewer.get("/api/v1/admin/commission/accruals").status_code == 403
    assert viewer.get("/api/v1/admin/payouts").status_code == 403


def test_view_permission_unchanged_for_the_lists(staff_client):
    for permission in (COMMISSION_VIEW, "platform.staff_customer.view"):
        client, _u = staff_client(permission)
        assert client.get(f"{BASE}/resellers?scope=affiliates").status_code == 403
    holder, _u = staff_client(VIEW)
    assert holder.get(f"{BASE}/resellers?scope=affiliates").status_code == 200


def admin_list_count(client, label: str) -> int:
    response = client.get(
        f"{BASE}/resellers", {"scope": "all", "type": label, "limit": 100}
    )
    assert response.status_code == 200, response.content
    return len(response.json()["results"])

"""The Partner Control Center's client types match what the API really returns.

The same reader as Phase 10 (``tests/phase10/ts_contract.py``): every interface
in ``frontend/src/lib/api/partnerConsole.ts`` and ``controlPlane.ts`` is checked
field by field against a response from the real stack, so a renamed field fails
here instead of blanking a cell.
"""

from __future__ import annotations

from tests.phase10 import ts_contract as ts

BASE = "/api/v1/admin/partners"


def test_recent_activity_matches_its_type(console, money_viewer, staff_factory):
    from django.utils import timezone

    from workeasy360.commercial.commissions import payouts

    _p, prep = staff_factory("platform.staff_payout.manage")
    payouts.prepare_payout(
        prep,
        partner_id=console["reseller"]["partner"].id,
        period_end=timezone.now().date(),
        idempotency_key="shape-open-payout",
    )
    body = money_viewer.get(f"{BASE}/recent").json()
    ts.assert_matches(body, "PartnersRecent", "recent")
    for section, interface in (
        ("partners", "RecentPartner"),
        ("provisioning", "RecentProvisioning"),
        ("sales", "RecentLedgerEntry"),
        ("commission", "RecentLedgerEntry"),
        ("payouts", "RecentPayout"),
        ("domains", "RecentDomain"),
    ):
        assert body[section], f"the fixture must produce a {section} row"
        ts.assert_matches(body[section][0], interface, f"{section}[0]")


def test_the_affiliate_list_matches_its_type(console, viewer, affiliate):
    from workeasy360.commercial.commissions import referrals

    referrals.create_referral_code(affiliate["actor"]())
    body = viewer.get(f"{BASE}/resellers", {"scope": "affiliates"}).json()
    ts.assert_page(body, "ResellerRow", "affiliates")
    row = body["results"][0]
    ts.assert_matches(row["referrals"], "ResellerReferrals", "referrals")
    assert row["referrals"]["codes"], "the fixture must produce a code"


def test_paid_figures_match_their_type(console, viewer, staff_factory):
    from tests.partner_center.test_partner_center import _pay_out

    _pay_out(console, staff_factory)
    row = next(
        r
        for r in viewer.get(f"{BASE}/resellers").json()["results"]
        if r["id"] == str(console["reseller"]["partner"].id)
    )
    ts.assert_matches(row["payouts"], "ResellerPayouts", "payouts")
    ts.assert_matches(row["payouts"]["paid"][0], "ResellerPaid", "paid[0]")


def test_the_referrals_tab_matches_its_type(
    console, viewer, affiliate, organization_factory
):
    from tests.partner_center.test_partner_center import _convert_one_referral

    _convert_one_referral(affiliate, organization_factory)
    body = viewer.get(f"{BASE}/{affiliate['partner'].id}/referrals").json()
    ts.assert_matches(body, "PartnerReferrals", "referrals")
    ts.assert_matches(body["codes"][0], "ReferralCodeRow", "codes[0]")
    ts.assert_matches(body["results"][0], "ReferralRow", "results[0]")


def test_the_partner_detail_and_summary_match_their_types(console, viewer, combined):
    pid = combined["partner"].id
    detail = viewer.get(f"{BASE}/{pid}").json()
    ts.assert_matches(detail, "PartnerDetail", "detail")
    assert detail["experience"], "the fixture must wear labels"
    summary = viewer.get(f"{BASE}/{pid}/summary").json()
    ts.assert_matches(summary, "PartnerSummaryView", "summary")
    ts.assert_matches(summary["referrals"], "ResellerReferrals", "summary.referrals")
    assert "capabilities" in {s["key"] for s in summary["setup_progress"]}

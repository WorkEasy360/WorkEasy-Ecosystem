"""The new client types match what the API really returns (ADR-047, ADR-048).

Every payload the Add Partner screen and the three workspaces read is declared as
a TypeScript interface, one field per line; TypeScript trusts those declarations,
so a field renamed on the server would show up as a blank cell, not an error. This
checks them against responses produced by the real stack - the partner is created
through the real command and the administrator signs in with the typed password.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.partner_experience.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    CHECK,
    CREATE,
    make_body,
    send,
)
from tests.phase10 import ts_contract as ts

pytestmark = pytest.mark.django_db

_PANEL = ts.interfaces(ts.FRONTEND_API / "partnerPanel.ts")


@pytest.fixture(autouse=True)
def panel_interfaces(monkeypatch):
    """The panel's interfaces beside the console's. A name both files declare keeps
    the console's meaning; none of the interfaces checked here is such a name."""
    merged = dict(ts.INTERFACES)
    for name, spec in _PANEL.items():
        merged.setdefault(name, spec)
    monkeypatch.setattr(ts, "INTERFACES", merged)


def api(partner_id, path: str) -> str:
    return f"/api/v1/partner/{partner_id}/{path}"


def test_the_reader_sees_the_new_interfaces():
    for name in (
        "CredentialsCheck",
        "PasswordPolicy",
        "ProfileCompletion",
        "CompletionSection",
        "ListCompletion",
        "OverviewRecentPartner",
        "CreatedReferralCode",
        "CreatedPartner",
        "CreatedAdministrator",
        "CapabilityExperience",
        "ExperienceChange",
        "PortalPartner",
        "AffiliateOverview",
        "AffiliateLink",
        "AffiliateCampaign",
        "WhiteLabelStatus",
    ):
        assert name in ts.INTERFACES, name


def test_the_credentials_check_matches_its_type(catalog, admin):
    body = send(
        admin,
        CHECK,
        {
            "administrator": {
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "password_confirm": ADMIN_PASSWORD,
            }
        },
    ).json()
    ts.assert_matches(body, "CredentialsCheck", "check")
    ts.assert_matches(body["policy"], "PasswordPolicy", "policy")
    weak = send(
        admin,
        CHECK,
        {
            "administrator": {
                "email": ADMIN_EMAIL,
                "password": "abc",
                "password_confirm": "abd",
            }
        },
    ).json()
    ts.assert_matches(weak, "CredentialsCheck", "weak")
    ts.assert_matches(weak["problems"][0], "ReviewProblem", "problems[0]")


def test_the_created_partner_matches_its_type(catalog, admin):
    body = send(admin, CREATE, make_body("white_label", name="Shape Created")).json()
    ts.assert_matches(body, "CreatedPartner", "created")
    ts.assert_matches(body["administrator"], "CreatedAdministrator", "administrator")
    assert ADMIN_PASSWORD not in json.dumps(body)


def test_the_capability_catalog_carries_the_three_experiences(admin):
    body = admin.get("/api/v1/admin/partners/capabilities").json()
    ts.assert_matches(body, "CapabilityCatalog", "catalog")
    assert [e["key"] for e in body["experiences"]] == [
        "reseller",
        "white_label",
        "affiliate",
    ]
    for item in body["experiences"]:
        ts.assert_matches(item, "CapabilityExperience", item["key"])


def test_the_portal_answer_matches_its_type_for_each_experience(
    reseller, white_label, affiliate
):
    for client, _pid in (reseller, white_label, affiliate):
        me = client.get("/api/v1/partner/portal").json()["partners"][0]
        ts.assert_matches(me, "PortalPartner", "portal")
        assert me["workspaces"] and me["landing"] and me["default_experience"]
        ts.assert_matches(me["workspaces"][0], "ExperienceLabel", "workspaces[0]")


def test_the_admin_list_and_detail_match_their_types(reseller, admin):
    _client, pid = reseller
    page = admin.get("/api/v1/admin/partners/resellers", {"scope": "all"}).json()
    ts.assert_page(page, "ResellerRow", "list")
    row = next(r for r in page["results"] if r["id"] == str(pid))
    assert row["primary_experience"] == "reseller" and row["capabilities"]
    ts.assert_matches(row["administrator"], "ListAdministrator", "administrator")
    detail = admin.get(f"/api/v1/admin/partners/{pid}").json()
    ts.assert_matches(detail, "PartnerDetail", "detail")
    assert detail["landing"] == "reseller" and detail["workspaces"] == [
        {"key": "reseller", "label": "Reseller"}
    ]


def test_the_experience_change_matches_its_type(catalog, admin, affiliate):
    _client, pid = affiliate
    body = send(
        admin,
        f"/api/v1/admin/partners/{pid}/experience",
        {
            "action": "grant",
            "experience": "reseller",
            "products": ["crm"],
            "reason": "Became a reseller too",
        },
    )
    assert body.status_code == 200, body.content
    ts.assert_matches(body.json(), "ExperienceChange", "change")


def test_the_affiliate_overview_matches_its_type(affiliate):
    client, pid = affiliate
    empty = client.get(api(pid, "affiliate/overview")).json()
    ts.assert_matches(empty, "AffiliateOverview", "empty overview")
    created = client.post(
        api(pid, "referrals"),
        json.dumps({"label": "LinkedIn"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert created.status_code == 201, created.content
    data = client.get(api(pid, "affiliate/overview")).json()
    ts.assert_matches(data, "AffiliateOverview", "overview")
    ts.assert_matches(data["primary_link"], "AffiliateLink", "primary_link")
    ts.assert_matches(data["links"][0], "AffiliateLink", "links[0]")
    ts.assert_matches(data["campaigns"][0], "AffiliateCampaign", "campaigns[0]")


def test_the_white_label_status_and_the_reports_match_their_types(white_label, affiliate):
    client, pid = white_label
    ts.assert_matches(
        client.get(api(pid, "white-label/status")).json(), "WhiteLabelStatus", "status"
    )
    ts.assert_matches(client.get(api(pid, "reports")).json(), "PartnerReports", "reports")
    aclient, apid = affiliate
    ts.assert_matches(
        aclient.get(api(apid, "reports")).json(), "PartnerReports", "affiliate reports"
    )


# --- simplified partner management (ADR-048) ------------------------------------------------


def test_the_dashboard_matches_its_type(reseller, affiliate, admin):
    body = admin.get("/api/v1/admin/partners/overview").json()
    ts.assert_matches(body, "PartnersOverview", "overview")
    for card in body["cards"]:
        ts.assert_matches(card, "OverviewCard", card["key"])
    assert body["recent_partners"]
    for item in body["recent_partners"]:
        ts.assert_matches(item, "OverviewRecentPartner", item["code"])
        ts.assert_matches(
            item["profile_completion"], "ListCompletion", "profile_completion"
        )


def test_the_list_rows_and_the_summary_carry_the_completion_types(reseller, admin):
    _client, pid = reseller
    page = admin.get(
        "/api/v1/admin/partners/resellers",
        {"scope": "all", "type": "reseller", "profile": "complete"},
    ).json()
    ts.assert_page(page, "ResellerRow", "list")
    row = next(r for r in page["results"] if r["id"] == str(pid))
    ts.assert_matches(
        row["profile_completion"], "ListCompletion", "row.profile_completion"
    )
    summary = admin.get(f"/api/v1/admin/partners/{pid}/summary").json()
    ts.assert_matches(summary, "PartnerSummaryView", "summary")
    ts.assert_matches(
        summary["profile_completion"], "ProfileCompletion", "profile_completion"
    )
    for section in summary["profile_completion"]["sections"]:
        ts.assert_matches(section, "CompletionSection", section["key"])


def test_the_staff_referral_code_and_read_match_their_types(catalog, admin):
    created = send(admin, CREATE, make_body("affiliate", name="Shape Affiliate")).json()
    code = send(
        admin,
        f"/api/v1/admin/partners/{created['id']}/referral-codes",
        {"label": "Spring"},
    ).json()
    ts.assert_matches(code, "CreatedReferralCode", "code")
    read = admin.get(f"/api/v1/admin/partners/{created['id']}/referrals").json()
    ts.assert_matches(read, "PartnerReferrals", "referrals")
    ts.assert_matches(read["codes"][0], "ReferralCodeRow", "codes[0]")


def test_the_profile_carries_the_timezone_and_currency(catalog, admin):
    created = send(admin, CREATE, make_body("reseller", name="Shape Profile")).json()
    body = admin.get(f"/api/v1/admin/partners/{created['id']}/profile").json()
    ts.assert_matches(body, "PartnerProfile", "profile")
    ts.assert_matches(body["business"], "PartnerBusiness", "business")
    ts.assert_matches(body["payout"], "PartnerPayoutPrefs", "payout")

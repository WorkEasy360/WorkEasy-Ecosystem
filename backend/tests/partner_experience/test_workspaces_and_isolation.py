"""The three workspaces' reads, capability isolation, suspension and the admin list.

Every partner here is created through the real Add Partner command and signs in
through the real login endpoint with the credentials Super Admin typed - so each
test is about what that person can and cannot do, not about a fixture.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.partner_experience.conftest import (
    ADMIN_PASSWORD,
    make_body,
    send,
    sign_in,
)
from workeasy360.commercial.commissions import referrals
from workeasy360.commercial.partners import capabilities as caps
from workeasy360.commercial.partners.models import PartnerAccount

pytestmark = pytest.mark.django_db


def api(partner_id, path: str) -> str:
    return f"/api/v1/partner/{partner_id}/{path}"


def call(client, method: str, url: str, body=None):
    if method == "get":
        return client.get(url)
    # Commercial commands require a command id; sending one means the request is
    # judged on who is asking, not on a missing header.
    return client.post(
        url,
        json.dumps(body or {}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )


# --- capability isolation: the affiliate cannot reach the commercial side ------------------


AFFILIATE_FORBIDDEN = [
    ("get", "customers"),
    ("post", "customers"),
    ("get", "customers/export"),
    ("get", "catalog"),
    ("get", "plans"),
    ("get", "price-book"),
    ("get", "currencies"),
    ("get", "packages"),
    ("get", "orders"),
    ("get", "sub-partners"),
    ("post", "sub-partners"),
    ("get", "branding"),
    ("post", "domains"),
    ("get", "domains"),
    ("get", "reports/../customers"),
]


@pytest.mark.parametrize(("method", "path"), AFFILIATE_FORBIDDEN)
def test_an_affiliate_is_refused_every_customer_pricing_and_brand_endpoint(
    affiliate, method, path
):
    client, pid = affiliate
    response = call(
        client,
        method,
        api(pid, path),
        {"organization_name": "X", "owner_email": "x@example.com"},
    )
    assert response.status_code in (400, 403, 404), (method, path, response.status_code)
    assert response.status_code != 200 and response.status_code != 201


def test_an_affiliate_cannot_sell_or_change_a_subscription_even_for_a_real_customer(
    catalog, admin, affiliate, reseller, organization_factory
):
    from tests.phase7.support import provision

    client, pid = affiliate
    # A real customer of the *reseller*, named by id, is simply not found.
    _rclient, rid = reseller
    org, _membership, _owner = organization_factory()
    for path, body in (
        (
            f"customers/{org.id}/sell",
            {
                "plan_version_id": str(catalog["versions"]["crm_starter"].id),
                "quantity": 1,
            },
        ),
        (f"customers/{org.id}/billing", {"currency": "GBP"}),
        (f"customers/{org.id}/subscriptions/{uuid.uuid4()}/cancel", {}),
        (f"customers/{org.id}/subscriptions/{uuid.uuid4()}/activate", {}),
        (
            f"customers/{org.id}/items/{uuid.uuid4()}/change-plan",
            {"to_plan_version_id": str(uuid.uuid4()), "expected_version": 0},
        ),
        (f"customers/{org.id}/diagnose", {"product_code": "crm"}),
    ):
        response = call(client, "post", api(pid, path), body)
        assert response.status_code in (403, 404), (path, response.status_code)
    assert provision is not None and rid


def test_a_partner_cannot_use_the_staff_pricing_or_commission_endpoints(
    affiliate, reseller
):
    for client, pid in (affiliate, reseller):
        for path, body in (
            (
                f"/api/v1/admin/partners/{pid}/prices",
                {
                    "plan_version_id": str(uuid.uuid4()),
                    "interval": "monthly",
                    "amount_minor": 1,
                },
            ),
            (
                f"/api/v1/admin/partners/{pid}/commission",
                {"rate_bp": 9000, "reason": "Raise my own rate", "expected_version": 0},
            ),
            (
                f"/api/v1/admin/partners/{pid}/capabilities",
                {"capability": "customer.manage", "reason": "Give me more"},
            ),
            (
                f"/api/v1/admin/partners/{pid}/experience",
                {
                    "action": "grant",
                    "experience": "reseller",
                    "reason": "Make me a reseller",
                },
            ),
            ("/api/v1/admin/partners/create", make_body("reseller", name="Self Made")),
        ):
            response = call(client, "post", path, body)
            assert response.status_code in (401, 403, 404), (path, response.status_code)
    assert not PartnerAccount.objects.filter(code="self-made").exists()


def test_a_partner_cannot_name_another_partner_in_the_path(reseller, affiliate):
    _client, rid = reseller
    aclient, _aid = affiliate
    for path in (
        "dashboard",
        "customers",
        "referrals",
        "affiliate/overview",
        "reports",
        "members",
    ):
        assert aclient.get(api(rid, path)).status_code == 404, path


def test_the_reseller_is_refused_the_affiliate_overview_and_gets_no_referral_report(
    reseller,
):
    client, pid = reseller
    assert client.get(api(pid, "affiliate/overview")).status_code == 403
    report = client.get(api(pid, "reports")).json()
    assert report["referrals"] is None
    assert report["customers"] is not None and report["products"] is not None


def test_the_affiliate_report_has_no_customer_section(affiliate):
    client, pid = affiliate
    report = client.get(api(pid, "reports")).json()
    assert report["customers"] is None and report["products"] is None
    assert report["referrals"] is not None and report["sales"] is not None


# --- the affiliate dashboard ------------------------------------------------------------------


def test_the_affiliate_overview_counts_clicks_and_conversions_and_builds_the_link(
    affiliate, organization_factory
):
    client, pid = affiliate
    created = call(client, "post", api(pid, "referrals"), {"label": "LinkedIn"})
    assert created.status_code == 201, created.content
    code = created.json()["code"]
    # Two visitors follow the link; one becomes a customer.
    first = referrals.capture_referral(code)
    referrals.capture_referral(code)
    org, _m, owner = organization_factory()
    assert referrals.convert_referral(
        user_id=owner.id, token=first, organization_id=org.id
    )

    data = client.get(api(pid, "affiliate/overview")).json()
    assert data["kpis"]["clicks"] == 2
    assert data["kpis"]["conversions"] == 1
    assert data["kpis"]["conversion_rate_bp"] == 5000  # integer basis points: 50.00%
    # ADR-053: registrations are recorded now (none in this test's journey
    # verified an account through the link's cookie).
    assert isinstance(data["kpis"]["signups"], int)
    assert "affiliate_signups" not in data["not_implemented"]
    assert data["primary_link"]["code"] == code
    assert data["primary_link"]["url"].endswith(f"/r/{code}")
    assert data["primary_link"]["campaign"] == "LinkedIn"
    (campaign,) = data["campaigns"]
    assert {k: campaign[k] for k in ("campaign", "links", "clicks", "conversions")} == {
        "campaign": "LinkedIn",
        "links": 1,
        "clicks": 2,
        "conversions": 1,
    }
    assert campaign["conversion_rate_bp"] == 5000
    assert "General" in data["campaign_presets"]
    assert data["money"] is not None  # commission.view is in the bundle
    # Nothing identifies a visitor or the customer.
    text = json.dumps(data)
    assert org.slug not in text and owner.email not in text


def test_a_rate_with_no_clicks_is_none_not_zero(affiliate):
    client, pid = affiliate
    data = client.get(api(pid, "affiliate/overview")).json()
    assert data["kpis"]["clicks"] == 0 and data["kpis"]["conversion_rate_bp"] is None
    assert data["primary_link"] is None


def test_a_disabled_link_has_no_address_and_stops_taking_visitors(affiliate):
    client, pid = affiliate
    created = call(client, "post", api(pid, "referrals"), {"label": "Email"}).json()
    disabled = call(client, "post", api(pid, f"referral-codes/{created['id']}/disable"))
    assert disabled.status_code == 200
    link = client.get(api(pid, "affiliate/overview")).json()["links"][0]
    assert link["status"] == "revoked" and link["url"] is None
    assert referrals.capture_referral(created["code"]) is None


def test_the_range_is_validated_not_defaulted(affiliate):
    client, pid = affiliate
    assert (
        client.get(api(pid, "affiliate/overview"), {"range": "forever"}).status_code
        == 400
    )
    assert (
        client.get(
            api(pid, "reports"),
            {"range": "custom", "from": "2026-02-01", "to": "2026-01-01"},
        ).status_code
        == 400
    )


def test_rate_in_basis_points_rounds_half_up_with_integers():
    from workeasy360.commercial.controlplane.partner_workspaces import rate_bp

    assert rate_bp(1, 3) == 3333 and rate_bp(2, 3) == 6667 and rate_bp(0, 5) == 0
    assert rate_bp(1, 0) is None and rate_bp(5, 5) == 10000


# --- the white-label dashboard ----------------------------------------------------------------


def test_the_white_label_status_shows_brand_and_domain_progress(white_label):
    client, pid = white_label
    status = client.get(api(pid, "white-label/status")).json()
    assert status["brand"]["state"] == "not_started"
    assert status["domain"] == {"state": "none", "hostname": ""}


def test_the_white_label_status_is_not_given_to_a_partner_without_brand_grants(
    affiliate, reseller
):
    for client, pid in (affiliate, reseller):
        status = client.get(api(pid, "white-label/status")).json()
        assert status == {"brand": None, "domain": None, "storefront": None}


# --- money belongs to the commission view ---------------------------------------------------------


def test_a_role_that_may_see_referrals_but_not_commission_is_shown_no_amounts(
    catalog, partner_factory, organization_factory
):
    from workeasy360.commercial.controlplane import partner_network, partner_workspaces

    fx = partner_factory(caps.REFERRAL_CREATE, caps.REFERRAL_VIEW, code="ref-only")
    ctx = fx["actor"]()
    code = referrals.create_referral_code(ctx, label="LinkedIn")
    token = referrals.capture_referral(code.code)
    org, _membership, owner = organization_factory()
    assert referrals.convert_referral(
        user_id=owner.id, token=token, organization_id=org.id
    )
    overview = partner_workspaces.affiliate_overview(ctx)
    assert overview["money"] is None
    recent = overview["recent"]["conversions"]
    assert recent and all(row["commission_minor"] is None for row in recent)
    page = partner_network.conversions(ctx)
    assert page.items and all(
        row["commission_minor"] is None and row["currency"] == "" for row in page.items
    )


def test_a_malformed_administrator_email_is_reported_by_the_credentials_check(
    catalog, admin
):
    from tests.partner_experience.conftest import CHECK

    data = send(
        admin,
        CHECK,
        {
            "administrator": {
                "email": "not-an-email",
                "password": ADMIN_PASSWORD,
                "password_confirm": ADMIN_PASSWORD,
            }
        },
    ).json()
    assert any(p["field"] == "administrator.email" for p in data["problems"])


def test_the_referral_counts_carry_the_servers_total(affiliate):
    client, pid = affiliate
    created = call(client, "post", api(pid, "referrals"), {"label": "Email"}).json()
    referrals.capture_referral(created["code"])
    referrals.capture_referral(created["code"])
    data = client.get(api(pid, "referrals")).json()
    counts = data["counts"]
    assert data["total"] == counts["captured"] + counts["converted"] + counts["expired"]
    assert data["total"] == 2


# --- prices belong to the pricing view -----------------------------------------------------------


def test_products_are_listed_with_prices_only_to_a_partner_that_may_see_pricing(
    catalog, admin, reseller, affiliate
):
    rclient, rid = reseller
    sold = rclient.get(api(rid, "products")).json()
    prices = [
        i for p in sold["products"] for plan in p["plans"] for i in plan["intervals"]
    ]
    assert prices, "a reseller sees the prices of what it sells"
    # An affiliate holding a product grant (what a transition from reseller could
    # leave behind) is shown the plans, and no price.
    aclient, aid = affiliate
    granted = call(
        admin,
        "post",
        f"/api/v1/admin/partners/{aid}/product-grants",
        {"product_code": "crm"},
    )
    assert granted.status_code == 201, granted.content
    seen = aclient.get(api(aid, "products")).json()
    assert seen["products"], "the grant is listed"
    assert all(plan["intervals"] == [] for p in seen["products"] for plan in p["plans"])


# --- suspension -------------------------------------------------------------------------------


def suspend(admin, pid, target="suspended"):
    version = admin.get(f"/api/v1/admin/partners/{pid}").json()["version"]
    return send(
        admin,
        f"/api/v1/admin/partners/{pid}/status",
        {
            "status": target,
            "reason": "Suspended by the test",
            "expected_version": version,
        },
    )


def test_a_suspended_partner_keeps_history_and_loses_every_new_action(admin, reseller):
    client, pid = reseller
    assert (
        call(
            client,
            "post",
            api(pid, "customers"),
            {"organization_name": "Before", "owner_email": "c@example.com"},
        ).status_code
        == 201
    )
    assert suspend(admin, pid).status_code == 200

    me = client.get("/api/v1/partner/portal").json()["partners"][0]
    assert me["status"] == "suspended"
    # Reading survives; none of the manage permissions do.
    assert "platform.partner_customer.view" in me["permissions"]
    assert not [
        p for p in me["permissions"] if p.endswith((".provision", ".sell", ".manage"))
    ]
    assert client.get(api(pid, "customers")).status_code == 200
    after = call(
        client,
        "post",
        api(pid, "customers"),
        {"organization_name": "After", "owner_email": "d@example.com"},
    )
    assert after.status_code == 403
    # The customer the partner made earlier is untouched.
    assert (
        client.get(api(pid, "customers")).json()["results"][0]["organization_status"]
        == "active"
    )


def test_reactivation_gives_the_actions_back_and_keeps_the_landing(admin, reseller):
    client, pid = reseller
    suspend(admin, pid)
    assert suspend(admin, pid, "active").status_code == 200
    me = client.get("/api/v1/partner/portal").json()["partners"][0]
    assert me["status"] == "active" and me["landing"] == "reseller"
    assert "platform.partner_customer.provision" in me["permissions"]


def test_a_terminated_partner_sees_only_its_money(admin, affiliate):
    client, pid = affiliate
    assert suspend(admin, pid, "terminated").status_code == 200
    me = client.get("/api/v1/partner/portal").json()["partners"][0]
    assert set(me["permissions"]) <= {
        "platform.partner_commission.view",
        "platform.partner_payout.view",
    }


# --- the portal answer and the admin list -------------------------------------------------------


def test_the_portal_answers_with_the_workspaces_and_the_landing(reseller):
    client, _pid = reseller
    me = client.get("/api/v1/partner/portal").json()["partners"][0]
    assert me["default_experience"] == "reseller" and me["landing"] == "reseller"
    assert me["workspaces"] == [{"key": "reseller", "label": "Reseller"}]
    assert me["capabilities"] == sorted(me["capabilities"])


def test_the_admin_list_shows_the_administrator_the_experience_and_the_last_sign_in(
    admin, reseller, affiliate
):
    page = admin.get("/api/v1/admin/partners/resellers", {"scope": "all"}).json()
    rows = {r["code"]: r for r in page["results"]}
    row = rows["rs-one"]
    assert row["administrator"]["email"] == "owner@rs-one.example.com"
    assert row["administrator"]["role"] == "owner"
    # They signed in to get a session, so the platform knows when.
    assert row["administrator"]["last_login_at"]
    assert row["primary_experience"] == "reseller"
    assert rows["af-one"]["primary_experience"] == "affiliate"
    assert "customer.provision" in row["capabilities"]
    assert "customer.provision" not in rows["af-one"]["capabilities"]
    assert row["experience"] == [{"key": "reseller", "label": "Reseller"}]


def test_a_password_reset_link_is_the_only_reset_and_reveals_nothing(admin, reseller):
    from workeasy360.commercial.partners.models import PartnerMember

    client, pid = reseller
    member = PartnerMember.objects.get(partner_id=pid)
    response = send(
        admin, f"/api/v1/admin/partners/{pid}/members/{member.id}/password-reset", {}
    )
    assert response.status_code == 200 and response.json() == {"sent": True}
    assert ADMIN_PASSWORD not in response.content.decode()
    # The current password is untouched: they can still sign in with it.
    _client, login = sign_in("owner@rs-one.example.com", ADMIN_PASSWORD)
    assert login.status_code == 200
    # A member of another partner is not found.
    other = send(
        admin,
        f"/api/v1/admin/partners/{uuid.uuid4()}/members/{member.id}/password-reset",
        {},
    )
    assert other.status_code == 404


def test_the_reset_action_says_sent_only_when_a_link_can_have_been_sent(admin, reseller):
    from workeasy360.commercial.partners.models import PartnerMember
    from workeasy360.platform.identity.models import User

    _client, pid = reseller
    member = PartnerMember.objects.get(partner_id=pid)
    User.objects.filter(pk=member.user_id).update(status="disabled")
    response = send(
        admin, f"/api/v1/admin/partners/{pid}/members/{member.id}/password-reset", {}
    )
    assert response.status_code == 200 and response.json() == {"sent": False}

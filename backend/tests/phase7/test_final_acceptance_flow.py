"""The Phase 7 acceptance scenario, end to end, through the real HTTP stack.

One test, one story, numbered exactly as the brief's FINAL ACCEPTANCE FLOW so a
reviewer can follow it line by line. Every actor signs in through the real login
endpoint; every request is authorized by the same middleware and services a
browser would reach.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def post(client, url, body=None, *, key=False):
    extra = {"HTTP_IDEMPOTENCY_KEY": uuid.uuid4().hex} if key else {}
    return client.post(
        url, json.dumps(body or {}), content_type="application/json", **extra
    )


def put(client, url, body):
    return client.put(url, json.dumps(body), content_type="application/json")


def test_the_phase_7_acceptance_scenario(
    catalog,
    register_product,
    staff_client,
    user_factory,
    http_login,
    organization_factory,
):
    from tests.phase7.conftest import PASSWORD
    from workeasy360.commercial.partners.models import (
        PartnerBrand,
        PartnerOrganizationLink,
    )
    from workeasy360.platform.entitlements.selectors import decision_for_organization

    register_product("crm")

    # 1. The platform Super Admin creates Partner A.
    admin, _staff_user = staff_client(
        "platform.staff_partner.manage",
        "platform.staff_partner.view",
        "platform.staff_commission.manage",
        "platform.staff_customer.view",
        "platform.staff_support.view",
    )
    made = post(
        admin,
        "/api/v1/admin/partners",
        {"code": "partner-a", "display_name": "Partner A", "payout_currency": "GBP"},
    )
    assert made.status_code == 201, made.content
    a = made.json()
    a_user = user_factory(password=PASSWORD)
    assert (
        post(
            admin,
            f"/api/v1/admin/partners/{a['id']}/members",
            {"email": a_user.email, "role": "admin"},
        ).status_code
        == 201
    )

    # 2. Grants A customer.provision, subscription.sell, commission.earn
    #    (plus commission.view - earning is passive; seeing it is its own capability)
    #    and the product it may sell, then activates it. 3. NOT branding.manage.
    for capability in (
        "customer.provision",
        "subscription.sell",
        "commission.earn",
        "commission.view",
    ):
        granted = post(
            admin,
            f"/api/v1/admin/partners/{a['id']}/capabilities",
            {"capability": capability, "reason": "Programme terms agreed for Partner A"},
        )
        assert granted.status_code == 201, granted.content
    assert (
        post(
            admin,
            f"/api/v1/admin/partners/{a['id']}/product-grants",
            {"product_code": "crm"},
        ).status_code
        == 201
    )
    assert (
        post(
            admin,
            "/api/v1/admin/commission/rules",
            {
                "code": "default",
                "rate_bp": 1000,
                "reason": "Programme default rate of ten percent",
            },
        ).status_code
        == 201
    )
    activated = post(
        admin,
        f"/api/v1/admin/partners/{a['id']}/status",
        {
            "status": "active",
            "reason": "Onboarding complete",
            "expected_version": a["version"],
        },
    )
    assert activated.status_code == 200, activated.content

    # 4. Partner A's user logs in.  5. The portal offers provisioning and commissions...
    partner_a = http_login(a_user)
    portal = partner_a.get("/api/v1/partner/portal").json()["partners"]
    assert [p["partner_id"] for p in portal] == [a["id"]]
    caps = set(portal[0]["capabilities"])
    assert {"customer.provision", "subscription.sell", "commission.view"} <= caps
    base = f"/api/v1/partner/{a['id']}"
    assert partner_a.get(f"{base}/customers").status_code == 200
    assert partner_a.get(f"{base}/commissions").status_code == 200

    # 6. ...and white-label is absent and denied.
    assert "branding.manage" not in caps and "domain.manage" not in caps
    assert partner_a.get(f"{base}/branding").status_code == 403
    assert put(partner_a, f"{base}/branding", {"brand_name": "Sneaky"}).status_code == 403
    assert (
        post(
            partner_a, f"{base}/domains", {"hostname": "app.sneaky-example.com"}
        ).status_code
        == 403
    )

    # 7. Partner A provisions Customer X.  8. It gets its own organization and owner.
    owner_x = user_factory(password=PASSWORD)
    provisioned = post(
        partner_a,
        f"{base}/customers",
        {"organization_name": "Customer X", "owner_email": owner_x.email},
        key=True,
    )
    assert provisioned.status_code in (200, 201), provisioned.content
    org_x, slug_x = provisioned.json()["organization_id"], provisioned.json()["slug"]
    owner_client = http_login(owner_x)
    invites = owner_client.get("/api/v1/invitations").json()["results"]
    assert [i["organization_id"] for i in invites] == [org_x]
    assert post(owner_client, f"/api/v1/invitations/{org_x}/accept").status_code in (
        200,
        201,
    )
    orgs = owner_client.get("/api/v1/organizations").json()["organizations"]
    assert [o["organization_id"] for o in orgs] == [org_x] and orgs[0]["is_owner"] is True

    # 9. Partner A sells Product P / Plan Y.  10. Ordinary provisioning creates the entitlement.
    plans = partner_a.get(f"{base}/catalog").json()["results"]
    plan = next(p for p in plans if p["plan_code"] == "crm_professional")
    sold = post(
        partner_a,
        f"{base}/customers/{org_x}/sell",
        {"plan_version_id": plan["plan_version_id"], "quantity": 2},
        key=True,
    )
    assert sold.status_code in (200, 201), sold.content
    assert decision_for_organization(uuid.UUID(org_x), "crm").allowed

    # 11. Customer X sees Product P in its launcher, through the existing directory.
    apps = owner_client.get(f"/api/v1/orgs/{slug_x}/apps").json()["apps"]
    crm = next(app for app in apps if app["product_code"] == "crm")
    assert crm["state"] in ("active", "no_seat"), (
        crm
    )  # a per-seat product: the owner assigns a seat

    # 12. No product-domain data exists in Workeasy360.
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename ~ '^(crm|books|inventory|payroll|hr|projects|marketing)_'"
        )
        assert cur.fetchall() == []

    # 13. Partner A earns exactly one deterministic commission.
    from workeasy360.commercial.commissions import accrual
    from workeasy360.commercial.commissions.models import CommissionAccrual

    rows = list(CommissionAccrual.objects.filter(partner_id=a["id"]))
    assert len(rows) == 1 and rows[0].kind == "accrual" and rows[0].rule_version == 1
    # 10% of 4999 is 499.9; the one rounding rule (half-up) makes it 500, every time.
    assert (
        rows[0].rate_bp == 1000
        and rows[0].basis_minor == 4999
        and rows[0].amount_minor == 500
    )

    # 14. A duplicate delivery of the same sale cannot create a second commission.
    accrual.sweep()
    accrual.accrue_missing(organization_ids=[uuid.UUID(org_x)])
    assert CommissionAccrual.objects.filter(partner_id=a["id"]).count() == 1

    # 15. Partner A cannot see unrelated Customer Y.
    org_y, _m, owner_y = organization_factory()
    listed = partner_a.get(f"{base}/customers").json()["results"]
    assert {c["organization_id"] for c in listed} == {org_x}
    assert partner_a.get(f"{base}/customers/{org_y.id}/subscription").status_code == 404
    assert (
        post(
            partner_a,
            f"{base}/customers/{org_y.id}/sell",
            {"plan_version_id": plan["plan_version_id"], "quantity": 1},
            key=True,
        ).status_code
        == 404
    )

    # 16. Affiliate-only Partner B can create/view referrals and commissions, but cannot manage Customer X.
    b = post(
        admin,
        "/api/v1/admin/partners",
        {"code": "partner-b", "display_name": "Partner B", "payout_currency": "GBP"},
    ).json()
    b_user = user_factory(password=PASSWORD)
    assert (
        post(
            admin,
            f"/api/v1/admin/partners/{b['id']}/members",
            {"email": b_user.email, "role": "admin"},
        ).status_code
        == 201
    )
    for capability in (
        "referral.create",
        "referral.view",
        "commission.earn",
        "commission.view",
    ):
        assert (
            post(
                admin,
                f"/api/v1/admin/partners/{b['id']}/capabilities",
                {"capability": capability, "reason": "Affiliate programme terms agreed"},
            ).status_code
            == 201
        )
    assert (
        post(
            admin,
            f"/api/v1/admin/partners/{b['id']}/status",
            {
                "status": "active",
                "reason": "Onboarding complete",
                "expected_version": b["version"],
            },
        ).status_code
        == 200
    )
    partner_b = http_login(b_user)
    b_base = f"/api/v1/partner/{b['id']}"
    assert post(
        partner_b, f"{b_base}/referrals", {"label": "Newsletter"}
    ).status_code in (200, 201)
    assert partner_b.get(f"{b_base}/referrals").status_code == 200
    assert partner_b.get(f"{b_base}/commissions").status_code == 200
    assert partner_b.get(f"{b_base}/customers").status_code == 403
    assert partner_b.get(f"{b_base}/customers/{org_x}/subscription").status_code in (
        403,
        404,
    )
    assert post(
        partner_b,
        f"{b_base}/customers/{org_x}/sell",
        {"plan_version_id": plan["plan_version_id"], "quantity": 1},
        key=True,
    ).status_code in (403, 404)
    assert (
        post(
            partner_b,
            f"{b_base}/customers",
            {"organization_name": "No", "owner_email": "n@example.com"},
            key=True,
        ).status_code
        == 403
    )

    # 17. Partner A is granted branding.manage (and the domain capability to request a domain).
    for capability in ("branding.manage", "domain.manage"):
        assert (
            post(
                admin,
                f"/api/v1/admin/partners/{a['id']}/capabilities",
                {"capability": capability, "reason": "White-label add-on agreed"},
            ).status_code
            == 201
        )

    # 18. It can configure controlled branding metadata.
    logo = partner_a.post(f"{base}/branding/logo", PNG, content_type="image/png")
    assert logo.status_code == 201, logo.content
    saved = put(
        partner_a,
        f"{base}/branding",
        {
            "brand_name": "Partner A Cloud",
            "accent_color": "#1a56db",
            "theme": {},
            "logo_file_id": logo.json()["logo_file_id"],
        },
    )
    assert saved.status_code == 200, saved.content
    # Hostile content is refused or inert, never stored as markup or a stylesheet.
    hostile = put(
        partner_a,
        f"{base}/branding",
        {
            "brand_name": "X",
            "accent_color": "red;background:url(//evil)",
            "theme": {},
            "expected_version": saved.json()["version"],
        },
    )
    assert hostile.status_code == 400
    assert PartnerBrand.objects.get(partner_id=a["id"]).accent_color == "#1a56db"

    # 19. It requests a custom domain.  20. The domain stays inactive until verified.
    domain = post(partner_a, f"{base}/domains", {"hostname": "app.partner-a-example.com"})
    assert domain.status_code in (200, 201), domain.content
    assert domain.json()["status"] == "requested"
    anonymous = __import__("django.test", fromlist=["Client"]).Client()
    assert anonymous.get(
        "/api/v1/public/branding", {"host": "app.partner-a-example.com"}
    ).json() == {"brand": None}
    assert post(
        partner_a, f"{base}/domains/{domain.json()['id']}/activate"
    ).status_code in (400, 403, 409)

    # 21. The Partner A -> Customer X relationship ends.
    link = PartnerOrganizationLink.objects.get(partner_id=a["id"], organization_id=org_x)
    ended = post(
        admin,
        f"/api/v1/admin/partner-links/{link.id}/status",
        {"status": "ended", "reason": "Reseller agreement ended by mutual consent"},
    )
    assert ended.status_code == 200, ended.content

    # 22. Partner A immediately loses access to Customer X.
    assert partner_a.get(f"{base}/customers/{org_x}/subscription").status_code == 404
    assert (
        post(
            partner_a,
            f"{base}/customers/{org_x}/sell",
            {"plan_version_id": plan["plan_version_id"], "quantity": 1},
            key=True,
        ).status_code
        == 404
    )
    assert org_x not in {
        c["organization_id"]
        for c in partner_a.get(f"{base}/customers").json()["results"]
        if c["link_status"] == "active"
    }

    # 23. Customer X still signs in and uses what it has paid for.
    again = http_login(owner_x)
    assert again.get("/api/v1/organizations").status_code == 200
    assert decision_for_organization(uuid.UUID(org_x), "crm").allowed
    assert any(
        app["product_code"] == "crm"
        for app in again.get(f"/api/v1/orgs/{slug_x}/apps").json()["apps"]
    )

    # 24. The Super Admin can diagnose Customer X's access, and sees state - never secrets.
    diagnosed = post(
        admin,
        f"/api/v1/admin/customers/{org_x}/diagnose",
        {"product_code": "crm", "member_email": owner_x.email},
    )
    assert diagnosed.status_code == 200, diagnosed.content
    report = diagnosed.json()
    assert report["read_only"] is True and [s["step"] for s in report["chain"]]
    text = json.dumps(report).lower()
    for forbidden in ("secret", "password", "token", "key_id", "private"):
        assert forbidden not in text, forbidden

    # 25. The external product is untouched: this repository still contains no product code.
    import pathlib

    products = pathlib.Path(__file__).resolve().parents[2] / "workeasy360" / "products"
    assert [p.name for p in products.rglob("*.py") if p.name != "__init__.py"] == []

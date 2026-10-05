"""The Super Admin, partner and public HTTP surfaces, through the real
middleware stack, session cookies and CSRF."""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.django_db


def post(client, url, body=None, key=True, **extra):
    headers = {"HTTP_IDEMPOTENCY_KEY": uuid.uuid4().hex} if key else {}
    return client.post(
        url, json.dumps(body or {}), content_type="application/json", **headers, **extra
    )


# --- authentication and the staff boundary -----------------------------------


def test_admin_endpoints_need_a_session(client):
    for method, url in (
        ("get", "/api/v1/admin/dashboard"),
        ("get", "/api/v1/admin/customers"),
        ("post", "/api/v1/admin/partners"),
    ):
        response = getattr(client, method)(url)
        assert response.status_code == 401 and response.json()["code"] == "login_required"


def test_a_signed_in_customer_is_not_staff(http_login, user_factory):
    from tests.phase7.conftest import PASSWORD

    client = http_login(user_factory(password=PASSWORD))
    for url in (
        "/api/v1/admin/session",
        "/api/v1/admin/customers",
        "/api/v1/admin/dashboard",
    ):
        response = client.get(url)
        assert (
            response.status_code == 403 and response.json()["code"] == "permission_denied"
        )


def test_staff_without_the_permission_is_refused_per_endpoint(staff_client):
    client, _u = staff_client("platform.staff_customer.view")
    assert client.get("/api/v1/admin/customers").status_code == 200
    assert client.get("/api/v1/admin/partners").status_code == 403
    assert client.get("/api/v1/admin/audit").status_code == 403
    assert client.get("/api/v1/admin/payouts").status_code == 403
    assert client.get("/api/v1/admin/security/grants").status_code == 403
    assert (
        post(
            client,
            "/api/v1/admin/partners",
            {"code": "x-co", "display_name": "X", "payout_currency": "GBP"},
        ).status_code
        == 403
    )


def test_a_dangerous_action_needs_a_fresh_second_factor(staff_client):
    client, _u = staff_client("platform.staff_partner.manage", aal2=False)
    body = {"code": "aal-test", "display_name": "AAL", "payout_currency": "GBP"}
    response = post(client, "/api/v1/admin/partners", body)
    assert response.status_code == 401 and response.json()["code"] == "step_up_required"
    from workeasy360.commercial.partners.models import PartnerAccount

    assert PartnerAccount.objects.count() == 0  # nothing written


def test_the_session_view_lists_only_control_plane_permissions_held(staff_client):
    client, _u = staff_client(
        "platform.staff_customer.view", "platform.staff_partner.manage"
    )
    data = client.get("/api/v1/admin/session").json()
    assert data["is_staff"] and "platform.staff_partner.manage" in data["permissions"]
    assert "platform.staff_payout.approve" not in data["permissions"]


# --- CSRF ------------------------------------------------------------------


def test_cookie_authenticated_writes_require_csrf(staff_client, partner_client):
    client, _u = staff_client("platform.staff_partner.manage", enforce_csrf=True)
    body = json.dumps({"code": "csrf-co", "display_name": "C", "payout_currency": "GBP"})
    # No token: refused before any handler runs.
    assert (
        client.post(
            "/api/v1/admin/partners", body, content_type="application/json"
        ).status_code
        == 403
    )
    token = client.cookies["w360_csrf"].value
    ok = client.post(
        "/api/v1/admin/partners",
        body,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert ok.status_code == 201

    pclient, fx = partner_client("customer.provision", enforce_csrf=True)
    url = f"/api/v1/partner/{fx['partner'].id}/customers"
    payload = json.dumps({"organization_name": "X", "owner_email": "o@example.com"})
    assert pclient.post(url, payload, content_type="application/json").status_code == 403


def test_the_public_referral_endpoint_is_not_csrf_exempt():
    from workeasy360.interfaces.api.v1 import public_views

    assert not getattr(public_views.referral_capture_view, "csrf_exempt", False)


# --- staff flows over HTTP ---------------------------------------------------


def test_partner_administration_over_http_ignores_forged_fields(staff_client):
    client, staff_user = staff_client("platform.staff_partner.manage")
    response = post(
        client,
        "/api/v1/admin/partners",
        {
            "code": "forge-co",
            "display_name": "Forge",
            "payout_currency": "GBP",
            # None of these may be honoured:
            "status": "active",
            "created_by": str(uuid.uuid4()),
            "version": 99,
            "activated_at": "2020-01-01T00:00:00Z",
            "id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert (
        data["status"] == "draft"
        and data["version"] == 0
        and data["activated_at"] is None
    )
    from workeasy360.commercial.partners.models import PartnerAccount

    row = PartnerAccount.objects.get(code="forge-co")
    assert row.created_by_id == staff_user.id and str(row.id) == data["id"]


def test_capability_and_grant_bodies_cannot_smuggle_actors(staff_client, user_factory):
    client, staff_user = staff_client("platform.staff_partner.manage")
    partner = post(
        client,
        "/api/v1/admin/partners",
        {"code": "cap-co", "display_name": "C", "payout_currency": "GBP"},
    ).json()
    other = user_factory()
    response = post(
        client,
        f"/api/v1/admin/partners/{partner['id']}/capabilities",
        {
            "capability": "customer.provision",
            "reason": "Approved by the programme lead",
            "granted_by": str(other.id),
            "granted_at": "2001-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 201
    from workeasy360.commercial.partners.models import PartnerCapabilityGrant

    grant = PartnerCapabilityGrant.objects.get()
    assert grant.granted_by_id == staff_user.id
    assert grant.granted_at.year != 2001
    bad = post(
        client,
        f"/api/v1/admin/partners/{partner['id']}/capabilities",
        {"capability": "superuser", "reason": "Not an allowlisted capability"},
    )
    assert bad.status_code == 400 and bad.json()["code"] == "partner_validation_error"


def test_directory_is_paginated_and_constant_in_queries(
    staff_client, organization_factory
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client, _u = staff_client("platform.staff_customer.view")
    for _ in range(12):
        organization_factory()
    with CaptureQueriesContext(connection) as small:
        first = client.get("/api/v1/admin/customers?limit=3")
    for _ in range(30):
        organization_factory()
    with CaptureQueriesContext(connection) as large:
        second = client.get("/api/v1/admin/customers?limit=25")
    assert first.status_code == second.status_code == 200
    assert len(first.json()["results"]) == 3 and first.json()["has_more"]
    assert len(second.json()["results"]) == 25
    assert len(large) - len(small) <= 2  # not one query per customer
    assert client.get("/api/v1/admin/customers?limit=abc").status_code == 200
    assert (
        len(client.get("/api/v1/admin/customers?limit=100000").json()["results"]) <= 100
    )


def test_customer_detail_shows_commercial_facts_and_no_product_domain_data(
    staff_client, organization_factory, entitled
):
    client, _u = staff_client(
        "platform.staff_customer.view", "platform.staff_billing.view"
    )
    org, _m, _o = organization_factory()
    entitled(org.id, "crm", seats=3)
    data = client.get(f"/api/v1/admin/customers/{org.id}").json()
    assert set(data) == {
        "organization",
        "members",
        "subscriptions",
        "entitlements",
        "licenses",
        "entitlement_overrides",
        "billing",
        "partner",
        "audit",
    }
    assert data["entitlements"]["crm"]["status"] == "active"
    assert data["licenses"][0]["seats_total"] == 3
    assert data["billing"] is None  # no billing account exists; nothing is invented
    text = json.dumps(data).lower()
    for forbidden in ("contact", "invoice", "deal", "journal", "secret", "password"):
        assert forbidden not in text
    assert client.get(f"/api/v1/admin/customers/{uuid.uuid4()}").status_code == 404


def test_billing_state_is_labelled_manual_and_needs_its_own_permission(
    staff_client, customer, billing_account
):
    from workeasy360.contracts.values.money import CurrencyCode

    billing_account(customer["ctx"], CurrencyCode.GBP)
    plain, _u = staff_client("platform.staff_customer.view")
    assert (
        plain.get(f"/api/v1/admin/customers/{customer['org'].id}").json()["billing"]
        is None
    )
    full, _u2 = staff_client(
        "platform.staff_customer.view", "platform.staff_billing.view"
    )
    billing = full.get(f"/api/v1/admin/customers/{customer['org'].id}").json()["billing"]
    assert billing["provider"] == "none" and "No payment is collected" in billing["note"]
    assert set(billing) == {
        "currency",
        "collection_mode",
        "billing_region",
        "provider",
        "note",
    }


def test_organization_suspension_over_http_blocks_writes_and_is_audited_and_reversible(
    staff_client, organization_factory, http_login
):
    from tests.phase7.conftest import PASSWORD

    client, _u = staff_client(
        "platform.staff_customer.view", "platform.staff_customer.manage"
    )
    org, _m, owner = organization_factory()
    owner.set_password(PASSWORD)
    owner.save(update_fields=["password"])
    owner_client = http_login(owner)
    assert owner_client.get(f"/api/v1/orgs/{org.slug}/context").status_code == 200
    assert (
        post(owner_client, f"/api/v1/orgs/{org.slug}/context", key=False).status_code
        == 200
    )

    short = post(client, f"/api/v1/admin/customers/{org.id}/suspend", {"reason": "no"})
    assert short.status_code == 400
    ok = post(
        client,
        f"/api/v1/admin/customers/{org.id}/suspend",
        {"reason": "Non-payment after three reminders"},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "suspended"
    blocked = post(owner_client, f"/api/v1/orgs/{org.slug}/context", key=False)
    assert blocked.status_code == 402
    assert (
        owner_client.get(f"/api/v1/orgs/{org.slug}/context").status_code == 200
    )  # read-only, not a lockout

    detail = client.get(f"/api/v1/admin/customers/{org.id}").json()
    assert any(a["action"] == "organization.suspended_by_staff" for a in detail["audit"])
    assert (
        post(
            client,
            f"/api/v1/admin/customers/{org.id}/reactivate",
            {"reason": "Balance settled in full"},
        ).status_code
        == 200
    )
    assert (
        post(owner_client, f"/api/v1/orgs/{org.slug}/context", key=False).status_code
        == 200
    )


# --- support diagnostics ---------------------------------------------------------


def _diagnose(client, org, **body):
    return post(client, f"/api/v1/admin/customers/{org.id}/diagnose", body, key=False)


def test_diagnostics_give_a_reason_chain_and_change_nothing(
    staff_client, organization_factory, entitled
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client, _u = staff_client("platform.staff_support.view")
    org, _m, owner = organization_factory()
    entitled(org.id, "crm", seats=2)

    with CaptureQueriesContext(connection) as queries:
        response = _diagnose(client, org, product_code="crm", member_email=owner.email)
    assert response.status_code == 200
    data = response.json()
    assert data["read_only"] and data["can_launch"] is False
    steps = {s["step"]: s for s in data["chain"]}
    assert (
        steps["organization"]["ok"] and steps["user"]["ok"] and steps["membership"]["ok"]
    )
    assert steps["entitlement"]["ok"]
    assert steps["seat"]["code"] == "no_seat_assigned" and data["blocking_step"] in {
        "subscription",
        "seat",
    }
    writes = [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        # The framework's own sliding-window session touch
        # (SlidingSessionMiddleware), in the platform session registry
        # since the Phase 8 extension.
        and '"identity_session"' not in q["sql"]
    ]
    assert writes == []


def test_diagnostics_report_each_blocking_fact(
    staff_client, organization_factory, user_factory
):
    client, _u = staff_client("platform.staff_support.view")
    org, _m, _owner = organization_factory()
    unknown = _diagnose(
        client, org, product_code="crm", member_email="nobody@example.com"
    ).json()
    assert unknown["blocking_step"] in {"organization", "user"} and any(
        s["code"] == "user_not_found" for s in unknown["chain"]
    )
    outsider = user_factory()
    data = _diagnose(client, org, product_code="crm", member_email=outsider.email).json()
    assert any(s["code"] == "not_a_member" for s in data["chain"])
    assert (
        _diagnose(client, org, product_code="Not A Code!").json()["blocking_step"]
        == "product"
    )


def test_diagnostics_expose_state_never_secrets(
    staff_client, organization_factory, entitled
):
    client, _u = staff_client("platform.staff_support.view")
    org, _m, owner = organization_factory()
    entitled(org.id, "crm", seats=1)
    text = json.dumps(
        _diagnose(client, org, product_code="crm", member_email=owner.email).json()
    ).lower()
    for secret in (
        "password",
        "secret",
        "token",
        "signing",
        "smtp",
        "mfa",
        "private_key",
        "credential",
    ):
        assert secret not in text


def test_diagnostics_need_the_support_permission(staff_client, organization_factory):
    client, _u = staff_client("platform.staff_customer.view")
    org, _m, _o = organization_factory()
    assert _diagnose(client, org, product_code="crm").status_code == 403


# --- the partner portal ------------------------------------------------------------


def test_the_portal_navigation_is_derived_from_capabilities_not_a_type(partner_client):
    client, fx = partner_client("referral.create", "commission.view")
    data = client.get("/api/v1/partner/portal").json()
    assert data["partners"][0]["capabilities"] == ["commission.view", "referral.create"]
    assert "type" not in data["partners"][0]


def test_a_partner_id_the_caller_does_not_belong_to_is_a_404(
    partner_client, partner_factory
):
    client, _fx = partner_client("customer.provision")
    other = partner_factory("customer.provision")
    for url in (
        f"/api/v1/partner/{other['partner'].id}/customers",
        f"/api/v1/partner/{uuid.uuid4()}/customers",
    ):
        response = client.get(url)
        assert (
            response.status_code == 404
            and response.json()["code"] == "partner_record_not_found"
        )


def test_an_affiliate_over_http_cannot_reach_customer_endpoints(partner_client):
    client, fx = partner_client("referral.create", "commission.earn", "commission.view")
    base = f"/api/v1/partner/{fx['partner'].id}"
    assert client.get(f"{base}/customers").status_code == 403
    assert client.get(f"{base}/catalog").status_code == 403
    assert (
        post(
            client,
            f"{base}/customers",
            {"organization_name": "X", "owner_email": "a@b.co"},
        ).status_code
        == 403
    )
    assert client.get(f"{base}/branding").status_code == 403
    assert client.get(f"{base}/referrals").status_code == 200
    assert client.get(f"{base}/commissions").status_code == 200


def test_provisioning_over_http_needs_a_key_and_is_idempotent(
    partner_client, user_factory
):
    client, fx = partner_client("customer.provision")
    url = f"/api/v1/partner/{fx['partner'].id}/customers"
    owner = user_factory()
    body = {"organization_name": "Http Co", "owner_email": owner.email}
    assert post(client, url, body, key=False).status_code == 400
    headers = {"HTTP_IDEMPOTENCY_KEY": "http-key-1"}
    first = client.post(url, json.dumps(body), content_type="application/json", **headers)
    again = client.post(url, json.dumps(body), content_type="application/json", **headers)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["organization_id"] == again.json()["organization_id"]
    assert first.json()["owner"] == "invited"
    page = client.get(url).json()
    assert len(page["results"]) == 1 and owner.email not in json.dumps(page)


def test_a_refused_sale_over_http_leaves_no_partial_state(
    partner_client, catalog, user_factory
):
    from workeasy360.commercial.subscriptions.models import Subscription
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    client, fx = partner_client("customer.provision", "subscription.sell")
    base = f"/api/v1/partner/{fx['partner'].id}"
    made = post(
        client,
        f"{base}/customers",
        {"organization_name": "Refuse Co", "owner_email": user_factory().email},
    ).json()
    response = post(
        client,
        f"{base}/customers/{made['organization_id']}/sell",
        {
            "plan_version_id": str(catalog["versions"]["crm_professional"].id),
            "quantity": 1,
        },
    )
    assert response.status_code == 403  # no product grant
    with (
        unscoped_access(reason="Test counts subscriptions", module="tests"),
        organization_db_scope(made["organization_id"]),
    ):
        assert (
            Subscription.unscoped.filter(organization_id=made["organization_id"]).count()
            == 0
        )


def test_partner_a_cannot_read_partner_bs_customer_over_http(
    partner_client, partner_factory, user_factory
):
    from tests.phase7.support import provision

    client, fx = partner_client("customer.provision", "subscription.sell")
    other = partner_factory("customer.provision")
    other["user_factory"] = user_factory
    theirs, _o = provision(other)
    base = f"/api/v1/partner/{fx['partner'].id}"
    for suffix in ("subscription", "diagnose"):
        method = (
            client.get
            if suffix == "subscription"
            else (lambda u: post(client, u, {"product_code": "crm"}, key=False))
        )
        response = method(f"{base}/customers/{theirs.organization_id}/{suffix}")
        assert response.status_code in (403, 404)
    assert post(
        client, f"{base}/customers/{theirs.organization_id}/billing", {"currency": "GBP"}
    ).status_code in (403, 404)


def test_partner_commission_views_carry_no_derivation_or_customer_identity(
    partner_client, catalog, user_factory, staff_factory
):
    from tests.phase7.support import provision, sell
    from workeasy360.commercial.commissions import rules
    from workeasy360.commercial.partners import services

    client, fx = partner_client(
        "customer.provision",
        "subscription.sell",
        "commission.earn",
        "commission.view",
        "payout.view",
    )
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    _u, cctx = staff_factory("platform.staff_commission.manage")
    rules.create_rule_version(
        cctx, code="default", rate_bp=1000, reason="Programme default rate"
    )
    fx.update(catalog=catalog, user_factory=user_factory)
    made, _o = provision(fx)
    sell(fx, made.organization_id)
    data = client.get(f"/api/v1/partner/{fx['partner'].id}/commissions").json()
    assert data["balances"][0]["balance_minor"] == 500
    text = json.dumps(data)
    for leak in (
        "rate_bp",
        "rule_",
        "basis",
        "source_ref",
        "organization_id",
        "attribution",
    ):
        assert leak not in text


# --- public surface -----------------------------------------------------------------


def test_public_branding_falls_back_to_default_for_any_host(client):
    for host in ("", "unknown.example.com", "../../etc", "10.0.0.1", "a" * 300):
        response = client.get("/api/v1/public/branding", {"host": host})
        assert response.status_code == 200 and response.json() == {"brand": None}


def test_a_forged_host_header_does_not_select_a_brand(client, partner_factory):
    fx = partner_factory("branding.manage", "domain.manage")
    from workeasy360.commercial.partners import branding, domains

    branding.update_brand(fx["actor"](), brand_name="Genuine")
    dom = domains.request_domain(fx["actor"](), hostname="app.forge-example.com")

    class Proves:
        def txt_values(self, name):
            return [dom.verification_token]

    previous = domains.set_txt_resolver(Proves())
    try:
        domains.verify_domain(fx["actor"](), domain_id=dom.id)
        domains.activate_domain(fx["actor"](), domain_id=dom.id)
    finally:
        domains.set_txt_resolver(previous)
    # Only the explicit, canonicalised ?host= selects presentation. A Host or
    # X-Forwarded-Host header is not read, so it cannot switch brand.
    poisoned = client.get(
        "/api/v1/public/branding",
        HTTP_X_FORWARDED_HOST="app.forge-example.com",
        HTTP_ORIGIN="https://app.forge-example.com",
    )
    assert poisoned.json() == {"brand": None}
    genuine = client.get(
        "/api/v1/public/branding", {"host": "APP.forge-example.com"}
    ).json()
    assert genuine["brand"]["brand_name"] == "Genuine"
    assert set(genuine["brand"]) >= {"brand_name", "accent_color", "theme", "logo_url"}
    assert "partner" not in json.dumps(genuine).lower()


def test_referral_capture_is_rate_limited_and_uniform(client):
    from django.conf import settings

    limit = settings.CONTROL_PLANE_RATE_LIMITS["public_referral_ip"][0]
    codes = 0
    for _ in range(limit + 3):
        response = client.post(
            "/api/v1/public/referrals",
            json.dumps({"code": "ZZZZZZZZ"}),
            content_type="application/json",
        )
        if response.status_code == 429:
            codes += 1
        else:
            assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert codes >= 1


def test_only_the_invited_owner_can_accept_over_http(
    partner_client, user_factory, http_login
):
    from tests.phase7.conftest import PASSWORD

    client, fx = partner_client("customer.provision")
    owner = user_factory(password=PASSWORD)
    stranger = user_factory(password=PASSWORD)
    made = post(
        client,
        f"/api/v1/partner/{fx['partner'].id}/customers",
        {"organization_name": "Invite Co", "owner_email": owner.email},
    ).json()
    org_id, slug = made["organization_id"], made["slug"]

    owner_client = http_login(owner)
    # Until accepted the owner has no access to the organization at all ...
    assert owner_client.get(f"/api/v1/orgs/{slug}/context").status_code == 404
    pending = owner_client.get("/api/v1/invitations").json()["results"]
    assert [p["organization_id"] for p in pending] == [org_id]

    stranger_client = http_login(stranger)
    assert stranger_client.get("/api/v1/invitations").json()["results"] == []
    assert (
        post(
            stranger_client, f"/api/v1/invitations/{org_id}/accept", key=False
        ).status_code
        == 404
    )

    assert (
        post(owner_client, f"/api/v1/invitations/{org_id}/accept", key=False).status_code
        == 200
    )
    assert owner_client.get(f"/api/v1/orgs/{slug}/context").status_code == 200
    assert owner_client.get("/api/v1/invitations").json()["results"] == []

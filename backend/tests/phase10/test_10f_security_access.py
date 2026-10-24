"""Phase 10f security, part 1: who may reach what.

Cross-cutting checks over every endpoint this phase added - IDOR, partner
isolation, permission and capability bypass, AAL2, CSRF, stale authority and
termination. Cases 10d (branding, domains, logos) and 10e (invitation replay,
expiry, race, wrong user or organization) already pin in their own files are
not repeated here.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.phase10 import test_10d_white_label as _white_label
from tests.phase10.conftest import MANAGE, PASSWORD, VIEW
from workeasy360.commercial.partners import domains, services
from workeasy360.commercial.partners.models import PartnerDomain, PartnerOrganizationLink
from workeasy360.contracts.enums import PartnerStatus

pytestmark = pytest.mark.django_db

# The fake DNS resolver fixture is 10d's own, re-exported by assignment.
dns = _white_label.dns


def call(client, method, url, body=None):
    if method == "GET":
        return client.get(url, body or {})
    return getattr(client, method.lower())(
        url,
        json.dumps(body or {}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )


@pytest.fixture
def login(http_login):
    """A signed-in client for a partner fixture's administrator."""

    def make(fx, *, enforce_csrf=False):
        fx["user"].set_password(PASSWORD)
        fx["user"].save(update_fields=["password"])
        return http_login(fx["user"], enforce_csrf=enforce_csrf)

    return make


@pytest.fixture
def rivals(console, partner_factory, login):
    """Two partners that each hold everything, and a client for each."""
    caps = (
        "customer.provision",
        "customer.manage",
        "subscription.sell",
        "subscription.manage",
        "branding.manage",
        "domain.manage",
        "commission.view",
        "payout.view",
        "referral.create",
        "referral.view",
        "support.access",
        "pricing.view",
    )
    a = partner_factory(*caps, code="rival-a")
    b = partner_factory(*caps, code="rival-b")
    return {"a": a, "b": b, "a_http": login(a), "b_http": login(b)}


PARTNER_READS = (
    "customers",
    "catalog",
    "commissions",
    "payouts",
    "referrals",
    "branding",
    "domains",
)


# --- IDOR and isolation ---------------------------------------------------------------------


@pytest.mark.parametrize("suffix", PARTNER_READS)
def test_another_partners_path_is_indistinguishable_from_one_that_does_not_exist(
    rivals, suffix
):
    other = rivals["b"]["partner"]
    real = rivals["a_http"].get(f"/api/v1/partner/{other.id}/{suffix}")
    unknown = rivals["a_http"].get(f"/api/v1/partner/{uuid.uuid4()}/{suffix}")
    assert real.status_code == unknown.status_code == 404, (suffix, real.content)
    assert real.json()["code"] == unknown.json()["code"]


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        (
            "POST",
            "customers",
            {"organization_name": "Sneak Ltd", "owner_email": "x@y.example"},
        ),
        ("PUT", "branding", {"brand_name": "Sneak"}),
        ("POST", "domains", {"hostname": "sneak.example.org"}),
    ],
)
def test_writing_through_another_partners_path_fails_and_changes_nothing(
    rivals, method, suffix, body
):
    victim = rivals["b"]["partner"]
    before = (PartnerDomain.objects.count(), PartnerOrganizationLink.objects.count())
    response = call(
        rivals["a_http"], method, f"/api/v1/partner/{victim.id}/{suffix}", body
    )
    assert response.status_code == 404, response.content
    assert (
        PartnerDomain.objects.count(),
        PartnerOrganizationLink.objects.count(),
    ) == before
    assert not PartnerDomain.objects.filter(partner_id=victim.id).exists()


@pytest.mark.parametrize("action", ["verify", "activate", "disable"])
def test_a_partner_cannot_act_on_another_partners_domain_by_its_id(rivals, dns, action):
    victim = rivals["b"]
    domain_id = domains.request_domain(
        victim["actor"](), hostname="victim.rival-example.com"
    ).id
    before = PartnerDomain.objects.get(id=domain_id)
    dns.records["_w360-verify.victim.rival-example.com"] = [before.verification_token]
    a = rivals["a"]["partner"]
    response = rivals["a_http"].post(
        f"/api/v1/partner/{a.id}/domains/{domain_id}/{action}",
        "{}",
        content_type="application/json",
    )
    assert response.status_code == 404, response.content
    after = PartnerDomain.objects.get(id=domain_id)
    assert (after.status, after.version) == (before.status, before.version)


def test_a_partner_cannot_sell_to_or_diagnose_another_partners_customer(
    rivals, user_factory
):
    from tests.phase7.support import provision

    result, _owner = provision(
        {**rivals["b"], "user_factory": user_factory}, name="Victim Co"
    )
    org = result.organization_id
    a = rivals["a"]["partner"]
    base = f"/api/v1/partner/{a.id}/customers/{org}"
    for suffix, body in (
        ("sell", {"plan_version_id": str(uuid.uuid4()), "quantity": 1}),
        ("diagnose", None),
        ("billing", {"currency": "GBP"}),
    ):
        response = call(rivals["a_http"], "POST", f"{base}/{suffix}", body)
        assert response.status_code in (403, 404), (suffix, response.content)
    listing = rivals["a_http"].get(f"/api/v1/partner/{a.id}/customers").json()
    assert str(org) not in json.dumps(listing)


def test_a_partner_user_has_no_reach_into_the_staff_console(rivals):
    a = rivals["a"]["partner"]
    for url in (
        "/api/v1/admin/partners/overview",
        "/api/v1/admin/partners/resellers",
        "/api/v1/admin/partners/customers",
        f"/api/v1/admin/partners/{a.id}/summary",
        f"/api/v1/admin/partners/{a.id}/activity",
        "/api/v1/admin/partners/white-label",
        f"/api/v1/admin/partners/{a.id}/brand",
    ):
        assert rivals["a_http"].get(url).status_code == 403, url
    for url in (
        f"/api/v1/admin/partners/{a.id}/commission",
        f"/api/v1/admin/partners/{a.id}/prices",
        f"/api/v1/admin/partners/{a.id}/administrators",
        "/api/v1/admin/partners/reseller-review",
    ):
        assert call(rivals["a_http"], "POST", url, {}).status_code == 403, url


def test_an_ordinary_customer_user_has_no_reach_into_either_console(
    http_login, user_factory, rivals
):
    customer = http_login(user_factory(password=PASSWORD))
    a = rivals["a"]["partner"]
    assert customer.get("/api/v1/admin/partners/overview").status_code == 403
    assert customer.get(f"/api/v1/partner/{a.id}/customers").status_code == 404


def test_the_partner_portal_lists_only_the_callers_own_partners(rivals):
    ids = {
        p["partner_id"]
        for p in rivals["a_http"].get("/api/v1/partner/portal").json()["partners"]
    }
    assert ids == {str(rivals["a"]["partner"].id)}


def test_staff_ids_that_do_not_exist_are_a_404_and_reveal_nothing(staff_client):
    staff, _u = staff_client(
        VIEW, MANAGE, "platform.staff_audit.view", "platform.staff_commission.view"
    )
    ghost = uuid.uuid4()
    for url in (
        f"/api/v1/admin/partners/{ghost}/summary",
        f"/api/v1/admin/partners/{ghost}/customers",
        f"/api/v1/admin/partners/{ghost}/pricing",
        f"/api/v1/admin/partners/{ghost}/activity",
        f"/api/v1/admin/partners/{ghost}/brand",
        f"/api/v1/admin/partners/{ghost}/commission",
    ):
        assert staff.get(url).status_code == 404, url


# --- permission and capability bypass ----------------------------------------------------------


PRICE = {"plan_version_id": str(uuid.uuid4()), "interval": "monthly", "amount_minor": 100}
RATE = {"rate_bp": 1000, "reason": "Agreed programme terms", "expected_version": 0}

STAFF_ENDPOINTS = [
    # (method, url template, the permission that grants it, a body that parses)
    ("GET", "/api/v1/admin/partners/overview", VIEW, None),
    ("GET", "/api/v1/admin/partners/resellers", VIEW, None),
    ("GET", "/api/v1/admin/partners/white-label", VIEW, None),
    ("GET", "/api/v1/admin/partners/customers", VIEW, None),
    ("GET", "/api/v1/admin/partners/products", VIEW, None),
    ("GET", "/api/v1/admin/partners/{p}/summary", VIEW, None),
    ("GET", "/api/v1/admin/partners/{p}/customers", VIEW, None),
    ("GET", "/api/v1/admin/partners/{p}/pricing", VIEW, None),
    (
        "GET",
        "/api/v1/admin/partners/{p}/commission",
        "platform.staff_commission.view",
        None,
    ),
    ("GET", "/api/v1/admin/partners/{p}/activity", "platform.staff_audit.view", None),
    ("POST", "/api/v1/admin/partners/reseller-review", VIEW, {}),
    ("PUT", "/api/v1/admin/partners/{p}/brand", MANAGE, {"brand_name": "X"}),
    (
        "POST",
        "/api/v1/admin/partners/{p}/domains/request",
        MANAGE,
        {"hostname": "perm.example-brand.com"},
    ),
    (
        "POST",
        "/api/v1/admin/partners/{p}/administrators",
        MANAGE,
        {"email": "x@admin-example.com", "role": "admin"},
    ),
    ("POST", "/api/v1/admin/partners/{p}/prices", "platform.staff_pricing.manage", PRICE),
    (
        "POST",
        "/api/v1/admin/partners/{p}/commission",
        "platform.staff_commission.manage",
        RATE,
    ),
    (
        "POST",
        "/api/v1/admin/partners/{p}/customers/{o}/owner-invitation/resend",
        MANAGE,
        {},
    ),
    (
        "POST",
        "/api/v1/admin/partners/{p}/customers/{o}/owner-invitation/revoke",
        MANAGE,
        {},
    ),
]


@pytest.mark.parametrize(
    "method,template,permission,body",
    STAFF_ENDPOINTS,
    ids=[
        f"{m}-{t.split('/admin/partners/')[-1].replace('{p}/', '').replace('/', '_')}"
        for m, t, _p, _b in STAFF_ENDPOINTS
    ],
)
def test_every_staff_endpoint_needs_its_own_permission(
    console, staff_client, method, template, permission, body
):
    partner = console["reseller"]["partner"]
    url = template.format(p=partner.id, o=console["org"])
    unrelated, _ = staff_client("platform.staff_catalog.view")
    assert call(unrelated, method, url, body).status_code == 403, url
    nothing, _ = staff_client()
    assert call(nothing, method, url, body).status_code == 403, url
    # A control-plane grant is never inherited from a customer or partner role.
    holder, _ = staff_client(permission)
    assert call(holder, method, url, body).status_code != 403, url


def test_a_partner_without_a_capability_is_refused_by_the_server_whatever_the_ui_shows(
    partner_factory, login
):
    """The portal's menu hides what a partner lacks; the server is what refuses."""
    reader = partner_factory("pricing.view", code="reader-only")
    client = login(reader)
    base = f"/api/v1/partner/{reader['partner'].id}"
    assert (
        call(
            client,
            "POST",
            f"{base}/customers",
            {"organization_name": "X", "owner_email": "a@b.example"},
        ).status_code
        == 403
    )
    assert call(client, "PUT", f"{base}/branding", {"brand_name": "X"}).status_code == 403
    assert (
        call(client, "POST", f"{base}/domains", {"hostname": "x.example.org"}).status_code
        == 403
    )
    assert client.get(f"{base}/commissions").status_code == 403
    assert client.get(f"{base}/payouts").status_code == 403


def test_a_capability_revoked_mid_session_stops_working_at_once(channel, login):
    client = login(channel)
    base = f"/api/v1/partner/{channel['partner'].id}"
    body = {
        "organization_name": "Before Ltd",
        "owner_email": f"b-{uuid.uuid4().hex[:6]}@customer-example.com",
    }
    first = client.post(
        f"{base}/customers",
        json.dumps(body),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert first.status_code in (200, 201), first.content
    services.revoke_capability(
        channel["staff_ctx"],
        channel["partner"].id,
        capability="customer.provision",
        reason="Revoked while the partner is signed in",
    )
    body["organization_name"] = "After Ltd"
    second = client.post(
        f"{base}/customers",
        json.dumps(body),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert second.status_code == 403, second.content
    assert (
        "customer.provision"
        not in client.get("/api/v1/partner/portal").json()["partners"][0]["capabilities"]
    )


def test_an_expired_capability_grant_is_not_authority(channel, login):
    """Grant history is immutable (a database trigger refuses any edit), so
    expiry is tested the honest way: a grant that lapses."""
    import time
    from datetime import timedelta

    from django.utils import timezone

    services.revoke_capability(
        channel["staff_ctx"],
        channel["partner"].id,
        capability="customer.provision",
        reason="Replaced by a time-boxed grant",
    )
    services.grant_capability(
        channel["staff_ctx"],
        channel["partner"].id,
        capability="customer.provision",
        reason="Time-boxed for the expiry test",
        expires_at=timezone.now() + timedelta(seconds=2),
    )
    client = login(channel)
    url = f"/api/v1/partner/{channel['partner'].id}/customers"

    def provision(name):
        return client.post(
            url,
            json.dumps(
                {
                    "organization_name": name,
                    "owner_email": f"{uuid.uuid4().hex[:8]}@customer-example.com",
                }
            ),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        )

    assert provision("In Time Ltd").status_code in (200, 201)
    time.sleep(2.5)
    late = provision("Too Late Ltd")
    assert late.status_code == 403, late.content


def test_a_member_removed_mid_session_loses_the_partner_at_once(channel, login):
    client = login(channel)
    base = f"/api/v1/partner/{channel['partner'].id}"
    assert client.get(f"{base}/customers").status_code == 200
    from workeasy360.commercial.partners.models import PartnerMember

    member = PartnerMember.objects.get(
        partner_id=channel["partner"].id, user_id=channel["user"].id
    )
    services.remove_member(
        channel["staff_ctx"], channel["partner"].id, member_id=member.id
    )
    assert client.get(f"{base}/customers").status_code == 404
    assert client.get("/api/v1/partner/portal").json()["partners"] == []


def test_a_staff_grant_revoked_mid_session_stops_working_at_once(staff_client):
    from workeasy360.platform.authz import staff
    from workeasy360.platform.authz.models import StaffGrant

    client, user = staff_client(VIEW)
    assert client.get("/api/v1/admin/partners/overview").status_code == 200
    grant = StaffGrant.objects.get(user_id=user.id, permission=VIEW)
    staff.end_grant(
        grant_id=grant.id, revoked_by_id=user.id, reason="Revoked while signed in"
    )
    assert client.get("/api/v1/admin/partners/overview").status_code == 403


# --- step-up (AAL2) ---------------------------------------------------------------------------------


DANGEROUS_STAFF = [
    (
        "POST",
        "/api/v1/admin/partners/{p}/administrators",
        {"email": "new@admin-example.com", "role": "admin"},
        MANAGE,
    ),
    ("POST", "/api/v1/admin/partners/{p}/prices", PRICE, "platform.staff_pricing.manage"),
    (
        "POST",
        "/api/v1/admin/partners/{p}/commission",
        {"rate_bp": 1200, "reason": "Agreed terms for the test", "expected_version": 0},
        "platform.staff_commission.manage",
    ),
    (
        "POST",
        "/api/v1/admin/partners/{p}/domains/request",
        {"hostname": "aal.example-brand.com"},
        MANAGE,
    ),
    (
        "POST",
        "/api/v1/admin/partners/{p}/customers/{o}/owner-invitation/resend",
        {},
        MANAGE,
    ),
    (
        "POST",
        "/api/v1/admin/partners/{p}/customers/{o}/owner-invitation/revoke",
        {},
        MANAGE,
    ),
]


def test_the_brand_and_the_domain_request_are_separate_submits(rivals, login):
    """Branding is presentation; a domain is authority over a hostname. They
    are separate submits, and a domain is only a request until its DNS proof is
    seen."""
    a = rivals["a"]
    weak = login(a)
    base = f"/api/v1/partner/{a['partner'].id}"
    brand = weak.put(
        f"{base}/branding",
        json.dumps({"brand_name": "Plain Brand"}),
        content_type="application/json",
    )
    assert brand.status_code == 200, brand.content
    domain = call(weak, "POST", f"{base}/domains", {"hostname": "asym.example-brand.com"})
    assert domain.status_code == 201, domain.content
    row = PartnerDomain.objects.get(partner_id=a["partner"].id)
    assert row.status in ("requested", "verification_pending")


# --- CSRF -------------------------------------------------------------------------------------------


def _writes(partner, org, domain):
    p, o = partner.id, org
    return [
        ("POST", "/api/v1/admin/partners/reseller-review"),
        ("PUT", f"/api/v1/admin/partners/{p}/brand"),
        ("POST", f"/api/v1/admin/partners/{p}/domains/request"),
        ("POST", f"/api/v1/admin/partners/{p}/administrators"),
        ("POST", f"/api/v1/admin/partners/{p}/prices"),
        ("POST", f"/api/v1/admin/partners/{p}/commission"),
        ("POST", f"/api/v1/admin/partners/{p}/customers/{o}/owner-invitation/resend"),
        ("POST", f"/api/v1/admin/partners/{p}/customers/{o}/owner-invitation/revoke"),
        ("POST", f"/api/v1/admin/domains/{domain}/verify"),
        ("POST", f"/api/v1/admin/domains/{domain}/activate"),
    ]


def test_every_staff_write_refuses_a_request_without_the_csrf_token(
    console, staff_client
):
    wl = console["white_label"]["partner"]
    domain = PartnerDomain.objects.filter(partner_id=wl.id).first()
    client, _u = staff_client(
        MANAGE,
        VIEW,
        "platform.staff_pricing.manage",
        "platform.staff_commission.manage",
        enforce_csrf=True,
    )
    for method, url in _writes(wl, console["org"], domain.id):
        # A signed-in browser that did not send the double-submit header.
        response = getattr(client, method.lower())(
            url, "{}", content_type="application/json"
        )
        assert response.status_code == 403, (method, url, response.status_code)
    # The same call *with* the header gets past CSRF (and is then judged on its merits).
    token = client.cookies["w360_csrf"].value
    ok = client.post(
        "/api/v1/admin/partners/reseller-review",
        "{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert ok.status_code != 403 or ok.json().get("code") != "csrf_failed"


def test_every_partner_write_refuses_a_request_without_the_csrf_token(rivals, login):
    a = rivals["a"]
    client = login(a, enforce_csrf=True)
    base = f"/api/v1/partner/{a['partner'].id}"
    org = uuid.uuid4()
    for method, url in (
        ("POST", f"{base}/customers"),
        ("PUT", f"{base}/branding"),
        ("POST", f"{base}/domains"),
        ("POST", f"{base}/customers/{org}/owner-invitation/resend"),
        ("POST", f"{base}/customers/{org}/owner-invitation/revoke"),
        ("POST", f"{base}/customers/{org}/sell"),
    ):
        response = getattr(client, method.lower())(
            url, "{}", content_type="application/json"
        )
        assert response.status_code == 403, (method, url, response.status_code)


def test_a_bare_browser_cannot_use_the_public_owner_calls_without_the_token(journey_link):
    from django.test import Client

    for url in (
        "/api/v1/public/owner-invitations/preview",
        "/api/v1/public/owner-invitations/accept",
    ):
        response = Client(enforce_csrf_checks=True).post(
            url, json.dumps({"token": journey_link}), content_type="application/json"
        )
        assert response.status_code == 403


@pytest.fixture
def journey_link(channel):
    from tests.phase10.conftest import deliver_account_mail, token_from
    from workeasy360.commercial.partners import customers

    address = f"csrf-{uuid.uuid4().hex[:8]}@customer-example.com"
    customers.provision_customer(
        channel["actor"](),
        organization_name="Csrf Ltd",
        owner_email=address,
        idempotency_key=uuid.uuid4().hex,
    )
    return token_from(next(m for m in deliver_account_mail() if address in m.to))


# --- termination -----------------------------------------------------------------------------------------


REFUSED = ("partner_not_active", "permission_denied")


def test_a_terminated_partner_keeps_only_the_money_it_is_owed(channel, login):
    """partners/resolver.py: terminated = commission and payout visibility only,
    so what is owed stays readable after the relationship ends. Everything
    else - customers, catalog, brand, domains - is refused at once."""
    from tests.phase7.support import provision

    result, _o = provision(channel, name="Held Co")
    client = login(channel)
    base = f"/api/v1/partner/{channel['partner'].id}"
    services.set_partner_status(
        channel["staff_ctx"],
        channel["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Programme membership ended for the test",
    )
    assert client.get(f"{base}/commissions").status_code == 200
    assert client.get(f"{base}/payouts").status_code == 200
    for suffix in ("customers", "catalog", "branding", "domains", "referrals"):
        assert client.get(f"{base}/{suffix}").status_code == 403, suffix
    body = {"organization_name": "New Ltd", "owner_email": "n@customer-example.com"}
    refused = call(client, "POST", f"{base}/customers", body)
    assert refused.status_code == 403 and refused.json()["code"] in REFUSED
    assert (
        client.put(
            f"{base}/branding",
            json.dumps({"brand_name": "Z"}),
            content_type="application/json",
        ).status_code
        == 403
    )
    assert call(
        client,
        "POST",
        f"{base}/customers/{result.organization_id}/owner-invitation/resend",
    ).status_code in (403, 404)
    assert (
        not PartnerOrganizationLink.objects.filter(partner_id=channel["partner"].id)
        .exclude(status="ended")
        .exists()
    )
    # Its own sign-in and portal entry still work: it is told, not thrown out.
    portal = client.get("/api/v1/partner/portal").json()["partners"]
    assert portal[0]["status"] == "terminated"


def test_a_suspended_partner_cannot_transact_but_can_read(channel, login):
    client = login(channel)
    base = f"/api/v1/partner/{channel['partner'].id}"
    services.set_partner_status(
        channel["staff_ctx"],
        channel["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Suspended for the isolation test",
    )
    assert client.get(f"{base}/customers").status_code == 200
    body = {"organization_name": "Sus Ltd", "owner_email": "s@customer-example.com"}
    refused = call(client, "POST", f"{base}/customers", body)
    assert refused.status_code == 403 and refused.json()["code"] in REFUSED

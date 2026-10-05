"""The capability catalog endpoint and the same-origin partner logo."""

from __future__ import annotations

import pytest

from tests.phase7.test_branding_domains_referrals import PNG

pytestmark = pytest.mark.django_db

HOST = "app.logo-example.com"


class _Proves:
    def __init__(self, token):
        self.token = token

    def txt_values(self, name):
        return [self.token]


def _active_brand(fx, *, with_logo: bool):
    from workeasy360.commercial.partners import branding, domains

    logo = (
        branding.upload_logo(fx["actor"](), content=PNG, content_type="image/png")
        if with_logo
        else None
    )
    branding.update_brand(fx["actor"](), brand_name="Logo Co", logo_file_id=logo)
    dom = domains.request_domain(fx["actor"](), hostname=HOST)
    previous = domains.set_txt_resolver(_Proves(dom.verification_token))
    try:
        domains.verify_domain(fx["actor"](), domain_id=dom.id)
        domains.activate_domain(fx["actor"](), domain_id=dom.id)
    finally:
        domains.set_txt_resolver(previous)
    return dom


# --- GET /admin/partners/capabilities --------------------------------------------


def test_the_capability_catalog_is_the_registry(staff_client):
    from workeasy360.commercial.partners.capabilities import CAPABILITIES

    client, _u = staff_client("platform.staff_partner.view")
    response = client.get("/api/v1/admin/partners/capabilities")
    assert response.status_code == 200
    body = response.json()["results"]
    assert {c["key"] for c in body} == set(CAPABILITIES)
    assert all(c["label"] for c in body)


def test_the_capability_catalog_needs_the_partner_view_permission(client, staff_client):
    assert client.get("/api/v1/admin/partners/capabilities").status_code == 401
    other, _u = staff_client("platform.staff_customer.view")
    assert other.get("/api/v1/admin/partners/capabilities").status_code == 403


def test_a_partner_user_cannot_read_the_staff_capability_catalog(partner_client):
    client, _fx = partner_client("commission.view")
    assert client.get("/api/v1/admin/partners/capabilities").status_code == 403


# --- GET /public/branding/logo ------------------------------------------------------


def test_an_active_brands_logo_is_served_same_origin(client, partner_factory):
    fx = partner_factory("branding.manage", "domain.manage")
    _active_brand(fx, with_logo=True)

    brand = client.get("/api/v1/public/branding", {"host": HOST}).json()["brand"]
    assert brand["logo_url"].startswith("/api/v1/public/branding/logo?host=")

    response = client.get(brand["logo_url"])
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response.content == PNG
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    # The URL is same-origin (a path), never an object-store URL.
    assert "://" not in brand["logo_url"]


def test_the_logo_endpoint_takes_a_host_never_a_file_id(client, partner_factory):
    fx = partner_factory("branding.manage", "domain.manage")
    _active_brand(fx, with_logo=True)
    from workeasy360.commercial.partners.models import PartnerBrand

    file_id = str(PartnerBrand.objects.get(partner_id=fx["partner"].id).logo_file_id)
    for params in ({"file_id": file_id}, {"id": file_id}, {"host": file_id}, {}):
        assert client.get("/api/v1/public/branding/logo", params).status_code == 404


def test_no_logo_and_unknown_hosts_are_404(client, partner_factory):
    fx = partner_factory("branding.manage", "domain.manage")
    _active_brand(fx, with_logo=False)
    brand = client.get("/api/v1/public/branding", {"host": HOST}).json()["brand"]
    assert brand["logo_url"] is None
    assert client.get("/api/v1/public/branding/logo", {"host": HOST}).status_code == 404
    assert (
        client.get(
            "/api/v1/public/branding/logo", {"host": "nobody.example.com"}
        ).status_code
        == 404
    )


def test_a_disabled_domain_stops_serving_its_logo(client, partner_factory):
    from workeasy360.commercial.partners import domains

    fx = partner_factory("branding.manage", "domain.manage")
    dom = _active_brand(fx, with_logo=True)
    assert client.get("/api/v1/public/branding/logo", {"host": HOST}).status_code == 200
    domains.disable_domain(fx["actor"](), domain_id=dom.id)
    assert client.get("/api/v1/public/branding/logo", {"host": HOST}).status_code == 404


def test_another_partners_host_cannot_fetch_this_partners_logo(client, partner_factory):
    """Two brands: the one without a logo never returns the other's."""
    from workeasy360.commercial.partners import branding, domains

    with_logo = partner_factory("branding.manage", "domain.manage")
    _active_brand(with_logo, with_logo=True)
    other = partner_factory("branding.manage", "domain.manage")
    branding.update_brand(other["actor"](), brand_name="No Logo")
    dom = domains.request_domain(other["actor"](), hostname="other.logo-example.com")
    previous = domains.set_txt_resolver(_Proves(dom.verification_token))
    try:
        domains.verify_domain(other["actor"](), domain_id=dom.id)
        domains.activate_domain(other["actor"](), domain_id=dom.id)
    finally:
        domains.set_txt_resolver(previous)
    response = client.get(
        "/api/v1/public/branding/logo", {"host": "other.logo-example.com"}
    )
    assert response.status_code == 404


def test_the_logo_endpoint_is_rate_limited(client):
    from django.conf import settings

    limit = settings.CONTROL_PLANE_RATE_LIMITS["public_branding_ip"][0]
    # Twice the limit: the counter is per fixed window, and a run that crossed
    # a window boundary split ``limit + 5`` requests into two windows that
    # each stayed under it (2026-10 audit). However the requests fall across
    # at most two windows, one of them now goes over.
    codes = {
        client.get("/api/v1/public/branding/logo", {"host": "x.example.com"}).status_code
        for _ in range(2 * limit + 5)
    }
    assert 429 in codes


# --- retention: a spent verification token is not kept (Task 122) -----------------------------


def test_a_spent_verification_token_is_cleared_but_a_live_claim_keeps_its_own(
    partner_factory,
):
    from workeasy360.commercial.partners import domains
    from workeasy360.commercial.partners.models import PartnerDomain

    fx = partner_factory("branding.manage", "domain.manage")
    live = _active_brand(fx, with_logo=False)  # verified and active on HOST
    assert PartnerDomain.objects.get(id=live.id).verification_token, (
        "a live claim needs its proof"
    )

    pending = domains.request_domain(
        fx["actor"](), hostname="pending.retention-example.com"
    )
    assert PartnerDomain.objects.get(id=pending.id).verification_token
    domains.disable_domain(fx["actor"](), domain_id=pending.id)
    assert PartnerDomain.objects.get(id=pending.id).verification_token == ""

    domains.disable_domain(fx["actor"](), domain_id=live.id)
    assert PartnerDomain.objects.get(id=live.id).verification_token == ""


def test_exhausted_verification_attempts_clear_the_token(partner_factory):
    from workeasy360.commercial.partners import domains
    from workeasy360.commercial.partners.models import PartnerDomain

    class NoRecord:
        def txt_values(self, name):
            return []

    fx = partner_factory("domain.manage")
    row = domains.request_domain(fx["actor"](), hostname="never.retention-example.com")
    previous = domains.set_txt_resolver(NoRecord())
    try:
        for _ in range(domains.MAX_VERIFICATION_ATTEMPTS):
            domains.verify_domain(fx["actor"](), domain_id=row.id)
    finally:
        domains.set_txt_resolver(previous)
    row = PartnerDomain.objects.get(id=row.id)
    assert row.status == "failed" and row.failure_code == "verification_expired"
    assert row.verification_token == ""

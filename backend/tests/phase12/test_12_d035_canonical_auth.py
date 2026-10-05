"""D-035 decision B (ADR-039): authentication stays on the canonical host.

A partner's custom domain is a branded landing page that hands the visitor to
the canonical sign-in page. These tests pin the backend half:

- the edge's on-demand TLS gate issues a certificate only for the canonical
  host and a partner domain that would serve a brand *right now*;
- the API is still never served on a partner's hostname (the Phase 10 guard),
  so no session cookie can be set for, or CSRF origin trusted from, one.

The frontend half (no credential field on a partner host, hand-off target
built from the configured canonical origin) is pinned in
``frontend/src/features/auth/d035.test.tsx``.
"""

from __future__ import annotations

import pytest
from django.test import Client

from tests.phase10 import test_10d_white_label as _white_label
from workeasy360.commercial.partners import branding, domains

pytestmark = pytest.mark.django_db

dns = _white_label.dns
HOST = "app.brand-example.com"
TLS = "/api/v1/public/edge/tls-allowed"


@pytest.fixture
def live_brand(partner_factory, dns):
    fx = partner_factory("branding.manage", "domain.manage", code="d035-brand")
    branding.update_brand(fx["actor"](), brand_name="D035 Brand", accent_color="#1a56db")
    row = domains.request_domain(fx["actor"](), hostname=HOST)
    dns.records[f"_w360-verify.{HOST}"] = [row.verification_token]
    domains.verify_domain(fx["actor"](), domain_id=row.id)
    domains.activate_domain(fx["actor"](), domain_id=row.id)
    fx["domain_id"] = row.id
    return fx


def test_the_canonical_host_may_have_a_certificate(settings):
    settings.W360_FRONTEND_URL = "https://app.workeasy360.com"
    assert Client().get(TLS, {"domain": "app.workeasy360.com"}).status_code == 200


def test_an_arbitrary_name_pointed_at_the_edge_gets_no_certificate(settings):
    settings.W360_FRONTEND_URL = "https://app.workeasy360.com"
    for name in (
        "attacker.example.net",
        "",
        "localhost",
        "app.brand-example.com.evil.net",
    ):
        assert Client().get(TLS, {"domain": name}).status_code == 404, name


def test_a_serving_partner_domain_may_have_a_certificate(live_brand):
    assert Client().get(TLS, {"domain": HOST}).status_code == 200
    assert Client().get(TLS, {"domain": HOST.upper() + "."}).status_code == 200


def test_disabling_the_domain_stops_certificates_at_once(live_brand):
    domains.disable_domain(live_brand["actor"](), domain_id=live_brand["domain_id"])
    assert Client().get(TLS, {"domain": HOST}).status_code == 404


def test_a_verified_but_not_activated_domain_gets_no_certificate(partner_factory, dns):
    fx = partner_factory("branding.manage", "domain.manage", code="d035-pending")
    branding.update_brand(fx["actor"](), brand_name="Pending", accent_color="#1a56db")
    row = domains.request_domain(fx["actor"](), hostname="pending.brand-example.com")
    dns.records["_w360-verify.pending.brand-example.com"] = [row.verification_token]
    domains.verify_domain(fx["actor"](), domain_id=row.id)
    assert Client().get(TLS, {"domain": "pending.brand-example.com"}).status_code == 404


def test_the_api_is_never_served_on_a_partner_hostname(live_brand):
    """No session, CSRF cookie or sign-in can happen on the partner host: the
    platform refuses the Host before any view (the edge also rewrites the
    branding lookup to the canonical Host)."""
    spoofed = Client(HTTP_HOST=HOST)
    for path in ("/api/v1/auth/session", "/api/v1/auth/login", TLS):
        assert spoofed.get(path).status_code == 400, path
    assert (
        spoofed.post(
            "/api/v1/auth/login", {}, content_type="application/json"
        ).status_code
        == 400
    )


def test_a_sign_in_posted_from_a_partner_origin_is_refused_by_csrf(live_brand, settings):
    """The decision B guarantee at the CSRF layer: the partner origin is never
    trusted (no wildcard, no per-domain additions)."""
    assert not any("brand-example" in o for o in settings.CSRF_TRUSTED_ORIGINS)
    assert "*" not in "".join(settings.CSRF_TRUSTED_ORIGINS)
    browser = Client(enforce_csrf_checks=True)
    browser.get("/api/v1/auth/session")
    token = browser.cookies["w360_csrf"].value
    response = browser.post(
        "/api/v1/auth/login",
        {"email": "a@example.com", "password": "x"},
        content_type="application/json",
        HTTP_ORIGIN=f"https://{HOST}",
        HTTP_X_CSRFTOKEN=token,
        secure=True,
    )
    assert response.status_code == 403

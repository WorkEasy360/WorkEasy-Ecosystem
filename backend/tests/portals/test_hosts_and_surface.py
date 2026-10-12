"""Which host serves which API surface, Host spoofing, CSRF origins, and the
links the platform builds for each portal (ADR-043, design sections 2 and 5).

The surface rules are a narrowing: they can only refuse (404). Nothing they
let through is decided by the host.
"""

from __future__ import annotations

import pytest

from tests.portals.conftest import ORIGINS, HostClient, new_state

pytestmark = pytest.mark.django_db


# --- each privileged surface on its own host only ------------------------------------


@pytest.mark.parametrize(
    "path,owner",
    [
        ("/api/v1/admin/session", "admin"),
        ("/api/v1/partner/portal", "partner"),
        ("/api/v1/orgs/anything/apps", "app"),
        ("/api/v1/auth/login", "auth"),
        ("/api/v1/auth/handoff", "auth"),
        ("/api/v1/integrations/oauth/authorize", "auth"),
        ("/api/v1/integrations/oauth/resume", "auth"),
    ],
)
def test_a_portal_owned_prefix_is_not_served_on_any_other_host(portals_on, path, owner):
    """Anonymous requests: on the owning host the view (or its own guard)
    answers; on every other host the surface rule answers 404 first."""

    def call(portal):
        client = HostClient(portal)
        writes = ("/api/v1/auth/login", "/api/v1/auth/handoff")
        return client.post(path, {}) if path in writes else client.get(path)

    def refused_by_surface(response):
        return response.status_code == 404 and response.json() == {"code": "not_found"}

    assert not refused_by_surface(call(owner)), path
    for portal in ("admin", "partner", "app", "auth", "api"):
        if portal != owner:
            assert refused_by_surface(call(portal)), (portal, path)


def test_the_redeem_endpoint_is_served_on_the_three_destinations_only(portals_on):
    for portal, expected in (
        ("admin", 400),
        ("partner", 400),
        ("app", 400),
        ("auth", 404),
        ("api", 404),
    ):
        response = HostClient(portal).post("/api/v1/auth/handoff/redeem", {"code": "x"})
        assert response.status_code == expected, portal


def test_shared_endpoints_answer_on_every_portal(portals_on):
    for portal in ("admin", "partner", "app", "auth"):
        assert HostClient(portal).get("/api/v1/auth/session").status_code == 200
        assert HostClient(portal).post("/api/v1/auth/logout").status_code == 200


def test_the_surface_rules_never_grant(portals_on, customer, sign_in, hand_off):
    """Served on its own host, a privileged prefix is still decided by
    identity: a customer on the admin host is refused there."""
    app, _, _ = hand_off(sign_in(customer["user"]), "user")
    admin = HostClient("admin")
    admin.client.cookies = app.client.cookies
    assert admin.get("/api/v1/admin/customers").status_code in (401, 403)


def test_the_surface_middleware_only_ever_refuses():
    """Unit: for every rule and host, the middleware either returns the
    view's own response untouched or a 404 - never anything else."""
    from django.http import HttpResponse
    from django.test import RequestFactory, override_settings

    from tests.portals.conftest import HOST
    from workeasy360.interfaces.api.portal_surface import (
        SURFACE_RULES,
        PortalSurfaceMiddleware,
    )

    sentinel = HttpResponse("view", status=299)
    middleware = PortalSurfaceMiddleware(lambda request: sentinel)
    with override_settings(
        PORTALS_ENABLED=True,
        W360_PORTAL_ORIGINS=ORIGINS,
        ALLOWED_HOSTS=list(HOST.values()),
    ):
        for path, _exact, _hosts in SURFACE_RULES:
            for host in HOST.values():
                response = middleware(RequestFactory().get(path, HTTP_HOST=host))
                assert response is sentinel or response.status_code == 404


# --- Host header -------------------------------------------------------------------------


def test_a_spoofed_host_is_refused_before_any_view(portals_on):
    from django.test import Client

    for host in ("evil.example.com", "admin.workeasy360.com.evil.com", "workeasy360.com"):
        response = Client().get("/api/v1/auth/session", HTTP_HOST=host)
        assert response.status_code == 400, host


def test_no_view_derives_tenant_or_authority_from_the_host():
    """The only code that reads the Host header is the surface narrowing and
    the hand-off redemption's binding check - both of which can only refuse."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "workeasy360"
    allowed = {
        root / "interfaces" / "api" / "portal_surface.py",
        root / "interfaces" / "api" / "v1" / "auth_views.py",
    }
    pattern = re.compile(r"get_host\(|HTTP_HOST|SERVER_NAME|HTTP_X_FORWARDED_HOST")
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "tests" not in path.parts
        and path not in allowed
        and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# --- CSRF --------------------------------------------------------------------------------


def test_a_write_from_an_untrusted_origin_is_refused(portals_on, customer, sign_in):
    """A product or any other sibling subdomain is not a trusted origin."""
    auth = sign_in(customer["user"])
    strict = HostClient("auth", enforce_csrf=True)
    strict.client.cookies = auth.client.cookies
    token = strict.client.cookies["w360_csrf"].value
    body = {"mode": "user", "state": new_state()}
    for origin in (
        "https://crm.workeasy360.com",
        "https://evil.example.com",
        "https://auth.workeasy360.com.evil.com",
        "null",
    ):
        response = strict.post(
            "/api/v1/auth/handoff", body, HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=origin
        )
        assert response.status_code == 403, origin
    accepted = strict.post(
        "/api/v1/auth/handoff",
        body,
        HTTP_X_CSRFTOKEN=token,
        HTTP_ORIGIN=ORIGINS["auth"],
    )
    assert accepted.status_code == 200, accepted.content


def test_trusted_origins_carry_no_wildcard(portals_on):
    from django.conf import settings

    assert not any("*" in o for o in settings.CSRF_TRUSTED_ORIGINS)
    assert set(settings.CSRF_TRUSTED_ORIGINS) == {
        ORIGINS[n] for n in ("admin", "partner", "app", "auth")
    }


# --- links name their portal ---------------------------------------------------------------


def test_account_links_go_to_the_sign_in_host_and_billing_to_the_customer_portal(
    portals_on,
):
    from workeasy360.platform.administration import invitations, self_service
    from workeasy360.platform.identity import portals

    assert portals.auth_url() == ORIGINS["auth"]
    assert portals.app_url() == ORIGINS["app"]
    assert invitations._url("t").startswith(ORIGINS["auth"] + "/invitations/accept#")
    assert self_service._url("/reset-password", "t").startswith(
        ORIGINS["auth"] + "/reset-password#"
    )


def test_single_host_links_stay_on_the_one_frontend(settings):
    from workeasy360.platform.identity import portals

    settings.W360_FRONTEND_URL = "https://one.example.com"
    assert portals.auth_url() == portals.app_url() == "https://one.example.com"


def test_an_oidc_login_is_parked_on_the_sign_in_host(portals_on):
    from types import SimpleNamespace

    from workeasy360.interfaces.api.v1 import ecosystem_views
    from workeasy360.platform.ecosystem import protocol

    parked = SimpleNamespace(token="tok")
    original = ecosystem_views.authorization.create_pending
    ecosystem_views.authorization.create_pending = lambda request_obj: parked
    try:
        response = ecosystem_views._park_and_redirect(
            object(), protocol.OAuthError(protocol.LOGIN_REQUIRED, "")
        )
    finally:
        ecosystem_views.authorization.create_pending = original
    assert response["Location"] == f"{ORIGINS['auth']}/login?resume=tok"


def test_the_edge_issues_certificates_for_every_platform_host(portals_on, client):
    for host in ("admin", "partner", "app", "auth", "api"):
        name = ORIGINS[host].removeprefix("https://")
        response = HostClient("api").get(f"/api/v1/public/edge/tls-allowed?domain={name}")
        assert response.status_code == 200, name
    unknown = HostClient("api").get(
        "/api/v1/public/edge/tls-allowed?domain=x.workeasy360.com"
    )
    assert unknown.status_code == 404

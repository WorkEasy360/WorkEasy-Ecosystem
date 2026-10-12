"""The independent final audit's findings, each pinned (ADR-043).

- P2-1: a hand-off never renews the sign-in's 30-day absolute lifetime.
- P2-2: staff catalog and integration administration under ``/orgs/`` is the
  Super Admin host's, not the customer portal's.
- P2-3: every entitlement read - held products and feature values, on the
  request path and the product path - ends at ``ends_at``, not only launch.
- P3: a write naming another portal's origin is refused; a default port is
  the same origin; a sign-in without a sign-in id gets one before a hand-off.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.portals.conftest import ORIGINS, HostClient, code_of, new_state

pytestmark = pytest.mark.django_db


def _redeem(auth, mode="user"):
    state = new_state()
    issued = auth.post("/api/v1/auth/handoff", {"mode": mode, "state": state})
    assert issued.status_code == 200, issued.content
    portal = HostClient({"user": "app", "admin": "admin", "partner": "partner"}[mode])
    portal.set_cookie("w360_portal_state", state)
    redeemed = portal.post("/api/v1/auth/handoff/redeem", {"code": code_of(issued)})
    return portal, redeemed


def _row(client):
    from workeasy360.platform.identity.models import UserSession

    return UserSession.objects.get(session_key=client.session.session_key)


# --- P2-1 ---------------------------------------------------------------------------------


def test_a_portal_session_keeps_the_sign_ins_age(sign_in, customer):
    from workeasy360.platform.tenancy.resolution import SESSION_AUTHENTICATED_AT_KEY

    auth = sign_in(customer["user"])
    session = auth.session
    signed_in = time.time() - 10 * 86400  # ten days ago
    session[SESSION_AUTHENTICATED_AT_KEY] = signed_in
    session.save()
    app, redeemed = _redeem(auth)
    assert redeemed.status_code == 200
    born = _row(app).created_at
    assert abs(born.timestamp() - signed_in) < 2, (born, signed_in)


def test_a_portal_session_ends_with_its_sign_ins_absolute_lifetime(
    sign_in, customer, settings
):
    from workeasy360.platform.tenancy.resolution import SESSION_AUTHENTICATED_AT_KEY

    auth = sign_in(customer["user"])
    session = auth.session
    # Signed in a minute short of the cap; the auth row itself is new.
    session[SESSION_AUTHENTICATED_AT_KEY] = (
        time.time() - settings.SESSION_ABSOLUTE_TIMEOUT + 60
    )
    session.save()
    app, _ = _redeem(auth)
    assert app.get("/api/v1/auth/session").json()["authenticated"] is True
    from workeasy360.platform.identity.models import UserSession

    UserSession.objects.filter(pk=_row(app).pk).update(
        created_at=timezone.now()
        - timedelta(seconds=settings.SESSION_ABSOLUTE_TIMEOUT + 1)
    )
    assert app.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_an_ordinary_sign_in_is_dated_now(sign_in, customer):
    auth = sign_in(customer["user"])
    assert timezone.now() - _row(auth).created_at < timedelta(seconds=30)


# --- P2-2 ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/orgs/platform/commercial/products",
        "/api/v1/orgs/platform/commercial/price-books",
        "/api/v1/orgs/platform/integrations",
        f"/api/v1/orgs/platform/integrations/{uuid.uuid4().hex}/credentials",
    ],
)
def test_staff_administration_under_orgs_is_the_admin_hosts(portals_on, path):
    for portal in ("app", "partner", "auth", "api"):
        response = HostClient(portal).get(path)
        assert response.status_code == 404 and response.json() == {"code": "not_found"}, (
            portal
        )
    answered = HostClient("admin").get(path)
    assert answered.json() != {"code": "not_found"}


def test_the_customer_api_stays_on_the_customer_portal(portals_on):
    from workeasy360.interfaces.api.portal_surface import allowed_portals

    assert allowed_portals("/api/v1/orgs/acme/apps") == frozenset({"app"})
    assert allowed_portals("/api/v1/orgs/acme/commercial-notes") == frozenset({"app"})
    assert allowed_portals("/api/v1/orgs/acme/integrations") == frozenset({"admin"})


# --- P2-3 ---------------------------------------------------------------------------------


def _resolved(products, features):
    from workeasy360.platform.entitlements.services.read import ResolvedEntitlements

    return ResolvedEntitlements(
        organization_id=uuid.uuid4(), version=1, products=products, features=features
    )


def test_held_products_and_features_end_with_the_entitlement(monkeypatch):
    from workeasy360.platform.entitlements.services import read

    past = (timezone.now() - timedelta(minutes=1)).isoformat()
    future = (timezone.now() + timedelta(days=3)).isoformat()
    resolved = _resolved(
        {
            "easy_crm": {"status": "trial", "seats": 5, "write": True, "ends_at": past},
            "easybook": {"status": "trial", "seats": 5, "write": True, "ends_at": future},
            "easy_project": {
                "status": "active",
                "seats": 5,
                "write": True,
                "ends_at": None,
            },
        },
        {
            "easy_crm.sales.pipeline": True,
            "easybook.accounting.records": True,
            "easy_project.ai.monthly_budget_micros": 500,
        },
    )
    monkeypatch.setattr(read, "_resolve", lambda organization_id: resolved)
    org = resolved.organization_id
    assert sorted(read.products(org)) == ["easy_project", "easybook"]
    assert read.feature(org, "easy_crm.sales.pipeline") is None
    assert read.feature(org, "easybook.accounting.records") is True
    assert read.quota(org, "easy_project.ai.monthly_budget_micros") == 500


def test_the_product_path_feature_read_ends_too(organization_factory):
    from workeasy360.platform.entitlements.models import EntitlementSnapshot
    from workeasy360.platform.entitlements.selectors import feature_for_organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org, _membership, _user = organization_factory()
    past = (timezone.now() - timedelta(minutes=1)).isoformat()
    with (
        unscoped_access(reason="test seeds a snapshot", module="tests"),
        organization_db_scope(org.id),
    ):
        EntitlementSnapshot.unscoped.update_or_create(
            organization_id=org.id,
            defaults={
                "products": {
                    "easy_crm": {"status": "trial", "seats": 5, "ends_at": past}
                },
                "features": {"easy_crm.ai.monthly_budget_micros": 900},
                "computed_at": timezone.now(),
            },
        )
    assert feature_for_organization(org.id, "easy_crm.ai.monthly_budget_micros") is None


# --- P3 -------------------------------------------------------------------------------------


def test_a_write_naming_another_portals_origin_is_refused(portals_on):
    admin = HostClient("admin")
    for origin in (
        ORIGINS["app"],
        ORIGINS["partner"],
        ORIGINS["auth"],
        "https://crm.workeasy360.com",
    ):
        response = admin.post("/api/v1/auth/logout", HTTP_ORIGIN=origin)
        assert response.status_code == 403, origin
        assert response.json()["code"] == "csrf_failed"
    assert (
        admin.post("/api/v1/auth/logout", HTTP_ORIGIN=ORIGINS["admin"]).status_code == 200
    )
    # No Origin (a server-to-server caller) is left to Django's own checks.
    assert admin.post("/api/v1/auth/logout").status_code == 200
    # Reads are never refused by origin.
    assert (
        admin.get("/api/v1/auth/session", HTTP_ORIGIN=ORIGINS["app"]).status_code == 200
    )


def test_a_default_port_is_the_same_origin():
    from workeasy360.contracts.values.portals import parse_origin, portal_for_host

    assert parse_origin("https://admin.workeasy360.com:443") == (
        "https",
        "admin.workeasy360.com",
    )
    assert parse_origin("http://app.example.com:80") == ("http", "app.example.com")
    assert parse_origin("https://admin.workeasy360.com:8443") == (
        "https",
        "admin.workeasy360.com:8443",
    )
    assert parse_origin("https://admin.workeasy360.com:99999") is None
    assert (
        portal_for_host(
            "admin.workeasy360.com", {"admin": "https://admin.workeasy360.com:443"}
        )
        == "admin"
    )


def test_a_sign_in_without_an_id_gets_one_before_the_hand_off(sign_in, customer):
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    auth = sign_in(customer["user"])
    session = auth.session
    del session[SESSION_SIGN_IN_ID_KEY]
    session.save()
    app, redeemed = _redeem(auth)
    assert redeemed.status_code == 200
    minted = auth.session[SESSION_SIGN_IN_ID_KEY]
    assert minted and app.session[SESSION_SIGN_IN_ID_KEY] == minted
    app.post("/api/v1/auth/logout")
    assert auth.get("/api/v1/auth/session").json() == {"authenticated": False}

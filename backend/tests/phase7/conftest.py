"""Phase 7 fixtures: a fully-configured reseller channel."""

from __future__ import annotations

import pytest

from tests.phase7.support import CAPS


@pytest.fixture
def channel(catalog, partner_factory, staff_factory, user_factory):
    """An ACTIVE reseller with a product grant and a 10% programme rule."""
    from workeasy360.commercial.commissions import rules
    from workeasy360.commercial.partners import services

    fx = partner_factory(*CAPS)
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    _u, commission_ctx = staff_factory("platform.staff_commission.manage")
    rules.create_rule_version(
        commission_ctx, code="default", rate_bp=1000, reason="Programme default rate"
    )
    fx.update(catalog=catalog, commission_ctx=commission_ctx, user_factory=user_factory)
    return fx


# --- HTTP clients -----------------------------------------------------------

PASSWORD = "correct horse battery staple 42"


@pytest.fixture
def http_login(db):
    """Sign a user in through the real login endpoint and return the client."""
    from django.test import Client

    from tests.factories.sessions import lapsed_step_up
    from workeasy360.platform.tenancy.resolution import record_step_up

    def make(
        user,
        *,
        aal2: bool = True,
        enforce_csrf: bool = False,
        mfa_verified: bool | None = None,
    ):
        """``mfa_verified`` (default: staff only) marks a session without
        ``aal2`` as having verified a factor earlier (D-037)."""
        client = Client(enforce_csrf_checks=enforce_csrf)
        client.get("/api/v1/auth/session")  # issues the CSRF cookie
        response = client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": PASSWORD},
            content_type="application/json",
            **(
                {"HTTP_X_CSRFTOKEN": client.cookies["w360_csrf"].value}
                if enforce_csrf
                else {}
            ),
        )
        assert response.status_code == 200, response.content
        verified = user.is_staff if mfa_verified is None else mfa_verified
        if aal2 or verified:
            session = client.session
            if aal2:
                record_step_up(session)
            else:
                lapsed_step_up(session)
            session.save()
        return client

    return make


@pytest.fixture
def staff_client(staff_factory, user_factory, http_login):
    """A signed-in staff member holding exactly ``permissions``."""
    from workeasy360.platform.authz import staff

    root = user_factory(is_staff=True)

    def make(
        *permissions: str,
        aal2: bool = True,
        enforce_csrf: bool = False,
        mfa_verified: bool | None = None,
    ):
        user = user_factory(is_staff=True, password=PASSWORD)
        for permission in permissions:
            staff.create_grant(
                user_id=user.id,
                permission=permission,
                granted_by_id=root.id,
                reason="Test fixture grants a control-plane permission",
            )
        return (
            http_login(
                user, aal2=aal2, enforce_csrf=enforce_csrf, mfa_verified=mfa_verified
            ),
            user,
        )

    return make


@pytest.fixture
def partner_client(partner_factory, http_login, user_factory):
    """A signed-in partner administrator. Returns ``(client, fx)``."""

    def make(*capabilities: str, aal2: bool = True, enforce_csrf: bool = False):
        fx = partner_factory(*capabilities)
        # The fixture's member has no password; set one so the real login works.
        fx["user"].set_password(PASSWORD)
        fx["user"].save(update_fields=["password"])
        return http_login(fx["user"], aal2=aal2, enforce_csrf=enforce_csrf), fx

    return make

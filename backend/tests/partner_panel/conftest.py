"""Partner business panel (ADR-045): fixtures.

Everything is reused: the Phase 7 channel (a reseller with a catalog, a product
grant and a programme commission rule), the Phase 10 console (a reseller with a
real sale and a cleared commission, a white-label partner and a draft) and the
real HTTP clients that sign in through ``/api/v1/auth/login``. Nothing here
creates a partner, a member or a sub-partner any way but the existing commands.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.phase10 import conftest as _phase10

# Re-exported by assignment (not import) so that using a fixture as a test
# argument is not a redefinition of an imported name.
PASSWORD = _phase10.PASSWORD
channel = _phase10.channel
http_login = _phase10.http_login
partner_client = _phase10.partner_client
staff_client = _phase10.staff_client
console = _phase10.console
anonymous = _phase10.anonymous
count_queries_of = _phase10.count_queries

ALL_SELL = (
    "customer.provision",
    "customer.manage",
    "subscription.sell",
    "subscription.manage",
    "pricing.view",
    "commission.earn",
    "commission.view",
    "payout.view",
)


def api(partner_id, path: str = "") -> str:
    return f"/api/v1/partner/{partner_id}/{path}".rstrip("/")


def send(client, method: str, url: str, body=None, *, key: bool = False, **extra):
    headers = {"HTTP_IDEMPOTENCY_KEY": uuid.uuid4().hex} if key else {}
    if body is None and method in ("get", "delete"):
        return getattr(client, method)(url, **headers, **extra)
    return getattr(client, method)(
        url, json.dumps(body or {}), content_type="application/json", **headers, **extra
    )


@pytest.fixture
def member_client(user_factory, http_login):
    """A signed-in member of ``fx``'s partner in ``role`` (added by staff)."""
    from workeasy360.commercial.partners import services

    def make(fx, role: str, *, aal2: bool = True):
        user = user_factory(password=PASSWORD)
        services.add_member(
            fx["staff_ctx"], fx["partner"].id, email=user.email, role=role
        )
        return http_login(user, aal2=aal2), user

    return make


@pytest.fixture
def seller(partner_factory, catalog, staff_factory, user_factory):
    """An ACTIVE partner holding the selling capabilities, a ``crm`` grant and a
    programme commission rule - the Phase 7 channel without its member."""
    from workeasy360.commercial.commissions import rules
    from workeasy360.commercial.partners import services

    fx = partner_factory(*ALL_SELL, "subpartner.manage", "branding.manage")
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    _u, commission_ctx = staff_factory("platform.staff_commission.manage")
    rules.create_rule_version(
        commission_ctx, code="default", rate_bp=1000, reason="Programme default rate"
    )
    fx.update(catalog=catalog, commission_ctx=commission_ctx, user_factory=user_factory)
    return fx


@pytest.fixture
def owner_client(seller, http_login):
    """A signed-in *owner* of ``seller`` (the fixture member is an admin)."""
    from workeasy360.commercial.partners.models import PartnerMember

    seller["user"].set_password(PASSWORD)
    seller["user"].save(update_fields=["password"])
    PartnerMember.objects.filter(
        partner_id=seller["partner"].id, user=seller["user"]
    ).update(role="owner")
    return http_login(seller["user"])


@pytest.fixture
def http_login_with_password(db):
    """Sign in through the real endpoint with an email and a password the test
    was handed at runtime (never one written in the repository)."""
    from django.test import Client

    def make(email: str, password: str, *, change_to: str | None = None):
        client = Client()
        client.get("/api/v1/auth/session")
        response = client.post(
            "/api/v1/auth/login",
            {"email": email, "password": password},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        if change_to is not None:
            changed = client.post(
                "/api/v1/auth/password",
                {"current_password": password, "new_password": change_to},
                content_type="application/json",
            )
            assert changed.status_code == 200, changed.content
            from workeasy360.platform.tenancy.resolution import record_step_up

            session = client.session
            record_step_up(session)
            session.save()
        return client

    return make


# The Phase 11 customer shop: an organization whose owner is signed in.
from tests.phase11 import conftest as _phase11

console_org = _phase11.console_org
login = _phase11.login
configure_trial = _phase11.configure_trial


def partner_audit(partner_id, *fields):
    """The audit rows whose recorded changes name ``partner_id``, read the way the
    platform reads its trail: inside the platform organization's scope."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with (
        tenant_context(routing_context(PLATFORM_ORGANIZATION_ID)),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        return list(
            AuditEvent.objects.filter(changes__partner_id=str(partner_id)).values(*fields)
        )

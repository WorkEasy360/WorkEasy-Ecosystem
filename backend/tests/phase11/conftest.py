"""Phase 11 fixtures: the catalog, checkout and reseller channel of Phases 2-10,
unchanged, plus what four separately deployed products need.

Phase 11 adds presentation fields, a per-product trial and screens over
existing services - not a second commercial engine - so the earlier fixtures
(real HTTP clients with real sessions and step-up, a populated catalog, a
console organization, a configured reseller) are reused as they are.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.extension import conftest as _extension
from tests.extension.conftest import (  # noqa: F401 - helpers, not fixtures
    PASSWORD,
    place_and_pay,
    staff_scope,
    url,
)
from tests.phase7 import conftest as _phase7
from tests.phase9 import conftest as _phase9

# Re-exported by assignment (not import) so that using a fixture as a test
# argument is not a redefinition of an imported name.
console_org = _extension.console_org
fake_payments = _extension.fake_payments
login = _extension.login
staff_login = _extension.staff_login
channel = _phase7.channel
http_login = _phase7.http_login
partner_client = _phase7.partner_client
staff_client = _phase7.staff_client
anonymous = _phase9.anonymous
deliver_account_mail = _phase9.deliver_account_mail

CATALOG_STAFF = ("platform.staff_catalog.manage", "platform.staff_pricing.manage")


def post(client, path, body=None, *, key=True, **extra):
    headers = {"HTTP_IDEMPOTENCY_KEY": uuid.uuid4().hex} if key else {}
    return client.post(
        path, json.dumps(body or {}), content_type="application/json", **headers, **extra
    )


def put(client, path, body=None, **extra):
    return client.put(
        path, json.dumps(body or {}), content_type="application/json", **extra
    )


def audit_of(resource_id, *fields, organization_id=None):
    """The audit rows about one resource, read the way the platform reads a
    trail: inside the owning organization's scope (the platform organization's
    for control-plane actions, a customer's for what happened in it)."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    owner = organization_id or PLATFORM_ORGANIZATION_ID
    with (
        tenant_context(routing_context(owner)),
        organization_db_scope(owner),
    ):
        return list(
            AuditEvent.objects.filter(resource_id=str(resource_id)).values(*fields)
        )


def tenant_rows(model, organization_id, **filters):
    """Rows of a tenant-owned table for one organization, read the way a
    cross-tenant job reads them: application scoping relaxed audibly, and the
    database's row-level security still confined to that one organization."""
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads a tenant table", module="tests.conftest"),
        organization_db_scope(organization_id),
    ):
        return list(model.unscoped.filter(organization_id=organization_id, **filters))


def count_queries(call):
    """Run ``call()``; return ``(how many queries it made, what it returned)``.

    The query log is cleared first and a count of zero is an error, so the
    measurement cannot silently stop measuring (a Phase 10 lesson).
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    connection.queries_log.clear()
    with CaptureQueriesContext(connection) as captured:
        result = call()
    count = len(captured.captured_queries)
    assert count > 0, "no queries were captured: the measurement is not measuring"
    return count, result


# --- shared scenario helpers (Phases 11b onward) --------------------------------------------


def configure_trial(catalog, product="crm", plans=("crm_starter",), **overrides):
    """Switch a product's trial on (14 days by default) - the platform ships no
    trial anywhere, so every scenario that needs one says so."""
    from workeasy360.commercial.catalog import services as catalog_services

    terms = {
        "enabled": True,
        "duration_days": 14,
        "seat_limit": None,
        "payment_method_required": False,
        "eligible_plan_codes": list(plans),
    }
    terms.update(overrides)
    with staff_scope(*CATALOG_STAFF) as ctx:
        catalog_services.configure_product_trial(
            ctx, product_id=catalog["products"][product].id, **terms
        )


def start_trial(shop, product="crm", plan="crm_starter", *, key=None, client=None):
    return (client or shop["client"]).post(
        url(shop["slug"], "trials"),
        {"product_code": product, "plan_code": plan},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
    )


def register_bare(platform_admin, in_tenant, product_code, *, activate=True, **manifest):
    """Register (and by default activate) an integration with **no credential**
    - what a product looks like before its owner has built single sign-on."""
    from tests.factories.manifests import manifest_for
    from workeasy360.platform.ecosystem import services

    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        integration = services.register_integration(
            ctx, manifest=manifest_for(product_code, **manifest)
        )
        if activate:
            services.transition(ctx, client_id=integration.client_id, to_status="active")
    return integration


@pytest.fixture
def shop(console_org, login, catalog, fake_payments):
    """An organization whose owner is signed in, with the CRM trial configured
    (14 days, on ``crm_starter`` only) and a billing account for buying."""
    configure_trial(catalog)
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP", "billing_email": "billing@example.com"},
        content_type="application/json",
    )
    assert opened.status_code == 200, opened.content
    return {**org, "client": owner, "catalog": catalog, "id": org["org"].id}

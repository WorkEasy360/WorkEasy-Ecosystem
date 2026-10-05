"""Phase 10 fixtures: the reseller channel of Phase 7, plus a populated console.

The Phase 7 fixtures (a fully configured reseller, real HTTP clients with real
sessions and step-up) are reused unchanged - Phase 10 adds screens and one use
case over that channel, not a second channel.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.phase7 import conftest as _phase7
from tests.phase9 import conftest as _phase9

# Re-exported by assignment (not import) so that using a fixture as a test
# argument is not a redefinition of an imported name.
PASSWORD = _phase7.PASSWORD
channel = _phase7.channel
http_login = _phase7.http_login
partner_client = _phase7.partner_client
staff_client = _phase7.staff_client
anonymous = _phase9.anonymous
deliver_account_mail = _phase9.deliver_account_mail
token_from = _phase9.token_from

VIEW = "platform.staff_partner.view"
MANAGE = "platform.staff_partner.manage"


def post(client, url, body=None, *, key=True, **extra):
    headers = {"HTTP_IDEMPOTENCY_KEY": uuid.uuid4().hex} if key else {}
    return client.post(
        url, json.dumps(body or {}), content_type="application/json", **headers, **extra
    )


def get(client, url, **params):
    return client.get(url, params)


def count_queries(call):
    """Run ``call()``; return ``(how many queries it made, what it returned)``.

    Django keeps the last 9000 queries of a connection in a ring, and a test
    that has just seeded a lot of data can fill it - after which a capture reads
    ``0`` before *and* after and every "same number of queries" assertion passes
    for the wrong reason. The log is cleared first, and a count of zero is an
    error, so the measurement cannot silently stop measuring.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    connection.queries_log.clear()
    with CaptureQueriesContext(connection) as captured:
        result = call()
    count = len(captured.captured_queries)
    assert count > 0, "no queries were captured: the measurement is not measuring"
    return count, result


@pytest.fixture
def viewer(staff_client):
    """A staff member who may only *look* at partners."""
    client, user = staff_client(VIEW)
    return client


@pytest.fixture
def console(channel, partner_factory, staff_factory, monkeypatch):
    """Three partners in three different situations, with real sales behind them.

    * ``reseller`` - the Phase 7 channel: active, sells crm, one customer with a
      live subscription, one cleared commission of 500 GBP minor, one open domain.
    * ``white_label`` - active, holds only branding and domain capabilities.
    * ``draft`` - created, holds nothing.
    """
    from tests.phase7.support import provision, sell
    from workeasy360.commercial.commissions import accrual
    from workeasy360.commercial.partners import domains, services

    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    reseller = channel
    result, owner = provision(reseller, name="Console Customer")
    sell(reseller, result.organization_id)

    white_label = partner_factory("branding.manage", "domain.manage", code="wl-console")
    domains.request_domain(white_label["actor"](), hostname="brand.console-example.com")

    _su, sctx = staff_factory(MANAGE)
    draft = services.create_partner(
        sctx, code="draft-console", display_name="Draft Co", payout_currency="GBP"
    )
    return {
        "reseller": reseller,
        "white_label": white_label,
        "draft": draft,
        "org": result.organization_id,
        "owner": owner,
        "staff_ctx": sctx,
    }

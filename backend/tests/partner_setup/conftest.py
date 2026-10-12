"""Reseller + white-label complete setup: fixtures.

Everything is reused - the Phase 7 channel, the Phase 10 console, the Partner
Control Center's fixtures and the Phase 11 shop (an organization whose owner is
signed in, over a catalog with a configurable trial). Nothing here creates a
partner, a staff member or an organization any other way than the existing
commands do.
"""

from __future__ import annotations

import json

import pytest

from tests.partner_center import conftest as _center
from tests.phase11 import conftest as _phase11

# Re-exported by assignment (not import) so that using a fixture as a test
# argument is not a redefinition of an imported name.
PASSWORD = _center.PASSWORD
VIEW = _center.VIEW
MANAGE = _center.MANAGE
channel = _center.channel
http_login = _center.http_login
partner_client = _center.partner_client
staff_client = _center.staff_client
console = _center.console
viewer = _center.viewer
count_queries = _center.count_queries
post = _center.post
console_org = _phase11.console_org
fake_payments = _phase11.fake_payments
login = _phase11.login
staff_login = _phase11.staff_login
anonymous = _phase11.anonymous
configure_trial = _phase11.configure_trial
start_trial = _phase11.start_trial
url = _phase11.url


def put(client, path, body=None, **extra):
    return client.put(
        path, json.dumps(body or {}), content_type="application/json", **extra
    )


def patch(client, path, body=None, **extra):
    return client.patch(
        path, json.dumps(body or {}), content_type="application/json", **extra
    )


@pytest.fixture
def manager(staff_client):
    """A staff member who may change partners (with a second factor)."""
    client, _user = staff_client(MANAGE, VIEW)
    return client


@pytest.fixture
def draft_partner(console):
    """A draft partner nobody has set anything on."""
    return console["draft"]


def profile_url(partner_id) -> str:
    return f"/api/v1/admin/partners/{partner_id}/profile"

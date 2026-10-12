"""Partner Control Center + sign-in mode fixtures.

Everything is reused: the Phase 7 channel (a reseller with real sales), the
Phase 10 console (a reseller, a white-label partner and a draft), and the real
HTTP clients that sign in through ``/api/v1/auth/login``. Nothing here creates
a partner or a staff member any other way than the existing commands do.
"""

from __future__ import annotations

import pytest

from tests.phase10 import conftest as _phase10

# Re-exported by assignment (not import) so that using a fixture as a test
# argument is not a redefinition of an imported name.
PASSWORD = _phase10.PASSWORD
VIEW = _phase10.VIEW
MANAGE = _phase10.MANAGE
COMMISSION_VIEW = "platform.staff_commission.view"
channel = _phase10.channel
http_login = _phase10.http_login
partner_client = _phase10.partner_client
staff_client = _phase10.staff_client
console = _phase10.console
viewer = _phase10.viewer
count_queries = _phase10.count_queries
post = _phase10.post

AFFILIATE_CAPS = (
    "referral.create",
    "referral.view",
    "commission.earn",
    "commission.view",
    "payout.view",
)


@pytest.fixture
def money_viewer(staff_client):
    """A staff member who may look at partners *and* at commission money."""
    client, _user = staff_client(VIEW, COMMISSION_VIEW)
    return client


@pytest.fixture
def affiliate(partner_factory):
    """An ACTIVE partner holding only referral and commission capabilities -
    what the Add Partner flow's Affiliate preset grants."""
    return partner_factory(*AFFILIATE_CAPS, code="aff-center")


@pytest.fixture
def combined(console, partner_factory):
    """ONE partner holding reseller, white-label and affiliate capabilities."""
    return partner_factory(
        "customer.provision",
        "subscription.sell",
        "branding.manage",
        "domain.manage",
        "referral.create",
        "referral.view",
        "commission.earn",
        code="all-three",
    )

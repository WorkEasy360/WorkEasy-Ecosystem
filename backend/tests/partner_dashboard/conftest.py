"""Unified partner dashboard (2026-11-03): fixtures, reused from the partner
panel suites - the Phase 10 console (a reseller with a customer, a live
subscription and a cleared commission), real HTTP clients and the staff
clients. Nothing here creates a partner any way but the existing commands."""

from __future__ import annotations

import pytest

from tests.partner_panel import conftest as _panel

PASSWORD = _panel.PASSWORD
api = _panel.api
send = _panel.send
channel = _panel.channel
console = _panel.console
http_login = _panel.http_login
staff_client = _panel.staff_client
partner_client = _panel.partner_client
member_client = _panel.member_client


@pytest.fixture
def reseller(console, http_login):
    """The console's reseller, signed in as its administrator."""
    fx = console["reseller"]
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    return http_login(fx["user"]), fx


@pytest.fixture
def customer(console, http_login):
    """The reseller's customer organization, its owner signed in. Returns
    ``(client, slug)``."""
    from workeasy360.commercial.partners import customers
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    owner = console["owner"]
    customers.accept_owner_invite(user_id=owner.id, organization_id=console["org"])
    owner.set_password(PASSWORD)
    owner.save(update_fields=["password"])
    with unscoped_access(
        reason="Test reads the customer's slug", module="tests.partner_dashboard"
    ):
        slug = Organization.objects.get(id=console["org"]).slug
    return http_login(owner), slug

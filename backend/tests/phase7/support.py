"""Shared helpers for the Phase 7 suites."""

from __future__ import annotations

import uuid

from workeasy360.commercial.partners import customers

CAPS = (
    "customer.provision",
    "customer.manage",
    "subscription.sell",
    "subscription.manage",
    "commission.earn",
    "pricing.view",
    "commission.view",
    "payout.view",
)


def provision(fx, name="Customer X", key=None, owner=None):
    owner = owner or fx["user_factory"]()
    result = customers.provision_customer(
        fx["actor"](),
        organization_name=name,
        owner_email=owner.email,
        idempotency_key=key or uuid.uuid4().hex,
    )
    return result, owner


def sell(fx, org_id, plan="crm_professional", key=None, **kw):
    return customers.sell_plan(
        fx["actor"](),
        org_id,
        plan_version_id=fx["catalog"]["versions"][plan].id,
        quantity=kw.pop("quantity", 1),
        idempotency_key=key or uuid.uuid4().hex,
        **kw,
    )


def links_for(partner_id, *, live_only=True):
    """A partner's link rows, oldest first (a test reads the table directly)."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    queryset = PartnerOrganizationLink.objects.filter(partner_id=partner_id)
    if live_only:
        queryset = queryset.exclude(status="ended")
    return list(queryset.order_by("started_at", "id"))

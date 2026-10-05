"""Seed a scratch database for the real-browser verification (not part of the repo)."""

import uuid

import django

django.setup()

from django.utils import timezone

from workeasy360.commercial.commissions import referrals, rules
from workeasy360.commercial.partners import branding, capabilities, customers, domains, services
from workeasy360.commercial.partners.access import partner_actor
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.platform.authz import staff
from workeasy360.platform.authz.control_plane import platform_context
from workeasy360.platform.authz.permissions import STAFF_PERMISSIONS
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.unscoped import unscoped_access

PASSWORD = "Browser-Test-Password-1!"


def user(email, **extra):
    with unscoped_access(reason="browser seed", module="seed"):
        u = User.objects.create_user(
            email=email, password=PASSWORD, status="active", email_verified_at=timezone.now(), **extra
        )
    return u


root = user("root@browser.test", is_staff=True)
admin = user("staff@browser.test", is_staff=True)
for p in STAFF_PERMISSIONS:
    staff.create_grant(user_id=admin.id, permission=str(p.key), granted_by_id=root.id, reason="Browser verification fixture")

sctx = platform_context(user_id=admin.id, aal=2)
partner_user = user("partner@browser.test")
owner = user("owner@browser.test")

partner = services.create_partner(
    sctx, code="browser-partner", display_name="Browser Partner", payout_currency="GBP", contact_email="ops@browser.test"
)
services.add_member(sctx, partner.id, email=partner_user.email, role="admin")
for cap in capabilities.CAPABILITIES:
    services.grant_capability(sctx, partner.id, capability=cap, reason="Browser verification fixture")
services.set_partner_status(sctx, partner.id, target=PartnerStatus.ACTIVE, reason="Browser verification")
rules.create_rule_version(sctx, code="default", rate_bp=1000, reason="Programme default rate")

actor = partner_actor(user_id=partner_user.id, partner_id=partner.id, aal=2)
customers.provision_customer(
    actor, organization_name="Browser Customer", owner_email=owner.email, idempotency_key=uuid.uuid4().hex
)
branding.update_brand(actor, brand_name="Browser Brand", accent_color="#0b5cad")
domains.request_domain(actor, hostname="app.browser-example.com")
referrals.create_referral_code(actor, label="Launch newsletter")
print("SEEDED", partner.id)

"""Seed a scratch database for the Partner Control Center + sign-in mode check.

Everything is created through the existing commands, as in earlier checks:

- the Super Admin (``bootstrap_superadmin``) with an authenticator, who *also*
  owns an ordinary organization - Journey D signs them in as a User;
- one sellable product (Example Desk) with an active integration, and the
  programme's default commission rate;
- ``Seed Reseller``: an active reseller whose customer's owner (the *normal
  customer user* of Journeys B and C) has activated and holds the product;
- ``Seed Brand``: white-label capabilities only, with a domain awaiting proof;
- ``Seed Affiliate``: referral and commission capabilities, one code, one
  captured and one converted referral;
- ``Seed Combined``: ONE partner holding reseller, white-label and affiliate
  capabilities (Journey E).

Scratch databases only - see README.md.
"""

import os
import uuid

import django

django.setup()

from django.core.management import call_command

from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.commissions import referrals, rules
from workeasy360.commercial.partners import customers, domains
from workeasy360.commercial.partners import services as partners
from workeasy360.commercial.partners.access import partner_actor
from workeasy360.commercial.pricing import services as pricing
from workeasy360.contracts.enums import (
    BillingInterval,
    CatalogStatus,
    FeatureKind,
    LicensingMode,
    PartnerStatus,
    PriceBookChannel,
    ProductStatus,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.identity import lifecycle
from workeasy360.platform.identity import services as identity
from workeasy360.platform.identity.models import User
from workeasy360.platform.onboarding.services import create_organization
from workeasy360.platform.tenancy.unscoped import unscoped_access

PASSWORD = "Browser-Test-Password-1!"

os.environ["PLATFORM_SUPERADMIN_EMAIL"] = "staff@browser.test"
os.environ["PLATFORM_SUPERADMIN_PASSWORD"] = PASSWORD
call_command("bootstrap_superadmin")
admin = User.objects.get(email="staff@browser.test")
sctx = platform_context(user_id=admin.id, aal=2)


def enroll_totp(user):
    """No second factor exists any more (ADR-050): nothing to enrol. The harnesses
    still read a ``STAFF_TOTP`` line, so this returns an empty one."""
    return ""


def person(email, name):
    with unscoped_access(reason="partner center browser seed", module="seed"):
        user = User.objects.create_user(email=email, password=None)
        lifecycle.complete_with_password(user, PASSWORD, full_name=name, activate=True)
        return user


staff_totp = enroll_totp(admin)
# The administrator's own, ordinary organization (Journey D: "Sign in as User").
staff_org = create_organization(
    user_id=admin.id, name="Staff Own Company", idempotency_key="pcc-staff-org"
)

with acting_scope(sctx):
    product = catalog.create_product(
        sctx,
        code="exampledesk",
        name="Example Desk",
        licensing_mode=LicensingMode.PER_ORGANIZATION,
        slug="example-desk",
        tagline="Shared inboxes, queues and service levels for small teams.",
        description="Every customer conversation in one place.",
    )
    catalog.set_product_status(
        sctx, product_id=product.id, status=ProductStatus.AVAILABLE
    )
    catalog.create_feature(
        sctx,
        product_id=product.id,
        key="exampledesk.queues.max",
        name="Queues",
        kind=FeatureKind.QUOTA,
        unit="queues",
        default_value=0,
    )
    plan = catalog.create_plan(
        sctx, product_id=product.id, code="desk_team", name="Team", caption="Team package"
    )
    catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
    version = catalog.draft_plan_version(
        sctx,
        plan_id=plan.id,
        features={"exampledesk.queues.max": 3},
        included_seats=5,
        trial_days=0,
    )
    catalog.publish_plan_version(sctx, plan_version_id=version.id)
    book = pricing.create_price_book(
        sctx,
        code="direct_gbp",
        name="Direct GBP",
        currency=CurrencyCode.GBP,
        channel=PriceBookChannel.DIRECT,
    )
    pricing.publish_price(
        sctx,
        price_book_id=book.id,
        plan_version_id=version.id,
        interval=BillingInterval.MONTHLY,
        amount_minor=1499,
    )
    from tests.factories.manifests import manifest_for
    from workeasy360.platform.ecosystem import services as ecosystem

    integration = ecosystem.register_integration(
        sctx, manifest=manifest_for("exampledesk")
    )
    ecosystem.transition(sctx, client_id=integration.client_id, to_status="active")
    rules.create_rule_version(
        sctx, code="default", rate_bp=1000, reason="Programme default rate of ten percent"
    )


RESELLER = (
    "customer.provision",
    "customer.manage",
    "subscription.sell",
    "subscription.manage",
    "pricing.view",
    "commission.earn",
    "commission.view",
    "payout.view",
    "support.access",
)
WHITE_LABEL = ("branding.manage", "domain.manage")
AFFILIATE = (
    "referral.create",
    "referral.view",
    "commission.earn",
    "commission.view",
    "payout.view",
)


def make_partner(code, name, capabilities, admin_email, admin_name, products=()):
    partner = partners.create_partner(
        sctx,
        code=code,
        display_name=name,
        legal_name=f"{name} Ltd",
        contact_name=admin_name,
        contact_email=admin_email,
        payout_currency="GBP",
    )
    for capability in dict.fromkeys(capabilities):
        partners.grant_capability(
            sctx, partner.id, capability=capability, reason="Seeded for the browser check"
        )
    for product_code in products:
        partners.grant_product(sctx, partner.id, product_code=product_code)
    user = person(admin_email, admin_name)
    partners.add_member(sctx, partner.id, email=admin_email, role="admin")
    partners.set_partner_status(
        sctx,
        partner.id,
        target=PartnerStatus.ACTIVE,
        reason="Seeded for the browser check",
    )
    return partner, user


reseller, reseller_user = make_partner(
    "seed-reseller",
    "Seed Reseller",
    RESELLER,
    "seed-reseller@browser.test",
    "Riley Reseller",
    products=("exampledesk",),
)
actor = partner_actor(user_id=reseller_user.id, partner_id=reseller.id, aal=2)
customer_org = customers.provision_customer(
    actor,
    organization_name="Customer Company",
    owner_email="customer@browser.test",
    idempotency_key=uuid.uuid4().hex,
)
customer = User.objects.get(email="customer@browser.test")
with unscoped_access(reason="partner center browser seed", module="seed"):
    lifecycle.complete_with_password(
        customer, PASSWORD, full_name="Casey Customer", activate=True
    )
customers.accept_owner_invite(
    user_id=customer.id, organization_id=customer_org.organization_id
)
customers.sell_plan(
    actor,
    customer_org.organization_id,
    plan_version_id=version.id,
    quantity=1,
    idempotency_key=uuid.uuid4().hex,
)

brand, _brand_user = make_partner(
    "seed-brand", "Seed Brand", WHITE_LABEL, "seed-brand@browser.test", "Blair Brand"
)
domains.request_domain(sctx, hostname="app.seed-brand-example.com", partner_id=brand.id)

affiliate, affiliate_user = make_partner(
    "seed-affiliate",
    "Seed Affiliate",
    AFFILIATE,
    "seed-affiliate@browser.test",
    "Ari Affiliate",
)
code = referrals.create_referral_code(
    partner_actor(user_id=affiliate_user.id, partner_id=affiliate.id, aal=2),
    label="Spring campaign",
)
converted = referrals.capture_referral(code.code)
referrals.capture_referral(code.code)  # a visitor who never signed up
referred = person("referred-owner@browser.test", "Robin Referred")
referred_org = create_organization(
    user_id=referred.id, name="Referred Company", idempotency_key="pcc-referred"
)
referrals.convert_referral(
    user_id=referred.id, token=converted, organization_id=referred_org.organization_id
)

combined, _combined_user = make_partner(
    "seed-combined",
    "Seed Combined",
    RESELLER + WHITE_LABEL + AFFILIATE,
    "seed-combined@browser.test",
    "Corey Combined",
    products=("exampledesk",),
)

print("STAFF_TOTP", staff_totp)
print("RESELLER_ID", reseller.id)
print("BRAND_ID", brand.id)
print("AFFILIATE_ID", affiliate.id)
print("COMBINED_ID", combined.id)
print("CUSTOMER_ORG", customer_org.slug)
print("STAFF_ORG", staff_org.slug)
print("REFERRAL_CODE", code.code)

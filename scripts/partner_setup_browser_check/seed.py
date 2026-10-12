"""Seed a scratch database for the complete reseller + white-label setup check.

Everything is created through the existing commands, as in earlier checks:

- the Super Admin (``bootstrap_superadmin``) with an authenticator;
- TWO sellable products (Example Desk, Example Books) so the product steps list
  more than one, the first with a configured platform trial and the programme's
  default commission rate;
- ``Seed Reseller``: an active reseller whose customer's owner has activated and
  holds Example Desk - the person Journey W signs in as to see the brand;
- ``Seed Affiliate``: an active affiliate with no white-label (Journey A enables
  white-label on this existing partner and checks no second partner appears).

The journeys themselves create the other partners through the wizards.

Scratch databases only - see README.md.
"""

import base64
import os
import uuid

import django

django.setup()

from django.core.management import call_command

from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.commissions import referrals, rules
from workeasy360.commercial.partners import customers
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
from workeasy360.platform.identity import lifecycle, mfa
from workeasy360.platform.identity import services as identity
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.unscoped import unscoped_access

PASSWORD = "Browser-Test-Password-1!"

os.environ["PLATFORM_SUPERADMIN_EMAIL"] = "staff@browser.test"
os.environ["PLATFORM_SUPERADMIN_PASSWORD"] = PASSWORD
call_command("bootstrap_superadmin")
admin = User.objects.get(email="staff@browser.test")
sctx = platform_context(user_id=admin.id, aal=2)


def enroll_totp(user):
    start = identity.begin_totp_enrollment(user.id)
    secret = start.secret_base32 + "=" * (-len(start.secret_base32) % 8)
    identity.confirm_totp_enrollment(
        user.id, start.factor_id, mfa.current_totp(base64.b32decode(secret))
    )
    return start.secret_base32


def person(email, name):
    with unscoped_access(reason="partner setup browser seed", module="seed"):
        user = User.objects.create_user(email=email, password=None)
        lifecycle.complete_with_password(user, PASSWORD, full_name=name, activate=True)
        return user


staff_totp = enroll_totp(admin)

versions = {}
with acting_scope(sctx):
    book = pricing.create_price_book(
        sctx,
        code="direct_gbp",
        name="Direct GBP",
        currency=CurrencyCode.GBP,
        channel=PriceBookChannel.DIRECT,
    )
    from tests.factories.manifests import manifest_for
    from workeasy360.platform.ecosystem import services as ecosystem

    for code, name, slug, tagline, plan_code, plan_name, price in (
        (
            "exampledesk",
            "Example Desk",
            "example-desk",
            "Shared inboxes, queues and service levels for small teams.",
            "desk_team",
            "Team",
            1499,
        ),
        (
            "examplebooks",
            "Example Books",
            "example-books",
            "Invoicing and bookkeeping for growing businesses.",
            "books_pro",
            "Professional",
            2999,
        ),
    ):
        product = catalog.create_product(
            sctx,
            code=code,
            name=name,
            licensing_mode=LicensingMode.PER_ORGANIZATION,
            slug=slug,
            tagline=tagline,
            description=f"{name} for the browser check.",
        )
        catalog.set_product_status(
            sctx, product_id=product.id, status=ProductStatus.AVAILABLE
        )
        catalog.create_feature(
            sctx,
            product_id=product.id,
            key=f"{code}.seats.max",
            name="Seats",
            kind=FeatureKind.QUOTA,
            unit="seats",
            default_value=0,
        )
        plan = catalog.create_plan(
            sctx, product_id=product.id, code=plan_code, name=plan_name, caption=f"{plan_name} package"
        )
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx,
            plan_id=plan.id,
            features={f"{code}.seats.max": 5},
            included_seats=5,
            trial_days=0,
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        pricing.publish_price(
            sctx,
            price_book_id=book.id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=price,
        )
        integration = ecosystem.register_integration(sctx, manifest=manifest_for(code))
        ecosystem.transition(sctx, client_id=integration.client_id, to_status="active")
        versions[code] = (product, version)
    # The platform's trial for Example Desk: 14 days on the Team plan.
    catalog.configure_product_trial(
        sctx,
        product_id=versions["exampledesk"][0].id,
        enabled=True,
        duration_days=14,
        seat_limit=None,
        payment_method_required=False,
        eligible_plan_codes=["desk_team"],
    )
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
        sctx, partner.id, target=PartnerStatus.ACTIVE, reason="Seeded for the browser check"
    )
    return partner, user


reseller, reseller_user = make_partner(
    "seed-reseller",
    "Seed Reseller",
    RESELLER,
    "seed-reseller@browser.test",
    "Riley Reseller",
    products=("exampledesk", "examplebooks"),
)
actor = partner_actor(user_id=reseller_user.id, partner_id=reseller.id, aal=2)
customer_org = customers.provision_customer(
    actor,
    organization_name="Customer Company",
    owner_email="customer@browser.test",
    idempotency_key=uuid.uuid4().hex,
)
customer = User.objects.get(email="customer@browser.test")
with unscoped_access(reason="partner setup browser seed", module="seed"):
    lifecycle.complete_with_password(
        customer, PASSWORD, full_name="Casey Customer", activate=True
    )
customers.accept_owner_invite(
    user_id=customer.id, organization_id=customer_org.organization_id
)

affiliate, affiliate_user = make_partner(
    "seed-affiliate",
    "Seed Affiliate",
    AFFILIATE,
    "seed-affiliate@browser.test",
    "Ari Affiliate",
)
referrals.create_referral_code(
    partner_actor(user_id=affiliate_user.id, partner_id=affiliate.id, aal=2),
    label="Spring campaign",
)

print("STAFF_TOTP", staff_totp)
print("RESELLER_ID", reseller.id)
print("AFFILIATE_ID", affiliate.id)
print("CUSTOMER_ORG", customer_org.slug)

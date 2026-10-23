"""Seed a scratch database for the admin-created partner check (ADR-047).

Everything goes through the existing commands, as in earlier checks:

- the Super Admin (``bootstrap_superadmin``) with an authenticator;
- two sellable products (shown as Easy CRM and EasyBook) with a plan and a price
  each, and the programme's default 10% commission rule;
- a plain customer account (``existing.customer@browser.test``) that belongs to no
  partner: the journey that links an existing account uses it, and checks that its
  password is not changed.

The journeys CREATE the partners themselves, through the Add Partner screen -
nothing about a partner or its administrator is seeded. Scratch databases only.
"""

import base64
import os

import django

django.setup()

from django.core.management import call_command

from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.commissions import rules
from workeasy360.commercial.pricing import services as pricing
from workeasy360.contracts.enums import (
    BillingInterval,
    CatalogStatus,
    FeatureKind,
    LicensingMode,
    PriceBookChannel,
    ProductStatus,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.identity import lifecycle, mfa
from workeasy360.platform.identity import services as identity
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.unscoped import unscoped_access

# Fresh for every run (CHECK_PASSWORD); nothing fixed is committed.
PASSWORD = os.environ["CHECK_PASSWORD"]

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


staff_totp = enroll_totp(admin)

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
        ("exampledesk", "Easy CRM", "easy-crm", "Customers, deals and follow-ups.", "crm_standard", "Standard", 1499),
        ("examplebooks", "EasyBook", "easybook", "Invoicing and bookkeeping for growing businesses.", "book_standard", "Standard", 2999),
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
        catalog.set_product_status(sctx, product_id=product.id, status=ProductStatus.AVAILABLE)
        catalog.create_feature(
            sctx, product_id=product.id, key=f"{code}.seats.max", name="Seats",
            kind=FeatureKind.QUOTA, unit="seats", default_value=0,
        )
        plan = catalog.create_plan(sctx, product_id=product.id, code=plan_code, name=plan_name, caption=f"{plan_name} package")
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx, plan_id=plan.id, features={f"{code}.seats.max": 5}, included_seats=5, trial_days=0
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        pricing.publish_price(
            sctx, price_book_id=book.id, plan_version_id=version.id,
            interval=BillingInterval.MONTHLY, amount_minor=price,
        )
        integration = ecosystem.register_integration(sctx, manifest=manifest_for(code))
        ecosystem.transition(sctx, client_id=integration.client_id, to_status="active")
    rules.create_rule_version(sctx, code="default", rate_bp=1000, reason="Programme default rate of ten percent")

# An account that already exists and belongs to no partner.
with unscoped_access(reason="partner experience browser seed", module="seed"):
    existing = User.objects.create_user(email="existing.customer@browser.test", password=None)
    lifecycle.complete_with_password(existing, PASSWORD, full_name="Existing Customer", activate=True)

print("STAFF_TOTP", staff_totp)

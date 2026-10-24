"""Seed a scratch database for the partner upgrade check (ADR-049).

Everything goes through the existing commands, as in earlier checks:

- the Super Admin (``bootstrap_superadmin``) with an authenticator;
- two sellable products (shown as Easy CRM and EasyBook) with a plan and a price
  each, and the programme's default 10% commission rule;
- five partners, each created by the Add Partner command with an administrator
  email and password, its profile completed and activated as staff do:

    affiliate.one   an Affiliate with a referral code, one captured and converted
                    referral and a ledger row (the history Test D must find again);
    affiliate.two   an Affiliate for the direct White-label upgrade (Test B);
    affiliate.three an Affiliate that tries URLs and API calls it may not (Test C);
    affiliate.four  an Affiliate with the same history, for the White-label history
                    check (Test E);
    reseller.one    a Reseller for Reseller -> White-label (Test F).

The partner administrators' password is ``CHECK_PASSWORD`` (fresh per run, never
committed); the journeys log in with it, upgrade the SAME accounts, and log in with
it again. Scratch databases only.
"""

import os

import django

django.setup()

from django.core.management import call_command

from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.commissions import ledger, referrals, rules
from workeasy360.commercial.controlplane import partner_onboarding
from workeasy360.commercial.partners import profile as partner_profile
from workeasy360.commercial.partners import services as partner_services
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
from workeasy360.platform.identity import services as identity
from workeasy360.platform.identity.models import User

# Fresh for every run (CHECK_PASSWORD); nothing fixed is committed.
PASSWORD = os.environ["CHECK_PASSWORD"]

os.environ["PLATFORM_SUPERADMIN_EMAIL"] = "staff@browser.test"
os.environ["PLATFORM_SUPERADMIN_PASSWORD"] = PASSWORD
call_command("bootstrap_superadmin")
admin = User.objects.get(email="staff@browser.test")
sctx = platform_context(user_id=admin.id, aal=2)


def enroll_totp(user):
    """No second factor exists any more (ADR-050): nothing to enrol. The harnesses
    still read a ``STAFF_TOTP`` line, so this returns an empty one."""
    return ""


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


def make_partner(experience, name, email, *, products=(), history=False):
    """Add Partner, then Complete Your Profile and activate - the staff path."""
    created = partner_onboarding.create(
        sctx,
        {
            "experience": experience,
            "partner": {"display_name": name, "country": "GB"},
            "administrator": {
                "email": email,
                "mode": "create",
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
        },
    )
    partner_id = created.partner_id
    version = partner_profile.profile_view(partner_id)["version"]
    partner_profile.update_profile(
        sctx,
        partner_id,
        expected_version=version,
        data={
            "business": {"business_email": f"hello@{email.split('@')[1]}", "country": "GB"},
            "contacts": {"primary": {"name": "Pat Owner", "email": email}},
        },
    )
    for code in products:
        partner_services.grant_product(sctx, partner_id, product_code=code)
    code = None
    if experience == "affiliate":
        code = referrals.create_referral_code_for_partner(sctx, partner_id, label="Launch")
    row = partner_services.set_partner_status(
        sctx,
        partner_id,
        target=PartnerStatus.ACTIVE,
        reason="Profile complete, activating for the check",
        expected_version=None,
    )
    if history and code is not None:
        # A referral can only be captured by an active partner's code.
        assert referrals.capture_referral(code.code)
        ledger.record_adjustment(
            sctx,
            partner_id,
            amount_minor=2500,
            currency=created.payout_currency,
            reason="History the upgrade must never lose",
            idempotency_key=f"seed-history-{partner_id}",
        )
    return row


make_partner("affiliate", "Alpha Affiliates", "affiliate.one@browser.test", history=True)
make_partner("affiliate", "Bravo Affiliates", "affiliate.two@browser.test")
make_partner("affiliate", "Charlie Affiliates", "affiliate.three@browser.test")
make_partner("affiliate", "Delta Affiliates", "affiliate.four@browser.test", history=True)
make_partner("reseller", "Echo Resellers", "reseller.one@browser.test", products=("exampledesk",))

print("STAFF_TOTP", staff_totp)

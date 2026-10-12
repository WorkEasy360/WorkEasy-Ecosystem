"""Seed a scratch database for the multi-portal + four-product trial check.

Everything is created through the existing commands and services:

- the Super Admin (``bootstrap_superadmin``) with an authenticator;
- the four real products from ``infra/ecosystem/products.json`` through
  ``register_products --activate-integrations`` (their real launch URLs; the
  7-day trial durations recorded switched off, as the command does with no
  plan named);
- one TEST plan and monthly TEST price per product (the platform invents none:
  these exist only in this scratch database), and then each product's 7-day
  trial switched on for that plan through the Product Manager's own service -
  what staff do once a plan exists;
- a customer organization whose owner has a billing account (GBP);
- ``Seed Reseller``: an active reseller granted Easy CRM, EasyBook and Easy
  Help Desk - and not Easy Project;
- a staff member's organization membership is NOT created: the Super Admin has
  none, which is what the portal-switching journey checks.

Scratch databases only - see README.md.
"""

import base64
import json
import os
import uuid
from pathlib import Path

import django

django.setup()

from django.core.management import call_command

from workeasy360.commercial.billing import services as billing
from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.partners import services as partners
from workeasy360.commercial.pricing import services as pricing
from workeasy360.contracts.enums import (
    BillingInterval,
    CatalogStatus,
    PartnerStatus,
    PriceBookChannel,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.identity import lifecycle, mfa
from workeasy360.platform.identity import services as identity
from workeasy360.platform.identity.models import User
from workeasy360.platform.onboarding.services import create_organization
from workeasy360.platform.tenancy.unscoped import unscoped_access

PASSWORD = "Browser-Test-Password-1!"
ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = json.loads((ROOT / "infra" / "ecosystem" / "products.json").read_text(encoding="utf-8"))["products"]
PRICES = {"easy_crm": 2900, "easybook": 1900, "easy_project": 2400, "easy_helpdesk": 1500}

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
    with unscoped_access(reason="portals browser seed", module="seed"):
        user = User.objects.create_user(email=email, password=None)
        lifecycle.complete_with_password(user, PASSWORD, full_name=name, activate=True)
        return user


staff_totp = enroll_totp(admin)

# The four products, exactly as an operator registers them.
call_command("register_products", actor=admin.email, activate_integrations=True)

with acting_scope(sctx):
    book = pricing.create_price_book(
        sctx,
        code="direct_gbp",
        name="Direct GBP",
        currency=CurrencyCode.GBP,
        channel=PriceBookChannel.DIRECT,
    )
    by_code = {p["code"]: p for p in catalog_selectors.visible_products()}
    for entry in PRODUCTS:
        code = entry["code"]
        product_id = by_code[code]["id"]
        plan = catalog.create_plan(
            sctx, product_id=product_id, code=f"{code}_team", name="Team", caption="TEST plan"
        )
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx,
            plan_id=plan.id,
            features={feature["key"]: True for feature in entry["features"]},
            included_seats=5,
            trial_days=0,
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        pricing.publish_price(
            sctx,
            price_book_id=book.id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=PRICES[code],
        )
        # What staff do in the Product Manager once a plan exists: switch the
        # recorded 7-day trial on, for that plan.
        offer = catalog_selectors.trial_offer(code)
        catalog.configure_product_trial(
            sctx,
            product_id=product_id,
            enabled=True,
            duration_days=offer.duration_days,
            eligible_plan_codes=[f"{code}_team"],
        )

    # The offline test payment provider, configured by staff exactly as a
    # real one is (adapter "fake": PAYMENTS_FAKE_PROVIDER_ENABLED is on in
    # this scratch backend's settings only).
    from workeasy360.commercial.payments import staff as payments_staff

    provider = payments_staff.create_provider(
        sctx,
        {
            "key": "test-gbp",
            "adapter": "fake",
            "display_name": "Test provider",
            "credential_ref": "TEST_GBP",
            "webhook_secret_ref": "TEST_GBP",
            "currencies": ["GBP"],
        },
    )
    payments_staff.update_provider(sctx, "test-gbp", {"status": "enabled", "version": provider["version"]})

# The customer.
owner = person("owner@browser.test", "Olive Owner")
created = create_organization(user_id=owner.id, name="Customer Company")
from workeasy360.contracts.enums import OrganizationStatus  # noqa: E402
from workeasy360.contracts.values.tenant import TenantContext  # noqa: E402
from workeasy360.platform.tenancy.context import tenant_context  # noqa: E402
from workeasy360.platform.tenancy.db import tenant_db_scope  # noqa: E402

owner_ctx = TenantContext(
    organization_id=created.organization_id,
    membership_id=created.membership_id,
    user_id=owner.id,
    organization_status=OrganizationStatus.ACTIVE,
    aal=2,
)
with tenant_context(owner_ctx), tenant_db_scope(owner_ctx):
    billing.open_billing_account(
        owner_ctx, currency=CurrencyCode.GBP, billing_email="billing@browser.test"
    )

# The reseller: three of the four products.
RESELLER = (
    "customer.provision",
    "customer.manage",
    "subscription.sell",
    "subscription.manage",
    "pricing.view",
    "commission.earn",
    "commission.view",
    "payout.view",
)
reseller = partners.create_partner(
    sctx,
    code="seed-reseller",
    display_name="Seed Reseller",
    legal_name="Seed Reseller Ltd",
    contact_name="Riley Reseller",
    contact_email="partner@browser.test",
    payout_currency="GBP",
)
for capability in RESELLER:
    partners.grant_capability(sctx, reseller.id, capability=capability, reason="Seeded for the browser check")
for code in ("easy_crm", "easybook", "easy_helpdesk"):
    partners.grant_product(sctx, reseller.id, product_code=code)
person("partner@browser.test", "Riley Reseller")
partners.add_member(sctx, reseller.id, email="partner@browser.test", role="admin")
partners.set_partner_status(
    sctx, reseller.id, target=PartnerStatus.ACTIVE, reason="Seeded for the browser check"
)

print("STAFF_TOTP", staff_totp)
print("CUSTOMER_ORG", created.slug)
print("PARTNER_ID", reseller.id)
print("SEED_ID", uuid.uuid4().hex[:8])

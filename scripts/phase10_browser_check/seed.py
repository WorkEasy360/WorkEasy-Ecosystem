"""Seed a scratch database for the Phase 10 real-browser check.

- The Super Admin, created the way a deployment creates it (``bootstrap_superadmin``),
  with an authenticator so the check can step up for dangerous actions.
- One sellable product (Example Desk, two plans, direct GBP prices) whose
  integration is registered and active, so a customer who holds it can open it,
  and the programme's default commission rate.
- ``Seed Reseller``: an active reseller with an administrator, one customer
  whose owner has *not* activated yet and one whose owner has - so the console's
  tables, cards and Owner column have real rows to show.
- ``Seed Brand``: an active partner holding only branding and domain capability,
  with an administrator who has an authenticator - the white-label journey's
  partner.

Scratch databases only - see README.md.
"""

import base64
import os
import uuid

import django

django.setup()

from django.core.management import call_command
from django.utils import timezone

from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.commissions import rules
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

# --- the Super Admin, from "deployment secrets" ----------------------------------------
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
    """An active identity with a password (a partner's administrator here has
    signed up already; the wizard's own invitation path is what the check drives)."""
    with unscoped_access(reason="phase 10 browser seed", module="seed"):
        user = User.objects.create_user(email=email, password=None)
        lifecycle.complete_with_password(user, PASSWORD, full_name=name, activate=True)
        return user


staff_totp = enroll_totp(admin)

# --- a product to sell, with prices, an integration and a commission rate ----------------
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
    catalog.set_product_status(sctx, product_id=product.id, status=ProductStatus.AVAILABLE)
    catalog.create_feature(
        sctx,
        product_id=product.id,
        key="exampledesk.queues.max",
        name="Queues",
        kind=FeatureKind.QUOTA,
        unit="queues",
        default_value=0,
    )
    versions = {}
    for code, name, queues in (("desk_team", "Team", 3), ("desk_business", "Business", 25)):
        plan = catalog.create_plan(
            sctx, product_id=product.id, code=code, name=name, caption=f"{name} package"
        )
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx,
            plan_id=plan.id,
            features={"exampledesk.queues.max": queues},
            included_seats=5,
            trial_days=0,
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        versions[code] = version
    book = pricing.create_price_book(
        sctx,
        code="direct_gbp",
        name="Direct GBP",
        currency=CurrencyCode.GBP,
        channel=PriceBookChannel.DIRECT,
    )
    for code, monthly in (("desk_team", 1499), ("desk_business", 4999)):
        pricing.publish_price(
            sctx,
            price_book_id=book.id,
            plan_version_id=versions[code].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=monthly,
        )
    from tests.factories.manifests import manifest_for
    from workeasy360.platform.ecosystem import services as ecosystem

    integration = ecosystem.register_integration(sctx, manifest=manifest_for("exampledesk"))
    ecosystem.transition(sctx, client_id=integration.client_id, to_status="active")
    rules.create_rule_version(
        sctx, code="default", rate_bp=1000, reason="Programme default rate of ten percent"
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
    for capability in capabilities:
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


# --- Seed Reseller: an active reseller with two customers -----------------------------------
reseller, reseller_user = make_partner(
    "seed-reseller",
    "Seed Reseller",
    (
        "customer.provision",
        "customer.manage",
        "subscription.sell",
        "subscription.manage",
        "pricing.view",
        "commission.earn",
        "commission.view",
        "payout.view",
        "support.access",
    ),
    "seed-reseller@browser.test",
    "Riley Reseller",
    products=("exampledesk",),
)
actor = partner_actor(user_id=reseller_user.id, partner_id=reseller.id, aal=2)
pending = customers.provision_customer(
    actor,
    organization_name="Pending Owner Ltd",
    owner_email="pending-owner@browser.test",
    idempotency_key=uuid.uuid4().hex,
)
active_org = customers.provision_customer(
    actor,
    organization_name="Activated Owner Ltd",
    owner_email="activated-owner@browser.test",
    idempotency_key=uuid.uuid4().hex,
)
activated_owner = User.objects.get(email="activated-owner@browser.test")
with unscoped_access(reason="phase 10 browser seed", module="seed"):
    lifecycle.complete_with_password(activated_owner, PASSWORD, full_name="Avery Activated", activate=True)
customers.accept_owner_invite(user_id=activated_owner.id, organization_id=active_org.organization_id)
customers.sell_plan(
    actor,
    active_org.organization_id,
    plan_version_id=versions["desk_team"].id,
    quantity=1,
    idempotency_key=uuid.uuid4().hex,
)

# --- Seed Brand: a white-label partner whose administrator has an authenticator --------------
brand, brand_user = make_partner(
    "seed-brand",
    "Seed Brand",
    ("branding.manage", "domain.manage"),
    "seed-brand@browser.test",
    "Blair Brand",
)
brand_totp = enroll_totp(brand_user)

print("SEEDED", reseller.id, brand.id)
print("STAFF_TOTP", staff_totp)
print("BRAND_TOTP", brand_totp)
print("PLAN_TEAM", versions["desk_team"].id)
print("PENDING_ORG", pending.slug, pending.organization_id)
print("ACTIVE_ORG", active_org.slug, active_org.organization_id)
print("NOW", timezone.now().isoformat())

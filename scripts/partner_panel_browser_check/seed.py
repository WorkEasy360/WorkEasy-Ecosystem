"""Seed a scratch database for the partner business panel check.

Everything is created through the existing commands, as in earlier checks:

- the Super Admin (``bootstrap_superadmin``) with an authenticator;
- THREE sellable products (Example Desk, Example Books, Example HR) - the
  partner is granted the first two, so the third shows as "available, not
  granted" - with the programme's default 10% commission rule;
- ``Panel Partner``: an active partner holding EVERY capability (reseller,
  white-label, affiliate, sub-partners), an owner who can sign in, one customer
  that has bought Example Desk (so the ledger, the dashboard and the export have
  real rows) and a second, activated customer;
- ``Rival Partner``: another active reseller with its own customer, to prove
  nothing crosses;
- a plain customer who is not a member of any partner;
- the development demo partner, made by the real ``create_demo_partner`` code,
  whose temporary password is written to a file in the SCRATCH directory only
  (``DEMO_PASSWORD_FILE``) so the check can sign in once and change it.

Scratch databases only - see README.md.
"""

import base64
import os
import uuid

import django

django.setup()

from django.core.management import call_command

from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.commissions import rules
from workeasy360.commercial.controlplane import demo_partner
from workeasy360.commercial.partners import capabilities, customers
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

# A throwaway password for this scratch database, made fresh for every run by
# the runner (CHECK_PASSWORD); nothing fixed is committed.
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


def person(email, name):
    with unscoped_access(reason="partner panel browser seed", module="seed"):
        user = User.objects.create_user(email=email, password=None)
        lifecycle.complete_with_password(user, PASSWORD, full_name=name, activate=True)
        return user


def second_factor(user):
    """The partner owner answers a second factor in the dangerous journeys."""
    return enroll_totp(user)


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
        ("exampledesk", "Example Desk", "example-desk", "Shared inboxes, queues and service levels.", "desk_team", "Team", 1499),
        ("examplebooks", "Example Books", "example-books", "Invoicing and bookkeeping for growing businesses.", "books_pro", "Professional", 2999),
        ("examplehr", "Example HR", "example-hr", "People records and leave for small teams.", "hr_basic", "Basic", 999),
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
        versions[code] = (product, version)
    catalog.configure_product_trial(
        sctx, product_id=versions["exampledesk"][0].id, enabled=True, duration_days=14,
        seat_limit=None, payment_method_required=False, eligible_plan_codes=["desk_team"],
    )
    rules.create_rule_version(sctx, code="default", rate_bp=1000, reason="Programme default rate of ten percent")

EVERYTHING = tuple(sorted(capabilities.CAPABILITIES))


def make_partner(code, name, caps, email, person_name, products=()):
    partner = partners.create_partner(
        sctx, code=code, display_name=name, legal_name=f"{name} Ltd",
        contact_name=person_name, contact_email=email, payout_currency="GBP",
    )
    for capability in dict.fromkeys(caps):
        partners.grant_capability(sctx, partner.id, capability=capability, reason="Seeded for the browser check")
    for product_code in products:
        partners.grant_product(sctx, partner.id, product_code=product_code)
    user = person(email, person_name)
    partners.add_member(sctx, partner.id, email=email, role="owner")
    partners.set_partner_status(sctx, partner.id, target=PartnerStatus.ACTIVE, reason="Seeded for the browser check")
    return partner, user


def sell_to(actor, name, owner_email, owner_name):
    result = customers.provision_customer(
        actor, organization_name=name, owner_email=owner_email, idempotency_key=uuid.uuid4().hex
    )
    owner = User.objects.get(email=owner_email)
    with unscoped_access(reason="partner panel browser seed", module="seed"):
        lifecycle.complete_with_password(owner, PASSWORD, full_name=owner_name, activate=True)
    customers.accept_owner_invite(user_id=owner.id, organization_id=result.organization_id)
    return result


panel, panel_user = make_partner(
    "panel-partner", "Panel Partner", EVERYTHING, "owner@panel.test", "Olivia Owner",
    products=("exampledesk", "examplebooks"),
)
panel_totp = second_factor(panel_user)
actor = partner_actor(user_id=panel_user.id, partner_id=panel.id, aal=2)
first = sell_to(actor, "Zen Retail", "zen@panel.test", "Zed Zen")
customers.sell_plan(
    actor, first.organization_id,
    plan_version_id=versions["exampledesk"][1].id, quantity=1, idempotency_key=uuid.uuid4().hex,
)
second = sell_to(actor, "Orchid Studio", "orchid@panel.test", "Ola Orchid")

rival, rival_user = make_partner(
    "rival-partner", "Rival Partner", ("customer.provision", "subscription.sell", "pricing.view", "commission.earn", "commission.view"),
    "owner@rival.test", "Rae Rival", products=("exampledesk",),
)
rival_actor = partner_actor(user_id=rival_user.id, partner_id=rival.id, aal=2)
sell_to(rival_actor, "Rival Customer Ltd", "rivalcust@panel.test", "Rin Rival")

person("customer@panel.test", "Plain Customer")  # not a member of any partner

# A finance member of the panel partner: sees money, not customers.
finance = person("finance@panel.test", "Fran Finance")
partners.add_member(sctx, panel.id, email="finance@panel.test", role="finance")

# The development demo partner, by the real command's code. Its temporary
# password goes to a file in the scratch directory, never to the repository.
demo = demo_partner.create_demo_partner(currency="GBP")
password_file = os.environ.get("DEMO_PASSWORD_FILE")
if password_file and demo.temporary_password:
    with open(password_file, "w", encoding="utf-8") as handle:
        handle.write(demo.temporary_password)

print("STAFF_TOTP", staff_totp)
print("PANEL_TOTP", panel_totp)
print("PANEL_ID", panel.id)
print("RIVAL_ID", rival.id)
print("DEMO_ID", demo.partner_id)
print("CUSTOMER_ORG", first.slug)

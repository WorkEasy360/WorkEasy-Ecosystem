"""Seed a scratch database for the Phase 9 real-browser check.

- The Super Admin, created the way a deployment creates it: ``bootstrap_superadmin``
  with the two environment variables.
- A public catalog: a listed product with a trial plan and a paid plan
  (GBP), a coming-soon product, platform settings (catalog currency, support
  address) and the development test payment provider.
- An organization whose owner holds an order awaiting payment, with a member
  and an administrator - for the landing rules and the orders page.
- A second organization that already bought through a pay-first order, so
  the staff subscriptions, entitlements and orders pages have rows.

Scratch databases only - see README.md.
"""

import os
import uuid

import django

django.setup()

from django.core.management import call_command
from django.utils import timezone

from workeasy360.commercial.billing import services as billing
from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.payments import orders
from workeasy360.commercial.payments import staff as payments_staff
from workeasy360.commercial.pricing import services as pricing
from workeasy360.contracts.enums import (
    BillingInterval,
    CatalogStatus,
    FeatureKind,
    LicensingMode,
    PriceBookChannel,
    ProductListing,
    ProductStatus,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.administration import site
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.identity import services as identity
from workeasy360.platform.identity.models import User
from workeasy360.platform.onboarding.services import create_organization
from workeasy360.platform.organizations import projection
from workeasy360.platform.organizations.models import Membership, Organization
from workeasy360.platform.tenancy.db import organization_db_scope
from workeasy360.platform.tenancy.unscoped import unscoped_access

PASSWORD = "Browser-Test-Password-1!"

# --- the Super Admin, from "deployment secrets" ------------------------------------
os.environ["PLATFORM_SUPERADMIN_EMAIL"] = "staff@browser.test"
os.environ["PLATFORM_SUPERADMIN_PASSWORD"] = PASSWORD
call_command("bootstrap_superadmin")
admin = User.objects.get(email="staff@browser.test")
sctx = platform_context(user_id=admin.id, aal=2)

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
    catalog.create_product(
        sctx,
        code="voicedesk",
        name="Voice Desk",
        slug="voice-desk",
        tagline="Calls next to your tickets.",
        listing=ProductListing.COMING_SOON,
    )
    versions = {}
    for code, name, queues, trial in (
        ("desk_team", "Team", 3, 14),
        ("desk_business", "Business", 25, 0),
    ):
        plan = catalog.create_plan(
            sctx, product_id=product.id, code=code, name=name, caption=f"{name} package"
        )
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx,
            plan_id=plan.id,
            features={"exampledesk.queues.max": queues},
            included_seats=5,
            trial_days=trial,
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
    for code, monthly, annual in (
        ("desk_team", 1499, 14990),
        ("desk_business", 4999, 49990),
    ):
        for interval, amount in (
            (BillingInterval.MONTHLY, monthly),
            (BillingInterval.ANNUAL, annual),
        ):
            pricing.publish_price(
                sctx,
                price_book_id=book.id,
                plan_version_id=versions[code].id,
                interval=interval,
                amount_minor=amount,
            )
    # The product registers its integration (manifest, then activation) - what
    # makes it launchable, and so what My products shows as held.
    from tests.factories.manifests import manifest_for
    from workeasy360.platform.ecosystem import services as ecosystem

    integration = ecosystem.register_integration(sctx, manifest=manifest_for("exampledesk"))
    ecosystem.transition(sctx, client_id=integration.client_id, to_status="active")
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
    payments_staff.update_provider(
        sctx, "test-gbp", {"status": "enabled", "version": provider["version"]}
    )
    site.update_settings(
        sctx,
        {
            "signup_enabled": True,
            "support_email": "support@browser.test",
            "sales_email": "sales@browser.test",
            "public_catalog_currency": "GBP",
        },
        expected_version=0,
    )


def user(email, name):
    with unscoped_access(reason="phase 9 browser seed", module="seed"):
        return User.objects.create_user(
            email=email,
            password=PASSWORD,
            full_name=name,
            status="active",
            email_verified_at=timezone.now(),
        )


def organization(owner, name, key):
    created = create_organization(user_id=owner.id, name=name, idempotency_key=key)
    with unscoped_access(reason="phase 9 browser seed", module="seed"):
        org = Organization.objects.get(id=created.organization_id)
        with organization_db_scope(org.id):
            membership = Membership.unscoped.get(organization_id=org.id, user_id=owner.id)
    ctx = TenantContext(
        organization_id=org.id, membership_id=membership.id, user_id=owner.id, aal=2
    )
    return org, ctx


def add_member(org, email, name, role):
    person = user(email, name)
    with (
        unscoped_access(reason="phase 9 browser seed", module="seed"),
        organization_db_scope(org.id),
    ):
        member = Membership(
            organization_id=org.id,
            user_id=person.id,
            status="active",
            member_type="internal",
        )
        member.save()
        with acting_scope(
            TenantContext(
                organization_id=org.id, membership_id=member.id, user_id=person.id
            )
        ):
            authz.replace_system_role(
                organization_id=org.id, membership_id=member.id, role_key=role
            )
        projection.sync_access_for_membership(
            user_id=person.id, membership=member, organization=org
        )
    authz.bump_permission_version(org.id)


# --- an organization with an order awaiting payment ---------------------------------
owner = user("owner@browser.test", "Olivia Owner")
org, octx = organization(owner, "Browser Organization", "p9-browser-owner")
add_member(org, "admin@browser.test", "Alex Admin", "admin")
add_member(org, "member@browser.test", "Mo Member", "member")
with acting_scope(octx):
    billing.open_billing_account(
        octx, currency=CurrencyCode.GBP, billing_email="billing@browser.test"
    )
    orders.place(
        octx,
        idempotency_key=uuid.uuid4().hex,
        plan_version_id=str(versions["desk_team"].id),
    )

# --- a customer that already bought (trial, fulfilled at once) ------------------------
customer = user("customer@browser.test", "Casey Customer")
corg, cctx = organization(customer, "Customer Company", "p9-browser-customer")
with acting_scope(cctx):
    billing.open_billing_account(cctx, currency=CurrencyCode.GBP)
    orders.place(
        cctx,
        idempotency_key=uuid.uuid4().hex,
        plan_version_id=str(versions["desk_team"].id),
        with_trial=True,
    )


print("SEEDED", org.slug, corg.slug)
print("PLAN_BUSINESS", versions["desk_business"].id)
print("OWNER_TOTP", "")

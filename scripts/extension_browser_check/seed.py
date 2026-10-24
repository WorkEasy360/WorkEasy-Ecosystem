"""Seed a scratch database for the Phase 8 extension's real-browser check.

A staff member holding every staff permission (Super Admin console), an
organization owner whose organization has members of every role, sign-in
activity across the last week, a pending invitation, a small catalog, a
billing account, a subscription with an open invoice and the development test
payment provider - so every console page renders rows, not empty states.
Scratch databases only - see README.md.
"""

import uuid
from datetime import timedelta

import django

django.setup()

from django.utils import timezone

from workeasy360.commercial.billing import services as billing
from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.payments import invoicing
from workeasy360.commercial.payments import staff as payments_staff
from workeasy360.commercial.pricing import services as pricing
from workeasy360.commercial.subscriptions import services as subscriptions
from workeasy360.contracts.enums import (
    BillingInterval,
    BundleKind,
    CatalogStatus,
    FeatureKind,
    LicensingMode,
    PriceBookChannel,
    ProductStatus,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.administration import invitations
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz import staff
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.authz.permissions import STAFF_PERMISSIONS
from workeasy360.platform.identity import services as identity
from workeasy360.platform.identity.models import User
from workeasy360.platform.onboarding.services import create_organization
from workeasy360.platform.organizations import projection
from workeasy360.platform.organizations.models import Membership, MemberSignIn, Organization
from workeasy360.platform.tenancy.db import organization_db_scope
from workeasy360.platform.tenancy.unscoped import unscoped_access

PASSWORD = "Browser-Test-Password-1!"
REASON = "Extension browser verification fixture"


def user(email, name, **extra):
    with unscoped_access(reason="extension browser seed", module="seed"):
        return User.objects.create_user(
            email=email,
            password=PASSWORD,
            full_name=name,
            status="active",
            email_verified_at=timezone.now(),
            **extra,
        )


# --- Super Admin -----------------------------------------------------------------
root = user("root@browser.test", "Root", is_staff=True)
admin = user("staff@browser.test", "Sam Staff", is_staff=True)
for permission in STAFF_PERMISSIONS:
    staff.create_grant(user_id=admin.id, permission=str(permission.key), granted_by_id=root.id, reason=REASON)
sctx = platform_context(user_id=admin.id, aal=2)

established = timezone.now() - timedelta(days=30)
with acting_scope(sctx):
    product = catalog.create_product(sctx, code="exampledesk", name="Example Desk", licensing_mode=LicensingMode.PER_SEAT)
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
    for code, name, queues, seats, trial in (("desk_team", "Team", 3, 5, 14), ("desk_business", "Business", 25, 20, 0)):
        plan = catalog.create_plan(sctx, product_id=product.id, code=code, name=name, caption=f"{name} package")
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx,
            plan_id=plan.id,
            features={"exampledesk.queues.max": queues},
            included_seats=seats,
            trial_days=trial,
            effective_from=established,
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        versions[code] = version
    book = pricing.create_price_book(
        sctx,
        code="direct_gbp",
        name="Direct GBP",
        currency=CurrencyCode.GBP,
        channel=PriceBookChannel.DIRECT,
        valid_from=established,
    )
    for code, monthly, annual in (("desk_team", 1499, 14990), ("desk_business", 4999, 49990)):
        for interval, amount in ((BillingInterval.MONTHLY, monthly), (BillingInterval.ANNUAL, annual)):
            pricing.publish_price(
                sctx,
                price_book_id=book.id,
                plan_version_id=versions[code].id,
                interval=interval,
                amount_minor=amount,
                valid_from=established,
            )
    bundle = catalog.create_bundle(sctx, code="desk_suite", name="Desk Suite", kind=BundleKind.PREDEFINED)
    bundle_version = catalog.draft_bundle_version(
        sctx,
        bundle_id=bundle.id,
        items=[{"plan_version_id": versions["desk_business"].id}],
        effective_from=established,
    )
    catalog.publish_bundle_version(sctx, bundle_version_id=bundle_version.id)
    catalog.set_bundle_status(sctx, bundle_id=bundle.id, status=CatalogStatus.AVAILABLE)
    pricing.publish_price(
        sctx,
        price_book_id=book.id,
        bundle_version_id=bundle_version.id,
        interval=BillingInterval.MONTHLY,
        amount_minor=4499,
        valid_from=established,
    )
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

# --- the organization ----------------------------------------------------------------
owner = user("owner@browser.test", "Olivia Owner")
created = create_organization(user_id=owner.id, name="Browser Organization", idempotency_key="extension-browser")
org_id = created.organization_id
with unscoped_access(reason="extension browser seed", module="seed"):
    organization = Organization.objects.get(id=org_id)
    with organization_db_scope(org_id):
        owner_membership = Membership.unscoped.get(organization_id=org_id, user_id=owner.id)

people = [
    ("admin@browser.test", "Alex Admin", "admin"),
    ("billing@browser.test", "Bea Billing", "billing_manager"),
    ("member1@browser.test", "Mo Member", "member"),
    ("member2@browser.test", "Nia Member", "member"),
    ("viewer@browser.test", "Vic Viewer", "readonly"),
]
memberships = [owner_membership]
for email, name, role in people:
    person = user(email, name)
    with unscoped_access(reason="extension browser seed", module="seed"), organization_db_scope(org_id):
        member = Membership(organization_id=org_id, user_id=person.id, status="active", member_type="internal")
        member.save()
        with acting_scope(TenantContext(organization_id=org_id, membership_id=member.id, user_id=person.id)):
            authz.replace_system_role(organization_id=org_id, membership_id=member.id, role_key=role)
        projection.sync_access_for_membership(user_id=person.id, membership=member, organization=organization)
    memberships.append(member)
authz.bump_permission_version(org_id)

# A week of sign-ins into the organization, so the dashboard's chart and the
# most-active table have rows.
now = timezone.now()
with unscoped_access(reason="extension browser seed", module="seed"), organization_db_scope(org_id):
    for day in range(7):
        for index, member in enumerate(memberships):
            if (day + index) % 3 == 0:
                continue
            MemberSignIn.unscoped.create(
                organization_id=org_id,
                membership=member,
                user_id=member.user_id,
                session_ref=uuid.uuid4(),
                occurred_at=now - timedelta(days=day, hours=index),
                ip_prefix="203.0.113.0/24",
                device_label="Chrome on Windows",
            )

octx = TenantContext(organization_id=org_id, membership_id=owner_membership.id, user_id=owner.id, aal=2)
with acting_scope(octx):
    invitations.invite(octx, email="new.person@browser.test", role_key="member")
    billing.open_billing_account(octx, currency=CurrencyCode.GBP, billing_email="billing@browser.test")
    subscriptions.purchase_plan(
        octx, plan_version_id=versions["desk_team"].id, idempotency_key=uuid.uuid4().hex, quantity=3
    )
invoicing.ensure_invoices(org_id)

print("SEEDED", created.slug, org_id)
print("OWNER_TOTP", "")

"""Acceptance data for the User Admin Panel browser check (2026-11-11), written by
the working tree's own services.

- Easy CRM gets two TEST plans priced in INR (Basic 999.00, Advanced 1,999.00 a
  month); the offline test payment provider takes INR;
- "UAP Acme Enterprises", created through onboarding by its owner (Manoj Kumar),
  with an INR billing account and its profile, Easy CRM Advanced for 10 users
  (the first period's invoice open), an Easy CRM setup record waiting for the
  customer, and three more people: a Billing Manager, an Administrator and a
  Member;
- its three wallets: the Platform Wallet holds INR 10,000 that arrived through a
  real top-up ORDER (placed by the owner, marked paid by staff as a bank
  transfer, credited by fulfilment - so this month's spend is real); AI Credits
  hold 4,82,000; the Consumption Wallet holds INR 500 under its INR 1,000
  low-balance threshold, with WhatsApp and SMS usage charged against it;
- "UAP Beta Labs", a second organization of the same owner (the switcher);
- three partners for Login as Guest, each through the real KYC flow and
  activation: an Affiliate, a Reseller and a White Label.

Scratch databases only. Every password is ``CHECK_PASSWORD``.
"""

# ruff: noqa: S105, S106

import json
import os
import uuid

import django

django.setup()

from django.utils import timezone  # noqa: E402

from workeasy360.commercial.billing import services as billing  # noqa: E402
from workeasy360.commercial.catalog import selectors as catalog_selectors  # noqa: E402
from workeasy360.commercial.catalog import services as catalog  # noqa: E402
from workeasy360.commercial.partners import experience, kyc, promotion  # noqa: E402
from workeasy360.commercial.partners import services as partners  # noqa: E402
from workeasy360.commercial.partners.access import partner_actor  # noqa: E402
from workeasy360.commercial.payments import staff as payments_staff  # noqa: E402
from workeasy360.commercial.payments import wallet_orders  # noqa: E402
from workeasy360.commercial.payments.models import Invoice  # noqa: E402
from workeasy360.commercial.pricing import services as pricing  # noqa: E402
from workeasy360.commercial.provisioning.models import CustomerSetup  # noqa: E402
from workeasy360.commercial.subscriptions import services as subscriptions  # noqa: E402
from workeasy360.commercial.wallets import admin as wallet_admin  # noqa: E402
from workeasy360.commercial.wallets import ledger, usage  # noqa: E402
from workeasy360.commercial.wallets.models import UsageRate  # noqa: E402
from workeasy360.contracts.enums import (  # noqa: E402
    BillingInterval,
    CatalogStatus,
    OrganizationStatus,
    PartnerStatus,
    PriceBookChannel,
)
from workeasy360.contracts.values.money import CurrencyCode  # noqa: E402
from workeasy360.contracts.values.tenant import TenantContext  # noqa: E402
from workeasy360.platform.authz import services as authz  # noqa: E402
from workeasy360.platform.authz.control_plane import (  # noqa: E402
    acting_scope,
    platform_context,
)
from workeasy360.platform.identity import lifecycle  # noqa: E402
from workeasy360.platform.identity.models import User  # noqa: E402
from workeasy360.platform.onboarding import services as onboarding  # noqa: E402
from workeasy360.platform.organizations import projection  # noqa: E402
from workeasy360.platform.organizations.models import (  # noqa: E402
    Membership,
    Organization,
)
from workeasy360.platform.tenancy.context import tenant_context  # noqa: E402
from workeasy360.platform.tenancy.db import (  # noqa: E402
    organization_db_scope,
    tenant_db_scope,
)
from workeasy360.platform.tenancy.unscoped import unscoped_access  # noqa: E402

PASSWORD = os.environ["CHECK_PASSWORD"]
STAFF = os.environ["PLATFORM_SUPERADMIN_EMAIL"]
PRODUCT = "easy_crm"

admin = User.objects.get(email=STAFF)
sctx = platform_context(user_id=admin.id)


def person(email: str, name: str) -> User:
    with unscoped_access(reason="user admin panel acceptance seed", module="seed"):
        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create_user(email=email, password=None)
            lifecycle.complete_with_password(
                user, PASSWORD, full_name=name, activate=True
            )
        return user


def partner(code, name, email, kind, *, products=(), promote=()):
    row = partners.create_partner(
        sctx,
        code=code,
        display_name=name,
        legal_name=f"{name} Pvt Ltd",
        contact_name="Pat Owner",
        contact_email=email,
        payout_currency="INR",
    )
    experience.grant_experience(
        sctx,
        row.id,
        experience=kind,
        also=[],
        reason="Acceptance seed",
        make_default=True,
    )
    for product in products:
        partners.grant_product(sctx, row.id, product_code=product)
    for product in promote:
        promotion.grant_promotion(sctx, row.id, product_code=product)
    owner = person(email, "Pat Owner")
    partners.add_member(sctx, row.id, email=email, role="owner")
    pctx = partner_actor(user_id=owner.id, partner_id=row.id)
    kyc.update_details(pctx, row.id, {"legal_name": f"{name} Pvt Ltd", "country": "IN"})
    kyc.submit(pctx, row.id)
    kyc.start_review(sctx, row.id)
    kyc.decide(sctx, row.id, decision="approve")
    partners.set_partner_status(
        sctx,
        row.id,
        target=PartnerStatus.ACTIVE,
        reason="KYC approved; seeded activation",
    )
    print("PARTNER", code, row.id)
    return row


def owner_ctx(org_id, user_id):
    with (
        unscoped_access(reason="user admin panel acceptance seed", module="seed"),
        organization_db_scope(org_id),
    ):
        membership = Membership.unscoped.get(organization_id=org_id, user_id=user_id)
    return TenantContext(
        organization_id=org_id,
        membership_id=membership.id,
        user_id=user_id,
        organization_status=OrganizationStatus.ACTIVE,
    )


def add_member(org_id, email, name, role):
    user = person(email, name)
    with (
        unscoped_access(reason="user admin panel acceptance seed", module="seed"),
        organization_db_scope(org_id),
    ):
        organization = Organization.objects.get(id=org_id)
        membership = Membership(
            organization_id=org_id,
            user_id=user.id,
            status="active",
            member_type="internal",
        )
        membership.save()
        with acting_scope(
            TenantContext(
                organization_id=org_id, membership_id=membership.id, user_id=user.id
            )
        ):
            authz.replace_system_role(
                organization_id=org_id, membership_id=membership.id, role_key=role
            )
        projection.sync_access_for_membership(
            user_id=user.id, membership=membership, organization=organization
        )
    authz.bump_permission_version(org_id)
    return user


# --- the catalog, priced in INR, and the test provider ----------------------------
with acting_scope(sctx):
    book = pricing.create_price_book(
        sctx,
        code="direct_inr",
        name="Direct INR",
        currency=CurrencyCode.INR,
        channel=PriceBookChannel.DIRECT,
    )
    by_code = {p["code"]: p for p in catalog_selectors.visible_products()}
    versions = {}
    for suffix, label, amount in (
        ("basic", "Basic", 99900),
        ("advanced", "Advanced", 199900),
    ):
        plan = catalog.create_plan(
            sctx,
            product_id=by_code[PRODUCT]["id"],
            code=f"{PRODUCT}_{suffix}",
            name=label,
            caption="TEST plan",
        )
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx, plan_id=plan.id, features={}, included_seats=5, trial_days=0
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        pricing.publish_price(
            sctx,
            price_book_id=book.id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=amount,
        )
        versions[suffix] = version.id
    provider = payments_staff.create_provider(
        sctx,
        {
            "key": "test-inr",
            "adapter": "fake",
            "display_name": "Test provider",
            "credential_ref": "TEST_INR",
            "webhook_secret_ref": "TEST_INR",  # pragma: allowlist secret - a test reference name
            "currencies": ["INR"],
        },
    )
    payments_staff.update_provider(
        sctx, "test-inr", {"status": "enabled", "version": provider["version"]}
    )
    # What usage costs: WhatsApp 0.80 and SMS 0.25 a message, 100 of each free a month.
    for meter, unit in (("whatsapp", 80), ("sms", 25)):
        UsageRate.objects.create(
            meter=meter,
            currency="INR",
            unit_amount=unit,
            per_units=1,
            included_units=100,
            created_by_user_id=admin.id,
            reason="Acceptance seed",
        )

# --- the partners Login as Guest opens -----------------------------------------------
ids = {
    "affiliate": str(
        partner(
            "uap-aff",
            "UAP Affiliate Co",
            "owner@uap-affiliate.dev",
            "affiliate",
            promote=[PRODUCT],
        ).id
    ),
    "reseller": str(
        partner(
            "uap-res",
            "UAP Reseller Co",
            "owner@uap-reseller.dev",
            "reseller",
            products=[PRODUCT],
        ).id
    ),
    "white_label": str(
        partner("uap-wl", "UAP Brand Co", "owner@uap-brand.dev", "white_label").id
    ),
}

# --- the customer ----------------------------------------------------------------------
owner = person("manoj@uap-acme.dev", "Manoj Kumar")
acme = onboarding.create_organization(user_id=owner.id, name="UAP Acme Enterprises")
beta = onboarding.create_organization(user_id=owner.id, name="UAP Beta Labs")
org_id = acme.organization_id
ctx = owner_ctx(org_id, owner.id)
add_member(org_id, "billing@uap-acme.dev", "Bina Billing", "billing_manager")
add_member(org_id, "admin@uap-acme.dev", "Arun Admin", "admin")
add_member(org_id, "member@uap-acme.dev", "Neha Sharma", "member")

with tenant_context(ctx), tenant_db_scope(ctx):
    billing.open_billing_account(
        ctx, currency=CurrencyCode.INR, billing_email="accounts@uap-acme.dev"
    )
    subscriptions.purchase_plan(
        ctx,
        plan_version_id=versions["advanced"],
        idempotency_key=uuid.uuid4().hex,
        quantity=10,
    )
    # The owner adds INR 10,000 to the Platform Wallet: an order and its invoice.
    placed = wallet_orders.member_top_up(
        ctx,
        wallet_kind="platform",
        amount_minor=10_000_00,
        currency="INR",
        idempotency_key=uuid.uuid4().hex,
    )

# The bank transfer arrived: staff mark that invoice paid, and fulfilment credits it.
with (
    unscoped_access(reason="user admin panel acceptance seed", module="seed"),
    organization_db_scope(org_id),
):
    invoice = Invoice.unscoped.get(order_id=placed["order_id"])
payments_staff.mark_invoice_paid(
    sctx,
    org_id,
    invoice.id,
    reference="NEFT-UAP-001",
    reason="Bank transfer received for the wallet top-up",
    expected_version=invoice.version,
)

with acting_scope(sctx):
    ai = ledger.ensure(
        owner_type="organization", owner_id=org_id, kind="ai_credits", currency=""
    )
    ledger.post(
        ai.id,
        amount=482_000,
        entry_type="credit_purchase",
        reason="AI credit pack (acceptance seed)",
        idempotency_key=uuid.uuid4().hex,
    )
    consumption = ledger.ensure(
        owner_type="organization", owner_id=org_id, kind="consumption", currency="INR"
    )
    wallet_admin.record_offline_top_up(
        sctx,
        consumption.id,
        amount=700_00,
        reference="NEFT-UAP-002",
        reason="Bank transfer received for messaging",
        idempotency_key=uuid.uuid4().hex,
    )
    wallet_admin.set_threshold(sctx, consumption.id, 1_000_00)

# Messages the products sent this month: 250 WhatsApp (150 charged), 900 SMS (800 charged).
now = timezone.now()
for meter, quantity in (("whatsapp", 250), ("sms", 900)):
    usage.record(
        organization_id=org_id,
        product_code=PRODUCT,
        meter=meter,
        quantity=quantity,
        idempotency_key=uuid.uuid4().hex,
        currency="INR",
        occurred_at=now,
    )

with unscoped_access(reason="user admin panel acceptance seed", module="seed"):
    CustomerSetup.objects.create(
        organization_id=org_id, product_code=PRODUCT, status="pending"
    )

ids.update(org=str(org_id), slug=acme.slug, beta=beta.slug)
print("SEED " + json.dumps(ids))

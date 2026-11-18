"""The Digital Store check's own data (ADR-065), after the User Admin check's seed,
written by the working tree's own services wherever one exists.

- EasyBook goes on the Store in INR: Professional at 24,000.00 a year (the owner's
  checkout image) and a Free plan, a 7-day trial on Professional, its features,
  setup requirements and an official Android link;
- Easy Project is listed as Coming Soon (shown, never sold);
- Easy CRM is listed too (the seeded customer already holds it);
- the AI-token rate and an AI credit pack in INR (the Recharge page);
- the Refund & Cancellation Policy, written and published by staff (Help content).

Scratch databases only.
"""

# ruff: noqa: S105, S106

import json
import os

import django

django.setup()

from workeasy360.commercial.catalog import selectors as catalog_selectors  # noqa: E402
from workeasy360.commercial.catalog import services as catalog  # noqa: E402
from workeasy360.commercial.catalog.models import Product  # noqa: E402
from workeasy360.commercial.pricing import services as pricing  # noqa: E402
from workeasy360.commercial.pricing.models import PriceBook  # noqa: E402
from workeasy360.commercial.wallets.models import CreditPack, UsageRate  # noqa: E402
from workeasy360.contracts.enums import BillingInterval, CatalogStatus  # noqa: E402
from workeasy360.platform.administration import help_content  # noqa: E402
from workeasy360.platform.authz.control_plane import (  # noqa: E402
    acting_scope,
    platform_context,
)
from workeasy360.platform.identity.models import User  # noqa: E402

STAFF = os.environ["PLATFORM_SUPERADMIN_EMAIL"]
admin = User.objects.get(email=STAFF)
sctx = platform_context(user_id=admin.id)

with acting_scope(sctx):
    book = PriceBook.objects.get(code="direct_inr")
    by_code = {p["code"]: p for p in catalog_selectors.visible_products()}
    versions = {}
    for code, name, amount, interval in (
        ("easybook_professional", "Professional", 24_000_00, BillingInterval.ANNUAL),
        ("easybook_free", "Free", 0, BillingInterval.MONTHLY),
    ):
        plan = catalog.create_plan(
            sctx,
            product_id=by_code["easybook"]["id"],
            code=code,
            name=name,
            caption="Store check plan",
        )
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx, plan_id=plan.id, features={}, included_seats=10, trial_days=0
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        pricing.publish_price(
            sctx,
            price_book_id=book.id,
            plan_version_id=version.id,
            interval=interval,
            amount_minor=amount,
        )
        versions[code] = str(version.id)

    live_rates = UsageRate.objects.filter(effective_to__isnull=True)
    rate_count = live_rates.filter(meter="ai_tokens").count()
    if not rate_count:
        UsageRate.objects.create(
            meter="ai_tokens",
            currency="",
            unit_amount=1,
            per_units=1000,
            included_units=0,
            created_by_user_id=admin.id,
            reason="Store check seed",
        )
    CreditPack.objects.get_or_create(
        code="store-check-starter",
        defaults={
            "name": "Starter Pack",
            "credits": 10_000,
            "price_minor": 499_00,
            "currency": "INR",
        },
    )

    policy = help_content.create(
        sctx,
        {
            "title": "Refund & Cancellation Policy",
            "body": "STORE CHECK TEXT - the owner's approved policy goes here.",
            "kind": "policy",
            "policy_key": "refund-cancellation",
        },
    )
    help_content.set_status(sctx, policy["id"], "published")

# The listing: what the Product Manager's Store Listing would set, written directly
# on this scratch database (the publish checklist is not what this check is about).
Product.objects.filter(code__in=["easybook", "easy_crm", "easy_project"]).update(
    listing="listed", customer_store_enabled=True, status="available"
)
Product.objects.filter(code="easybook").update(
    slug="easybook",
    category="Accounting",
    store_title="WorkEasy Books",
    store_summary="Billing, invoicing and accounting made simple.",
    highlights=[
        "Invoices & Estimates",
        "Expenses & GST Reports",
        "Payments & Reconciliation",
    ],
    setup_requirements=["Your business's GSTIN, if you file GST"],
    app_links={
        "android": "https://play.google.com/store/apps/details?id=com.workeasy360.books"
    },
    popular=True,
)
Product.objects.filter(code="easy_crm").update(slug="easy-crm", category="Sales")
Product.objects.filter(code="easy_project").update(
    slug="easy-project", category="Operations", listing="coming_soon"
)
catalog.refresh_public_catalog()

# The owner's second organization buys in the check: no subscription yet (a product
# added to a running subscription renews with its period), an INR billing account
# and INR 10,000 in its Platform Wallet - the owner's checkout image.
import uuid  # noqa: E402

from workeasy360.commercial.billing import services as billing  # noqa: E402
from workeasy360.commercial.wallets import ledger  # noqa: E402
from workeasy360.contracts.enums import OrganizationStatus  # noqa: E402
from workeasy360.contracts.values.money import CurrencyCode  # noqa: E402
from workeasy360.contracts.values.tenant import TenantContext  # noqa: E402
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

owner = User.objects.get(email="manoj@uap-acme.dev")
with unscoped_access(reason="store check seed", module="seed"):
    beta = Organization.objects.get(slug="uap-beta-labs")
    with organization_db_scope(beta.id):
        membership = Membership.unscoped.get(organization_id=beta.id, user_id=owner.id)
ctx = TenantContext(
    organization_id=beta.id,
    membership_id=membership.id,
    user_id=owner.id,
    organization_status=OrganizationStatus(beta.status),
)
with tenant_context(ctx), tenant_db_scope(ctx):
    billing.open_billing_account(
        ctx, currency=CurrencyCode.INR, billing_email="billing@acmeenterprise.com"
    )
wallet = ledger.ensure(
    owner_type="organization", owner_id=beta.id, kind="platform", currency="INR"
)
ledger.post(
    wallet.id,
    amount=10_000_00,
    entry_type="top_up",
    reason="Store check seed: a paid top-up",
    idempotency_key=uuid.uuid4().hex,
)

print("STORE " + json.dumps({"versions": versions, "buyer": beta.slug}))

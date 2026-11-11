"""Acceptance data for the Partner Admin Panel browser check (ADR-063), written by
the working tree's own services.

- Easy CRM gets two TEST plans priced in INR (Basic 999.00, Advanced 1,999.00 a
  month); the offline test payment provider takes INR;
- a 10% commission programme and a 20% Reseller / 25% White-label margin: the
  partner purchase price is list less that margin;
- four partners, each through the real KYC flow (details, submit, review,
  approve) and activation, except the draft:
  * PAP Affiliate Co - Affiliate, may promote Easy CRM;
  * PAP Reseller Co - Reseller, may sell Easy CRM, Platform Wallet INR 1,000
    (an offline top-up staff recorded), two customers it created;
  * PAP Brand Co - White Label ONLY: branding and a domain, nothing to sell;
  * PAP Draft Co - a Reseller application, KYC not sent, not active;
- a published partner FAQ and a sales guide for Easy CRM.

Scratch databases only. Every password is ``CHECK_PASSWORD``.
"""

# ruff: noqa: S105, S106

import json
import os
import uuid

import django

django.setup()

from workeasy360.commercial.catalog import selectors as catalog_selectors  # noqa: E402
from workeasy360.commercial.catalog import services as catalog  # noqa: E402
from workeasy360.commercial.commissions import margins, rules  # noqa: E402
from workeasy360.commercial.commissions.models import CommissionRule  # noqa: E402
from workeasy360.commercial.partners import (  # noqa: E402
    customers,
    experience,
    kyc,
    promotion,
)
from workeasy360.commercial.partners import services as partners  # noqa: E402
from workeasy360.commercial.partners.access import partner_actor  # noqa: E402
from workeasy360.commercial.payments import staff as payments_staff  # noqa: E402
from workeasy360.commercial.pricing import services as pricing  # noqa: E402
from workeasy360.commercial.wallets import admin as wallet_admin  # noqa: E402
from workeasy360.commercial.wallets import ledger  # noqa: E402
from workeasy360.contracts.enums import (  # noqa: E402
    BillingInterval,
    CatalogStatus,
    PartnerStatus,
    PriceBookChannel,
)
from workeasy360.contracts.values.money import CurrencyCode  # noqa: E402
from workeasy360.platform.administration import help_content  # noqa: E402
from workeasy360.platform.authz.control_plane import (  # noqa: E402
    acting_scope,
    platform_context,
)
from workeasy360.platform.identity import lifecycle  # noqa: E402
from workeasy360.platform.identity.models import User  # noqa: E402
from workeasy360.platform.tenancy.unscoped import unscoped_access  # noqa: E402

PASSWORD = os.environ["CHECK_PASSWORD"]
STAFF = os.environ["PLATFORM_SUPERADMIN_EMAIL"]
PRODUCT = "easy_crm"

admin = User.objects.get(email=STAFF)
sctx = platform_context(user_id=admin.id)


def person(email: str, name: str) -> User:
    with unscoped_access(reason="partner admin panel acceptance seed", module="seed"):
        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create_user(email=email, password=None)
            lifecycle.complete_with_password(
                user, PASSWORD, full_name=name, activate=True
            )
        return user


def partner(code, name, email, kind, *, also=(), products=(), promote=(), approve=True):
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
        also=list(also),
        reason="Acceptance seed",
        make_default=True,
    )
    for product in products:
        partners.grant_product(sctx, row.id, product_code=product)
    for product in promote:
        promotion.grant_promotion(sctx, row.id, product_code=product)
    owner = person(email, "Pat Owner")
    partners.add_member(sctx, row.id, email=email, role="owner")
    if approve:
        pctx = partner_actor(user_id=owner.id, partner_id=row.id)
        kyc.update_details(
            pctx, row.id, {"legal_name": f"{name} Pvt Ltd", "country": "IN"}
        )
        kyc.submit(pctx, row.id)
        kyc.start_review(sctx, row.id)
        kyc.decide(sctx, row.id, decision="approve")
        partners.set_partner_status(
            sctx,
            row.id,
            target=PartnerStatus.ACTIVE,
            reason="KYC approved; seeded activation",
        )
    print("PARTNER", code, row.id, kyc.status_of(row.id))
    return row, owner


with acting_scope(sctx):
    book = pricing.create_price_book(
        sctx,
        code="direct_inr",
        name="Direct INR",
        currency=CurrencyCode.INR,
        channel=PriceBookChannel.DIRECT,
    )
    by_code = {p["code"]: p for p in catalog_selectors.visible_products()}
    product_id = by_code[PRODUCT]["id"]
    for suffix, label, amount in (
        ("basic", "Basic", 99900),
        ("advanced", "Advanced", 199900),
    ):
        plan = catalog.create_plan(
            sctx,
            product_id=product_id,
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
    if not CommissionRule.objects.filter(
        code="default", retired_at__isnull=True
    ).exists():
        rules.create_rule_version(
            sctx, code="default", rate_bp=1000, reason="Programme default"
        )
    margins.set_margin(
        sctx, experience="reseller", discount_bp=2000, reason="Reseller terms"
    )
    margins.set_margin(
        sctx, experience="white_label", discount_bp=2500, reason="White-label terms"
    )

ids = {}
row, _ = partner(
    "pap-aff",
    "PAP Affiliate Co",
    "owner@pap-affiliate.dev",
    "affiliate",
    promote=[PRODUCT],
)
ids["affiliate"] = str(row.id)
row, reseller_owner = partner(
    "pap-res", "PAP Reseller Co", "owner@pap-reseller.dev", "reseller", products=[PRODUCT]
)
ids["reseller"] = str(row.id)
row, _ = partner("pap-wl", "PAP Brand Co", "owner@pap-brand.dev", "white_label")
ids["white_label"] = str(row.id)
row, _ = partner(
    "pap-draft", "PAP Draft Co", "owner@pap-draft.dev", "reseller", approve=False
)
ids["draft"] = str(row.id)

# The reseller's two customers, created as the reseller creates them.
res_ctx = partner_actor(user_id=reseller_owner.id, partner_id=uuid.UUID(ids["reseller"]))
orgs = []
for name, email in (
    ("PAP Customer One", "one@pap-customer.dev"),
    ("PAP Customer Two", "two@pap-customer.dev"),
):
    made = customers.provision_customer(
        res_ctx,
        organization_name=name,
        owner_email=email,
        idempotency_key=uuid.uuid4().hex,
        currency="INR",
    )
    orgs.append(str(made.organization_id))
ids["customers"] = orgs

# INR 1,000 a bank transfer brought in, recorded by staff: enough for one Basic
# activation (799.20) and not for an Advanced one (1,599.20) after it.
with acting_scope(sctx):
    wallet = ledger.ensure(
        owner_type="partner",
        owner_id=uuid.UUID(ids["reseller"]),
        kind="platform",
        currency="INR",
    )
    wallet_admin.record_offline_top_up(
        sctx,
        wallet.id,
        amount=100_000,
        reference="NEFT-PAP-001",
        reason="Bank transfer received",
        idempotency_key=uuid.uuid4().hex,
    )
    for body in (
        {
            "title": "Which KYC documents do I need?",
            "body": "A PAN, a GST certificate if you have one, and an address proof.",
            "kind": "faq",
            "audience": "partner",
        },
        {
            "title": "Selling Easy CRM to a small team",
            "body": "Start with Basic; move to Advanced at ten users.",
            "kind": "sales_guide",
            "audience": "partner",
            "product_code": PRODUCT,
        },
    ):
        article = help_content.create(sctx, body)
        help_content.set_status(sctx, article["id"], "published")

print("SEED " + json.dumps(ids))

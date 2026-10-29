"""Acceptance data for the ecosystem browser check (ADR-053), written by the
WORKING TREE's services on top of the rehearsed database.

- the four real products (registered by ``prepare.py``) each get two TEST plans
  (Basic, Advanced) priced in INR, monthly and yearly, and a 7-day trial on
  Basic - what staff do in the Product Manager; these prices exist only here;
- the offline test payment provider for INR;
- the commission programme default (10%), a Reseller margin (20% off list), a
  White-label margin (25%) and a Reseller set-up fee (INR 25,000);
- three customers with INR billing accounts; an Affiliate (Aman); a second
  Affiliate who will upgrade; a Reseller granted ONLY EasyBook and Easy Help
  Desk; a White-label partner (Acme Cloud) granted Easy CRM and EasyBook with
  its brand.

Scratch databases only. Every password is ``CHECK_PASSWORD``.
"""

import os

import django

django.setup()

from workeasy360.commercial.billing import services as billing  # noqa: E402
from workeasy360.commercial.catalog import selectors as catalog_selectors  # noqa: E402
from workeasy360.commercial.catalog import services as catalog  # noqa: E402
from workeasy360.commercial.commissions import margins, rules  # noqa: E402
from workeasy360.commercial.commissions.models import CommissionRule  # noqa: E402
from workeasy360.commercial.partners import experience, upgrades  # noqa: E402
from workeasy360.commercial.partners import services as partners  # noqa: E402
from workeasy360.commercial.partners.models import PartnerBrand  # noqa: E402
from workeasy360.commercial.payments import staff as payments_staff  # noqa: E402
from workeasy360.commercial.pricing import services as pricing  # noqa: E402
from workeasy360.contracts.enums import (  # noqa: E402
    BillingInterval,
    CatalogStatus,
    OrganizationStatus,
    PartnerStatus,
    PriceBookChannel,
)
from workeasy360.contracts.values.money import CurrencyCode  # noqa: E402
from workeasy360.contracts.values.tenant import TenantContext  # noqa: E402
from workeasy360.platform.authz.control_plane import (  # noqa: E402
    acting_scope,
    platform_context,
)
from workeasy360.platform.identity import lifecycle  # noqa: E402
from workeasy360.platform.identity.models import User  # noqa: E402
from workeasy360.platform.onboarding.services import create_organization  # noqa: E402
from workeasy360.platform.tenancy.context import tenant_context  # noqa: E402
from workeasy360.platform.tenancy.db import tenant_db_scope  # noqa: E402
from workeasy360.platform.tenancy.unscoped import unscoped_access  # noqa: E402

PASSWORD = os.environ["CHECK_PASSWORD"]
STAFF = os.environ["PLATFORM_SUPERADMIN_EMAIL"]
CODES = ("easy_crm", "easybook", "easy_project", "easy_helpdesk")
#: Basic / Advanced, monthly; yearly is ten months.
PRICES = {
    "easy_crm": (99900, 199900),
    "easybook": (79900, 149900),
    "easy_project": (89900, 169900),
    "easy_helpdesk": (59900, 119900),
}

admin = User.objects.get(email=STAFF)
sctx = platform_context(user_id=admin.id)


def person(email, name):
    with unscoped_access(reason="ecosystem acceptance seed", module="seed"):
        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create_user(email=email, password=None)
            lifecycle.complete_with_password(
                user, PASSWORD, full_name=name, activate=True
            )
        return user


def customer(email, name, org_name):
    owner = person(email, name)
    created = create_organization(user_id=owner.id, name=org_name)
    ctx = TenantContext(
        organization_id=created.organization_id,
        membership_id=created.membership_id,
        user_id=owner.id,
        organization_status=OrganizationStatus.ACTIVE,
    )
    with tenant_context(ctx), tenant_db_scope(ctx):
        billing.open_billing_account(ctx, currency=CurrencyCode.INR, billing_email=email)
    print("ORG", email, created.slug)
    return created


def partner(code, name, email, person_name, *, experience_key=None, caps=(), products=()):
    row = partners.create_partner(
        sctx,
        code=code,
        display_name=name,
        legal_name=f"{name} Pvt Ltd",
        contact_name=person_name,
        contact_email=email,
        payout_currency="INR",
    )
    if experience_key:
        experience.grant_experience(
            sctx,
            row.id,
            experience=experience_key,
            reason="Acceptance seed",
            make_default=True,
        )
    for capability in caps:
        partners.grant_capability(
            sctx, row.id, capability=capability, reason="Acceptance seed"
        )
    for product in products:
        partners.grant_product(sctx, row.id, product_code=product)
    person(email, person_name)
    partners.add_member(sctx, row.id, email=email, role="admin")
    partners.set_partner_status(
        sctx, row.id, target=PartnerStatus.ACTIVE, reason="Acceptance seed"
    )
    print("PARTNER", code, row.id)
    return row


with acting_scope(sctx):
    book = pricing.create_price_book(
        sctx,
        code="direct_inr",
        name="Direct INR",
        currency=CurrencyCode.INR,
        channel=PriceBookChannel.DIRECT,
    )
    by_code = {p["code"]: p for p in catalog_selectors.visible_products()}
    for code in CODES:
        product_id = by_code[code]["id"]
        for suffix, label, amount in (
            ("basic", "Basic", PRICES[code][0]),
            ("advanced", "Advanced", PRICES[code][1]),
        ):
            plan = catalog.create_plan(
                sctx,
                product_id=product_id,
                code=f"{code}_{suffix}",
                name=label,
                caption="TEST plan",
            )
            catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
            version = catalog.draft_plan_version(
                sctx, plan_id=plan.id, features={}, included_seats=5, trial_days=0
            )
            catalog.publish_plan_version(sctx, plan_version_id=version.id)
            for interval, price in (
                (BillingInterval.MONTHLY, amount),
                (BillingInterval.ANNUAL, amount * 10),
            ):
                pricing.publish_price(
                    sctx,
                    price_book_id=book.id,
                    plan_version_id=version.id,
                    interval=interval,
                    amount_minor=price,
                )
        catalog.configure_product_trial(
            sctx,
            product_id=product_id,
            enabled=True,
            duration_days=7,
            eligible_plan_codes=[f"{code}_basic"],
        )

    provider = payments_staff.create_provider(
        sctx,
        {
            "key": "test-inr",
            "adapter": "fake",
            "display_name": "Test provider",
            "credential_ref": "TEST_INR",
            "webhook_secret_ref": "TEST_INR",
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
    upgrades.set_program_fee(
        sctx, experience="reseller", amount_minor=2_500_000, currency="INR"
    )

    aman = partner(
        "aman-affiliate",
        "Aman Affiliates",
        "aman@eco.test",
        "Aman Gupta",
        experience_key="affiliate",
    )
    grow = partner(
        "grow-affiliate",
        "Grow Affiliates",
        "grow@eco.test",
        "Gita Grow",
        experience_key="affiliate",
    )
    reseller = partner(
        "res-one",
        "Res One",
        "res@eco.test",
        "Ravi Reseller",
        experience_key="reseller",
        products=("easybook", "easy_helpdesk"),
    )
    acme = partner(
        "acme-cloud",
        "Acme Cloud Partners",
        "wl@eco.test",
        "Asha White",
        experience_key="white_label",
        products=("easy_crm", "easybook"),
    )
    PartnerBrand.objects.create(
        partner_id=acme.id,
        brand_name="Acme Cloud",
        accent_color="#0f766e",
        updated_by_id=admin.id,
        support_email="help@acme-cloud.in",
        portal={
            "welcome_message": "Business apps from Acme Cloud.",
            "website_url": "https://acme-cloud.in",
            "terms_url": "https://acme-cloud.in/terms",
            "privacy_url": "https://acme-cloud.in/privacy",
        },
    )

customer("trial@eco.test", "Tara Trial", "Trial Company")
customer("cart@eco.test", "Kiran Cart", "Cart Company")
customer("buyer@eco.test", "Bina Buyer", "Store Buyer")
print("SEED done")

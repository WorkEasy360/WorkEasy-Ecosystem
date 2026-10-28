"""History made by the COMMITTED code, before the ecosystem migrations (ADR-053).

Run by ``prepare.py`` with ``PYTHONPATH`` pointing at a ``git archive HEAD``
export, against the scratch database, after ``migrate`` + ``bootstrap_superadmin``
+ ``register_products``. It writes the kinds of rows production holds today -
partners with members and grants, a referral code with captured referrals, a
customer the reseller provisioned and sold to (a subscription and a commission
ledger row), a brand - so the rehearsal can prove the new migrations keep every
one of them byte-for-byte.

Only services that exist at HEAD are used. Scratch databases only.
"""

import os
import uuid

import django

django.setup()

from workeasy360.commercial.catalog import selectors as catalog_selectors  # noqa: E402
from workeasy360.commercial.catalog import services as catalog  # noqa: E402
from workeasy360.commercial.commissions import referrals, rules  # noqa: E402
from workeasy360.commercial.partners import customers  # noqa: E402
from workeasy360.commercial.partners import services as partners  # noqa: E402
from workeasy360.commercial.pricing import services as pricing  # noqa: E402
from workeasy360.contracts.enums import (  # noqa: E402
    BillingInterval,
    CatalogStatus,
    PartnerStatus,
    PriceBookChannel,
)
from workeasy360.contracts.values.money import CurrencyCode  # noqa: E402
from workeasy360.platform.authz.control_plane import (  # noqa: E402
    acting_scope,
    platform_context,
)
from workeasy360.platform.identity import lifecycle  # noqa: E402
from workeasy360.platform.identity.models import User  # noqa: E402
from workeasy360.platform.tenancy.unscoped import unscoped_access  # noqa: E402

PASSWORD = os.environ["CHECK_PASSWORD"]
STAFF = os.environ["PLATFORM_SUPERADMIN_EMAIL"]

admin = User.objects.get(email=STAFF)
sctx = platform_context(user_id=admin.id)


def person(email, name):
    with unscoped_access(reason="ecosystem history seed", module="seed"):
        user = User.objects.create_user(email=email, password=None)
        lifecycle.complete_with_password(user, PASSWORD, full_name=name, activate=True)
        return user


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
AFFILIATE = (
    "referral.create",
    "referral.view",
    "commission.earn",
    "commission.view",
    "payout.view",
)

with acting_scope(sctx):
    # One plan and an INR price for EasyBook, as staff would have made it.
    book = pricing.create_price_book(
        sctx,
        code="hist_inr",
        name="History INR",
        currency=CurrencyCode.INR,
        channel=PriceBookChannel.DIRECT,
    )
    product_id = {p["code"]: p["id"] for p in catalog_selectors.visible_products()}[
        "easybook"
    ]
    plan = catalog.create_plan(
        sctx, product_id=product_id, code="easybook_hist", name="History"
    )
    catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
    version = catalog.draft_plan_version(
        sctx, plan_id=plan.id, features={}, included_seats=3, trial_days=0
    )
    catalog.publish_plan_version(sctx, plan_version_id=version.id)
    pricing.publish_price(
        sctx,
        price_book_id=book.id,
        plan_version_id=version.id,
        interval=BillingInterval.MONTHLY,
        amount_minor=150000,
    )
    rules.create_rule_version(
        sctx, code="default", rate_bp=1000, reason="Programme default (history)"
    )

    # An affiliate with a code and two captured visits.
    aff = partners.create_partner(
        sctx,
        code="hist-affiliate",
        display_name="History Affiliate",
        payout_currency="INR",
        contact_email="hist-aff@eco.test",
    )
    for capability in AFFILIATE:
        partners.grant_capability(
            sctx, aff.id, capability=capability, reason="History seed"
        )
    person("hist-aff@eco.test", "Hana History")
    partners.add_member(sctx, aff.id, email="hist-aff@eco.test", role="admin")
    partners.set_partner_status(
        sctx, aff.id, target=PartnerStatus.ACTIVE, reason="History seed"
    )
    code = referrals.create_referral_code_for_partner(sctx, aff.id, label="LinkedIn")
    referrals.capture_referral(code.code)
    referrals.capture_referral(code.code)

    # A reseller that provisioned a customer and sold it EasyBook.
    res = partners.create_partner(
        sctx,
        code="hist-reseller",
        display_name="History Reseller",
        payout_currency="INR",
        contact_email="hist-res@eco.test",
    )
    for capability in RESELLER:
        partners.grant_capability(
            sctx, res.id, capability=capability, reason="History seed"
        )
    partners.grant_product(sctx, res.id, product_code="easybook")
    person("hist-res@eco.test", "Rafi History")
    partners.add_member(sctx, res.id, email="hist-res@eco.test", role="admin")
    partners.set_partner_status(
        sctx, res.id, target=PartnerStatus.ACTIVE, reason="History seed"
    )

from workeasy360.commercial.partners.access import partner_actor  # noqa: E402

member = User.objects.get(email="hist-res@eco.test")
actor = partner_actor(user_id=member.id, partner_id=res.id)
owner = person("hist-owner@eco.test", "Owen History")
created = customers.provision_customer(
    actor,
    organization_name="History Customer",
    owner_email=owner.email,
    idempotency_key=uuid.uuid4().hex,
    currency=CurrencyCode.INR,
)
customers.sell_plan(
    actor,
    created.organization_id,
    plan_version_id=version.id,
    quantity=1,
    idempotency_key=uuid.uuid4().hex,
)
print("HISTORY_SEEDED", created.organization_id)

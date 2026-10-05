"""Seed the Phase 12 staging rehearsal (runs INSIDE the api container).

    docker compose ... exec -T api python manage.py shell < scripts/phase12_staging/seed.py

Staging data only - never a production decision. Prices, plans and the trial
length below are rehearsal values (the owner has not set real ones, and the
platform must not invent them: ADR-038); they exist so the journeys have
something to buy. Everything goes through the same services the Super Admin
console calls:

- one "Team" plan per product (5 seats), a monthly GBP price, a 14-day trial;
- the offline TEST payment provider (payments are TEST/MANUAL in staging);
- the programme's default commission rate.

Idempotent enough to re-run: existing rows are reused.
"""

from datetime import timedelta

from django.utils import timezone

from workeasy360.commercial.catalog import services as catalog
from workeasy360.commercial.catalog.models import Plan, Product
from workeasy360.commercial.commissions import rules
from workeasy360.commercial.payments.models import PaymentProvider
from workeasy360.commercial.pricing import services as pricing
from workeasy360.commercial.pricing.models import PriceBook
from workeasy360.contracts.enums import BillingInterval, CatalogStatus, PriceBookChannel
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.identity.models import User

PRICES = {"easy_crm": 2499, "easybook": 1999, "easy_project": 2999, "easy_helpdesk": 1499}

admin = User.objects.get(email="superadmin@staging.workeasy360.com")
sctx = platform_context(user_id=admin.id, aal=2)

with acting_scope(sctx):
    book = PriceBook.objects.filter(code="staging_direct_gbp").first() or pricing.create_price_book(
        sctx,
        code="staging_direct_gbp",
        name="Direct GBP (staging)",
        currency=CurrencyCode.GBP,
        channel=PriceBookChannel.DIRECT,
    )
    for code, amount in PRICES.items():
        product = Product.objects.get(code=code)
        plan_code = f"{code}_team"
        if Plan.objects.filter(code=plan_code).exists():
            print("exists", plan_code)
            continue
        features = {f.key: True for f in product.features.all()}
        plan = catalog.create_plan(sctx, product_id=product.id, code=plan_code, name="Team")
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx,
            plan_id=plan.id,
            features=features,
            included_seats=5,
            effective_from=timezone.now() - timedelta(days=1),
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        pricing.publish_price(
            sctx,
            price_book_id=book.id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=amount,
            valid_from=timezone.now() - timedelta(days=1),
        )
        catalog.configure_product_trial(
            sctx,
            product_id=product.id,
            enabled=True,
            duration_days=14,
            eligible_plan_codes=[plan_code],
        )
        print("seeded", plan_code, version.id)

    PaymentProvider.objects.get_or_create(
        key="staging-test",
        defaults={
            "adapter": "fake",
            "display_name": "Test provider (staging)",
            "status": "enabled",
            "credential_ref": "TEST",
            "webhook_secret_ref": "TEST",
            "currencies": ["GBP", "INR", "USD"],
        },
    )
    try:
        rules.create_rule_version(
            sctx, code="default", rate_bp=1000, reason="Staging rehearsal default rate"
        )
    except Exception as exc:  # already set on a re-run
        print("commission default:", type(exc).__name__)

print("SEED_DONE")

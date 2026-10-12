"""Commercial setup for the future product, after staff registered it in the
console: one TEST plan, a price, and its 7-day trial switched on - what staff
do in the plan builder and the Product Manager. Sign-in needs none of this;
the customer needs something to start a trial of.

    python future_plan.py test_future_product      (with prepare.backend_env())

Scratch database only.
"""

import sys

import django

django.setup()

from workeasy360.commercial.catalog import selectors as catalog_selectors  # noqa: E402
from workeasy360.commercial.catalog import services as catalog  # noqa: E402
from workeasy360.commercial.pricing import services as pricing  # noqa: E402
from workeasy360.commercial.pricing.models import PriceBook  # noqa: E402
from workeasy360.contracts.enums import BillingInterval, CatalogStatus  # noqa: E402
from workeasy360.platform.authz.control_plane import (  # noqa: E402
    acting_scope,
    platform_context,
)
from workeasy360.platform.identity.models import User  # noqa: E402


def main(code: str) -> None:
    admin = User.objects.get(email="staff@usso.test")
    sctx = platform_context(user_id=admin.id, aal=2)
    with acting_scope(sctx):
        product = next(
            p for p in catalog_selectors.visible_products() if p["code"] == code
        )
        plan = catalog.create_plan(
            sctx,
            product_id=product["id"],
            code=f"{code}_team",
            name="Team",
            caption="TEST plan",
        )
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx, plan_id=plan.id, features={}, included_seats=5, trial_days=0
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        book = PriceBook.objects.get(code="direct_gbp")
        pricing.publish_price(
            sctx,
            price_book_id=book.id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=900,
        )
        catalog.configure_product_trial(
            sctx,
            product_id=product["id"],
            enabled=True,
            duration_days=7,
            eligible_plan_codes=[f"{code}_team"],
        )
    print("plan and 7-day trial ready for", code)


if __name__ == "__main__":
    main(sys.argv[1])

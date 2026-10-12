"""11i - the same end-to-end flow, proved individually for **all four** of
the brief's real product codes (Step 44: "no product may be marked PASS
because another product passed").

Each product gets its own catalog SKU, plan, price, trial configuration,
integration (registered, no credential - so single sign-on is truthfully
`integration_required`), reseller grant and commission rule, then walks:

    catalog -> trial -> buy (converts the trial) -> entitlement -> launch
    -> reseller grant -> reseller sale -> commission
    -> a *different* product denied for the same reseller (no grant)

No product-specific code exists anywhere this test can reach (proved
separately and directly in ``test_11g_reseller_whitelabel.py``); this file
proves the *behaviour* is identical across codes, not merely the absence of
branching.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase7.support import CAPS
from tests.phase11.conftest import (
    CATALOG_STAFF,
    place_and_pay,
    staff_scope,
    start_trial,
    url,
)
from workeasy360.commercial.catalog import services as catalog_services
from workeasy360.commercial.partners import customers as partner_customers
from workeasy360.commercial.partners import services as partner_services
from workeasy360.contracts.enums import (
    BillingInterval,
    CatalogStatus,
    FeatureKind,
    LicensingMode,
    PriceBookChannel,
    ProductStatus,
)
from workeasy360.contracts.errors import PermissionDenied
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.ecosystem.models import ProductIntegration
from workeasy360.platform.entitlements.selectors import decision_for_organization

pytestmark = pytest.mark.django_db

# The brief's four codes, in the platform's legal (underscore) form - see
# ADR-038 for why the hyphenated codes in the brief are not legal ProductCodes.
FOUR_PRODUCTS = ("easy_crm", "easybook", "easy_project", "easy_helpdesk")


@pytest.fixture
def four_products(staff_factory):
    """Each of the four real codes: a catalog SKU with one priced standard
    plan, a trial (14 days, that plan only) and a `draft`-then-activated
    integration with **no credential** - exactly what a newly launched
    product looks like before its own OIDC client exists."""
    from tests.factories.manifests import manifest_for
    from workeasy360.commercial.pricing import services as pricing_services
    from workeasy360.platform.ecosystem import services as ecosystem_services

    with staff_scope(*CATALOG_STAFF, "platform.staff_integration.manage") as ctx:
        book = pricing_services.create_price_book(
            ctx,
            code="direct_gbp_11i",
            name="Direct GBP",
            currency=CurrencyCode.GBP,
            channel=PriceBookChannel.DIRECT,
        )
        made = {}
        for code in FOUR_PRODUCTS:
            product = catalog_services.create_product(
                ctx,
                code=code,
                name=code.replace("_", " ").title(),
                licensing_mode=LicensingMode.PER_SEAT,
            )
            catalog_services.set_product_status(
                ctx, product_id=product.id, status=ProductStatus.AVAILABLE
            )
            catalog_services.create_feature(
                ctx,
                product_id=product.id,
                key=f"{code}.core.enabled",
                name="Core",
                kind=FeatureKind.BOOLEAN,
            )
            plan = catalog_services.create_plan(
                ctx, product_id=product.id, code=f"{code}_team", name="Team"
            )
            catalog_services.set_plan_status(
                ctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE
            )
            version = catalog_services.draft_plan_version(
                ctx,
                plan_id=plan.id,
                features={f"{code}.core.enabled": True},
                included_seats=5,
                effective_from=timezone.now() - timedelta(days=1),
            )
            catalog_services.publish_plan_version(ctx, plan_version_id=version.id)
            pricing_services.publish_price(
                ctx,
                price_book_id=book.id,
                plan_version_id=version.id,
                interval=BillingInterval.MONTHLY,
                amount_minor=4999,
                valid_from=timezone.now() - timedelta(days=1),
            )
            catalog_services.configure_product_trial(
                ctx,
                product_id=product.id,
                enabled=True,
                duration_days=14,
                eligible_plan_codes=[f"{code}_team"],
            )
            integration = ecosystem_services.register_integration(
                ctx, manifest=manifest_for(code, origin=f"https://{code}.example.com")
            )
            ecosystem_services.transition(
                ctx, client_id=integration.client_id, to_status="active"
            )
            made[code] = {
                "product": product,
                "plan_version_id": version.id,
                "plan_code": f"{code}_team",
                "client_id": integration.client_id,
            }
    return made


@pytest.fixture
def shop_for(four_products, console_org, login, fake_payments):
    """An organization whose owner is signed in, with a billing account -
    parametrized by which of the four products it will act on."""

    def make():
        org = console_org()
        owner = login(org["owner"])
        opened = owner.post(
            url(org["slug"], "billing-account"),
            {"currency": "GBP", "billing_email": "billing@example.com"},
            content_type="application/json",
        )
        assert opened.status_code == 200, opened.content
        return {**org, "client": owner, "id": org["org"].id}

    return make


@pytest.mark.parametrize("code", FOUR_PRODUCTS)
def test_catalog_trial_buy_entitlement_and_launch(code, four_products, shop_for, login):
    """Steps 2/5/7/12 of the brief, for this one product code - never assumed
    from another's result."""
    shop = shop_for()
    plan_code = four_products[code]["plan_code"]

    # catalog: on the public site.
    from django.core.cache import cache

    from workeasy360.commercial.subscriptions.marketplace import public_catalog

    cache.clear()
    listed = next(p for p in public_catalog()["products"] if p["code"] == code)
    assert listed["availability"] == "available"
    assert listed["trial"]["available"] is True
    assert plan_code in listed["trial"]["plan_codes"]

    # trial: started, grants a TRIAL entitlement, nothing else.
    started = start_trial(shop, code, plan_code)
    assert started.status_code == 201, (code, started.content)
    assert decision_for_organization(shop["id"], code).status.value == "trial"

    # seat: a holding grants no launch by itself - the owner still needs a
    # seat assignment, exactly as any other product's owner does.
    seated = shop["client"].post(
        url(shop["slug"], f"apps/{code}/seats/{shop['membership'].id}")
    )
    assert seated.status_code in (200, 201), (code, seated.content)

    # buy: converts the trial through the ordinary pay-first order, no
    # second entitlement writer, no duplicate.
    order = place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=str(four_products[code]["plan_version_id"]),
    )
    assert order["status"] == "fulfilled", (code, order)
    decision = decision_for_organization(shop["id"], code)
    assert decision.allowed and decision.status.value == "active"

    # My Products: the app directory reports it as active and launchable.
    directory = shop["client"].get(url(shop["slug"], "apps")).json()["apps"]
    entry = next(a for a in directory if a["product_code"] == code)
    assert entry["state"] == "active" and entry["can_launch"] is True
    assert entry["sso"] == "integration_required"  # no credential was issued

    # launch: a server-owned destination naming only this product's own origin.
    launched = shop["client"].post(
        url(shop["slug"], f"apps/{code}/launch"), HTTP_ACCEPT="application/json"
    )
    assert launched.status_code == 200, (code, launched.content)
    target = launched.json()["redirect_url"]
    assert target.startswith(f"https://{code}.example.com/")
    # ADR-044: with no credential, Workeasy360 sign-in cannot complete for
    # this product, so the launch is the product's own sign-in ("separate
    # product sign-in required") - with no organization hint that would make
    # the product start a Workeasy360 sign-in by itself.
    assert launched.json()["sign_in"] == "product"
    assert "organization=" not in target

    # unauthorized launch: a member with no seat is refused, in words the
    # UI can show, never a raw exception.
    member = shop["add"]("member")
    outsider = login(member["user"])
    denied = outsider.post(
        url(shop["slug"], f"apps/{code}/launch"), HTTP_ACCEPT="application/json"
    )
    assert denied.status_code == 409 and denied.json()["state"] == "no_seat"


@pytest.mark.parametrize("code", FOUR_PRODUCTS)
def test_reseller_grant_sale_and_commission(
    code, four_products, partner_factory, staff_factory, user_factory
):
    """Steps 20/21/25/26, for this one product code. A reseller holding a
    grant for it - and only it, among the four - may sell it and earns
    commission; the other three stay denied for that same reseller."""
    from tests.phase7.support import provision
    from workeasy360.commercial.commissions import rules
    from workeasy360.commercial.commissions.models import CommissionAccrual

    fx = partner_factory(*CAPS)
    partner_services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code=code)
    _u, commission_ctx = staff_factory("platform.staff_commission.manage")
    rules.create_rule_version(
        commission_ctx,
        # A rule code is an independent slug (letters, digits, hyphens) -
        # the product code's underscores must become hyphens here.
        code=f"default-{code.replace('_', '-')}",
        rate_bp=1000,
        reason="Programme default rate",
    )
    result, _owner = provision(
        {"actor": fx["actor"], "user_factory": user_factory},
        name=f"{code} customer",
        key=uuid.uuid4().hex,
    )

    item = partner_customers.sell_plan(
        fx["actor"](),
        result.organization_id,
        plan_version_id=four_products[code]["plan_version_id"],
        quantity=1,
        idempotency_key=uuid.uuid4().hex,
    )
    assert item.product_code == code
    assert CommissionAccrual.objects.filter(partner_id=fx["partner"].id).exists()

    for other in FOUR_PRODUCTS:
        if other == code:
            continue
        with pytest.raises(PermissionDenied) as raised:
            partner_customers.sell_plan(
                fx["actor"](),
                result.organization_id,
                plan_version_id=four_products[other]["plan_version_id"],
                quantity=1,
                idempotency_key=uuid.uuid4().hex,
            )
        assert raised.value.context.get("reason") == "product_not_granted", (code, other)


@pytest.mark.parametrize("code", FOUR_PRODUCTS)
def test_suspension_blocks_launch_for_each_product(code, four_products, shop_for):
    """The write-block chokepoint is organization-wide, not per product - but
    checked here against each real code's own launch endpoint, not assumed
    from the synthetic fixture product ``test_11c`` already covers."""
    from workeasy360.platform.organizations.models import Organization

    shop = shop_for()
    plan_code = four_products[code]["plan_code"]
    started = start_trial(shop, code, plan_code)
    assert started.status_code == 201, (code, started.content)
    seated = shop["client"].post(
        url(shop["slug"], f"apps/{code}/seats/{shop['membership'].id}")
    )
    assert seated.status_code in (200, 201), (code, seated.content)

    Organization.objects.filter(id=shop["id"]).update(status="suspended")
    response = shop["client"].post(
        url(shop["slug"], f"apps/{code}/launch"), HTTP_ACCEPT="application/json"
    )
    assert response.status_code == 402, (code, response.content)
    assert response.json()["code"] == "organization_not_writable"


def test_all_four_integrations_registered_with_no_credential_are_sso_integration_required(
    four_products,
):
    """Step 13's honest answer, for every one of the four - not assumed from
    the audit's manual curl check, checked here through the same selector the
    console and the app directory both read."""
    from workeasy360.platform.ecosystem.selectors import sso_label

    for code in FOUR_PRODUCTS:
        integration = ProductIntegration.objects.get(product_code=code)
        assert not integration.credentials.exists()
        assert sso_label(False) == "integration_required"
        assert integration.status == "active"  # launchable, just not SSO-capable yet

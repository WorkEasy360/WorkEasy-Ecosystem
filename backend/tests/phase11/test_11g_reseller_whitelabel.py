"""11g/11h - the reseller and white-label channels, proved with a **real**
Phase 11 product code (not the shared fixture's "crm"), and the customer-own
white-label brand endpoint (Step 30).

Nothing here is a new engine: selling goes through
``commercial.partners.customers.sell_plan`` exactly as it always has, pricing
through ``pricing.resolution.resolve_price``, commission through the existing
ledger, and branding through the existing ``PartnerBrand``/capability model.
The only new code is the *selector and view* that resolve a customer's own
brand through its live link instead of a Host header (D-035 is untouched:
nothing here signs anyone in on a partner's domain).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase7.support import CAPS, provision
from tests.phase11.conftest import CATALOG_STAFF, staff_scope
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

pytestmark = pytest.mark.django_db

REAL_PRODUCTS = ("easy_crm", "easy_project")  # granted, and deliberately not granted


@pytest.fixture
def real_products(staff_factory):
    """Two of the brief's real product codes, registered exactly as
    ``register_products`` would (catalog only), each with one standard,
    priced plan - which the command deliberately never creates, so the sale
    test adds it here."""
    from workeasy360.commercial.pricing import services as pricing_services

    _actor, sctx = staff_factory(*CATALOG_STAFF)
    made = {}
    with staff_scope(*CATALOG_STAFF) as ctx:
        book = pricing_services.create_price_book(
            ctx,
            code="direct_gbp_11g",
            name="Direct GBP",
            currency=CurrencyCode.GBP,
            channel=PriceBookChannel.DIRECT,
        )
        for code in REAL_PRODUCTS:
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
            made[code] = {"product": product, "plan_version_id": version.id}
    return made


@pytest.fixture
def reseller(partner_factory, staff_factory, user_factory, real_products):
    """An ACTIVE reseller granted only ``easy_crm`` (never ``easy_project``),
    with a 15% commission rule and a customer already provisioned."""
    from workeasy360.commercial.commissions import rules

    fx = partner_factory(*CAPS)
    partner_services.grant_product(
        fx["staff_ctx"], fx["partner"].id, product_code="easy_crm"
    )
    _u, commission_ctx = staff_factory("platform.staff_commission.manage")
    rules.create_rule_version(
        commission_ctx, code="default", rate_bp=1500, reason="Programme default rate"
    )
    result, owner = provision(
        {"actor": fx["actor"], "user_factory": user_factory},
        name="Journey Customer Ltd",
        key=uuid.uuid4().hex,
    )
    fx.update(
        products=real_products,
        org_id=result.organization_id,
        org_slug=result.slug,
        owner=owner,
    )
    return fx


# --- selling a real product through the existing channel -----------------------------------


def test_selling_a_real_product_uses_the_existing_purchase_path_no_new_writer(reseller):
    from workeasy360.commercial.commissions.models import CommissionAccrual
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.models import Subscription
    from workeasy360.platform.entitlements.selectors import decision_for_organization

    item = partner_customers.sell_plan(
        reseller["actor"](),
        reseller["org_id"],
        plan_version_id=reseller["products"]["easy_crm"]["plan_version_id"],
        quantity=1,
        idempotency_key=uuid.uuid4().hex,
    )
    assert item.product_code == "easy_crm"

    reconcile(reseller["org_id"])
    decision = decision_for_organization(reseller["org_id"], "easy_crm")
    assert decision.allowed and decision.status.value == "active"

    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason="Test reads the tenant's own subscription", module="tests.conftest"
        ),
        organization_db_scope(reseller["org_id"]),
    ):
        subs = list(Subscription.unscoped.filter(organization_id=reseller["org_id"]))
    assert len(subs) == 1  # sold through the one ordinary subscription writer

    accruals = CommissionAccrual.objects.filter(partner_id=reseller["partner"].id)
    assert accruals.exists()
    accrual = accruals.get()
    assert accrual.rate_bp == 1500
    assert accrual.amount_minor == 750  # 15% of the 4999 sale, rounded half up


def test_the_partner_price_not_the_list_price_is_what_is_billed(reseller):
    from workeasy360.commercial.partners.services import set_partner_price

    with staff_scope("platform.staff_pricing.manage") as ctx:
        set_partner_price(
            ctx,
            reseller["partner"].id,
            plan_version_id=reseller["products"]["easy_crm"]["plan_version_id"],
            interval=BillingInterval.MONTHLY,
            amount_minor=3999,
        )
    item = partner_customers.sell_plan(
        reseller["actor"](),
        reseller["org_id"],
        plan_version_id=reseller["products"]["easy_crm"]["plan_version_id"],
        quantity=1,
        idempotency_key=uuid.uuid4().hex,
    )
    assert item.total_amount_minor == 3999
    assert item.price_source == "partner_book"


def test_selling_an_ungranted_product_is_denied_step_48(reseller):
    """The mandatory browser-journey assertion, proved at the service layer:
    a reseller granted only easy_crm may not sell easy_project."""
    from workeasy360.commercial.subscriptions.models import Subscription
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with pytest.raises(PermissionDenied) as raised:
        partner_customers.sell_plan(
            reseller["actor"](),
            reseller["org_id"],
            plan_version_id=reseller["products"]["easy_project"]["plan_version_id"],
            quantity=1,
            idempotency_key=uuid.uuid4().hex,
        )
    assert raised.value.context.get("reason") == "product_not_granted"

    with (
        unscoped_access(
            reason="Test verifies nothing was written", module="tests.conftest"
        ),
        organization_db_scope(reseller["org_id"]),
    ):
        assert not Subscription.unscoped.filter(
            organization_id=reseller["org_id"]
        ).exists()


def test_granting_the_product_and_retrying_the_same_request_now_succeeds(reseller):
    """Denial is a policy answer, not a broken product: granting fixes it."""
    partner_services.grant_product(
        reseller["staff_ctx"], reseller["partner"].id, product_code="easy_project"
    )
    item = partner_customers.sell_plan(
        reseller["actor"](),
        reseller["org_id"],
        plan_version_id=reseller["products"]["easy_project"]["plan_version_id"],
        quantity=1,
        idempotency_key=uuid.uuid4().hex,
    )
    assert item.product_code == "easy_project"


def test_no_product_specific_code_exists_for_these_two_codes():
    """The architecture claim, checked directly: neither product code is
    hard-coded anywhere in platform, commercial, interfaces or integration."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3] / "backend" / "workeasy360"
    hits = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name in {"products.json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for code in ("easy_crm", "easy_project", "easy_helpdesk", "easybook"):
            if code in text:
                hits.append(f"{path.relative_to(root)}: {code}")
    assert hits == []


# --- white-label: the customer's own brand (Step 30) ----------------------------------------


@pytest.fixture
def white_label(partner_factory):
    """An ACTIVE partner holding both white-label capabilities, with a brand,
    and a customer organization linked to it (no product sold - branding is
    independent of what the customer holds)."""
    from workeasy360.commercial.partners import branding
    from workeasy360.commercial.partners import services as services

    fx = partner_factory("branding.manage", "domain.manage", "customer.provision")
    branding.update_brand(
        fx["staff_ctx"],
        brand_name="ABC Business Cloud",
        accent_color="#336699",
        support_email="support@abc.example.com",
        support_url="",
        theme={},
        partner_id=fx["partner"].id,
    )
    return fx


def _linked_org(fx, user_factory, *, name="Branded Customer Ltd"):
    result, owner = provision(
        {"actor": fx["actor"], "user_factory": user_factory},
        name=name,
        key=uuid.uuid4().hex,
    )
    return result, owner


def test_a_customers_own_brand_is_served_through_the_live_link(white_label, user_factory):
    from workeasy360.commercial.partners.selectors import brand_for_organization

    result, _owner = _linked_org(white_label, user_factory)
    brand = brand_for_organization(result.organization_id)
    assert brand is not None
    assert brand["brand_name"] == "ABC Business Cloud"
    assert brand["accent_color"] == "#336699"
    assert "partner_id" not in brand and "capabilities" not in brand


def test_an_unlinked_organization_sees_no_brand(organization_factory):
    from workeasy360.commercial.partners.selectors import brand_for_organization

    org, _membership, _user = organization_factory()
    assert brand_for_organization(org.id) is None


def test_branding_alone_without_domain_capability_shows_no_brand(
    partner_factory, user_factory
):
    """ "White-label" is both capabilities together, exactly as
    ``experience_labels`` already defines it - not branding.manage alone."""
    from workeasy360.commercial.partners import branding
    from workeasy360.commercial.partners.selectors import brand_for_organization

    fx = partner_factory(
        "branding.manage", "customer.provision"
    )  # domain.manage withheld
    branding.update_brand(
        fx["staff_ctx"],
        brand_name="Half Set",
        accent_color="#112233",
        support_email="",
        support_url="",
        theme={},
        partner_id=fx["partner"].id,
    )
    result, _owner = _linked_org(fx, user_factory)
    assert brand_for_organization(result.organization_id) is None


def test_a_terminated_partner_serves_no_brand_to_its_former_customer(
    white_label, user_factory
):
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.selectors import brand_for_organization
    from workeasy360.contracts.enums import PartnerStatus

    result, _owner = _linked_org(white_label, user_factory)
    assert brand_for_organization(result.organization_id) is not None
    services.set_partner_status(
        white_label["staff_ctx"],
        white_label["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Terminating for the test",
    )
    assert brand_for_organization(result.organization_id) is None


def _add_active_member(organization_id, user_factory):
    """A plain, credentialed member of ``organization_id`` - not the invited
    owner, whose account has no password until they follow the activation
    link (D-020). The brand endpoint needs no role, only membership."""
    from tests.extension.conftest import PASSWORD
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    user = user_factory(password=PASSWORD)
    with (
        unscoped_access(reason="Test adds a member directly", module="tests.conftest"),
        organization_db_scope(organization_id),
    ):
        member = Membership(
            organization_id=organization_id,
            user_id=user.id,
            status=MembershipStatus.ACTIVE.value,
            member_type="internal",
        )
        member.save()
    return user


def test_the_brand_endpoint_is_readable_by_any_member_and_leaks_nothing_extra(
    white_label, user_factory, login
):
    result, _owner = _linked_org(white_label, user_factory)
    member = _add_active_member(result.organization_id, user_factory)
    client = login(member)
    response = client.get(f"/api/v1/orgs/{result.slug}/brand")
    assert response.status_code == 200, response.content
    body = response.json()["brand"]
    assert body["brand_name"] == "ABC Business Cloud"
    assert set(body) == {
        "brand_name",
        "accent_color",
        "theme",
        "support_email",
        "support_url",
        "logo_url",
        # The public part of the customer portal (ADR-042): words and support
        # details, never the sender identity of emails sent in the partner's name.
        "portal",
    }
    assert "sender_name" not in body["portal"] and "reply_to_email" not in body["portal"]


def test_a_member_of_another_organization_cannot_read_this_ones_brand(
    white_label, user_factory, login, console_org
):
    result, _owner = _linked_org(white_label, user_factory)
    other = console_org()
    client = login(other["owner"])
    response = client.get(f"/api/v1/orgs/{result.slug}/brand")
    assert response.status_code in (403, 404)


def test_an_organization_with_no_link_gets_the_default_null_brand_not_an_error(
    console_org, login
):
    org = console_org()
    client = login(org["owner"])
    response = client.get(f"/api/v1/orgs/{org['slug']}/brand")
    assert response.status_code == 200
    assert response.json() == {"brand": None}

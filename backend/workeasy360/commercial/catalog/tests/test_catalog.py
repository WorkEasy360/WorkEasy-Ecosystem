"""Product, feature, plan and bundle behaviour.

The assertions that matter most here are the negative ones: what the catalog
**refuses**. A catalog that accepts a malformed plan produces a purchase that
cannot be provisioned, and that failure surfaces at a customer's checkout.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors, services
from workeasy360.commercial.catalog.domain import compare_plan_versions
from workeasy360.commercial.catalog.models import (
    Bundle,
    BundleItem,
    Feature,
    Plan,
    PlanFeature,
    PlanVersion,
    Product,
)
from workeasy360.contracts.enums import (
    BundleKind,
    CatalogStatus,
    FeatureKind,
    ProductStatus,
)
from workeasy360.contracts.errors import (
    CatalogValidationError,
    ImmutableRecord,
    InvalidStateTransition,
    PermissionDenied,
)

pytestmark = pytest.mark.django_db


# --- products -------------------------------------------------------------


def test_product_is_created_in_draft(platform_admin, in_tenant):
    """A product with no plan and no price is not sellable."""
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        product = services.create_product(ctx, code="projects", name="Projects")

    assert product.status_enum is ProductStatus.DRAFT
    assert not product.status_enum.is_sellable


def test_product_codes_are_unique_and_validated(platform_admin, in_tenant, catalog):
    ctx = platform_admin["ctx"]
    with in_tenant(ctx), pytest.raises(CatalogValidationError):
        services.create_product(ctx, code="crm", name="CRM again")

    with in_tenant(ctx), pytest.raises(CatalogValidationError):
        services.create_product(ctx, code="Not A Code", name="Bad")


def test_product_identity_does_not_depend_on_its_name(platform_admin, in_tenant, catalog):
    """Renaming is a display change and nothing else.

    If anything keyed off the name, a rename would silently move what
    customers are entitled to.
    """
    ctx = platform_admin["ctx"]
    crm = catalog["products"]["crm"]

    with in_tenant(ctx):
        Product.objects.filter(id=crm.id).update(name="Relationships")
        reloaded = Product.objects.get(id=crm.id)

    assert reloaded.code == "crm"
    assert reloaded.name == "Relationships"


def test_product_lifecycle_transitions(platform_admin, in_tenant, catalog):
    ctx = platform_admin["ctx"]
    crm = catalog["products"]["crm"]

    with in_tenant(ctx):
        deprecated = services.set_product_status(
            ctx, product_id=crm.id, status=ProductStatus.DEPRECATED
        )
        assert deprecated.deprecated_at is not None
        assert not deprecated.status_enum.is_sellable

        retired = services.set_product_status(
            ctx, product_id=crm.id, status=ProductStatus.RETIRED
        )
        assert retired.retired_at is not None


def test_invalid_product_transition_is_refused(platform_admin, in_tenant, catalog):
    """``retired`` is terminal: resurrecting a withdrawn SKU under the same
    code makes historical reporting ambiguous."""
    ctx = platform_admin["ctx"]
    crm = catalog["products"]["crm"]

    with in_tenant(ctx):
        services.set_product_status(
            ctx, product_id=crm.id, status=ProductStatus.DEPRECATED
        )
        services.set_product_status(ctx, product_id=crm.id, status=ProductStatus.RETIRED)
        with pytest.raises(InvalidStateTransition):
            services.set_product_status(
                ctx, product_id=crm.id, status=ProductStatus.AVAILABLE
            )


def test_retiring_a_product_leaves_its_published_versions_readable(
    platform_admin, in_tenant, catalog
):
    """A retired product must not invalidate historical subscription records.

    The structural reason: a subscription pins an immutable ``PlanVersion``,
    and nothing about retiring the product touches that row.
    """
    ctx = platform_admin["ctx"]
    crm = catalog["products"]["crm"]
    version_id = catalog["versions"]["crm_professional"].id

    with in_tenant(ctx):
        services.set_product_status(
            ctx, product_id=crm.id, status=ProductStatus.DEPRECATED
        )
        services.set_product_status(ctx, product_id=crm.id, status=ProductStatus.RETIRED)
        dto = selectors.plan_version(version_id)

    assert dto is not None
    assert dto.is_published
    assert dto.feature_map()["crm.pipelines.max"] == 25


# --- features -------------------------------------------------------------


def test_feature_key_must_belong_to_its_product(platform_admin, in_tenant, catalog):
    """A key declared under the wrong product is sold by one product and
    checked by another."""
    ctx = platform_admin["ctx"]
    books = catalog["products"]["books"]

    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="leading segment"):
        services.create_feature(
            ctx,
            product_id=books.id,
            key="crm.pipelines.other",
            name="Wrong product",
            kind=FeatureKind.QUOTA,
            default_value=0,
        )


def test_feature_keys_are_not_free_text(platform_admin, in_tenant, catalog):
    ctx = platform_admin["ctx"]
    crm = catalog["products"]["crm"]

    with in_tenant(ctx), pytest.raises(CatalogValidationError):
        services.create_feature(
            ctx,
            product_id=crm.id,
            key="Pipelines Max",
            name="Bad key",
            kind=FeatureKind.QUOTA,
            default_value=0,
        )


def test_enum_features_must_declare_an_ordering(platform_admin, in_tenant, catalog):
    """Without an ordering, overlapping grants cannot be resolved."""
    ctx = platform_admin["ctx"]
    crm = catalog["products"]["crm"]

    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="enum_ordering"):
        services.create_feature(
            ctx,
            product_id=crm.id,
            key="crm.support.tier",
            name="Support tier",
            kind=FeatureKind.ENUM,
            default_value="standard",
        )


def test_feature_key_and_kind_cannot_be_changed(platform_admin, in_tenant, catalog):
    """The update service exposes presentation fields only.

    Asserted structurally rather than by calling with a bad argument: the
    guarantee is that no caller *can* pass them.
    """
    import inspect

    signature = inspect.signature(services.update_feature)
    assert "key" not in signature.parameters
    assert "kind" not in signature.parameters

    ctx = platform_admin["ctx"]
    feature = catalog["features"]["pipelines"]
    with in_tenant(ctx):
        updated = services.update_feature(
            ctx, feature_id=feature.id, name="Sales pipelines"
        )
    assert updated.key == "crm.pipelines.max"
    assert updated.kind == FeatureKind.QUOTA.value


def test_plans_reference_features_rather_than_embedding_booleans():
    """No boolean-per-capability columns anywhere on the plan tables.

    Columns cannot express a quota, cannot be compared on a pricing page, and
    make every new capability a migration.
    """
    from django.db import models

    for model in (Plan, PlanVersion):
        offenders = [
            field.name
            for field in model._meta.get_fields()
            if isinstance(field, models.BooleanField) and field.name not in {"is_public"}
        ]
        assert not offenders, f"{model.__name__} embeds feature booleans: {offenders}"

    assert PlanFeature._meta.get_field("feature").related_model is Feature


# --- plan versions and immutability ---------------------------------------


def test_plan_version_value_must_match_the_feature_kind(
    platform_admin, in_tenant, catalog
):
    """A quota stored as a string compares as a string, which is True for the
    wrong reason and only shows up at a customer's limit."""
    ctx = platform_admin["ctx"]
    plan = catalog["plans"]["crm_starter"]

    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="quota"):
        services.draft_plan_version(
            ctx, plan_id=plan.id, features={"crm.pipelines.max": "25"}
        )


def test_plan_version_refuses_a_feature_of_another_product(
    platform_admin, in_tenant, catalog
):
    ctx = platform_admin["ctx"]
    plan = catalog["plans"]["crm_starter"]

    with in_tenant(ctx), pytest.raises(CatalogValidationError):
        services.draft_plan_version(
            ctx, plan_id=plan.id, features={"books.ledger.multi_currency": True}
        )


def test_plan_version_refuses_an_undeclared_feature(platform_admin, in_tenant, catalog):
    """Commercial can only sell features that exist in the catalog."""
    ctx = platform_admin["ctx"]
    plan = catalog["plans"]["crm_starter"]

    with in_tenant(ctx), pytest.raises(CatalogValidationError, match="Unknown feature"):
        services.draft_plan_version(
            ctx, plan_id=plan.id, features={"crm.invented.thing": True}
        )


def test_publishing_twice_is_refused(platform_admin, in_tenant, catalog):
    ctx = platform_admin["ctx"]
    version = catalog["versions"]["crm_starter"]

    with in_tenant(ctx), pytest.raises(ImmutableRecord):
        services.publish_plan_version(ctx, plan_version_id=version.id)


def test_published_plan_version_terms_are_immutable_at_the_database(
    platform_admin, in_tenant, catalog
):
    """The application guard is bypassed by a data-fix script; the trigger is
    not. This asserts the trigger, not the service."""
    ctx = platform_admin["ctx"]
    version = catalog["versions"]["crm_professional"]

    with in_tenant(ctx):
        with pytest.raises(Exception) as excinfo, transaction.atomic():
            PlanVersion.objects.filter(id=version.id).update(included_seats=999)
        assert "immutable" in str(excinfo.value).lower()

        reloaded = PlanVersion.objects.get(id=version.id)
    assert reloaded.included_seats == 10


def test_published_plan_features_are_frozen_at_the_database(
    platform_admin, in_tenant, catalog
):
    """Immutable terms with mutable feature rows is not immutability.

    The version's identity would be unchanged while what it grants was
    rewritten - which is exactly the retroactive change versioning exists to
    prevent.
    """
    ctx = platform_admin["ctx"]
    version = catalog["versions"]["crm_professional"]

    with in_tenant(ctx):
        with pytest.raises(Exception) as excinfo, transaction.atomic():
            PlanFeature.objects.filter(plan_version_id=version.id).update(value=9999)
        assert "published" in str(excinfo.value).lower()

        values = set(
            PlanFeature.objects.filter(plan_version_id=version.id).values_list(
                "value", flat=True
            )
        )
    assert 25 in values


def test_publishing_a_new_version_supersedes_the_previous_one(
    platform_admin, in_tenant, catalog
):
    """New subscriptions get v2; existing ones keep v1 until migrated."""
    ctx = platform_admin["ctx"]
    plan = catalog["plans"]["crm_starter"]
    first = catalog["versions"]["crm_starter"]

    with in_tenant(ctx):
        second = services.draft_plan_version(
            ctx, plan_id=plan.id, features={"crm.pipelines.max": 5}, included_seats=3
        )
        services.publish_plan_version(ctx, plan_version_id=second.id)

        current = selectors.current_plan_version("crm_starter", at=timezone.now())
        original = selectors.plan_version(first.id)

    assert second.version == first.version + 1
    assert current is not None and current.id == second.id
    assert current.feature_map()["crm.pipelines.max"] == 5
    # The original is untouched, which is the whole point.
    assert original is not None
    assert original.feature_map()["crm.pipelines.max"] == 3
    assert original.effective_to is not None


def test_plan_versions_are_numbered_per_plan(platform_admin, in_tenant, catalog):
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        starter = services.draft_plan_version(
            ctx,
            plan_id=catalog["plans"]["crm_starter"].id,
            features={"crm.pipelines.max": 4},
        )
        professional = services.draft_plan_version(
            ctx,
            plan_id=catalog["plans"]["crm_professional"].id,
            features={"crm.pipelines.max": 30},
        )
    assert starter.version == 2
    assert professional.version == 2


# --- bundles --------------------------------------------------------------


def test_bundle_grants_plan_versions_not_products(catalog):
    items = list(BundleItem.objects.filter(bundle_version=catalog["bundle_version"]))
    assert len(items) == 2
    assert all(item.plan_version_id is not None for item in items)


def test_bundle_rejects_duplicate_items(platform_admin, in_tenant, catalog):
    """A duplicate expands into two identical subscription items and bills
    twice."""
    ctx = platform_admin["ctx"]
    version_id = catalog["versions"]["crm_professional"].id

    with in_tenant(ctx):
        bundle = services.create_bundle(ctx, code="dupes", name="Dupes")
        with pytest.raises(CatalogValidationError, match="more than once"):
            services.draft_bundle_version(
                ctx,
                bundle_id=bundle.id,
                items=[
                    {"plan_version_id": version_id},
                    {"plan_version_id": version_id},
                ],
            )


def test_bundle_rejects_two_plans_of_the_same_product(platform_admin, in_tenant, catalog):
    """Which tier the customer holds would be ambiguous."""
    ctx = platform_admin["ctx"]

    with in_tenant(ctx):
        bundle = services.create_bundle(ctx, code="two_crm", name="Two CRM tiers")
        with pytest.raises(CatalogValidationError, match="two plans of product"):
            services.draft_bundle_version(
                ctx,
                bundle_id=bundle.id,
                items=[
                    {"plan_version_id": catalog["versions"]["crm_starter"].id},
                    {"plan_version_id": catalog["versions"]["crm_professional"].id},
                ],
            )


def test_bundle_rejects_an_unpublished_plan_version(platform_admin, in_tenant, catalog):
    """A draft can still change under the bundle."""
    ctx = platform_admin["ctx"]

    with in_tenant(ctx):
        draft = services.draft_plan_version(
            ctx,
            plan_id=catalog["plans"]["crm_starter"].id,
            features={"crm.pipelines.max": 4},
        )
        bundle = services.create_bundle(ctx, code="drafty", name="Drafty")
        with pytest.raises(CatalogValidationError, match="not published"):
            services.draft_bundle_version(
                ctx, bundle_id=bundle.id, items=[{"plan_version_id": draft.id}]
            )


def test_bundle_rejects_an_empty_item_list(platform_admin, in_tenant, catalog):
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        bundle = services.create_bundle(ctx, code="empty", name="Empty")
        with pytest.raises(CatalogValidationError, match="at least one item"):
            services.draft_bundle_version(ctx, bundle_id=bundle.id, items=[])


def test_a_bundle_cannot_contain_a_bundle():
    """Nested bundles are not supported, structurally.

    Phase 0 does not require them, and recursion turns expansion, pricing and
    commission into algorithms nobody can explain to a customer. Asserted on
    the schema rather than by a service check, because a service check can be
    removed.
    """
    from django.db import models

    related = {
        field.related_model
        for field in BundleItem._meta.get_fields()
        if isinstance(field, models.ForeignKey)
    }
    assert Bundle not in related
    assert not any(
        isinstance(field, models.ForeignKey) and field.related_model is Bundle
        for field in Bundle._meta.get_fields()
        if field.name != "versions"
    )


def test_activating_a_bundle_with_a_retired_plan_is_refused(
    platform_admin, in_tenant, catalog
):
    """A purchase of it could not be fulfilled."""
    ctx = platform_admin["ctx"]

    with in_tenant(ctx):
        services.set_plan_status(
            ctx,
            plan_id=catalog["plans"]["books_professional"].id,
            status=CatalogStatus.RETIRED,
        )
        services.set_bundle_status(
            ctx, bundle_id=catalog["bundle"].id, status=CatalogStatus.GRANDFATHERED
        )
        with pytest.raises(CatalogValidationError, match="retired plan"):
            services.set_bundle_status(
                ctx, bundle_id=catalog["bundle"].id, status=CatalogStatus.AVAILABLE
            )


def test_custom_bundles_are_never_public(platform_admin, in_tenant):
    """An enterprise package must not appear on the pricing page."""
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        bundle = services.create_bundle(
            ctx,
            code="acme_enterprise",
            name="ACME Enterprise",
            kind=BundleKind.CUSTOM,
            is_public=True,
        )
    assert bundle.is_public is False

    with in_tenant(ctx), pytest.raises(IntegrityError), transaction.atomic():
        Bundle.objects.filter(id=bundle.id).update(is_public=True)


def test_the_catalog_holds_no_tenant_references():
    """Control plane and data plane stay separate.

    A catalog table with an organization column would need RLS, and a bug in
    catalog querying could then leak one customer's bespoke packaging to
    another.
    """
    from django.db import models

    for model in (Product, Feature, Plan, PlanVersion, PlanFeature, Bundle, BundleItem):
        columns = {
            field.attname
            for field in model._meta.get_fields()
            if hasattr(field, "attname")
        }
        assert "organization_id" not in columns, (
            f"{model.__name__} is control-plane data and must not carry a tenant."
        )
        assert not any(
            isinstance(field, models.ForeignKey)
            and field.related_model is not None
            and field.related_model._meta.app_label == "organizations"
            for field in model._meta.get_fields()
        )


# --- authorization --------------------------------------------------------


def test_a_customer_cannot_manage_the_global_catalog(customer, in_tenant):
    """The customer admin holds subscription permissions and nothing else."""
    ctx = customer["ctx"]
    with in_tenant(ctx), pytest.raises(PermissionDenied):
        services.create_product(ctx, code="sneaky", name="Sneaky")


def test_catalog_management_requires_an_internal_identity(
    db, organization_factory, grant_permissions, tenant_ctx_factory, in_tenant
):
    """The second barrier: permissions alone are not enough.

    Simulates the failure this guards against - a customer role that has
    somehow been granted ``commercial.catalog.manage``. Without the staff
    check, that role would edit the global catalog for every tenant.
    """
    org, membership, user = organization_factory()
    grant_permissions(org, membership, ("commercial.catalog.manage",))
    ctx = tenant_ctx_factory(org, membership, user)

    assert user.is_staff is False
    with in_tenant(ctx), pytest.raises(PermissionDenied, match="control-plane"):
        services.create_product(ctx, code="sneaky", name="Sneaky")


# --- change classification ------------------------------------------------


@pytest.mark.parametrize(
    ("from_seats", "to_seats", "from_features", "to_features", "expected"),
    [
        (5, 10, {"a": 10}, {"a": 10}, "upgrade"),
        (10, 5, {"a": 10}, {"a": 10}, "downgrade"),
        (5, 5, {"a": 10}, {"a": 25}, "upgrade"),
        (5, 5, {"a": 25}, {"a": 10}, "downgrade"),
        (5, 5, {"a": True}, {"a": False}, "downgrade"),
        (5, 5, {"a": 10}, {"a": -1}, "upgrade"),
        (5, 5, {"a": -1}, {"a": 100}, "downgrade"),
        (5, 5, {"a": 10}, {"a": 10}, "lateral"),
        (5, 5, {"a": 10, "b": True}, {"a": 10}, "downgrade"),
        # Mixed: more seats but a feature withdrawn. Must be a downgrade, or
        # it would apply immediately and silently remove paid-for capability.
        (5, 10, {"a": True}, {}, "downgrade"),
    ],
)
def test_plan_change_direction(
    from_seats, to_seats, from_features, to_features, expected
):
    assert (
        compare_plan_versions(
            from_seats=from_seats,
            to_seats=to_seats,
            from_features=from_features,
            to_features=to_features,
        )
        == expected
    )

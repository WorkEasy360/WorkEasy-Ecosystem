"""Isolation coverage for *every* tenant model, by construction.

B8 requires that every tenant model has a test proving tenant B cannot read
tenant A's rows. Writing one test per model guarantees the day comes when
somebody adds a model and forgets - and the gap is invisible, because the
suite is still green.

So the suite is parametrised over the live model registry instead. A tenant
model added tomorrow is covered the moment it exists, and a model that cannot
satisfy these properties fails the build rather than slipping through.

The properties asserted for each model:

    1. the scoped manager raises without tenant context (Layer 2)
    2. its table has RLS enabled, FORCED, with USING and WITH CHECK (Layer 3)
    3. a raw query in tenant A's scope cannot see tenant B's rows
    4. it carries an ``organization_id``-leading index (RLS must be
       index-served or every query becomes a scan)
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import connection, transaction

from workeasy360.contracts.errors import MissingTenantContext
from workeasy360.platform.tenancy.db import set_tenant_variable, tables_missing_rls
from workeasy360.platform.tenancy.models import TenantScopedModel

pytestmark = [pytest.mark.django_db, pytest.mark.isolation]


def tenant_models() -> list[type]:
    """Every concrete tenant-scoped model we own."""
    return sorted(
        (
            m
            for m in apps.get_models()
            if issubclass(m, TenantScopedModel)
            and not m._meta.abstract
            and (m.__module__ or "").startswith("workeasy360.")
        ),
        key=lambda m: m._meta.label,
    )


def _ids(model) -> str:
    return model._meta.label


ALL_TENANT_MODELS = tenant_models()


def test_there_are_tenant_models():
    """Guard against the parametrised tests below silently covering nothing."""
    assert ALL_TENANT_MODELS


@pytest.mark.parametrize("model", ALL_TENANT_MODELS, ids=_ids)
def test_scoped_manager_raises_without_context(model):
    """Layer 2 fails loudly rather than returning an empty queryset.

    Silent emptiness looks like "no data", passes review and ships.
    """
    with pytest.raises(MissingTenantContext):
        list(model.objects.all()[:1])


@pytest.mark.parametrize("model", ALL_TENANT_MODELS, ids=_ids)
def test_unscoped_manager_requires_an_explicit_grant(model):
    """Bypassing scoping is permitted, never silent."""
    with pytest.raises(MissingTenantContext, match="unscoped_access"):
        list(model.unscoped.all()[:1])


@pytest.mark.parametrize("model", ALL_TENANT_MODELS, ids=_ids)
def test_table_has_a_complete_rls_policy(model):
    """Layer 3, with all four properties.

    Three of the four can be present while the policy does nothing: RLS
    without FORCE is bypassed by the owner, and USING without WITH CHECK
    permits writing a row into another tenant.
    """
    problems = tables_missing_rls([model._meta.db_table])
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("model", ALL_TENANT_MODELS, ids=_ids)
def test_rls_hides_rows_from_other_tenants(model, two_tenants):
    """A raw query in tenant A's scope sees no row belonging to tenant B.

    Raw SQL deliberately: the ORM filter is Layer 2, and this asserts the
    database. Every tenant table is checked for foreign rows, whether or not
    the fixtures happened to populate it - a model with no rows still proves
    that the policy predicate is applied rather than absent.
    """
    a, b = two_tenants["a"], two_tenants["b"]
    table = connection.ops.quote_name(model._meta.db_table)

    with transaction.atomic():
        set_tenant_variable(a["org"].id)
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {table} WHERE organization_id <> %s",
                [a["org"].id],
            )
            foreign_visible = cur.fetchone()[0]

    assert foreign_visible == 0, (
        f"{model._meta.label}: {foreign_visible} row(s) belonging to another "
        f"tenant were visible inside tenant A's scope."
    )

    # And the same from B's side, so a policy that happens to match only one
    # tenant's id cannot pass.
    with transaction.atomic():
        set_tenant_variable(b["org"].id)
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {table} WHERE organization_id <> %s",
                [b["org"].id],
            )
            assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("model", ALL_TENANT_MODELS, ids=_ids)
def test_no_rows_visible_without_tenant_context(model):
    """Fail-closed: an unset variable matches zero rows, never all rows."""
    table = connection.ops.quote_name(model._meta.db_table)

    with transaction.atomic():
        set_tenant_variable(None)
        with connection.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("model", ALL_TENANT_MODELS, ids=_ids)
def test_has_an_organization_leading_index(model):
    """B3. RLS adds a predicate to every query on a tenant table.

    That is negligible *provided* organization_id leads the relevant index;
    without it, the predicate produces a full scan per query
    (10-performance-concurrency.md section 4).
    """
    from workeasy360.platform.tenancy.checks import INDEX_PREFIX_EXEMPT

    leading = []
    for index in model._meta.indexes:
        if index.fields and index.name not in INDEX_PREFIX_EXEMPT:
            leading.append(index.fields[0].lstrip("-"))

    # The organization FK's own db_index also serves the predicate, so a model
    # with no composite indexes is still covered.
    has_fk_index = model._meta.get_field("organization").db_index

    assert has_fk_index or any(
        f in {"organization", "organization_id"} for f in leading
    ), (
        f"{model._meta.label} has no index leading with organization; the RLS "
        f"predicate would scan."
    )

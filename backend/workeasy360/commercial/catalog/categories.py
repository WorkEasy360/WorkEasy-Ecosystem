"""The product categories the Super Admin offers (2026-11-16, ADR-068).

Staff (``commercial.catalog.manage``) keep one list: add a category, rename
it, put the list in order, delete one. The Product Publisher's and the
Listing tab's category field offer exactly this list, and the Product Catalog
filters by it.

A product still carries its category as plain text (``Product.category``), so
the Store, its filters and every existing reader are unchanged. That is why:

- **renaming** a category renames it on every product that has it, in the
  same transaction, and refreshes the public catalog;
- **deleting** a category that products use needs another category to move
  them to - a product is never left pointing at a category that no longer
  exists without saying so;
- a product whose category was set some other way before the list existed
  keeps it: ``options`` still offers it, so editing that product never loses it.

Nothing branches on a category; it is display text only.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.db.models import Count, Max
from django.db.models.functions import Lower

from workeasy360.commercial.catalog.domain import validate_category
from workeasy360.commercial.catalog.services import (
    _authorise,
    _one,
    refresh_public_catalog,
)
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

#: A list longer than this stops being a choice.
MAX_CATEGORIES: Final = 60
VIEW: Final = "platform.staff_catalog.view"


def _counts() -> dict[str, int]:
    """How many products carry each category text (archived ones included)."""
    from workeasy360.commercial.catalog.models import Product

    return {
        row["category"]: row["n"]
        for row in Product.objects.exclude(category="")
        .values("category")
        .annotate(n=Count("id"))
    }


def _rows() -> list[Any]:
    from workeasy360.commercial.catalog.models import ProductCategory

    return list(ProductCategory.objects.order_by("sort_order", Lower("name")))


def options() -> list[str]:
    """The category field's choices, in the list's order, then any category a
    product already carries that is not on the list (so editing that product
    never loses it)."""
    names = [row.name for row in _rows()]
    listed = set(names)
    return [*names, *sorted(set(_counts()) - listed, key=str.lower)]


def _json(row: Any, counts: dict[str, int]) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "products": counts.get(row.name, 0),
    }


@in_scope
def list_categories(ctx: TenantContext) -> dict[str, Any]:
    """The list, each with how many products are in it, and whether this
    person may change it."""
    authz.require(ctx, VIEW)
    counts = _counts()
    rows = _rows()
    listed = {row.name for row in rows}
    return {
        "categories": [_json(row, counts) for row in rows],
        # In use on a product but not on the list (set before the list existed).
        "unlisted": [
            {"name": name, "products": counts[name]}
            for name in sorted(set(counts) - listed, key=str.lower)
        ],
        "max_categories": MAX_CATEGORIES,
        "can_manage": authz.check(ctx, "platform.staff_catalog.manage").allowed,
    }


def _clean_name(raw: Any) -> str:
    try:
        name = validate_category(raw)
    except CatalogValidationError as exc:
        raise CatalogValidationError(exc.message, field="name") from exc
    if not name:
        raise CatalogValidationError("Name the category.", field="name")
    return name


def _assert_unique(name: str, *, except_id: UUID | None = None) -> None:
    from workeasy360.commercial.catalog.models import ProductCategory

    clash = ProductCategory.objects.filter(name__iexact=name)
    if except_id is not None:
        clash = clash.exclude(id=except_id)
    if clash.exists():
        raise CatalogValidationError(
            f"There is already a category called {name!r}.", field="name"
        )


@in_scope
def create_category(ctx: TenantContext, *, name: Any) -> dict[str, Any]:
    """Add a category at the end of the list."""
    from workeasy360.commercial.catalog.models import ProductCategory

    _authorise(ctx, "catalog.category.create")
    clean = _clean_name(name)
    with transaction.atomic():
        # One writer at a time: the count and the position read below hold.
        list(ProductCategory.objects.select_for_update().values_list("id", flat=True))
        if ProductCategory.objects.count() >= MAX_CATEGORIES:
            raise CatalogValidationError(
                f"The list holds at most {MAX_CATEGORIES} categories. Delete one first.",
                field="name",
            )
        _assert_unique(clean)
        last = ProductCategory.objects.aggregate(m=Max("sort_order"))["m"]
        row = ProductCategory.objects.create(
            name=clean, sort_order=0 if last is None else last + 1
        )
        audit.record(
            ctx,
            "commercial.product_category.created",
            resource_type="catalog.product_category",
            resource_id=row.id,
            changes={"name": clean},
        )
    return _json(row, _counts())


@in_scope
def rename_category(
    ctx: TenantContext, *, category_id: UUID, name: Any
) -> dict[str, Any]:
    """Rename a category, and with it every product that is in it."""
    from django.utils import timezone

    from workeasy360.commercial.catalog.models import Product, ProductCategory

    _authorise(ctx, "catalog.category.rename")
    clean = _clean_name(name)
    with transaction.atomic():
        row = _one(ProductCategory.objects.select_for_update(), id=category_id)
        if row.name == clean:
            return _json(row, _counts())
        _assert_unique(clean, except_id=row.id)
        before = row.name
        row.name = clean
        row.save(update_fields=["name", "updated_at"])
        moved = Product.objects.filter(category=before).update(
            category=clean, updated_at=timezone.now()
        )
        if moved:
            refresh_public_catalog()
        audit.record(
            ctx,
            "commercial.product_category.renamed",
            resource_type="catalog.product_category",
            resource_id=row.id,
            changes={"name": {"from": before, "to": clean}, "products": moved},
        )
    return _json(row, _counts())


@in_scope
def delete_category(
    ctx: TenantContext, *, category_id: UUID, move_to: Any = None
) -> dict[str, Any]:
    """Delete a category. When products are in it, ``move_to`` (another
    category's id) says where they go; without it the delete is refused."""
    from django.utils import timezone

    from workeasy360.commercial.catalog.models import Product, ProductCategory

    _authorise(ctx, "catalog.category.delete")
    with transaction.atomic():
        row = _one(ProductCategory.objects.select_for_update(), id=category_id)
        in_it = Product.objects.filter(category=row.name)
        count = in_it.count()
        target = None
        if move_to not in (None, ""):
            try:
                target_id = UUID(str(move_to))
            except ValueError as exc:
                raise CatalogValidationError(
                    "Choose a category to move the products to.", field="move_to"
                ) from exc
            if target_id == row.id:
                raise CatalogValidationError(
                    "Move the products to a different category.", field="move_to"
                )
            target = _one(ProductCategory.objects.all(), id=target_id)
        if count and target is None:
            plural = "product is" if count == 1 else "products are"
            raise CatalogValidationError(
                f"{count} {plural} in {row.name!r}. Choose a category to move "
                f"{'it' if count == 1 else 'them'} to.",
                field="move_to",
            )
        if count and target is not None:
            in_it.update(category=target.name, updated_at=timezone.now())
            refresh_public_catalog()
        audit.record(
            ctx,
            "commercial.product_category.deleted",
            resource_type="catalog.product_category",
            resource_id=row.id,
            changes={
                "name": row.name,
                "products_moved": count,
                **({"moved_to": target.name} if count and target is not None else {}),
            },
        )
        row.delete()
    listed: dict[str, Any] = list_categories(ctx)
    return listed


@in_scope
def reorder_categories(ctx: TenantContext, *, order: Any) -> dict[str, Any]:
    """Put the list in exactly the order given: every category, each once."""
    from workeasy360.commercial.catalog.models import ProductCategory

    _authorise(ctx, "catalog.category.reorder")
    if not isinstance(order, list) or not all(isinstance(i, str) for i in order):
        raise CatalogValidationError("order is a list of category ids.", field="order")
    with transaction.atomic():
        rows = {str(r.id): r for r in ProductCategory.objects.select_for_update()}
        if sorted(order) != sorted(rows) or len(set(order)) != len(order):
            raise CatalogValidationError(
                "order must name every category exactly once.", field="order"
            )
        for position, category_id in enumerate(order):
            row = rows[category_id]
            if row.sort_order != position:
                row.sort_order = position
                row.save(update_fields=["sort_order", "updated_at"])
        audit.record(
            ctx,
            "commercial.product_category.reordered",
            resource_type="catalog.product_category",
            resource_id="",
            changes={"order": [rows[i].name for i in order]},
        )
    listed: dict[str, Any] = list_categories(ctx)
    return listed

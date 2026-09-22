"""The ``ProductDirectory`` implementation, registered into contracts at ready.

The concrete half of the runtime-registry pattern in
``contracts/ports/catalog.py``, and the same shape as
``platform/organizations/resolver.py`` and
``platform/entitlements/apps.py``: the module that *owns* the data supplies
the implementation, and the caller reaches it through the port without ever
naming this class.

That indirection is what lets ``platform/ecosystem`` verify a product code
against the catalog while F-4 still forbids ``platform -> commercial``. The
platform asks "is this a product?"; it does not learn what the product costs,
which plans include it, or whether anybody has bought it.
"""

from __future__ import annotations

from workeasy360.contracts.enums import LicensingMode, ProductStatus
from workeasy360.contracts.ports.catalog import ProductDescriptor
from workeasy360.contracts.values.identifiers import ProductCode

#: What the launcher may show a customer. Not ``draft`` (unannounced) and not
#: ``retired`` (withdrawn) - see ``ProductDirectory.list_marketable``.
_MARKETABLE_STATUSES = (ProductStatus.AVAILABLE.value, ProductStatus.DEPRECATED.value)

_FIELDS = ("code", "name", "description", "licensing_mode")


class CatalogProductDirectory:
    """Answers "is this code a sellable SKU, and how is it licensed?" from
    ``catalog.Product``.

    ``get`` is deliberately indifferent to ``ProductStatus``. A product
    integration is registered at *deploy* time, which is normally before the
    SKU is ``available`` - refusing to register against a ``draft`` product
    would force the commercial launch to precede the technical one, which is
    backwards. Whether anyone may *use* the product is the entitlement
    gate's answer, not this one's. ``list_marketable`` is the one place
    status does matter, because it drives what a customer sees.
    """

    def get(self, code: ProductCode) -> ProductDescriptor | None:
        from workeasy360.commercial.catalog.models import Product

        row = Product.objects.filter(code=str(code)).values(*_FIELDS).first()
        if row is None:
            return None
        return _descriptor(row)

    def list_marketable(self) -> tuple[ProductDescriptor, ...]:
        from workeasy360.commercial.catalog.models import Product

        rows = Product.objects.filter(status__in=_MARKETABLE_STATUSES).values(*_FIELDS)
        return tuple(_descriptor(row) for row in rows)


def _descriptor(row: dict) -> ProductDescriptor:
    return ProductDescriptor(
        code=ProductCode(row["code"]),
        display_name=row["name"],
        description=row["description"],
        licensing_mode=LicensingMode(row["licensing_mode"]),
    )


def register() -> None:
    """Called from ``CatalogConfig.ready()``."""
    from workeasy360.contracts.ports.catalog import register_product_directory

    register_product_directory(CatalogProductDirectory())

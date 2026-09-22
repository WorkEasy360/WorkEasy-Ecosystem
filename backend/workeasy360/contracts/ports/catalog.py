"""The catalog read contract - "is this product code a real SKU?".

One question, asked by one caller, for one reason: ``platform/ecosystem``
must refuse to register an integration for a product code the business does
not sell. Registering ``crmm`` instead of ``crm`` would otherwise create an
integration that authenticates nobody - every entitlement check against a
code no plan grants denies - and the resulting support ticket says "SSO is
broken" rather than "the code is misspelt".

Why a port rather than an import: ``platform/`` may not import
``commercial/`` (F-4). That rule is not incidental here - it is the reason a
product never learns *how* it was purchased. The runtime registry keeps the
rule intact while still letting the platform ask the catalog a question, and
it is the identical shape ``ports/entitlements.py`` and
``platform/observability/health.py`` already use
(16-ecosystem-product-boundary.md section 4.1, frozen decision 5).

Deliberately **not** a general catalog API. A second method here would be a
second reason for the platform to care about commerce, and the entire
entitlement design exists so that it does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from workeasy360.contracts.values.identifiers import ProductCode


@dataclass(frozen=True, slots=True)
class ProductDescriptor:
    """The minimum the ecosystem needs to know about a sellable product.

    ``display_name`` is here so the registry does not store a second copy of
    the product's name - one of the two would drift, and the launcher would
    then show a different name from the invoice.
    """

    code: ProductCode
    display_name: str


@runtime_checkable
class ProductDirectory(Protocol):
    """Implemented by ``commercial/catalog``, registered at app-ready."""

    def get(self, code: ProductCode) -> ProductDescriptor | None:
        """The product with this code, or None. Never raises for a miss."""
        ...


# --- runtime registry -------------------------------------------------------

_directory: ProductDirectory | None = None


def register_product_directory(directory: ProductDirectory) -> None:
    """Install the concrete directory. Called once, from
    ``commercial.catalog``'s ``AppConfig.ready()``."""
    global _directory
    _directory = directory


def get_product_directory() -> ProductDirectory:
    """The registered directory.

    Raises rather than returning None, exactly as ``get_gate()`` does. A
    registration path that could not verify a product code must fail loudly;
    quietly accepting any string would turn a typo into a permanently broken
    integration that looks correct in every listing.
    """
    if _directory is None:
        raise RuntimeError(
            "No ProductDirectory is registered. commercial.catalog must be "
            "installed and its AppConfig.ready() must have run before a "
            "product integration can be registered."
        )
    return _directory


def reset_product_directory() -> None:
    """Test hook only."""
    global _directory
    _directory = None

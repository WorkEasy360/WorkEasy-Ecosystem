"""The catalog read contract - "is this product code a real SKU, and how is
it licensed?".

Two questions, asked by two callers, for two reasons:

- ``platform/ecosystem`` must refuse to register an integration for a product
  code the business does not sell. Registering ``crmm`` instead of ``crm``
  would otherwise create an integration that authenticates nobody - every
  entitlement check against a code no plan grants denies - and the resulting
  support ticket says "SSO is broken" rather than "the code is misspelt".
- ``platform/launcher`` must know a product's *licensing mode* to decide
  whether to enforce a seat, and must list what is marketable to render the
  app directory without a hardcoded product list (Phase 4, Task 21/Task 10).

Why a port rather than an import: ``platform/`` may not import
``commercial/`` (F-4). That rule is not incidental here - it is the reason a
product never learns *how* it was purchased. The runtime registry keeps the
rule intact while still letting the platform ask the catalog a question, and
it is the identical shape ``ports/entitlements.py`` and
``platform/observability/health.py`` already use
(16-ecosystem-product-boundary.md section 4.1, frozen decision 5).

**Still deliberately narrow.** ``licensing_mode`` and ``description`` are
descriptive, not commercial: nothing here names a plan, a price, a bundle or
a partner book. A second method that answered a *pricing* question would be a
reason for the platform to care about commerce, and the entire entitlement
design exists so that it does not (ADR frozen decision, see
docs/phase-4/04-seat-enforcement.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from workeasy360.contracts.enums import LicensingMode
from workeasy360.contracts.values.identifiers import ProductCode


@dataclass(frozen=True, slots=True)
class ProductDescriptor:
    """The minimum the ecosystem and the launcher need to know about a
    sellable product.

    ``display_name`` and ``description`` are here so the registry and the
    launcher do not store a second copy of them - a second copy is a copy
    that drifts, and the launcher would then show different text from the
    invoice.

    ``licensing_mode`` is intrinsic to the product (ADR-005 /
    contracts/enums.py: "how a product's value is consumed"), not to any
    plan a customer bought, which is exactly why the launcher may read it
    through this port without learning anything about pricing.
    """

    code: ProductCode
    display_name: str
    description: str = ""
    licensing_mode: LicensingMode = LicensingMode.PER_SEAT


@runtime_checkable
class ProductDirectory(Protocol):
    """Implemented by ``commercial/catalog``, registered at app-ready."""

    def get(self, code: ProductCode) -> ProductDescriptor | None:
        """The product with this code, or None. Never raises for a miss."""
        ...

    def list_marketable(self) -> tuple[ProductDescriptor, ...]:
        """Every product a customer may currently see or already holds.

        Excludes ``draft`` (unannounced) and ``retired`` (withdrawn) SKUs;
        includes ``available`` and ``deprecated`` (existing holders still see
        it). This is what lets the app directory be data-driven rather than
        a hardcoded product list (Phase 4, Task 21).
        """
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

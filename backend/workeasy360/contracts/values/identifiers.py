"""Typed identifiers for things that are strings on the wire.

These are thin ``str`` subclasses with validation at construction. The point is
not type-safety theatre: it is that ``ProductCode``, ``FeatureKey`` and
``PermissionKey`` have *format rules* that must be enforced in a single place,
because a mistyped feature key silently grants or denies paid access.
"""

from __future__ import annotations

import re
from typing import Final

_PRODUCT_CODE_RE: Final = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_FEATURE_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,3}$")
_PERMISSION_KEY_RE: Final = re.compile(
    r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
)


class ProductCode(str):
    """A sellable product's stable code: ``crm``, ``books``.

    A string rather than a foreign key, deliberately. ``catalog.Product`` is a
    sellable SKU; ``products/crm`` is code. An FK from catalog into product
    modules would make catalog depend on every product, and adding a product
    would require a catalog migration (01-domain-boundaries.md section 4).
    """

    __slots__ = ()

    def __new__(cls, value: str) -> ProductCode:
        if not _PRODUCT_CODE_RE.match(value):
            raise ValueError(
                f"Invalid product code {value!r}: expected lowercase "
                f"alphanumeric/underscore, 2-32 characters."
            )
        return super().__new__(cls, value)


class FeatureKey(str):
    """A sellable capability: ``crm.pipelines.max``.

    Format ``<product>.<area>.<name>``. The leading segment must be a
    registered product code; a system check asserts that at boot so a typo
    fails at startup rather than in production (W360.E005).
    """

    __slots__ = ()

    def __new__(cls, value: str) -> FeatureKey:
        if not _FEATURE_KEY_RE.match(value):
            raise ValueError(
                f"Invalid feature key {value!r}: expected '<product>.<area>.<name>'."
            )
        return super().__new__(cls, value)

    @property
    def product(self) -> str:
        return self.split(".", 1)[0]


class PermissionKey(str):
    """An authorization permission: ``crm.deal.update``.

    The format is mechanical - ``<product>.<resource>.<action>`` - so the full
    privilege surface of the platform is greppable. Platform permissions use
    the ``platform.*`` prefix (04-identity-authz.md section 4.2).
    """

    __slots__ = ()

    def __new__(cls, value: str) -> PermissionKey:
        if not _PERMISSION_KEY_RE.match(value):
            raise ValueError(
                f"Invalid permission key {value!r}: expected "
                f"'<product>.<resource>.<action>'."
            )
        return super().__new__(cls, value)

    @property
    def product(self) -> str:
        return self.split(".", 1)[0]

    @property
    def resource(self) -> str:
        return self.split(".")[1]

    @property
    def action(self) -> str:
        return self.split(".")[2]


PLATFORM_PRODUCT_CODE: Final = ProductCode("platform")

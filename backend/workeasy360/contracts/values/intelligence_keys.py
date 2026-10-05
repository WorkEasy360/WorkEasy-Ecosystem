"""Who owns which AI skill and analytics metric name (Phase 8, Tasks 14, 17, 64).

The same structural rule Phase 6 applies to event types
(``contracts/events/namespaces.py``), applied to the two new kinds of name a
product registers:

- a **skill** - a versioned AI contract a product invokes through the gateway:
  ``product.<code>.<name>`` (``product.crm.deal_summary``);
- a **metric** - a versioned analytics definition over a product's events:
  ``product.<code>.<name>`` (``product.crm.pipeline_value``).

``platform.<name>`` belongs to Workeasy360 alone. A product's namespace is
derived from its **authenticated** product code, so ``product.books.*`` can
only ever be registered by the credential registered for ``books``; there is
no request field in which a product could claim to be another product.

Pure and Django-free, like every contract: a product's own test suite, a
documentation generator or a conformance harness can validate a name without
the platform present.
"""

from __future__ import annotations

import re
from typing import Final

PRODUCT_ROOT: Final = "product"
PLATFORM_ROOT: Final = "platform"

MAX_KEY_LENGTH: Final = 128
MAX_VERSION: Final = 999

_SEGMENT: Final = r"[a-z][a-z0-9_]{0,39}"
_PRODUCT_CODE: Final = r"[a-z][a-z0-9_]{1,31}"

#: ``product.<code>.<name>`` with up to two further segments
#: (``product.crm.deal.summary``) - enough to group, bounded so a key cannot
#: be a data channel.
_PRODUCT_KEY_RE: Final = re.compile(
    rf"^{PRODUCT_ROOT}\.(?P<code>{_PRODUCT_CODE})\.{_SEGMENT}(\.{_SEGMENT}){{0,2}}\Z"
)
_PLATFORM_KEY_RE: Final = re.compile(
    rf"^{PLATFORM_ROOT}\.{_SEGMENT}(\.{_SEGMENT}){{0,2}}\Z"
)
_SLUG_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}\Z")
_PRODUCT_CODE_RE: Final = re.compile(rf"^{_PRODUCT_CODE}\Z")


def owner_of(key: str) -> str | None:
    """The owning product code of ``key``, ``"platform"`` for a platform key,
    or ``None`` when ``key`` is not a well-formed key at all."""
    if not isinstance(key, str) or len(key) > MAX_KEY_LENGTH:
        return None
    match = _PRODUCT_KEY_RE.match(key)
    if match is not None:
        return match.group("code")
    if _PLATFORM_KEY_RE.match(key):
        return PLATFORM_ROOT
    return None


def product_owns_key(product_code: str, key: str) -> bool:
    """The ownership rule: a product may register only ``product.<its code>.*``
    names, never another product's and never ``platform.*``."""
    return _PRODUCT_KEY_RE.match(key or "") is not None and owner_of(key) == product_code


def is_platform_key(key: str) -> bool:
    return owner_of(key) == PLATFORM_ROOT


def is_slug(value: str) -> bool:
    """A short lowercase identifier: source keys, dimension names and values."""
    return isinstance(value, str) and _SLUG_RE.match(value) is not None


def is_product_code(value: str) -> bool:
    return isinstance(value, str) and _PRODUCT_CODE_RE.match(value) is not None


def versioned_ref(key: str, version: int) -> str:
    """``<key>.v<n>`` - how a registered version is named in logs and docs."""
    return f"{key}.v{int(version)}"

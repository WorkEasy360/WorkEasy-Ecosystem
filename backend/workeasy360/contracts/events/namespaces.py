"""Event-type namespaces: who owns which event names (Phase 6, Tasks 6/9).

Two families of event travel through the integration platform, and they are
owned by different parties:

**Workeasy360-owned.** Platform, commercial and integration facts. Their
schemas are declared in ``contracts/events/`` and validated by the registry
(``organizations.membership.added``, ``integration.webhook.pinged``). No
external party may publish one - an event claiming to be
``platform.identity.*`` from a product is an impersonation attempt, not a
namespacing mistake.

**Product-owned.** ``product.<product_code>.<aggregate>.<verb>`` and nothing
else. The product owns the name, the version and the meaning; Workeasy360
routes it as an opaque, bounded, versioned envelope and never declares its
schema here (A14 - contracts/ declares no product-domain event schema). The
product *registers* the names it publishes in its manifest
(``ProductManifest.events``), which is the contract that makes an event
routable at all.

Ownership is structural rather than checked by list: a product's namespace is
derived from its authenticated ``product_code``, so ``product.crm.*`` can only
ever be published by the credential registered for ``crm``. There is no
request field in which a product could claim another product's code.

Filters (what a subscription or an automation trigger matches) have exactly
two forms, deliberately: one exact versioned type, or every event from one
product. Anything richer - arbitrary globs, regexes - would make matching a
scan instead of an indexed ``IN`` over at most two candidate strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: Roots only Workeasy360 itself may publish under. ``organizations``,
#: ``identity`` and ``entitlements`` are the Phase 1 platform roots, named
#: before the ``platform.`` convention existed; they are owned all the same.
PLATFORM_OWNED_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "platform",
        "commercial",
        "integration",
        "organizations",
        "identity",
        "entitlements",
    }
)

#: The one root an external product may publish under.
PRODUCT_ROOT: Final = "product"

MAX_EVENT_TYPE_LENGTH: Final = 128
MAX_EVENT_VERSION: Final = 999

_SEGMENT: Final = r"[a-z][a-z0-9_]{0,39}"
_PRODUCT_CODE: Final = r"[a-z][a-z0-9_]{1,31}"

#: ``product.<code>.<aggregate>.<verb>`` with up to two extra qualifying
#: segments - enough for ``product.books.invoice.line.added`` and bounded so
#: a name cannot be a data channel.
PRODUCT_EVENT_TYPE_RE: Final = re.compile(
    rf"^{PRODUCT_ROOT}\.(?P<code>{_PRODUCT_CODE})\.{_SEGMENT}(\.{_SEGMENT}){{1,3}}\Z"
)

#: A Workeasy360-owned type: the contracts registry's three-segment grammar.
PLATFORM_EVENT_TYPE_RE: Final = re.compile(rf"^{_SEGMENT}\.{_SEGMENT}\.{_SEGMENT}\Z")

_REF_RE: Final = re.compile(r"^(?P<type>[a-z0-9_.]+)\.v(?P<version>[1-9][0-9]{0,2})\Z")
_NAMESPACE_FILTER_RE: Final = re.compile(
    rf"^{PRODUCT_ROOT}\.(?P<code>{_PRODUCT_CODE})\.\*\Z"
)


def product_namespace(product_code: str) -> str:
    """The prefix every event of ``product_code`` must carry."""
    return f"{PRODUCT_ROOT}.{product_code}."


def product_code_of(event_type: str) -> str | None:
    """The owning product of a product event type, or ``None`` when the type
    is not a well-formed product event type at all."""
    if len(event_type) > MAX_EVENT_TYPE_LENGTH:
        return None
    match = PRODUCT_EVENT_TYPE_RE.match(event_type)
    return match.group("code") if match else None


def is_platform_owned(event_type: str) -> bool:
    """Whether ``event_type`` sits under a Workeasy360-owned root."""
    return event_type.split(".", 1)[0] in PLATFORM_OWNED_ROOTS


def product_owns(product_code: str, event_type: str) -> bool:
    """The namespace-ownership rule: a product may publish only its own
    ``product.<code>.*`` types, and never a platform-owned one."""
    return product_code_of(event_type) == product_code


def event_ref(event_type: str, version: int) -> str:
    """``<type>.v<n>`` - the canonical reference a filter matches."""
    return f"{event_type}.v{int(version)}"


def parse_event_ref(ref: str) -> tuple[str, int] | None:
    """``product.x.record.created.v1`` -> ``("product.x.record.created", 1)``."""
    match = _REF_RE.match(ref or "")
    if match is None:
        return None
    return match.group("type"), int(match.group("version"))


@dataclass(frozen=True, slots=True)
class EventFilter:
    """A validated subscription/trigger filter."""

    value: str
    #: ``exact`` - one versioned type; ``namespace`` - every event of a product.
    kind: str
    product_code: str | None
    event_type: str | None = None
    version: int | None = None


def parse_event_filter(value: str) -> EventFilter | None:
    """Parse a filter, or ``None`` when it has neither permitted form."""
    if not isinstance(value, str) or len(value) > MAX_EVENT_TYPE_LENGTH + 5:
        return None
    namespace = _NAMESPACE_FILTER_RE.match(value)
    if namespace is not None:
        return EventFilter(
            value=value, kind="namespace", product_code=namespace.group("code")
        )
    parsed = parse_event_ref(value)
    if parsed is None:
        return None
    event_type, version = parsed
    code = product_code_of(event_type)
    if code is None and not (
        PLATFORM_EVENT_TYPE_RE.match(event_type) and is_platform_owned(event_type)
    ):
        return None
    return EventFilter(
        value=value,
        kind="exact",
        product_code=code,
        event_type=event_type,
        version=version,
    )


def candidate_filters(event_type: str, version: int) -> tuple[str, ...]:
    """Every filter string that matches this event - at most two.

    This is what lets matching be one indexed ``filter IN (...)`` query no
    matter how many subscriptions or rules an organization has (Tasks 73/74).
    """
    exact = event_ref(event_type, version)
    code = product_code_of(event_type)
    if code is None:
        return (exact,)
    return (exact, f"{product_namespace(code)}*")

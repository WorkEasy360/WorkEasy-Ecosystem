"""Catalog rules as pure Python. No Django, no I/O, no database (G3).

Everything here is a total function of its arguments, so the rules are
testable in microseconds and cannot accidentally acquire a query. The ORM
services above call in; nothing here calls out.
"""

from __future__ import annotations

from typing import Any, Final

from workeasy360.contracts.enums import CatalogStatus, FeatureKind, ProductStatus
from workeasy360.contracts.errors import (
    CatalogValidationError,
    InvalidStateTransition,
)
from workeasy360.contracts.ports.entitlements import UNLIMITED
from workeasy360.contracts.values.identifiers import FeatureKey, ProductCode

#: Legal product lifecycle moves. Written as an explicit table rather than as
#: ``if`` statements: the table *is* the specification, a reviewer can read it
#: in one glance, and a missing edge is visible rather than implied.
#:
#: Note what is absent. ``retired -> available`` does not exist: resurrecting
#: a withdrawn SKU under the same code makes historical reporting ambiguous
#: about which product a subscription referred to. Reintroduction is a new
#: code.
PRODUCT_TRANSITIONS: Final[dict[ProductStatus, frozenset[ProductStatus]]] = {
    ProductStatus.DRAFT: frozenset({ProductStatus.AVAILABLE, ProductStatus.RETIRED}),
    ProductStatus.AVAILABLE: frozenset({ProductStatus.DEPRECATED}),
    ProductStatus.DEPRECATED: frozenset({ProductStatus.AVAILABLE, ProductStatus.RETIRED}),
    ProductStatus.RETIRED: frozenset(),
}

#: Shared by ``Plan`` and ``Bundle``.
#:
#: ``grandfathered -> available`` is permitted because re-opening a plan to
#: new customers is a normal commercial decision and costs nothing; the plan's
#: published versions are unchanged either way.
CATALOG_TRANSITIONS: Final[dict[CatalogStatus, frozenset[CatalogStatus]]] = {
    CatalogStatus.DRAFT: frozenset({CatalogStatus.AVAILABLE, CatalogStatus.RETIRED}),
    CatalogStatus.AVAILABLE: frozenset(
        {CatalogStatus.GRANDFATHERED, CatalogStatus.RETIRED}
    ),
    CatalogStatus.GRANDFATHERED: frozenset(
        {CatalogStatus.AVAILABLE, CatalogStatus.RETIRED}
    ),
    CatalogStatus.RETIRED: frozenset(),
}


def assert_product_transition(current: ProductStatus, target: ProductStatus) -> None:
    """Raise unless the move is on the table. Self-transitions are no-ops."""
    if current is target:
        return
    if target not in PRODUCT_TRANSITIONS[current]:
        raise InvalidStateTransition(
            entity="catalog.Product",
            from_status=current.value,
            to_status=target.value,
        )


def assert_catalog_transition(current: CatalogStatus, target: CatalogStatus) -> None:
    if current is target:
        return
    if target not in CATALOG_TRANSITIONS[current]:
        raise InvalidStateTransition(
            entity="catalog.Plan/Bundle",
            from_status=current.value,
            to_status=target.value,
        )


def validate_product_code(code: str) -> ProductCode:
    """Enforce the stable-identifier format.

    Raised as a catalog validation error rather than letting ``ValueError``
    escape, so the API layer maps it to 400 with a code the UI can branch on.
    """
    try:
        return ProductCode(code)
    except ValueError as exc:
        raise CatalogValidationError(str(exc), field="code") from exc


def validate_feature_key(key: str, *, product_code: str) -> FeatureKey:
    """A feature key must be well-formed **and** belong to its product.

    The second half is the one that matters. ``crm.pipelines.max`` declared
    under the Books product would be sold by Books plans and checked by CRM
    code, and the mismatch would surface as "the customer paid and it still
    says not entitled".
    """
    try:
        parsed = FeatureKey(key)
    except ValueError as exc:
        raise CatalogValidationError(str(exc), field="key") from exc

    if parsed.product != product_code:
        raise CatalogValidationError(
            f"Feature key {key!r} starts with {parsed.product!r} but is "
            f"declared under product {product_code!r}. The leading segment is "
            f"the owning product and the two must agree.",
            field="key",
        )
    return parsed


def validate_feature_value(
    *, key: str, kind: FeatureKind, value: Any, enum_ordering: list[str] | None = None
) -> None:
    """Check a plan's stated value against the feature's declared kind.

    Done here rather than by a database constraint because the rule depends
    on another row's ``kind``, and done at all because a quota stored as the
    string ``"50"`` compares as ``"50" >= 10`` - which is True for the wrong
    reason and is exactly the sort of defect that only appears at a customer's
    limit.
    """
    if kind is FeatureKind.BOOLEAN:
        if not isinstance(value, bool):
            raise CatalogValidationError(
                f"Feature {key!r} is boolean; got {type(value).__name__}.",
                field="value",
            )
        return

    if kind is FeatureKind.QUOTA:
        # bool is a subclass of int, so it must be excluded explicitly or
        # ``True`` silently becomes a quota of one.
        if isinstance(value, bool) or not isinstance(value, int):
            raise CatalogValidationError(
                f"Feature {key!r} is a quota; got {type(value).__name__}. Use "
                f"an integer, or {UNLIMITED} for unlimited.",
                field="value",
            )
        if value < UNLIMITED:
            raise CatalogValidationError(
                f"Feature {key!r}: {value} is not a valid quota. Use a "
                f"non-negative integer, or {UNLIMITED} for unlimited.",
                field="value",
            )
        return

    if not isinstance(value, str):
        raise CatalogValidationError(
            f"Feature {key!r} is an enum; got {type(value).__name__}.", field="value"
        )
    if enum_ordering and value not in enum_ordering:
        raise CatalogValidationError(
            f"Feature {key!r}: {value!r} is not one of {enum_ordering}.", field="value"
        )


def compare_plan_versions(
    *,
    from_seats: int,
    to_seats: int,
    from_features: dict[str, Any],
    to_features: dict[str, Any],
) -> str:
    """Classify a plan change as ``upgrade``, ``downgrade`` or ``lateral``.

    Used to pick the default effective timing, and recorded on the event so a
    consumer does not have to re-derive it.

    The rule: a change is a downgrade if **anything** gets smaller - fewer
    seats, a feature withdrawn, or a quota reduced. Only if nothing shrinks
    and something grows is it an upgrade. Classifying a mixed change as an
    upgrade would apply it immediately and silently remove capability the
    customer is still paying for.
    """
    shrinks = to_seats < from_seats
    grows = to_seats > from_seats

    for key, old in from_features.items():
        new = to_features.get(key)
        if new is None:
            shrinks = True
            continue
        if _is_less_generous(new, old):
            shrinks = True
        elif _is_less_generous(old, new):
            grows = True

    if any(key not in from_features for key in to_features):
        grows = True

    if shrinks:
        return "downgrade"
    return "upgrade" if grows else "lateral"


def _is_less_generous(candidate: Any, reference: Any) -> bool:
    """Whether ``candidate`` grants strictly less than ``reference``.

    Mirrors the entitlement resolver's ordering so that "this is a downgrade"
    and "this reduces what the runtime allows" cannot disagree. Values of
    different shapes are treated as incomparable rather than guessed at.
    """
    if isinstance(candidate, bool) or isinstance(reference, bool):
        return bool(reference) and not bool(candidate)
    if isinstance(candidate, int) and isinstance(reference, int):
        if reference == UNLIMITED:
            return candidate != UNLIMITED
        if candidate == UNLIMITED:
            return False
        return candidate < reference
    return False

"""Catalog rules as pure Python. No Django, no I/O, no database (G3).

Everything here is a total function of its arguments, so the rules are
testable in microseconds and cannot accidentally acquire a query. The ORM
services above call in; nothing here calls out.
"""

from __future__ import annotations

from typing import Any, Final

from workeasy360.contracts.enums import (
    CatalogStatus,
    FeatureKind,
    ProductListing,
    ProductStatus,
    PublicAvailability,
)
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


# --- Phase 9: public presentation --------------------------------------------

#: A URL slug: lower-case words joined by single hyphens.
_SLUG_ALPHABET: Final = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
MAX_SLUG: Final = 64
MAX_TAGLINE: Final = 160
MAX_MARKETING_URL: Final = 500


def public_availability(
    status: ProductStatus, listing: ProductListing
) -> PublicAvailability:
    """The one mapping from lifecycle and listing to what a visitor sees.

    ====================  ==========================================
    listing ``hidden``    private (whatever the status)
    deprecated/retired    disabled - existing holders keep it
    ``coming_soon``       coming soon (draft or available)
    available + listed    available
    draft + listed        draft - not announced yet
    ====================  ==========================================
    """
    if listing is ProductListing.HIDDEN:
        return PublicAvailability.PRIVATE
    if status in (ProductStatus.DEPRECATED, ProductStatus.RETIRED):
        return PublicAvailability.DISABLED
    if listing is ProductListing.COMING_SOON:
        return PublicAvailability.COMING_SOON
    if status is ProductStatus.AVAILABLE:
        return PublicAvailability.AVAILABLE
    return PublicAvailability.DRAFT


def validate_slug(raw: Any) -> str:
    slug = str(raw or "").strip()
    if (
        not slug
        or len(slug) > MAX_SLUG
        or not set(slug) <= _SLUG_ALPHABET
        or slug.startswith("-")
        or slug.endswith("-")
        or "--" in slug
    ):
        raise CatalogValidationError(
            "A slug is lower-case letters and digits in words joined by single "
            f"hyphens, at most {MAX_SLUG} characters (for example "
            "whatsapp-marketing).",
            field="slug",
        )
    return slug


def slug_for_code(code: str) -> str:
    """The default slug for a product code (``whatsapp_marketing`` ->
    ``whatsapp-marketing``)."""
    return "-".join(part for part in code.lower().replace("_", "-").split("-") if part)


def validate_tagline(raw: Any) -> str:
    text = " ".join(str(raw or "").split())
    if len(text) > MAX_TAGLINE:
        raise CatalogValidationError(
            f"The tagline is at most {MAX_TAGLINE} characters.", field="tagline"
        )
    return text


#: Phase 11: the visual a product card draws. A **key**, never an image or a
#: URL - the frontend maps each key to an inline SVG it owns, so a product can
#: never make a page load a remote image (CSP ``img-src 'self' data:``) and a
#: bad value can never become markup. Named for the picture, not the product,
#: so nothing here (or in the frontend) branches on a product code (E-2).
PRODUCT_ICON_KEYS: Final = frozenset(
    {
        "customers",
        "book",
        "board",
        "headset",
        "box",
        "chart",
        "megaphone",
        "wallet",
        "users",
        "cart",
        "cog",
        "calendar",
        "shield",
        "generic",
    }
)
MAX_CATEGORY: Final = 64
#: A trial is at least a day and at most a year; the platform sets no default.
MIN_TRIAL_DAYS: Final = 1
MAX_TRIAL_DAYS: Final = 365
MAX_TRIAL_SEATS: Final = 100_000
MAX_ELIGIBLE_TRIAL_PLANS: Final = 50


def validate_category(raw: Any) -> str:
    """Free text such as ``Finance / Accounting``: single-spaced, no control
    characters, at most ``MAX_CATEGORY``. It is only ever rendered as text."""
    text = " ".join(str(raw or "").split())
    if len(text) > MAX_CATEGORY or any(ord(ch) < 32 for ch in text):
        raise CatalogValidationError(
            f"The category is at most {MAX_CATEGORY} characters of plain text.",
            field="category",
        )
    return text


def validate_icon(raw: Any) -> str:
    """Empty (the frontend then draws a monogram) or one allow-listed key."""
    key = str(raw or "").strip().lower()
    if key and key not in PRODUCT_ICON_KEYS:
        raise CatalogValidationError(
            "icon is one of: " + ", ".join(sorted(PRODUCT_ICON_KEYS)) + ".",
            field="icon",
        )
    return key


def validate_trial_terms(
    *,
    enabled: Any,
    duration_days: Any,
    seat_limit: Any,
    payment_method_required: Any,
    eligible_plan_codes: Any,
) -> dict[str, Any]:
    """The shape and bounds of one product's trial configuration.

    Pure. Whether the named plans exist, belong to the product and are on sale
    is decided by the service, which can read the catalog.
    """
    if not isinstance(enabled, bool):
        raise CatalogValidationError("enabled is true or false.", field="enabled")
    if (
        isinstance(duration_days, bool)
        or not isinstance(duration_days, int)
        or not MIN_TRIAL_DAYS <= duration_days <= MAX_TRIAL_DAYS
    ):
        raise CatalogValidationError(
            f"duration_days is a whole number from {MIN_TRIAL_DAYS} to {MAX_TRIAL_DAYS}.",
            field="duration_days",
        )
    if seat_limit is not None and (
        isinstance(seat_limit, bool)
        or not isinstance(seat_limit, int)
        or not 1 <= seat_limit <= MAX_TRIAL_SEATS
    ):
        raise CatalogValidationError(
            f"seat_limit is empty (the plan's included seats) or a whole number "
            f"from 1 to {MAX_TRIAL_SEATS}.",
            field="seat_limit",
        )
    if not isinstance(payment_method_required, bool):
        raise CatalogValidationError(
            "payment_method_required is true or false.", field="payment_method_required"
        )
    if payment_method_required:
        # Honest, not decorative: there is no stored payment method to require
        # (D-028), so accepting the flag would promise a control that does not
        # exist.
        raise CatalogValidationError(
            "A trial cannot require a payment method yet: the platform stores "
            "none (every invoice is paid through hosted checkout).",
            field="payment_method_required",
        )
    if (
        not isinstance(eligible_plan_codes, list)
        or len(eligible_plan_codes) > MAX_ELIGIBLE_TRIAL_PLANS
        or not all(isinstance(c, str) and 0 < len(c) <= 64 for c in eligible_plan_codes)
    ):
        raise CatalogValidationError(
            "eligible_plan_codes is a list of plan codes.", field="eligible_plan_codes"
        )
    codes = sorted(set(eligible_plan_codes))
    if enabled and not codes:
        raise CatalogValidationError(
            "A trial that is switched on names at least one eligible plan.",
            field="eligible_plan_codes",
        )
    return {
        "enabled": enabled,
        "duration_days": duration_days,
        "seat_limit": seat_limit,
        "payment_method_required": False,
        "eligible_plan_codes": codes,
    }


def validate_marketing_url(raw: Any) -> str:
    """Empty, or an absolute ``https`` address with a host and no credentials.

    It is rendered as a link on the public site, so every other scheme -
    ``javascript:``, ``data:``, ``http:`` - is refused here rather than trusted
    to the frontend.
    """
    from urllib.parse import urlsplit

    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        parts = None
    if (
        parts is None
        or len(value) > MAX_MARKETING_URL
        or parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or any(ch.isspace() or ord(ch) < 32 for ch in value)
    ):
        raise CatalogValidationError(
            "The marketing page must be an https:// address.", field="marketing_url"
        )
    return value

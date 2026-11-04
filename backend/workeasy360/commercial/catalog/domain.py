"""Catalog rules as pure Python. No Django, no I/O, no database (G3).

Everything here is a total function of its arguments, so the rules are
testable in microseconds and cannot accidentally acquire a query. The ORM
services above call in; nothing here calls out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

from workeasy360.contracts.enums import (
    CatalogStatus,
    FeatureKind,
    ProductListing,
    ProductMediaKind,
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
#:
#: ``draft -> deprecated`` is the Product Manager's "Archive" of a product that
#: never went on sale (ADR-051): reversible, unlike ``retired``.
PRODUCT_TRANSITIONS: Final[dict[ProductStatus, frozenset[ProductStatus]]] = {
    ProductStatus.DRAFT: frozenset(
        {ProductStatus.AVAILABLE, ProductStatus.DEPRECATED, ProductStatus.RETIRED}
    ),
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


#: ADR-056 merchandising: the store's tags and the listing's bullet lists.
MAX_TAGS: Final = 12
MAX_TAG: Final = 32
MAX_LIST_ITEMS: Final = 12
MAX_LIST_ITEM: Final = 200
#: The listing's bullet lists, in the order a product page shows them.
MERCHANDISING_LISTS: Final = ("highlights", "benefits", "use_cases")
#: The store's hand-set badges (``New`` is also shown for 30 days after launch;
#: ``best_seller`` is the marketplace's "Best Seller", 2026-11-04).
MERCHANDISING_FLAGS: Final = ("featured", "popular", "badge_new", "best_seller")

#: The marketplace's "Product type" (2026-11-04): what kind of offering a
#: product is. Presentation and filtering only - never an access decision.
PRODUCT_TYPES: Final[dict[str, str]] = {
    "saas": "SaaS Products",
    "ai_tool": "AI Tools",
    "business_suite": "Business Suite",
}
DEFAULT_PRODUCT_TYPE: Final = "saas"
#: The marketplace's "Best for": who a product suits, from a fixed list so
#: the Store's filter offers the same words for every product.
BEST_FOR: Final[dict[str, str]] = {
    "startups": "Startups",
    "small_business": "Small businesses",
    "agencies": "Agencies",
    "enterprises": "Enterprises",
    "freelancers": "Freelancers",
    "ecommerce": "E-commerce",
    "education": "Education",
    "healthcare": "Healthcare",
    "real_estate": "Real estate",
}
#: The apps a product connects to ("WhatsApp", "Razorpay"): short plain text.
MAX_INTEGRATIONS: Final = 12
MAX_INTEGRATION: Final = 32


def validate_product_type(raw: Any) -> str:
    value = str(raw or "").strip()
    if value not in PRODUCT_TYPES:
        raise CatalogValidationError(
            "product_type is one of: " + ", ".join(PRODUCT_TYPES) + ".",
            field="product_type",
        )
    return value


def validate_best_for(raw: Any) -> list[str]:
    """Some of ``BEST_FOR``'s keys, in that list's order, duplicates dropped."""
    if not isinstance(raw, list) or any(not isinstance(v, str) for v in raw):
        raise CatalogValidationError("best_for is a list of choices.", field="best_for")
    unknown = sorted(set(raw) - set(BEST_FOR))
    if unknown:
        raise CatalogValidationError(
            "best_for entries are among: " + ", ".join(BEST_FOR) + ".", field="best_for"
        )
    return [key for key in BEST_FOR if key in raw]


def validate_integrations(raw: Any) -> list[str]:
    """Up to ``MAX_INTEGRATIONS`` app names as typed ("WhatsApp"), duplicates
    (ignoring case) dropped."""
    if not isinstance(raw, list) or len(raw) > MAX_INTEGRATIONS:
        raise CatalogValidationError(
            f"integrations is a list of at most {MAX_INTEGRATIONS} entries.",
            field="integrations",
        )
    kept: list[str] = []
    for item in raw:
        name = _plain(item, field="integrations", maximum=MAX_INTEGRATION)
        if name.lower() not in {k.lower() for k in kept}:
            kept.append(name)
    return kept


#: A product page's video (2026-11-04): a YouTube or Vimeo link. The page never
#: frames the address an admin typed - it frames a fixed player address built
#: from the provider and the id parsed here, so the only framed origins are the
#: two the frontend's CSP ``frame-src`` names.
MAX_VIDEO_URL: Final = 200
_YOUTUBE_HOSTS: Final = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtube-nocookie.com",
    }
)
_VIMEO_HOSTS: Final = frozenset({"vimeo.com", "www.vimeo.com", "player.vimeo.com"})
_YOUTUBE_ID: Final = re.compile(r"[A-Za-z0-9_-]{11}")
_VIMEO_ID: Final = re.compile(r"[0-9]{1,12}")
_VIDEO_ERROR: Final = (
    "The video is a YouTube or Vimeo link, such as "
    "https://www.youtube.com/watch?v=... or https://vimeo.com/123456."
)


def parse_video(raw: Any) -> tuple[str, str] | None:
    """``(provider, id)`` of a YouTube or Vimeo address, or ``None`` when it is
    neither. Accepts the watch, short, embed and shorts forms."""
    value = str(raw or "").strip()
    if (
        not value
        or len(value) > MAX_VIDEO_URL
        or any(ch.isspace() or ord(ch) < 32 for ch in value)
    ):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password or parts.port:
        return None
    host = (parts.hostname or "").lower()
    segments = [seg for seg in parts.path.split("/") if seg]
    if host in _YOUTUBE_HOSTS:
        if host == "youtu.be":
            candidate = segments[0] if len(segments) == 1 else ""
        elif segments == ["watch"]:
            candidate = (parse_qs(parts.query).get("v") or [""])[0]
        elif len(segments) == 2 and segments[0] in {"embed", "shorts", "live"}:
            candidate = segments[1]
        else:
            candidate = ""
        return ("youtube", candidate) if _YOUTUBE_ID.fullmatch(candidate) else None
    if host in _VIMEO_HOSTS:
        if host == "player.vimeo.com":
            candidate = (
                segments[1] if len(segments) == 2 and segments[0] == "video" else ""
            )
        else:
            candidate = segments[0] if len(segments) == 1 else ""
        return ("vimeo", candidate) if _VIMEO_ID.fullmatch(candidate) else None
    return None


def validate_video_url(raw: Any) -> str:
    """Empty, or a YouTube / Vimeo link, stored in its canonical watch form."""
    if not str(raw or "").strip():
        return ""
    parsed = parse_video(raw)
    if parsed is None:
        raise CatalogValidationError(_VIDEO_ERROR, field="video_url")
    provider, video_id = parsed
    if provider == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}"
    return f"https://vimeo.com/{video_id}"


def video_embed(url: Any) -> dict[str, str] | None:
    """What a product page needs to play a stored video: the provider, the id,
    the fixed player address (YouTube's no-cookie host) and the watch link."""
    parsed = parse_video(url)
    if parsed is None:
        return None
    provider, video_id = parsed
    if provider == "youtube":
        return {
            "provider": "youtube",
            "id": video_id,
            "embed_url": f"https://www.youtube-nocookie.com/embed/{video_id}",
            "watch_url": f"https://www.youtube.com/watch?v={video_id}",
        }
    return {
        "provider": "vimeo",
        "id": video_id,
        "embed_url": f"https://player.vimeo.com/video/{video_id}",
        "watch_url": f"https://vimeo.com/{video_id}",
    }


def validate_subcategory(raw: Any) -> str:
    """A second, narrower category (``Invoicing`` under ``Finance``): the same
    plain-text rules as the category."""
    try:
        return validate_category(raw)
    except CatalogValidationError as exc:
        raise CatalogValidationError(
            f"The subcategory is at most {MAX_CATEGORY} characters of plain text.",
            field="subcategory",
        ) from exc


def _plain(item: Any, *, field: str, maximum: int) -> str:
    text = " ".join(str(item if item is not None else "").split())
    if not text or len(text) > maximum or any(ord(ch) < 32 for ch in text):
        raise CatalogValidationError(
            f"Each {field} entry is 1 to {maximum} characters of plain text.",
            field=field,
        )
    return text


def validate_tags(raw: Any) -> list[str]:
    """Up to ``MAX_TAGS`` short search tags, lower-cased, duplicates dropped."""
    if not isinstance(raw, list) or len(raw) > MAX_TAGS:
        raise CatalogValidationError(
            f"tags is a list of at most {MAX_TAGS} entries.", field="tags"
        )
    seen: list[str] = []
    for item in raw:
        tag = _plain(item, field="tags", maximum=MAX_TAG).lower()
        if tag not in seen:
            seen.append(tag)
    return seen


def validate_bullets(raw: Any, *, field: str) -> list[str]:
    """One of the listing's bullet lists: up to ``MAX_LIST_ITEMS`` lines."""
    if not isinstance(raw, list) or len(raw) > MAX_LIST_ITEMS:
        raise CatalogValidationError(
            f"{field} is a list of at most {MAX_LIST_ITEMS} entries.", field=field
        )
    return [_plain(item, field=field, maximum=MAX_LIST_ITEM) for item in raw]


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
    # ADR-051: a trial may be switched on before the product has a plan. With
    # no eligible plan nobody can start it (the offer and the start both need
    # one), so the Product Manager can set a new product's trial up front
    # without the platform inventing which plans it covers.
    return {
        "enabled": enabled,
        "duration_days": duration_days,
        "seat_limit": seat_limit,
        "payment_method_required": False,
        "eligible_plan_codes": codes,
    }


def validate_link_url(raw: Any, *, field: str) -> str:
    """Empty, or an address a store page may link a browser to (ADR-051).

    The integration registry's own URL gate, so a product link and a launch
    URL are judged by one rule: ``https`` only (``javascript:``, ``data:``,
    ``file:`` and plain ``http`` are refused), no credentials, no backslash or
    percent-encoded host, and never loopback, a private network or a metadata
    address - in any environment.
    """
    from workeasy360.contracts.values.manifest import ManifestError, validate_browser_url

    value = str(raw or "").strip()
    if not value:
        return ""
    if len(value) > MAX_MARKETING_URL or any(
        ch.isspace() or ord(ch) < 32 for ch in value
    ):
        raise CatalogValidationError(
            f"{field} must be an https:// address of at most {MAX_MARKETING_URL} characters.",
            field=field,
        )
    try:
        return validate_browser_url(value, field=field)
    except ManifestError as exc:
        raise CatalogValidationError(str(exc), field=field) from None


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
    # ADR-051: and the registry's gate - no internal or metadata host either.
    return validate_link_url(value, field="marketing_url")


# --- ADR-051: the Product Manager -----------------------------------------------

#: The categories the Product Manager offers. Configuration, not logic: a
#: category is display text that nothing branches on, and a product may keep
#: any other plain-text category it already has (``validate_category``).
PRODUCT_CATEGORIES: Final[tuple[str, ...]] = (
    "Sales",
    "Finance",
    "Project Management",
    "Customer Support",
    "Marketing",
    "Communication",
    "AI",
    "HR",
    "Other",
)
MAX_STORE_TITLE: Final = 128
MAX_STORE_SUMMARY: Final = 200


def validate_store_text(raw: Any, *, field: str, maximum: int) -> str:
    """Single-spaced plain text with no control characters - a store card
    renders it as text, never as markup."""
    text = " ".join(str(raw or "").split())
    if len(text) > maximum or any(ord(ch) < 32 for ch in text):
        raise CatalogValidationError(
            f"{field} is at most {maximum} characters of plain text.", field=field
        )
    return text


def code_for_slug(slug: str) -> ProductCode:
    """The permanent product code a new product's slug implies
    (``acme-notes`` -> ``acme_notes``) - the inverse of ``slug_for_code``."""
    try:
        return ProductCode(slug.replace("-", "_"))
    except ValueError:
        raise CatalogValidationError(
            "This slug cannot become a product code: use at most 32 characters, "
            "starting with a letter.",
            field="slug",
        ) from None


#: The three states the Product Manager shows. ``deprecated`` and ``retired``
#: are both "archived": no longer sold, history intact.
MANAGER_STATUS: Final[dict[ProductStatus, str]] = {
    ProductStatus.DRAFT: "draft",
    ProductStatus.AVAILABLE: "active",
    ProductStatus.DEPRECATED: "archived",
    ProductStatus.RETIRED: "archived",
}

#: Store-listing images: PNG or JPEG only (SVG can carry script; WebP and GIF
#: cannot be verified by signature here), bounded in bytes and in pixels.
MEDIA_CONTENT_TYPES: Final = frozenset({"image/png", "image/jpeg"})
MAX_SCREENSHOTS: Final = 8
MAX_MEDIA_CAPTION: Final = 140


@dataclass(frozen=True, slots=True)
class MediaRule:
    max_bytes: int
    min_width: int
    min_height: int
    max_width: int
    max_height: int


MEDIA_RULES: Final[dict[ProductMediaKind, MediaRule]] = {
    ProductMediaKind.LOGO: MediaRule(512 * 1024, 64, 64, 2048, 2048),
    ProductMediaKind.BANNER: MediaRule(2 * 1024 * 1024, 600, 150, 4096, 4096),
    ProductMediaKind.SCREENSHOT: MediaRule(2 * 1024 * 1024, 320, 200, 4096, 4096),
}


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) read from a PNG's IHDR or a JPEG's start-of-frame, or
    ``None`` when the bytes are neither (or are truncated). Pure; no decoder."""
    if (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        and len(data) >= 24
        and data[12:16] == b"IHDR"
    ):
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length
            i += 2
            continue
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if length < 2:
            return None
        # SOF0-SOF15, except DHT (C4), JPG (C8) and DAC (CC).
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 > len(data):
                return None
            height = int.from_bytes(data[i + 5 : i + 7], "big")
            width = int.from_bytes(data[i + 7 : i + 9], "big")
            return width, height
        i += 2 + length
    return None


#: The product page's uploaded video (2026-11-04): MP4 or WebM, one per
#: product. 90 MB stays under Cloudflare's 100 MB request ceiling in front of
#: production, with room for the request's own headers.
VIDEO_CONTENT_TYPES: Final = {"video/mp4": "mp4", "video/webm": "webm"}
MAX_VIDEO_BYTES: Final = 90 * 1024 * 1024
#: MP4 major brands (the files platform keeps the same list): QuickTime and the
#: HEIF/AVIF image brands share the ``ftyp`` box and are refused.
_MP4_BRANDS: Final = frozenset(
    {
        b"isom",
        b"iso2",
        b"iso4",
        b"iso5",
        b"iso6",
        b"mp41",
        b"mp42",
        b"avc1",
        b"M4V ",
        b"M4VP",
        b"dash",
        b"mmp4",
        b"MSNV",
    }
)


def validate_video_upload(*, content_type: str, size_bytes: int, head: bytes) -> str:
    """The declared type of an uploaded product video, checked against its
    size and its first bytes (``head``, at least 12). The files platform checks
    the signature again when it stores the file."""
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in VIDEO_CONTENT_TYPES:
        raise CatalogValidationError(
            "A video must be an MP4 or WebM file.", field="content_type"
        )
    if size_bytes <= 0 or size_bytes > MAX_VIDEO_BYTES:
        raise CatalogValidationError(
            f"A video must be between 1 byte and {MAX_VIDEO_BYTES // (1024 * 1024)} MB.",
            field="file",
        )
    if declared == "video/mp4":
        looks_right = head[4:8] == b"ftyp" and head[8:12] in _MP4_BRANDS
    else:
        looks_right = head[:4] == bytes((0x1A, 0x45, 0xDF, 0xA3))
    if not looks_right:
        raise CatalogValidationError(
            "The file is not the MP4 or WebM video it claims to be. "
            "Export it as MP4 (H.264) and try again.",
            field="file",
        )
    return declared


def validate_media(
    *, kind: ProductMediaKind, content_type: str, data: bytes
) -> tuple[str, int, int]:
    """Type, size and pixel bounds of one store-listing image, judged on the
    bytes. Returns ``(content type, width, height)``. The files platform checks
    the signature again when it stores the file."""
    if kind is ProductMediaKind.VIDEO:
        raise CatalogValidationError(
            "A video is uploaded as a video (MP4 or WebM), not as an image.", field="kind"
        )
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in MEDIA_CONTENT_TYPES:
        raise CatalogValidationError(
            "An image must be a PNG or a JPEG. SVG and other formats are not accepted.",
            field="content_type",
        )
    rule = MEDIA_RULES[kind]
    if not data or len(data) > rule.max_bytes:
        raise CatalogValidationError(
            f"A {kind.value} must be between 1 byte and {rule.max_bytes // 1024} KB.",
            field="file",
        )
    size = image_dimensions(data)
    sniffed = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    if size is None or sniffed != declared:
        raise CatalogValidationError(
            "The file is not the PNG or JPEG image it claims to be.", field="file"
        )
    width, height = size
    if not (
        rule.min_width <= width <= rule.max_width
        and rule.min_height <= height <= rule.max_height
    ):
        raise CatalogValidationError(
            f"A {kind.value} must be from {rule.min_width}x{rule.min_height} to "
            f"{rule.max_width}x{rule.max_height} pixels; this one is {width}x{height}.",
            field="file",
        )
    return declared, width, height


#: What a product needs before it may be published to the store, in the order
#: the Product Manager lists it. ``launch_url`` is the integration's.
PUBLISH_REQUIREMENTS: Final[tuple[tuple[str, str], ...]] = (
    ("name", "Product name"),
    ("slug", "Slug"),
    ("category", "Category"),
    ("tagline", "Short description"),
    ("logo", "Logo"),
    ("application_url", "Application URL"),
    ("launch_url", "Launch URL"),
    ("status", "Not archived"),
)


def publish_checklist(
    *,
    name: str,
    slug: str,
    category: str,
    tagline: str,
    has_logo: bool,
    application_url: str,
    launch_url: str,
    status: ProductStatus,
) -> list[dict[str, Any]]:
    """Each publish requirement and whether it is met. Pure: the caller reads
    the product, its logo and its integration's launch URL."""
    met = {
        "name": bool(name.strip()),
        "slug": bool(slug.strip()),
        "category": bool(category.strip()),
        "tagline": bool(tagline.strip()),
        "logo": has_logo,
        "application_url": bool(application_url.strip()),
        "launch_url": bool(launch_url.strip()),
        "status": status in (ProductStatus.DRAFT, ProductStatus.AVAILABLE),
    }
    return [
        {"key": key, "label": label, "ok": met[key]}
        for key, label in PUBLISH_REQUIREMENTS
    ]

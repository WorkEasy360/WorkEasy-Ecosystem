"""Partner-owned presentation strings, by locale (ADR-046).

A partner may word the **customer-facing labels and messages** of its own brand
in a customer's language. Nothing else: this is not a general translation
system, and it is built so that it cannot become one.

- **A closed list of keys** (:data:`KEYS`). A partner translates the words on
  its customer portal (navigation labels, the welcome text, the support
  heading and text) and the greeting lines of the emails sent in its name. It
  cannot name a key of its own, and the list holds no security message, no
  legal platform warning, no validation error and no authorization wording -
  those are not partner copy and a test pins that no such key can appear.
- **A closed list of locales** (:data:`LOCALES`).
- **One line of plain text** per value: no ``<`` or ``>``, no control
  characters, and no brace, so there is no markup to render and no template
  syntax to execute. Output is escaped where it is shown.
- **A fixed fall-back chain**, applied by the server:
  partner translation for the locale -> the platform's wording for the locale
  -> the platform's English default. A missing or withdrawn partner string can
  therefore never leave a screen blank.

Presentation only: nothing here is read by authorization, entitlement,
validation, pricing or any security decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.partners import access, inputs
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW: Final = "platform.partner_branding.view"
MANAGE: Final = "platform.partner_branding.manage"

#: ``code -> English name``. A partner translates into these and no others.
LOCALES: Final[dict[str, str]] = {
    "en": "English",
    "hi": "Hindi",
    "bn": "Bengali",
    "ta": "Tamil",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ar": "Arabic",
    "id": "Indonesian",
}

DEFAULT_LOCALE: Final = "en"


@dataclass(frozen=True, slots=True)
class StringSpec:
    key: str
    group: str
    label: str
    #: The platform's English wording - the end of the fall-back chain.
    default: str
    max_length: int


def _spec(
    key: str, group: str, label: str, default: str, max_length: int = 120
) -> StringSpec:
    return StringSpec(key, group, label, default, max_length)


#: Every string a partner may translate. Adding one is a deliberate act: it must
#: be a label or message a customer reads on the partner's own brand.
KEYS: Final[dict[str, StringSpec]] = {
    s.key: s
    for s in (
        _spec("portal.nav.dashboard", "Navigation labels", "Dashboard", "Dashboard", 40),
        _spec(
            "portal.nav.my_products",
            "Navigation labels",
            "My Products",
            "My Products",
            40,
        ),
        _spec(
            "portal.nav.explore",
            "Navigation labels",
            "Explore Products",
            "Explore Products",
            40,
        ),
        _spec("portal.nav.settings", "Navigation labels", "Settings", "Settings", 40),
        _spec("portal.welcome_title", "Welcome", "Welcome heading", "Welcome", 80),
        _spec("portal.welcome_message", "Welcome", "Welcome message", "", 280),
        _spec(
            "portal.support.heading", "Support text", "Support heading", "Need help?", 80
        ),
        _spec(
            "portal.support.text",
            "Support text",
            "Support text",
            "Contact our support team and we will be glad to help.",
            280,
        ),
        _spec("portal.footer", "Customer portal labels", "Footer text", "", 160),
        _spec("email.greeting", "Email copy", "Email greeting", "Hello", 60),
        _spec("email.signoff", "Email copy", "Email sign-off", "Thank you", 80),
    )
}

#: The platform's own translations of those strings: ``{locale: {key: text}}``.
#: Only English exists today, so the second step of the fall-back chain is
#: honest about being empty for every other locale. Adding a language here makes
#: it the fall-back for every partner that has not worded the key itself.
PLATFORM: Final[dict[str, dict[str, str]]] = {
    "en": {spec.key: spec.default for spec in KEYS.values()},
}


def _clean_locale(value: Any) -> str:
    if not isinstance(value, str) or value not in LOCALES:
        raise PartnerValidationError(
            f"locale must be one of {sorted(LOCALES)}.", field="locale"
        )
    return value


def _clean_value(spec: StringSpec, value: Any) -> str:
    text = inputs.clean_markup_free(value, field="value", max_length=spec.max_length)
    # No template or expression syntax of any kind: a translation is words.
    if any(ch in text for ch in "{}$%`\\"):
        raise PartnerValidationError(
            "Use plain words only: no braces, $, % or backslash.", field="value"
        )
    return text


# --- the fall-back chain -----------------------------------------------------------------


def resolve(partner_id: UUID, locale: str, key: str) -> str:
    """The string a customer of ``partner_id`` reads for ``key`` in ``locale``:
    the partner's wording, else the platform's for the locale, else English."""
    from workeasy360.commercial.partners.models import PartnerTranslation

    if key not in KEYS:
        raise PartnerValidationError("Unknown string.", field="key")
    loc = locale if locale in LOCALES else DEFAULT_LOCALE
    row = (
        PartnerTranslation.objects.filter(partner_id=partner_id, locale=loc, key=key)
        .values_list("value", flat=True)
        .first()
    )
    if row:
        return str(row)
    return PLATFORM.get(loc, {}).get(key) or PLATFORM[DEFAULT_LOCALE][key]


def public_map(partner_id: UUID) -> dict[str, dict[str, str]]:
    """``{locale: {key: value}}`` of what the partner itself has worded - the
    part of the fall-back chain only the partner holds, for the customer's
    browser to apply. Bounded (closed keys x closed locales)."""
    from workeasy360.commercial.partners.models import PartnerTranslation

    out: dict[str, dict[str, str]] = {}
    for locale, key, value in PartnerTranslation.objects.filter(
        partner_id=partner_id, locale__in=list(LOCALES), key__in=list(KEYS)
    ).values_list("locale", "key", "value"):
        if value:
            out.setdefault(locale, {})[key] = value
    return out


# --- the manager ----------------------------------------------------------------------------


@in_scope
def overview(ctx: TenantContext, *, locale: Any = DEFAULT_LOCALE) -> dict[str, Any]:
    """One locale's strings with where each effective value comes from."""
    from workeasy360.commercial.partners.models import PartnerTranslation

    authz.require(ctx, VIEW)
    loc = _clean_locale(locale)
    mine = dict(
        PartnerTranslation.objects.filter(
            partner_id=ctx.partner_id, locale=loc
        ).values_list("key", "value")
    )
    held = {row["locale"]: row["n"] for row in _counts(ctx.partner_id)}
    items = []
    for spec in KEYS.values():
        own = mine.get(spec.key, "")
        platform = PLATFORM.get(loc, {}).get(spec.key, "")
        if own:
            source, effective = "partner", own
        elif platform and loc != DEFAULT_LOCALE:
            source, effective = "platform", platform
        else:
            source, effective = "english", PLATFORM[DEFAULT_LOCALE][spec.key]
        items.append(
            {
                "key": spec.key,
                "group": spec.group,
                "label": spec.label,
                "max_length": spec.max_length,
                "value": own,
                "effective": effective,
                "source": source,
                "english": PLATFORM[DEFAULT_LOCALE][spec.key],
            }
        )
    permissions = authz.effective_permissions(ctx)
    return {
        "locale": loc,
        "locales": [
            {"code": code, "name": name, "translated": held.get(code, 0)}
            for code, name in LOCALES.items()
        ],
        "strings": items,
        "can_manage": MANAGE in permissions,
        "fallback": [
            "Your translation for this language",
            "Workeasy360's wording for this language",
            "English",
        ],
        "never_translatable": [
            "Security messages and sign-in errors",
            "Legal notices and platform warnings",
            "Validation messages",
            "Anything that decides who may do what",
        ],
    }


def _counts(partner_id: UUID) -> list[dict[str, Any]]:
    from django.db.models import Count

    from workeasy360.commercial.partners.models import PartnerTranslation

    return list(
        PartnerTranslation.objects.filter(partner_id=partner_id)
        .values("locale")
        .annotate(n=Count("id"))
    )


@in_scope
def save(ctx: TenantContext, *, locale: Any, strings: Any) -> dict[str, Any]:
    """Set (or, with an empty value, withdraw) a partner's strings for one
    locale. All-or-nothing: a bad key or value changes nothing."""
    from workeasy360.commercial.partners.models import PartnerTranslation

    access.require_transacting(ctx)
    authz.require(ctx, MANAGE)
    loc = _clean_locale(locale)
    if not isinstance(strings, dict) or not strings:
        raise PartnerValidationError(
            "strings must be a non-empty object.", field="strings"
        )
    if len(strings) > len(KEYS):
        raise PartnerValidationError("Too many strings.", field="strings")
    cleaned: dict[str, str] = {}
    for key, raw in strings.items():
        spec = KEYS.get(key) if isinstance(key, str) else None
        if spec is None:
            raise PartnerValidationError("Unknown string.", field="strings")
        cleaned[key] = _clean_value(spec, raw)
    changed: list[str] = []
    now = timezone.now()
    with transaction.atomic():
        existing = {
            row.key: row
            for row in PartnerTranslation.objects.select_for_update().filter(
                partner_id=ctx.partner_id, locale=loc, key__in=list(cleaned)
            )
        }
        for key, value in cleaned.items():
            row = existing.get(key)
            if not value:
                if row is not None:
                    row.delete()
                    changed.append(key)
                continue
            if row is None:
                PartnerTranslation.objects.create(
                    partner_id=ctx.partner_id,
                    locale=loc,
                    key=key,
                    value=value,
                    updated_by_id=ctx.user_id,
                    updated_at=now,
                )
                changed.append(key)
            elif row.value != value:
                row.value = value
                row.updated_by_id = ctx.user_id
                row.updated_at = now
                row.save(update_fields=["value", "updated_by", "updated_at"])
                changed.append(key)
        if changed:
            audit.record(
                ctx,
                "partner.translations_changed",
                resource_type="partners.translation",
                resource_id=ctx.partner_id,
                # Keys and the locale only - the words themselves stay out of
                # the log (they are the partner's copy, not a security fact).
                changes={
                    "partner_id": str(ctx.partner_id),
                    "locale": loc,
                    "keys": sorted(changed),
                },
            )
    return overview(ctx, locale=loc)

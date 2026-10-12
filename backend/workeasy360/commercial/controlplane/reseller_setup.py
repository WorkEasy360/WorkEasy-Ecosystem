"""The Add Reseller review: a dry run that writes nothing (Phase 10b, extended).

The wizard collects a choice per step and runs the *existing* commands one at
a time. Before it does, this module answers "what will this create?" - and it
is the only place a figure on that screen is computed:

- **Validation is the commands' own.** Each field goes through the same
  primitives ``partners.services`` validates with (the partner code, bounded
  text, the email, the currency, the capability registry, the rate check, the
  profile parser), so the review cannot accept what a command would refuse, and
  reports every problem at once instead of the first.
- **Money is the server's.** The list price is ``resolve_price`` with no
  partner - the one function that decides a price. The partner price is the
  amount the operator proposed (or the list price when none is proposed). The
  discount is integer arithmetic on those two, the commission is
  ``domain.commission_amount`` (integer minor units, basis points, half-up), and
  the platform's net is the difference. The browser displays these; it never
  computes, rounds or converts one.
- **Nothing is persisted.** No row, no audit entry, no notification, no
  reservation of the partner code - so a review can be repeated freely and a
  race for a code is decided by the command that really creates it.

*Problems* and *readiness* are different things. A problem is something that is
**wrong** (an address that is not an address) and blocks everything, a draft
included. Readiness is what is **missing before the partner can be activated**
(an administrator, a contact, a capability): a draft may be saved without it,
the "Activate" action may not be taken without it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.conf import settings
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.commissions import domain as commission_domain
from workeasy360.commercial.commissions import rules as commission_rules
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.controlplane import partner_console, readiness
from workeasy360.commercial.partners import capabilities, profile, selectors, services
from workeasy360.commercial.partners.domain import clean_text, validate_partner_code
from workeasy360.commercial.partners.inputs import Problems
from workeasy360.commercial.pricing.resolution import resolve_price
from workeasy360.contracts.enums import BillingInterval, PartnerMemberRole
from workeasy360.contracts.errors import PriceNotAvailable
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

PARTNER_VIEW = "platform.staff_partner.view"

#: No partner has this id, so ``select_rule`` returns the programme default.
_NO_PARTNER = uuid.UUID(int=0)

MAX_PRODUCTS = 20
MAX_PRICES = 200

#: The figures on the commission step are shown for a customer paying this
#: whole amount (in the partner's currency): a worked example, not a forecast.
EXAMPLE_PURCHASE_MAJOR = 10_000

#: When to send the administrator's setup link: with the setup itself, or at the
#: moment the partner is activated.
INVITATION_TIMINGS = ("now", "on_activation")
PRICING_MODES = ("standard", "custom")


def review(ctx: TenantContext, draft: dict[str, Any]) -> dict[str, Any]:
    """Validate a proposed reseller and return what it would create, with the
    financial figures the server computes."""
    authz.require(ctx, PARTNER_VIEW)
    problems = Problems()
    now = timezone.now()

    details = draft.get("partner") if isinstance(draft.get("partner"), dict) else {}
    parsed = profile.parse(draft.get("profile"))
    problems.items.extend(parsed.problems.items)

    partner = _partner(details, parsed, problems)
    currency = _currency(details, problems)
    keys = _capabilities(draft.get("capabilities"), problems)
    white_label = _white_label(draft.get("white_label"), keys, problems)
    products = _products(draft.get("products"), problems, now)
    chosen = [p["code"] for p in products]
    options = _product_options(draft.get("product_options"), chosen, problems)
    pricing_mode = _pricing_mode(draft.get("pricing_mode"), problems)
    proposals = _proposed_prices(draft.get("partner_prices"), problems)
    warnings: list[str] = []
    if pricing_mode == "standard" and proposals:
        proposals = {}
        warnings.append(
            "Standard partner pricing was chosen, so the custom prices you typed "
            "were ignored."
        )
    commission = _commission(draft.get("commission"), chosen, currency, now, problems)
    administrator = _administrator(draft.get("administrator"), problems)

    pricing = _pricing(products, proposals, currency, commission, now, problems)
    trials = _trials(products, options)
    holds = {*keys}
    notes = [*_warnings(keys, products, pricing, commission), *warnings]
    return {
        "ok": not problems.items,
        "problems": problems.items,
        "partner": partner,
        "profile": _profile_echo(parsed),
        "capabilities": [
            {"key": k, "label": capabilities.CAPABILITIES[k].label} for k in keys
        ],
        "experience": capabilities.experience_labels(set(keys)),
        "products": [
            {
                "code": p["code"],
                "name": p["name"],
                "plans": [
                    {"plan_code": pl["plan_code"], "plan_name": pl["plan_name"]}
                    for pl in p["plans"]
                ],
            }
            for p in products
        ],
        "pricing_mode": pricing_mode,
        "pricing": pricing,
        "trials": trials,
        "commission": commission,
        "administrator": administrator,
        "white_label": white_label,
        "warnings": notes,
        "readiness": _readiness(partner, parsed, holds, products, administrator),
        "missing_optional": _missing_optional(parsed, white_label),
    }


# --- the partner ----------------------------------------------------------------


def _partner(
    details: dict[str, Any], parsed: profile.Parsed, problems: Problems
) -> dict[str, Any]:
    code = problems.run("code", validate_partner_code, details.get("code"))
    name = problems.run(
        "display_name",
        clean_text,
        details.get("display_name"),
        field="display_name",
        max_length=120,
    )
    legal = problems.run(
        "legal_name",
        clean_text,
        details.get("legal_name"),
        field="legal_name",
        max_length=200,
        required=False,
    )
    contact = problems.run(
        "contact_name",
        clean_text,
        details.get("contact_name"),
        field="contact_name",
        max_length=120,
        required=False,
    )
    email = problems.run(
        "contact_email",
        services.clean_email,
        details.get("contact_email"),
        required=False,
        field="contact_email",
    )
    if code and selectors.code_taken(code):
        problems.add("code", f"Partner code {code!r} is already in use.")
    # The primary contact the profile carries is the account's contact: the two
    # are the same person (``PartnerContact``), so the echo shows the profile's.
    primary = parsed.contacts.get("primary")
    if primary is not None:
        contact = primary.get("name") or contact
        email = primary.get("email") or email
    return {
        "code": code or "",
        "display_name": name or parsed.business.get("display_name", ""),
        "legal_name": legal or parsed.business.get("legal_name", ""),
        "contact_name": contact or "",
        "contact_email": email or "",
    }


def _profile_echo(parsed: profile.Parsed) -> dict[str, Any]:
    """What would be stored beyond the account: only the parts that were sent."""
    return {
        "business": {
            k: v
            for k, v in parsed.business.items()
            if k not in ("display_name", "legal_name")
        },
        "contacts": parsed.contacts,
        "support": parsed.support,
        "payout": parsed.payout,
        "has_internal_notes": bool(parsed.internal_notes),
    }


def _currency(details: dict[str, Any], problems: Problems) -> CurrencyCode | None:
    return problems.run(
        "payout_currency", services.parse_currency, details.get("payout_currency")
    )


def _capabilities(raw: Any, problems: Problems) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > len(capabilities.CAPABILITIES):
        problems.add("capabilities", "capabilities must be a list of capability keys.")
        return []
    keys: list[str] = []
    for key in raw:
        spec = problems.run("capabilities", capabilities.require_capability, key)
        if spec is not None and spec.key not in keys:
            keys.append(spec.key)
    return keys


def _white_label(raw: Any, keys: list[str], problems: Problems) -> dict[str, Any]:
    """Whether the reseller is also set up as white-label. It is the *same
    partner* gaining the pair ``branding.manage`` + ``domain.manage`` - never a
    second account - so the review lists exactly the capabilities that would be
    added to the ones already chosen, and the wizard grants them like any other.
    """
    if raw is not None and not isinstance(raw, bool):
        problems.add("white_label", "white_label must be true or false.")
        raw = False
    wanted = bool(raw)
    pair = (capabilities.BRANDING_MANAGE, capabilities.DOMAIN_MANAGE)
    added = [k for k in pair if wanted and k not in keys]
    for key in added:
        keys.append(key)
    return {"requested": wanted, "capabilities_added": added}


# --- products and prices --------------------------------------------------------------


def _products(raw: Any, problems: Problems, now: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_PRODUCTS:
        problems.add(
            "products", f"products must be a list of at most {MAX_PRODUCTS} codes."
        )
        return []
    if not all(isinstance(code, str) for code in raw):
        problems.add("products", "Each product must be a product code (text).")
        return []
    wanted = list(dict.fromkeys(raw))
    known = {p["code"]: p for p in catalog.visible_products()}
    chosen: list[dict[str, Any]] = []
    for code in wanted:
        if code not in known:
            problems.add("products", f"No such product: {code}.")
            continue
        chosen.append(known[code])
    plans: dict[str, list[dict[str, Any]]] = {}
    for plan in catalog.sellable_plans([p["code"] for p in chosen], at=now):
        plans.setdefault(plan["product_code"], []).append(plan)
    return [{**p, "plans": plans.get(p["code"], [])} for p in chosen]


def _product_options(
    raw: Any, chosen: list[str], problems: Problems
) -> dict[str, dict[str, bool]]:
    """The per-product choices: may this partner's customers trial it, and is it
    shown on its portal. Absent means the default (both on)."""
    out = {code: {"trial_allowed": True, "portal_visible": True} for code in chosen}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        problems.add("product_options", "product_options must be an object.")
        return out
    for code, entry in raw.items():
        if code not in out:
            problems.add(
                "product_options", f"Options were given for {code}, which is not chosen."
            )
            continue
        if not isinstance(entry, dict):
            problems.add("product_options", "Each product's options must be an object.")
            continue
        for key in ("trial_allowed", "portal_visible"):
            if key in entry:
                if isinstance(entry[key], bool):
                    out[code][key] = entry[key]
                else:
                    problems.add("product_options", f"{key} must be true or false.")
        extra = sorted(set(entry) - {"trial_allowed", "portal_visible"})
        if extra:
            problems.add("product_options", f"Unknown product options {extra}.")
    return out


def _trials(
    products: list[dict[str, Any]], options: dict[str, dict[str, bool]]
) -> list[dict[str, Any]]:
    """For each chosen product: what the platform configured for its trial, and
    whether this partner's customers would get it. The partner's choice only
    narrows the platform's - it can never add a trial the product lacks."""
    rows: list[dict[str, Any]] = []
    for product in products:
        platform = product.get("trial")
        enabled = bool(platform and platform["enabled"])
        allowed = options[product["code"]]["trial_allowed"]
        rows.append(
            {
                "product_code": product["code"],
                "product_name": product["name"],
                "platform_trial_enabled": enabled,
                "duration_days": platform["duration_days"] if platform else None,
                "seat_limit": platform["seat_limit"] if platform else None,
                "eligible_plan_codes": platform["eligible_plan_codes"]
                if platform
                else [],
                "partner_allows": allowed,
                "available_to_customers": enabled and allowed,
                "portal_visible": options[product["code"]]["portal_visible"],
            }
        )
    return rows


def _pricing_mode(raw: Any, problems: Problems) -> str:
    if raw is None:
        return "custom"
    if raw not in PRICING_MODES:
        problems.add(
            "pricing_mode", f"pricing_mode must be one of {list(PRICING_MODES)}."
        )
        return "standard"
    return str(raw)


def _proposed_prices(raw: Any, problems: Problems) -> dict[tuple[str, str], int]:
    """``{(plan_version_id, interval): amount_minor}`` the operator proposes."""
    if raw is None:
        return {}
    if not isinstance(raw, list) or len(raw) > MAX_PRICES:
        problems.add("partner_prices", "partner_prices must be a short list.")
        return {}
    out: dict[tuple[str, str], int] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            problems.add("partner_prices", "Each partner price must be an object.")
            continue
        amount = entry.get("amount_minor")
        try:
            plan_version = str(uuid.UUID(str(entry.get("plan_version_id"))))
            cycle = BillingInterval(str(entry.get("interval")))
        except ValueError:
            problems.add(
                "partner_prices", "A partner price names an unknown plan or cycle."
            )
            continue
        if (
            isinstance(amount, bool)
            or not isinstance(amount, int)
            or not 0 <= amount <= services.MAX_PARTNER_PRICE_MINOR
        ):
            problems.add(
                "partner_prices",
                "A partner price must be a whole number of minor units, zero or more "
                f"and at most {services.MAX_PARTNER_PRICE_MINOR}.",
            )
            continue
        out[(plan_version, cycle.value)] = amount
    return out


def _pricing(
    products: list[dict[str, Any]],
    proposals: dict[tuple[str, str], int],
    currency: CurrencyCode | None,
    commission: dict[str, Any],
    now: Any,
    problems: Problems,
) -> list[dict[str, Any]]:
    """One row per (plan, cycle) the reseller could sell: monthly always, and any
    other cycle the operator proposed a price for."""
    if currency is None:
        return []
    cycles_for: dict[str, set[str]] = {}
    for plan_version, cycle in proposals:
        cycles_for.setdefault(plan_version, set()).add(cycle)

    rows: list[dict[str, Any]] = []
    known_versions = {
        str(plan["plan_version_id"]) for product in products for plan in product["plans"]
    }
    for plan_version in cycles_for:
        if plan_version not in known_versions:
            problems.add(
                "partner_prices",
                "A partner price is for a plan that is not one of the chosen products.",
            )
    for product in products:
        for plan in product["plans"]:
            version_id = str(plan["plan_version_id"])
            cycles = {BillingInterval.MONTHLY.value, *cycles_for.get(version_id, set())}
            for cycle in sorted(cycles):
                rows.append(
                    _price_row(
                        product,
                        plan,
                        BillingInterval(cycle),
                        currency,
                        proposals.get((version_id, cycle)),
                        commission,
                        now,
                    )
                )
    return rows


def _price_row(
    product: dict[str, Any],
    plan: dict[str, Any],
    cycle: BillingInterval,
    currency: CurrencyCode,
    proposed: int | None,
    commission: dict[str, Any],
    now: Any,
) -> dict[str, Any]:
    list_minor: int | None
    try:
        quote = resolve_price(
            plan_version_id=plan["plan_version_id"],
            organization_id=None,
            currency=currency,
            interval=cycle,
            quantity=1,
            at=now,
            partner_id=None,
        )
        list_minor = quote.total.minor_units
    except PriceNotAvailable:
        list_minor = None

    partner_minor = proposed if proposed is not None else list_minor
    figures = partner_console.price_figures(
        list_minor=list_minor,
        partner_minor=partner_minor,
        rate_bp=commission["product_rates"].get(product["code"]),
        currency=currency,
    )
    return {
        "product_code": product["code"],
        "product_name": product["name"],
        "plan_version_id": str(plan["plan_version_id"]),
        "plan_code": plan["plan_code"],
        "plan_name": plan["plan_name"],
        "interval": cycle.value,
        "currency": currency.value,
        "list_price_minor": list_minor,
        "partner_price_minor": partner_minor,
        "partner_price_source": "proposed" if proposed is not None else "list",
        **figures,
    }


# --- commission --------------------------------------------------------------------------


def _commission(
    raw: Any,
    product_codes: list[str],
    currency: CurrencyCode | None,
    now: datetime,
    problems: Problems,
) -> dict[str, Any]:
    """Which rate would apply to each product, and where it comes from.

    ``mode=default`` assigns nothing: the partner earns the programme default
    (whatever it is when a sale happens). ``mode=custom`` would create one rule
    per product in scope - every product when ``product_codes`` is empty - each
    effective from ``effective_from`` (default: when it is assigned). A rule is
    never backdated: sales already made keep the rate they were computed under.

    The one commission *method* the platform has is a percentage of what the
    customer pays, in whole basis points; the hold before a commission becomes
    payable is a platform setting, not a per-partner one, so it is reported, not
    chosen.
    """
    spec = raw if isinstance(raw, dict) else {}
    mode = spec.get("mode") or "default"
    if mode not in ("default", "custom"):
        problems.add("commission", "mode must be 'default' or 'custom'.")
        mode = "default"
    custom: int | None = None
    if mode == "custom":
        rate = spec.get("rate_bp")
        if isinstance(rate, bool) or not isinstance(rate, int) or not 0 <= rate <= 10_000:
            problems.add(
                "commission",
                "rate_bp must be a whole number of basis points between 0 and 10000.",
            )
        else:
            custom = rate

    scope = _commission_scope(spec.get("product_codes"), product_codes, problems)
    effective_from = _effective_from(spec.get("effective_from"), now, problems)

    default_rule = commission_selectors.effective_rule(
        _NO_PARTNER, product_code="", at=now
    )
    rates: dict[str, int | None] = {}
    for code in product_codes:
        if mode == "custom" and (not scope or code in scope):
            rates[code] = custom
        else:
            rule = commission_selectors.effective_rule(
                _NO_PARTNER, product_code=code, at=now
            )
            rates[code] = rule["rate_bp"] if rule else None
    # The example illustrates the rate this partner is being given: the custom
    # rate (for every product, or for the ones in scope), else the programme
    # default it would otherwise earn.
    headline = (
        custom
        if mode == "custom"
        else (default_rule["rate_bp"] if default_rule else None)
    )
    return {
        "mode": mode,
        "rate_bp": custom,
        "programme_default_bp": default_rule["rate_bp"] if default_rule else None,
        "source": (
            "custom"
            if mode == "custom"
            else "programme_default"
            if default_rule
            else "none"
        ),
        "product_rates": rates,
        "method": "percentage",
        "scope": "products" if scope else "all",
        "product_codes": scope,
        "effective_from": effective_from.isoformat() if effective_from else None,
        "hold_days": int(getattr(settings, "W360_COMMISSION_HOLDBACK_DAYS", 30)),
        "example": _example(currency, headline),
    }


def _commission_scope(raw: Any, chosen: list[str], problems: Problems) -> list[str]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        problems.add("commission", "product_codes must be a list of product codes.")
        return []
    scope = list(dict.fromkeys(raw))
    outside = [c for c in scope if c not in chosen]
    if outside:
        problems.add(
            "commission",
            f"A commission scope names {outside}, which this partner may not sell.",
        )
    return [c for c in scope if c in chosen]


def _effective_from(raw: Any, now: datetime, problems: Problems) -> datetime | None:
    if raw in (None, ""):
        return None
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        problems.add("commission", "effective_from is not a valid timestamp.")
        return None
    if moment.tzinfo is None:
        problems.add("commission", "effective_from needs a timezone.")
        return None
    if moment < now - commission_rules.BACKDATE_TOLERANCE:
        problems.add(
            "commission",
            "A commission rule cannot take effect in the past: that would change "
            "what earlier sales are worth.",
        )
        return None
    return moment


def _example(currency: CurrencyCode | None, rate_bp: int | None) -> dict[str, Any] | None:
    """A worked example from the configured rate, or ``None`` when there is no
    rate (or no currency) to make one from. Integers only."""
    if currency is None or rate_bp is None:
        return None
    purchase = EXAMPLE_PURCHASE_MAJOR * 10**currency.exponent
    commission = commission_domain.commission_amount(
        basis_minor=purchase, currency=currency, rate_bp=rate_bp
    )
    return {
        "currency": currency.value,
        "purchase_minor": purchase,
        "commission_minor": commission,
        "platform_net_minor": purchase - commission,
        "rate_bp": rate_bp,
    }


# --- the administrator ---------------------------------------------------------------------


def _administrator(raw: Any, problems: Problems) -> dict[str, Any] | None:
    """The person who runs the partner account. Optional for a draft - leaving it
    out is "not yet", reported as missing for activation - but once given, an
    email is required."""
    if raw is None:
        return None
    spec = raw if isinstance(raw, dict) else {}
    email = problems.run(
        "administrator.email", services.clean_email, spec.get("email"), required=True
    )
    role = spec.get("role") or PartnerMemberRole.ADMIN.value
    if role not in {r.value for r in PartnerMemberRole}:
        problems.add("administrator.role", "role must be 'admin' or 'viewer'.")
        role = PartnerMemberRole.ADMIN.value
    first = problems.run(
        "administrator.first_name",
        clean_text,
        spec.get("first_name"),
        field="first_name",
        max_length=100,
        required=False,
    )
    last = problems.run(
        "administrator.last_name",
        clean_text,
        spec.get("last_name"),
        field="last_name",
        max_length=100,
        required=False,
    )
    timing = spec.get("send_invitation") or "now"
    if timing not in INVITATION_TIMINGS:
        problems.add(
            "administrator.send_invitation",
            f"send_invitation must be one of {list(INVITATION_TIMINGS)}.",
        )
        timing = "now"
    return {
        "email": email or "",
        "role": role,
        "first_name": first or "",
        "last_name": last or "",
        "send_invitation": timing,
    }


# --- readiness and what is missing ---------------------------------------------------------


def _readiness(
    partner: dict[str, Any],
    parsed: profile.Parsed,
    keys: set[str],
    products: list[dict[str, Any]],
    administrator: dict[str, Any] | None,
) -> dict[str, Any]:
    """What must exist before this partner can be **activated** - not before a
    draft can be saved. A draft is inert (it can do nothing until activated), so
    saving an incomplete one creates no commercial state; activating one is the
    act that does, and is refused until these are done. The rule is the one the
    saved partner's Overview applies (``readiness.evaluate``)."""
    return readiness.evaluate(
        contact_name=partner["contact_name"],
        contact_email=partner["contact_email"],
        business_email=parsed.business.get("business_email", ""),
        held=keys,
        product_count=len(products),
        has_administrator=administrator is not None and bool(administrator["email"]),
    )


def _missing_optional(
    parsed: profile.Parsed, white_label: dict[str, Any]
) -> list[dict[str, str]]:
    """Things nobody has to provide but a complete partner usually has. Shown on
    the review so they are noticed, never as a blocker."""
    business = parsed.business
    out: list[dict[str, str]] = []

    def note(key: str, label: str) -> None:
        out.append({"key": key, "label": label})

    if not business.get("website"):
        note("website", "Website")
    if not business.get("registration_number"):
        note("registration_number", "Registration number")
    if not business.get("tax_number"):
        note("tax_number", "Tax / GST / VAT number")
    address = business.get("address") or {}
    if not any(address.values()):
        note("address", "Address")
    if not parsed.support.get("email") and not parsed.support.get("url"):
        note("support", "Customer support details")
    if not parsed.payout.get("contact_email"):
        note("payout_contact_email", "Payout contact email")
    if not white_label["requested"]:
        note("white_label", "White-label")
    return out


def _warnings(
    keys: list[str],
    products: list[dict[str, Any]],
    pricing: list[dict[str, Any]],
    commission: dict[str, Any],
) -> list[str]:
    notes: list[str] = []
    if products and not any(
        capabilities.SUBSCRIPTION_SELL == k or capabilities.CUSTOMER_PROVISION == k
        for k in keys
    ):
        notes.append(
            "Products are allowed but no capability lets this partner sell or create customers."
        )
    if keys and capabilities.COMMISSION_EARN not in keys:
        notes.append("Without 'Earn commission' this partner earns nothing on its sales.")
    for row in pricing:
        if row["list_price_minor"] is None and row["partner_price_source"] == "list":
            notes.append(
                f"{row['plan_name']} ({row['interval']}) has no list price in "
                f"{row['currency']}: it cannot be sold until a price is set."
            )
        elif (
            row["discount_minor"] is not None
            and row["discount_minor"] < 0
            and row["partner_price_source"] == "proposed"
        ):
            notes.append(
                f"{row['plan_name']}: the partner price is above the list price."
            )
    if commission["source"] == "none":
        notes.append(
            "There is no programme default commission rule, so no commission would be earned."
        )
    return list(dict.fromkeys(notes))

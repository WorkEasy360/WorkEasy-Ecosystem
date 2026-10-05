"""The Add Reseller review: a dry run that writes nothing (Phase 10b).

The wizard collects a choice per step and runs the *existing* commands one at
a time. Before it does, this module answers "what will this create?" - and it
is the only place a figure on that screen is computed:

- **Validation is the commands' own.** Each field goes through the same
  primitives ``partners.services`` validates with (the partner code, bounded
  text, the email, the currency, the capability registry, the rate check), so
  the review cannot accept what a command would refuse, and reports every
  problem at once instead of the first.
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
"""

from __future__ import annotations

import uuid
from typing import Any

from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.controlplane import partner_console
from workeasy360.commercial.partners import capabilities, selectors, services
from workeasy360.commercial.partners.domain import clean_text, validate_partner_code
from workeasy360.commercial.pricing.resolution import resolve_price
from workeasy360.contracts.enums import BillingInterval, PartnerMemberRole
from workeasy360.contracts.errors import PartnerValidationError, PriceNotAvailable
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

PARTNER_VIEW = "platform.staff_partner.view"

#: No partner has this id, so ``select_rule`` returns the programme default.
_NO_PARTNER = uuid.UUID(int=0)

MAX_PRODUCTS = 20
MAX_PRICES = 200


class _Problems:
    """Collects what is wrong instead of stopping at the first thing."""

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, field: str, message: str) -> None:
        self.items.append({"field": field, "message": message})

    def run(self, on_field: str, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        """Call a validating primitive; a refusal becomes a problem *on the
        wizard's own field name* (a primitive may name it differently)."""
        try:
            return fn(*args, **kwargs)
        except PartnerValidationError as exc:
            self.add(on_field, exc.message)
        return None


def review(ctx: TenantContext, draft: dict[str, Any]) -> dict[str, Any]:
    """Validate a proposed reseller and return what it would create, with the
    financial figures the server computes."""
    authz.require(ctx, PARTNER_VIEW)
    problems = _Problems()
    now = timezone.now()

    details = draft.get("partner") if isinstance(draft.get("partner"), dict) else {}
    partner = _partner(details, problems)
    currency = _currency(details, problems)
    keys = _capabilities(draft.get("capabilities"), problems)
    products = _products(draft.get("products"), problems, now)
    proposals = _proposed_prices(draft.get("partner_prices"), problems)
    commission = _commission(
        draft.get("commission"), [p["code"] for p in products], problems
    )
    administrator = _administrator(draft.get("administrator"), problems)

    pricing = _pricing(products, proposals, currency, commission, now, problems)
    return {
        "ok": not problems.items,
        "problems": problems.items,
        "partner": partner,
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
        "pricing": pricing,
        "commission": commission,
        "administrator": administrator,
        "warnings": _warnings(keys, products, pricing, commission),
    }


# --- the partner ----------------------------------------------------------------


def _partner(details: dict[str, Any], problems: _Problems) -> dict[str, Any]:
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
    return {
        "code": code or "",
        "display_name": name or "",
        "legal_name": legal or "",
        "contact_name": contact or "",
        "contact_email": email or "",
    }


def _currency(details: dict[str, Any], problems: _Problems) -> CurrencyCode | None:
    return problems.run(
        "payout_currency", services.parse_currency, details.get("payout_currency")
    )


def _capabilities(raw: Any, problems: _Problems) -> list[str]:
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


# --- products and prices --------------------------------------------------------------


def _products(raw: Any, problems: _Problems, now: Any) -> list[dict[str, Any]]:
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


def _proposed_prices(raw: Any, problems: _Problems) -> dict[tuple[str, str], int]:
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
    problems: _Problems,
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
    raw: Any, product_codes: list[str], problems: _Problems
) -> dict[str, Any]:
    """Which rate would apply to each product, and where it comes from.

    ``mode=default`` assigns nothing: the partner earns the programme default
    (whatever it is when a sale happens). ``mode=custom`` would create one rule
    for this partner, effective when it is assigned, for every product.
    """
    spec = raw if isinstance(raw, dict) else {}
    mode = spec.get("mode") or "default"
    now = timezone.now()
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

    default_rule = commission_selectors.effective_rule(
        _NO_PARTNER, product_code="", at=now
    )
    rates: dict[str, int | None] = {}
    for code in product_codes:
        if mode == "custom":
            rates[code] = custom
        else:
            rule = commission_selectors.effective_rule(
                _NO_PARTNER, product_code=code, at=now
            )
            rates[code] = rule["rate_bp"] if rule else None
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
    }


# --- the administrator ---------------------------------------------------------------------


def _administrator(raw: Any, problems: _Problems) -> dict[str, Any]:
    spec = raw if isinstance(raw, dict) else {}
    email = problems.run(
        "administrator.email", services.clean_email, spec.get("email"), required=True
    )
    role = spec.get("role") or PartnerMemberRole.ADMIN.value
    if role not in {r.value for r in PartnerMemberRole}:
        problems.add("administrator.role", "role must be 'admin' or 'viewer'.")
        role = PartnerMemberRole.ADMIN.value
    return {"email": email or "", "role": role}


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

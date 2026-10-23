"""Profile completion: what a partner still needs before it is fully set up (ADR-048).

One rule, computed on the server, in two forms that a test holds to the same
answers:

- :func:`evaluate` - the sections of one partner's profile, each ``complete``,
  ``incomplete``, ``pending`` or ``optional``, the percentage and the list of
  what is missing. The Partner Detail header, the Complete Your Profile wizard,
  the All Partners column and the Activate guidance all show this answer;
- :func:`complete_q` - the same question as a filter over partners, so All
  Partners can be filtered on "profile complete / incomplete" by the database
  and the dashboard can count incomplete profiles, however many partners there
  are.

**Which sections a partner needs follows from the capabilities it holds**, never
from a stored type. The experience a partner was created as is only the bundle
of grants it was given (``capabilities.EXPERIENCE_BUNDLES``), so a partner that
later also holds affiliate grants also needs a referral program, and one whose
white-label grants are withdrawn stops needing a brand:

- every partner: business information (primary contact name and email, business
  email, country), a partner administrator, and at least one capability;
- a partner that sells or brands (the Reseller or White-label capabilities): at
  least one product it may sell (``PartnerProductGrant``);
- a partner that sells or refers (the Reseller or Affiliate capabilities): a
  commission configuration - it holds *Earn commission* and a rule is in force
  for it (its own, or the programme default);
- a partner that brands (the White-label capabilities): a named brand;
- a partner that refers (the Affiliate capabilities): an active referral code.

Optional sections are shown but never counted: partner prices (a plan without
one is charged the list price) and a custom domain.

**Completion grants nothing.** No permission, workspace or price reads it. It is
guidance for the screens and the activation gate the console shows; what a
partner may do is still decided by its membership, capability grants and
product grants on every request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.db.models import Q

from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners import console_reads as partner_reads
from workeasy360.contracts.enums import PartnerStatus

_RESELLER: Final = capabilities.experience_keys(capabilities.RESELLER)
_WHITE_LABEL: Final = capabilities.experience_keys(capabilities.WHITE_LABEL)
_AFFILIATE: Final = capabilities.experience_keys(capabilities.AFFILIATE)

#: Holding any of these makes the section required.
PRODUCTS_FOR: Final = _RESELLER | _WHITE_LABEL
COMMISSION_FOR: Final = _RESELLER | _AFFILIATE
BRAND_FOR: Final = _WHITE_LABEL
REFERRAL_FOR: Final = _AFFILIATE
#: Pricing and a custom domain are shown to a partner that holds these, never required.
PRICING_FOR: Final = _RESELLER | _WHITE_LABEL
DOMAIN_FOR: Final = _WHITE_LABEL
ANY_CAPABILITY: Final = frozenset(capabilities.CAPABILITIES)

#: The section keys, in the order a screen lists them.
SECTION_ORDER: Final = (
    "business",
    "capabilities",
    "products",
    "pricing",
    "commission",
    "brand",
    "domain",
    "referral",
    "administrator",
)

#: What the console calls the state of a partner that is still a draft.
SETUP_REQUIRED: Final = "setup_required"
READY: Final = "ready"


@dataclass(slots=True)
class Facts:
    """What the rule reads about one partner. Every field is a plain value the
    owning module answered; nothing here opens a table."""

    status: str
    held: frozenset[str]
    contact_name: str = ""
    contact_email: str = ""
    business_email: str = ""
    country: str = ""
    has_administrator: bool = False
    product_count: int = 0
    rule_applies: bool = False
    brand_named: bool = False
    active_referral_codes: int = 0
    #: ``None`` when not read (the list page); the section then says nothing about prices.
    has_partner_price: bool | None = None
    #: The most advanced custom-domain state, or ``none``.
    domain_state: str = "none"


def _section(
    key: str, label: str, state: str, *, required: bool, detail: str = ""
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "state": state,
        "required": required,
        "detail": detail,
    }


def evaluate(f: Facts) -> dict[str, Any]:
    """The completion of one partner's profile. Pure: the same facts always
    give the same answer, and the SQL form (:func:`complete_q`) agrees."""
    held = f.held
    sections: list[dict[str, Any]] = []

    gaps = [
        label
        for label, present in (
            ("the primary contact's name", f.contact_name),
            ("the primary contact's email", f.contact_email),
            ("a business email", f.business_email),
            ("the country", f.country),
        )
        if not present
    ]
    sections.append(
        _section(
            "business",
            "Business information",
            "incomplete" if gaps else "complete",
            required=True,
            detail=f"Add {', '.join(gaps)}." if gaps else "",
        )
    )
    sections.append(
        _section(
            "capabilities",
            "Partner type",
            "complete" if held else "incomplete",
            required=True,
            detail="" if held else "It holds no capability, so it can do nothing yet.",
        )
    )
    if held & PRODUCTS_FOR:
        sections.append(
            _section(
                "products",
                "Products",
                "complete" if f.product_count else "incomplete",
                required=True,
                detail=""
                if f.product_count
                else "Allow at least one product this partner may sell.",
            )
        )
    if held & PRICING_FOR:
        if f.has_partner_price:
            sections.append(_section("pricing", "Pricing", "complete", required=False))
        else:
            sections.append(
                _section(
                    "pricing",
                    "Pricing",
                    "optional",
                    required=False,
                    detail="Not required: a plan without a partner price is charged "
                    "the list price.",
                )
            )
    if held & COMMISSION_FOR:
        earns = capabilities.COMMISSION_EARN in held
        if earns and f.rule_applies:
            state, detail = "complete", ""
        elif not earns:
            state, detail = "incomplete", "It does not hold Earn commission."
        else:
            state, detail = (
                "incomplete",
                "No commission rule applies: it would earn nothing.",
            )
        sections.append(
            _section("commission", "Commission", state, required=True, detail=detail)
        )
    if held & BRAND_FOR:
        sections.append(
            _section(
                "brand",
                "Brand identity",
                "complete" if f.brand_named else "incomplete",
                required=True,
                detail="" if f.brand_named else "Give the brand a name.",
            )
        )
    if held & DOMAIN_FOR:
        if f.domain_state == "active":
            sections.append(
                _section("domain", "Custom domain", "complete", required=False)
            )
        elif f.domain_state != "none":
            sections.append(
                _section(
                    "domain",
                    "Custom domain",
                    "pending",
                    required=False,
                    detail=f.domain_state.replace("_", " ").capitalize() + ".",
                )
            )
        else:
            sections.append(
                _section(
                    "domain",
                    "Custom domain",
                    "optional",
                    required=False,
                    detail="Optional: the brand works on the Workeasy360 address.",
                )
            )
    if held & REFERRAL_FOR:
        sections.append(
            _section(
                "referral",
                "Referral program",
                "complete" if f.active_referral_codes else "incomplete",
                required=True,
                detail=""
                if f.active_referral_codes
                else "Create at least one referral code.",
            )
        )
    sections.append(
        _section(
            "administrator",
            "Partner administrator",
            "complete" if f.has_administrator else "incomplete",
            required=True,
            detail=""
            if f.has_administrator
            else "Nobody can sign in for this partner yet.",
        )
    )

    required = [s for s in sections if s["required"]]
    done = [s for s in required if s["state"] == "complete"]
    complete = len(done) == len(required)
    # Floor, so "100%" is only ever said of a complete profile.
    percent = (100 * len(done)) // len(required) if required else 100
    return {
        "percent": percent,
        "complete": complete,
        "required": len(required),
        "done": len(done),
        "sections": sections,
        "missing": [
            {"key": s["key"], "label": s["label"], "detail": s["detail"]}
            for s in required
            if s["state"] != "complete"
        ],
        "setup_status": setup_status(f.status, complete),
    }


def setup_status(status: str, complete: bool) -> str:
    """The partner's state as the console names it: a draft is *setup required*
    until its profile is complete, then *ready* (to be activated by staff). Any
    other status is its own name. Display only - the stored status is unchanged."""
    if status == PartnerStatus.DRAFT.value:
        return READY if complete else SETUP_REQUIRED
    return status


def summary_line(result: dict[str, Any]) -> dict[str, Any]:
    """What a list row shows: the percentage, whether complete, and the state."""
    return {
        "percent": result["percent"],
        "complete": result["complete"],
        "setup_status": result["setup_status"],
    }


# --- the batch form --------------------------------------------------------------------


def for_partners(
    partner_ids: list[UUID],
    now: datetime,
    *,
    statuses: dict[UUID, str],
    held: dict[UUID, set[str]] | None = None,
    referrals: dict[UUID, dict[str, Any]] | None = None,
    priced: dict[UUID, bool] | None = None,
    domains: dict[UUID, str] | None = None,
) -> dict[UUID, dict[str, Any]]:
    """The completion of each of these partners, in a fixed number of queries
    whatever their number. ``held`` and ``referrals`` are reused when the
    caller has already read them (the list page has). ``priced`` and
    ``domains`` add the two optional sections' states (one partner's page
    reads them; a list does not need them, as they never change the result)."""
    if not partner_ids:
        return {}
    if held is None:
        held = partner_reads.held_capabilities(partner_ids, now)
    if referrals is None:
        referrals = commission_selectors.referral_facts(partner_ids)
    facts = partner_reads.completion_facts(partner_ids, now)
    with_rule = commission_selectors.partners_with_applicable_rule(partner_ids, now)
    out: dict[UUID, dict[str, Any]] = {}
    for partner_id in partner_ids:
        own = facts.get(partner_id)
        if own is None:
            continue
        out[partner_id] = evaluate(
            Facts(
                status=statuses.get(partner_id, ""),
                held=frozenset(held.get(partner_id, set())),
                contact_name=own["contact_name"],
                contact_email=own["contact_email"],
                business_email=own["business_email"],
                country=own["country"],
                has_administrator=own["has_administrator"],
                product_count=own["product_count"],
                rule_applies=partner_id in with_rule,
                brand_named=own["brand_named"],
                active_referral_codes=referrals.get(partner_id, {}).get(
                    "active_codes", 0
                ),
                has_partner_price=(priced or {}).get(partner_id),
                domain_state=(domains or {}).get(partner_id, "none"),
            )
        )
    return out


# --- the SQL form ------------------------------------------------------------------------


def complete_q(now: datetime) -> Q:
    """A filter over partners: the profile is complete (the same rule as
    :func:`evaluate`). ``~complete_q(now)`` is "incomplete"."""
    return partner_reads.profile_complete_q(
        now,
        any_capability=ANY_CAPABILITY,
        products_for=PRODUCTS_FOR,
        commission_for=COMMISSION_FOR,
        brand_for=BRAND_FOR,
        referral_for=REFERRAL_FOR,
        commission_earn=capabilities.COMMISSION_EARN,
        commission_rule=commission_selectors.rule_applies_exists(now),
        referral_code=commission_selectors.active_referral_code_exists(),
    )


def incomplete_q(now: datetime) -> Q:
    """A filter over partners: the profile is incomplete and the partner is not
    terminated (a terminated partner is history, not unfinished setup)."""
    return ~complete_q(now) & ~Q(status=PartnerStatus.TERMINATED.value)


PROFILE_FILTERS: Final = ("complete", "incomplete")


def profile_filter(value: str | None, now: datetime) -> Q | None:
    """The list filter for ``profile=complete|incomplete``, or ``None``."""
    if not value:
        return None
    from workeasy360.contracts.errors import PartnerValidationError

    if value not in PROFILE_FILTERS:
        raise PartnerValidationError("Unknown profile filter.", field="profile")
    return complete_q(now) if value == "complete" else incomplete_q(now)

"""The capability registry - the one place partner capability names live.

A partner's powers are a *set of capabilities* (ADR-028), never a type. Every
capability the platform recognises is declared here, once, with the
permissions it unlocks. Nothing else in the codebase spells a capability
name as a string literal: services ask for a permission, and the mapping from
capability to permission is this table. That is what keeps "which partners can
do X?" answerable by reading one file, and what stops a capability being
invented by request input (Task 17-18).

A capability unlocks two tiers of permission:

- ``view`` - read-only permissions, held by every active member;
- ``manage`` - everything else, held by ``ADMIN`` members only.

``commission.earn`` unlocks **no permission at all**: earning is passive. The
capability is what commission accrual consults to decide whether a sale made
by, or attributed to, this partner earns anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from workeasy360.contracts.errors import PartnerValidationError


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    key: str
    label: str
    view: tuple[str, ...] = ()
    manage: tuple[str, ...] = ()


CUSTOMER_PROVISION: Final = "customer.provision"
CUSTOMER_MANAGE: Final = "customer.manage"
SUBSCRIPTION_SELL: Final = "subscription.sell"
SUBSCRIPTION_MANAGE: Final = "subscription.manage"
PRICING_VIEW: Final = "pricing.view"
BRANDING_MANAGE: Final = "branding.manage"
DOMAIN_MANAGE: Final = "domain.manage"
COMMISSION_EARN: Final = "commission.earn"
COMMISSION_VIEW: Final = "commission.view"
PAYOUT_VIEW: Final = "payout.view"
SUPPORT_ACCESS: Final = "support.access"
REFERRAL_CREATE: Final = "referral.create"
REFERRAL_VIEW: Final = "referral.view"

_SPECS: Final = (
    CapabilitySpec(
        CUSTOMER_PROVISION,
        "Create customer organizations",
        view=("platform.partner_customer.view",),
        manage=("platform.partner_customer.provision",),
    ),
    CapabilitySpec(
        CUSTOMER_MANAGE,
        "Manage the commercial setup of related customers",
        view=("platform.partner_customer.view",),
        manage=("platform.partner_customer.manage",),
    ),
    CapabilitySpec(
        SUBSCRIPTION_SELL,
        "Sell approved products and plans to related customers",
        # Selling means seeing what is for sale and at what price. Reading the
        # catalog is the view tier - it needs no second factor - while the sale
        # itself (the manage tier) does.
        view=("platform.partner_customer.view", "platform.partner_pricing.view"),
        manage=("platform.partner_subscription.sell",),
    ),
    CapabilitySpec(
        SUBSCRIPTION_MANAGE,
        "Change, cancel and activate related customers' subscriptions",
        view=("platform.partner_customer.view",),
        manage=("platform.partner_subscription.manage",),
    ),
    CapabilitySpec(
        PRICING_VIEW,
        "See the prices approved for this partner",
        view=("platform.partner_pricing.view",),
    ),
    CapabilitySpec(
        BRANDING_MANAGE,
        "Configure white-label branding",
        view=("platform.partner_branding.view",),
        manage=("platform.partner_branding.manage",),
    ),
    CapabilitySpec(
        DOMAIN_MANAGE,
        "Register and activate custom domains",
        view=("platform.partner_domain.view",),
        manage=("platform.partner_domain.manage",),
    ),
    CapabilitySpec(COMMISSION_EARN, "Earn commission on attributed sales"),
    CapabilitySpec(
        COMMISSION_VIEW,
        "View earned commission",
        view=("platform.partner_commission.view",),
    ),
    CapabilitySpec(
        PAYOUT_VIEW,
        "View payouts",
        view=("platform.partner_payout.view",),
    ),
    CapabilitySpec(
        SUPPORT_ACCESS,
        "Run read-only access diagnostics for related customers",
        manage=("platform.partner_support.access",),
    ),
    CapabilitySpec(
        REFERRAL_CREATE,
        "Create referral codes",
        view=("platform.partner_referral.view",),
        manage=("platform.partner_referral.create",),
    ),
    CapabilitySpec(
        REFERRAL_VIEW,
        "View referrals and their conversion",
        view=("platform.partner_referral.view",),
    ),
)

CAPABILITIES: Final[dict[str, CapabilitySpec]] = {spec.key: spec for spec in _SPECS}

#: The read-only permissions a *terminated* partner keeps, so that money it is
#: owed remains visible after the programme membership ends.
HISTORY_ONLY_PERMISSIONS: Final = frozenset(
    {"platform.partner_commission.view", "platform.partner_payout.view"}
)

# --- presentation: groups, presets and derived labels (Phase 10) -------------
#
# Everything below is **presentation over the registry above**. It grants
# nothing, is not stored and adds no capability: a group is a heading a screen
# lists capabilities under, a preset is a list of existing keys a form may tick
# in one click, and an *experience label* ("Reseller", "White-label",
# "Affiliate") is computed from the capabilities a partner holds. There is no
# partner type - a partner is the set of its grants, so any combination is
# valid and one account can wear several labels at once.

RESELLER: Final = "reseller"
WHITE_LABEL: Final = "white_label"
AFFILIATE: Final = "affiliate"

#: label key -> (label, the capabilities any one of which earns it)
EXPERIENCES: Final[dict[str, tuple[str, frozenset[str]]]] = {
    RESELLER: (
        "Reseller",
        frozenset(
            {CUSTOMER_PROVISION, CUSTOMER_MANAGE, SUBSCRIPTION_SELL, SUBSCRIPTION_MANAGE}
        ),
    ),
    WHITE_LABEL: ("White-label", frozenset({BRANDING_MANAGE, DOMAIN_MANAGE})),
    AFFILIATE: ("Affiliate", frozenset({REFERRAL_CREATE, REFERRAL_VIEW})),
}

#: How a screen groups capabilities for a person. Every capability is in
#: exactly one group (asserted by a test), so nothing is unreachable from a form.
CAPABILITY_GROUPS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("customer_management", "Customer management", (CUSTOMER_PROVISION, CUSTOMER_MANAGE)),
    (
        "products_sales",
        "Products and sales",
        (SUBSCRIPTION_SELL, SUBSCRIPTION_MANAGE, PRICING_VIEW),
    ),
    ("commercial", "Commercial", (COMMISSION_EARN, COMMISSION_VIEW, PAYOUT_VIEW)),
    ("support", "Support", (SUPPORT_ACCESS,)),
    ("white_label", "White-label", (BRANDING_MANAGE, DOMAIN_MANAGE)),
    ("referrals", "Referrals", (REFERRAL_CREATE, REFERRAL_VIEW)),
)

#: The short name a form shows beside each capability. The registry ``label`` is
#: the full sentence (it is what the audit log and the grant form say); this is
#: the plain two-or-three-word version a setup wizard lists under a heading.
#: Presentation only - the key is what is stored and granted. (Viewing customers
#: is not a capability of its own: it comes with either customer capability.)
SHORT_LABELS: Final[dict[str, str]] = {
    CUSTOMER_PROVISION: "Create customers",
    CUSTOMER_MANAGE: "Manage customers",
    SUBSCRIPTION_SELL: "Sell products",
    SUBSCRIPTION_MANAGE: "Manage subscriptions",
    PRICING_VIEW: "View pricing",
    COMMISSION_EARN: "Earn commissions",
    COMMISSION_VIEW: "View commissions",
    PAYOUT_VIEW: "View payouts",
    SUPPORT_ACCESS: "Customer support access",
    BRANDING_MANAGE: "Manage branding",
    DOMAIN_MANAGE: "Manage custom domains",
    REFERRAL_CREATE: "Create referral codes",
    REFERRAL_VIEW: "View referrals",
}

#: One-click starting points for a capability form. A preset is only a list of
#: keys the form ticks; applying it goes through the ordinary
#: ``grant_capability`` command for each key, so it can never grant more than
#: the registry defines.
CAPABILITY_PRESETS: Final[tuple[dict[str, object], ...]] = (
    {
        "key": "standard_reseller",
        "label": "Standard Reseller",
        "description": (
            "Create and manage customers, sell approved products, see prices, "
            "earn and view commission and payouts, and run access diagnostics."
        ),
        "capabilities": (
            CUSTOMER_PROVISION,
            CUSTOMER_MANAGE,
            SUBSCRIPTION_SELL,
            SUBSCRIPTION_MANAGE,
            PRICING_VIEW,
            COMMISSION_EARN,
            COMMISSION_VIEW,
            PAYOUT_VIEW,
            SUPPORT_ACCESS,
        ),
    },
    # The Add Partner flow offers one starting point per business purpose
    # (Partner Control Center). Each is still only a list of registry keys:
    # choosing "White-label" or "Affiliate" stores no purpose anywhere - the
    # partner wears that label because, and only while, it holds the grants.
    {
        "key": "white_label",
        "label": "White-label",
        "description": (
            "Configure the partner's own brand and register custom domains. "
            "Add selling or referral capabilities separately if it needs them."
        ),
        "capabilities": (BRANDING_MANAGE, DOMAIN_MANAGE),
    },
    {
        "key": "affiliate",
        "label": "Affiliate",
        "description": (
            "Create referral codes and see their conversion, earn commission on "
            "referred customers, and view that commission and its payouts."
        ),
        "capabilities": (
            REFERRAL_CREATE,
            REFERRAL_VIEW,
            COMMISSION_EARN,
            COMMISSION_VIEW,
            PAYOUT_VIEW,
        ),
    },
)


def experience_keys(label: str) -> frozenset[str]:
    """The capabilities any one of which earns ``label`` (for a SQL filter)."""
    return EXPERIENCES[label][1]


def experience_labels(held: frozenset[str] | set[str]) -> list[dict[str, str]]:
    """The labels a partner holding ``held`` wears - a pure function of the
    capability keys, in a fixed order. This is the **only** place the mapping
    exists; the API returns its result and the console only renders it."""
    return [
        {"key": key, "label": label}
        for key, (label, needs) in EXPERIENCES.items()
        if needs & set(held)
    ]


def require_capability(key: str) -> CapabilitySpec:
    """The spec for ``key``, or a 400 - a capability is never free text."""
    spec = CAPABILITIES.get(key) if isinstance(key, str) else None
    if spec is None:
        raise PartnerValidationError(
            f"{key!r} is not a partner capability. Allowed: {sorted(CAPABILITIES)}.",
            field="capability",
        )
    return spec


def permissions_for(
    capabilities: frozenset[str] | set[str], *, admin: bool
) -> frozenset[str]:
    """Permissions unlocked by ``capabilities`` for a member of this tier."""
    granted: set[str] = set()
    for key in capabilities:
        spec = CAPABILITIES.get(key)
        if spec is None:
            continue
        granted.update(spec.view)
        if admin:
            granted.update(spec.manage)
    return frozenset(granted)


def all_partner_permission_keys() -> frozenset[str]:
    keys: set[str] = set()
    for spec in CAPABILITIES.values():
        keys.update(spec.view)
        keys.update(spec.manage)
    return frozenset(keys)

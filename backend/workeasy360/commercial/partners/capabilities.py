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
SUBPARTNER_MANAGE: Final = "subpartner.manage"

_SPECS: Final = (
    CapabilitySpec(
        CUSTOMER_PROVISION,
        "Create customer organizations",
        view=("platform.partner_customer.view", "platform.partner_customer.export"),
        manage=("platform.partner_customer.provision",),
    ),
    CapabilitySpec(
        CUSTOMER_MANAGE,
        "Manage the commercial setup of related customers",
        view=("platform.partner_customer.view", "platform.partner_customer.export"),
        manage=("platform.partner_customer.manage",),
    ),
    CapabilitySpec(
        SUBSCRIPTION_SELL,
        "Sell approved products and plans to related customers",
        # Selling means seeing what is for sale and at what price. Reading the
        # catalog is the view tier, while the sale itself is the manage tier.
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
    CapabilitySpec(
        SUBPARTNER_MANAGE,
        "Create and manage sub-partners under this partner",
        view=("platform.partner_subpartner.view",),
        manage=("platform.partner_subpartner.manage",),
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
    ("sub_partners", "Sub-partners", (SUBPARTNER_MANAGE,)),
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
    SUBPARTNER_MANAGE: "Manage sub-partners",
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


#: What choosing an experience **at creation** grants (ADR-047). A bundle is only
#: a list of keys from the registry above: applying it goes through the ordinary
#: ``grant_capability`` command for each key, so it can never grant more than the
#: registry defines, and choosing one stores no partner type - the partner wears
#: the label because, and only while, it holds the grants.
#:
#: Team management, reports, support and the profile are not capabilities: the
#: partner's own account is baseline (``BASELINE_*`` below), held by every member
#: of an active partner by role. So "reports.view" or "team.manage" has no key to
#: grant, and no bundle lists one.
#:
#: - the **affiliate** bundle holds nothing that reaches a customer, a price or
#:   a subscription (asserted by a test);
#: - the **white-label** bundle is the reseller's commercial set *plus* the brand
#:   and domain pair, without sub-partners (white-label is a label, not a kind
#:   of account);
#: - the **reseller** bundle is the standard reseller set plus sub-partners.
_COMMERCIAL: Final = (
    CUSTOMER_PROVISION,
    CUSTOMER_MANAGE,
    SUBSCRIPTION_SELL,
    SUBSCRIPTION_MANAGE,
    PRICING_VIEW,
    COMMISSION_EARN,
    COMMISSION_VIEW,
    PAYOUT_VIEW,
    SUPPORT_ACCESS,
)

EXPERIENCE_BUNDLES: Final[dict[str, tuple[str, ...]]] = {
    RESELLER: (*_COMMERCIAL, SUBPARTNER_MANAGE),
    WHITE_LABEL: (*_COMMERCIAL, BRANDING_MANAGE, DOMAIN_MANAGE),
    AFFILIATE: (
        REFERRAL_CREATE,
        REFERRAL_VIEW,
        COMMISSION_EARN,
        COMMISSION_VIEW,
        PAYOUT_VIEW,
    ),
}

#: One line per experience for the Add Partner type cards. Words only.
EXPERIENCE_DESCRIPTIONS: Final[dict[str, str]] = {
    RESELLER: "Sell Workeasy360 products to customers.",
    WHITE_LABEL: "Sell Workeasy360 products using the partner's own branding.",
    AFFILIATE: "Promote Workeasy360 products and earn commission from referrals.",
}

#: An experience whose workspace another one replaces when both are earned.
#: Empty since ADR-053: the owner's model is additive - a partner holding the
#: White-label bundle (which includes the reseller's commercial set) is offered
#: the Reseller workspace beside the White-label one, and an upgraded affiliate
#: switches between Affiliate, Reseller and White-label. Switching is
#: presentation only; every request is authorized again from the grants.
SUBSUMED_BY: Final[dict[str, str]] = {}


def require_experience(key: object) -> str:
    """``key`` if it names an experience, or a 400 - never free text."""
    if isinstance(key, str) and key in EXPERIENCES:
        return key
    raise PartnerValidationError(
        f"experience must be one of {sorted(EXPERIENCES)}.", field="experience"
    )


def experience_bundle(key: str) -> tuple[str, ...]:
    """The capability keys choosing ``key`` grants."""
    return EXPERIENCE_BUNDLES[require_experience(key)]


def workspaces(held: frozenset[str] | set[str]) -> list[str]:
    """The workspaces a partner holding ``held`` can open, in a fixed order: the
    experience labels its capabilities earn, minus those another earned label
    subsumes. A pure function of the grants - the same capabilities that decide
    what the partner may do decide which workspaces exist, so a workspace can
    never be opened on a preference alone."""
    earned = [item["key"] for item in experience_labels(held)]
    return [key for key in earned if SUBSUMED_BY.get(key) not in earned]


#: With no usable preference a partner opens its largest workspace first: the
#: one whose grants include the others' selling power (ADR-053 keeps them all).
LANDING_ORDER: Final = (WHITE_LABEL, RESELLER, AFFILIATE)


def resolve_landing(stored: str, held: frozenset[str] | set[str]) -> str | None:
    """The workspace a partner opens first: the stored preference when its
    capabilities still allow it, else the workspace that now contains it, else
    the largest workspace there is (``LANDING_ORDER``). ``None`` for a partner
    that earns no label (the generic portal opens). The preference only ever
    **selects among** workspaces the grants already allow; it grants nothing
    and removes nothing."""
    options = workspaces(held)
    if not options:
        return None
    if stored in options:
        return stored
    inside = SUBSUMED_BY.get(stored)
    if inside is not None and inside in options:
        return inside
    return min(options, key=LANDING_ORDER.index)


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


# --- the partner's own account: baseline permissions -------------------------
#
# Running the account itself - the dashboard, the business profile, the team,
# the product list, the activity trail - belongs to no sellable capability: a
# pure affiliate has an account too. These are held by every member of an active
# partner (the view set) or by its administrators (the manage set), and they
# reach nothing of a customer, a price or a payout.

BASELINE_VIEW: Final = (
    "platform.partner_dashboard.view",
    "platform.partner_profile.view",
    "platform.partner_member.view",
    "platform.partner_product.view",
)
BASELINE_MANAGE: Final = (
    "platform.partner_profile.manage",
    "platform.partner_member.manage",
    "platform.partner_product.manage",
    "platform.partner_activity.view",
    "platform.partner_apikey.view",
    "platform.partner_apikey.manage",
)

# --- member roles: presets over the capabilities -----------------------------


@dataclass(frozen=True, slots=True)
class RoleSpec:
    key: str
    label: str
    description: str
    #: Holds the account-management permissions (``BASELINE_MANAGE``).
    admin: bool
    #: May exercise the *manage* tier of the capabilities below.
    manage: bool
    #: The capabilities this role may exercise; ``None`` means every capability
    #: the partner holds. Always intersected with what the partner holds, so a
    #: role can narrow a partner's powers and never widen them.
    capabilities: frozenset[str] | None

    @property
    def value(self) -> str:
        """The stored role string (the same word ``PartnerMemberRole`` uses)."""
        return self.key


ROLE_PRESETS: Final[dict[str, RoleSpec]] = {
    spec.key: spec
    for spec in (
        RoleSpec(
            "owner",
            "Partner Owner",
            "Everything the partner can do, including who else may act for it.",
            admin=True,
            manage=True,
            capabilities=None,
        ),
        RoleSpec(
            "admin",
            "Partner Admin",
            "Everything the partner can do, except changing an owner.",
            admin=True,
            manage=True,
            capabilities=None,
        ),
        RoleSpec(
            "sales",
            "Sales",
            "Create customers, sell and manage subscriptions, see prices and referrals.",
            admin=False,
            manage=True,
            capabilities=frozenset(
                {
                    CUSTOMER_PROVISION,
                    CUSTOMER_MANAGE,
                    SUBSCRIPTION_SELL,
                    SUBSCRIPTION_MANAGE,
                    PRICING_VIEW,
                    REFERRAL_CREATE,
                    REFERRAL_VIEW,
                }
            ),
        ),
        RoleSpec(
            "finance",
            "Finance",
            "Read prices, commission and payouts. Changes nothing.",
            admin=False,
            manage=False,
            capabilities=frozenset({PRICING_VIEW, COMMISSION_VIEW, PAYOUT_VIEW}),
        ),
        RoleSpec(
            "support",
            "Support",
            "Run read-only access diagnostics for related customers.",
            admin=False,
            manage=True,
            capabilities=frozenset({SUPPORT_ACCESS}),
        ),
        RoleSpec(
            "marketing",
            "Marketing",
            "Manage the brand and referral links. Custom domains stay with admins.",
            admin=False,
            manage=True,
            capabilities=frozenset(
                {BRANDING_MANAGE, REFERRAL_CREATE, REFERRAL_VIEW, PRICING_VIEW}
            ),
        ),
        RoleSpec(
            "viewer",
            "Viewer",
            "Read-only access to everything the partner holds.",
            admin=False,
            manage=False,
            capabilities=None,
        ),
    )
}

#: Roles that run the account (hold ``BASELINE_MANAGE``). A partner needs one.
ADMIN_ROLES: Final = frozenset(k for k, r in ROLE_PRESETS.items() if r.admin)


def is_admin_role(role: str) -> bool:
    return role in ADMIN_ROLES


def require_role(key: object) -> RoleSpec:
    """The preset for ``key``, or a 400 - a role is never free text."""
    spec = ROLE_PRESETS.get(key) if isinstance(key, str) else None
    if spec is None:
        raise PartnerValidationError(
            f"role must be one of {sorted(ROLE_PRESETS)}.", field="role"
        )
    return spec


def permissions_for_role(
    capabilities: frozenset[str] | set[str], *, role: str
) -> frozenset[str]:
    """Permissions a member in ``role`` holds over the partner's ``capabilities``.

    Capabilities are narrowed to the role's, the manage tier is included only
    when the role has it, and the account-management baseline follows the role's
    ``admin`` flag. An unknown stored role gets the read-only viewer set: it
    fails closed.
    """
    spec = ROLE_PRESETS.get(role) or ROLE_PRESETS["viewer"]
    usable = (
        set(capabilities)
        if spec.capabilities is None
        else set(capabilities) & spec.capabilities
    )
    granted = set(permissions_for(usable, admin=spec.manage))
    granted.update(BASELINE_VIEW)
    if spec.admin:
        granted.update(BASELINE_MANAGE)
    return frozenset(granted)


# --- what a parent may hand to a sub-partner ----------------------------------

#: The capabilities a partner holding ``subpartner.manage`` may give a sub-partner
#: it creates, and only those the parent holds itself. Deliberately absent:
#: money (commission, payouts - staff decide who earns), custom domains (a
#: dangerous, takeover-adjacent act), and ``subpartner.manage`` itself (the
#: hierarchy is one level deep).
DELEGABLE_CAPABILITIES: Final = frozenset(
    {
        CUSTOMER_PROVISION,
        CUSTOMER_MANAGE,
        SUBSCRIPTION_SELL,
        SUBSCRIPTION_MANAGE,
        PRICING_VIEW,
        SUPPORT_ACCESS,
        BRANDING_MANAGE,
        REFERRAL_CREATE,
        REFERRAL_VIEW,
    }
)


def all_partner_permission_keys() -> frozenset[str]:
    keys: set[str] = set(BASELINE_VIEW) | set(BASELINE_MANAGE)
    for spec in CAPABILITIES.values():
        keys.update(spec.view)
        keys.update(spec.manage)
    return frozenset(keys)

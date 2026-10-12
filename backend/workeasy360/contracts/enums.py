"""Enumerations shared across module boundaries.

These are in contracts/ because more than one module must agree on their
values. An enum used by exactly one module belongs to that module.
"""

from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    """String-valued enum.

    Subclasses ``str`` so values round-trip through JSON, database columns and
    Django choices without conversion. ``enum.StrEnum`` is 3.11+; this keeps
    contracts/ importable on 3.10.
    """

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


#: Public alias of the base above.
#:
#: A module outside ``contracts/`` whose enum is used by exactly one module
#: declares it there rather than here (see this file's docstring), but it
#: should still round-trip through JSON, database columns and Django choices
#: the same way every other enum in the system does. Importing this is how it
#: does that without redefining the base - and without importing an
#: underscore-prefixed name across a module boundary.
StrEnum = _StrEnum


class OrganizationStatus(_StrEnum):
    """Tenant lifecycle. See 03-multi-tenancy.md §3.1.

    The read/write/login matrix is expressed by the helper predicates below
    rather than by callers branching on the value. Branching on status names
    in business code is the same defect class as branching on plan names.
    """

    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    LOCKED = "locked"
    PENDING_DELETION = "pending_deletion"
    DELETED = "deleted"

    @property
    def allows_read(self) -> bool:
        return self in _READABLE_ORG_STATUSES

    @property
    def allows_write(self) -> bool:
        """Only ``active`` permits writes.

        ``suspended`` is deliberately read-only rather than a lockout
        (03-multi-tenancy.md §3.1): a customer whose card expired must still
        be able to see their invoices, export their data and pay.
        """
        return self is OrganizationStatus.ACTIVE

    @property
    def allows_login(self) -> bool:
        return self in _LOGIN_ORG_STATUSES


_READABLE_ORG_STATUSES = frozenset(
    {
        OrganizationStatus.ACTIVE,
        OrganizationStatus.SUSPENDED,
        OrganizationStatus.PENDING_DELETION,
    }
)
_LOGIN_ORG_STATUSES = frozenset(
    {
        OrganizationStatus.ACTIVE,
        OrganizationStatus.SUSPENDED,
        OrganizationStatus.PENDING_DELETION,
    }
)


class MembershipStatus(_StrEnum):
    """A user's participation in one organization. 03-multi-tenancy.md §3.4."""

    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"

    @property
    def grants_access(self) -> bool:
        return self is MembershipStatus.ACTIVE


class MemberType(_StrEnum):
    """Why this membership exists.

    ``service`` covers API-only principals, so machine access is a membership
    with roles like any other rather than a parallel privilege system.
    """

    INTERNAL = "internal"
    COLLABORATOR = "collaborator"
    SERVICE = "service"


class UserStatus(_StrEnum):
    """Global identity state. Not per-organization — that is MembershipStatus."""

    ACTIVE = "active"
    DISABLED = "disabled"
    LOCKED = "locked"
    #: Registered through self-service signup and not yet verified: the
    #: address has not proved it belongs to whoever chose the password, so the
    #: account cannot sign in, join an organization or buy anything (Phase 9).
    PENDING = "pending"

    @property
    def can_authenticate(self) -> bool:
        return self is UserStatus.ACTIVE


class EntitlementStatus(_StrEnum):
    """What products see. 06-entitlements.md §4.2.

    ``past_due`` and ``grace`` deliberately restrict nothing: a failed card is
    usually an expired card, and degrading service on first failure costs more
    in churn and support than it recovers in revenue.
    """

    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    GRACE = "grace"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def allows_read(self) -> bool:
        return self is not EntitlementStatus.EXPIRED

    @property
    def allows_write(self) -> bool:
        return self in _WRITABLE_ENTITLEMENT_STATUSES


_WRITABLE_ENTITLEMENT_STATUSES = frozenset(
    {
        EntitlementStatus.TRIAL,
        EntitlementStatus.ACTIVE,
        EntitlementStatus.PAST_DUE,
        EntitlementStatus.GRACE,
        EntitlementStatus.CANCELLED,
    }
)


class EntitlementSourceKind(_StrEnum):
    SUBSCRIPTION = "subscription"
    BUNDLE = "bundle"
    GRANT = "grant"
    TRIAL = "trial"


class FeatureKind(_StrEnum):
    """Three kinds only. R-28 accepts this limit deliberately: it keeps
    packaging comparable and displayable in a pricing table."""

    BOOLEAN = "boolean"
    QUOTA = "quota"
    ENUM = "enum"


class EntitlementReason(_StrEnum):
    """Machine-readable reason accompanying an entitlement decision.

    Distinct from the HTTP status because the UI renders three different
    things for "you never bought this", "you bought it and it lapsed" and
    "the org has it but you have no seat". A bare False forces the UI to guess.
    """

    OK = "ok"
    NOT_ENTITLED = "not_entitled"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    NO_SEAT = "no_seat"
    READ_ONLY = "read_only"
    UNKNOWN = "unknown"


# --- Commercial ----------------------------------------------------------
#
# These live in contracts/ because the catalog, pricing, subscriptions and
# provisioning modules must all agree on them, and because the API layer
# serialises them. Nothing here is imported by products/: a product must not
# be able to name a plan status, let alone branch on one.


class ProductStatus(_StrEnum):
    """Commercial availability of a sellable product. 05-commercial-model.md §3.1.

    ``deprecated`` and ``retired`` are distinct and both are needed:
    deprecated means "no new subscriptions, existing ones continue and still
    renew"; retired means the SKU is gone. Neither invalidates a historical
    subscription record, because a subscription pins an immutable
    ``PlanVersion`` rather than reading the live product row.
    """

    DRAFT = "draft"
    AVAILABLE = "available"
    DEPRECATED = "deprecated"
    RETIRED = "retired"

    @property
    def is_sellable(self) -> bool:
        """Whether a *new* subscription may be created against it."""
        return self is ProductStatus.AVAILABLE


class ProductListing(_StrEnum):
    """Where a product may be *seen* (Phase 9), independent of whether it can
    be *sold* (``ProductStatus``).

    - ``listed``: on the public site and in Explore once it is available.
    - ``coming_soon``: announced on the public site; not purchasable there.
    - ``hidden``: private - sold only by staff, partners or contract, and
      absent from the public site and Explore.
    """

    LISTED = "listed"
    COMING_SOON = "coming_soon"
    HIDDEN = "hidden"


class PublicAvailability(_StrEnum):
    """What the public site and the admin show for a product, derived from
    ``ProductStatus`` and ``ProductListing`` by one function
    (``commercial.catalog.domain.public_availability``) - never stored."""

    AVAILABLE = "available"
    COMING_SOON = "coming_soon"
    PRIVATE = "private"
    DISABLED = "disabled"
    DRAFT = "draft"

    @property
    def is_public(self) -> bool:
        return self in (PublicAvailability.AVAILABLE, PublicAvailability.COMING_SOON)


class LicensingMode(_StrEnum):
    """How a product's value is consumed, and therefore how it is licensed.

    Intrinsic to the product, not to the plan: CRM is per-seat because value
    scales with users; Books is per-organization because a ledger is not
    per-person. A plan may narrow this but never contradict it.
    """

    PER_SEAT = "per_seat"
    PER_ORGANIZATION = "per_organization"
    USAGE = "usage"


class CatalogStatus(_StrEnum):
    """Lifecycle shared by ``Plan`` and ``Bundle``. 05-commercial-model.md §3.3.

    ``grandfathered`` is the status people forget to model until they need it
    under time pressure: no new subscriptions, existing ones continue and
    still renew. Distinct from ``retired``, where existing subscriptions must
    migrate.
    """

    DRAFT = "draft"
    AVAILABLE = "available"
    GRANDFATHERED = "grandfathered"
    RETIRED = "retired"

    @property
    def accepts_new_subscriptions(self) -> bool:
        return self is CatalogStatus.AVAILABLE

    @property
    def honours_existing_subscriptions(self) -> bool:
        return self in _HONOURED_CATALOG_STATUSES


_HONOURED_CATALOG_STATUSES = frozenset(
    {
        CatalogStatus.AVAILABLE,
        CatalogStatus.GRANDFATHERED,
        # A retired plan still honours what was already sold: revoking access
        # because a SKU was withdrawn is theft, and the subscription pins an
        # immutable version that is unaffected by the catalog row's status.
        CatalogStatus.RETIRED,
    }
)


class PlanBillingType(_StrEnum):
    """How a plan or bundle is sold (Phase 8 extension, the plan builder's
    "Type"). A ``free_trial`` offer always starts as a trial, only for a
    customer with no live subscription, and simply ends unless converted; it
    must carry trial days. Informs selling - never entitlement."""

    PAID = "paid"
    FREE_TRIAL = "free_trial"


class PlanKind(_StrEnum):
    """``add_on``: bought *on top of* a subscription that already holds the
    product it extends (``Plan.addon_for``) - never alone, never in a bundle."""

    STANDARD = "standard"
    ADD_ON = "add_on"


class BundleKind(_StrEnum):
    """Why a bundle exists. Drives visibility, never entitlement."""

    PREDEFINED = "predefined"
    ECOSYSTEM = "ecosystem"
    CUSTOM = "custom"


class BillingInterval(_StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    #: Phase 8 extension: every six months ("bi-annual" in the plan builder).
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"
    ONE_TIME = "one_time"

    @property
    def months(self) -> int:
        """Period length. ``one_time`` is 0 - it never rolls."""
        return _INTERVAL_MONTHS[self]


_INTERVAL_MONTHS: dict[BillingInterval, int] = {
    BillingInterval.MONTHLY: 1,
    BillingInterval.QUARTERLY: 3,
    BillingInterval.SEMI_ANNUAL: 6,
    BillingInterval.ANNUAL: 12,
    BillingInterval.ONE_TIME: 0,
}


class PriceModel(_StrEnum):
    """``tiered`` and ``volume`` are distinct and routinely confused.

    ``tiered`` charges each band at its own rate (graduated); ``volume``
    charges *all* units at the rate of the band reached. Conflating them
    produces invoices customers dispute.
    """

    FLAT = "flat"
    PER_SEAT = "per_seat"
    TIERED = "tiered"
    VOLUME = "volume"
    USAGE = "usage"


class PriceBookChannel(_StrEnum):
    DIRECT = "direct"
    PARTNER = "partner"
    INTERNAL = "internal"


class PriceSource(_StrEnum):
    """Which rule produced a quote.

    Recorded on the subscription item so "why is this customer paying this?"
    is answerable in production - a support question that is otherwise
    unanswerable (05-commercial-model.md §4.3).
    """

    CONTRACT = "contract"
    PARTNER_BOOK = "partner_book"
    REGIONAL_BOOK = "regional_book"
    DEFAULT_BOOK = "default_book"


class TaxBehaviour(_StrEnum):
    INCLUSIVE = "inclusive"
    EXCLUSIVE = "exclusive"


class SubscriptionStatus(_StrEnum):
    """The commercial relationship's state. 06-entitlements.md §3.1.

    Deliberately the same seven states as :class:`EntitlementStatus`, with
    ``trialing`` mapping to ``trial``. The 06 §3.1 sketch also named
    ``unpaid``; it is represented here by ``suspended``, which is the state
    dunning exhaustion actually produces and which already carries the
    read-only semantics products see. One-to-one mapping is what makes the
    entitlement-consistency invariants derivable rather than hand-maintained.

    ``cancelled`` vs ``expired`` matters: cancelled means "will not renew" and
    the customer keeps access until ``current_period_end``, because they paid
    for it. Revoking on cancellation is theft and generates chargebacks.
    """

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    GRACE = "grace"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def is_live(self) -> bool:
        """Whether the subscription still grants anything at all."""
        return self is not SubscriptionStatus.EXPIRED

    @property
    def entitlement_status(self) -> EntitlementStatus:
        """The status products see. The single mapping point between the two
        vocabularies; nothing else translates between them."""
        return _SUBSCRIPTION_TO_ENTITLEMENT[self]


_SUBSCRIPTION_TO_ENTITLEMENT: dict[SubscriptionStatus, EntitlementStatus] = {
    SubscriptionStatus.TRIALING: EntitlementStatus.TRIAL,
    SubscriptionStatus.ACTIVE: EntitlementStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE: EntitlementStatus.PAST_DUE,
    SubscriptionStatus.GRACE: EntitlementStatus.GRACE,
    SubscriptionStatus.SUSPENDED: EntitlementStatus.SUSPENDED,
    SubscriptionStatus.CANCELLED: EntitlementStatus.CANCELLED,
    SubscriptionStatus.EXPIRED: EntitlementStatus.EXPIRED,
}


class TrialSource(_StrEnum):
    """Where a trial came from. An audit trail, not a branch in product code."""

    SELF_SERVE = "self_serve"
    SALES = "sales"
    PARTNER = "partner"
    EXTENSION = "extension"


class ProductTrialStatus(_StrEnum):
    """Where one product's trial stands (Phase 11).

    ``active`` grants a trial entitlement until ``ends_at``. The other three
    are terminal and none can return to ``active``: a trial is once per
    organization and product, and the database says so.
    """

    ACTIVE = "active"
    #: The organization bought the product; the paid line took over.
    CONVERTED = "converted"
    #: The time ran out without a purchase.
    EXPIRED = "expired"
    #: The customer ended it early.
    CANCELLED = "cancelled"


class RenewalPolicy(_StrEnum):
    AUTO_RENEW = "auto_renew"
    MANUAL = "manual"
    NONE = "none"


class CollectionMode(_StrEnum):
    """Who is invoiced. Separate from who benefits: entitlements are always
    granted to the customer's organization."""

    AUTOMATIC = "automatic"
    INVOICE = "invoice"
    PARTNER_BILLED = "partner_billed"


class ChangeTiming(_StrEnum):
    """When a plan change takes effect.

    Not a free choice per call: upgrades are ``immediate`` because the
    customer asked for more and expects it now; downgrades default to
    ``period_end`` because the current period is already paid for at the
    higher tier. See docs/phase-2/subscriptions.md.
    """

    IMMEDIATE = "immediate"
    PERIOD_END = "period_end"


class ActorKind(_StrEnum):
    """Who performed an audited action. 11-observability.md §9."""

    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"
    #: Workeasy360 staff acting through the control plane under an explicit
    #: ``platform.staff_*`` grant. Distinct from STAFF_BREAK_GLASS, which is
    #: impersonation of a customer identity (04-identity-authz.md section 6);
    #: that mode is not implemented (ADR-027, Phase 7 Task 51).
    STAFF = "staff"
    STAFF_BREAK_GLASS = "staff_break_glass"
    PARTNER = "partner"


class AuditOutcome(_StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


class OutboxStatus(_StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class NotificationChannel(_StrEnum):
    EMAIL = "email"
    IN_APP = "in_app"
    SMS = "sms"
    WEBHOOK = "webhook"


class DeliveryStatus(_StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    SUPPRESSED = "suppressed"


class RoleScopeType(_StrEnum):
    ORGANIZATION = "organization"
    PRODUCT = "product"
    RESOURCE = "resource"


class FileStatus(_StrEnum):
    """Nothing is servable until scanned. 12-security.md §10."""

    PENDING_UPLOAD = "pending_upload"
    QUARANTINED = "quarantined"
    CLEAN = "clean"
    INFECTED = "infected"
    DELETED = "deleted"

    @property
    def is_servable(self) -> bool:
        return self is FileStatus.CLEAN


# --- Control-plane principals and the partner channel (Phase 7) -----------


class PrincipalKind(_StrEnum):
    """What kind of identity a ``TenantContext`` speaks for.

    The authorization decision function has exactly one entry point
    (``authz.check``) for every principal, so the kind is data on the context
    rather than a second code path. What differs is only **where the
    permission set comes from**: a member's role assignments, a staff
    member's explicit ``platform.staff_*`` grants, or a partner user's
    capability grants on a live partner relationship (ADR-027).
    """

    MEMBER = "member"
    STAFF = "staff"
    PARTNER = "partner"


class PartnerStatus(_StrEnum):
    """Partner lifecycle. Historical records survive every state."""

    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"

    @property
    def may_transact(self) -> bool:
        """Only an active partner may perform new commercial actions."""
        return self is PartnerStatus.ACTIVE


class PartnerLinkStatus(_StrEnum):
    """Partner-to-customer relationship lifecycle. ``ENDED`` is terminal."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    ENDED = "ended"


class PartnerMemberRole(_StrEnum):
    """A partner user's tier. Two tiers, deliberately.

    ``ADMIN`` receives every permission the partner's capability grants
    unlock; ``VIEWER`` receives only the read permissions among them. More
    granular partner-user roles would be an RBAC system of their own, which
    nothing needs yet.
    """

    ADMIN = "admin"
    VIEWER = "viewer"


class PartnerContactKind(_StrEnum):
    """Which business role a partner contact fills. A contact is a *record of a
    person to reach*, never a login: nobody signs in as a contact."""

    PRIMARY = "primary"
    BILLING = "billing"
    TECHNICAL = "technical"
    SUPPORT = "support"


class PayoutFrequency(_StrEnum):
    """How often staff intend to prepare this partner's payouts. A preference
    staff read when they prepare one - nothing runs on it and nothing is paid
    automatically (the platform moves no money)."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    MANUAL = "manual"


class GrantStatus(_StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class DomainStatus(_StrEnum):
    """Custom-domain lifecycle (Phase 7 Task 32).

    ``ACTIVE`` is reachable only through ``VERIFIED``, and ``VERIFIED`` only
    through a successful DNS-control check performed by the platform.
    """

    REQUESTED = "requested"
    VERIFICATION_PENDING = "verification_pending"
    VERIFIED = "verified"
    ACTIVE = "active"
    FAILED = "failed"
    DISABLED = "disabled"


class ReferralStatus(_StrEnum):
    CAPTURED = "captured"
    CONVERTED = "converted"
    EXPIRED = "expired"


class AccrualKind(_StrEnum):
    """The three things a commission ledger row can be. Never edited."""

    ACCRUAL = "accrual"
    REVERSAL = "reversal"
    ADJUSTMENT = "adjustment"


class PayoutStatus(_StrEnum):
    """Payout lifecycle. ``PAID`` records a manual confirmation only; no
    payment is executed by this platform (Phase 7 Task 47)."""

    DRAFT = "draft"
    READY = "ready"
    APPROVED = "approved"
    PAID = "paid"
    CANCELLED = "cancelled"


class ProductSignInMode(_StrEnum):
    """How a product lets people in: the product's own declaration, recorded in
    its integration manifest (``sign_in_mode``, manifest ``2026-10-12``).

    Never a person's choice. A person's preference (:class:`SignInPreference`)
    chooses between the doors a product offers; it cannot open one the product
    does not have (docs/universal-sso/design.md section 2).
    """

    #: Workeasy360 sign-in is the only way in. The default for a manifest that
    #: predates the field: a registration has redirect URIs by construction.
    SSO_ONLY = "sso_only"
    #: Workeasy360 sign-in, and the product's own sign-in page (``login_url``).
    SSO_OPTIONAL = "sso_optional"
    #: The product does not sign people in with Workeasy360 at all.
    LOCAL_ONLY = "local_only"

    @property
    def offers_sso(self) -> bool:
        return self is not ProductSignInMode.LOCAL_ONLY

    @property
    def offers_product_login(self) -> bool:
        return self is not ProductSignInMode.SSO_ONLY


class SignInPreference(_StrEnum):
    """How a person wants to enter Workeasy360 products: once as their default
    for every product (including products added later), optionally overridden
    per product. Stored values only; the words a person reads are frontend copy.
    """

    #: Per product only: follow the person's default. Never stored - choosing
    #: it removes the override.
    INHERIT = "inherit"
    #: Sign in with the existing Workeasy360 session, without asking.
    AUTO_SSO = "auto_sso"
    #: Ask each time: Workeasy360 or the product's own sign-in.
    ASK = "ask"
    #: Prefer the product's own sign-in page where the product has one.
    PRODUCT_LOGIN = "product_login"

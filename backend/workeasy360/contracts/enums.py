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


class ActorKind(_StrEnum):
    """Who performed an audited action. 11-observability.md §9."""

    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"
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

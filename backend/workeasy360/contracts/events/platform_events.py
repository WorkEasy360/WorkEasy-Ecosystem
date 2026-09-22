"""Platform event schemas.

Phase 1 declares only the platform facts the foundation itself emits. Product
events are not declared here and will not be until a product exists - Phase 1
builds the mechanism, not the traffic.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.events.registry import EventSchema, FieldSpec, registry

# --- organizations -------------------------------------------------------

ORGANIZATION_CREATED_V1: Final = registry.register(
    EventSchema(
        event_type="organizations.organization.created",
        version=1,
        aggregate_type="organizations.organization",
        description="A new tenant was provisioned.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("slug", str),
            FieldSpec("display_name", str),
            FieldSpec("created_by_user_id", str),
        ),
    )
)

ORGANIZATION_SUSPENDED_V1: Final = registry.register(
    EventSchema(
        event_type="organizations.organization.suspended",
        version=1,
        aggregate_type="organizations.organization",
        description="Writes are now blocked for this tenant; reads continue.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("reason", str, required=False),
        ),
    )
)

ORGANIZATION_REACTIVATED_V1: Final = registry.register(
    EventSchema(
        event_type="organizations.organization.reactivated",
        version=1,
        aggregate_type="organizations.organization",
        description="Tenant returned to active; writes resume.",
        fields=(FieldSpec("organization_id", str),),
    )
)

MEMBERSHIP_ADDED_V1: Final = registry.register(
    EventSchema(
        event_type="organizations.membership.added",
        version=1,
        aggregate_type="organizations.membership",
        description="A user joined an organization.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("membership_id", str),
            FieldSpec("user_id", str),
            FieldSpec("member_type", str),
        ),
    )
)

MEMBERSHIP_REMOVED_V1: Final = registry.register(
    EventSchema(
        event_type="organizations.membership.removed",
        version=1,
        aggregate_type="organizations.membership",
        description="A user left or was removed. Consumers release seats and "
        "role assignments.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("membership_id", str),
            FieldSpec("user_id", str),
        ),
    )
)

# --- identity ------------------------------------------------------------

USER_REGISTERED_V1: Final = registry.register(
    EventSchema(
        event_type="identity.user.registered",
        version=1,
        aggregate_type="identity.user",
        description="A global identity was created.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("user_id", str),
        ),
    )
)

USER_DISABLED_V1: Final = registry.register(
    EventSchema(
        event_type="identity.user.disabled",
        version=1,
        aggregate_type="identity.user",
        description="A global identity was disabled; access stops everywhere.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("user_id", str),
            FieldSpec("reason", str, required=False),
        ),
    )
)

# --- entitlements --------------------------------------------------------

ENTITLEMENT_GRANTED_V1: Final = registry.register(
    EventSchema(
        event_type="entitlements.product.granted",
        version=1,
        aggregate_type="entitlements.entitlement",
        description="An organization gained access to a product.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("product_code", str),
            FieldSpec("status", str),
            FieldSpec("seats_total", int, required=False),
        ),
    )
)

ENTITLEMENT_REVOKED_V1: Final = registry.register(
    EventSchema(
        event_type="entitlements.product.revoked",
        version=1,
        aggregate_type="entitlements.entitlement",
        description="An organization lost access to a product.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("product_code", str),
            FieldSpec("reason", str, required=False),
        ),
    )
)

ENTITLEMENT_STATUS_CHANGED_V1: Final = registry.register(
    EventSchema(
        event_type="entitlements.status.changed",
        version=1,
        aggregate_type="entitlements.entitlement",
        description="Lifecycle transition; drives UI banners and notifications.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("product_code", str),
            FieldSpec("from_status", str, required=False),
            FieldSpec("to_status", str),
        ),
    )
)

ENTITLEMENT_DRIFT_REPAIRED_V1: Final = registry.register(
    EventSchema(
        event_type="entitlements.drift.repaired",
        version=1,
        aggregate_type="entitlements.entitlement",
        description=(
            "Reconciliation found stored entitlements disagreeing with what "
            "commercial state implies, and repaired them. Drift is a bug and "
            "is never silently fixed: a rising rate means provisioning logic "
            "or event delivery is broken (06-entitlements.md §5.3)."
        ),
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("products_changed", list),
            FieldSpec("features_changed", list),
        ),
    )
)

#: Convenience export for tests and the manifest graph check.
ALL_PLATFORM_SCHEMAS: Final = (
    ORGANIZATION_CREATED_V1,
    ORGANIZATION_SUSPENDED_V1,
    ORGANIZATION_REACTIVATED_V1,
    MEMBERSHIP_ADDED_V1,
    MEMBERSHIP_REMOVED_V1,
    USER_REGISTERED_V1,
    USER_DISABLED_V1,
    ENTITLEMENT_GRANTED_V1,
    ENTITLEMENT_REVOKED_V1,
    ENTITLEMENT_STATUS_CHANGED_V1,
    ENTITLEMENT_DRIFT_REPAIRED_V1,
)

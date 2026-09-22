"""Commercial domain event schemas.

Facts about the commercial relationship, published through the outbox in the
same transaction as the state change they describe (D3, ADR-008).

Two rules govern every payload here and are asserted by
``test_commercial_events_carry_identifiers_not_dumps``:

1. **Stable identifiers, never ORM dumps.** A payload is a set of ids and
   codes a consumer can resolve for itself. Embedding a serialised
   subscription welds every consumer to this module's schema and makes the
   event impossible to version.
2. **No money, no personal data, no negotiated terms.** Events are broadcast
   to every subscribed consumer and are retained in the outbox and the dead
   letter table. A contract price in an event payload is a contract price
   disclosed to everything downstream. Consumers that need an amount ask
   ``commercial/pricing`` with the ids they were given.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.events.registry import EventSchema, FieldSpec, registry

SUBSCRIPTION_AGGREGATE: Final = "subscriptions.subscription"

SUBSCRIPTION_ACTIVATED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.subscription.activated",
        version=1,
        aggregate_type=SUBSCRIPTION_AGGREGATE,
        description=(
            "A subscription began granting access. Provisioning recomputes "
            "entitlements from durable state; it does not read this payload."
        ),
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("subscription_id", str),
            FieldSpec("from_status", str, required=False),
            FieldSpec("to_status", str),
            FieldSpec("product_codes", list),
        ),
    )
)

SUBSCRIPTION_SUSPENDED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.subscription.suspended",
        version=1,
        aggregate_type=SUBSCRIPTION_AGGREGATE,
        description="Access became read-only. Not a lockout.",
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("subscription_id", str),
            FieldSpec("from_status", str, required=False),
            FieldSpec("reason_code", str, required=False),
        ),
    )
)

SUBSCRIPTION_CANCELLED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.subscription.cancelled",
        version=1,
        aggregate_type=SUBSCRIPTION_AGGREGATE,
        description=(
            "The subscription will not renew. Access continues until "
            "effective_at, which is the period end unless cancellation was "
            "immediate."
        ),
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("subscription_id", str),
            FieldSpec("effective_at", str),
            FieldSpec("immediate", bool),
            FieldSpec("reason_code", str, required=False),
        ),
    )
)

SUBSCRIPTION_ITEM_ADDED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.subscription.item_added",
        version=1,
        aggregate_type=SUBSCRIPTION_AGGREGATE,
        description=(
            "A commercialised line was added, individually or as part of a "
            "bundle expansion. One event per item, so a bundle purchase is "
            "reconstructable line by line."
        ),
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("subscription_id", str),
            FieldSpec("subscription_item_id", str),
            FieldSpec("product_code", str),
            FieldSpec("plan_code", str),
            FieldSpec("plan_version", int),
            FieldSpec("bundle_code", str, required=False),
            FieldSpec("quantity", int),
        ),
    )
)

PLAN_CHANGED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.plan.changed",
        version=1,
        aggregate_type=SUBSCRIPTION_AGGREGATE,
        description=(
            "An upgrade or downgrade. ``effective_at`` is in the future for a "
            "period-end downgrade, which is why consumers must not assume the "
            "change has already taken effect."
        ),
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("subscription_id", str),
            FieldSpec("product_code", str),
            FieldSpec("from_plan_code", str),
            FieldSpec("from_plan_version", int),
            FieldSpec("to_plan_code", str),
            FieldSpec("to_plan_version", int),
            FieldSpec("direction", str),
            FieldSpec("effective_at", str),
        ),
    )
)

TRIAL_ENDED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.subscription.trial_ended",
        version=1,
        aggregate_type=SUBSCRIPTION_AGGREGATE,
        description=(
            "A trial reached its end. ``converted`` distinguishes a conversion "
            "to paid from an expiry, which drive different notifications."
        ),
        fields=(
            FieldSpec("organization_id", str),
            FieldSpec("subscription_id", str),
            FieldSpec("converted", bool),
        ),
    )
)

#: Convenience export for tests and the manifest graph check.
ALL_COMMERCIAL_SCHEMAS: Final = (
    SUBSCRIPTION_ACTIVATED_V1,
    SUBSCRIPTION_SUSPENDED_V1,
    SUBSCRIPTION_CANCELLED_V1,
    SUBSCRIPTION_ITEM_ADDED_V1,
    PLAN_CHANGED_V1,
    TRIAL_ENDED_V1,
)

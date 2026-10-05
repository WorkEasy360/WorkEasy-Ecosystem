"""Partner-channel domain event schemas (Phase 7, Task 95).

Facts about who may sell, who is related to whom, what was earned and what was
approved for payment. Published through the outbox in the same transaction as
the change, under the platform-owned ``commercial`` root.

Same two rules as ``commercial_events``, asserted by the same kind of test:

1. **Identifiers, never dumps.** A consumer resolves what it needs.
2. **No money, no personal data, no negotiated terms.** A commission amount in
   an event is a commission amount disclosed to every downstream consumer, and
   the partner's contact details are personal data. Consumers that need an
   amount ask ``commercial/commissions`` with the ids they were given.

These are **platform-internal** events. The customer-facing event catalogue
(``integration/events/catalog.py``) offers an organization only the events of
products it is entitled to, so nothing here is subscribable by a customer's
webhook - which is also what keeps a customer from observing that a partner
earns on it.
"""

from __future__ import annotations

from typing import Final

from workeasy360.contracts.events.registry import EventSchema, FieldSpec, registry

PARTNER_AGGREGATE: Final = "partners.partner"
LINK_AGGREGATE: Final = "partners.link"
COMMISSION_AGGREGATE: Final = "commissions.accrual"
PAYOUT_AGGREGATE: Final = "commissions.payout"

PARTNER_ACTIVATED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.partner.activated",
        version=1,
        aggregate_type=PARTNER_AGGREGATE,
        description="A partner became able to transact.",
        fields=(FieldSpec("partner_id", str), FieldSpec("from_status", str)),
    )
)

PARTNER_SUSPENDED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.partner.suspended",
        version=1,
        aggregate_type=PARTNER_AGGREGATE,
        description="A partner may no longer perform new commercial actions.",
        fields=(FieldSpec("partner_id", str), FieldSpec("from_status", str)),
    )
)

PARTNER_TERMINATED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.partner.terminated",
        version=1,
        aggregate_type=PARTNER_AGGREGATE,
        description=(
            "A partner's programme membership ended. Its customer "
            "relationships ended with it; the customers were not touched."
        ),
        fields=(
            FieldSpec("partner_id", str),
            FieldSpec("from_status", str),
            FieldSpec("links_ended", int),
        ),
    )
)

PARTNER_CAPABILITY_GRANTED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.partner.capability_granted",
        version=1,
        aggregate_type=PARTNER_AGGREGATE,
        description="A partner was granted a capability.",
        fields=(FieldSpec("partner_id", str), FieldSpec("capability", str)),
    )
)

PARTNER_CAPABILITY_REVOKED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.partner.capability_revoked",
        version=1,
        aggregate_type=PARTNER_AGGREGATE,
        description="A partner lost a capability, effective immediately.",
        fields=(FieldSpec("partner_id", str), FieldSpec("capability", str)),
    )
)

PARTNER_CUSTOMER_LINKED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.partner.customer_linked",
        version=1,
        aggregate_type=LINK_AGGREGATE,
        description="A partner became commercially related to a customer.",
        fields=(
            FieldSpec("partner_id", str),
            FieldSpec("organization_id", str),
            FieldSpec("link_id", str),
        ),
    )
)

PARTNER_CUSTOMER_UNLINKED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.partner.customer_unlinked",
        version=1,
        aggregate_type=LINK_AGGREGATE,
        description=(
            "A partner-customer relationship ended. The customer, its "
            "subscription and its entitlements are unchanged."
        ),
        fields=(
            FieldSpec("partner_id", str),
            FieldSpec("organization_id", str),
            FieldSpec("link_id", str),
            FieldSpec("reason_code", str),
        ),
    )
)

COMMISSION_ACCRUED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.commission.accrued",
        version=1,
        aggregate_type=COMMISSION_AGGREGATE,
        description="A commission was earned on a sale. The amount is not carried.",
        fields=(
            FieldSpec("partner_id", str),
            FieldSpec("accrual_id", str),
            FieldSpec("source_ref", str),
        ),
    )
)

COMMISSION_REVERSED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.commission.reversed",
        version=1,
        aggregate_type=COMMISSION_AGGREGATE,
        description="An earlier commission was offset by a reversal entry.",
        fields=(
            FieldSpec("partner_id", str),
            FieldSpec("accrual_id", str),
            FieldSpec("reverses_id", str),
            FieldSpec("reason_code", str),
        ),
    )
)

PAYOUT_APPROVED_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.payout.approved",
        version=1,
        aggregate_type=PAYOUT_AGGREGATE,
        description="A payout was approved for payment. No payment was made.",
        fields=(FieldSpec("partner_id", str), FieldSpec("payout_id", str)),
    )
)

PAYOUT_PAID_V1: Final = registry.register(
    EventSchema(
        event_type="commercial.payout.paid",
        version=1,
        aggregate_type=PAYOUT_AGGREGATE,
        description="A staff member recorded that a payout was paid out of band.",
        fields=(FieldSpec("partner_id", str), FieldSpec("payout_id", str)),
    )
)

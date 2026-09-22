"""Time-driven subscription operations. The **only** system write pathway.

Some commercial state changes have no actor. A trial reaches its end, a
scheduled cancellation's date arrives, a billing period rolls. They happen
because a clock moved, and there is no request, no session and no membership
behind them.

That creates a genuine problem with R-10. A tenant context makes the
persistence chokepoint refuse writes for a read-only organization - and the
organizations most in need of these operations are precisely the suspended,
non-paying ones whose subscription must still be cancellable and expirable.
A suspended customer whose cancellation could never take effect would keep an
open commercial record forever.

So this module - and nothing else in ``commercial/`` - writes subscription
state without a tenant context, using the audited escape hatch:

    unscoped_access(reason=...)   relaxes application scoping (Layer 2)
    organization_db_scope(org)    satisfies the RLS policy (Layer 3)

Both are required and neither is sufficient. RLS is **not** relaxed: every
write still happens inside exactly one organization's database scope, so a
bug here can corrupt one tenant's own record and cannot reach another's.

**This is not a general bypass**, and three things keep it that way:

1. It is one file, named in the ``unscoped`` allowlist, so ``grep`` gives the
   complete list of system writers.
2. Every operation is audited with ``ActorKind.SYSTEM`` and a reason. An
   audit failure fails the operation.
3. Nothing here takes a caller-supplied status. Each function applies exactly
   one transition, decided by a durable timestamp that has passed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.subscriptions.domain import advance_period, assert_transition
from workeasy360.commercial.subscriptions.hooks import notify_commercial_change
from workeasy360.contracts.enums import ActorKind, RenewalPolicy, SubscriptionStatus
from workeasy360.contracts.events import commercial_events as schemas
from workeasy360.contracts.events.envelope import Actor, DomainEvent
from workeasy360.platform.audit import services as audit
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

SYSTEM_TRANSITIONS = "commercial_system_transitions_total"


@contextmanager
def system_scope(organization_id: UUID, *, reason: str) -> Iterator[None]:
    """Enter both isolation layers for one organization, with no actor.

    The reason is mandatory and is recorded by ``unscoped_access``. A short
    or absent reason is refused rather than accepted with a shrug: this
    record is what an auditor reads.
    """
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason=reason, module="commercial.subscriptions.system"),
        organization_db_scope(organization_id),
    ):
        yield


def _system_event(
    *, organization_id: UUID, event_type: str, aggregate_id: Any, payload: dict[str, Any]
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        event_version=1,
        organization_id=organization_id,
        aggregate_type=schemas.SUBSCRIPTION_AGGREGATE,
        aggregate_id=str(aggregate_id),
        payload=payload,
        actor=Actor(kind=ActorKind.SYSTEM, service_name="commercial.subscriptions"),
    )


def process_due_subscriptions(*, at: datetime | None = None) -> dict[str, int]:
    """Apply every clock-driven transition that is now due.

    One pass over the subscriptions whose durable timestamps have passed. Each
    organization is handled in its own transaction and its own database
    scope, so one corrupt tenant cannot stop the sweep for everyone else -
    and cannot have its rows written while another tenant's scope is open.
    """
    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_due_for_attention,
    )
    from workeasy360.platform.organizations.selectors import all_organization_ids

    moment = at or timezone.now()
    counts = {
        "trials_expired": 0,
        "cancellations_applied": 0,
        "periods_rolled": 0,
        "failed": 0,
    }

    # Organizations come from the control plane; due subscriptions come from
    # inside each one's scope. A single cross-tenant scan is not available -
    # a tenant table matches zero rows without a tenant variable - and would
    # be forbidden even if it were (03-multi-tenancy.md).
    #
    # The cost is one indexed query per organization per run. At the scale
    # this schedule is set for that is unremarkable; the trigger for a
    # cheaper work list is recorded in docs/phase-2/performance-review.md.
    for organization_id in all_organization_ids():
        try:
            due = subscriptions_due_for_attention(organization_id, at=moment)
        except Exception as exc:
            counts["failed"] += 1
            logger.error(
                "commercial.system.scan_failed",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )
            continue

        changed = False
        for subscription_id in due:
            try:
                applied = _process_one(organization_id, subscription_id, moment)
                for key, value in applied.items():
                    counts[key] += value
                changed = changed or any(applied.values())
            except Exception as exc:
                counts["failed"] += 1
                logger.error(
                    "commercial.system.sweep_failed",
                    organization_id=str(organization_id),
                    error_type=type(exc).__name__,
                )
        if changed:
            notify_commercial_change(organization_id)

    if any(counts.values()):
        logger.info("commercial.system.sweep", **counts)
    return counts


def _process_one(
    organization_id: UUID, subscription_id: UUID, moment: datetime
) -> dict[str, int]:
    """Apply whichever transitions this subscription is due, in one transaction."""
    from workeasy360.commercial.subscriptions.models import Subscription

    applied = {"trials_expired": 0, "cancellations_applied": 0, "periods_rolled": 0}

    with (
        system_scope(
            organization_id,
            reason="Clock-driven commercial transition for one organization",
        ),
        transaction.atomic(),
    ):
        subscription = (
            Subscription.unscoped.select_for_update()
            .filter(id=subscription_id, organization_id=organization_id)
            .first()
        )
        if subscription is None:
            return applied

        status = subscription.status_enum

        if (
            status is SubscriptionStatus.TRIALING
            and subscription.trial_ends_at is not None
            and subscription.trial_ends_at <= moment
        ):
            _expire_trial(subscription, moment)
            applied["trials_expired"] = 1
            return applied

        if (
            status is SubscriptionStatus.CANCELLED
            and subscription.cancel_at is not None
            and subscription.cancel_at <= moment
        ):
            _expire_cancelled(subscription, moment)
            applied["cancellations_applied"] = 1
            return applied

        if (
            subscription.current_period_end <= moment
            and subscription.renewal_policy == RenewalPolicy.AUTO_RENEW.value
            and status.is_live
        ):
            _roll_period(subscription, moment)
            applied["periods_rolled"] = 1

    return applied


def _expire_trial(subscription: Any, moment: datetime) -> None:
    """A trial ended without converting. Access stops; data is retained.

    Expiry moves to ``expired`` (export-only), never deletion. Data is kept
    for the standard retention window so a late conversion restores
    everything instantly, and because deleting on expiry is legally risky and
    commercially fatal to win-backs.
    """
    assert_transition(subscription.status_enum, SubscriptionStatus.EXPIRED)
    subscription.status = SubscriptionStatus.EXPIRED.value
    subscription.ended_at = moment
    subscription.version += 1
    subscription.save(update_fields=["status", "ended_at", "version", "updated_at"])

    _publish(
        subscription,
        schemas.TRIAL_ENDED_V1.event_type,
        {
            "organization_id": str(subscription.organization_id),
            "subscription_id": str(subscription.id),
            "converted": False,
        },
    )
    audit.record_system(
        subscription.organization_id,
        "commercial.subscription.trial_expired",
        resource_type="subscriptions.subscription",
        resource_id=subscription.id,
        changes={"to": SubscriptionStatus.EXPIRED.value},
        reason="Trial period ended without conversion to a paid plan.",
    )
    metrics.increment(SYSTEM_TRANSITIONS, transition="trial_expired")


def _expire_cancelled(subscription: Any, moment: datetime) -> None:
    """A scheduled cancellation's effective date arrived."""
    assert_transition(subscription.status_enum, SubscriptionStatus.EXPIRED)
    subscription.status = SubscriptionStatus.EXPIRED.value
    subscription.ended_at = subscription.cancel_at or moment
    subscription.version += 1
    subscription.save(update_fields=["status", "ended_at", "version", "updated_at"])

    audit.record_system(
        subscription.organization_id,
        "commercial.subscription.expired",
        resource_type="subscriptions.subscription",
        resource_id=subscription.id,
        changes={"to": SubscriptionStatus.EXPIRED.value},
        reason="Scheduled cancellation reached its effective date.",
    )
    metrics.increment(SYSTEM_TRANSITIONS, transition="cancellation_applied")


def _roll_period(subscription: Any, moment: datetime) -> None:
    """Advance to the current billing period.

    **No payment is taken and none is recorded.** Phase 2 has no payment
    provider, and a renewal that quietly marked itself paid would be the most
    dangerous placeholder in the system. This advances the period so that the
    subscription's window reflects reality; collecting for it is a later
    phase's responsibility.
    """
    start, end = advance_period(
        start=subscription.current_period_start,
        end=subscription.current_period_end,
        interval=subscription.interval_enum,
        at=moment,
    )
    if (start, end) == (
        subscription.current_period_start,
        subscription.current_period_end,
    ):
        return

    subscription.current_period_start = start
    subscription.current_period_end = end
    subscription.version += 1
    subscription.save(
        update_fields=[
            "current_period_start",
            "current_period_end",
            "version",
            "updated_at",
        ]
    )
    audit.record_system(
        subscription.organization_id,
        "commercial.subscription.period_rolled",
        resource_type="subscriptions.subscription",
        resource_id=subscription.id,
        changes={
            "current_period_start": start.isoformat(),
            "current_period_end": end.isoformat(),
        },
        reason="Billing period reached its end under an auto-renew policy.",
    )
    metrics.increment(SYSTEM_TRANSITIONS, transition="period_rolled")


def suspend_for_non_payment(
    organization_id: UUID, *, subscription_id: UUID, reason_code: str
) -> Any:
    """Move a subscription to ``suspended``: read-only, not locked out.

    The customer's data stays visible and exportable, the "pay now" path
    stays open, and an entire category of angry support escalation
    disappears. Writes, background automation and API mutations stop.

    Exposed for the dunning process a payment integration will drive. It is
    not called by anything in Phase 2, and is covered by its own test rather
    than left unexercised.
    """
    from workeasy360.commercial.subscriptions.models import Subscription

    with (
        system_scope(
            organization_id,
            reason="Dunning suspension applied without a request tenant context",
        ),
        transaction.atomic(),
    ):
        subscription = (
            Subscription.unscoped.select_for_update()
            .filter(id=subscription_id, organization_id=organization_id)
            .first()
        )
        if subscription is None:
            return None

        previous = subscription.status_enum
        assert_transition(previous, SubscriptionStatus.SUSPENDED)
        subscription.status = SubscriptionStatus.SUSPENDED.value
        subscription.version += 1
        subscription.save(update_fields=["status", "version", "updated_at"])

        _publish(
            subscription,
            schemas.SUBSCRIPTION_SUSPENDED_V1.event_type,
            {
                "organization_id": str(organization_id),
                "subscription_id": str(subscription.id),
                "from_status": previous.value,
                "reason_code": reason_code,
            },
        )
        audit.record_system(
            organization_id,
            "commercial.subscription.suspended",
            resource_type="subscriptions.subscription",
            resource_id=subscription.id,
            changes={"from": previous.value, "reason_code": reason_code},
            reason=f"Subscription suspended: {reason_code}.",
        )
        metrics.increment(SYSTEM_TRANSITIONS, transition="suspended")

    notify_commercial_change(organization_id)
    return subscription


def _publish(subscription: Any, event_type: str, payload: dict[str, Any]) -> None:
    """Publish inside the caller's transaction, as every publisher must."""
    from workeasy360.integration.events import services as events

    events.publish(
        _system_event(
            organization_id=subscription.organization_id,
            event_type=event_type,
            aggregate_id=subscription.id,
            payload=payload,
        )
    )

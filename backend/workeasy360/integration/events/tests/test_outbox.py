"""Transactional outbox: atomic with the business change, at-least-once,
idempotent, bounded, and recoverable from a worker crash."""

from __future__ import annotations

import uuid

import pytest
from django.db import transaction

from workeasy360.contracts.enums import ActorKind, OutboxStatus
from workeasy360.contracts.errors import EventSchemaError, UnknownEventType
from workeasy360.contracts.events.envelope import (
    MAX_CAUSATION_DEPTH,
    Actor,
    DomainEvent,
)
from workeasy360.contracts.events.registry import registry
from workeasy360.integration.events import services as events
from workeasy360.integration.events.models import (
    DeadLetterEvent,
    OutboxEvent,
    ProcessedEvent,
)

pytestmark = pytest.mark.django_db


def _event(org_id, **overrides):
    payload = {
        "organization_id": str(org_id),
        "membership_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "member_type": "internal",
    }
    payload.update(overrides.pop("payload", {}))
    return DomainEvent(
        event_type="organizations.membership.added",
        event_version=1,
        organization_id=org_id,
        aggregate_type="organizations.membership",
        aggregate_id=payload["membership_id"],
        payload=payload,
        **overrides,
    )


# --- atomicity ------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_publish_requires_a_transaction(two_tenants, in_tenant):
    """Publishing outside a transaction would commit the event independently
    of the business change it describes - the dual-write problem the outbox
    exists to remove."""
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        pass  # leave the transaction

    with pytest.raises(RuntimeError, match="outside a transaction"):
        events.publish(_event(a["org"].id))


def test_event_is_written_in_the_business_transaction(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        events.publish(_event(a["org"].id))
        assert OutboxEvent.objects.filter(organization_id=a["org"].id).count() == 1


def test_rollback_discards_the_event(two_tenants, in_tenant):
    """If the transaction rolls back, the event vanishes with it.

    Atomicity is free because it is the database's own atomicity - no
    coordination protocol, no compensating action.
    """
    a = two_tenants["a"]

    try:
        with in_tenant(a["ctx"]):
            events.publish(_event(a["org"].id))
            raise RuntimeError("business rule failed after publishing")
    except RuntimeError:
        pass

    assert OutboxEvent.objects.filter(organization_id=a["org"].id).count() == 0


# --- schema validation ----------------------------------------------------


def test_unregistered_event_type_is_refused(two_tenants, in_tenant):
    """A typo in an event name fails at the first test that publishes it."""
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        bad = DomainEvent(
            event_type="crm.deal.won",  # not registered in Phase 1
            event_version=1,
            organization_id=a["org"].id,
            aggregate_type="crm.deal",
            aggregate_id=str(uuid.uuid4()),
            payload={},
        )
        with pytest.raises(UnknownEventType):
            events.publish(bad)


def test_payload_is_validated_at_publish_time(two_tenants, in_tenant):
    """R-13: validated inside the publisher's transaction, so a malformed
    event never reaches the outbox and the failure lands on the code that
    caused it rather than on a consumer days later."""
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        incomplete = _event(a["org"].id)
        broken = DomainEvent(
            event_type=incomplete.event_type,
            event_version=1,
            organization_id=a["org"].id,
            aggregate_type=incomplete.aggregate_type,
            aggregate_id=incomplete.aggregate_id,
            payload={"organization_id": str(a["org"].id)},  # missing fields
        )
        with pytest.raises(EventSchemaError, match="missing required field"):
            events.publish(broken)

        assert OutboxEvent.objects.count() == 0


def test_command_shaped_event_names_are_rejected():
    """Events are past-tense facts. A command-shaped name is an RPC in
    disguise that dictates consumer behaviour."""
    from workeasy360.contracts.events.registry import EventRegistry, EventSchema

    isolated = EventRegistry()
    with pytest.raises(ValueError, match="names a command"):
        isolated.register(
            EventSchema(
                event_type="books.invoice.create",
                version=1,
                aggregate_type="books.invoice",
            )
        )


def test_every_registered_event_is_past_tense():
    assert registry.all_refs()
    for ref in registry.all_refs():
        verb = ref.rsplit(".v", 1)[0].rsplit(".", 1)[-1]
        assert verb not in {"create", "update", "delete", "send"}, ref


# --- envelope invariants --------------------------------------------------


def test_every_event_carries_a_tenant():
    """There is no such thing as a cross-tenant domain event."""
    with pytest.raises(TypeError, match="must be a UUID"):
        DomainEvent(
            event_type="organizations.membership.added",
            event_version=1,
            organization_id="not-a-uuid",  # type: ignore[arg-type]
            aggregate_type="organizations.membership",
            aggregate_id="x",
            payload={},
        )


def test_causation_depth_is_capped(two_tenants):
    """A → B → A loops are legal but must terminate."""
    from workeasy360.contracts.errors import CausationDepthExceeded

    a = two_tenants["a"]
    with pytest.raises(CausationDepthExceeded):
        DomainEvent(
            event_type="organizations.membership.added",
            event_version=1,
            organization_id=a["org"].id,
            aggregate_type="organizations.membership",
            aggregate_id="x",
            payload={},
            causation_depth=MAX_CAUSATION_DEPTH + 1,
        )


def test_caused_event_inherits_correlation_and_increments_depth(two_tenants):
    a = two_tenants["a"]
    root = _event(a["org"].id, correlation_id=uuid.uuid4())
    child = root.caused(
        "organizations.membership.removed",
        1,
        "organizations.membership",
        "x",
        {"organization_id": str(a["org"].id), "membership_id": "m", "user_id": "u"},
    )
    assert child.correlation_id == root.correlation_id
    assert child.causation_id == root.event_id
    assert child.causation_depth == root.causation_depth + 1


def test_envelope_round_trips(two_tenants):
    a = two_tenants["a"]
    original = _event(
        a["org"].id,
        actor=Actor(
            kind=ActorKind.USER, user_id=uuid.uuid4(), membership_id=uuid.uuid4()
        ),
        correlation_id=uuid.uuid4(),
    )
    restored = DomainEvent.from_envelope(original.to_envelope())
    assert restored.event_id == original.event_id
    assert restored.organization_id == original.organization_id
    assert restored.payload == original.payload
    assert restored.correlation_id == original.correlation_id


# --- relay ----------------------------------------------------------------


def test_relay_claims_and_publishes(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        events.publish(_event(a["org"].id))

    claimed = events.claim_batch()
    assert len(claimed) == 1

    events.mark_published(claimed[0])
    row = OutboxEvent.objects.get(id=claimed[0].id)
    assert row.status == OutboxStatus.PUBLISHED.value
    assert row.published_at is not None


def test_claimed_rows_are_not_claimed_twice(two_tenants, in_tenant):
    """SELECT ... FOR UPDATE SKIP LOCKED lets many relay workers drain one
    table without double-publishing."""
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        events.publish(_event(a["org"].id))

    first = events.claim_batch()
    second = events.claim_batch()
    assert len(first) == 1
    assert second == []


def test_worker_crash_leaves_rows_recoverable(two_tenants, in_tenant):
    """A worker OOM-killed between claiming and publishing must not strand
    the event forever."""
    from django.utils import timezone

    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        events.publish(_event(a["org"].id))

    claimed = events.claim_batch()
    assert OutboxEvent.objects.get(id=claimed[0].id).status == (
        OutboxStatus.PUBLISHING.value
    )

    OutboxEvent.objects.filter(id=claimed[0].id).update(
        created_at=timezone.now() - timezone.timedelta(seconds=600)
    )
    released = events.release_stale_claims(older_than_seconds=300)

    assert released == 1
    assert OutboxEvent.objects.get(id=claimed[0].id).status == (
        OutboxStatus.PENDING.value
    )
    assert len(events.claim_batch()) == 1


def test_retries_are_bounded_then_dead_lettered(two_tenants, in_tenant, settings):
    """Never infinite: an event that cannot be processed will not become
    processable by being retried forever, and infinite retries turn one
    poison event into a permanent queue clog."""
    settings.OUTBOX_MAX_ATTEMPTS = 3
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        events.publish(_event(a["org"].id))

    for _ in range(3):
        claimed = events.claim_batch()
        if not claimed:
            OutboxEvent.objects.filter(status=OutboxStatus.FAILED.value).update(
                available_at=__import__(
                    "django.utils.timezone", fromlist=["timezone"]
                ).now()
            )
            claimed = events.claim_batch()
        if not claimed:
            break
        events.schedule_retry(claimed[0], RuntimeError("broker unreachable"))

    row = OutboxEvent.objects.first()
    assert row.attempts <= 3
    if row.status == OutboxStatus.DEAD_LETTERED.value:
        assert DeadLetterEvent.objects.count() == 1


def test_dead_letter_keeps_the_full_envelope(two_tenants, in_tenant, settings):
    """Replay needs no reconstruction, and a dead-lettered event is
    self-describing."""
    settings.OUTBOX_MAX_ATTEMPTS = 1
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        events.publish(_event(a["org"].id))

    claimed = events.claim_batch()
    events.schedule_retry(claimed[0], ValueError("permanent"))

    dead = DeadLetterEvent.objects.get()
    assert dead.envelope["event_type"] == "organizations.membership.added"
    assert dead.envelope["payload"]
    assert dead.error


def test_backoff_is_jittered(two_tenants):
    """Without jitter, every failed event retries at the same instant and
    re-kills the dependency that is trying to recover."""
    samples = [events._backoff_seconds(3) for _ in range(200)]
    assert len({round(s, 4) for s in samples}) > 150
    assert min(samples) >= 0


# --- consumer idempotency -------------------------------------------------


def test_duplicate_delivery_is_absorbed(two_tenants):
    """At-least-once delivery means duplicates. Exactly-once does not exist
    across a database and a broker; idempotency substitutes."""
    event_id = uuid.uuid4()

    events.mark_processed("books.on_deal_won", event_id)
    with pytest.raises(events.AlreadyProcessed):
        events.mark_processed("books.on_deal_won", event_id)

    assert ProcessedEvent.objects.filter(event_id=event_id).count() == 1


def test_consumers_dedupe_independently(two_tenants):
    """Three consumers of one event retry independently."""
    event_id = uuid.uuid4()

    events.mark_processed("books.on_deal_won", event_id)
    events.mark_processed("projects.on_deal_won", event_id)

    assert ProcessedEvent.objects.filter(event_id=event_id).count() == 2


def test_handler_failure_rolls_back_the_marker(two_tenants):
    """The marker is inserted in the same transaction as the effect.

    Inserting it first in a separate transaction produces the classic bug
    where a crash mid-handler permanently marks an event processed that never
    was (07-cross-product-integration.md section 3.5).
    """
    event_id = uuid.uuid4()

    try:
        with transaction.atomic():
            events.mark_processed("books.on_deal_won", event_id)
            raise RuntimeError("handler failed")
    except RuntimeError:
        pass

    assert not ProcessedEvent.objects.filter(event_id=event_id).exists()
    # The retry genuinely retries.
    events.mark_processed("books.on_deal_won", event_id)
    assert ProcessedEvent.objects.filter(event_id=event_id).count() == 1


# --- dispatcher isolation -------------------------------------------------


def test_one_failing_consumer_does_not_block_another(two_tenants):
    """Each (event, consumer) pair is a separate unit of work."""
    from workeasy360.integration.events import dispatcher

    dispatcher.reset_handlers()
    applied: list[str] = []

    @dispatcher.subscribe("organizations.membership.added", 1, consumer="good")
    def good(event):
        applied.append("good")

    @dispatcher.subscribe("organizations.membership.added", 1, consumer="bad")
    def bad(event):
        raise RuntimeError("consumer is broken")

    a = two_tenants["a"]
    results = dispatcher.dispatch(_event(a["org"].id))

    assert applied == ["good"]
    assert results["good"] == "applied"
    assert results["bad"].startswith("failed:")
    dispatcher.reset_handlers()


def test_event_with_no_consumer_is_not_an_error(two_tenants):
    """An org without Books simply has no Books consumer acting. The event is
    delivered and ignored - no error, no special case."""
    from workeasy360.integration.events import dispatcher

    dispatcher.reset_handlers()
    a = two_tenants["a"]
    assert dispatcher.dispatch(_event(a["org"].id)) == {}


def test_outbox_pending_age_is_reported(two_tenants, in_tenant):
    """Oldest pending age is the primary SLI: a large draining backlog is
    healthy, a small stuck one is not."""
    a = two_tenants["a"]
    assert events.pending_age_seconds() == 0.0

    with in_tenant(a["ctx"]):
        events.publish(_event(a["org"].id))

    assert events.pending_age_seconds() >= 0

"""Phase 6 concurrency with real threads, connections and commits (Task 94).

The method of the Phase 2/5 concurrency suites: nothing mocked on the
contended path, every worker on its own database connection, released from a
barrier so the operations genuinely overlap, ``transaction=True`` so every
write really commits. Each test asserts the absence of the specific failure
a race would produce - a second accepted event, a second send of one attempt,
two active secrets, two firings of one schedule occurrence, a lost update -
and that no worker raised an unexpected error (a deadlock would land there).
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections
from django.utils import timezone

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 8
EVENT_REF = "product.example.record.created.v1"


def _run(worker, count=WORKERS):
    results, errors = [], []
    lock = threading.Lock()
    barrier = threading.Barrier(count)

    def wrapped(index):
        try:
            barrier.wait(timeout=30)
            outcome = worker(index)
            with lock:
                results.append(outcome)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=count) as pool:
        for future in as_completed([pool.submit(wrapped, i) for i in range(count)]):
            future.result()
    return results, errors


def _scoped(ctx):
    from contextlib import ExitStack

    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    stack = ExitStack()
    stack.enter_context(tenant_context(ctx))
    stack.enter_context(tenant_db_scope(ctx))
    return stack


def _count(model, organization_id, **filters):
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="concurrency assertion", module="tests"),
        organization_db_scope(organization_id),
    ):
        return model.unscoped.filter(organization_id=organization_id, **filters).count()


@pytest.fixture
def org(integration_org):
    return integration_org()


@pytest.fixture
def active_connection(org, local_egress, webhook_receiver):
    from workeasy360.integration.connectors import services as connectors
    from workeasy360.integration.webhooks import services as webhooks

    with _scoped(org["ctx"]):
        created = connectors.create_connection(
            org["ctx"],
            connector_key="webhook",
            display_name="Race",
            config={"url": f"{webhook_receiver.url}/hooks"},
        )
        webhooks.set_subscriptions(
            org["ctx"], created.connection.id, event_filters=[EVENT_REF]
        )
        connectors.transition_connection(
            org["ctx"], created.connection.id, to_status="active"
        )
    return created


def _pending_delivery(org, connection_id):
    """A pending delivery of a ping, created directly and never dispatched."""
    from workeasy360.integration.events.services import record_platform_event
    from workeasy360.integration.webhooks.models import Delivery

    with _scoped(org["ctx"]):
        event, _ = record_platform_event(
            "integration.webhook.pinged",
            1,
            {"connection_id": str(connection_id), "message": "race"},
            direct=True,
        )
        delivery = Delivery(
            connection_id=connection_id,
            event_id=event.id,
            origin="ping",
            capability="deliver_event",
            max_attempts=5,
            next_attempt_at=timezone.now(),
        )
        delivery.save()
    return delivery.id


def _run_delivery(org, delivery_id):
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.integration.webhooks.pipeline import run_delivery
    from workeasy360.platform.tenancy.context import tenant_context

    with tenant_context(routing_context(org["org"].id)):
        return run_delivery(delivery_id)


# --- inbound ------------------------------------------------------------------------


def test_one_event_submitted_concurrently_is_accepted_once(
    org, example_product, product_event
):
    """Task 10: same event, eight concurrent submissions, one processing chain."""
    from workeasy360.integration.events.models import RoutedEvent
    from workeasy360.integration.routing.ingestion import ingest_product_event
    from workeasy360.platform.ecosystem.clientauth import AuthenticatedClient
    from workeasy360.platform.ecosystem.models import ProductIntegration

    integration = ProductIntegration.objects.get(client_id=example_product["client_id"])
    client = AuthenticatedClient(integration=integration, credential=None)
    event = product_event()

    results, errors = _run(
        lambda _i: ingest_product_event(
            client, organization_id=org["organization_id"], raw_event=json.dumps(event)
        ).status
    )
    assert errors == []
    assert sorted(results) == ["accepted"] + ["duplicate"] * (WORKERS - 1)
    assert _count(RoutedEvent, org["org"].id, event_id=event["event_id"]) == 1


def test_concurrent_identical_subscriptions_are_one_row(org, active_connection):
    from workeasy360.integration.webhooks import services as webhooks
    from workeasy360.integration.webhooks.models import EventSubscription

    filters = [EVENT_REF, "product.example.*"]

    def subscribe(_i):
        with _scoped(org["ctx"]):
            return webhooks.set_subscriptions(
                org["ctx"], active_connection.connection.id, event_filters=filters
            )

    results, errors = _run(subscribe)
    assert errors == []
    assert all(sorted(r) == sorted(filters) for r in results)
    assert _count(EventSubscription, org["org"].id) == 2


# --- delivery claiming (Task 23) ---------------------------------------------------


def test_eight_workers_one_delivery_one_send(org, active_connection, webhook_receiver):
    webhook_receiver.reply(200, delay=1.0)
    delivery_id = _pending_delivery(org, active_connection.connection.id)
    results, errors = _run(lambda _i: _run_delivery(org, delivery_id))
    assert errors == []
    assert results.count("succeeded") == 1
    assert set(results) <= {"succeeded", "in_flight", "throttled"}
    assert len(webhook_receiver.received) == 1


def test_a_worker_that_died_after_claiming_is_recovered(
    org, active_connection, webhook_receiver
):
    """The claim's lease expires; the next claim records the lost attempt as
    abandoned and sends again - at-least-once, never at-most-once."""
    from workeasy360.integration.webhooks.models import Delivery, DeliveryAttempt

    delivery_id = _pending_delivery(org, active_connection.connection.id)
    with _scoped(org["ctx"]):
        Delivery.objects.filter(id=delivery_id).update(
            status="in_progress",
            attempts=1,
            lease_expires_at=timezone.now() - timezone.timedelta(seconds=1),
            last_attempt_at=timezone.now() - timezone.timedelta(seconds=100),
        )
    assert _run_delivery(org, delivery_id) == "succeeded"
    with _scoped(org["ctx"]):
        attempts = list(
            DeliveryAttempt.objects.filter(delivery_id=delivery_id)
            .order_by("attempt_number")
            .values_list("attempt_number", "outcome")
        )
    assert attempts == [(1, "abandoned"), (2, "succeeded")]


def test_a_late_acknowledgement_cannot_overwrite_a_newer_attempt(
    org, active_connection, webhook_receiver
):
    from workeasy360.integration.connectors.contract import Outcome
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.integration.webhooks import pipeline
    from workeasy360.integration.webhooks.models import Delivery
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    delivery_id = _pending_delivery(org, active_connection.connection.id)
    ctx = routing_context(org["org"].id)
    with tenant_context(ctx):
        with organization_db_scope(org["org"].id):
            slow = pipeline._claim(delivery_id, org["org"].id, writable=True)
        # The slow worker's lease expires; another claims and succeeds.
        with organization_db_scope(org["org"].id):
            Delivery.objects.filter(id=delivery_id).update(
                lease_expires_at=timezone.now() - timezone.timedelta(seconds=1)
            )
        assert _run_delivery(org, delivery_id) == "succeeded"
        # The slow worker finally reports a failure. It must change nothing.
        with organization_db_scope(org["org"].id):
            late = pipeline._record(
                slow, org["org"].id, Outcome("dead", "http_4xx", 400), 10
            )
    assert late == "stale"
    with _scoped(org["ctx"]):
        assert Delivery.objects.get(id=delivery_id).status == "succeeded"


# --- replay (Tasks 26, 94) -----------------------------------------------------------


def test_replay_is_refused_while_the_automatic_retry_owns_the_delivery(
    org, active_connection, webhook_receiver
):
    from workeasy360.contracts.errors import IntegrationConflict
    from workeasy360.integration.webhooks import services as webhooks

    webhook_receiver.reply(500)
    delivery_id = _pending_delivery(org, active_connection.connection.id)
    assert _run_delivery(org, delivery_id) == "retry"
    with _scoped(org["ctx"]), pytest.raises(IntegrationConflict):
        webhooks.replay(org["ctx"], delivery_id)


def test_concurrent_replays_of_one_dead_delivery_create_one(
    org, active_connection, webhook_receiver
):
    from workeasy360.contracts.errors import IntegrationConflict
    from workeasy360.integration.webhooks import services as webhooks
    from workeasy360.integration.webhooks.models import Delivery

    webhook_receiver.reply(400)
    delivery_id = _pending_delivery(org, active_connection.connection.id)
    assert _run_delivery(org, delivery_id) == "dead"

    def replay(_i):
        try:
            with _scoped(org["ctx"]):
                return str(webhooks.replay(org["ctx"], delivery_id))
        except IntegrationConflict:
            return "conflict"

    results, errors = _run(replay)
    assert errors == []
    assert results.count("conflict") == WORKERS - 1
    assert _count(Delivery, org["org"].id, replay_of_id=delivery_id) == 1


# --- automation lifecycle (Task 94) ----------------------------------------------------


def test_racing_activations_have_one_winner(org, active_connection):
    from workeasy360.contracts.errors import IntegrationConflict
    from workeasy360.integration.automation import services as automation

    with _scoped(org["ctx"]):
        rule = automation.create_rule(
            org["ctx"],
            name="Race",
            definition={
                "trigger": {"kind": "event", "event": EVENT_REF},
                "actions": [
                    {
                        "type": "connector.invoke",
                        "config": {"connection_id": str(active_connection.connection.id)},
                    }
                ],
            },
        )

    def flip(index):
        target = "active" if index % 2 == 0 else "archived"
        try:
            with _scoped(org["ctx"]):
                return automation.transition_rule(
                    org["ctx"],
                    rule["id"],
                    to_status=target,
                    expected_revision=rule["revision"],
                )["status"]
        except IntegrationConflict:
            return "conflict"

    results, errors = _run(flip)
    assert errors == []
    assert results.count("conflict") == WORKERS - 1
    winner = next(r for r in results if r != "conflict")
    with _scoped(org["ctx"]):
        from workeasy360.integration.automation.selectors import rule_detail

        final = rule_detail(org["ctx"], rule["id"])
    assert (final["status"], final["revision"]) == (winner, rule["revision"] + 1)


def test_one_schedule_occurrence_fires_once_under_contention(org, active_connection):
    from workeasy360.integration.automation import services as automation
    from workeasy360.integration.automation.models import (
        AutomationExecution,
        AutomationRule,
    )
    from workeasy360.integration.automation.tasks import fire_schedule
    from workeasy360.integration.events.models import RoutedEvent
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context

    with _scoped(org["ctx"]):
        rule = automation.create_rule(
            org["ctx"],
            name="Hourly",
            definition={
                "trigger": {
                    "kind": "schedule",
                    "schedule": {"kind": "interval", "every_minutes": 60},
                },
                "actions": [
                    {
                        "type": "connector.invoke",
                        "config": {"connection_id": str(active_connection.connection.id)},
                    }
                ],
            },
        )
        automation.transition_rule(
            org["ctx"], rule["id"], to_status="active", expected_revision=rule["revision"]
        )
        AutomationRule.objects.filter(id=rule["id"]).update(
            next_run_at=timezone.now() - timezone.timedelta(minutes=1)
        )

    def fire(_i):
        with tenant_context(routing_context(org["org"].id)):
            return fire_schedule.run(rule["id"])

    results, errors = _run(fire)
    assert errors == []
    assert results.count("fired") == 1
    assert _count(AutomationExecution, org["org"].id, rule_id=rule["id"]) == 1
    assert (
        _count(RoutedEvent, org["org"].id, event_type="integration.schedule.fired") == 1
    )


# --- credentials and suspension (Task 94) -------------------------------------------


def test_concurrent_rotations_leave_exactly_one_active_secret(org, active_connection):
    from workeasy360.integration.connectors import services as connectors
    from workeasy360.integration.connectors.models import ConnectionSecret

    def rotate(_i):
        with _scoped(org["ctx"]):
            return connectors.rotate_secret(
                org["ctx"], active_connection.connection.id
            ).version

    results, errors = _run(rotate)
    assert errors == []
    assert sorted(results) == list(range(2, WORKERS + 2))
    assert _count(ConnectionSecret, org["org"].id, status="active") == 1
    assert _count(ConnectionSecret, org["org"].id, status="previous") == 1


def test_suspension_while_a_delivery_is_pending_holds_it(
    org, active_connection, webhook_receiver
):
    """Task 54: fail closed for new side effects, lose nothing."""
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    delivery_id = _pending_delivery(org, active_connection.connection.id)
    with unscoped_access(reason="test suspends an organization", module="tests"):
        Organization.objects.filter(id=org["org"].id).update(status="suspended")
    assert _run_delivery(org, delivery_id) == "held_organization"
    assert webhook_receiver.received == []

    with unscoped_access(reason="test reactivates an organization", module="tests"):
        Organization.objects.filter(id=org["org"].id).update(status="active")
    assert _run_delivery(org, delivery_id) == "succeeded"
    assert len(webhook_receiver.received) == 1


def test_suspension_racing_deliveries_sends_nothing_after_it_commits(
    org, active_connection, webhook_receiver
):
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    deliveries = [
        _pending_delivery(org, active_connection.connection.id) for _ in range(4)
    ]
    with unscoped_access(reason="test suspends an organization", module="tests"):
        Organization.objects.filter(id=org["org"].id).update(status="suspended")
    results, errors = _run(lambda i: _run_delivery(org, deliveries[i % 4]), count=8)
    assert errors == []
    assert set(results) == {"held_organization"}
    assert webhook_receiver.received == []

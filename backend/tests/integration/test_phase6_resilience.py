"""Phase 6 failure behaviour (Tasks 49, 50, 67-72).

Every test injects one failure at the point it would really occur - a
receiver that refuses, times out, throttles or errors; a broker that is
down; a cache that is down; a database write that fails; a worker that dies
between sending and recording - and asserts two things: the documented
semantics hold, and the failure stays inside the integration it belongs to.
"""

from __future__ import annotations

import socket

import pytest
from django.utils import timezone

pytestmark = [pytest.mark.django_db]

EVENT_REF = "product.example.record.created.v1"


def _scoped(ctx):
    from contextlib import ExitStack

    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    stack = ExitStack()
    stack.enter_context(tenant_context(ctx))
    stack.enter_context(tenant_db_scope(ctx))
    return stack


def _connection(org, url, *, name="Endpoint"):
    from workeasy360.integration.connectors import services as connectors
    from workeasy360.integration.webhooks import services as webhooks

    with _scoped(org["ctx"]):
        created = connectors.create_connection(
            org["ctx"], connector_key="webhook", display_name=name, config={"url": url}
        )
        webhooks.set_subscriptions(
            org["ctx"], created.connection.id, event_filters=[EVENT_REF]
        )
        connectors.transition_connection(
            org["ctx"], created.connection.id, to_status="active"
        )
    return created


def _publish(org, example_product, product_event, **overrides):
    """Ingest and route one event, returning the ids of the deliveries it made."""
    import json

    from workeasy360.integration.routing import fanout
    from workeasy360.integration.routing.ingestion import ingest_product_event
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.ecosystem.clientauth import AuthenticatedClient
    from workeasy360.platform.ecosystem.models import ProductIntegration
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    integration = ProductIntegration.objects.get(client_id=example_product["client_id"])
    event = product_event(**overrides)
    ingest_product_event(
        AuthenticatedClient(integration=integration, credential=None),
        organization_id=org["organization_id"],
        raw_event=json.dumps(event),
    )
    from workeasy360.integration.events.models import RoutedEvent

    with (
        tenant_context(routing_context(org["org"].id)),
        organization_db_scope(org["org"].id),
    ):
        pk = RoutedEvent.objects.get(event_id=event["event_id"]).id
        return fanout.route_event(pk).deliveries


def _deliver(org, delivery_id):
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.integration.webhooks.pipeline import run_delivery
    from workeasy360.platform.tenancy.context import tenant_context

    with tenant_context(routing_context(org["org"].id)):
        return run_delivery(delivery_id)


def _make_due(org, delivery_id):
    from workeasy360.integration.webhooks.models import Delivery

    with _scoped(org["ctx"]):
        Delivery.objects.filter(id=delivery_id).update(
            next_attempt_at=timezone.now() - timezone.timedelta(seconds=1)
        )


def _delivery(org, delivery_id):
    from workeasy360.integration.webhooks.models import Delivery

    with _scoped(org["ctx"]):
        return Delivery.objects.get(id=delivery_id)


def _connection_row(org, connection_id):
    from workeasy360.integration.connectors.models import Connection

    with _scoped(org["ctx"]):
        return Connection.objects.get(id=connection_id)


@pytest.fixture
def org(integration_org, local_egress):
    return integration_org()


def _refusing_url():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/hooks"


# --- the receiver is down (Tasks 70, 72) -----------------------------------------


def test_a_down_receiver_retries_with_growing_backoff_then_dies(
    org, example_product, product_event, settings
):
    settings.WEBHOOK_MAX_ATTEMPTS = 4
    _connection(org, _refusing_url())
    (delivery_id,) = _publish(org, example_product, product_event)

    delays = []
    for _attempt in range(4):
        _make_due(org, delivery_id)
        status = _deliver(org, delivery_id)
        # Measured from after the attempt: the backoff starts when the
        # failure is recorded, not when the (possibly slow) connect began.
        after = timezone.now()
        row = _delivery(org, delivery_id)
        if status == "retry":
            delays.append((row.next_attempt_at - after).total_seconds())
    assert status == "dead"
    assert (row.dead_reason, row.attempts) == ("retries_exhausted", 4)
    assert row.last_category == "connect_failed"
    # Equal jitter: attempt n waits in [base*2^(n-1)/2, base*2^(n-1)].
    base = settings.WEBHOOK_RETRY_BASE_S
    for n, delay in enumerate(delays):
        assert base * 2**n / 2 - 1 <= delay <= base * 2**n + 1


def test_one_failing_integration_leaves_the_others_unaffected(
    org, example_product, product_event, webhook_receiver, client
):
    healthy = _connection(org, f"{webhook_receiver.url}/ok", name="Healthy")
    broken = _connection(org, _refusing_url(), name="Broken")
    deliveries = _publish(org, example_product, product_event)
    outcomes = {_delivery(org, d).connection_id: _deliver(org, d) for d in deliveries}
    assert outcomes[healthy.connection.id] == "succeeded"
    assert outcomes[broken.connection.id] == "retry"
    assert len(webhook_receiver.received) == 1
    # And no external endpoint is anywhere near readiness (Task 71).
    assert client.get("/healthz/ready").status_code == 200


def test_the_circuit_opens_and_holds_deliveries_without_charging_attempts(
    org, example_product, product_event, settings
):
    settings.INTEGRATION_CIRCUIT_FAILURE_THRESHOLD = 2
    created = _connection(org, _refusing_url())
    first, second, third = (
        _publish(org, example_product, product_event)[0] for _ in range(3)
    )
    assert _deliver(org, first) == "retry"
    assert _deliver(org, second) == "retry"
    connection = _connection_row(org, created.connection.id)
    assert connection.circuit_open_until > timezone.now()
    # The third is held: no attempt, no request, its timer moved to the
    # circuit's reopening.
    assert _deliver(org, third) == "held_circuit"
    assert _delivery(org, third).attempts == 0


def test_sustained_failure_auto_suspends_the_connection(
    org, example_product, product_event, settings
):
    """Task 50: a permanently broken endpoint stops consuming capacity."""
    settings.INTEGRATION_CIRCUIT_FAILURE_THRESHOLD = 100
    settings.INTEGRATION_AUTO_SUSPEND_FAILURES = 3
    created = _connection(org, _refusing_url())
    for _ in range(3):
        (delivery_id,) = _publish(org, example_product, product_event)
        _deliver(org, delivery_id)
    connection = _connection_row(org, created.connection.id)
    assert connection.status == "suspended"
    assert "automatically" in connection.status_reason
    # New events no longer fan out to it at all.
    assert _publish(org, example_product, product_event) == []
    from workeasy360.platform.audit.models import AuditEvent

    with _scoped(org["ctx"]):
        assert AuditEvent.objects.filter(
            action="integration.connection.auto_suspended"
        ).exists()


def test_a_backlogged_connection_does_not_grow_without_bound(
    org, example_product, product_event, settings
):
    """Task 50/72: beyond the open-delivery bound, new deliveries are
    recorded dead (visible, replayable) - no job, no timer, no retry."""
    from workeasy360.integration.scheduling.models import DueWork

    settings.WEBHOOK_MAX_OPEN_PER_CONNECTION = 3
    _connection(org, _refusing_url())
    created = [d for _ in range(6) for d in _publish(org, example_product, product_event)]
    assert len(created) == 3
    from workeasy360.integration.webhooks.models import Delivery

    with _scoped(org["ctx"]):
        dead = Delivery.objects.filter(status="dead", dead_reason="backlog_limit").count()
    assert dead == 3
    assert DueWork.objects.filter(kind="webhooks.delivery").count() == 3


# --- endpoint behaviours (Tasks 20, 21, 67) ------------------------------------------


def test_retry_after_is_honoured_but_clamped(
    org, example_product, product_event, webhook_receiver, settings
):
    settings.WEBHOOK_RETRY_AFTER_MAX_S = 600
    _connection(org, f"{webhook_receiver.url}/hooks")
    webhook_receiver.reply(429, headers={"Retry-After": "86400"})
    (delivery_id,) = _publish(org, example_product, product_event)
    before = timezone.now()
    assert _deliver(org, delivery_id) == "retry"
    wait = (_delivery(org, delivery_id).next_attempt_at - before).total_seconds()
    assert 599 <= wait <= 601


@pytest.mark.parametrize(
    ("status", "outcome", "category"),
    [
        (400, "dead", "http_4xx"),
        (404, "dead", "http_4xx"),
        (410, "dead", "http_4xx"),
        (301, "dead", "redirect_refused"),
        (500, "retry", "http_5xx"),
        (503, "retry", "http_5xx"),
    ],
)
def test_status_codes_map_to_retry_or_dead(
    org, example_product, product_event, webhook_receiver, status, outcome, category
):
    _connection(org, f"{webhook_receiver.url}/hooks")
    webhook_receiver.reply(status, headers={"Location": "http://169.254.169.254/"})
    (delivery_id,) = _publish(org, example_product, product_event)
    assert _deliver(org, delivery_id) == outcome
    assert _delivery(org, delivery_id).last_category == category


def test_a_timeout_is_retried(
    org, example_product, product_event, webhook_receiver, settings
):
    settings.INTEGRATION_EGRESS_READ_TIMEOUT_S = 0.5
    settings.INTEGRATION_EGRESS_TOTAL_TIMEOUT_S = 2.0
    _connection(org, f"{webhook_receiver.url}/hooks")
    webhook_receiver.reply(200, delay=1.5)
    (delivery_id,) = _publish(org, example_product, product_event)
    assert _deliver(org, delivery_id) == "retry"
    assert _delivery(org, delivery_id).last_category in {
        "read_timeout",
        "deadline_exceeded",
    }


def test_a_dns_failure_is_retried(org, example_product, product_event, settings):
    settings.INTEGRATION_EGRESS_ALLOW_HTTP = False
    settings.INTEGRATION_EGRESS_ALLOW_LOOPBACK = False
    _connection(org, "https://does-not-exist.invalid/hooks")
    (delivery_id,) = _publish(org, example_product, product_event)
    assert _deliver(org, delivery_id) == "retry"
    assert _delivery(org, delivery_id).last_category in {"dns_failure", "dns_timeout"}


def test_rebinding_at_send_time_kills_the_delivery_without_sending(
    org, example_product, product_event, settings, monkeypatch
):
    """Saved while the name was public; resolves to the metadata service at
    send time. Refused (dead, not retried) and nothing is sent."""
    from workeasy360.integration.egress import client as egress

    settings.INTEGRATION_EGRESS_ALLOW_HTTP = False
    settings.INTEGRATION_EGRESS_ALLOW_LOOPBACK = False
    _connection(org, "https://hooks.example.com/in")
    monkeypatch.setattr(
        egress.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))
        ],
    )
    monkeypatch.setattr(
        egress.socket, "create_connection", lambda *a, **k: pytest.fail("connected")
    )
    (delivery_id,) = _publish(org, example_product, product_event)
    assert _deliver(org, delivery_id) == "dead"
    row = _delivery(org, delivery_id)
    assert (row.dead_reason, row.last_category) == ("rejected", "destination_blocked")


# --- the platform's own dependencies (Tasks 67-69) -------------------------------------


def test_a_broker_outage_loses_no_work(
    org, example_product, product_event, webhook_receiver, monkeypatch
):
    """Task 68: every unit of work has a durable timer; the sweeper finds it
    once the broker is back. A broker outage is not per-item, so the first
    failed enqueue ends the batch: a real unreachable broker costs its 1 s
    connect timeout per attempt, and trying all 200 items of a batch would
    hold the worker far past its time limit."""
    from kombu.exceptions import OperationalError

    from workeasy360.integration.scheduling.models import DueWork
    from workeasy360.integration.scheduling.tasks import dispatch_due
    from workeasy360.integration.webhooks import tasks as webhook_tasks

    for n in range(3):
        _connection(org, f"{webhook_receiver.url}/hooks/{n}", name=f"Endpoint {n}")
    delivery_ids = _publish(org, example_product, product_event)
    assert len(delivery_ids) == 3
    assert DueWork.objects.filter(target_id__in=delivery_ids).count() == 3

    attempts = []

    def broker_down(*_a, **_k):
        attempts.append(1)
        raise OperationalError("[Errno 111] Connection refused")

    DueWork.objects.update(due_at=timezone.now())
    monkeypatch.setattr(webhook_tasks.deliver, "apply_async", broker_down)
    result = dispatch_due()
    assert len(attempts) == 1  # stopped at the first broker error
    assert result["failed"] == 1 and result["deferred"] == 2
    assert webhook_receiver.received == []
    monkeypatch.undo()

    # The claim leases are still running; once they expire the sweeper retries.
    DueWork.objects.update(due_at=timezone.now())
    dispatch_due()
    assert len(webhook_receiver.received) == 3
    assert {_delivery(org, d).status for d in delivery_ids} == {"succeeded"}
    assert not DueWork.objects.filter(target_id__in=delivery_ids).exists()


def test_an_after_commit_fanout_enqueue_stops_at_the_first_broker_error(
    org, django_capture_on_commit_callbacks
):
    """The after-commit fast path for a 500-subscription fan-out must not
    spend 500 connect timeouts failing one by one during an outage: the
    items already have timers, so the first broker error hands the rest
    to them."""
    import uuid

    from kombu.exceptions import OperationalError

    from workeasy360.integration.scheduling.services import enqueue_after_commit

    class Unreachable:
        name = "integration.test.unreachable"
        calls: list = []

        def apply_async(self, args):
            self.calls.append(args)
            raise OperationalError("[Errno 111] Connection refused")

    task = Unreachable()
    with django_capture_on_commit_callbacks(execute=True):
        enqueue_after_commit(task, [uuid.uuid4() for _ in range(5)], ctx=org["ctx"])
    assert len(task.calls) == 1


def test_a_cache_outage_degrades_limits_not_correctness(
    org, example_product, product_event, product_client, signing_key, monkeypatch
):
    """Task 69: rate limiting falls back to a per-process window; ingestion
    and authorization keep working without Redis."""
    from django.core.cache import cache

    def down(*_a, **_k):
        raise ConnectionError("redis unreachable")

    for name in ("get", "set", "add", "incr", "delete"):
        monkeypatch.setattr(cache, name, down)
    publisher = product_client(example_product)
    status, body = publisher.publish_event(
        organization_id=org["organization_id"], event=product_event()
    )
    assert (status, body["status"]) == (202, "accepted")


def test_a_database_failure_while_recording_is_recovered_as_an_abandoned_attempt(
    org, example_product, product_event, webhook_receiver, monkeypatch
):
    """Task 67: the send happened, the record failed. The lease expires and
    the delivery is attempted again - a duplicate the receiver deduplicates,
    never a lost event."""
    from django.db import OperationalError

    from workeasy360.integration.webhooks import pipeline
    from workeasy360.integration.webhooks.models import Delivery, DeliveryAttempt

    _connection(org, f"{webhook_receiver.url}/hooks")
    (delivery_id,) = _publish(org, example_product, product_event)

    def failing_record(*_a, **_k):
        raise OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(pipeline, "_record", failing_record)
    with pytest.raises(OperationalError):
        _deliver(org, delivery_id)
    monkeypatch.undo()
    assert len(webhook_receiver.received) == 1

    with _scoped(org["ctx"]):
        Delivery.objects.filter(id=delivery_id).update(
            lease_expires_at=timezone.now() - timezone.timedelta(seconds=1)
        )
    assert _deliver(org, delivery_id) == "succeeded"
    assert len(webhook_receiver.received) == 2
    event_ids = {r.json["event"]["id"] for r in webhook_receiver.received}
    assert len(event_ids) == 1  # the receiver sees one event, twice
    with _scoped(org["ctx"]):
        outcomes = list(
            DeliveryAttempt.objects.filter(delivery_id=delivery_id)
            .order_by("attempt_number")
            .values_list("outcome", flat=True)
        )
    assert outcomes == ["abandoned", "succeeded"]


def test_a_loop_through_an_external_system_stops_at_the_depth_limit(
    org, example_product, product_event, settings
):
    """Task 46: each event names the one that caused it; depth is derived
    from the platform's own records and automation stops past the limit."""
    from workeasy360.integration.automation import services as automation
    from workeasy360.integration.automation.models import AutomationExecution

    settings.AUTOMATION_MAX_CHAIN_DEPTH = 2
    created = _connection(org, _refusing_url())
    with _scoped(org["ctx"]):
        rule = automation.create_rule(
            org["ctx"],
            name="Echo",
            definition={
                "trigger": {"kind": "event", "event": EVENT_REF},
                "actions": [
                    {
                        "type": "connector.invoke",
                        "config": {"connection_id": str(created.connection.id)},
                    }
                ],
            },
        )
        automation.transition_rule(
            org["ctx"], rule["id"], to_status="active", expected_revision=rule["revision"]
        )

    import uuid

    parent = None
    for _depth in range(5):
        event_id = str(uuid.uuid4())
        _publish(
            org, example_product, product_event, event_id=event_id, causation_id=parent
        )
        parent = event_id
    with _scoped(org["ctx"]):
        rows = list(
            AutomationExecution.objects.order_by("chain_depth").values_list(
                "chain_depth", "status", "failure_category"
            )
        )
    assert [r[0] for r in rows] == [0, 1, 2, 3, 4]
    assert [r[1] for r in rows[:3]] == ["pending"] * 3
    assert all(r[1:] == ("dead", "chain_depth_exceeded") for r in rows[3:])

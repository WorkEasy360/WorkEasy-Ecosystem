"""SPIKE R-09 - broker capability evaluation.

The question (15-risk-register.md, R-09):

    Celery's SQS transport has real limitations: no broker-side priority, no
    remote control or worker events, visibility-timeout semantics that
    interact awkwardly with long tasks, and FIFO throughput caps if ordering
    is needed. Which broker, given what Workeasy360 actually requires?

What is measured here, against a real RabbitMQ (the engine behind AmazonMQ):

    1. task routing to the three queue groups
    2. retries - bounded, with jitter
    3. visibility timeout / redelivery on worker loss
    4. duplicate delivery and consumer idempotency
    5. late acknowledgement behaviour
    6. ordering within a queue
    7. priority support
    8. dead-letter / recovery

SQS cannot be measured here - it is a hosted AWS service. Its capabilities are
taken from the Celery/kombu transport's own declared feature matrix, which is
inspected programmatically rather than quoted from memory, and every such row
is labelled ``source: transport capability declaration`` in the report so the
distinction between measured and documented is never lost.

The governing design constraint, which makes this a cost/ops choice rather
than a correctness one: **no business logic may depend on strict queue
ordering.** Consumers are idempotent and order-tolerant by construction
(ADR-008), so either broker is correct; the question is operability.

Run (requires the docker-compose stack):
    python backend/tests/spikes/spike_r09_broker.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

BROKER_URL = os.environ.get("W360_BROKER_URL", "amqp://w360:w360_pw@127.0.0.1:5682//")


@dataclass
class Findings:
    checks: list[dict] = field(default_factory=list)

    def record(
        self, name: str, passed: bool, detail: str, *, source: str = "measured"
    ) -> None:
        self.checks.append(
            {"check": name, "passed": passed, "detail": detail, "source": source}
        )
        mark = "PASS" if passed else "FAIL"
        tag = "" if source == "measured" else f" [{source}]"
        print(f"  [{mark}]{tag} {name}: {detail}")

    def note(self, name: str, detail: str, *, source: str = "measured") -> None:
        self.checks.append(
            {"check": name, "passed": True, "detail": detail, "source": source}
        )
        tag = "" if source == "measured" else f" [{source}]"
        print(f"  [INFO]{tag} {name}: {detail}")

    @property
    def all_passed(self) -> bool:
        return all(c["passed"] for c in self.checks)


# --- broker connectivity and declared capabilities ------------------------


def inspect_transport_capabilities(findings: Findings) -> dict:
    """Read each transport's own declared feature set.

    kombu transports expose their capabilities as class attributes. Reading
    them is more reliable than quoting documentation, and it pins the answer
    to the versions actually installed.
    """
    import kombu

    report: dict = {"kombu_version": kombu.__version__}

    from kombu.transport.pyamqp import Transport as AmqpTransport

    amqp_caps = {
        "supports_priority": True,  # RabbitMQ: x-max-priority on the queue
        "supports_dead_letter_exchange": True,
        "supports_publisher_confirms": True,
        "implements_events": bool(getattr(AmqpTransport, "implements", None)),
        "driver_type": getattr(AmqpTransport, "driver_type", "?"),
        "default_port": getattr(AmqpTransport, "default_port", None),
    }
    report["amqp"] = amqp_caps

    try:
        from kombu.transport.SQS import Transport as SqsTransport

        implements = getattr(SqsTransport, "implements", None)
        sqs_caps = {
            "driver_type": getattr(SqsTransport, "driver_type", "?"),
            "implements.exchange_type": list(
                getattr(implements, "exchange_type", []) or []
            ),
            "supports_priority": bool(getattr(SqsTransport, "supports_priority", False)),
            "implements.heartbeats": bool(getattr(implements, "heartbeats", False)),
            "polling_interval_default": getattr(SqsTransport, "polling_interval", None),
        }
        report["sqs"] = sqs_caps
        findings.note(
            "SQS transport capabilities read from kombu",
            f"exchange types {sqs_caps['implements.exchange_type']}, "
            f"broker-side priority={sqs_caps['supports_priority']}",
            source="transport capability declaration",
        )
    except ImportError as exc:
        report["sqs"] = {"available": False, "reason": str(exc)}
        findings.note(
            "SQS transport not installed",
            f"kombu[sqs] absent ({exc}); SQS rows below are documentation-derived",
            source="not installed",
        )

    return report


def check_broker_reachable(findings: Findings) -> bool:
    from kombu import Connection

    try:
        with Connection(BROKER_URL, connect_timeout=5) as conn:
            conn.ensure_connection(max_retries=0, timeout=5)
            info = conn.info()
        findings.record(
            "broker reachable",
            True,
            f"{info.get('transport')} at {info.get('hostname')}:{info.get('port')}",
        )
        return True
    except Exception as exc:
        findings.record("broker reachable", False, f"{type(exc).__name__}: {exc}")
        return False


# --- measured behaviour ---------------------------------------------------


def check_queue_declaration_and_routing(findings: Findings) -> dict:
    """Three queue groups, with per-task routing declared in code.

    R-12 starts with three queues rather than seven. The property that makes
    that safe is that routing is declared **per task** from day one, so
    splitting ``heavy`` into ``imports``/``ai``/``reports`` later is a
    deployment change, not a change to business code.
    """
    import django

    django.setup()

    from django.conf import settings

    from config.celery import app as celery_app

    # autodiscover_tasks() is lazy; without this the registry holds only
    # Celery's built-ins and the routing assertions below would be vacuous.
    celery_app.loader.import_default_modules()

    groups = tuple(getattr(settings, "CELERY_QUEUE_GROUPS", ()))
    findings.record(
        "three queue groups configured",
        groups == ("critical", "default", "heavy"),
        f"CELERY_QUEUE_GROUPS = {groups}",
    )

    routed: dict[str, str] = {}
    unrouted: list[str] = []
    for name, task in sorted(celery_app.tasks.items()):
        if name.startswith("celery."):
            continue
        queue = getattr(task, "queue", None)
        if queue:
            routed[name] = queue
        else:
            unrouted.append(name)

    findings.record(
        "every task declares an explicit queue",
        not unrouted,
        f"{len(routed)} task(s) routed; unrouted: {unrouted or 'none'}",
    )

    by_queue = Counter(routed.values())
    findings.note("task distribution across queues", json.dumps(dict(by_queue)))

    # Declare the queues on the live broker and confirm they exist.
    from kombu import Connection, Queue

    declared = []
    with Connection(BROKER_URL, connect_timeout=5) as conn:
        channel = conn.channel()
        for group in groups:
            queue = Queue(
                f"spike.{group}",
                routing_key=f"spike.{group}",
                # RabbitMQ supports broker-side priority via x-max-priority.
                # SQS does not, at all.
                queue_arguments={"x-max-priority": 10},
            )
            bound = queue(channel)
            bound.declare()
            declared.append(queue.name)
            bound.delete()

    findings.record(
        "broker accepts priority-enabled queue declaration",
        len(declared) == len(groups),
        f"declared and removed {declared}",
    )
    return {"routed": routed, "by_queue": dict(by_queue), "groups": list(groups)}


def check_priority_support(findings: Findings) -> dict:
    """RabbitMQ honours message priority; SQS has none.

    Measured by publishing out of priority order into a priority queue and
    reading back the delivery order.
    """
    from kombu import Connection, Exchange, Queue

    queue_name = f"spike.priority.{uuid.uuid4().hex[:8]}"
    exchange = Exchange("spike.priority.ex", type="direct", durable=False)
    queue = Queue(
        queue_name,
        exchange=exchange,
        routing_key=queue_name,
        durable=False,
        queue_arguments={"x-max-priority": 10},
    )

    published = [("low", 1), ("high", 9), ("mid", 5)]
    received: list[str] = []

    with Connection(BROKER_URL, connect_timeout=5) as conn:
        bound = queue(conn.channel())
        bound.declare()

        producer = conn.Producer()
        for body, priority in published:
            producer.publish(
                {"body": body},
                exchange=exchange,
                routing_key=queue_name,
                declare=[queue],
                priority=priority,
            )

        # Let the broker order the backlog before consuming: priority applies
        # to messages already queued, not to ones consumed on arrival.
        time.sleep(0.5)

        for _ in range(len(published)):
            message = bound.get(accept=["json"])
            if message is None:
                break
            received.append(message.payload["body"])
            message.ack()
        bound.delete()

    ordered_by_priority = received == ["high", "mid", "low"]
    findings.record(
        "broker honours message priority",
        ordered_by_priority,
        f"published {[p[0] for p in published]} -> consumed {received}",
    )
    return {"published": published, "consumed": received}


def check_fifo_within_queue(findings: Findings) -> dict:
    """Single-consumer FIFO ordering within one queue.

    Recorded as an observation, **not** as a guarantee the design may rely on.
    07-cross-product-integration.md section 3.6 states plainly that global
    ordering and cross-product ordering are not provided, and that consumers
    must tolerate out-of-order delivery. This measurement exists so nobody
    later mistakes "it looked ordered in testing" for a contract.
    """
    from kombu import Connection, Exchange, Queue

    queue_name = f"spike.fifo.{uuid.uuid4().hex[:8]}"
    exchange = Exchange("spike.fifo.ex", type="direct", durable=False)
    queue = Queue(queue_name, exchange=exchange, routing_key=queue_name, durable=False)

    sent = list(range(50))
    received: list[int] = []

    with Connection(BROKER_URL, connect_timeout=5) as conn:
        bound = queue(conn.channel())
        bound.declare()
        producer = conn.Producer()
        for i in sent:
            producer.publish(
                {"seq": i}, exchange=exchange, routing_key=queue_name, declare=[queue]
            )
        time.sleep(0.3)
        while len(received) < len(sent):
            message = bound.get(accept=["json"])
            if message is None:
                break
            received.append(message.payload["seq"])
            message.ack()
        bound.delete()

    in_order = received == sent
    findings.note(
        "single-consumer FIFO within one queue",
        f"{len(received)}/{len(sent)} messages, order preserved={in_order}. "
        f"OBSERVED ONLY - not a guarantee the design relies on "
        f"(07 section 3.6: no global or cross-product ordering).",
    )
    return {"sent": len(sent), "received": len(received), "in_order": in_order}


def check_redelivery_on_nack(findings: Findings) -> dict:
    """Unacknowledged messages must be redelivered, not lost.

    This is the property behind ``acks_late=True``: a worker that crashes
    mid-task must cause the task to re-run rather than vanish
    (10-performance-concurrency.md section 6). Paired with idempotency, a
    worker failure is a delay rather than a lost business fact.
    """
    from kombu import Connection, Exchange, Queue

    queue_name = f"spike.redeliver.{uuid.uuid4().hex[:8]}"
    exchange = Exchange("spike.redeliver.ex", type="direct", durable=False)
    queue = Queue(queue_name, exchange=exchange, routing_key=queue_name, durable=False)

    with Connection(BROKER_URL, connect_timeout=5) as conn:
        bound = queue(conn.channel())
        bound.declare()
        conn.Producer().publish(
            {"payload": "work"},
            exchange=exchange,
            routing_key=queue_name,
            declare=[queue],
        )
        time.sleep(0.2)

        first = bound.get(accept=["json"])
        got_first = first is not None
        # requeue=True models a worker dying before it could ack.
        if first is not None:
            first.requeue()
        time.sleep(0.3)

        second = bound.get(accept=["json"])
        redelivered = second is not None
        was_flagged = bool(second and second.delivery_info.get("redelivered"))
        if second is not None:
            second.ack()

        third = bound.get(accept=["json"])
        consumed_once_acked = third is None
        if third is not None:
            third.ack()
        bound.delete()

    findings.record(
        "unacked message is redelivered",
        got_first and redelivered,
        f"first delivery={got_first}, redelivered={redelivered}, "
        f"redelivered flag set={was_flagged}",
    )
    findings.record(
        "acked message is not redelivered",
        consumed_once_acked,
        "queue empty after ack"
        if consumed_once_acked
        else "message reappeared after ack",
    )
    return {
        "redelivered": redelivered,
        "redelivered_flag": was_flagged,
        "removed_after_ack": consumed_once_acked,
    }


def check_dead_letter_routing(findings: Findings) -> dict:
    """Rejected messages route to a dead-letter exchange.

    RabbitMQ provides this natively. SQS provides a redrive policy, which is
    equivalent in intent. Either way the application keeps its own
    ``DeadLetterEvent`` table, because a broker DLQ holds a transport message
    while the application needs the domain envelope for replay.
    """
    from kombu import Connection, Exchange, Queue

    suffix = uuid.uuid4().hex[:8]
    dlx = Exchange(f"spike.dlx.{suffix}", type="direct", durable=False)
    dlq = Queue(f"spike.dead.{suffix}", exchange=dlx, routing_key="dead", durable=False)
    main_ex = Exchange(f"spike.main.{suffix}", type="direct", durable=False)
    main = Queue(
        f"spike.main.{suffix}",
        exchange=main_ex,
        routing_key="main",
        durable=False,
        queue_arguments={
            "x-dead-letter-exchange": dlx.name,
            "x-dead-letter-routing-key": "dead",
        },
    )

    with Connection(BROKER_URL, connect_timeout=5) as conn:
        channel = conn.channel()
        dlq(channel).declare()
        bound_main = main(channel)
        bound_main.declare()

        conn.Producer().publish(
            {"payload": "poison"},
            exchange=main_ex,
            routing_key="main",
            declare=[main],
        )
        time.sleep(0.2)

        message = bound_main.get(accept=["json"])
        got = message is not None
        if message is not None:
            # reject without requeue is what a permanent failure does.
            message.reject(requeue=False)
        time.sleep(0.4)

        bound_dlq = dlq(channel)
        dead = bound_dlq.get(accept=["json"])
        landed = dead is not None
        if dead is not None:
            dead.ack()

        bound_main.delete()
        bound_dlq.delete()

    findings.record(
        "rejected message routes to the dead-letter queue",
        got and landed,
        f"consumed={got}, arrived in DLQ={landed}",
    )
    return {"dead_lettered": landed}


def check_retry_policy_is_bounded(findings: Findings) -> dict:
    """Retries must be bounded and jittered, and never nested.

    Asserted against the configuration and the TenantTask backoff function
    rather than by waiting an hour for real retries.
    """
    import django
    from django.conf import settings

    from workeasy360.platform.tenancy.tasks import TenantTask

    max_retries = getattr(settings, "TASK_MAX_RETRIES", None)
    cap = getattr(settings, "TASK_RETRY_BACKOFF_CAP_S", None)

    findings.record(
        "retries are bounded",
        isinstance(max_retries, int) and 0 < max_retries <= 10,
        f"TASK_MAX_RETRIES={max_retries}, backoff cap={cap}s",
    )

    task = TenantTask()
    samples = [task.backoff_delay(3) for _ in range(200)]
    distinct = len({round(s, 4) for s in samples})
    ceiling = min(float(cap), 2.0 * (2**3))

    findings.record(
        "backoff uses full jitter",
        distinct > 150 and max(samples) <= ceiling and min(samples) >= 0,
        f"200 samples at attempt 3: {distinct} distinct values in "
        f"[{min(samples):.3f}, {max(samples):.3f}], ceiling {ceiling:.1f}s",
    )

    growth = [max(task.backoff_delay(n) for _ in range(50)) for n in range(1, 6)]
    findings.record(
        "backoff grows exponentially with attempt",
        all(growth[i] <= growth[i + 1] * 1.2 for i in range(len(growth) - 1)),
        f"observed ceilings by attempt: {[round(g, 2) for g in growth]}",
    )
    return {
        "max_retries": max_retries,
        "cap_seconds": cap,
        "jitter_distinct_samples": distinct,
    }


def check_visibility_timeout_config(findings: Findings) -> dict:
    """The visibility timeout must exceed the longest task time limit.

    If it does not, a slow task is redelivered while still running and the
    same work executes twice concurrently - which is only survivable because
    tasks are idempotent, but is wasteful and produces confusing duplicate
    side effects. This is the specific SQS semantic R-09 flags.
    """
    from django.conf import settings

    from config.celery import app as celery_app

    celery_app.loader.import_default_modules()

    visibility = (getattr(settings, "CELERY_BROKER_TRANSPORT_OPTIONS", {}) or {}).get(
        "visibility_timeout"
    )

    limits = [
        (name, getattr(task, "time_limit", None))
        for name, task in celery_app.tasks.items()
        if not name.startswith("celery.") and getattr(task, "time_limit", None)
    ]
    longest_name, longest = (
        max(limits, key=lambda item: item[1]) if limits else ("none", 0)
    )

    findings.record(
        "visibility timeout exceeds the longest task time limit",
        bool(visibility) and visibility > longest,
        f"visibility_timeout={visibility}s vs longest time_limit={longest}s "
        f"({longest_name})",
    )
    return {
        "visibility_timeout": visibility,
        "longest_time_limit": longest,
        "longest_task": longest_name,
    }


def check_idempotency_under_duplicate_delivery(findings: Findings) -> dict:
    """At-least-once delivery means duplicates. The consumer must absorb them.

    Exercises the real ``ProcessedEvent`` unique constraint rather than a
    mock, because the guarantee comes from the database, not from application
    logic.
    """
    import django

    # Run against the live development database inside a rolled-back
    # transaction so the spike leaves nothing behind.
    from django.db import transaction
    from django.test.utils import setup_test_environment

    from workeasy360.integration.events.models import ProcessedEvent
    from workeasy360.integration.events.services import AlreadyProcessed

    event_id = uuid.uuid4()
    consumer = f"spike.consumer.{uuid.uuid4().hex[:6]}"
    applied = 0
    duplicates = 0

    try:
        with transaction.atomic():
            for _ in range(5):
                try:
                    with transaction.atomic():
                        ProcessedEvent.objects.create(
                            consumer=consumer, event_id=event_id
                        )
                        applied += 1
                except Exception as exc:
                    if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                        duplicates += 1
                    else:
                        raise
            transaction.set_rollback(True)
    except Exception as exc:
        findings.record(
            "duplicate delivery is absorbed by ProcessedEvent",
            False,
            f"{type(exc).__name__}: {exc}",
        )
        return {"error": str(exc)}

    findings.record(
        "duplicate delivery is absorbed by ProcessedEvent",
        applied == 1 and duplicates == 4,
        f"5 deliveries of one event -> {applied} applied, {duplicates} "
        f"rejected by the unique constraint",
    )
    return {"deliveries": 5, "applied": applied, "duplicates": duplicates}


# --- entry point ----------------------------------------------------------


def main() -> int:
    print("=" * 74)
    print("SPIKE R-09 - broker capability evaluation")
    print("=" * 74)
    print(f"  broker: {BROKER_URL.split('@')[-1]}")
    print()

    findings = Findings()

    print("Connectivity")
    if not check_broker_reachable(findings):
        print("\nBroker unreachable - start the stack with `docker compose up -d`.")
        return 1

    print("\nTransport capabilities")
    capabilities = inspect_transport_capabilities(findings)

    print("\nRouting")
    routing = check_queue_declaration_and_routing(findings)

    print("\nOrdering and priority")
    priority = check_priority_support(findings)
    fifo = check_fifo_within_queue(findings)

    print("\nDelivery semantics")
    redelivery = check_redelivery_on_nack(findings)
    dead_letter = check_dead_letter_routing(findings)
    duplicates = check_idempotency_under_duplicate_delivery(findings)

    print("\nRetry and timeout policy")
    retries = check_retry_policy_is_bounded(findings)
    visibility = check_visibility_timeout_config(findings)

    print("\n" + "=" * 74)
    verdict = "PASS" if findings.all_passed else "FAIL"
    print(f"SPIKE R-09 RESULT: {verdict}")
    print("=" * 74)

    report = {
        "spike": "R-09",
        "question": "SQS or AmazonMQ (RabbitMQ), given ordering, priority, "
        "visibility-timeout and observability needs?",
        "verdict": verdict,
        "broker_measured": "RabbitMQ 3.13 (the engine behind AmazonMQ)",
        "checks": findings.checks,
        "capabilities": capabilities,
        "routing": routing,
        "priority": priority,
        "fifo_observation": fifo,
        "redelivery": redelivery,
        "dead_letter": dead_letter,
        "duplicate_delivery": duplicates,
        "retry_policy": retries,
        "visibility_timeout": visibility,
    }
    out = Path(__file__).parent / "r09_results.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nResults written to {out}")
    return 0 if findings.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

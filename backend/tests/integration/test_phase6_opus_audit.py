"""Regressions for the defects the Phase 6 adversarial audit found.

Each test failed against the code as it stood before the audit's fix:

- **Bulkhead per organization.** Only a per-connection cap existed, so one
  organization with many slow connections could occupy every delivery
  worker - which every organization shares.
- **Throttled deliveries re-polled the shared timer every 2-6 s.** A
  backlog on one slow endpoint became a constant stream of claims through
  the one due-work sweeper that carries every organization's retries and
  schedules. They are now parked, and a finishing attempt pulls the next
  one forward.
- **A due-work timer's organization was mutable** (D-017). Re-scheduling a
  target overwrote the organization the sweeper dispatches it into, and
  completing a timer ignored the organization in scope.
- **Namespace grammar accepted a trailing newline** (``$`` matches before
  one).

The overall egress deadline is covered in ``test_egress_client.py``; the
condition-evaluation overflow in ``automation/tests/test_conditions.py``.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from django.db import connections
from django.utils import timezone

TIMER_KIND = "webhooks.delivery"


def _scoped(ctx):
    from contextlib import ExitStack

    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    stack = ExitStack()
    stack.enter_context(tenant_context(ctx))
    stack.enter_context(tenant_db_scope(ctx))
    return stack


def _active_connection(org, url, name):
    from workeasy360.integration.connectors import services as connectors

    with _scoped(org["ctx"]):
        created = connectors.create_connection(
            org["ctx"], connector_key="webhook", display_name=name, config={"url": url}
        )
        connectors.transition_connection(
            org["ctx"], created.connection.id, to_status="active"
        )
    return created.connection.id


def _pending_delivery(org, connection_id):
    from workeasy360.integration.events.services import record_platform_event
    from workeasy360.integration.webhooks.models import Delivery

    with _scoped(org["ctx"]):
        event, _ = record_platform_event(
            "integration.webhook.pinged",
            1,
            {"connection_id": str(connection_id), "message": "audit"},
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


def _timer(target_id):
    from workeasy360.integration.scheduling.models import DueWork

    return DueWork.objects.filter(kind=TIMER_KIND, target_id=target_id).first()


class _GaugedReceiver:
    """Counts requests and the most that were ever in progress at once."""

    def __init__(self, delay):
        self.delay = delay
        self.active = self.peak = self.count = 0
        self._lock = threading.Lock()

    def start(self):
        gauge = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with gauge._lock:
                    gauge.active += 1
                    gauge.count += 1
                    gauge.peak = max(gauge.peak, gauge.active)
                time.sleep(gauge.delay)
                with gauge._lock:
                    gauge.active -= 1
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()


# --- the organization bulkhead ------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
def test_one_organization_cannot_hold_more_than_its_share_of_delivery_workers(
    integration_org, local_egress, settings
):
    """Eight workers claim eight deliveries on eight different connections of
    one organization at once. Each connection is far below its own cap; the
    organization's cap of two must still hold - and the parked six must
    still all be delivered, pulled forward as slots free."""
    settings.WEBHOOK_MAX_INFLIGHT_PER_ORGANIZATION = 2
    workers = 8
    org = integration_org()
    receiver = _GaugedReceiver(delay=1.0)
    url = receiver.start()
    try:
        deliveries = [
            _pending_delivery(org, _active_connection(org, f"{url}/c{i}", f"C{i}"))
            for i in range(workers)
        ]
        barrier = threading.Barrier(workers)
        results, errors = [], []

        def worker(index):
            try:
                barrier.wait(timeout=30)
                results.append(_run_delivery(org, deliveries[index]))
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(worker, i) for i in range(workers)]):
                future.result()
    finally:
        receiver.stop()

    assert errors == []
    assert receiver.peak <= 2, f"{receiver.peak} requests in flight for one organization"
    assert "throttled" in results
    # Parked, not lost: finishing attempts pulled every one of them forward
    # (the test runs Celery eagerly, so a pulled delivery runs at once).
    assert receiver.count == workers


# --- parked, then pulled forward ------------------------------------------------------


@pytest.mark.django_db
def test_a_throttled_delivery_is_parked_then_pulled_forward_when_a_slot_frees(
    integration_org, local_egress, webhook_receiver, settings
):
    from workeasy360.integration.webhooks.models import Delivery

    settings.WEBHOOK_MAX_INFLIGHT_PER_ORGANIZATION = 1
    org = integration_org()
    first = _pending_delivery(
        org, _active_connection(org, f"{webhook_receiver.url}/a", "A")
    )
    second = _pending_delivery(
        org, _active_connection(org, f"{webhook_receiver.url}/b", "B")
    )

    # A worker is mid-send on ``first``: the organization's one slot is taken.
    with _scoped(org["ctx"]):
        Delivery.objects.filter(id=first).update(
            status="in_progress",
            attempts=1,
            lease_expires_at=timezone.now() + timezone.timedelta(seconds=60),
        )
    assert _run_delivery(org, second) == "throttled"
    # Parked for minutes - not re-polled through the shared sweeper every
    # few seconds.
    parked = _timer(second).due_at - timezone.now()
    assert parked.total_seconds() >= settings.WEBHOOK_THROTTLE_HOLD_S - 5

    # That worker finishes (here: the attempt is re-run to completion).
    with _scoped(org["ctx"]):
        Delivery.objects.filter(id=first).update(
            status="pending", attempts=0, lease_expires_at=None
        )
    assert _run_delivery(org, first) == "succeeded"
    # The freed slot went straight to the parked delivery.
    assert _timer(second).due_at <= timezone.now() + timezone.timedelta(seconds=1)


# --- D-017: a timer's organization is fixed ------------------------------------------


@pytest.mark.django_db
def test_a_timer_keeps_its_organization_and_only_its_organization_completes_it(
    integration_org,
):
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context

    a, b = integration_org(), integration_org()
    target = uuid.uuid4()
    due = timezone.now() + timezone.timedelta(minutes=5)
    scheduling.schedule(
        TIMER_KIND, organization_id=a["org"].id, target_id=target, due_at=due
    )

    # The same target named under another organization: refused, not moved.
    scheduling.schedule(
        TIMER_KIND,
        organization_id=b["org"].id,
        target_id=target,
        due_at=timezone.now(),
    )
    row = _timer(target)
    assert (row.organization_id, row.due_at) == (a["org"].id, due)

    # Its own organization still moves it, and resets its claim count.
    later = due + timezone.timedelta(minutes=5)
    scheduling.schedule(
        TIMER_KIND, organization_id=a["org"].id, target_id=target, due_at=later
    )
    assert (_timer(target).due_at, _timer(target).claims) == (later, 0)

    # A consumer running in the wrong organization cancels nothing.
    with tenant_context(routing_context(b["org"].id)):
        scheduling.complete(TIMER_KIND, target)
    assert _timer(target) is not None
    with tenant_context(routing_context(a["org"].id)):
        scheduling.complete(TIMER_KIND, target)
    assert _timer(target) is None


# --- namespace grammar ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "event_type", "owned"),
    [
        ("crm", "product.crm.deal.created", True),
        ("crm", "product.crm.deal.created\n", False),
        ("crm", "product.crm2.deal.created", False),
        ("crm", "product.crmevil.deal.created", False),
        ("crm", "PRODUCT.crm.deal.created", False),
        ("crm", "product.CRM.deal.created", False),
        ("crm", "product.books.deal.created", False),
        ("crm", "platform.identity.user.created", False),
        ("crm", "integration.webhook.pinged", False),
        ("crm_1", "product.crm_1.deal.created", True),
        ("crm", "product.crm_1.deal.created", False),
        ("crm-1", "product.crm-1.deal.created", False),
    ],
)
def test_a_product_owns_exactly_its_own_namespace(code, event_type, owned):
    from workeasy360.contracts.events.namespaces import product_owns

    assert product_owns(code, event_type) is owned


@pytest.mark.parametrize(
    "value",
    [
        "product.crm.*\n",
        "product.crm.deal.created.v1\n",
        "integration.webhook.pinged.v1\n",
    ],
)
def test_a_filter_with_a_trailing_newline_is_not_a_filter(value):
    from workeasy360.contracts.events.namespaces import parse_event_filter

    assert parse_event_filter(value) is None

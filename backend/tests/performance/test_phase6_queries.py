"""Phase 6 performance: query counts that do not grow, plans that use the
indexes (Tasks 73-75, 100).

Query counts are asserted, not just printed: the fan-out must be the same
handful of queries at 0, 10, 100 and 500 matching subscriptions or rules,
because a per-subscription query is exactly the N+1 Task 73 forbids. Run with
``-s`` to see the timings recorded in docs/phase-6/08-performance.md; they
are local measurements, not production throughput.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

pytestmark = [pytest.mark.django_db]

EVENT_REF = "product.example.record.created.v1"
SIZES = (0, 10, 100, 500)


def _scope(org):
    from contextlib import ExitStack

    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    stack = ExitStack()
    stack.enter_context(tenant_context(routing_context(org["org"].id)))
    stack.enter_context(organization_db_scope(org["org"].id))
    return stack


def _ingest(org, example_product, product_event):
    from workeasy360.integration.events.models import RoutedEvent
    from workeasy360.integration.routing.ingestion import ingest_product_event
    from workeasy360.platform.ecosystem.clientauth import AuthenticatedClient
    from workeasy360.platform.ecosystem.models import ProductIntegration

    integration = ProductIntegration.objects.get(client_id=example_product["client_id"])
    event = product_event()
    ingest_product_event(
        AuthenticatedClient(integration=integration, credential=None),
        organization_id=org["organization_id"],
        raw_event=json.dumps(event),
    )
    with _scope(org):
        return RoutedEvent.objects.get(event_id=event["event_id"]).id


def _seed_connections(org, count):
    from workeasy360.integration.connectors.models import Connection
    from workeasy360.integration.webhooks.models import EventSubscription

    with _scope(org):
        rows = Connection.objects.bulk_create(
            [
                Connection(
                    organization_id=org["org"].id,
                    connector_key="webhook",
                    display_name=f"c{i}",
                    status="active",
                    config={"url": f"https://hooks{i}.example.com/in"},
                )
                for i in range(count)
            ]
        )
        EventSubscription.objects.bulk_create(
            [
                EventSubscription(
                    organization_id=org["org"].id,
                    connection_id=c.id,
                    event_filter=EVENT_REF,
                )
                for c in rows
            ]
        )


def _seed_rules(org, count, connection_id):
    from workeasy360.integration.automation.models import (
        AutomationRule,
        AutomationRuleVersion,
    )

    definition = {
        "trigger": {"kind": "event", "event": EVENT_REF},
        "conditions": {"match": "all", "rules": []},
        "actions": [
            {
                "id": "a1",
                "type": "connector.invoke",
                "version": 1,
                "config": {
                    "connection_id": str(connection_id),
                    "capability": "deliver_event",
                },
            }
        ],
    }
    with _scope(org):
        rules = AutomationRule.objects.bulk_create(
            [
                AutomationRule(
                    organization_id=org["org"].id,
                    name=f"r{i}",
                    status="active",
                    trigger_kind="event",
                    trigger_filter=EVENT_REF,
                )
                for i in range(count)
            ]
        )
        versions = AutomationRuleVersion.objects.bulk_create(
            [
                AutomationRuleVersion(
                    organization_id=org["org"].id,
                    rule_id=r.id,
                    version=1,
                    definition=definition,
                )
                for r in rules
            ]
        )
        for rule, version in zip(rules, versions):
            rule.current_version_id = version.id
        AutomationRule.objects.bulk_update(rules, ["current_version"])


@pytest.mark.parametrize("size", SIZES)
def test_fanout_to_n_subscriptions_is_a_constant_number_of_queries(
    size, integration_org, example_product, product_event
):
    from workeasy360.integration.routing import fanout

    org = integration_org()
    _seed_connections(org, size)
    event_pk = _ingest(org, example_product, product_event)

    with _scope(org), CaptureQueriesContext(connection) as captured:
        started = time.perf_counter()
        routed = fanout.route_event(event_pk)
        elapsed = time.perf_counter() - started

    assert len(routed.deliveries) == size
    print(
        f"\nfan-out to {size} subscriptions: {len(captured)} queries, {elapsed * 1000:.1f} ms"
    )
    # Fixed whatever the size: lock event, match subscriptions, filter active
    # connections, backlog count, bulk insert, read-back, timer upsert, match
    # rules, mark routed, drop timer - plus savepoints. Never one per row.
    assert len(captured) <= 14


@pytest.mark.parametrize("size", SIZES)
def test_n_active_rules_are_matched_with_a_constant_number_of_queries(
    size, integration_org, example_product, product_event
):
    from workeasy360.integration.routing import fanout

    org = integration_org()
    _seed_connections(org, 1)
    from workeasy360.integration.connectors.models import Connection

    with _scope(org):
        connection_id = Connection.objects.values_list("id", flat=True).get()
    _seed_rules(org, size, connection_id)
    event_pk = _ingest(org, example_product, product_event)

    with _scope(org), CaptureQueriesContext(connection) as captured:
        started = time.perf_counter()
        routed = fanout.route_event(event_pk)
        elapsed = time.perf_counter() - started

    assert len(routed.executions) == size
    print(
        f"\nrouting with {size} matching rules: {len(captured)} queries, {elapsed * 1000:.1f} ms"
    )
    assert len(captured) <= 17


def test_ingestion_is_a_bounded_number_of_queries(
    integration_org, example_product, product_event
):
    from workeasy360.integration.routing.ingestion import ingest_product_event
    from workeasy360.platform.ecosystem.clientauth import AuthenticatedClient
    from workeasy360.platform.ecosystem.models import ProductIntegration

    org = integration_org()
    integration = ProductIntegration.objects.get(client_id=example_product["client_id"])
    client = AuthenticatedClient(integration=integration, credential=None)
    timings = []
    for _ in range(20):
        body = json.dumps(product_event())
        with CaptureQueriesContext(connection) as captured:
            started = time.perf_counter()
            ingest_product_event(
                client, organization_id=org["organization_id"], raw_event=body
            )
            timings.append(time.perf_counter() - started)
    timings.sort()
    print(
        f"\ningest (after client auth): {len(captured)} queries, "
        f"p50 {timings[10] * 1000:.1f} ms, p95 {timings[18] * 1000:.1f} ms"
    )
    assert len(captured) <= 16


@pytest.mark.parametrize("listing", ["connections", "deliveries", "automations"])
def test_listing_does_not_grow_with_the_page(
    listing,
    integration_org,
    example_product,
    product_event,
    client,
    signed_in,
    local_egress,
):
    """The three Settings lists (Task 85): the same number of queries for a
    page of 5 as for a page of 50, over 60 rows."""
    from workeasy360.integration.connectors.models import Connection
    from workeasy360.integration.routing import fanout

    org = integration_org()
    if listing == "connections":
        _seed_connections(org, 60)
    elif listing == "deliveries":
        _seed_connections(org, 12)
        for _ in range(5):
            with _scope(org):
                fanout.route_event(_ingest(org, example_product, product_event))
    else:
        _seed_connections(org, 1)
        with _scope(org):
            connection_id = Connection.objects.values_list("id", flat=True).get()
        _seed_rules(org, 60, connection_id)
    signed_in(org["user"], aal=2)
    url = f"/api/v1/orgs/{org['org'].slug}/{listing}"
    client.get(url)  # warm the per-session caches so both pages compare equal
    counts, timings = {}, {}
    for limit in (5, 50):
        with CaptureQueriesContext(connection) as captured:
            started = time.perf_counter()
            response = client.get(f"{url}?limit={limit}")
            timings[limit] = (time.perf_counter() - started) * 1000
        assert response.status_code == 200
        assert len(response.json()["results"]) == limit
        counts[limit] = len(captured)
    print(
        f"\n{listing} list: queries {counts}, "
        f"ms {{5: {timings[5]:.1f}, 50: {timings[50]:.1f}}}"
    )
    assert counts[5] == counts[50]


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
def test_the_hot_queries_are_index_served_on_a_padded_table(
    integration_org, example_product, product_event
):
    """Plans against realistic volume (Task 75): other tenants' rows pad
    every table, statistics are fresh, and the plan must use an index."""
    import os

    import psycopg

    from workeasy360.integration.scheduling.models import DueWork

    orgs = [integration_org() for _ in range(3)]
    # The tenant under test has a realistic 50 subscriptions; other tenants
    # pad the table so its share is small, as it would be in production.
    _seed_connections(orgs[0], 50)
    for org in orgs[1:]:
        _seed_connections(org, 3000)
    DueWork.objects.bulk_create(
        [
            DueWork(
                kind="webhooks.delivery",
                organization_id=orgs[0]["org"].id,
                target_id=uuid.uuid4(),
                due_at=timezone.now() + timezone.timedelta(hours=1 + i % 48),
            )
            for i in range(20_000)
        ],
        batch_size=5000,
    )
    with psycopg.connect(
        host=os.environ.get("W360_DB_DIRECT_HOST", "127.0.0.1"),
        port=os.environ.get("W360_DB_DIRECT_PORT", "5442"),
        dbname=connection.settings_dict["NAME"],
        user=os.environ.get("W360_DB_MIGRATOR_USER", "w360_migrator"),
        password=os.environ.get("W360_DB_MIGRATOR_PASSWORD", "w360_migrator_pw"),
        autocommit=True,
    ) as owner:
        for table in (
            "webhooks_subscription",
            "connectors_connection",
            "scheduling_due_work",
        ):
            owner.execute(f"ANALYZE {table}")

    from workeasy360.integration.webhooks.models import EventSubscription

    with _scope(orgs[0]):
        subscription_sql, params = (
            EventSubscription.objects.filter(
                event_filter__in=[EVENT_REF, "product.example.*"]
            )
            .values_list("connection_id", flat=True)
            .query.sql_with_params()
        )
        with connection.cursor() as cur:
            cur.execute(f"EXPLAIN {subscription_sql}", params)
            plan = "\n".join(row[0] for row in cur.fetchall())
    print(f"\nsubscription match plan:\n{plan}")
    assert "Seq Scan on webhooks_subscription" not in plan

    from workeasy360.integration.scheduling.services import _CLAIM_SQL

    now = timezone.now()
    with connection.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(f"EXPLAIN {_CLAIM_SQL}", [now, 200, now, 120.0, 3600.0])
        plan = "\n".join(row[0] for row in cur.fetchall())
        cur.execute("ROLLBACK")
    print(f"\ndue-work claim plan:\n{plan}")
    assert "ix_due_work_due" in plan

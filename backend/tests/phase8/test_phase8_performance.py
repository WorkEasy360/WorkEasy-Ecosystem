"""Phase 8 cost shapes: gateway overhead, reservation, indexing, retrieval,
fact ingestion, projection and analytics queries (Task 114).

Local measurements through the real HTTP stack and the real pipelines, with
the loopback provider answering instantly - so every millisecond reported is
the platform's own. The product authenticates with a ``client_secret``
(SHA-256 on the platform) rather than ``private_key_jwt``: the reference
client re-parses and signs with its RSA key on every call, which is the
product's cost, not the platform's. Printed with ``-s`` and recorded in
docs/phase-8/11-performance.md:

    pytest -s -m slow backend/tests/phase8/test_phase8_performance.py

What is *asserted* is shape, not speed (a shared laptop is too noisy for a
latency threshold to mean anything):

- the query count of a retrieval and of an analytics query is the same at
  10, 100, 500 and 5,000 rows;
- the retrieval and the analytics query each have an index-served plan
  (``EXPLAIN`` with sequential scans disabled needs no ``Seq Scan`` of the
  big table);
- a retrieval over a small authorized set costs the same with thousands of
  other rows in the table.
"""

from __future__ import annotations

import json
import random
import statistics
import time
import uuid
from contextlib import contextmanager
from datetime import timezone as dt_timezone

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.factories.intelligence import order_metric, record_event, summary_skill
from tests.phase8.helpers import drain, in_org

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.slow]

SIZES = (10, 100, 500, 5000)
REPEATS = 15
METRIC = "product.example.record_value"
_WORDS = (
    "invoice delivery refund warehouse ledger contract payment customer order "
    "shipment supplier return policy account balance schedule report budget "
    "forecast inventory quantity price discount tax region channel campaign "
    "lead opportunity pipeline stage renewal support ticket escalation"
).split()


@pytest.fixture
def secret_client(example_product, platform_admin, in_tenant, client):
    """The ``example`` product's reference client over a client secret."""
    from tests.factories.manifests import TEST_ISSUER
    from tests.factories.reference_product_client import ReferenceProductClient
    from workeasy360.platform.ecosystem import services

    with in_tenant(platform_admin["ctx"]):
        issued = services.create_credential(
            platform_admin["ctx"],
            client_id=example_product["client_id"],
            kind="client_secret",
        )
    return ReferenceProductClient(
        http=client,
        issuer=TEST_ISSUER,
        client_id=example_product["client_id"],
        redirect_uri=example_product["redirect_uri"],
        private_key_pem="",
        credential_kid="",
        client_secret=issued.secret,
    )


def _timed(call, repeats=REPEATS):
    call()  # warm
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        call()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), max(samples)


def _count_queries(call):
    with CaptureQueriesContext(connection) as queries:
        call()
    return len(queries.captured_queries), [q["sql"] for q in queries.captured_queries]


def _seq_scans(node, table):
    found = []
    if isinstance(node, list):
        for item in node:
            found += _seq_scans(item, table)
    elif isinstance(node, dict):
        if node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == table:
            found.append(node)
        for value in node.values():
            found += _seq_scans(value, table)
    return found


def _assert_indexable(sql, table):
    with connection.cursor() as cur:
        cur.execute("SET enable_seqscan = off")
        try:
            cur.execute("EXPLAIN (FORMAT JSON) " + sql)
            plan = cur.fetchone()[0]
        finally:
            cur.execute("SET enable_seqscan = on")
    plan = json.loads(plan) if isinstance(plan, str) else plan
    assert not _seq_scans(plan, table), f"no index serves the query on {table}"


def _paragraphs(n, seed):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        words = []
        while sum(len(w) + 1 for w in words) < 690:
            words.append(rng.choice(_WORDS))
        out.append(" ".join(words).capitalize() + ".")
    return out


def _report(title, rows):
    print(f"\n{title}")
    for row in rows:
        print("  " + " | ".join(str(c) for c in row))


@contextmanager
def _organization_scope(organization_id):
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with (
        tenant_context(routing_context(organization_id)),
        organization_db_scope(organization_id),
    ):
        yield


# --- AI: gateway overhead and reservation ---------------------------------------------------


def test_gateway_overhead_and_reservation(fake_ai, ai_registry, ai_org, secret_client):
    ai_client = secret_client
    from workeasy360.intelligence.ai import budget, policy

    org = ai_org()
    skill = summary_skill(retrieval={"enabled": False})
    assert ai_client.register_skill(skill)[0] == 201
    request = {"skill": skill["key"], "input": {"record": "A short record."}}

    # The fixed cost every product call pays: client authentication, rate
    # limiting, rendering. Listing one's skills does nothing else.
    baseline, _ = _timed(lambda: ai_client.intelligence("ai_skills"))
    invoke_p50, invoke_max = _timed(
        lambda: ai_client.invoke(organization_id=org["organization_id"], request=request)
    )
    invoke_queries, _ = _count_queries(
        lambda: ai_client.invoke(organization_id=org["organization_id"], request=request)
    )

    grant = policy.authorize(org["org"].id, "example")

    def reserve_and_settle():
        reserved = budget.reserve(
            grant,
            client_id="perf",
            operation="invoke",
            mode="sync",
            estimate_micros=10_000,
        )
        budget.settle(
            grant.ctx, reserved.execution.id, succeeded=True, outcome="ok", lines=[]
        )

    cycle_p50, cycle_max = _timed(reserve_and_settle)
    cycle_queries, _ = _count_queries(reserve_and_settle)
    _report(
        "AI gateway (loopback provider answering instantly)",
        [
            ("authenticated no-op call", f"p50 {baseline:.1f} ms"),
            (
                "sync invoke, no retrieval",
                f"p50 {invoke_p50:.1f} ms, max {invoke_max:.1f} ms, {invoke_queries} queries",
            ),
            ("gateway overhead (invoke - no-op)", f"{invoke_p50 - baseline:.1f} ms"),
            (
                "reserve + settle (row locks, timer)",
                f"p50 {cycle_p50:.1f} ms, max {cycle_max:.1f} ms, {cycle_queries} queries",
            ),
        ],
    )
    assert fake_ai.calls("/v1/messages")


# --- RAG: indexing and retrieval ------------------------------------------------------------


def _index(ai_client, org, chunks, seed):
    """Submit documents totalling about ``chunks`` chunks and index them all."""
    from workeasy360.intelligence.rag.models import KnowledgeChunk, KnowledgeDocument

    per_document = min(chunks, 250)
    started = time.perf_counter()
    for number in range(max(1, chunks // per_document)):
        body = "\n\n".join(_paragraphs(per_document, seed * 1000 + number))
        status, answer = ai_client.index_document(
            organization_id=org["organization_id"],
            document={
                "source": "help_center",
                "document_id": f"doc-{number}",
                "version": "1",
                "title": f"Document {number}",
                "content": body,
            },
        )
        assert status == 202, answer
    for _ in range(40):
        drain(org["org"].id, rounds=1)
        with in_org(org["org"].id):
            pending = (
                KnowledgeDocument.unscoped.filter(organization_id=org["org"].id)
                .exclude(status="indexed")
                .count()
            )
        if not pending:
            break
    elapsed = time.perf_counter() - started
    with in_org(org["org"].id):
        stored = KnowledgeChunk.unscoped.filter(organization_id=org["org"].id).count()
    assert not pending and stored >= chunks * 0.9
    return stored, elapsed


def _retrieval_sql(sqls):
    matches = [s for s in sqls if "rag_chunk" in s and "<=>" in s]
    assert len(matches) == 1, [s[:120] for s in sqls]
    return matches[0]


def test_indexing_and_retrieval_scale_with_the_authorized_set(
    fake_ai, ai_registry, ai_org, secret_client
):
    ai_client = secret_client
    query = {
        "query": "refund policy for a delayed shipment",
        "sources": ["help_center"],
        "top_k": 5,
    }
    rows, counts, small = [], set(), None
    for seed, size in enumerate(SIZES):
        org = ai_org()
        stored, elapsed = _index(ai_client, org, size, seed)

        def retrieve(org=org):
            status, body = ai_client.retrieve(
                organization_id=org["organization_id"], query=query
            )
            assert status == 200 and len(body["results"]) == min(5, stored), body

        p50, worst = _timed(retrieve)
        count, sqls = _count_queries(retrieve)
        _assert_indexable(_retrieval_sql(sqls), "rag_chunk")
        counts.add(count)
        rows.append(
            (
                f"{stored} chunks",
                f"indexed in {elapsed:.1f} s ({stored / elapsed:.0f} chunks/s)",
                f"retrieve p50 {p50:.1f} ms, max {worst:.1f} ms",
                f"{count} queries",
            )
        )
        if small is None:
            small = (org, retrieve, p50)

    # The first organization again, now that thousands of other tenants'
    # chunks share the table: the candidate set, not the table, sets the cost.
    org, retrieve, before = small
    after, _ = _timed(retrieve)
    rows.append(
        (
            f"{SIZES[0]} chunks again, {sum(SIZES) - SIZES[0]}+ other chunks in the table",
            "",
            f"retrieve p50 {after:.1f} ms (was {before:.1f} ms)",
            "",
        )
    )
    _report("RAG indexing and retrieval (embedding = loopback provider)", rows)
    assert len(counts) == 1, f"retrieval query count varies with size: {counts}"


# --- Analytics: ingestion, projection, query ------------------------------------------------


def _seed_facts(organization_id, n):
    from workeasy360.intelligence.analytics.models import AnalyticsFact

    now = timezone.now()
    statuses = ("open", "won", "lost")
    facts = [
        AnalyticsFact(
            organization_id=organization_id,
            source="product.example",
            event_id=uuid.uuid4(),
            metric_key=METRIC,
            metric_version=1,
            occurred_at=now - timezone.timedelta(hours=(i * 7) % (29 * 24)),
            received_at=now,
            value=1000 + i,
            currency="GBP",
            dims={"status": statuses[i % 3]},
            dims_key=f"status={statuses[i % 3]}",
        )
        for i in range(n)
    ]
    with in_org(organization_id):
        AnalyticsFact.unscoped.bulk_create(facts, batch_size=1000)


def _project_all(organization_id):
    from workeasy360.intelligence.analytics.projection import project

    started = time.perf_counter()
    with _organization_scope(organization_id):
        while project(organization_id) == "more":
            pass
    return time.perf_counter() - started


def test_fact_ingestion_projection_and_queries(entitled_org, secret_client):
    client = secret_client
    assert client.register_metric(order_metric())[0] == 201
    midnight = (
        timezone.now()
        .astimezone(dt_timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )
    query = {
        "metric": METRIC,
        "start": (midnight - timezone.timedelta(days=30)).isoformat(),
        "end": (midnight + timezone.timedelta(days=1)).isoformat(),
        "granularity": "day",
        "group_by": ["status"],
    }

    # Ingestion: an event through the Phase 6 endpoint, routed, one fact.
    org = entitled_org("example")
    ingest_p50, ingest_max = _timed(
        lambda: client.publish_event(
            organization_id=org["organization_id"], event=record_event()
        ),
        repeats=30,
    )

    rows, counts = [], set()
    for size in SIZES:
        org = entitled_org("example")
        _seed_facts(org["org"].id, size)
        projection_s = _project_all(org["org"].id)

        def run(org=org):
            status, body = client.query_metric(
                organization_id=org["organization_id"], query=query
            )
            assert status == 200 and body["freshness"]["up_to_date"], body
            assert sum(r["count"] for r in body["results"]) == size

        p50, worst = _timed(run)
        count, sqls = _count_queries(run)
        aggregate_sql = [
            s for s in sqls if "analytics_aggregate" in s and "GROUP BY" in s
        ]
        assert aggregate_sql, [s[:120] for s in sqls]
        _assert_indexable(aggregate_sql[0], "analytics_aggregate")
        counts.add(count)
        rows.append(
            (
                f"{size} facts",
                f"projected in {projection_s * 1000:.0f} ms ({size / projection_s:.0f} facts/s)",
                f"query (30 days x 3 statuses) p50 {p50:.1f} ms, max {worst:.1f} ms",
                f"{count} queries",
            )
        )
    rows.insert(
        0,
        (
            "event ingestion (HTTP, routed, fact written)",
            f"p50 {ingest_p50:.1f} ms, max {ingest_max:.1f} ms",
            "",
            "",
        ),
    )
    _report("Analytics", rows)
    assert len(counts) == 1, f"analytics query count varies with size: {counts}"

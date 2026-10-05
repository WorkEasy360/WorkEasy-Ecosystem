"""Phase 8 concurrency: real threads, real connections, real commits (Task 98).

Every test uses ``transaction=True`` - a rollback-wrapped test gives each
thread a private view and proves nothing - and asserts an *invariant*:
exactly one reservation, exactly one version, exactly one fact, never a
double charge or a double count.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections
from django.test import Client
from django.utils import timezone

from tests.factories.intelligence import (
    document,
    order_metric,
    record_event,
    summary_skill,
)
from tests.factories.reference_product_client import ReferenceProductClient
from tests.phase8.helpers import drain, executions, in_org, spend

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 8
PLAIN = summary_skill(
    key="product.example.plain", retrieval={"enabled": False, "top_k": 3}
)


def _run(worker, count=WORKERS):
    results: list = []
    errors: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(count)

    def wrapped(index: int) -> None:
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


def _thread_client(template: ReferenceProductClient) -> ReferenceProductClient:
    """One product client per thread: its own HTTP transport, the same identity."""
    return ReferenceProductClient(
        http=Client(),
        issuer=template.issuer,
        client_id=template.client_id,
        redirect_uri=template.redirect_uri,
        private_key_pem=template.private_key_pem,
        credential_kid=template.credential_kid,
        client_secret=template.client_secret,
    )


def _invoke(template, org, **extra):
    return _thread_client(template).invoke(
        organization_id=org["organization_id"],
        request={"skill": "product.example.plain", "input": {"record": "same"}, **extra},
    )


# --- AI quota -----------------------------------------------------------------------


def test_eight_requests_against_one_requests_budget_reserve_exactly_one(
    fake_ai, ai_registry, ai_org, ai_client
):
    ai_client.register_skill(PLAIN)
    probe = ai_org()
    assert _invoke(ai_client, probe)[0] == 200
    one_request = executions(probe["org"].id)[0]["reserved_micros"]

    org = ai_org(budget=one_request)
    results, errors = _run(lambda i: _invoke(ai_client, org))
    assert errors == []
    statuses = sorted(status for status, _ in results)
    assert statuses.count(200) == 1, results
    assert statuses.count(429) == WORKERS - 1
    assert {body["error"] for status, body in results if status == 429} == {
        "ai_quota_exceeded"
    }
    month = spend(org["org"].id, "example")
    assert month["reserved_micros"] == 0
    assert 0 < month["spent_micros"] <= one_request
    assert len(executions(org["org"].id)) == 1


def test_the_per_organization_concurrency_cap_holds_under_a_burst(
    fake_ai, ai_registry, ai_org, ai_client, settings
):
    ai_client.register_skill(PLAIN)
    settings.AI_MAX_CONCURRENT_PER_ORG = 2
    fake_ai.latency = 1.0
    org = ai_org()
    results, errors = _run(lambda i: _invoke(ai_client, org))
    assert errors == []
    succeeded = [body for status, body in results if status == 200]
    limited = [body for status, body in results if status == 429]
    assert len(succeeded) == 2 and len(limited) == WORKERS - 2
    assert {body["error"] for body in limited} == {"ai_concurrency_limited"}
    assert fake_ai.peak_in_flight <= 2


def test_one_idempotency_key_raced_eight_ways_is_one_execution_and_one_call(
    fake_ai, ai_registry, ai_org, ai_client
):
    ai_client.register_skill(PLAIN)
    fake_ai.latency = 0.5
    org = ai_org()
    results, errors = _run(lambda i: _invoke(ai_client, org, idempotency_key="same-key"))
    assert errors == []
    assert len(executions(org["org"].id)) == 1
    assert len(fake_ai.calls("/v1/messages")) == 1
    assert sum(1 for s, b in results if s == 200 and b.get("replayed") is not True) == 1
    for status, body in results:
        assert status == 200 or (status, body["error"]) == (409, "ai_request_conflict")


def test_a_crashed_callers_reservation_is_settled_at_its_full_amount_once(
    ai_registry, ai_org
):
    """Task 59: the caller dies between reserving and settling. The lease
    timer settles it at the reserved amount - never zero, never twice - and a
    late settle from the dead caller changes nothing."""
    from workeasy360.intelligence.ai import budget, policy

    org = ai_org()
    grant = policy.authorize(org["org"].id, "example")
    reserved = budget.reserve(
        grant, client_id="c", operation="invoke", mode="sync", estimate_micros=12_345
    )
    execution_id = reserved.execution.id
    with in_org(org["org"].id):
        from workeasy360.intelligence.ai.models import AIExecution

        AIExecution.unscoped.filter(id=execution_id).update(
            lease_expires_at=timezone.now() - timezone.timedelta(seconds=1)
        )
    drain(org["org"].id)

    [row] = executions(org["org"].id)
    assert (row["status"], row["outcome"], row["cost_micros"]) == (
        "expired",
        "abandoned",
        12_345,
    )
    month = spend(org["org"].id)
    assert (month["reserved_micros"], month["spent_micros"]) == (0, 12_345)

    late = budget.settle(grant.ctx, execution_id, succeeded=True, outcome="ok", lines=[])
    assert late is None
    assert spend(org["org"].id)["spent_micros"] == 12_345


def test_settlement_racing_expiry_counts_once(ai_registry, ai_org):
    from workeasy360.intelligence.ai import budget, policy
    from workeasy360.intelligence.ai.models import AIExecution

    org = ai_org()
    grant = policy.authorize(org["org"].id, "example")
    reserved = budget.reserve(
        grant, client_id="c", operation="invoke", mode="sync", estimate_micros=1_000
    )
    execution_id = reserved.execution.id
    with in_org(org["org"].id):
        AIExecution.unscoped.filter(id=execution_id).update(
            lease_expires_at=timezone.now() - timezone.timedelta(seconds=1)
        )
    line = budget.UsageLine(
        kind="generation",
        model_key="test.primary",
        provider_key="anthropic",
        price_id=None,
        attempt=1,
        outcome="ok",
        input_tokens=10,
        output_tokens=10,
        cost_micros=400,
    )

    def worker(i):
        if i % 2:
            return budget.settle(
                grant.ctx, execution_id, succeeded=True, outcome="ok", lines=[line]
            )
        return budget.expire(grant.ctx, execution_id)

    results, errors = _run(worker)
    assert errors == []
    month = spend(org["org"].id)
    assert month["reserved_micros"] == 0
    assert month["requests"] == 1
    assert month["spent_micros"] in (400, 1_000)


# --- retrieval indexing ----------------------------------------------------------------


def test_one_document_version_submitted_eight_ways_indexes_once(
    fake_ai, ai_registry, ai_org, ai_client
):
    from workeasy360.intelligence.rag.models import (
        KnowledgeChunk,
        KnowledgeDocumentVersion,
    )

    org = ai_org()
    results, errors = _run(
        lambda i: _thread_client(ai_client).index_document(
            organization_id=org["organization_id"], document=document()
        )
    )
    assert errors == []
    assert sorted(status for status, _ in results).count(202) == 1
    drain(org["org"].id)
    with in_org(org["org"].id):
        assert (
            KnowledgeDocumentVersion.unscoped.filter(
                organization_id=org["org"].id
            ).count()
            == 1
        )
        chunks = KnowledgeChunk.unscoped.filter(organization_id=org["org"].id)
        assert chunks.count() == 1
    assert len(fake_ai.calls("/v1/embeddings")) == 1


def test_racing_versions_leave_only_the_newest_serving(
    fake_ai, ai_registry, ai_org, ai_client
):
    from workeasy360.intelligence.rag.models import KnowledgeChunk, KnowledgeDocument

    org = ai_org()
    results, errors = _run(
        lambda i: _thread_client(ai_client).index_document(
            organization_id=org["organization_id"],
            document=document(
                version=str(i + 1), content=f"Version {i + 1} of the policy."
            ),
        )
    )
    assert errors == []
    drain(org["org"].id)
    with in_org(org["org"].id):
        doc = KnowledgeDocument.unscoped.get(organization_id=org["org"].id)
        assert doc.served_seq == doc.latest_seq == WORKERS
        texts = list(
            KnowledgeChunk.unscoped.filter(organization_id=org["org"].id).values_list(
                "text", flat=True
            )
        )
    assert texts == [f"Version {doc.latest_version_label} of the policy."]


# --- analytics --------------------------------------------------------------------------


def test_one_event_published_eight_ways_is_one_fact(
    example_product, product_client, entitled_org
):
    from workeasy360.intelligence.analytics.models import AnalyticsFact

    x = product_client(example_product)
    x.register_metric(order_metric())
    org = entitled_org("example")
    event = record_event(amount_minor=900)
    results, errors = _run(
        lambda i: _thread_client(x).publish_event(
            organization_id=org["organization_id"], event=event
        )
    )
    assert errors == []
    assert sorted(s for s, _ in results).count(202) == 1
    drain(org["org"].id)
    with in_org(org["org"].id):
        assert AnalyticsFact.unscoped.filter(organization_id=org["org"].id).count() == 1


def test_concurrent_projections_add_every_fact_exactly_once(
    example_product, product_client, entitled_org, settings
):
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.intelligence.analytics import projection
    from workeasy360.intelligence.analytics.models import (
        AnalyticsAggregate,
        AnalyticsFact,
    )
    from workeasy360.platform.tenancy.context import tenant_context

    x = product_client(example_product)
    x.register_metric(order_metric())
    org = entitled_org("example")
    late = timezone.now() - timezone.timedelta(days=2)
    for i in range(40):
        x.publish_event(
            organization_id=org["organization_id"],
            event=record_event(amount_minor=i + 1, occurred_at=late if i % 2 else None),
        )
    settings.ANALYTICS_PROJECTION_BATCH = 7

    def worker(i):
        ctx = routing_context(org["org"].id)
        from workeasy360.platform.tenancy.db import organization_db_scope

        outcomes = []
        with tenant_context(ctx), organization_db_scope(org["org"].id):
            for _ in range(10):
                outcomes.append(projection.project(org["org"].id))
        return outcomes

    results, errors = _run(worker)
    assert errors == []
    with in_org(org["org"].id):
        assert not AnalyticsFact.unscoped.filter(
            organization_id=org["org"].id, projected_at__isnull=True
        ).exists()
        daily = AnalyticsAggregate.unscoped.filter(
            organization_id=org["org"].id, granularity="day"
        )
        assert sum(r.sum_value for r in daily) == sum(range(1, 41))
        assert sum(r.event_count for r in daily) == 40
        hourly = AnalyticsAggregate.unscoped.filter(
            organization_id=org["org"].id, granularity="hour"
        )
        assert sum(r.sum_value for r in hourly) == sum(range(1, 41))

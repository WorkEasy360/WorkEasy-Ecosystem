"""Phase 8 final acceptance - AI (the brief's 13 steps), end to end.

Real HTTP through the real middleware; the product is the reference client
(imports nothing from the platform, discovers every endpoint); the vendor is
a loopback server reached through the real adapters and the real egress
client; commits are real, so after-commit work and timers run as deployed.
"""

from __future__ import annotations

import pytest

from tests.factories.intelligence import document, summary_skill
from tests.phase8.helpers import executions, in_org, spend

pytestmark = [pytest.mark.django_db(transaction=True)]


def _ask(**overrides):
    request = {
        "skill": "product.example.summary",
        "version": 1,
        "input": {"record": "A customer asks how returns and refunds work."},
        "retrieval": {"query": "returns refund policy", "sources": ["help_center"]},
    }
    request.update(overrides)
    return request


def test_phase_8_ai_acceptance_flow(
    client,
    fake_ai,
    ai_registry,
    ai_org,
    ai_client,
    second_product,
    product_client,
):
    # 1. Organization A is entitled to Product X and to AI for it.
    org_a = ai_org()
    a = org_a["organization_id"]

    # 2-3. Product X authenticates (private_key_jwt) and registers its skill.
    status, body = ai_client.register_skill(summary_skill())
    assert status == 201, body
    assert body["skill"]["key"] == "product.example.summary"

    # 5. An authorized Product X document is indexed for Organization A.
    status, body = ai_client.index_document(organization_id=a, document=document())
    assert status == 202, body
    status, state = ai_client.document_status(
        organization_id=a, source="help_center", document_id="article-1"
    )
    assert status == 200 and state["status"] == "indexed", state
    assert state["indexed_version"] == "1" and state["stale"] is False

    # 3-4, 6-8. Invoke: product, organization, entitlement, skill and quota
    # are validated; retrieval returns only A's, X's passages; the approved
    # model answers; the answer cites real stored provenance.
    status, answer = ai_client.invoke(
        organization_id=a, request=_ask(idempotency_key="acceptance-1")
    )
    assert status == 200, answer
    assert answer["status"] == "succeeded"
    assert answer["model"] == {"provider": "anthropic", "model": "test.primary"}
    assert answer["citations"] == [
        {
            "ref": "S1",
            "source": "help_center",
            "document_id": "article-1",
            "version": "1",
            "chunk": 0,
            "title": "Returns policy",
            "url": "https://help.example.com/articles/1",
        }
    ]
    sent = fake_ai.calls("/v1/messages")[-1]
    assert sent.headers["x-api-key"].startswith("sk-ant-test-")
    assert "tools" not in sent.body  # the model is given no actions (Task 16)
    assert "returns" in sent.body["messages"][0]["content"].lower()

    # 9. Usage and cost are recorded - tokens, provider, model, price version.
    [invoked] = [e for e in executions(org_a["org"].id) if e["operation"] == "invoke"]
    assert invoked["status"] == "succeeded"
    assert invoked["model_key"] == "test.primary"
    assert invoked["input_tokens"] > 0 and invoked["output_tokens"] > 0
    assert invoked["embedding_tokens"] > 0
    assert invoked["cost_micros"] > 0 and invoked["currency"] == "USD"
    assert answer["usage"]["estimated_cost_micros"] == invoked["cost_micros"]
    month = spend(org_a["org"].id)
    assert month["reserved_micros"] == 0
    assert month["spent_micros"] >= invoked["cost_micros"]
    spent_before_retry = month["spent_micros"]
    provider_calls = len(fake_ai.requests)

    # 10. A retry under the same idempotency key is not charged again and
    # reaches no provider: it gets the stored answer.
    status, again = ai_client.invoke(
        organization_id=a, request=_ask(idempotency_key="acceptance-1")
    )
    assert status == 200 and again["replayed"] is True
    assert again["request_id"] == answer["request_id"]
    assert again["output"] == answer["output"]
    assert spend(org_a["org"].id)["spent_micros"] == spent_before_retry
    assert len(fake_ai.requests) == provider_calls

    # 11. Organization B can never retrieve Organization A's source.
    org_b = ai_org()
    b = org_b["organization_id"]
    ai_client.index_document(
        organization_id=b,
        document=document(
            document_id="article-b",
            title="Shipping policy",
            url="https://help.example.com/articles/b",
            content="Orders ship within two days. Returns are covered in another article.",
        ),
    )
    status, found = ai_client.retrieve(
        organization_id=b,
        query={
            "query": "returns refund policy thirty days",
            "sources": ["help_center"],
            "top_k": 10,
        },
    )
    assert status == 200, found
    assert {r["document_id"] for r in found["results"]} == {"article-b"}
    status, answer_b = ai_client.invoke(organization_id=b, request=_ask())
    assert status == 200, answer_b
    assert {c["document_id"] for c in answer_b["context"]} == {"article-b"}

    # 12. Product Y cannot invoke Product X's private skill - reported as
    # not found, exactly like a skill that does not exist, before anything
    # about the organization is even looked at.
    product_y = product_client(second_product)
    status, refused = product_y.invoke(organization_id=a, request=_ask())
    assert status == 404 and refused["error"] == "ai_skill_not_found", refused
    status, missing = product_y.invoke(
        organization_id=a, request=_ask(skill="product.example.no_such_skill")
    )
    assert (status, missing["error"]) == (404, "ai_skill_not_found")

    # 13. A provider outage is a bounded AI failure and nothing else: the
    # request fails fast with a retryable code, is settled (reservation
    # released), and sign-in's dependencies, readiness and the entitlement
    # API are untouched.
    fake_ai.fail("/v1/messages", 503, times=20)
    status, outage = ai_client.invoke(organization_id=a, request=_ask())
    assert status == 503 and outage["error"] == "ai_provider_unavailable", outage
    failed = executions(org_a["org"].id)[-1]
    assert failed["status"] == "failed" and failed["outcome"] == "overloaded"
    assert spend(org_a["org"].id)["reserved_micros"] == 0
    assert client.get("/healthz/ready").status_code == 200
    assert ai_client.verify_entitlement(organization_id=a)["decision"] == "allow"

    with in_org(org_a["org"].id):
        from workeasy360.intelligence.ai.models import AIUsageLine

        lines = list(
            AIUsageLine.unscoped.filter(
                organization_id=org_a["org"].id, execution_id=failed["id"]
            ).values_list("kind", "outcome")
        )
    # The query embedding succeeded and is charged; generation was attempted
    # exactly AI_MAX_ATTEMPTS times - bounded, never an unbounded retry storm.
    assert sorted(lines) == [
        ("embedding", "ok"),
        ("generation", "overloaded"),
        ("generation", "overloaded"),
        ("generation", "overloaded"),
    ]

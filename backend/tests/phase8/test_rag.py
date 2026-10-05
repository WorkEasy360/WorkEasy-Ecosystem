"""Retrieval-augmented generation: ingestion, lifecycle, isolation (Tasks 21-43).

Through the product endpoints and the real indexing pipeline; the vendor is
the loopback fake, whose embeddings are a deterministic bag of words, so
"nearest" means something and cross-tenant tests are adversarial for real:
the other tenant's passage is the *closest* one in the whole table.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.utils import timezone

from tests.factories.intelligence import document
from tests.phase8.helpers import drain, executions, in_org

pytestmark = [pytest.mark.django_db(transaction=True)]

SECRET_PASSAGE = (
    "Project Nightingale acquisition closes on the ninth. Nightingale price is "
    "forty million."
)


def _docs(organization_id):
    from workeasy360.intelligence.rag.models import KnowledgeDocument

    with in_org(organization_id):
        return list(
            KnowledgeDocument.unscoped.filter(organization_id=organization_id).values()
        )


def _chunks(organization_id):
    from workeasy360.intelligence.rag.models import KnowledgeChunk

    with in_org(organization_id):
        return list(
            KnowledgeChunk.unscoped.filter(organization_id=organization_id).values(
                "version_id", "text", "product_code", "embedding_model"
            )
        )


def _retrieve(client, org, query, **extra):
    return client.retrieve(
        organization_id=org["organization_id"],
        query={"query": query, "sources": ["help_center"], "top_k": 10, **extra},
    )


# --- idempotency and versions ----------------------------------------------------------


def test_the_same_version_twice_is_a_duplicate_and_different_content_a_conflict(
    fake_ai, ai_registry, ai_org, ai_client
):
    org = ai_org()
    assert (
        ai_client.index_document(
            organization_id=org["organization_id"], document=document()
        )[0]
        == 202
    )
    status, body = ai_client.index_document(
        organization_id=org["organization_id"], document=document()
    )
    assert (status, body["status"]) == (200, "duplicate")
    status, body = ai_client.index_document(
        organization_id=org["organization_id"], document=document(content="Different.")
    )
    assert (status, body["error"]) == (409, "knowledge_conflict")
    assert len(_chunks(org["org"].id)) == 1
    assert len(fake_ai.calls("/v1/embeddings")) == 1


def test_a_new_version_replaces_the_old_chunks_atomically(
    fake_ai, ai_registry, ai_org, ai_client
):
    org = ai_org()
    ai_client.index_document(organization_id=org["organization_id"], document=document())
    old = {c["version_id"] for c in _chunks(org["org"].id)}
    ai_client.index_document(
        organization_id=org["organization_id"],
        document=document(
            version="2", content="Returns are now accepted for sixty days."
        ),
    )
    chunks = _chunks(org["org"].id)
    assert {c["version_id"] for c in chunks}.isdisjoint(old)
    status, found = _retrieve(ai_client, org, "returns days")
    assert [r["text"] for r in found["results"]] == [
        "Returns are now accepted for sixty days."
    ]
    assert found["results"][0]["version"] == "2"


def _queued_only(monkeypatch):
    """Record versions without the after-commit fast path running them - as
    if no worker had picked them up yet."""
    from workeasy360.intelligence.rag import tasks

    monkeypatch.setattr(tasks.index_version, "apply_async", lambda *a, **k: None)


def _claim_as_worker(org, version_label):
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.intelligence.rag import indexing
    from workeasy360.intelligence.rag.models import KnowledgeDocumentVersion
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with in_org(org["org"].id):
        version = KnowledgeDocumentVersion.unscoped.get(
            organization_id=org["org"].id, version_label=version_label
        )
    ctx = routing_context(org["org"].id)
    with tenant_context(ctx), organization_db_scope(org["org"].id):
        return ctx, indexing._claim(version.id, org["org"].id)


def test_an_older_version_finishing_late_never_replaces_a_newer_one(
    fake_ai, ai_registry, ai_org, ai_client, monkeypatch
):
    """The version race: a worker claims v1; v2 is submitted and fully
    indexed meanwhile; then v1's worker finishes - and is discarded."""
    from workeasy360.intelligence.rag import indexing
    from workeasy360.intelligence.rag.models import KnowledgeDocumentVersion
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    org = ai_org()
    _queued_only(monkeypatch)
    ai_client.index_document(organization_id=org["organization_id"], document=document())
    ctx, claimed = _claim_as_worker(org, "1")
    monkeypatch.undo()

    ai_client.index_document(
        organization_id=org["organization_id"],
        document=document(version="2", content="The newer policy text."),
    )
    with tenant_context(ctx):
        outcome = indexing._embed(ctx, claimed)
        with organization_db_scope(org["org"].id):
            assert indexing._activate(claimed, org["org"].id, *outcome) == "superseded"

    with in_org(org["org"].id):
        statuses = dict(
            KnowledgeDocumentVersion.unscoped.filter(
                organization_id=org["org"].id
            ).values_list("version_label", "status")
        )
    assert statuses == {"1": "superseded", "2": "indexed"}
    assert [c["text"] for c in _chunks(org["org"].id)] == ["The newer policy text."]


def test_a_worker_that_dies_mid_index_is_recovered_without_duplicates(
    fake_ai, ai_registry, ai_org, ai_client, monkeypatch
):
    """A worker claims a version and never reports back. Its lease lapses;
    the timer re-dispatches; the next attempt indexes it exactly once."""
    from workeasy360.intelligence.rag.models import KnowledgeDocumentVersion

    org = ai_org()
    _queued_only(monkeypatch)
    ai_client.index_document(organization_id=org["organization_id"], document=document())
    _ctx, claimed = _claim_as_worker(org, "1")  # ...and the worker dies here.
    monkeypatch.undo()
    assert claimed.attempt == 1

    with in_org(org["org"].id):
        version = KnowledgeDocumentVersion.unscoped.get(organization_id=org["org"].id)
        assert version.status == "indexing"
        KnowledgeDocumentVersion.unscoped.filter(id=version.id).update(
            lease_expires_at=timezone.now() - timezone.timedelta(seconds=1)
        )
    drain(org["org"].id)
    with in_org(org["org"].id):
        version.refresh_from_db()
    assert (version.status, version.attempts) == ("indexed", 2)
    assert len(_chunks(org["org"].id)) == 1
    assert len(fake_ai.calls("/v1/embeddings")) == 1


def test_an_embedding_outage_is_never_reported_as_indexed(
    fake_ai, ai_registry, ai_org, ai_client, settings
):
    org = ai_org()
    fake_ai.fail("/v1/embeddings", 500, times=100)
    ai_client.index_document(organization_id=org["organization_id"], document=document())
    status, state = ai_client.document_status(
        organization_id=org["organization_id"],
        source="help_center",
        document_id="article-1",
    )
    assert state["status"] == "pending"
    assert state["latest_version_status"] == "retrying"
    assert state["last_error"] == "server_error"
    assert state["indexed_version"] is None
    status, body = _retrieve(ai_client, org, "returns")
    assert (status, body["error"]) == (409, "index_not_ready")

    settings.RAG_MAX_INDEX_ATTEMPTS = 2
    drain(org["org"].id)
    status, state = ai_client.document_status(
        organization_id=org["organization_id"],
        source="help_center",
        document_id="article-1",
    )
    assert state["status"] == "failed" and state["latest_version_status"] == "failed"
    assert _chunks(org["org"].id) == []


def test_a_serving_document_reports_stale_while_its_new_version_fails(
    fake_ai, ai_registry, ai_org, ai_client
):
    org = ai_org()
    ai_client.index_document(organization_id=org["organization_id"], document=document())
    fake_ai.fail("/v1/embeddings", 500, times=100)
    ai_client.index_document(
        organization_id=org["organization_id"],
        document=document(version="2", content="New."),
    )
    status, state = ai_client.document_status(
        organization_id=org["organization_id"],
        source="help_center",
        document_id="article-1",
    )
    assert (state["status"], state["indexed_version"], state["latest_version"]) == (
        "indexed",
        "1",
        "2",
    )
    assert state["stale"] is True and state["last_error"] == "server_error"


# --- deletion and revocation -----------------------------------------------------------


def test_a_deleted_document_stops_being_retrievable_and_its_chunks_are_purged(
    fake_ai, ai_registry, ai_org, ai_client
):
    org = ai_org()
    ai_client.index_document(organization_id=org["organization_id"], document=document())
    status, body = ai_client.delete_document(
        organization_id=org["organization_id"],
        source="help_center",
        document_id="article-1",
    )
    assert status == 200 and body["chunks_purged"] == 1
    assert _chunks(org["org"].id) == []
    assert _docs(org["org"].id)[0]["status"] == "deleted"
    status, body = _retrieve(ai_client, org, "returns")
    assert (status, body["error"]) == (409, "index_not_ready")
    status, body = ai_client.delete_document(
        organization_id=org["organization_id"],
        source="help_center",
        document_id="article-1",
    )
    assert (status, body["error"]) == (404, "knowledge_not_found")


def test_a_revoked_source_is_unretrievable_at_once_and_purged_behind_a_timer(
    fake_ai, ai_registry, ai_org, ai_client
):
    org = ai_org()
    for i in range(3):
        ai_client.index_document(
            organization_id=org["organization_id"],
            document=document(document_id=f"a-{i}", content=f"Returns rule number {i}."),
        )
    status, body = ai_client.intelligence(
        "rag_source_revoke", organization_id=org["organization_id"], source="help_center"
    )
    assert status == 200 and body["documents"] == 3
    status, body = _retrieve(ai_client, org, "returns")
    assert (status, body["error"]) == (404, "knowledge_not_found")
    drain(org["org"].id)
    assert _chunks(org["org"].id) == []


def test_revocation_holds_while_the_organization_is_suspended(
    fake_ai, ai_registry, ai_org, ai_client
):
    """A privacy operation completes even when the organization is read-only;
    adding content does not."""
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org = ai_org()
    ai_client.index_document(organization_id=org["organization_id"], document=document())
    with unscoped_access(reason="test suspends an organization", module="tests"):
        Organization.objects.filter(id=org["org"].id).update(status="suspended")
    status, body = ai_client.index_document(
        organization_id=org["organization_id"], document=document(document_id="new")
    )
    assert status == 403
    status, body = ai_client.delete_document(
        organization_id=org["organization_id"],
        source="help_center",
        document_id="article-1",
    )
    assert status == 200 and _chunks(org["org"].id) == []


# --- isolation -------------------------------------------------------------------------


def test_the_nearest_passage_in_another_tenant_is_never_returned(
    fake_ai, ai_registry, ai_org, ai_client
):
    """Adversarial nearest neighbour (Task 31): Organization A's passage is
    word-for-word the query; Organization B's only passage is unrelated. B
    must still get only its own."""
    org_a, org_b = ai_org(), ai_org()
    ai_client.index_document(
        organization_id=org_a["organization_id"],
        document=document(document_id="secret", content=SECRET_PASSAGE),
    )
    ai_client.index_document(
        organization_id=org_b["organization_id"],
        document=document(document_id="unrelated", content="The canteen opens at eight."),
    )
    status, found = _retrieve(ai_client, org_b, SECRET_PASSAGE)
    assert status == 200
    assert [r["document_id"] for r in found["results"]] == ["unrelated"]
    assert all("Nightingale" not in r["text"] for r in found["results"])

    # And the database agrees on its own: inside B's scope, A's chunks do not
    # exist even for a raw query with no organization predicate.
    with in_org(org_b["org"].id), connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM rag_chunk WHERE text LIKE %s", ["%Nightingale%"]
        )
        assert cur.fetchone()[0] == 0


def test_a_product_never_searches_another_products_knowledge(
    fake_ai,
    ai_registry,
    ai_client,
    second_product,
    product_client,
    entitled_org,
    entitle_products,
):
    """Task 32: same organization, same source name, two products - deny by
    default."""
    y = product_client(second_product)
    org = entitled_org("example")
    entitle_products(
        org["org"].id,
        {
            "example": {"example.ai.monthly_budget_micros": 5_000_000},
            "second": {"second.ai.monthly_budget_micros": 5_000_000},
        },
    )
    ai_client.index_document(
        organization_id=org["organization_id"],
        document=document(document_id="x-only", content=SECRET_PASSAGE),
    )
    status, body = _retrieve(y, org, SECRET_PASSAGE)
    assert (status, body["error"]) == (404, "knowledge_not_found")
    y.index_document(
        organization_id=org["organization_id"],
        document=document(document_id="y-only", content="Item catalogue notes."),
    )
    status, found = _retrieve(y, org, SECRET_PASSAGE)
    assert [r["document_id"] for r in found["results"]] == ["y-only"]
    assert {c["product_code"] for c in _chunks(org["org"].id)} == {"example", "second"}


def test_a_product_cannot_touch_another_products_documents(
    fake_ai, ai_registry, ai_client, second_product, product_client, ai_org
):
    y = product_client(second_product)
    org = ai_org()
    ai_client.index_document(organization_id=org["organization_id"], document=document())
    for call in (y.delete_document, y.document_status):
        status, body = call(
            organization_id=org["organization_id"],
            source="help_center",
            document_id="article-1",
        )
        assert (status, body["error"]) == (404, "knowledge_not_found")
    assert len(_chunks(org["org"].id)) == 1


def test_tagged_documents_are_candidates_only_for_callers_with_a_tag(
    fake_ai, ai_registry, ai_org, ai_client
):
    org = ai_org()
    ai_client.index_document(
        organization_id=org["organization_id"],
        document=document(
            document_id="hr",
            content="Salary bands for returns team.",
            access_tags=["team:hr"],
        ),
    )
    ai_client.index_document(
        organization_id=org["organization_id"],
        document=document(document_id="public", content="Returns are accepted."),
    )
    status, found = _retrieve(ai_client, org, "returns salary")
    assert [r["document_id"] for r in found["results"]] == ["public"]
    status, found = _retrieve(ai_client, org, "returns salary", access_tags=["team:hr"])
    assert {r["document_id"] for r in found["results"]} == {"hr", "public"}


# --- bounds and safety -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ({"query": "x", "top_k": 1000}, "retrieval.top_k"),
        ({"query": "x" * 5000}, "retrieval.query"),
        ({"query": "x", "sources": [f"s{i}" for i in range(50)]}, "retrieval.sources"),
        ({"query": "x", "filter": "1=1"}, "retrieval"),
    ],
)
def test_retrieval_is_bounded(fake_ai, ai_registry, ai_org, ai_client, query, field):
    status, body = ai_client.retrieve(
        organization_id=ai_org()["organization_id"], query=query
    )
    assert (status, body["field"]) == (400, field)


def test_an_oversized_document_is_refused_before_anything_is_stored(
    fake_ai, ai_registry, ai_org, ai_client, settings
):
    settings.RAG_MAX_CHUNKS_PER_DOCUMENT = 3
    org = ai_org()
    status, body = ai_client.index_document(
        organization_id=org["organization_id"],
        document=document(content="\n\n".join("para " * 150 for _ in range(10))),
    )
    assert (status, body["field"]) == (400, "content")
    assert _docs(org["org"].id) == []


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "http://help.example.com/x", "https://a:b@evil.example/"],
)
def test_citation_links_are_https_and_never_scripts(
    fake_ai, ai_registry, ai_org, ai_client, url
):
    status, body = ai_client.index_document(
        organization_id=ai_org()["organization_id"], document=document(url=url)
    )
    assert (status, body["field"]) == (400, "url")


def test_secrets_are_redacted_before_they_are_stored_or_embedded(
    fake_ai, ai_registry, ai_org, ai_client
):
    org = ai_org()
    ai_client.index_document(
        organization_id=org["organization_id"],
        document=document(
            content="Deploy with api_key=AbCdEfGh12345678 then returns open."
        ),
    )
    embedded = fake_ai.calls("/v1/embeddings")[-1].body["input"]
    assert all("AbCdEfGh12345678" not in text for text in embedded)
    assert all("AbCdEfGh12345678" not in c["text"] for c in _chunks(org["org"].id))


def test_indexing_is_metered_and_charged_to_the_product(
    fake_ai, ai_registry, ai_org, ai_client
):
    org = ai_org()
    ai_client.index_document(organization_id=org["organization_id"], document=document())
    [indexed] = [e for e in executions(org["org"].id) if e["operation"] == "index"]
    assert indexed["status"] == "succeeded" and indexed["embedding_tokens"] > 0
    assert indexed["product_code"] == "example" and indexed["model_key"] == "test.embed"


def test_indexing_without_ai_entitlement_is_refused(
    fake_ai, ai_registry, entitled_org, ai_client
):
    org = entitled_org("example")
    status, body = ai_client.index_document(
        organization_id=org["organization_id"], document=document()
    )
    assert (status, body["error"]) == (403, "ai_not_entitled")
    assert fake_ai.requests == []

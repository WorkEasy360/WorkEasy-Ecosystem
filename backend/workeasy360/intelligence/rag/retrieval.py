"""Retrieval: the authorized candidate set first, then nearest neighbours
(Tasks 31-36).

**Authorization happens before the search, in the query itself** (Task 33).
The statement that ranks by distance can only see rows that are:

- in the organization - both the explicit ``organization_id`` predicate and
  RLS on every table joined (defence in depth: either alone suffices);
- owned by the **calling product** - ``product_code`` is the authenticated
  product, never a request field; another product's knowledge in the same
  organization is not in the candidate set at all (Task 32, deny by default);
- in the sources requested, each of which must be the caller's own;
- the **serving** version of an **indexed** document in an **active** source
  (deleted, revoked, superseded and not-yet-activated content is invisible,
  Task 37);
- visible to the caller's ``access_tags``: a document with tags is a
  candidate only for a caller presenting at least one of them; an untagged
  document is visible to every caller of that product (the product derives
  the tags from its own authorization of its user);
- embedded with the model the query is embedded with.

There is no "retrieve everything, then filter" step anywhere, so nothing
outside the authorized set can influence a ranking, a count, a timing or a
model's context.

**Exact search** over that candidate set (``ORDER BY embedding <=> query``,
pgvector's cosine distance), served by the ``(organization, product,
source)`` index. No approximate index yet: the candidate set is one product's
sources in one organization, measured in thousands of chunks, where an exact
scan is milliseconds and has perfect recall - and an approximate index with a
selective filter would trade recall for nothing. The trigger to revisit is
recorded in docs/phase-8/05-rag.md (§ Scaling).

**Provenance** (Task 36): every returned chunk carries its stable source key,
document id, served version, chunk ordinal, and the title and https URL the
product supplied - from stored metadata, never from a model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

from django.conf import settings
from django.db import connection

from workeasy360.contracts.errors import (
    AIInvalidRequest,
    IndexNotReady,
    KnowledgeNotFound,
)
from workeasy360.intelligence.ai import invocation, registry
from workeasy360.intelligence.ai.budget import UsageLine
from workeasy360.intelligence.ai.safety import redact
from workeasy360.intelligence.ai.transport import Deadline
from workeasy360.intelligence.rag.fields import to_literal
from workeasy360.intelligence.rag.models import (
    DocumentStatus,
    KnowledgeDocument,
    KnowledgeSource,
    SourceStatus,
)
from workeasy360.platform.observability.metrics import metrics

#: Parameterised throughout; no identifier or value is interpolated (G7).
#: ``%(organization)s`` etc. are bound by the driver.
_SEARCH_SQL: Final = """
    SELECT c.id, c.ordinal, c.text, d.document_key, d.title, d.url,
           d.served_version_label, s.source_key,
           c.embedding <=> %(query)s::vector AS distance
    FROM rag_chunk c
    JOIN rag_document d
      ON d.id = c.document_id AND d.organization_id = c.organization_id
    JOIN rag_source s
      ON s.id = d.source_id AND s.organization_id = d.organization_id
    WHERE c.organization_id = %(organization)s
      AND c.product_code = %(product)s
      AND c.source_id = ANY(%(sources)s)
      AND c.embedding_model = %(model)s
      AND d.status = 'indexed'
      AND d.served_version_id = c.version_id
      AND s.status = 'active'
      AND (cardinality(c.access_tags) = 0 OR c.access_tags && %(tags)s::varchar[])
    ORDER BY distance, c.id
    LIMIT %(limit)s
"""


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    text: str
    sources: tuple[str, ...]
    top_k: int
    access_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    ref: str
    source: str
    document_id: str
    version: str
    chunk: int
    title: str
    url: str
    text: str
    score: float

    def citation(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "source": self.source,
            "document_id": self.document_id,
            "version": self.version,
            "chunk": self.chunk,
            "title": self.title,
            "url": self.url,
        }

    def public(self) -> dict[str, Any]:
        return {**self.citation(), "text": self.text, "score": round(self.score, 6)}


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    lines: list[UsageLine]
    model_key: str
    provider_key: str
    redactions: int
    failure: str = ""


def parse_query(raw: Any, *, default_top_k: int | None = None) -> RetrievalQuery:
    """Validate a retrieval request's bounds (Task 35) before anything runs."""
    if not isinstance(raw, dict):
        raise AIInvalidRequest("retrieval must be an object.", field="retrieval")
    unknown = sorted(set(raw) - {"query", "sources", "top_k", "access_tags"})
    if unknown:
        raise AIInvalidRequest(
            f"Unknown retrieval field(s): {', '.join(unknown)}.", field="retrieval"
        )
    text = raw.get("query")
    limit = int(settings.RAG_MAX_QUERY_CHARS)
    if not isinstance(text, str) or not text.strip() or len(text) > limit:
        raise AIInvalidRequest(
            f"retrieval.query must be 1-{limit} characters.", field="retrieval.query"
        )
    sources = raw.get("sources", [])
    max_sources = int(settings.RAG_MAX_SOURCES_PER_QUERY)
    if (
        not isinstance(sources, list)
        or len(sources) > max_sources
        or not all(isinstance(s, str) and 0 < len(s) <= 64 for s in sources)
    ):
        raise AIInvalidRequest(
            f"retrieval.sources must list at most {max_sources} source keys.",
            field="retrieval.sources",
        )
    top_k = raw.get("top_k", default_top_k or settings.RAG_DEFAULT_TOP_K)
    if (
        isinstance(top_k, bool)
        or not isinstance(top_k, int)
        or not 1 <= top_k <= int(settings.RAG_MAX_TOP_K)
    ):
        raise AIInvalidRequest(
            f"retrieval.top_k must be 1-{settings.RAG_MAX_TOP_K}.",
            field="retrieval.top_k",
        )
    tags = raw.get("access_tags", [])
    if (
        not isinstance(tags, list)
        or len(tags) > int(settings.RAG_MAX_ACCESS_TAGS)
        or not all(isinstance(t, str) and 0 < len(t) <= 64 for t in tags)
    ):
        raise AIInvalidRequest(
            "retrieval.access_tags must list short tags.", field="retrieval.access_tags"
        )
    return RetrievalQuery(
        text=text,
        sources=tuple(dict.fromkeys(sources)),
        top_k=top_k,
        access_tags=tuple(sorted(set(tags))),
    )


def search(
    grant: Any,
    query: RetrievalQuery,
    *,
    deadline: Deadline,
    request_id: Any,
) -> RetrievalResult:
    """Embed the query and return the nearest authorized chunks. Must run
    between a reservation and its settlement; opens no transaction across
    the provider call. Raises ``KnowledgeNotFound`` / ``IndexNotReady``
    before anything is spent."""
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with tenant_context(grant.ctx), organization_db_scope(grant.organization_id):
        source_ids = _authorized_sources(grant.product_code, query.sources)
        _require_indexed(source_ids)

    model = registry.select_embedding(allowed_providers=grant.allowed_providers)
    if model is None:
        return RetrievalResult([], [], "", "", 0, failure="embedding_unavailable")

    text, found = redact(query.text)
    started = time.monotonic()
    embedded = invocation.embed(model, [text], deadline=deadline, request_id=request_id)
    if embedded.vectors is None:
        return RetrievalResult(
            [],
            embedded.lines,
            model.key,
            model.provider_key,
            sum(found.values()),
            failure=embedded.failure,
        )

    with tenant_context(grant.ctx), organization_db_scope(grant.organization_id):
        with connection.cursor() as cur:
            cur.execute(
                _SEARCH_SQL,
                {
                    "query": to_literal(embedded.vectors[0]),
                    "organization": grant.organization_id,
                    "product": grant.product_code,
                    "sources": list(source_ids),
                    "model": model.key,
                    "tags": list(query.access_tags),
                    "limit": query.top_k,
                },
            )
            rows = cur.fetchall()

    metrics.observe("rag_retrieval_duration_seconds", time.monotonic() - started)
    metrics.observe("rag_retrieval_results", float(len(rows)))
    chunks = [
        RetrievedChunk(
            ref=f"S{index}",
            source=source_key,
            document_id=document_key,
            version=version_label,
            chunk=ordinal,
            title=title,
            url=url,
            text=chunk_text,
            score=max(0.0, 1.0 - float(distance)),
        )
        for index, (
            _id,
            ordinal,
            chunk_text,
            document_key,
            title,
            url,
            version_label,
            source_key,
            distance,
        ) in enumerate(rows, start=1)
    ]
    return RetrievalResult(
        chunks, embedded.lines, model.key, model.provider_key, sum(found.values())
    )


def _authorized_sources(product_code: str, requested: tuple[str, ...]) -> list[Any]:
    """The calling product's active sources, narrowed to those requested.

    A requested source the caller does not own - another product's, or none
    at all - is ``KnowledgeNotFound``: one answer for both, so a product
    cannot probe another's source names."""
    rows = KnowledgeSource.objects.filter(
        product_code=product_code, status=SourceStatus.ACTIVE.value
    )
    if requested:
        rows = rows.filter(source_key__in=requested)
    found = {row.source_key: row.id for row in rows}
    if requested and set(requested) - set(found):
        metrics.increment("ai_access_denied_total", reason="source_not_owned")
        raise KnowledgeNotFound("No such knowledge source.")
    return list(found.values())


def _require_indexed(source_ids: list[Any]) -> None:
    """Refuse to spend a query embedding on sources with nothing to search."""
    if not source_ids:
        raise IndexNotReady("There is no indexed knowledge to search.")
    statuses = set(
        KnowledgeDocument.objects.filter(source_id__in=source_ids)
        .exclude(status=DocumentStatus.DELETED.value)
        .values_list("status", flat=True)
        .distinct()
    )
    if DocumentStatus.INDEXED.value not in statuses:
        raise IndexNotReady(
            "No document in the requested sources is indexed yet.",
            pending=DocumentStatus.PENDING.value in statuses,
        )

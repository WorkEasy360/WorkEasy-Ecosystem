"""Indexing one document version: claim, embed, activate - never a transaction
across the network (Tasks 39, 41, 42).

::

    tx 1 (org scope)   lock version -> superseded/terminal/in-flight checks ->
                       status indexing, attempts + 1, lease; timer past lease
    no tx              chunk (deterministic) -> reserve AI spend -> embed
                       through the gateway's provider path -> settle spend
    tx 2 (org scope)   lock document + version -> fenced on (indexing,
                       attempt n) -> newest version still? -> insert chunks,
                       delete every other version's chunks, point the
                       document at this version -> timer removed

**Idempotent under retry and crash** (Task 41). Chunking is deterministic;
chunks are written only in tx 2, under the version's lock and the attempt
fence, and ``(version, ordinal)`` is unique - so a retry, a duplicate
dispatch or a worker that outlived its lease can never produce a second set
of chunks. A worker killed mid-embedding leaves a lapsed lease; the timer
re-dispatches and the next claim starts attempt n+1 (the lost attempt's
spend was settled, or is settled at its reservation by the AI lease timer).

**Version race.** A version whose document has a newer submitted version is
``superseded`` at claim time (no embeddings spent) or at activation (vectors
discarded), so an old version finishing late can never replace a newer one.

**Failure is visible** (Task 42). A provider outage, an open circuit, a
spend limit or the concurrency cap leaves the version ``retrying`` with
backoff (``RAG_RETRY_BASE_S`` doubling to ``RAG_RETRY_CAP_S``); after
``RAG_MAX_INDEX_ATTEMPTS`` it is ``failed``. The document is never reported
``indexed`` because of a version that did not index: if an older version is
serving it keeps serving and the status API reports the document stale.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.errors import (
    AIConcurrencyLimited,
    AIDisabled,
    AINotEntitled,
    AIQuotaExceeded,
)
from workeasy360.contracts.values.backoff import equal_jitter
from workeasy360.intelligence.ai import budget, invocation, policy, registry
from workeasy360.intelligence.ai.domain import Operation
from workeasy360.intelligence.ai.tokens import cost_micros, estimate_tokens
from workeasy360.intelligence.ai.transport import Deadline
from workeasy360.intelligence.rag.chunking import TooManyChunks, chunk_text
from workeasy360.intelligence.rag.ingestion import INDEX_TIMER
from workeasy360.intelligence.rag.models import (
    DocumentStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    VersionStatus,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Claimed:
    version_id: UUID
    document_id: UUID
    source_id: UUID
    product_code: str
    attempt: int
    seq: int
    content: str
    access_tags: list[str]


def run_index(version_pk: Any) -> str:
    """Index one version once. Runs inside ``TenantTask``'s context."""
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.organizations.selectors import organization_status
    from workeasy360.platform.tenancy.context import current_tenant, tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    dispatched = current_tenant()
    organization_id = dispatched.organization_id
    try:
        key = UUID(str(version_pk))
    except ValueError:
        return "missing"
    live = organization_status(organization_id)
    if live is None:
        scheduling.complete(INDEX_TIMER, key)
        return "organization_gone"
    ctx = dispatched.replace_status(live)

    with tenant_context(ctx):
        with organization_db_scope(organization_id):
            claimed = _claim(key, organization_id)
        if isinstance(claimed, str):
            return claimed

        started = time.monotonic()
        outcome = _embed(ctx, claimed)
        with organization_db_scope(organization_id):
            if isinstance(outcome, str):
                return _record_failure(claimed, organization_id, outcome)
            result = _activate(claimed, organization_id, *outcome)
    metrics.observe("rag_indexing_duration_seconds", time.monotonic() - started)
    return result


def _claim(version_id: UUID, organization_id: UUID) -> _Claimed | str:
    from workeasy360.integration.scheduling import services as scheduling

    now = timezone.now()
    with transaction.atomic():
        version = (
            KnowledgeDocumentVersion.objects.select_for_update()
            .filter(id=version_id)
            .first()
        )
        if version is None:
            scheduling.complete(INDEX_TIMER, version_id)
            return "missing"
        if version.status_enum.is_terminal:
            scheduling.complete(INDEX_TIMER, version_id)
            return version.status
        if (
            version.status == VersionStatus.INDEXING.value
            and version.lease_expires_at is not None
            and version.lease_expires_at > now
        ):
            return "in_flight"
        doc = KnowledgeDocument.objects.select_for_update().get(id=version.document_id)
        if doc.latest_seq > version.seq:
            version.status = VersionStatus.SUPERSEDED.value
            version.content = None
            version.save(update_fields=["status", "content", "updated_at"])
            scheduling.complete(INDEX_TIMER, version_id)
            return "superseded"
        if version.attempts >= int(settings.RAG_MAX_INDEX_ATTEMPTS):
            _fail(version, doc, version.last_error or "retries_exhausted")
            scheduling.complete(INDEX_TIMER, version_id)
            return "failed"
        if version.content is None:
            _fail(version, doc, "content_unavailable")
            scheduling.complete(INDEX_TIMER, version_id)
            return "failed"

        version.status = VersionStatus.INDEXING.value
        version.attempts += 1
        version.lease_expires_at = now + timezone.timedelta(
            seconds=int(settings.RAG_INDEX_LEASE_S)
        )
        version.save(
            update_fields=["status", "attempts", "lease_expires_at", "updated_at"]
        )
        scheduling.schedule(
            INDEX_TIMER,
            organization_id=organization_id,
            target_id=version_id,
            due_at=version.lease_expires_at,
        )
        return _Claimed(
            version_id=version.id,
            document_id=doc.id,
            source_id=doc.source_id,
            product_code=doc.product_code,
            attempt=version.attempts,
            seq=version.seq,
            content=version.content,
            access_tags=list(version.access_tags or ()),
        )


def _embed(
    ctx: Any, claimed: _Claimed
) -> tuple[list[str], tuple[tuple[float, ...], ...], str] | str:
    """Chunk, reserve, embed, settle. Returns (chunks, vectors, model key) or
    a failure code. No database transaction is open while the provider is
    called."""
    try:
        chunks = chunk_text(
            claimed.content,
            size=int(settings.RAG_CHUNK_CHARS),
            overlap=int(settings.RAG_CHUNK_OVERLAP_CHARS),
            max_chunks=int(settings.RAG_MAX_CHUNKS_PER_DOCUMENT),
        )
    except TooManyChunks:
        return "fatal:too_many_chunks"
    if not chunks:
        return "fatal:empty"

    try:
        grant = policy.authorize(ctx.organization_id, claimed.product_code)
    except (AINotEntitled, AIDisabled) as exc:
        return f"retry:{exc.code}"
    model = registry.select_embedding(allowed_providers=grant.allowed_providers)
    if model is None or model.price is None:
        return "retry:embedding_unavailable"

    tokens = sum(estimate_tokens(c) for c in chunks)
    estimate = cost_micros(
        input_tokens=tokens,
        output_tokens=0,
        input_micros_per_mtok=model.price.input_micros_per_mtok,
        output_micros_per_mtok=0,
    )
    try:
        reserved = budget.reserve(
            grant,
            client_id="",
            operation=Operation.INDEX.value,
            mode="sync",
            estimate_micros=estimate,
        )
    except (AIQuotaExceeded, AIConcurrencyLimited) as exc:
        return f"retry:{exc.code}"

    execution_id = reserved.execution.id
    budget.mark_running(grant.ctx, execution_id)
    outcome = invocation.embed(
        model,
        chunks,
        deadline=Deadline(float(settings.AI_EMBEDDING_DEADLINE_S)),
        request_id=execution_id,
    )
    budget.settle(
        grant.ctx,
        execution_id,
        succeeded=outcome.vectors is not None,
        outcome="ok" if outcome.vectors is not None else outcome.failure,
        lines=outcome.lines,
        model_key=model.key,
        provider_key=model.provider_key,
    )
    if outcome.vectors is None:
        metrics.increment("rag_embedding_failures_total", category=outcome.failure)
        return f"retry:{outcome.failure}"
    return chunks, outcome.vectors, model.key


def _activate(
    claimed: _Claimed,
    organization_id: UUID,
    chunks: list[str],
    vectors: tuple[tuple[float, ...], ...],
    model_key: str,
) -> str:
    from workeasy360.integration.scheduling import services as scheduling

    now = timezone.now()
    with transaction.atomic():
        doc = KnowledgeDocument.objects.select_for_update().get(id=claimed.document_id)
        version = KnowledgeDocumentVersion.objects.select_for_update().get(
            id=claimed.version_id
        )
        if (
            version.status != VersionStatus.INDEXING.value
            or version.attempts != claimed.attempt
        ):
            # A newer attempt owns this version now; this result is stale.
            metrics.increment("rag_late_completions_total")
            return "stale_attempt"
        if doc.status == DocumentStatus.DELETED.value:
            version.status = VersionStatus.DISCARDED.value
            version.content = None
            version.save(update_fields=["status", "content", "updated_at"])
            scheduling.complete(INDEX_TIMER, version.id)
            return "discarded"
        if doc.latest_seq > version.seq:
            version.status = VersionStatus.SUPERSEDED.value
            version.content = None
            version.save(update_fields=["status", "content", "updated_at"])
            scheduling.complete(INDEX_TIMER, version.id)
            return "superseded"

        # The swap: every other version's chunks go, this version's arrive,
        # and the document points here - one transaction, so retrieval sees
        # the old version or the new one, never both and never neither.
        KnowledgeChunk.objects.filter(document_id=doc.id).exclude(
            version_id=version.id
        ).delete()
        KnowledgeChunk.objects.filter(version_id=version.id).delete()
        KnowledgeChunk.objects.bulk_create(
            [
                KnowledgeChunk(
                    document_id=doc.id,
                    version_id=version.id,
                    source_id=doc.source_id,
                    product_code=doc.product_code,
                    access_tags=version.access_tags,
                    ordinal=ordinal,
                    text=text,
                    token_estimate=estimate_tokens(text),
                    embedding=vector,
                    embedding_model=model_key,
                    embedding_dimensions=len(vector),
                )
                for ordinal, (text, vector) in enumerate(
                    zip(chunks, vectors, strict=True)
                )
            ]
        )
        doc.status = DocumentStatus.INDEXED.value
        doc.served_version_id = version.id
        doc.served_seq = version.seq
        doc.served_version_label = version.version_label
        doc.served_embedding_model = model_key
        doc.title = version.title
        doc.url = version.url
        doc.access_tags = version.access_tags
        doc.last_indexed_at = now
        doc.last_error = ""
        doc.save()
        version.status = VersionStatus.INDEXED.value
        version.content = None
        version.chunk_count = len(chunks)
        version.embedding_model = model_key
        version.indexed_at = now
        version.lease_expires_at = None
        version.last_error = ""
        version.save()
        scheduling.complete(INDEX_TIMER, version.id)
    metrics.increment("rag_versions_indexed_total")
    metrics.increment("rag_chunks_indexed_total", float(len(chunks)))
    logger.info(
        "rag.version.indexed",
        version_id=str(version.id),
        chunks=len(chunks),
        model=model_key,
        attempt=claimed.attempt,
    )
    return "indexed"


def _record_failure(claimed: _Claimed, organization_id: UUID, failure: str) -> str:
    from workeasy360.integration.scheduling import services as scheduling

    fatal = failure.startswith("fatal:")
    code = failure.split(":", 1)[1][:64]
    now = timezone.now()
    with transaction.atomic():
        doc = KnowledgeDocument.objects.select_for_update().get(id=claimed.document_id)
        version = KnowledgeDocumentVersion.objects.select_for_update().get(
            id=claimed.version_id
        )
        if (
            version.status != VersionStatus.INDEXING.value
            or version.attempts != claimed.attempt
        ):
            metrics.increment("rag_late_completions_total")
            return "stale_attempt"
        if fatal or version.attempts >= int(settings.RAG_MAX_INDEX_ATTEMPTS):
            _fail(version, doc, code)
            scheduling.complete(INDEX_TIMER, version.id)
            logger.warning("rag.version.failed", version_id=str(version.id), reason=code)
            return "failed"
        delay = equal_jitter(
            version.attempts - 1,
            base=float(settings.RAG_RETRY_BASE_S),
            cap=float(settings.RAG_RETRY_CAP_S),
        )
        version.status = VersionStatus.RETRYING.value
        version.last_error = code
        version.lease_expires_at = None
        version.save(
            update_fields=["status", "last_error", "lease_expires_at", "updated_at"]
        )
        doc.last_error = code
        doc.save(update_fields=["last_error", "updated_at"])
        scheduling.schedule(
            INDEX_TIMER,
            organization_id=organization_id,
            target_id=version.id,
            due_at=now + timezone.timedelta(seconds=delay),
        )
    metrics.increment("rag_index_retries_total", reason=code)
    logger.warning(
        "rag.version.retrying",
        version_id=str(claimed.version_id),
        reason=code,
        delay_s=delay,
    )
    return "retrying"


def _fail(version: KnowledgeDocumentVersion, doc: KnowledgeDocument, code: str) -> None:
    version.status = VersionStatus.FAILED.value
    version.last_error = code[:64]
    version.content = None
    version.lease_expires_at = None
    version.save()
    doc.last_error = code[:64]
    if doc.served_version_id is None and doc.status != DocumentStatus.DELETED.value:
        doc.status = DocumentStatus.FAILED.value
    doc.save()
    metrics.increment("rag_versions_failed_total", reason=code[:64])

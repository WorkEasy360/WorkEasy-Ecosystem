"""The ingestion contract: how a product puts its content into the index
(Tasks 22-25, 37, 38).

A product sends, over its Phase 3 credential, for one organization:

.. code-block:: json

    {"source": "help_center", "document_id": "article-42", "version": "7",
     "title": "Returns policy", "url": "https://help.example.com/42",
     "access_tags": ["team:support"], "content": "..."}

The platform never reaches into a product's database (Task 23): what is
indexed is exactly what the product submitted. Ownership comes from the
credential - the ``source`` is always *the calling product's* source of that
name, so two products may use the same source name without ever sharing it,
and no field lets a product write into another's.

**Idempotent** (Task 24): ``(organization, product, source, document_id,
version)`` identifies a submission. The same version resubmitted with the same
content is ``duplicate`` and changes nothing; with different content it is a
409 - one version identifier cannot stand for two contents. Decided by a
unique constraint under the document's row lock, so concurrent submissions of
one version produce one version row and at most one set of chunks.

**Versioned** (Task 25): a new version is queued for indexing and the old one
keeps serving until the new one is indexed; activation replaces the old
chunks in one transaction. A version that arrives after a newer one never
serves (it is ``superseded``).

**Deletion and revocation** (Task 37): deleting a document or revoking a
source removes the serving pointer and purges the chunks in the same
transaction (revocation purges in bounded batches behind a timer; nothing in
a revoked source is retrievable from the moment it commits).

**Secrets never enter the index** (Task 44): content and title pass the
forbidden-secret filter before they are stored, chunked or embedded.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.errors import (
    AIInvalidRequest,
    KnowledgeConflict,
    KnowledgeNotFound,
)
from workeasy360.contracts.values import intelligence_keys as keys
from workeasy360.intelligence.ai import policy
from workeasy360.intelligence.ai.safety import redact
from workeasy360.intelligence.rag.chunking import TooManyChunks, chunk_text
from workeasy360.intelligence.rag.models import (
    DocumentStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeSource,
    SourceStatus,
    VersionStatus,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

INDEX_TIMER: Final = "rag.index"
PURGE_TIMER: Final = "rag.purge"

_FIELDS: Final[frozenset[str]] = frozenset(
    {"source", "document_id", "version", "title", "url", "access_tags", "content"}
)
_TAG_CHARS: Final = frozenset("abcdefghijklmnopqrstuvwxyz0123456789:_-.")
MAX_DOCUMENT_KEY: Final = 200
MAX_VERSION_LABEL: Final = 64


def submit(client: Any, *, organization_id: Any, raw: Any) -> dict[str, Any]:
    """Accept one document version for indexing, or refuse."""
    from workeasy360.platform.ecosystem import ratelimit

    ratelimit.consume("rag_ingest", client.client_id, endpoint="rag_ingest")
    organization = _uuid(organization_id)
    document = parse_document(raw)
    ratelimit.consume(
        "rag_ingest_org", f"{client.client_id}:{organization}", endpoint="rag_ingest"
    )

    # Indexing spends AI budget, so it needs the same decision as any AI
    # call: entitled product, AI granted, organization accepting work.
    grant = policy.authorize(organization, client.product_code)

    content, found = redact(document["content"])
    title, found_in_title = redact(document["title"])
    redactions = sum(found.values()) + sum(found_in_title.values())
    try:
        chunk_text(
            content,
            size=int(settings.RAG_CHUNK_CHARS),
            overlap=int(settings.RAG_CHUNK_OVERLAP_CHARS),
            max_chunks=int(settings.RAG_MAX_CHUNKS_PER_DOCUMENT),
        )
    except TooManyChunks:
        raise AIInvalidRequest(
            f"The content would need more than {settings.RAG_MAX_CHUNKS_PER_DOCUMENT} "
            "chunks. Split it into several documents.",
            field="content",
        ) from None
    digest = hashlib.sha256(
        json.dumps(
            {
                "content": content,
                "title": title,
                "url": document["url"],
                "access_tags": document["access_tags"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    result = _record(grant, client.product_code, document, content, title, digest)
    metrics.increment("rag_documents_submitted_total", result=result["status"])
    if redactions:
        metrics.increment("ai_redactions_total", float(redactions), where="ingestion")
    logger.info(
        "rag.document.submitted",
        product=client.product_code,
        result=result["status"],
        chars=len(content),
        redactions=redactions,
    )
    return result


def delete_document(
    client: Any, *, organization_id: Any, source: Any, document_id: Any
) -> dict[str, Any]:
    """Stop a document being retrievable and purge its chunks, now."""
    ctx = _context(_uuid(organization_id))
    source_key = _source_key(source)
    document_key = _document_key(document_id)
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with (
        tenant_context(ctx),
        organization_db_scope(ctx.organization_id),
        transaction.atomic(),
    ):
        doc = (
            KnowledgeDocument.objects.select_for_update()
            .filter(
                product_code=client.product_code,
                source__source_key=source_key,
                document_key=document_key,
            )
            .first()
        )
        if doc is None or doc.status == DocumentStatus.DELETED.value:
            raise KnowledgeNotFound("No such document.")
        purged, _ = KnowledgeChunk.objects.filter(document_id=doc.id).delete()
        pending = list(
            KnowledgeDocumentVersion.objects.filter(document_id=doc.id)
            .exclude(status__in=[v.value for v in VersionStatus if v.is_terminal])
            .values_list("id", flat=True)
        )
        KnowledgeDocumentVersion.objects.filter(document_id=doc.id).update(content=None)
        KnowledgeDocumentVersion.objects.filter(id__in=pending).update(
            status=VersionStatus.DISCARDED.value
        )
        for version_id in pending:
            scheduling.complete(INDEX_TIMER, version_id)
        _unserve(doc, DocumentStatus.DELETED)
    metrics.increment("rag_documents_deleted_total")
    logger.info("rag.document.deleted", product=client.product_code, chunks=purged)
    return {"status": "deleted", "document_id": document_key, "chunks_purged": purged}


def revoke_source(client: Any, *, organization_id: Any, source: Any) -> dict[str, Any]:
    """Revoke a whole source: nothing in it is retrievable from commit; its
    chunks are purged in bounded batches behind a timer."""
    ctx = _context(_uuid(organization_id))
    source_key = _source_key(source)
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    now = timezone.now()
    with (
        tenant_context(ctx),
        organization_db_scope(ctx.organization_id),
        transaction.atomic(),
    ):
        row = (
            KnowledgeSource.objects.select_for_update()
            .filter(product_code=client.product_code, source_key=source_key)
            .first()
        )
        if row is None or row.status == SourceStatus.REVOKED.value:
            raise KnowledgeNotFound("No such source.")
        row.status = SourceStatus.REVOKED.value
        row.revoked_at = now
        row.save(update_fields=["status", "revoked_at", "updated_at"])
        documents = KnowledgeDocument.objects.filter(source_id=row.id).exclude(
            status=DocumentStatus.DELETED.value
        )
        count = documents.update(
            status=DocumentStatus.DELETED.value,
            served_version_id=None,
            served_seq=None,
            deleted_at=now,
            updated_at=now,
        )
        KnowledgeDocumentVersion.objects.filter(
            document__source_id=row.id,
            status__in=[
                VersionStatus.PENDING.value,
                VersionStatus.RETRYING.value,
                VersionStatus.INDEXING.value,
            ],
        ).update(status=VersionStatus.DISCARDED.value, content=None)
        scheduling.schedule(
            PURGE_TIMER, organization_id=ctx.organization_id, target_id=row.id, due_at=now
        )
    metrics.increment("rag_sources_revoked_total")
    logger.info("rag.source.revoked", product=client.product_code, documents=count)
    return {"status": "revoked", "source": source_key, "documents": count}


def purge_source(source_id: UUID) -> str:
    """One bounded batch of a revoked source's chunks (the purge timer's work).
    Runs in the organization's scope (the task enters it)."""
    from workeasy360.integration.scheduling import services as scheduling

    with transaction.atomic():
        row = KnowledgeSource.objects.filter(id=source_id).first()
        if row is None or row.status != SourceStatus.REVOKED.value:
            scheduling.complete(PURGE_TIMER, source_id)
            return "nothing"
        batch = list(
            KnowledgeChunk.objects.filter(source_id=source_id).values_list(
                "id", flat=True
            )[: int(settings.RAG_PURGE_BATCH)]
        )
        KnowledgeChunk.objects.filter(id__in=batch).delete()
        KnowledgeDocumentVersion.objects.filter(document__source_id=source_id).update(
            content=None
        )
        if len(batch) < int(settings.RAG_PURGE_BATCH):
            scheduling.complete(PURGE_TIMER, source_id)
            return "done"
        scheduling.schedule(
            PURGE_TIMER,
            organization_id=row.organization_id,
            target_id=source_id,
            due_at=timezone.now(),
        )
    return "more"


def status(
    client: Any, *, organization_id: Any, source: Any, document_id: Any
) -> dict[str, Any]:
    """Where one document is: submitted, indexing, serving, stale or failed
    (Task 38). Never reports indexed when the last attempt failed."""
    from workeasy360.intelligence.ai import registry
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    ctx = _context(_uuid(organization_id))
    source_key = _source_key(source)
    document_key = _document_key(document_id)
    with tenant_context(ctx), organization_db_scope(ctx.organization_id):
        doc = KnowledgeDocument.objects.filter(
            product_code=client.product_code,
            source__source_key=source_key,
            document_key=document_key,
        ).first()
        if doc is None:
            raise KnowledgeNotFound("No such document.")
        latest = (
            KnowledgeDocumentVersion.objects.filter(document_id=doc.id)
            .order_by("-seq")
            .values("version_label", "status", "last_error", "attempts")
            .first()
        )
    current_model = registry.current_embedding_model_key()
    serving = doc.served_version_id is not None
    stale = bool(
        serving
        and (
            doc.served_seq != doc.latest_seq
            or (current_model and doc.served_embedding_model != current_model)
        )
    )
    return {
        "source": source_key,
        "document_id": document_key,
        "status": doc.status,
        "latest_version": doc.latest_version_label,
        "latest_version_status": latest["status"] if latest else None,
        "indexed_version": doc.served_version_label or None,
        "stale": stale,
        "last_indexed_at": doc.last_indexed_at.isoformat()
        if doc.last_indexed_at
        else None,
        "last_error": doc.last_error or None,
    }


# --- internals -----------------------------------------------------------------------


def parse_document(raw: Any) -> dict[str, Any]:
    limit = int(settings.RAG_MAX_DOCUMENT_CHARS)
    if not isinstance(raw, str) or not raw:
        raise AIInvalidRequest(
            "A 'document' field holding JSON is required.", field="document"
        )
    if len(raw) > limit * 2 + 16_384:
        raise AIInvalidRequest("The document is too large.", field="document")
    try:
        parsed = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise AIInvalidRequest(
            "The document is not valid JSON.", field="document"
        ) from None
    if not isinstance(parsed, dict):
        raise AIInvalidRequest("The document must be a JSON object.", field="document")
    unknown = sorted(set(parsed) - _FIELDS)
    if unknown:
        raise AIInvalidRequest(
            f"Unknown field(s): {', '.join(unknown)}.", field=unknown[0]
        )

    content = parsed.get("content")
    if not isinstance(content, str) or not content.strip() or len(content) > limit:
        raise AIInvalidRequest(
            f"content must be 1-{limit} characters of text.", field="content"
        )
    version = parsed.get("version")
    if isinstance(version, int) and not isinstance(version, bool):
        version = str(version)
    if (
        not isinstance(version, str)
        or not 0 < len(version) <= MAX_VERSION_LABEL
        or not version.isprintable()
    ):
        raise AIInvalidRequest(
            f"version must be 1-{MAX_VERSION_LABEL} printable characters.",
            field="version",
        )
    title = parsed.get("title", "") or ""
    if not isinstance(title, str) or len(title) > 200 or not title.isprintable():
        raise AIInvalidRequest(
            "title must be at most 200 printable characters.", field="title"
        )
    return {
        "source": _source_key(parsed.get("source")),
        "document_id": _document_key(parsed.get("document_id")),
        "version": version,
        "title": title,
        "url": _url(parsed.get("url", "") or ""),
        "access_tags": _tags(parsed.get("access_tags", []) or []),
        "content": content,
    }


def _record(
    grant: Any,
    product_code: str,
    document: dict[str, Any],
    content: str,
    title: str,
    digest: str,
) -> dict[str, Any]:
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    now = timezone.now()
    with tenant_context(grant.ctx), organization_db_scope(grant.organization_id):
        with transaction.atomic():
            source = _source(product_code, document["source"])
            doc = _document(source, product_code, document["document_id"])
            existing = KnowledgeDocumentVersion.objects.filter(
                document_id=doc.id, version_label=document["version"]
            ).first()
            if existing is not None:
                return _resubmitted(existing, doc, digest, document, grant, now)

            doc.latest_seq += 1
            doc.latest_version_label = document["version"]
            if doc.status in (DocumentStatus.DELETED.value, DocumentStatus.FAILED.value):
                doc.status = DocumentStatus.PENDING.value
                doc.deleted_at = None
            doc.save()
            # Anything older still waiting will never serve: a newer version
            # exists. Mark it now so no worker spends embeddings on it.
            KnowledgeDocumentVersion.objects.filter(
                document_id=doc.id,
                status__in=[VersionStatus.PENDING.value, VersionStatus.RETRYING.value],
            ).update(status=VersionStatus.SUPERSEDED.value, content=None)
            version = KnowledgeDocumentVersion.objects.create(
                document_id=doc.id,
                version_label=document["version"],
                seq=doc.latest_seq,
                content_hash=digest,
                content=content,
                content_chars=len(content),
                title=title,
                url=document["url"],
                access_tags=document["access_tags"],
            )
            scheduling.schedule(
                INDEX_TIMER,
                organization_id=grant.organization_id,
                target_id=version.id,
                due_at=now
                + timezone.timedelta(seconds=float(settings.WEBHOOK_DISPATCH_GRACE_S)),
            )
            _enqueue(grant.ctx, version.id)
    return {
        "status": "accepted",
        "source": document["source"],
        "document_id": document["document_id"],
        "version": document["version"],
        "document_status": doc.status,
    }


def _resubmitted(
    existing: KnowledgeDocumentVersion,
    doc: KnowledgeDocument,
    digest: str,
    document: dict[str, Any],
    grant: Any,
    now: Any,
) -> dict[str, Any]:
    from workeasy360.integration.scheduling import services as scheduling

    if existing.content_hash != digest:
        raise KnowledgeConflict(
            "This document version was already submitted with different content. "
            "Submit a new version."
        )
    status = "duplicate"
    if existing.status == VersionStatus.FAILED.value and existing.seq == doc.latest_seq:
        # An explicit resubmission of a failed version is the product asking
        # for another try - but only with the content we no longer hold.
        raise KnowledgeConflict(
            "This version failed to index and its content is no longer held. "
            "Submit it again as a new version."
        )
    if existing.status == VersionStatus.RETRYING.value:
        scheduling.schedule(
            INDEX_TIMER,
            organization_id=grant.organization_id,
            target_id=existing.id,
            due_at=now,
        )
    return {
        "status": status,
        "source": document["source"],
        "document_id": document["document_id"],
        "version": document["version"],
        "document_status": doc.status,
    }


def _source(product_code: str, source_key: str) -> KnowledgeSource:
    try:
        with transaction.atomic():
            source, _ = KnowledgeSource.objects.get_or_create(
                product_code=product_code, source_key=source_key
            )
    except IntegrityError:
        source = KnowledgeSource.objects.get(
            product_code=product_code, source_key=source_key
        )
    if source.status == SourceStatus.REVOKED.value:
        source.status = SourceStatus.ACTIVE.value
        source.revoked_at = None
        source.save(update_fields=["status", "revoked_at", "updated_at"])
    return source


def _document(
    source: KnowledgeSource, product_code: str, document_key: str
) -> KnowledgeDocument:
    doc = (
        KnowledgeDocument.objects.select_for_update()
        .filter(source_id=source.id, document_key=document_key)
        .first()
    )
    if doc is not None:
        return doc
    if KnowledgeDocument.objects.exclude(
        status=DocumentStatus.DELETED.value
    ).count() >= int(settings.RAG_MAX_DOCUMENTS_PER_ORGANIZATION):
        raise KnowledgeConflict("The organization's knowledge document limit is reached.")
    try:
        with transaction.atomic():
            KnowledgeDocument.objects.create(
                source_id=source.id, product_code=product_code, document_key=document_key
            )
    except IntegrityError:
        pass
    return (
        KnowledgeDocument.objects.select_for_update()
        .filter(source_id=source.id, document_key=document_key)
        .get()
    )


def _unserve(doc: KnowledgeDocument, status: DocumentStatus) -> None:
    doc.status = status.value
    doc.served_version_id = None
    doc.served_seq = None
    doc.served_version_label = ""
    doc.deleted_at = timezone.now() if status is DocumentStatus.DELETED else None
    doc.save()


def _enqueue(ctx: Any, version_id: UUID) -> None:
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.intelligence.rag import tasks

    scheduling.enqueue_after_commit(tasks.index_version, [version_id], ctx=ctx)


def _context(organization_id: UUID) -> Any:
    """The organization's routing context, or not-found. Deletion, revocation
    and status need no entitlement - a product may always remove or inspect
    its own content - but they are always scoped to the calling product."""
    from workeasy360.integration.scheduling.services import routing_context

    ctx = routing_context(organization_id)
    if ctx is None:
        raise KnowledgeNotFound("No such document.")
    return ctx


def _uuid(value: Any) -> UUID:
    try:
        return UUID(str(value)) if isinstance(value, str | UUID) else _bad_org()
    except ValueError:
        return _bad_org()


def _bad_org() -> UUID:
    raise AIInvalidRequest("organization_id must be a UUID.", field="organization_id")


def _source_key(value: Any) -> str:
    if not isinstance(value, str) or not keys.is_slug(value):
        raise AIInvalidRequest(
            "source must be a short lowercase identifier (a-z, 0-9, _).", field="source"
        )
    return value


def _document_key(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= MAX_DOCUMENT_KEY
        or not value.isprintable()
    ):
        raise AIInvalidRequest(
            f"document_id must be 1-{MAX_DOCUMENT_KEY} printable characters.",
            field="document_id",
        )
    return value


def _url(value: Any) -> str:
    """A citation link. Only https, no credentials, no script schemes: it is
    rendered to people, and the model never supplies it (Task 106)."""
    if value == "":
        return ""
    if not isinstance(value, str) or len(value) > 512 or not value.isprintable():
        raise AIInvalidRequest("url must be an https URL.", field="url")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise AIInvalidRequest("url must be an https URL.", field="url") from None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise AIInvalidRequest("url must be an https URL.", field="url")
    if any(ch.isspace() for ch in value):
        raise AIInvalidRequest("url must be an https URL.", field="url")
    return value


def _tags(value: Any) -> list[str]:
    limit = int(settings.RAG_MAX_ACCESS_TAGS)
    if (
        not isinstance(value, list)
        or len(value) > limit
        or not all(
            isinstance(t, str) and 0 < len(t) <= 64 and set(t) <= _TAG_CHARS
            for t in value
        )
    ):
        raise AIInvalidRequest(
            f"access_tags must list at most {limit} tags of a-z, 0-9, ':', '_', '-', '.'.",
            field="access_tags",
        )
    return sorted(set(value))


def _reject_constant(token: str) -> Any:
    raise ValueError(f"{token} is not valid JSON.")

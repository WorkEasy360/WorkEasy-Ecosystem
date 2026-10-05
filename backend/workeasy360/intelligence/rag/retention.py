"""RAG retention (Tasks 37, 83, 84), per organization, in bounded batches.

Chunks and embeddings never outlive their document's serving version: a new
version's activation deletes the old chunks, and deletion or revocation
purges them at once (behind a timer for a large source). What remains to
prune is bookkeeping: document tombstones, finished non-serving versions,
and revoked sources that no longer hold anything.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import Exists, OuterRef
from django.utils import timezone

from workeasy360.intelligence.rag.models import (
    DocumentStatus,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeSource,
    SourceStatus,
    VersionStatus,
)


def prune_organization() -> dict[str, int]:
    """Runs inside one organization's scope (the caller enters it)."""
    batch = int(settings.AI_PRUNE_BATCH)
    cutoff = timezone.now() - timezone.timedelta(
        days=int(settings.RAG_TOMBSTONE_RETENTION_DAYS)
    )

    tombstones = list(
        KnowledgeDocument.objects.filter(
            status=DocumentStatus.DELETED.value, deleted_at__lt=cutoff
        ).values_list("id", flat=True)[:batch]
    )
    KnowledgeDocument.objects.filter(id__in=tombstones).delete()

    serving = KnowledgeDocument.objects.filter(served_version_id=OuterRef("id"))
    finished = list(
        KnowledgeDocumentVersion.objects.filter(
            status__in=[
                VersionStatus.INDEXED.value,
                VersionStatus.FAILED.value,
                VersionStatus.SUPERSEDED.value,
                VersionStatus.DISCARDED.value,
            ],
            updated_at__lt=cutoff,
        )
        .exclude(Exists(serving))
        .values_list("id", flat=True)[:batch]
    )
    KnowledgeDocumentVersion.objects.filter(id__in=finished).delete()

    documents = KnowledgeDocument.objects.filter(source_id=OuterRef("id"))
    sources, _ = (
        KnowledgeSource.objects.filter(
            status=SourceStatus.REVOKED.value, revoked_at__lt=cutoff
        )
        .exclude(Exists(documents))
        .delete()
    )
    return {
        "rag_documents": len(tombstones),
        "rag_versions": len(finished),
        "rag_sources": sources,
    }

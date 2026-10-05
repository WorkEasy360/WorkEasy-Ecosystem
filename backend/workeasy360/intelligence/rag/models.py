"""Retrieval-augmented generation: what is indexed, for whom, and at which version.

Every table is tenant data (RLS, fail-closed manager, composite keys), and
every row is bound to the four things Task 22 requires: the **organization**,
the **source product** (``product_code`` - the authenticated product that
submitted it, never a request field), the product's own **source identifier**
(``source_key`` / ``document_key``) with its **authorization metadata**
(``access_tags``), and a **version**. Nothing here names a CRM, Books or any
other product's field: a document is text plus opaque identifiers (Task 21).

::

    KnowledgeSource        one product's named collection in one organization
      KnowledgeDocument    a product-identified document; which version serves
        KnowledgeDocumentVersion
                           one submitted version: its content until indexed,
                           its lifecycle and its attempts
        KnowledgeChunk     a bounded passage of the *serving* version, with its
                           embedding (pgvector), model and dimensions

**Which chunks are retrievable** is a property of the database, not a filter
applied after ranking (Task 33): a chunk is searched only when its document
is ``indexed`` and names that chunk's version as the one serving, its source
is ``active``, and it belongs to the organization (RLS) and the calling
product. Activating a new version replaces the old version's chunks in the
same transaction, and deleting a document or revoking a source removes the
serving pointer at once, so stale or deleted content stops being retrievable
the moment the change commits (Tasks 25, 37).
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models

from workeasy360.contracts.enums import StrEnum
from workeasy360.intelligence.rag.fields import VectorField
from workeasy360.platform.tenancy.models import TenantScopedModel


def _choices(enum_cls: type[StrEnum]) -> list[tuple[str, str]]:
    return [(m.value, m.name.replace("_", " ").title()) for m in enum_cls]


class SourceStatus(StrEnum):
    ACTIVE = "active"
    #: Revoked by its product: nothing in it is retrievable, and its chunks
    #: are purged. Submitting to it again reactivates it.
    REVOKED = "revoked"


class DocumentStatus(StrEnum):
    #: Accepted; no version has been indexed yet.
    PENDING = "pending"
    #: A version is serving. A newer submitted version may still be pending -
    #: the document is then *stale*, which the status API says.
    INDEXED = "indexed"
    #: No version could be indexed. Never reported as indexed (Task 42).
    FAILED = "failed"
    DELETED = "deleted"


class VersionStatus(StrEnum):
    PENDING = "pending"
    INDEXING = "indexing"
    INDEXED = "indexed"
    #: Retrying after a transient failure (a provider outage, a spend limit).
    RETRYING = "retrying"
    FAILED = "failed"
    #: A newer version of the document arrived before this one served.
    SUPERSEDED = "superseded"
    #: The document was deleted before this version served.
    DISCARDED = "discarded"

    @property
    def is_terminal(self) -> bool:
        return self in (
            VersionStatus.INDEXED,
            VersionStatus.FAILED,
            VersionStatus.SUPERSEDED,
            VersionStatus.DISCARDED,
        )


class KnowledgeSource(TenantScopedModel):
    product_code = models.CharField(max_length=32)
    source_key = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=_choices(SourceStatus), default=SourceStatus.ACTIVE.value
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    #: Exempt from the R-10 suspension write block (ADR-031): the only path
    #: that adds content - ingestion - is refused for an organization that is
    #: not writable by the AI access decision, before anything is stored. The
    #: exemption is what lets deletion, source revocation and purging - privacy
    #: obligations - complete while an organization is suspended.
    tenancy_write_block_exempt = True

    class Meta:
        db_table = "rag_source"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "product_code", "source_key"],
                name="uq_rag_source_key",
            ),
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_rag_source_id_org"
            ),
        ]


class KnowledgeDocument(TenantScopedModel):
    source = models.ForeignKey(
        KnowledgeSource, on_delete=models.CASCADE, related_name="+"
    )
    product_code = models.CharField(max_length=32)
    document_key = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16,
        choices=_choices(DocumentStatus),
        default=DocumentStatus.PENDING.value,
    )
    #: Assigned under this row's lock; orders submitted versions.
    latest_seq = models.PositiveIntegerField(default=0)
    latest_version_label = models.CharField(max_length=64, blank=True, default="")
    # The serving version. A plain id rather than a foreign key: the version
    # table already points here, and the pointer is only ever set by the
    # activation transaction from a version it has just locked.
    served_version_id = models.UUIDField(null=True, blank=True)
    served_seq = models.PositiveIntegerField(null=True, blank=True)
    served_version_label = models.CharField(max_length=64, blank=True, default="")
    served_embedding_model = models.CharField(max_length=64, blank=True, default="")
    # The serving version's metadata, copied at activation so retrieval can
    # cite it without a further join.
    title = models.CharField(max_length=200, blank=True, default="")
    url = models.CharField(max_length=512, blank=True, default="")
    access_tags = ArrayField(models.CharField(max_length=64), default=list, blank=True)
    last_indexed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=64, blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)

    #: Exempt from the R-10 suspension write block (ADR-031): the only path
    #: that adds content - ingestion - is refused for an organization that is
    #: not writable by the AI access decision, before anything is stored. The
    #: exemption is what lets deletion, source revocation and purging - privacy
    #: obligations - complete while an organization is suspended.
    tenancy_write_block_exempt = True

    class Meta:
        db_table = "rag_document"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "source", "document_key"],
                name="uq_rag_document_key",
            ),
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_rag_document_id_org"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="ix_rag_doc_org_status"),
            models.Index(
                fields=["organization", "deleted_at"],
                name="ix_rag_doc_tombstones",
                condition=models.Q(deleted_at__isnull=False),
            ),
        ]

    @property
    def status_enum(self) -> DocumentStatus:
        return DocumentStatus(self.status)


class KnowledgeDocumentVersion(TenantScopedModel):
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="+"
    )
    #: The product's own version identifier - opaque, compared for equality.
    version_label = models.CharField(max_length=64)
    seq = models.PositiveIntegerField()
    #: SHA-256 of the submitted (redacted) content and metadata: the same
    #: version resubmitted is a duplicate; different content is a conflict.
    content_hash = models.CharField(max_length=64)
    #: Held only until the version is indexed, superseded or discarded.
    content = models.TextField(null=True, blank=True)  # noqa: DJ001 - NULL = no longer held
    content_chars = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200, blank=True, default="")
    url = models.CharField(max_length=512, blank=True, default="")
    access_tags = ArrayField(models.CharField(max_length=64), default=list, blank=True)
    status = models.CharField(
        max_length=16,
        choices=_choices(VersionStatus),
        default=VersionStatus.PENDING.value,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=64, blank=True, default="")
    chunk_count = models.PositiveIntegerField(default=0)
    embedding_model = models.CharField(max_length=64, blank=True, default="")
    indexed_at = models.DateTimeField(null=True, blank=True)

    #: Exempt from the R-10 suspension write block (ADR-031): the only path
    #: that adds content - ingestion - is refused for an organization that is
    #: not writable by the AI access decision, before anything is stored. The
    #: exemption is what lets deletion, source revocation and purging - privacy
    #: obligations - complete while an organization is suspended.
    tenancy_write_block_exempt = True

    class Meta:
        db_table = "rag_document_version"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "document", "version_label"],
                name="uq_rag_version_label",
            ),
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_rag_version_id_org"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "status"], name="ix_rag_version_org_status"
            ),
            models.Index(
                fields=["organization", "document"], name="ix_rag_version_org_doc"
            ),
        ]

    @property
    def status_enum(self) -> VersionStatus:
        return VersionStatus(self.status)


class KnowledgeChunk(TenantScopedModel):
    """A passage of a serving version, with its embedding. Immutable."""

    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="+"
    )
    version = models.ForeignKey(
        KnowledgeDocumentVersion, on_delete=models.CASCADE, related_name="+"
    )
    #: Denormalised from the document for the retrieval pre-filter; the
    #: composite document key keeps it honest (same organization).
    source_id = models.UUIDField()
    product_code = models.CharField(max_length=32)
    access_tags = ArrayField(models.CharField(max_length=64), default=list, blank=True)
    ordinal = models.PositiveIntegerField()
    text = models.TextField()
    token_estimate = models.PositiveIntegerField()
    embedding = VectorField()
    embedding_model = models.CharField(max_length=64)
    embedding_dimensions = models.PositiveIntegerField()

    #: Exempt from the R-10 suspension write block (ADR-031): the only path
    #: that adds content - ingestion - is refused for an organization that is
    #: not writable by the AI access decision, before anything is stored. The
    #: exemption is what lets deletion, source revocation and purging - privacy
    #: obligations - complete while an organization is suspended.
    tenancy_write_block_exempt = True

    class Meta:
        db_table = "rag_chunk"
        constraints = [
            models.UniqueConstraint(
                fields=["version", "ordinal"], name="uq_rag_chunk_ordinal"
            )
        ]
        indexes = [
            # The retrieval pre-filter: organization, product, source - the
            # authorized candidate set, found before any distance is computed.
            models.Index(
                fields=["organization", "product_code", "source_id"],
                name="ix_rag_chunk_scope",
            ),
            models.Index(
                fields=["organization", "document"], name="ix_rag_chunk_org_doc"
            ),
        ]

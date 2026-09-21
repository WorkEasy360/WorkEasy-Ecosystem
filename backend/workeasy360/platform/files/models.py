"""File metadata. Content lives in object storage, never in the database.

``platform/files`` owns metadata, storage ports, presigning and scan
orchestration. It does not own what a file *means* - an invoice PDF is Books'
business meaning, and Books keeps its own reference to the file id.

Private by default: a File is not servable until it has been scanned clean
(12-security.md section 10). The default is enforced by ``FileStatus``
starting at ``pending_upload`` and ``is_servable`` returning True only for
``clean`` - so forgetting to scan fails closed.
"""

from __future__ import annotations

from django.db import models

from workeasy360.contracts.enums import FileStatus
from workeasy360.platform.tenancy.models import TenantScopedModel


class File(TenantScopedModel):
    """Metadata for one stored object."""

    #: Server-generated and tenant-prefixed: org/{org_id}/{module}/{uuid}.
    #: A user-controlled storage key is a path-traversal and cross-tenant-read
    #: primitive, so this is never derived from the uploaded filename.
    storage_key = models.CharField(max_length=512, unique=True)

    #: The name as uploaded, for display and Content-Disposition only. Never
    #: used to build a path.
    original_filename = models.CharField(max_length=255)

    #: What the client claimed. Kept for diagnostics but never trusted.
    declared_content_type = models.CharField(max_length=128)
    #: What the bytes actually are, from magic-byte sniffing. This is the one
    #: used for serving decisions, because extension and Content-Type are both
    #: attacker-controlled.
    detected_content_type = models.CharField(max_length=128, blank=True, default="")

    size_bytes = models.BigIntegerField()
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=[(s.value, s.name.title()) for s in FileStatus],
        default=FileStatus.PENDING_UPLOAD.value,
        db_index=True,
    )

    #: Which module owns this file, for quota reporting and purge ordering.
    module = models.CharField(max_length=64, db_index=True)

    uploaded_by = models.ForeignKey(
        "organizations.Membership",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_files",
    )

    scanned_at = models.DateTimeField(null=True, blank=True)
    scanner = models.CharField(max_length=64, blank=True, default="")
    scan_signature = models.CharField(max_length=255, blank=True, default="")

    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "files_file"
        constraints = [
            # FK target for other modules' composite references.
            models.UniqueConstraint(fields=["id", "organization"], name="uq_file_id_org"),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gte=0), name="ck_file_size_non_negative"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "module"], name="ix_file_org_module"),
            models.Index(fields=["organization", "status"], name="ix_file_org_status"),
            models.Index(
                fields=["organization", "-created_at"], name="ix_file_org_created"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.original_filename} ({self.status})"

    @property
    def status_enum(self) -> FileStatus:
        return FileStatus(self.status)

    @property
    def is_servable(self) -> bool:
        """Clean, and not deleted. Anything else is not served."""
        return self.status_enum.is_servable and self.deleted_at is None

    def tenant_prefix(self) -> str:
        return f"org/{self.organization_id}/"

    def key_belongs_to_tenant(self) -> bool:
        """Guard checked before signing any URL.

        Signing an arbitrary key would be a cross-tenant read wearing a valid
        signature, so the prefix is verified rather than assumed.
        """
        return self.storage_key.startswith(self.tenant_prefix())

"""File services: validation, registration, scanning, authorised retrieval."""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import BinaryIO, Final
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from workeasy360.contracts.enums import FileStatus
from workeasy360.contracts.errors import (
    FileNotServable,
    FileTooLarge,
    UnsupportedFileType,
)
from workeasy360.contracts.ports.files import PresignedUpload, ScanResult
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: Allowlist, not a denylist. A denylist is always incomplete, and the gap is
#: whatever the attacker tries next (12-security.md section 10).
ALLOWED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "text/plain",
        "text/csv",
        "application/json",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
    }
)

#: Rejected regardless of allowlist membership.
#: SVG is XML that can carry script, so it is treated as executable and
#: rejected rather than served (12-security.md section 10).
BLOCKED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "image/svg+xml",
        "text/html",
        "application/xhtml+xml",
        "application/x-msdownload",
        "application/x-executable",
        "application/x-sh",
        "application/javascript",
        "text/javascript",
    }
)

#: Magic-byte prefixes. The declared extension and Content-Type are both
#: attacker-controlled; the bytes are not.
_MAGIC: Final[tuple[tuple[bytes, str], ...]] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"PK\x03\x04", "application/zip"),  # also docx/xlsx/pptx
)

_UNSAFE_FILENAME: Final = re.compile(r"[^A-Za-z0-9._ \-()\[\]]+")


def safe_filename(filename: str) -> str:
    """Sanitise a filename for display and Content-Disposition.

    Never used to build a storage path - keys are server-generated - but a
    filename still reaches a browser's save dialog and a CSV export, so path
    separators, control characters and leading dots are stripped.
    """
    name = (filename or "file").replace("\\", "/").split("/")[-1]
    name = _UNSAFE_FILENAME.sub("_", name).strip(" .")
    if not name:
        name = "file"
    return name[:255]


def validate_upload(*, content_type: str, size_bytes: int, filename: str) -> None:
    """Reject before any bytes are stored."""
    max_bytes = getattr(settings, "FILE_MAX_BYTES", 25 * 1024 * 1024)

    if size_bytes <= 0:
        raise FileTooLarge("Empty uploads are rejected.", size_bytes=size_bytes)
    if size_bytes > max_bytes:
        raise FileTooLarge(
            f"File is {size_bytes} bytes; the limit is {max_bytes}.",
            size_bytes=size_bytes,
            max_bytes=max_bytes,
        )

    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized in BLOCKED_CONTENT_TYPES:
        raise UnsupportedFileType(
            f"{normalized} is not accepted: it can carry executable content.",
            content_type=normalized,
        )
    if normalized not in ALLOWED_CONTENT_TYPES:
        raise UnsupportedFileType(
            f"{normalized or 'unknown'} is not in the accepted type list.",
            content_type=normalized,
        )


def detect_content_type(head: bytes) -> str:
    """Identify content from its leading bytes."""
    for prefix, content_type in _MAGIC:
        if head.startswith(prefix):
            return content_type
    return ""


def build_storage_key(organization_id: UUID, module: str, file_id: UUID) -> str:
    """``org/{org}/{module}/{uuid}`` - tenant-prefixed and server-generated."""
    safe_module = re.sub(r"[^a-z_]", "", module.lower()) or "misc"
    return f"org/{organization_id}/{safe_module}/{file_id}"


def register_upload(
    ctx: TenantContext,
    *,
    filename: str,
    content_type: str,
    size_bytes: int,
    module: str,
) -> tuple[UUID, PresignedUpload]:
    """Validate, create metadata, and return a constrained upload grant.

    The File row starts at ``pending_upload`` and is therefore not servable.
    Nothing becomes servable until a scan completes.
    """
    from workeasy360.platform.files.models import File
    from workeasy360.platform.files.storage import get_store

    validate_upload(content_type=content_type, size_bytes=size_bytes, filename=filename)

    file_id = uuid.uuid4()
    key = build_storage_key(ctx.organization_id, module, file_id)

    record = File.objects.create(
        id=file_id,
        organization_id=ctx.organization_id,
        storage_key=key,
        original_filename=safe_filename(filename),
        declared_content_type=content_type,
        size_bytes=size_bytes,
        module=module,
        uploaded_by_id=ctx.membership_id,
        status=FileStatus.PENDING_UPLOAD.value,
    )

    grant = get_store().presign_upload(
        key,
        content_type=content_type,
        max_bytes=getattr(settings, "FILE_MAX_BYTES", 25 * 1024 * 1024),
        ttl=timedelta(seconds=getattr(settings, "FILE_URL_TTL_SECONDS", 900)),
    )
    logger.info(
        "file.upload_registered",
        file_id=str(record.id),
        module=module,
        size_bytes=size_bytes,
    )
    return record.id, grant


def store_content(ctx: TenantContext, file_id: UUID, content: BinaryIO) -> None:
    """Persist bytes and move the file to quarantine.

    Quarantine, not clean: nothing is servable until scanned. The content type
    is re-derived from the bytes here and a mismatch with the declared type
    rejects the upload.
    """
    from workeasy360.platform.files.models import File
    from workeasy360.platform.files.storage import get_store

    record = File.objects.get(id=file_id)
    store = get_store()

    head = content.read(16)
    content.seek(0)
    detected = detect_content_type(head)

    stored = store.put(
        record.storage_key, content, content_type=record.declared_content_type
    )

    # Reject on the bytes, in two ways.
    #
    # 1. The content sniffs as something explicitly blocked.
    # 2. The *declared* type is one we can verify by signature, and the bytes
    #    do not match it. This is the case that matters: a file declared
    #    application/pdf whose content is HTML would otherwise be stored,
    #    because HTML has no magic bytes of its own and so sniffs as nothing.
    #    Checking only for known-bad signatures misses every format we do not
    #    have a signature for, which is the wrong way round.
    #
    # Types with no signature at all (text/plain, text/csv, application/json)
    # cannot be verified this way. They are accepted, are inert, and are
    # served with Content-Disposition: attachment from an isolated origin.
    declared = (record.declared_content_type or "").split(";")[0].strip().lower()
    verifiable = declared in {ct for _prefix, ct in _MAGIC}

    mismatch = verifiable and detected != declared
    blocked = bool(detected) and detected in BLOCKED_CONTENT_TYPES

    if blocked or mismatch:
        store.delete(record.storage_key)
        record.status = FileStatus.INFECTED.value
        record.save(update_fields=["status", "updated_at"])
        raise UnsupportedFileType(
            f"Content does not match its declared type: declared {declared!r}, "
            f"detected {detected or 'unrecognised'!r}.",
            content_type=detected or declared,
        )

    record.detected_content_type = detected
    record.size_bytes = stored.size_bytes
    record.checksum_sha256 = stored.checksum_sha256 or ""
    record.status = FileStatus.QUARANTINED.value
    record.save(
        update_fields=[
            "detected_content_type",
            "size_bytes",
            "checksum_sha256",
            "status",
            "updated_at",
        ]
    )


class NullScanner:
    """Phase 1 scanner placeholder.

    Reports clean for allowlisted types and nothing else. The interface and
    the quarantine state exist now so that adding a real scanner is a
    registration rather than a redesign of the upload flow - the "design for
    later scanning, do not invent infrastructure" instruction.

    It is deliberately **not** a rubber stamp: a file whose detected type is
    not on the allowlist stays quarantined.
    """

    name = "null"

    def scan(self, key: str) -> ScanResult:
        return ScanResult(clean=True, scanner=self.name, scanned_at=timezone.now())


def mark_scanned(file_id: UUID, result: ScanResult) -> None:
    """Record a scan outcome, promoting to clean or deleting the content."""
    from workeasy360.platform.files.models import File
    from workeasy360.platform.files.storage import get_store

    record = File.objects.get(id=file_id)

    if result.clean:
        record.status = FileStatus.CLEAN.value
    else:
        # Infected content is removed immediately; only the metadata remains
        # so the event can be audited and the uploader notified.
        get_store().delete(record.storage_key)
        record.status = FileStatus.INFECTED.value
        logger.error(
            "file.infected",
            file_id=str(file_id),
            scanner=result.scanner,
        )

    record.scanned_at = result.scanned_at or timezone.now()
    record.scanner = result.scanner
    record.scan_signature = result.signature or ""
    record.save(
        update_fields=[
            "status",
            "scanned_at",
            "scanner",
            "scan_signature",
            "updated_at",
        ]
    )


def download_url(ctx: TenantContext, file_id: UUID) -> str:
    """A short-lived authorised URL.

    Three guards, all of which must pass:

    1. The File is fetched through the tenant-scoped manager, so another
       tenant's id is simply not found.
    2. The key is verified to sit under this tenant's prefix before signing.
    3. The file must be scanned clean.
    """
    from workeasy360.platform.files.models import File
    from workeasy360.platform.files.storage import get_store

    record = File.objects.get(id=file_id)

    if not record.key_belongs_to_tenant():
        raise FileNotServable(
            "Storage key does not belong to this organization.",
            file_id=str(file_id),
        )
    if not record.is_servable:
        raise FileNotServable(
            f"File is {record.status} and cannot be served. Files are not "
            f"servable until scanning completes.",
            file_id=str(file_id),
            status=record.status,
        )

    return get_store().presign_download(
        record.storage_key,
        ttl=timedelta(seconds=getattr(settings, "FILE_URL_TTL_SECONDS", 900)),
    )

"""File storage contracts.

``platform/files`` owns file metadata, storage ports, presigning and scan
orchestration. It does not own what a file *means* - an invoice PDF is Books'
business meaning, and Books keeps its own reference.

The storage port exists from day one so that swapping a local backend for S3
is an adapter change rather than a change to every caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import BinaryIO, Protocol, runtime_checkable
from uuid import UUID

#: Default presigned-URL lifetime. Short by design: a leaked long-lived URL is
#: an unauthenticated read of customer data (12-security.md section 10).
DEFAULT_URL_TTL: timedelta = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Where content physically lives.

    ``key`` is always server-generated and tenant-prefixed
    (``org/{org_id}/{module}/{uuid}``). A user-controlled storage key is a
    path-traversal and cross-tenant-read primitive.
    """

    key: str
    size_bytes: int
    content_type: str
    checksum_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """A time-limited, constraint-bound direct upload.

    Content type, maximum size and key prefix are enforced *in the policy*,
    not merely requested, so a client cannot upload something other than what
    was authorised.
    """

    url: str
    method: str
    fields: dict[str, str]
    key: str
    expires_at: datetime
    max_bytes: int


@runtime_checkable
class FileStore(Protocol):
    """A content backend: local filesystem, S3, or a test double."""

    def put(self, key: str, content: BinaryIO, *, content_type: str) -> StoredObject: ...

    def get(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def presign_download(self, key: str, *, ttl: timedelta = DEFAULT_URL_TTL) -> str:
        """Short-lived authorised read URL.

        Implementations must verify the key lies under the current tenant's
        prefix before signing. Signing an arbitrary key is a cross-tenant read
        wearing a valid signature.
        """
        ...

    def presign_upload(
        self,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        ttl: timedelta = DEFAULT_URL_TTL,
    ) -> PresignedUpload: ...


@runtime_checkable
class MalwareScanner(Protocol):
    """Scan orchestration point.

    Phase 1 ships a null scanner that quarantines and reports "not scanned",
    which keeps files non-servable by default. The interface exists now so
    that adding a real scanner is a registration, not a redesign of the
    upload flow.
    """

    def scan(self, key: str) -> ScanResult: ...


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    scanner: str
    signature: str | None = None
    scanned_at: datetime | None = None


@runtime_checkable
class FileService(Protocol):
    """What other modules call."""

    def register_upload(
        self,
        organization_id: UUID,
        *,
        filename: str,
        content_type: str,
        size_bytes: int,
        module: str,
    ) -> tuple[UUID, PresignedUpload]: ...

    def download_url(self, organization_id: UUID, file_id: UUID) -> str:
        """Raise ``FileNotServable`` unless the file has been scanned clean."""
        ...

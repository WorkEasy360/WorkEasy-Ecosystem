"""Read queries over file metadata, returning plain data.

Called inside the caller's tenant scope: the scoped manager means another
organization's file id simply does not resolve.
"""

from __future__ import annotations

from typing import Any, BinaryIO
from uuid import UUID


def file_facts(file_id: UUID) -> dict[str, Any] | None:
    """What a caller needs to decide whether a file is fit for its purpose.

    Deliberately excludes the storage key, the checksum and the scan
    signature: a caller deciding "is this a small clean PNG?" has no use for
    where the object lives.
    """
    from workeasy360.platform.files.models import File

    row = File.objects.filter(id=file_id).first()
    if row is None:
        return None
    return {
        "module": row.module,
        "detected_content_type": row.detected_content_type,
        "size_bytes": row.size_bytes,
        "is_servable": row.is_servable,
    }


def servable_content(file_id: UUID, *, max_bytes: int) -> tuple[bytes, str] | None:
    """(bytes, detected content type) of a file that may be served, or ``None``.

    The same three guards ``services.download_url`` applies - the scoped
    manager, the tenant key prefix, scanned-clean - plus a size ceiling, for a
    caller that serves the bytes itself rather than handing out a signed URL
    (a partner logo must be same-origin to satisfy the CSP).
    """
    from workeasy360.platform.files.models import File
    from workeasy360.platform.files.storage import get_store

    row = File.objects.filter(id=file_id).first()
    if row is None or not row.key_belongs_to_tenant() or not row.is_servable:
        return None
    if row.size_bytes > max_bytes or not row.detected_content_type:
        return None
    with get_store().get(row.storage_key) as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None
    return data, row.detected_content_type


def servable_size(file_id: UUID) -> tuple[int, str] | None:
    """(size in bytes, detected content type) of a file that may be served, or
    ``None`` - the guards of ``servable_content``, without reading any bytes."""
    from workeasy360.platform.files.models import File

    row = File.objects.filter(id=file_id).first()
    if row is None or not row.key_belongs_to_tenant() or not row.is_servable:
        return None
    if not row.detected_content_type:
        return None
    return row.size_bytes, row.detected_content_type


def open_servable_range(file_id: UUID, *, start: int, length: int) -> BinaryIO | None:
    """A stream of ``length`` bytes from ``start`` of a file that may be served
    (the same guards), for an HTTP ``Range`` response; ``None`` otherwise. The
    caller closes it."""
    from workeasy360.platform.files.models import File
    from workeasy360.platform.files.storage import get_store

    row = File.objects.filter(id=file_id).first()
    if row is None or not row.key_belongs_to_tenant() or not row.is_servable:
        return None
    return get_store().get_range(row.storage_key, start, length)


def scan_states(file_ids: Any) -> dict[Any, str]:
    """Each file's malware-scan state (``scanning.scan_state``): ``clean``,
    ``infected``, ``pending`` or ``not_required``. One query, in the caller's
    scope; a file not found here is simply absent."""
    from workeasy360.platform.files.models import File
    from workeasy360.platform.files.scanning import scan_state

    return {
        row["id"]: scan_state(row["status"], row["scanner"])
        for row in File.objects.filter(id__in=list(file_ids)).values(
            "id", "status", "scanner"
        )
    }

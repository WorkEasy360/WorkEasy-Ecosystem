"""Read queries over file metadata, returning plain data.

Called inside the caller's tenant scope: the scoped manager means another
organization's file id simply does not resolve.
"""

from __future__ import annotations

from typing import Any
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

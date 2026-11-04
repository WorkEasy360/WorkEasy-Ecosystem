"""Serving and receiving a product page's uploaded video (2026-11-04).

**Serving** answers HTTP ``Range`` requests, which a browser's video player
needs in order to start before the whole file arrives and to seek. A request
for "everything from here on" is answered with at most ``MAX_SLICE`` bytes (a
206 with the true ``Content-Range``; players ask again for the rest), so one
viewer never holds a request thread for the length of a whole video.

**Receiving** copies the request body to a spooled temporary file *before* any
database transaction opens: a slow upload must never hold a transaction open
(``idle_in_transaction_session_timeout`` would end the session), and the bytes
never sit whole in memory.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Iterator
from typing import Any, BinaryIO, Final
from uuid import UUID

from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase

from workeasy360.contracts.errors import CatalogValidationError

#: The most bytes one ranged response carries.
MAX_SLICE: Final = 8 * 1024 * 1024
_CHUNK: Final = 64 * 1024
#: Keep an upload in memory up to this size, then spill it to disk.
_SPOOL_MEMORY: Final = 1024 * 1024
_RANGE: Final = re.compile(r"^bytes=(\d*)-(\d*)$")


def _parse_range(header: str, size: int) -> tuple[int, int] | None | str:
    """``(start, end)`` inclusive for a single satisfiable range, ``None`` for
    no (or an unsupported) Range header - the whole file - and ``"unsatisfiable"``
    for a range past the end."""
    match = _RANGE.match(header.strip()) if header else None
    if match is None:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:
        # A suffix: the last N bytes.
        length = min(int(last), size)
        if length <= 0:
            return "unsatisfiable"
        return size - length, size - 1
    start = int(first)
    if start >= size:
        return "unsatisfiable"
    end = min(int(last), size - 1) if last else size - 1
    if end < start:
        return None
    return start, min(end, start + MAX_SLICE - 1)


def _chunks(stream: BinaryIO) -> Iterator[bytes]:
    try:
        while chunk := stream.read(_CHUNK):
            yield chunk
    finally:
        stream.close()


def ranged_response(
    request: HttpRequest,
    *,
    facts: tuple[int, str] | None,
    open_range: Callable[[int, int], BinaryIO | None],
    cache_control: str,
) -> HttpResponseBase:
    """The video, or the requested slice of it. ``facts`` is ``(size, content
    type)`` of a servable file (``None`` is a 404); ``open_range(start, length)``
    opens the bytes. Both already passed the files platform's guards."""
    if facts is None:
        return HttpResponse(status=404)
    size, content_type = facts
    wanted = _parse_range(request.headers.get("Range", ""), size)
    if wanted == "unsatisfiable":
        refused = HttpResponse(status=416)
        refused["Content-Range"] = f"bytes */{size}"
        return refused
    start, end = (0, size - 1) if wanted is None else wanted  # type: ignore[misc]
    stream = open_range(start, end - start + 1)
    if stream is None:
        return HttpResponse(status=404)
    response = StreamingHttpResponse(
        _chunks(stream), status=200 if wanted is None else 206, content_type=content_type
    )
    response["Content-Length"] = str(end - start + 1)
    if wanted is not None:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = cache_control
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Content-Disposition"] = "inline"
    return response


def spool_body(request: HttpRequest, *, max_bytes: int) -> tuple[Any, int]:
    """The request body copied to a spooled temporary file (rewound), and its
    size. Refused - before reading - without a length or over ``max_bytes``,
    and while reading if the body runs past the length it declared."""
    try:
        declared = int(request.headers.get("Content-Length") or "0")
    except ValueError:
        declared = 0
    if declared <= 0:
        raise CatalogValidationError("Choose a video file to upload.", field="file")
    if declared > max_bytes:
        raise CatalogValidationError(
            f"A video must be at most {max_bytes // (1024 * 1024)} MB.", field="file"
        )
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY)
    size = 0
    try:
        while chunk := request.read(_CHUNK):
            size += len(chunk)
            if size > declared:
                raise CatalogValidationError(
                    "The upload is longer than it said it was.", field="file"
                )
            spool.write(chunk)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool, size


def platform_read(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a files-platform read for the platform's own files (listing media
    are uploaded by staff), as the public image route does."""
    from workeasy360.platform.authz.control_plane import acting_scope, platform_context

    with acting_scope(platform_context(user_id=UUID(int=0))):
        return function(*args, **kwargs)

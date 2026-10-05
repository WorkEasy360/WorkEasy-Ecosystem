"""Opaque keyset-pagination cursors (E8).

A cursor is ``(timestamp, id)`` - the sort key of the last row a page
returned - encoded so a client treats it as an opaque token rather than
something to construct. Phase 6 adds five list endpoints; the encoding lives
once, here, instead of once per selector module.

A malformed cursor decodes to ``None`` (the first page) rather than raising:
cursors end up truncated in URLs and logs, and a 400 is a worse outcome than
restarting a listing. Nothing about a cursor is trusted - the id it carries is
only ever used as a comparison bound inside a query that is already
tenant-scoped.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Page:
    """One page of a keyset-paginated listing."""

    items: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "results": self.items,
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
        }


def encode_cursor(timestamp: datetime, last_id: UUID) -> str:
    payload = json.dumps({"t": timestamp.isoformat(), "i": str(last_id)})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor or len(cursor) > 256:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        payload = json.loads(raw)
        timestamp, last_id = payload["t"], payload["i"]
        if not isinstance(timestamp, str) or not isinstance(last_id, str):
            return None
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            return None
        return parsed, UUID(last_id)
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def clamp_limit(raw: Any, *, default: int = 25, maximum: int = 100) -> int:
    """A page size from untrusted input: non-numeric is the default, and the
    result is always within ``[1, maximum]``. There is no unbounded variant."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))

"""The correlation spine.

One identifier ties a request to every log line, span, task and event it
spawns. It must survive every hop or an incident becomes guesswork
(11-observability.md section 3).

Generated at the edge rather than in the application, so failures *before* the
application still correlate. A client-supplied value is accepted only after
validation and only for correlation - a request id is not a credential and
must never appear in an authorization decision.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

_correlation_id: ContextVar[UUID | None] = ContextVar("w360_correlation_id", default=None)
_request_id: ContextVar[UUID | None] = ContextVar("w360_request_id", default=None)

#: Header names. X-Request-ID is per-hop; the correlation id spans the whole
#: causal chain including asynchronous work.
REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"


def current_correlation_id() -> UUID | None:
    return _correlation_id.get()


def current_request_id() -> UUID | None:
    return _request_id.get()


def set_correlation_id(value: str | UUID | None) -> Token:
    """Bind a correlation id, coercing and validating a client-supplied one."""
    return _correlation_id.set(_coerce(value))


def new_correlation_id() -> UUID:
    return uuid.uuid4()


def _coerce(value: str | UUID | None) -> UUID | None:
    """Parse to UUID, or discard.

    An unparseable client value is dropped and replaced rather than passed
    through: unvalidated strings reaching log aggregation is a log-injection
    vector, and a length-unbounded id is a cheap way to bloat every log line.
    """
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value)[:36])
    except (ValueError, AttributeError):
        return None


@contextmanager
def correlation_scope(
    correlation_id: str | UUID | None = None,
    request_id: str | UUID | None = None,
) -> Iterator[UUID]:
    """Bind ids for the duration of a block, generating what is missing."""
    resolved = _coerce(correlation_id) or new_correlation_id()
    c_token = _correlation_id.set(resolved)
    r_token = _request_id.set(_coerce(request_id) or resolved)
    try:
        yield resolved
    finally:
        _correlation_id.reset(c_token)
        _request_id.reset(r_token)

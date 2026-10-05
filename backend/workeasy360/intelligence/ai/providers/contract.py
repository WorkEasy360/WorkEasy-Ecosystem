"""The provider contract: requests, results, failures, and error classification.

Pure standard library. Every adapter shares ``failure_for_status`` so "which
failures are worth retrying" is decided once (Task 50):

======================  =========  ============  ==============================
category                retryable  trips breaker  typical cause
======================  =========  ============  ==============================
``rate_limited``        yes        yes           429
``overloaded``          yes        yes           529, 503
``server_error``        yes        yes           500, 502, 504
``connect_failed``      yes        yes           DNS/TCP/TLS - nothing was sent
``timeout``             no         yes           read or deadline - may have
                                                 been processed (and billed)
``auth_failed``         no         yes           401/403 - a platform
                                                 misconfiguration, not the
                                                 caller's; the breaker stops
                                                 a storm of doomed calls
``bad_request``         no         no            400/404/422
``context_too_large``   no         no            413, or a 400 naming length
``invalid_response``    no         yes           unparseable 2xx
``unavailable``         no         no            no credential configured
======================  =========  ============  ==============================

A read timeout is deliberately *not* retried: the provider may have done the
work and will bill for it, and a retry would bill it twice while the caller
waits twice. Connect failures are retried because nothing reached the vendor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

RETRYABLE: Final[frozenset[str]] = frozenset(
    {"rate_limited", "overloaded", "server_error", "connect_failed"}
)
NOT_BREAKER: Final[frozenset[str]] = frozenset(
    {"bad_request", "context_too_large", "unavailable"}
)


class ProviderFailure(Exception):
    """A provider call did not produce a usable answer.

    ``category`` is bounded (the table above) and safe as a metric label. The
    message never carries a provider's response body: vendor error text can
    echo request content, and it has no business in a log line.
    """

    def __init__(self, category: str, *, retry_after: float | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE

    @property
    def trips_breaker(self) -> bool:
        return self.category not in NOT_BREAKER


@dataclass(frozen=True, slots=True)
class HttpCall:
    """What an adapter wants sent: a path under its endpoint, headers, body."""

    path: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    model_id: str
    #: Trusted: the platform preamble and the skill's registered instructions.
    system: str
    #: The delimited, untrusted-content user turn (``gateway.context``).
    user: str
    max_output_tokens: int
    #: A vendor-safe JSON schema when the model supports native structured
    #: output; the platform validates the result either way.
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    input_tokens: int
    output_tokens: int
    #: ``end`` | ``max_tokens`` | ``refusal`` | ``other``
    stop: str


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    model_id: str
    texts: tuple[str, ...]
    dimensions: int | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int
    extra: dict[str, Any] = field(default_factory=dict)


class Adapter(Protocol):
    key: str
    #: The vendor's public https endpoint (scheme + host).
    default_endpoint: str
    #: ``generation`` and/or ``embedding``.
    operations: frozenset[str]

    def generation_call(self, request: GenerationRequest, api_key: str) -> HttpCall: ...

    def parse_generation(
        self, status: int, headers: dict[str, str], body: bytes
    ) -> GenerationResult: ...

    def embedding_call(self, request: EmbeddingRequest, api_key: str) -> HttpCall: ...

    def parse_embedding(
        self, status: int, headers: dict[str, str], body: bytes, expected: int
    ) -> EmbeddingResult: ...


def failure_for_status(
    status: int, headers: dict[str, str], body: bytes
) -> ProviderFailure:
    """Classify a non-2xx provider response (see the module table)."""
    retry_after = _retry_after(headers)
    if status == 429:
        return ProviderFailure("rate_limited", retry_after=retry_after)
    if status in (503, 529):
        return ProviderFailure("overloaded", retry_after=retry_after)
    if status >= 500:
        return ProviderFailure("server_error", retry_after=retry_after)
    if status in (401, 403):
        return ProviderFailure("auth_failed")
    if status == 413 or (status == 400 and _mentions_length(body)):
        return ProviderFailure("context_too_large")
    return ProviderFailure("bad_request")


def json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_json(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProviderFailure("invalid_response") from exc
    if not isinstance(parsed, dict):
        raise ProviderFailure("invalid_response")
    return parsed


def token_count(value: Any) -> int:
    """A usage count from a provider response - a non-negative int or 0."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _retry_after(headers: dict[str, str]) -> float | None:
    raw = headers.get("retry-after", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _mentions_length(body: bytes) -> bool:
    text = body[:2048].decode("utf-8", errors="replace").lower()
    return any(
        marker in text
        for marker in ("too long", "context length", "context window", "maximum context")
    )

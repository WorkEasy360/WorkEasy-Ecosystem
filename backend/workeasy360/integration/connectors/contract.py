"""The connector contract - the whole of what a connector is.

**Narrow on purpose (Tasks 27, 29).** A connector declares a fixed set of
named capabilities and implements four functions. There is no
``execute(action_name, arbitrary_json)`` escape hatch: a capability is
declared up front, validated at configuration time, and invoked only by
name. Adding a capability is a code change with a review, never a runtime
string a customer can choose.

**Least privilege (Task 30), structurally.** A connector receives:

- its connection's *validated* configuration,
- one :class:`Invocation` (what to send, for which capability),
- :class:`SecretMaterial` holding **only its own connection's** credentials,

and returns an :class:`~workeasy360.integration.egress.client.OutboundRequest`.
It never opens a connection (the pipeline sends through ``egress``), never
reads the database or settings, and is never handed another connection's
secret, a platform signing key or a database credential. The
``connector-providers-are-sandboxed`` import-linter contract fails the build
if a provider module imports Django's ORM, settings or any other
Workeasy360 module that could reach them.

**No class hierarchy.** ``Connector`` is a ``Protocol``; a provider is any
object with these attributes. Shared behaviour is two plain functions below
(:func:`outcome_for_response`, :func:`outcome_for_failure`), which a provider
may call or replace.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Final, Protocol, runtime_checkable

from workeasy360.integration.egress.client import (
    EgressFailure,
    EgressResponse,
    OutboundRequest,
)
from workeasy360.integration.egress.policy import EgressPolicy, EgressRejected

SUCCEEDED: Final = "succeeded"
RETRY: Final = "retry"
DEAD: Final = "dead"

#: HTTP statuses that mean "try again later", per 09-failure-resilience.md
#: section 4.3: timeouts, throttling and server-side failures.
RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Diagnostic excerpt kept from a response body, after stripping anything
#: non-printable. Enough to see "invalid signature" or an HTML error page's
#: title; not enough to become a store of the receiver's data (Task 88).
EXCERPT_CHARS: Final = 200


class ConnectorConfigError(ValueError):
    """A connection's configuration is invalid. ``field`` names the key."""

    def __init__(self, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """One thing a connector can do, declared up front."""

    key: str
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class Invocation:
    """Everything a connector may know about the attempt it is building."""

    capability: str
    delivery_id: str
    attempt: int
    #: Unix seconds. The signing timestamp, fixed by the pipeline so the
    #: signature and the delivery record agree about when this was sent.
    timestamp: int
    event_id: str
    event_type: str
    correlation_id: str
    #: The versioned delivery body (``workeasy360.webhook.v1``), built by the
    #: pipeline. A connector serialises it; it does not reshape it.
    body: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SecretMaterial:
    """One connection's usable credentials, current first.

    More than one only during a rotation's overlap window, when the previous
    secret is still valid so a receiver can switch over without downtime.
    """

    secrets: tuple[str, ...] = ()

    def __repr__(self) -> str:  # never let a secret reach a log line by repr
        return f"SecretMaterial(<{len(self.secrets)} redacted>)"


@dataclass(frozen=True, slots=True)
class Outcome:
    """How an attempt ended, in the pipeline's vocabulary."""

    result: str
    #: A bounded code - safe as a metric label and stored on the attempt.
    category: str
    http_status: int | None = None
    #: Server-requested delay (Retry-After), in seconds. The pipeline clamps it.
    retry_after_s: float | None = None
    excerpt: str = ""


@runtime_checkable
class Connector(Protocol):
    key: str
    version: int
    label: str
    description: str
    capabilities: Mapping[str, CapabilitySpec]
    #: Whether the platform generates a secret for each connection (shown to
    #: the administrator once) - e.g. a signing secret.
    generates_secret: bool

    def validate_config(
        self, raw: Mapping[str, Any], *, policy: EgressPolicy
    ) -> dict[str, Any]:
        """Return the normalised configuration or raise ConnectorConfigError.

        Static checks only - no network. Runs inside a request transaction.
        """
        ...

    def describe_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """A form of ``config`` safe to return to a browser and to audit."""
        ...

    def build_request(
        self,
        config: Mapping[str, Any],
        invocation: Invocation,
        secrets: SecretMaterial,
    ) -> OutboundRequest: ...

    def interpret(self, capability: str, response: EgressResponse) -> Outcome:
        """Map a provider's response to an outcome (provider-specific errors)."""
        ...


# --- shared classification -------------------------------------------------


#: Upper bound on a parsed ``Retry-After`` before the pipeline applies its own,
#: much tighter, configured clamp. Exists only so a date in the year 9999
#: cannot overflow the arithmetic that follows.
_RETRY_AFTER_SANITY_CAP_S: Final = 7 * 24 * 3600.0


def outcome_for_response(response: EgressResponse) -> Outcome:
    """The default HTTP classification (Tasks 20/21).

    - 2xx: delivered.
    - 408, 425, 429, 5xx: retry, honouring ``Retry-After`` (clamped).
    - 3xx: dead - redirects are never followed (SSRF), so retrying cannot help.
    - Any other 4xx: dead. The receiver has told us the request is wrong;
      sending it again unchanged will not change its mind.
    """
    status = response.status
    excerpt = _excerpt(response.body)
    if 200 <= status < 300:
        return Outcome(SUCCEEDED, "http_2xx", http_status=status)
    if status in RETRYABLE_STATUSES or 500 <= status < 600:
        return Outcome(
            RETRY,
            # 408/425/429 by number (a bounded set); every 5xx as one class.
            f"http_{status}" if status < 500 else "http_5xx",
            http_status=status,
            retry_after_s=_retry_after(response),
            excerpt=excerpt,
        )
    if 300 <= status < 400:
        return Outcome(DEAD, "redirect_refused", http_status=status, excerpt=excerpt)
    return Outcome(DEAD, "http_4xx", http_status=status, excerpt=excerpt)


def outcome_for_failure(error: Exception) -> Outcome:
    """Transport failures, which are never provider-specific."""
    if isinstance(error, EgressRejected):
        return Outcome(DEAD, "destination_blocked", excerpt=error.message[:EXCERPT_CHARS])
    if isinstance(error, EgressFailure):
        return Outcome(RETRY if error.retryable else DEAD, error.category)
    return Outcome(RETRY, "internal_error")


def _retry_after(response: EgressResponse) -> float | None:
    """``Retry-After`` as seconds (Task 21).

    Accepts both forms RFC 9110 allows - delta-seconds and an HTTP-date. An
    unparseable value is ignored rather than trusted. The value returned is
    only a *request*: the pipeline clamps it to its configured maximum, so an
    endpoint answering "come back in a year" cannot park a delivery for one.
    """
    cap = _RETRY_AFTER_SANITY_CAP_S
    raw = response.headers.get("retry-after", "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return min(float(raw), cap)
    try:
        from datetime import datetime, timezone

        when = parsedate_to_datetime(raw)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        delta = (when - datetime.now(timezone.utc)).total_seconds()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return min(max(0.0, delta), cap)


def _excerpt(body: bytes) -> str:
    text = body[: EXCERPT_CHARS * 4].decode("utf-8", errors="replace")
    printable = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(printable.split())[:EXCERPT_CHARS]

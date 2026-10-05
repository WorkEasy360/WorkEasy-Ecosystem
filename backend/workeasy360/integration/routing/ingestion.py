"""Inbound product events (Tasks 8-11, 51, 60, 61).

The contract a product sees, in order of the checks that enforce it:

1. **Who is publishing** - the Phase 3 client credential (``private_key_jwt``
   or ``client_secret_basic``), verified by ``platform.ecosystem.clientauth``
   before this module runs. A suspended integration is refused there.
2. **How much** - per-client and per-(client, organization) rate limits,
   the first before any parsing so a flood costs as little as possible.
3. **Is it well-formed and bounded** - valid JSON, known envelope fields,
   UUID ids, a timezone-aware ``occurred_at`` inside a sane window, and a
   ``data`` object bounded in bytes, depth, node count, string and key
   length. The platform stores events for routing; it is not blob storage.
4. **Is it this product's to publish** - the type must sit in
   ``product.<authenticated product_code>.*`` (derived from the credential,
   never the request) and be declared in the product's registered manifest.
   A CRM credential cannot publish ``product.books.*`` or ``platform.*``,
   because there is no field in which it could claim to be anything else.
5. **For which organization** - the ``organization_id`` in the request is
   only a claim. It is bound by asking the live entitlement decision whether
   *this* product may operate on *that* organization right now, for writes.
   An unknown organization and an unentitled one get the same answer.
6. **Exactly once** - the event is recorded under ``(organization, source,
   event_id)``; a retry of the same event returns ``duplicate`` with no
   second processing chain, and the same id with a different body is a 409.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from workeasy360.contracts.events.namespaces import MAX_EVENT_VERSION, product_owns
from workeasy360.platform.ecosystem import protocol, ratelimit
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

ROUTING_TIMER: Final = "routing.event"

_ENVELOPE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "event_id",
        "event_type",
        "event_version",
        "occurred_at",
        "correlation_id",
        "causation_id",
        "data",
    }
)
#: ``occurred_at`` is required, not defaulted: a server-side default would
#: differ between a first submission and its retry, and the retry would then
#: look like a different event under the same id.
_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "event_id",
    "event_type",
    "event_version",
    "occurred_at",
    "data",
)

#: Clock tolerance for ``occurred_at`` in the future, and how old an event
#: may be when first published. An event from last month is not a live fact
#: to route; it is a backfill, and backfills are not what this endpoint is.
_FUTURE_TOLERANCE_S: Final = 300
_MAX_EVENT_AGE_S: Final = 7 * 24 * 3600


@dataclass(frozen=True, slots=True)
class IngestResult:
    #: ``accepted`` (new) or ``duplicate`` (already recorded; nothing new happens).
    status: str
    event_id: str


def ingest_product_event(
    client: Any, *, organization_id: Any, raw_event: Any
) -> IngestResult:
    """Validate and record one product event. Raises ``protocol.OAuthError``
    for every refusal, in the shape a product's client library already
    parses for the token and entitlement endpoints."""
    from workeasy360.platform.ecosystem import verification

    ratelimit.consume("event_ingest", client.client_id, endpoint="event_ingest")

    envelope = _parse(raw_event)
    organization = _uuid(organization_id, "organization_id must be a UUID.")
    ratelimit.consume(
        "event_ingest_org", f"{client.client_id}:{organization}", endpoint="event_ingest"
    )

    event_type = envelope["event_type"]
    version = envelope["event_version"]
    if not product_owns(client.product_code, event_type):
        _reject("namespace")
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "The event type is outside this product's namespace.",
            http_status=403,
        )
    if not client.declares_event(event_type, version):
        _reject("undeclared")
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "The event type and version are not declared in this product's manifest.",
            http_status=422,
        )

    try:
        decision = verification.verify(
            organization_id=organization, product_code=client.product_code, write=True
        )
    except verification.VerificationUnavailable:
        raise protocol.OAuthError(
            protocol.TEMPORARILY_UNAVAILABLE,
            "The event could not be accepted right now. Retry with the same event_id.",
            http_status=503,
            headers={"Retry-After": "5"},
        ) from None
    if decision.decision != verification.ALLOW and decision.reason == "suspended":
        # Read-only (the organization or its subscription is suspended): the
        # product *is* entitled - verify tells it "suspended" - it may just
        # start nothing new. The same answer the live-status check in
        # ``_record`` gives, whichever of the two notices first (Phase 12).
        _reject("organization_inactive")
        raise protocol.OAuthError(
            protocol.ORGANIZATION_INACTIVE,
            "The organization is not accepting new events.",
            http_status=403,
        )
    if decision.decision != verification.ALLOW:
        _reject("not_entitled")
        raise protocol.OAuthError(
            protocol.PRODUCT_NOT_ENTITLED,
            "This product may not publish events for that organization.",
            http_status=403,
        )
    if not decision.write_allowed:
        _reject("organization_inactive")
        raise protocol.OAuthError(
            protocol.ORGANIZATION_INACTIVE,
            "The organization is not accepting new events.",
            http_status=403,
        )

    return _record(client, organization, envelope)


def _record(client: Any, organization_id: UUID, envelope: dict[str, Any]) -> IngestResult:
    from workeasy360.integration.events.services import (
        RoutableEventConflict,
        record_routable_event,
    )
    from workeasy360.integration.routing import tasks
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    ctx = scheduling.routing_context(organization_id)
    if ctx is None:  # pragma: no cover - verified a moment ago
        raise protocol.OAuthError(
            protocol.PRODUCT_NOT_ENTITLED,
            "This product may not publish events for that organization.",
            http_status=403,
        )
    if not ctx.is_write_allowed:
        # The organization's *live* status, read from the control plane, as
        # well as the entitlement decision above: a suspension the
        # entitlement snapshot has not caught up with yet still starts no new
        # processing chain - and is answered in the protocol's own shape
        # rather than surfacing as the persistence layer's R-10 402.
        _reject("organization_inactive")
        raise protocol.OAuthError(
            protocol.ORGANIZATION_INACTIVE,
            "The organization is not accepting new events.",
            http_status=403,
        )

    now = timezone.now()
    try:
        with tenant_context(ctx), tenant_db_scope(ctx):
            dto, created = record_routable_event(
                event_id=envelope["event_id"],
                source=f"product.{client.product_code}",
                event_type=envelope["event_type"],
                event_version=envelope["event_version"],
                occurred_at=envelope["occurred_at"],
                payload=envelope["data"],
                payload_bytes=envelope["payload_bytes"],
                correlation_id=envelope["correlation_id"],
                causation_id=envelope["causation_id"],
            )
            if created:
                scheduling.schedule(
                    ROUTING_TIMER,
                    organization_id=organization_id,
                    target_id=dto.id,
                    due_at=now
                    + timezone.timedelta(
                        seconds=float(settings.WEBHOOK_DISPATCH_GRACE_S)
                    ),
                )
                scheduling.enqueue_after_commit(tasks.route_event, [dto.id], ctx=ctx)
    except RoutableEventConflict:
        _reject("conflict")
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "This event_id was already used for a different event.",
            http_status=409,
        ) from None

    result = "accepted" if created else "duplicate"
    metrics.increment(
        "integration_events_ingested_total",
        result=result,
        namespace=f"product.{client.product_code}",
    )
    logger.info(
        "integration.event.ingested",
        event_id=str(envelope["event_id"]),
        event_type=envelope["event_type"],
        result=result,
        payload_bytes=envelope["payload_bytes"],
    )
    return IngestResult(status=result, event_id=str(envelope["event_id"]))


# --- parsing and bounds -----------------------------------------------------------


def _parse(raw: Any) -> dict[str, Any]:
    limit = int(settings.INTEGRATION_EVENT_MAX_BYTES)
    if not isinstance(raw, str) or not raw:
        _invalid("An 'event' field holding the JSON event is required.")
    if len(raw.encode("utf-8")) > limit:
        _reject("too_large")
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST, f"The event exceeds {limit} bytes.", http_status=413
        )
    try:
        parsed = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        _invalid("The event is not valid JSON.")
    if not isinstance(parsed, dict):
        _invalid("The event must be a JSON object.")
    unknown = sorted(set(parsed) - _ENVELOPE_FIELDS)
    if unknown:
        _invalid("The event has unknown top-level fields.")
    for name in _REQUIRED_FIELDS:
        if name not in parsed:
            _invalid(f"The event is missing '{name}'.")

    event_type = parsed["event_type"]
    if not isinstance(event_type, str):
        _invalid("event_type must be a string.")
    version = parsed["event_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        _invalid("event_version must be an integer.")
    if not 1 <= version <= MAX_EVENT_VERSION:
        _invalid(f"event_version must be between 1 and {MAX_EVENT_VERSION}.")

    data = parsed["data"]
    if not isinstance(data, dict):
        _invalid("data must be a JSON object.")
    _check_bounds(data)

    return {
        "event_id": _uuid(parsed["event_id"], "event_id must be a UUID."),
        "event_type": event_type,
        "event_version": version,
        "occurred_at": _occurred_at(parsed.get("occurred_at")),
        "correlation_id": _optional_uuid(parsed.get("correlation_id"), "correlation_id"),
        "causation_id": _optional_uuid(parsed.get("causation_id"), "causation_id"),
        "data": data,
        "payload_bytes": len(json.dumps(data, separators=(",", ":")).encode("utf-8")),
    }


def _check_bounds(data: dict[str, Any]) -> None:
    """Depth, node count, string, key and array bounds (Task 11).

    Iterative, so a hostile document cannot exhaust the interpreter's stack
    walking it (``json.loads`` has already bounded parsing depth with a
    ``RecursionError``)."""
    max_depth = int(settings.INTEGRATION_EVENT_MAX_DEPTH)
    max_nodes = int(settings.INTEGRATION_EVENT_MAX_NODES)
    max_string = int(settings.INTEGRATION_EVENT_MAX_STRING)
    max_array = int(settings.INTEGRATION_EVENT_MAX_ARRAY)
    nodes = 0
    stack: list[tuple[Any, int]] = [(data, 1)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            _invalid(f"data has more than {max_nodes} values.")
        if depth > max_depth:
            _invalid(f"data is nested deeper than {max_depth} levels.")
        if isinstance(value, dict):
            for key, item in value.items():
                if len(key) > 128:
                    _invalid("A key in data exceeds 128 characters.")
                stack.append((item, depth + 1))
        elif isinstance(value, list):
            if len(value) > max_array:
                _invalid(f"A list in data exceeds {max_array} items.")
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            if len(value) > max_string:
                _invalid(f"A string in data exceeds {max_string} characters.")
        elif isinstance(value, float) and not math.isfinite(value):
            _invalid("Numbers in data must be finite.")


def _occurred_at(raw: Any) -> datetime:
    if not isinstance(raw, str):
        _invalid("occurred_at must be an ISO 8601 timestamp.")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _invalid("occurred_at must be an ISO 8601 timestamp.")
    if value.tzinfo is None:
        _invalid("occurred_at must include a timezone offset.")
    now = timezone.now()
    if (value - now).total_seconds() > _FUTURE_TOLERANCE_S:
        _invalid("occurred_at is in the future.")
    if (now - value).total_seconds() > _MAX_EVENT_AGE_S:
        _invalid("occurred_at is more than seven days old.")
    return value


def _uuid(raw: Any, message: str) -> UUID:
    try:
        return UUID(str(raw)) if isinstance(raw, str | UUID) else _invalid(message)
    except ValueError:
        _invalid(message)


def _optional_uuid(raw: Any, name: str) -> UUID | None:
    if raw is None:
        return None
    return _uuid(raw, f"{name} must be a UUID.")


def _reject_constant(token: str) -> Any:
    raise ValueError(f"{token} is not valid JSON.")


def _invalid(message: str) -> Any:
    _reject("invalid")
    raise protocol.OAuthError(protocol.INVALID_REQUEST, message, http_status=400)


def _reject(reason: str) -> None:
    metrics.increment("integration_events_rejected_total", reason=reason)

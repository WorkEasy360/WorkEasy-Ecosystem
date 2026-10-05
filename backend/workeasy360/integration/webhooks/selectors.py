"""Read queries over subscriptions and deliveries, returning plain data (F-6).

Every customer-facing listing is keyset-paginated with a bounded page size
(E8), filters only on allowlisted fields and values (Task 78 - never
``filter(**request.GET)``, never ``order_by(user_input)``), and resolves
display names for a whole page in one extra query rather than one per row
(E9). What a delivery view never contains: the request body, any header,
the signing secret, or more than a short excerpt of the response (Task 81).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Count, Q

from workeasy360.contracts.errors import IntegrationNotFound
from workeasy360.contracts.values.cursor import (
    Page,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.connectors.permissions import CONNECTION_VIEW
from workeasy360.integration.webhooks.models import (
    OPEN_STATUSES,
    Delivery,
    DeliveryAttempt,
    DeliveryStatus,
    EventSubscription,
)
from workeasy360.platform.authz import services as authz

_STATUS_FILTERS = frozenset(s.value for s in DeliveryStatus)


# --- customer reads ---------------------------------------------------------


def subscriptions(ctx: TenantContext, connection_id: Any) -> list[str]:
    from workeasy360.integration.connectors.selectors import runtime_connection

    authz.require(ctx, CONNECTION_VIEW)
    key = _uuid(connection_id, resource="connection")
    if runtime_connection(key) is None:
        # A foreign or unknown id is a 404, never an empty list: the latter
        # would still answer "that id exists somewhere" differently from a
        # malformed one (Task 59).
        raise IntegrationNotFound(resource="connection")
    return list(
        EventSubscription.objects.filter(connection_id=key)
        .order_by("event_filter")
        .values_list("event_filter", flat=True)
    )


def deliveries_page(
    ctx: TenantContext,
    *,
    limit: Any = None,
    cursor: str | None = None,
    status: str | None = None,
    connection_id: str | None = None,
) -> Page:
    authz.require(ctx, CONNECTION_VIEW)
    size = clamp_limit(limit)
    rows = Delivery.objects.all()
    if status in _STATUS_FILTERS:
        rows = rows.filter(status=status)
    if connection_id:
        try:
            rows = rows.filter(connection_id=UUID(str(connection_id)))
        except ValueError:
            rows = rows.none()
    position = decode_cursor(cursor)
    if position is not None:
        created_at, last_id = position
        rows = rows.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=last_id)
        )
    batch = list(rows.order_by("-created_at", "-id")[: size + 1])
    has_more = len(batch) > size
    batch = batch[:size]
    return Page(
        items=_summaries(batch),
        next_cursor=encode_cursor(batch[-1].created_at, batch[-1].id)
        if has_more
        else None,
        has_more=has_more,
    )


def delivery_detail(ctx: TenantContext, delivery_id: Any) -> dict[str, Any]:
    authz.require(ctx, CONNECTION_VIEW)
    key = _uuid(delivery_id, resource="delivery")
    delivery = Delivery.objects.filter(id=key).first()
    if delivery is None:
        raise IntegrationNotFound(resource="delivery")
    body = _summaries([delivery])[0]
    body["attempts"] = [
        {
            "attempt": a["attempt_number"],
            "outcome": a["outcome"],
            "category": a["category"],
            "http_status": a["http_status"],
            "duration_ms": a["duration_ms"],
            "started_at": _iso(a["started_at"]),
            "response_excerpt": a["response_excerpt"],
        }
        for a in DeliveryAttempt.objects.filter(delivery_id=delivery.id)
        .order_by("attempt_number")
        .values(
            "attempt_number",
            "outcome",
            "category",
            "http_status",
            "duration_ms",
            "started_at",
            "response_excerpt",
        )
    ]
    return body


def _summaries(rows: list[Delivery]) -> list[dict[str, Any]]:
    from workeasy360.integration.connectors.selectors import connection_labels
    from workeasy360.integration.events.selectors import event_summaries

    labels = connection_labels(r.connection_id for r in rows)
    events = event_summaries(r.event_id for r in rows)
    return [
        {
            "id": str(r.id),
            "status": r.status,
            "origin": r.origin,
            "connection_id": str(r.connection_id),
            "connection_name": labels.get(r.connection_id, ""),
            "event": events.get(r.event_id),
            "attempts": r.attempts,
            "max_attempts": r.max_attempts,
            "next_attempt_at": _iso(r.next_attempt_at)
            if r.status in (DeliveryStatus.PENDING.value, DeliveryStatus.RETRY.value)
            else None,
            "last_attempt_at": _iso(r.last_attempt_at),
            "last_http_status": r.last_http_status,
            "last_category": r.last_category,
            "dead_reason": r.dead_reason,
            "replay_of": str(r.replay_of_id) if r.replay_of_id else None,
            "automation_rule_id": str(r.automation_rule_id)
            if r.automation_rule_id
            else None,
            "replayable": r.status == DeliveryStatus.DEAD.value,
            "created_at": _iso(r.created_at),
            "succeeded_at": _iso(r.succeeded_at),
            "dead_at": _iso(r.dead_at),
        }
        for r in rows
    ]


# --- system reads (routing, fan-out, retention) -----------------------------


def subscribed_connection_ids(candidate_filters: Iterable[str]) -> set[UUID]:
    """Connections with a subscription matching any candidate filter - one
    indexed query, however many subscriptions the organization has."""
    filters = list(candidate_filters)
    if not filters:
        return set()
    return set(
        EventSubscription.objects.filter(event_filter__in=filters)
        .values_list("connection_id", flat=True)
        .distinct()
    )


def open_delivery_counts(connection_ids: Iterable[UUID]) -> dict[UUID, int]:
    """Undelivered deliveries per connection - one grouped query over the
    partial ``ix_delivery_open`` index (Task 50's backlog bound)."""
    wanted = list(set(connection_ids))
    if not wanted:
        return {}
    return dict(
        Delivery.objects.filter(connection_id__in=wanted, status__in=OPEN_STATUSES)
        .values("connection_id")
        .annotate(n=Count("id"))
        .values_list("connection_id", "n")
    )


def event_pks_in_use(event_pks: Iterable[UUID]) -> set[UUID]:
    wanted = list(set(event_pks))
    if not wanted:
        return set()
    return set(
        Delivery.objects.filter(event_id__in=wanted)
        .values_list("event_id", flat=True)
        .distinct()
    )


def _uuid(raw: Any, *, resource: str) -> UUID:
    try:
        return UUID(str(raw))
    except ValueError:
        raise IntegrationNotFound(resource=resource) from None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None

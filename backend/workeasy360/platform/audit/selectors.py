"""Audit reads: bounded, keyset-paginated, no secrets.

Run inside the caller's tenant scope, so an organization's page holds only
its own entries (RLS), and the control-plane page (the platform organization's
scope) holds only control-plane entries.
"""

from __future__ import annotations

import operator
from collections.abc import Iterable
from functools import reduce
from typing import Any

from django.db.models import Q

from workeasy360.contracts.values.cursor import (
    Page,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)


def audit_page(
    *,
    action_prefix: str | None = None,
    actions: Iterable[str] | None = None,
    resource_ids: Iterable[str] | None = None,
    changes_match: tuple[str, str] | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> Page:
    """Newest-first audit entries in the current scope.

    ``changes`` is included (it was redacted when written); IP hashes and the
    hash chain are not - they are integrity metadata, not something a reader
    needs.

    ``actions`` is an exact allow-list of action names (the partner Notification
    Center shows only the kinds a person's permissions allow).

    ``resource_ids`` and ``changes_match`` narrow the page to entries *about*
    something (Phase 10: one partner's activity) without a second audit store:
    an entry matches when its ``resource_id`` is one of ``resource_ids`` or its
    ``changes`` carry ``key == value``. Both are compared for equality against
    bound parameters; neither is ever interpreted.
    """
    from workeasy360.platform.audit.models import AuditEvent

    limit = clamp_limit(limit, default=25, maximum=100)
    queryset = AuditEvent.objects.order_by("-occurred_at", "-id")
    if action_prefix:
        queryset = queryset.filter(action__startswith=action_prefix[:64])
    if actions is not None:
        # An exact allow-list: an empty one matches nothing (never "everything").
        queryset = queryset.filter(action__in=[str(a)[:64] for a in actions][:100])
    about: list[Q] = []
    if resource_ids is not None:
        about.append(Q(resource_id__in=[str(i) for i in resource_ids][:50]))
    if changes_match is not None:
        key, value = changes_match
        about.append(Q(**{f"changes__{key}": value}))
    if about:
        queryset = queryset.filter(reduce(operator.or_, about))
    decoded = decode_cursor(cursor)
    if decoded is not None:
        stamp, last_id = decoded
        queryset = queryset.filter(
            Q(occurred_at__lt=stamp) | Q(occurred_at=stamp, id__lt=last_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    items: list[dict[str, Any]] = [
        {
            "id": str(r.id),
            "occurred_at": r.occurred_at.isoformat(),
            "action": r.action,
            "actor_kind": r.actor_kind,
            "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "outcome": r.outcome,
            "reason": r.reason,
            "changes": r.changes,
        }
        for r in rows
    ]
    return Page(
        items=items,
        next_cursor=encode_cursor(rows[-1].occurred_at, rows[-1].id)
        if has_more
        else None,
        has_more=has_more,
    )

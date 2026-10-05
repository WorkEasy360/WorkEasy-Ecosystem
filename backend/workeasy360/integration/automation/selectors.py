"""Read queries over rules and executions, returning plain data (F-6)."""

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
from workeasy360.integration.automation.models import (
    AutomationExecution,
    AutomationRule,
    AutomationRuleVersion,
    RuleStatus,
    TriggerKind,
)
from workeasy360.integration.automation.permissions import AUTOMATION_VIEW
from workeasy360.platform.authz import services as authz

_STATUS_FILTERS = frozenset(s.value for s in RuleStatus)


def rule_dto(
    rule: AutomationRule, *, definition: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description,
        "status": rule.status,
        "revision": rule.revision,
        "trigger_kind": rule.trigger_kind,
        "trigger_filter": rule.trigger_filter,
        "definition": definition,
        "next_run_at": _iso(rule.next_run_at),
        "last_fired_for": _iso(rule.last_fired_for),
        "activated_at": _iso(rule.activated_at),
        "created_at": _iso(rule.created_at),
        "updated_at": _iso(rule.updated_at),
    }


def current_definition(rule: AutomationRule) -> dict[str, Any]:
    if rule.current_version_id is None:  # pragma: no cover - created atomically
        return {}
    return dict(
        AutomationRuleVersion.objects.filter(id=rule.current_version_id)
        .values_list("definition", flat=True)
        .first()
        or {}
    )


# --- customer reads ----------------------------------------------------------------


def rules_page(
    ctx: TenantContext,
    *,
    limit: Any = None,
    cursor: str | None = None,
    status: str | None = None,
) -> Page:
    authz.require(ctx, AUTOMATION_VIEW)
    size = clamp_limit(limit)
    rows = AutomationRule.objects.all()
    if status in _STATUS_FILTERS:
        rows = rows.filter(status=status)
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
        # The list omits definitions: they are fetched per rule, on demand,
        # keeping the list one query regardless of rule size.
        items=[rule_dto(r, definition=None) for r in batch],
        next_cursor=encode_cursor(batch[-1].created_at, batch[-1].id)
        if has_more
        else None,
        has_more=has_more,
    )


def rule_detail(ctx: TenantContext, rule_id: Any) -> dict[str, Any]:
    authz.require(ctx, AUTOMATION_VIEW)
    rule = _get(rule_id)
    return rule_dto(rule, definition=current_definition(rule))


def executions_page(
    ctx: TenantContext, rule_id: Any, *, limit: Any = None, cursor: str | None = None
) -> Page:
    authz.require(ctx, AUTOMATION_VIEW)
    rule = _get(rule_id)
    size = clamp_limit(limit)
    rows = AutomationExecution.objects.filter(rule_id=rule.id)
    position = decode_cursor(cursor)
    if position is not None:
        created_at, last_id = position
        rows = rows.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=last_id)
        )
    batch = list(
        rows.order_by("-created_at", "-id")
        .select_related("rule_version")
        .only(
            "id",
            "status",
            "attempts",
            "conditions_matched",
            "failure_category",
            "action_results",
            "chain_depth",
            "correlation_id",
            "trigger_event_id",
            "started_at",
            "finished_at",
            "created_at",
            "rule_version__version",
        )[: size + 1]
    )
    has_more = len(batch) > size
    batch = batch[:size]
    from workeasy360.integration.events.selectors import event_summaries

    events = event_summaries(e.trigger_event_id for e in batch)
    return Page(
        items=[
            {
                "id": str(e.id),
                "status": e.status,
                "rule_version": e.rule_version.version,
                "attempts": e.attempts,
                "conditions_matched": e.conditions_matched,
                "failure_category": e.failure_category,
                "action_results": e.action_results,
                "chain_depth": e.chain_depth,
                "correlation_id": str(e.correlation_id) if e.correlation_id else None,
                "event": events.get(e.trigger_event_id),
                "started_at": _iso(e.started_at),
                "finished_at": _iso(e.finished_at),
                "created_at": _iso(e.created_at),
            }
            for e in batch
        ],
        next_cursor=encode_cursor(batch[-1].created_at, batch[-1].id)
        if has_more
        else None,
        has_more=has_more,
    )


# --- system reads ------------------------------------------------------------------


def matching_rules(candidate_filters: Iterable[str]) -> list[Any]:
    """Active event-triggered rules whose filter matches - one indexed query
    over ``ix_rule_trigger``, however many rules exist (Task 74)."""
    from workeasy360.integration.automation.services import MatchedRule

    filters = list(candidate_filters)
    if not filters:
        return []
    return [
        MatchedRule(id=rid, name=name, current_version_id=vid)
        for rid, name, vid in AutomationRule.objects.filter(
            status=RuleStatus.ACTIVE.value,
            trigger_kind=TriggerKind.EVENT.value,
            trigger_filter__in=filters,
            current_version__isnull=False,
        ).values_list("id", "name", "current_version_id")
    ]


def recent_execution_counts(rule_ids: list[UUID], *, since: datetime) -> dict[UUID, int]:
    if not rule_ids:
        return {}
    return dict(
        AutomationExecution.objects.filter(rule_id__in=rule_ids, created_at__gte=since)
        .exclude(failure_category="rate_limited")
        .values("rule_id")
        .annotate(n=Count("id"))
        .values_list("rule_id", "n")
    )


def event_pks_in_use(event_pks: Iterable[UUID]) -> set[UUID]:
    wanted = list(set(event_pks))
    if not wanted:
        return set()
    return set(
        AutomationExecution.objects.filter(trigger_event_id__in=wanted)
        .values_list("trigger_event_id", flat=True)
        .distinct()
    )


def _get(rule_id: Any) -> AutomationRule:
    try:
        key = UUID(str(rule_id))
    except ValueError:
        raise IntegrationNotFound(resource="automation_rule") from None
    rule = AutomationRule.objects.filter(id=key).first()
    if rule is None:
        raise IntegrationNotFound(resource="automation_rule")
    return rule


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None

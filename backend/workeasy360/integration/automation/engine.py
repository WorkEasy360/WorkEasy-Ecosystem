"""The automation engine: evaluate one execution and run its actions.

::

    tx 1   lock execution -> claim attempt N (running, lease) -> commit
    tx 2   lock + fence on N -> evaluate conditions (pure) -> run every
           action -> record results -> commit      (all or nothing)
    tx 3   only if tx 2 raised: retry with backoff, or dead after
           AUTOMATION_MAX_ATTEMPTS

**Why the actions share one transaction.** Every action's effect is a
database record - a delivery the webhook pipeline will send, a notification
the notification service will send - never a network call (Task 40). So an
execution either commits *all* its effects together with its own status, or
none of them: a crash midway rolls the lot back and the retry starts clean.
Nothing external has happened yet at commit time; the external side effects
are then carried out by pipelines with their own at-least-once retries.

**Version pinning (Task 39).** The execution runs the rule *version* it
was created with. Editing the rule meanwhile creates a new version and
changes nothing here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from workeasy360.contracts.values.backoff import equal_jitter
from workeasy360.integration.automation import conditions as condition_rules
from workeasy360.integration.automation.actions import (
    FAILED,
    ActionInvocation,
    get_action,
)
from workeasy360.integration.automation.models import (
    AutomationExecution,
    AutomationRule,
    AutomationRuleVersion,
    ExecutionStatus,
)
from workeasy360.integration.automation.services import EXECUTION_TIMER
from workeasy360.integration.events.selectors import routed_event
from workeasy360.integration.scheduling import services as scheduling
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Claimed:
    execution_id: UUID
    attempt: int


def run_execution(execution_id: Any) -> str:
    from workeasy360.platform.organizations.selectors import organization_status
    from workeasy360.platform.tenancy.context import current_tenant, tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    dispatched_ctx = current_tenant()
    organization_id = dispatched_ctx.organization_id
    try:
        key = UUID(str(execution_id))
    except ValueError:
        return "missing"
    live = organization_status(organization_id)
    if live is None:
        scheduling.complete(EXECUTION_TIMER, key)
        return "organization_gone"

    ctx = dispatched_ctx.replace_status(live)
    with tenant_context(ctx):
        with organization_db_scope(organization_id):
            claimed = _claim(key, organization_id, writable=live.allows_write)
        if isinstance(claimed, str):
            return claimed
        try:
            with organization_db_scope(organization_id):
                return _run(claimed, organization_id, ctx)
        except Exception as exc:
            logger.error(
                "automation.execution.error",
                execution_id=str(claimed.execution_id),
                error_type=type(exc).__name__,
            )
            with organization_db_scope(organization_id):
                return _retry_or_dead(claimed, organization_id)


def _claim(
    execution_id: UUID, organization_id: UUID, *, writable: bool
) -> _Claimed | str:
    now = timezone.now()
    execution = (
        AutomationExecution.objects.select_for_update().filter(id=execution_id).first()
    )
    if execution is None:
        scheduling.complete(EXECUTION_TIMER, execution_id)
        return "missing"
    status = execution.status_enum
    if status.is_terminal:
        scheduling.complete(EXECUTION_TIMER, execution_id)
        return status.value
    if (
        status is ExecutionStatus.RUNNING
        and execution.lease_expires_at is not None
        and execution.lease_expires_at > now
    ):
        return "in_flight"
    if status is not ExecutionStatus.RUNNING and execution.next_attempt_at > now:
        _timer(organization_id, execution_id, execution.next_attempt_at)
        return "not_due"
    if not writable:
        # A suspended organization starts no new side effects (Task 54).
        _timer(
            organization_id,
            execution_id,
            now + timezone.timedelta(seconds=float(settings.WEBHOOK_HOLD_RECHECK_S)),
        )
        return "held_organization"
    if execution.attempts >= int(settings.AUTOMATION_MAX_ATTEMPTS):
        return _finish_dead(execution_id, "retries_exhausted", now)

    attempt = execution.attempts + 1
    lease = now + timezone.timedelta(seconds=int(settings.AUTOMATION_LEASE_S))
    AutomationExecution.objects.filter(id=execution_id).update(
        status=ExecutionStatus.RUNNING.value,
        attempts=attempt,
        lease_expires_at=lease,
        started_at=execution.started_at or now,
        updated_at=now,
    )
    _timer(organization_id, execution_id, lease + timezone.timedelta(seconds=5))
    return _Claimed(execution_id=execution_id, attempt=attempt)


def _run(claimed: _Claimed, organization_id: UUID, ctx: Any) -> str:
    from workeasy360.integration.webhooks import tasks as webhook_tasks

    now = timezone.now()
    execution = (
        AutomationExecution.objects.select_for_update()
        .filter(
            id=claimed.execution_id,
            status=ExecutionStatus.RUNNING.value,
            attempts=claimed.attempt,
        )
        .first()
    )
    if execution is None:
        metrics.increment("automation_late_completions_total")
        return "stale"

    event = routed_event(execution.trigger_event_id)
    if event is None:  # pragma: no cover - PROTECT keeps it while referenced
        return _finish_dead(execution.id, "event_expired", now)
    definition = (
        AutomationRuleVersion.objects.filter(id=execution.rule_version_id)
        .values_list("definition", flat=True)
        .first()
    )
    rule_name = (
        AutomationRule.objects.filter(id=execution.rule_id)
        .values_list("name", flat=True)
        .first()
        or ""
    )

    matched = condition_rules.evaluate(
        definition.get("conditions") or {},
        event={
            "type": event.event_type,
            "version": event.event_version,
            "source": event.source,
        },
        payload=event.payload,
    )
    if not matched:
        AutomationExecution.objects.filter(id=execution.id).update(
            status=ExecutionStatus.SKIPPED.value,
            conditions_matched=False,
            lease_expires_at=None,
            finished_at=now,
            updated_at=now,
        )
        scheduling.complete(EXECUTION_TIMER, execution.id)
        metrics.increment("automation_executions_total", result="skipped")
        return ExecutionStatus.SKIPPED.value

    results: list[dict[str, Any]] = []
    deliveries: list[str] = []
    for action in definition.get("actions") or []:
        spec = get_action(action["type"])
        if spec is None:
            results.append(
                {
                    "action": action["id"],
                    "type": action["type"],
                    "status": FAILED,
                    "detail": "unknown_action",
                }
            )
            continue
        # A transient error raises out of here and rolls back every effect
        # this attempt created; the retry starts from nothing (module doc).
        outcome = spec.execute(
            ActionInvocation(
                organization_id=organization_id,
                execution_id=execution.id,
                rule_id=execution.rule_id,
                rule_name=rule_name,
                action_key=action["id"],
                event=event,
                config=action.get("config") or {},
            )
        )
        results.append(
            {
                "action": action["id"],
                "type": action["type"],
                "status": outcome.status,
                "reference": outcome.reference,
                "detail": outcome.detail,
            }
        )
        if outcome.reference.startswith("delivery:"):
            deliveries.append(outcome.reference.split(":", 1)[1])

    failed = any(r["status"] == FAILED for r in results)
    final = ExecutionStatus.FAILED if failed else ExecutionStatus.SUCCEEDED
    AutomationExecution.objects.filter(id=execution.id).update(
        status=final.value,
        conditions_matched=True,
        action_results=results,
        failure_category="action_failed" if failed else "",
        lease_expires_at=None,
        finished_at=now,
        updated_at=now,
    )
    scheduling.complete(EXECUTION_TIMER, execution.id)
    scheduling.enqueue_after_commit(webhook_tasks.deliver, deliveries, ctx=ctx)
    metrics.increment("automation_executions_total", result=final.value)
    logger.info(
        "automation.execution.finished",
        execution_id=str(execution.id),
        rule_id=str(execution.rule_id),
        event_id=str(event.event_id),
        correlation_id=str(event.correlation_id),
        status=final.value,
        actions=len(results),
    )
    return final.value


def _retry_or_dead(claimed: _Claimed, organization_id: UUID) -> str:
    now = timezone.now()
    if claimed.attempt >= int(settings.AUTOMATION_MAX_ATTEMPTS):
        updated = AutomationExecution.objects.filter(
            id=claimed.execution_id,
            status=ExecutionStatus.RUNNING.value,
            attempts=claimed.attempt,
        ).update(
            status=ExecutionStatus.DEAD.value,
            failure_category="retries_exhausted",
            lease_expires_at=None,
            finished_at=now,
            updated_at=now,
        )
        if updated:
            scheduling.complete(EXECUTION_TIMER, claimed.execution_id)
            metrics.increment("automation_executions_total", result="dead")
        return ExecutionStatus.DEAD.value

    retry_at = now + timezone.timedelta(
        seconds=equal_jitter(
            claimed.attempt - 1,
            base=float(settings.AUTOMATION_RETRY_BASE_S),
            cap=float(settings.AUTOMATION_RETRY_CAP_S),
        )
    )
    updated = AutomationExecution.objects.filter(
        id=claimed.execution_id,
        status=ExecutionStatus.RUNNING.value,
        attempts=claimed.attempt,
    ).update(
        status=ExecutionStatus.RETRY.value,
        next_attempt_at=retry_at,
        lease_expires_at=None,
        updated_at=now,
    )
    if updated:
        _timer(organization_id, claimed.execution_id, retry_at)
        metrics.increment("automation_executions_total", result="retry")
    return ExecutionStatus.RETRY.value


def _finish_dead(execution_id: UUID, category: str, now: Any) -> str:
    AutomationExecution.objects.filter(id=execution_id).update(
        status=ExecutionStatus.DEAD.value,
        failure_category=category,
        lease_expires_at=None,
        finished_at=now,
        updated_at=now,
    )
    scheduling.complete(EXECUTION_TIMER, execution_id)
    metrics.increment("automation_executions_total", result="dead")
    return ExecutionStatus.DEAD.value


def _timer(organization_id: UUID, execution_id: UUID, due_at: Any) -> None:
    scheduling.schedule(
        EXECUTION_TIMER,
        organization_id=organization_id,
        target_id=execution_id,
        due_at=due_at,
    )

"""Automation rules - the application services.

Customer functions: authorize, validate the *whole* definition (trigger,
conditions, every action's config), and only then write, in one atomic block
(D-016). Every edit creates a new immutable version; the rule head points at
it. Every change names the ``revision`` it read (optimistic concurrency).

System functions: execution creation for routing and for schedules, and the
schedule firing itself. Both are idempotent by unique constraint
(``(rule, trigger_event)``) and by a deterministic schedule event id, so a
duplicate event, a re-run router or two workers firing one schedule produce
exactly one execution (Tasks 10, 44).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from workeasy360.contracts.errors import (
    IntegrationConflict,
    IntegrationNotFound,
    IntegrationValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.automation import conditions as condition_rules
from workeasy360.integration.automation import schedules
from workeasy360.integration.automation.actions import get_action
from workeasy360.integration.automation.models import (
    RULE_TRANSITIONS,
    AutomationExecution,
    AutomationRule,
    AutomationRuleVersion,
    ExecutionStatus,
    RuleStatus,
    TriggerKind,
)
from workeasy360.integration.automation.permissions import AUTOMATION_MANAGE
from workeasy360.integration.events.selectors import RoutedEventDTO
from workeasy360.integration.scheduling import services as scheduling
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

RESOURCE: Final = "integration.automation_rule"
EXECUTION_TIMER: Final = "automation.execution"
SCHEDULE_TIMER: Final = "automation.schedule"
MAX_NAME_LENGTH: Final = 120
MAX_DESCRIPTION_LENGTH: Final = 500

#: Namespace for deterministic schedule-firing event ids: one occurrence of
#: one rule is one event id, whichever worker fires it.
_SCHEDULE_NAMESPACE: Final = uuid.UUID("7f3b1e44-2c6d-4f7e-9a53-0d6c1b2e8a91")

_TRANSITION_ACTIONS: Final = {
    RuleStatus.ACTIVE: "integration.automation.activated",
    RuleStatus.PAUSED: "integration.automation.paused",
    RuleStatus.ARCHIVED: "integration.automation.archived",
}


@dataclass(frozen=True, slots=True)
class MatchedRule:
    id: UUID
    name: str
    current_version_id: UUID


# --- definition ---------------------------------------------------------------------


def normalize_definition(ctx: TenantContext, raw: Any) -> dict[str, Any]:
    """Validate a rule definition in full and return its canonical form.

    Everything a rule can do is checked here - the trigger against the
    organization's event catalogue, each condition against the grammar, each
    action by its own registered validator - so an invalid rule is refused
    before anything is written, and a stored version is always runnable.
    """
    from workeasy360.integration.events.catalog import validate_filter

    if not isinstance(raw, Mapping):
        raise IntegrationValidationError(
            "definition must be an object.", field="definition"
        )
    unknown = sorted(set(raw) - {"trigger", "conditions", "actions"})
    if unknown:
        raise IntegrationValidationError(
            f"Unknown definition field(s): {', '.join(unknown)}.", field="definition"
        )

    trigger = raw.get("trigger")
    if not isinstance(trigger, Mapping):
        raise IntegrationValidationError("A trigger is required.", field="trigger")
    kind = trigger.get("kind")
    if kind == TriggerKind.EVENT.value:
        if set(trigger) - {"kind", "event"}:
            raise IntegrationValidationError("Unknown trigger field.", field="trigger")
        event_filter = validate_filter(
            ctx.organization_id, trigger.get("event"), field="trigger.event"
        ).value
        normalized_trigger: dict[str, Any] = {"kind": kind, "event": event_filter}
    elif kind == TriggerKind.SCHEDULE.value:
        if set(trigger) - {"kind", "schedule"}:
            raise IntegrationValidationError("Unknown trigger field.", field="trigger")
        try:
            schedule = schedules.normalize(
                trigger.get("schedule"),
                min_interval_minutes=int(settings.AUTOMATION_MIN_INTERVAL_MINUTES),
            )
        except schedules.ScheduleError as exc:
            raise IntegrationValidationError(exc.message, field=exc.field) from None
        normalized_trigger = {"kind": kind, "schedule": schedule}
    else:
        raise IntegrationValidationError(
            "trigger.kind must be 'event' or 'schedule'.", field="trigger.kind"
        )

    try:
        normalized_conditions = condition_rules.normalize(raw.get("conditions"))
    except condition_rules.ConditionError as exc:
        raise IntegrationValidationError(exc.message, field=exc.field) from None

    actions = raw.get("actions")
    limit = int(settings.AUTOMATION_MAX_ACTIONS)
    if not isinstance(actions, list) or not actions or len(actions) > limit:
        raise IntegrationValidationError(
            f"A rule has 1 to {limit} actions.", field="actions"
        )
    normalized_actions = []
    for index, action in enumerate(actions):
        field = f"actions[{index}]"
        if not isinstance(action, Mapping) or set(action) - {"type", "config"}:
            raise IntegrationValidationError("An action is {type, config}.", field=field)
        spec = get_action(str(action.get("type", "")))
        if spec is None:
            raise IntegrationValidationError(
                "Unknown action type.", field=f"{field}.type"
            )
        config = action.get("config") or {}
        try:
            normalized_config = spec.validate(ctx, config)
        except IntegrationValidationError as exc:
            inner = exc.context.get("field") or "config"
            raise IntegrationValidationError(
                exc.message, field=f"{field}.{inner}"
            ) from None
        normalized_actions.append(
            {
                # Stable within a version; the idempotency key of the
                # action's effect is (execution_id, id) - Task 37.
                "id": f"a{index + 1}",
                "type": spec.key,
                "version": spec.version,
                "config": normalized_config,
            }
        )

    return {
        "trigger": normalized_trigger,
        "conditions": normalized_conditions,
        "actions": normalized_actions,
    }


# --- customer: create, edit, lifecycle ------------------------------------------------


def create_rule(
    ctx: TenantContext, *, name: Any, description: Any = "", definition: Any
) -> Any:
    from workeasy360.integration.automation.selectors import rule_dto

    authz.require(ctx, AUTOMATION_MANAGE)
    clean_name = _validated_name(name)
    clean_description = _validated_description(description)
    normalized = normalize_definition(ctx, definition)
    limit = int(settings.AUTOMATION_MAX_RULES)
    if AutomationRule.objects.exclude(status=RuleStatus.ARCHIVED.value).count() >= limit:
        raise IntegrationConflict(
            f"This organization already has {limit} automation rules. Archive one first."
        )

    with transaction.atomic():
        rule = AutomationRule(
            name=clean_name,
            description=clean_description,
            status=RuleStatus.DRAFT.value,
            trigger_kind=normalized["trigger"]["kind"],
            trigger_filter=normalized["trigger"].get("event", ""),
            created_by_id=ctx.user_id,
        )
        rule.save()
        version = AutomationRuleVersion(
            rule_id=rule.id, version=1, definition=normalized, created_by_id=ctx.user_id
        )
        version.save()
        AutomationRule.objects.filter(id=rule.id).update(current_version_id=version.id)
        audit.record(
            ctx,
            "integration.automation.created",
            resource_type=RESOURCE,
            resource_id=rule.id,
            changes={"name": clean_name, "trigger": normalized["trigger"]},
        )
    rule.refresh_from_db()
    return rule_dto(rule, definition=normalized)


def update_rule(
    ctx: TenantContext,
    rule_id: Any,
    *,
    expected_revision: Any,
    name: Any = None,
    description: Any = None,
    definition: Any = None,
) -> Any:
    """Edit a rule. A changed definition becomes a new immutable version;
    executions already created keep running the version they pinned."""
    from workeasy360.integration.automation.selectors import current_definition, rule_dto

    authz.require(ctx, AUTOMATION_MANAGE)
    rule = _load(rule_id)
    revision = _validated_revision(expected_revision)
    if rule.status_enum is RuleStatus.ARCHIVED:
        raise IntegrationConflict("An archived rule cannot be changed.")

    fields: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    if name is not None:
        clean = _validated_name(name)
        if clean != rule.name:
            fields["name"], changes["name"] = clean, clean
    if description is not None:
        clean = _validated_description(description)
        if clean != rule.description:
            fields["description"] = clean
            changes["description"] = "changed"
    normalized = None
    existing = current_definition(rule)
    if definition is not None:
        normalized = normalize_definition(ctx, definition)
        if normalized == existing:
            normalized = None
    if not fields and normalized is None:
        return rule_dto(rule, definition=existing)

    now = timezone.now()
    with transaction.atomic():
        locked = AutomationRule.objects.select_for_update().filter(id=rule.id).first()
        if locked is None or locked.revision != revision:
            raise IntegrationConflict(
                "This rule was changed by someone else. Reload and retry.",
                resource="automation_rule",
            )
        if normalized is not None:
            version_number = (
                locked.current_version.version if locked.current_version else 0
            ) + 1
            version = AutomationRuleVersion(
                rule_id=rule.id,
                version=version_number,
                definition=normalized,
                created_by_id=ctx.user_id,
            )
            version.save()
            fields.update(
                current_version_id=version.id,
                trigger_kind=normalized["trigger"]["kind"],
                trigger_filter=normalized["trigger"].get("event", ""),
            )
            changes["version"] = version_number
            changes["trigger"] = normalized["trigger"]
            if locked.status_enum is RuleStatus.ACTIVE:
                fields["next_run_at"] = _next_run(normalized, now=now)
        AutomationRule.objects.filter(id=rule.id).update(
            **fields, revision=F("revision") + 1, updated_at=now
        )
        if "next_run_at" in fields:
            _arm_schedule_timer(rule.id, fields["next_run_at"])
        audit.record(
            ctx,
            "integration.automation.updated",
            resource_type=RESOURCE,
            resource_id=rule.id,
            changes=changes,
        )
    rule.refresh_from_db()
    return rule_dto(rule, definition=normalized or existing)


def transition_rule(
    ctx: TenantContext, rule_id: Any, *, to_status: Any, expected_revision: Any
) -> Any:
    """Activate, pause or archive. Activation re-validates the current
    definition against *today's* state - a connection disabled or a product
    no longer entitled since the rule was written blocks activation rather
    than producing executions that can only fail."""
    from workeasy360.integration.automation.selectors import current_definition, rule_dto

    authz.require(ctx, AUTOMATION_MANAGE)
    try:
        target = RuleStatus(str(to_status))
    except ValueError:
        raise IntegrationValidationError("Unknown rule status.", field="status") from None
    revision = _validated_revision(expected_revision)
    rule = _load(rule_id)
    current = rule.status_enum
    if target not in RULE_TRANSITIONS[current]:
        raise IntegrationConflict(f"A {current.value} rule cannot become {target.value}.")

    definition = current_definition(rule)
    if target is RuleStatus.ACTIVE:
        normalize_definition(ctx, _as_submitted(definition))

    now = timezone.now()
    fields: dict[str, Any] = {"status": target.value, "updated_at": now}
    if target is RuleStatus.ACTIVE:
        fields["next_run_at"] = _next_run(definition, now=now)
        if rule.activated_at is None:
            fields["activated_at"] = now
    else:
        fields["next_run_at"] = None

    with transaction.atomic():
        # Conditional on both status and revision: of two racing lifecycle
        # changes (or a change racing an edit), exactly one matches a row.
        if not AutomationRule.objects.filter(
            id=rule.id, status=current.value, revision=revision
        ).update(**fields, revision=F("revision") + 1):
            raise IntegrationConflict(
                "This rule was changed concurrently. Reload and retry.",
                resource="automation_rule",
            )
        # Only after the conditional update matched: nothing is written
        # ahead of a 409 (D-016).
        _arm_schedule_timer(rule.id, fields["next_run_at"])
        audit.record(
            ctx,
            _TRANSITION_ACTIONS[target],
            resource_type=RESOURCE,
            resource_id=rule.id,
            changes={"from": current.value, "to": target.value},
        )
    rule.refresh_from_db()
    return rule_dto(rule, definition=definition)


# --- system: executions ---------------------------------------------------------------


def create_executions(event: RoutedEventDTO, rules: list[MatchedRule]) -> list[UUID]:
    """One execution per matched rule for ``event``; returns those to run.

    Two bounds are applied here rather than at run time, so a stopped
    execution is visible (dead, with a reason) instead of never existing:

    - **loop protection (Task 46)** - an event more than
      ``AUTOMATION_MAX_CHAIN_DEPTH`` automation-driven hops from its origin
      starts nothing;
    - **per-rule rate cap** - a rule already at
      ``AUTOMATION_MAX_EXECUTIONS_PER_RULE_PER_HOUR`` is not run again this
      hour. This is the loop breaker that works even when a chain passes
      through an external system that does not propagate causation.
    """
    from workeasy360.integration.automation.selectors import recent_execution_counts

    if not rules:
        return []
    now = timezone.now()
    depth_exceeded = event.chain_depth > int(settings.AUTOMATION_MAX_CHAIN_DEPTH)
    cap = int(settings.AUTOMATION_MAX_EXECUTIONS_PER_RULE_PER_HOUR)
    counts = recent_execution_counts(
        [r.id for r in rules], since=now - timezone.timedelta(hours=1)
    )

    rows = []
    for rule in rules:
        row = AutomationExecution(
            organization_id=event.organization_id,
            rule_id=rule.id,
            rule_version_id=rule.current_version_id,
            trigger_event_id=event.id,
            next_attempt_at=now,
            chain_depth=event.chain_depth,
            correlation_id=event.correlation_id,
        )
        reason = ""
        if depth_exceeded:
            reason = "chain_depth_exceeded"
        elif counts.get(rule.id, 0) >= cap:
            reason = "rate_limited"
        if reason:
            row.status = ExecutionStatus.DEAD.value
            row.failure_category = reason
            row.finished_at = now
            metrics.increment("automation_executions_total", result=reason)
        rows.append(row)
    AutomationExecution.objects.bulk_create(rows, ignore_conflicts=True)

    pending = list(
        AutomationExecution.objects.filter(
            trigger_event_id=event.id,
            rule_id__in=[r.id for r in rules],
            status=ExecutionStatus.PENDING.value,
        ).values_list("id", flat=True)
    )
    grace = timezone.timedelta(seconds=float(settings.WEBHOOK_DISPATCH_GRACE_S))
    scheduling.schedule_many(
        EXECUTION_TIMER,
        [(event.organization_id, execution_id) for execution_id in pending],
        due_at=now + grace,
    )
    return pending


def fire_schedule(rule_id: Any) -> tuple[str, list[UUID]]:
    """Fire one scheduled rule if it is due. Runs in the rule's organization
    scope, inside a transaction, with the routing context.

    Claiming is the rule row lock plus ``next_run_at``: two workers firing
    the same rule serialise on the lock, and the second sees ``next_run_at``
    already advanced. The deterministic event id and the ``(rule, event)``
    unique constraint make even a lock-free double fire one execution
    (Task 44). A run missed during downtime is coalesced (Task 45).
    """
    from workeasy360.contracts.events.integration_events import SCHEDULE_FIRED_V1
    from workeasy360.integration.automation.selectors import current_definition
    from workeasy360.integration.events.services import record_platform_event

    try:
        key = UUID(str(rule_id))
    except ValueError:
        return "missing", []
    rule = AutomationRule.objects.select_for_update().filter(id=key).first()
    if (
        rule is None
        or rule.status_enum is not RuleStatus.ACTIVE
        or rule.trigger_kind != TriggerKind.SCHEDULE.value
    ):
        scheduling.complete(SCHEDULE_TIMER, key)
        return "inactive", []

    now = timezone.now()
    definition = current_definition(rule)
    schedule = definition["trigger"]["schedule"]
    due = rule.next_run_at or schedules.next_after(schedule, now)
    if due > now:
        scheduling.schedule(
            SCHEDULE_TIMER,
            organization_id=rule.organization_id,
            target_id=rule.id,
            due_at=due,
        )
        if rule.next_run_at is None:
            AutomationRule.objects.filter(id=rule.id).update(next_run_at=due)
        return "not_due", []

    fire_for, missed, next_run = schedules.coalesce(schedule, due=due, now=now)
    event, _created = record_platform_event(
        SCHEDULE_FIRED_V1.event_type,
        SCHEDULE_FIRED_V1.version,
        {
            "rule_id": str(rule.id),
            "scheduled_for": fire_for.isoformat(),
            "missed_occurrences": missed,
        },
        event_id=uuid.uuid5(_SCHEDULE_NAMESPACE, f"{rule.id}:{fire_for.isoformat()}"),
        direct=True,
    )
    pending = create_executions(
        event,
        [
            MatchedRule(
                id=rule.id, name=rule.name, current_version_id=rule.current_version_id
            )
        ],
    )
    AutomationRule.objects.filter(id=rule.id).update(
        next_run_at=next_run, last_fired_for=fire_for, updated_at=now
    )
    scheduling.schedule(
        SCHEDULE_TIMER,
        organization_id=rule.organization_id,
        target_id=rule.id,
        due_at=next_run,
    )
    if missed:
        metrics.increment("automation_schedule_missed_total", value=float(missed))
    metrics.increment("automation_schedule_fired_total")
    return "fired", pending


def prune_executions(*, before: Any, limit: int = 1000) -> int:
    """Delete terminal executions older than ``before``, in bounded batches."""
    terminal = [s.value for s in ExecutionStatus if s.is_terminal]
    total = 0
    while True:
        ids = list(
            AutomationExecution.objects.filter(status__in=terminal, created_at__lt=before)
            .order_by("created_at")
            .values_list("id", flat=True)[:limit]
        )
        if not ids:
            return total
        deleted, _ = AutomationExecution.objects.filter(id__in=ids).delete()
        total += deleted
        if len(ids) < limit:
            return total


# --- internals ----------------------------------------------------------------------------


def _as_submitted(definition: Mapping[str, Any]) -> dict[str, Any]:
    """A stored (normalised) definition in the shape a caller submits, so it
    can be re-validated against today's state. Action ids and versions are
    assigned by normalisation, never accepted from a caller."""
    return {
        "trigger": definition["trigger"],
        "conditions": definition["conditions"],
        "actions": [
            {"type": a["type"], "config": a.get("config") or {}}
            for a in definition.get("actions") or []
        ],
    }


def _next_run(definition: Mapping[str, Any], *, now: Any) -> Any:
    """When an active rule next runs: ``None`` for an event-triggered rule."""
    trigger = definition["trigger"]
    if trigger["kind"] != TriggerKind.SCHEDULE.value:
        return None
    return schedules.next_after(trigger["schedule"], now)


def _arm_schedule_timer(rule_id: UUID, next_run: Any) -> None:
    """Set (or clear) the timer that fires a schedule rule. Called inside the
    caller's transaction, after its conditional write succeeded, so the
    timer commits - or rolls back - with the rule."""
    if next_run is None:
        scheduling.complete(SCHEDULE_TIMER, rule_id)
        return
    from workeasy360.platform.tenancy.context import current_tenant

    scheduling.schedule(
        SCHEDULE_TIMER,
        organization_id=current_tenant().organization_id,
        target_id=rule_id,
        due_at=next_run,
    )


def _load(rule_id: Any) -> AutomationRule:
    try:
        key = UUID(str(rule_id))
    except ValueError:
        raise IntegrationNotFound(resource="automation_rule") from None
    rule = AutomationRule.objects.select_related("current_version").filter(id=key).first()
    if rule is None:
        raise IntegrationNotFound(resource="automation_rule")
    return rule


def _validated_name(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise IntegrationValidationError("A name is required.", field="name")
    name = " ".join(raw.split())
    if len(name) > MAX_NAME_LENGTH:
        raise IntegrationValidationError(
            f"The name may be at most {MAX_NAME_LENGTH} characters.", field="name"
        )
    return name


def _validated_description(raw: Any) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str) or len(raw) > MAX_DESCRIPTION_LENGTH:
        raise IntegrationValidationError(
            f"The description may be at most {MAX_DESCRIPTION_LENGTH} characters.",
            field="description",
        )
    return raw.strip()


def _validated_revision(raw: Any) -> int:
    if isinstance(raw, bool):
        raise IntegrationValidationError("revision is required.", field="revision")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise IntegrationValidationError(
            "revision is required.", field="revision"
        ) from None

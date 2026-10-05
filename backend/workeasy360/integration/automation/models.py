"""Automation rules, their immutable versions, and executions.

No product-specific field anywhere (Task 31): a rule is a trigger, a
bounded condition set and a list of registered actions, and every one of
those is generic. What a rule *means* for a CRM or a ledger is the
customer's configuration and the product's events, never this schema.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from workeasy360.contracts.enums import StrEnum
from workeasy360.platform.tenancy.models import TenantScopedModel


class RuleStatus(StrEnum):
    """Rule lifecycle (Task 31).

    - ``draft``    editable, never runs.
    - ``active``   runs on its trigger.
    - ``paused``   keeps its configuration, runs nothing; executions already
                   created still finish (they are pinned to a version).
    - ``archived`` terminal; kept for its execution history.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


RULE_TRANSITIONS: dict[RuleStatus, frozenset[RuleStatus]] = {
    RuleStatus.DRAFT: frozenset({RuleStatus.ACTIVE, RuleStatus.ARCHIVED}),
    RuleStatus.ACTIVE: frozenset({RuleStatus.PAUSED, RuleStatus.ARCHIVED}),
    RuleStatus.PAUSED: frozenset({RuleStatus.ACTIVE, RuleStatus.ARCHIVED}),
    RuleStatus.ARCHIVED: frozenset(),
}


class TriggerKind(StrEnum):
    #: An event matching ``trigger_filter`` arrived (Task 32).
    EVENT = "event"
    #: A schedule reached ``next_run_at`` (Task 42).
    SCHEDULE = "schedule"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY = "retry"
    #: Conditions matched and every action completed.
    SUCCEEDED = "succeeded"
    #: Conditions did not match. Nothing was done.
    SKIPPED = "skipped"
    #: Conditions matched and at least one action failed permanently.
    FAILED = "failed"
    #: Stopped without running: loop depth exceeded, rate cap reached,
    #: retries exhausted, or its trigger event is gone.
    DEAD = "dead"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.SKIPPED,
            ExecutionStatus.FAILED,
            ExecutionStatus.DEAD,
        )


OPEN_EXECUTION_STATUSES = (
    ExecutionStatus.PENDING.value,
    ExecutionStatus.RUNNING.value,
    ExecutionStatus.RETRY.value,
)


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(m.value, m.name.title()) for m in enum_cls]


class AutomationRule(TenantScopedModel):
    """The mutable head of a rule. Its behaviour lives in versions."""

    name = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=_choices(RuleStatus), default=RuleStatus.DRAFT.value
    )
    #: Denormalised from the current version for the matching index - the
    #: routing query filters on these two columns and nothing else.
    trigger_kind = models.CharField(max_length=16, choices=_choices(TriggerKind))
    trigger_filter = models.CharField(max_length=160, blank=True, default="")
    current_version = models.ForeignKey(
        "AutomationRuleVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        db_index=False,
    )
    #: Optimistic-concurrency token, bumped by every edit *and* every
    #: lifecycle change. A caller names the revision it read; a stale one is
    #: refused (409), so an activation racing a pause - or an edit racing
    #: either - resolves to exactly one winner (Task 94).
    revision = models.PositiveIntegerField(default=1)

    #: Schedule rules: when the next run is due (UTC). Also mirrored into a
    #: scheduling timer while the rule is active.
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_fired_for = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=False,
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "automation_rule"
        constraints = [
            models.UniqueConstraint(fields=["id", "organization"], name="uq_rule_id_org"),
        ]
        indexes = [
            # The prefiltered trigger index (Task 74): event routing asks
            # "active event rules whose filter is one of <= 2 strings" and
            # never scans rules one by one.
            models.Index(
                fields=["organization", "status", "trigger_kind", "trigger_filter"],
                name="ix_rule_trigger",
            ),
            models.Index(
                fields=["organization", "-created_at", "id"], name="ix_rule_list"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.name} ({self.status})"

    @property
    def status_enum(self) -> RuleStatus:
        return RuleStatus(self.status)


class AutomationRuleVersion(TenantScopedModel):
    """An immutable snapshot of a rule's trigger, conditions and actions.

    Every edit creates a new version; nothing ever changes one (a trigger in
    ``tenancy.0005`` refuses updates to ``rule``, ``version`` and
    ``definition``). An execution pins the version that was current when it
    was created, so editing a rule never changes what an in-flight execution
    does (Task 39).
    """

    rule = models.ForeignKey(
        AutomationRule, on_delete=models.CASCADE, related_name="+", db_index=False
    )
    version = models.PositiveIntegerField()
    definition = models.JSONField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=False,
    )

    class Meta:
        db_table = "automation_rule_version"
        constraints = [
            models.UniqueConstraint(fields=["rule", "version"], name="uq_rule_version"),
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_rule_version_id_org"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.rule_id} v{self.version}"


class AutomationExecution(TenantScopedModel):
    """One evaluation of one rule version against one trigger event."""

    rule = models.ForeignKey(
        AutomationRule, on_delete=models.CASCADE, related_name="+", db_index=False
    )
    rule_version = models.ForeignKey(
        AutomationRuleVersion, on_delete=models.CASCADE, related_name="+", db_index=False
    )
    #: The event that triggered it. PROTECT / ``ON DELETE NO ACTION``: the
    #: event outlives every execution that refers to it.
    trigger_event = models.ForeignKey(
        "events.RoutedEvent", on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=16,
        choices=_choices(ExecutionStatus),
        default=ExecutionStatus.PENDING.value,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    conditions_matched = models.BooleanField(null=True, blank=True)
    #: Bounded code for DEAD/FAILED: ``chain_depth_exceeded``,
    #: ``rate_limited``, ``retries_exhausted``, ``event_expired``,
    #: ``action_failed``.
    failure_category = models.CharField(max_length=48, blank=True, default="")
    #: One entry per action: ``{"action": "a1", "type": ..., "status": ...,
    #: "reference": "delivery:<id>"}``. References, never payload data.
    action_results = models.JSONField(default=list)
    chain_depth = models.PositiveSmallIntegerField(default=0)
    correlation_id = models.UUIDField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "automation_execution"
        constraints = [
            # Exactly one execution per (rule, event): a duplicate event, a
            # re-run of routing or a double schedule firing all collapse here
            # (Tasks 10, 44).
            models.UniqueConstraint(
                fields=["rule", "trigger_event"], name="uq_execution_rule_event"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "rule", "-created_at", "id"],
                name="ix_execution_by_rule",
            ),
            models.Index(
                fields=["organization", "status", "created_at"],
                name="ix_execution_status_age",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"execution {self.id} of {self.rule_id} [{self.status}]"

    @property
    def status_enum(self) -> ExecutionStatus:
        return ExecutionStatus(self.status)

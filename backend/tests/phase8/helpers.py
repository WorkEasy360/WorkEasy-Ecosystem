"""Assertion helpers for the Phase 8 suites: read an organization's rows the
way a test must - both isolation layers entered explicitly - and run the
due-work timers a live deployment's beat would."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from django.utils import timezone


def in_org(organization_id: Any) -> ExitStack:
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    stack = ExitStack()
    stack.enter_context(unscoped_access(reason="phase 8 assertion", module="tests"))
    stack.enter_context(organization_db_scope(organization_id))
    return stack


def fast_forward(organization_id: Any) -> None:
    """Make every timer of the organization due now."""
    from workeasy360.integration.scheduling.models import DueWork

    DueWork.objects.filter(organization_id=organization_id).update(
        due_at=timezone.now() - timezone.timedelta(seconds=1)
    )


def sweep() -> Any:
    from workeasy360.integration.scheduling.tasks import dispatch_due

    return dispatch_due()


def drain(organization_id: Any, rounds: int = 5) -> None:
    for _ in range(rounds):
        fast_forward(organization_id)
        sweep()


def executions(organization_id: Any) -> list[dict[str, Any]]:
    from workeasy360.intelligence.ai.models import AIExecution

    with in_org(organization_id):
        return list(
            AIExecution.unscoped.filter(organization_id=organization_id)
            .order_by("created_at")
            .values()
        )


def spend(organization_id: Any, scope: str = "") -> dict[str, Any] | None:
    from workeasy360.intelligence.ai.models import AIBudgetPeriod

    with in_org(organization_id):
        return (
            AIBudgetPeriod.unscoped.filter(organization_id=organization_id, scope=scope)
            .values()
            .first()
        )

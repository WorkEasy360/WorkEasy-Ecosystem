"""AI retention (Tasks 46, 83, 84), per organization, in bounded batches.

======================  =============================  ==========================
data                    kept                           setting
======================  =============================  ==========================
execution content       until ``expires_at`` (15 min   ``AI_RESULT_RETENTION_S``,
(results, job input)    sync, 24 h async)              ``AI_JOB_RESULT_RETENTION_S``
execution metadata      400 days, then deleted with    ``AI_EXECUTION_RETENTION_DAYS``
+ usage lines           its usage lines
budget periods          36 months                      fixed
======================  =============================  ==========================

Only terminal executions are pruned: a reservation still in flight is the
quota's own bookkeeping and is settled by its timer, never deleted.
"""

from __future__ import annotations

from typing import Final

from django.conf import settings
from django.utils import timezone

from workeasy360.intelligence.ai.domain import ExecutionStatus
from workeasy360.intelligence.ai.models import (
    AIBudgetPeriod,
    AIExecution,
    AIExecutionContent,
)

BUDGET_PERIOD_MONTHS: Final = 36


def prune_organization() -> dict[str, int]:
    """Runs inside one organization's scope (the caller enters it)."""
    now = timezone.now()
    batch = int(settings.AI_PRUNE_BATCH)

    expired_content = list(
        AIExecutionContent.objects.filter(expires_at__lte=now).values_list(
            "id", flat=True
        )[:batch]
    )
    AIExecutionContent.objects.filter(id__in=expired_content).delete()

    cutoff = now - timezone.timedelta(days=int(settings.AI_EXECUTION_RETENTION_DAYS))
    old = list(
        AIExecution.objects.filter(
            created_at__lt=cutoff,
            status__in=[
                ExecutionStatus.SUCCEEDED.value,
                ExecutionStatus.FAILED.value,
                ExecutionStatus.EXPIRED.value,
            ],
        ).values_list("id", flat=True)[:batch]
    )
    AIExecution.objects.filter(id__in=old).delete()

    oldest_period = (now - timezone.timedelta(days=31 * BUDGET_PERIOD_MONTHS)).date()
    periods, _ = AIBudgetPeriod.objects.filter(
        period_start__lt=oldest_period.replace(day=1), reserved_micros=0
    ).delete()
    return {
        "ai_content": len(expired_content),
        "ai_executions": len(old),
        "ai_budget_periods": periods,
    }

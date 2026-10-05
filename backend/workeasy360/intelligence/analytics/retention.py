"""Analytics retention (Task 83), per organization, in bounded batches.

=======================  ============  ==================================
data                     kept          setting
=======================  ============  ==================================
facts (projected)        400 days      ``ANALYTICS_FACT_RETENTION_DAYS``
hourly aggregates        90 days       ``ANALYTICS_HOURLY_RETENTION_DAYS``
daily aggregates         5 years       ``ANALYTICS_DAILY_RETENTION_DAYS``
=======================  ============  ==================================

A fact is pruned only once it has been projected, so retention can never
lose a measurement that has not reached its aggregates. Facts are pruned by
``occurred_at``, the same clock the rebuild window uses.
"""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from workeasy360.intelligence.analytics.models import (
    AnalyticsAggregate,
    AnalyticsFact,
    Granularity,
)


def prune_organization() -> dict[str, int]:
    now = timezone.now()
    batch = int(settings.AI_PRUNE_BATCH)
    fact_cutoff = now - timezone.timedelta(
        days=int(settings.ANALYTICS_FACT_RETENTION_DAYS)
    )
    facts = list(
        AnalyticsFact.objects.filter(
            occurred_at__lt=fact_cutoff, projected_at__isnull=False
        ).values_list("id", flat=True)[:batch]
    )
    AnalyticsFact.objects.filter(id__in=facts).delete()

    hourly, _ = AnalyticsAggregate.objects.filter(
        granularity=Granularity.HOUR.value,
        bucket_start__lt=now
        - timezone.timedelta(days=int(settings.ANALYTICS_HOURLY_RETENTION_DAYS)),
    ).delete()
    daily, _ = AnalyticsAggregate.objects.filter(
        granularity=Granularity.DAY.value,
        bucket_start__lt=now
        - timezone.timedelta(days=int(settings.ANALYTICS_DAILY_RETENTION_DAYS)),
    ).delete()
    return {
        "analytics_facts": len(facts),
        "analytics_hourly": hourly,
        "analytics_daily": daily,
    }

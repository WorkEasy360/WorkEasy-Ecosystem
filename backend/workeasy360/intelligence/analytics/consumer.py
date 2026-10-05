"""Product events in, metric facts out (Tasks 60, 61, 67, 68).

Analytics ingests **the same authenticated, versioned events** the Phase 6
integration platform already accepts - there is no second ingestion path
(Task 61). A product publishes ``product.<code>.<aggregate>.<verb>`` over
``/integrations/events`` exactly once; by then the platform has
authenticated the product, bound the organization through the live
entitlement decision, checked the namespace and the manifest declaration, and
recorded the event idempotently. When routing fans the event out, this
consumer - registered with ``integration.routing.consumers`` - turns it into
a fact for every active metric the owning product defined over it.

It runs inside the routing transaction and the organization's scope, so:

- **exactly once** (Task 68): a duplicate event is never routed, routing one
  event twice is a no-op, and the fact table is unique on (organization,
  source, event, metric) besides;
- **no direct product database query** (Task 60): the only input is the
  event the product chose to publish;
- **cheap**: one indexed query for the definitions, one insert, one timer.
  Aggregation happens later in ``projection``, off the routing path, so a
  projection problem can never hold up a webhook.
"""

from __future__ import annotations

from typing import Any, Final

from django.conf import settings
from django.utils import timezone

from workeasy360.intelligence.analytics import registry
from workeasy360.intelligence.analytics.extraction import Rejected, extract
from workeasy360.intelligence.analytics.models import AnalyticsFact
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

PROJECT_TIMER: Final = "analytics.project"
_PRODUCT_PREFIX: Final = "product."


def on_routed_event(event: Any) -> None:
    from workeasy360.integration.scheduling import services as scheduling

    if not event.source.startswith(_PRODUCT_PREFIX):
        return
    product = event.source[len(_PRODUCT_PREFIX) :]
    definitions = registry.metrics_for_event(
        product, event.event_type, event.event_version
    )
    if not definitions:
        return

    facts = []
    for metric in definitions:
        try:
            extracted = extract(metric, event.payload)
        except Rejected as exc:
            metrics.increment("analytics_facts_rejected_total", reason=exc.reason)
            logger.info(
                "analytics.fact.rejected",
                metric=metric.key,
                event_id=str(event.event_id),
                reason=exc.reason,
            )
            continue
        facts.append(
            AnalyticsFact(
                source=event.source,
                event_id=event.event_id,
                metric_key=metric.key,
                metric_version=metric.version,
                occurred_at=event.occurred_at,
                received_at=event.created_at,
                value=extracted.value,
                currency=extracted.currency,
                dims=extracted.dims,
                dims_key=extracted.dims_key,
            )
        )
    if not facts:
        return
    AnalyticsFact.objects.bulk_create(facts, ignore_conflicts=True)
    scheduling.schedule(
        PROJECT_TIMER,
        organization_id=event.organization_id,
        target_id=event.organization_id,
        due_at=timezone.now()
        + timezone.timedelta(seconds=float(settings.ANALYTICS_PROJECTION_GRACE_S)),
    )
    metrics.increment("analytics_facts_recorded_total", float(len(facts)))
    lag = (timezone.now() - event.occurred_at).total_seconds()
    metrics.observe("analytics_ingestion_lag_seconds", max(0.0, lag))

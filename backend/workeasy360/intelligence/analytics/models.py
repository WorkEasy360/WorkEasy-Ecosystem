"""Unified analytics: metric definitions, accepted facts, derived aggregates.

::

    AnalyticsMetric     control plane. A product's versioned definition of a
                        number over one of its own events: ``product.<code>.*``
                        key, unit, aggregation, the event field it measures,
                        enumerated dimensions, and who else may read it.
    AnalyticsFact       tenant. One accepted measurement extracted from one
                        routed event for one metric. The authoritative raw
                        record: immutable (a trigger refuses any change but
                        the projection mark), idempotent (one per event and
                        metric), and what every aggregate is rebuilt from.
    AnalyticsAggregate  tenant, derived. Hourly and daily sums and counts per
                        metric, bucket, dimension combination and currency.
                        Disposable: ``rebuild`` recreates it from facts.

No table here has a CRM, Books or any product's column. A product owns what
its metric *means*; the platform stores integers, enumerated labels and
currency codes, and adds them up (Tasks 63, 64).
"""

from __future__ import annotations

from django.db import models

from workeasy360.contracts.enums import StrEnum
from workeasy360.platform.tenancy.models import TenantScopedModel


class MetricStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class Aggregation(StrEnum):
    SUM = "sum"
    COUNT = "count"


class ValueType(StrEnum):
    #: A plain integer (a count, a quantity, milliseconds).
    INTEGER = "integer"
    #: Integer minor units with an explicit currency, never summed across
    #: currencies (F1, Task 79).
    MONEY = "money"


class Granularity(StrEnum):
    HOUR = "hour"
    DAY = "day"


class AnalyticsMetric(models.Model):
    """One immutable version of a metric definition (control plane)."""

    key = models.CharField(max_length=128)
    version = models.PositiveIntegerField()
    owner = models.CharField(max_length=32, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in MetricStatus],
        default=MetricStatus.ACTIVE.value,
    )
    description = models.CharField(max_length=300, blank=True, default="")
    unit = models.CharField(max_length=24)
    aggregation = models.CharField(max_length=8)
    value_type = models.CharField(max_length=8)
    source_event_type = models.CharField(max_length=128)
    source_event_version = models.PositiveSmallIntegerField()
    #: The event ``data`` field measured (dotted path, at most three levels).
    #: Empty for a count.
    measure_field = models.CharField(max_length=128, blank=True, default="")
    currency_field = models.CharField(max_length=128, blank=True, default="")
    #: ``[{"name", "field", "values": [...]}]`` - enumerated values only.
    dimensions = models.JSONField(default=list)
    shared_with = models.JSONField(default=list)
    definition_hash = models.CharField(max_length=64)
    registered_by = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "analytics_metric"
        constraints = [
            models.UniqueConstraint(
                fields=["key", "version"], name="uq_analytics_metric_version"
            )
        ]
        indexes = [
            models.Index(
                fields=["source_event_type", "source_event_version", "status"],
                name="ix_analytics_metric_source",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.key} v{self.version}"


class AnalyticsFact(TenantScopedModel):
    """One measurement from one event (the rebuild source, Task 71)."""

    #: ``product.<code>`` - who published the event.
    source = models.CharField(max_length=64)
    event_id = models.UUIDField()
    metric_key = models.CharField(max_length=128)
    metric_version = models.PositiveIntegerField()
    #: When it happened (the bucket), and when the platform accepted it -
    #: never interchanged (Task 67).
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField()
    value = models.BigIntegerField()
    currency = models.CharField(max_length=3, blank=True, default="")
    dims = models.JSONField(default=dict)
    #: Canonical ``name=value|...`` - the aggregate grouping key.
    dims_key = models.CharField(max_length=512, blank=True, default="")
    #: Set by the projection in the same transaction as the aggregate update.
    projected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "analytics_fact"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "source",
                    "event_id",
                    "metric_key",
                    "metric_version",
                ],
                name="uq_analytics_fact_event_metric",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "created_at"],
                name="ix_analytics_fact_unprojected",
                condition=models.Q(projected_at__isnull=True),
            ),
            models.Index(
                fields=["organization", "metric_key", "occurred_at"],
                name="ix_analytics_fact_metric_time",
            ),
        ]


class AnalyticsAggregate(TenantScopedModel):
    """A derived bucket: sum and count for one combination. Rebuildable."""

    metric_key = models.CharField(max_length=128)
    metric_version = models.PositiveIntegerField()
    granularity = models.CharField(max_length=8)
    bucket_start = models.DateTimeField()
    dims_key = models.CharField(max_length=512, blank=True, default="")
    dims = models.JSONField(default=dict)
    currency = models.CharField(max_length=3, blank=True, default="")
    sum_value = models.BigIntegerField(default=0)
    event_count = models.BigIntegerField(default=0)

    class Meta:
        db_table = "analytics_aggregate"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "metric_key",
                    "metric_version",
                    "granularity",
                    "bucket_start",
                    "dims_key",
                    "currency",
                ],
                name="uq_analytics_aggregate_bucket",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "metric_key", "granularity", "bucket_start"],
                name="ix_analytics_agg_query",
            )
        ]

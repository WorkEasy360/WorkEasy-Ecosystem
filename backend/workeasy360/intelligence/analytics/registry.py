"""The metric registry: semantic metadata a product owns (Tasks 62-65, 77).

A product registers, over its own credential, a versioned definition of a
number derived from one of **its own declared events**:

.. code-block:: json

    {"key": "product.example.order_value", "version": 1,
     "description": "Value of orders placed", "unit": "currency",
     "aggregation": "sum", "value_type": "money",
     "event": {"type": "product.example.order.placed", "version": 1},
     "measure": "amount_minor", "currency_field": "currency",
     "dimensions": [{"name": "channel", "field": "channel",
                     "values": ["web", "store", "phone"]}],
     "shared_with": ["books"]}

**Namespace ownership is structural** (Task 64): the key must be
``product.<authenticated code>.*`` and the source event must be in the same
product's namespace *and* declared in its current manifest. Product A cannot
register, change or read (Task 76) a metric in Product B's namespace, and the
platform defines no product's business metric.

**No arbitrary schema, no arbitrary SQL** (Task 62): a definition names one
integer field, optionally one currency field, and at most three dimensions -
each an allowlist of enumerated values (Task 65). An event value outside the
allowlist is counted under ``other``; a missing one under ``unknown``. So a
user email, a UUID or free text can never become an aggregate key, however a
product fills its events.

**Versioned and immutable**: re-registering an identical definition is
idempotent, a different one under the same number is a conflict; changing a
metric is registering its next version.

**Sharing is explicit** (Task 77): ``shared_with`` lists the products the
owner lets query it. Nobody else may, and a metric one may not query is
reported exactly like one that does not exist (Task 108).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final

from django.conf import settings
from django.db import IntegrityError, transaction

from workeasy360.contracts.errors import (
    AIInvalidRequest,
    AIRegistryConflict,
    MetricNotFound,
)
from workeasy360.contracts.events.namespaces import product_owns
from workeasy360.contracts.values import intelligence_keys as keys
from workeasy360.intelligence.analytics.models import (
    Aggregation,
    AnalyticsMetric,
    MetricStatus,
    ValueType,
)
from workeasy360.platform.observability.metrics import metrics

UNITS: Final[frozenset[str]] = frozenset(
    {"count", "items", "currency", "seconds", "milliseconds", "bytes", "basis_points"}
)
MAX_DIMENSIONS: Final = 3
MAX_DIMENSION_VALUES: Final = 50
MAX_SHARED_WITH: Final = 20
_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "key",
        "version",
        "description",
        "unit",
        "aggregation",
        "value_type",
        "event",
        "measure",
        "currency_field",
        "dimensions",
        "shared_with",
    }
)
#: Dimension names that would describe a person or an identifier. Refused by
#: name as a second line behind the value allowlist, so the intent is
#: rejected at the door rather than silently bucketed.
_FORBIDDEN_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {
        "email",
        "user",
        "user_id",
        "id",
        "uuid",
        "name",
        "phone",
        "ip",
        "address",
        "customer",
    }
)


@dataclass(frozen=True, slots=True)
class Dimension:
    name: str
    field: str
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class MetricDTO:
    key: str
    version: int
    owner: str
    status: str
    description: str
    unit: str
    aggregation: str
    value_type: str
    source_event_type: str
    source_event_version: int
    measure_field: str
    currency_field: str
    dimensions: tuple[Dimension, ...]
    shared_with: tuple[str, ...]

    @property
    def is_money(self) -> bool:
        return self.value_type == ValueType.MONEY.value

    def dimension(self, name: str) -> Dimension | None:
        return next((d for d in self.dimensions if d.name == name), None)

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "owner": self.owner,
            "status": self.status,
            "description": self.description,
            "unit": self.unit,
            "aggregation": self.aggregation,
            "value_type": self.value_type,
            "event": {
                "type": self.source_event_type,
                "version": self.source_event_version,
            },
            "dimensions": [
                {"name": d.name, "values": sorted(d.values)} for d in self.dimensions
            ],
            "shared_with": list(self.shared_with),
        }


def register(
    product_code: str, client_id: str, payload: Any, *, declares_event: Any
) -> tuple[MetricDTO, bool]:
    """Register one metric version for the calling product. ``declares_event``
    is the authenticated client's manifest check. Validates before writing."""
    definition = _parse(product_code, payload, declares_event)
    digest = hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with transaction.atomic():
        existing = AnalyticsMetric.objects.filter(
            key=definition["key"], version=definition["version"]
        ).first()
        if existing is not None:
            if existing.definition_hash != digest:
                raise AIRegistryConflict(
                    "This metric version is already registered with a different "
                    "definition. Register a new version.",
                    key=definition["key"],
                    version=definition["version"],
                )
            return _dto(existing), False
        if AnalyticsMetric.objects.filter(owner=product_code).count() >= int(
            settings.ANALYTICS_MAX_METRICS_PER_PRODUCT
        ):
            raise AIRegistryConflict("The product's metric limit is reached.")
        try:
            with transaction.atomic():
                row = AnalyticsMetric.objects.create(
                    key=definition["key"],
                    version=definition["version"],
                    owner=product_code,
                    description=definition["description"],
                    unit=definition["unit"],
                    aggregation=definition["aggregation"],
                    value_type=definition["value_type"],
                    source_event_type=definition["event_type"],
                    source_event_version=definition["event_version"],
                    measure_field=definition["measure"],
                    currency_field=definition["currency_field"],
                    dimensions=definition["dimensions"],
                    shared_with=definition["shared_with"],
                    definition_hash=digest,
                    registered_by=client_id[:64],
                )
        except IntegrityError:
            row = AnalyticsMetric.objects.get(
                key=definition["key"], version=definition["version"]
            )
            if row.definition_hash != digest:
                raise AIRegistryConflict(
                    "This metric version is already registered with a different definition."
                ) from None
            return _dto(row), False
    metrics.increment("analytics_metrics_registered_total")
    return _dto(row), True


def metrics_for_event(
    product_code: str, event_type: str, event_version: int
) -> list[MetricDTO]:
    """Active metrics a product's event feeds - one indexed query."""
    return [
        _dto(row)
        for row in AnalyticsMetric.objects.filter(
            source_event_type=event_type,
            source_event_version=event_version,
            owner=product_code,
            status=MetricStatus.ACTIVE.value,
        )
    ]


def resolve(key: Any, version: Any = None) -> MetricDTO:
    """An active metric version (the latest when ``version`` is omitted)."""
    if not isinstance(key, str) or keys.owner_of(key) is None:
        raise MetricNotFound("No such metric.")
    if version is not None and (
        isinstance(version, bool) or not isinstance(version, int)
    ):
        raise AIInvalidRequest("version must be an integer.", field="version")
    rows = AnalyticsMetric.objects.filter(key=key, status=MetricStatus.ACTIVE.value)
    rows = (
        rows.filter(version=version) if version is not None else rows.order_by("-version")
    )
    row = rows.first()
    if row is None:
        raise MetricNotFound("No such metric.")
    return _dto(row)


def owned_or_shared(product_code: str) -> list[MetricDTO]:
    """What a product may query: its own metrics and those shared with it."""
    from django.db.models import Q

    rows = AnalyticsMetric.objects.filter(
        Q(owner=product_code) | Q(shared_with__contains=[product_code]),
        status=MetricStatus.ACTIVE.value,
    ).order_by("key", "version")
    return [_dto(r) for r in rows[:500]]


def for_owners(owners: set[str]) -> list[MetricDTO]:
    rows = AnalyticsMetric.objects.filter(
        owner__in=sorted(owners), status=MetricStatus.ACTIVE.value
    ).order_by("key", "version")
    return [_dto(r) for r in rows[:500]]


def _parse(product_code: str, payload: Any, declares_event: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AIInvalidRequest("The metric must be a JSON object.")
    unknown = sorted(set(payload) - _FIELDS)
    if unknown:
        raise AIInvalidRequest(
            f"Unknown field(s): {', '.join(unknown)}.", field=unknown[0]
        )
    key = payload.get("key")
    if not isinstance(key, str) or not keys.product_owns_key(product_code, key):
        raise AIInvalidRequest(
            f"The metric key must be 'product.{product_code}.<name>' - a product "
            "registers metrics only in its own namespace.",
            field="key",
        )
    version = payload.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 1 <= version <= keys.MAX_VERSION
    ):
        raise AIInvalidRequest(
            "version must be an integer from 1 to 999.", field="version"
        )
    description = payload.get("description", "")
    if not isinstance(description, str) or len(description) > 300:
        raise AIInvalidRequest(
            "description must be at most 300 characters.", field="description"
        )

    aggregation = payload.get("aggregation")
    if aggregation not in {a.value for a in Aggregation}:
        raise AIInvalidRequest(
            "aggregation must be 'sum' or 'count'.", field="aggregation"
        )
    value_type = payload.get("value_type", ValueType.INTEGER.value)
    if value_type not in {v.value for v in ValueType}:
        raise AIInvalidRequest(
            "value_type must be 'integer' or 'money'.", field="value_type"
        )
    unit = payload.get("unit")
    if unit not in UNITS:
        raise AIInvalidRequest(
            f"unit must be one of {', '.join(sorted(UNITS))}.", field="unit"
        )

    event = payload.get("event")
    if not isinstance(event, dict) or set(event) != {"type", "version"}:
        raise AIInvalidRequest("event must be {'type', 'version'}.", field="event")
    event_type, event_version = event["type"], event["version"]
    if (
        not isinstance(event_type, str)
        or isinstance(event_version, bool)
        or not isinstance(event_version, int)
        or not product_owns(product_code, event_type)
        or not declares_event(event_type, event_version)
    ):
        raise AIInvalidRequest(
            "event must be one of this product's own events, declared in its "
            "current manifest.",
            field="event",
        )

    measure = payload.get("measure", "") or ""
    currency_field = payload.get("currency_field", "") or ""
    if aggregation == Aggregation.COUNT.value:
        if measure or value_type != ValueType.INTEGER.value or unit != "count":
            raise AIInvalidRequest(
                "A count metric has no measure, value_type 'integer' and unit 'count'.",
                field="aggregation",
            )
    else:
        _path(measure, "measure")
    if value_type == ValueType.MONEY.value:
        _path(currency_field, "currency_field")
        if unit != "currency":
            raise AIInvalidRequest("A money metric's unit is 'currency'.", field="unit")
    elif currency_field:
        raise AIInvalidRequest(
            "Only a money metric has a currency_field.", field="currency_field"
        )

    raw_dimensions = payload.get("dimensions", [])
    if not isinstance(raw_dimensions, list) or len(raw_dimensions) > MAX_DIMENSIONS:
        raise AIInvalidRequest(
            f"dimensions may list at most {MAX_DIMENSIONS} dimensions.",
            field="dimensions",
        )
    dimensions = []
    names = set()
    for item in raw_dimensions:
        if not isinstance(item, dict) or set(item) != {"name", "field", "values"}:
            raise AIInvalidRequest(
                "Each dimension is {'name', 'field', 'values'}.", field="dimensions"
            )
        name, field, values = item["name"], item["field"], item["values"]
        if not isinstance(name, str) or not keys.is_slug(name) or name in names:
            raise AIInvalidRequest(
                "Dimension names are unique short identifiers.", field="dimensions"
            )
        if name in _FORBIDDEN_DIMENSIONS or name.endswith("_id"):
            raise AIInvalidRequest(
                f"'{name}' would identify a person or a record; dimensions are "
                "enumerated categories.",
                field="dimensions",
            )
        _path(field, "dimensions")
        if (
            not isinstance(values, list)
            or not 0 < len(values) <= MAX_DIMENSION_VALUES
            or not all(isinstance(v, str) and keys.is_slug(v) for v in values)
            or {"other", "unknown"} & set(values)
        ):
            raise AIInvalidRequest(
                f"A dimension enumerates 1-{MAX_DIMENSION_VALUES} short lowercase "
                "values ('other' and 'unknown' are reserved).",
                field="dimensions",
            )
        names.add(name)
        dimensions.append({"name": name, "field": field, "values": sorted(set(values))})

    shared = payload.get("shared_with", [])
    if (
        not isinstance(shared, list)
        or len(shared) > MAX_SHARED_WITH
        or not all(isinstance(p, str) and keys.is_product_code(p) for p in shared)
        or product_code in shared
    ):
        raise AIInvalidRequest(
            f"shared_with must list at most {MAX_SHARED_WITH} other product codes.",
            field="shared_with",
        )
    return {
        "key": key,
        "version": version,
        "description": description,
        "unit": unit,
        "aggregation": aggregation,
        "value_type": value_type,
        "event_type": event_type,
        "event_version": event_version,
        "measure": measure,
        "currency_field": currency_field,
        "dimensions": sorted(dimensions, key=lambda d: d["name"]),
        "shared_with": sorted(set(shared)),
    }


def _path(value: Any, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not all(keys.is_slug(part) for part in value.split("."))
        or value.count(".") > 2
    ):
        raise AIInvalidRequest(
            f"{field} must name an event data field (a.b.c, at most three levels).",
            field=field,
        )


def _dto(row: AnalyticsMetric) -> MetricDTO:
    return MetricDTO(
        key=row.key,
        version=row.version,
        owner=row.owner,
        status=row.status,
        description=row.description,
        unit=row.unit,
        aggregation=row.aggregation,
        value_type=row.value_type,
        source_event_type=row.source_event_type,
        source_event_version=row.source_event_version,
        measure_field=row.measure_field,
        currency_field=row.currency_field,
        dimensions=tuple(
            Dimension(name=d["name"], field=d["field"], values=frozenset(d["values"]))
            for d in (row.dimensions or [])
        ),
        shared_with=tuple(row.shared_with or ()),
    )

"""From one event to one fact: pure, bounded, deterministic.

The only place an event's ``data`` is read for analytics, and it reads
exactly the fields a metric definition names - one integer measure, one
currency code, and each dimension's field mapped through its allowlist
(Task 65). Nothing else in the payload is stored.

- a count's value is 1; a sum's is the measure, which must be an integer
  (floats are refused: F1) within +/- 10**15 - negative values are allowed,
  because a correction is a new corrective event (Task 73);
- a money metric's currency must be a supported ISO 4217 code, and is part of
  every grouping key, so amounts in different currencies are never added
  together (Task 79);
- a dimension value outside the definition's enumeration becomes ``other``, a
  missing one ``unknown``.

A refused event is counted by reason and skipped - it never blocks the
routing of the event it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.intelligence.analytics.registry import MetricDTO

MAX_ABS_VALUE: Final = 10**15
OTHER: Final = "other"
UNKNOWN: Final = "unknown"


class Rejected(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Extracted:
    value: int
    currency: str
    dims: dict[str, str]
    dims_key: str


def extract(metric: MetricDTO, data: dict[str, Any]) -> Extracted:
    if metric.aggregation == "count":
        value = 1
    else:
        raw = _at(data, metric.measure_field)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise Rejected("measure_not_integer")
        if abs(raw) > MAX_ABS_VALUE:
            raise Rejected("measure_out_of_range")
        value = raw

    currency = ""
    if metric.is_money:
        code = _at(data, metric.currency_field)
        if not isinstance(code, str) or code not in {c.value for c in CurrencyCode}:
            raise Rejected("currency_unsupported")
        currency = code

    dims: dict[str, str] = {}
    for dimension in metric.dimensions:
        raw_dim = _at(data, dimension.field)
        if raw_dim is None:
            dims[dimension.name] = UNKNOWN
        elif isinstance(raw_dim, str) and raw_dim in dimension.values:
            dims[dimension.name] = raw_dim
        else:
            dims[dimension.name] = OTHER
    return Extracted(
        value=value,
        currency=currency,
        dims=dims,
        dims_key="|".join(f"{name}={dims[name]}" for name in sorted(dims)),
    )


def _at(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node

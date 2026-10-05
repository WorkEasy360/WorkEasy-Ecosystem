"""Automation conditions: bounded, deterministic, never code (Tasks 33/34).

A condition set is data, evaluated by a fixed interpreter of seven
operators over explicitly addressed fields. There is no expression language:
nothing here parses Python, JavaScript, SQL, Jinja or a regex a customer
wrote, and nothing is ever handed to ``eval``, a template engine or a query
builder. That is the whole security argument, and it is structural - there
is simply no code path from a condition to execution.

Shape (normalised)::

    {"match": "all" | "any",
     "rules": [{"path": "payload.order.total", "op": "greater_than", "value": 100}]}

Bounds, each enforced at save time:

- at most ``MAX_RULES`` rules, one level, no nesting;
- a path is ``event.<field>`` or ``payload.<segment>...``, at most
  ``MAX_PATH_SEGMENTS`` segments of a strict grammar (letters, digits,
  ``_``/``-``, or a list index), so resolving it is a bounded walk;
- string values at most ``MAX_STRING_LENGTH``, ``in`` lists at most
  ``MAX_IN_VALUES``, numbers finite.

Evaluation is type-strict: ``1`` does not equal ``true``, ``"1"`` does not
equal ``1``, and ordering operators apply only to numbers. A missing field
makes a rule false (``exists`` excepted). Pure Python, no Django.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Final

MAX_RULES: Final = 20
MAX_PATH_SEGMENTS: Final = 8
MAX_STRING_LENGTH: Final = 256
MAX_IN_VALUES: Final = 20

OPERATORS: Final[frozenset[str]] = frozenset(
    {"equals", "not_equals", "contains", "exists", "greater_than", "less_than", "in"}
)

#: The event metadata a condition may address. Deliberately not the
#: envelope's internals (no organization id, no correlation id).
EVENT_FIELDS: Final[frozenset[str]] = frozenset({"type", "version", "source"})

_SEGMENT_RE: Final = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_-]{0,63}|[0-9]{1,3})\Z")
_MISSING: Final = object()


class ConditionError(ValueError):
    """An invalid condition definition. ``field`` locates it."""

    def __init__(self, message: str, *, field: str = "conditions") -> None:
        super().__init__(message)
        self.message = message
        self.field = field


def normalize(raw: Any) -> dict[str, Any]:
    """Validate a condition set and return its canonical form."""
    if raw is None or raw == {}:
        return {"match": "all", "rules": []}
    if not isinstance(raw, Mapping):
        raise ConditionError("conditions must be an object.")
    unknown = sorted(set(raw) - {"match", "rules"})
    if unknown:
        raise ConditionError(f"Unknown conditions field(s): {', '.join(unknown)}.")
    match = raw.get("match", "all")
    if match not in ("all", "any"):
        raise ConditionError(
            "conditions.match must be 'all' or 'any'.", field="conditions.match"
        )
    rules = raw.get("rules", [])
    if not isinstance(rules, list):
        raise ConditionError("conditions.rules must be a list.", field="conditions.rules")
    if len(rules) > MAX_RULES:
        raise ConditionError(
            f"At most {MAX_RULES} conditions are allowed.", field="conditions.rules"
        )
    return {
        "match": match,
        "rules": [
            _normalize_rule(rule, f"conditions.rules[{i}]")
            for i, rule in enumerate(rules)
        ],
    }


def evaluate(
    conditions: Mapping[str, Any], *, event: Mapping[str, Any], payload: Any
) -> bool:
    """Whether the (normalised) condition set holds for this event."""
    rules = conditions.get("rules") or []
    if not rules:
        return True
    roots = {"event": event, "payload": payload}
    results = (_evaluate_rule(rule, roots) for rule in rules)
    return all(results) if conditions.get("match", "all") == "all" else any(results)


# --- validation -----------------------------------------------------------------


def _normalize_rule(rule: Any, field: str) -> dict[str, Any]:
    if not isinstance(rule, Mapping):
        raise ConditionError("Each condition must be an object.", field=field)
    unknown = sorted(set(rule) - {"path", "op", "value"})
    if unknown:
        raise ConditionError(
            f"Unknown condition field(s): {', '.join(unknown)}.", field=field
        )
    path = _normalize_path(rule.get("path"), f"{field}.path")
    op = rule.get("op")
    if not isinstance(op, str) or op not in OPERATORS:
        raise ConditionError(
            f"Unknown operator. Allowed: {', '.join(sorted(OPERATORS))}.",
            field=f"{field}.op",
        )
    value = rule.get("value")
    value_field = f"{field}.value"
    if op == "exists":
        if not isinstance(value, bool):
            raise ConditionError("'exists' takes true or false.", field=value_field)
    elif op in ("greater_than", "less_than"):
        if not _is_number(value):
            raise ConditionError(f"'{op}' takes a number.", field=value_field)
    elif op == "in":
        if not isinstance(value, list) or not value:
            raise ConditionError("'in' takes a non-empty list.", field=value_field)
        if len(value) > MAX_IN_VALUES:
            raise ConditionError(
                f"'in' takes at most {MAX_IN_VALUES} values.", field=value_field
            )
        for item in value:
            _check_scalar(item, value_field)
    else:
        _check_scalar(value, value_field)
    return {"path": path, "op": op, "value": value}


def _normalize_path(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ConditionError("A condition needs a path.", field=field)
    segments = raw.split(".")
    root, rest = segments[0], segments[1:]
    if root not in ("event", "payload"):
        raise ConditionError("A path starts with 'event.' or 'payload.'.", field=field)
    if not rest or len(rest) > MAX_PATH_SEGMENTS:
        raise ConditionError(
            f"A path has 1 to {MAX_PATH_SEGMENTS} segments after its root.", field=field
        )
    if root == "event" and (len(rest) != 1 or rest[0] not in EVENT_FIELDS):
        raise ConditionError(
            f"event.* paths are {', '.join(sorted(EVENT_FIELDS))}.", field=field
        )
    for segment in rest:
        # Dunder names are refused even though resolution only ever does a
        # dict lookup: a path that *looks* like attribute traversal is one a
        # reviewer has to reason about, and no real payload key needs it.
        if not _SEGMENT_RE.match(segment) or segment.startswith("__"):
            raise ConditionError(f"Invalid path segment {segment[:40]!r}.", field=field)
    return raw


def _check_scalar(value: Any, field: str) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise ConditionError(
                f"String values are at most {MAX_STRING_LENGTH} characters.", field=field
            )
        return
    if _is_number(value):
        return
    raise ConditionError(
        "A value must be a string, number, boolean or null.", field=field
    )


def _is_number(value: Any) -> bool:
    # An int is always finite - and ``math.isfinite`` raises OverflowError on
    # one too large for a float, which a product payload can carry (a JSON
    # integer of a few hundred digits parses as an int).
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


# --- evaluation ------------------------------------------------------------------


def _resolve(path: str, roots: Mapping[str, Any]) -> Any:
    segments = path.split(".")
    node: Any = roots.get(segments[0], _MISSING)
    for segment in segments[1:]:
        if isinstance(node, Mapping):
            node = node.get(segment, _MISSING)
        elif isinstance(node, list) and segment.isdigit():
            index = int(segment)
            node = node[index] if index < len(node) else _MISSING
        else:
            return _MISSING
        if node is _MISSING:
            return _MISSING
    return node


def _same(left: Any, right: Any) -> bool:
    """Type-strict equality: booleans only equal booleans, numbers compare
    numerically (``1 == 1.0``), everything else by value."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if _is_number(left) and _is_number(right):
        return bool(left == right)
    if type(left) is not type(right):
        return False
    return bool(left == right)


def _evaluate_rule(rule: Mapping[str, Any], roots: Mapping[str, Any]) -> bool:
    actual = _resolve(rule["path"], roots)
    op, expected = rule["op"], rule["value"]
    if op == "exists":
        return (actual is not _MISSING) is expected
    if actual is _MISSING:
        return False
    if op == "equals":
        return _same(actual, expected)
    if op == "not_equals":
        return not _same(actual, expected)
    if op == "in":
        return any(_same(actual, candidate) for candidate in expected)
    if op == "contains":
        if isinstance(actual, str) and isinstance(expected, str):
            return expected in actual
        if isinstance(actual, list):
            return any(_same(item, expected) for item in actual)
        return False
    if not (_is_number(actual) and _is_number(expected)):
        return False
    return actual > expected if op == "greater_than" else actual < expected

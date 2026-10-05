"""Automation conditions: bounded, type-strict, and never code (Tasks 33/34)."""

from __future__ import annotations

import pytest

from workeasy360.integration.automation.conditions import (
    MAX_PATH_SEGMENTS,
    MAX_RULES,
    ConditionError,
    evaluate,
    normalize,
)

EVENT = {
    "type": "product.example.record.created",
    "version": 1,
    "source": "product.example",
}
PAYLOAD = {
    "amount": 250,
    "ratio": 0.5,
    "status": "open",
    "flag": True,
    "tags": ["vip", "eu"],
    "customer": {"country": "GB", "orders": [{"total": 10}, {"total": 99}]},
    "nothing": None,
}


def _eval(*rules, match="all"):
    return evaluate(
        normalize({"match": match, "rules": list(rules)}), event=EVENT, payload=PAYLOAD
    )


def test_an_empty_condition_set_always_matches():
    assert evaluate(normalize(None), event=EVENT, payload=PAYLOAD) is True


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ({"path": "payload.status", "op": "equals", "value": "open"}, True),
        ({"path": "payload.status", "op": "not_equals", "value": "open"}, False),
        ({"path": "payload.amount", "op": "greater_than", "value": 100}, True),
        ({"path": "payload.amount", "op": "less_than", "value": 100}, False),
        ({"path": "payload.ratio", "op": "less_than", "value": 1}, True),
        ({"path": "payload.tags", "op": "contains", "value": "vip"}, True),
        ({"path": "payload.status", "op": "contains", "value": "pe"}, True),
        ({"path": "payload.customer.country", "op": "in", "value": ["GB", "IE"]}, True),
        ({"path": "payload.customer.orders.1.total", "op": "equals", "value": 99}, True),
        ({"path": "payload.missing", "op": "exists", "value": False}, True),
        ({"path": "payload.nothing", "op": "exists", "value": True}, True),
        ({"path": "payload.missing", "op": "equals", "value": "x"}, False),
        (
            {
                "path": "event.type",
                "op": "equals",
                "value": "product.example.record.created",
            },
            True,
        ),
        ({"path": "event.version", "op": "greater_than", "value": 1}, False),
    ],
)
def test_each_operator(rule, expected):
    assert _eval(rule) is expected


def test_comparisons_are_type_strict():
    """``1`` is not ``true``, ``"250"`` is not ``250``, and ordering applies
    only to numbers - no coercion a customer could be surprised by."""
    assert _eval({"path": "payload.flag", "op": "equals", "value": 1}) is False
    assert _eval({"path": "payload.amount", "op": "equals", "value": "250"}) is False
    assert _eval({"path": "payload.status", "op": "greater_than", "value": 0}) is False
    assert _eval({"path": "payload.flag", "op": "greater_than", "value": 0}) is False


def test_all_and_any():
    yes = {"path": "payload.status", "op": "equals", "value": "open"}
    no = {"path": "payload.status", "op": "equals", "value": "closed"}
    assert _eval(yes, no, match="all") is False
    assert _eval(yes, no, match="any") is True


@pytest.mark.parametrize(
    "raw",
    [
        {"rules": [{"path": "payload.x", "op": "matches_regex", "value": ".*"}]},
        {"rules": [{"path": "payload.x", "op": "eval", "value": "__import__('os')"}]},
        {"rules": [{"path": "__class__.__mro__", "op": "exists", "value": True}]},
        {"rules": [{"path": "payload.__class__", "op": "exists", "value": True}]},
        {"rules": [{"path": "payload.a b", "op": "exists", "value": True}]},
        {"rules": [{"path": "payload.x;DROP TABLE", "op": "exists", "value": True}]},
        {"rules": [{"path": "event.organization_id", "op": "exists", "value": True}]},
        {"rules": [{"path": "payload", "op": "exists", "value": True}]},
        {"rules": [{"path": "payload.x", "op": "greater_than", "value": "10"}]},
        {"rules": [{"path": "payload.x", "op": "equals", "value": {"nested": 1}}]},
        {"rules": [{"path": "payload.x", "op": "equals", "value": "x" * 300}]},
        {"rules": [{"path": "payload.x", "op": "in", "value": list(range(50))}]},
        {"rules": [{"path": "payload.x", "op": "exists", "value": "yes"}]},
        {"rules": [{"path": "payload.x", "op": "equals", "value": 1, "extra": 1}]},
        {"match": "most", "rules": []},
        {"rules": "not a list"},
        {"unknown": 1},
    ],
)
def test_anything_outside_the_grammar_is_refused(raw):
    with pytest.raises(ConditionError):
        normalize(raw)


def test_path_depth_and_rule_count_are_bounded():
    deep = "payload." + ".".join(["a"] * (MAX_PATH_SEGMENTS + 1))
    with pytest.raises(ConditionError):
        normalize({"rules": [{"path": deep, "op": "exists", "value": True}]})
    too_many = [{"path": "payload.a", "op": "exists", "value": True}] * (MAX_RULES + 1)
    with pytest.raises(ConditionError):
        normalize({"rules": too_many})


def test_the_engine_never_evaluates_code():
    """Structural: the interpreter has no path to ``eval``/``exec``/compile,
    a template engine or a query builder."""
    import ast
    import inspect

    from workeasy360.integration.automation import conditions

    tree = ast.parse(inspect.getsource(conditions))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"eval", "exec", "compile", "__import__", "getattr"}
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert not imported & {"jinja2", "Template", "connection", "RawSQL"}


# --- Phase 6 audit ---------------------------------------------------------------


def test_an_integer_too_large_for_a_float_is_compared_not_crashed_on():
    """A product payload may carry a JSON integer of a few hundred digits
    (it parses as an int and fits the size bound). Evaluating it must give
    an answer, not an OverflowError that retries the execution to death."""
    huge = int("9" * 400)
    payload = {"n": huge, "items": [huge]}
    for rule, expected in [
        ({"path": "payload.n", "op": "equals", "value": "x"}, False),
        ({"path": "payload.n", "op": "not_equals", "value": 1}, True),
        ({"path": "payload.n", "op": "greater_than", "value": 1.5}, True),
        ({"path": "payload.n", "op": "less_than", "value": 10}, False),
        ({"path": "payload.n", "op": "in", "value": [1, 2]}, False),
        ({"path": "payload.items", "op": "contains", "value": 3}, False),
    ]:
        conditions = normalize({"rules": [rule]})
        assert evaluate(conditions, event=EVENT, payload=payload) is expected, rule


@pytest.mark.parametrize("op", [[], {}, ["equals"], 1, None])
def test_an_operator_that_is_not_a_string_is_a_validation_error(op):
    with pytest.raises(ConditionError):
        normalize({"rules": [{"path": "payload.amount", "op": op, "value": 1}]})


def test_a_trailing_newline_does_not_pass_as_a_path_segment():
    with pytest.raises(ConditionError):
        normalize(
            {"rules": [{"path": "payload.amount\n", "op": "exists", "value": True}]}
        )

"""Deeply nested JSON is malformed input, never a server error (2026-10 audit).

CPython's JSON decoder raises ``RecursionError`` - not ``ValueError`` - at
about a thousand levels of nesting. Every request-body parser in
``interfaces/`` caught only ``JSONDecodeError``/``ValueError``, so a 40 KB
body of brackets was a 500 from unauthenticated sign-in (verified on
staging). ``contracts.values.json_input.loads`` reports it as malformed JSON.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.extension import conftest as _extension
from workeasy360.contracts.values import json_input

# Re-exported by assignment (not import): see tests/phase10/conftest.py.
login = _extension.login
PASSWORD = _extension.PASSWORD

DEEP = "[" * 20_000 + "]" * 20_000
INTERFACES = Path(__file__).resolve().parents[2] / "workeasy360" / "interfaces"


def test_the_loader_reports_deep_nesting_as_malformed_json():
    with pytest.raises(json.JSONDecodeError):
        json_input.loads(DEEP)
    with pytest.raises(ValueError):  # what most callers catch
        json_input.loads(DEEP.encode())
    assert json_input.loads('{"a": [1, 2]}') == {"a": [1, 2]}
    assert json_input.loads(b"[[1]]") == [[1]]


@pytest.mark.django_db
def test_unauthenticated_sign_in_with_a_deeply_nested_body_is_not_a_server_error(client):
    response = client.post("/api/v1/auth/login", DEEP, content_type="application/json")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


@pytest.mark.django_db
def test_a_signed_in_write_with_a_deeply_nested_body_is_a_400(user_factory, login):
    client = login(user_factory(password=PASSWORD), aal2=True)
    response = client.post("/api/v1/auth/password", DEEP, content_type="application/json")
    assert response.status_code == 400


def test_no_view_parses_json_with_json_loads_directly():
    """Every request body in ``interfaces/`` goes through ``json_input.loads``;
    a direct ``json.loads`` would bring the 500 back."""
    offenders = []
    for path in INTERFACES.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "loads"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
            ):
                offenders.append(f"{path.relative_to(INTERFACES.parent)}:{node.lineno}")
    assert offenders == []

"""Check that the frontend's declared wire types match real API responses.

``frontend/src/lib/api/partnerConsole.ts`` declares an interface for every
payload the Phase 10 screens read, one field per line. TypeScript trusts those
declarations, so a field renamed or missing on the server would show up as a
blank cell, not an error. This reads the interfaces and checks them, field by
field and recursively, against **responses produced by the real stack**.

(The Phase 7 reader does the same for ``controlPlane.ts``; this one takes the
file as a parameter so both clients are covered.)
"""

from __future__ import annotations

import pathlib
import re

FRONTEND_API = (
    pathlib.Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "api"
)
PARTNER_CONSOLE_TS = FRONTEND_API / "partnerConsole.ts"
CONTROL_PLANE_TS = FRONTEND_API / "controlPlane.ts"


def interfaces(*paths: pathlib.Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"export interface (\w+)(?: extends (\w+))? \{\n(.*?)\n\}", source, re.S
        ):
            name, base, body = match.groups()
            fields: dict[str, tuple[bool, str]] = {}
            for line in body.splitlines():
                field = re.match(r"^  (\w+)(\??): (.*);$", line)
                if field:
                    fields[field.group(1)] = (field.group(2) == "?", field.group(3))
            found[name] = {"base": base, "fields": fields}
    return found


INTERFACES = interfaces(CONTROL_PLANE_TS, PARTNER_CONSOLE_TS)


def all_fields(name: str) -> dict[str, tuple[bool, str]]:
    spec = INTERFACES[name]
    fields = dict(all_fields(spec["base"])) if spec["base"] else {}
    fields.update(spec["fields"])
    return fields


def _inline_fields(type_text: str) -> dict[str, tuple[bool, str]] | None:
    match = re.search(r"\{\s*([^{}]*)\}", type_text)
    if not match or type_text.strip().startswith("Record<"):
        return None
    out: dict[str, tuple[bool, str]] = {}
    for part in match.group(1).split(";"):
        part = part.strip()
        if not part:
            continue
        field = re.match(r"^(\w+)(\??): (.*)$", part)
        if field:
            out[field.group(1)] = (field.group(2) == "?", field.group(3))
    return out


def _check(value, fields, where: str, problems: list[str]) -> None:
    if not isinstance(value, dict):
        problems.append(f"{where}: expected an object, got {type(value).__name__}")
        return
    for key, (optional, type_text) in fields.items():
        if key not in value:
            if not optional:
                problems.append(f"{where}: missing required field '{key}'")
            continue
        _check_type(value[key], type_text, f"{where}.{key}", problems)


def _check_type(value, type_text: str, where: str, problems: list[str]) -> None:
    if value is None:
        return
    is_array = type_text.strip().endswith("[]") or "}[]" in type_text
    base = re.sub(r"\s*\|\s*null", "", type_text).strip()
    named = re.match(r"^(\w+)(\[\])?$", base)
    if named and named.group(1) in INTERFACES:
        if named.group(2):
            for i, item in enumerate(value[:3]):
                _check(item, all_fields(named.group(1)), f"{where}[{i}]", problems)
        else:
            _check(value, all_fields(named.group(1)), where, problems)
        return
    inline = _inline_fields(base)
    if inline:
        if is_array:
            for i, item in enumerate((value or [])[:3]):
                _check(item, inline, f"{where}[{i}]", problems)
        elif isinstance(value, dict):
            _check(value, inline, where, problems)


def assert_matches(payload, interface: str, where: str) -> None:
    problems: list[str] = []
    _check(payload, all_fields(interface), where, problems)
    assert not problems, "\n".join(problems)


def assert_page(payload, interface: str, where: str) -> None:
    assert isinstance(payload, dict) and {"results", "next_cursor", "has_more"} <= set(
        payload
    ), where
    assert payload["results"], (
        f"{where}: the fixture produced no rows, so nothing was checked"
    )
    assert_matches(payload["results"][0], interface, f"{where}.results[0]")

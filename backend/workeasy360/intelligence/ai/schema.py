"""The JSON-schema subset for skill input and output contracts (Tasks 17, 19).

A deliberately small, explicit subset rather than a full JSON Schema engine
(no dependency, no ``$ref``, no remote resolution, no regular expressions a
product could make catastrophic):

``type`` (``object``, ``array``, ``string``, ``integer``, ``number``,
``boolean``, ``null``, or a list of them), ``properties``, ``required``,
``additionalProperties`` (a boolean), ``items``, ``enum``, ``minLength``,
``maxLength``, ``minItems``, ``maxItems``, ``minimum``, ``maximum``, and the
annotations ``title`` and ``description``.

Two choices differ from JSON Schema's defaults, both toward strictness:

- ``additionalProperties`` defaults to **false**: an output contract that
  silently accepts unexpected fields is not a contract;
- an unknown keyword is an error at registration, not ignored - a product
  that writes ``maxlength`` must find out then, not when its limit never
  applies.

Schemas are bounded in size and depth, and validation is iterative, so a
hostile schema or instance cannot exhaust the interpreter.
"""

from __future__ import annotations

import json
import math
from typing import Any, Final

TYPES: Final[frozenset[str]] = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)
_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "title",
        "description",
    }
)
#: Constraints the platform validates but vendors' native structured-output
#: modes may reject; stripped from the copy sent to a provider.
_VALIDATION_ONLY: Final[frozenset[str]] = frozenset(
    {"minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"}
)
MAX_SCHEMA_BYTES: Final = 16_384
MAX_DEPTH: Final = 6
MAX_PROPERTIES: Final = 100
MAX_ENUM: Final = 100
MAX_ERRORS: Final = 10


class SchemaError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def check_definition(schema: Any) -> None:
    """Raise ``SchemaError`` unless ``schema`` is a bounded, supported schema."""
    if not isinstance(schema, dict):
        raise SchemaError("A schema must be a JSON object.")
    try:
        encoded = json.dumps(schema, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise SchemaError("A schema must be plain JSON.") from None
    if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise SchemaError(f"A schema may be at most {MAX_SCHEMA_BYTES} bytes.")

    stack: list[tuple[Any, str, int]] = [(schema, "$", 1)]
    while stack:
        node, path, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise SchemaError(f"{path}: schemas may nest at most {MAX_DEPTH} levels.")
        if not isinstance(node, dict):
            raise SchemaError(f"{path}: expected a schema object.")
        unknown = sorted(set(node) - _KEYWORDS)
        if unknown:
            raise SchemaError(f"{path}: unsupported keyword(s) {', '.join(unknown)}.")
        types = _types(node, path)
        if "properties" in node:
            properties = node["properties"]
            if not isinstance(properties, dict) or len(properties) > MAX_PROPERTIES:
                raise SchemaError(
                    f"{path}.properties: an object of at most {MAX_PROPERTIES} schemas."
                )
            for name, child in properties.items():
                if not isinstance(name, str) or not 0 < len(name) <= 64:
                    raise SchemaError(f"{path}.properties: names are 1-64 characters.")
                stack.append((child, f"{path}.{name}", depth + 1))
        if "required" in node:
            required = node["required"]
            declared = set(node.get("properties") or {})
            if (
                not isinstance(required, list)
                or not all(isinstance(r, str) for r in required)
                or not set(required) <= declared
            ):
                raise SchemaError(f"{path}.required: names from properties only.")
        if "additionalProperties" in node and not isinstance(
            node["additionalProperties"], bool
        ):
            raise SchemaError(f"{path}.additionalProperties: must be a boolean.")
        if "items" in node:
            stack.append((node["items"], f"{path}[]", depth + 1))
        if "enum" in node:
            enum = node["enum"]
            if (
                not isinstance(enum, list)
                or not 0 < len(enum) <= MAX_ENUM
                or not all(
                    v is None or isinstance(v, str | int | float | bool) for v in enum
                )
            ):
                raise SchemaError(f"{path}.enum: 1-{MAX_ENUM} scalar values.")
        for keyword in ("minLength", "maxLength", "minItems", "maxItems"):
            if keyword in node and (
                isinstance(node[keyword], bool)
                or not isinstance(node[keyword], int)
                or node[keyword] < 0
            ):
                raise SchemaError(f"{path}.{keyword}: a non-negative integer.")
        for keyword in ("minimum", "maximum"):
            if keyword in node and (
                isinstance(node[keyword], bool)
                or not isinstance(node[keyword], int | float)
            ):
                raise SchemaError(f"{path}.{keyword}: a number.")
        for keyword in ("title", "description"):
            if keyword in node and not isinstance(node[keyword], str):
                raise SchemaError(f"{path}.{keyword}: a string.")
        if not types:
            raise SchemaError(f"{path}: 'type' is required.")


def validate(schema: dict[str, Any], value: Any) -> list[str]:
    """Every violation of ``schema`` by ``value``, as ``$.path: reason`` (at
    most ``MAX_ERRORS``). Empty means valid."""
    errors: list[str] = []
    stack: list[tuple[dict[str, Any], Any, str]] = [(schema, value, "$")]
    while stack and len(errors) < MAX_ERRORS:
        node, item, path = stack.pop()
        types = _types(node, path)
        matched = next((t for t in sorted(types) if _is_type(item, t)), None)
        if matched is None:
            errors.append(f"{path}: expected {' or '.join(sorted(types))}.")
            continue
        if "enum" in node and item not in node["enum"]:
            errors.append(f"{path}: not one of the permitted values.")
            continue
        if matched == "string":
            if len(item) < node.get("minLength", 0):
                errors.append(f"{path}: shorter than {node['minLength']} characters.")
            if "maxLength" in node and len(item) > node["maxLength"]:
                errors.append(f"{path}: longer than {node['maxLength']} characters.")
        elif matched in ("integer", "number"):
            if "minimum" in node and item < node["minimum"]:
                errors.append(f"{path}: below {node['minimum']}.")
            if "maximum" in node and item > node["maximum"]:
                errors.append(f"{path}: above {node['maximum']}.")
        elif matched == "array":
            if len(item) < node.get("minItems", 0):
                errors.append(f"{path}: fewer than {node['minItems']} items.")
            if "maxItems" in node and len(item) > node["maxItems"]:
                errors.append(f"{path}: more than {node['maxItems']} items.")
            if "items" in node:
                stack.extend(
                    (node["items"], child, f"{path}[{index}]")
                    for index, child in enumerate(item)
                )
        elif matched == "object":
            properties = node.get("properties") or {}
            for name in node.get("required") or []:
                if name not in item:
                    errors.append(f"{path}.{name}: required.")
            if not node.get("additionalProperties", False):
                extra = sorted(set(item) - set(properties))
                if extra:
                    errors.append(f"{path}: unexpected field(s) {', '.join(extra[:5])}.")
            stack.extend(
                (properties[name], item[name], f"{path}.{name}")
                for name in properties
                if name in item
            )
    return errors


def vendor_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A copy for a vendor's native structured-output mode: validation-only
    constraints removed, and every object closed. The platform still
    validates the result against the *original* schema."""

    def convert(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, item in node.items():
            if key in _VALIDATION_ONLY:
                continue
            if key == "properties":
                out[key] = {name: convert(child) for name, child in item.items()}
            elif key == "items":
                out[key] = convert(item)
            else:
                out[key] = item
        if "object" in _types(node, "$"):
            out["additionalProperties"] = False
            out.setdefault("properties", {})
        return out

    converted = convert(schema)
    return converted if isinstance(converted, dict) else {}


def _types(node: dict[str, Any], path: str) -> frozenset[str]:
    raw = node.get("type")
    values = raw if isinstance(raw, list) else [raw] if raw is not None else []
    if not all(isinstance(v, str) and v in TYPES for v in values):
        raise SchemaError(f"{path}.type: one of {', '.join(sorted(TYPES))}.")
    return frozenset(values)


def _is_type(value: Any, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    return False

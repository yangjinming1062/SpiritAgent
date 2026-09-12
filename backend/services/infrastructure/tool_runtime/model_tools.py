import math
from typing import Any

from components import get_logger, safe_json_loads

logger = get_logger(__name__)


def coerce_tool_args(tool_name: str, args: dict[str, Any], schema: dict | None) -> dict[str, Any]:
    if not args or not isinstance(args, dict) or not schema:
        return args

    properties = (schema.get("parameters") or {}).get("properties")
    if not properties:
        return args

    for key, value in args.items():
        prop_schema = properties.get(key)
        if not prop_schema:
            continue
        expected = prop_schema.get("type")

        if expected == "array" and value is not None and not isinstance(value, list | tuple):
            if isinstance(value, str):
                coerced = _coerce_value(value, expected, schema=prop_schema)
                if coerced is not value:
                    args[key] = coerced
                    continue
                if value.strip().startswith("["):
                    logger.warning(
                        "coerce_tool_args: looks like a JSON array string but could not be parsed, falling back to single-element list",
                        extra={"tool_name": tool_name, "key": key},
                    )
            args[key] = [value]
            continue

        if not isinstance(value, str):
            continue
        if not expected and not _schema_allows_null(prop_schema):
            continue
        coerced = _coerce_value(value, expected, schema=prop_schema)
        if coerced is not value:
            args[key] = coerced

    return args


def _coerce_value(value: str, expected_type, schema: dict | None = None) -> Any:
    if _schema_allows_null(schema) and value.strip().lower() == "null":
        return None

    if isinstance(expected_type, list):
        for t in expected_type:
            result = _coerce_value(value, t, schema=schema)
            if result is not value:
                return result
        return value

    match expected_type:
        case "integer" | "number":
            return _coerce_number(value, integer_only=(expected_type == "integer"))
        case "boolean":
            return _coerce_boolean(value)
        case "array":
            return _coerce_json(value, list)
        case "object":
            return _coerce_json(value, dict)
        case "null" if value.strip().lower() == "null":
            return None
        case _:
            return value


def _schema_allows_null(schema: dict | None) -> bool:
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if schema_type == "null":
        return True
    if isinstance(schema_type, list) and "null" in schema_type:
        return True
    if schema.get("nullable") is True:
        return True
    for union_key in ("anyOf", "oneOf"):
        variants = schema.get(union_key)
        if isinstance(variants, list) and any(isinstance(v, dict) and v.get("type") == "null" for v in variants):
            return True
    return False


def _coerce_json(value: str, expected_python_type: type) -> Any:
    parsed = safe_json_loads(value)
    return parsed if isinstance(parsed, expected_python_type) else value


def _coerce_number(value: str, integer_only: bool = False) -> Any:
    try:
        f = float(value)
    except (ValueError, OverflowError):
        return value
    if not math.isfinite(f):
        return value
    if f == int(f):
        return int(f)
    return value if integer_only else f


def _coerce_boolean(value: str) -> Any:
    low = value.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return value

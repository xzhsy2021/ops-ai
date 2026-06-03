from __future__ import annotations

from typing import Any, Dict, List
from fastapi import HTTPException


def validate_schema(arguments: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Small JSON-schema subset validator for tool input.

    It intentionally avoids adding a runtime dependency. It validates required
    fields, additionalProperties=false, primitive types, arrays and objects.
    """
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise HTTPException(status_code=400, detail="Tool arguments must be a JSON object")
    schema = schema or {"type": "object"}
    props = schema.get("properties") or {}
    required: List[str] = schema.get("required") or []
    for key in required:
        if key not in arguments or arguments.get(key) is None:
            raise HTTPException(status_code=400, detail=f"Missing required tool argument: {key}")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(arguments.keys()) - set(props.keys()))
        if extra:
            raise HTTPException(status_code=400, detail=f"Unknown tool arguments: {', '.join(extra)}")
    normalized = {}
    for key, value in arguments.items():
        spec = props.get(key, {})
        normalized[key] = _coerce_value(key, value, spec)
    return normalized


def _coerce_value(key: str, value: Any, spec: Dict[str, Any]) -> Any:
    typ = spec.get("type")
    if typ == "string":
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.strip()
    if typ == "integer":
        try:
            return int(value)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Argument {key} must be integer")
    if typ == "number":
        try:
            return float(value)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Argument {key} must be number")
    if typ == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
    if typ == "array":
        if value is None:
            return []
        if not isinstance(value, list):
            raise HTTPException(status_code=400, detail=f"Argument {key} must be array")
        item_spec = spec.get("items") or {}
        return [_coerce_value(f"{key}[]", item, item_spec) for item in value]
    if typ == "object":
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail=f"Argument {key} must be object")
        return value
    return value

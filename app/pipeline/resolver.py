"""Pipeline 变量解析器 — 统一优先级解析与追踪输出"""
from typing import Dict, Any, List, Optional, Tuple
from .variables import get_bindable_fields, get_variable_registry

SOURCE_RUNTIME = "runtime"
SOURCE_ENVIRONMENT = "environment"
SOURCE_SERVICE = "service"
SOURCE_PIPELINE_DEFAULT = "pipeline_default"
SOURCE_LITERAL = "literal"


def _is_binding_field(field_value: Any) -> bool:
    return isinstance(field_value, dict) and field_value.get("mode") == "binding"


def _is_literal_field(field_value: Any) -> bool:
    if isinstance(field_value, dict) and field_value.get("mode") == "literal":
        return True
    return not isinstance(field_value, dict) or "mode" not in field_value


def _extract_literal_value(field_value: Any) -> str:
    if isinstance(field_value, dict):
        return str(field_value.get("value", ""))
    return str(field_value) if field_value is not None else ""


def _extract_binding_var(field_value: Dict) -> Optional[str]:
    return field_value.get("var")


def _normalize_step_config(config: Optional[Dict]) -> Dict[str, Any]:
    if not isinstance(config, dict):
        return {"fields": {}, "defaults": {}}
    if "fields" in config:
        fields = config.get("fields", {})
        defaults = config.get("defaults", {})
        return {"fields": dict(fields) if isinstance(fields, dict) else {},
                "defaults": dict(defaults) if isinstance(defaults, dict) else {}}
    if not config:
        return {"fields": {}, "defaults": {}}
    return {"fields": dict(config), "defaults": {}}


def resolve_step_field(
    field_name: str,
    field_value: Any,
    step_type: str,
    variables: Dict[str, Any],
    fallback_defaults: Dict[str, Any],
) -> Tuple[str, str, str, str]:
    """
    解析单个步骤字段。
    返回: (resolved_value, source_type, source_key, var_name)
    """
    if _is_binding_field(field_value):
        var_name = _extract_binding_var(field_value) or field_name
        if var_name in variables:
            return str(variables[var_name]), SOURCE_SERVICE, f"service.{var_name}", var_name
        if var_name in fallback_defaults:
            return str(fallback_defaults[var_name]), SOURCE_PIPELINE_DEFAULT, f"pipeline.{var_name}", var_name
        return "", SOURCE_PIPELINE_DEFAULT, "", var_name

    literal = _extract_literal_value(field_value)
    if literal:
        return literal, SOURCE_LITERAL, "literal", ""
    if field_name in fallback_defaults:
        return str(fallback_defaults[field_name]), SOURCE_PIPELINE_DEFAULT, f"pipeline.{field_name}", ""
    return "", SOURCE_LITERAL, "literal", ""


def resolve_pipeline_steps(
    steps: List[Dict[str, Any]],
    resolved_variables: Dict[str, Any],
    pipeline_defaults: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    解析所有 Pipeline 步骤的绑定字段。
    返回: (resolved_steps, trace_entries)
    """
    pd = pipeline_defaults or {}
    resolved_steps: List[Dict[str, Any]] = []
    trace: List[Dict[str, Any]] = []

    for step in steps:
        step_type = step.get("step_type", step.get("type", ""))
        step_name = step.get("name", "")
        raw_config = step.get("config", {})
        normalized = _normalize_step_config(raw_config)
        fields = normalized["fields"]
        fallback_defaults = normalized.get("defaults", {})

        resolved_fields: Dict[str, Any] = {}
        for fname, fval in fields.items():
            resolved_value, source_type, source_key, var_name = resolve_step_field(
                fname, fval, step_type, resolved_variables, {**pd, **fallback_defaults}
            )
            resolved_fields[fname] = resolved_value
            trace.append({
                "step_id": step.get("id", ""),
                "step_name": step_name,
                "step_type": step_type,
                "field": fname,
                "value": resolved_value,
                "source_type": source_type,
                "source_key": source_key,
                "bound_var": var_name,
            })

        resolved_steps.append({
            **{k: v for k, v in step.items() if k != "config"},
            "config": {**fields, **(resolved_fields if fields else {})},
            "resolved_fields": resolved_fields,
        })

    return resolved_steps, trace


def validate_required_fields(
    resolved_steps: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """验证 required 字段是否已解析为非空值"""
    errors: List[Dict[str, str]] = []
    registry = get_variable_registry()
    required_vars = {v["name"] for v in registry if v.get("required_by")}

    for step in resolved_steps:
        step_type = step.get("step_type", step.get("type", ""))
        bindable = {b["field"]: b["var"] for b in get_bindable_fields(step_type)}
        resolved = step.get("resolved_fields", {})
        for fname, varname in bindable.items():
            if varname in required_vars:
                val = resolved.get(fname, "")
                if not val:
                    errors.append({
                        "step": step.get("name", ""),
                        "step_type": step_type,
                        "field": fname,
                        "variable": varname,
                        "message": f"必需变量 '{varname}' ({step_type}.{fname}) 未解析",
                    })
    return errors

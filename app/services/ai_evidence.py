from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd|secret|token|access[_-]?key|secret[_-]?key|authorization|cookie)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)(jdbc:[^\s]+://[^\s:]+:)[^@\s]+(@)"),
]


def mask_sensitive(value: Any, limit: int = 20000) -> str:
    text = str(value if value is not None else "")[:limit]
    for pattern in SENSITIVE_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}=******" if "jdbc:" not in m.group(1).lower() else f"{m.group(1)}******{m.group(2)}", text)
    return text


def as_json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def normalize_list(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        result = []
        for idx, item in enumerate(value, start=1):
            if isinstance(item, dict):
                result.append(item)
            else:
                result.append({"id": f"item-{idx}", "content": mask_sensitive(item)})
        return result
    if isinstance(value, dict):
        return [value]
    return [{"id": "item-1", "content": mask_sensitive(value)}]


def normalize_analysis_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(payload or {})
    facts = normalize_list(payload.get("facts"))
    inferences = normalize_list(payload.get("inferences"))
    recommendations = normalize_list(payload.get("recommendations"))
    evidence = normalize_list(payload.get("evidence"))
    if not facts and payload.get("summary"):
        facts = [{"id": "fact-1", "source_tool": payload.get("source_tool") or "ops.ai", "content": mask_sensitive(payload.get("summary"))}]
    return {
        "summary": mask_sensitive(payload.get("summary") or "AI 分析已生成"),
        "facts": facts,
        "inferences": inferences,
        "recommendations": recommendations,
        "evidence": evidence,
        "confidence": payload.get("confidence") or "medium",
        "metadata": payload.get("metadata") or {},
    }


def finding_rows_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    normalized = normalize_analysis_payload(payload)
    for idx, item in enumerate(normalized.get("inferences") or [], start=1):
        rows.append({
            "title": item.get("title") or item.get("claim") or f"AI 推断 {idx}",
            "finding_type": item.get("finding_type") or "inference",
            "severity": item.get("severity") or item.get("risk_level") or "info",
            "claim": item.get("claim") or item.get("content") or "",
            "evidence_json": item.get("evidence") or item.get("based_on") or [],
            "suggestion": item.get("suggestion") or "",
            "confidence": item.get("confidence") or normalized.get("confidence") or "medium",
        })
    for idx, item in enumerate(normalized.get("recommendations") or [], start=1):
        rows.append({
            "title": item.get("title") or item.get("action") or f"AI 建议 {idx}",
            "finding_type": "recommendation",
            "severity": item.get("risk") or "info",
            "claim": item.get("action") or item.get("content") or "",
            "evidence_json": item.get("based_on") or [],
            "suggestion": item.get("description") or item.get("action") or item.get("content") or "",
            "confidence": item.get("confidence") or normalized.get("confidence") or "medium",
        })
    return rows

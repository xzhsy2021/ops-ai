from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from app.services.sensitive_data import mask_sensitive
from app.services.tool_registry import registry

LOG_DIRS = [Path("data/logs"), Path("logs")]
ERROR_PATTERNS = ["error", "exception", "traceback", "failed", "timeout", "refused", "denied"]


def _safe_limit(value, default=100, max_value=500):
    try:
        return max(1, min(int(value or default), max_value))
    except Exception:
        return default


def _collect_local_logs(limit_files: int = 10) -> List[Path]:
    files: List[Path] = []
    for base in LOG_DIRS:
        if base.exists() and base.is_dir():
            files.extend([p for p in base.rglob("*.log") if p.is_file()])
            files.extend([p for p in base.rglob("*.txt") if p.is_file()])
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return files[:limit_files]


def _read_tail(path: Path, limit: int) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    return lines[-limit:]


@registry.register(
    name="ops.log.tail",
    title="读取日志尾部摘要",
    description="读取平台本地日志尾部摘要。限制行数并脱敏；不做全量日志读取。",
    scopes=["ops:read"],
    risk="medium",
    category="log",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"path_hint": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 300}}, "additionalProperties": False},
)
def tail_log(args: Dict[str, Any], ctx, db):
    limit = _safe_limit(args.get("limit"), 100, 300)
    path_hint = (args.get("path_hint") or "").strip()
    candidates = _collect_local_logs()
    if path_hint:
        candidates = [p for p in candidates if path_hint.lower() in str(p).lower()]
    if not candidates:
        return {"summary": "未找到可读取的本地日志文件。", "lines": [], "truncated": False}
    path = candidates[0]
    lines = [mask_sensitive(x, 2000) for x in _read_tail(path, limit)]
    return {"summary": f"读取 {path} 最近 {len(lines)} 行。", "path": str(path), "lines": lines, "truncated": True}


@registry.register(
    name="ops.log.search",
    title="搜索日志关键词",
    description="在平台本地日志中搜索关键词。限制文件数、返回行数和字节数并脱敏。",
    scopes=["ops:read"],
    risk="medium",
    category="log",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"keyword": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 300}}, "required": ["keyword"], "additionalProperties": False},
)
def search_log(args: Dict[str, Any], ctx, db):
    keyword = str(args.get("keyword") or "").strip()
    limit = _safe_limit(args.get("limit"), 100, 300)
    if not keyword:
        return {"summary": "keyword 不能为空", "matches": []}
    matches = []
    for path in _collect_local_logs(10):
        for i, line in enumerate(_read_tail(path, 2000), start=1):
            if keyword.lower() in line.lower():
                matches.append({"path": str(path), "line": i, "content": mask_sensitive(line, 2000)})
                if len(matches) >= limit:
                    break
        if len(matches) >= limit:
            break
    return {"summary": f"关键词 {keyword} 命中 {len(matches)} 行。", "matches": matches, "total": len(matches), "truncated": len(matches) >= limit}


@registry.register(
    name="ops.log.summarize_errors",
    title="汇总异常日志",
    description="扫描平台本地日志尾部中的错误/异常/超时等关键词并生成摘要。",
    scopes=["ops:read"],
    risk="medium",
    category="log",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 300}}, "additionalProperties": False},
)
def summarize_errors(args: Dict[str, Any], ctx, db):
    limit = _safe_limit(args.get("limit"), 100, 300)
    hits = []
    counts: Dict[str, int] = {}
    for path in _collect_local_logs(10):
        for line in _read_tail(path, 2000):
            low = line.lower()
            matched = [p for p in ERROR_PATTERNS if p in low]
            if matched:
                key = matched[0]
                counts[key] = counts.get(key, 0) + 1
                if len(hits) < limit:
                    hits.append({"path": str(path), "pattern": key, "content": mask_sensitive(line, 2000)})
    return {"summary": f"发现异常日志 {sum(counts.values())} 条。", "counts": counts, "matches": hits, "truncated": sum(counts.values()) > len(hits)}


@registry.register(
    name="ops.log.find_patterns",
    title="识别日志模式",
    description="识别常见安全/错误日志模式。",
    scopes=["ops:read"],
    risk="medium",
    category="log",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"patterns": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer"}}, "additionalProperties": False},
)
def find_patterns(args: Dict[str, Any], ctx, db):
    patterns = args.get("patterns") or ["select.*from", "union select", "<script", "../", "No space left", "Connection refused"]
    combined = []
    for pat in patterns[:20]:
        try:
            regex = re.compile(str(pat), re.I)
        except Exception:
            continue
        for path in _collect_local_logs(10):
            for line in _read_tail(path, 1000):
                if regex.search(line):
                    combined.append({"pattern": str(pat), "path": str(path), "content": mask_sensitive(line, 2000)})
                    break
    return {"summary": f"识别到 {len(combined)} 条模式命中。", "matches": combined[:_safe_limit(args.get('limit'), 100, 200)]}


@registry.register(
    name="ops.log.get_recent_exceptions",
    title="获取近期异常",
    description="获取近期 exception/traceback/error 日志摘要。",
    scopes=["ops:read"],
    risk="medium",
    category="log",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False},
)
def recent_exceptions(args: Dict[str, Any], ctx, db):
    return summarize_errors(args, ctx, db)

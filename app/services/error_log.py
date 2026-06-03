"""Recent error-log aggregation for diagnostics and MCP read-only tools."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from app.core.config import ROOT_DIR, get_runtime_path

ERROR_KEYWORDS = (
    " error ", "[error]", "exception", "traceback", "failed", "failure",
    "critical", "fatal", "异常", "失败", "错误",
)
WARNING_KEYWORDS = (" warning ", "[warning]", " warn ", "[warn]", "警告")
LOG_LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,\.]\d+)?)?\s*"
    r"(?:\[(?P<level>[A-Z]+)\])?\s*"
    r"(?:(?P<module>[A-Za-z0-9_.:-]+):\s*)?"
    r"(?P<message>.*)$"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _candidate_log_dirs() -> List[Path]:
    seen: set[str] = set()
    dirs = [
        Path(get_runtime_path("LOG_DIR", "logs")),
        Path(ROOT_DIR) / "logs",
        Path(ROOT_DIR) / "data" / "logs",
    ]
    result: List[Path] = []
    for item in dirs:
        try:
            key = str(item.resolve())
        except Exception:
            key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _iter_log_files() -> Iterable[Path]:
    env_files = [x.strip() for x in os.getenv("OPS_EXTRA_LOG_FILES", "").split(os.pathsep) if x.strip()]
    for raw in env_files:
        path = Path(raw)
        if path.exists() and path.is_file():
            yield path
    for directory in _candidate_log_dirs():
        if not directory.exists() or not directory.is_dir():
            continue
        for pattern in ("*.log", "*.log.*", "*.txt"):
            for path in directory.glob(pattern):
                if path.is_file() and not path.is_symlink():
                    yield path


def _tail_lines(path: Path, max_bytes: int = 512 * 1024) -> List[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(max(0, size - max_bytes))
            data = fh.read(max_bytes)
    except Exception:
        return []
    return data.decode("utf-8", errors="ignore").splitlines()


def _classify(line: str) -> str:
    lowered = f" {line.lower()} "
    if any(key in lowered for key in ERROR_KEYWORDS):
        return "error"
    if any(key in lowered for key in WARNING_KEYWORDS):
        return "warn"
    return "info"


def _parse_line(path: Path, line_no: int, line: str) -> Dict[str, Any]:
    clean = line.strip()
    match = LOG_LINE_RE.match(clean)
    level = _classify(clean)
    timestamp = ""
    module = ""
    summary = clean
    if match:
        gd = match.groupdict()
        timestamp = gd.get("time") or ""
        raw_level = (gd.get("level") or "").lower()
        if raw_level in {"error", "critical", "fatal"}:
            level = "error"
        elif raw_level in {"warn", "warning"} and level != "error":
            level = "warn"
        module = gd.get("module") or ""
        summary = (gd.get("message") or clean).strip() or clean
    if len(summary) > 500:
        summary = summary[:497] + "..."
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(tzinfo=None).isoformat()
    except Exception:
        mtime = ""
    return {
        "time": timestamp or mtime,
        "level": level,
        "source": path.name,
        "path": str(path),
        "line_no": line_no,
        "module": module,
        "summary": summary,
        "raw": clean[:2000],
    }


def get_recent_errors(limit: int = 20, include_warnings: bool = True) -> Dict[str, Any]:
    """Return structured recent errors and warnings from local OPS log files.

    The output is intentionally bounded and suitable for a diagnostics page,
    export report, or MCP read-only tool response.
    """
    limit = max(1, min(int(limit or 20), 200))
    entries: List[Dict[str, Any]] = []
    scanned_files: List[Dict[str, Any]] = []
    for path in sorted(set(_iter_log_files()), key=lambda p: str(p)):
        try:
            stat = path.stat()
        except Exception:
            continue
        scanned_files.append({
            "file": path.name,
            "path": str(path),
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(tzinfo=None).isoformat(),
        })
        lines = _tail_lines(path)
        start_line = max(1, len(lines) - len(lines[-2000:]) + 1)
        for offset, line in enumerate(lines[-2000:], start=start_line):
            if not line.strip():
                continue
            level = _classify(line)
            if level == "info" or (level == "warn" and not include_warnings):
                continue
            entries.append(_parse_line(path, offset, line))
    # mtime-backed timestamps sort lexicographically with parsed ISO-like values well enough here.
    entries.sort(key=lambda x: str(x.get("time") or ""), reverse=True)
    entries = entries[:limit]
    error_count = sum(1 for x in entries if x.get("level") == "error")
    warn_count = sum(1 for x in entries if x.get("level") == "warn")
    status = "error" if error_count else "warn" if warn_count else "ok"
    return {
        "status": status,
        "message": "发现最近错误日志" if error_count else "发现最近警告日志" if warn_count else "未发现最近错误或警告日志",
        "generated_at": _now_iso(),
        "limit": limit,
        "scanned_files": scanned_files[:50],
        "error_count": error_count,
        "warning_count": warn_count,
        "items": entries,
    }


def error_log_overview(limit: int = 5) -> Dict[str, Any]:
    data = get_recent_errors(limit=limit, include_warnings=True)
    return {
        "status": data.get("status"),
        "message": data.get("message"),
        "error_count": data.get("error_count", 0),
        "warning_count": data.get("warning_count", 0),
        "latest": (data.get("items") or [])[:limit],
    }

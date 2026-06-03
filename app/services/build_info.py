"""Build/runtime version metadata for release-route hardening diagnostics."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from app.core.config import ROOT_DIR

BACKEND_STARTED_AT = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
BACKEND_VERSION = "2.1.13"

_CHECK_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}


def _project_root() -> Path:
    return Path(ROOT_DIR).resolve()


def _frontend_root() -> Path:
    return _project_root() / "frontend"


def _dist_dir() -> Path:
    return _frontend_root() / "dist"


def _iso_mtime(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(tzinfo=None).isoformat()


def _latest_file(paths: Iterable[Path]) -> Path | None:
    latest: Path | None = None
    latest_mtime = -1.0
    for path in paths:
        try:
            if path.is_file():
                mtime = path.stat().st_mtime
                if mtime > latest_mtime:
                    latest = path
                    latest_mtime = mtime
        except OSError:
            continue
    return latest


def _iter_source_files() -> Iterable[Path]:
    frontend = _frontend_root()
    for rel in ["src", "index.html", "package.json", "vite.config.ts", "tsconfig.json"]:
        path = frontend / rel
        if path.is_file():
            yield path
        elif path.is_dir():
            for item in path.rglob("*"):
                if item.is_file() and item.suffix in {".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json"}:
                    yield item


def _read_generated_frontend_build() -> Dict[str, Any]:
    build_ts = _frontend_root() / "src" / "generated" / "buildInfo.ts"
    if not build_ts.exists():
        return {}
    try:
        text = build_ts.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    match = re.search(r"FRONTEND_BUILD_INFO\s*=\s*(\{.*?\})\s+as\s+const", text, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except Exception:
        return {}


def get_build_info() -> Dict[str, Any]:
    root = _project_root()
    frontend = _frontend_root()
    dist = _dist_dir()
    index_html = dist / "index.html"
    assets = dist / "assets"
    js_assets = sorted(assets.glob("*.js")) if assets.exists() else []
    css_assets = sorted(assets.glob("*.css")) if assets.exists() else []
    dist_latest = _latest_file([index_html, *js_assets, *css_assets])
    source_latest = _latest_file(_iter_source_files())
    dist_mtime = dist_latest.stat().st_mtime if dist_latest else 0
    source_mtime = source_latest.stat().st_mtime if source_latest else 0
    stale = bool(source_latest and dist_latest and source_mtime > dist_mtime + 5)
    frontend_build = _read_generated_frontend_build()
    status = "ok"
    message = "前后端构建信息已读取"
    if not index_html.exists() or not js_assets:
        status = "error"
        message = "frontend/dist 构建产物缺失或不完整"
    elif stale:
        status = "warn"
        message = "前端源码比 dist 更新，请重新执行 npm run build"
    return {
        "status": status,
        "message": message,
        "backend": {
            "version": BACKEND_VERSION,
            "started_at": BACKEND_STARTED_AT,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "frontend": {
            "version": frontend_build.get("version") or "unknown",
            "built_at": frontend_build.get("builtAt") or _iso_mtime(dist_latest),
            "git_commit": frontend_build.get("gitCommit") or "unknown",
            "node_version": frontend_build.get("nodeVersion") or "unknown",
            "root": str(frontend),
            "dist_dir": str(dist),
            "index_html": str(index_html),
            "dist_exists": dist.exists(),
            "index_exists": index_html.exists(),
            "js_assets": len(js_assets),
            "css_assets": len(css_assets),
            "latest_dist_file": dist_latest.name if dist_latest else "",
            "latest_dist_mtime": _iso_mtime(dist_latest),
            "latest_source_file": str(source_latest.relative_to(root)) if source_latest else "",
            "latest_source_mtime": _iso_mtime(source_latest),
            "dist_stale": stale,
        },
        "checks": {
            "serve_frontend": os.getenv("SERVE_FRONTEND", "auto"),
            "dist_ready": index_html.exists() and bool(js_assets),
            "dist_stale": stale,
        },
    }


def get_frontend_build_check() -> Dict[str, Any]:
    now = datetime.now(timezone.utc).timestamp()
    if _CHECK_CACHE["data"] is not None and (now - _CHECK_CACHE["ts"]) < 30:
        return _CHECK_CACHE["data"]
    info = get_build_info()
    frontend = info["frontend"]
    result = {
        "status": info["status"],
        "message": info["message"],
        "dist_dir": frontend["dist_dir"],
        "index_html": frontend["index_html"],
        "js_assets": frontend["js_assets"],
        "css_assets": frontend["css_assets"],
        "latest_dist_mtime": frontend["latest_dist_mtime"],
        "latest_source_file": frontend["latest_source_file"],
        "latest_source_mtime": frontend["latest_source_mtime"],
        "dist_stale": frontend["dist_stale"],
        "serve_frontend": info["checks"]["serve_frontend"],
    }
    _CHECK_CACHE["ts"] = now
    _CHECK_CACHE["data"] = result
    return result


def get_frontend_build_check_force() -> Dict[str, Any]:
    """Force-refresh frontend build check, bypassing TTL cache."""
    info = get_build_info()
    frontend = info["frontend"]
    return {
        "status": info["status"],
        "message": info["message"],
        "dist_dir": frontend["dist_dir"],
        "index_html": frontend["index_html"],
        "js_assets": frontend["js_assets"],
        "css_assets": frontend["css_assets"],
        "latest_dist_mtime": frontend["latest_dist_mtime"],
        "latest_source_file": frontend["latest_source_file"],
        "latest_source_mtime": frontend["latest_source_mtime"],
        "dist_stale": frontend["dist_stale"],
        "serve_frontend": info["checks"]["serve_frontend"],
    }

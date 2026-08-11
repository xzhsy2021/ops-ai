#!/usr/bin/env python3
"""Preflight checks for local/team OPS startup.

The script is intentionally dependency-free so it can run before a virtualenv is
ready. It validates the conditions that most often break single-process local
startup: Python version, Node/npm availability, frontend/dist freshness, writable
runtime directories, SQLite accessibility and port occupancy.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR") or ROOT / "data")


@dataclass
class Check:
    name: str
    status: str
    message: str
    details: dict


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _check(name: str, status: str, message: str, **details) -> Check:
    return Check(name=name, status=status, message=message, details=details)


def _latest_file(paths: Iterable[Path]) -> Path | None:
    latest = None
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
    for rel in ["src", "index.html", "package.json", "vite.config.ts", "tsconfig.json"]:
        path = FRONTEND / rel
        if path.is_file():
            yield path
        elif path.is_dir():
            for item in path.rglob("*"):
                if item.is_file() and item.suffix.lower() in {".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json"}:
                    yield item


def _python_check() -> Check:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 10):
        return _check("python", "error", "Python 版本偏低，建议使用 3.10+", version=version, executable=sys.executable)
    return _check("python", "ok", "Python 版本可用", version=version, executable=sys.executable)


def _node_check() -> Check:
    node = which("node")
    npm = which("npm")
    if not node or not npm:
        return _check("node_npm", "warn", "未检测到 Node/npm；如需重新构建前端，请安装 Node.js 18+", node=bool(node), npm=bool(npm))
    return _check("node_npm", "ok", "Node/npm 可用", node=node, npm=npm)


def _frontend_dist_check(require_dist: bool, deep_scan: bool = False) -> Check:
    index = DIST / "index.html"
    assets = DIST / "assets"
    js_assets = sorted(assets.glob("*.js")) if assets.exists() else []
    css_assets = sorted(assets.glob("*.css")) if assets.exists() else []
    dist_latest = _latest_file([index, *js_assets, *css_assets])
    details = {
        "dist_dir": str(DIST),
        "index_exists": index.exists(),
        "js_assets": len(js_assets),
        "css_assets": len(css_assets),
        "latest_dist_file": dist_latest.name if dist_latest else "",
        "latest_dist_mtime": datetime.fromtimestamp(dist_latest.stat().st_mtime).isoformat() if dist_latest else None,
    }
    if not index.exists() or not js_assets:
        status = "error" if require_dist else "warn"
        return _check("frontend_dist", status, "frontend/dist 缺失或不完整；单进程生产模式需要先构建前端", **details)

    stale = False
    if not deep_scan:
        src_dir = FRONTEND / "src"
        if src_dir.exists() and index.exists():
            src_mtime = src_dir.stat().st_mtime
            dist_mtime = index.stat().st_mtime
            if src_mtime <= dist_mtime + 5:
                details["latest_source_file"] = ""
                details["latest_source_mtime"] = None
                details["dist_stale"] = False
                details["scan_mode"] = "shallow"
                return _check("frontend_dist", "ok", "frontend/dist 已构建且未检测到过期（浅层检查）", **details)
            details["scan_mode"] = "shallow-fallback"

    source_latest = _latest_file(_iter_source_files())
    details["latest_source_file"] = str(source_latest.relative_to(ROOT)) if source_latest else ""
    details["latest_source_mtime"] = datetime.fromtimestamp(source_latest.stat().st_mtime).isoformat() if source_latest else None
    details["scan_mode"] = "deep"
    stale = bool(source_latest and dist_latest and source_latest.stat().st_mtime > dist_latest.stat().st_mtime + 5)
    details["dist_stale"] = stale
    if stale:
        return _check("frontend_dist", "warn", "frontend/dist 比源码旧，建议执行 cd frontend && npm ci && npm run build", **details)
    return _check("frontend_dist", "ok", "frontend/dist 已构建且未检测到过期", **details)


def _package_files_check() -> Check:
    req = ROOT / "requirements.txt"
    pkg = FRONTEND / "package.json"
    lock = FRONTEND / "package-lock.json"
    missing = [str(p.relative_to(ROOT)) for p in [req, pkg, lock] if not p.exists()]
    if missing:
        return _check("package_files", "error", "必要依赖声明文件缺失", missing=missing)
    return _check("package_files", "ok", "依赖声明文件存在", files=["requirements.txt", "frontend/package.json", "frontend/package-lock.json"])


def _writable_dir(path: Path) -> tuple[bool, str]:
    probe = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        # Use a unique temporary file. A fixed probe can be left behind by a
        # different process/identity and make a writable directory look broken.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".ops_preflight_", suffix=".tmp",
            dir=path, delete=False,
        ) as handle:
            probe = Path(handle.name)
            handle.write("ok")
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        if probe is not None:
            try:
                probe.unlink(missing_ok=True)
            except Exception:
                pass


def _runtime_dirs_check() -> Check:
    dirs = {
        "app_data": APP_DATA_DIR,
        "uploads": Path(os.getenv("UPLOAD_DIR") or APP_DATA_DIR / "uploads"),
        "keys": Path(os.getenv("KEYS_DIR") or APP_DATA_DIR / "keys"),
        "backups": Path(os.getenv("BACKUP_DIR") or APP_DATA_DIR / "backups"),
        "logs": Path(os.getenv("LOG_DIR") or APP_DATA_DIR / "logs"),
        "runtime": Path(os.getenv("RUNTIME_DIR") or APP_DATA_DIR / "runtime"),
        "reports": Path(os.getenv("REPORT_DIR") or APP_DATA_DIR / "reports"),
    }
    results = {}
    errors: List[str] = []
    for name, path in dirs.items():
        ok, err = _writable_dir(path)
        results[name] = {"path": str(path), "writable": ok, "error": err}
        if not ok:
            errors.append(name)
    if errors:
        return _check("runtime_dirs", "error", "运行目录不可写", items=results, errors=errors)
    return _check("runtime_dirs", "ok", "运行目录可写", items=results)


def _sqlite_check() -> Check:
    db_path = Path(os.getenv("DATABASE_PATH") or APP_DATA_DIR / "ops.db")
    if not db_path.exists():
        return _check("sqlite", "warn", "SQLite 数据库尚未创建，首次启动会自动初始化", path=str(db_path), exists=False)
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return _check("sqlite", "ok", "SQLite 可访问", path=str(db_path), exists=True, size_bytes=db_path.stat().st_size)
    except Exception as exc:
        return _check("sqlite", "error", f"SQLite 访问失败: {exc}", path=str(db_path), exists=True)


def _port_check(host: str, port: int) -> Check:
    # Bind check works for startup preflight and avoids external dependencies.
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_host = "0.0.0.0" if host in {"0.0.0.0", "::"} else host
        sock.bind((bind_host, port))
        sock.close()
        return _check("port", "ok", "端口可用", host=host, port=port)
    except OSError as exc:
        return _check("port", "error", f"端口 {port} 已被占用或不可绑定", host=host, port=port, error=str(exc))


def run_checks(host: str, port: int, require_dist: bool, deep_scan: bool = False) -> dict:
    checks = [
        _python_check(),
        _node_check(),
        _package_files_check(),
        _frontend_dist_check(require_dist=require_dist, deep_scan=deep_scan),
        _runtime_dirs_check(),
        _sqlite_check(),
        _port_check(host, port),
    ]
    errors = sum(1 for c in checks if c.status == "error")
    warnings = sum(1 for c in checks if c.status == "warn")
    return {
        "status": "ok" if errors == 0 else "error",
        "generated_at": _now(),
        "root": str(ROOT),
        "platform": platform.platform(),
        "summary": {"errors": errors, "warnings": warnings, "checks": len(checks)},
        "checks": [asdict(c) for c in checks],
    }


def print_human(report: dict) -> None:
    print("=" * 64)
    print("OPS startup preflight")
    print(f"Root: {report['root']}")
    print(f"Summary: {report['summary']['errors']} error(s), {report['summary']['warnings']} warning(s)")
    for item in report["checks"]:
        prefix = "OK" if item["status"] == "ok" else "WARN" if item["status"] == "warn" else "ERROR"
        print(f"[{prefix}] {item['name']}: {item['message']}")
    print("=" * 64)


def main() -> int:
    parser = argparse.ArgumentParser(description="OPS local startup preflight checks")
    parser.add_argument("--host", default=os.getenv("HOST") or os.getenv("BACKEND_HOST") or "0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT") or os.getenv("BACKEND_PORT") or "8000"))
    parser.add_argument("--require-dist", action="store_true", help="treat missing frontend/dist as an error")
    parser.add_argument("--deep-scan", action="store_true", help="force deep source scan even when shallow check passes")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--strict-warnings", action="store_true", help="exit non-zero on warnings too")
    args = parser.parse_args()
    report = run_checks(args.host, args.port, args.require_dist, deep_scan=args.deep_scan)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
    errors = int(report["summary"]["errors"])
    warnings = int(report["summary"]["warnings"])
    if errors:
        return 2
    if args.strict_warnings and warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

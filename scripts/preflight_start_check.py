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
from typing import Iterable, List, Mapping

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
APP_DATA_DIR = Path(os.getenv("APP_DATA_DIR") or ROOT / "data")

# 直接运行 `python scripts/preflight_start_check.py` 时 sys.path[0] 是 scripts/，
# 需要显式把仓库根加入 path 才能复用同一套密钥强度判定（app.core.secrets_policy
# 只依赖标准库，因此本脚本仍然是"无第三方依赖"的）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.secrets_policy import (  # noqa: E402  (路径修正后再导入)
    classify_secret,
    is_production,
    resolve_secret,
    rotate_hint,
)


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


def _approval_signing_key_check(environ: Mapping[str, str] | None = None) -> Check:
    env = os.environ if environ is None else environ
    source = "APPROVAL_SIGNING_KEY"
    key = str(env.get(source) or "").strip()
    if not key:
        source = "QCLAW_APPROVAL_SIGNING_KEY"
        key = str(env.get(source) or "").strip()
    # 判定标准统一走 app.core.secrets_policy（与 qclaw_routing / diagnostics 同源），
    # 避免同一项目出现多套黑名单而漏掉 "change-me-to-a-…" 这类占位值。
    info = classify_secret(key, name="APPROVAL_SIGNING_KEY")
    if info["status"] != "ok":
        return _check(
            "approval_signing_key",
            "error",
            f"审批签名密钥缺失或强度不足（{info['reason']}）；请配置至少 32 位的 APPROVAL_SIGNING_KEY",
            configured=bool(key),
            source=source if key else "",
            minimum_length=32,
            reason=info["reason"],
        )
    return _check(
        "approval_signing_key",
        "ok",
        "审批签名密钥已配置",
        configured=True,
        source=source,
        minimum_length=32,
    )


def _secret_strength_check(environ: Mapping[str, str] | None = None) -> Check:
    """会话签名密钥与凭据加密密钥的强度检查。

    历史缺陷：`.env` 中这两个密钥是仓库 `.env.example` 里逐字节相同的占位值，
    而对它们唯一的生产校验是"缺失才报错"，占位值可正常启动；启动自检也只覆盖
    APPROVAL_SIGNING_KEY，于是"可伪造会话令牌 / 可解密库内凭据"的实例一路绿灯。
    """
    env = dict(os.environ) if environ is None else dict(environ)
    tracked = ("SESSION_SECRET", "OPS_SECRET_KEY")
    report = {name: classify_secret(resolve_secret(name, env), name=name) for name in tracked}
    details = {
        "keys": {
            name: {
                "status": info["status"],
                "reason": info["reason"],
                "length": info["length"],
                "fingerprint": info["fingerprint"],
                "documented_example": info["documented_example"],
            }
            for name, info in report.items()
        },
        "rotate_hint": rotate_hint(),
    }
    insecure = [name for name in tracked if report[name]["status"] == "insecure"]
    weak = [name for name in tracked if report[name]["status"] == "weak"]
    missing = [name for name in tracked if report[name]["status"] == "missing"]
    # 生产模式必须 fail-closed（拒绝启动）；非生产只告警，避免"因历史占位密钥而无法重启"
    # 造成新的可用性事故。UI 侧 diagnostics/system_health 始终按 error 呈现，不会被忽略。
    production = is_production(env)
    if insecure:
        detail = "；".join(f"{name}：{report[name]['reason']}" for name in insecure)
        message = f"会话/加密密钥不安全（{detail}）；更换命令：{rotate_hint()}"
        return _check("secret_strength", "error" if production else "warn", message, **details)
    if weak:
        detail = "；".join(f"{name}：{report[name]['reason']}" for name in weak)
        return _check("secret_strength", "warn", f"会话/加密密钥强度不足（{detail}）", **details)
    if missing:
        return _check(
            "secret_strength",
            "warn",
            f"未配置 {'、'.join(missing)}；非生产环境会使用随机会话密钥，且库内凭据不会被加密",
            **details,
        )
    return _check("secret_strength", "ok", "会话/加密密钥强度合格", **details)


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
        _approval_signing_key_check(),
        _secret_strength_check(),
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

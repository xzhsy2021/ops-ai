"""Local install diagnostics for stable delivery and regression hardening."""
from __future__ import annotations

import json
import mimetypes
import tempfile
import zipfile
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import (
    get_app_data_dir,
    get_database_path,
    get_runtime_path,
    ROOT_DIR,
)
from app.core.platform import platform_info
from app.core.secrets_policy import rotate_hint, secret_key_report
from app.services.runtime_resources import get_runtime_usage, get_storage_usage
from app.services.system_health import build_system_health
from app.services.build_info import get_build_info, get_frontend_build_check
from app.services.error_log import get_recent_errors
from app.services.recommendations import build_recommendations


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _ok(message: str, **extra: Any) -> Dict[str, Any]:
    return {"status": "ok", "message": message, **extra}


def _warn(message: str, **extra: Any) -> Dict[str, Any]:
    return {"status": "warn", "message": message, **extra}


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    return {"status": "error", "message": message, **extra}


def _project_root() -> Path:
    return Path(ROOT_DIR).resolve()


def _frontend_dist() -> Path:
    return _project_root() / "frontend" / "dist"


def _frontend_build_check() -> Dict[str, Any]:
    dist = _frontend_dist()
    index = dist / "index.html"
    assets = dist / "assets"
    js_files = sorted(assets.glob("*.js")) if assets.exists() else []
    css_files = sorted(assets.glob("*.css")) if assets.exists() else []
    data = {
        "dist_dir": str(dist),
        "index_html": str(index),
        "assets_dir": str(assets),
        "js_assets": len(js_files),
        "css_assets": len(css_files),
        "serve_frontend": os.getenv("SERVE_FRONTEND", "auto"),
    }
    if not index.exists():
        return _error("frontend/dist/index.html 不存在，单进程模式无法提供前端页面", **data)
    if not js_files:
        return _error("frontend/dist/assets 下没有 JS 资源，构建产物不完整", **data)
    return _ok("前端静态资源已构建", **data)


def _asset_mime_check() -> Dict[str, Any]:
    assets = _frontend_dist() / "assets"
    js_files = sorted(assets.glob("*.js")) if assets.exists() else []
    css_files = sorted(assets.glob("*.css")) if assets.exists() else []
    samples: List[Dict[str, str]] = []
    for fp in [*js_files[:2], *css_files[:2]]:
        mime, _ = mimetypes.guess_type(str(fp))
        samples.append({"file": fp.name, "mime": mime or ""})
    if not js_files:
        return _error("未找到可检查的 /assets/*.js 文件", samples=samples)
    bad = [s for s in samples if s["file"].endswith(".js") and s["mime"] not in {"text/javascript", "application/javascript"}]
    if bad:
        return _warn("本机 MIME 猜测异常；请用 smoke_check.py 校验真实 HTTP 响应", samples=samples)
    return _ok("静态资源 MIME 猜测正常", samples=samples)


def _dir_writable(path: Path) -> Dict[str, Any]:
    probe = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".ops_diagnostics_", suffix=".tmp",
            dir=path, delete=False,
        ) as handle:
            probe = Path(handle.name)
            handle.write("ok")
        return _ok("目录可写", path=str(path))
    except Exception as exc:
        return _error(f"目录不可写: {exc}", path=str(path))
    finally:
        if probe is not None:
            try:
                probe.unlink(missing_ok=True)
            except Exception:
                pass


def _runtime_dirs_check() -> Dict[str, Any]:
    dirs = {
        "app_data": Path(get_app_data_dir()),
        "uploads": Path(get_runtime_path("UPLOAD_DIR", "uploads")),
        "keys": Path(get_runtime_path("KEYS_DIR", "keys")),
        "backups": Path(get_runtime_path("BACKUP_DIR", "backups")),
        "logs": Path(get_runtime_path("LOG_DIR", "logs")),
        "runtime": Path(get_runtime_path("RUNTIME_DIR", "runtime")),
        "reports": Path(get_runtime_path("REPORT_DIR", "reports")),
    }
    items = {name: _dir_writable(path) for name, path in dirs.items()}
    errors = [name for name, item in items.items() if item.get("status") == "error"]
    return {
        "status": "ok" if not errors else "error",
        "message": "运行目录可写" if not errors else f"运行目录不可写: {', '.join(errors)}",
        "items": items,
    }


def _database_check(db: Session | None = None) -> Dict[str, Any]:
    db_path = Path(get_database_path())
    base = {"path": str(db_path), "exists": db_path.exists()}
    try:
        if db is not None:
            db.execute(text("SELECT 1")).scalar_one_or_none()
            base["query"] = "SELECT 1 ok"
        if db_path.exists():
            base["size_bytes"] = db_path.stat().st_size
        return _ok("SQLite 可连接", **base)
    except Exception as exc:
        return _error(f"SQLite 检查失败: {exc}", **base)


def _secret_key_check() -> Dict[str, Any]:
    """密钥强度检查。

    历史缺陷：这里只判断"是否配置 + 长度 ≥ 24"，于是与仓库 `.env.example` 逐字节相同的
    占位密钥会被报成 `ok`（"OPS_SECRET_KEY 已配置"）。而该密钥可离线伪造会话令牌
    （接管平台）并可解密库内服务器凭据，属于必须报警的高危配置。
    """
    report = secret_key_report()
    keys = report["keys"]
    payload: Dict[str, Any] = {
        "configured": keys["OPS_SECRET_KEY"]["status"] != "missing",
        "length": keys["OPS_SECRET_KEY"]["length"],
        "keys": {
            name: {
                "status": info["status"],
                "reason": info["reason"],
                "length": info["length"],
                "fingerprint": info["fingerprint"],
                "documented_example": info["documented_example"],
            }
            for name, info in keys.items()
        },
    }
    problems = [
        name
        for name in ("SESSION_SECRET", "OPS_SECRET_KEY")
        if keys[name]["status"] in ("insecure", "weak")
    ]
    if problems:
        detail = "；".join(f"{name}：{keys[name]['reason']}" for name in problems)
        return _error(
            f"密钥不安全（{detail}）。更换命令：{rotate_hint()}；"
            "也可用 scripts/rotate_secrets.py --check 体检、--apply 轮换后重启",
            **payload,
        )
    if keys["OPS_SECRET_KEY"]["status"] == "missing":
        return _warn(
            "OPS_SECRET_KEY 未配置，当前为开发兼容模式（库内凭据不加密）；"
            "正式使用建议设置 32 字符以上随机字符串",
            **payload,
        )
    return _ok("密钥强度合格", **payload)


def _python_runtime_check() -> Dict[str, Any]:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok = sys.version_info >= (3, 10)
    return (_ok if ok else _warn)("Python 版本可用" if ok else "Python 版本偏低，建议 3.10+", version=version, executable=sys.executable)


def _mcp_self_check(db: Session) -> Dict[str, Any]:
    from app.services.tool_registry import register_builtin_tools, registry
    from app.services.tool_policy import get_capability_settings
    from app.services.tool_context import ToolContext
    from app.db.models import ToolCallLog

    register_builtin_tools()
    settings = get_capability_settings(db)
    ctx = ToolContext(
        username="diagnostics",
        role="admin",
        is_admin=True,
        can_deploy=True,
        auth_type="diagnostics",
        scopes=["*"],
        allow_write=True,
        allow_prod=True,
        client_name="ops-diagnostics",
    )
    listed = registry.list_tools(db, ctx, include_disabled=True, include_schema=True, limit=500, cursor=0)
    tools = listed.get("tools", []) if isinstance(listed, dict) else listed
    schema_errors: List[str] = []
    high_risk = 0
    write_tools = 0
    for tool in tools:
        schema = tool.get("input_schema") or {}
        if schema.get("type") != "object":
            schema_errors.append(f"{tool.get('name')}: input_schema.type must be object")
        if tool.get("risk") in {"high", "critical"}:
            high_risk += 1
        if tool.get("write"):
            write_tools += 1
    try:
        recent_calls = int(db.query(ToolCallLog).count() or 0)
    except Exception:
        recent_calls = -1
    status = "ok" if not schema_errors else "warn"
    return {
        "status": status,
        "message": "MCP 工具 schema 与权限策略可读取" if not schema_errors else "部分 MCP 工具 schema 需要检查",
        "settings": settings,
        "tool_count": len(tools),
        "high_risk_tools": high_risk,
        "write_tools": write_tools,
        "schema_errors": schema_errors[:20],
        "tool_call_log_count": recent_calls,
        "capability_version": registry.capability_version(db, ctx),
    }




def _safe_dir_summary(path: Path) -> Dict[str, Any]:
    total = 0
    count = 0
    if not path.exists():
        return {"path": str(path), "exists": False, "file_count": 0, "size_bytes": 0}
    for fp in path.rglob("*"):
        if count >= 5000:
            break
        try:
            if fp.is_file() and not fp.is_symlink():
                total += fp.stat().st_size
                count += 1
        except OSError:
            continue
    return {"path": str(path), "exists": True, "file_count": count, "size_bytes": total}


def _recent_slow_requests(max_lines: int = 20) -> List[str]:
    log_file = Path(get_runtime_path("LOG_DIR", "logs")) / "ops.log"
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-1000:]
    except Exception:
        return []
    return [line for line in lines if "slow request" in line][-max_lines:]


def _performance_summary() -> Dict[str, Any]:
    dist = _frontend_dist()
    assets = dist / "assets"
    dist_summary = _safe_dir_summary(dist)
    asset_summary = _safe_dir_summary(assets)
    return {
        "status": "ok",
        "message": "性能配置与前端构建摘要已读取",
        "frontend_dist": dist_summary,
        "frontend_assets": asset_summary,
        "config": {
            "slow_request_ms": int(os.getenv("SLOW_REQUEST_MS", "500")),
            "resource_cache_ttl_seconds": int(os.getenv("RESOURCE_CACHE_TTL_SECONDS", "8")),
            "max_log_tail_lines": int(os.getenv("MAX_LOG_TAIL_LINES", "500")),
            "deploy_poll_active_ms": os.getenv("VITE_DEPLOY_POLL_ACTIVE_MS", "2000"),
            "deploy_poll_hidden_ms": os.getenv("VITE_DEPLOY_POLL_HIDDEN_MS", "15000"),
        },
        "recent_slow_requests": _recent_slow_requests(),
    }


def build_startup_checks(db: Session | None = None) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {
        "python": _python_runtime_check(),
        "platform": _ok("平台信息已读取", **platform_info()),
        "frontend_build": get_frontend_build_check(),
        "static_assets": _asset_mime_check(),
        "runtime_dirs": _runtime_dirs_check(),
        "database": _database_check(db),
        "secret_key": _secret_key_check(),
    }
    errors = sum(1 for c in checks.values() if c.get("status") == "error")
    warnings = sum(1 for c in checks.values() if c.get("status") == "warn")
    return {
        "status": "ok" if errors == 0 else "error",
        "generated_at": _now(),
        "summary": {"errors": errors, "warnings": warnings, "checks": len(checks)},
        "checks": checks,
    }



def _system_overview(db: Session | None = None) -> Dict[str, Any]:
    """Human-readable runtime overview used by diagnostics UI and JSON report."""
    app_data = Path(get_app_data_dir())
    db_path = Path(get_database_path())
    return {
        "status": "ok",
        "message": "系统概览已生成",
        "generated_at": _now(),
        "environment": os.getenv("ENV", "development"),
        "startup_mode": "single-process" if os.getenv("SERVE_FRONTEND", "auto").lower() in {"true", "auto"} else "api-only",
        "project_root": str(_project_root()),
        "app_data_dir": str(app_data),
        "database_path": str(db_path),
        "database_exists": db_path.exists(),
        "database_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "backup_dir": get_runtime_path("BACKUP_DIR", "backups"),
        "log_dir": get_runtime_path("LOG_DIR", "logs"),
        "upload_dir": get_runtime_path("UPLOAD_DIR", "uploads"),
        "runtime_dir": get_runtime_path("RUNTIME_DIR", "runtime"),
        "report_dir": get_runtime_path("REPORT_DIR", "reports"),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "platform": platform_info(),
        "serve_frontend": os.getenv("SERVE_FRONTEND", "auto"),
    }

def build_diagnostics(db: Session) -> Dict[str, Any]:
    startup = build_startup_checks(db)
    health = build_system_health(db)
    try:
        runtime = get_runtime_usage(db)
    except Exception as exc:
        runtime = {"error": str(exc)}
    try:
        storage = get_storage_usage(db)
    except Exception as exc:
        storage = {"error": str(exc)}
    try:
        mcp = _mcp_self_check(db)
    except Exception as exc:
        mcp = _warn(f"MCP 自检失败: {exc}")
    try:
        performance = _performance_summary()
    except Exception as exc:
        performance = _warn(f"性能摘要读取失败: {exc}")
    try:
        build_info = get_build_info()
    except Exception as exc:
        build_info = _warn(f"构建信息读取失败: {exc}")
    try:
        recent_errors = get_recent_errors(limit=20, include_warnings=True)
    except Exception as exc:
        recent_errors = _warn(f"最近错误日志读取失败: {exc}")
    try:
        overview = _system_overview(db)
    except Exception as exc:
        overview = _warn(f"系统概览生成失败: {exc}")

    sections = {
        "overview": overview,
        "startup": startup,
        "health": health,
        "runtime": runtime,
        "storage": storage,
        "performance": performance,
        "build_info": build_info,
        "recent_errors": recent_errors,
        "mcp": mcp,
    }
    errors = int(startup.get("summary", {}).get("errors") or 0) + int(health.get("summary", {}).get("errors") or 0)
    warnings = int(startup.get("summary", {}).get("warnings") or 0) + int(health.get("summary", {}).get("warnings") or 0)
    if build_info.get("status") == "warn":
        warnings += 1
    if build_info.get("status") == "error":
        errors += 1
    if mcp.get("status") == "warn":
        warnings += 1
    if mcp.get("status") == "error":
        errors += 1
    if recent_errors.get("status") == "warn":
        warnings += 1
    if recent_errors.get("status") == "error":
        # Recent app errors should degrade diagnostics but not make the local
        # installation unreachable by themselves. Keep them as warnings here.
        warnings += 1
    data = {
        "status": "healthy" if errors == 0 and warnings == 0 else "degraded" if errors == 0 else "unhealthy",
        "generated_at": _now(),
        "project_root": str(_project_root()),
        "startup_mode": "single-process" if os.getenv("SERVE_FRONTEND", "auto").lower() in {"true", "auto"} else "api-only",
        "platform": platform_info(),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "summary": {"errors": errors, "warnings": warnings, "sections": len(sections)},
        "sections": sections,
    }
    data["recommendations"] = build_recommendations(data)
    return data




def build_diagnostics_report(db: Session) -> Dict[str, Any]:
    """Build a single JSON-friendly diagnostics report for export or MCP."""
    diagnostics = build_diagnostics(db)
    return {
        "schema_version": "iter32.diagnostics-report.v1",
        "generated_at": _now(),
        "diagnostics": diagnostics,
        "overview": diagnostics.get("sections", {}).get("overview", {}),
        "health": diagnostics.get("sections", {}).get("health", {}),
        "startup": diagnostics.get("sections", {}).get("startup", {}),
        "build_info": diagnostics.get("sections", {}).get("build_info", {}),
        "recent_errors": diagnostics.get("sections", {}).get("recent_errors", {}),
        "mcp": diagnostics.get("sections", {}).get("mcp", {}),
        "recommendations": diagnostics.get("recommendations", []),
    }


def export_diagnostics_report_json(db: Session) -> str:
    """Write a bounded JSON report to runtime dir and return its path."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(get_runtime_path("RUNTIME_DIR", "runtime"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ops-diagnostics-report-{stamp}.json"
    out_path.write_text(json.dumps(build_diagnostics_report(db), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(out_path)

def export_diagnostics_bundle(db: Session) -> str:
    """Create a small diagnostics zip bundle for local troubleshooting."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(get_runtime_path("RUNTIME_DIR", "runtime"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ops-diagnostics-{stamp}.zip"

    diagnostics = build_diagnostics(db)
    startup = build_startup_checks(db)
    try:
        runtime = get_runtime_usage(db)
    except Exception as exc:
        runtime = {"error": str(exc)}
    try:
        storage = get_storage_usage(db)
    except Exception as exc:
        storage = {"error": str(exc)}

    log_file = Path(get_runtime_path("LOG_DIR", "logs")) / "ops.log"
    recent_errors: List[str] = []
    recent_slow: List[str] = []
    if log_file.exists():
        try:
            lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]
            recent_errors = [line for line in lines if any(key in line.lower() for key in ["error", "exception", "failed", "traceback", "失败", "异常"] )][-200:]
            recent_slow = [line for line in lines if "slow request" in line][-200:]
        except Exception:
            pass

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics-report.json", json.dumps(build_diagnostics_report(db), ensure_ascii=False, indent=2, default=str))
        zf.writestr("diagnostics.json", json.dumps(diagnostics, ensure_ascii=False, indent=2, default=str))
        zf.writestr("startup-check.json", json.dumps(startup, ensure_ascii=False, indent=2, default=str))
        zf.writestr("runtime-usage.json", json.dumps(runtime, ensure_ascii=False, indent=2, default=str))
        zf.writestr("storage-usage.json", json.dumps(storage, ensure_ascii=False, indent=2, default=str))
        zf.writestr("build-info.json", json.dumps(get_build_info(), ensure_ascii=False, indent=2, default=str))
        zf.writestr("recent-errors.log", "\n".join(recent_errors))
        zf.writestr("recent-slow-requests.log", "\n".join(recent_slow))
        zf.writestr("README.txt", "OPS diagnostics bundle. It contains local health, runtime, storage and recent error summaries. Sensitive secrets are not exported.\n")
    return str(out_path)

def log_startup_checks(db: Session | None, logger: Any) -> None:
    checks = build_startup_checks(db)
    logger.info("=" * 50)
    logger.info("  启动自检")
    for key, item in checks.get("checks", {}).items():
        status = item.get("status")
        prefix = "OK" if status == "ok" else "WARN" if status == "warn" else "ERROR"
        logger.info("  [%s] %s: %s", prefix, key, item.get("message") or "")
    logger.info("  自检汇总: %s", json.dumps(checks.get("summary", {}), ensure_ascii=False))
    logger.info("=" * 50)

"""
Ops Platform v2.1.10 - 运维发布平台
纯 V2 架构入口
"""
import os
import sys
import logging
import threading
import time
from contextlib import asynccontextmanager
from app.core.config import ensure_runtime_dirs, get_runtime_path

sys.setrecursionlimit(2000)

REAL_DIR = os.path.dirname(os.path.abspath(__file__))
if REAL_DIR not in sys.path:
    sys.path.insert(0, REAL_DIR)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from logging.handlers import RotatingFileHandler

ensure_runtime_dirs()
_log_dir = get_runtime_path("LOG_DIR", "logs")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            os.path.join(_log_dir, "ops.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ]
)
logger = logging.getLogger("ops")

logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.db import init_db
    from app.db.bootstrap import ensure_domain_defaults
    from app.core.auth_v2 import init_default_user
    from app.db.base import SessionLocal

    init_db()
    with SessionLocal() as bootstrap_db:
        ensure_domain_defaults(bootstrap_db)
    try:
        from app.config.servers import migrate_server_secrets_at_rest
        migrate_server_secrets_at_rest()
    except Exception:
        logger.exception("Failed to migrate server secrets at rest")
        raise

    db = SessionLocal()
    try:
        init_default_user(db)
        try:
            from app.maintenance.service import recover_interrupted_jobs
            recovered = recover_interrupted_jobs(db)
            if recovered:
                logger.warning("  数据清理任务已自动暂停: %s", recovered)
        except Exception:
            logger.exception("Failed to recover interrupted cleanup jobs")
        try:
            from app.services.tool_registry import ensure_builtin_registered
            ensure_builtin_registered()
            logger.info("  AI 工具注册表已初始化")
        except Exception:
            logger.exception("Failed to initialize tool registry")
        try:
            from app.api.deploy_v2 import ensure_deploy_worker_running
            ensure_deploy_worker_running()
            logger.info("  发布后台 Worker 已启动")
        except Exception:
            logger.exception("Failed to start deploy worker")
        try:
            from app.services.diagnostics import log_startup_checks
            log_startup_checks(db, logger)
        except Exception:
            logger.exception("Startup diagnostics failed")

        try:
            from app.services.audit_writer import start_audit_writer
            start_audit_writer()
            logger.info("  审计写入线程已启动")
        except Exception:
            logger.exception("Failed to start audit writer")

    finally:
        db.close()

    # 系统数和服务器数从 DB 表查询。
    try:
        from app.db import ServerRepository, SystemRepository, SessionLocal
        with SessionLocal() as _db:
            server_count = len(ServerRepository(_db).list_all())
            system_count = len(SystemRepository(_db).list_all())
    except Exception:
        server_count = 0
        system_count = 0

    logger.info("=" * 50)
    logger.info("  Ops Platform v2.1.10 已启动")
    logger.info(f"  系统数: {system_count} 服务器数: {server_count}")
    logger.info(f"  访问地址: http://localhost:8000")
    logger.info(f"  API 文档: http://localhost:8000/api/docs")
    try:
        from app.services.build_info import get_frontend_build_check
        frontend_check = get_frontend_build_check()
        app.state.frontend_check = frontend_check
        logger.info("  前端 dist: %s JS=%s CSS=%s stale=%s", frontend_check.get("status"), frontend_check.get("js_assets"), frontend_check.get("css_assets"), frontend_check.get("dist_stale"))
    except Exception:
        logger.exception("Failed to read frontend dist status")

    try:
        from app.services.sqlite_cleanup import run_cleanup
        import threading as _t
        def _schedule_cleanup():
            try:
                run_cleanup()
            except Exception:
                logger.exception("Scheduled SQLite cleanup failed")
            _t.Timer(12 * 3600, _schedule_cleanup).start()
        _t.Timer(60, _schedule_cleanup).start()
        logger.info("  SQLite 定期清理已调度 (每12h)")
    except Exception:
        logger.exception("Failed to schedule SQLite cleanup")
    logger.info("=" * 50)

    def _ssh_pool_cleanup_loop():
        import time
        from ssh_client import SSHConnectionPool
        pool = SSHConnectionPool.get_instance()
        while True:
            time.sleep(60)
            try:
                pool.cleanup()
            except Exception:
                logger.exception("SSH pool cleanup failed")

    t = threading.Thread(target=_ssh_pool_cleanup_loop, daemon=True)
    t.start()

    yield

    from app.api.deploy._shared import _shutdown_log_db
    try:
        _shutdown_log_db()
    except Exception:
        pass

    try:
        from app.services.audit_writer import shutdown_audit_writer
        shutdown_audit_writer()
        logger.info("  审计写入线程已关闭")
    except Exception:
        logger.exception("Audit writer shutdown failed")

    try:
        from app.deploy.stream import shutdown_all_subscribers
        shutdown_all_subscribers()
        logger.info("  SSE 订阅者已清空")
    except Exception:
        logger.exception("SSE subscriber shutdown failed")

    from ssh_client import SSHConnectionPool
    try:
        pool = SSHConnectionPool.get_instance()
        pool.close_all()
        logger.info("  SSH 连接池已关闭")
    except Exception:
        logger.exception("SSH 连接池关闭失败")

    logger.info("  Ops Platform 停止")


app = FastAPI(
    title="Ops Platform",
    version="2.1.10",
    description="运维发布平台 - 系统/服务中心化架构",
    # 把 Swagger UI 迁到 /api/docs，腾出 /docs 给项目文档（docs/*.md）使用
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

_cors_origins = os.getenv("CORS_ORIGINS")
_env = os.getenv("ENV", "development")

if not _cors_origins:
    if _env == "production":
        raise RuntimeError("CORS_ORIGINS must be configured in production")
    _cors_origins = "http://localhost:3000,http://127.0.0.1:3000"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.core.security import create_auth_middleware
create_auth_middleware(app)

from app.core.log_context import RequestLogFilter, set_request_id, get_request_id

for handler in logging.getLogger().handlers:
    handler.addFilter(RequestLogFilter())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s",
    force=True,
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            os.path.join(_log_dir, "ops.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)
for handler in logging.getLogger().handlers:
    handler.addFilter(RequestLogFilter())


@app.middleware("http")
async def request_id_middleware(request, call_next):
    rid = request.headers.get("X-Request-ID") or ""
    set_request_id(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = get_request_id()
    return response

_slow_request_ms = int(os.getenv("SLOW_REQUEST_MS", "500"))

@app.middleware("http")
async def slow_request_logging_middleware(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Process-Time-Ms"] = str(duration_ms)
    if request.url.path.startswith("/assets/"):
        # Hashed Vite assets are safe to cache aggressively. Keep index.html
        # uncached via pages.py / FileResponse middleware below to avoid stale UI.
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    elif request.url.path in {"/", "/login", "/dashboard", "/deploy", "/database", "/tools", "/maintenance", "/system", "/system/diagnostics"}:
        response.headers.setdefault("Cache-Control", "no-cache")
    if duration_ms >= _slow_request_ms and not request.url.path.startswith("/assets/"):
        logger.warning("slow request path=%s method=%s status=%s duration_ms=%s", request.url.path, request.method, response.status_code, duration_ms)
    return response

from app.api.helpers import register_exception_handlers
register_exception_handlers(app)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok", "service": "ops-platform"}


@app.get("/readyz", include_in_schema=False)
def readyz():
    try:
        from app.db.base import SessionLocal
        db = SessionLocal()
        try:
            from sqlalchemy import text
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return {"status": "ready", "checks": {"database": "ok"}}
    except Exception as exc:
        return {"status": "not_ready", "checks": {"database": str(exc)}}

_frontend_dist = os.path.join(REAL_DIR, "frontend", "dist")
_serve_frontend = os.getenv("SERVE_FRONTEND", "auto")
if _serve_frontend == "auto":
    if os.path.isdir(_frontend_dist):
        try:
            from app.services.build_info import get_frontend_build_check
            _frontend_check = get_frontend_build_check()
            _serve_frontend = "false" if _frontend_check.get("dist_stale") else "true"
            if _frontend_check.get("dist_stale"):
                logger.warning("  frontend/dist 已过期，自动切换为 API-only；请执行 cd frontend && npm run build")
        except Exception:
            _serve_frontend = "true"
    else:
        _serve_frontend = "false"

if _serve_frontend == "true" and os.path.isdir(_frontend_dist):
    try:
        from app.services.build_info import get_frontend_build_check
        _explicit_frontend_check = get_frontend_build_check()
        if _explicit_frontend_check.get("dist_stale"):
            msg = "frontend/dist 比源码旧；建议重新执行 cd frontend && npm ci && npm run build"
            if os.getenv("STRICT_FRONTEND_DIST", "0").lower() in {"1", "true", "yes", "on"}:
                raise RuntimeError(msg)
            logger.warning("  %s", msg)
    except RuntimeError:
        raise
    except Exception:
        logger.exception("frontend/dist 状态检查失败，继续按 SERVE_FRONTEND=true 提供静态文件")
    app.mount("/assets", StaticFiles(directory=os.path.join(_frontend_dist, "assets"), html=False), name="react-assets")
    logger.info("  前端静态文件: 由后端提供 (设置 SERVE_FRONTEND=false 可禁用)")
elif _serve_frontend == "false":
    logger.info("  前端静态文件: 独立部署模式或 dist 已过期 (后端仅提供 API)")

# /docs 提供项目根目录下 docs/ 的 markdown 文档（如集成文档 / 巡检 spec 等）。
# 仅在 docs 目录存在时挂载，避免空目录导致 500。
_docs_dir = os.path.join(REAL_DIR, "docs")
if os.path.isdir(_docs_dir):
    app.mount("/docs", StaticFiles(directory=_docs_dir, html=False), name="project-docs")
    logger.info("  项目文档: 由后端提供 /docs/  (markdown 文件)")
else:
    logger.info("  项目文档: docs/ 目录不存在，跳过 /docs 静态服务")

from app.api.deploy_v2 import deploy_v2_router, pipeline_v2_router, resource_v2_router
from app.api.auth_v2 import auth_v2_router
from app.api.groups import groups_v2_router
from app.api.servers import servers_v2_router
from app.api.jump_hosts import jump_hosts_v2_router
from app.api.config import admin_ops_router
from app.api.terminal import terminal_ws_router
from app.api.sftp import sftp_router
from app.api.maintenance import maintenance_router
from app.api.task_center import router as task_center_router, audit_router
from app.api.tools import tools_router, mcp_router, capabilities_router
from app.api.system import system_router
from app.api.reports import router as reports_router
from app.api.inspection import router as inspection_router
from app.api.db_tools import router as db_tools_router
from app.api.dashboard import router as dashboard_router
from app.api.mcp_gateway import router as mcp_gateway_router
from app.api.approvals import router as approvals_router
from app.api.execution_plans import router as execution_plans_router
from app.api.temporary_approvals import router as temporary_approvals_router
from app.api.v2.status import router as status_aggregate_router
from app.api.matrix import matrix_router, matrix_deploy_router

_v2_routers = [
    deploy_v2_router, pipeline_v2_router, resource_v2_router,
    auth_v2_router, groups_v2_router,
    servers_v2_router, jump_hosts_v2_router, admin_ops_router, sftp_router,
    maintenance_router, task_center_router, audit_router, tools_router, mcp_router, capabilities_router, system_router, reports_router, inspection_router, db_tools_router, dashboard_router, mcp_gateway_router, status_aggregate_router, approvals_router, execution_plans_router, temporary_approvals_router,
    matrix_router, matrix_deploy_router,
]
_v2_routers_ws = [
    terminal_ws_router,
]
for r in _v2_routers:
    app.include_router(r)
for r in _v2_routers_ws:
    app.include_router(r)

from app.pages import pages_router
app.include_router(pages_router)


if __name__ == "__main__":
    import uvicorn
    import platform
    workers = 1 if platform.system() == "Windows" else 1
    _port = int(os.getenv("PORT", "8000"))
    _host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=_host, port=_port, log_level="info", workers=workers)

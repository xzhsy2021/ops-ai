"""页面路由 - React SPA 分发 - V2 only"""
import os
from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

pages_router = APIRouter(tags=["前端"])



def _project_root() -> str:
    # app/pages.py lives under <project_root>/app/pages.py.
    # The previous implementation walked three directories up, which pointed to
    # the parent of the project root on Windows/Linux and made single-process
    # mode report "frontend/dist/index.html" as missing even when it existed.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _frontend_dist_dir() -> str:
    return os.path.join(_project_root(), "frontend", "dist")


REACT_DIST_DIR = _frontend_dist_dir()
REACT_INDEX_HTML = os.path.join(REACT_DIST_DIR, "index.html")

SPA_ROUTES = [
    "/",
    "/login",
    "/dashboard",
    "/deploy",
    "/pipelines",
    "/deployments",
    "/systems",
    "/system",
    "/system/status",
    "/system/diagnostics",
    "/servers",
    "/services",
    "/logs",
    "/config",
    "/groups",
    "/maintenance",
    "/files",
    "/sql-query",
    "/tasks",
    "/task-center",
    "/audit",
    "/reports",
    "/database",
    "/tools",
]


def _serve_spa():
    if os.path.exists(REACT_INDEX_HTML):
        return FileResponse(REACT_INDEX_HTML)
    return HTMLResponse(
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'><title>OPS v2</title>"
        "<style>body{font-family:Arial,'Microsoft YaHei',sans-serif;margin:48px;line-height:1.7;color:#1f2937}code{background:#f3f4f6;padding:2px 6px;border-radius:4px}.box{max-width:760px}</style>"
        "</head><body><div class='box'><h1>OPS v2</h1>"
        "<h2>前端静态文件未构建</h2>"
        "<p>当前以单进程模式访问后端，但没有找到 <code>frontend/dist/index.html</code>。</p>"
        "<p>Windows 推荐运行：<code>powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\start_single_process.ps1</code></p>"
        "<p>手动构建：<code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>，然后重启后端。</p>"
        "<p>如果使用前后端分离开发模式，请访问 Vite 地址：<code>http://localhost:3000</code>。</p>"
        "</div></body></html>",
        status_code=503,
    )


for path in SPA_ROUTES:
    def _make_handler():
        async def _handler():
            return _serve_spa()
        return _handler
    pages_router.get(path)(_make_handler())


@pages_router.get("/servers/{server_name}")
async def spa_server_detail(server_name: str):
    return _serve_spa()

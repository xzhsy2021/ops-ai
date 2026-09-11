"""页面路由 - React SPA 分发 - V2 only"""
import os
import re
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
    "/inspection",
    "/database",
    "/tools",
]


def _diagnostic_page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'><title>OPS v2</title>"
        "<style>body{font-family:Arial,'Microsoft YaHei',sans-serif;margin:48px;line-height:1.7;color:#1f2937}"
        "code{background:#f3f4f6;padding:2px 6px;border-radius:4px}.box{max-width:820px}"
        "ul{margin:8px 0 0 0}</style>"
        "</head><body><div class='box'><h1>OPS v2</h1>"
        "<h2>%s</h2>%s</div></body></html>" % (title, body),
        status_code=503,
    )


def _index_asset_refs() -> list:
    """index.html 中引用的 /assets/* 资源相对路径。"""
    try:
        with open(REACT_INDEX_HTML, encoding="utf-8", errors="ignore") as fh:
            html = fh.read()
    except OSError:
        return []
    return re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)


def _missing_index_assets() -> list:
    """返回 index.html 引用了、但磁盘上不存在的资源。

    非空意味着后端没有挂载 /assets（例如 dist 被判定过期后自动切换为
    API-only），此时直接返回 index.html 只会得到一张白屏的 SPA 外壳：
    HTML 能打开、JS/CSS 全部 404。这里显式给出可读原因，避免再次白屏。
    """
    assets_dir = os.path.join(REACT_DIST_DIR, "assets")
    missing = []
    for ref in _index_asset_refs():
        if not os.path.exists(os.path.join(assets_dir, os.path.basename(ref))):
            missing.append(ref)
    return missing


def _serve_spa():
    if os.path.exists(REACT_INDEX_HTML):
        missing = _missing_index_assets()
        if missing:
            items = "".join("<li><code>%s</code></li>" % ref for ref in missing[:8])
            return _diagnostic_page(
                "前端资源缺失，已阻止白屏",
                "<p><code>frontend/dist/index.html</code> 引用的下列资源在后端不可访问"
                "（后端可能已因 <code>dist</code> 过期或未构建而关闭静态挂载）：</p>"
                "<ul>%s</ul>"
                "<p>请在项目根目录重新构建前端后重启后端：</p>"
                "<p><code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code></p>"
                "<p>若需强制按现有 dist 提供静态文件（不做新鲜度判断），"
                "设置环境变量 <code>SERVE_FRONTEND=true</code> 后重启。</p>" % items,
            )
        return FileResponse(REACT_INDEX_HTML)
    return _diagnostic_page(
        "前端静态文件未构建",
        "<p>当前以单进程模式访问后端，但没有找到 <code>frontend/dist/index.html</code>。</p>"
        "<p>Windows 推荐运行：<code>powershell -NoProfile -ExecutionPolicy Bypass -File "
        ".\\scripts\\start_single_process.ps1</code></p>"
        "<p>手动构建：<code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>，然后重启后端。</p>"
        "<p>如果使用前后端分离开发模式，请访问 Vite 地址：<code>http://localhost:3000</code>。</p>",
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

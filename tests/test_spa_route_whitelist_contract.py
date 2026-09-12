"""SPA 深链白名单契约（2026-09-12 复盘第 10 轮）。

背景（真实缺陷，已在生产 OPS 上实测复现）：

  * 前端 `frontend/src/routes.ts` 的 `SPA_PAGE_ROUTES` 是页面路由的权威清单，
    其中包含 `/mcp/tools`、`/mcp/audit` 以及 4 个配置中心编辑页；
  * 后端 `app/pages.py` 的 `SPA_ROUTES` 是**另一份硬编码副本**，当时少了这 6 条，
    还多出 5 条前端根本没有的历史条目（/deployments、/services、/logs、/config、/groups）；
  * 后果：**已登录用户刷新/直达这些深链会拿到 404 {"detail":"Not Found"}**
    （`/mcp/audit` 实测 `auth=404`）。匿名访问是 307 跳 `/login`，再由浏览器
    跟随到登录页返回 200，所以"只测匿名"完全看不出问题——这也是它长期没被发现的原因。

本文件把"两份清单必须一致"固化为契约，避免再次漂移。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.pages import SPA_ROUTES, pages_router

ROUTES_TS = Path("frontend/src/routes.ts")


def _frontend_spa_routes() -> list[str]:
    """解析 routes.ts，把 SPA_PAGE_ROUTES 里的常量引用还原成真实路径。"""
    text = ROUTES_TS.read_text(encoding="utf-8")

    const_block = re.search(r"export const ROUTES = \{(.*?)\}\s*as const", text, re.S)
    assert const_block, "未能在 frontend/src/routes.ts 中定位 ROUTES 常量表"
    consts = dict(re.findall(r"(\w+):\s*'([^']+)'", const_block.group(1)))
    assert consts.get("mcpAudit") and consts.get("mcpTools"), f"ROUTES 解析异常: {consts}"

    list_block = re.search(r"export const SPA_PAGE_ROUTES[^=]*=\s*\[(.*?)\]", text, re.S)
    assert list_block, "未能在 frontend/src/routes.ts 中定位 SPA_PAGE_ROUTES"

    routes: list[str] = []
    for literal, const_ref in re.findall(r"'([^']+)'|(ROUTES\.\w+)", list_block.group(1)):
        if literal:
            routes.append(literal)
        else:
            routes.append(consts[const_ref.split(".", 1)[1]])
    assert routes, "SPA_PAGE_ROUTES 解析为空"
    return routes


def _to_backend_path(frontend_path: str) -> str:
    """React Router 的 :param 语法 → FastAPI 的 {param} 语法。"""
    return re.sub(r":(\w+)", r"{\1}", frontend_path)


def test_backend_spa_whitelist_covers_every_frontend_page_route():
    """核心回归：前端声明的每个页面路由，后端都必须能返回 SPA 外壳。

    修复前失败：/mcp/tools、/mcp/audit、/systems/create、
    /systems/:name/edit、/systems/:systemName/services/create、
    /systems/:systemName/services/{serviceName}/edit 共 6 条缺失。
    """
    frontend = {_to_backend_path(p) for p in _frontend_spa_routes()}
    backend = set(SPA_ROUTES)
    missing = sorted(frontend - backend)
    assert not missing, f"以下前端页面路由在后端 SPA_ROUTES 中缺失（已登录刷新会 404）：{missing}"


def test_backend_spa_whitelist_has_no_stale_entry():
    """反向一致性：后端不应保留前端已不存在的历史条目（会给出误导性的 200 SPA）。"""
    frontend = {_to_backend_path(p) for p in _frontend_spa_routes()}
    extra = sorted(set(SPA_ROUTES) - frontend)
    assert not extra, f"以下后端白名单条目在前端路由表里不存在（历史残留）：{extra}"


def test_backend_paths_use_fastapi_syntax_not_react_router_syntax():
    """别把 React 的 :param 直接抄进 FastAPI 路径模板。"""
    offenders = [p for p in SPA_ROUTES if ":" in p]
    assert not offenders, f"后端路径不得包含 React Router 的冒号参数语法：{offenders}"


def test_every_spa_route_is_actually_registered_on_the_router():
    """清单里写了但没注册（例如漏进注册循环）同样是缺陷。"""
    registered = {getattr(r, "path", None) for r in pages_router.routes}
    not_registered = sorted(set(SPA_ROUTES) - registered)
    assert not not_registered, f"这些 SPA 路由未注册到 pages_router：{not_registered}"

    declared = [p for p in _frontend_spa_routes() if ":" not in p]
    not_registered_frontend = sorted(set(declared) - registered)
    assert not not_registered_frontend, f"前端字面量路由未注册：{not_registered_frontend}"


@pytest.mark.parametrize(
    "path",
    ["/mcp/audit", "/mcp/tools", "/systems/create", "/systems/demo/edit", "/systems/demo/services/create"],
)
def test_affected_deep_links_serve_spa_shell(path):
    """行为层：这些深链必须真的返回 SPA HTML，而不是 404 JSON。"""
    dist_index = Path("frontend/dist/index.html")
    if not dist_index.exists():
        pytest.skip("frontend/dist 未构建，跳过 SPA 分发行为验证")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(pages_router)
    client = TestClient(app)
    resp = client.get(path)
    assert resp.status_code == 200, (path, resp.status_code, resp.text[:120])
    assert "html" in resp.headers.get("content-type", "").lower(), (path, resp.headers.get("content-type"))

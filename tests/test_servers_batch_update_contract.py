"""服务器批量编辑与认证方式校验回归（2026-09-12 复盘第 8 轮）。

本轮修出的四类问题（都在"服务器连接配置"这一域，且都是"两条路径不一致/静默失败"）：

 1. 批量编辑**静默空操作**：前端批量弹窗有"服务器状态"（online/disabled/offline）并会提交
    ``updates.status``，而后端批量处理器完全不识别 ``status``，接口仍返回成功、
    前端提示"批量编辑完成 N 台成功"——点了没反应。
 2. 批量改 ``auth_type`` **不校验新模式所需凭据**（单机创建会校验，单机更新会沿用既有凭据），
    可一次把 ≤50 台改成永远连不通的配置；``auth_type`` 本身也没有枚举校验
    （生产已存在历史非法值 ``auth_type="key"``）。
 3. 批量 ``port`` 不做校验：非数字时 ``int()`` 抛 ValueError → 整批 500，
    且此前已保存的服务器无法回滚；越界端口（0/70000）照样写入。
 4. 分组同步引用未导入的 ``ServerGroup`` → ``NameError`` 被 ``except Exception`` 吞成
    warning，**单机更新的 ``server_groups.server_names`` 同步从未生效**，批量路径则完全没有该逻辑。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ADMIN = {"id": 1, "username": "admin-user", "role": "admin", "is_admin": True}


def _server(name: str, **overrides):
    base = {
        "name": name,
        "host": f"{name}.example.com",
        "port": 22,
        "username": "root",
        "auth_type": "key_file",
        "key": "/root/.ssh/id_rsa",
        "password": None,
        "key_content": None,
        "description": "",
        "tags": [],
        "group": "old-group",
        "status": "online",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def batch_env(tmp_path, monkeypatch):
    """挂载 servers_v2_router；inventory/save_server 用内存假实现，DB 用临时库。"""
    from app.api import servers as servers_api
    from app.db import get_db
    from app.db.base import Base
    from app.db.models import ServerGroup

    engine = create_engine(f"sqlite:///{tmp_path / 'servers.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    db.add(ServerGroup(id="g1", name="old-group", description="", server_names=["srv-a", "srv-b"]))
    db.add(ServerGroup(id="g2", name="new-group", description="", server_names=[]))
    db.commit()

    store = {"srv-a": _server("srv-a"), "srv-b": _server("srv-b")}
    saved: list[dict] = []

    monkeypatch.setattr(servers_api, "require_admin", lambda request, db: ADMIN)
    monkeypatch.setattr(servers_api.inventory, "get_server", lambda name: dict(store[name]) if name in store else None)

    def _fake_save(server):
        saved.append(dict(server))
        store[server["name"]] = dict(server)
        return True

    monkeypatch.setattr(servers_api, "save_server", _fake_save)

    app = FastAPI()
    app.include_router(servers_api.servers_v2_router)
    app.dependency_overrides[get_db] = lambda: db

    @app.middleware("http")
    async def _inject_user(request, call_next):
        # 真实环境由认证中间件写入 request.state.username；此处 require_admin 被替换，
        # 需要补上后续日志/审计所依赖的字段。
        request.state.username = ADMIN["username"]
        return await call_next(request)

    def _groups():
        return {g.name: list(g.server_names or []) for g in db.query(ServerGroup).all()}

    env = SimpleNamespace(client=TestClient(app), db=db, store=store, saved=saved, groups=_groups)
    try:
        yield env
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. 批量编辑：表单字段必须真正生效（不再静默空操作）
# ---------------------------------------------------------------------------

def test_batch_status_update_is_applied(batch_env):
    """前端批量弹窗的"服务器状态"必须真的改（此前接口返回成功但什么都没改）。"""
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a", "srv-b"], "updates": {"status": "disabled"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]

    assert body["updated"] == ["srv-a", "srv-b"], body
    assert batch_env.store["srv-a"]["status"] == "disabled"
    assert batch_env.store["srv-a"]["enabled"] is False
    assert batch_env.store["srv-b"]["status"] == "disabled"


def test_batch_field_set_matches_frontend_form(batch_env):
    """前端批量弹窗提交的字段集必须全部被后端支持（防止再次出现静默忽略）。"""
    from app.api.servers import BATCH_UPDATABLE_FIELDS

    # ServerListPage.tsx handleBatchSubmit 会写入 updates 的键
    frontend_keys = {"description", "tags", "jump_host", "username", "auth_type", "port", "group", "status"}
    missing = sorted(frontend_keys - set(BATCH_UPDATABLE_FIELDS))
    assert missing == [], f"后端批量不支持前端提交的字段：{missing}"


def test_batch_rejects_unknown_fields(batch_env):
    """未知字段必须 400：此前被静默忽略却返回成功。"""
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a"], "updates": {"password": "x"}})
    assert resp.status_code == 400, resp.text
    assert "Unsupported batch update fields" in resp.json()["detail"]
    assert batch_env.saved == [], "被拒的请求不得写入任何服务器"


def test_batch_requires_updates(batch_env):
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a"], "updates": {}})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 2. auth_type：枚举校验 + 切换时必须具备目标模式凭据
# ---------------------------------------------------------------------------

def test_batch_auth_type_change_without_credential_is_rejected_per_server(batch_env):
    """key_file → password 但没有密码：必须逐台报错，不能写入连不通的配置。"""
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a", "srv-b"], "updates": {"auth_type": "password"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]

    assert body["updated"] == []
    assert {f["name"] for f in body["failed"]} == {"srv-a", "srv-b"}
    assert all("密码" in f["error"] for f in body["failed"]), body["failed"]
    assert batch_env.saved == [], "校验失败不得写入"
    assert batch_env.store["srv-a"]["auth_type"] == "key_file"


def test_batch_auth_type_change_allowed_when_credential_exists(batch_env):
    """目标模式凭据已存在（沿用旧值）时不得误拦。"""
    batch_env.store["srv-a"] = _server("srv-a", key_content="-----BEGIN OPENSSH PRIVATE KEY-----k")
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a"], "updates": {"auth_type": "key_content"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["updated"] == ["srv-a"]
    assert batch_env.store["srv-a"]["auth_type"] == "key_content"


def test_batch_invalid_auth_type_rejected(batch_env):
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a"], "updates": {"auth_type": "key"}})
    assert resp.status_code == 400, resp.text
    assert "Unsupported auth_type" in resp.json()["detail"]
    assert batch_env.saved == []


def test_batch_invalid_status_rejected(batch_env):
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a"], "updates": {"status": "paused"}})
    assert resp.status_code == 400, resp.text
    assert batch_env.saved == []


def test_create_rejects_invalid_auth_type(batch_env):
    """创建路径同样不得接受非法 auth_type（生产已存在历史非法值，不能再新增）。"""
    resp = batch_env.client.post("/api/v2/servers", json={
        "name": "new-srv", "host": "1.2.3.4", "auth_type": "key", "key": "/k",
    })
    assert resp.status_code == 400, resp.text
    assert "Unsupported auth_type" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 3. port 校验（批量 500 → 明确 400，且不产生部分写入）
# ---------------------------------------------------------------------------

def test_batch_non_numeric_port_rejected_without_partial_write(batch_env):
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a", "srv-b"], "updates": {"description": "d", "port": "abc"}})
    assert resp.status_code == 400, resp.text
    assert "Invalid port" in resp.json()["detail"]
    assert batch_env.saved == [], "参数非法时不得先改一部分再失败"


@pytest.mark.parametrize("bad_port", [0, -1, 70000, 65536])
def test_batch_out_of_range_port_rejected(batch_env, bad_port):
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a"], "updates": {"port": bad_port}})
    assert resp.status_code == 400, resp.text
    assert "Invalid port" in resp.json()["detail"]


def test_batch_valid_port_applied(batch_env):
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a"], "updates": {"port": 2222}})
    assert resp.status_code == 200, resp.text
    assert batch_env.store["srv-a"]["port"] == 2222


def test_single_update_rejects_invalid_port(batch_env):
    resp = batch_env.client.put("/api/v2/servers/srv-a", json={"port": 70000})
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# 4. 分组同步（批量此前完全没有；单机此前 NameError 被吞）
# ---------------------------------------------------------------------------

def test_batch_group_change_syncs_server_groups(batch_env):
    assert batch_env.groups()["old-group"] == ["srv-a", "srv-b"]

    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a"], "updates": {"group": "new-group"}})
    assert resp.status_code == 200, resp.text

    groups = batch_env.groups()
    assert "srv-a" not in groups["old-group"], f"旧组应剔除 srv-a：{groups}"
    assert "srv-a" in groups["new-group"], f"新组应加入 srv-a：{groups}"
    assert groups["old-group"] == ["srv-b"], groups
    assert batch_env.store["srv-a"]["group"] == "new-group"


def test_batch_group_clear_removes_from_all_groups(batch_env):
    resp = batch_env.client.put("/api/v2/servers/batch", json={"names": ["srv-a"], "updates": {"group": ""}})
    assert resp.status_code == 200, resp.text
    groups = batch_env.groups()
    assert all("srv-a" not in members for members in groups.values()), groups


def test_single_update_group_sync_actually_works(batch_env):
    """单机更新此前引用未导入的 ServerGroup → NameError 被吞，同步从未生效。"""
    resp = batch_env.client.put("/api/v2/servers/srv-b", json={"group": "new-group"})
    assert resp.status_code == 200, resp.text

    groups = batch_env.groups()
    assert "srv-b" not in groups["old-group"], f"旧组应剔除 srv-b：{groups}"
    assert "srv-b" in groups["new-group"], f"新组应加入 srv-b：{groups}"


# ---------------------------------------------------------------------------
# 5. 单机更新：认证方式校验与历史脏数据兼容
# ---------------------------------------------------------------------------

def test_single_update_auth_type_change_requires_credential(batch_env):
    """key_file → password 但库里没有密码：必须 400，不能静默写入连不通配置。"""
    resp = batch_env.client.put("/api/v2/servers/srv-a", json={"auth_type": "password"})
    assert resp.status_code == 400, resp.text
    assert "密码" in resp.json()["detail"]
    assert batch_env.saved == []
    assert batch_env.store["srv-a"]["auth_type"] == "key_file"


def test_single_update_auth_type_change_allowed_with_cached_password(batch_env):
    batch_env.store["srv-a"] = _server("srv-a", password="secret")
    resp = batch_env.client.put("/api/v2/servers/srv-a", json={"auth_type": "password"})
    assert resp.status_code == 200, resp.text
    assert batch_env.store["srv-a"]["auth_type"] == "password"


def test_single_update_keeps_legacy_invalid_auth_type_editable(batch_env):
    """历史非法值（生产中的 auth_type="key"）：只改描述不得被拒，改用合法值应通过。"""
    batch_env.store["legacy"] = _server("legacy", auth_type="key")

    resp = batch_env.client.put("/api/v2/servers/legacy", json={"description": "只改描述", "auth_type": "key"})
    assert resp.status_code == 200, f"存量脏数据不得阻断普通编辑：{resp.text}"
    assert batch_env.store["legacy"]["description"] == "只改描述"

    resp = batch_env.client.put("/api/v2/servers/legacy", json={"auth_type": "key_file"})
    assert resp.status_code == 200, resp.text
    assert batch_env.store["legacy"]["auth_type"] == "key_file"

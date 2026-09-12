"""凭据写入安全契约回归（2026-09-12 复盘第 3 轮）。

缺陷一：编辑数据库连接会静默清空已保存的 SSH 凭据
--------------------------------------------------
``app/api/maintenance.py::update_connection`` 传的是 ``body.model_dump()``（**全量**字典，
未用 ``exclude_unset=True``），而 ``CleanupService.update_connection`` 对密钥类字段是
"字段在 payload 里就覆盖"：

    if "ssh_password" in data:
        conn.ssh_password_encrypted = encrypt_secret(...) if data.get("ssh_password") else None

于是 **每次保存连接**（哪怕只改描述）都会发生：

* ``ssh_password`` / ``ssh_key_passphrase`` / ``ssh_target_password`` /
  ``ssh_target_key_passphrase`` —— 前端表单把这些字段初始化成 ``''`` 并随 ``...connForm``
  一起提交（GET 接口从不返回它们的值，无法回填）→ 被判定为"要清空" → 置 None；
* ``ssh_key_content`` —— 前端明确 ``delete payload.ssh_key_content``（有意不动已上传的私钥），
  但 ``model_dump()`` 又把它补回 ``None`` → 已上传私钥被删除。

影响：SSH 隧道/跳板机的 DB 巡检与 SQL 查询从此连不上（``service.py`` 的
``_build_ssh_tunnel`` 与 ``sql_query.py`` 都依赖 ``ssh_key_content_encrypted``），
而列表只显示 ``ssh_key_has_content=false``，操作员难以察觉原因。
注意 ``password`` 字段**本来就有**"空值不覆盖"保护（``if "password" in data and data.get("password")``），
说明其余密钥字段属遗漏。

缺陷二：服务器"创建"接口会把脱敏占位符当真实密钥入库
--------------------------------------------------
``redact_server_secrets`` 把 ``key_content``/``password`` 脱敏成 ``"********"`` 返回给浏览器；
**更新**接口专门识别了这个占位符（``incoming not in (None, "", "********")`` 才覆盖），
但**创建**接口只判真值 —— 客户端（克隆/复制服务器、脚本或 Agent 回填脱敏值）一旦提交
``key_content="********"``，库里就存下这 8 个星号，``auth_type=key_content`` 的服务器
从此无法用私钥登录，且原私钥内容无从恢复。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SECRETS = {
    "password_encrypted": "db-password",
    "ssh_password_encrypted": "ssh-password",
    "ssh_key_passphrase_encrypted": "key-passphrase",
    "ssh_key_content_encrypted": "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----",
    "ssh_target_password_encrypted": "bastion-target-password",
    "ssh_target_key_passphrase_encrypted": "target-key-passphrase",
}


@pytest.fixture()
def db(tmp_path):
    from app.db.base import Base
    from app.core.secret_store import encrypt_secret

    engine = create_engine(f"sqlite:///{tmp_path / 'conn.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    from app.db.models import DatabaseConnection

    session.add(DatabaseConnection(
        id="conn-1", name="prod-db", environment="prod", db_type="mysql",
        host="10.0.0.9", port=3306, username="cleaner", database_name="app",
        use_ssh_tunnel=True, ssh_mode="manual", ssh_host="10.0.0.1", ssh_username="ops",
        allowed_tables=["t1"], max_affected_rows_default=250, require_dml_reason=False,
        **{field: encrypt_secret(value) for field, value in SECRETS.items()},
    ))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _ui_payload(**overrides) -> dict:
    """前端 ConnectionsTab 提交的真实形状：密钥字段为 ''，且不含 ssh_key_content。"""
    payload = {
        "name": "prod-db", "environment": "prod", "db_type": "mysql",
        "host": "10.0.0.9", "port": 3306, "username": "cleaner", "database_name": "app",
        "description": "改个描述", "use_ssh_tunnel": True, "ssh_mode": "manual",
        "ssh_host": "10.0.0.1", "ssh_port": 22, "ssh_username": "ops",
        "ssh_password": "", "ssh_key_passphrase": "", "ssh_target_password": "",
        "ssh_target_key_passphrase": "", "ssh_key_path": None,
        "allow_dml": False, "allowed_dml_types": [], "allowed_tables": ["t1"],
        "blocked_tables": [], "max_affected_rows_default": 250, "require_dml_reason": False,
    }
    payload.update(overrides)
    return payload


def _stored(db) -> dict:
    from app.db.models import DatabaseConnection

    row = db.query(DatabaseConnection).filter_by(id="conn-1").one()
    return {field: getattr(row, field) for field in SECRETS}


def test_update_connection_service_keeps_secrets_for_ui_shaped_payload(db):
    """服务层：空值/缺省的密钥字段不得覆盖已保存的凭据。"""
    from app.core.secret_store import decrypt_secret
    from app.maintenance.service import CleanupService

    CleanupService(db).update_connection("conn-1", _ui_payload())

    stored = _stored(db)
    for field, expected in SECRETS.items():
        assert stored[field], f"{field} 被清空（应为保留）"
        assert decrypt_secret(stored[field]) == expected, f"{field} 内容被改写"


def test_update_connection_service_still_updates_explicit_secrets(db):
    """显式提供的新口令必须写入（修复不能把"设置新口令"一并挡掉）。"""
    from app.core.secret_store import decrypt_secret
    from app.maintenance.service import CleanupService

    CleanupService(db).update_connection(
        "conn-1", _ui_payload(ssh_password="new-ssh-password", ssh_key_content="NEW-KEY-CONTENT")
    )

    stored = _stored(db)
    assert decrypt_secret(stored["ssh_password_encrypted"]) == "new-ssh-password"
    assert decrypt_secret(stored["ssh_key_content_encrypted"]) == "NEW-KEY-CONTENT"
    # 未提供的其它密钥保持原值
    assert decrypt_secret(stored["password_encrypted"]) == SECRETS["password_encrypted"]


def test_update_connection_endpoint_preserves_secrets(monkeypatch, db):
    """接口层：前端提交的形状（'' + 无 ssh_key_content）经 PUT 后凭据仍在，且审计留痕为"无变化"。"""
    from app.api import maintenance as maintenance_api
    from app.core.secret_store import decrypt_secret

    audits: list = []
    monkeypatch.setattr(maintenance_api, "require_admin", lambda request, db: {"username": "admin"})
    monkeypatch.setattr(maintenance_api, "audit", lambda *args, **kwargs: audits.append(args))
    body = maintenance_api.ConnectionCreate(**_ui_payload())

    maintenance_api.update_connection("conn-1", body, _FakeRequest({}), db)

    stored = _stored(db)
    for field, expected in SECRETS.items():
        assert stored[field], f"{field} 经 PUT 后被清空"
        assert decrypt_secret(stored[field]) == expected

    assert audits, "连接更新必须写审计"
    action, target_type, target_name, details = audits[-1][:4]
    assert action == "maintenance.connection.update"
    assert target_type == "database_connection" and target_name == "prod-db"
    assert "secrets_changed=none" in details
    for secret in SECRETS.values():
        assert secret not in details, "审计详情不得包含密钥明文"


def test_update_connection_explicit_null_clears_and_is_detected_in_audit(monkeypatch, db):
    """显式 JSON null = 主动清空，且审计能看出 ssh_key_content 由有变无。"""
    from app.api import maintenance as maintenance_api

    audits: list = []
    monkeypatch.setattr(maintenance_api, "require_admin", lambda request, db: {"username": "admin"})
    monkeypatch.setattr(maintenance_api, "audit", lambda *args, **kwargs: audits.append(args))
    payload = _ui_payload()
    payload["ssh_key_content"] = None  # 显式清空（不是前端表单形状，属有意操作）

    maintenance_api.update_connection("conn-1", maintenance_api.ConnectionCreate(**payload), _FakeRequest({}), db)

    assert _stored(db)["ssh_key_content_encrypted"] in (None, "")
    assert "ssh_key_content_encrypted:1->0" in audits[-1][3]


def test_connection_detail_never_returns_secrets(monkeypatch, db):
    """GET 详情只暴露 has_* 布尔位，不返回任何密钥明文（前端因此无法回填密钥）。"""
    from app.api import maintenance as maintenance_api

    monkeypatch.setattr(maintenance_api, "require_auth", lambda request, db: {"username": "admin"})
    response = maintenance_api.get_connection("conn-1", object(), db)
    data = response["data"] if isinstance(response, dict) and "data" in response else response

    for field in ("password", "ssh_password", "ssh_key_passphrase", "ssh_key_content",
                  "ssh_target_password", "ssh_target_key_passphrase"):
        assert field not in data, f"详情接口泄漏了 {field}"
    assert data["ssh_key_has_content"] is True
    assert data["use_ssh_tunnel"] is True


# ─── 缺陷二：服务器写接口不得把脱敏占位符当真实密钥 ──────────────────────────────


class _FakeRequest:
    """只实现处理函数用到的 Request 接口（json()/state.username/cookies）。"""

    def __init__(self, payload: dict):
        self._payload = payload
        self.state = type("State", (), {"username": "admin"})()
        self.cookies: dict = {}

    async def json(self):
        return self._payload


def _patch_server_writes(monkeypatch, existing=None):
    """打桩服务器写接口的外部依赖，返回捕获 save_server 入参的容器。"""
    import asyncio  # noqa: F401  (调用方使用)

    from app.api import servers as servers_api

    saved: dict = {}
    monkeypatch.setattr(servers_api, "require_admin", lambda request, db: {"username": "admin"})
    monkeypatch.setattr(servers_api.inventory, "get_server", lambda name: dict(existing) if existing else None)
    monkeypatch.setattr(servers_api, "save_server", lambda server: saved.update(server) or True)
    monkeypatch.setattr(servers_api, "build_audit_context",
                        lambda server: {"host": server.get("host"), "port": server.get("port"), "auth_mode": server.get("auth_type")})
    monkeypatch.setattr(servers_api, "get_hop_summary", lambda server: {"hops": []})
    monkeypatch.setattr(servers_api, "audit", lambda *a, **k: None)
    return servers_api, saved


def test_blank_redacted_secrets_covers_top_level_and_inline_jump_host():
    """脱敏占位符（含内联跳板机配置）一律置 None，真实值原样保留。"""
    from app.config.servers import blank_redacted_secrets, is_blank_or_redacted_secret, is_redacted_secret

    out = blank_redacted_secrets({
        "name": "s1",
        "password": "********",
        "key_content": "REAL-KEY",
        "jump_host": {"name": "jh", "key_content": "********", "password": "real-pass", "key": "tiaoban"},
    })

    assert out["password"] is None
    assert out["key_content"] == "REAL-KEY"
    assert out["jump_host"]["key_content"] is None
    assert out["jump_host"]["password"] == "real-pass"
    assert out["jump_host"]["key"] == "tiaoban"

    assert is_redacted_secret("********") and is_redacted_secret(" ******** ")
    assert is_redacted_secret("***"), "MCP 工具层用 3 个星号脱敏，也要识别"
    assert not is_redacted_secret("real") and not is_redacted_secret(None)
    assert is_blank_or_redacted_secret(None) and is_blank_or_redacted_secret("") and is_blank_or_redacted_secret("********")
    assert not is_blank_or_redacted_secret("real")


def test_update_connection_ignores_mcp_style_mask(db):
    """更新时提交 "***"（MCP 详情接口的脱敏值）同样视为"不改动"。"""
    from app.core.secret_store import decrypt_secret
    from app.maintenance.service import CleanupService

    CleanupService(db).update_connection("conn-1", {
        "name": "prod-db", "password": "***", "ssh_password": "********",
        "ssh_key_passphrase": "***", "ssh_key_content": "***",
        "ssh_target_password": "***", "ssh_target_key_passphrase": "***",
    })

    stored = _stored(db)
    for field, expected in SECRETS.items():
        assert stored[field], f"{field} 被掩码覆盖"
        assert decrypt_secret(stored[field]) == expected


def test_create_connection_does_not_store_mask(db):
    """新建连接时提交掩码 → 视为未提供（不落库为占位符）。"""
    from app.maintenance.service import CleanupService

    conn = CleanupService(db).create_connection({
        "name": "another-db", "environment": "dev", "db_type": "mysql",
        "host": "10.0.0.9", "username": "cleaner",
        "password": "********", "ssh_password": "***", "ssh_key_content": "***",
    }, created_by="tester")

    assert conn.password_encrypted in (None, "")
    assert conn.ssh_password_encrypted in (None, "")
    assert conn.ssh_key_content_encrypted in (None, "")


def test_server_create_rejects_redacted_key_content(monkeypatch):
    """创建时提交 key_content='********' → 400 明确报错，绝不把占位符入库。"""
    import asyncio

    from fastapi import HTTPException

    servers_api, saved = _patch_server_writes(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(servers_api.create_server_v2(
            _FakeRequest({"name": "s1", "host": "10.0.0.1", "auth_type": "key_content", "key_content": "********"}),
            object(),
        ))

    assert excinfo.value.status_code == 400
    assert "密钥内容" in str(excinfo.value.detail)
    assert saved == {}, "占位符不应落库"


def test_server_create_rejects_redacted_password(monkeypatch):
    """同上：password='********' 视为"未提供密码"，报错而非存下星号。"""
    import asyncio

    from fastapi import HTTPException

    servers_api, saved = _patch_server_writes(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(servers_api.create_server_v2(
            _FakeRequest({"name": "s2", "host": "10.0.0.2", "auth_type": "password", "password": "********"}),
            object(),
        ))

    assert excinfo.value.status_code == 400
    assert saved == {}


def test_server_create_keeps_real_secret(monkeypatch):
    """真实密钥不受影响（修复不能把正常创建一并挡掉）。"""
    import asyncio

    servers_api, saved = _patch_server_writes(monkeypatch)

    asyncio.run(servers_api.create_server_v2(
        _FakeRequest({"name": "s3", "host": "10.0.0.3", "auth_type": "key_content", "key_content": "REAL-KEY"}),
        object(),
    ))

    assert saved["key_content"] == "REAL-KEY"
    assert saved["name"] == "s3"


def test_server_update_treats_mask_as_unchanged(monkeypatch):
    """更新时提交脱敏占位符 = 保持原密钥（前端表单会回填掩码后原样提交）。"""
    import asyncio

    servers_api, saved = _patch_server_writes(monkeypatch, existing={
        "name": "s1", "host": "10.0.0.1", "port": 22, "username": "root",
        "auth_type": "key_content", "key_content": "REAL-KEY", "password": None, "key": None,
    })

    asyncio.run(servers_api.update_server_v2(
        _FakeRequest({"key_content": "********", "description": "只改描述"}),
        "s1",
        object(),
    ))

    assert saved["key_content"] == "REAL-KEY"
    assert saved["description"] == "只改描述"
    assert saved["host"] == "10.0.0.1"


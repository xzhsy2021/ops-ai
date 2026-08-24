"""Matrix 部署包对接 MCP 工具测试。

验证 qclaw/Agent 能发现并调用 ops.matrix.* 工具：
- ops.matrix.scan_media_events   只读扫描房间媒体事件
- ops.matrix.pull_attachment    拉取附件到文件中心
- ops.matrix.deploy_from_matrix 完整发布链路
- 房间绑定（bound_room_ids）拦截
"""
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.migrations.runner import run_schema_migrations
from app.services.matrix_client import MatrixMediaEvent
from app.services.tool_context import ToolContext
from app.services.tool_registry import ensure_builtin_registered, registry


@pytest.fixture(scope="module")
def db():
    # StaticPool：所有线程共享同一条内存连接（deploy 核心会把
    # save_package_fileobj 卸载到线程池；默认 SingletonThreadPool 下
    # 工作线程会拿到一条全新的空 ：memory: 连接）。
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _ctx(**overrides) -> ToolContext:
    base = dict(
        username="qclaw-bot",
        role="operator",
        is_admin=False,
        can_deploy=True,
        allow_write=True,
        allow_prod=False,
        auth_type="tool_token",
        token_owner="qclaw-bot",
        scopes=["ops:read", "package:write", "deploy:execute"],
    )
    base.update(overrides)
    return ToolContext(**base)


def _ctx_bound_room(*room_ids: str) -> ToolContext:
    """构造绑定房间的 ctx（bound_room_ids 由 channel_bindings 派生）。"""
    return ToolContext(
        username="qclaw-bot",
        role="operator",
        is_admin=False,
        can_deploy=True,
        allow_write=True,
        allow_prod=False,
        auth_type="tool_token",
        token_owner="qclaw-bot",
        scopes=["ops:read", "package:write", "deploy:execute"],
        channel_bindings=[
            {"channel": "matrix", "channel_account_id": "default", "conversation_id": rid}
            for rid in room_ids
        ],
    )


def _media_event(event_id="$abc", filename="crypto-frontend.tar.gz", sender="@alice:example.org"):
    return MatrixMediaEvent(
        event_id=event_id,
        sender=sender,
        origin_server_ts=int(time.time() * 1000) - 60_000,
        msgtype="m.file",
        filename=filename,
        mxc_url="mxc://hs.example/abc",
        encrypted=False,
    )


def _patch_matrix_client(monkeypatch, *, event=None, events=None, content=b"PKG", filename="crypto-frontend.tar.gz"):
    """Monkeypatch matrix_tools.MatrixClient with a fake configured client."""
    import app.services.tool_adapters.matrix_tools as mt

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def list_media_events(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return events if events is not None else ([event] if event else [])

        def find_latest_media_event(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return event

        def download_media(self, mxc_url):
            return content, filename

        def debug_recent_events(self, room_id, sender="", minutes=15, limit=50):
            return {
                "total_events": 1,
                "room_message_count": 1,
                "in_window": 1,
                "out_of_window": 0,
                "window_minutes": minutes,
                "msgtype_distribution": {"m.file": 1} if event else {"m.text": 1},
                "sender_distribution": {sender or "@test:example.org": 1},
                "text_with_filename_hint": [],
                "hint": "diagnostic stub",
            }

    monkeypatch.setattr(mt, "_matrix_client", lambda *a, **k: FakeClient())
    return mt


def _patch_save_package(monkeypatch, mt, meta=None):
    meta = meta or {"package_name": "crypto-frontend.tar.gz", "sha256": "a" * 64, "size_bytes": 10}
    calls = {}

    def fake_save(db, filename, fileobj, system="", service="", uploaded_by="", overwrite=False,
                  source_context=None, source_message_key=None, allow_any_extension=False):
        calls["filename"] = filename
        calls["source_message_key"] = source_message_key
        calls["allow_any_extension"] = allow_any_extension
        return meta

    monkeypatch.setattr(mt, "save_package_fileobj", fake_save)
    return calls


# ── 注册与发现 ──

def test_matrix_tools_registered(monkeypatch, db):
    ensure_builtin_registered()
    names = {name for name in registry._tools if name.startswith("ops.matrix.")}
    assert {"ops.matrix.scan_media_events", "ops.matrix.pull_attachment", "ops.matrix.deploy_from_matrix"} <= names


def test_matrix_tools_visible_to_ai_profile(monkeypatch, db):
    """ai_full profile 下 Agent 应能发现 Matrix 工具。"""
    ensure_builtin_registered()
    ctx = _ctx()
    listed = registry.list_tools(db, ctx, profile="ai_full", include_schema=True, output_format="mcp")
    names = {t.get("name") for t in listed.get("tools", [])}
    assert "ops.matrix.scan_media_events" in names
    assert "ops.matrix.pull_attachment" in names
    assert "ops.matrix.deploy_from_matrix" in names


# ── scan_media_events ──

def test_scan_media_events(monkeypatch, db):
    mt = _patch_matrix_client(monkeypatch, event=_media_event())
    from app.services.tool_adapters.matrix_tools import scan_media_events

    result = scan_media_events({"room_id": "!room:example.org", "sender": "@alice:example.org"}, _ctx(), db)
    assert result["room_id"] == "!room:example.org"
    assert len(result["events"]) == 1
    assert result["events"][0]["event_id"] == "$abc"


def test_scan_media_events_room_binding_blocks(monkeypatch, db):
    mt = _patch_matrix_client(monkeypatch, event=_media_event())
    from app.services.tool_adapters.matrix_tools import scan_media_events

    ctx = _ctx_bound_room("!other:example.org")
    with pytest.raises(HTTPException) as exc:
        scan_media_events({"room_id": "!room:example.org"}, ctx, db)
    assert exc.value.status_code == 403


def test_scan_media_events_empty_includes_debug(monkeypatch, db):
    """scan 工具在 events 为空时返回 debug 信息便于 Agent 诊断。"""
    mt = _patch_matrix_client(monkeypatch, events=[])  # 显式传空 list
    from app.services.tool_adapters.matrix_tools import scan_media_events

    result = scan_media_events(
        {"room_id": "!room:example.org", "sender": "@alice:example.org"}, _ctx(), db
    )
    assert result["events"] == []
    assert "debug" in result
    assert result["debug"]["msgtype_distribution"] == {"m.text": 1}
    assert "窗口内 0 条媒体" in result["summary"]


# ── pull_attachment ──

def test_pull_attachment_happy_path(monkeypatch, db):
    mt = _patch_matrix_client(monkeypatch, event=_media_event())
    calls = _patch_save_package(monkeypatch, mt)
    from app.services.tool_adapters.matrix_tools import pull_attachment

    result = pull_attachment(
        {"room_id": "!room:example.org", "sender": "@alice:example.org", "service": "api"},
        _ctx(),
        db,
    )
    assert result["package_name"] == "crypto-frontend.tar.gz"
    assert result["sha256"] == "a" * 64
    assert result["event_id"] == "$abc"
    assert calls["filename"] == "crypto-frontend.tar.gz"
    assert calls["source_message_key"] == "matrix:default:!room:example.org:$abc"
    assert result["matrix"]["room_id"] == "!room:example.org"


def test_pull_attachment_allows_any_file_format(monkeypatch, db):
    """普通文件（.log/.txt/.pdf 等）拉取入库不受部署包扩展名白名单限制。"""
    mt = _patch_matrix_client(
        monkeypatch,
        event=_media_event(event_id="$plain", filename="server-error.log"),
        content=b"2026-08-24 ERROR oom killed",
        filename="server-error.log",
    )
    meta = {"package_name": "server-error.log", "sha256": "b" * 64, "size_bytes": 30}
    calls = _patch_save_package(monkeypatch, mt, meta=meta)
    from app.services.tool_adapters.matrix_tools import pull_attachment

    result = pull_attachment(
        {"room_id": "!room:example.org", "sender": "@alice:example.org"},
        _ctx(),
        db,
    )
    assert result["package_name"] == "server-error.log"
    assert calls["filename"] == "server-error.log"
    # 拉取路径显式放行任意扩展名（大小上限仍生效）
    assert calls["allow_any_extension"] is True


def test_pull_attachment_extension_gate_env_kill_switch(monkeypatch, db):
    """MATRIX_PULL_ALLOW_ANY_EXTENSION=0 时恢复白名单校验（save 收到 False）。"""
    mt = _patch_matrix_client(monkeypatch, event=_media_event())
    calls = _patch_save_package(monkeypatch, mt)
    monkeypatch.setenv("MATRIX_PULL_ALLOW_ANY_EXTENSION", "0")
    from app.services.tool_adapters.matrix_tools import pull_attachment

    pull_attachment({"room_id": "!room:example.org", "sender": "@alice:example.org"}, _ctx(), db)
    assert calls["allow_any_extension"] is False


def test_save_package_fileobj_allow_any_extension(db, monkeypatch, tmp_path):
    """文件中心层：allow_any_extension=True 放行普通格式；默认白名单仍拒绝。"""
    import os
    from io import BytesIO

    import pytest
    from fastapi import HTTPException

    from app.db.models import DeployPackage
    from app.services import package_retention

    monkeypatch.setattr(package_retention, "get_runtime_path", lambda env, default: str(tmp_path / "uploads"))

    # 默认白名单：.txt 被拒
    with pytest.raises(HTTPException) as exc:
        package_retention.save_package_fileobj(db, filename="notes.txt", fileobj=BytesIO(b"x"))
    assert exc.value.status_code == 400

    # 放行后：任意扩展名可入库，元数据完整
    meta = package_retention.save_package_fileobj(
        db,
        filename="notes.txt",
        fileobj=BytesIO(b"hello plain text"),
        uploaded_by="matrix",
        allow_any_extension=True,
    )
    assert meta["package_name"] == "notes.txt"
    assert meta["size_bytes"] == len(b"hello plain text")

    # 清理，避免污染同模块其他用例
    row = db.query(DeployPackage).filter(DeployPackage.package_name == "notes.txt").first()
    if row is not None:
        db.delete(row)
        db.commit()
    if os.path.isfile(meta.get("file_path") or ""):
        os.remove(meta["file_path"])


def test_pull_attachment_e2ee_blocked(monkeypatch, db):
    encrypted = MatrixMediaEvent(
        event_id="$e2ee",
        sender="@alice:example.org",
        origin_server_ts=int(time.time() * 1000) - 60_000,
        msgtype="m.file",
        filename="secret.tar.gz",
        mxc_url="",
        encrypted=True,
    )
    mt = _patch_matrix_client(monkeypatch, event=encrypted)
    _patch_save_package(monkeypatch, mt)
    from app.services.tool_adapters.matrix_tools import pull_attachment

    with pytest.raises(HTTPException) as exc:
        pull_attachment({"room_id": "!room:example.org", "sender": "@alice:example.org"}, _ctx(), db)
    assert exc.value.status_code == 400
    assert "E2EE" in str(exc.value.detail)


def test_pull_attachment_no_sender(monkeypatch, db):
    mt = _patch_matrix_client(monkeypatch, event=_media_event())
    from app.services.tool_adapters.matrix_tools import pull_attachment

    with pytest.raises(HTTPException) as exc:
        pull_attachment({"room_id": "!room:example.org"}, _ctx(), db)
    assert exc.value.status_code == 400


def test_pull_attachment_encrypted_event_via_async_decryptor(monkeypatch, db):
    """回归：加密事件（解密前无 mxc_url）对同步筛选不可见，pull 必须走
    find_latest_media_event_async 并传入 E2EE 解密器，否则误报"未找到媒体事件"。"""
    import time as _time

    mt = _patch_matrix_client(monkeypatch)  # 基础桩（同步路径不应被触达）
    encrypted = MatrixMediaEvent(
        event_id="$enc1",
        sender="@alice:example.org",
        origin_server_ts=int(_time.time() * 1000) - 60_000,
        msgtype="m.file",
        filename="secret.tar.gz",
        mxc_url="",  # 加密事件特征：无明文 mxc
        encrypted=True,
        file_dict={"url": "mxc://hs/enc"},
    )

    seen = {}

    class FakeAsyncClient:
        configured = True
        media_msgtypes = ["m.file", "m.image"]

        async def find_latest_media_event_async(self, room_id, *, sender="", minutes=None,
                                                filename_hint="", limit=50, event_decryptor=None):
            seen["decryptor"] = event_decryptor
            seen["room_id"] = room_id
            return encrypted

        def find_latest_media_event(self, room_id, **kw):
            raise AssertionError("加密事件场景下不得回退同步筛选路径")

    monkeypatch.setattr(mt, "_matrix_client", lambda args=None: FakeAsyncClient())

    async def fake_decryptor(raw_event):
        return None

    monkeypatch.setattr(mt, "_try_build_decryptor", lambda room_id: fake_decryptor)

    async def fake_pull_media_bytes(client, event, **conn):
        seen["pulled_event"] = event
        return b"PKG-BYTES", "secret.tar.gz"

    monkeypatch.setattr(mt, "pull_media_bytes", fake_pull_media_bytes)
    calls = _patch_save_package(
        monkeypatch, mt, meta={"package_name": "secret.tar.gz", "sha256": "e" * 64, "size_bytes": 9}
    )

    from app.services.tool_adapters.matrix_tools import pull_attachment

    result = pull_attachment(
        {"room_id": "!room:example.org", "sender": "@alice:example.org"},
        _ctx(),
        db,
    )
    assert seen["room_id"] == "!room:example.org"
    assert seen["decryptor"] is not None, "E2EE 解密器必须传入异步筛选"
    assert seen["pulled_event"].encrypted is True
    assert result["package_name"] == "secret.tar.gz"
    assert result["event_id"] == "$enc1"
    assert result["encrypted"] is True
    assert calls["filename"] == "secret.tar.gz"


# ── deploy_from_matrix ──

def test_deploy_from_matrix_happy_path(monkeypatch, db):
    import app.api.matrix as matrix_api

    mt = _patch_matrix_client(monkeypatch, event=_media_event())
    _patch_save_package(monkeypatch, mt)

    # matrix_deploy_core 内部使用 app.api.matrix._client()
    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def find_latest_media_event(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return _media_event()

        def download_media(self, mxc_url):
            return b"PKG", "crypto-frontend.tar.gz"

    monkeypatch.setattr(matrix_api, "_client", lambda *a, **k: FakeClient())

    from app.services.tool_adapters.matrix_tools import deploy_from_matrix

    async def fake_queue(user, data, db):
        assert data["file_name"] == "crypto-frontend.tar.gz"
        assert data["environment"] == "test"
        return {"success": True, "data": {"task_id": "task-1", "deployment_id": "dep-1", "queued": True}}

    monkeypatch.setattr(matrix_api, "queue_deploy_v2", fake_queue)

    audit_calls = []

    def fake_audit(action, target_type, target_name, details):
        audit_calls.append(details)

    monkeypatch.setattr(matrix_api, "audit", fake_audit)

    result = deploy_from_matrix(
        {"room_id": "!room:example.org", "sender": "@alice:example.org", "service": "api", "env": "test"},
        _ctx(),
        db,
    )
    assert result["success"] is True
    assert result["data"]["task_id"] == "task-1"
    assert result["data"]["matrix"]["room_id"] == "!room:example.org"
    assert any("roomId=!room:example.org" in d and "sender=@alice:example.org" in d for d in audit_calls)


def test_deploy_from_matrix_requires_service(monkeypatch, db):
    mt = _patch_matrix_client(monkeypatch, event=_media_event())
    from app.services.tool_adapters.matrix_tools import deploy_from_matrix

    with pytest.raises(HTTPException) as exc:
        deploy_from_matrix({"room_id": "!room:example.org", "sender": "@alice:example.org", "env": "test"}, _ctx(), db)
    assert exc.value.status_code == 400


def test_deploy_from_matrix_room_binding_blocks(monkeypatch, db):
    mt = _patch_matrix_client(monkeypatch, event=_media_event())
    from app.services.tool_adapters.matrix_tools import deploy_from_matrix

    ctx = _ctx_bound_room("!other:example.org")
    with pytest.raises(HTTPException) as exc:
        deploy_from_matrix(
            {"room_id": "!room:example.org", "sender": "@alice:example.org", "service": "api", "env": "test"},
            ctx,
            db,
        )
    assert exc.value.status_code == 403


# ── 连接参数由 Agent 传入（参数优先，环境变量兜底）──

def test_scan_media_events_agent_supplied_connection(monkeypatch, db):
    """Agent 传入 homeserver_url / access_token 时，客户端使用传入值。"""
    import app.services.tool_adapters.matrix_tools as mt

    captured = {}

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def __init__(self, homeserver_url="", access_token=""):
            captured["homeserver_url"] = homeserver_url
            captured["access_token"] = access_token

        def list_media_events(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return [_media_event()]

    monkeypatch.setattr(
        mt, "_matrix_client",
        lambda args: FakeClient(
            homeserver_url=str((args or {}).get("homeserver_url") or ""),
            access_token=str((args or {}).get("access_token") or ""),
        ),
    )
    from app.services.tool_adapters.matrix_tools import scan_media_events

    result = scan_media_events(
        {
            "room_id": "!room:example.org",
            "homeserver_url": "https://agent-matrix.example.org",
            "access_token": "syt_agent_secret",
        },
        _ctx(),
        db,
    )
    assert result["room_id"] == "!room:example.org"
    assert captured["homeserver_url"] == "https://agent-matrix.example.org"
    assert captured["access_token"] == "syt_agent_secret"


def test_pull_attachment_agent_supplied_connection(monkeypatch, db):
    """pull_attachment 支持 Agent 传入 homeserver/token 并透传到 MatrixClient。"""
    import app.services.tool_adapters.matrix_tools as mt

    captured = {}

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def __init__(self, homeserver_url="", access_token=""):
            captured["homeserver_url"] = homeserver_url
            captured["access_token"] = access_token

        def find_latest_media_event(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return _media_event()

        def download_media(self, mxc_url):
            return b"PKG", "crypto-frontend.tar.gz"

    monkeypatch.setattr(
        mt, "_matrix_client",
        lambda args: FakeClient(
            homeserver_url=str((args or {}).get("homeserver_url") or ""),
            access_token=str((args or {}).get("access_token") or ""),
        ),
    )
    calls = _patch_save_package(monkeypatch, mt)
    from app.services.tool_adapters.matrix_tools import pull_attachment

    result = pull_attachment(
        {
            "room_id": "!room:example.org",
            "sender": "@alice:example.org",
            "homeserver_url": "https://agent-matrix.example.org",
            "access_token": "syt_agent_secret",
        },
        _ctx(),
        db,
    )
    assert result["package_name"] == "crypto-frontend.tar.gz"
    assert captured["homeserver_url"] == "https://agent-matrix.example.org"
    assert captured["access_token"] == "syt_agent_secret"


def test_deploy_from_matrix_forwards_connection_to_core(monkeypatch, db):
    """deploy_from_matrix 把 Agent 传入的 homeserver/token 转发到 matrix_deploy_core。"""
    import app.api.matrix as matrix_api

    _patch_save_package(monkeypatch, matrix_api)

    captured = {}

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def __init__(self, homeserver_url="", access_token=""):
            captured["homeserver_url"] = homeserver_url
            captured["access_token"] = access_token

        def find_latest_media_event(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return _media_event()

        def download_media(self, mxc_url):
            return b"PKG", "crypto-frontend.tar.gz"

    monkeypatch.setattr(matrix_api, "_client", lambda *a, **k: FakeClient(*a, **k))

    async def fake_queue(user, data, db):
        return {"success": True, "data": {"task_id": "t1", "deployment_id": "d1", "queued": True}}

    monkeypatch.setattr(matrix_api, "queue_deploy_v2", fake_queue)
    monkeypatch.setattr(matrix_api, "audit", lambda *a, **k: None)

    from app.services.tool_adapters.matrix_tools import deploy_from_matrix

    result = deploy_from_matrix(
        {
            "room_id": "!room:example.org",
            "sender": "@alice:example.org",
            "service": "api",
            "env": "test",
            "homeserver_url": "https://agent-matrix.example.org",
            "access_token": "syt_agent_secret",
        },
        _ctx(),
        db,
    )
    assert result["success"] is True
    assert captured["homeserver_url"] == "https://agent-matrix.example.org"
    assert captured["access_token"] == "syt_agent_secret"

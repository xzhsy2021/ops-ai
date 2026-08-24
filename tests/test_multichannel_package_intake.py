"""Task 10: 多通道附件绑定包入库测试。

每个通道（matrix/wechat/telegram）通过 /api/v2/tools/packages/upload 上传附件，
携带 JSON message_context 字段。断言：
- token conversation binding 被强制；
- 实际 SHA-256 等于声明的 package_sha256；
- 包元数据存储 channel/account/conversation/message/sender 与 source_message_key；
- 同消息同哈希重试复用已有包；
- 同消息不同哈希返回 409；
- 旧 Matrix 表单字段仅在该端点规范化。
"""
import asyncio
import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import tools as tools_api
from app.db.base import Base
from app.db.migrations.runner import run_schema_migrations
from app.db.models import DeployPackage
from app.services import package_retention
from app.services.message_context import SUPPORTED_CHANNELS, MessageContext


@pytest.fixture(scope="module")
def db():
    # StaticPool：所有线程共享同一条内存连接。上传端点会把
    # save_package_fileobj 卸载到线程池，默认 SingletonThreadPool 下
    # 工作线程会拿到一条全新的空 ：memory: 连接（no such table）。
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# 系统级 message_routing.rooms：允许各通道 room-1 与 legacy 矩阵房间，
# 但排除 room-2（供 cross-conversation 拒绝用例）。token 级绑定不再参与作用域。
_ALLOWED_ROOMS = [
    {"channel": "matrix", "channel_account_id": "primary", "conversation_id": "room-1"},
    {"channel": "wechat", "channel_account_id": "primary", "conversation_id": "room-1"},
    {"channel": "telegram", "channel_account_id": "primary", "conversation_id": "room-1"},
    {"channel": "matrix", "channel_account_id": "default", "conversation_id": "!ops:example.org"},
]


@pytest.fixture(autouse=True)
def _system_rooms(monkeypatch):
    from app.services.tool_adapters import approval_tools

    monkeypatch.setattr(
        approval_tools,
        "get_all_systems",
        lambda: {
            "crypto-trader": {
                "name": "crypto-trader",
                "message_routing": {
                    "enabled": True,
                    "aliases": ["量化"],
                    "keywords": [],
                    "priority": 10,
                    "approvers": ["@ops:example.org"],
                    "rooms": list(_ALLOWED_ROOMS),
                },
                "services": [],
            }
        },
    )


class _Upload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.file = BytesIO(content)

    async def seek(self, offset: int):
        self.file.seek(offset)


def _bindings(channel: str, conversation_id: str = "room-1") -> list[dict]:
    return [{
        "channel": channel,
        "channel_account_id": "primary",
        "conversation_id": conversation_id,
    }]


def _context(channel: str, **overrides) -> MessageContext:
    values = {
        "channel": channel,
        "channel_account_id": "primary",
        "conversation_id": "room-1",
        "message_id": "message-1",
        "sender_id": "user-1",
        "content_sha256": "a" * 64,
    }
    values.update(overrides)
    return MessageContext(**values)


def _token_ctx(bindings):
    return SimpleNamespace(
        auth_type="tool_token",
        scopes=["ops:read"],
        channel_bindings=bindings,
        username="qclaw",
        token_owner="qclaw",
        client_name="qclaw",
        has_scope=lambda scope: scope == "ops:read",
    )


def _patch_env(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: _token_ctx(_bindings("matrix")))
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        package_retention,
        "get_runtime_path",
        lambda env, default: str(tmp_path / "uploads"),
    )


def _upload(db, ctx, *, content, package_sha256, message_context=None, room_id="", request_event_id="", content_sha256="", db_arg=None):
    return asyncio.run(tools_api.upload_package_by_tool_token(
        request=SimpleNamespace(),
        file=_Upload("frontend.tar.gz", content),
        system="crypto-trader",
        service="crypto-frontend",
        overwrite=False,
        approval_intake=True,
        room_id=room_id,
        request_event_id=request_event_id,
        content_sha256=content_sha256,
        package_sha256=package_sha256,
        message_context=message_context or "",
        db=db_arg if db_arg is not None else db,
    ))


@pytest.mark.parametrize("channel", sorted(SUPPORTED_CHANNELS))
def test_upload_stores_channel_source_metadata(monkeypatch, tmp_path, db, channel):
    """每个通道上传成功后，包元数据记录通道上下文与 source_message_key。"""
    context = _context(channel)
    content = b"package payload"
    package_sha256 = hashlib.sha256(content).hexdigest()

    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: _token_ctx(_bindings(channel)))
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        package_retention,
        "get_runtime_path",
        lambda env, default: str(tmp_path / "uploads"),
    )

    response = _upload(db, _token_ctx(_bindings(channel)), content=content, package_sha256=package_sha256,
                       message_context=json.dumps(context.to_dict()))

    assert response["data"]["result"]["approval_intake"] is True
    assert response["data"]["result"]["reused"] is False

    row = db.query(DeployPackage).filter(
        DeployPackage.package_name == response["data"]["result"]["package_name"]
    ).first()
    assert row is not None
    stored = row.source_context
    assert stored["channel"] == channel
    assert stored["channel_account_id"] == "primary"
    assert stored["conversation_id"] == "room-1"
    assert stored["message_id"] == "message-1"
    assert stored["sender_id"] == "user-1"
    assert row.source_message_key == f"{channel}:primary:room-1:message-1:{package_sha256}"


@pytest.mark.parametrize("channel", sorted(SUPPORTED_CHANNELS))
def test_upload_rejects_cross_conversation(monkeypatch, tmp_path, db, channel):
    """token 绑定的是 room-1，消息上下文来自 room-2 → 403。"""
    context = _context(channel, conversation_id="room-2")
    content = b"package payload"
    package_sha256 = hashlib.sha256(content).hexdigest()

    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: _token_ctx(_bindings(channel)))
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc:
        _upload(db, _token_ctx(_bindings(channel)), content=content, package_sha256=package_sha256,
                message_context=json.dumps(context.to_dict()))
    assert exc.value.status_code == 403


def test_upload_verifies_actual_package_sha256(monkeypatch, tmp_path, db):
    """实际文件哈希与声明的 package_sha256 不符 → 409。"""
    context = _context("matrix")
    content = b"package payload"
    wrong_sha = "b" * 64

    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: _token_ctx(_bindings("matrix")))
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc:
        _upload(db, _token_ctx(_bindings("matrix")), content=content, package_sha256=wrong_sha,
                message_context=json.dumps(context.to_dict()))
    assert exc.value.status_code == 409


def _purge_matrix_intake_rows(db):
    """清理本消息键的既有包记录，保证用例不依赖其他用例的回滚副作用。

    同模块的 metadata 用例会先写入相同 source_message_key（固定 room-1/message-1）；
    全量运行时后续用例的异常路径会顺带回滚该插入，而按 -k 过滤运行时不会，
    导致 retry/409 用例首传即命中 reused=True。显式清空消除顺序依赖。
    """
    deleted = db.query(DeployPackage).delete()
    db.commit()
    return deleted


@pytest.mark.parametrize("channel", sorted(SUPPORTED_CHANNELS))
def test_retry_same_message_same_hash_reuses_package(monkeypatch, tmp_path, db, channel):
    """同消息同哈希重试复用已有包，不重复写入。"""
    context = _context(channel)
    content = b"package payload"
    package_sha256 = hashlib.sha256(content).hexdigest()
    payload = json.dumps(context.to_dict())
    _purge_matrix_intake_rows(db)

    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: _token_ctx(_bindings(channel)))
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        package_retention,
        "get_runtime_path",
        lambda env, default: str(tmp_path / "uploads"),
    )

    first = _upload(db, _token_ctx(_bindings(channel)), content=content, package_sha256=package_sha256,
                    message_context=payload)
    assert first["data"]["result"]["reused"] is False

    second = _upload(db, _token_ctx(_bindings(channel)), content=content, package_sha256=package_sha256,
                     message_context=payload)
    assert second["data"]["result"]["reused"] is True
    assert second["data"]["result"]["package_name"] == first["data"]["result"]["package_name"]


@pytest.mark.parametrize("channel", sorted(SUPPORTED_CHANNELS))
def test_same_message_different_hash_returns_409(monkeypatch, tmp_path, db, channel):
    """同消息不同哈希 → 409，不允许覆盖同一消息的内容。"""
    context = _context(channel)
    first_content = b"package payload"
    first_sha = hashlib.sha256(first_content).hexdigest()
    payload = json.dumps(context.to_dict())
    _purge_matrix_intake_rows(db)

    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: _token_ctx(_bindings(channel)))
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        package_retention,
        "get_runtime_path",
        lambda env, default: str(tmp_path / "uploads"),
    )

    first = _upload(db, _token_ctx(_bindings(channel)), content=first_content, package_sha256=first_sha,
                    message_context=payload)
    assert first["data"]["result"]["reused"] is False

    second_content = b"different package payload"
    second_sha = hashlib.sha256(second_content).hexdigest()
    with pytest.raises(HTTPException) as exc:
        _upload(db, _token_ctx(_bindings(channel)), content=second_content, package_sha256=second_sha,
                message_context=payload)
    assert exc.value.status_code == 409


def test_legacy_matrix_fields_normalize_at_upload_boundary(monkeypatch, tmp_path, db):
    """旧 Matrix 表单字段（room_id/request_event_id/content_sha256）仅在端点规范化。"""
    content = b"package payload"
    package_sha256 = hashlib.sha256(content).hexdigest()

    # legacy 字段规范化后对应 matrix:default:!ops:example.org，token 必须绑定该会话
    legacy_bindings = [{
        "channel": "matrix",
        "channel_account_id": "default",
        "conversation_id": "!ops:example.org",
    }]
    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: _token_ctx(legacy_bindings))
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        package_retention,
        "get_runtime_path",
        lambda env, default: str(tmp_path / "uploads"),
    )

    response = _upload(
        db,
        _token_ctx(legacy_bindings),
        content=content,
        package_sha256=package_sha256,
        room_id="!ops:example.org",
        request_event_id="$legacy-event",
        content_sha256="c" * 64,
    )

    assert response["data"]["result"]["approval_intake"] is True
    row = db.query(DeployPackage).filter(
        DeployPackage.package_name == response["data"]["result"]["package_name"]
    ).first()
    assert row is not None
    stored = row.source_context
    assert stored["channel"] == "matrix"
    assert stored["channel_account_id"] == "default"
    assert stored["conversation_id"] == "!ops:example.org"
    assert stored["message_id"] == "$legacy-event"
    assert row.source_message_key == f"matrix:default:!ops:example.org:$legacy-event:{package_sha256}"


def test_upload_rejects_malformed_message_context(monkeypatch, tmp_path, db):
    """message_context 不是合法 JSON → 400。"""
    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: _token_ctx(_bindings("matrix")))
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)

    content = b"package payload"
    package_sha256 = hashlib.sha256(content).hexdigest()
    with pytest.raises(HTTPException) as exc:
        _upload(db, _token_ctx(_bindings("matrix")), content=content, package_sha256=package_sha256,
                message_context="{not json")
    assert exc.value.status_code == 400

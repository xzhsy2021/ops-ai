"""Matrix 部署包对接：API 端点集成测试。

覆盖：
- GET /api/v2/matrix/status
- POST /api/v2/matrix/rooms/{room_id}/scan
- POST /api/deploy（别名）+ POST /api/v2/matrix/deploy
"""
import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, Dict

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.helpers import api_response
from app.api.matrix import (
    _matrix_deploy_handler,
    _require_matrix_deploy_user,
    matrix_deploy_router,
    matrix_router,
)
from app.db.base import Base
from app.db.migrations.runner import run_schema_migrations
from app.services.matrix_client import MatrixClient, MatrixMediaEvent


@pytest.fixture(scope="module")
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ── 伪造工具 ──

class FakeRequest:
    """伪造 FastAPI Request，可注入 JSON body 和 auth header。"""

    def __init__(self, body: Dict[str, Any] = None, auth_header: str = ""):
        self._body = body or {}
        self._auth = auth_header
        self.state = SimpleNamespace()
        self.client = SimpleNamespace(host="127.0.0.1")

    async def json(self):
        return self._body

    def headers(self):
        h = dict(self._body.get("_headers", {}))
        if self._auth:
            h["authorization"] = self._auth
        return SimpleNamespace(get=lambda key, default="": h.get(key, default))


# ── 测试 status ──

def test_matrix_status(monkeypatch, db):
    from app.api.matrix import matrix_status

    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True}

    monkeypatch.setattr("app.api.matrix.require_auth", fake_user)
    fake_req = FakeRequest()
    resp = matrix_status(fake_req, db)
    assert resp["success"] is True
    data = resp["data"]
    assert "configured" in data
    assert data["configured"] is False  # no env vars


# ── 测试 scan ──

def test_scan_room(monkeypatch, db):
    from app.api.matrix import matrix_room_scan

    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True}

    monkeypatch.setattr("app.api.matrix.require_auth", fake_user)
    # Mock MatrixClient
    now_ms = int(time.time() * 1000)
    mock_event = MatrixMediaEvent(
        event_id="$abc",
        sender="@alice:example.org",
        origin_server_ts=now_ms - 60_000,
        msgtype="m.file",
        filename="crypto-frontend.tar.gz",
        mxc_url="mxc://hs/abc",
        encrypted=False,
    )

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def list_media_events(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            assert room_id == "!room:example.org"
            assert sender == "@alice:example.org"
            return [mock_event]

        def debug_recent_events(self, room_id, sender="", minutes=15, limit=50):
            return {
                "total_events": 1,
                "room_message_count": 1,
                "in_window": 1,
                "out_of_window": 0,
                "window_minutes": minutes,
                "msgtype_distribution": {"m.file": 1},
                "sender_distribution": {sender: 1},
                "text_with_filename_hint": [],
                "hint": "...",
            }

    monkeypatch.setattr("app.api.matrix._client", lambda *a, **k: FakeClient())

    fake_req = FakeRequest(body={"sender": "@alice:example.org", "minutes": 15, "filename": "crypto"})
    resp = asyncio.run(matrix_room_scan("!room:example.org", fake_req, db))
    data = resp["data"]
    assert data["room_id"] == "!room:example.org"
    assert len(data["events"]) == 1
    assert data["events"][0]["event_id"] == "$abc"


# ── 测试 deploy 端点 ──

def _make_matrix_deploy_request(env="test", service="api", sender="@alice:example.org", **overrides):
    body = {
        "env": env,
        "service": service,
        "system": "crypto-trader",
        "matrix": {
            "roomId": "!room:example.org",
            "sender": sender,
            "triggerEventId": "$trigger",
            "filename": "crypto-frontend",
        },
        **overrides,
    }
    return body


def test_matrix_deploy_happy_path(monkeypatch, db):
    """完整流程：auth → 拉事件 → 找媒体 → 下载 → 入库 → 排队发布 → 审计。"""
    # 1. 伪造 auth 返回会话用户
    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True, "can_deploy": True}

    monkeypatch.setattr("app.api.matrix._require_matrix_deploy_user", fake_user)
    monkeypatch.setattr("app.api.matrix._enforce_matrix_deploy_env", lambda user, env: None)

    # 2. 伪造 MatrixClient 返回事件
    now_ms = int(time.time() * 1000)
    mock_event = MatrixMediaEvent(
        event_id="$abc",
        sender="@alice:example.org",
        origin_server_ts=now_ms - 60_000,
        msgtype="m.file",
        filename="crypto-frontend.tar.gz",
        mxc_url="mxc://hs/abc",
        encrypted=False,
    )

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def find_latest_media_event(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return mock_event

        def download_media(self, mxc_url):
            assert mxc_url == "mxc://hs/abc"
            return b"PACKAGE-CONTENT", "crypto-frontend.tar.gz"

    monkeypatch.setattr("app.api.matrix._client", lambda *a, **k: FakeClient())

    # 3. 伪造 save_package_fileobj 返回写入的元数据
    fake_meta = {"package_name": "crypto-frontend.tar.gz", "name": "crypto-frontend.tar.gz", "sha256": "a" * 64}

    def fake_save(db, filename, fileobj, system="", service="", uploaded_by="", overwrite=False, source_context=None, source_message_key=None):
        assert filename == "crypto-frontend.tar.gz"
        assert system == "crypto-trader"
        assert service == "api"
        return fake_meta

    monkeypatch.setattr("app.api.matrix.save_package_fileobj", fake_save)

    # 4. 伪造 queue_deploy_v2 返回排队结果
    fake_queue_result = {"success": True, "data": {"task_id": "task-123", "deployment_id": "dep-456", "queued": True}}

    async def fake_queue(user, data, db):
        assert data["system"] == "crypto-trader"
        assert data["service"] == "api"
        assert data["environment"] == "test"
        assert data["file_name"] == "crypto-frontend.tar.gz"
        return fake_queue_result

    monkeypatch.setattr("app.api.matrix.queue_deploy_v2", fake_queue)

    # 5. 伪造 audit
    audit_calls = []

    def fake_audit(action, target_type, target_name, details):
        audit_calls.append((action, target_type, target_name, details))

    monkeypatch.setattr("app.api.matrix.audit", fake_audit)

    # 执行
    fake_req = FakeRequest(body=_make_matrix_deploy_request())
    result = asyncio.run(_matrix_deploy_handler(fake_req, db))

    # 验证响应
    assert result["success"] is True
    assert result["data"]["task_id"] == "task-123"
    assert result["data"]["matrix"]["room_id"] == "!room:example.org"
    assert result["data"]["matrix"]["media_event_id"] == "$abc"

    # 验证审计
    assert len(audit_calls) == 1
    action, target_type, target_name, details = audit_calls[0]
    assert action == "deploy.execute.matrix"
    assert "roomId=!room:example.org" in details
    assert "mediaEventId=$abc" in details
    assert "triggerEventId=$trigger" in details
    assert "sender=@alice:example.org" in details
    assert "env=test" in details
    assert "service=api" in details


def test_matrix_deploy_no_room_id(monkeypatch, db):
    """缺少 roomId 应返回 400。"""
    body = {"env": "test", "service": "api", "matrix": {"sender": "@alice:example.org"}}
    fake_req = FakeRequest(body=body)

    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True, "can_deploy": True}

    monkeypatch.setattr("app.api.matrix._require_matrix_deploy_user", fake_user)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_matrix_deploy_handler(fake_req, db))
    assert exc.value.status_code == 400


def test_matrix_deploy_no_sender(monkeypatch, db):
    """缺少 sender 应返回 400。"""
    body = {"env": "test", "service": "api", "matrix": {"roomId": "!room:example.org"}}
    fake_req = FakeRequest(body=body)

    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True, "can_deploy": True}

    monkeypatch.setattr("app.api.matrix._require_matrix_deploy_user", fake_user)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_matrix_deploy_handler(fake_req, db))
    assert exc.value.status_code == 400


def test_matrix_deploy_matrix_not_configured(monkeypatch, db):
    """Matrix 未配置时应返回 503。"""
    body = _make_matrix_deploy_request()
    fake_req = FakeRequest(body=body)

    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True, "can_deploy": True}

    monkeypatch.setattr("app.api.matrix._require_matrix_deploy_user", fake_user)
    # MatrixClient 未配置
    monkeypatch.setattr("app.api.matrix.MatrixClient", lambda: SimpleNamespace(configured=False))
    monkeypatch.setattr("app.api.matrix._client", lambda *a, **k: (_ for _ in ()).throw(HTTPException(503, "Matrix 未配置")))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_matrix_deploy_handler(fake_req, db))
    assert exc.value.status_code == 503


def test_matrix_deploy_e2ee_encrypted(monkeypatch, db):
    """E2EE 加密媒体应返回 400。"""
    body = _make_matrix_deploy_request()
    fake_req = FakeRequest(body=body)

    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True, "can_deploy": True}

    monkeypatch.setattr("app.api.matrix._require_matrix_deploy_user", fake_user)
    monkeypatch.setattr("app.api.matrix._enforce_matrix_deploy_env", lambda user, env: None)

    now_ms = int(time.time() * 1000)
    mock_event = MatrixMediaEvent(
        event_id="$e2ee",
        sender="@alice:example.org",
        origin_server_ts=now_ms - 60_000,
        msgtype="m.file",
        filename="secret.tar.gz",
        mxc_url="",
        encrypted=True,
    )

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def find_latest_media_event(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return mock_event

    monkeypatch.setattr("app.api.matrix._client", lambda *a, **k: FakeClient())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_matrix_deploy_handler(fake_req, db))
    assert exc.value.status_code == 400
    assert "E2EE" in str(exc.value.detail)


# ── 连接参数由 Agent 传入 ──

def test_deploy_agent_supplied_homeserver_token(monkeypatch, db):
    """Agent 传入 matrix.homeserverUrl / matrix.accessToken 时，客户端用传入值。"""
    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True, "can_deploy": True}

    monkeypatch.setattr("app.api.matrix._require_matrix_deploy_user", fake_user)
    monkeypatch.setattr("app.api.matrix._enforce_matrix_deploy_env", lambda user, env: None)

    captured = {}

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def __init__(self, homeserver_url="", access_token=""):
            captured["homeserver_url"] = homeserver_url
            captured["access_token"] = access_token

        def find_latest_media_event(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return MatrixMediaEvent(
                event_id="$abc",
                sender=sender,
                origin_server_ts=int(time.time() * 1000) - 60_000,
                msgtype="m.file",
                filename="crypto-frontend.tar.gz",
                mxc_url="mxc://agent-hs/abc",
                encrypted=False,
            )

        def download_media(self, mxc_url):
            return b"PKG", "crypto-frontend.tar.gz"

    monkeypatch.setattr("app.api.matrix._client", lambda *a, **k: FakeClient(*a, **k))
    monkeypatch.setattr(
        "app.api.matrix.save_package_fileobj",
        lambda db, filename, fileobj, system="", service="", uploaded_by="", overwrite=False, source_context=None, source_message_key=None:
        {"package_name": filename, "name": filename, "sha256": "b" * 64},
    )

    async def fake_queue(user, data, db):
        return {"success": True, "data": {"task_id": "t1", "deployment_id": "d1", "queued": True}}

    monkeypatch.setattr("app.api.matrix.queue_deploy_v2", fake_queue)
    monkeypatch.setattr("app.api.matrix.audit", lambda *a, **k: None)

    body = _make_matrix_deploy_request(
        **{"matrix": {"roomId": "!room:example.org", "sender": "@alice:example.org",
                      "homeserverUrl": "https://agent-matrix.example.org",
                      "accessToken": "syt_agent_secret"}}
    )
    result = asyncio.run(_matrix_deploy_handler(FakeRequest(body=body), db))
    assert result["success"] is True
    assert captured["homeserver_url"] == "https://agent-matrix.example.org"
    assert captured["access_token"] == "syt_agent_secret"


# ── 404 + 诊断 ──

def test_matrix_deploy_404_includes_debug_when_no_event(monkeypatch, db):
    """未找到媒体事件时，404 detail 应附带 debug 信息提示真上传附件。"""
    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True, "can_deploy": True}

    monkeypatch.setattr("app.api.matrix._require_matrix_deploy_user", fake_user)
    monkeypatch.setattr("app.api.matrix._enforce_matrix_deploy_env", lambda user, env: None)

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def find_latest_media_event(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return None

        def debug_recent_events(self, room_id, sender="", minutes=15, limit=50):
            return {
                "total_events": 8,
                "room_message_count": 8,
                "in_window": 8,
                "out_of_window": 0,
                "window_minutes": minutes,
                "msgtype_distribution": {"m.text": 7, "m.file": 0, "m.image": 0},
                "sender_distribution": {sender: 8},
                "text_with_filename_hint": [
                    {"event_id": "$x", "sender": sender, "ts": 0,
                     "body": "crypto-trader-web.tar.gz 请部署到测试环境"}
                ],
                "hint": "房间内只有 m.text 文字，没有 m.file 附件",
            }

    monkeypatch.setattr("app.api.matrix._client", lambda *a, **k: FakeClient())

    body = _make_matrix_deploy_request()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_matrix_deploy_handler(FakeRequest(body=body), db))
    assert exc.value.status_code == 404
    detail = str(exc.value.detail)
    assert "未找到" in detail
    assert "m.text" in detail
    assert "m.file" in detail
    assert "msgtype" in detail


def test_scan_empty_includes_debug(monkeypatch, db):
    """scan 端点未匹配时自动附带 debug 信息。"""
    from app.api.matrix import matrix_room_scan

    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True}

    monkeypatch.setattr("app.api.matrix.require_auth", fake_user)

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        def list_media_events(self, room_id, sender="", minutes=15, filename_hint="", limit=50):
            return []

        def debug_recent_events(self, room_id, sender="", minutes=15, limit=50):
            return {
                "total_events": 5,
                "room_message_count": 5,
                "in_window": 5,
                "out_of_window": 0,
                "window_minutes": minutes,
                "msgtype_distribution": {"m.text": 5},
                "sender_distribution": {sender: 5},
                "text_with_filename_hint": [],
                "hint": "...",
            }

    monkeypatch.setattr("app.api.matrix._client", lambda *a, **k: FakeClient())
    fake_req = FakeRequest(body={"sender": "@alice:example.org", "minutes": 30})
    resp = asyncio.run(matrix_room_scan("!room:example.org", fake_req, db))
    data = resp["data"]
    assert data["events"] == []
    assert data["debug"]["msgtype_distribution"] == {"m.text": 5}
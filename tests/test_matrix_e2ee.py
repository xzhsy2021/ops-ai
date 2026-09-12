"""Matrix E2EE 接入测试。

覆盖：
- MATRIX_E2EE_* 配置加载（enabled / device_id / crypto store 路径）
- MatrixClient.list_media_events_async：解密器路径（m.room.encrypted → 媒体事件）、
  解密失败跳过、sender/窗口/文件名过滤对已解密事件同样生效
- MatrixClient.find_latest_media_event_async：有/无解密器的回退行为
- MatrixClient.fetch_media_ciphertext：认证端点 v1 → 旧版 v3 回退
- matrix_e2ee：配置快照、不可用提示、whoami 解析、Megolm 事件解密封装
  （unknown_session 归因）、加密媒体 AES-CTR 解密（真实 SDK round-trip）
- API / MCP 集成：加密事件在 E2EE 就绪时可走通发布链路；
  未配置时保持 400 + "E2EE" 契约
"""
import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import matrix_client as mc_module
from app.services import matrix_e2ee
from app.services.matrix_client import MatrixClient, MatrixMediaEvent
from app.services.matrix_e2ee import (
    MatrixE2eeError,
    MatrixE2eeSession,
    build_room_event_decryptor,
    e2ee_config,
    e2ee_unavailable_detail,
    get_e2ee_session,
    pull_media_bytes,
    reset_sessions,
)


@pytest.fixture(autouse=True)
def _clean_sessions():
    reset_sessions()
    yield
    reset_sessions()


# ── 配置 ──

def test_e2ee_config_defaults():
    cfg = e2ee_config()
    assert cfg["enabled"] is True
    assert cfg["sdk_available"] is True  # 测试解释器已安装 matrix-nio[e2e]
    assert cfg["device_id"] == "OPS-AI-BOT"
    assert cfg["crypto_store_path"].endswith("crypto_store")
    # 未配置连接信息时 configured 必须为 False（防止误发起网络请求）
    if not (cfg["homeserver_url"] and cfg["has_access_token"]):
        assert cfg["configured"] is False


def test_unavailable_detail_mentions_fix(monkeypatch):
    monkeypatch.setattr(matrix_e2ee, "MATRIX_HOMESERVER_URL", "")
    monkeypatch.setattr(matrix_e2ee, "MATRIX_ACCESS_TOKEN", "")
    detail = e2ee_unavailable_detail()
    assert "E2EE" in detail


# ── whoami 解析 ──

def test_resolve_user_id_via_whoami(monkeypatch):
    class FakeAsyncHttp:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            return SimpleNamespace(status_code=200, json=lambda: {"user_id": "@bot:example.org"})

    monkeypatch.setattr(matrix_e2ee.httpx, "AsyncClient", FakeAsyncHttp)
    session = MatrixE2eeSession(
        homeserver_url="https://hs.example", access_token="tok", user_id=""
    )
    resolved = asyncio.run(session._resolve_user_id())
    assert resolved == "@bot:example.org"
    assert session.user_id == "@bot:example.org"


def test_resolve_user_id_rejects_invalid_token(monkeypatch):
    class FakeAsyncHttp:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            return SimpleNamespace(status_code=401, json=lambda: {"errcode": "M_UNKNOWN_TOKEN"})

    monkeypatch.setattr(matrix_e2ee.httpx, "AsyncClient", FakeAsyncHttp)
    session = MatrixE2eeSession(
        homeserver_url="https://hs.example", access_token="bad", user_id=""
    )
    with pytest.raises(MatrixE2eeError) as exc:
        asyncio.run(session._resolve_user_id())
    assert exc.value.reason == "auth_failed"


# ── get_e2ee_session 配置门禁 ──

def test_get_session_requires_credentials(monkeypatch):
    monkeypatch.setattr(matrix_e2ee, "MATRIX_HOMESERVER_URL", "")
    monkeypatch.setattr(matrix_e2ee, "MATRIX_ACCESS_TOKEN", "")

    async def _run():
        return await get_e2ee_session(homeserver_url="", access_token="")

    with pytest.raises(MatrixE2eeError) as exc:
        asyncio.run(_run())
    assert exc.value.reason == "not_configured"


def test_get_session_refuses_dynamic_credentials(monkeypatch):
    """专用设备守卫：调用方动态传入的凭据不得用于 E2EE sync。

    场景：Agent 传入 qclaw 等活跃 E2EE 客户端的 token —— 若 OPS 用它做
    sync/密钥上传，会覆盖原设备密钥并偷走 to-device room key，静默破坏
    原客户端的加密能力。必须只允许 .env 配置的专用 Bot 凭据。
    """
    monkeypatch.setattr(matrix_e2ee, "MATRIX_HOMESERVER_URL", "")
    monkeypatch.setattr(matrix_e2ee, "MATRIX_ACCESS_TOKEN", "")

    async def _run():
        return await get_e2ee_session(
            homeserver_url="https://matrix.example.com",
            access_token="syt_live_qclaw_token",
        )

    with pytest.raises(MatrixE2eeError) as exc:
        asyncio.run(_run())
    assert exc.value.reason == "not_configured"
    assert "专用" in str(exc.value)


def test_build_decryptor_requires_config(monkeypatch):
    monkeypatch.setattr(matrix_e2ee, "MATRIX_HOMESERVER_URL", "")
    monkeypatch.setattr(matrix_e2ee, "MATRIX_ACCESS_TOKEN", "")
    with pytest.raises(MatrixE2eeError):
        asyncio.run(build_room_event_decryptor(room_id="!room:example.org"))


# ── 回归：MCP HTTP 异步端点内调用同步工具（tools.py -32000 修复）──

def test_try_build_decryptor_runs_inside_running_loop(monkeypatch):
    """回归：在已运行的事件循环里调用同步工具，不得因 asyncio.run 嵌套
    而丢弃协程（症状：RuntimeWarning 'build_room_event_decryptor was
    never awaited' + MCP -32000）。"""
    from app.services.tool_adapters import matrix_tools as mt

    called: dict = {}

    async def fake_builder(*, room_id, homeserver_url="", access_token=""):
        called["room_id"] = room_id

        async def decryptor(raw_event):
            return None

        return decryptor

    monkeypatch.setattr(mt, "build_room_event_decryptor", fake_builder)

    async def dispatch():
        # 模拟 MCP HTTP 端点：同步工具直接跑在 uvicorn 的事件循环上
        return mt._try_build_decryptor("!room:x")

    decryptor = asyncio.run(dispatch())
    assert called == {"room_id": "!room:x"}
    assert callable(decryptor)


def test_scan_media_events_tool_inside_running_loop(monkeypatch):
    """回归：scan 工具在运行中的 loop 内完整走通（解密器生效路径）。"""
    from types import SimpleNamespace as NS

    from app.services.tool_adapters import matrix_tools as mt

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        async def list_media_events_async(self, room_id, *, sender="", minutes=None,
                                          filename_hint="", limit=50, event_decryptor=None):
            assert event_decryptor is not None
            return [
                MatrixMediaEvent(
                    event_id="$enc1",
                    sender="@alice:example.org",
                    origin_server_ts=int(time.time() * 1000) - 60_000,
                    msgtype="m.file",
                    filename="secret.tar.gz",
                    mxc_url="",
                    encrypted=True,
                    file_dict={"url": "mxc://hs/enc"},
                )
            ]

    monkeypatch.setattr(mt, "_matrix_client", lambda args=None: FakeClient())

    async def fake_builder(*, room_id, homeserver_url="", access_token=""):
        async def decryptor(raw_event):
            return None

        return decryptor

    monkeypatch.setattr(mt, "build_room_event_decryptor", fake_builder)

    ctx = NS(bound_room_ids=[])

    async def dispatch():
        return mt.scan_media_events({"room_id": "!room:x"}, ctx, None)

    result = asyncio.run(dispatch())
    assert result["e2ee"]["decryptor_active"] is True
    assert len(result["events"]) == 1
    assert result["events"][0]["encrypted"] is True


# ── list_media_events_async：解密器路径 ──

def _encrypted_wrapper(event_id="$enc1", sender="@alice:example.org", ts_ms=None):
    return {
        "type": "m.room.encrypted",
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": ts_ms if ts_ms is not None else int(time.time() * 1000) - 60_000,
        "content": {
            "algorithm": "m.megolm.v1.aes-sha2",
            "ciphertext": "AwgAEoAB",
            "sender_key": "curve25519key",
            "session_id": "session-1",
            "device_id": "DEV",
        },
    }


def _decrypted_inner(event_id="$enc1", sender="@alice:example.org", filename="pkg.tar.gz", ts_ms=None):
    # 第 11 轮：origin_server_ts 支持显式传入（显式值即最终值，不再额外偏移）。
    # 此前固定用挂钟毫秒 -60s，凡是"多个事件 + 断言顺序"的用例都会因为两次调用跨毫秒边界
    # 而随机反转（list_media_events_async 按 origin_server_ts 倒序）。
    ts = (int(time.time() * 1000) - 60_000) if ts_ms is None else int(ts_ms)
    return {
        "type": "m.room.message",
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": ts,
        "content": {
            "msgtype": "m.file",
            "body": filename,
            "file": {
                "url": "mxc://hs/encrypted-media",
                "key": {"k": "aeskey", "alg": "A256CTR", "ext": True},
                "iv": "ivvalue",
                "hashes": {"sha256": "digest"},
            },
        },
    }


def test_list_media_events_async_with_decryptor():
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    base_ms = int(time.time() * 1000)
    ts_older, ts_newer = base_ms - 120_000, base_ms - 60_000

    async def decryptor(raw_event):
        assert raw_event["type"] == "m.room.encrypted"
        # 用显式时间戳（都在 15 分钟窗口内）：$enc1 更早、$enc2 更新。
        # 挂钟毫秒会让两次调用跨毫秒边界，从而随机反转断言顺序。
        ts = ts_older if raw_event["event_id"] == "$enc1" else ts_newer
        return _decrypted_inner(event_id=raw_event["event_id"], ts_ms=ts)

    chunk = [_encrypted_wrapper("$enc1", ts_ms=ts_older),
             _encrypted_wrapper("$enc2", ts_ms=ts_newer)]
    client.fetch_room_messages = lambda room_id, limit=50: chunk

    events = asyncio.run(
        client.list_media_events_async("!r:x", event_decryptor=decryptor, limit=10)
    )
    # 契约：媒体事件按 origin_server_ts 倒序（最新在前）——显式断言语义，而不是依赖相等时间戳的稳定性
    assert [e.event_id for e in events] == ["$enc2", "$enc1"]
    assert all(e.encrypted for e in events)
    assert events[0].file_dict["url"] == "mxc://hs/encrypted-media"
    assert events[0].filename == "pkg.tar.gz"


def test_list_media_events_async_equal_timestamps_keep_input_order():
    """同一时间戳的事件保持调用方给出的顺序（sort 稳定性契约，确定性用例）。"""
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    same_ts = int(time.time() * 1000) - 60_000

    async def decryptor(raw_event):
        return _decrypted_inner(event_id=raw_event["event_id"], ts_ms=same_ts)

    chunk = [_encrypted_wrapper("$enc1", ts_ms=same_ts), _encrypted_wrapper("$enc2", ts_ms=same_ts)]
    client.fetch_room_messages = lambda room_id, limit=50: chunk

    events = asyncio.run(
        client.list_media_events_async("!r:x", event_decryptor=decryptor, limit=10)
    )
    assert [e.event_id for e in events] == ["$enc1", "$enc2"]


def test_list_media_events_async_decrypt_failure_skips_event():
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")

    async def bad_decryptor(raw_event):
        raise MatrixE2eeError("no session found", reason="unknown_session")

    client.fetch_room_messages = lambda room_id, limit=50: [
        _encrypted_wrapper("$enc1"),
        {
            "type": "m.room.message",
            "event_id": "$plain",
            "sender": "@alice:example.org",
            "origin_server_ts": int(time.time() * 1000) - 30_000,
            "content": {"msgtype": "m.file", "body": "plain.tar.gz", "url": "mxc://hs/plain"},
        },
    ]
    events = asyncio.run(
        client.list_media_events_async("!r:x", event_decryptor=bad_decryptor, limit=10)
    )
    # 加密事件解密失败被跳过，明文事件不受影响
    assert [e.event_id for e in events] == ["$plain"]
    assert events[0].encrypted is False


def test_list_media_events_async_filters_apply_to_decrypted():
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")

    async def decryptor(raw_event):
        # 解密出的内层事件来自 bob，且文件名不匹配 hint 的场景单独构造
        return _decrypted_inner(event_id=raw_event["event_id"], sender="@bob:example.org")

    client.fetch_room_messages = lambda room_id, limit=50: [_encrypted_wrapper("$enc1")]
    events = asyncio.run(
        client.list_media_events_async(
            "!r:x", sender="@alice:example.org", event_decryptor=decryptor, limit=10
        )
    )
    # sender 过滤对已解密事件同样生效
    assert events == []

    events_all = asyncio.run(
        client.list_media_events_async(
            "!r:x",
            sender="@bob:example.org",
            filename_hint="pkg",
            event_decryptor=decryptor,
            limit=10,
        )
    )
    assert [e.event_id for e in events_all] == ["$enc1"]


def test_find_latest_media_event_async_fallback_without_decryptor():
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    called = {}

    def fake_find(room_id, **kwargs):
        called["sync"] = True
        return MatrixMediaEvent(
            event_id="$plain",
            sender="@alice:example.org",
            origin_server_ts=int(time.time() * 1000),
            msgtype="m.file",
            filename="plain.tar.gz",
            mxc_url="mxc://hs/plain",
        )

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(client, "find_latest_media_event", fake_find)
        latest = asyncio.run(client.find_latest_media_event_async("!r:x"))
        assert latest.event_id == "$plain"
        assert called.get("sync") is True
    finally:
        monkey.undo()


# ── fetch_media_ciphertext 端点回退 ──

class _FakeMediaResponse:
    def __init__(self, content=b"CIPHER"):
        self.content = content
        self.headers = {"content-disposition": 'attachment; filename="pkg.tar.gz"'}


def test_fetch_media_ciphertext_prefers_authenticated_v1(monkeypatch):
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    paths = []

    def fake_request(method, path, headers=None, params=None, timeout=None):
        paths.append(path)
        return _FakeMediaResponse()

    monkeypatch.setattr(client, "_request", fake_request)
    data, filename = client.fetch_media_ciphertext("mxc://hs.example/AbCdEf")
    assert data == b"CIPHER"
    assert filename == "pkg.tar.gz"
    assert paths == ["/_matrix/client/v1/media/download/hs.example/AbCdEf"]


def test_fetch_media_ciphertext_falls_back_to_v3_on_404(monkeypatch):
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    paths = []

    def fake_request(method, path, headers=None, params=None, timeout=None):
        paths.append(path)
        if path.startswith("/_matrix/client/v1/"):
            raise mc_module.MatrixClientError(
                f"Matrix GET {path} -> HTTP 404: "
                '{"errcode":"M_UNRECOGNIZED","error":"Unrecognized request"}'
            )
        return _FakeMediaResponse()

    monkeypatch.setattr(client, "_request", fake_request)
    data, _ = client.fetch_media_ciphertext("mxc://hs.example/AbCdEf")
    assert data == b"CIPHER"
    assert paths[0].startswith("/_matrix/client/v1/")
    assert paths[-1] == "/_matrix/client/v3/media/download/hs.example/AbCdEf"


def test_fetch_media_ciphertext_raises_immediately_on_media_not_found(monkeypatch):
    """媒体本身不存在（M_NOT_FOUND）不回退端点，立即抛出真实原因，避免掩盖错误。"""
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    calls = []

    def fake_request(method, path, headers=None, params=None, timeout=None):
        calls.append(path)
        raise mc_module.MatrixClientError(f"Matrix GET {path} -> HTTP 404: M_NOT_FOUND")

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(mc_module.MatrixClientError) as exc:
        client.fetch_media_ciphertext("mxc://hs.example/Missing")
    assert "M_NOT_FOUND" in str(exc.value)
    assert len(calls) == 1
    assert calls[0].startswith("/_matrix/client/v1/")


# ── Megolm 事件解密封装 ──

def _fake_nio_client(decrypt_result=None, decrypt_error=None):
    olm = SimpleNamespace()

    def decrypt_megolm_event(event, room_id=None):
        if decrypt_error is not None:
            raise decrypt_error
        return decrypt_result

    olm.decrypt_megolm_event = decrypt_megolm_event
    return SimpleNamespace(olm=olm)


def test_decrypt_room_event_returns_plaintext_source(monkeypatch):
    session = MatrixE2eeSession(homeserver_url="https://hs.example", access_token="tok")
    decrypted = SimpleNamespace(source=_decrypted_inner())
    fake_client = _fake_nio_client(decrypt_result=decrypted)

    async def fake_get_client():
        return fake_client

    monkeypatch.setattr(session, "get_client", fake_get_client)
    plain = asyncio.run(session.decrypt_room_event(_encrypted_wrapper(), "!r:x"))
    assert plain["type"] == "m.room.message"
    assert plain["content"]["file"]["url"] == "mxc://hs/encrypted-media"


def test_decrypt_room_event_maps_unknown_session(monkeypatch):
    from nio.exceptions import EncryptionError as NioEncryptionError

    session = MatrixE2eeSession(homeserver_url="https://hs.example", access_token="tok")
    fake_client = _fake_nio_client(
        decrypt_error=NioEncryptionError(
            "Error decrypting megolm event, no session found with session id S1 for room !r:x"
        )
    )

    async def fake_get_client():
        return fake_client

    monkeypatch.setattr(session, "get_client", fake_get_client)
    with pytest.raises(MatrixE2eeError) as exc:
        asyncio.run(session.decrypt_room_event(_encrypted_wrapper(), "!r:x"))
    assert exc.value.reason == "unknown_session"


def test_decrypt_room_event_rejects_bad_event(monkeypatch):
    import nio

    session = MatrixE2eeSession(homeserver_url="https://hs.example", access_token="tok")
    fake_client = _fake_nio_client(decrypt_result=nio.UnknownBadEvent({"type": "x"}))

    async def fake_get_client():
        return fake_client

    monkeypatch.setattr(session, "get_client", fake_get_client)
    with pytest.raises(MatrixE2eeError):
        asyncio.run(session.decrypt_room_event(_encrypted_wrapper(), "!r:x"))


def test_megolmevent_from_dict_parses_wrapper():
    """matrix_e2ee.decrypt_room_event 依赖的 MegolmEvent.from_dict 能解析原始包装事件。"""
    import nio

    wrapper = _encrypted_wrapper()
    wrapper["room_id"] = "!r:x"
    megolm = nio.MegolmEvent.from_dict(wrapper)
    assert isinstance(megolm, nio.MegolmEvent)
    assert megolm.session_id == "session-1"
    assert megolm.room_id == "!r:x"


# ── 加密媒体解密（真实 SDK round-trip）──

def test_decrypt_media_payload_roundtrip_with_real_sdk():
    from nio.crypto import encrypt_attachment

    plaintext = b"OPS-PACKAGE-BYTES-\xf0\x9f\x93\xa6"
    ciphertext, file_dict = encrypt_attachment(plaintext)
    session = MatrixE2eeSession(homeserver_url="https://hs.example", access_token="tok")
    out = asyncio.run(session.decrypt_media_payload(ciphertext, file_dict))
    assert out == plaintext


def test_decrypt_media_payload_missing_fields():
    session = MatrixE2eeSession(homeserver_url="https://hs.example", access_token="tok")
    with pytest.raises(MatrixE2eeError) as exc:
        asyncio.run(session.decrypt_media_payload(b"x", {"iv": "iv"}))
    assert exc.value.reason == "media_decrypt_failed"


def test_decrypt_media_payload_corrupt_ciphertext():
    from nio.crypto import encrypt_attachment

    _, file_dict = encrypt_attachment(b"data")
    session = MatrixE2eeSession(homeserver_url="https://hs.example", access_token="tok")
    with pytest.raises(MatrixE2eeError) as exc:
        asyncio.run(session.decrypt_media_payload(b"corrupted-bytes", file_dict))
    assert exc.value.reason == "media_decrypt_failed"


# ── pull_media_bytes ──

def test_pull_media_bytes_plain_event_uses_download_media():
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    client.download_media = lambda mxc: (b"PLAIN-PKG", "plain.tar.gz")
    event = MatrixMediaEvent(
        event_id="$p",
        sender="@a:x",
        origin_server_ts=int(time.time() * 1000),
        msgtype="m.file",
        filename="plain.tar.gz",
        mxc_url="mxc://hs/plain",
    )
    data, name = asyncio.run(pull_media_bytes(client, event))
    assert data == b"PLAIN-PKG"
    assert name == "plain.tar.gz"


def test_pull_media_bytes_encrypted_event_full_chain(monkeypatch):
    from nio.crypto import encrypt_attachment

    plaintext = b"E2EE-PACKAGE"
    ciphertext, file_dict = encrypt_attachment(plaintext)
    # 真实事件中 mxc 地址由客户端上传后写入 content.file.url
    file_dict = {"url": "mxc://hs/enc", **file_dict}

    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    client.fetch_media_ciphertext = lambda mxc: (ciphertext, "secret.tar.gz")
    event = MatrixMediaEvent(
        event_id="$e",
        sender="@a:x",
        origin_server_ts=int(time.time() * 1000),
        msgtype="m.file",
        filename="secret.tar.gz",
        mxc_url="",
        encrypted=True,
        file_dict=file_dict,
    )
    data, name = asyncio.run(pull_media_bytes(client, event))
    assert data == plaintext
    assert name == "secret.tar.gz"


def test_pull_media_bytes_encrypted_requires_file_url():
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    event = MatrixMediaEvent(
        event_id="$e",
        sender="@a:x",
        origin_server_ts=int(time.time() * 1000),
        msgtype="m.file",
        filename="x.tar.gz",
        mxc_url="",
        encrypted=True,
        file_dict={},
    )
    with pytest.raises(MatrixE2eeError) as exc:
        asyncio.run(pull_media_bytes(client, event))
    assert exc.value.reason == "media_decrypt_failed"


# ── API 集成：E2EE 就绪时的部署链路 ──

def test_api_deploy_encrypted_happy_path(monkeypatch):
    """E2EE 就绪时加密媒体走「异步筛选 + pull_media_bytes」完整链路。"""
    from app.api.matrix import _matrix_deploy_handler, matrix_deploy_core

    class FakeRequest:
        def __init__(self, body):
            self._body = body
            self.state = SimpleNamespace()
            self.client = SimpleNamespace(host="127.0.0.1")

        async def json(self):
            return self._body

        def headers(self):
            h = {}
            return SimpleNamespace(get=lambda key, default="": h.get(key, default))

    def fake_user(r, d):
        return {"username": "tester", "role": "admin", "is_admin": True, "can_deploy": True}

    monkeypatch.setattr("app.api.matrix._require_matrix_deploy_user", fake_user)
    monkeypatch.setattr("app.api.matrix._enforce_matrix_deploy_env", lambda user, env: None)

    async def fake_build_decryptor(*, room_id, homeserver_url="", access_token=""):
        async def decryptor(raw_event):
            return _decrypted_inner(event_id=raw_event["event_id"])

        return decryptor

    monkeypatch.setattr("app.api.matrix.build_room_event_decryptor", fake_build_decryptor)

    now_ms = int(time.time() * 1000)
    encrypted_event = MatrixMediaEvent(
        event_id="$enc1",
        sender="@alice:example.org",
        origin_server_ts=now_ms - 60_000,
        msgtype="m.file",
        filename="secret-pkg.tar.gz",
        mxc_url="",
        encrypted=True,
        file_dict={"url": "mxc://hs/encrypted-media"},
    )

    class FakeClient:
        configured = True
        media_msgtypes = ["m.file", "m.image", "m.video", "m.audio"]

        async def find_latest_media_event_async(self, room_id, *, sender="", minutes=None,
                                                filename_hint="", limit=50, event_decryptor=None):
            assert event_decryptor is not None
            return encrypted_event

        def debug_recent_events(self, *a, **k):
            return {}

    monkeypatch.setattr("app.api.matrix._client", lambda *a, **k: FakeClient())

    async def fake_pull(client, event, **conn):
        assert event.encrypted is True
        assert conn["access_token"] == "syt_secret"
        return b"DECRYPTED-PACKAGE", "secret-pkg.tar.gz"

    monkeypatch.setattr("app.api.matrix.pull_media_bytes", fake_pull)
    monkeypatch.setattr(
        "app.api.matrix.save_package_fileobj",
        lambda db, filename, fileobj, system="", service="", uploaded_by="",
        overwrite=False, source_context=None, source_message_key=None:
        {"package_name": filename, "name": filename, "sha256": "c" * 64},
    )

    async def fake_queue(user, data, db):
        assert data["file_name"] == "secret-pkg.tar.gz"
        return {"success": True, "data": {"task_id": "t9"}}

    monkeypatch.setattr("app.api.matrix.queue_deploy_v2", fake_queue)
    monkeypatch.setattr("app.api.matrix.audit", lambda *a, **k: None)

    body = {
        "env": "test",
        "service": "api",
        "matrix": {
            "roomId": "!room:example.org",
            "sender": "@alice:example.org",
            "accessToken": "syt_secret",
        },
    }
    result = asyncio.run(_matrix_deploy_handler(FakeRequest(body), None))
    assert result["success"] is True
    assert result["data"]["task_id"] == "t9"
    assert result["data"]["matrix"]["encrypted"] is True
    assert result["data"]["matrix"]["media_event_id"] == "$enc1"


# ── MCP 工具集成：pull_attachment 在 E2EE 未配置时保持契约 ──

def test_tool_pull_attachment_encrypted_contract_kept(monkeypatch):
    """未配置 E2EE 时加密包仍返回 400 + "E2EE"（既有 Agent 契约）。"""
    from app.services.tool_adapters import matrix_tools as mt

    encrypted = MatrixMediaEvent(
        event_id="$e2ee",
        sender="@alice:example.org",
        origin_server_ts=int(time.time() * 1000) - 60_000,
        msgtype="m.file",
        filename="secret.tar.gz",
        mxc_url="",
        encrypted=True,
    )

    class FakeClient:
        configured = True

        def find_latest_media_event(self, room_id, **kw):
            return encrypted

    monkeypatch.setattr(mt, "_matrix_client", lambda args=None: FakeClient())

    ctx = SimpleNamespace(
        username="tester",
        token_owner="tester",
        role="admin",
        is_admin=True,
        can_deploy=True,
        allow_prod=False,
        auth_type="session",
        bound_room_ids=[],
    )
    with pytest.raises(HTTPException) as exc:
        mt.pull_attachment(
            {"room_id": "!room:example.org", "sender": "@alice:example.org"}, ctx, None
        )
    assert exc.value.status_code == 400
    assert "E2EE" in str(exc.value.detail)


def test_debug_recent_events_counts_encrypted_wrappers(monkeypatch):
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    chunk = [
        _encrypted_wrapper("$e1"),
        _encrypted_wrapper("$e2", sender="@bob:example.org"),
        {
            "type": "m.room.message",
            "event_id": "$t1",
            "sender": "@alice:example.org",
            "origin_server_ts": int(time.time() * 1000),
            "content": {"msgtype": "m.text", "body": "hello"},
        },
    ]
    client.fetch_room_messages = lambda room_id, limit=50: chunk
    info = client.debug_recent_events("!r:x")
    assert info["encrypted_events"] == 2
    assert info["encrypted_in_window"] == 2
    assert info["msgtype_distribution"] == {"m.text": 1}
    assert "加密房间" in info["hint"]

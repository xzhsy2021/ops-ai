"""Matrix 部署包对接：MatrixClient 单元测试。

覆盖：
- mxc:// 解析
- 拉房间事件（fetch_room_messages）→ 筛选媒体事件（sender/msgtype/时间窗口/文件名）
- 下载媒体（download_media）
- E2EE 加密事件识别
"""
import asyncio
import time

import httpx
import pytest

from app.services.matrix_client import (
    MatrixClient,
    MatrixClientError,
    MatrixMediaEvent,
    parse_mxc_url,
)


# ── mxc 解析 ──

def test_parse_mxc_url_valid():
    assert parse_mxc_url("mxc://example.org/AbCdEf123456") == ("example.org", "AbCdEf123456")


def test_parse_mxc_url_invalid():
    assert parse_mxc_url("") is None
    assert parse_mxc_url("https://example.org/file") is None
    assert parse_mxc_url("mxc://only-server") is None


# ── 拉房间事件 + 筛选 ──

def _event(event_id, sender, msgtype, filename, url, ts):
    return {
        "type": "m.room.message",
        "event_id": event_id,
        "sender": sender,
        "origin_server_ts": ts,
        "content": {"msgtype": msgtype, "body": filename, "url": url},
    }


def test_list_media_events_filters_sender_msgtype_window_filename(monkeypatch):
    now_ms = int(time.time() * 1000)
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")

    chunk = [
        _event("$1", "@alice:example.org", "m.file", "crypto-frontend-v1.tar.gz", "mxc://hs/aaa", now_ms - 60_000),
        _event("$2", "@bob:example.org", "m.file", "other.tar.gz", "mxc://hs/bbb", now_ms - 120_000),
        _event("$3", "@alice:example.org", "m.text", "hello", "", now_ms - 180_000),
        _event("$4", "@alice:example.org", "m.file", "crypto-frontend-v2.tar.gz", "mxc://hs/ccc", now_ms - 300_000),
        _event("$5", "@alice:example.org", "m.file", "crypto-frontend-old.tar.gz", "mxc://hs/ddd", now_ms - 20 * 60_000),
    ]
    monkeypatch.setattr(client, "fetch_room_messages", lambda room_id, limit=50: chunk)

    # 默认窗口 15 分钟，过滤掉 $5
    events = client.list_media_events("!room:example.org", sender="@alice:example.org")
    assert [e.event_id for e in events] == ["$1", "$4"]

    # 文件名 hint
    events = client.list_media_events("!room:example.org", sender="@alice:example.org", filename_hint="v2")
    assert [e.event_id for e in events] == ["$4"]

    # 最新一条
    latest = client.find_latest_media_event("!room:example.org", sender="@alice:example.org")
    assert latest.event_id == "$1"
    assert latest.filename == "crypto-frontend-v1.tar.gz"


def test_list_media_events_detects_e2ee_encrypted():
    now_ms = int(time.time() * 1000)
    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    encrypted = {
        "type": "m.room.message",
        "event_id": "$e2ee",
        "sender": "@alice:example.org",
        "origin_server_ts": now_ms - 60_000,
        "content": {
            "msgtype": "m.file",
            "body": "secret.tar.gz",
            "file": {"url": "mxc://hs/enc", "key": "k", "iv": "iv"},
        },
    }
    client.fetch_room_messages = lambda room_id, limit=50: [encrypted]
    events = client.list_media_events("!room:example.org")
    assert len(events) == 1
    assert events[0].encrypted is True
    assert events[0].mxc_url == ""


def test_fetch_room_messages_uses_token_and_parses_chunk(monkeypatch):
    captured = {}

    def fake_request(method, path, headers=None, params=None, timeout=None):
        captured["method"] = method
        captured["path"] = path
        captured["headers"] = headers
        captured["params"] = params
        return httpx.Response(200, json={"chunk": [{"event_id": "$x"}], "start": "t1", "end": "t2"})

    client = MatrixClient(homeserver_url="https://hs.example", access_token="secret")
    monkeypatch.setattr(client, "_request", fake_request)
    chunk = client.fetch_room_messages("!room:example.org", limit=42)
    assert chunk == [{"event_id": "$x"}]
    assert captured["path"] == "/_matrix/client/v3/rooms/%21room%3Aexample.org/messages"
    assert captured["params"]["dir"] == "b"
    assert captured["params"]["limit"] == 42
    # _request 内部应注入 Bearer 头
    assert client._headers() == {"Authorization": "Bearer secret"}


def test_fetch_room_messages_raises_on_error(monkeypatch):
    def fake_request(method, path, headers=None, params=None, timeout=None):
        raise MatrixClientError("Matrix 请求失败: 403 forbidden")

    client = MatrixClient(homeserver_url="https://hs.example", access_token="secret")
    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(MatrixClientError):
        client.fetch_room_messages("!room:example.org")


def test_download_media(monkeypatch):
    """明文下载：优先认证媒体端点 v1，成功即返回。"""
    class FakeResponse:
        content = b"PACKAGE-BYTES"
        headers = {"content-disposition": 'attachment; filename="crypto-frontend.tar.gz"'}

    captured = {}

    def fake_request(method, path, headers=None, params=None, timeout=None):
        captured["path"] = path
        return FakeResponse()

    client = MatrixClient(homeserver_url="https://hs.example", access_token="secret")
    monkeypatch.setattr(client, "_request", fake_request)
    data, filename = client.download_media("mxc://hs.example/AbCdEf")
    assert data == b"PACKAGE-BYTES"
    assert filename == "crypto-frontend.tar.gz"
    assert captured["path"] == "/_matrix/client/v1/media/download/hs.example/AbCdEf"


def test_download_media_falls_back_when_endpoint_unrecognized(monkeypatch):
    """v1 端点不存在（M_UNRECOGNIZED）时按链回退到可用端点。"""
    class FakeResponse:
        content = b"BYTES"
        headers = {}

    attempted = []

    def fake_request(method, path, headers=None, params=None, timeout=None):
        attempted.append(path)
        if path.startswith("/_matrix/client/v1/"):
            raise MatrixClientError(
                "Matrix GET /_matrix/client/v1/media/download/hs/A -> HTTP 404: "
                '{"errcode":"M_UNRECOGNIZED","error":"Unrecognized request"}'
            )
        return FakeResponse()

    client = MatrixClient(homeserver_url="https://hs.example", access_token="secret")
    monkeypatch.setattr(client, "_request", fake_request)
    data, _ = client.download_media("mxc://hs/A")
    assert data == b"BYTES"
    assert attempted[0].startswith("/_matrix/client/v1/")
    assert attempted[1].startswith("/_matrix/client/v3/")


def test_download_media_raises_on_real_media_miss(monkeypatch):
    """媒体本身不存在（M_NOT_FOUND）不回退端点，立即抛出真实原因。"""
    def fake_request(method, path, headers=None, params=None, timeout=None):
        raise MatrixClientError(
            f"Matrix GET {path} -> HTTP 404: "
            '{"errcode":"M_NOT_FOUND","error":"Media not found"}'
        )

    client = MatrixClient(homeserver_url="https://hs.example", access_token="secret")
    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(MatrixClientError) as exc:
        client.download_media("mxc://hs/gone")
    assert "M_NOT_FOUND" in str(exc.value)


def test_download_media_rejects_non_mxc(monkeypatch):
    client = MatrixClient(homeserver_url="https://hs.example", access_token="secret")
    with pytest.raises(MatrixClientError):
        client.download_media("https://example.org/file")


def test_client_configured_flag():
    assert MatrixClient(homeserver_url="", access_token="").configured is False
    assert MatrixClient(homeserver_url="https://hs.example", access_token="tok").configured is True


def test_debug_recent_events_returns_msgtype_distribution(monkeypatch):
    """debug_recent_events 应返回 msgtype 分布、文字含文件名提示、sender 分布。"""
    now_ms = int(time.time() * 1000)

    def fake_fetch(room_id, *, limit=50, direction="b", from_token=""):
        return [
            {"type": "m.room.message", "sender": "@alice:hs",
             "origin_server_ts": now_ms - 60_000,
             "content": {"msgtype": "m.text", "body": "上传了 crypto-trader-web.tar.gz 请部署"}},
            {"type": "m.room.message", "sender": "@alice:hs",
             "origin_server_ts": now_ms - 30_000,
             "content": {"msgtype": "m.text", "body": "再确认下"}},
            {"type": "m.room.message", "sender": "@bob:hs",
             "origin_server_ts": now_ms - 5_000,
             "content": {"msgtype": "m.text", "body": "ok"}},
            {"type": "m.room.message", "sender": "@alice:hs",
             "origin_server_ts": now_ms - 70 * 60_000,  # 超出 15 分钟窗口
             "content": {"msgtype": "m.file", "body": "old.tar.gz", "url": "mxc://hs/x"}},
        ]

    client = MatrixClient(homeserver_url="https://hs.example", access_token="tok")
    monkeypatch.setattr(client, "fetch_room_messages", fake_fetch)
    debug = client.debug_recent_events("!room:hs", sender="@alice:hs", minutes=15, limit=50)
    assert debug["msgtype_distribution"] == {"m.text": 2, "m.file": 1}
    assert debug["in_window"] == 2
    assert debug["out_of_window"] == 1
    assert len(debug["text_with_filename_hint"]) == 1
    assert "crypto-trader-web.tar.gz" in debug["text_with_filename_hint"][0]["body"]

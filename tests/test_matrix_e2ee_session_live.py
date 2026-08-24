"""Matrix E2EE 会话生命周期测试：真实 matrix-nio 栈 + 本地桩 homeserver。

验证（不依赖外部网络，仅 loopback）：
- restore_login + load_store 首次运行自动生成 Olm 账号并写入持久化 crypto store；
- crypto store 目录下生成 SQLite 文件（<user>_<device>.db 命名约定）；
- 设备身份密钥（curve25519/ed25519）真实生成；
- ensure_synced 全量 sync + keys_upload 对桩服务可用；
- 重启（新建会话、同 store）后账号从 store 恢复（不重新生成）。
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.services.matrix_e2ee import MatrixE2eeSession

USER_ID = "@opsbot:e2ee.test"
DEVICE_ID = "TESTDEV"

SYNC_BODY = {
    "next_batch": "s1",
    "rooms": {"join": {}, "invite": {}, "leave": {}},
    "presence": {"events": []},
    "account_data": {"events": []},
    "to_device": {"events": []},
    "device_lists": {"changed": [], "left": []},
    "device_one_time_keys_count": {"signed_curve25519": 50},
    "device_unused_fallback_key_types": [],
}


class _StubHomeserver(BaseHTTPRequestHandler):
    """最小 Matrix Client-Server API 桩：whoami / sync / keys upload+query。"""

    def _json(self, payload, code=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/_matrix/client/v3/account/whoami"):
            self._json({"user_id": USER_ID})
        elif self.path.startswith("/_matrix/client/v3/sync"):
            self._json(SYNC_BODY)
        else:
            self._json({"errcode": "M_UNRECOGNIZED"}, code=404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path.startswith("/_matrix/client/v3/keys/upload"):
            self._json({"one_time_key_counts": {"signed_curve25519": 50}})
        elif self.path.startswith("/_matrix/client/v3/keys/query"):
            self._json({"device_keys": {}})
        elif self.path.startswith("/_matrix/client/v3/keys/claim"):
            self._json({"one_time_keys": {}})
        else:
            self._json({})

    def log_message(self, *args):  # 静默访问日志
        pass


@pytest.fixture()
def stub_homeserver():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHomeserver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _session(homeserver: str, store_path: str) -> MatrixE2eeSession:
    return MatrixE2eeSession(
        homeserver_url=homeserver,
        access_token="syt_stub_token",
        user_id=USER_ID,
        device_id=DEVICE_ID,
        store_path=store_path,
        sync_timeout_ms=200,
    )


def test_first_run_creates_crypto_store_with_olm_account(tmp_path, stub_homeserver):
    store_dir = tmp_path / "crypto_store"

    async def _run():
        session = _session(stub_homeserver, str(store_dir))
        client = await session.get_client()
        identity_keys = dict(client.olm.account.identity_keys)
        try:
            await session.ensure_synced(force=True)
            last_sync = session._last_sync_ts
        finally:
            await session.aclose()
        return identity_keys, last_sync

    identity_keys, last_sync = asyncio.run(_run())

    # 1. crypto store SQLite 文件按净化命名生成（含设备 ID、不含非法字符）
    db_files = [p.name for p in store_dir.iterdir() if p.name.endswith(".db")]
    assert any("TESTDEV" in name and ":" not in name for name in db_files), db_files

    # 2. Olm 账号已创建且设备身份密钥真实存在
    assert "curve25519" in identity_keys
    assert "ed25519" in identity_keys

    # 3. 增量 sync 已成功并记录时间戳
    assert last_sync > 0


def test_restart_restores_account_from_store(tmp_path, stub_homeserver):
    store_dir = tmp_path / "crypto_store"

    async def _first():
        session = _session(stub_homeserver, str(store_dir))
        client = await session.get_client()
        keys = dict(client.olm.account.identity_keys)
        await session.aclose()
        return keys

    first_keys = asyncio.run(_first())

    # 模拟进程重启：全新会话对象、同一 crypto store
    async def _second():
        session = _session(stub_homeserver, str(store_dir))
        client = await session.get_client()
        keys = dict(client.olm.account.identity_keys)
        loaded_sync_token = getattr(client, "loaded_sync_token", "")
        await session.aclose()
        return keys, loaded_sync_token

    second_keys, sync_token = asyncio.run(_second())
    assert second_keys["curve25519"] == first_keys["curve25519"]
    assert second_keys["ed25519"] == first_keys["ed25519"]


def test_status_reports_session_state(tmp_path, stub_homeserver):
    session = _session(stub_homeserver, str(tmp_path / "crypto_store"))

    async def _run():
        try:
            await session.get_client()
            status = session.status()
        finally:
            await session.aclose()
        return status

    status = asyncio.run(_run())
    assert status["sdk_available"] is True
    assert status["resolved_user_id"] == USER_ID
    assert status["olm_account_ready"] is True

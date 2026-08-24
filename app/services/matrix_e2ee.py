"""Matrix E2EE 会话管理：接入 Matrix E2EE SDK（matrix-nio[e2e] / vodozemac）。

配置（见 app/core/config.py）：
- MATRIX_HOMESERVER_URL / MATRIX_ACCESS_TOKEN   Bot 账号连接信息（复用）
- MATRIX_USER_ID                                @bot:example.org（留空自动 whoami 解析）
- MATRIX_DEVICE_ID                              设备 ID，必须稳定（默认 OPS-AI-BOT）
- MATRIX_CRYPTO_STORE_PATH                      E2EE crypto store 目录
                                                （nio SqliteStore 在其下建 <user>_<device>.db）
- MATRIX_E2EE_ENABLED                           默认 true；未装 SDK 自动降级
- MATRIX_E2EE_SYNC_TIMEOUT_MS                   解密前增量 sync 的长轮询超时

Crypto store 持久化两类密钥（跨进程/重启保留）：
1. Olm 账号密钥（Bot 设备身份 curve25519/ed25519 + one-time keys）
2. Megolm 入站会话（m.room_key to-device 事件带来的房间解密密钥）

注意：room key 经 to-device 通道只投递一次。首次接入 E2EE 后，历史消息的密钥
不会补发 —— 无法解密的旧事件会返回明确错误（unknown_session），需用户重发包，
或在其他端导出 room keys 后用 nio 导入。

事件循环：FastAPI 异步端点跑在 uvicorn loop 上；MCP 同步工具经
_run_coroutine_sync 桥接（无 loop 直接 asyncio.run；已有 loop 则转投
独立线程私有 loop 执行）。会话按运行中 loop 的弱引用缓存（WeakKeyDictionary）：
主 loop 长期复用，线程桥的临时 loop 销毁后条目自动蒸发。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import weakref
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

import httpx

from app.core.config import (
    MATRIX_ACCESS_TOKEN,
    MATRIX_CRYPTO_STORE_PATH,
    MATRIX_DEVICE_ID,
    MATRIX_E2EE_ENABLED,
    MATRIX_E2EE_SYNC_TIMEOUT_MS,
    MATRIX_HOMESERVER_URL,
    MATRIX_HTTP_TIMEOUT,
    MATRIX_USER_ID,
)
from app.services.matrix_client import (
    MatrixClient,
    MatrixClientError,
    MatrixMediaEvent,
)

logger = logging.getLogger(__name__)

# ── SDK 可用性探测（未安装 matrix-nio[e2e] 时优雅降级）──

SDK_IMPORT_ERROR = ""
try:  # pragma: no cover - 环境相关
    import nio
    from nio.crypto import decrypt_attachment
    from nio.exceptions import EncryptionError as NioEncryptionError
    from nio.store import SqliteStore

    SDK_AVAILABLE = True
except Exception as _exc:  # pragma: no cover - 未安装依赖时触发
    SDK_AVAILABLE = False
    SDK_IMPORT_ERROR = str(_exc)
    nio = None  # type: ignore[assignment]
    decrypt_attachment = None  # type: ignore[assignment]
    NioEncryptionError = Exception  # type: ignore[assignment,misc]
    SqliteStore = None  # type: ignore[assignment]

SYNC_DEBOUNCE_SECONDS = 5.0


def _safe_store_name(user_id: str, device_id: str) -> str:
    """crypto store SQLite 文件名：跨平台安全（无 ':' 等非法字符）。"""
    def _clean(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value).strip("_")

    return f"{_clean(user_id) or 'bot'}_{_clean(device_id) or 'device'}.db"


class MatrixE2eeError(RuntimeError):
    """E2EE 配置缺失或解密失败。

    reason 取值：disabled / sdk_missing / not_configured / auth_failed /
    sync_failed / unknown_session / decrypt_failed / media_decrypt_failed
    """

    def __init__(self, message: str, reason: str = "decrypt_failed", hint: str = ""):
        super().__init__(message)
        self.reason = reason
        self.hint = hint


def e2ee_config() -> Dict[str, Any]:
    """当前 E2EE 相关配置快照（不发起网络请求）。"""
    homeserver_url = MATRIX_HOMESERVER_URL.rstrip("/")
    return {
        "enabled": bool(MATRIX_E2EE_ENABLED),
        "sdk_available": SDK_AVAILABLE,
        "sdk": "matrix-nio[e2e]" if SDK_AVAILABLE else "",
        "homeserver_url": homeserver_url,
        "has_access_token": bool(MATRIX_ACCESS_TOKEN),
        "user_id": MATRIX_USER_ID or "(auto via whoami)",
        "device_id": MATRIX_DEVICE_ID,
        "crypto_store_path": MATRIX_CRYPTO_STORE_PATH,
        "sync_timeout_ms": MATRIX_E2EE_SYNC_TIMEOUT_MS,
        "configured": bool(
            MATRIX_E2EE_ENABLED
            and SDK_AVAILABLE
            and homeserver_url
            and MATRIX_ACCESS_TOKEN
        ),
        "sdk_import_error": SDK_IMPORT_ERROR if not SDK_AVAILABLE else "",
    }


def e2ee_unavailable_detail(err: MatrixE2eeError | None = None) -> str:
    """生成面向调用方（Agent/用户）的可执行提示。"""
    cfg = e2ee_config()
    if not cfg["enabled"]:
        return (
            "E2EE 解密已通过 MATRIX_E2EE_ENABLED=false 关闭；"
            "如需解密加密房间请在 .env 中设置 MATRIX_E2EE_ENABLED=true 并重启服务"
        )
    if not cfg["sdk_available"]:
        detail = "Matrix E2EE SDK 未安装（需要 matrix-nio[e2e]，vodozemac 加密后端）"
        if cfg.get("sdk_import_error"):
            detail += f"：{cfg['sdk_import_error']}"
        detail += "。请安装依赖后重启 OPS 服务"
        return detail
    if not cfg["homeserver_url"] or not cfg["has_access_token"]:
        return (
            "E2EE 未配置完整：请设置 MATRIX_HOMESERVER_URL 与 MATRIX_ACCESS_TOKEN，"
            f"并确认 crypto store 目录可写（{cfg['crypto_store_path']}）"
        )
    base = "E2EE 解密失败"
    if err is not None:
        base = f"E2EE 解密失败（{err.reason}）：{err}"
    if err is not None and err.reason == "unknown_session":
        base += (
            "；缺少该房间/会话的历史 room key（to-device 密钥只投递一次）。"
            "请让发送者重新上传部署包，或将已有端的 room keys 导出到 "
            f"{cfg['crypto_store_path']} 所在 crypto store 后重试"
        )
    return base


class MatrixE2eeSession:
    """单个 (homeserver, user, device, store) 维度的 nio AsyncClient 封装。

    生命周期：懒构建 → restore_login + load_store（首次运行自动生成 Olm
    账号并写入 crypto store）→ 需要时增量 sync 拾取 m.room_key。
    """

    def __init__(
        self,
        *,
        homeserver_url: str,
        access_token: str,
        user_id: str = "",
        device_id: str = "",
        store_path: str = "",
        sync_timeout_ms: int = 8000,
        http_timeout: float = 30.0,
    ):
        self.homeserver_url = homeserver_url.rstrip("/")
        self.access_token = access_token
        self.user_id = user_id
        self.device_id = device_id or MATRIX_DEVICE_ID
        self.store_path = store_path or MATRIX_CRYPTO_STORE_PATH
        self.sync_timeout_ms = int(sync_timeout_ms)
        self.http_timeout = float(http_timeout)
        self._client: Any = None
        self._build_lock = threading.Lock()
        self._last_sync_ts = 0.0

    # ── 构建 ──

    async def _resolve_user_id(self) -> str:
        """MATRIX_USER_ID 未配置时通过 /account/whoami 解析。"""
        if self.user_id:
            return self.user_id
        url = f"{self.homeserver_url}/_matrix/client/v3/account/whoami"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as http:
                resp = await http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise MatrixE2eeError(
                f"Matrix whoami 请求失败: {exc}", reason="auth_failed"
            ) from exc
        data = {}
        try:
            data = resp.json() or {}
        except ValueError:
            pass
        if resp.status_code >= 400:
            raise MatrixE2eeError(
                f"Matrix whoami -> HTTP {resp.status_code}: {str(data)[:200]}"
                "；请检查 MATRIX_ACCESS_TOKEN 是否有效",
                reason="auth_failed",
            )
        resolved = str(data.get("user_id") or "").strip()
        if not resolved:
            raise MatrixE2eeError(
                "Matrix whoami 未返回 user_id，无法初始化 E2EE crypto store",
                reason="auth_failed",
            )
        self.user_id = resolved
        logger.info("Matrix E2EE: resolved bot user_id %s via whoami", resolved)
        return resolved

    async def get_client(self) -> Any:
        """构建（或复用）已加载 crypto store 的 nio AsyncClient。"""
        if self._client is not None:
            return self._client
        with self._build_lock:
            if self._client is not None:
                return self._client
            if not SDK_AVAILABLE:  # pragma: no cover - 前置校验已拦
                raise MatrixE2eeError(e2ee_unavailable_detail(), reason="sdk_missing")
            user_id = await self._resolve_user_id()
            # nio SqliteStore 不会自建父目录，这里确保 crypto store 目录存在
            try:
                os.makedirs(self.store_path, exist_ok=True)
            except OSError as exc:
                raise MatrixE2eeError(
                    f"无法创建 crypto store 目录（{self.store_path}）: {exc}",
                    reason="not_configured",
                ) from exc
            # 显式指定 SQLite 文件名：nio 默认用 "<user_id>_<device_id>.db"，
            # 其中 user_id 含 ":"，在 Windows/NTFS 上会退化为 ADS 流、FAT 上直接失败。
            store_name = _safe_store_name(user_id, self.device_id)
            config = nio.AsyncClientConfig(
                store=SqliteStore,
                store_sync_tokens=True,     # next_batch 持久化进 store
                encryption_enabled=True,
                store_name=store_name,
            )
            client = nio.AsyncClient(
                self.homeserver_url,
                user=user_id,
                device_id=self.device_id,
                store_path=self.store_path,
                config=config,
            )
            # Bot 场景：向未验证设备分发会话密钥不阻断（接收/解密不受影响）
            client.ignore_unverified_devices = True
            # 扫描历史消息时缺 room key 的告警属常态（密钥不补发），降噪到 ERROR
            logging.getLogger("nio").setLevel(logging.ERROR)
            client.restore_login(user_id, self.device_id, self.access_token)
            try:
                client.load_store()
            except Exception as exc:
                raise MatrixE2eeError(
                    f"加载 E2EE crypto store 失败（{self.store_path}）: {exc}；"
                    "请检查目录权限，或备份后清空该目录让 Bot 重新生成设备密钥"
                    "（清空会导致无法解密历史消息）",
                    reason="not_configured",
                ) from exc
            has_account = bool(client.olm is not None)
            first_run = has_account and not client.store.load_sync_token()
            self._client = client
            self._first_run = first_run
            logger.info(
                "Matrix E2EE session ready: user=%s device=%s store=%s new_account=%s",
                user_id, self.device_id, self.store_path, first_run,
            )
            return client

    # ── 同步 ──

    async def ensure_synced(self, *, force: bool = False) -> None:
        """增量 sync：拾取新到的 m.room_key / 设备列表；带去抖。

        full_state 仅在 crypto store 首次运行（新 Olm 账号）时拉取一次；
        之后一律增量 —— 账号房间较多时全量同步可达数十秒，必须避免。
        """
        client = await self.get_client()
        now = time.monotonic()
        if not force and self._last_sync_ts and now - self._last_sync_ts < SYNC_DEBOUNCE_SECONDS:
            return
        full_state = bool(getattr(self, "_first_run", False))
        try:
            resp = await client.sync(timeout=max(0, self.sync_timeout_ms), full_state=full_state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise MatrixE2eeError(f"Matrix sync 请求失败: {exc}", reason="sync_failed") from exc
        if isinstance(resp, nio.SyncResponse):
            if getattr(client, "should_upload_keys", False):
                try:
                    await client.keys_upload()
                except Exception as exc:  # 上传失败不阻断解密
                    logger.warning("Matrix E2EE keys_upload failed: %s", exc)
            if getattr(client, "should_query_keys", False):
                try:
                    await client.keys_query()
                except Exception as exc:
                    logger.warning("Matrix E2EE keys_query failed: %s", exc)
        else:
            message = getattr(resp, "message", "") or type(resp).__name__
            raise MatrixE2eeError(f"Matrix sync 失败: {message}", reason="sync_failed")
        self._last_sync_ts = time.monotonic()

    # ── 解密 ──

    async def decrypt_room_event(self, raw_event: Dict[str, Any], room_id: str) -> Dict[str, Any]:
        """解密一条 m.room.encrypted(Megolm) 事件，返回明文内层事件 dict。

        成功后返回的 dict 即原始 m.room.message 结构（type/content/sender 等），
        加密媒体位于 content.file（含 url/key/iv/hashes）。
        """
        client = await self.get_client()
        event_dict = {**raw_event, "room_id": room_id}
        megolm = nio.MegolmEvent.from_dict(event_dict)
        if not isinstance(megolm, nio.MegolmEvent):
            raise MatrixE2eeError(
                "事件不是有效的 Megolm 加密格式", reason="decrypt_failed"
            )
        try:
            decrypted = client.olm.decrypt_megolm_event(megolm, room_id)
        except NioEncryptionError as exc:
            text = str(exc) or "megolm decryption error"
            reason = "unknown_session" if "no session found" in text.lower() else "decrypt_failed"
            raise MatrixE2eeError(text, reason=reason) from exc
        if decrypted is None or isinstance(decrypted, (nio.BadEvent, nio.UnknownBadEvent)):
            raise MatrixE2eeError(
                "Megolm 解密结果无效（BadEvent）", reason="decrypt_failed"
            )
        source = getattr(decrypted, "source", None)
        if not isinstance(source, dict) or "content" not in source:
            raise MatrixE2eeError(
                "Megolm 解密结果缺少明文内容", reason="decrypt_failed"
            )
        return source

    async def decrypt_media_payload(self, ciphertext: bytes, file_dict: Dict[str, Any]) -> bytes:
        """按 Matrix 加密附件规范（AES-CTR + SHA256 校验）解密媒体字节。"""
        return _decrypt_attachment_bytes(ciphertext, file_dict)

    # ── 状态 ──

    def status(self) -> Dict[str, Any]:
        cfg = e2ee_config()
        client = self._client
        cfg.update(
            {
                "session_active": client is not None,
                "resolved_user_id": self.user_id or "",
                "store_loaded": bool(client is not None and getattr(client, "store", None)),
                "olm_account_ready": bool(client is not None and getattr(client, "olm", None)),
                "last_sync_monotonic": round(self._last_sync_ts, 3),
            }
        )
        return cfg

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # pragma: no cover
                pass
            self._client = None
            self._last_sync_ts = 0.0


# ── 会话缓存 ──
# 按事件循环弱引用缓存：uvicorn 主 loop 长期复用一个会话；
# MCP 同步桥（线程 + 私有 loop）的 loop 销毁后条目自动蒸发，不泄漏连接。

_LOOP_SESSIONS: "weakref.WeakKeyDictionary[Any, MatrixE2eeSession]" = weakref.WeakKeyDictionary()
_SESSIONS_LOCK = threading.Lock()


def reset_sessions() -> None:
    """清空会话缓存（测试与配置热更新用）。"""
    with _SESSIONS_LOCK:
        _LOOP_SESSIONS.clear()


async def aclose_loop_session() -> None:
    """关闭并驱逐绑定在当前事件循环上的会话。

    供 MCP 同步桥的临时线程 loop 在协程结束后调用，
    避免 aiohttp 连接未关闭；uvicorn 主 loop 的会话不受影响。
    """
    loop = asyncio.get_running_loop()
    with _SESSIONS_LOCK:
        session = _LOOP_SESSIONS.pop(loop, None)
    if session is not None:
        await session.aclose()


async def get_e2ee_session(
    *,
    homeserver_url: str = "",
    access_token: str = "",
) -> MatrixE2eeSession:
    """获取（并按 loop 缓存）E2EE 会话；配置不完整时抛 MatrixE2eeError。

    专用设备守卫：E2EE sync 只允许使用 .env 中配置的专用 Bot 凭据
    （MATRIX_HOMESERVER_URL + MATRIX_ACCESS_TOKEN）。调用方传入的
    homeserver/token（Agent 动态参数，可能是某个活跃 E2EE 客户端如 qclaw
    正在使用的设备凭据）一律拒绝用于解密 —— 共用同一 device 会导致：
    1) nio 首次运行重新生成设备密钥并上传，覆盖原设备的身份密钥；
    2) 两端共享同一条 to-device 队列，互相"偷走" m.room_key。
    任一发生都会静默破坏原客户端的加密能力。

    如确需对其他 homeserver 启用解密，可显式设置
    MATRIX_E2EE_ALLOW_ANY_DEVICE=true（自担风险），此时才接受传入凭据。
    """
    del homeserver_url, access_token  # 见 docstring：仅环境配置可用于 E2EE
    if not MATRIX_E2EE_ENABLED:
        raise MatrixE2eeError(e2ee_unavailable_detail(), reason="disabled")
    if not SDK_AVAILABLE:
        raise MatrixE2eeError(e2ee_unavailable_detail(), reason="sdk_missing")
    hs = MATRIX_HOMESERVER_URL.rstrip("/")
    token = MATRIX_ACCESS_TOKEN
    if not hs or not token:
        raise MatrixE2eeError(
            e2ee_unavailable_detail()
            + "。注意：请为 OPS 配置专用 Bot 设备（不要复用 qclaw 等活跃"
            " E2EE 客户端的 access token / device），否则会破坏其加密会话",
            reason="not_configured",
        )
    probe = MatrixE2eeSession(
        homeserver_url=hs,
        access_token=token,
        sync_timeout_ms=MATRIX_E2EE_SYNC_TIMEOUT_MS,
        http_timeout=MATRIX_HTTP_TIMEOUT,
    )
    loop = asyncio.get_running_loop()
    with _SESSIONS_LOCK:
        cached = _LOOP_SESSIONS.get(loop)
        # 注意：不要拿 user_id 参与比较 —— 会话创建时 user_id 为空，
        # whoami 解析后被原地更新，会导致缓存永远未命中、反复新建客户端。
        if cached is not None and (
            cached.homeserver_url,
            cached.device_id,
            cached.store_path,
        ) == (
            probe.homeserver_url,
            probe.device_id,
            probe.store_path,
        ):
            return cached
        _LOOP_SESSIONS[loop] = probe
        return probe


# ── 面向上层（API / MCP 工具）的组合能力 ──


async def build_room_event_decryptor(
    *,
    room_id: str,
    homeserver_url: str = "",
    access_token: str = "",
) -> Callable[[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]:
    """构造 list_media_events 用的 m.room.encrypted 解密器。

    返回 async callable(raw_event) -> 明文事件 dict。

    会话解析是惰性的：解密器可能在「构建时的 loop」（MCP 同步桥的临时
    线程 loop）之外的另一个 loop（调用方 uvicorn 主 loop）上被消费，
    因此闭包不捕获会话对象，而是每次调用经 get_e2ee_session 按当前
    loop 解析（WeakKeyDictionary 缓存保证同 loop 复用、跨 loop 隔离）。
    构建阶段的首次 sync 仅用于提前暴露凭据/store 问题。
    """
    initial_session = await get_e2ee_session(homeserver_url=homeserver_url, access_token=access_token)
    # 增量校验（非 full_state）：提前暴露凭据/store 问题即可，避免慢全量同步
    await initial_session.ensure_synced()

    async def _decrypt(raw_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        session = await get_e2ee_session(homeserver_url=homeserver_url, access_token=access_token)
        try:
            await session.ensure_synced()
        except MatrixE2eeError as exc:
            logger.debug("Matrix E2EE pre-decrypt sync skipped: %s", exc)
        return await session.decrypt_room_event(raw_event, room_id)

    return _decrypt


async def pull_media_bytes(
    client: MatrixClient,
    event: MatrixMediaEvent,
    *,
    homeserver_url: str = "",
    access_token: str = "",
) -> Tuple[bytes, str]:
    """下载媒体；加密事件走「密文下载 + decrypt_attachment」链路。

    返回 (明文字节, 文件名)。文件名取不到时为空串，由调用方兜底。

    说明：加密媒体的对称密钥内嵌在事件 content.file 中（AES-CTR），
    解密不依赖 Olm 会话 / crypto store —— 只有解密 m.room.encrypted
    包装事件（拿到 content.file）才需要 build_room_event_decryptor。
    """
    if not event.encrypted:
        return client.download_media(event.mxc_url)
    file_dict = dict(event.file_dict or {})
    ciphertext_url = file_dict.get("url") or ""
    if not ciphertext_url:
        raise MatrixE2eeError(
            "加密媒体事件缺少 content.file.url（需先经 E2EE 解密器还原事件，"
            "或房间本身未加密却携带加密附件的异常情况）",
            reason="media_decrypt_failed",
        )
    try:
        ciphertext, disposition_name = client.fetch_media_ciphertext(ciphertext_url)
    except MatrixClientError as exc:
        raise MatrixE2eeError(f"下载加密媒体失败: {exc}", reason="media_decrypt_failed") from exc
    plaintext = _decrypt_attachment_bytes(ciphertext, file_dict)
    return plaintext, disposition_name or ""


def _decrypt_attachment_bytes(ciphertext: bytes, file_dict: Dict[str, Any]) -> bytes:
    """纯函数：按 content.file 字段解密媒体字节（不依赖会话/网络）。"""
    key = ((file_dict.get("key") or {}).get("k")) or ""
    iv = file_dict.get("iv") or ""
    sha256 = (file_dict.get("hashes") or {}).get("sha256") or ""
    if not (key and iv and sha256):
        raise MatrixE2eeError(
            "加密媒体缺少 key/iv/sha256 字段，无法解密",
            reason="media_decrypt_failed",
        )
    try:
        return decrypt_attachment(ciphertext, key=key, hash=sha256, iv=iv)
    except NioEncryptionError as exc:
        raise MatrixE2eeError(
            f"媒体解密失败（哈希校验不匹配或密钥错误）: {exc}",
            reason="media_decrypt_failed",
        ) from exc
    except Exception as exc:
        raise MatrixE2eeError(f"媒体解密失败: {exc}", reason="media_decrypt_failed") from exc


__all__ = [
    "MatrixE2eeError",
    "MatrixE2eeSession",
    "SDK_AVAILABLE",
    "build_room_event_decryptor",
    "e2ee_config",
    "e2ee_unavailable_detail",
    "get_e2ee_session",
    "pull_media_bytes",
    "reset_sessions",
]


def _ensure_store_dir() -> None:
    """确保 crypto store 目录存在（模块导入外的显式入口）。"""
    try:
        os.makedirs(MATRIX_CRYPTO_STORE_PATH, exist_ok=True)
    except OSError as exc:  # pragma: no cover
        logger.warning("Failed to create Matrix crypto store dir: %s", exc)


# 导入时确保目录存在，避免首次 load_store 因目录缺失失败
if MATRIX_E2EE_ENABLED and SDK_AVAILABLE:  # pragma: no branch
    _ensure_store_dir()

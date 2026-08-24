"""Matrix Client-Server API 客户端：拉房间事件、筛选媒体事件、下载媒体。

身份：专用 Bot / Service 账号 access token（推荐）。
能力：
1. 拉房间事件  GET /_matrix/client/v3/rooms/{roomId}/messages（dir=b, limit）
2. 筛选最新媒体事件：sender 匹配 + msgtype 白名单 + 时间窗口 + 可选文件名匹配
3. 下载媒体    GET /_matrix/client/v3/media/download/{serverName}/{mediaId}
   （加密媒体密文优先走认证端点 /_matrix/client/v1/media/download，404 回退 v3）

E2EE 房间：明文媒体事件携带 content.url（mxc://...），可直接下载；加密事件
（m.room.encrypted 包装，解密后 content.file）需要 Matrix E2EE SDK 解密，
由 app/services/matrix_e2ee.py 提供 event_decryptor / 媒体解密能力。
未接入解密器时加密事件被跳过（计数进 debug_recent_events）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from app.core.config import (
    MATRIX_ACCESS_TOKEN,
    MATRIX_HOMESERVER_URL,
    MATRIX_HTTP_TIMEOUT,
    MATRIX_MEDIA_MSGTYPES,
    MATRIX_MEDIA_WINDOW_MINUTES,
)

logger = logging.getLogger(__name__)

DEFAULT_MEDIA_MSGTYPES = tuple(MATRIX_MEDIA_MSGTYPES or ["m.file", "m.image", "m.video", "m.audio"])

# m.room.encrypted 事件解密器：async callable(raw_event) -> 明文事件 dict | None
EventDecryptor = Any  # Callable[[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]


class MatrixClientError(RuntimeError):
    """Matrix API 调用失败（配置缺失、HTTP 错误、媒体不存在等）。"""


@dataclass(frozen=True)
class MatrixMediaEvent:
    """筛选后的媒体事件摘要。"""

    event_id: str
    sender: str
    origin_server_ts: int
    msgtype: str
    filename: str
    mxc_url: str
    encrypted: bool = False
    raw: Dict[str, Any] | None = None
    # 加密媒体规范字段（content.file：url/key/iv/hashes），仅 encrypted=True 时有值
    file_dict: Dict[str, Any] = field(default_factory=dict)


def parse_mxc_url(url: str) -> Tuple[str, str] | None:
    """解析 mxc://serverName/mediaId → (serverName, mediaId)。

    Matrix 内容字段（content.url / content.file.url）为标准 mxc URI。
    """
    if not url or not isinstance(url, str):
        return None
    value = url.strip()
    if not value.startswith("mxc://"):
        return None
    rest = value[len("mxc://"):]
    parts = rest.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _filename_from_disposition(value: str) -> str:
    """从 Content-Disposition 头提取文件名。"""
    if not value:
        return ""
    for token in value.split(";"):
        token = token.strip()
        if token.lower().startswith("filename*="):
            raw = token.split("=", 1)[1].strip().strip('"')
            if "''" in raw:
                raw = raw.split("''", 1)[1]
            try:
                from urllib.parse import unquote
                return unquote(raw)
            except Exception:
                return raw
        if token.lower().startswith("filename="):
            return token.split("=", 1)[1].strip().strip('"')
    return ""


class MatrixClient:
    """Matrix Client-Server API 客户端。"""

    def __init__(
        self,
        homeserver_url: str = "",
        access_token: str = "",
        timeout: float = 0,
        media_msgtypes: Optional[Tuple[str, ...]] = None,
    ):
        self.homeserver_url = (homeserver_url or MATRIX_HOMESERVER_URL).rstrip("/")
        self.access_token = access_token or MATRIX_ACCESS_TOKEN
        self.timeout = timeout or MATRIX_HTTP_TIMEOUT
        self.media_msgtypes = tuple(media_msgtypes or DEFAULT_MEDIA_MSGTYPES)

    # ── 配置/状态 ──

    @property
    def configured(self) -> bool:
        return bool(self.homeserver_url and self.access_token)

    def status(self) -> Dict[str, Any]:
        # 延迟导入避免循环依赖（matrix_e2ee 反向依赖本模块）
        from app.services.matrix_e2ee import e2ee_config

        return {
            "configured": self.configured,
            "homeserver_url": self.homeserver_url,
            "has_access_token": bool(self.access_token),
            "media_msgtypes": list(self.media_msgtypes),
            "media_window_minutes": MATRIX_MEDIA_WINDOW_MINUTES,
            "e2ee": e2ee_config(),
        }

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self.configured:
            raise MatrixClientError("Matrix 未配置：请设置 MATRIX_HOMESERVER_URL 与 MATRIX_ACCESS_TOKEN")
        url = f"{self.homeserver_url}{path}"
        headers = {**self._headers(), **(kwargs.pop("headers", {}) or {})}
        try:
            resp = httpx.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
        except httpx.HTTPError as exc:
            raise MatrixClientError(f"Matrix 请求失败: {exc}") from exc
        if resp.status_code >= 400:
            raise MatrixClientError(f"Matrix {method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    # ── 1. 拉房间事件 ──

    def fetch_room_messages(
        self,
        room_id: str,
        *,
        limit: int = 50,
        direction: str = "b",
        from_token: str = "",
    ) -> List[Dict[str, Any]]:
        """GET /_matrix/client/v3/rooms/{roomId}/messages。

        direction='b' 表示向历史方向分页（最新在前）。
        返回事件 chunk 列表（原始 event dict）。
        """
        path = f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/messages"
        params: Dict[str, Any] = {"dir": direction, "limit": max(1, min(int(limit or 50), 200))}
        if from_token:
            params["from"] = from_token
        resp = self._request("GET", path, params=params)
        data = resp.json()
        return data.get("chunk") or []

    # ── 2. 筛选最新媒体事件 ──

    def _media_event_from_dict(self, ev: Dict[str, Any]) -> Optional[MatrixMediaEvent]:
        """从（明文或已解密的）m.room.message 事件提取媒体事件摘要。

        加密判定：content.url 为空且存在 content.file（Matrix 加密媒体规范字段）。
        """
        content = ev.get("content") or {}
        msgtype = content.get("msgtype") or ""
        if msgtype not in self.media_msgtypes:
            return None
        mxc_url = content.get("url") or ""
        file_dict = content.get("file") if isinstance(content.get("file"), dict) else {}
        encrypted = bool(not mxc_url and file_dict)
        if not mxc_url and not file_dict:
            return None
        filename = str(content.get("body") or content.get("filename") or "").strip()
        return MatrixMediaEvent(
            event_id=ev.get("event_id") or "",
            sender=ev.get("sender") or "",
            origin_server_ts=int(ev.get("origin_server_ts") or 0),
            msgtype=msgtype,
            filename=filename,
            mxc_url=mxc_url,
            encrypted=encrypted,
            raw=ev,
            file_dict=file_dict,
        )

    async def list_media_events_async(
        self,
        room_id: str,
        *,
        sender: str = "",
        minutes: int | None = None,
        filename_hint: str = "",
        limit: int = 50,
        event_decryptor: Any | None = None,
    ) -> List[MatrixMediaEvent]:
        """list_media_events 的异步版本：支持传入 m.room.encrypted 解密器。

        event_decryptor: async callable(raw_event) -> 明文事件 dict | None，
        由 app/services/matrix_e2ee.build_room_event_decryptor 构造；
        解密失败的事件会被跳过并记录日志。
        """
        window_minutes = minutes if minutes is not None else MATRIX_MEDIA_WINDOW_MINUTES
        cutoff_ts = int(time.time() * 1000) - max(0, int(window_minutes)) * 60_000
        chunk = self.fetch_room_messages(room_id, limit=limit)
        results: List[MatrixMediaEvent] = []

        def _collect(candidate: Dict[str, Any]) -> None:
            """对（明文或已解密的）事件套用与 list_media_events 一致的过滤。"""
            ev_sender = candidate.get("sender") or ""
            if sender and ev_sender != sender:
                return
            origin_ts = int(candidate.get("origin_server_ts") or 0)
            if origin_ts and origin_ts < cutoff_ts:
                return
            media = self._media_event_from_dict(candidate)
            if not media:
                return
            if filename_hint and filename_hint not in media.filename:
                return
            results.append(media)

        for ev in chunk:
            ev_type = ev.get("type") or ""
            if ev_type != "m.room.message" and ev_type != "m.room.encrypted":
                continue
            if ev_type == "m.room.encrypted":
                if event_decryptor is None:
                    continue
                try:
                    decrypted = await event_decryptor(ev)
                except Exception as exc:  # 单事件解密失败不阻断整批筛选
                    # 历史事件缺 room key 属常态（密钥不补发），降级为 debug 防刷屏
                    logger.debug(
                        "Matrix E2EE decrypt failed for event %s in %s: %s",
                        ev.get("event_id"), room_id, exc,
                    )
                    continue
                if isinstance(decrypted, dict):
                    _collect(decrypted)
                continue
            _collect(ev)
        results.sort(key=lambda e: e.origin_server_ts, reverse=True)
        return results

    def list_media_events(
        self,
        room_id: str,
        *,
        sender: str = "",
        minutes: int | None = None,
        filename_hint: str = "",
        limit: int = 50,
    ) -> List[MatrixMediaEvent]:
        """拉取房间近期事件并按过滤条件筛选媒体事件（按时间倒序）。

        过滤条件（与需求建议一致）：
        - sender == 请求里的 sender（避免挂别人的包）；为空则不限
        - content.msgtype in (m.file, m.image, m.video, ...)
        - origin_server_ts 在触发前 N 分钟内（默认 15 分钟）
        - 可选：指令里带文件名再匹配 body / filename

        注意：加密房间的事件为 m.room.encrypted 包装，需要 E2EE 解密器才能
        筛选 —— 请使用 list_media_events_async(event_decryptor=...)。
        """
        window_minutes = minutes if minutes is not None else MATRIX_MEDIA_WINDOW_MINUTES
        cutoff_ts = int(time.time() * 1000) - max(0, int(window_minutes)) * 60_000
        chunk = self.fetch_room_messages(room_id, limit=limit)
        results: List[MatrixMediaEvent] = []
        for ev in chunk:
            if ev.get("type") != "m.room.message":
                continue
            ev_sender = ev.get("sender") or ""
            if sender and ev_sender != sender:
                continue
            origin_ts = int(ev.get("origin_server_ts") or 0)
            if origin_ts and origin_ts < cutoff_ts:
                continue
            media = self._media_event_from_dict(ev)
            if not media:
                continue
            if filename_hint and filename_hint not in media.filename:
                continue
            results.append(media)
        results.sort(key=lambda e: e.origin_server_ts, reverse=True)
        return results

    def debug_recent_events(
        self,
        room_id: str,
        *,
        sender: str = "",
        minutes: int | None = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """诊断房间近期事件：返回 msgtype 分布 / sender 分布 / 窗口内外计数，
        帮助 Agent / 用户判断"为什么没匹配到媒体事件"。

        不做严格的白名单过滤，原样返回统计信息，便于排查"用户发了文件名文字（m.text）"
        vs "真上传了 m.file 附件"这类情况。
        """
        window_minutes = minutes if minutes is not None else MATRIX_MEDIA_WINDOW_MINUTES
        cutoff_ts = int(time.time() * 1000) - max(0, int(window_minutes)) * 60_000
        chunk = self.fetch_room_messages(room_id, limit=limit)
        msgtype_counter: Dict[str, int] = {}
        sender_counter: Dict[str, int] = {}
        text_with_filename: List[Dict[str, Any]] = []
        in_window = 0
        out_of_window = 0
        encrypted_events = 0
        encrypted_in_window = 0
        for ev in chunk:
            if ev.get("type") == "m.room.encrypted":
                # 加密房间事件包装：需 E2EE 解密后才能看到真实 msgtype
                encrypted_events += 1
                origin_ts = int(ev.get("origin_server_ts") or 0)
                if not origin_ts or origin_ts >= cutoff_ts:
                    encrypted_in_window += 1
                continue
            if ev.get("type") != "m.room.message":
                continue
            ev_sender = ev.get("sender") or ""
            if sender and ev_sender != sender:
                continue
            content = ev.get("content") or {}
            msgtype = content.get("msgtype") or ""
            msgtype_counter[msgtype or "(none)"] = msgtype_counter.get(msgtype or "(none)", 0) + 1
            sender_counter[ev_sender] = sender_counter.get(ev_sender, 0) + 1
            origin_ts = int(ev.get("origin_server_ts") or 0)
            if origin_ts and origin_ts < cutoff_ts:
                out_of_window += 1
            else:
                in_window += 1
            if msgtype == "m.text":
                body = str(content.get("body") or "")
                if any(tok in body for tok in (".tar.gz", ".tgz", ".zip", ".jar", ".war", ".gz", ".bin")):
                    text_with_filename.append({
                        "event_id": ev.get("event_id"),
                        "sender": ev_sender,
                        "ts": origin_ts,
                        "body": body[:200],
                    })
        return {
            "total_events": len(chunk),
            "room_message_count": sum(msgtype_counter.values()),
            "in_window": in_window,
            "out_of_window": out_of_window,
            "window_minutes": window_minutes,
            "encrypted_events": encrypted_events,
            "encrypted_in_window": encrypted_in_window,
            "msgtype_distribution": dict(sorted(msgtype_counter.items(), key=lambda x: -x[1])),
            "sender_distribution": dict(sorted(sender_counter.items(), key=lambda x: -x[1])),
            "text_with_filename_hint": text_with_filename[:10],
            "hint": (
                "若 m.file / m.image / m.video / m.audio 计数为 0，"
                "说明用户在房间只发送了文字（m.text），并未真上传 Matrix 附件；"
                "请提示用户拖拽文件上传或使用附件按钮。"
                + (
                    f" 房间内存在 {encrypted_events} 条 m.room.encrypted 加密事件"
                    f"（窗口内 {encrypted_in_window} 条）：这是加密房间，需配置 E2EE "
                    "(MATRIX_E2EE_ENABLED + crypto store) 才能解密其中的部署包。"
                    if encrypted_events else ""
                )
            ),
        }

    def find_latest_media_event(
        self,
        room_id: str,
        *,
        sender: str = "",
        minutes: int | None = None,
        filename_hint: str = "",
        limit: int = 50,
    ) -> Optional[MatrixMediaEvent]:
        """取最新一条匹配的媒体事件；无匹配返回 None。"""
        events = self.list_media_events(
            room_id, sender=sender, minutes=minutes, filename_hint=filename_hint, limit=limit
        )
        return events[0] if events else None

    async def find_latest_media_event_async(
        self,
        room_id: str,
        *,
        sender: str = "",
        minutes: int | None = None,
        filename_hint: str = "",
        limit: int = 50,
        event_decryptor: Any | None = None,
    ) -> Optional[MatrixMediaEvent]:
        """find_latest_media_event 的异步版本：带 E2EE 解密器时加密事件也参与匹配。

        解密器不可用（None）或客户端不支持异步筛选时回退同步路径。
        """
        if event_decryptor is not None and hasattr(self, "list_media_events_async"):
            events = await self.list_media_events_async(
                room_id,
                sender=sender,
                minutes=minutes,
                filename_hint=filename_hint,
                limit=limit,
                event_decryptor=event_decryptor,
            )
            return events[0] if events else None
        return self.find_latest_media_event(
            room_id, sender=sender, minutes=minutes, filename_hint=filename_hint, limit=limit
        )

    # ── 3. 下载媒体 ──

    # 媒体下载端点尝试顺序：
    # - /_matrix/client/v1/... : MSC3916 认证媒体端点（新 Synapse 默认启用）
    # - /_matrix/client/v3/... : 较早的客户端媒体路径（部分实现支持）
    # - /_matrix/media/v3|r0/...: 传统内容仓库端点（旧 homeserver）
    _MEDIA_DOWNLOAD_PATHS = (
        "/_matrix/client/v1/media/download/",
        "/_matrix/client/v3/media/download/",
        "/_matrix/media/v3/download/",
        "/_matrix/media/r0/download/",
    )

    def _media_download_response(self, mxc_url: str):
        """按端点优先级下载 mxc 媒体，返回首个可用端点的响应。

        端点不存在（HTTP 404 + M_UNRECOGNIZED）时自动回退下一候选；
        媒体本身的错误（如 M_NOT_FOUND、限流 429）立即抛出，不掩盖真实原因。
        """
        parsed = parse_mxc_url(mxc_url)
        if not parsed:
            raise MatrixClientError(f"无效的媒体地址: {mxc_url}")
        server_name, media_id = parsed
        quoted = f"{quote(server_name, safe='')}/{quote(media_id, safe='')}"
        last_exc: Optional[MatrixClientError] = None
        for base in self._MEDIA_DOWNLOAD_PATHS:
            try:
                return self._request("GET", f"{base}{quoted}")
            except MatrixClientError as exc:
                message = str(exc)
                if "M_UNRECOGNIZED" not in message:
                    raise
                last_exc = exc
        raise last_exc or MatrixClientError(f"无法下载媒体: {mxc_url}")

    def download_media(self, mxc_url: str) -> Tuple[bytes, str]:
        """下载明文 mxc 媒体，返回 (bytes, 文件名)。

        文件名优先取 Content-Disposition 头；取不到返回空串，由调用方决定兜底名。
        """
        resp = self._media_download_response(mxc_url)
        filename = _filename_from_disposition(resp.headers.get("content-disposition", ""))
        return resp.content, filename

    def fetch_media_ciphertext(self, mxc_url: str) -> Tuple[bytes, str]:
        """下载（可能加密的）媒体密文，返回 (ciphertext bytes, 文件名)。

        与 download_media 共用同一条端点回退链（认证媒体 v1 优先）。
        解密由 matrix_e2ee._decrypt_attachment_bytes 完成。
        """
        resp = self._media_download_response(mxc_url)
        filename = _filename_from_disposition(resp.headers.get("content-disposition", ""))
        return resp.content, filename

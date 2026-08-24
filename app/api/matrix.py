"""Matrix 部署包对接 API。

完整链路：用户在 Matrix 房间发送部署包（未 @）→ 用户 @bot 下达发布指令 →
Agent 调用本模块的发布接口 → 拉取房间事件 → 找到 sender 最近的媒体事件 →
下载媒体（加密房间自动经 E2EE crypto store 解密）到文件中心 →
走现有发布流程（校验和/制品库/集群/发版）→ 审计。

E2EE：接入 matrix-nio[e2e]（vodozemac）。配置 MATRIX_E2EE_ENABLED /
MATRIX_USER_ID / MATRIX_DEVICE_ID / MATRIX_CRYPTO_STORE_PATH 后，Bot 设备密钥
与 Megolm 入站会话持久化在 crypto store，加密房间的部署包可直接拉取解密；
未配置或解密失败时返回带原因的 400（保持既有 E2EE 契约关键字）。

入口：
- GET  /api/v2/matrix/status                连接与配置状态（含 e2ee 子状态）
- POST /api/v2/matrix/rooms/{room_id}/scan  预览房间内匹配的媒体事件（不发布）
- POST /api/v2/deploy/matrix                发布：拉事件+下载+入文件中心+排队发布
- POST /api/deploy                          别名（Agent 常用简短路径）
"""
from __future__ import annotations

import io
import logging
import os
import re
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.helpers import api_response, audit
from app.core.auth_v2 import require_auth, require_deploy_for_env
from app.db import get_db
from app.services.matrix_client import (
    MATRIX_MEDIA_WINDOW_MINUTES,
    MatrixClient,
    MatrixClientError,
    MatrixMediaEvent,
)
from app.services.matrix_e2ee import (
    MatrixE2eeError,
    build_room_event_decryptor,
    e2ee_unavailable_detail,
    pull_media_bytes,
)
from app.services.package_retention import save_package_fileobj
from app.api.deploy.executions import queue_deploy_v2

logger = logging.getLogger(__name__)

matrix_router = APIRouter(prefix="/api/v2/matrix", tags=["Matrix 部署包对接"])
matrix_deploy_router = APIRouter(prefix="/api", tags=["发布管理-Matrix"])

MEDIA_WINDOW_MINUTES = MATRIX_MEDIA_WINDOW_MINUTES


def _user_from_token(request: Request, db: Session) -> Dict[str, Any]:
    """Bearer tool token → user dict（与 deploy_tools 的 ctx 语义一致）。"""
    from app.services.tool_token import validate_tool_token

    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = validate_tool_token(db, auth.split(" ", 1)[1].strip())
    return {
        "username": token.owner,
        "role": "operator" if token.allow_write else "readonly",
        "is_admin": False,
        "can_deploy": bool(token.allow_write),
        "allow_prod": bool(token.allow_prod),
        "auth_type": "tool_token",
    }


def _require_matrix_deploy_user(request: Request, db: Session) -> Dict[str, Any]:
    """同时支持 session 用户与 Bearer tool token。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return _user_from_token(request, db)
    return require_auth(request, db)


def _enforce_matrix_deploy_env(user: Dict[str, Any], environment: str) -> None:
    """鉴权：谁可以部署、哪个环境。"""
    is_prod = (environment or "").lower() in ("prod", "production")
    if user.get("auth_type") == "tool_token":
        if not user.get("can_deploy"):
            raise HTTPException(status_code=403, detail="Deploy permission required")
        if is_prod and not (user.get("allow_prod") or user.get("is_admin")):
            raise HTTPException(status_code=403, detail="Only admin or allow_prod token can deploy to production")
        return
    require_deploy_for_env(user, environment)


def _media_event_to_dict(event: MatrixMediaEvent) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "sender": event.sender,
        "origin_server_ts": event.origin_server_ts,
        "msgtype": event.msgtype,
        "filename": event.filename,
        "mxc_url": event.mxc_url,
        "encrypted": event.encrypted,
    }

def _matrix_conn_from(data: Dict[str, Any]) -> Dict[str, str]:
    """从请求体提取 Matrix 连接参数（参数优先，环境变量兜底）。

    支持两种放置方式：
    - matrix: { homeserverUrl, accessToken }   （推荐，Agent 传入）
    - 顶层   { homeserver_url, access_token }
    返回 {"homeserver_url": ..., "access_token": ...}，未传则为空串（走 env）。
    """
    matrix = data.get("matrix") if isinstance(data.get("matrix"), dict) else {}
    return {
        "homeserver_url": str(
            matrix.get("homeserverUrl")
            or matrix.get("homeserver_url")
            or data.get("homeserver_url")
            or ""
        ).strip(),
        "access_token": str(
            matrix.get("accessToken")
            or matrix.get("access_token")
            or data.get("access_token")
            or ""
        ).strip(),
    }


def _client(homeserver_url: str = "", access_token: str = "") -> MatrixClient:
    client = MatrixClient(homeserver_url=homeserver_url, access_token=access_token)
    if not client.configured:
        raise HTTPException(
            status_code=503,
            detail="Matrix 连接参数缺失：请传入 matrix.homeserverUrl / matrix.accessToken，"
            "或配置 MATRIX_HOMESERVER_URL / MATRIX_ACCESS_TOKEN",
        )
    return client


@matrix_router.get("/status")
def matrix_status(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    client = MatrixClient()
    return api_response(data=client.status())


@matrix_router.post("/rooms/{room_id}/scan")
async def matrix_room_scan(
    room_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """预览房间内匹配的媒体事件（不触发发布）。

    若 events 为空，自动附带 debug 诊断（msgtype 分布 / 文字含文件名提示），
    便于 Agent 排查"用户只发了文件名文字（m.text）但未真上传附件（m.file）"的情形。
    """
    require_auth(request, db)
    body = await request.json() or {}
    sender = str(body.get("sender") or "").strip()
    minutes = int(body.get("minutes") or MEDIA_WINDOW_MINUTES)
    filename_hint = str(body.get("filename") or body.get("filename_hint") or "").strip()
    limit = int(body.get("limit") or 200)
    include_debug = bool(body.get("include_debug") or False)
    conn = _matrix_conn_from(body)
    client = _client(conn["homeserver_url"], conn["access_token"])
    # E2EE：仅使用 .env 专用 Bot 凭据（见 matrix_e2ee.get_e2ee_session 守卫），
    # 调用方传入的动态 token 不用于解密，避免劫持活跃 E2EE 设备。
    decryptor = None
    e2ee_note = ""
    try:
        decryptor = await build_room_event_decryptor(room_id=room_id)
    except MatrixE2eeError as exc:
        e2ee_note = e2ee_unavailable_detail(exc)
    except MatrixClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    try:
        if decryptor is not None:
            events = await client.list_media_events_async(
                room_id,
                sender=sender,
                minutes=minutes,
                filename_hint=filename_hint,
                limit=limit,
                event_decryptor=decryptor,
            )
        else:
            events = client.list_media_events(
                room_id, sender=sender, minutes=minutes, filename_hint=filename_hint, limit=limit
            )
    except MatrixClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    data: Dict[str, Any] = {
        "room_id": room_id,
        "window_minutes": minutes,
        "limit": limit,
        "media_msgtypes": list(client.media_msgtypes),
        "events": [_media_event_to_dict(ev) for ev in events],
    }
    data["e2ee"] = {"decryptor_active": decryptor is not None}
    if e2ee_note:
        data["e2ee"]["note"] = e2ee_note
    if include_debug or not events:
        try:
            data["debug"] = client.debug_recent_events(
                room_id, sender=sender, minutes=minutes, limit=limit
            )
        except MatrixClientError as exc:
            data["debug"] = {"error": str(exc)}
    return api_response(data=data)


async def _matrix_deploy_handler(request: Request, db: Session) -> Dict[str, Any]:
    """Matrix 部署包发布：拉事件 → 找最新媒体 → 下载 → 文件中心 → 排队发布。"""
    user = _require_matrix_deploy_user(request, db)
    data = await request.json() or {}
    return await matrix_deploy_core(user, data, db)


async def matrix_deploy_core(user: Dict[str, Any], data: Dict[str, Any], db: Session) -> Dict[str, Any]:
    """Matrix 发布核心链路（HTTP 端点与 MCP 工具共用）。

    user: 已鉴权用户 dict（session 用户或 tool token 派生 dict）
    data: {env, system, service, version, servers, server_group, pipeline_id,
           variables, confirm_text, reason,
           matrix: {roomId, sender, triggerEventId, filename, minutes,
                    homeserverUrl, accessToken}}   ← homeserver/token 由 Agent 传入
    """
    environment = str(data.get("env") or data.get("environment") or "").strip()
    service = str(data.get("service") or "").strip()
    system = str(data.get("system") or "").strip()
    matrix = data.get("matrix") if isinstance(data.get("matrix"), dict) else {}
    room_id = str(matrix.get("roomId") or matrix.get("room_id") or "").strip()
    trigger_event_id = str(matrix.get("triggerEventId") or matrix.get("trigger_event_id") or "").strip()
    sender = str(matrix.get("sender") or "").strip()
    filename_hint = str(matrix.get("filename") or data.get("filename") or "").strip()
    minutes = int(matrix.get("minutes") or data.get("minutes") or MEDIA_WINDOW_MINUTES)

    if not room_id:
        raise HTTPException(status_code=400, detail="matrix.roomId is required")
    if not sender:
        raise HTTPException(status_code=400, detail="matrix.sender is required（谁发的包）")
    if not environment or not service:
        raise HTTPException(status_code=400, detail="env 与 service 必填")
    _enforce_matrix_deploy_env(user, environment)

    conn = _matrix_conn_from(data)
    client = _client(conn["homeserver_url"], conn["access_token"])
    # E2EE：仅使用 .env 专用 Bot 凭据（专用设备守卫，见 matrix_e2ee）；
    # 不可用时降级为仅明文事件匹配（保持旧行为）。
    try:
        decryptor = await build_room_event_decryptor(room_id=room_id)
    except MatrixE2eeError:
        decryptor = None
    except MatrixClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    try:
        # 兼容简化客户端（测试 Fake / 未来实现）：优先异步解密查找，缺失时回退同步路径
        async_finder = getattr(client, "find_latest_media_event_async", None)
        if callable(async_finder):
            event = await async_finder(
                room_id,
                sender=sender,
                minutes=minutes,
                filename_hint=filename_hint,
                limit=200,
                event_decryptor=decryptor,
            )
        else:
            event = client.find_latest_media_event(
                room_id,
                sender=sender,
                minutes=minutes,
                filename_hint=filename_hint,
                limit=200,
            )
    except MatrixClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if not event:
        # 没找到：附带 debug 信息返回，便于 Agent 提示用户
        try:
            debug_info = client.debug_recent_events(
                room_id, sender=sender, minutes=minutes, limit=200
            )
        except MatrixClientError:
            debug_info = None
        detail = f"未找到 {sender} 最近 {minutes} 分钟内的媒体事件"
        if filename_hint:
            detail += f"（文件名包含 {filename_hint}）"
        if debug_info:
            non_media = sum(v for k, v in (debug_info.get("msgtype_distribution") or {}).items() if k not in client.media_msgtypes)
            detail += (
                f"；房间内 window 内共 {debug_info.get('in_window', 0)} 条 room.message，"
                f"msgtype 分布 {debug_info.get('msgtype_distribution')}，"
                f"其中 {non_media} 条非媒体（很可能是 m.text 文字）。"
                "请确认用户真上传了 Matrix 附件（m.file / m.image / m.video / m.audio），而非仅发送文件名文字。"
            )
            encrypted_count = int(debug_info.get("encrypted_events") or 0)
            if encrypted_count:
                detail += (
                    f" 房间内还有 {encrypted_count} 条 m.room.encrypted 加密事件"
                    f"（窗口内 {debug_info.get('encrypted_in_window', 0)} 条）未被解密匹配："
                    "请确认 Matrix E2EE 已配置（MATRIX_E2EE_* + crypto store）且 Bot 设备已被信任。"
                )
        raise HTTPException(status_code=404, detail=detail)
    # 3. 下载媒体（加密事件自动走「密文下载 + E2EE 解密」链路）
    try:
        media_bytes, disposition_filename = await pull_media_bytes(
            client,
            event,
            homeserver_url=conn["homeserver_url"],
            access_token=conn["access_token"],
        )
    except MatrixE2eeError as exc:
        # 保留 400 + "E2EE" 关键字的契约（测试/Agent 依赖），附可执行提示
        raise HTTPException(
            status_code=400,
            detail=f"该媒体为 E2EE 加密媒体，解密未完成：{e2ee_unavailable_detail(exc)}",
        )
    except MatrixClientError as exc:
        raise HTTPException(status_code=502, detail=f"下载媒体失败: {exc}")

    package_name = event.filename or disposition_filename
    if not package_name:
        package_name = f"matrix-{re.sub(r'[^A-Za-z0-9._-]', '_', event.event_id)[:24]}.bin"
    package_name = os.path.basename(package_name)

    # 4. 写入文件中心（校验和、DeployPackage 元数据）
    # 磁盘写入 + SHA256 是阻塞操作；matrix_deploy_core 既被 async 端点直接 await
    # （主事件循环上），也被 MCP 工具经线程桥调用——统一放线程池避免占住循环。
    from fastapi.concurrency import run_in_threadpool as _rit
    try:
        meta = await _rit(
            save_package_fileobj,
            db,
            filename=package_name,
            fileobj=io.BytesIO(media_bytes),
            system=system or "",
            service=service or "",
            uploaded_by=user.get("username") or "matrix",
            overwrite=True,
            source_context={
                "channel": "matrix",
                "channel_account_id": "default",
                "conversation_id": room_id,
                "message_id": event.event_id,
                "sender_id": sender,
            },
            source_message_key=f"matrix:default:{room_id}:{event.event_id}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to save matrix media package")
        raise HTTPException(status_code=500, detail=f"保存媒体包失败: {exc}")
    package_name = meta.get("package_name") or package_name

    # 5. 走现有发布流程（复用 queue_deploy_v2：鉴权/锁/任务/审计/通知）
    deploy_data = {**data}
    deploy_data["system"] = system
    deploy_data["service"] = service
    deploy_data["environment"] = environment
    deploy_data["file_name"] = package_name
    deploy_data["version"] = str(data.get("version") or package_name)
    deploy_data.pop("matrix", None)
    try:
        result = await queue_deploy_v2(user, deploy_data, db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to queue matrix deploy")
        raise HTTPException(status_code=500, detail=f"排队发布失败: {exc}")

    # 审计：roomId、mediaEventId、triggerEventId、sender、环境、服务名
    audit(
        "deploy.execute.matrix",
        "system",
        system or service,
        f"roomId={room_id} mediaEventId={event.event_id} triggerEventId={trigger_event_id or '-'} "
        f"sender={sender} env={environment} service={service} file={package_name} "
        f"sha256={meta.get('sha256')} task_id={(result.get('data') or {}).get('task_id')}",
    )
    result.setdefault("data", {})["matrix"] = {
        "room_id": room_id,
        "media_event_id": event.event_id,
        "trigger_event_id": trigger_event_id or "",
        "sender": sender,
        "filename": package_name,
        "mxc_url": event.mxc_url,
        "encrypted": bool(event.encrypted),
    }
    return result


@matrix_deploy_router.post("/deploy")
async def matrix_deploy_alias(request: Request, db: Session = Depends(get_db)):
    """POST /api/deploy —— Agent 调用的简短别名路径。"""
    return await _matrix_deploy_handler(request, db)


@matrix_router.post("/deploy")
async def matrix_deploy_v2(request: Request, db: Session = Depends(get_db)):
    """POST /api/v2/matrix/deploy —— 规范的 Matrix 发布入口。"""
    return await _matrix_deploy_handler(request, db)

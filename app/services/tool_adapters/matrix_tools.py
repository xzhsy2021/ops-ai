"""Matrix 部署包对接 MCP 工具。

把「OPS 直接从 Matrix 房间拉附件」能力暴露给 qclaw / OpenClaw Agent：
- ops.matrix.scan_media_events    预览房间内匹配的媒体事件（只读，不下载）
- ops.matrix.pull_attachment     从 Matrix 房间拉取最新媒体附件到文件中心（下载 + 校验和 + 入库）
- ops.matrix.deploy_from_matrix  完整链路：拉附件 → 文件中心 → 排队发布（复用 queue_deploy_v2）

这些工具与 app/api/matrix.py 的 HTTP 端点共用 MatrixClient / save_package_fileobj /
queue_deploy_v2，保证 HTTP 与 MCP 两条通道行为一致。
E2EE：加密房间事件经 app/services/matrix_e2ee.py 解密（matrix-nio[e2e] +
持久化 crypto store），未配置或解密失败时返回带原因的明确错误。
"""
from __future__ import annotations

import asyncio
import atexit
import io
import os
import re
import time
from typing import Any, Dict, List

from fastapi import HTTPException

from app.services.tool_registry import registry
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


def _matrix_pull_allow_any_extension() -> bool:
    """Matrix 附件拉取是否放行任意文件格式。

    默认 True：房间里的 .txt/.pdf/.log/.conf 等普通文件也能拉入文件中心
    （大小上限仍受 max_upload_size_mb 约束）。设 MATRIX_PULL_ALLOW_ANY_EXTENSION=0
    可恢复部署包格式白名单。
    """
    return os.getenv("MATRIX_PULL_ALLOW_ANY_EXTENSION", "1").strip().lower() not in {"0", "false", "no", "off"}


import concurrent.futures
import threading


def _run_coroutine_sync(coroutine):
    """在同步上下文中执行协程的桥接器。

    - 无事件循环（pytest / CLI / 线程池 worker）：直接 asyncio.run；
    - 已有事件循环在跑（MCP HTTP 异步端点 mcp_streamable_http_endpoint
      直接调用同步工具）：asyncio.run 会抛 "cannot be called from a
      running event loop" 并丢弃协程 —— 转投独立线程用私有 loop 执行。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    from app.services.matrix_e2ee import aclose_loop_session

    def _runner():
        async def _main():
            try:
                return await coroutine
            finally:
                # 线程 loop 即将销毁：关闭绑定其上的 E2EE 会话，避免连接泄漏
                try:
                    await aclose_loop_session()
                except Exception:
                    pass

        return asyncio.run(_main())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="matrix-sync-bridge") as pool:
        return pool.submit(_runner).result(timeout=180)


# ── Matrix 网络 IO 独立受限线程池 ────────────────────────────────
# scan / pull 这类"只读但联网秒级"的工具，若并发出现会打满 FastAPI 通用 worker
# 线程，拖累任务中心轮询、审批确认等请求。将其网络段统一投到独立受限池并发执行，
# 从而收敛慢 IO 的并发占用（最多 N 个），避免服务整体卡死。
_MATRIX_IO_THREAD_PREFIX = "matrix-io"
_MATRIX_IO_MAX_WORKERS = 4
_MATRIX_IO_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=_MATRIX_IO_MAX_WORKERS, thread_name_prefix=_MATRIX_IO_THREAD_PREFIX
)
# 预热常驻池并避免进程退出时 join 空闲 worker 造成延迟
atexit.register(_MATRIX_IO_EXECUTOR.shutdown, wait=False)


def _in_matrix_io_pool() -> bool:
    return threading.current_thread().name.startswith(_MATRIX_IO_THREAD_PREFIX)


def matrix_run_io(fn):
    """在独立受限 IO 池线程执行 fn；已在该池内则直接执行，避免自池内重提交死锁。"""
    if _in_matrix_io_pool():
        return fn()
    return _MATRIX_IO_EXECUTOR.submit(fn).result()


# ── scan 媒体事件短缓存（同参 30s TTL）────────────────────────────
# 发版链路每次都会 scan 确认房间附件；同一房间/参数在短窗口内重复全量拉取是无用功，
# 命中缓存可让后续 scan 帧级返回。缓存带与参数绑定，避免跨房间/跨窗口串数据。
_SCAN_MEDIA_CACHE_TTL = 30.0
_SCAN_MEDIA_CACHE_MAX = 512
_SCAN_MEDIA_CACHE: dict[str, tuple[float, Dict[str, Any]]] = {}
_SCAN_MEDIA_CACHE_LOCK = threading.Lock()
_SCAN_DEFAULT_LIMIT = 50


def _scan_cache_key(room_id: str, sender: str, minutes: int, filename: str, limit: int) -> str:
    return "\x00".join((room_id, sender, str(minutes), filename, str(limit)))


def _scan_cache_get(key: str):
    with _SCAN_MEDIA_CACHE_LOCK:
        item = _SCAN_MEDIA_CACHE.get(key)
        if item is not None and item[0] > time.monotonic():
            return item[1]
        _SCAN_MEDIA_CACHE.pop(key, None)
    return None


def _scan_cache_set(key: str, value: Dict[str, Any]) -> None:
    with _SCAN_MEDIA_CACHE_LOCK:
        _SCAN_MEDIA_CACHE[key] = (time.monotonic() + _SCAN_MEDIA_CACHE_TTL, value)
        if len(_SCAN_MEDIA_CACHE) > _SCAN_MEDIA_CACHE_MAX:
            _SCAN_MEDIA_CACHE.clear()


def _execute_media_scan(args: Dict[str, Any], room_id: str, sender: str,
                        minutes: int, filename: str, limit: int) -> Dict[str, Any]:
    """在 IO 池内执行一次完整扫描，返回结果 dict。"""
    client = _matrix_client(args)
    _conn_args(args)
    decryptor = _try_build_decryptor(room_id)
    if decryptor is not None:
        events = _run_coroutine_sync(
            client.list_media_events_async(
                room_id, sender=sender, minutes=minutes,
                filename_hint=filename, limit=limit, event_decryptor=decryptor,
            )
        )
    else:
        events = client.list_media_events(
            room_id, sender=sender, minutes=minutes,
            filename_hint=filename, limit=limit,
        )
    result: Dict[str, Any] = {
        "room_id": room_id,
        "window_minutes": minutes,
        "limit": limit,
        "media_msgtypes": list(client.media_msgtypes),
        "events": [_event_to_dict(ev) for ev in events],
        "e2ee": {"decryptor_active": decryptor is not None},
        "summary": f"找到 {len(events)} 条匹配的媒体事件"
        + (
            "（窗口内 0 条媒体：若用户发了文件名，房间内大概率只有 m.text 而无 m.file，请提示真上传附件）"
            if not events else ""
        ),
    }
    if args.get("include_debug") or not events:
        try:
            result["debug"] = client.debug_recent_events(
                room_id, sender=sender, minutes=minutes, limit=limit,
            )
        except MatrixClientError as exc:
            result["debug"] = {"error": str(exc)}
    return result


def _try_build_decryptor(room_id: str) -> Any | None:
    """尽力构建 m.room.encrypted 解密器；E2EE 不可用时返回 None。

    仅使用 .env 专用 Bot 凭据（专用设备守卫，见 matrix_e2ee.get_e2ee_session）。
    """
    try:
        return _run_coroutine_sync(build_room_event_decryptor(room_id=room_id))
    except MatrixE2eeError:
        return None


def _matrix_client(args: Dict[str, Any] | None = None) -> MatrixClient:
    """构建 MatrixClient。homeserver / token 由 Agent 调用时传入（参数优先），
    未传时回退到环境变量 MATRIX_HOMESERVER_URL / MATRIX_ACCESS_TOKEN。"""
    args = args or {}
    homeserver_url = str(args.get("homeserver_url") or args.get("homeserverUrl") or "").strip()
    access_token = str(args.get("access_token") or args.get("accessToken") or "").strip()
    client = MatrixClient(homeserver_url=homeserver_url, access_token=access_token)
    if not client.configured:
        raise HTTPException(
            status_code=503,
            detail="Matrix 连接参数缺失：请传入 homeserver_url 与 access_token，"
            "或配置 MATRIX_HOMESERVER_URL / MATRIX_ACCESS_TOKEN",
        )
    return client


def _conn_args(args: Dict[str, Any] | None = None) -> Dict[str, str]:
    """从工具参数提取 Matrix 连接信息（供 E2EE 会话复用）。"""
    args = args or {}
    return {
        "homeserver_url": str(args.get("homeserver_url") or args.get("homeserverUrl") or "").strip(),
        "access_token": str(args.get("access_token") or args.get("accessToken") or "").strip(),
    }


def _enforce_room_binding(ctx, room_id: str) -> None:
    """qclaw token 绑定房间后，只能拉取绑定房间内的媒体。"""
    bound = list(getattr(ctx, "bound_room_ids", []) or [])
    if bound and room_id not in bound:
        raise HTTPException(
            status_code=403,
            detail=f"room_id {room_id} 不在当前 token 绑定房间内: {','.join(bound)}",
        )


def _event_to_dict(event: MatrixMediaEvent) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "sender": event.sender,
        "origin_server_ts": event.origin_server_ts,
        "msgtype": event.msgtype,
        "filename": event.filename,
        "mxc_url": event.mxc_url,
        "encrypted": event.encrypted,
    }


def _annotate_file_center_matches(db, events: List[Dict[str, Any]], room_id: str) -> None:
    """为扫描到的事件标注文件中心入库状态（source_message_key 精确匹配）。

    每个 event 增补：
    - already_in_file_center: 该 Matrix 事件是否已拉取入库（bool）
    - file_center_package: 命中包的 {package_name, sha256, size_bytes} 或 null
    判定键 = pull 入库时写入的 source_message_key（matrix:default:{room}:{event_id}），
    同一事件必然同一包——不靠文件名猜测。
    """
    from app.db.models import DeployPackage

    if db is None or not events:
        for ev in events:
            ev.setdefault("already_in_file_center", False)
            ev.setdefault("file_center_package", None)
        return
    keys = {
        f"matrix:default:{room_id}:{ev.get('event_id') or ''}": ev
        for ev in events
        if ev.get("event_id")
    }
    if not keys:
        return
    rows = (
        db.query(DeployPackage)
        .filter(
            DeployPackage.source_message_key.in_(list(keys.keys())),
            DeployPackage.deleted == False,  # noqa: E712
        )
        .all()
    )
    for row in rows:
        ev = keys.get(row.source_message_key or "")
        if ev is None:
            continue
        ev["already_in_file_center"] = True
        ev["file_center_package"] = {
            "package_name": row.package_name,
            "sha256": row.sha256 or "",
            "size_bytes": int(row.size_bytes or 0),
        }
    for ev in events:
        ev.setdefault("already_in_file_center", False)
        ev.setdefault("file_center_package", None)


@registry.register(
    name="ops.matrix.scan_media_events",
    title="Scan Matrix room media events",
    description="预览 Matrix 房间内匹配的媒体事件（sender / msgtype / 时间窗口 / 可选文件名），不下载、不发布。返回匹配事件的 event_id、文件名、mxc 地址与是否加密；并标注 already_in_file_center / file_center_package（该事件是否已拉取入库及对应包 sha256——判定键为事件指纹 source_message_key，非文件名猜测）。任意文件类型均可见（部署包或 .txt/.pdf 等普通文件）。中文: 查看Matrix房间媒体/扫描房间附件/房间发了什么文件.",
    scopes=["ops:read"],
    risk="low",
    category="package_read",
    keywords=["matrix", "room", "media", "attachment", "scan", "附件", "文件"],
    recommended_use_cases=["Agent 需要确认 Matrix 房间里谁发了什么文件（部署包或普通文件）"],
    example_prompts=["扫一下 Matrix 房间里的媒体事件", "看看房间最近有没有人发部署包"],
    input_schema={
        "type": "object",
        "properties": {
            "room_id": {"type": "string", "description": "Matrix 房间 ID，如 !room:example.org"},
            "sender": {"type": "string", "description": "限定发送者（Matrix user id），为空则不限"},
            "minutes": {"type": "integer", "description": f"回看窗口（分钟），默认 {MATRIX_MEDIA_WINDOW_MINUTES}"},
            "filename": {"type": "string", "description": "可选文件名匹配（content.body / filename）"},
            "limit": {"type": "integer", "description": f"拉取事件条数，默认 {_SCAN_DEFAULT_LIMIT}（Matrix API 上限 200）"},
            "include_debug": {"type": "boolean", "description": "是否额外返回房间事件诊断（msgtype 分布 / 文字含文件名提示），用于排查为什么没匹配到媒体"},
            "homeserver_url": {"type": "string", "description": "Matrix homeserver 地址（Agent 传入），如 https://matrix.example.org"},
            "access_token": {"type": "string", "description": "Matrix Bot/Service 账号 access token（Agent 传入）"},
        },
        "required": ["room_id"],
        "additionalProperties": False,
    },
)
def scan_media_events(args: Dict[str, Any], ctx, db) -> Dict[str, Any]:
    room_id = str(args.get("room_id") or "").strip()
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")
    _enforce_room_binding(ctx, room_id)
    sender = str(args.get("sender") or "").strip()
    minutes = int(args.get("minutes") or MATRIX_MEDIA_WINDOW_MINUTES)
    filename = str(args.get("filename") or "").strip()
    limit = int(args.get("limit") or _SCAN_DEFAULT_LIMIT)

    key = _scan_cache_key(room_id, sender, minutes, filename, limit)
    cached = _scan_cache_get(key)
    if cached is not None:
        cached = dict(cached)
        cached["cached"] = True
        return cached

    try:
        result = matrix_run_io(
            lambda: _execute_media_scan(args, room_id, sender, minutes, filename, limit)
        )
    except MatrixClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # 标注文件中心入库状态（source_message_key 精确匹配，不靠文件名猜）
    _annotate_file_center_matches(db, result.get("events") or [], room_id)

    _scan_cache_set(key, result)
    return result


def pull_matrix_attachment_core(
    db,
    *,
    room_id: str,
    sender: str,
    minutes: int | None = None,
    filename_hint: str = "",
    system: str = "",
    service: str = "",
    overwrite: bool = False,
    uploaded_by: str = "",
    homeserver_url: str = "",
    access_token: str = "",
) -> Dict[str, Any]:
    """拉取 Matrix 附件到文件中心的核心实现（MCP 工具与计划步骤共用）。

    下载最新匹配媒体 → E2EE 解密（如加密）→ SHA256 → save_package_fileobj 入库。
    失败抛 HTTPException（计划步骤执行器会转为步骤 FAILED）。
    """
    if not room_id or not sender:
        raise HTTPException(status_code=400, detail="room_id 与 sender 必填")
    client = _matrix_client({
        "homeserver_url": homeserver_url,
        "access_token": access_token,
    })
    # 加密事件在解密前没有 mxc_url，同步筛选对它不可见（表现为"未找到媒体事件"）。
    # 因此优先走带 E2EE 解密器的异步筛选路径；解密器不可用或旧客户端无异步方法时回退同步。
    decryptor = _try_build_decryptor(room_id)
    async_finder = getattr(client, "find_latest_media_event_async", None)
    minutes = int(minutes or MATRIX_MEDIA_WINDOW_MINUTES)
    try:
        if async_finder is not None:
            event = _run_coroutine_sync(
                async_finder(
                    room_id,
                    sender=sender,
                    minutes=minutes,
                    filename_hint=filename_hint,
                    limit=100,
                    event_decryptor=decryptor,
                )
            )
        else:
            event = client.find_latest_media_event(
                room_id,
                sender=sender,
                minutes=minutes,
                filename_hint=filename_hint,
                limit=100,
            )
    except MatrixClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if not event:
        raise HTTPException(
            status_code=404,
            detail=f"未找到 {sender} 最近 {minutes or MATRIX_MEDIA_WINDOW_MINUTES} 分钟内的媒体事件",
        )
    try:
        # 未加密事件直接下载；加密事件走「密文下载 + E2EE 解密」
        media_bytes, disposition_filename = _run_coroutine_sync(
            pull_media_bytes(client, event, homeserver_url=homeserver_url, access_token=access_token)
        )
    except MatrixE2eeError as exc:
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

    try:
        meta = save_package_fileobj(
            db,
            filename=package_name,
            fileobj=io.BytesIO(media_bytes),
            system=system,
            service=service,
            uploaded_by=uploaded_by or "matrix",
            overwrite=overwrite,
            source_context={
                "channel": "matrix",
                "channel_account_id": "default",
                "conversation_id": room_id,
                "message_id": event.event_id,
                "sender_id": sender,
            },
            source_message_key=f"matrix:default:{room_id}:{event.event_id}",
            allow_any_extension=_matrix_pull_allow_any_extension(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存媒体包失败: {exc}")

    return {
        "package_name": meta.get("package_name") or package_name,
        "sha256": meta.get("sha256"),
        "size_bytes": meta.get("size_bytes"),
        "event_id": event.event_id,
        "sender": sender,
        "room_id": room_id,
        "mxc_url": event.mxc_url,
        "filename": event.filename,
        "encrypted": event.encrypted,
        "matrix": {
            "room_id": room_id,
            "media_event_id": event.event_id,
            "sender": sender,
            "filename": meta.get("package_name") or package_name,
        },
    }


@registry.register(
    name="ops.matrix.pull_attachment",
    title="Pull latest Matrix attachment into File Center",
    description="从 Matrix 房间拉取发送者最近一次媒体附件（m.file/m.image/m.video/m.audio）到 OPS 文件中心：下载 mxc 媒体、计算 SHA256、写入 DeployPackage 元数据并返回 package_name。支持任意文件格式（.txt/.pdf/.log 等普通文件均可，MATRIX_PULL_ALLOW_ANY_EXTENSION=0 可恢复部署包白名单）。加密房间的 E2EE 媒体在配置 MATRIX_E2EE_* 后自动解密；解密失败返回明确原因。中文: 从Matrix拉附件/拉取Matrix文件/Matrix附件入库/下载房间文件.",
    scopes=["ops:read", "package:write"],
    risk="medium",
    category="package_write",
    write=True,
    requires_confirmation=True,
    data_sensitivity="sensitive",
    keywords=["matrix", "attachment", "pull", "download", "file center", "拉附件", "拉取"],
    recommended_use_cases=["用户说'从 Matrix 房间拉取我发的部署包'时，拉取附件到文件中心"],
    example_prompts=["把 Matrix 房间里的最新附件拉到文件中心", "拉取 alice 发的部署包"],
    input_schema={
        "type": "object",
        "properties": {
            "room_id": {"type": "string", "description": "Matrix 房间 ID，如 !room:example.org"},
            "sender": {"type": "string", "description": "发送者 Matrix user id（谁发的包），必填"},
            "minutes": {"type": "integer", "description": f"回看窗口（分钟），默认 {MATRIX_MEDIA_WINDOW_MINUTES}"},
            "filename": {"type": "string", "description": "可选文件名匹配（content.body / filename）"},
            "system": {"type": "string", "description": "所属系统（可选，入库元数据用）"},
            "service": {"type": "string", "description": "所属服务（可选，入库元数据用）"},
            "overwrite": {"type": "boolean", "description": "同名包已存在时是否覆盖，默认 false"},
            "confirm_text": {"type": "string", "description": "确认短语：CONFIRM ops.matrix.pull_attachment（中风险写操作必需；作为执行计划步骤执行时无需此参数）"},
            "homeserver_url": {"type": "string", "description": "Matrix homeserver 地址（Agent 传入），如 https://matrix.example.org"},
            "access_token": {"type": "string", "description": "Matrix Bot/Service 账号 access token（Agent 传入）"},
        },
        "required": ["room_id", "sender"],
        "additionalProperties": False,
    },
)
def pull_attachment(args: Dict[str, Any], ctx, db) -> Dict[str, Any]:
    room_id = str(args.get("room_id") or "").strip()
    sender = str(args.get("sender") or "").strip()
    if not room_id or not sender:
        raise HTTPException(status_code=400, detail="room_id 与 sender 必填")
    _enforce_room_binding(ctx, room_id)
    result = pull_matrix_attachment_core(
        db,
        room_id=room_id,
        sender=sender,
        minutes=int(args.get("minutes") or MATRIX_MEDIA_WINDOW_MINUTES),
        filename_hint=str(args.get("filename") or "").strip(),
        system=str(args.get("system") or ""),
        service=str(args.get("service") or ""),
        overwrite=bool(args.get("overwrite")),
        uploaded_by=ctx.username or ctx.token_owner or "matrix",
        homeserver_url=str(args.get("homeserver_url") or args.get("homeserverUrl") or ""),
        access_token=str(args.get("access_token") or args.get("accessToken") or ""),
    )
    # 部署包格式 → 建议后续发布动作；普通文件（非白名单后缀）仅入库留存，
    # 提示不能发布（发版制品格式守卫会拒绝）。
    deployable = any(
        result["package_name"].lower().endswith(ext)
        for ext in (".tar.gz", ".tgz", ".tar", ".zip", ".jar", ".war", ".gz", ".bin")
    )
    if deployable:
        result["next_actions"] = [
            {"tool": "ops_matrix_deploy_from_matrix", "description": "直接用这个包触发发布"},
            {"tool": "ops_create_deploy_plan", "arguments": {"package_name": result["package_name"]}, "description": "先创建发布计划"},
        ]
    else:
        result["next_actions"] = [
            {
                "note": (
                    f"「{result['package_name']}」是普通文件，已入库留存；"
                    "发版制品必须是部署包格式（.tar.gz/.tgz/.tar/.zip/.jar/.war/.gz/.bin），"
                    "该文件不能作为发版制品"
                ),
            },
        ]
    return result


@registry.register(
    name="ops.matrix.deploy_from_matrix",
    title="Deploy package pulled from Matrix room",
    description="完整 Matrix 发布链路：从 Matrix 房间拉取发送者最近媒体附件 → 写入文件中心（SHA256）→ 排队发布（复用现有发布流程：校验和/制品库/集群/发版/审计）。仅接受部署包格式制品（.tar.gz/.tgz/.tar/.zip/.jar/.war/.gz/.bin）；普通文件请用 ops.matrix.pull_attachment 仅入库。生产环境需管理员或 allow_prod 权限并附 confirm_text。中文: Matrix发布/从Matrix发版/拉取Matrix附件并发布.",
    scopes=["ops:read", "package:write", "deploy:execute"],
    risk="high",
    category="deploy_execute",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    keywords=["matrix", "deploy", "release", "发版", "发布"],
    recommended_use_cases=["用户说'把 Matrix 房间里我发的包部署到测试环境'时，走完整发布链路"],
    example_prompts=["从 Matrix 房间拉取部署包并发布到测试环境", "部署 alice 刚在房间发的包"],
    input_schema={
        "type": "object",
        "properties": {
            "room_id": {"type": "string", "description": "Matrix 房间 ID，如 !room:example.org"},
            "sender": {"type": "string", "description": "发送者 Matrix user id（谁发的包），必填"},
            "env": {"type": "string", "description": "发布环境，如 test / staging / prod"},
            "environment": {"type": "string", "description": "environment 别名（与 env 二选一）"},
            "system": {"type": "string", "description": "所属系统"},
            "service": {"type": "string", "description": "所属服务，必填"},
            "minutes": {"type": "integer", "description": f"回看窗口（分钟），默认 {MATRIX_MEDIA_WINDOW_MINUTES}"},
            "filename": {"type": "string", "description": "可选文件名匹配"},
            "version": {"type": "string", "description": "版本号，缺省用包名"},
            "servers": {"type": "array", "items": {"type": "string"}},
            "server_group": {"type": "string"},
            "pipeline_id": {"type": "string"},
            "variables": {"type": "object"},
            "confirm_text": {"type": "string", "description": "生产环境二次确认短语（CONFIRM / 确认发布 xxx）"},
            "reason": {"type": "string", "description": "发布原因（生产环境必填）"},
            "trigger_event_id": {"type": "string", "description": "触发指令的 Matrix 事件 id（审计记录用）"},
            "homeserver_url": {"type": "string", "description": "Matrix homeserver 地址（Agent 传入），如 https://matrix.example.org"},
            "access_token": {"type": "string", "description": "Matrix Bot/Service 账号 access token（Agent 传入）"},
        },
        "required": ["room_id", "sender", "service"],
        "additionalProperties": False,
    },
)
def deploy_from_matrix(args: Dict[str, Any], ctx, db) -> Dict[str, Any]:
    room_id = str(args.get("room_id") or "").strip()
    sender = str(args.get("sender") or "").strip()
    service = str(args.get("service") or "").strip()
    environment = str(args.get("env") or args.get("environment") or "").strip()
    if not room_id or not sender:
        raise HTTPException(status_code=400, detail="room_id 与 sender 必填")
    if not service:
        raise HTTPException(status_code=400, detail="service 必填")
    if not environment:
        raise HTTPException(status_code=400, detail="env/environment 必填")
    _enforce_room_binding(ctx, room_id)

    # 与 HTTP POST /api/deploy 共用同一核心链路（拉事件、下载、入库、排队发布、审计）
    from app.api.matrix import matrix_deploy_core

    user = {
        "username": ctx.username or ctx.token_owner or "",
        "role": ctx.role,
        "is_admin": bool(getattr(ctx, "is_admin", False)),
        "can_deploy": bool(getattr(ctx, "can_deploy", False) or getattr(ctx, "allow_write", False)),
        "allow_prod": bool(getattr(ctx, "allow_prod", False)),
        "auth_type": getattr(ctx, "auth_type", ""),
    }
    body = {
        "env": environment,
        "system": str(args.get("system") or ""),
        "service": service,
        "version": str(args.get("version") or ""),
        "servers": args.get("servers") or [],
        "server_group": str(args.get("server_group") or ""),
        "pipeline_id": str(args.get("pipeline_id") or ""),
        "variables": args.get("variables") or {},
        "confirm_text": str(args.get("confirm_text") or ""),
        "reason": str(args.get("reason") or ""),
        "matrix": {
            "roomId": room_id,
            "sender": sender,
            "triggerEventId": str(args.get("trigger_event_id") or ""),
            "filename": str(args.get("filename") or ""),
            "minutes": int(args.get("minutes") or MATRIX_MEDIA_WINDOW_MINUTES),
            "homeserverUrl": str(args.get("homeserver_url") or args.get("homeserverUrl") or ""),
            "accessToken": str(args.get("access_token") or args.get("accessToken") or ""),
        },
    }

    try:
        result = _run_coroutine_sync(matrix_deploy_core(user, body, db))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Matrix 发布失败: {exc}")
    return result

"""QClaw 多渠道审批 MCP 工具。

注册 ops.routing.* 和 ops.approval.* MCP 工具，供 qclaw 通过 MCP 调用。
这些工具是 qclaw 集成的唯一 OPS 接口，不暴露 deploy:execute / package:write
等原始危险 scope。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict
from fastapi import HTTPException

from app.services.tool_registry import registry
from app.services.message_context import MessageContext, is_unbound_content_sha256, is_valid_content_sha256, message_context_schema, normalize_message_context
from app.services.tool_token import normalize_approver_identities
from app.services.qclaw_routing import (
    resolve_message_target,
    issue_ticket,
    verify_ticket,
    compute_routing_revision,
    _decode_ticket_payload,
    _extract_routing,
    _extract_approvers,
    _extract_rooms,
    normalize_routing_approvers,
    RoutingOutcome,
)
from app.services.action_approval import ActionApprovalService
from app.services.execution_plan import ExecutionPlanService, step_approval_details
from app.services.package_intake import intake_package, list_staging_packages
from app.config.systems import get_all_systems
from app.services.tool_adapters.file_transfer_tools import (
    _remote_file_path,
    _resolve_source,
)
from app.services.package_retention import get_package_retention_policy, inspect_package_file


def _current_channel_identities(
    identities,
    *,
    channel: str,
    channel_account_id: str,
) -> list[dict[str, str]]:
    normalized = normalize_approver_identities(identities)
    return [
        identity
        for identity in normalized
        if identity["channel"] == channel
        and identity["channel_account_id"] == channel_account_id
    ]


def _effective_approver_identities(
    ctx,
    configured,
    *,
    channel: str,
    channel_account_id: str,
) -> list[dict[str, str]]:
    """系统设置(message_routing.approvers)为审批人唯一来源。

    已彻底移除 token 级 approver_identities 回退。系统配置的原始审批人才可
    审批/自批，请求者==审批者的自批不再被 token 排除。
    """
    try:
        configured_identities = normalize_routing_approvers(configured)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail=f"Invalid routing approver configuration: {exc}",
        ) from exc
    if not configured_identities:
        return []
    matched = _current_channel_identities(
        configured_identities,
        channel=channel,
        channel_account_id=channel_account_id,
    )
    if not matched:
        raise HTTPException(
            status_code=403,
            detail="No authorized approver is configured for this channel account",
        )
    return matched


def _lookup_approvers(
    ctx,
    system_name: str,
    service_name: str | None = None,
    *,
    channel: str,
    channel_account_id: str,
) -> list[str]:
    """Resolve approvers for one channel account without cross-channel fallback."""
    if not system_name:
        configured = []
    else:
        systems = get_all_systems()
        sys_cfg = systems.get(system_name)
        configured = []
        if sys_cfg:
            try:
                sys_routing = _extract_routing(sys_cfg)
                configured = list(_extract_approvers(sys_routing))
            except ValueError as exc:
                raise HTTPException(
                    status_code=403,
                    detail=f"Invalid routing approver configuration: {exc}",
                ) from exc
    identities = _effective_approver_identities(
        ctx,
        configured,
        channel=channel,
        channel_account_id=channel_account_id,
    )
    return [identity["sender_id"] for identity in identities]


def _resolve_system_rooms(system_name: str) -> list[dict[str, str]]:
    """解析系统级 message_routing.rooms（授权会话绑定）。"""
    if not system_name:
        return []
    sys_cfg = get_all_systems().get(system_name)
    if not sys_cfg:
        return []
    try:
        return list(_extract_rooms(_extract_routing(sys_cfg)))
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail=f"Invalid routing room configuration: {exc}",
        ) from exc


def _enforce_system_room(message_context: MessageContext, system_name: str) -> None:
    """系统级房间作用域：若 message_routing.rooms 指定了会话，则仅这些会话可用。"""
    rooms = _resolve_system_rooms(system_name)
    if not rooms:
        return
    candidate = {
        "channel": message_context.channel,
        "channel_account_id": message_context.channel_account_id,
        "conversation_id": message_context.conversation_id,
    }
    if candidate not in rooms:
        raise HTTPException(
            status_code=403,
            detail="System is not usable in this channel conversation (message_routing.rooms)",
        )


# ──────────────────────────────────────────────────────────────
# ops.routing.* — 消息路由
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.routing.resolve_message_target",
    title="解析 QClaw 消息路由目标",
    description="将 QClaw 渠道消息确定性路由到 OPS 系统/服务，并签发绑定完整消息上下文的路由票据。未显式传 content_sha256 时自动按消息原文（UTF-8）计算，调用方无需本地预计算。",
    scopes=["ops:read"],
    risk="low",
    category="routing",
    input_schema={
        "type": "object",
        "properties": {
            "message_text": {
                "type": "string",
                "description": "QClaw 渠道消息原文",
            },
            "message_context": message_context_schema(),
            "room_id": {
                "type": "string",
                "description": "兼容字段：Matrix 房间 ID",
            },
            "event_id": {
                "type": "string",
                "description": "兼容字段：Matrix 事件 ID",
            },
            "request_event_id": {
                "type": "string",
                "description": "兼容字段：Matrix 请求事件 ID",
            },
            "sender_matrix_id": {
                "type": "string",
                "description": "兼容字段：Matrix 发起人 ID",
            },
            "content_sha256": {
                "type": "string",
                "description": "消息内容的 SHA-256（hex 64 位）。可选：未传时 OPS 自动按 message_text 计算",
            },
        },
        "required": ["message_text"],
        "additionalProperties": False,
    },
)
def routing_resolve_message_target(args, ctx, db):
    message_text = args["message_text"]
    legacy_fields = {
        "room_id",
        "event_id",
        "request_event_id",
        "sender_matrix_id",
        "content_sha256",
    }
    try:
        if "message_context" in args:
            if legacy_fields & set(args):
                raise ValueError(
                    "message_context cannot be combined with legacy Matrix fields"
                )
            # 非法摘要先剔除再规范化：调用方可能误把附件路径/文件名/包校验值
            # 当作 content_sha256 传入（zeroclaw 2026-09-01 实测案例），旧规则
            # 下的这种请求不应被 400 卡断——忽略垃圾值，走自动补算。
            raw_context = dict(args["message_context"])
            raw_digest = str(raw_context.get("content_sha256") or "").strip()
            if raw_digest and not is_valid_content_sha256(raw_digest):
                raw_context.pop("content_sha256", None)
            message_context = normalize_message_context(raw_context)
            # 缺 content_sha256（或占位值）时自动按消息原文计算：
            # 票据绑定的摘要即 OPS 实际路由的消息文本，agent 无需本地预计算
            # SHA-256（多端 hex 大小写/编码差异是常见阻断源）。
            if is_unbound_content_sha256(message_context.content_sha256):
                message_context = MessageContext(
                    channel=message_context.channel,
                    channel_account_id=message_context.channel_account_id,
                    conversation_id=message_context.conversation_id,
                    message_id=message_context.message_id,
                    sender_id=message_context.sender_id,
                    content_sha256=hashlib.sha256(
                        message_text.encode("utf-8")
                    ).hexdigest(),
                )
        else:
            legacy_context = {
                key: args[key]
                for key in legacy_fields
                if key in args
            }
            # 同上：顶层兼容字段的垃圾摘要直接忽略，走自动补算
            legacy_digest = str(legacy_context.get("content_sha256") or "").strip()
            if legacy_digest and not is_valid_content_sha256(legacy_digest):
                legacy_context.pop("content_sha256", None)
            if not legacy_context.get("content_sha256"):
                legacy_context["content_sha256"] = hashlib.sha256(
                    message_text.encode("utf-8")
                ).hexdigest()
            message_context = normalize_message_context(legacy_context)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 获取所有系统配置
    systems_dict = get_all_systems()
    systems = [
        {**system, "name": system.get("name") or name}
        for name, system in systems_dict.items()
    ]

    # 解析路由
    try:
        decision = resolve_message_target(message_text, systems)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail=f"Invalid routing approver configuration: {exc}",
        ) from exc

    configured_identities = [dict(item) for item in decision.approvers]
    effective_identities: list[dict[str, str]] = []
    if decision.outcome == RoutingOutcome.RESOLVED:
        effective_identities = _effective_approver_identities(
            ctx,
            configured_identities,
            channel=message_context.channel,
            channel_account_id=message_context.channel_account_id,
        )
    approver_sender_ids = [item["sender_id"] for item in effective_identities]
    approver_actor_keys = [
        f'{item["channel"]}:{item["channel_account_id"]}:{item["sender_id"]}'
        for item in effective_identities
    ]

    if decision.outcome != RoutingOutcome.RESOLVED:
        return {
            "outcome": decision.outcome.value,
            "system_name": None,
            "service_name": None,
            "matched_by": None,
            "candidates": list(decision.candidates),
            "message_context": message_context.to_dict(),
            "configured_approver_identities": configured_identities,
            "approver_identities": [],
            "approver_actor_keys": [],
            "approvers": [],
            "ticket": None,
            "ticket_digest": None,
            "routing_config_revision": decision.routing_config_revision,
        }

    # 签发路由票据
    ticket = issue_ticket(
        message_context=message_context,
        system_name=decision.system_name,
        service_name=decision.service_name,
        routing_config_revision=decision.routing_config_revision,
    )

    return {
        "outcome": decision.outcome.value,
        "system_name": decision.system_name,
        "service_name": decision.service_name,
        "matched_by": decision.matched_by,
        "candidates": list(decision.candidates),
        "message_context": message_context.to_dict(),
        "configured_approver_identities": configured_identities,
        "approver_identities": effective_identities,
        "approver_actor_keys": approver_actor_keys,
        "approvers": approver_sender_ids,
        "allowed_rooms": _resolve_system_rooms(decision.system_name),
        "ticket": ticket.ticket,
        "ticket_digest": ticket.digest,
        "routing_config_revision": decision.routing_config_revision,
        "next_step": (
            "票据已签发（15 分钟内有效）。请在同一轮内立即调用 "
            "ops.approval.prepare_plan：message_context 原样回传本结果的 "
            "message_context（勿改任何字段）、routing_ticket=本结果 ticket、"
            "system_name=本结果 system_name、environment、steps、policy。"
            "不要在两步之间停下回复用户。"
        ),
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.prepare_* — 准备审批工单
# ──────────────────────────────────────────────────────────────

def _common_prepare_schema() -> dict:
    """prepare_* 工具的公共输入 schema。"""
    return {
        "type": "object",
        "properties": {
            "message_context": message_context_schema(),
            "routing_ticket": {"type": "string", "description": "完整签名路由票据"},
            "room_id": {"type": "string", "description": "兼容字段：Matrix 房间 ID"},
            "request_event_id": {"type": "string", "description": "兼容字段：Matrix 请求事件 ID"},
            "event_id": {"type": "string", "description": "兼容字段：Matrix 事件 ID"},
            "sender_matrix_id": {"type": "string", "description": "兼容字段：Matrix 发起人 ID"},
            "content_sha256": {"type": "string", "description": "兼容字段：消息内容 SHA-256"},
            "system_name": {"type": "string", "description": "目标系统名"},
            "service_name": {"type": "string", "description": "目标服务名（可选）"},
            "environment": {"type": "string", "description": "环境（test/staging/prod）"},
            "ai_reason": {"type": "string", "description": "AI 建议此操作的理由"},
        },
        "required": [
            "routing_ticket",
            "system_name",
            "environment",
        ],
        "additionalProperties": False,
    }


# 动作类型 → 中文动词（reply_template 步骤摘要用，与 approval_phrase 保持一致）
_STEP_VERBS = {
    "RELEASE": "发布",
    "SERVICE_CONTROL": "服务控制",
    "HEALTH_CHECK": "健康检查",
    "FILE_UPLOAD": "上传制品",
    "ROLLBACK": "回滚",
    "DML": "SQL变更",
    "PACKAGE_CLEANUP": "包清理",
    "MATRIX_PULL": "拉取附件",
}


def _approval_reply_template(
    *,
    kind: str,
    status: str,
    object_id: str,
    short_code: str,
    system_name: str,
    service_name: str,
    environment: str,
    targets,
    expires_at,
    steps=None,
    approvers=None,
    package_size_bytes=None,
    package_sha256: str = "",
) -> str:
    """生成固定格式的中文 Markdown 审批回执（Agent 直接转发，禁止自由发挥）。

    责任分配：OPS 是格式的唯一事实源——结构化字段供 Agent 程序化消费，
    reply_template 供 Agent 原样展示到房间（Matrix msgtype=m.text 可直接使用）。
    单独的可复制审批行（```text 批准 <短码>```）在消息末尾。
    """
    from datetime import datetime, timezone, timedelta

    def _fmt_expiry(value) -> str:
        if not value:
            return "-"
        try:
            dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
        beijing = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        beijing = beijing.astimezone(timezone(timedelta(hours=8)))
        return beijing.strftime("%Y-%m-%d %H:%M:%S") + "（北京时间）"

    steps = steps or []
    if steps:
        step_line = " → ".join(
            _STEP_VERBS.get(str(s.get("action_type") or s.get("action_type")).strip().upper()
                            if isinstance(s, dict) else str(getattr(s, "action_type", "")),
                            str(getattr(s, "action_type", "")) if not isinstance(s, dict) else "")
            for s in steps
        )
    else:
        step_line = ""

    noun = "执行计划" if kind == "plan" else "审批工单"
    lines = [
        f"OPS 审批{noun}已创建，当前状态：`{status}`",
        "",
        f"- {('计划' if kind == 'plan' else '工单')} ID：`{object_id}`",
        f"- 确认短语：`{short_code}`",
    ]
    if approvers:
        lines.append(f"- 指定审批人：`{'、'.join(approvers)}`")
    if service_name:
        lines.append(f"- 服务：`{service_name}`")
    if system_name:
        lines.append(f"- 系统：`{system_name}`")
    if environment:
        lines.append(f"- 环境：`{environment}`")
    if targets:
        lines.append(f"- 目标：`{', '.join(list(targets))}`")
    if step_line:
        lines.append(f"- 步骤：{step_line}")
    if package_size_bytes:
        lines.append(f"- 包大小：`{int(package_size_bytes):,} bytes`")
    if package_sha256:
        lines.append(f"- SHA256：`{package_sha256}`")
    lines.append(f"- 有效期至：`{_fmt_expiry(expires_at)}`")
    lines += [
        "",
        "请指定审批人回复：",
        "",
        "```text",
        f"批准 {short_code}",
        "```",
    ]
    return "\n".join(lines)


def _display_approver(value: str) -> str:
    """通道身份 → 人名展示。

    matrix:default:@jun:hubtel.xyz → Jun（@jun:hubtel.xyz）
    @jack.han:hubtel.xyz            → Jack.han（@jack.han:hubtel.xyz）
    admin / matrix:default:u-1     → 原样（无 Matrix 格式可解析）
    保留完整 ID 括注——审计场景人名与身份都可见，不丢证据。
    """
    raw = str(value or "").strip()
    if not raw:
        return "-"
    # 剥通道前缀 matrix:default:
    mx = raw
    if mx.startswith("matrix:"):
        parts = mx.split(":", 2)
        if len(parts) == 3:
            mx = parts[2]
    if not (mx.startswith("@") and ":" in mx):
        return raw  # 非标准 Matrix 身份——原样
    local = mx[1:].split(":", 1)[0]
    if not local:
        return raw
    human = local.replace(".", " ").replace("_", " ").strip().capitalize()
    return f"{human}（{mx}）"


def _execution_reply_template(plan, *, grant_hint: str = "") -> str:
    """执行完成后的固定格式回执（Agent 原样转发，禁止自由发挥/重复播报过程）。

    与 prepare 的 _approval_reply_template 同一责任分配：OPS 是格式唯一事实源。
    内容收敛原则：只报结果态（每步骤一行 ✅/❌），不报过程流水（中间轮询/
    重试/房间扫描等由 Agent 自行消化，不进房间消息）。
    """
    steps = plan.steps or []
    step_lines = []
    for s in steps:
        verb = _STEP_VERBS.get(str(s.action_type or "").strip().upper(), s.action_type or "步骤")
        mark = "✅" if s.status == "SUCCEEDED" else ("❌" if s.status in ("FAILED", "SKIPPED") else "⏳")
        line = f"- {verb} {mark} `{s.status}`"
        # 结果摘要取第一行且截断——完整 result 在结构化字段里，房间消息不刷屏
        result_text = ""
        try:
            import json as _json
            raw = s.result
            if isinstance(raw, str):
                try:
                    raw = _json.loads(raw)
                except Exception:
                    pass
            if isinstance(raw, dict):
                for key in ("summary", "message", "detail", "error", "output"):
                    v = raw.get(key)
                    if v:
                        result_text = str(v)
                        break
                if not result_text:
                    result_text = _json.dumps(raw, ensure_ascii=False)
            elif raw is not None:
                result_text = str(raw)
        except Exception:
            result_text = ""
        if s.error_message:
            result_text = s.error_message
        if result_text:
            one_line = result_text.strip().splitlines()[0][:120]
            line += f"：{one_line}"
        step_lines.append(line)

    status_emoji = {"SUCCEEDED": "✅", "FAILED": "❌", "PARTIAL_FAILED": "⚠️"}.get(plan.status, "⏳")
    lines = [
        f"{status_emoji} 执行{'成功完成' if plan.status == 'SUCCEEDED' else '已结束'}",
        "",
        f"计划 `{plan.id[:8]}...` {plan.status}（{len(steps)} 步骤）",
        "",
        *step_lines,
        "",
        f"审批人：{_display_approver(plan.approved_by or '')}{'（' + grant_hint + '）' if grant_hint else ''}｜审计 ID {plan.id}",
    ]
    if plan.system_name:
        scope = f"{plan.system_name}"
        if plan.service_name:
            scope += f"/{plan.service_name}"
        if plan.environment:
            scope += f"（{plan.environment}）"
        lines.append(f"对象：`{scope}`")
    if plan.failure_reason:
        lines.append(f"失败原因：`{plan.failure_reason[:200]}`")
    lines.append("")
    lines.append("（过程细节已收敛，如需完整证据链：审计页 → 操作链路回放）")
    return "\n".join(lines)


def _routing_systems() -> list[dict]:
    return [
        {**system, "name": system.get("name") or name}
        for name, system in get_all_systems().items()
    ]


def _validated_prepare_ticket(args, ctx):
    """Validate a prepare request before any approval or plan is created."""
    if "routing_config_revision" in args or "routing_ticket_digest" in args:
        raise HTTPException(
            status_code=400,
            detail="routing revision and ticket digest are computed by OPS",
        )

    legacy_fields = {
        "room_id",
        "request_event_id",
        "event_id",
        "sender_matrix_id",
        "content_sha256",
    }
    try:
        if "message_context" in args:
            if legacy_fields & set(args):
                raise ValueError(
                    "message_context cannot be combined with legacy Matrix fields"
                )
            # 非法摘要（附件路径/文件名等误传）剔除后再构造，走票据反填
            raw_context = dict(args["message_context"])
            raw_digest = str(raw_context.get("content_sha256") or "").strip()
            if raw_digest and not is_valid_content_sha256(raw_digest):
                raw_context.pop("content_sha256", None)
            context = MessageContext.from_dict(raw_context)
        else:
            legacy_context = {key: args[key] for key in legacy_fields if key in args}
            legacy_digest = str(legacy_context.get("content_sha256") or "").strip()
            if legacy_digest and not is_valid_content_sha256(legacy_digest):
                legacy_context.pop("content_sha256", None)
            context = normalize_message_context(legacy_context)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    routing_ticket = str(args.get("routing_ticket") or "").strip()
    if not routing_ticket:
        raise HTTPException(status_code=400, detail="routing_ticket is required")

    # 缺 content_sha256（或占位值）时从签名票据 payload 反填：摘要来自服务端
    # 签发的票据（比信任调用方传值更安全），票据校验随后仍按完整上下文硬比对。
    # 非法摘要（路径/文件名误传）已在构造前剔除，同样落到这里走反填。
    if is_unbound_content_sha256(context.content_sha256):
        ticket_payload = _decode_ticket_payload(routing_ticket)
        ticket_context = (ticket_payload or {}).get("message_context") or {}
        bound_digest = str(ticket_context.get("content_sha256") or "").strip()
        if not bound_digest or is_unbound_content_sha256(bound_digest):
            raise HTTPException(
                status_code=400,
                detail="message_context missing content_sha256 and routing ticket "
                "does not carry a bound digest; call ops.routing.resolve_message_target "
                "first and pass its returned message_context",
            )
        context = MessageContext(
            channel=context.channel,
            channel_account_id=context.channel_account_id,
            conversation_id=context.conversation_id,
            message_id=context.message_id,
            sender_id=context.sender_id,
            content_sha256=bound_digest,
        )

    try:
        revision = compute_routing_revision(_routing_systems())
        # revision 仅作信息返回（routing_config_revision），不再作为票据有效性的
        # 硬校验条件：它是全局配置快照，任何系统路由字段变更都会使其改变，硬比对
        # 会让在途票据在配置变动时集体失效。房间作用域与审批人仍按当前配置重新校验。
        expected_service_name = args.get("service_name")
        if not expected_service_name:
            # 调用方未显式传服务名时，读取票据自身绑定的服务名作为期望值：
            # 服务级 ticket（如 crypto-trader-web）必须能被 prepare_plan 接受，
            # 而不是与 None 硬比较失败。（_decode_ticket_payload 已模块级导入）
            ticket_payload = _decode_ticket_payload(routing_ticket)
            if isinstance(ticket_payload, dict) and ticket_payload.get("service_name"):
                expected_service_name = ticket_payload.get("service_name")
        valid = verify_ticket(
            routing_ticket,
            expected_message_context=context,
            expected_system_name=args["system_name"],
            expected_service_name=expected_service_name,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail=f"Current routing configuration is invalid: {exc}",
        ) from exc
    if not valid:
        raise HTTPException(
            status_code=403,
            detail="Routing ticket is invalid, expired, or bound to another message target",
        )

    _enforce_system_room(context, args["system_name"])

    ticket_digest = hashlib.sha256(routing_ticket.encode("utf-8")).hexdigest()
    return context, revision, ticket_digest


def _freeze_file_upload_parameters(raw_parameters: dict, db, *, require_package_name: bool = False):
    raw_parameters = dict(raw_parameters or {})
    if require_package_name and not raw_parameters.get("package_name"):
        raise HTTPException(
            status_code=400,
            detail="FILE_UPLOAD steps in a batch plan must reference an OPS File Center package_name",
        )

    source, filename = _resolve_source(raw_parameters)
    remote_path = _remote_file_path(raw_parameters.get("remote_path"))
    overwrite = bool(raw_parameters.get("overwrite", False))
    confirm_path = str(raw_parameters.get("confirm_path") or "")
    if overwrite and confirm_path != remote_path:
        raise HTTPException(status_code=400, detail="Overwriting requires confirm_path equal to remote_path")

    policy = get_package_retention_policy(db) if db is not None else None
    inspection = inspect_package_file(source, filename=filename, policy=policy, calculate_sha256=True)
    if inspection.get("blockers"):
        raise HTTPException(status_code=400, detail="; ".join(inspection["blockers"]))
    package_sha256 = str(inspection.get("sha256") or "").lower()
    package_size_bytes = int(inspection.get("size_bytes") or source.stat().st_size)
    supplied_sha256 = str(raw_parameters.get("expected_sha256") or "").strip().lower()
    if supplied_sha256 and supplied_sha256 != package_sha256:
        raise HTTPException(status_code=409, detail="expected_sha256 does not match the local package")
    if raw_parameters.get("expected_size_bytes") is not None:
        try:
            supplied_size = int(raw_parameters["expected_size_bytes"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="expected_size_bytes must be an integer")
        if supplied_size != package_size_bytes:
            raise HTTPException(status_code=409, detail="expected_size_bytes does not match the local package")

    action_parameters = {
        "remote_path": remote_path,
        "overwrite": overwrite,
        "expected_sha256": package_sha256,
        "expected_size_bytes": package_size_bytes,
    }
    if raw_parameters.get("package_name"):
        action_parameters["package_name"] = filename
    else:
        action_parameters["local_path"] = str(source)
    if raw_parameters.get("filename") or not raw_parameters.get("package_name"):
        action_parameters["filename"] = filename
    if confirm_path:
        action_parameters["confirm_path"] = confirm_path
    return action_parameters, filename, package_sha256, package_size_bytes, remote_path


def _steps_fed_by_matrix_pull(steps: list[dict]) -> set[str]:
    """返回依赖链上存在 MATRIX_PULL 步骤的 step_key 集合。

    这些步骤的包来自审批后才执行的 MATRIX_PULL 入库，创建计划时包尚不存在，
    不能冻结其 SHA256/大小，需延迟到执行时从前置步骤结果回填。
    """
    matrix_keys = {
        step.get("step_key")
        for step in steps
        if str(step.get("action_type") or "").strip() == "MATRIX_PULL"
    }
    fed: set[str] = set()
    changed = True
    while changed:
        changed = False
        for step in steps:
            key = step.get("step_key")
            if key in fed:
                continue
            deps = [d for d in (step.get("dependencies") or [])]
            if any(d in matrix_keys or d in fed for d in deps):
                fed.add(key)
                changed = True
    return fed


def _defer_matrix_pull_fed_upload(raw_parameters: dict) -> dict:
    """为依赖 MATRIX_PULL 的 FILE_UPLOAD 步骤做仅静态字段冻结。

    包名、SHA256、大小在审批执行后由 MATRIX_PULL 结果回填，这里只校验并保留
    远端路径等与包无关的静态字段，标记 defer_package_from_dependency 供执行器识别。
    """
    remote_path = _remote_file_path(raw_parameters.get("remote_path"))
    overwrite = bool(raw_parameters.get("overwrite", False))
    confirm_path = str(raw_parameters.get("confirm_path") or "")
    if overwrite and confirm_path != remote_path:
        raise HTTPException(
            status_code=400, detail="Overwriting requires confirm_path equal to remote_path"
        )
    deferred = {
        "remote_path": remote_path,
        "overwrite": overwrite,
        "defer_package_from_dependency": True,
    }
    if confirm_path:
        deferred["confirm_path"] = confirm_path
    return deferred


def _normalize_matrix_pull_steps(steps: list[dict], message_context, args: dict | None = None) -> list[dict]:
    """计划步骤参数规范化（2026-09-01，两级）。

    agent 常按步骤惯例把业务参数嵌在 action_parameters 里（实测
    plan 5e8f3551 的 MATRIX_PULL room_id/sender 与 plan fab55b7a 的
    SERVICE_CONTROL system_name/service_name 均因嵌套而执行失败，
    执行器历史版本只从顶层读取）。这里在计划创建时统一处理：

    - MATRIX_PULL：参数提升到顶层 + 用计划请求上下文补全 room_id/sender
      ——它的语义就是"拉取触发本次工单的那条消息的附件"
    - SERVICE_CONTROL：system_name/service_name 补全（顶层 args →
      action_parameters → 计划级 args），让系统级计划（service=None）
      的服务控制在创建时就有明确服务名，而不是执行时才炸
    """
    normalized = []
    for raw_step in steps:
        step = dict(raw_step)
        action_type = str(step.get("action_type") or "").strip()
        if action_type == "MATRIX_PULL":
            parameters = dict(step.get("parameters") or {})
            action_parameters = dict(parameters.get("action_parameters") or {})
            merged = {**action_parameters, **{k: v for k, v in parameters.items() if k != "action_parameters"}}
            if not str(merged.get("room_id") or "").strip():
                merged["room_id"] = message_context.conversation_id
            if not str(merged.get("sender") or "").strip():
                merged["sender"] = message_context.sender_id
            parameters = {k: v for k, v in merged.items() if k != "action_parameters"}
            step["parameters"] = parameters
        elif action_type == "SERVICE_CONTROL":
            parameters = dict(step.get("parameters") or {})
            action_parameters = dict(parameters.get("action_parameters") or {})
            plan_level = args or {}
            for key, aliases in (
                ("system_name", ("system",)),
                ("service_name", ("service",)),
                ("environment", ()),
                ("targets", ()),
            ):
                value = parameters.get(key)
                if value is None or (isinstance(value, str) and not value.strip()):
                    value = action_parameters.get(key)
                for alias in aliases:
                    if value is None or (isinstance(value, str) and not value.strip()):
                        value = parameters.get(alias) or action_parameters.get(alias)
                if (value is None or (isinstance(value, str) and not value.strip())) and plan_level.get(key):
                    value = plan_level[key]
                if value is not None and not (isinstance(value, str) and not value.strip()):
                    parameters[key] = value
            step["parameters"] = parameters
        normalized.append(step)
    return normalized


def _freeze_file_upload_plan_steps(steps: list[dict], db) -> list[dict]:
    frozen_steps = []
    fed_by_matrix = _steps_fed_by_matrix_pull(steps)
    for raw_step in steps:
        step = dict(raw_step)
        if str(step.get("action_type") or "").strip() == "FILE_UPLOAD":
            parameters = dict(step.get("parameters") or {})
            action_parameters = dict(parameters.get("action_parameters") or {})
            if step.get("step_key") in fed_by_matrix and not action_parameters.get("package_name"):
                parameters["action_parameters"] = _defer_matrix_pull_fed_upload(action_parameters)
            else:
                action_parameters, _, _, _, _ = _freeze_file_upload_parameters(
                    action_parameters,
                    db,
                    require_package_name=True,
                )
                parameters["action_parameters"] = action_parameters
            step["parameters"] = parameters
        frozen_steps.append(step)
    return frozen_steps


# ──────────────────────────────────────────────────────────────
# ops.approval.prepare_service_control — 服务控制审批
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.approval.prepare_service_control",
    title="准备服务控制审批",
    description="为服务控制操作（重启/停止/启动/更新）创建不可变审批工单，返回一次性确认短语与固定格式回执 reply_template。Agent 应把 reply_template 原样发送到房间（中文 Markdown + 可复制审批行「批准 <短语>」），不要自由改写。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            **_common_prepare_schema()["properties"],
            "control_action": {
                "type": "string",
                "enum": ["restart", "stop", "start", "update"],
                "description": "控制操作类型：restart / stop / start / update（update=拉取镜像并重新部署）",
            },
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "目标服务器列表",
            },
            "action_parameters": {
                "type": "object",
                "description": "额外参数（如 compose_service 指定 Docker Compose 服务名、命令覆盖等）",
            },
        },
        "required": _common_prepare_schema()["required"] + ["control_action", "targets"],
        "additionalProperties": False,
    },
)
def approval_prepare_service_control(args, ctx, db):
    message_context, routing_revision, ticket_digest = _validated_prepare_ticket(
        args, ctx
    )
    control_action = args["control_action"]
    action_label = {"restart": "重启", "stop": "停止", "start": "启动", "update": "更新"}.get(control_action, control_action)
    approvers = _lookup_approvers(
        ctx,
        args["system_name"],
        args.get("service_name"),
        channel=message_context.channel,
        channel_account_id=message_context.channel_account_id,
    )
    service = ActionApprovalService(db)
    approval, short_code = service.prepare(
        action_type="SERVICE_CONTROL",
        tool_name="ops.approval.prepare_service_control",
        message_context=message_context,
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=args["targets"],
        action_parameters={
            "control_action": control_action,
            **args.get("action_parameters", {}),
        },
        routing_config_revision=routing_revision,
        routing_ticket_digest=ticket_digest,
        risk_level="high",
        ai_reason=args.get("ai_reason", ""),
        authorized_identities=[
            {"channel": message_context.channel, "channel_account_id": message_context.channel_account_id, "sender_id": sender_id}
            for sender_id in approvers
        ],
    )
    reply_template = _approval_reply_template(
        kind="action",
        status=approval.status,
        object_id=approval.id,
        short_code=short_code,
        system_name=args["system_name"],
        service_name=args.get("service_name") or "",
        environment=args["environment"],
        targets=args["targets"],
        expires_at=approval.expires_at,
        steps=[{"action_type": "SERVICE_CONTROL"}],
        approvers=approvers,
    )
    return {
        "approval_id": approval.id,
        "short_code": short_code,
        "action_digest": approval.action_digest,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "status": approval.status,
        "control_action": control_action,
        "action_label": action_label,
        "targets": args["targets"],
        "authorized_approvers": approvers,
        # 固定格式回执模板：Agent 原样转发到房间，不要自由改写
        "reply_template": reply_template,
    }


@registry.register(
    name="ops.approval.prepare_file_upload",
    title="Prepare file upload approval",
    description=(
        "Create a one-time Element approval for uploading a validated package to one or more "
        "configured servers. The package checksum, size, targets, and remote path are frozen "
        "in the approval; approval execution performs the SFTP upload only."
    ),
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            **_common_prepare_schema()["properties"],
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Configured target server names",
            },
            "action_parameters": {
                "type": "object",
                "properties": {
                    "package_name": {"type": "string"},
                    "local_path": {"type": "string"},
                    "filename": {"type": "string"},
                    "remote_path": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                    "confirm_path": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                    "expected_size_bytes": {"type": "integer"},
                },
                "required": ["remote_path"],
                "additionalProperties": False,
            },
        },
        "required": _common_prepare_schema()["required"] + ["targets", "action_parameters"],
        "additionalProperties": False,
    },
)
def approval_prepare_file_upload(args, ctx, db):
    message_context, routing_revision, ticket_digest = _validated_prepare_ticket(
        args, ctx
    )

    targets = [str(item or "").strip() for item in (args.get("targets") or [])]
    if not targets or any(not item for item in targets):
        raise HTTPException(status_code=400, detail="File upload approval requires at least one target server")
    if len(set(targets)) != len(targets):
        raise HTTPException(status_code=400, detail="Duplicate target servers are not allowed")

    action_parameters, filename, package_sha256, package_size_bytes, remote_path = _freeze_file_upload_parameters(
        args.get("action_parameters") or {},
        db,
    )

    approvers = _lookup_approvers(
        ctx,
        args["system_name"],
        args.get("service_name"),
        channel=message_context.channel,
        channel_account_id=message_context.channel_account_id,
    )
    service = ActionApprovalService(db)
    approval, short_code = service.prepare(
        action_type="FILE_UPLOAD",
        tool_name="ops.approval.prepare_file_upload",
        message_context=message_context,
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=targets,
        action_parameters=action_parameters,
        routing_config_revision=routing_revision,
        routing_ticket_digest=ticket_digest,
        risk_level="high",
        ai_reason=args.get("ai_reason", ""),
        package_name=filename,
        package_sha256=package_sha256,
        package_size_bytes=package_size_bytes,
        authorized_identities=[
            {"channel": message_context.channel, "channel_account_id": message_context.channel_account_id, "sender_id": sender_id}
            for sender_id in approvers
        ],
    )
    return {
        "approval_id": approval.id,
        "short_code": short_code,
        "action_type": approval.action_type,
        "action_digest": approval.action_digest,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "status": approval.status,
        "system_name": args["system_name"],
        "service_name": args.get("service_name"),
        "targets": targets,
        "package_name": filename,
        "package_size_bytes": package_size_bytes,
        "package_sha256": package_sha256,
        "remote_path": remote_path,
        "overwrite": action_parameters.get("overwrite", False),
        "authorized_approvers": approvers,
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.execute / reject / get — 审批操作
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.approval.execute",
    title="执行已审批的操作",
    description="消费审批确认短语（如「批准服务控制 crypto-trader@test A3F9C2D1」），验证通过后将审批工单标记为 EXECUTING 并触发实际操作（部署/回滚/DML/包清理）。安全门禁是确认短语本身（一次性、15 分钟过期、绑定房间+事件、内容指纹防跨单复用），而非调用方 token scope，因此只需 ops:read。",
    scopes=["ops:read"],
    risk="low",
    category="approval_execute",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "approval_id": {"type": "string", "description": "审批工单 ID"},
            "short_code": {"type": "string", "description": "一次性确认短语，来自 prepare 返回（如 批准服务控制 crypto-trader@test A3F9C2D1）"},
            "message_context": message_context_schema(),
            "approver_matrix_id": {"type": "string", "description": "审批人的 Matrix user ID"},
            "room_id": {"type": "string", "description": "Matrix 房间 ID"},
            "approval_event_id": {"type": "string", "description": "审批消息的 Matrix 事件 ID"},
        },
        "required": ["approval_id", "short_code"],
        "additionalProperties": False,
    },
)
def approval_execute(args, ctx, db):
    # execute is called from the same room that prepared the approval, so the
    # room_id is the natural anchor for the binding check.
    approval_context = normalize_message_context(
        args.get("message_context") or {
            "room_id": args.get("room_id"),
            "request_event_id": args.get("approval_event_id"),
            "sender_matrix_id": args.get("approver_matrix_id"),
            "content_sha256": args.get("content_sha256") or "0" * 64,
        }
    )
    service = ActionApprovalService(db)
    approval = service.consume(
        approval_id=args["approval_id"],
        short_code=args["short_code"],
        approver_matrix_id=approval_context.sender_id,
        room_id=approval_context.conversation_id,
        approval_event_id=approval_context.message_id,
        approval_context=approval_context,
    )
    if not approval:
        return {
            "ok": False,
            "error": "确认短语无效、已过期、已被消费或房间不匹配",
            "approval_id": args["approval_id"],
        }

    # 同步执行已审批操作。单机部署不需要异步 job 队列，consume 成功后
    # 立即在当前事务中执行，执行结果直接反映在 approval.status 上。
    from app.services.approval_executor import ApprovalExecutor
    executor = ApprovalExecutor(db)
    approval = executor.execute(approval.id)

    if not approval:
        return {
            "ok": False,
            "error": "执行器未找到审批工单",
            "approval_id": args["approval_id"],
        }

    return {
        "ok": approval.status == "SUCCEEDED",
        "approval_id": approval.id,
        "message_context": {
            "channel": approval.channel,
            "channel_account_id": approval.channel_account_id,
            "conversation_id": approval.conversation_id,
            "message_id": approval.request_message_id,
            "sender_id": approval.request_sender_id,
            "content_sha256": approval.content_sha256,
        },
        "action_type": approval.action_type,
        "status": approval.status,
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at.isoformat() if approval.approved_at else None,
        "executed_at": approval.executed_at.isoformat() if approval.executed_at else None,
        "execution_result": approval.execution_result,
        "failure_reason": approval.failure_reason,
        "message": (
            f"审批已通过，操作 {approval.action_type} 执行{'成功' if approval.status == 'SUCCEEDED' else '失败'}"
            if approval.status in ("SUCCEEDED", "FAILED")
            else f"审批已通过，操作 {approval.action_type} 状态: {approval.status}"
        ),
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.list — 审批列表
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.approval.list",
    title="查询审批列表",
    description="查询审批工单列表，可按状态、操作类型过滤。",
    scopes=["ops:read"],
    risk="low",
    category="approval_read",
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "状态过滤"},
            "action_type": {"type": "string", "description": "操作类型过滤"},
            "limit": {"type": "integer", "description": "返回数量，默认 50"},
            "message_context": message_context_schema(),
            "room_id": {
                "type": "string",
                "description": "Legacy Matrix room ID",
            },
        },
        "additionalProperties": False,
    },
)
def approval_list(args, ctx, db):
    from app.db.models import AiActionApproval

    query = db.query(AiActionApproval)
    if "message_context" in args:
        context = normalize_message_context(args["message_context"])
        query = query.filter(
            AiActionApproval.channel == context.channel,
            AiActionApproval.channel_account_id == context.channel_account_id,
            AiActionApproval.conversation_id == context.conversation_id,
        )
    elif "room_id" in args:
        context = normalize_message_context({
            "room_id": str(args.get("room_id") or "").strip(),
            "request_event_id": "approval-list",
            "sender_matrix_id": "approval-list",
            "content_sha256": "0" * 64,
        })
        query = query.filter(
            AiActionApproval.channel == context.channel,
            AiActionApproval.channel_account_id == context.channel_account_id,
            AiActionApproval.conversation_id == context.conversation_id,
        )
    if args.get("status"):
        query = query.filter(AiActionApproval.status == args["status"])
    if args.get("action_type"):
        query = query.filter(AiActionApproval.action_type == args["action_type"])
    items = query.order_by(AiActionApproval.created_at.desc()).limit(int(args.get("limit", 50))).all()

    return {
        "ok": True,
        "total": len(items),
        "items": [
            {
                "approval_id": a.id,
                "action_type": a.action_type,
                "status": a.status,
                "system_name": (a.request_payload or {}).get("system_name"),
                "environment": (a.request_payload or {}).get("environment"),
                "risk_level": a.risk_level,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                "approved_by": a.approved_by,
                "message_context": {
                    "channel": a.channel,
                    "channel_account_id": a.channel_account_id,
                    "conversation_id": a.conversation_id,
                    "message_id": a.request_message_id,
                    "sender_id": a.request_sender_id,
                    "content_sha256": a.content_sha256,
                },
            }
            for a in items
        ],
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.expire_stale — 过期清理
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# ops.approval.prepare_plan / execute_plan — 消息级执行计划
# ──────────────────────────────────────────────────────────────
#
# 一条 Element 消息对应一个 ExecutionPlan：授权人批准一次后，计划内
# 声明的多个步骤按顺序自动执行，不再逐步骤审批。
# 注意：这些工具绝不调用旧 prepare_* 审批工具（避免审批递归）。


def _plan_steps_schema() -> dict:
    """执行计划步骤的 JSON schema（用于 prepare_plan 输入）。"""
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "step_key": {"type": "string", "description": "计划内唯一步骤键"},
                "action_type": {"type": "string", "description": "Step type: SERVICE_CONTROL / FILE_UPLOAD / HEALTH_CHECK / RELEASE / ROLLBACK / DML / PACKAGE_CLEANUP / MATRIX_PULL（从 Matrix 房间拉取附件到文件中心，parameters: room_id, sender, minutes?, filename?, system?, service?, overwrite?）"},
                "parameters": {"type": "object", "description": "冻结的步骤参数"},
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "依赖的前置步骤 step_key 列表",
                },
            },
            "required": ["step_key", "action_type"],
            "additionalProperties": False,
        },
    }


@registry.register(
    name="ops.approval.prepare_plan",
    title="准备消息级执行计划审批",
    description="为一条 Element 消息的完整执行流程创建不可变执行计划，返回一次性确认短语与固定格式回执 reply_template。Agent 应把 reply_template 原样发送到房间（Matrix msgtype=m.text，中文 Markdown：计划ID/确认短语/审批人/服务/环境/目标/步骤/包大小/SHA256/有效期 + 可复制审批行「批准 <短语>」），不要自由改写、截断或重排。授权人批准一次后，计划内所有步骤按顺序自动执行。相同计划内容（plan_digest）的待审批计划幂等复用，不生成新短语。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            **_common_prepare_schema()["properties"],
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "目标服务器列表",
            },
            "steps": _plan_steps_schema(),
            "policy": {
                "type": "object",
                "description": "执行策略（如 continue_on_error / max_retries）",
            },
            "risk_level": {"type": "string", "description": "风险等级（low/medium/high）"},
            "temporary_grant_id": {
                "type": "string",
                "description": "临时自审批授权 ID（可选；存在时校验当前请求方为活跃受益人）",
            },
            "grant_self_approval": {
                "type": "boolean",
                "description": "请求方申请自审批路径（可选；存在活跃授权时自动附加）",
            },
        },
        "required": _common_prepare_schema()["required"] + ["steps"],
        "additionalProperties": False,
    },
)
def approval_prepare_plan(args, ctx, db):
    """创建消息级执行计划并返回一次性确认短语。

    若存在活跃临时自审批授权且当前请求方为授权受益人，则把受益人加入
    授权身份集合并记录 temporary_grant_id，使受益人可通过自审批路径消费计划。
    原始审批人始终保留在授权身份中。
    """
    from app.services.temporary_approval import TemporaryApprovalService

    message_context, routing_revision, ticket_digest = _validated_prepare_ticket(
        args, ctx
    )
    approvers = _lookup_approvers(
        ctx,
        args["system_name"],
        args.get("service_name"),
        channel=message_context.channel,
        channel_account_id=message_context.channel_account_id,
    )
    steps = _freeze_file_upload_plan_steps(
        _normalize_matrix_pull_steps(args["steps"], message_context, args), db
    )
    service = ExecutionPlanService(db)

    # 临时自审批授权：请求方为活跃授权受益人时自动附加自审批路径。
    temporary_grant_id = None
    authorized = [
        {"channel": message_context.channel, "channel_account_id": message_context.channel_account_id, "sender_id": sender_id}
        for sender_id in approvers
    ]
    grant_service = TemporaryApprovalService(db)
    action_types = [step.get("action_type") for step in steps if isinstance(step, dict)]
    if grant_service.is_self_approval_allowed(
        actor_key=message_context.actor_key,
        system_name=args["system_name"],
        environment_name=args["environment"],
        action_types=action_types,
        message_context=message_context,
    ):
        active = grant_service.get_active_grant(
            actor_key=message_context.actor_key,
            system_name=args["system_name"],
            environment_name=args["environment"],
            message_context=message_context,
        )
        if active is not None:
            temporary_grant_id = active.id
            beneficiary_identity = {
                "channel": message_context.channel,
                "channel_account_id": message_context.channel_account_id,
                "sender_id": message_context.sender_id,
            }
            if beneficiary_identity not in authorized:
                authorized.append(beneficiary_identity)

    plan, short_code = service.prepare(
        message_context=message_context,
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=args.get("targets", []),
        steps=steps,
        policy=args.get("policy", {}),
        routing_config_revision=routing_revision,
        routing_ticket_digest=ticket_digest,
        risk_level=args.get("risk_level", "high"),
        ai_reason=args.get("ai_reason", ""),
        authorized_identities=authorized,
        temporary_grant_id=temporary_grant_id,
    )

    # 提取制品信息（FILE_UPLOAD 冻结参数；MATRIX_PULL 回填步骤无冻结值则缺省）
    package_size_bytes = None
    package_sha256 = ""
    for s in plan.steps:
        if str(s.action_type or "").strip() == "FILE_UPLOAD":
            ap = (s.parameters or {}).get("action_parameters") or {}
            if ap.get("expected_size_bytes"):
                package_size_bytes = int(ap["expected_size_bytes"])
            if ap.get("expected_sha256"):
                package_sha256 = str(ap["expected_sha256"]).lower()
            break

    reply_template = _approval_reply_template(
        kind="plan",
        status=plan.status,
        object_id=plan.id,
        short_code=short_code,
        system_name=plan.system_name,
        service_name=plan.service_name,
        environment=plan.environment,
        targets=plan.targets,
        expires_at=plan.expires_at,
        steps=plan.steps,
        approvers=approvers,
        package_size_bytes=package_size_bytes,
        package_sha256=package_sha256,
    )

    return {
        "plan_id": plan.id,
        "short_code": short_code,
        "plan_digest": plan.plan_digest,
        "status": plan.status,
        "expires_at": plan.expires_at.isoformat() if plan.expires_at else None,
        "system_name": plan.system_name,
        "service_name": plan.service_name,
        "environment": plan.environment,
        "targets": plan.targets,
        "package_size_bytes": package_size_bytes,
        "package_sha256": package_sha256,
        "step_count": len(plan.steps),
        "temporary_grant_id": plan.temporary_grant_id,
        "steps": [
            {
                "step_key": s.step_key,
                "step_order": s.step_order,
                "action_type": s.action_type,
                "status": s.status,
                "approval_details": step_approval_details(s.action_type, s.parameters, plan.targets),
            }
            for s in plan.steps
        ],
        "authorized_approvers": approvers,
        # 固定格式回执模板：Agent 应原样发送到房间（Matrix msgtype=m.text），
        # 不要自由改写/截断/重排；如需自定义展示，使用上面的结构化字段自组。
        "reply_template": reply_template,
        "next_step": (
            "把 reply_template 原样发到房间等待审批——这是唯一需要播报的消息；"
            "扫描房间/读事件/核对附件等中间动作静默执行，不要把过程复述到房间。"
            "当审批人在同一房间回复"
            f"「批准 {short_code}」后，立即调用 ops.approval.execute_plan："
            "plan_id=本结果 plan_id、short_code=审批消息里的完整短语、"
            "room_id=审批消息所在房间、approver_matrix_id=审批人 Matrix ID。"
            "不要因'缺少上下文'而停止——房间与审批人就是审批消息本身携带的，"
            "直接调用即可。"
        ),
    }


@registry.register(
    name="ops.approval.execute_plan",
    title="执行已审批的执行计划",
    description="消费一次性确认短语（如「批准发布+健康检查 crypto-trader@test A3F9C2D1」），验证通过后将执行计划置为可执行并按冻结步骤顺序执行。安全门禁是确认短语本身（一次性、15 分钟过期、绑定房间+事件、内容指纹防跨计划复用），而非调用方 token scope，因此只需 ops:read。",
    scopes=["ops:read"],
    risk="low",
    category="approval_execute",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "执行计划 ID"},
            "short_code": {"type": "string", "description": "一次性确认短语，来自 prepare_plan 返回（如 批准发布+健康检查 crypto-trader@test A3F9C2D1）"},
            "message_context": message_context_schema(),
            "approver_matrix_id": {"type": "string", "description": "审批人的 Matrix user ID"},
            "room_id": {"type": "string", "description": "Matrix 房间 ID"},
            "approval_event_id": {"type": "string", "description": "审批消息的 Matrix 事件 ID"},
        },
        "required": ["plan_id", "short_code"],
        "additionalProperties": False,
    },
)
def approval_execute_plan(args, ctx, db):
    """消费确认短语并执行已审批的计划。"""
    approval_context = normalize_message_context(
        args.get("message_context") or {
            "room_id": args.get("room_id"),
            "request_event_id": args.get("approval_event_id"),
            "sender_matrix_id": args.get("approver_matrix_id"),
            "content_sha256": args.get("content_sha256") or "0" * 64,
        }
    )
    service = ExecutionPlanService(db)
    plan = service.consume(
        plan_id=args["plan_id"],
        short_code=args["short_code"],
        approver_matrix_id=approval_context.sender_id,
        room_id=approval_context.conversation_id,
        approval_event_id=approval_context.message_id,
        approval_context=approval_context,
    )
    if not plan:
        return {
            "ok": False,
            "error": "确认短语无效、已过期、已被消费、房间不匹配或计划内容已变化",
            "plan_id": args["plan_id"],
        }

    # 同步执行计划内全部步骤
    from app.services.plan_executor import PlanExecutor
    executor = PlanExecutor(db)
    plan = executor.execute(plan.id)

    if not plan:
        return {
            "ok": False,
            "error": "执行器未找到执行计划",
            "plan_id": args["plan_id"],
        }

    result = {
        "ok": plan.status == "SUCCEEDED",
        "plan_id": plan.id,
        "message_context": {
            "channel": plan.channel,
            "channel_account_id": plan.channel_account_id,
            "conversation_id": plan.conversation_id,
            "message_id": plan.request_message_id,
            "sender_id": plan.request_sender_id,
            "content_sha256": plan.content_sha256,
        },
        "plan_digest": plan.plan_digest,
        "status": plan.status,
        "approved_by": plan.approved_by,
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        "failure_reason": plan.failure_reason,
        "steps": [
            {
                "step_key": s.step_key,
                "action_type": s.action_type,
                "status": s.status,
                "result": s.result,
                "error_message": s.error_message,
                "attempt_count": s.attempt_count,
            }
            for s in plan.steps
        ],
        # 固定格式回执模板：Agent 把它原样发送到房间作为唯一回报——
        # 不要再自行复述执行过程/步骤流水/中间轮询（会造成过程刷屏）。
        "reply_template": _execution_reply_template(plan),
        "message": (
            f"执行计划已完成: {plan.status}"
            if plan.status in ("SUCCEEDED", "FAILED", "PARTIAL_FAILED")
            else f"执行计划状态: {plan.status}"
        ),
    }
    if plan.status == "SUCCEEDED":
        result["next_step"] = (
            "把 reply_template 原样发送到房间作为唯一回报——这是本次执行的完整结果，"
            "不要追加执行过程复述、步骤流水、扫描/轮询细节或重复回执"
            "（用户已通过审批短语知情流程，房间消息只需要最终结果态）。"
        )
    elif plan.status == "PARTIAL_FAILED":
        failed_keys = [s.step_key for s in plan.steps if s.status == "FAILED"]
        succeeded_keys = [s.step_key for s in plan.steps if s.status == "SUCCEEDED"]
        result["next_step"] = (
            f"计划 {plan.id} 部分失败（成功: {succeeded_keys or '无'}；失败: {failed_keys}）。"
            "失败计划是终态：不能重试 execute_plan、不能用旧短语创建新计划。"
            "正确动作：①把 reply_template 原样发到房间（含失败步骤摘要）；"
            "②调用 ops.integration.save_lesson 回写踩坑"
            "（pattern/guidance/evidence 带计划ID）；③请用户重新触发完整流程"
            "（附件+消息同一条）获得新审批。"
        )
    elif plan.status == "FAILED":
        result["next_step"] = (
            f"计划 {plan.id} 执行失败：{plan.failure_reason or '见失败步骤 error_message'}。"
            "失败计划是终态：把 reply_template 原样发到房间；踩坑先 save_lesson 回写再回报；"
            "请用户重新触发完整流程。"
        )
    else:
        result["next_step"] = f"计划 {plan.id} 状态 {plan.status}：等待执行完成或人工介入。"
    return result


# ──────────────────────────────────────────────────────────────
# ops.approval.reject_plan — 拒绝执行计划
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.approval.reject_plan",
    title="拒绝执行计划",
    description="将一条待审批（PENDING_APPROVAL）的执行计划正式标记为 REJECTED 终态，阻止其后续执行。用于审批链路中把被否决/放弃的工单同步为已拒绝。幂等：计划已处于终态时返回其当前状态，不产生副作用；仅 PENDING_APPROVAL 可被拒绝。需要 ops:write 权限并由调用方身份（rejected_by）记录审计。",
    scopes=["ops:write"],
    risk="medium",
    category="approval_reject",
    write=True,
    input_schema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "待拒绝的执行计划 ID"},
            "reason": {"type": "string", "description": "拒绝原因（可选，落库为 failure_reason 以便审计）"},
            "message_context": message_context_schema(),
            "rejecter_matrix_id": {"type": "string", "description": "拒绝人的身份 ID（无 message_context 时使用，如 Matrix user ID / 用户名）"},
            "room_id": {"type": "string", "description": "来源房间 ID（兼容 legacy Matrix 调用）"},
            "request_event_id": {"type": "string", "description": "来源消息事件 ID（兼容 legacy Matrix 调用）"},
        },
        "required": ["plan_id"],
        "additionalProperties": False,
    },
)
def approval_reject_plan(args, ctx, db):
    """拒绝一条待审批的执行计划（幂等），记录拒绝者并可附拒绝原因。"""
    from app.db.models import ExecutionPlan

    plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == args["plan_id"]).first()
    if not plan:
        return {"ok": False, "error": "执行计划不存在", "plan_id": args["plan_id"]}

    if plan.status != "PENDING_APPROVAL":
        return {
            "ok": True,
            "plan_id": plan.id,
            "status": plan.status,
            "note": "计划已处于终态，无需重复拒绝",
        }

    rejecter = ""
    reject_ctx = None
    raw_ctx = args.get("message_context")
    if raw_ctx:
        try:
            reject_ctx = normalize_message_context(raw_ctx)
            rejecter = reject_ctx.sender_id
        except ValueError:
            reject_ctx = None
    if not rejecter:
        rejecter = (
            args.get("rejecter_matrix_id")
            or args.get("approver_matrix_id")
            or getattr(ctx, "username", "")
            or "system"
        )

    service = ExecutionPlanService(db)
    plan = service.reject(
        plan_id=args["plan_id"],
        rejecter_matrix_id=rejecter,
        rejection_context=reject_ctx,
    )
    if not plan:
        current = (
            db.query(ExecutionPlan).filter(ExecutionPlan.id == args["plan_id"]).first()
        )
        status = current.status if current else "unknown"
        return {
            "ok": False,
            "error": f"拒绝失败：计划当前状态为 {status}，仅 PENDING_APPROVAL 可拒绝",
            "plan_id": args["plan_id"],
            "status": status,
        }

    reason = (args.get("reason") or "").strip()
    if reason:
        plan.failure_reason = reason
        db.commit()
        db.refresh(plan)

    return {
        "ok": True,
        "plan_id": plan.id,
        "status": plan.status,
        "rejected_by": plan.rejected_by,
        "rejected_at": plan.rejected_at.isoformat() if plan.rejected_at else None,
        "reason": reason or None,
    }


# ──────────────────────────────────────────────────────────────
# ops.approval.temporary_access — 临时自审批授权
# ──────────────────────────────────────────────────────────────

@registry.register(
    name="ops.approval.temporary_access",
    title="管理临时自审批授权",
    description="为测试环境变更创建/确认/撤销限时自审批授权。操作身份只从 message_context.sender_id 推导；仅配置的原始审批人能确认/撤销；授权只允许固定受益人、固定测试系统/环境、固定动作集与授权生命周期。确认码 15 分钟有效且仅能消费一次。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",
    input_schema={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["request", "confirm", "revoke"],
                "description": "操作类型：request 发起授权申请 / confirm 确认授权 / revoke 撤销授权",
            },
            "message_context": message_context_schema(),
            "system_name": {"type": "string", "description": "目标系统名（request 必填）"},
            "environment": {"type": "string", "description": "测试环境名（request 必填）"},
            "beneficiary_identity": {"type": "string", "description": "受益人 actor key，如 matrix:default:@user:example.org（request 必填）"},
            "allowed_actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "允许的自审批动作集（request 必填）：MATRIX_PULL / FILE_UPLOAD / RELEASE / SERVICE_CONTROL / HEALTH_CHECK",
            },
            "reason": {"type": "string", "description": "授权理由（request 必填）"},
            "duration_value": {"type": "integer", "description": "授权时长数值（request 可选，默认 1）"},
            "duration_unit": {"type": "string", "enum": ["day", "week"], "description": "授权时长单位（request 可选，默认 day）"},
            "grant_id": {"type": "string", "description": "授权 ID（confirm/revoke 必填）"},
            "short_code": {"type": "string", "description": "一次性确认码（confirm 必填）"},
            "revoke_reason": {"type": "string", "description": "撤销理由（revoke 可选）"},
        },
        "required": ["operation", "message_context"],
        "additionalProperties": False,
    },
)
def temporary_access(args, ctx, db):
    """临时自审批授权管理工具：request / confirm / revoke。

    操作身份只从 message_context.sender_id 推导；原始审批人从
    _lookup_approvers（数据库/Token 策略）解析，绝不信任消息文本里的用户名。
    """
    from app.services.temporary_approval import TemporaryApprovalService

    operation = str(args.get("operation") or "").strip()
    if operation not in ("request", "confirm", "revoke"):
        return {"ok": False, "error": "operation must be request, confirm or revoke"}

    try:
        message_context = normalize_message_context(args.get("message_context") or {})
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"invalid message_context: {exc}"}

    service = TemporaryApprovalService(db)

    if operation == "request":
        system_name = str(args.get("system_name") or "").strip()
        environment = str(args.get("environment") or "").strip()
        beneficiary = str(args.get("beneficiary_identity") or "").strip()
        allowed_actions = args.get("allowed_actions") or []
        reason = str(args.get("reason") or "").strip()
        if not system_name or not environment:
            return {"ok": False, "error": "system_name and environment are required for request"}
        if not beneficiary:
            return {"ok": False, "error": "beneficiary_identity is required for request"}
        if not isinstance(allowed_actions, list) or not allowed_actions:
            return {"ok": False, "error": "allowed_actions must not be empty"}
        if not reason:
            return {"ok": False, "error": "reason is required for request"}

        try:
            _enforce_system_room(message_context, system_name)
        except HTTPException as exc:
            return {"ok": False, "error": str(exc.detail)}

        try:
            approvers = _lookup_approvers(
                ctx,
                system_name,
                None,
                channel=message_context.channel,
                channel_account_id=message_context.channel_account_id,
            )
        except HTTPException as exc:
            return {"ok": False, "error": str(exc.detail)}
        if not approvers:
            return {"ok": False, "error": "no authorized approver is configured for this channel account"}

        try:
            grant, short_code = service.request(
                message_context=message_context,
                beneficiary_actor_key=beneficiary,
                system_name=system_name,
                environment_name=environment,
                allowed_actions=allowed_actions,
                reason=reason,
                authorized_identities=[
                    {
                        "channel": message_context.channel,
                        "channel_account_id": message_context.channel_account_id,
                        "sender_id": approver,
                    }
                    for approver in approvers
                ],
                duration_value=int(args.get("duration_value") or 1),
                duration_unit=str(args.get("duration_unit") or "day"),
            )
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

        payload = grant.to_dict()
        if short_code:
            payload["short_code"] = short_code
        payload["ok"] = True
        payload["grant_id"] = payload.get("id")
        return payload

    if operation in ("confirm", "revoke"):
        grant_id = str(args.get("grant_id") or "").strip()
        if not grant_id:
            return {"ok": False, "error": "grant_id is required"}
        actor_key = message_context.actor_key

        if operation == "confirm":
            short_code = str(args.get("short_code") or "").strip()
            if not short_code:
                return {"ok": False, "error": "short_code is required for confirm"}
            confirmed = service.confirm(
                grant_id,
                short_code,
                actor_key=actor_key,
                message_context=message_context,
            )
            if confirmed is None:
                return {"ok": False, "error": "confirmation failed: invalid code, expired, wrong actor or wrong conversation"}
            payload = confirmed.to_dict()
            payload["ok"] = True
            payload["grant_id"] = payload.get("id")
            return payload

        reason = str(args.get("revoke_reason") or "").strip()
        revoked = service.revoke(
            grant_id,
            actor_key=actor_key,
            message_context=message_context,
            reason=reason,
        )
        if revoked is None:
            return {"ok": False, "error": "revoke failed: grant not found, not active, wrong actor or wrong conversation"}
        payload = revoked.to_dict()
        payload["ok"] = True
        payload["grant_id"] = payload.get("id")
        return payload

    return {"ok": False, "error": f"unsupported operation: {operation}"}

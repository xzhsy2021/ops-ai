"""Contextual OPS help builder.

Builds a read-only, live-facts help view for a given token/channel/project
context. Never reads token values, credentials, private keys or approval-code
hashes; the help output is assembled exclusively from tool metadata, tool
policy evaluation, database system/environment rows, configured approver
policy and active temporary grants.
"""
from __future__ import annotations

from typing import Any

from app.services.temporary_approval import TemporaryApprovalService
from app.services.message_context import MessageContext, normalize_message_context
from app.db.models import System, SystemEnvironment

# 主题关键词 → 工具名匹配规则（子串匹配，大小写不敏感）
_TOPIC_RULES: dict[str, list[str]] = {
    "临时审批": ["temporary", "approval", "grant"],
    "临时授权": ["temporary", "grant", "approval"],
    "授权": ["grant", "approval", "temporary"],
    "审批": ["approval"],
    "包上传": ["upload", "package"],
    "部署": ["deploy", "plan", "release", "rollback"],
    "发版": ["release", "deploy", "plan"],
    "发布": ["release", "deploy", "plan"],
    "巡检": ["inspection"],
    "服务器": ["server", "ssh"],
    "数据库": ["db", "connection"],
    "备份": ["backup"],
    "日志": ["log"],
    "风险": ["risk"],
    "Matrix附件": ["matrix"],
    "Matrix拉取": ["matrix"],
    "Matrix下载": ["matrix"],
    "拉取文件": ["matrix"],
    "下载文件": ["matrix", "file_transfer"],
    "附件": ["matrix"],
}

# 主题 → 完整申请示例（帮助输出，不泄露任何密钥/确认码）
# 每个示例包含：自然语言申请消息、对应的工具与参数、后续流程提示。
_TOPIC_EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "Matrix拉取": [
        {
            "message": "把 Matrix 房间里 @alice 刚发的附件拉到文件中心",
            "tool": "ops.matrix.pull_attachment",
            "operation": "pull",
            "arguments": {
                "room_id": "!room:example.org",
                "sender": "@alice:example.org",
                "minutes": 15,
                "confirm_text": "CONFIRM ops.matrix.pull_attachment",
            },
            "note": (
                "支持任意普通文件格式（.txt/.pdf/.log 等均可入库，不限部署包扩展名）；"
                "加密房间自动解密。返回 package_name 供后续引用；"
                "注意：非部署包格式仅入库留存，不能作为发版制品。"
            ),
        },
        {
            "message": "看看房间里最近发了什么文件",
            "tool": "ops.matrix.scan_media_events",
            "operation": "scan",
            "arguments": {"room_id": "!room:example.org", "minutes": 30},
            "note": "只读预览，不下载；返回 event_id/文件名/是否加密。",
        },
    ],
    "Matrix附件": [
        {
            "message": "用户在房间发了新包，帮我一次审批完成拉包+发布到测试环境",
            "tool": "ops.approval.prepare_plan",
            "operation": "prepare_plan",
            "arguments": {
                "system_name": "crypto-trader",
                "environment": "test",
                "steps": [
                    {
                        "step_key": "pull",
                        "action_type": "MATRIX_PULL",
                        "parameters": {"room_id": "!room:example.org", "sender": "@alice:example.org"},
                        "dependencies": [],
                    },
                    {
                        "step_key": "release",
                        "action_type": "RELEASE",
                        "parameters": {},
                        "dependencies": ["pull"],
                    },
                ],
            },
            "note": (
                "MATRIX_PULL 拉到的 package_name 自动回填 RELEASE 步骤，无需预知包名；"
                "RELEASE 只接受部署包格式制品（.tar.gz/.tgz/.tar/.zip/.jar/.war/.gz/.bin），"
                "普通文件会被格式守卫拒绝。"
            ),
        },
    ],
    "临时授权": [
        {
            "message": "给@Leo 申请后端服务 RELEASE、SERVICE_CONTROL 权限，时长一周，用途：后端开发自测发版",
            "tool": "ops.approval.temporary_access",
            "operation": "request",
            "arguments": {
                "operation": "request",
                "system_name": "crypto-trader",
                "environment": "test",
                "beneficiary_identity": "matrix:default:@leo:example.org",
                "allowed_actions": ["RELEASE", "SERVICE_CONTROL"],
                "reason": "后端开发自测发版",
                "duration_value": 1,
                "duration_unit": "week",
            },
            "note": "仅测试环境可用；动作限 MATRIX_PULL/FILE_UPLOAD/RELEASE/SERVICE_CONTROL/HEALTH_CHECK；受益人为固定 actor key。",
        },
        {
            "message": "帮我在测试环境给 @Leo 开两天包上传和健康检查权限，理由是联调验证",
            "tool": "ops.approval.temporary_access",
            "operation": "request",
            "arguments": {
                "operation": "request",
                "system_name": "crypto-trader",
                "environment": "test",
                "beneficiary_identity": "wechat:primary:leo",
                "allowed_actions": ["FILE_UPLOAD", "HEALTH_CHECK"],
                "reason": "联调验证",
                "duration_value": 2,
                "duration_unit": "day",
            },
            "note": "确认码 15 分钟有效、仅消费一次；重复作用域不叠加。",
        },
        {
            "message": "查一下现在有哪些临时授权还在生效，受益人都是谁",
            "tool": "ops.approval.temporary_access",
            "operation": "query",
            "arguments": {
                "operation": "query",
                "status": "ACTIVE",
            },
            "note": "query 按当前会话过滤，返回受益人/动作/有效期；可用 system_name、environment、beneficiary_identity、grant_id、status 过滤。",
        },
    ],
    "临时审批": [
        {
            "message": "给@Leo 申请后端服务 RELEASE、SERVICE_CONTROL 权限，时长一周，用途：后端开发自测发版",
            "tool": "ops.approval.temporary_access",
            "operation": "request",
            "arguments": {
                "operation": "request",
                "system_name": "crypto-trader",
                "environment": "test",
                "beneficiary_identity": "matrix:default:@leo:example.org",
                "allowed_actions": ["RELEASE", "SERVICE_CONTROL"],
                "reason": "后端开发自测发版",
                "duration_value": 1,
                "duration_unit": "week",
            },
            "note": "申请后由原始审批人在同一会话 confirm（提供一次性短码）即可生效。",
        },
    ],
    "授权": [
        {
            "message": "给@Leo 申请后端服务 RELEASE、SERVICE_CONTROL 权限，时长一周，用途：后端开发自测发版",
            "tool": "ops.approval.temporary_access",
            "operation": "request",
            "arguments": {
                "operation": "request",
                "system_name": "crypto-trader",
                "environment": "test",
                "beneficiary_identity": "matrix:default:@leo:example.org",
                "allowed_actions": ["RELEASE", "SERVICE_CONTROL"],
                "reason": "后端开发自测发版",
                "duration_value": 1,
                "duration_unit": "week",
            },
            "note": "临时授权只支持固定测试环境与白名单动作；生产/DML/回滚/包删除不可走该路径。",
        },
    ],
}

# 帮助输出中绝不允许出现的敏感子串（防御性，双保险）
_FORBIDDEN_MARKERS = (
    "confirmation_code_hash",
    "pbkdf2_sha256",
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "access_token",
    "secret",
)


def _strip_secrets(value: Any) -> Any:
    """递归剔除包含敏感标记的键值，防御纵深。"""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if any(marker in key_l for marker in _FORBIDDEN_MARKERS):
                continue
            cleaned[key] = _strip_secrets(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_secrets(item) for item in value]
    if isinstance(value, str):
        low = value.lower()
        if any(marker in low for marker in _FORBIDDEN_MARKERS):
            return "[redacted]"
        return value
    return value


def _topic_matches(tool_name: str, topic: str) -> bool:
    """工具名是否匹配主题关键词规则。"""
    if not topic:
        return True
    topic_l = topic.strip().lower()
    # 直接子串匹配优先（例如 system 名）
    if topic_l in tool_name.lower():
        return True
    keywords = _TOPIC_RULES.get(topic.strip())
    if not keywords:
        # 未知主题：仅匹配直接子串
        return False
    name_l = tool_name.lower()
    return any(keyword.lower() in name_l for keyword in keywords)


def _capability_status(policy: dict[str, Any], tool: dict[str, Any]) -> str:
    """根据工具策略计算能力状态。"""
    if not tool.get("enabled", True):
        return "forbidden"
    if not policy.get("allowed"):
        reason = str(policy.get("blocked_reason") or "")
        if "approval" in reason.lower() or "human" in reason.lower():
            return "requires_authorization"
        return "forbidden"
    return "available"


def build_help(
    db,
    ctx,
    *,
    topic: str = "",
    include_all: bool = False,
    system_name: str = "",
    environment: str = "",
    message_context: MessageContext | None = None,
) -> dict[str, Any]:
    """从实时事实构建上下文帮助。"""
    from app.services.tool_registry import registry

    system_name = str(system_name or "").strip()
    environment = str(environment or "").strip()
    topic = str(topic or "").strip()

    # 1. 能力列表：来自 registry 实时策略
    catalog = registry.list_tools(
        db=db,
        ctx=ctx,
        include_disabled=True,
        include_schema=False,
        limit=500,
    )
    tools = catalog.get("tools") if isinstance(catalog, dict) else catalog

    capabilities: list[dict[str, Any]] = []
    for tool in tools:
        name = tool.get("name") or ""
        if topic and not _topic_matches(name, topic):
            continue
        policy = tool.get("policy") or {}
        status = _capability_status(policy, tool)
        if status == "forbidden" and not include_all:
            continue
        capabilities.append({
            "name": name,
            "title": tool.get("title") or name,
            "description": tool.get("description") or "",
            "category": tool.get("category") or "",
            "risk": tool.get("risk") or "",
            "write": bool(tool.get("write")),
            "status": status,
            "requires_confirmation": bool(
                tool.get("requires_confirmation") or tool.get("requires_human_approval")
            ),
        })

    capabilities.sort(key=lambda item: (item["category"], item["name"]))

    result: dict[str, Any] = {
        "ok": True,
        "topic": topic or None,
        "include_all": bool(include_all),
        "capabilities": capabilities,
    }

    # 主题 → 完整申请示例（自然语言消息 + 工具调用参数），供调用方直接套用
    if topic:
        examples = _TOPIC_EXAMPLES.get(topic.strip())
        if examples:
            result["examples"] = examples

    # 2. 系统/环境上下文
    system_found = False
    if system_name:
        result["system_name"] = system_name
        system_row = db.query(System).filter(System.name == system_name).first()
        if system_row is None:
            result["system_found"] = False
            result["required"] = {
                "system_name": system_name,
                "hint": "系统未注册。请先在 OPS 后台注册系统与测试环境，或确认系统名拼写。",
                "register_required": ["system 行", "至少一个 category=test 的环境行"],
            }
        else:
            system_found = True
            result["system_found"] = True
            env_rows = (
                db.query(SystemEnvironment)
                .filter(SystemEnvironment.system_name == system_name)
                .order_by(SystemEnvironment.name)
                .all()
            )
            envs = [
                {
                    "name": row.name,
                    "display_name": row.display_name or row.name,
                    "category": row.category or "custom",
                    "temporary_approval_eligible": str(row.category or "").strip().lower() == "test",
                }
                for row in env_rows
            ]
            result["environments"] = envs
            if environment:
                matched = next((e for e in envs if e["name"] == environment), None)
                result["environment"] = environment
                result["environment_found"] = matched is not None
                if matched is None:
                    result["environment_hint"] = (
                        f"环境 {environment} 不存在。可用: {', '.join(e['name'] for e in envs) or '无'}"
                    )

            # 审批人策略（仅显示渠道/ID，不显示 token）
            try:
                from app.services.tool_adapters.approval_tools import _lookup_approvers
                channel = getattr(message_context, "channel", "matrix") or "matrix"
                account = getattr(message_context, "channel_account_id", "default") or "default"
                approvers = _lookup_approvers(
                    ctx,
                    system_name,
                    channel=channel,
                    channel_account_id=account,
                )
                result["approvers"] = approvers
            except Exception:
                result["approvers"] = []

            # 3. 活跃临时授权（不含确认码）
            if message_context is not None:
                try:
                    service = TemporaryApprovalService(db)
                    grants = service.list(status="ACTIVE", limit=50)
                    system_grants = [
                        g.to_dict()
                        for g in grants
                        if g.system_id == system_row.id
                    ]
                    if environment:
                        env_row = next(
                            (row for row in env_rows if row.name == environment),
                            None,
                        )
                        if env_row is not None:
                            system_grants = [
                                g for g in system_grants
                                if g.get("environment_id") == env_row.id
                            ]
                    result["active_grants"] = system_grants
                except Exception:
                    result["active_grants"] = []

    return _strip_secrets(result)


def help_query(args: dict, ctx, db) -> dict[str, Any]:
    """ops.help.query 工具实现。"""
    try:
        message_context = None
        if args.get("message_context"):
            message_context = normalize_message_context(args["message_context"])
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"invalid message_context: {exc}"}

    return build_help(
        db,
        ctx,
        topic=str(args.get("topic") or ""),
        include_all=bool(args.get("include_all", False)),
        system_name=str(args.get("system_name") or ""),
        environment=str(args.get("environment") or ""),
        message_context=message_context,
    )

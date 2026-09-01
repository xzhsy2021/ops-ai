"""Agent Context Layer：agent 接入 OPS 的机器可读自描述层。

背景（docs/agent-integration-abstraction-layer.md）：八轮 zeroclaw 接入
故障的共同本质是 OPS 为事实源但缺机器可读出口——能力/配置/流程/教训靠
人工抄进 agent 指令文件，抄写必然滞后失真。本层以三个 MCP 工具形式提供：

- ops.integration.get_context_pack：一次性返回 agent 正常工作所需的权威事实
  （能力声明 + DB 实时 facts + 流程索引 + 教训库），pack_revision 缓存
- ops.integration.get_flow_guide：机器可读流程编排（步骤/参数样例/硬规则）
- ops.integration.save_lesson：失败教训回写（pending 确认流，Phase 2）

设计原则：零侵入（不调用不比现状差，next_step 与执行器宽容兜底）；
OPS 唯一事实源；revision 未变走轻量 unchanged 响应。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# ──────────────────────────────────────────────────────────────
# 静态能力声明（随代码演进，修复落地时同步更新）
# ──────────────────────────────────────────────────────────────

# 宽容行为：调用方传参不符时的服务端兜底（对应故障 A 类）
TOLERANCES = [
    "content_sha256 一律可不传：ops.routing.resolve_message_target 自动按消息原文（UTF-8）计算摘要；非法值（路径/文件名/截断值）会被忽略并自动补算",
    "message_context 传五字段即可（channel/channel_account_id/conversation_id/message_id/sender_id）；ops.approval.prepare_plan 原样回传 resolve 返回的 message_context，丢字段/填错值会从签名票据反填",
    "计划步骤参数支持顶层与 action_parameters 两种层级；MATRIX_PULL 缺 room_id/sender 时回退计划自身请求上下文",
    "SERVICE_CONTROL 系统级计划（service_name=None）按包名前缀在 OPS 服务表内推断服务名（如 crypto-trader-web.tar.gz → crypto-trader-web），配置内不存在则明确报错",
]

# 硬性禁止（违反会被服务端拒绝）
FORBIDDEN = [
    "ops.approval.prepare_plan 前必须先调 ops.routing.resolve_message_target 换取 routing_ticket（15 分钟有效）",
    "禁止为 MATRIX_PULL 单独创建第二个计划/第二个审批——拉取与部署必须一个计划一次审批",
    "禁止把附件包的 SHA-256 当作消息摘要填进 content_sha256（包校验值只用于 FILE_UPLOAD 步骤参数）",
    "禁止在 resolve 之后停下输出中间状态（如'票据已签发但尚未创建计划'）——Step 链路必须同轮推进到等待审批",
]

# ──────────────────────────────────────────────────────────────
# 教训库（八轮故障提炼；superseded = 已修复，勿再引用旧结论）
# ──────────────────────────────────────────────────────────────

LESSONS: list[dict[str, Any]] = [
    {
        "id": "L001",
        "pattern": "认为无法生成 content_sha256 → OPS 不会签发路由票据",
        "guidance": "不需要本地计算消息摘要：resolve 未收到摘要就按消息原文自动算，误传垃圾值也会被忽略补算",
        "status": "superseded",
        "superseded_note": "2026-09-01 OPS 已全链路自动处理（commit 6a97f58/27b97a8/19fca2b），旧结论作废",
        "severity": "info",
    },
    {
        "id": "L002",
        "pattern": "把附件路径/文件名当 content_sha256 传入",
        "guidance": "不要传任何 content_sha256；OPS 忽略非法值并自动补算",
        "status": "superseded",
        "superseded_note": "2026-09-01 OPS 忽略垃圾值（commit 19fca2b）",
        "severity": "info",
    },
    {
        "id": "L003",
        "pattern": "resolve 成功后停在中间状态，prepare_plan 未发出",
        "guidance": "resolve 返回的 next_step 字段是权威指令：同一轮内立即调用 prepare_plan，禁止中途停下回报状态",
        "status": "active",
        "severity": "warning",
    },
    {
        "id": "L004",
        "pattern": "审批人已批准但以'缺少原始请求上下文'为由不执行",
        "guidance": "execute_plan 只需 4 个参数：plan_id/short_code/room_id/approver_matrix_id——全部在审批消息本身中，不需要原始 message_context",
        "status": "active",
        "severity": "warning",
    },
    {
        "id": "L005",
        "pattern": "MATRIX_PULL 报'room_id 与 sender 参数未传入'",
        "guidance": "参数可传顶层或 action_parameters；完全缺失时 OPS 回退计划请求上下文（2026-09-01 commit 56728b4 修复）",
        "status": "superseded",
        "superseded_note": "已修复：两层读取 + 上下文回填",
        "severity": "info",
    },
    {
        "id": "L006",
        "pattern": "SERVICE_CONTROL 报'缺少 system_name 和 service_name'",
        "guidance": "SERVICE_CONTROL 步骤必须显式带 service_name（真实前端服务是 crypto-trader-web，不是 crypto-frontend）；系统级计划缺省时 OPS 按包名前缀在服务表内推断（commit 2cbd7ab）",
        "status": "active",
        "severity": "warning",
    },
    {
        "id": "L007",
        "pattern": "引用了服务表中不存在的服务名（如 crypto-frontend）",
        "guidance": "服务名以 pack facts 区块为准（OPS DB 实时组装）；指令文件中的服务名历史记录不可信",
        "status": "active",
        "severity": "warning",
    },
    {
        "id": "L008",
        "pattern": "换 agent 后沿用上一代 workspace 的旧规则与失败结论",
        "guidance": "接入新 agent 前先拉 context pack 对齐事实；旧 workspace 记忆中与 pack 冲突的内容以 pack 为准",
        "status": "active",
        "severity": "warning",
    },
]

# ──────────────────────────────────────────────────────────────
# 内置流程编排（flow guides）
# ──────────────────────────────────────────────────────────────

FLOW_GUIDES: dict[str, dict[str, Any]] = {
    "frontend-release": {
        "flow_id": "frontend-release",
        "title": "前端发版（Matrix 附件）",
        "trigger": "同一消息内：附件（crypto-trader-web.tar.gz）+ @agent + 含'发版/前端/量化'关键词",
        "atomic": True,
        "steps": [
            {
                "n": 1,
                "tool": "ops.list_packages",
                "purpose": "确认文件中心是否已有同名包（避免重复上传）",
                "args_hint": {"system": "crypto-trader", "service": "crypto-trader-web"},
                "on_skip": "包存在且 sha256 与附件一致 → 跳过上传直接进入第 2 步",
            },
            {
                "n": 2,
                "tool": "ops.routing.resolve_message_target",
                "purpose": "签发路由票据（绑定房间/消息/发送者/摘要）",
                "args_hint": {
                    "message_text": "<触发消息原文，一字不差>",
                    "message_context": "五字段：channel=matrix/channel_account_id=default/conversation_id=<消息所在房间>/message_id=<消息ID>/sender_id=<发送者>",
                },
                "must_follow": "不传 content_sha256；同一轮内立即进入下一步，禁止停下回报'票据已签发'",
            },
            {
                "n": 3,
                "tool": "ops.approval.prepare_plan",
                "purpose": "创建审批计划（一次审批覆盖上传+更新）",
                "args_hint": {
                    "message_context": "<resolve 返回的 message_context 原样>",
                    "routing_ticket": "<resolve 返回的 ticket>",
                    "system_name": "<resolve 返回的 system_name>",
                    "environment": "test",
                    "steps": "见 steps_template（FILE_UPLOAD + SERVICE_CONTROL 两步，依赖链）",
                },
            },
            {
                "n": 4,
                "tool": "(matrix 回复)",
                "purpose": "把 prepare_plan 返回的 reply_template 原样发到房间，等待审批人批准",
            },
            {
                "n": 5,
                "tool": "ops.approval.execute_plan",
                "purpose": "审批人回复「批准 <短语>」后立即执行",
                "args_hint": {
                    "plan_id": "<prepare_plan 返回>",
                    "short_code": "<审批消息里的完整短语>",
                    "room_id": "<审批消息所在房间>",
                    "approver_matrix_id": "<审批人 Matrix ID>",
                },
                "must_follow": "禁止以'缺少原始上下文'为由停止——四个参数全部在审批消息中",
            },
        ],
        "steps_template": {
            "FILE_UPLOAD": {
                "step_key": "step-1-upload",
                "action_type": "FILE_UPLOAD",
                "parameters": {
                    "action_parameters": {
                        "package_name": "crypto-trader-web.tar.gz",
                        "remote_path": "/data/web/crypto-trader-web.tar.gz",
                        "overwrite": True,
                        "confirm_path": "/data/web/crypto-trader-web.tar.gz",
                    }
                },
            },
            "SERVICE_CONTROL": {
                "step_key": "step-2-update",
                "action_type": "SERVICE_CONTROL",
                "parameters": {
                    "action_parameters": {
                        "control_action": "update",
                        "system_name": "crypto-trader",
                        "service_name": "crypto-trader-web",
                        "targets": ["203.0.113.10"],
                        "compose_dir": "/data/web",
                    }
                },
                "dependencies": ["step-1-upload"],
            },
        },
        "hard_rules": [
            "拉取与部署必须一个计划一次审批（禁止第二个计划）",
            "SERVICE_CONTROL 必须带真实服务名（以 facts 区块为准）",
            "ai_reason 建议携带包更新时间 package_updated_at=YYYY-MM-DD HH:MM:SS",
        ],
    },
    "service-restart": {
        "flow_id": "service-restart",
        "title": "服务重启（单动作）",
        "trigger": "消息含系统关键词（如'量化'/'crypto-trader'）+ 重启意图",
        "atomic": True,
        "steps": [
            {
                "n": 1,
                "tool": "ops.routing.resolve_message_target",
                "purpose": "路由 + 签发票据",
                "args_hint": {"message_text": "<消息原文>", "message_context": "五字段"},
                "must_follow": "不传 content_sha256",
            },
            {
                "n": 2,
                "tool": "ops.approval.prepare_plan",
                "purpose": "创建重启审批计划",
                "args_hint": {
                    "message_context": "<resolve 返回值原样>",
                    "routing_ticket": "<resolve.ticket>",
                    "system_name": "<resolve.system_name>",
                    "environment": "test",
                    "steps": "单步 SERVICE_CONTROL：control_action=restart + system_name/service_name（见 facts）+ targets",
                },
            },
            {"n": 3, "tool": "(matrix 回复)", "purpose": "reply_template 原样发房间等待批准"},
            {
                "n": 4,
                "tool": "ops.approval.execute_plan",
                "purpose": "批准后立即执行",
                "args_hint": {"plan_id": "<prepare 返回>", "short_code": "<审批短语>", "room_id": "<房间>", "approver_matrix_id": "<审批人>"},
            },
        ],
        "steps_template": {},
        "hard_rules": ["服务名以 facts 区块为准，不要臆测"],
    },
    "package-pull-release": {
        "flow_id": "package-pull-release",
        "title": "拉包 + 发布（MATRIX_PULL 路径）",
        "trigger": "用户要求从 Matrix 房间拉包并部署/发布",
        "atomic": True,
        "steps": [
            {
                "n": 1,
                "tool": "ops.routing.resolve_message_target",
                "purpose": "路由 + 签发票据",
                "args_hint": {"message_text": "<消息原文>", "message_context": "五字段"},
                "must_follow": "不传 content_sha256",
            },
            {
                "n": 2,
                "tool": "ops.approval.prepare_plan",
                "purpose": "一次审批覆盖拉取+发布",
                "args_hint": {
                    "message_context": "<resolve 返回值原样>",
                    "routing_ticket": "<resolve.ticket>",
                    "system_name": "<resolve.system_name>",
                    "environment": "test",
                    "steps": "step-1-pull → MATRIX_PULL（room_id/sender 可省，回退计划上下文）；step-2 → RELEASE，dependencies=[step-1-pull]",
                },
            },
            {"n": 3, "tool": "(matrix 回复)", "purpose": "reply_template 原样发房间等待批准"},
            {
                "n": 4,
                "tool": "ops.approval.execute_plan",
                "purpose": "批准后立即执行（拉取入库 → 回填 package_name → 发布）",
                "args_hint": {"plan_id": "<prepare 返回>", "short_code": "<审批短语>", "room_id": "<房间>", "approver_matrix_id": "<审批人>"},
            },
        ],
        "steps_template": {
            "MATRIX_PULL": {
                "step_key": "step-1-pull",
                "action_type": "MATRIX_PULL",
                "parameters": {"minutes": 15, "filename": "<包名>"},
            },
            "RELEASE": {
                "step_key": "step-2-release",
                "action_type": "RELEASE",
                "parameters": {},
                "dependencies": ["step-1-pull"],
            },
        },
        "hard_rules": [
            "MATRIX_PULL 拉到的 package_name 自动回填依赖它的 RELEASE，无需预知包名",
            "禁止为拉取单独创建第二个计划",
        ],
    },
}


def _sha256_of(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# ──────────────────────────────────────────────────────────────
# facts 组装（DB 实时读取——agent 永远拿不到不存在的服务名）
# ──────────────────────────────────────────────────────────────


def _assemble_facts(db) -> list[dict[str, Any]]:
    from app.db.models import Service, System

    systems: list[dict[str, Any]] = []
    for system in db.query(System).order_by(System.name).all():
        routing = system.message_routing or {}
        rooms = [
            str(room.get("conversation_id") or "").strip()
            for room in (routing.get("rooms") or [])
            if room.get("conversation_id")
        ]
        services = sorted(
            svc.name
            for svc in db.query(Service).filter(Service.system_name == system.name).all()
        )
        systems.append({"name": system.name, "services": services, "rooms": rooms})
    return systems


def _assemble_capabilities(db, ctx) -> dict[str, Any]:
    """工具清单 + MCP annotations 映射（补充①：readOnly=!write、destructiveHint=risk=high）。"""
    from app.services.tool_registry import registry

    listed = registry.list_tools(db, ctx, include_disabled=False, include_schema=False, limit=2000)
    tools = []
    for tool in listed.get("tools", []):
        tools.append(
            {
                "name": tool["name"],
                "title": tool.get("title", ""),
                "risk": tool.get("risk", ""),
                "write": bool(tool.get("write", False)),
                "mcp_annotations": {
                    "readOnly": not bool(tool.get("write", False)),
                    "destructiveHint": str(tool.get("risk", "")) == "high",
                    "idempotentHint": str(tool.get("risk", "")) == "low",
                },
            }
        )
    return {
        "tool_count": len(tools),
        "tools": tools,
        "tolerances": TOLERANCES,
        "forbidden": FORBIDDEN,
    }


def build_context_pack(db, ctx, *, agent_name: str = "default", channel: str = "matrix") -> dict[str, Any]:
    """组装 AgentContextPack。revision = 全内容哈希，缓存协议的键。"""
    facts = _assemble_facts(db)
    capabilities = _assemble_capabilities(db, ctx)
    flows = [
        {
            "flow_id": flow["flow_id"],
            "title": flow["title"],
            "trigger": flow["trigger"],
            "flow_revision": _sha256_of(flow),
        }
        for flow in FLOW_GUIDES.values()
    ]
    pack = {
        "agent_name": agent_name,
        "channel": channel,
        "capabilities": capabilities,
        "facts": {"systems": facts},
        "flows": flows,
        "lessons": LESSONS,
    }
    pack["pack_revision"] = _sha256_of(
        {k: v for k, v in pack.items() if k != "pack_revision"}
    )
    return pack


def get_flow_guide(flow_id: str) -> dict[str, Any] | None:
    """返回流程编排定义；不存在返回 None。"""
    guide = FLOW_GUIDES.get(flow_id)
    if guide is None:
        return None
    result = dict(guide)
    result["flow_revision"] = _sha256_of(guide)
    return result


def list_flow_ids() -> list[str]:
    return sorted(FLOW_GUIDES)

"""Agent Context Layer MCP 工具：ops.integration.get_context_pack / get_flow_guide。

零侵入设计：不调用这些工具的 agent 也能继续工作（next_step + 执行器宽容
兜底）。工具本身只读（ops:read scope、write=False、risk=low），任何持有
只读 token 的 agent 均可拉取自己的接入上下文。

分类说明：category="routing"（沿用现有常规类别）而非 "agent"——后者受
agent_runtime_enabled 轻量模式开关控制，会把本层误伤。
"""
from __future__ import annotations

from app.services import agent_context
from app.services.tool_registry import registry


@registry.register(
    name="ops.integration.get_context_pack",
    title="获取 Agent 接入上下文包",
    description="一次性返回 agent 正常接入 OPS 所需的权威事实：能力声明（工具清单+宽容行为+硬性禁止）、配置事实（系统/服务/房间，OPS DB 实时组装）、流程索引、教训库。带 pack_revision 缓存协议：传入 cached_revision 与当前一致时返回轻量 unchanged 响应。每会话启动时调用一次；工具返回中的 next_step 是权威流程指令。中文: 获取接入上下文/agent上下文包/接入对齐。",
    scopes=["ops:read"],
    risk="low",
    category="routing",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": "调用方 agent 标识（如 zeroclaw/openclaw/qclaw），默认 default",
            },
            "channel": {
                "type": "string",
                "description": "接入渠道（matrix/wechat/telegram），默认 matrix",
            },
            "cached_revision": {
                "type": "string",
                "description": "上次缓存的 pack_revision；与当前一致时返回 {unchanged: true}",
            },
            "include_tools": {
                "type": "boolean",
                "description": "是否包含 120+ 工具清单（首次拉取建议 true，续期可 false 省流量）",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
)
def agent_get_context_pack(args, ctx, db):
    pack = agent_context.build_context_pack(
        db, ctx,
        agent_name=str(args.get("agent_name") or "default").strip() or "default",
        channel=str(args.get("channel") or "matrix").strip() or "matrix",
    )
    cached = str(args.get("cached_revision") or "").strip()
    if cached and cached == pack["pack_revision"]:
        return {
            "unchanged": True,
            "pack_revision": pack["pack_revision"],
            "hint": "内容未变，可继续使用本地缓存；执行中的实时引导以工具返回 next_step 为准",
        }
    if not args.get("include_tools", True):
        tools = pack["capabilities"].pop("tools", [])
        pack["capabilities"]["tool_count"] = len(tools)
    pack["next_step"] = (
        "按 flows 索引选择本次任务的 flow_id，调用 ops.integration.get_flow_guide"
        "（flow_id=…）获取机器可读分步编排；与本地记忆冲突时以本 pack 为准。"
    )
    return pack


@registry.register(
    name="ops.integration.get_flow_guide",
    title="获取流程编排指南",
    description="返回指定流程的机器可读分步编排：步骤顺序、每步工具与参数样例（args_hint）、步骤模板（steps_template）、硬规则（hard_rules）、atomic 声明（禁止中途停顿回报状态）。内置流程：frontend-release（Matrix 附件发版）/ service-restart（服务重启）/ package-pull-release（拉包+发布）。中文: 获取流程指南/流程编排/操作流程。",
    scopes=["ops:read"],
    risk="low",
    category="routing",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "flow_id": {
                "type": "string",
                "description": "流程 ID，如 frontend-release / service-restart / package-pull-release",
            },
            "list_only": {
                "type": "boolean",
                "description": "true 时仅返回可用流程索引（不返回具体编排）",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
)
def agent_get_flow_guide(args, ctx, db):
    if args.get("list_only"):
        return {
            "flow_ids": agent_context.list_flow_ids(),
            "hint": "传 flow_id 获取完整编排；流程以 atomic 方式执行：一轮内推进到等待审批",
        }
    flow_id = str(args.get("flow_id") or "").strip()
    if not flow_id:
        return {
            "flow_ids": agent_context.list_flow_ids(),
            "hint": "未指定 flow_id，已返回可用索引；传 flow_id 获取完整编排",
        }
    guide = agent_context.get_flow_guide(flow_id)
    if guide is None:
        return {
            "error": f"flow not found: {flow_id}",
            "flow_ids": agent_context.list_flow_ids(),
        }
    guide["next_step"] = (
        "按 steps 顺序执行：每步工具返回的 next_step 字段是权威指令；"
        "atomic=true 的流程必须一轮推进到'等待审批'，禁止中途停下回报状态。"
    )
    return guide


@registry.register(
    name="ops.integration.save_lesson",
    title="回写接入教训",
    description="把接入/执行中发现的教训回写 OPS 共享教训库（pattern=什么情况，guidance=怎么办，evidence=证据如计划ID/错误消息）。默认进 pending 待确认队列，管理端确认后对所有 agent 生效；同 pattern 幂等更新。失败/踩坑后调用，不要静默吞掉。中文: 回写教训/记录教训/共享经验。",
    scopes=["ops:write"],
    risk="low",
    category="routing",
    write=True,
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "什么情况下会遇到此问题（一句话，可被其他 agent 检索匹配）",
            },
            "guidance": {
                "type": "string",
                "description": "应该怎么办（具体可执行的动作）",
            },
            "evidence": {
                "type": "string",
                "description": "证据：失败计划 ID / 错误消息 / 轮次编号（可选但强烈建议）",
            },
            "severity": {
                "type": "string",
                "enum": ["info", "warning"],
                "description": "info=经验提示，warning=会导致流程失败的坑，默认 info",
            },
            "agent_name": {
                "type": "string",
                "description": "回写方 agent 标识（如 zeroclaw），便于追溯",
            },
        },
        "required": ["pattern", "guidance"],
        "additionalProperties": False,
    },
)
def agent_save_lesson(args, ctx, db):
    result = agent_context.save_lesson(
        db,
        pattern=str(args.get("pattern") or ""),
        guidance=str(args.get("guidance") or ""),
        evidence=str(args.get("evidence") or ""),
        severity=str(args.get("severity") or "info"),
        agent_name=str(args.get("agent_name") or ctx.username or "unknown"),
    )
    result["next_step"] = (
        "教训已入库；继续当前流程（pending 不阻塞执行）。"
        "下次会话 get_context_pack 的 lessons 区块会带上确认后的条目。"
    )
    return result

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

import copy
import hashlib
import json
import re
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
    {
        "id": "L009",
        "pattern": "宿主 agent 自身的工具审批门弹出 [XXXXXX] approve 短码拦截 ops.integration.* 调用",
        "guidance": "宿主审批门（如 zeroclaw/openclaw 的 auto_approve 配置）需把接入层工具加入白名单并重启 daemon；[短码] 是宿主工具审批，与 OPS 发版审批（中文完整短语「批准 <动作> <system>@<env> <指纹8>」）是两套体系，不要拿宿主短码去映射 OPS 计划",
        "status": "active",
        "severity": "warning",
    },
    {
        "id": "L010",
        "pattern": "消息附件与文件中心已有包是否同一个——靠文件名猜测（同名不同版本会误判）",
        "guidance": "scan_media_events 返回 already_in_file_center + file_center_package（按事件指纹 source_message_key 精确匹配，非文件名）；list_packages 也输出 source_message_key。附件已入库时直接建不含 MATRIX_PULL 的计划引用既有包；同名但 already_in_file_center=false 的是新附件，必须拉取",
        "status": "active",
        "severity": "warning",
    },
    {
        "id": "L011",
        "pattern": "测试环境受益人计划含 MATRIX_PULL 时 temporary_grant_id 为 null，无法自审批整链",
        "guidance": "MATRIX_PULL 已纳入临时自审批动作集（2026-09-02 commit 6bdeeba）；授权 request 时 allowed_actions 需含 MATRIX_PULL 才覆盖全链。旧授权（动作集不含 MATRIX_PULL）仍有效但只覆盖不含拉取的计划——或者先确认包已在文件中心（见 L010）走无拉取计划",
        "status": "active",
        "severity": "info",
    },
]

# ──────────────────────────────────────────────────────────────
# 内置流程编排（flow guides）
# ──────────────────────────────────────────────────────────────

FLOW_GUIDES: dict[str, dict[str, Any]] = {
    "frontend-release": {
        "flow_id": "frontend-release",
        "title": "前端发版（Matrix 附件）",
        "trigger": "同一消息内：附件（<FRONTEND_PACKAGE>）+ @agent + 含'发版/前端/更新'或系统路由关键词",
        "verified": "2026-09-01 zeroclaw 元指令版端到端验证通过（完整链路：pack → flow guide → resolve → prepare_plan → 批准 → execute_plan → 上传 SHA 一致 + www.sh 执行成功）",
        "atomic": True,
        "parameterized": True,
        "default_system": "crypto-trader",
        "steps": [
            {
                "n": 1,
                "tool": "ops.matrix.scan_media_events",
                "purpose": "确认附件事件是否已入库（already_in_file_center 字段按事件指纹精确判定，非文件名——见 L010）",
                "args_hint": {"room_id": "<消息所在房间>", "sender": "<发送者>", "filename": "<FRONTEND_PACKAGE>"},
                "on_skip": "already_in_file_center=true 且 file_center_package.sha256 非空 → 附件已入库，建不含 MATRIX_PULL 的计划直接引用既有包（见 L011 两条路径）；false → 新附件，计划需含 MATRIX_PULL 步骤",
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
                        "package_name": "<FRONTEND_PACKAGE>",
                        "remote_path": "<DEPLOY_PATH>/<FRONTEND_PACKAGE>",
                        "overwrite": True,
                        "confirm_path": "<DEPLOY_PATH>/<FRONTEND_PACKAGE>",
                    }
                },
            },
            "SERVICE_CONTROL": {
                "step_key": "step-2-update",
                "action_type": "SERVICE_CONTROL",
                "parameters": {
                    "action_parameters": {
                        "control_action": "update",
                        "system_name": "<SYSTEM>",
                        "service_name": "<SERVICE>",
                        "targets": "<TARGETS>",
                        "compose_dir": "<DEPLOY_PATH>",
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
        "trigger": "消息含系统路由关键词（见 facts.routing_keywords）+ 重启意图",
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


def _assemble_facts(db) -> dict[str, Any]:
    """facts 全自动化（Phase 3）：systems + approvers + environments，全部 OPS DB 实时组装。

    - systems：每系统 services（服务表）+ rooms/approvers（message_routing）+ routing keywords
    - approvers：跨系统去重的审批人清单（channel/channel_account_id/sender_id）
    - environments：SystemEnvironment 表中启用环境
    agent 拿到的永远是真实存在的服务名/房间/审批人——不存在的配置进不了 pack。
    """
    from app.db.models import Service, System, SystemEnvironment

    systems: list[dict[str, Any]] = []
    approver_index: dict[str, dict[str, Any]] = {}
    for system in db.query(System).order_by(System.name).all():
        routing = system.message_routing or {}
        rooms = [
            str(room.get("conversation_id") or "").strip()
            for room in (routing.get("rooms") or [])
            if room.get("conversation_id")
        ]
        approvers = []
        for appr in (routing.get("approvers") or []):
            entry = {
                "channel": str(appr.get("channel") or "").strip(),
                "channel_account_id": str(appr.get("channel_account_id") or "default").strip(),
                "sender_id": str(appr.get("sender_id") or "").strip(),
            }
            if entry["sender_id"]:
                approvers.append(entry)
                key = f"{entry['channel']}:{entry['channel_account_id']}:{entry['sender_id']}"
                approver_index[key] = entry
        services = sorted(
            svc.name
            for svc in db.query(Service).filter(Service.system_name == system.name).all()
        )
        systems.append(
            {
                "name": system.name,
                "services": services,
                "rooms": rooms,
                "approvers": [a["sender_id"] for a in approvers if a["channel"] == "matrix"],
                "routing_keywords": [str(k) for k in (routing.get("keywords") or [])][:20],
            }
        )
    try:
        environments = [
            str(row[0])
            for row in db.query(SystemEnvironment.name)
            .distinct()
            .order_by(SystemEnvironment.name)
            .all()
        ]
    except Exception:
        environments = []
    return {
        "systems": systems,
        "approvers": list(approver_index.values()),
        "environments": environments,
    }


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


def _assemble_lessons(db) -> list[dict[str, Any]]:
    """教训库：DB（agent 回写 + 人工录入）优先，静态内置兜底合并。

    - DB active/superseded 记录取正文；pending 只计数不入正文（防污染）
    - 内置教训始终在场（代码演进的一部分）；DB 中存在同 pattern 的
      active/superseded 记录时以 DB 为准（内置项可被人工 supersede 覆盖）
    """
    lessons: list[dict[str, Any]] = []
    seen_patterns: set[str] = set()
    try:
        from app.db.models import AgentLesson

        rows = (
            db.query(AgentLesson)
            .filter(AgentLesson.status.in_(["active", "superseded"]))
            .order_by(AgentLesson.created_at)
            .all()
        )
        for row in rows:
            item = {
                "id": row.id,
                "pattern": row.pattern,
                "guidance": row.guidance,
                "status": row.status,
                "severity": row.severity or "info",
                "origin": row.origin,
            }
            if row.status == "superseded":
                item["superseded_note"] = row.superseded_note or ""
            lessons.append(item)
            seen_patterns.add(row.pattern.strip())
    except Exception:
        lessons = []
    for builtin in LESSONS:
        if builtin["pattern"].strip() in seen_patterns:
            continue  # DB 同 pattern 记录优先
        lessons.append(dict(builtin))
    return lessons


def save_lesson(
    db,
    *,
    pattern: str,
    guidance: str,
    evidence: str = "",
    severity: str = "info",
    agent_name: str = "unknown",
) -> dict[str, Any]:
    """agent 回写教训：默认 pending（管理端确认后 active 对所有 agent 生效）。

    幂等：同 pattern 的 pending/active 记录存在时更新而非新增。
    ID 分配：查 DB 与内置教训的最大 L 序号 + 1。
    """
    from datetime import datetime, timezone

    from app.db.models import AgentLesson

    pattern_clean = (pattern or "").strip()
    guidance_clean = (guidance or "").strip()
    if not pattern_clean or not guidance_clean:
        raise ValueError("pattern 与 guidance 均不能为空")

    existing = (
        db.query(AgentLesson)
        .filter(AgentLesson.pattern == pattern_clean, AgentLesson.status.in_(["pending", "active"]))
        .order_by(AgentLesson.created_at.desc())
        .first()
    )
    if existing:
        existing.guidance = guidance_clean
        existing.evidence = (evidence or "").strip() or existing.evidence
        existing.severity = severity if severity in {"info", "warning"} else existing.severity
        existing.agent_name = agent_name
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        return {"lesson_id": existing.id, "updated": True, "status": existing.status}

    max_num = 0
    for row in db.query(AgentLesson.id).all():
        raw = str(row[0] or "")
        if raw.startswith("L") and raw[1:].isdigit():
            max_num = max(max_num, int(raw[1:]))
    for builtin in LESSONS:
        raw = str(builtin.get("id") or "")
        if raw.startswith("L") and raw[1:].isdigit():
            max_num = max(max_num, int(raw[1:]))
    new_id = f"L{max_num + 1:03d}"

    row = AgentLesson(
        id=new_id,
        pattern=pattern_clean,
        guidance=guidance_clean,
        evidence=(evidence or "").strip() or None,
        severity=severity if severity in {"info", "warning"} else "info",
        status="pending",
        origin="agent",
        agent_name=agent_name,
    )
    db.add(row)
    db.commit()
    return {
        "lesson_id": new_id,
        "updated": False,
        "status": "pending",
        "note": "已入待确认队列（pending）；管理端确认后进入 active 对所有 agent 生效",
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
        "facts": facts,
        "flows": flows,
        "lessons": _assemble_lessons(db),
    }
    if channel == "heartbeat":
        pack["heartbeat_ops"] = _assemble_heartbeat_ops(db)
    pack["pack_revision"] = _sha256_of(
        {k: v for k, v in pack.items() if k != "pack_revision"}
    )
    return pack


_PLACEHOLDER_PATTERN = re.compile(r"<(SYSTEM|SERVICE|FRONTEND_PACKAGE|TARGETS|DEPLOY_PATH)>")


def _flow_binding_values(db, system_name: str) -> dict[str, Any]:
    """按系统从 DB 解析占位符实值——服务名/部署路径/目标服务器全部实时读取，
    agent 永远拿不到 DB 里不存在的配置（与 facts 同一防线）。"""
    from app.db.models import Service, System, SystemEnvironment

    values: dict[str, Any] = {"SYSTEM": system_name, "SERVICE": "", "DEPLOY_PATH": "", "TARGETS": []}
    system = db.query(System).filter(System.name == system_name).first()
    if system is None:
        return values
    # SERVICE：系统内 name 含 "web"/"frontend"/"前端" 的服务，否则取 display_name
    # 含"前端"者；再退而取首个服务（模板流程的主目标）
    services = db.query(Service).filter(Service.system_name == system_name).order_by(Service.name).all()
    frontend = None
    for svc in services:
        if any(tag in svc.name.lower() for tag in ("web", "frontend")):
            frontend = svc
            break
    if frontend is None:
        for svc in services:
            if "前端" in (svc.display_name or ""):
                frontend = svc
                break
    if frontend is None and services:
        frontend = services[0]
    if frontend is not None:
        values["SERVICE"] = frontend.name
        template_vars = getattr(frontend, "template_variables", None) or {}
        if not isinstance(template_vars, dict):
            template_vars = {}
        values["DEPLOY_PATH"] = str(template_vars.get("deploy_path") or "").rstrip("/")
        values["FRONTEND_PACKAGE"] = f"{frontend.name}.tar.gz"
        # TARGETS 优先读服务自身 servers（真实清单），空则退系统环境 servers
        servers = getattr(frontend, "servers", None) or []
        if isinstance(servers, list):
            ids: list[str] = []
            for s in servers:
                if isinstance(s, dict):
                    sid = str(s.get("id") or s.get("name") or "").strip()
                elif isinstance(s, str):
                    sid = s.strip()
                else:
                    sid = ""
                if sid:
                    ids.append(sid)
            values["TARGETS"] = ids
    if not values["TARGETS"]:
        env_row = (
            db.query(SystemEnvironment)
            .filter(SystemEnvironment.system_name == system_name, SystemEnvironment.name == "test")
            .first()
        )
        if env_row is not None:
            servers = env_row.servers or []
            values["TARGETS"] = [
                str(s.get("id") or s.get("name") or "").strip() for s in servers if isinstance(s, dict)
            ]
            values["TARGETS"] = [t for t in values["TARGETS"] if t]
    return values


def render_flow_guide(db, flow_id: str, system_name: str = "") -> dict[str, Any] | None:
    """返回 flow guide 并按 system_name 实例化占位符。

    - 未传 system_name → 用 flow 的 default_system（向后兼容：zeroclaw 现行
      调用拿到的仍是 crypto-trader 实例）
    - 占位符全部从 DB 实时解析；系统不存在时占位符原样保留并附 hint
    - flow_revision 只对模板计算（实例化值不进 revision——配置变更由
      pack_revision 的 facts 区块感知）
    """
    guide = FLOW_GUIDES.get(flow_id)
    if guide is None:
        return None
    result = copy.deepcopy(guide)
    system_name = (system_name or guide.get("default_system") or "").strip()
    rendered_system = bool(system_name)
    if not result.get("parameterized"):
        # 无占位符的流程（service-restart/package-pull-release）原样返回
        result["flow_revision"] = _sha256_of(guide)
        return result

    values = _flow_binding_values(db, system_name) if system_name else {}
    missing = not values.get("SERVICE")
    resolved_system = system_name if rendered_system and values.get("SERVICE") else ""

    def _sub(obj: Any) -> Any:
        if isinstance(obj, str):
            def _replace(m: "re.Match[str]") -> str:
                key = m.group(1)
                if key in values and values[key] not in ("", []):
                    if key == "TARGETS":
                        return json.dumps(values[key], ensure_ascii=False)
                    return str(values[key])
                return m.group(0)  # 未解析的占位符原样保留
            return _PLACEHOLDER_PATTERN.sub(_replace, obj)
        if isinstance(obj, list):
            return [_sub(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _sub(v) for k, v in obj.items()}
        return obj

    result = _sub(result)
    # TARGETS 占位符是字符串，渲染后需要还原成 JSON 数组
    template = result.get("steps_template") or {}
    svc_ctrl = template.get("SERVICE_CONTROL")
    if svc_ctrl:
        params = svc_ctrl.get("parameters") or {}
        nested = params.get("action_parameters")
        if isinstance(nested, dict) and isinstance(nested.get("targets"), str):
            try:
                nested["targets"] = json.loads(nested["targets"])
            except (ValueError, TypeError):
                pass
    if resolved_system:
        result["rendered_for_system"] = resolved_system
    elif system_name:
        result["render_warning"] = (
            f"系统 {system_name} 未找到可发版的前端服务（服务表内无匹配），"
            "占位符原样保留——请核对 pack facts 区块的服务清单后重试。"
        )
    result["flow_revision"] = _sha256_of(guide)
    return result


def get_flow_guide(flow_id: str) -> dict[str, Any] | None:
    """返回流程编排定义（模板，未实例化）；不存在返回 None。"""
    guide = FLOW_GUIDES.get(flow_id)
    if guide is None:
        return None
    result = dict(guide)
    result["flow_revision"] = _sha256_of(guide)
    return result


def list_flow_ids() -> list[str]:
    return sorted(FLOW_GUIDES)


# ──────────────────────────────────────────────────────────────
# Phase 3：契约自动化——样例与真实口径的机器校验
# ──────────────────────────────────────────────────────────────


def _step_param_reader_keys() -> dict[str, set[str]]:
    """各 action_type 执行器实际读取的参数键（含两级读取与别名）。

    与 plan_executor 的 _pick_step_param / handler 口径保持同步——
    这里是契约快照，测试用它对齐 steps_template，漂移即测试失败。
    """
    return {
        "MATRIX_PULL": {"room_id", "sender", "minutes", "filename", "system", "service", "overwrite"},
        "FILE_UPLOAD": {"package_name", "remote_path", "overwrite", "confirm_path", "local_path", "targets"},
        "SERVICE_CONTROL": {
            "control_action", "system_name", "system", "service_name", "service",
            "targets", "environment", "compose_dir", "compose_service", "compose_args", "env",
        },
        "RELEASE": {"package_name", "release_name", "rollback_point", "targets", "environment"},
        "ROLLBACK": {"release_name", "rollback_point", "targets"},
        "HEALTH_CHECK": {"service_name", "system_name", "targets"},
        "DML": {"connection", "sql", "confirm_text"},
        "PACKAGE_CLEANUP": {"system_name", "service_name", "keep_last", "dry_run"},
    }


def validate_flow_guide_contract(db, ctx) -> dict[str, Any]:
    """机器校验 flow guide 契约（Phase 3 核心）：

    1. steps[].tool 引用的工具必须真实注册（消灭"按记忆调用不存在工具"）
    2. args_hint 的键必须是工具 input_schema 的 properties 子集
       （占位值 <xxx> 允许；schema 不含该键 = 漂移，报错）
    3. steps_template 的参数键必须是执行器读取口径的子集
       （消灭"样例参数与执行器读取不一致"的 C 类根因）
    返回 {"ok": bool, "violations": [...]}；CI/测试用它消灭样例漂移。
    """
    from app.services.tool_registry import registry

    violations: list[str] = []
    listed = registry.list_tools(db, ctx, include_disabled=False, include_schema=True, limit=2000)
    schema_by_name = {t["name"]: t.get("input_schema") or {} for t in listed["tools"]}

    reader_keys = _step_param_reader_keys()
    for flow_id, flow in FLOW_GUIDES.items():
        for step in flow.get("steps", []):
            tool_name = str(step.get("tool") or "")
            if not tool_name.startswith("ops."):
                continue  # (matrix 回复) 等非工具步骤
            if tool_name not in schema_by_name:
                violations.append(f"{flow_id}#{step['n']}: 工具未注册 {tool_name}")
                continue
            props = set((schema_by_name[tool_name].get("properties") or {}).keys())
            for key in (step.get("args_hint") or {}):
                if key not in props:
                    violations.append(f"{flow_id}#{step['n']}: args_hint 键 {key} 不在 {tool_name} schema")

        for action_type, template in (flow.get("steps_template") or {}).items():
            params = (template.get("parameters") or {})
            nested = params.get("action_parameters")
            keys = set((nested or params).keys())
            allowed = reader_keys.get(action_type)
            if allowed is None:
                continue
            for key in keys:
                if key not in allowed:
                    violations.append(
                        f"{flow_id} steps_template.{action_type}: 参数键 {key} 不在执行器读取口径 {sorted(allowed)}"
                    )
    return {"ok": not violations, "violations": violations}


def validate_capability_annotations(db, ctx) -> dict[str, Any]:
    """MCP annotations 与 registry risk/write 字段同步校验（补充① CI 化）。

    对比两份推导：pack 组装用的映射函数 vs registry 字段直接推导——
    两者不一致即漂移（映射函数被改坏/字段语义变化都会被逮住）。
    """
    from app.services.tool_registry import registry

    violations: list[str] = []
    listed = registry.list_tools(db, ctx, include_disabled=False, include_schema=False, limit=2000)
    for tool in listed["tools"]:
        name = tool["name"]
        write = bool(tool.get("write", False))
        risk = str(tool.get("risk", ""))
        # registry 字段直接推导（期望值）
        expected_readonly = not write
        expected_destructive = risk == "high"
        expected_idempotent = risk == "low"
        # pack 组装路径（_assemble_capabilities 的映射函数复算）
        actual = _annotation_for(tool)
        if actual["readOnly"] is not expected_readonly:
            violations.append(f"{name}: readOnly={actual['readOnly']} 但 write={write}")
        if actual["destructiveHint"] is not expected_destructive:
            violations.append(f"{name}: destructiveHint 与 risk={risk} 不符")
        if actual["idempotentHint"] is not expected_idempotent:
            violations.append(f"{name}: idempotentHint 与 risk={risk} 不符")
    return {"ok": not violations, "violations": violations}


def _annotation_for(tool: dict[str, Any]) -> dict[str, bool]:
    """与 _assemble_capabilities 完全相同的映射（复制即契约：改一处不改另一处
    会被 validate_capability_annotations 逮住）。"""
    return {
        "readOnly": not bool(tool.get("write", False)),
        "destructiveHint": str(tool.get("risk", "")) == "high",
        "idempotentHint": str(tool.get("risk", "")) == "low",
    }


# ──────────────────────────────────────────────────────────────
# Phase 4：heartbeat_ops——审批催办闭环（openclaw 家族 HEARTBEAT.md/cron 消费）
# ──────────────────────────────────────────────────────────────


def _assemble_heartbeat_ops(db, *, approver_sender_ids: list[str] | None = None) -> dict[str, Any]:
    """组装定时巡检面数据：待审批/即将超时计划 + 巡检异常摘要。

    agent 把本区块写进宿主 HEARTBEAT.md / cron 任务，周期拉取后在需要时
    向房间催办——审批不再静默过期（15 分钟 TTL 一过就没人记得）。
    """
    from datetime import datetime, timedelta, timezone

    from app.db.models import ExecutionPlan

    # 数据卫生：先把过期未批的计划转终态 EXPIRED（expire_stale 幂等，无人
    # 周期调用时历史计划会一直挂在 PENDING_APPROVAL 里污染催办数据）
    try:
        from app.services.execution_plan import ExecutionPlanService

        ExecutionPlanService(db).expire_stale()
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    pending_rows = (
        db.query(ExecutionPlan)
        .filter(ExecutionPlan.status == "PENDING_APPROVAL")
        .order_by(ExecutionPlan.created_at)
        .all()
    )
    items: list[dict[str, Any]] = []
    for plan in pending_rows:
        expires = plan.expires_at
        minutes_left = None
        if expires is not None:
            try:
                exp = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
                minutes_left = int((exp - now).total_seconds() // 60)
            except Exception:
                minutes_left = None
        created = plan.created_at
        try:
            age_minutes = int(
                (now - (created if created.tzinfo else created.replace(tzinfo=timezone.utc))).total_seconds() // 60
            )
        except Exception:
            age_minutes = None
        item = {
            "plan_id": plan.id,
            "system_name": plan.system_name,
            "service_name": plan.service_name,
            "environment": plan.environment,
            "room_id": plan.room_id,
            "age_minutes": age_minutes,
            "expires_at": expires.isoformat() if expires else None,
            "minutes_left": minutes_left,
        }
        if minutes_left is not None and minutes_left <= 0:
            item["state"] = "expired"
        elif minutes_left is not None and minutes_left <= 5:
            item["state"] = "expiring"
        else:
            item["state"] = "pending"
        if approver_sender_ids and plan.request_sender_id:
            item["requested_by"] = plan.request_sender_id
        items.append(item)

    pending = [i for i in items if i["state"] == "pending"]
    expiring = [i for i in items if i["state"] == "expiring"]
    expired = [i for i in items if i["state"] == "expired"]

    ops: dict[str, Any] = {
        "approval_reminders": {
            "pending": pending,
            "expiring_soon": expiring,
            "expired": expired,
        },
        "generated_at": now.isoformat(),
        "instructions": (
            "催办动作：在 plan 的 room_id 房间发一条提醒（@ 审批人 + 短语前8位 + 剩余分钟），"
            "expiring_soon 优先；expired 的计划不再催办（终态，等用户重新触发）。"
            "没有待审批计划时保持沉默——不要发空消息。"
        ),
    }
    try:
        from app.db.models import InspectionIssue

        critical_issues = (
            db.query(InspectionIssue)
            .filter(InspectionIssue.status.in_(["open", "acknowledged"]))
            .order_by(InspectionIssue.created_at.desc())
            .limit(10)
            .all()
        )
        ops["inspection_alerts"] = [
            {
                "issue_id": issue.id,
                "title": (issue.title or issue.description or "")[:80],
                "severity": issue.severity,
                "status": issue.status,
            }
            for issue in critical_issues
        ]
    except Exception:
        ops["inspection_alerts"] = []
    return ops

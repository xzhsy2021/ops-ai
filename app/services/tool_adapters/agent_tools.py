from __future__ import annotations

from typing import Any, Dict

from app.db.models import Server
from app.services.tool_registry import registry


def _disabled_agent_summary() -> Dict[str, Any]:
    return {
        "enabled": False,
        "summary": "单项目轻量模式未启用 Agent 运行时，当前返回服务器资产的兼容视图。",
        "agents": [],
    }


@registry.register(
    name="ops.agent.list",
    title="查询 Agent 列表",
    description="查询 Agent 列表和在线状态。当前兼容未启用 Agent 的项目。",
    scopes=["ops:read"],
    risk="low",
    category="agent",
    write=False,
    enabled=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    example_prompts=["哪些 Agent 离线？", "列出 Agent 状态"],
    input_schema={"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
)
def list_agents(args: Dict[str, Any], ctx, db):
    # The current source package does not yet ship runtime Agent tables. Return a stable compatible shape.
    servers = db.query(Server).order_by(Server.created_at.desc()).limit(args.get("limit") or 50).all()
    return {
        "enabled": False,
        "summary": "单项目轻量模式不启用 Agent 表；以下为平台服务器资产兼容视图。",
        "agents": [],
        "servers_without_agent": [{"id": s.id, "name": s.name, "host": s.host, "port": s.port, "user": s.user} for s in servers],
    }


@registry.register(
    name="ops.agent.get",
    title="查看 Agent 详情",
    description="查看 Agent 详情。未启用时返回 disabled 状态。",
    scopes=["ops:read"],
    risk="low",
    category="agent",
    write=False,
    enabled=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"], "additionalProperties": False},
)
def get_agent(args: Dict[str, Any], ctx, db):
    return {**_disabled_agent_summary(), "agent_id": args.get("agent_id")}


@registry.register(
    name="ops.agent.get_status",
    title="查看 Agent 状态",
    description="查看 Agent 在线状态摘要。",
    scopes=["ops:read"],
    risk="low",
    category="agent",
    write=False,
    enabled=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"agent_id": {"type": "string"}}, "additionalProperties": False},
)
def get_agent_status(args: Dict[str, Any], ctx, db):
    return {"enabled": False, "online": 0, "offline": 0, "summary": "单项目轻量模式未启用 Agent 运行时。", "agent_id": args.get("agent_id") or ""}


@registry.register(
    name="ops.agent.list_tasks",
    title="查询 Agent 任务",
    description="查询 Agent 任务。未启用时返回空列表。",
    scopes=["ops:read"],
    risk="low",
    category="agent",
    write=False,
    enabled=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"agent_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
)
def list_agent_tasks(args: Dict[str, Any], ctx, db):
    return {"enabled": False, "items": [], "total": 0, "summary": "单项目轻量模式不启用 Agent 任务中心。"}


@registry.register(
    name="ops.agent.get_task",
    title="查看 Agent 任务详情",
    description="查看 Agent 任务详情。未启用时返回 disabled 状态。",
    scopes=["ops:read"],
    risk="low",
    category="agent",
    write=False,
    enabled=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False},
)
def get_agent_task(args: Dict[str, Any], ctx, db):
    return {"enabled": False, "task_id": args.get("task_id"), "summary": "单项目轻量模式不启用 Agent 任务中心。"}


@registry.register(
    name="ops.agent.get_logs",
    title="查看 Agent 日志摘要",
    description="查看 Agent 日志摘要。未启用时返回空摘要。",
    scopes=["ops:read"],
    risk="medium",
    category="agent",
    write=False,
    enabled=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    output_masking=True,
    input_schema={"type": "object", "properties": {"agent_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
)
def get_agent_logs(args: Dict[str, Any], ctx, db):
    return {"enabled": False, "logs": [], "summary": "单项目轻量模式不启用 Agent 日志中心。"}


@registry.register(
    name="ops.agent.health_check",
    title="Agent 健康检查",
    description="检查 Agent 功能是否启用。",
    scopes=["ops:read"],
    risk="low",
    category="agent",
    write=False,
    enabled=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    output_masking=True,
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def agent_health_check(args: Dict[str, Any], ctx, db):
    return {"enabled": False, "status": "not_configured", "summary": "当前采用单项目轻量架构，不启用服务器侧 Agent 运行时；巡检由平台本地/SSH 只读能力完成。"}

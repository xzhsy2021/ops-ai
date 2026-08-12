"""MCP tool adapter for contextual OPS help."""
from app.services.message_context import message_context_schema
from app.services.tool_registry import registry
from app.services.ops_help import help_query as _help_query


@registry.register(
    name="ops.help.query",
    title="查询 OPS 上下文帮助",
    description=(
        "根据当前 token/channel/系统环境上下文返回可用能力、审批人策略与活跃临时授权。"
        "支持主题过滤（临时审批/包上传/部署等）与 include_all 查看全部能力及状态。"
    ),
    scopes=["ops:read"],
    risk="low",
    category="help",
    write=False,
    input_schema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "主题过滤：临时审批/包上传/部署/巡检/服务器/数据库/备份/日志/风险，或系统名",
            },
            "include_all": {
                "type": "boolean",
                "description": "为 true 时包含不可用能力并标注 available/requires_authorization/forbidden",
            },
            "system_name": {
                "type": "string",
                "description": "系统名，用于展示环境列表、审批人策略与活跃临时授权",
            },
            "environment": {
                "type": "string",
                "description": "环境名（需与 system_name 搭配）",
            },
            "message_context": message_context_schema(),
        },
        "additionalProperties": False,
    },
)
def help_query(args, ctx, db):
    return _help_query(args, ctx, db)

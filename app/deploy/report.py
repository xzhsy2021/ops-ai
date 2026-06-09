from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db import DeployLogRepository, DeploymentRepository, DeploymentRuntimeRepository
from app.db.models import ToolCallLog, ToolPlan


def _dt(value: Any) -> str | None:
    return value.isoformat() if value else None



def _lower_text(value: Any) -> str:
    return str(value or "").lower()


def _is_bad_status(value: Any) -> bool:
    text = _lower_text(value)
    if text == "partial_failed":
        return True
    return text in {"failed", "error", "cancelled", "canceled", "timeout"} or "失败" in text or "异常" in text


def _suggest_for_message(message: str) -> str:
    text = _lower_text(message)
    if any(key in text for key in ["permission denied", "权限", "access denied"]):
        return "检查部署用户权限、目标目录写入权限和 sudo/systemd 权限。"
    if any(key in text for key in ["no space", "disk", "磁盘", "空间不足", "空间"]):
        return "检查目标服务器磁盘空间，优先清理日志、旧发布包或临时目录。"
    if any(key in text for key in ["connection refused", "timed out", "timeout", "ssh", "连接失败", "连接超时"]):
        return "检查服务器网络、SSH 端口、账号、密钥和跳板机配置。"
    if any(key in text for key in ["no such file", "not found", "不存在", "找不到"]):
        return "检查发布包、发布目录、脚本路径或服务配置是否存在。"
    if any(key in text for key in ["systemctl", "service", "restart", "端口", "port", "pm2"]):
        return "检查服务名、启动命令、端口占用和服务运行账号权限。"
    if any(key in text for key in ["checksum", "sha256", "校验"]):
        return "检查发布包是否完整上传，必要时重新上传并校验 SHA256。"
    return "查看失败步骤上下文日志，确认目标服务器状态和发布配置。"


def _analyze_failure(deployment: Any, logs: List[Any], server_tasks: List[Any], step_tasks: List[Any]) -> Dict[str, Any]:
    error_logs = []
    for log in logs:
        message = str(getattr(log, "message", "") or "")
        level = str(getattr(log, "level", "") or "")
        if level.lower() in {"error", "fatal"} or any(key in message for key in ["失败", "异常", "错误", "Error", "ERROR", "Failed", "failed"]):
            error_logs.append(log)

    failed_servers = []
    for task in server_tasks:
        if _is_bad_status(getattr(task, "status", "")) or _is_bad_status(getattr(task, "message", "")):
            failed_servers.append({
                "server_name": getattr(task, "server_name", "") or "-",
                "status": getattr(task, "status", "") or "failed",
                "message": getattr(task, "message", "") or "-",
            })

    failed_steps = []
    for task in step_tasks:
        if _is_bad_status(getattr(task, "status", "")) or _is_bad_status(getattr(task, "message", "")):
            failed_steps.append({
                "server_name": getattr(task, "server_name", "") or "-",
                "step_name": getattr(task, "step_name", "") or "-",
                "step_type": getattr(task, "step_type", "") or "-",
                "status": getattr(task, "status", "") or "failed",
                "message": getattr(task, "message", "") or "-",
            })

    if not failed_servers and error_logs:
        for log in error_logs[:8]:
            step = str(getattr(log, "step_name", "") or "")
            server_name = "-"
            if ":" in step:
                server_name = step.split(":", 1)[1] or "-"
            elif step.startswith("server"):
                server_name = step
            failed_servers.append({"server_name": server_name, "status": "failed", "message": str(getattr(log, "message", "") or "-")})

    messages = [str(getattr(x, "message", "") or "") for x in error_logs[:10]]
    messages.extend(str(x.get("message", "") or "") for x in failed_steps[:10])
    suggestions = []
    for msg in messages or [getattr(deployment, "message", "") or ""]:
        suggestion = _suggest_for_message(msg)
        if suggestion not in suggestions:
            suggestions.append(suggestion)
    suggestions = suggestions[:5]

    status = getattr(deployment, "status", "") or ""
    bad_deployment_status = _is_bad_status(status)
    summary_text = "发布成功，未发现失败步骤。"
    if bad_deployment_status or failed_servers or failed_steps or error_logs:
        server_names = [x.get("server_name") for x in failed_servers if x.get("server_name") and x.get("server_name") != "-"]
        step_names = [x.get("step_name") for x in failed_steps if x.get("step_name") and x.get("step_name") != "-"]
        bits = []
        if server_names:
            bits.append(f"失败服务器：{', '.join(sorted(set(server_names)))}")
        if step_names:
            bits.append(f"失败步骤：{', '.join(sorted(set(step_names)))}")
        if error_logs:
            bits.append(f"错误日志：{len(error_logs)} 条")
        summary_text = "；".join(bits) or (getattr(deployment, "message", "") or "发布失败，建议查看错误日志。")

    return {
        "status": "failed" if failed_servers or failed_steps or error_logs or bad_deployment_status else "ok",
        "summary_text": summary_text,
        "failed_servers": failed_servers[:20],
        "failed_steps": failed_steps[:30],
        "error_logs": [
            {
                "time": _dt(getattr(log, "created_at", None)),
                "level": getattr(log, "level", ""),
                "step_name": getattr(log, "step_name", ""),
                "message": getattr(log, "message", ""),
            }
            for log in error_logs[-20:]
        ],
        "suggestions": suggestions,
    }

def deployment_report_payload(deployment_id: str, db: Session) -> Dict[str, Any]:
    deployment = DeploymentRepository(db).get_by_id(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    logs = DeployLogRepository(db).list_by_deployment(deployment_id, limit=5000)
    runtime = DeploymentRuntimeRepository(db).list_for_deployment(deployment_id)
    duration_seconds = None
    if deployment.started_at and deployment.finished_at:
        try:
            duration_seconds = int((deployment.finished_at - deployment.started_at).total_seconds())
        except Exception:
            duration_seconds = None
    log_levels: Dict[str, int] = {}
    for log in logs:
        key = str(log.level or "info")
        log_levels[key] = log_levels.get(key, 0) + 1
    related_plans = db.query(ToolPlan).filter(ToolPlan.related_deployment_id == deployment_id).order_by(ToolPlan.created_at.desc()).limit(5).all()
    related_tool_calls = db.query(ToolCallLog).filter(ToolCallLog.related_deployment_id == deployment_id).order_by(ToolCallLog.created_at.desc()).limit(10).all()
    server_list = [x.strip() for x in str(deployment.servers or "").split(",") if x.strip()]
    rollback_available = bool(deployment.status == "success" and server_list)
    failure_analysis = _analyze_failure(deployment, logs, runtime["server_tasks"], runtime["step_tasks"])

    return {
        "id": deployment.id,
        "system": deployment.system,
        "service": deployment.service,
        "environment": deployment.environment,
        "status": deployment.status,
        "servers": deployment.servers,
        "version": deployment.version,
        "message": deployment.message,
        "created_by": deployment.created_by,
        "duration_seconds": duration_seconds,
        "rollback_available": rollback_available,
        "summary": {
            "app": deployment.service or deployment.system,
            "system": deployment.system,
            "service": deployment.service,
            "environment": deployment.environment,
            "version": deployment.version,
            "status": deployment.status,
            "duration_seconds": duration_seconds,
            "servers": server_list,
            "operator": deployment.created_by,
            "rollback_available": rollback_available,
        },
        "log_summary": {"total": len(logs), "levels": log_levels},
        "failure_analysis": failure_analysis,
        "summary_text": failure_analysis.get("summary_text") if failure_analysis.get("status") == "failed" else f"发布{deployment.status or '-'}：{deployment.system}/{deployment.service or '-'} @ {deployment.environment or '-'}，服务器 {len(server_list)} 台。",
        "failed_servers": failure_analysis.get("failed_servers", []),
        "failed_steps": failure_analysis.get("failed_steps", []),
        "suggestions": failure_analysis.get("suggestions", []),
        "related_tool_plans": [
            {
                "plan_id": plan.id,
                "plan_type": plan.plan_type,
                "source_tool": plan.source_tool,
                "risk_level": plan.risk_level,
                "status": plan.status,
                "created_by": plan.created_by,
                "created_at": _dt(plan.created_at),
            }
            for plan in related_plans
        ],
        "related_tool_calls": [
            {
                "id": call.id,
                "tool_name": call.tool_name,
                "status": call.status,
                "risk_level": call.risk_level,
                "username": call.username,
                "token_owner": call.token_owner,
                "created_at": _dt(call.created_at),
            }
            for call in related_tool_calls
        ],
        "started_at": _dt(deployment.started_at),
        "finished_at": _dt(deployment.finished_at),
        "server_tasks": [
            {
                "server_name": task.server_name,
                "status": task.status,
                "message": task.message,
                "started_at": _dt(task.started_at),
                "finished_at": _dt(task.finished_at),
            }
            for task in runtime["server_tasks"]
        ],
        "step_tasks": [
            {
                "server_name": task.server_name,
                "step_name": task.step_name,
                "step_type": task.step_type,
                "status": task.status,
                "message": task.message,
                "started_at": _dt(task.started_at),
                "finished_at": _dt(task.finished_at),
            }
            for task in runtime["step_tasks"]
        ],
        "distributions": [
            {
                "server_name": item.server_name,
                "package_name": item.package_name,
                "remote_path": item.remote_path,
                "status": item.status,
                "reused": bool(item.reused),
                "local_sha256": item.local_sha256,
                "remote_sha256": item.remote_sha256,
                "duration_ms": item.duration_ms,
                "message": item.message,
            }
            for item in runtime["distributions"]
        ],
        "logs": [
            {
                "time": _dt(log.created_at),
                "level": log.level,
                "step_name": log.step_name,
                "message": log.message,
            }
            for log in logs
        ],
    }


def _table_cell(value: Any) -> str:
    return str(value or "-").replace("|", "/")


def deployment_report_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        f"# OPS 发布报告 `{payload['id']}`",
        "",
        f"- 系统：{payload.get('system') or '-'}",
        f"- 服务：{payload.get('service') or '-'}",
        f"- 环境：{payload.get('environment') or '-'}",
        f"- 状态：{payload.get('status') or '-'}",
        f"- 版本/包：{payload.get('version') or '-'}",
        f"- 服务器：{payload.get('servers') or '-'}",
        f"- 操作人：{payload.get('created_by') or '-'}",
        f"- 耗时：{payload.get('duration_seconds') if payload.get('duration_seconds') is not None else '-'} 秒",
        f"- 可回滚：{'是' if payload.get('rollback_available') else '否'}",
        f"- 开始：{payload.get('started_at') or '-'}",
        f"- 结束：{payload.get('finished_at') or '-'}",
        "",
        "## 失败分析",
        "",
        payload.get("summary_text") or "-",
        "",
        "### 失败服务器",
        "",
        "| 服务器 | 状态 | 消息 |",
        "|---|---|---|",
    ]
    for item in payload.get("failed_servers", []) or []:
        lines.append(f"| {_table_cell(item.get('server_name'))} | {_table_cell(item.get('status'))} | {_table_cell(item.get('message'))} |")
    if not payload.get("failed_servers"):
        lines.append("| - | - | 未发现失败服务器 |")

    lines.extend(["", "### 处理建议", ""])
    for suggestion in payload.get("suggestions", []) or ["无特殊建议。"]:
        lines.append(f"- {suggestion}")

    lines.extend([
        "",
        "## 服务器任务",
        "",
        "| 服务器 | 状态 | 消息 |",
        "|---|---|---|",
    ])
    for task in payload.get("server_tasks", []):
        lines.append(f"| {_table_cell(task.get('server_name'))} | {_table_cell(task.get('status'))} | {_table_cell(task.get('message'))} |")

    lines.extend(["", "## 发布包分发", "", "| 服务器 | 包 | 状态 | 复用 | 远程路径 |", "|---|---|---|---|---|"])
    for item in payload.get("distributions", []):
        lines.append(f"| {_table_cell(item.get('server_name'))} | {_table_cell(item.get('package_name'))} | {_table_cell(item.get('status'))} | {item.get('reused')} | {_table_cell(item.get('remote_path'))} |")

    lines.extend(["", "## 步骤任务", "", "| 服务器 | 步骤 | 类型 | 状态 | 消息 |", "|---|---|---|---|---|"])
    for task in payload.get("step_tasks", []):
        lines.append(f"| {_table_cell(task.get('server_name'))} | {_table_cell(task.get('step_name'))} | {_table_cell(task.get('step_type'))} | {_table_cell(task.get('status'))} | {_table_cell(task.get('message'))} |")

    lines.extend(["", "## 日志", ""])
    for log in payload.get("logs", [])[-300:]:
        lines.append(f"- `{log.get('time')}` **{str(log.get('level') or '').upper()}** `{log.get('step_name') or '-'}` {log.get('message') or ''}")
    return "\n".join(lines)

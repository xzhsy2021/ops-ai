"""Actionable remediation suggestions for system diagnostics."""
from __future__ import annotations

from typing import Any, Dict, List


def _add(items: List[Dict[str, Any]], *, key: str, severity: str, title: str, reason: str, actions: List[str], source: str = "") -> None:
    items.append({
        "key": key,
        "severity": severity,
        "title": title,
        "reason": reason,
        "actions": actions,
        "source": source,
    })


def build_recommendations(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build concise, actionable suggestions from health/diagnostic payloads."""
    items: List[Dict[str, Any]] = []
    sections = payload.get("sections") or {}
    health = sections.get("health") if sections else payload
    checks = health.get("checks") or payload.get("checks") or {}
    build_info = sections.get("build_info") or payload.get("build_info") or {}
    recent_errors = sections.get("recent_errors") or payload.get("recent_errors") or {}
    mcp = sections.get("mcp") or payload.get("mcp") or {}

    secret = checks.get("secret_key") or {}
    if secret.get("status") == "warn":
        _add(
            items,
            key="secret_key",
            severity="medium",
            title="配置生产级 OPS_SECRET_KEY",
            reason=secret.get("message") or "当前密钥配置偏弱。",
            actions=[
                "在 .env 或启动环境中设置 OPS_SECRET_KEY，建议 32 字符以上随机字符串。",
                "重启后端服务。",
                "刷新 /system/diagnostics，确认 secret_key 恢复为正常。",
            ],
            source="system.health.secret_key",
        )

    backups = checks.get("backups") or {}
    if backups.get("status") == "warn" or backups.get("stale"):
        _add(
            items,
            key="backup_stale",
            severity="medium",
            title="创建一次最新数据库备份",
            reason=backups.get("message") or "当前没有可用备份或备份已过期。",
            actions=[
                "在系统状态页点击“立即备份数据库”。",
                "或执行 scripts/backup.sh / scripts/backup.ps1 做完整目录备份。",
                "确认备份列表出现最新文件，并记录备份时间。",
            ],
            source="system.health.backups",
        )

    disk = checks.get("disk") or {}
    if disk.get("status") in {"warn", "error"}:
        _add(
            items,
            key="disk_usage",
            severity="high" if disk.get("status") == "error" else "medium",
            title="清理磁盘或扩容 APP_DATA_DIR 所在分区",
            reason=disk.get("message") or "磁盘使用率偏高。",
            actions=[
                "优先清理旧日志、旧发布包、过期 runtime 临时文件。",
                "在维护页先做清理预览，再执行清理。",
                "业务环境建议扩容磁盘或迁移 APP_DATA_DIR。",
            ],
            source="system.health.disk",
        )

    runtime_dirs = checks.get("runtime_dirs") or {}
    if runtime_dirs.get("status") == "error":
        _add(
            items,
            key="runtime_dirs",
            severity="high",
            title="修复运行目录权限",
            reason=runtime_dirs.get("message") or "部分运行目录不可写。",
            actions=[
                "检查 APP_DATA_DIR、LOG_DIR、BACKUP_DIR、UPLOAD_DIR 是否存在。",
                "确认启动用户对这些目录有读写权限。",
                "修复后重启服务并重新运行安装诊断。",
            ],
            source="system.health.runtime_dirs",
        )

    if build_info.get("status") in {"warn", "error"} or (build_info.get("frontend") or {}).get("dist_stale"):
        _add(
            items,
            key="frontend_build",
            severity="medium" if build_info.get("status") == "warn" else "high",
            title="重新构建前端 dist",
            reason=build_info.get("message") or "前端构建产物缺失或过期。",
            actions=[
                "进入 frontend 目录执行 npm install。",
                "执行 npm run build。",
                "重启后端并 Ctrl+F5 强制刷新浏览器缓存。",
            ],
            source="diagnostics.build_info",
        )

    if (recent_errors.get("error_count") or 0) > 0:
        _add(
            items,
            key="recent_errors",
            severity="medium",
            title="查看最近错误日志",
            reason=f"最近日志中发现 {recent_errors.get('error_count')} 条错误。",
            actions=[
                "打开 /system/diagnostics 的“最近错误与警告”区域查看摘要。",
                "导出 JSON 诊断报告并保留现场。",
                "按错误来源模块优先排查 API、Worker、数据库或前端构建。",
            ],
            source="diagnostics.recent_errors",
        )

    if mcp.get("schema_errors"):
        _add(
            items,
            key="mcp_schema",
            severity="medium",
            title="修复 MCP 工具 schema",
            reason="部分 MCP 工具 input_schema 不符合工具注册约定。",
            actions=[
                "查看 MCP 自检中的 schema_errors。",
                "确保每个工具 input_schema.type 为 object。",
                "修复后运行 node scripts/frontend_route_check.js 和 MCP 自检。",
            ],
            source="diagnostics.mcp",
        )

    if not items:
        _add(
            items,
            key="healthy_baseline",
            severity="low",
            title="保持当前运行基线",
            reason="未发现必须立即处理的高优先级问题。",
            actions=[
                "保留当前诊断报告作为健康基线。",
                "发布或升级前重新运行诊断。",
                "定期检查备份、磁盘、MCP 调用审计和最近错误。",
            ],
            source="diagnostics.summary",
        )
    return items[:12]

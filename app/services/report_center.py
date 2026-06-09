"""Report Center for diagnostics, release, backup and MCP/AI audit evidence.

Iter39 keeps report generation read-only with respect to production resources:
it packages data that already exists in OPS into immutable JSON/Markdown
artifacts, records metadata, and emits a local notification event for traceability.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_runtime_path
from app.db.models import NotificationEvent, ReportArtifact

SCHEMA_VERSION = "iter39.report-center.v1"
REPORT_TYPES = {
    "diagnostics": {
        "title": "系统诊断报告",
        "target_type": "system",
        "description": "系统健康、构建状态、运行目录、最近错误和建议。",
        "formats": ["json", "md"],
    },
    "ai_diagnostics": {
        "title": "AI 诊断分析报告",
        "target_type": "system",
        "description": "只读 AI 诊断分析、MCP 工具链与安全边界。",
        "formats": ["json", "md"],
    },
    "operation_chain": {
        "title": "MCP/AI 操作链路报告",
        "target_type": "operation_chain",
        "description": "工具调用、风险策略、统一任务、发布计划与审计证据链。",
        "formats": ["json", "md"],
    },
    "operation_chains_index": {
        "title": "操作链路索引报告",
        "target_type": "audit",
        "description": "最近 OPS/MCP/AI 操作链路摘要索引。",
        "formats": ["json", "md"],
    },
    "ai_analysis": {
        "title": "AI 分析报告",
        "target_type": "ai_analysis",
        "description": "AI 工作流分析结果、事实、推断、建议和证据链报告。",
        "formats": ["json", "md"],
    },
    "deployment": {
        "title": "发布报告",
        "target_type": "deployment",
        "description": "发布结果、失败分析、分发校验、步骤任务和日志摘要。",
        "formats": ["json", "md"],
    },

    "inspection": {
        "title": "巡检报告",
        "target_type": "inspection_run",
        "description": "服务器巡检、项目巡检与项目综合巡检结果报告。",
        "formats": ["json", "md", "html"],
    },
    "db_query_export": {
        "title": "数据库查询导出",
        "target_type": "database",
        "description": "只读数据库查询结果导出制品，支持 CSV、JSON、XLSX、Markdown、SQL Query 和 SQL Insert。",
        "formats": ["csv", "json", "xlsx", "md", "sql_query"],
        "can_generate": False,
    },
}


def _now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _reports_dir() -> Path:
    path = Path(get_runtime_path("REPORT_DIR", "reports"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(value: str, fallback: str = "report") -> str:
    name = re.sub(r"[^A-Za-z0-9._@+\-=\u4e00-\u9fff]+", "_", str(value or fallback)).strip("._")
    return name[:120] or fallback


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _table_value(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=_json_default) if isinstance(value, (dict, list)) else str(value if value is not None else "-")
    return text.replace("|", "/").replace("\n", " ")[:500]


def _risk_emoji(level: str) -> str:
    return {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟢", "NONE": "✅"}.get(level, "⚪")


def _render_category_account(facts: Dict[str, Any], risk_level: str, message: str, suggestion: str) -> List[str]:
    """Render ACCOUNT_SECURITY parsed_facts as markdown table."""
    lines: List[str] = []
    uid0 = facts.get("uid0_accounts") or []
    login_users = facts.get("login_users") or []
    login_count = facts.get("login_user_count", len(login_users))
    total = facts.get("total_users", "-")

    lines.append(f"**判定**: {message}")
    lines.append("")

    if uid0:
        lines.extend(["| 账号 | UID | Shell | Home |", "|---|---|---|---|"])
        for a in uid0[:20]:
            lines.append(f"| {_table_value(a.get('user'))} | {_table_value(a.get('uid'))} | {_table_value(a.get('shell'))} | {_table_value(a.get('home'))} |")
        lines.append("")

    if login_users:
        lines.extend([f"**可登录用户** ({login_count} 个，展示前 {min(len(login_users), 20)} 个)：", "",
                       "| 账号 | Shell |", "|---|---|"])
        for u in login_users[:20]:
            lines.append(f"| {_table_value(u.get('user'))} | {_table_value(u.get('shell'))} |")
        lines.append("")

    lines.append(f"**建议**: {suggestion}")
    lines.append("")
    return lines


def _render_category_disk(facts: Dict[str, Any], risk_level: str, message: str, suggestion: str) -> List[str]:
    """Render DISK parsed_facts as markdown table."""
    lines: List[str] = []
    filesystems = facts.get("filesystems") or []
    max_pct = facts.get("max_pct", 0)

    lines.append(f"**判定**: {message}")
    lines.append("")

    if filesystems:
        lines.extend(["| 挂载点 | 类型 | 总量 | 已用 | 可用 | 使用率 |", "|---|---|---:|---:|---:|---:|"])
        for fs in filesystems[:30]:
            pct = fs.get("pct", 0)
            pct_str = f"**{pct}%**" if pct >= 90 else f"{pct}%"
            lines.append(f"| {_table_value(fs.get('mount'))} | {_table_value(fs.get('type'))} | {_table_value(fs.get('total'))} | {_table_value(fs.get('used'))} | {_table_value(fs.get('avail'))} | {pct_str} |")
        lines.append("")

    lines.append(f"**建议**: {suggestion}")
    lines.append("")
    return lines


def _render_category_login(facts: Dict[str, Any], risk_level: str, message: str, suggestion: str) -> List[str]:
    """Render LOGIN_SECURITY parsed_facts as markdown tables."""
    lines: List[str] = []
    success = facts.get("success_logins") or []
    attackers = facts.get("top_attackers") or []
    failed_total = facts.get("failed_total", 0)

    lines.append(f"**判定**: {message}")
    lines.append("")

    root_logins = [l for l in success if l.get("user") == "root"]
    if root_logins:
        lines.extend(["**root 登录** (最近 5 次)：", "",
                       "| 时间 | 来源 IP | 终端 | 时长 |", "|---|---|---|---|"])
        for l in root_logins[:5]:
            lines.append(f"| {_table_value(l.get('start'))} | {_table_value(l.get('ip'))} | {_table_value(l.get('tty'))} | {_table_value(l.get('duration'))} |")
        lines.append("")
    elif success:
        lines.extend(["**近期成功登录** (前 5 条)：", "",
                       "| 用户 | IP | 终端 | 时间 | 时长 |", "|---|---|---|---|---|"])
        for l in success[:5]:
            lines.append(f"| {_table_value(l.get('user'))} | {_table_value(l.get('ip'))} | {_table_value(l.get('tty'))} | {_table_value(l.get('start'))} | {_table_value(l.get('duration'))} |")
        lines.append("")

    if attackers:
        lines.extend(["**失败登录 Top 攻击源**：", "",
                       "| 攻击 IP | 失败次数 | 主要尝试账号 |", "|---|---|---|"])
        for a in attackers[:10]:
            users = ", ".join((a.get("attempted_users") or [])[:5])
            lines.append(f"| {_table_value(a.get('ip'))} | {_table_value(a.get('count'))} | {_table_value(users)} |")
        if failed_total:
            lines.append(f"\n失败登录总计：{failed_total} 次")
        lines.append("")

    lines.append(f"**建议**: {suggestion}")
    lines.append("")
    return lines


def _render_category_process_port(facts: Dict[str, Any], risk_level: str, message: str, suggestion: str) -> List[str]:
    """Render PROCESS_PORT parsed_facts as markdown tables."""
    lines: List[str] = []
    high_risk = facts.get("high_risk_ports") or []
    top_cpu = facts.get("top_cpu_procs") or []
    listening_count = facts.get("listening_count", 0)

    lines.append(f"**判定**: {message}")
    lines.append("")

    if high_risk:
        lines.extend(["| 端口 | 协议 | 进程 | 绑定地址 | 暴露面 |", "|---|---|---|---|---|"])
        for p in high_risk[:20]:
            exposed = "🌐 公网" if p.get("is_world") else "本机"
            lines.append(f"| {p.get('port', '-')} | {_table_value(p.get('proto'))} | {_table_value(p.get('service'))} | {_table_value(p.get('bind'))} | {exposed} |")
        lines.append("")

    if top_cpu:
        lines.extend(["**Top CPU 进程**：", "",
                       "| PID | 用户 | %CPU | %MEM | 命令 |", "|---|---|---|---|---|"])
        for p in top_cpu[:10]:
            lines.append(f"| {_table_value(p.get('pid'))} | {_table_value(p.get('user'))} | {_table_value(p.get('cpu'))} | {_table_value(p.get('mem'))} | {_table_value(p.get('cmd'))} |")
        lines.append("")

    if listening_count:
        lines.append(f"监听端口总数：{listening_count}")
        lines.append("")

    lines.append(f"**建议**: {suggestion}")
    lines.append("")
    return lines


def _render_category_backup(facts: Dict[str, Any], risk_level: str, message: str, suggestion: str) -> List[str]:
    """Render BACKUP parsed_facts as markdown."""
    lines: List[str] = []
    backup_dirs = facts.get("backup_dirs") or []
    cron_lines = facts.get("cron_lines") or []
    zero_byte_files = facts.get("zero_byte_files") or []

    lines.append(f"**判定**: {message}")
    lines.append("")

    if backup_dirs:
        lines.append("**已检查路径**：")
        for d in backup_dirs[:10]:
            exists = "存在" if d.get("exists") else "不存在"
            file_count = len(d.get("files") or [])
            lines.append(f"- `{d.get('path')}` — {exists}，{file_count} 个文件")
            for f in (d.get("files") or [])[:5]:
                lines.append(f"  - {_table_value(f.get('name'))} ({_table_value(f.get('size'))} bytes)")
        lines.append("")
    else:
        lines.append("**已检查路径**: /backup、/data/backups、/var/backups（均未发现备份文件）")
        lines.append("")

    if zero_byte_files:
        lines.append(f"**0 字节文件**（{len(zero_byte_files)} 个，可能损坏）：")
        for f in zero_byte_files[:10]:
            lines.append(f"- `{_table_value(f)}`")
        lines.append("")

    if cron_lines:
        lines.extend(["**cron 备份任务**：", "", "```"])
        for c in cron_lines[:10]:
            lines.append(c)
        lines.extend(["```", ""])
    else:
        lines.append("**cron 备份任务**: 无")
        lines.append("")

    lines.append(f"**建议**: {suggestion}")
    lines.append("")
    return lines


def _render_category_history(facts: Dict[str, Any], risk_level: str, message: str, suggestion: str) -> List[str]:
    """Render COMMAND_HISTORY parsed_facts as markdown."""
    lines: List[str] = []
    hit = facts.get("hit_keywords") or []
    lines.append(f"**判定**: {message}")
    lines.append("")
    if hit:
        lines.append(f"**命中危险特征**（{len(hit)} 个）：")
        for kw in hit[:10]:
            lines.append(f"- `{_table_value(kw)}`")
        lines.append("")
    lines.append(f"**建议**: {suggestion}")
    lines.append("")
    return lines


def _render_category_firewall(facts: Dict[str, Any], risk_level: str, message: str, suggestion: str) -> List[str]:
    """Render FIREWALL parsed_facts as markdown."""
    lines: List[str] = []
    global_pass = facts.get("global_pass_lines") or []
    default_accept = facts.get("default_policy_accept", False)
    lines.append(f"**判定**: {message}")
    lines.append("")
    if global_pass:
        lines.append(f"**全局放行规则**（{len(global_pass)} 条）：")
        for r in global_pass[:5]:
            lines.append(f"- `{_table_value(r[:120])}`")
        lines.append("")
    if default_accept:
        lines.append("**INPUT 链默认策略**: ACCEPT（存在风险）")
        lines.append("")
    lines.append(f"**建议**: {suggestion}")
    lines.append("")
    return lines


def _render_category_service(facts: Dict[str, Any], risk_level: str, message: str, suggestion: str) -> List[str]:
    """Render SERVICE_STATUS parsed_facts as markdown."""
    lines: List[str] = []
    inactive = facts.get("inactive_core_services") or []
    failed = facts.get("failed_units") or []
    lines.append(f"**判定**: {message}")
    lines.append("")
    if inactive:
        lines.append(f"**未运行的核心服务**（{len(inactive)} 个）：{', '.join(inactive)}")
        lines.append("")
    if failed:
        lines.append(f"**失败单元**（{len(failed)} 个）：")
        for u in failed[:10]:
            lines.append(f"- `{_table_value(u)}`")
        lines.append("")
    lines.append(f"**建议**: {suggestion}")
    lines.append("")
    return lines


def _render_category_memory(facts: Dict[str, Any], risk_level: str, message: str, suggestion: str) -> List[str]:
    """Render MEMORY parsed_facts as markdown."""
    lines: List[str] = []
    mem_pct = facts.get("mem_pct", 0)
    swap_pct = facts.get("swap_pct", 0)
    mem_total = facts.get("mem_total") or "-"
    swap_total = facts.get("swap_total") or "-"
    lines.append(f"**判定**: {message}")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 内存使用率 | **{mem_pct}%** (总量 {_table_value(mem_total)}) |")
    if swap_pct > 0:
        lines.append(f"| Swap 使用率 | **{swap_pct}%** (总量 {_table_value(swap_total)}) |")
    lines.append("")
    lines.append(f"**建议**: {suggestion}")
    lines.append("")
    return lines


def _render_category_item(item: Dict[str, Any]) -> List[str]:
    """Render a single inspection item with structured facts if available."""
    category = item.get("category", "")
    risk_level = item.get("risk_level", "NONE")
    message = item.get("message", "")
    suggestion = item.get("suggestion", "")
    facts = item.get("parsed_facts")
    emoji = _risk_emoji(risk_level)

    cat_name = {
        "LOGIN_SECURITY": "登录安全",
        "ACCOUNT_SECURITY": "账号安全",
        "PROCESS_PORT": "进程端口",
        "DISK": "磁盘空间",
        "BACKUP": "备份任务",
        "COMMAND_HISTORY": "命令日志",
        "FIREWALL": "防火墙",
        "SERVICE_STATUS": "服务状态",
        "MEMORY": "内存状况",
    }.get(category, category)

    lines = [f"#### {emoji} {cat_name} [{risk_level}]"]

    if facts and isinstance(facts, dict):
        # 量化摘要
        summary = facts.get("summary")
        if summary:
            lines.append(f"**📊 量化指标**: {summary}")
            lines.append("")

        renderer = {
            "ACCOUNT_SECURITY": _render_category_account,
            "DISK": _render_category_disk,
            "LOGIN_SECURITY": _render_category_login,
            "PROCESS_PORT": _render_category_process_port,
            "BACKUP": _render_category_backup,
            "COMMAND_HISTORY": _render_category_history,
            "FIREWALL": _render_category_firewall,
            "SERVICE_STATUS": _render_category_service,
            "MEMORY": _render_category_memory,
        }.get(category)
        if renderer:
            lines.extend(renderer(facts, risk_level, message, suggestion))
        else:
            lines.extend([f"**判定**: {message}", "", f"**建议**: {suggestion}", ""])
    else:
        lines.extend([f"**判定**: {message}", "", f"**建议**: {suggestion}", ""])

    # 评分依据（基于 parsed_facts.criteria 和 parsed_facts.thresholds）
    if facts and isinstance(facts, dict):
        criteria = facts.get("criteria")
        thresholds = facts.get("thresholds")
        if criteria or thresholds:
            lines.append("**📐 评分依据**")
            lines.append("")
            if criteria:
                lines.append(f"- 判定标准: {criteria}")
            if thresholds:
                t_parts = []
                for k, v in thresholds.items():
                    if isinstance(v, list):
                        v = ", ".join(str(x) for x in v)
                    t_parts.append(f"{k}={v}")
                if t_parts:
                    lines.append(f"- 阈值: {'; '.join(t_parts)}")
            lines.append(f"- 实际等级: {risk_level}")
            lines.append("")

    return lines


def _inspection_markdown(title: str, payload: Dict[str, Any]) -> str:
    data = payload.get("data") or {}
    run = data.get("run") or {}
    items = data.get("items") or []
    issues = data.get("issues") or []
    ledger = data.get("ledger")
    if isinstance(ledger, dict):
        ledger = [ledger]
    elif not isinstance(ledger, list):
        ledger = []
    category_summary = data.get("category_summary") or []
    issue_status = data.get("issue_status") or {}
    summary = payload.get("summary") or {}
    is_periodic = bool(summary.get("report_kind"))
    runs = data.get("runs") or []

    # ── 1. Header ──
    lines = [
        f"# {title}",
        "",
        "## 📊 概览",
        "",
        f"- 生成时间：{payload.get('generated_at') or '-'}",
        f"- 巡检范围：{run.get('scope_type') or summary.get('scope_type') or '-'}",
    ]

    server_ids = sorted(set(
        item.get("server_id") or run.get("server_id") or ""
        for item in items
        if item.get("server_id") or run.get("server_id")
    ))
    if server_ids:
        lines.append(f"- 服务器：{len(server_ids)} 台")
    else:
        lines.append(f"- 服务器：{run.get('server_id') or summary.get('server_id') or '-'}")

    if run.get("project_id") or summary.get("project_id"):
        lines.append(f"- 项目：{run.get('project_id') or summary.get('project_id')}")

    high = run.get("high_count", summary.get("high_count", 0))
    medium = run.get("medium_count", summary.get("medium_count", 0))
    low = run.get("low_count", summary.get("low_count", 0))
    score = run.get("score") if run.get("score") is not None else summary.get("score", summary.get("avg_score", "-"))
    open_issues = summary.get("open_issue_count", issue_status.get("OPEN", 0) + issue_status.get("PROCESSING", 0))

    lines.extend([
        f"- 总评分：{score}/100 · 🔴 {high} 高 / 🟠 {medium} 中 / 🟢 {low} 低",
        f"- 未闭环：{open_issues} 项",
        "",
    ])

    # ── 2. Risk Summary (non-PASS only, sorted by severity) ──
    risk_items = [i for i in items if i.get("risk_level") in ("HIGH", "MEDIUM", "LOW")]
    if risk_items:
        risk_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        risk_items.sort(key=lambda x: (risk_order.get(x.get("risk_level", "NONE"), 9), x.get("server_id", "")))

        lines.append("## 🚨 风险摘要（按严重度排序）")
        lines.append("")

        for level, emoji, label in [("HIGH", "🔴", "高危"), ("MEDIUM", "🟠", "中危"), ("LOW", "🟢", "低危")]:
            level_items = [i for i in risk_items if i.get("risk_level") == level]
            if not level_items:
                continue
            lines.append(f"### {emoji} {label} ({len(level_items)})")
            lines.append("")
            lines.extend(["| 服务器 | 巡检项 | 关键事实 | 建议 |", "|---|---|---|---|"])
            for i in level_items[:50]:
                srv = i.get("server_id") or "-"
                cat_name = {"LOGIN_SECURITY": "登录安全", "ACCOUNT_SECURITY": "账号安全", "PROCESS_PORT": "进程端口", "DISK": "磁盘空间", "BACKUP": "备份任务", "COMMAND_HISTORY": "命令日志", "FIREWALL": "防火墙", "SERVICE_STATUS": "服务状态", "MEMORY": "内存状况"}.get(i.get("category"), i.get("category", "-"))
                msg = (i.get("message") or "-")[:80]
                sug = (i.get("suggestion") or "-")[:60]
                lines.append(f"| {_table_value(srv)} | {cat_name} | {msg} | {sug} |")
            lines.append("")

    # ── 3. Per-server detail (collapsible) ──
    if server_ids and len(server_ids) > 1:
        lines.append("## 📦 按服务器展开")
        lines.append("")

        for srv in server_ids:
            srv_items = [i for i in items if i.get("server_id") == srv]
            if not srv_items:
                continue

            srv_high = sum(1 for i in srv_items if i.get("risk_level") == "HIGH")
            srv_medium = sum(1 for i in srv_items if i.get("risk_level") == "MEDIUM")
            srv_low = sum(1 for i in srv_items if i.get("risk_level") == "LOW")
            has_high = srv_high > 0

            risk_parts = []
            if srv_high: risk_parts.append(f"{srv_high}高")
            if srv_medium: risk_parts.append(f"{srv_medium}中")
            if srv_low: risk_parts.append(f"{srv_low}低")
            risk_str = "/".join(risk_parts) if risk_parts else "全通过"
            srv_emoji = "🔴" if has_high else ("🟠" if srv_medium else "✅")

            open_tag = " open" if has_high else ""
            lines.append(f"<details{open_tag}>")
            lines.append(f"<summary>{srv_emoji} {srv} · {risk_str}</summary>")
            lines.append("")

            for item in srv_items:
                lines.extend(_render_category_item(item))

            lines.append("</details>")
            lines.append("")

    else:
        lines.append("## 📋 巡检明细")
        lines.append("")
        for item in items[:100]:
            lines.extend(_render_category_item(item))

    # ── 4. Ledger (for periodic reports) ──
    if ledger:
        lines.extend(["## 巡检台账", "", "| 时间 | 范围 | 对象 | 状态 | 评分 | 巡检项 | 高/中/低 | 未闭环 |", "|---|---|---|---|---|---|---|---|"])
        for row in ledger[:500]:
            if not row:
                continue
            lines.append(f"| {_table_value(row.get('inspection_time'))} | {_table_value(row.get('scope_type'))} | {_table_value(row.get('target'))} | {_table_value(row.get('status'))} | {_table_value(row.get('score'))} | {_table_value(row.get('item_count'))} | {_table_value(str(row.get('high_count', 0)) + '/' + str(row.get('medium_count', 0)) + '/' + str(row.get('low_count', 0)))} | {_table_value(row.get('open_issue_count'))} |")
        lines.append("")

    # ── 5. Merged runs table ──
    if runs:
        lines.extend(["## 合并巡检记录", "", "| 巡检ID | 范围 | 对象 | 状态 | 评分 | 高/中/低 |", "|---|---|---|---|---|---|"])
        for r in runs[:200]:
            target = r.get("project_id") or r.get("server_id") or "-"
            lines.append(f"| {_table_value(r.get('id'))} | {_table_value(r.get('scope_type'))} | {_table_value(target)} | {_table_value(r.get('status'))} | {_table_value(r.get('score'))} | {_table_value(str(r.get('high_count', 0)) + '/' + str(r.get('medium_count', 0)) + '/' + str(r.get('low_count', 0)))} |")
        lines.append("")

    # ── 6. Category summary ──
    if category_summary:
        lines.extend(["## 分类汇总", "", "| 分类 | 总数 | 通过 | 风险 | 错误 | 高/中/低 |", "|---|---:|---:|---:|---:|---|"])
        for c in category_summary[:100]:
            lines.append(f"| {_table_value(c.get('category'))} | {_table_value(c.get('total'))} | {_table_value(c.get('pass'))} | {_table_value(c.get('risk'))} | {_table_value(c.get('error'))} | {_table_value(str(c.get('high',0)) + '/' + str(c.get('medium',0)) + '/' + str(c.get('low',0)))} |")
        lines.append("")

    # ── 7. Issue status ──
    if issue_status:
        lines.extend(["## 整改闭环统计", "", "| 状态 | 数量 |", "|---|---:|"])
        for key in ["OPEN", "PROCESSING", "FIXED", "VERIFIED", "IGNORED"]:
            lines.append(f"| {key} | {_table_value(issue_status.get(key, 0))} |")
        lines.append("")

    # ── 8. Issues table ──
    lines.extend(["## 风险问题", "", "| 等级 | 标题 | 对象 | 状态 | 描述 | 建议 |", "|---|---|---|---|---|---|"])
    if issues:
        for issue in issues[:500]:
            target = issue.get("project_id") or issue.get("server_id") or "-"
            lines.append(f"| {_table_value(issue.get('risk_level'))} | {_table_value(issue.get('title'))} | {_table_value(target)} | {_table_value(issue.get('status'))} | {_table_value(issue.get('description'))} | {_table_value(issue.get('suggestion'))} |")
    else:
        lines.append("| - | - | - | - | 暂无风险问题。 | - |")
    lines.extend(["", "## 处置要求", "", "- 高危风险：立即处理并复查验证。", "- 中危风险：24 小时内整改。", "- 低危风险：一周内整改或纳入例行清理。", "- 所有问题必须保留证据、处理人、处理状态和复查结果。", ""])
    lines.extend(["## 证据说明", "", "巡检证据已脱敏入库，可在巡检记录详情中按 evidence_id 查看。", ""])

    return "\n".join(lines)


def _html_escape(text: str) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _inspection_html(title: str, payload: Dict[str, Any]) -> str:
    """Generate a self-contained HTML inspection report with inline CSS."""
    data = payload.get("data") or {}
    run = data.get("run") or {}
    items = data.get("items") or []
    issues = data.get("issues") or []
    ledger = data.get("ledger")
    if isinstance(ledger, dict):
        ledger = [ledger]
    elif not isinstance(ledger, list):
        ledger = []
    category_summary = data.get("category_summary") or []
    issue_status = data.get("issue_status") or {}
    summary = payload.get("summary") or {}
    runs = data.get("runs") or []

    server_ids = sorted(set(
        item.get("server_id") or run.get("server_id") or ""
        for item in items
        if item.get("server_id") or run.get("server_id")
    ))
    high = run.get("high_count", summary.get("high_count", 0))
    medium = run.get("medium_count", summary.get("medium_count", 0))
    low = run.get("low_count", summary.get("low_count", 0))
    score = run.get("score") if run.get("score") is not None else summary.get("score", summary.get("avg_score", "-"))
    open_issues = summary.get("open_issue_count", issue_status.get("OPEN", 0) + issue_status.get("PROCESSING", 0))

    # ── Build HTML ──
    h = []  # html lines

    h.append("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>""" + _html_escape(title) + """</title>
<style>
:root{--bg:#f8f9fa;--card:#fff;--border:#e2e8f0;--text:#1a202c;--muted:#718096;--high:#e53e3e;--medium:#dd6b20;--low:#38a169;--none:#a0aec0;--accent:#3182ce;--high-bg:#fff5f5;--medium-bg:#fffaf0;--low-bg:#f0fff4}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans SC",sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:20px;max-width:1200px;margin:0 auto}
h1{font-size:1.75rem;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid var(--border)}
h2{font-size:1.35rem;margin:24px 0 12px;color:var(--text)}
h3{font-size:1.1rem;margin:16px 0 8px}
h4{font-size:1rem;margin:12px 0 6px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px 20px;margin-bottom:16px}
.overview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.overview-item{padding:8px 12px;border-radius:6px;background:var(--bg)}
.overview-item .label{font-size:.8rem;color:var(--muted)}
.overview-item .value{font-size:1.1rem;font-weight:700}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.75rem;font-weight:600;color:#fff}
.badge-high{background:var(--high)}.badge-medium{background:var(--medium)}.badge-low{background:var(--low)}.badge-none{background:var(--none)}
table{width:100%;border-collapse:collapse;font-size:.875rem;margin:8px 0}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--border)}
th{background:var(--bg);font-weight:600;white-space:nowrap}
td{vertical-align:top}
.risk-high{color:var(--high);font-weight:700}
.risk-medium{color:var(--medium);font-weight:700}
.risk-low{color:var(--low);font-weight:700}
details{margin:8px 0;border:1px solid var(--border);border-radius:6px;overflow:hidden}
summary{padding:10px 16px;cursor:pointer;font-weight:600;background:var(--card);user-select:none}
summary:hover{background:var(--bg)}
details[open]>summary{border-bottom:1px solid var(--border)}
.detail-body{padding:12px 16px}
.section-item{margin:12px 0;padding:12px;border-radius:6px;border-left:4px solid var(--none)}
.section-item.high{border-left-color:var(--high);background:var(--high-bg)}
.section-item.medium{border-left-color:var(--medium);background:var(--medium-bg)}
.section-item.low{border-left-color:var(--low);background:var(--low-bg)}
.section-item.none{border-left-color:var(--none)}
.judgment{font-weight:600;margin-bottom:6px}
.suggestion{color:var(--muted);font-size:.85rem;margin-top:6px}
.criteria{margin-top:8px;background:var(--bg);border-radius:4px;padding:4px 8px;font-size:.8rem}
.criteria summary{cursor:pointer;font-weight:600;color:var(--muted);padding:2px 0}
.criteria-line{margin:2px 0;color:var(--text)}
code{background:var(--bg);padding:1px 4px;border-radius:3px;font-size:.85em}
pre{background:#2d3748;color:#e2e8f0;padding:12px;border-radius:6px;overflow-x:auto;font-size:.8rem;margin:8px 0}
.footer{margin-top:24px;padding-top:12px;border-top:1px solid var(--border);color:var(--muted);font-size:.8rem}
</style>
</head>
<body>
<h1>""" + _html_escape(title) + """</h1>""")

    # ── Overview card ──
    h.append('<div class="card"><h2>📊 概览</h2><div class="overview-grid">')
    h.append(f'<div class="overview-item"><div class="label">生成时间</div><div class="value">{_html_escape(payload.get("generated_at") or "-")}</div></div>')
    scope = run.get("scope_type") or summary.get("scope_type") or "-"
    h.append(f'<div class="overview-item"><div class="label">巡检范围</div><div class="value">{_html_escape(scope)}</div></div>')
    if server_ids:
        h.append(f'<div class="overview-item"><div class="label">服务器</div><div class="value">{len(server_ids)} 台</div></div>')
    else:
        srv = run.get("server_id") or summary.get("server_id") or "-"
        h.append(f'<div class="overview-item"><div class="label">服务器</div><div class="value">{_html_escape(str(srv))}</div></div>')
    h.append(f'<div class="overview-item"><div class="label">评分</div><div class="value">{score}/100</div></div>')
    h.append(f'<div class="overview-item"><div class="label">风险</div><div class="value"><span class="badge badge-high">{high} 高</span> <span class="badge badge-medium">{medium} 中</span> <span class="badge badge-low">{low} 低</span></div></div>')
    h.append(f'<div class="overview-item"><div class="label">未闭环</div><div class="value">{open_issues} 项</div></div>')
    h.append('</div></div>')

    # ── Risk Summary ──
    risk_items = [i for i in items if i.get("risk_level") in ("HIGH", "MEDIUM", "LOW")]
    if risk_items:
        risk_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        risk_items.sort(key=lambda x: (risk_order.get(x.get("risk_level", "NONE"), 9), x.get("server_id", "")))
        h.append('<div class="card"><h2>🚨 风险摘要</h2>')
        for level, label, badge_cls in [("HIGH", "高危", "badge-high"), ("MEDIUM", "中危", "badge-medium"), ("LOW", "低危", "badge-low")]:
            level_items = [i for i in risk_items if i.get("risk_level") == level]
            if not level_items:
                continue
            h.append(f'<h3><span class="badge {badge_cls}">{label}</span> {len(level_items)} 项</h3>')
            h.append('<table><thead><tr><th>服务器</th><th>巡检项</th><th>关键事实</th><th>建议</th></tr></thead><tbody>')
            for i in level_items[:50]:
                srv = _html_escape(i.get("server_id") or "-")
                cat_name = {"LOGIN_SECURITY": "登录安全", "ACCOUNT_SECURITY": "账号安全", "PROCESS_PORT": "进程端口", "DISK": "磁盘空间", "BACKUP": "备份任务", "COMMAND_HISTORY": "命令日志", "FIREWALL": "防火墙", "SERVICE_STATUS": "服务状态", "MEMORY": "内存状况"}.get(i.get("category"), i.get("category", "-"))
                msg = _html_escape((i.get("message") or "-")[:80])
                sug = _html_escape((i.get("suggestion") or "-")[:60])
                h.append(f'<tr><td>{srv}</td><td>{_html_escape(cat_name)}</td><td>{msg}</td><td>{sug}</td></tr>')
            h.append('</tbody></table>')
        h.append('</div>')

    # ── Per-server detail ──
    if server_ids and len(server_ids) > 1:
        h.append('<h2>📦 按服务器展开</h2>')
        for srv in server_ids:
            srv_items = [i for i in items if i.get("server_id") == srv]
            if not srv_items:
                continue
            srv_high = sum(1 for i in srv_items if i.get("risk_level") == "HIGH")
            srv_medium = sum(1 for i in srv_items if i.get("risk_level") == "MEDIUM")
            srv_low = sum(1 for i in srv_items if i.get("risk_level") == "LOW")
            has_high = srv_high > 0
            risk_parts = []
            if srv_high: risk_parts.append(f'{srv_high}高')
            if srv_medium: risk_parts.append(f'{srv_medium}中')
            if srv_low: risk_parts.append(f'{srv_low}低')
            risk_str = "/".join(risk_parts) if risk_parts else "全通过"
            open_attr = " open" if has_high else ""
            h.append(f'<details{open_attr}><summary>{_html_escape(srv)} · {risk_str}</summary><div class="detail-body">')
            for item in srv_items:
                h.append(_html_category_item(item))
            h.append('</div></details>')
    else:
        h.append('<div class="card"><h2>📋 巡检明细</h2>')
        for item in items[:100]:
            h.append(_html_category_item(item))
        h.append('</div>')

    # ── Issues table ──
    h.append('<div class="card"><h2>风险问题</h2><table><thead><tr><th>等级</th><th>标题</th><th>对象</th><th>状态</th><th>建议</th></tr></thead><tbody>')
    if issues:
        for issue in issues[:500]:
            target = _html_escape(issue.get("project_id") or issue.get("server_id") or "-")
            rl = issue.get("risk_level") or "-"
            rl_cls = {"HIGH": "risk-high", "MEDIUM": "risk-medium", "LOW": "risk-low"}.get(rl, "")
            h.append(f'<tr><td class="{rl_cls}">{_html_escape(rl)}</td><td>{_html_escape(issue.get("title") or "-")}</td><td>{target}</td><td>{_html_escape(issue.get("status") or "-")}</td><td>{_html_escape((issue.get("suggestion") or "-")[:60])}</td></tr>')
    else:
        h.append('<tr><td colspan="5">暂无风险问题</td></tr>')
    h.append('</tbody></table></div>')

    # ── Footer ──
    h.append(f'<div class="footer">巡检证据已脱敏入库，可在巡检记录详情中按 evidence_id 查看。· 生成时间：{_html_escape(payload.get("generated_at") or "-")}</div>')
    h.append('</body></html>')
    return "\n".join(h)


def _html_history(facts: Dict[str, Any]) -> str:
    hit = facts.get("hit_keywords") or []
    if not hit:
        return ""
    parts = [f'<p>命中危险特征（{len(hit)} 个）：</p><ul>']
    for kw in hit[:10]:
        parts.append(f'<li><code>{_html_escape(kw)}</code></li>')
    parts.append('</ul>')
    return "\n".join(parts)


def _html_firewall(facts: Dict[str, Any]) -> str:
    global_pass = facts.get("global_pass_lines") or []
    default_accept = facts.get("default_policy_accept", False)
    parts = []
    if global_pass:
        parts.append(f'<p>全局放行规则（{len(global_pass)} 条）：</p><ul>')
        for r in global_pass[:5]:
            parts.append(f'<li><code>{_html_escape(r[:120])}</code></li>')
        parts.append('</ul>')
    if default_accept:
        parts.append('<p class="risk-medium">INPUT 链默认策略：ACCEPT（存在风险）</p>')
    return "\n".join(parts)


def _html_service(facts: Dict[str, Any]) -> str:
    inactive = facts.get("inactive_core_services") or []
    failed = facts.get("failed_units") or []
    parts = []
    if inactive:
        parts.append(f'<p class="risk-high">未运行的核心服务（{len(inactive)} 个）：{_html_escape(", ".join(inactive))}</p>')
    if failed:
        parts.append(f'<p>失败单元（{len(failed)} 个）：</p><ul>')
        for u in failed[:10]:
            parts.append(f'<li><code>{_html_escape(u)}</code></li>')
        parts.append('</ul>')
    return "\n".join(parts)


def _html_memory(facts: Dict[str, Any]) -> str:
    mem_pct = facts.get("mem_pct", 0)
    swap_pct = facts.get("swap_pct", 0)
    mem_total = _html_escape(facts.get("mem_total") or "-")
    swap_total = _html_escape(facts.get("swap_total") or "-")
    parts = ['<table><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>']
    mem_cls = "risk-high" if mem_pct >= 90 else ("risk-medium" if mem_pct >= 80 else "")
    parts.append(f'<tr><td>内存使用率</td><td class="{mem_cls}">{mem_pct}% (总量 {mem_total})</td></tr>')
    if swap_pct > 0:
        swap_cls = "risk-high" if swap_pct >= 50 else ""
        parts.append(f'<tr><td>Swap 使用率</td><td class="{swap_cls}">{swap_pct}% (总量 {swap_total})</td></tr>')
    parts.append('</tbody></table>')
    return "\n".join(parts)


def _html_category_item(item: Dict[str, Any]) -> str:
    """Render a single inspection item as HTML with structured facts."""
    category = item.get("category", "")
    risk_level = item.get("risk_level", "NONE")
    message = item.get("message", "")
    suggestion = item.get("suggestion", "")
    facts = item.get("parsed_facts")
    cat_name = {"LOGIN_SECURITY": "登录安全", "ACCOUNT_SECURITY": "账号安全", "PROCESS_PORT": "进程端口", "DISK": "磁盘空间", "BACKUP": "备份任务", "COMMAND_HISTORY": "命令日志", "FIREWALL": "防火墙", "SERVICE_STATUS": "服务状态", "MEMORY": "内存状况"}.get(category, category)
    rl_cls = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(risk_level, "none")
    badge_cls = {"HIGH": "badge-high", "MEDIUM": "badge-medium", "LOW": "badge-low", "NONE": "badge-none"}.get(risk_level, "badge-none")

    parts = [f'<div class="section-item {rl_cls}">']
    parts.append(f'<h4><span class="badge {badge_cls}">{_html_escape(risk_level)}</span> {_html_escape(cat_name)}</h4>')
    parts.append(f'<div class="judgment">{_html_escape(message)}</div>')

    if facts and isinstance(facts, dict):
        # 量化摘要
        summary = facts.get("summary")
        if summary:
            parts.append(f'<div class="criteria" style="margin-bottom:6px"><span class="criteria-line">📊 量化指标: {_html_escape(summary)}</span></div>')

        renderer = {
            "ACCOUNT_SECURITY": _html_account,
            "DISK": _html_disk,
            "LOGIN_SECURITY": _html_login,
            "PROCESS_PORT": _html_process_port,
            "BACKUP": _html_backup,
            "COMMAND_HISTORY": _html_history,
            "FIREWALL": _html_firewall,
            "SERVICE_STATUS": _html_service,
            "MEMORY": _html_memory,
        }.get(category)
        if renderer:
            parts.append(renderer(facts))

    parts.append(f'<div class="suggestion">💡 {_html_escape(suggestion)}</div>')

    # 评分依据
    if facts and isinstance(facts, dict):
        criteria = facts.get("criteria")
        thresholds = facts.get("thresholds")
        if criteria or thresholds:
            t_parts = []
            if thresholds:
                for k, v in thresholds.items():
                    if isinstance(v, list):
                        v = ", ".join(str(x) for x in v)
                    t_parts.append(f"{_html_escape(k)}={_html_escape(str(v))}")
            criteria_html = f'<div class="criteria-line">📐 判定标准: {_html_escape(criteria)}</div>' if criteria else ''
            thresholds_html = f'<div class="criteria-line">阈值: {_html_escape("; ".join(t_parts))}</div>' if t_parts else ''
            risk_html = f'<div class="criteria-line">实际等级: <span class="risk-{_html_escape(rl_cls)}">{_html_escape(risk_level)}</span></div>'
            parts.append(f'<details class="criteria"><summary>📐 评分依据</summary>{criteria_html}{thresholds_html}{risk_html}</details>')

    parts.append('</div>')
    return "\n".join(parts)


def _html_account(facts: Dict[str, Any]) -> str:
    uid0 = facts.get("uid0_accounts") or []
    login_users = facts.get("login_users") or []
    login_count = facts.get("login_user_count", len(login_users))
    parts = []
    if uid0:
        parts.append('<table><thead><tr><th>账号</th><th>UID</th><th>Shell</th><th>Home</th></tr></thead><tbody>')
        for a in uid0[:20]:
            parts.append(f'<tr><td>{_html_escape(a.get("user"))}</td><td>{_html_escape(str(a.get("uid")))}</td><td>{_html_escape(a.get("shell"))}</td><td>{_html_escape(a.get("home"))}</td></tr>')
        parts.append('</tbody></table>')
    if login_users:
        parts.append(f'<p>可登录用户（{login_count} 个）：</p>')
        parts.append('<table><thead><tr><th>账号</th><th>Shell</th></tr></thead><tbody>')
        for u in login_users[:20]:
            parts.append(f'<tr><td>{_html_escape(u.get("user"))}</td><td>{_html_escape(u.get("shell"))}</td></tr>')
        parts.append('</tbody></table>')
    return "\n".join(parts)


def _html_disk(facts: Dict[str, Any]) -> str:
    filesystems = facts.get("filesystems") or []
    if not filesystems:
        return ""
    parts = ['<table><thead><tr><th>挂载点</th><th>类型</th><th>总量</th><th>已用</th><th>可用</th><th>使用率</th></tr></thead><tbody>']
    for fs in filesystems[:30]:
        pct = fs.get("pct", 0)
        pct_str = f'<span class="risk-high">{pct}%</span>' if pct >= 90 else f'{pct}%'
        parts.append(f'<tr><td>{_html_escape(fs.get("mount"))}</td><td>{_html_escape(fs.get("type"))}</td><td>{_html_escape(fs.get("total"))}</td><td>{_html_escape(fs.get("used"))}</td><td>{_html_escape(fs.get("avail"))}</td><td>{pct_str}</td></tr>')
    parts.append('</tbody></table>')
    return "\n".join(parts)


def _html_login(facts: Dict[str, Any]) -> str:
    success = facts.get("success_logins") or []
    attackers = facts.get("top_attackers") or []
    failed_total = facts.get("failed_total", 0)
    parts = []
    root_logins = [l for l in success if l.get("user") == "root"]
    if root_logins:
        parts.append('<p>root 登录（最近 5 次）：</p>')
        parts.append('<table><thead><tr><th>时间</th><th>来源 IP</th><th>终端</th><th>时长</th></tr></thead><tbody>')
        for l in root_logins[:5]:
            parts.append(f'<tr><td>{_html_escape(l.get("start"))}</td><td>{_html_escape(l.get("ip"))}</td><td>{_html_escape(l.get("tty"))}</td><td>{_html_escape(l.get("duration"))}</td></tr>')
        parts.append('</tbody></table>')
    elif success:
        parts.append('<table><thead><tr><th>用户</th><th>IP</th><th>终端</th><th>时间</th><th>时长</th></tr></thead><tbody>')
        for l in success[:5]:
            parts.append(f'<tr><td>{_html_escape(l.get("user"))}</td><td>{_html_escape(l.get("ip"))}</td><td>{_html_escape(l.get("tty"))}</td><td>{_html_escape(l.get("start"))}</td><td>{_html_escape(l.get("duration"))}</td></tr>')
        parts.append('</tbody></table>')
    if attackers:
        parts.append(f'<p>失败登录 Top 攻击源（总计 {failed_total} 次）：</p>')
        parts.append('<table><thead><tr><th>攻击 IP</th><th>失败次数</th><th>主要尝试账号</th></tr></thead><tbody>')
        for a in attackers[:10]:
            users = ", ".join((a.get("attempted_users") or [])[:5])
            parts.append(f'<tr><td>{_html_escape(a.get("ip"))}</td><td>{_html_escape(str(a.get("count")))}</td><td>{_html_escape(users)}</td></tr>')
        parts.append('</tbody></table>')
    return "\n".join(parts)


def _html_process_port(facts: Dict[str, Any]) -> str:
    high_risk = facts.get("high_risk_ports") or []
    top_cpu = facts.get("top_cpu_procs") or []
    high_cpu = facts.get("high_cpu_procs") or []
    listening_count = facts.get("listening_count", 0)
    parts = []
    if high_risk:
        parts.append('<table><thead><tr><th>端口</th><th>协议</th><th>进程</th><th>绑定地址</th><th>暴露面</th></tr></thead><tbody>')
        for p in high_risk[:20]:
            exposed = "🌐 公网" if p.get("is_world") else "本机"
            parts.append(f'<tr><td>{p.get("port", "-")}</td><td>{_html_escape(p.get("proto"))}</td><td>{_html_escape(p.get("service"))}</td><td>{_html_escape(p.get("bind"))}</td><td>{exposed}</td></tr>')
        parts.append('</tbody></table>')
    if high_cpu:
        parts.append('<p class="risk-medium">高 CPU 占用进程：</p>')
        parts.append('<table><thead><tr><th>PID</th><th>用户</th><th>%CPU</th><th>%MEM</th><th>命令</th></tr></thead><tbody>')
        for p in high_cpu[:5]:
            parts.append(f'<tr><td>{_html_escape(str(p.get("pid")))}</td><td>{_html_escape(p.get("user"))}</td><td class="risk-medium">{_html_escape(str(p.get("cpu")))}</td><td>{_html_escape(str(p.get("mem")))}</td><td>{_html_escape(p.get("cmd"))}</td></tr>')
        parts.append('</tbody></table>')
    if top_cpu and not high_cpu:
        parts.append('<p>Top CPU 进程：</p>')
        parts.append('<table><thead><tr><th>PID</th><th>用户</th><th>%CPU</th><th>%MEM</th><th>命令</th></tr></thead><tbody>')
        for p in top_cpu[:10]:
            parts.append(f'<tr><td>{_html_escape(str(p.get("pid")))}</td><td>{_html_escape(p.get("user"))}</td><td>{_html_escape(str(p.get("cpu")))}</td><td>{_html_escape(str(p.get("mem")))}</td><td>{_html_escape(p.get("cmd"))}</td></tr>')
        parts.append('</tbody></table>')
    if listening_count:
        parts.append(f'<p>监听端口总数：{listening_count}</p>')
    return "\n".join(parts)


def _html_backup(facts: Dict[str, Any]) -> str:
    backup_dirs = facts.get("backup_dirs") or []
    cron_lines = facts.get("cron_lines") or []
    zero_byte_files = facts.get("zero_byte_files") or []
    parts = []
    if backup_dirs:
        parts.append('<p>已检查路径：</p><ul>')
        for d in backup_dirs[:10]:
            exists = "存在" if d.get("exists") else "不存在"
            file_count = len(d.get("files") or [])
            parts.append(f'<li><code>{_html_escape(d.get("path"))}</code> — {exists}，{file_count} 个文件')
            for f in (d.get("files") or [])[:5]:
                parts.append(f'<br><small>{_html_escape(f.get("name"))} ({f.get("size", 0)} bytes)</small>')
            parts.append('</li>')
        parts.append('</ul>')
    else:
        parts.append('<p>已检查路径：/backup、/data/backups、/var/backups（均未发现备份文件）</p>')
    if zero_byte_files:
        parts.append(f'<p class="risk-medium">0 字节文件（{len(zero_byte_files)} 个，可能损坏）：</p><ul>')
        for f in zero_byte_files[:10]:
            parts.append(f'<li><code>{_html_escape(f)}</code></li>')
        parts.append('</ul>')
    if cron_lines:
        parts.append('<p>cron 备份任务：</p><pre>')
        for c in cron_lines[:10]:
            parts.append(_html_escape(c))
        parts.append('</pre>')
    else:
        parts.append('<p>cron 备份任务：无</p>')
    return "\n".join(parts)


def _generic_markdown(title: str, payload: Dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    metadata = payload.get("metadata") or {}
    lines = [f"# {title}", "", f"- 生成时间：{payload.get('generated_at') or '-'}", f"- 报告类型：{payload.get('report_type') or '-'}", f"- 目标：{payload.get('target_id') or '-'}", f"- Schema：{payload.get('schema_version') or SCHEMA_VERSION}", ""]
    if isinstance(summary, dict) and summary:
        lines.extend(["## 摘要", "", "| 字段 | 值 |", "|---|---|"])
        for key, value in summary.items():
            lines.append(f"| {_table_value(key)} | {_table_value(value)} |")
        lines.append("")
    if isinstance(metadata, dict) and metadata:
        lines.extend(["## 元数据", "", "| 字段 | 值 |", "|---|---|"])
        for key, value in metadata.items():
            lines.append(f"| {_table_value(key)} | {_table_value(value)} |")
        lines.append("")
    data = payload.get("data")
    if isinstance(data, dict):
        if payload.get("report_type") == "operation_chain":
            timeline = data.get("timeline") or []
            lines.extend(["## 操作链路时间线", ""])
            for event in timeline[:300]:
                lines.append(f"- `{_table_value(event.get('time'))}` **{_table_value(event.get('title'))}** · { _table_value(event.get('status')) } · { _table_value(event.get('risk_level')) }")
                if event.get("detail"):
                    lines.append(f"  - { _table_value(event.get('detail')) }")
            lines.append("")
        elif payload.get("report_type") == "diagnostics":
            rec_raw = data.get("recommendations")
            if isinstance(rec_raw, dict):
                recommendations = rec_raw.get("items") or []
            elif isinstance(rec_raw, list):
                recommendations = rec_raw
            else:
                recommendations = []
            if isinstance(recommendations, list):
                lines.extend(["## 问题建议", ""])
                for item in recommendations[:50]:
                    if isinstance(item, dict):
                        lines.append(f"- **{_table_value(item.get('title') or item.get('message'))}**：{_table_value(item.get('description') or item.get('reason') or '')}")
                    else:
                        lines.append(f"- {_table_value(item)}")
                lines.append("")
        elif payload.get("report_type") == "deployment":
            lines.append("## 发布概览")
            lines.append("")
            for key in ["system", "service", "environment", "status", "version", "summary_text"]:
                lines.append(f"- {key}: {_table_value(data.get(key))}")
            lines.append("")
    lines.extend(["## 原始 JSON 摘要", "", "```json", json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)[:20000], "```", ""])
    return "\n".join(lines)


def _payload_for_report(db: Session, report_type: str, target_id: str = "", include_raw: bool = False, focus: str = "") -> Dict[str, Any]:
    if report_type == "diagnostics":
        from app.services.diagnostics import build_diagnostics_report
        data = build_diagnostics_report(db)
        summary = data.get("summary") or data.get("health") or {}
        return {"data": data, "summary": summary, "metadata": {"focus": focus or "general"}}
    if report_type == "ai_diagnostics":
        from app.services.ai_diagnostics import build_ai_diagnostic_analysis
        data = build_ai_diagnostic_analysis(db, mode="full" if include_raw else "summary", focus=focus or "general", include_report=include_raw)
        summary = data.get("summary") or {"status": data.get("status"), "highest_risk": data.get("highest_risk"), "finding_count": len(data.get("findings") or [])}
        return {"data": data, "summary": summary, "metadata": {"focus": focus or "general"}}
    if report_type == "operation_chain":
        if not target_id:
            raise HTTPException(status_code=400, detail="target_id is required for operation_chain reports")
        from app.services.audit_chain import build_operation_chain
        data = build_operation_chain(db, chain_id=target_id, include_raw=include_raw)
        summary = data.get("summary") or {}
        if not data.get("chain_id") and not summary:
            raise HTTPException(status_code=404, detail="Operation chain not found")
        return {"data": data, "summary": summary, "metadata": {"chain_id": data.get("chain_id") or target_id}}
    if report_type == "operation_chains_index":
        from app.services.audit_chain import list_operation_chains
        data = list_operation_chains(db, limit=200)
        items = data.get("items") or []
        summary = {"total": len(items), "generated_from": "operation_chains"}
        return {"data": data, "summary": summary, "metadata": {}}
    if report_type == "deployment":
        if not target_id:
            raise HTTPException(status_code=400, detail="target_id is required for deployment reports")
        from app.deploy.report import deployment_report_payload
        data = deployment_report_payload(target_id, db)
        summary = data.get("summary") or {"deployment_id": target_id, "status": data.get("status")}
        return {"data": data, "summary": summary, "metadata": {"deployment_id": target_id}}
    if report_type == "inspection":
        if not target_id:
            raise HTTPException(status_code=400, detail="target_id is required for inspection reports")
        from app.services.inspection_center import report_payload
        payload = report_payload(db, target_id)
        return payload
    raise HTTPException(status_code=400, detail=f"Unsupported report_type: {report_type}")


def _format_summary(report_type: str, summary: Dict[str, Any]) -> str:
    if report_type == "operation_chain":
        return f"链路 {summary.get('chain_id') or ''} 状态 {summary.get('status') or '-'}，最高风险 {summary.get('risk_level') or '-'}。".strip()
    if report_type == "diagnostics":
        return f"系统诊断报告，状态 {summary.get('status') or summary.get('overall_status') or '-'}。"
    if report_type == "deployment":
        return f"发布 {summary.get('system') or '-'} / {summary.get('service') or '-'} 状态 {summary.get('status') or '-'}。"
    if report_type == "inspection":
        return f"{summary.get('report_title') or '巡检报告'}：记录 {summary.get('run_count') or 1} 条，评分 {summary.get('score') or summary.get('avg_score') or 0}，高危 {summary.get('high_count') or 0}，中危 {summary.get('medium_count') or 0}，低危 {summary.get('low_count') or 0}，未闭环 {summary.get('open_issue_count') or 0}。"
    if report_type == "db_query_export":
        return f"数据库查询导出，格式 {summary.get('format') or '-'}，行数 {summary.get('row_count') or 0}。"
    if report_type == "ai_diagnostics":
        return f"AI 诊断分析，最高风险 {summary.get('highest_risk') or '-'}，发现 {summary.get('finding_count') or 0} 项。"
    if report_type == "ai_analysis":
        return f"AI 分析报告，置信度 {summary.get('confidence') or '-'}：{summary.get('summary') or '-'}"
    return "报告已生成。"


def report_to_dict(row: ReportArtifact) -> Dict[str, Any]:
    return {
        "id": row.id,
        "report_type": row.report_type,
        "title": row.title,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "status": row.status,
        "format": row.format,
        "file_path": row.file_path,
        "size_bytes": row.size_bytes or 0,
        "sha256": row.sha256,
        "summary": row.summary,
        "metadata": row.metadata_json or {},
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "download_url": f"/api/v2/reports/{row.id}/download",
    }


def list_report_types() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "types": REPORT_TYPES}


def list_reports(db: Session, *, report_type: str = "", limit: int = 100, since_id: str = "", offset: int = 0) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    q = db.query(ReportArtifact)
    if report_type:
        q = q.filter(ReportArtifact.report_type == report_type)
    if since_id:
        q = q.filter(ReportArtifact.id < since_id)
    total = q.count()
    rows = q.order_by(ReportArtifact.created_at.desc()).offset(offset).limit(limit).all()
    return {"items": [report_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset, "types": REPORT_TYPES}


def report_summary(db: Session) -> Dict[str, Any]:
    rows = db.query(ReportArtifact).order_by(ReportArtifact.created_at.desc()).limit(200).all()
    by_type: Dict[str, int] = {}
    total_size = 0
    for row in rows:
        by_type[row.report_type] = by_type.get(row.report_type, 0) + 1
        total_size += int(row.size_bytes or 0)
    return {
        "total_recent": len(rows),
        "by_type": by_type,
        "total_size_bytes": total_size,
        "latest": report_to_dict(rows[0]) if rows else None,
        "supported_types": REPORT_TYPES,
    }


def get_report(db: Session, report_id: str) -> ReportArtifact:
    row = db.query(ReportArtifact).filter(ReportArtifact.id == report_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return row


def delete_reports(db: Session, report_ids: List[str], *, actor: str = "") -> Dict[str, Any]:
    ids: List[str] = []
    seen = set()
    for raw in report_ids or []:
        report_id = str(raw or "").strip()
        if report_id and report_id not in seen:
            seen.add(report_id)
            ids.append(report_id)
    if not ids:
        raise HTTPException(status_code=400, detail="report_ids is required")
    if len(ids) > 200:
        raise HTTPException(status_code=400, detail="Cannot delete more than 200 reports at once")

    rows = db.query(ReportArtifact).filter(ReportArtifact.id.in_(ids)).all()
    by_id = {row.id: row for row in rows}
    missing = [report_id for report_id in ids if report_id not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"Report not found: {', '.join(missing[:5])}")

    deleted_files = 0
    skipped_files: List[str] = []
    for report_id in ids:
        row = by_id[report_id]
        try:
            path = report_download_path(row)
            path.unlink(missing_ok=True)
            deleted_files += 1
        except HTTPException as exc:
            if exc.status_code not in {404}:
                skipped_files.append(report_id)
        except Exception:
            skipped_files.append(report_id)
        db.delete(row)
    db.commit()
    return {
        "report_ids": ids,
        "deleted": len(ids),
        "deleted_files": deleted_files,
        "skipped_files": skipped_files,
        "actor": actor or "",
    }



def generate_report_from_payload(
    db: Session,
    payload: Dict[str, Any],
    *,
    report_type: str,
    target_id: str = "",
    fmt: str = "json",
    title: str = "",
    created_by: str = "",
) -> Dict[str, Any]:
    """Persist an already-built payload as a Report Center artifact."""
    report_type = (report_type or "").strip()
    if report_type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported report_type: {report_type}")
    fmt = (fmt or "json").lower().strip()
    if fmt not in REPORT_TYPES[report_type]["formats"]:
        raise HTTPException(status_code=400, detail=f"Unsupported format for {report_type}: {fmt}")
    generated_at = _now_dt()
    default = REPORT_TYPES[report_type]
    title = title or default["title"]
    payload = dict(payload or {})
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("report_type", report_type)
    payload.setdefault("title", title)
    payload.setdefault("target_type", default["target_type"])
    payload.setdefault("target_id", target_id or "local")
    payload.setdefault("generated_at", generated_at.isoformat())
    payload.setdefault("generated_by", created_by or "system")
    report_id = uuid4().hex
    basename = _safe_filename(f"{report_type}_{target_id or 'local'}_{generated_at.strftime('%Y%m%d_%H%M%S')}_{report_id[:8]}")
    path = _reports_dir() / f"{basename}.{fmt}"
    if fmt == "json":
        _write_json(path, payload)
    elif fmt == "html" and report_type == "inspection":
        path.write_text(_inspection_html(title, payload), encoding="utf-8")
    else:
        path.write_text(_inspection_markdown(title, payload) if report_type == "inspection" else _generic_markdown(title, payload), encoding="utf-8")
    row = ReportArtifact(
        id=report_id,
        report_type=report_type,

        title=title,
        target_type=default["target_type"],
        target_id=target_id or "local",
        status="ready",
        format=fmt,
        file_path=str(path),
        size_bytes=path.stat().st_size,
        sha256=_sha256_file(path),
        summary=_format_summary(report_type, payload.get("summary") or {}),
        metadata_json={"schema_version": payload.get("schema_version"), **(payload.get("metadata") or {})},
        created_by=created_by or "system",
        created_at=generated_at,
        updated_at=generated_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"report": report_to_dict(row), "payload_preview": {"summary": payload.get("summary"), "metadata": payload.get("metadata")}}


def generate_report(
    db: Session,
    *,
    report_type: str,
    target_id: str = "",
    fmt: str = "json",
    title: str = "",
    created_by: str = "",
    include_raw: bool = False,
    focus: str = "",
) -> Dict[str, Any]:
    report_type = (report_type or "").strip()
    if report_type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported report_type: {report_type}")
    if REPORT_TYPES.get(report_type, {}).get("can_generate") is False:
        raise HTTPException(status_code=400, detail=f"Report type {report_type} is generated by its dedicated workflow")
    fmt = (fmt or "json").lower().strip()
    if fmt not in REPORT_TYPES[report_type]["formats"]:
        raise HTTPException(status_code=400, detail=f"Unsupported format for {report_type}: {fmt}")
    generated_at = _now_dt()
    content = _payload_for_report(db, report_type, target_id=target_id, include_raw=include_raw, focus=focus)
    default = REPORT_TYPES[report_type]
    title = title or default["title"]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "report_type": report_type,
        "title": title,
        "target_type": default["target_type"],
        "target_id": target_id or "local",
        "generated_at": generated_at.isoformat(),
        "generated_by": created_by or "system",
        "summary": content.get("summary") or {},
        "metadata": content.get("metadata") or {},
        "data": content.get("data"),
    }
    report_id = uuid4().hex
    basename = _safe_filename(f"{report_type}_{target_id or 'local'}_{generated_at.strftime('%Y%m%d_%H%M%S')}_{report_id[:8]}")
    path = _reports_dir() / f"{basename}.{fmt}"
    if fmt == "json":
        _write_json(path, payload)
    elif fmt == "html" and report_type == "inspection":
        path.write_text(_inspection_html(title, payload), encoding="utf-8")
    else:
        path.write_text(_inspection_markdown(title, payload) if report_type == "inspection" else _generic_markdown(title, payload), encoding="utf-8")
    size = path.stat().st_size
    sha = _sha256_file(path)
    row = ReportArtifact(
        id=report_id,
        report_type=report_type,
        title=title,
        target_type=default["target_type"],
        target_id=target_id or "local",
        status="ready",
        format=fmt,
        file_path=str(path),
        size_bytes=size,
        sha256=sha,
        summary=_format_summary(report_type, content.get("summary") or {}),
        metadata_json={"schema_version": SCHEMA_VERSION, "focus": focus or "", **(content.get("metadata") or {})},
        created_by=created_by or "system",
        created_at=generated_at,
        updated_at=generated_at,
    )
    db.add(row)
    db.add(NotificationEvent(
        event_type="report.created",
        target=report_id,
        status="success",
        message=f"{title} 已生成",
        payload={"report_type": report_type, "target_id": target_id or "local", "format": fmt, "sha256": sha},
        created_at=generated_at,
    ))
    db.commit()
    db.refresh(row)
    return {"report": report_to_dict(row), "payload_preview": {"summary": payload.get("summary"), "metadata": payload.get("metadata")}}


def report_download_path(row: ReportArtifact) -> Path:
    if not row.file_path:
        raise HTTPException(status_code=404, detail="Report file path is empty")
    path = Path(row.file_path).resolve()
    root = _reports_dir().resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid report path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Report file not found")
    return path

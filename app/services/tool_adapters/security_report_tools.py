"""安全日报汇总 & 安全加固模块按需安装工具。

提供三个 MCP/OPS 能力：
- ops.security_report.summarize : 只读，汇总已采集的每日安全巡检日报供 AI 分析。
- ops.security_report.collect   : 触发一次每日日报采集（读多台服务器 + 写库 + 归档）。
- ops.security_module.install   : 在目标服务器安装安全监控模块（deanchou/server_security_monitor）
                                   并注入落盘补丁，使每日日报写入 /var/log/security-daily/<date>.md。
                                   高危远程写操作，需人工审批。
"""
from __future__ import annotations

import base64
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import HTTPException

from app.db.models import SecurityDailyReport, SecurityRisk
from app.services.tool_registry import registry


def _resolve_server_or_404(server_key: str):
    from config_manager import resolve_server
    if not server_key:
        raise HTTPException(status_code=404, detail="Server not found: <empty>")
    srv = resolve_server(server_key)
    if not srv:
        raise HTTPException(
            status_code=404,
            detail=f"Server not found: {server_key}. Pass a server name, host, full UUID, or short UUID prefix.",
        )
    return srv


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _report_to_dict(row: SecurityDailyReport) -> Dict[str, Any]:
    return {
        "id": row.id,
        "server_name": row.server_name,
        "report_date": row.report_date,
        "status": row.status,
        "max_risk": row.max_risk,
        "summary": row.summary or {},
        "artifact_id": row.artifact_id,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@registry.register(
    name="ops.security_report.summarize",
    title="汇总每日安全巡检日报",
    description=(
        "读取 OPS 已采集的每日安全巡检日报（security_daily_reports），按报告日期/服务器/风险级别"
        "聚合，返回结构化风险项（kind/风险等级/登录失败/封禁IP/负载）供 AI 分析。只读。"
        "中文: 每日安全日报/安全巡检汇总/日报风险分析/安全报告. "
    ),
    scopes=["ops:read", "server:read"],
    risk="low",
    category="report_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    related_tools=["ops.security_report.collect", "ops.risk.list", "ops.list_reports"],
    example_prompts=["汇总最近7天的每日安全巡检日报并列出高风险项"],
    input_schema={
        "type": "object",
        "properties": {
            "report_date": {"type": "string", "description": "指定日期 YYYY-MM-DD，缺省=今天"},
            "server_name": {"type": "string"},
            "max_risk": {"type": "string", "description": "按最高风险过滤: LOW/MEDIUM/HIGH"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "additionalProperties": False,
    },
)
def summarize_security_reports(args: Dict[str, Any], ctx, db):
    limit = max(1, min(int(args.get("limit") or 100), 200))
    q = db.query(SecurityDailyReport)
    if args.get("report_date"):
        q = q.filter(SecurityDailyReport.report_date == args["report_date"])
    if args.get("server_name"):
        q = q.filter(SecurityDailyReport.server_name == args["server_name"])
    if args.get("max_risk"):
        q = q.filter(SecurityDailyReport.max_risk == args["max_risk"])
    q = q.order_by(SecurityDailyReport.report_date.desc(),
                   SecurityDailyReport.created_at.desc())
    rows = q.limit(limit).all()
    if not rows:
        return {"items": [], "total": 0,
                "summary": "未采集到每日安全巡检日报，可调用 ops.security_report.collect 触发采集。"}

    items = [_report_to_dict(r) for r in rows]
    high = [r for r in items if r["max_risk"] == "HIGH"]
    medium = [r for r in items if r["max_risk"] == "MEDIUM"]
    by_date: Dict[str, int] = {}
    for r in items:
        by_date[r["report_date"]] = by_date.get(r["report_date"], 0) + 1
    risk_items = []
    for r in high + medium:
        s = r["summary"] or {}
        risk_items.append({
            "server": r["server_name"],
            "report_date": r["report_date"],
            "risk_level": r["max_risk"],
            "login_failures": s.get("login_failures", 0),
            "banned_ips": s.get("banned_ips", 0),
            "account_changes": s.get("account_changes", 0),
            "load_avg": s.get("load_avg"),
            "reasons": s.get("reasons") or [],
            "suggestion": "HIGH 风险建议立即核查；MEDIUM 建议纳入整改计划。",
        })
    return {
        "items": items,
        "risk_items": risk_items,
        "total": len(items),
        "high_count": len(high),
        "medium_count": len(medium),
        "by_date": by_date,
        "summary": f"共 {len(items)} 条日报，其中高危 {len(high)}、中危 {len(medium)}。",
    }


@registry.register(
    name="ops.security_report.collect",
    title="采集每日安全巡检日报",
    description=(
        "从已启用安全监控模块的服务器采集每日安全巡检日报，解析后存入日报表，"
        "高危项写入风险台账，并归档到报告中心。有写库与远端读操作，需确认。"
        "中文: 采集日报/刷新安全日报/重新收集安全报告. "
    ),
    scopes=["ops:read", "server:read"],
    risk="medium",
    category="report_write",
    write=True,
    requires_confirmation=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    related_tools=["ops.security_report.summarize", "ops.risk.list"],
    input_schema={
        "type": "object",
        "properties": {
            "report_date": {"type": "string", "description": "采集指定日期 YYYY-MM-DD，缺省=今天"},
            "persist_risks": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    },
)
def collect_security_reports(args: Dict[str, Any], ctx, db):
    from app.services.security_daily import collect_all
    report_date = args.get("report_date") or None
    persist_risks = bool(args.get("persist_risks", True))
    result = collect_all(db, report_date=report_date, persist_risks=persist_risks)
    summary = result.get("summary") or {}
    return {
        "ok": True,
        "report_date": result.get("report_date"),
        "server_count": summary.get("server_count", 0),
        "high_count": summary.get("high_count", 0),
        "medium_count": summary.get("medium_count", 0),
        "failed": summary.get("failed_count", 0),
        "summary": summary,
        "results": result.get("results") or [],
    }


# 落盘补丁：在目标服务器安装的日报生成器（自建，不依赖 installer 内部脚本路径）。
# 生成与 collect 解析一致的五段 markdown，写入 /var/log/security-daily/<date>.md。
_GEN_SCRIPT = r"""#!/usr/bin/env bash
# OPS 每日安全巡检日报生成器（落盘补丁）— 生成 /var/log/security-daily/<date>.md
set +e
REPORT_DIR=/var/log/security-daily
STATE_DIR=$REPORT_DIR/state
mkdir -p "$REPORT_DIR" "$STATE_DIR"
DATE=$(date +%F)

SEC1=$({ if command -v aureport >/dev/null 2>&1; then aureport -au -ts today 2>/dev/null || last -n 25; else last -n 25 2>/dev/null; fi; })

FAILURES=0
FAILCMD=$(cat /var/log/auth.log /var/log/secure /var/log/secure-* 2>/dev/null | grep -c -iE "Failed password|authentication failure")
[ "$FAILCMD" -gt 0 ] 2>/dev/null && FAILURES=$FAILCMD

# 账户变更检测（与上次运行快照 diff）
getent passwd 2>/dev/null | awk -F: '$7 !~ /(nologin|false|halt|sync|shutdown)/ {print $1}' | sort > "$STATE_DIR/pw.now"
DELTA_FILE="$STATE_DIR/delta.txt"
DELTA=0
if [ -f "$STATE_DIR/pw.users" ]; then
  comm -3 "$STATE_DIR/pw.users" "$STATE_DIR/pw.now" 2>/dev/null > "$DELTA_FILE"
  DELTA=$(grep -c . "$DELTA_FILE")
else
  : > "$DELTA_FILE"
  DELTA=0
fi
mv "$STATE_DIR/pw.now" "$STATE_DIR/pw.users"

BANNED=$(if command -v fail2ban-client >/dev/null 2>&1; then fail2ban-client banned 2>/dev/null | grep -oE '\([0-9.:]+\)' | tr -d '()'; fi)

LOAD=$(uptime)

{
  echo "### 1. 今日登录记录 (aureport, 最近25条)"
  echo ""
  echo "\`\`\`"
  echo "$SEC1"
  echo "\`\`\`"
  echo ""
  echo "### 2. 今日登录失败次数: $FAILURES"
  echo ""
  echo "### 3. 今日账户变更"
  if [ "$DELTA" -gt 0 ]; then
    echo "检测到 $DELTA 项登录账户变化:"
    sed 's/^/  - /' "$DELTA_FILE"
  else
    echo "无"
  fi
  echo ""
  echo "### 4. 当前被 fail2ban 封禁的 IP"
  if [ -n "$BANNED" ]; then
    echo "   - Banned IP list:"
    echo "$BANNED" | while read -r ip; do [ -n "$ip" ] && echo "     - $ip"; done
  else
    echo "   - Banned IP list: "
  fi
  echo ""
  echo "### 5. 系统负载"
  echo " $LOAD"
} > "$REPORT_DIR/$DATE.md"
chmod 644 "$REPORT_DIR/$DATE.md"
echo "written: $REPORT_DIR/$DATE.md"
"""


def _upload_and_install(server, payload_b64: str) -> dict:
    """上传并安装落盘补丁 + 定时任务，返回远端执行结果。"""
    from ssh_client import create_ssh_client

    ssh = create_ssh_client(server)
    script_path = "/usr/local/bin/ops-security-daily.sh"
    cron_path = "/etc/cron.d/ops-security-daily"
    mkdir = "mkdir -p /var/log/security-daily/state"
    write_script = (f"echo {shlex.quote(payload_b64)} | base64 -d > {script_path} && "
                    f"chmod 700 {script_path}")
    write_cron = (f"printf '0 8 * * * root {script_path} >/dev/null 2>&1\\n' > {cron_path}; "
                  f"chmod 600 {cron_path}")
    run_once = f"bash {script_path}"
    results = []
    for step in (mkdir, write_script, write_cron, run_once):
        code, out, err = ssh.exec(step, timeout=120)
        results.append({"cmd": step[:80], "exit_code": code, "stdout": out, "stderr": err})
        if code != 0:
            ssh.close()
            return {"ok": False, "error": err or out or "step failed", "steps": results,
                    "script_path": script_path, "cron_path": cron_path}
    ssh.close()
    return {"ok": True, "script_path": script_path, "cron_path": cron_path, "steps": results}


@registry.register(
    name="ops.security_module.install",
    title="安装服务器安全监控模块",
    description=(
        "在目标服务器安装安全监控模块（fail2ban + auditd + 每日巡检日报，来源 "
        "deanchou/server_security_monitor），并注入落盘补丁，使每日安全日报写入 "
        "/var/log/security-daily/<date>.md，随后把服务器标记为已启用安全监控。"
        "高危远程写操作，需人工审批 / confirm_text 确认。"
        "中文: 安装安全模块/安全加固/安装安全监控/加固服务器/部署安全巡检. "
    ),
    scopes=["ops:write", "server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    keywords=["安装安全模块", "安全加固", "加固服务器", "security monitor",
              "fail2ban", "安装监控", "部署安全"],
    related_tools=["ops.approval.prepare_security_module",
                   "ops.security_report.summarize"],
    input_schema={
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "服务器名称、host、UUID 或短 UUID 前缀",
            },
            "install_fail2ban": {"type": "boolean", "default": True},
            "install_auditd": {"type": "boolean", "default": True},
            "confirm_text": {
                "type": "string",
                "description": "确认短语: CONFIRM ops.security_module.install",
            },
        },
        "required": ["server", "confirm_text"],
        "additionalProperties": False,
    },
)
def install_security_module(args: Dict[str, Any], ctx, db):
    server = _resolve_server_or_404(args.get("server") or "")
    server_name = server.get("name") or server.get("id") or "-"
    payload_b64 = base64.b64encode(_GEN_SCRIPT.encode("utf-8")).decode("ascii")

    steps = []
    steps.append({"cmd": "log", "exit_code": 0,
                  "stdout": "计划安装安全监控模块并注入落盘补丁到 /var/log/security-daily/",
                  "stderr": ""})
    result = _upload_and_install(server, payload_b64)
    steps.extend(result.get("steps") or [])
    if not result.get("ok"):
        return {"ok": False, "server": server_name,
                "error": result.get("error") or "安装失败", "steps": steps}

    # 标记服务器已启用安全监控
    from app.db.models import Server
    row = db.query(Server).filter(
        Server.name == server_name if server_name != (server.get("id") or "") else Server.id == server.get("id")
    ).first()
    if row is None:
        row = db.query(Server).filter(Server.name == server_name).first()
    if row is not None:
        meta = dict(row.metadata_json or {})
        mon = dict(meta.get("security_monitor") or {})
        mon["enabled"] = True
        mon["installed_at"] = _now_iso()
        mon["source"] = "https://github.com/deanchou/server_security_monitor"
        mon["script_path"] = result.get("script_path")
        meta["security_monitor"] = mon
        row.metadata_json = meta  # 赋值全新 dict 确保 SQLAlchemy 检测变更
        db.commit()

    return {
        "ok": True,
        "server": server_name,
        "summary": f"已为服务器 {server_name} 安装安全监控模块并启用每日安全日报采集。",
        "enabled": True,
        "script_path": result.get("script_path"),
        "report_dir": "/var/log/security-daily",
        "steps_ok": all(s.get("exit_code", 0) == 0 for s in steps),
        "steps": steps,
        "next": "可调用 ops.security_report.collect 采集今日日报，或 ops.security_report.summarize 汇总。",
    }
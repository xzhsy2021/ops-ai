"""安全日报汇总 & 安全采集启用工具。

提供 OPS 能力：
- ops.security_report.summarize : 只读，汇总已采集的每日安全巡检日报供 AI 分析。
- ops.security_report.collect   : 触发一次每日日报采集（现场生成 + 写库 + 归档）。
- ops.security_module.probe     : 只读，诊断目标服务器上的安全监控模块与安全服务状态。
- ops.security_module.install   : 把目标服务器标记为"启用每日安全日报采集"，并可选立即采集一次。
                                    不改动服务器端任何文件（采集采用现场生成方式）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import HTTPException

from app.db.models import SecurityDailyReport
from app.services.tool_registry import registry

# 服务器上 deanchou/server_security_monitor 的日报脚本（仅用于 probe 诊断，采集不依赖它）
DAILY_SCRIPT = "/usr/local/bin/security-daily-summary.sh"


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


def _probe_runtime(server: Dict[str, Any]) -> Dict[str, Any]:
    """只读：诊断目标服务器上安全监控模块/日报脚本/安全服务状态。

    仅用于了解服务器现状（供 AI 判断加固必要性），采集功能不依赖这些结果。
    """
    from ssh_client import create_ssh_client

    ssh = create_ssh_client(server)
    out: Dict[str, Any] = {}
    try:
        ssh.connect(max_retries=1, per_attempt_timeout=10)
        def rd(label, cmd, timeout=25):
            try:
                code, co, err = ssh.exec(cmd, timeout=timeout)
                out[label] = {"exit": code, "out": (co or "").strip(), "err": (err or "").strip()}
            except Exception as e:
                out[label] = {"exit": -1, "out": "", "err": str(e)}

        rd("script", f"cat {DAILY_SCRIPT} 2>/dev/null || echo __MISSING__")
        rd("cron", "grep -rn -i 'security-daily\\|security_monitor' "
                   "/etc/cron.d/ /etc/crontab 2>/dev/null | head")
        rd("crontab", "crontab -l 2>/dev/null | grep -i security || true")
        rd("services", "systemctl is-active fail2ban 2>/dev/null; systemctl is-active auditd 2>/dev/null")
        rd("installer", "ls -1 /usr/local/bin/install_security_monitor.sh 2>/dev/null || true")
    finally:
        try:
            ssh.close()
        except Exception:
            pass

    script_present = out.get("script", {}).get("out", "").strip() not in ("", "__MISSING__")
    services_raw = (out.get("services", {}).get("out") or "").replace("\n", "; ")
    return {
        "server": server.get("name") or server.get("id") or "-",
        "connected": out != {},
        "security_monitor_installed": script_present,
        "fail2ban_active": "active" in (out.get("services", {}).get("out") or "").split(";")[0],
        "auditd_active": "active" in (out.get("services", {}).get("out") or "").split(";")[-1],
        "cron": (out.get("cron", {}).get("out") or "") or (out.get("crontab", {}).get("out") or ""),
        "services": services_raw,
        "installer_present": bool((out.get("installer", {}).get("out") or "").strip()),
        "note": "采集采用现场生成方式，不要求已安装 deanchou 监控模块，也不改动服务器文件。",
        "raw": out,
    }


@registry.register(
    name="ops.security_module.probe",
    title="诊断服务器安全监控/安全服务状态",
    description=(
        "只读诊断目标服务器上的安全监控模块（deanchou/server_security_monitor）日报脚本、"
        "定时任务、fail2ban/auditd 服务状态，供判断是否需要安全加固。不执行任何安装/写入。"
        "中文: 校验安全模块/检查安全加固/确认是否已安装/安全服务状态. "
    ),
    scopes=["ops:read", "server:read"],
    risk="low",
    category="report_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    output_masking=True,
    related_tools=["ops.security_module.install", "ops.inspection.run_server"],
    keywords=["校验安全模块", "检查安全加固", "是否已安装", "安全模块状态", "probe"],
    example_prompts=["检查 idn 上是否已安装安全监控模块"],
    input_schema={
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名称、host、UUID 或短 UUID 前缀"},
        },
        "required": ["server"],
        "additionalProperties": False,
    },
)
def probe_security_module(args: Dict[str, Any], ctx, db):
    server = _resolve_server_or_404(args.get("server") or "")
    return _probe_runtime(server)


@registry.register(
    name="ops.security_module.install",
    title="启用服务器每日安全日报采集",
    description=(
        "把目标服务器标记为「已启用每日安全日报采集」，并可选立即采集一次写入日报表/风险台账。"
        "采集采用现场生成方式：通过 SSH 现场执行日报生成命令，不改动服务器端任何文件，"
        "也不要求已安装 deanchou 安全监控模块。仅写 OPS 数据库标记（并可选触发一次只读采集），无需改动服务器。"
        "中文: 启用安全采集/开启日报/启用安全巡检/标记后加入每日日报. "
    ),
    scopes=["ops:write", "server:read"],
    risk="medium",
    category="report_write",
    write=True,
    requires_confirmation=True,
    data_sensitivity="internal",
    keywords=["启用安全采集", "开启日报", "启用安全巡检", "标记采集", "security monitor",
              "每日日报"],
    related_tools=["ops.security_module.probe", "ops.security_report.collect",
                   "ops.security_report.summarize"],
    input_schema={
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名称、host、UUID 或短 UUID 前缀"},
            "run_now": {"type": "boolean", "default": False,
                        "description": "标记后立即现场采集一次今日日报并入库"},
            "confirm_text": {"type": "string", "description": "确认短语: CONFIRM ops.security_module.install"},
        },
        "required": ["server", "confirm_text"],
        "additionalProperties": False,
    },
)
def install_security_module(args: Dict[str, Any], ctx, db):
    server = _resolve_server_or_404(args.get("server") or "")
    server_name = server.get("name") or server.get("id") or "-"
    run_now = bool(args.get("run_now", False))

    steps: List[Dict[str, Any]] = []

    # 标记服务器已启用每日安全日报采集
    from app.db.models import Server
    row = db.query(Server).filter(Server.name == server_name).first() or \
        db.query(Server).filter(Server.id == server.get("id")).first()
    if row is None:
        return {"ok": False, "server": server_name,
                "error": f"未找到服务器记录，无法标记启用（name/id={server_name}）"}
    meta = dict(row.metadata_json or {})
    mon = dict(meta.get("security_monitor") or {})
    mon["enabled"] = True
    mon["source"] = "on_site_generation"
    mon["enabled_at"] = _now_iso()
    meta["security_monitor"] = mon
    row.metadata_json = meta  # 赋值全新 dict 确保 SQLAlchemy 检测变更
    db.commit()
    steps.append({"cmd": "mark_enabled", "exit_code": 0,
                  "stdout": f"已标记 {server_name} 启用每日安全日报采集", "stderr": ""})

    collect_out: Dict[str, Any] = {}
    if run_now:
        from app.services.security_daily import collect_server_report
        report_date = datetime.now().strftime("%Y-%m-%d")
        try:
            collect_out = collect_server_report(db, server, report_date)
            steps.append({"cmd": "collect_now", "exit_code": 0,
                          "stdout": json.dumps(collect_out, ensure_ascii=False), "stderr": ""})
        except Exception as exc:
            collect_out = {"ok": False, "error": str(exc)}
            steps.append({"cmd": "collect_now", "exit_code": 1,
                          "stdout": str(exc), "stderr": ""})

    return {
        "ok": True,
        "server": server_name,
        "summary": f"已启用服务器 {server_name} 的每日安全日报采集"
                   + ("，并立即现场采集一次。" if run_now else "。"),
        "enabled": True,
        "collect_now": collect_out or None,
        "note": "采集为现场生成，不改动服务器任何文件。",
        "steps_ok": all(s.get("exit_code", 0) == 0 for s in steps),
        "steps": steps,
        "next": "可调用 ops.security_report.collect 采集全部已启用服务器的今日日报，"
                "或 ops.security_report.summarize 查看日报。",
    }
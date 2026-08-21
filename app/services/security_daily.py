"""每日安全巡检日报采集与分析。

数据源：在目标服务器上现场执行安全巡检命令（复刻 deanchou/server_security_monitor 的
日报脚本，去掉发送告警 Bot 一步），得到当日巡检 markdown。本服务通过 SSH 现场生成
→ 解析 → 风险判定 → 落库（SecurityDailyReport）→ 高危项写入风险台账（SecurityRisk，
可被 ops.risk.list 查询）→ 归档到报告中心（report_center）。全程不改动服务器文件。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config.servers import get_all_servers
from app.db.models import SecurityDailyReport, SecurityRisk

logger = logging.getLogger(__name__)

# 默认风险阈值
DEFAULT_THRESHOLDS = {
    "login_failures_high": 10,   # 当日报登录失败次数 >= 该值 → HIGH
    "banned_ips_high": 5,        # 被封禁 IP 数 >= 该值 → HIGH
    "banned_ips_medium": 1,      # 被封禁 IP 数 >= 该值 → MEDIUM
    "account_changes_medium": 1, # 账户变更 >= 该值 → MEDIUM
    "loadavg1_high": 4.0,        # 1 分钟负载 >= 该值 → HIGH
}

# security_monitor 标记字段在服务器 metadata_json 中的位置
SECURITY_MONITOR_KEY = "security_monitor"

# 采集时在目标服务器"现场生成"的每日巡检日报脚本（复刻 deanchou/server_security_monitor 的
# security-daily-summary.sh，但去掉发送告警 Bot 的一步，仅输出 markdown 到 stdout）。
# 注意：这里用 Python 字符串保留 $(...) 与引号，交由远端 shell 执行，本地不展开。
GENERATE_REPORT_CMD = r"""export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
echo "## 每日安全巡检报告 $(date '+%F %T')"
echo
echo "### 1. 今日登录记录 (aureport, 最近25条)"
timeout 8 aureport -l -i -ts today 2>/dev/null | tail -25 || echo "无记录"
echo
echo "### 2. 今日登录失败次数: $(timeout 8 aureport -l -i --failed -ts today 2>/dev/null | grep -cE '^[0-9]+\.' )"
echo
echo "### 3. 今日账户变更"
timeout 8 ausearch -ts today -m ADD_USER,DEL_USER,ADD_GROUP,DEL_GROUP,CHUSER_ID,CHGRP_ID,USER_CHAUTHTOK 2>/dev/null | tail -20 || echo "无记录"
echo
echo "### 4. 当前被 fail2ban 封禁的 IP"
timeout 5 fail2ban-client status sshd 2>/dev/null | sed -n '/Banned IP list/,+1p' || echo "无封禁"
echo
echo "### 5. 系统负载"
uptime"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def security_module_servers(db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """采集候选服务器：优先返回已标记启用安全监控且在线的服务器；
    否则退回所有在线/活跃服务器（现场生成日报，不依赖落盘补丁）。"""
    servers = get_all_servers(db=db)
    online = [s for s in servers if s.get("status") in ("online", "active")]
    enabled = [s for s in online if (((s.get("metadata_json") or {}).get(SECURITY_MONITOR_KEY) or {}).get("enabled"))]
    return enabled or online


def read_daily_markdown(server_cfg: Dict[str, Any], report_date: str,
                        timeout: int = 60) -> Dict[str, Any]:
    """通过 SSH 在目标服务器现场生成日报 markdown（复刻报表脚本，不发送告警）。
    返回 md 与能力探测（aureport 是否可用）。"""
    from ssh_client import create_ssh_client

    ssh = create_ssh_client(server_cfg)
    try:
        ssh.connect(max_retries=2, per_attempt_timeout=15)
        cap_cmd, cap_out, _ = ssh.exec("command -v aureport || true", timeout=timeout)
        capable = bool((cap_out or "").strip())
        # 传 on_output 走 ssh_client.exec 的空闲超时安全分支，避免 recv_exit_status 永久阻塞；
        # 命令挂起/传输异常时捕获并返回中断，防止占用后端工作线程导致服务无响应。
        try:
            code, out, err = ssh.exec(GENERATE_REPORT_CMD, timeout=timeout,
                                      on_output=lambda _stream, _data: None)
        except Exception as exc:
            try:
                ssh.close()
            except Exception:
                pass
            return {"ok": False, "capable": capable, "path": "generated",
                    "md": "", "missing": False,
                    "error": f"现场生成中断: {type(exc).__name__}: {exc}", "code": -1}
        if code != 0 and not (out or "").strip():
            return {"ok": False, "capable": capable, "path": "generated",
                    "md": "", "missing": False, "error": (err or "生成失败").strip(),
                    "code": code}
        return {
            "ok": True, "capable": capable,
            "path": "generated::" + report_date,
            "md": (out or "").strip(),
            "missing": False, "error": (err or "").strip(), "code": code,
        }
    finally:
        try:
            ssh.close()
        except Exception:
            pass


def _section_map(md: str) -> Dict[str, str]:
    """按 '### 数字. ' 拆分报告为各章节的原始文本。"""
    blocks = re.split(r"\s*###\s*(\d+)\.", md)
    # blocks[0] 是标题前片段；随后是 (编号, 内容) 交替
    sections: Dict[str, List[str]] = {}
    for i in range(1, len(blocks), 2):
        num = blocks[i].strip()
        body = blocks[i + 1] if i + 1 < len(blocks) else ""
        sections.setdefault(num, []).append(body)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def parse_daily_markdown(md: str) -> List[Dict[str, Any]]:
    """把日报 markdown 解析为结构化 items 列表。"""
    if not md:
        return []
    sections = _section_map(md)
    items: List[Dict[str, Any]] = []

    def plain(body: str) -> str:
        # 去步骤/代码标记并折叠空白
        text = re.sub(r"`|^-{3,}|```", "", body)
        return re.sub(r"\n{2,}", "\n", text).strip()

    # 1. 登录记录
    body = sections.get("1", "")
    login_raw = plain(body)
    success_count = 0
    login_events: List[Dict[str, str]] = []
    for line in login_raw.splitlines():
        if re.match(r"^\s*[\w.:/\-]+\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", line):
            success_count += 1
            parts = line.split()
            login_events.append({
                "user": parts[0] if parts else "",
                "time": parts[1] if len(parts) > 1 else "",
                "host": parts[2] if len(parts) > 2 else "",
                "term": parts[3] if len(parts) > 3 else "",
                "exe": parts[4] if len(parts) > 4 else "",
            })
    if login_events or login_raw:
        items.append({
            "kind": "login_records",
            "title": f"今日登录记录（成功 {success_count} 次）",
            "detail": login_raw[:2000],
            "count": success_count,
            "events": login_events[:25],
        })

    # 2. 登录失败次数
    body = plain(sections.get("2", ""))
    failures = 0
    m = re.search(r"(\d+)\s*(?:次)?", body)
    if m:
        try:
            failures = int(m.group(1))
        except ValueError:
            failures = 0
    items.append({
        "kind": "login_failures",
        "title": "今日登录失败次数",
        "count": failures,
        "detail": body[:500] or f"{failures} 次",
    })

    # 3. 账户变更
    body = plain(sections.get("3", ""))
    # 只统计形如 "  - user" 的 diff 行，忽略 "无" 与标题/摘要行
    account_changes = [ln for ln in body.splitlines()
                       if re.match(r"^\s*[-+*]\s+\S+", ln)]
    if account_changes:
        items.append({
            "kind": "account_changes",
            "title": f"今日账户变更（{len(account_changes)} 项）",
            "count": len(account_changes),
            "detail": "\n".join(account_changes[:50]),
            "entities": account_changes[:20],
        })

    # 4. fail2ban 封禁 IP
    body = plain(sections.get("4", ""))
    # 只提取合法 IP（IPv4/IPv6），避免把 "fail2ban.actions" 之类点分串误判为 IP
    ipv4 = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"
    ipv6 = (r"\b(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"
            r"|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}"
            r"|(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}"
            r"|(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}"
            r"|(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}"
            r"|(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}"
            r"|[0-9a-fA-F]{1,4}:(?::[0-9a-fA-F]{1,4}){1,6}"
            r"|:(?:(?::[0-9a-fA-F]{1,4}){1,7}|:)"
            r"|(?:[0-9a-fA-F]{1,4}:){1,7}:)\b"  # 以 :: 结尾的短 IP v6 放最后，避免吃掉后续组
            )
    banned_ips = list(dict.fromkeys(
        re.findall(rf"{ipv4}|{ipv6}", body)))  # 去重保序
    items.append({
        "kind": "fail2ban_bans",
        "title": f"fail2ban 封禁 IP（{len(banned_ips)} 个）",
        "count": len(banned_ips),
        "ips": banned_ips,
        "detail": "\n".join(banned_ips) if banned_ips else "无",
    })

    # 5. 系统负载
    body = plain(sections.get("5", ""))
    load_avg = None
    uptime = None
    m = re.search(r"load average:\s*([\d.]+),?\s*([\d.]+)?,?\s*([\d.]+)?", body)
    if m:
        load_avg = {
            "min1": float(m.group(1)),
            "min5": float(m.group(2)) if m.group(2) else None,
            "min15": float(m.group(3)) if m.group(3) else None,
        }
    um = re.search(r"up\s+([^,]+),\s+(\d+)\s+user", body)
    if um:
        uptime = {"duration": um.group(1).strip(), "users": int(um.group(2))}
    items.append({
        "kind": "system_load",
        "title": "系统负载",
        "load_avg": load_avg,
        "uptime": uptime,
        "detail": body[:300],
    })

    return items


def evaluate_risk(items: List[Dict[str, Any]],
                  thresholds: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """根据阈值判定日报整体风险等级，返回 (level, reasons)。"""
    thr = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        thr.update({k: v for k, v in thresholds.items() if v is not None})
    by_kind = {it["kind"]: it for it in items}
    level = "LOW"
    reasons: List[str] = []

    failures = (by_kind.get("login_failures") or {}).get("count", 0)
    if failures >= thr["login_failures_high"]:
        level = max_level(level, "HIGH")
        reasons.append(f"登录失败 {failures} 次，超过高危阈值 {thr['login_failures_high']}")
    elif failures > 0:
        level = max_level(level, "MEDIUM")
        reasons.append(f"登录失败 {failures} 次")

    banned = len((by_kind.get("fail2ban_bans") or {}).get("ips") or [])
    if banned >= thr["banned_ips_high"]:
        level = max_level(level, "HIGH")
        reasons.append(f"封禁 IP {banned} 个，超过高危阈值 {thr['banned_ips_high']}")
    elif banned >= thr["banned_ips_medium"]:
        level = max_level(level, "MEDIUM")
        reasons.append(f"封禁 IP {banned} 个")

    changes = (by_kind.get("account_changes") or {}).get("count", 0)
    if changes >= thr["account_changes_medium"]:
        level = max_level(level, "MEDIUM")
        reasons.append(f"账户变更 {changes} 项")

    load = (by_kind.get("system_load") or {}).get("load_avg") or {}
    load1 = load.get("min1")
    if load1 is not None and load1 >= thr["loadavg1_high"]:
        level = max_level(level, "HIGH")
        reasons.append(f"1 分钟负载 {load1}，超过高危阈值 {thr['loadavg1_high']}")

    return level, reasons


def max_level(a: str, b: str) -> str:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return b if order.get(b, 0) > order.get(a, 0) else a


def _risk_objects(server: Dict[str, Any], report_date: str, items: List[Dict[str, Any]],
                  max_risk: str, reasons: List[str], collection_id: str) -> List[SecurityRisk]:
    """把日报里的风险点转成风险台账记录。仅保留中危及以上。"""
    level = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    rows: List[SecurityRisk] = []
    for it in items:
        kind = it["kind"]
        if kind == "login_records" and it.get("events"):
            # 成功登录不视为风险，仅当有登录记录时提示关注
            pass
        if kind == "login_failures" and it.get("count", 0) > 0:
            rows.append(SecurityRisk(
                collection_id=collection_id,
                server_id=server.get("name") or server.get("id") or "-",
                report_date=report_date,
                source="security_daily",
                kind="login_failures",
                title="服务器登录失败告警",
                description=f"{server.get('name')} 今日登录失败 {it.get('count')} 次。",
                risk_level="HIGH" if it.get("count", 0) >= DEFAULT_THRESHOLDS["login_failures_high"] else "MEDIUM",
                status="OPEN",
                evidence={"server": server.get("name"), "report_date": report_date, "count": it.get("count")},
                suggestion="检查失败日志来源 IP，评估是否需要 fail2ban/防火墙加固及弱口令风险。",
            ))
        if kind == "fail2ban_bans" and it.get("ips"):
            rows.append(SecurityRisk(
                collection_id=collection_id,
                server_id=server.get("name") or server.get("id") or "-",
                report_date=report_date,
                source="security_daily",
                kind="fail2ban_bans",
                title="已被 fail2ban 封禁的攻击来源",
                description=f"{server.get('name')} 当前被 fail2ban 封禁 {len(it.get('ips') or [])} 个 IP。",
                risk_level="HIGH" if (it.get("count", 0) >= DEFAULT_THRESHOLDS["banned_ips_high"]) else "MEDIUM",
                status="OPEN",
                evidence={"server": server.get("name"), "report_date": report_date, "ips": it.get("ips")},
                suggestion="封禁数量异常升高通常意味着存在持续爆破，建议核查来源并检查公网暴露面。",
            ))
        if kind == "account_changes":
            rows.append(SecurityRisk(
                collection_id=collection_id,
                server_id=server.get("name") or server.get("id") or "-",
                report_date=report_date,
                source="security_daily",
                kind="account_changes",
                title="系统账户发生变更",
                description=f"{server.get('name')} 今日发生 {it.get('count', 0)} 项账户变更。",
                risk_level="MEDIUM",
                status="OPEN",
                evidence={"server": server.get("name"), "report_date": report_date, "changes": it.get("entities")},
                suggestion="核查新增/删除的账户是否授权，避免存在后门账户。",
            ))
        if kind == "system_load" and (it.get("load_avg") or {}).get("min1", 0) >= DEFAULT_THRESHOLDS["loadavg1_high"]:
            rows.append(SecurityRisk(
                collection_id=collection_id,
                server_id=server.get("name") or server.get("id") or "-",
                report_date=report_date,
                source="security_daily",
                kind="system_load",
                title="服务器负载过高",
                description=f"{server.get('name')} 1 分钟负载 {(it.get('load_avg') or {}).get('min1')}。",
                risk_level="HIGH",
                status="OPEN",
                evidence={"server": server.get("name"), "report_date": report_date, "load_avg": it.get("load_avg")},
                suggestion="检查 CPU/内存/进程占用，定位异常进程。",
            ))
    del level
    return rows


def collect_server_report(db: Session, server_cfg: Dict[str, Any],
                          report_date: str, *, thresholds: Optional[Dict[str, Any]] = None,
                          persist_risks: bool = True) -> Dict[str, Any]:
    """采集单台服务器日报并落库，返回结果 dict。"""
    server_name = server_cfg.get("name") or server_cfg.get("id") or "-"
    fetched = read_daily_markdown(server_cfg, report_date)
    if not fetched["ok"] or fetched.get("missing"):
        # 标记缺失，不覆盖已有正常记录
        existing = (db.query(SecurityDailyReport)
                    .filter_by(server_name=server_name, report_date=report_date)
                    .first())
        if not existing:
            db.add(SecurityDailyReport(
                server_name=server_name,
                report_date=report_date,
                report_path=fetched.get("path"),
                status="missing",
                error=(fetched.get("error") or "日报文件不存在"),
                summary={"fetched": False},
                max_risk="LOW",
            ))
            db.commit()
        return {"server": server_name, "ok": False,
                "status": "missing", "error": fetched.get("error")
                or "Daily report file not found on server"}

    md = fetched["md"]
    items = parse_daily_markdown(md)
    max_risk, reasons = evaluate_risk(items, thresholds=thresholds)
    collection_id = uuid4().hex

    row = (db.query(SecurityDailyReport)
           .filter_by(server_name=server_name, report_date=report_date)
           .first())
    if row is None:
        row = SecurityDailyReport(server_name=server_name, report_date=report_date)
        db.add(row)
    row.report_path = fetched["path"]
    row.raw_md = md
    row.items = items
    row.summary = {
        "login_failures": next((i.get("count") for i in items if i["kind"] == "login_failures"), 0),
        "banned_ips": len(next((i.get("ips") for i in items if i["kind"] == "fail2ban_bans"), [])),
        "account_changes": next((i.get("count") for i in items if i["kind"] == "account_changes"), 0),
        "load_avg": next((i.get("load_avg") for i in items if i["kind"] == "system_load"), None),
        "reasons": reasons,
    }
    row.max_risk = max_risk
    row.status = "ok"
    row.error = None

    if persist_risks:
        db.query(SecurityRisk).filter(
            SecurityRisk.server_id == server_name,
            SecurityRisk.report_date == report_date,
            SecurityRisk.source == "security_daily",
        ).delete(synchronize_session=False)
        for risk in _risk_objects(server_cfg, report_date, items, max_risk, reasons, collection_id):
            db.add(risk)
    db.commit()

    return {
        "server": server_name,
        "ok": True,
        "status": "ok",
        "max_risk": max_risk,
        "reasons": reasons,
        "items_count": len(items),
        "summary": row.summary,
    }


def aggregate_and_archive(db: Session, results: List[Dict[str, Any]], *,
                          report_date: str,
                          created_by: str = "security_daily") -> Dict[str, Any]:
    """汇总多台服务器日报并归档到报告中心。"""
    from app.services.report_center import generate_report_from_payload

    ok = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    high_count = sum(1 for r in results if r.get("max_risk") == "HIGH")
    medium_count = sum(1 for r in results if r.get("max_risk") == "MEDIUM")
    login_failures_total = sum(int((r.get("summary") or {}).get("login_failures") or 0) for r in ok)
    banned_ips_total = sum(int((r.get("summary") or {}).get("banned_ips") or 0) for r in ok)

    payload = {
        "title": f"每日安全巡检日报 {report_date}",
        "report_date": report_date,
        "server_count": len(results),
        "servers": results,
        "summary": {
            "report_date": report_date,
            "server_count": len(results),
            "ok_count": len(ok),
            "failed_count": len(failed),
            "high_count": high_count,
            "medium_count": medium_count,
            "login_failures_total": login_failures_total,
            "banned_ips_total": banned_ips_total,
        },
        "metadata": {
            "source": "security_daily",
            "report_date": report_date,
            "servers_failed": [r.get("server") for r in failed],
            "servers_high": [r.get("server") for r in results if r.get("max_risk") == "HIGH"],
        },
    }

    archived: Dict[str, Any] = {}
    if ok:
        try:
            archived_md = generate_report_from_payload(
                db, payload, report_type="security_daily",
                target_id=report_date, fmt="md",
                title=f"每日安全巡检日报 {report_date}", created_by=created_by,
            )
            archived_html = generate_report_from_payload(
                db, payload, report_type="security_daily",
                target_id=report_date, fmt="html",
                title=f"每日安全巡检日报 {report_date}", created_by=created_by,
            )
            # 回写 html artifact id（查看/下载默认 as=html；_resolve_report 会按 format 找兄弟）
            artifact_id = (archived_html.get("report") or {}).get("id") \
                or (archived_md.get("report") or {}).get("id")
            if artifact_id:
                db.query(SecurityDailyReport).filter(
                    SecurityDailyReport.report_date == report_date,
                ).update({SecurityDailyReport.artifact_id: artifact_id},
                         synchronize_session=False)
                db.commit()
            archived = {
                "md": (archived_md.get("report") or {}).get("id"),
                "html": (archived_html.get("report") or {}).get("id"),
            }
        except Exception as exc:  # 归档失败不影响采集结果
            logger.warning("security_daily archive failed: %s", exc)
            archived = {"error": str(exc)}
    return {"summary": payload["summary"], "archive": archived}


def collect_all(db: Session, report_date: Optional[str] = None, *,
                thresholds: Optional[Dict[str, Any]] = None,
                persist_risks: bool = True,
                progress_cb: Optional[Any] = None,
                concurrency: Optional[int] = None) -> Dict[str, Any]:
    """采集所有已启用安全监控模块的服务器日报（并发采集）。

    - 参照 inspection_center 批量巡检的并发模式：ThreadPoolExecutor，
      每个 worker 使用独立 SQLAlchemy session（collect_server_report 有写库）。
    - 并发度默认取环境变量 SECURITY_DAILY_CONCURRENCY（缺省 8，上限 16）。
    - progress_cb：可选回调 progress_cb(percent)，每完成一台服务器调用，
      用于任务中心实时进度（35 → 90 区间）。
    """
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    servers = security_module_servers(db=db)
    total = len(servers)
    effective = max(1, min(int(concurrency or os.getenv("SECURITY_DAILY_CONCURRENCY", "8") or 8), 16))
    results: List[Optional[Dict[str, Any]]] = [None] * total

    def worker(server_cfg: Dict[str, Any], index: int):
        from app.db.base import SessionLocal
        db2 = SessionLocal()
        try:
            try:
                r = collect_server_report(
                    db2, server_cfg, report_date, thresholds=thresholds,
                    persist_risks=persist_risks)
            except Exception as exc:
                logger.warning("collect security_daily failed for %s: %s",
                               server_cfg.get("name"), exc)
                r = {
                    "server": server_cfg.get("name") or server_cfg.get("id") or "-",
                    "ok": False, "status": "error", "error": str(exc),
                }
            return index, r
        finally:
            db2.close()

    done = 0
    if total == 0:
        pass
    elif effective <= 1 or total <= 1:
        for i, server in enumerate(servers):
            _, r = worker(server, i)
            results[i] = r
            done += 1
            if progress_cb is not None and total > 0:
                try:
                    progress_cb(35 + int(55 * done / total))
                except Exception:
                    pass
    else:
        with ThreadPoolExecutor(max_workers=min(effective, total)) as pool:
            futures = {pool.submit(worker, s, i): i for i, s in enumerate(servers)}
            for fut in as_completed(futures):
                i, r = fut.result()
                results[i] = r
                done += 1
                if progress_cb is not None and total > 0:
                    try:
                        progress_cb(35 + int(55 * done / total))
                    except Exception:
                        pass

    final_results = [r for r in results if r is not None]
    archived = aggregate_and_archive(db, final_results, report_date=report_date)
    return {
        "report_date": report_date,
        "servers": servers,
        "results": final_results,
        "summary": archived.get("summary") or {},
        "archive": archived.get("archive") or {},
    }
"""服务器/项目巡检服务。

首版实现以“只读巡检 + 留痕 + 报告中心可生成”为边界：
- 服务器巡检作为基础能力，覆盖登录、账号、命令、进程、端口、磁盘、服务、备份。
- 项目巡检作为业务落点，复用系统/服务配置与服务器巡检摘要。
- 所有远程命令均为后端内置白名单命令，前端不能传任意命令。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import (
    InspectionEvidence,
    InspectionIssue,
    InspectionItemResult,
    InspectionRun,
)

SCHEMA_VERSION = "inspection.center.v1"
SERVER_SCOPE = "SERVER"
PROJECT_SCOPE = "PROJECT"

SERVER_CATEGORIES = [
    {"code": "LOGIN_SECURITY", "name": "登录安全", "description": "成功/失败登录、暴力破解、root 登录等"},
    {"code": "ACCOUNT_SECURITY", "name": "账号安全", "description": "系统用户、特权用户、可登录用户"},
    {"code": "COMMAND_HISTORY", "name": "命令日志", "description": "history、bash_history 与高危命令痕迹"},
    {"code": "PROCESS_PORT", "name": "进程端口", "description": "进程、端口、高占用与异常监听"},
    {"code": "FIREWALL", "name": "防火墙", "description": "防火墙状态与开放策略"},
    {"code": "DISK", "name": "磁盘空间", "description": "系统、日志、备份目录空间"},
    {"code": "SERVICE_STATUS", "name": "服务状态", "description": "systemd、nginx、数据库、Redis 等基础服务"},
    {"code": "BACKUP", "name": "备份任务", "description": "cron、备份文件与任务日志"},
]

PROJECT_CATEGORIES = [
    {"code": "FILE_SECURITY", "name": "文件安全", "description": "文件权限、陌生脚本、哈希基线预留"},
    {"code": "CONFIG_SECURITY", "name": "配置安全", "description": "明文密码、密钥、debug、匿名访问"},
    {"code": "API_SECURITY", "name": "接口访问安全", "description": "接口错误、攻击特征、敏感接口访问"},
    {"code": "WHITELIST_SECURITY", "name": "白名单与网络权限", "description": "后台、接口、数据库、第三方白名单"},
    {"code": "CUSTOMER_SECURITY", "name": "客户与运营安全", "description": "客户白名单、客户权限、业务异常"},
    {"code": "BACKUP_SECURITY", "name": "项目备份", "description": "项目代码、配置、数据库、业务数据备份"},
    {"code": "RUNTIME_ENVIRONMENT", "name": "运行环境", "description": "项目进程、端口、运行用户、关联服务器风险"},
]

RISK_WEIGHT = {"HIGH": 20, "MEDIUM": 8, "LOW": 2, "NONE": 0}
ISSUE_STATUSES = {"OPEN", "PROCESSING", "FIXED", "VERIFIED", "IGNORED"}

SENSITIVE_PATTERNS = [
    (re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*([^\s,;]+)"), r"\1=******"),
    (re.compile(r"(?i)(secret|token|access[_-]?key|secret[_-]?key|private[_-]?key)\s*[:=]\s*([^\s,;]+)"), r"\1=******"),
    (re.compile(r"(?i)(authorization|cookie)\s*[:=]\s*([^\r\n]+)"), r"\1=******"),
    (re.compile(r"(?i)(jdbc:[^\s]+://[^\s:]+:[^@\s]+@)"), "jdbc:******@"),
]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mask(value: Any, limit: int = 20000) -> str:
    text = str(value if value is not None else "")[:limit]
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,;\s]+", value) if x.strip()]
    return [str(value)]


@dataclass
class CheckResult:
    category: str
    item_code: str
    item_name: str
    status: str
    risk_level: str
    message: str
    suggestion: str = ""
    evidence: str = ""
    command: str = ""
    source_type: str = "COMMAND"


def _save_result(db: Session, run: InspectionRun, result: CheckResult) -> InspectionItemResult:
    evidence_id = None
    evidence_text = _mask(result.evidence or result.message)
    if evidence_text or result.command:
        ev = InspectionEvidence(
            id=uuid4().hex,
            run_id=run.id,
            source_type=result.source_type or "COMMAND",
            command=_mask(result.command, limit=2000),
            content_snapshot=evidence_text,
            content_hash=_hash(evidence_text),
            masked=True,
            created_at=_now(),
        )
        db.add(ev)
        db.flush()
        evidence_id = ev.id
    row = InspectionItemResult(
        id=uuid4().hex,
        run_id=run.id,
        scope_type=run.scope_type,
        server_id=run.server_id,
        project_id=run.project_id,
        category=result.category,
        item_code=result.item_code,
        item_name=result.item_name,
        status=result.status,
        risk_level=result.risk_level,
        message=result.message,
        suggestion=result.suggestion,
        evidence_id=evidence_id,
        raw_output=evidence_text[:4000],
        started_at=_now(),
        finished_at=_now(),
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    if result.risk_level in {"HIGH", "MEDIUM", "LOW"} and result.status in {"RISK", "WARNING", "ERROR"}:
        issue = InspectionIssue(
            id=uuid4().hex,
            run_id=run.id,
            item_result_id=row.id,
            scope_type=run.scope_type,
            server_id=run.server_id,
            project_id=run.project_id,
            title=result.item_name,
            description=result.message,
            risk_level=result.risk_level,
            status="OPEN",
            suggestion=result.suggestion,
            evidence_id=evidence_id,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(issue)
    return row


def _run_remote(server_name: str, command: str, timeout: int = 20) -> Dict[str, Any]:
    # 命令均在服务端固定定义；禁止调用者拼接任意 shell 片段。
    from app.domain.inventory import inventory
    from ssh_client import create_ssh_client

    if server_name in {"local", "__local__", "127.0.0.1", "localhost"}:
        import subprocess
        started = time.time()
        proc = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=timeout)
        return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "duration_ms": int((time.time() - started) * 1000)}

    srv = inventory.get_server(server_name)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
    ssh = create_ssh_client(srv)
    if not ssh:
        raise HTTPException(status_code=503, detail=f"Cannot connect to {server_name}")
    started = time.time()
    try:
        code, out, err = ssh.exec(command, timeout=timeout)
        return {"exit_code": code, "stdout": out or "", "stderr": err or "", "duration_ms": int((time.time() - started) * 1000)}
    finally:
        try:
            ssh.close()
        except Exception:
            pass


def _remote_check(server_name: str, category: str, item_code: str, item_name: str, command: str, analyze) -> CheckResult:
    try:
        result = _run_remote(server_name, command)
        output = f"$ {command}\nexit={result.get('exit_code')} duration_ms={result.get('duration_ms')}\n{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
        risk_level, status, message, suggestion = analyze(result.get("stdout") or "", result.get("stderr") or "", result.get("exit_code"))
        return CheckResult(category, item_code, item_name, status, risk_level, message, suggestion, output, command)
    except Exception as exc:
        return CheckResult(category, item_code, item_name, "ERROR", "MEDIUM", f"巡检项执行失败：{exc}", "检查服务器连接、认证凭据与命令兼容性。", str(exc), command)


def _server_checkers(server_name: str, categories: Iterable[str]) -> List[CheckResult]:
    selected = set(categories or [c["code"] for c in SERVER_CATEGORIES])
    checks: List[CheckResult] = []
    if "LOGIN_SECURITY" in selected:
        checks.append(_remote_check(server_name, "LOGIN_SECURITY", "SERVER_LOGIN_RECENT", "近期登录与失败登录检查", "(last -n 20 2>/dev/null || true); echo '---FAILED---'; (lastb -n 20 2>/dev/null || true)", _analyze_login))
    if "ACCOUNT_SECURITY" in selected:
        checks.append(_remote_check(server_name, "ACCOUNT_SECURITY", "SERVER_ACCOUNT_PRIVILEGE", "系统账号与特权账号检查", "echo '---PASSWD---'; cat /etc/passwd 2>/dev/null | head -200; echo '---UID0---'; grep 'x:0:' /etc/passwd 2>/dev/null || true", _analyze_accounts))
    if "COMMAND_HISTORY" in selected:
        checks.append(_remote_check(server_name, "COMMAND_HISTORY", "SERVER_HISTORY_DANGEROUS", "高危命令历史检查", "(tail -n 200 ~/.bash_history 2>/dev/null || true); echo '---ROOT---'; (tail -n 200 /root/.bash_history 2>/dev/null || true)", _analyze_history))
    if "PROCESS_PORT" in selected:
        checks.append(_remote_check(server_name, "PROCESS_PORT", "SERVER_PROCESS_PORT", "进程与监听端口检查", "echo '---TOP---'; ps -eo pid,ppid,user,comm,%cpu,%mem,args --sort=-%cpu 2>/dev/null | head -30; echo '---PORTS---'; (ss -ntulp 2>/dev/null || netstat -ntulp 2>/dev/null || true)", _analyze_process_ports))
    if "FIREWALL" in selected:
        checks.append(_remote_check(server_name, "FIREWALL", "SERVER_FIREWALL_STATUS", "防火墙状态检查", "(systemctl is-active firewalld 2>/dev/null || true); (ufw status 2>/dev/null || true); (iptables -S 2>/dev/null | head -100 || true)", _analyze_firewall))
    if "DISK" in selected:
        checks.append(_remote_check(server_name, "DISK", "SERVER_DISK_USAGE", "磁盘空间检查", "df -PTh 2>/dev/null | head -100", _analyze_disk))
    if "SERVICE_STATUS" in selected:
        checks.append(_remote_check(server_name, "SERVICE_STATUS", "SERVER_SERVICE_STATUS", "基础服务状态检查", "(systemctl --failed --no-pager 2>/dev/null || true); echo '---COMMON---'; (systemctl is-active nginx mysql mysqld redis redis-server docker 2>/dev/null || true)", _analyze_service))
    if "BACKUP" in selected:
        checks.append(_remote_check(server_name, "BACKUP", "SERVER_BACKUP_TASK", "备份任务检查", "echo '---CRON---'; (crontab -l 2>/dev/null || true); echo '---BACKUPS---'; (find /backup /data/backup /data/backups /var/backups -maxdepth 2 -type f -mtime -2 2>/dev/null | head -100 || true)", _analyze_backup))
    return checks


def _analyze_login(out: str, err: str, code: int):
    failed_lines = [l for l in out.splitlines() if l and "FAILED" not in l and ("invalid" in l.lower() or "ssh" in l.lower() or "pts" in l.lower())]
    if "---FAILED---" in out:
        failed_part = out.split("---FAILED---", 1)[1]
        failed_count = len([l for l in failed_part.splitlines() if l.strip()])
        if failed_count >= 10:
            return "MEDIUM", "WARNING", f"近期失败登录记录较多（约 {failed_count} 条），存在暴力破解风险。", "核查来源 IP，必要时加入黑名单并收紧 SSH 白名单。"
    if "root" in out.lower() and ("pts" in out.lower() or "ssh" in out.lower()):
        return "LOW", "WARNING", "检测到 root 或远程登录记录，请确认是否符合运维规范。", "建议禁止 root 直接登录，使用个人账号 + sudo 审计。"
    return "NONE", "PASS", "未发现明显登录异常。", "保持登录日志留存不少于 90 天。"


def _analyze_accounts(out: str, err: str, code: int):
    uid0 = [l for l in out.splitlines() if ":x:0:" in l]
    login_users = [l for l in out.splitlines() if re.search(r":/(bin/)?(bash|sh|zsh)$", l)]
    if len(uid0) > 1:
        return "HIGH", "RISK", f"发现多个 UID=0 特权账号：{len(uid0)} 个。", "立即核查陌生特权账号，冻结并排查入侵痕迹。"
    if len(login_users) > 10:
        return "LOW", "WARNING", f"可登录用户较多（{len(login_users)} 个），建议复核闲置账号。", "清理离职、闲置、非必要登录账号。"
    return "NONE", "PASS", "系统账号与特权账号检查未发现明显异常。", "保持最小权限与账号定期复盘。"


def _analyze_history(out: str, err: str, code: int):
    patterns = ["rm -rf", "history -c", "chmod 777", "chown root", "curl ", "wget ", "scp ", "ftp ", "mysql ", "redis-cli", "kill -9", "reboot", "shutdown"]
    hit = [p for p in patterns if p.lower() in out.lower()]
    if "history -c" in hit:
        return "HIGH", "RISK", "发现 history 清空命令痕迹。", "立即核查操作人和时间窗口，结合登录日志排查入侵。"
    if hit:
        level = "HIGH" if any(x in hit for x in ["rm -rf", "chmod 777"]) else "MEDIUM"
        return level, "WARNING", f"发现高危命令特征：{', '.join(hit[:8])}。", "核查命令执行上下文，保留证据并确认是否为授权操作。"
    return "NONE", "PASS", "未发现明显高危命令痕迹。", "禁止手动清理 history，建议集中留存命令审计。"


def _analyze_process_ports(out: str, err: str, code: int):
    risky_ports = [" 22 ", ":22 ", ":3306", ":6379", ":27017", ":9200", ":11211"]
    hits = [p.strip() for p in risky_ports if p in out]
    suspicious = ["xmrig", "kinsing", "minerd", "kdevtmpfsi"]
    bad = [p for p in suspicious if p in out.lower()]
    if bad:
        return "HIGH", "RISK", f"发现疑似恶意/挖矿进程特征：{', '.join(bad)}。", "立即隔离服务器，保留进程与网络证据后排查入侵。"
    if hits:
        return "MEDIUM", "WARNING", f"检测到高风险端口监听：{', '.join(sorted(set(hits))) }。", "确认端口是否仅对白名单开放，避免数据库/缓存端口暴露外网。"
    return "NONE", "PASS", "进程和监听端口未发现明显异常。", "保持端口最小暴露。"


def _analyze_firewall(out: str, err: str, code: int):
    text = out.lower()
    if "inactive" in text or "not running" in text:
        return "MEDIUM", "WARNING", "防火墙可能未启用或未运行。", "确认安全组/防火墙策略，生产环境避免全局放行。"
    if "-p all" in text and "0.0.0.0/0" in text:
        return "HIGH", "RISK", "发现疑似全局放行规则。", "立即核查规则来源，收紧到必要端口和白名单 IP。"
    return "NONE", "PASS", "防火墙状态未发现明显异常。", "定期复核开放策略与黑白名单冲突。"


def _analyze_disk(out: str, err: str, code: int):
    high = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6 and parts[-2].endswith('%'):
            try:
                pct = int(parts[-2].rstrip('%'))
                if pct >= 90:
                    high.append(f"{parts[-1]} {pct}%")
            except Exception:
                pass
    if high:
        return "MEDIUM", "WARNING", f"磁盘使用率过高：{', '.join(high[:8])}。", "清理过期日志/备份，确认备份目录和日志目录不会撑满磁盘。"
    return "NONE", "PASS", "磁盘空间未发现明显异常。", "建议日志和备份目录纳入容量预警。"


def _analyze_service(out: str, err: str, code: int):
    if "failed" in out.lower() and "0 loaded units" not in out.lower():
        return "MEDIUM", "WARNING", "存在失败的 systemd 单元或基础服务异常。", "查看失败服务日志，确认是否影响项目运行。"
    return "NONE", "PASS", "基础服务状态未发现明显异常。", "持续关注 Nginx、数据库、Redis、Docker 等依赖服务。"


def _analyze_backup(out: str, err: str, code: int):
    if "---BACKUPS---" in out and not out.split("---BACKUPS---", 1)[1].strip():
        return "LOW", "WARNING", "未发现最近 2 天常见备份目录中的备份文件。", "确认备份路径是否已配置；生产项目需每日核查备份任务和文件有效性。"
    if "---CRON---" in out and not out.split("---CRON---", 1)[0].strip() and not out.split("---CRON---", 1)[1].strip():
        return "LOW", "WARNING", "未发现当前用户 crontab 备份任务。", "确认备份任务是否由其他调度系统执行。"
    return "NONE", "PASS", "备份任务和近期备份文件未发现明显异常。", "建议每周抽检备份文件可恢复性。"


def list_servers() -> List[Dict[str, Any]]:
    from app.domain.inventory import inventory
    items = []
    for srv in inventory.list_servers():
        items.append({
            "id": srv.get("name"),
            "name": srv.get("name"),
            "host": srv.get("host"),
            "ip": srv.get("host"),
            "group": srv.get("group") or "",
            "env": srv.get("env") or srv.get("environment") or "",
        })
    if not items:
        items.append({"id": "local", "name": "local", "host": "127.0.0.1", "ip": "127.0.0.1", "group": "本机", "env": "local"})
    return items


def list_projects() -> List[Dict[str, Any]]:
    from app.config.systems import get_all_systems
    systems = get_all_systems()
    result: List[Dict[str, Any]] = []
    for sys_name, sys_cfg in systems.items():
        services = sys_cfg.get("services") or []
        sys_servers = _as_list(sys_cfg.get("servers"))
        env_servers = []
        for env_cfg in (sys_cfg.get("environments") or {}).values():
            if isinstance(env_cfg, dict):
                env_servers.extend(_as_list(env_cfg.get("servers")))
        if not services:
            result.append({
                "id": sys_name,
                "system": sys_name,
                "service": "",
                "name": sys_cfg.get("display_name") or sys_name,
                "display_name": sys_cfg.get("display_name") or sys_name,
                "servers": sorted(set(sys_servers + env_servers)),
                "config": sys_cfg,
            })
            continue
        for svc in services:
            if not isinstance(svc, dict):
                continue
            svc_name = svc.get("name") or svc.get("service_name") or "service"
            servers = _as_list(svc.get("servers")) + _as_list(svc.get("server")) + sys_servers + env_servers
            result.append({
                "id": f"{sys_name}/{svc_name}",
                "system": sys_name,
                "service": svc_name,
                "name": f"{sys_cfg.get('display_name') or sys_name} / {svc.get('display_name') or svc_name}",
                "display_name": svc.get("display_name") or svc_name,
                "servers": sorted(set(servers)),
                "deploy_path": svc.get("deploy_path") or svc.get("path") or "",
                "log_path": svc.get("log_path") or "",
                "config_path": svc.get("config_path") or "",
                "backup_path": svc.get("backup_path") or "",
                "runtime_user": svc.get("runtime_user") or svc.get("user") or "",
                "main_port": svc.get("port") or svc.get("main_port") or "",
                "config": svc,
            })
    return result


def _get_project(project_id: str) -> Dict[str, Any]:
    for p in list_projects():
        if p["id"] == project_id:
            return p
    raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")


def _project_config_checks(project: Dict[str, Any], selected: set[str]) -> List[CheckResult]:
    checks: List[CheckResult] = []
    cfg = project.get("config") or {}
    raw = json.dumps(cfg, ensure_ascii=False, indent=2)
    if "CONFIG_SECURITY" in selected:
        secret_keys = ["password", "passwd", "secret", "token", "access_key", "secret_key", "private_key", "authorization", "cookie"]
        hits = [k for k in secret_keys if re.search(rf'"?{re.escape(k)}"?\s*:', raw, flags=re.IGNORECASE)]
        if hits:
            checks.append(CheckResult("CONFIG_SECURITY", "PROJECT_CONFIG_SECRET", "项目配置敏感信息扫描", "RISK", "HIGH", f"配置中疑似存在敏感字段：{', '.join(sorted(set(hits)))}。", "敏感信息应迁移到密钥管理或加密配置，报告展示已脱敏。", raw, source_type="CONFIG"))
        else:
            checks.append(CheckResult("CONFIG_SECURITY", "PROJECT_CONFIG_SECRET", "项目配置敏感信息扫描", "PASS", "NONE", "配置项未发现明显敏感字段。", "继续保持密钥不落明文配置。", raw, source_type="CONFIG"))
        if re.search(r'(?i)(debug|anonymous|allow_all|permit_all).*?(true|1|yes|on)', raw):
            checks.append(CheckResult("CONFIG_SECURITY", "PROJECT_CONFIG_DEBUG_OR_ANON", "Debug/匿名访问配置检查", "WARNING", "MEDIUM", "配置中疑似存在 debug 或匿名/全量放行开关。", "生产环境关闭 debug、匿名访问和全量放行配置。", raw, source_type="CONFIG"))
    if "WHITELIST_SECURITY" in selected:
        whitelist_text = json.dumps({k: v for k, v in cfg.items() if "white" in str(k).lower() or "allow" in str(k).lower()}, ensure_ascii=False)
        if "0.0.0.0/0" in raw or "*" in whitelist_text:
            checks.append(CheckResult("WHITELIST_SECURITY", "PROJECT_WHITELIST_WILDCARD", "项目白名单宽松配置检查", "RISK", "HIGH", "白名单配置疑似存在全量放行或通配符。", "立即收紧白名单，仅保留已审批 IP/客户/第三方标识。", raw, source_type="CONFIG"))
        else:
            checks.append(CheckResult("WHITELIST_SECURITY", "PROJECT_WHITELIST_CONFIG", "项目白名单配置检查", "PASS", "NONE", "未发现明显全量放行白名单配置。", "新增白名单需登记用途、负责人和有效期。", raw, source_type="CONFIG"))
    if "CUSTOMER_SECURITY" in selected:
        checks.append(CheckResult("CUSTOMER_SECURITY", "PROJECT_CUSTOMER_SECURITY_TODO", "客户与运营安全巡检占位", "WARNING", "LOW", "首版未接入业务客户日志和客户权限数据源，已预留巡检分类。", "后续接入客户白名单、客户操作日志、客户权限表后启用强规则。", raw, source_type="CONFIG"))
    return checks


def _project_remote_checks(project: Dict[str, Any], selected: set[str]) -> List[CheckResult]:
    checks: List[CheckResult] = []
    servers = project.get("servers") or []
    if not servers:
        checks.append(CheckResult("RUNTIME_ENVIRONMENT", "PROJECT_NO_SERVER", "项目部署服务器关联检查", "WARNING", "MEDIUM", "项目未配置部署服务器，无法关联服务器巡检基础能力。", "在系统/服务配置中维护项目部署服务器。", json.dumps(project, ensure_ascii=False), source_type="CONFIG"))
        return checks
    deploy_path = str(project.get("deploy_path") or "").strip()
    log_path = str(project.get("log_path") or "").strip()
    backup_path = str(project.get("backup_path") or "").strip()
    port = str(project.get("main_port") or "").strip()
    for server_name in servers[:5]:
        if "RUNTIME_ENVIRONMENT" in selected:
            cmd_parts = ["echo '---HOST---'; hostname 2>/dev/null; uptime 2>/dev/null"]
            if port and re.match(r"^\d{1,5}$", port):
                cmd_parts.append(f"echo '---PORT---'; (ss -ntulp 2>/dev/null || netstat -ntulp 2>/dev/null || true) | grep -E '(:{shlex.quote(port)}\\b)' || true")
            if deploy_path:
                cmd_parts.append(f"echo '---DEPLOY_PATH---'; ls -ld {shlex.quote(deploy_path)} 2>/dev/null || true")
                cmd_parts.append(f"du -sh {shlex.quote(deploy_path)} 2>/dev/null || true")
            if log_path:
                cmd_parts.append(f"echo '---LOG_PATH---'; ls -ld {shlex.quote(log_path)} 2>/dev/null || true; du -sh {shlex.quote(log_path)} 2>/dev/null || true")
            checks.append(_remote_check(server_name, "RUNTIME_ENVIRONMENT", "PROJECT_RUNTIME_ENV", f"项目运行环境检查：{server_name}", "; ".join(cmd_parts), _analyze_project_runtime))
        if "FILE_SECURITY" in selected and deploy_path:
            cmd = f"echo '---PERM---'; find {shlex.quote(deploy_path)} -maxdepth 2 \\( -perm -0002 -o -perm -4000 \\) -ls 2>/dev/null | head -100; echo '---SCRIPTS---'; find {shlex.quote(deploy_path)} -maxdepth 3 -type f \\( -name '*.sh' -o -name '*.exe' -o -name '.*' \\) 2>/dev/null | head -100"
            checks.append(_remote_check(server_name, "FILE_SECURITY", "PROJECT_FILE_PERMISSION", f"项目文件权限检查：{server_name}", cmd, _analyze_project_files))
        if "API_SECURITY" in selected and log_path:
            cmd = f"echo '---ERRORS---'; (grep -E ' 40[34] | 500 |SQL|select |union |xss|script|[.][.]/|/etc/passwd' -i {shlex.quote(log_path)}/* 2>/dev/null | tail -100 || true)"
            checks.append(_remote_check(server_name, "API_SECURITY", "PROJECT_API_LOG", f"项目接口日志安全检查：{server_name}", cmd, _analyze_project_api))
        if "BACKUP_SECURITY" in selected:
            if backup_path:
                cmd = f"find {shlex.quote(backup_path)} -maxdepth 2 -type f -mtime -2 -printf '%TY-%Tm-%Td %TH:%TM %s %p\\n' 2>/dev/null | head -100"
            else:
                cmd = "find /backup /data/backup /data/backups /var/backups -maxdepth 2 -type f -mtime -2 2>/dev/null | head -100 || true"
            checks.append(_remote_check(server_name, "BACKUP_SECURITY", "PROJECT_BACKUP_FILES", f"项目备份文件检查：{server_name}", cmd, _analyze_project_backup))
    return checks


def _analyze_project_runtime(out: str, err: str, code: int):
    if "---PORT---" in out and not out.split("---PORT---", 1)[1].strip():
        return "MEDIUM", "WARNING", "项目主端口未检测到监听或端口配置不匹配。", "确认项目进程、端口配置和负载均衡转发规则。"
    if "No such file" in out:
        return "MEDIUM", "WARNING", "项目部署目录或日志目录不存在。", "核查项目部署路径、日志路径配置。"
    return "NONE", "PASS", "项目运行环境检查未发现明显异常。", "建议项目巡检前先完成关联服务器巡检。"


def _analyze_project_files(out: str, err: str, code: int):
    if " -rws" in out or " 777 " in out or "rwxrwxrwx" in out:
        return "MEDIUM", "WARNING", "项目目录存在全局可写或 SUID 权限风险。", "禁止 777 权限，业务程序不应使用 root/SUID 权限运行。"
    scripts = [l for l in out.splitlines() if l.strip().endswith(('.sh', '.exe'))]
    if scripts:
        return "LOW", "WARNING", f"项目目录存在脚本/可执行文件 {len(scripts)} 个，需要确认来源。", "对脚本文件建立基线，核查陌生脚本和隐藏文件。"
    return "NONE", "PASS", "项目文件权限未发现明显异常。", "后续建议建立 SHA256 文件完整性基线。"


def _analyze_project_api(out: str, err: str, code: int):
    text = out.lower()
    attack = [x for x in ["union", "../", "/etc/passwd", "<script", "xss", "sql"] if x in text]
    if attack:
        return "HIGH", "RISK", f"接口日志出现疑似攻击特征：{', '.join(sorted(set(attack)))}。", "立即核查来源 IP、接口参数与 WAF/网关拦截策略。"
    error_count = len([l for l in out.splitlines() if "500" in l])
    if error_count >= 20:
        return "MEDIUM", "WARNING", f"接口 500 错误较多（采样 {error_count} 条）。", "定位高频报错接口并修复程序 BUG。"
    return "NONE", "PASS", "接口日志采样未发现明显攻击或错误激增。", "持续保留接口访问日志和错误日志。"


def _analyze_project_backup(out: str, err: str, code: int):
    if not out.strip():
        return "MEDIUM", "WARNING", "未发现最近 2 天项目备份文件。", "确认项目备份任务、备份路径与异地同步状态。"
    if re.search(r"\b0\s+/.+", out):
        return "HIGH", "RISK", "备份目录存在 0 字节文件，可能备份失败或损坏。", "立即手动触发备份并验证可恢复性。"
    return "NONE", "PASS", "项目近期备份文件检查通过。", "建议每周抽检备份文件解压/恢复。"


def _finalize_run(db: Session, run: InspectionRun) -> InspectionRun:
    rows = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).all()
    high = sum(1 for r in rows if r.risk_level == "HIGH")
    medium = sum(1 for r in rows if r.risk_level == "MEDIUM")
    low = sum(1 for r in rows if r.risk_level == "LOW")
    normal = sum(1 for r in rows if r.risk_level in {"NONE", ""} and r.status == "PASS")
    score = max(0, 100 - high * RISK_WEIGHT["HIGH"] - medium * RISK_WEIGHT["MEDIUM"] - low * RISK_WEIGHT["LOW"])
    run.high_count = high
    run.medium_count = medium
    run.low_count = low
    run.normal_count = normal
    run.score = score
    run.status = "SUCCESS" if not any(r.status == "ERROR" for r in rows) else "PARTIAL_SUCCESS"
    run.summary = f"巡检完成：评分 {score}，高危 {high}，中危 {medium}，低危 {low}，通过 {normal}。"
    run.finished_at = _now()
    run.updated_at = _now()
    db.commit()
    db.refresh(run)
    return run


def run_server_inspection(db: Session, *, server_id: str, categories: Optional[List[str]] = None, trigger_type: str = "MANUAL", created_by: str = "") -> Dict[str, Any]:
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id is required")
    cats = categories or [c["code"] for c in SERVER_CATEGORIES]
    run = InspectionRun(id=uuid4().hex, scope_type=SERVER_SCOPE, server_id=server_id, trigger_type=trigger_type, status="RUNNING", categories=cats, created_by=created_by, started_at=_now(), created_at=_now(), updated_at=_now())
    db.add(run)
    db.commit()
    db.refresh(run)
    for result in _server_checkers(server_id, cats):
        _save_result(db, run, result)
    run = _finalize_run(db, run)
    return inspection_run_detail(db, run.id)


def run_project_inspection(db: Session, *, project_id: str, categories: Optional[List[str]] = None, trigger_type: str = "MANUAL", created_by: str = "", include_server_summary: bool = True) -> Dict[str, Any]:
    project = _get_project(project_id)
    cats = categories or [c["code"] for c in PROJECT_CATEGORIES]
    selected = set(cats)
    run = InspectionRun(id=uuid4().hex, scope_type=PROJECT_SCOPE, project_id=project_id, trigger_type=trigger_type, status="RUNNING", categories=cats, metadata_json={"project": {k: v for k, v in project.items() if k != "config"}}, created_by=created_by, started_at=_now(), created_at=_now(), updated_at=_now())
    db.add(run)
    db.commit()
    db.refresh(run)
    for result in _project_config_checks(project, selected):
        _save_result(db, run, result)
    for result in _project_remote_checks(project, selected):
        _save_result(db, run, result)
    if include_server_summary:
        _append_server_summary(db, run, project)
    run = _finalize_run(db, run)
    return inspection_run_detail(db, run.id)


def _append_server_summary(db: Session, run: InspectionRun, project: Dict[str, Any]) -> None:
    servers = project.get("servers") or []
    if not servers:
        return
    since = _now() - timedelta(days=7)
    summaries = []
    for server in servers[:10]:
        last = db.query(InspectionRun).filter(
            InspectionRun.scope_type == SERVER_SCOPE,
            InspectionRun.server_id == server,
            InspectionRun.created_at >= since,
        ).order_by(InspectionRun.created_at.desc()).first()
        if last:
            summaries.append(f"{server}: 最近巡检 {last.created_at}，评分 {last.score}，高危 {last.high_count}，中危 {last.medium_count}")
        else:
            summaries.append(f"{server}: 近 7 天无服务器巡检记录")
    risk = "LOW" if any("无服务器巡检" in s for s in summaries) else "NONE"
    status = "WARNING" if risk == "LOW" else "PASS"
    _save_result(db, run, CheckResult("RUNTIME_ENVIRONMENT", "PROJECT_SERVER_SUMMARY", "关联服务器巡检摘要", status, risk, "；".join(summaries), "项目巡检前建议完成关联服务器基础巡检。", "\n".join(summaries), source_type="SUMMARY"))


def list_runs(db: Session, *, scope_type: str = "", server_id: str = "", project_id: str = "", limit: int = 100) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    q = db.query(InspectionRun)
    if scope_type:
        q = q.filter(InspectionRun.scope_type == scope_type.upper())
    if server_id:
        q = q.filter(InspectionRun.server_id == server_id)
    if project_id:
        q = q.filter(InspectionRun.project_id == project_id)
    rows = q.order_by(InspectionRun.created_at.desc()).limit(limit).all()
    return {"items": [_run_to_dict(r) for r in rows], "total": len(rows)}


def _run_to_dict(r: InspectionRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "scope_type": r.scope_type,
        "server_id": r.server_id,
        "project_id": r.project_id,
        "trigger_type": r.trigger_type,
        "status": r.status,
        "score": r.score,
        "high_count": r.high_count,
        "medium_count": r.medium_count,
        "low_count": r.low_count,
        "normal_count": r.normal_count,
        "summary": r.summary,
        "categories": r.categories or [],
        "metadata": r.metadata_json or {},
        "report_id": r.report_id,
        "created_by": r.created_by,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _item_to_dict(r: InspectionItemResult) -> Dict[str, Any]:
    return {"id": r.id, "run_id": r.run_id, "scope_type": r.scope_type, "server_id": r.server_id, "project_id": r.project_id, "category": r.category, "item_code": r.item_code, "item_name": r.item_name, "status": r.status, "risk_level": r.risk_level, "message": r.message, "suggestion": r.suggestion, "evidence_id": r.evidence_id, "raw_output": r.raw_output, "created_at": r.created_at.isoformat() if r.created_at else None}


def _issue_to_dict(r: InspectionIssue) -> Dict[str, Any]:
    return {"id": r.id, "run_id": r.run_id, "item_result_id": r.item_result_id, "scope_type": r.scope_type, "server_id": r.server_id, "project_id": r.project_id, "title": r.title, "description": r.description, "risk_level": r.risk_level, "status": r.status, "owner_id": r.owner_id, "deadline_at": r.deadline_at.isoformat() if r.deadline_at else None, "fixed_at": r.fixed_at.isoformat() if r.fixed_at else None, "verified_at": r.verified_at.isoformat() if r.verified_at else None, "suggestion": r.suggestion, "evidence_id": r.evidence_id, "created_at": r.created_at.isoformat() if r.created_at else None, "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def inspection_run_detail(db: Session, run_id: str) -> Dict[str, Any]:
    run = db.query(InspectionRun).filter(InspectionRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Inspection run not found")
    items = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run_id).order_by(InspectionItemResult.created_at.asc()).all()
    issues = db.query(InspectionIssue).filter(InspectionIssue.run_id == run_id).order_by(InspectionIssue.created_at.asc()).all()
    return {"run": _run_to_dict(run), "items": [_item_to_dict(x) for x in items], "issues": [_issue_to_dict(x) for x in issues]}


def list_issues(db: Session, *, scope_type: str = "", risk_level: str = "", status: str = "", server_id: str = "", project_id: str = "", limit: int = 200, offset: int = 0) -> Dict[str, Any]:
    q = db.query(InspectionIssue)
    if scope_type:
        q = q.filter(InspectionIssue.scope_type == scope_type.upper())
    if risk_level:
        q = q.filter(InspectionIssue.risk_level == risk_level.upper())
    if status:
        q = q.filter(InspectionIssue.status == status.upper())
    if server_id:
        q = q.filter(InspectionIssue.server_id == server_id)
    if project_id:
        q = q.filter(InspectionIssue.project_id == project_id)
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))
    total = q.count()
    rows = q.order_by(InspectionIssue.created_at.desc()).offset(offset).limit(limit).all()
    return {"items": [_issue_to_dict(x) for x in rows], "total": total, "limit": limit, "offset": offset}


def update_issue(db: Session, issue_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    row = db.query(InspectionIssue).filter(InspectionIssue.id == issue_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Inspection issue not found")
    if payload.get("status"):
        status = str(payload["status"]).upper()
        if status not in ISSUE_STATUSES:
            raise HTTPException(status_code=400, detail=f"Unsupported status: {status}")
        row.status = status
        if status == "FIXED":
            row.fixed_at = _now()
        if status == "VERIFIED":
            row.verified_at = _now()
    if "owner_id" in payload:
        row.owner_id = str(payload.get("owner_id") or "")[:128]
    if "suggestion" in payload:
        row.suggestion = str(payload.get("suggestion") or "")[:4000]
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return _issue_to_dict(row)


def overview(db: Session) -> Dict[str, Any]:
    recent = db.query(InspectionRun).order_by(InspectionRun.created_at.desc()).limit(50).all()
    issues = db.query(InspectionIssue).filter(InspectionIssue.status.in_(["OPEN", "PROCESSING"])).order_by(InspectionIssue.created_at.desc()).limit(200).all()
    server_runs = [r for r in recent if r.scope_type == SERVER_SCOPE]
    project_runs = [r for r in recent if r.scope_type == PROJECT_SCOPE]
    return {
        "schema_version": SCHEMA_VERSION,
        "server_categories": SERVER_CATEGORIES,
        "project_categories": PROJECT_CATEGORIES,
        "server_count": len(list_servers()),
        "project_count": len(list_projects()),
        "recent_run_count": len(recent),
        "latest_server_run": _run_to_dict(server_runs[0]) if server_runs else None,
        "latest_project_run": _run_to_dict(project_runs[0]) if project_runs else None,
        "open_issue_count": len(issues),
        "high_issue_count": sum(1 for i in issues if i.risk_level == "HIGH"),
        "medium_issue_count": sum(1 for i in issues if i.risk_level == "MEDIUM"),
        "low_issue_count": sum(1 for i in issues if i.risk_level == "LOW"),
        "recent_runs": [_run_to_dict(r) for r in recent[:10]],
        "recent_issues": [_issue_to_dict(i) for i in issues[:10]],
    }


def report_payload(db: Session, run_id: str) -> Dict[str, Any]:
    detail = inspection_run_detail(db, run_id)
    run = detail["run"]
    summary = {
        "score": run.get("score"),
        "scope_type": run.get("scope_type"),
        "server_id": run.get("server_id"),
        "project_id": run.get("project_id"),
        "high_count": run.get("high_count"),
        "medium_count": run.get("medium_count"),
        "low_count": run.get("low_count"),
        "status": run.get("status"),
    }
    return {"data": detail, "summary": summary, "metadata": {"run_id": run_id, "schema_version": SCHEMA_VERSION}}

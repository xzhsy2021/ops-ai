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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models import (
    InspectionBaseline,
    InspectionEvidence,
    InspectionIssue,
    InspectionItemConfig,
    InspectionItemResult,
    InspectionRule,
    InspectionRun,
    ProjectServerRelation,
)

SCHEMA_VERSION = "inspection.center.v2"
SERVER_SCOPE = "SERVER"
PROJECT_SCOPE = "PROJECT"
PROJECT_COMBINED_SCOPE = "PROJECT_COMBINED"

SERVER_CATEGORIES = [
    {"code": "LOGIN_SECURITY", "name": "登录安全", "description": "成功/失败登录、暴力破解、root 登录等"},
    {"code": "ACCOUNT_SECURITY", "name": "账号安全", "description": "系统用户、特权用户、可登录用户"},
    {"code": "COMMAND_HISTORY", "name": "命令日志", "description": "history、bash_history 与高危命令痕迹"},
    {"code": "PROCESS_PORT", "name": "进程端口", "description": "进程、端口、高占用与异常监听"},
    {"code": "FIREWALL", "name": "防火墙", "description": "防火墙状态与开放策略"},
    {"code": "DISK", "name": "磁盘空间", "description": "系统、日志、备份目录空间与 inode"},
    {"code": "MEMORY", "name": "内存状况", "description": "内存使用率、Swap 使用率与 OOM 风险"},
    {"code": "SERVICE_STATUS", "name": "服务状态", "description": "systemd、nginx、数据库、Redis 等基础服务"},
    {"code": "BACKUP", "name": "备份任务", "description": "cron、备份文件与任务日志"},
    {"code": "FAIL2BAN", "name": "暴力破解防护", "description": "Fail2ban 服务状态、jail 配置与封禁情况"},
    {"code": "AUDITD", "name": "安全检查审计", "description": "auditd 审计服务、审计规则与关键文件覆盖"},
    {"code": "KEY_FILE_SECURITY", "name": "关键文件安全", "description": "passwd/shadow/ssh/cron/.ssh 等关键文件权限与属主"},
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


SERVER_RULE_COMMANDS: Dict[str, str] = {
    "LOGIN_SECURITY": """# 登录安全巡检（只读）\nlast -n 80\nlastb -n 80\n# CentOS/RHEL 登录安全日志\ntail -n 200 /var/log/secure 2>/dev/null || true\n# Ubuntu/Debian 登录安全日志\ntail -n 200 /var/log/auth.log 2>/dev/null || true\n# 判定要点：陌生 IP、连续失败登录、root 远程登录、非工作时间登录。""",
    "ACCOUNT_SECURITY": """# 账号安全巡检（只读）
echo '---PASSWD---'
cat /etc/passwd 2>/dev/null | head -200
echo '---UID0---'
grep 'x:0:' /etc/passwd 2>/dev/null || true
echo '---SHADOW---'
(cat /etc/shadow 2>/dev/null | head -200 || echo 'PERMISSION_DENIED')
echo '---SHADOW_ERR---'
(cat /etc/shadow 2>/dev/null >/dev/null 2>&1 && echo 'OK' || echo 'ERR')
# 判定要点：陌生账号、UID=0 特权账号、可登录账号、闲置账号。""",
    "COMMAND_HISTORY": """# 命令日志巡检（只读）\nls -la /root/.bash_history /home/*/.bash_history 2>/dev/null || true\nfind /root /home -maxdepth 2 -name '.bash_history' -type f -print -exec tail -n 120 {} \\; 2>/dev/null || true\n# 高危命令关键词：rm -rf、chmod 777、chown、wget、curl、scp、ftp、history -c、mysql、redis-cli、kill。""",
    "PROCESS_PORT": """# 进程端口巡检（只读）\nps -eo pid,ppid,user,pcpu,pmem,etime,cmd --sort=-pcpu | head -n 60\nss -ntulp 2>/dev/null || netstat -ntulp 2>/dev/null || netstat -an | grep LISTEN\n# 判定要点：未知进程、高占用、挖矿特征、未知监听端口、高危端口外网暴露。""",
    "FIREWALL": """# 防火墙巡检（只读）\nsystemctl is-active firewalld 2>/dev/null || true\nfirewall-cmd --list-all 2>/dev/null || true\niptables -S 2>/dev/null || true\nufw status verbose 2>/dev/null || true\n# 判定要点：防火墙关闭、全局放行、高危端口放行、黑白名单冲突。""",
    "MEMORY": "free -h && echo '---' && cat /proc/meminfo | grep -E '^(MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree|Cached|Buffers)' && echo '---' && dmesg | grep -i 'oom\\|killed process' | tail -20",
    "DISK": """# 磁盘空间巡检（只读）\ndf -hT\ndu -sh /var/log 2>/dev/null || true\ndu -sh /data /backup /opt 2>/dev/null || true\n# 判定要点：磁盘使用率超过阈值、日志目录异常膨胀、备份目录空间不足。""",
    "SERVICE_STATUS": """# 服务状态巡检（只读）\nsystemctl --failed 2>/dev/null || true\nsystemctl is-active nginx 2>/dev/null || true\nsystemctl is-active redis 2>/dev/null || true\nsystemctl is-active mysql mysqld mariadb postgresql 2>/dev/null || true\nps -ef | egrep 'nginx|redis|mysql|postgres|java|node|python|gunicorn|uvicorn' | grep -v grep || true\n# 判定要点：基础服务异常、项目进程缺失、异常重启或失败单元。""",
    "BACKUP": """# 备份任务巡检（只读）\ncrontab -l 2>/dev/null || true\nls -lah /backup /data/backups 2>/dev/null || true\nfind /backup /data/backups -maxdepth 2 -type f -mtime -2 -printf '%TY-%Tm-%Td %TH:%TM %s %p\\n' 2>/dev/null | tail -n 80 || true\n# 判定要点：当日备份缺失、0KB 文件、备份脚本失败、备份堆积、异地同步异常。""",
    "FAIL2BAN": """# Fail2ban 暴力破解防护巡检（只读）\necho '---SERVICE---'\n(s=$(systemctl is-active fail2ban 2>/dev/null); echo \"${s:-UNKNOWN}\")\necho '---BIN---'\n(command -v fail2ban-client 2>/dev/null || echo 'FAIL2BAN_BIN_NOT_FOUND')\necho '---STATUS---'\n(fail2ban-client status 2>&1 || echo 'FAIL2BAN_DAEMON_DOWN')\necho '---JAIL_SSHD---'\n(fail2ban-client status sshd 2>&1 || echo 'NO_SSHD_JAIL')\necho '---CONFIG---'\n(cat /etc/fail2ban/jail.local 2>/dev/null || cat /etc/fail2ban/jail.conf 2>/dev/null || echo 'NO_FAIL2BAN_CONFIG')\necho '---BANNED---'\n(fail2ban-client status sshd 2>/dev/null | grep -iE 'banned|currently failed' || true)\n# 判定要点：fail2ban 二进制不存在（未安装）/已安装但服务未运行、无 sshd jail、maxretry/findtime/bantime 配置过松、封禁 IP 数量。""",
    "AUDITD": """# 安全检查审计巡检（只读）\necho '---SERVICE---'\n(s=$(systemctl is-active auditd 2>/dev/null); echo \"${s:-UNKNOWN}\")\necho '---STATUS---'\n(auditctl -s 2>/dev/null || echo 'AUDITCTL_NOT_FOUND')\necho '---RULES---'\n(auditctl -l 2>/dev/null | head -150 || echo 'NO_RULES')\necho '---RULES_DIR---'\n(ls -la /etc/audit/rules.d/ 2>/dev/null || true)\n(cat /etc/audit/rules.d/*.rules 2>/dev/null | head -150 || true)\necho '---LOG---'\n(ls -lh /var/log/audit/audit.log 2>/dev/null || echo 'NO_AUDIT_LOG')\n# 判定要点：auditd 未安装/未启用、审计未开启、无审计规则、关键文件（passwd/shadow/ssh/cron/.ssh）未覆盖、危险命令审计缺失。""",
    "KEY_FILE_SECURITY": """# 关键文件权限与完整性巡检（只读）\necho '---KEYFILES---'\nfor f in /etc/passwd /etc/shadow /etc/gshadow /etc/group /etc/ssh/sshd_config /etc/crontab /etc/cron.d /root/.ssh /etc/fail2ban/jail.local; do\n  if [ -e \"$f\" ]; then\n    stat -c '%n %U:%G %a %s' \"$f\" 2>/dev/null\n  else\n    echo \"$f MISSING\"\n  fi\ndone\necho '---SSHD_CONFIG---'\n(grep -viE '^\\s*#|^\\s*$' /etc/ssh/sshd_config 2>/dev/null | head -80 || echo 'NO_SSHD_CONFIG')\necho '---FAIL2BAN_CONF---'\n(grep -iE 'maxretry|findtime|bantime|ignoreip' /etc/fail2ban/jail.conf /etc/fail2ban/jail.local 2>/dev/null | head -30 || true)\n# 判定要点：shadow 可读、passwd/ssh 关键文件权限过宽或属主异常、.ssh/cron 全局可写、sshd 弱配置、关键文件缺失。""",
}

PROJECT_RULE_COMMANDS: Dict[str, str] = {
    "FILE_SECURITY": """# 项目文件安全巡检（只读，需替换 <deploy_path>）\nfind <deploy_path> -maxdepth 4 -type f \\( -name '*.sh' -o -name '*.exe' -o -name '*.zip' -o -name '.*' \\) -ls 2>/dev/null || true\nfind <deploy_path> -perm -0002 -ls 2>/dev/null || true\nfind <deploy_path> -type f -exec sha256sum {} \\; 2>/dev/null | sort | sha256sum\n# 判定要点：陌生脚本、隐藏文件、全局可写、文件 hash 基线变化。""",
    "CONFIG_SECURITY": """# 项目配置安全巡检（只读，需替换 <config_path>/<deploy_path>）\nfind <config_path> <deploy_path> -maxdepth 4 -type f \\( -name '*.yml' -o -name '*.yaml' -o -name '*.properties' -o -name '*.env' -o -name '*.json' -o -name '*.ini' -o -name '*nginx*' \\) -print 2>/dev/null\ngrep -RInE 'password|passwd|secret|token|access[_-]?key|secret[_-]?key|private[_-]?key|debug\\s*[:=]\\s*true|anonymous' <config_path> <deploy_path> 2>/dev/null | head -n 200 || true\n# 判定要点：明文密钥、debug 开启、匿名访问、端口/IP/白名单配置异常。""",
    "API_SECURITY": """# 项目接口日志巡检（只读，需替换 <log_path>）\ngrep -RInE ' 403 | 404 | 500 |Exception|ERROR|timeout|SQL syntax|union select|<script|\\.\\./|/etc/passwd' <log_path> 2>/dev/null | tail -n 300 || true\nawk '{print $1}' <log_path>/*.log 2>/dev/null | sort | uniq -c | sort -nr | head -n 30 || true\n# 判定要点：高频请求、4xx/5xx 激增、SQL 注入、XSS、路径遍历、越权访问。""",
    "WHITELIST_SECURITY": """# 白名单与网络权限巡检（只读）\ngrep -RInE 'allow|deny|whitelist|white_list|blacklist|ip|cidr' <config_path> <deploy_path> 2>/dev/null | head -n 200 || true\nss -ntulp 2>/dev/null || netstat -ntulp 2>/dev/null || true\n# 判定要点：白名单为空、全员放行、过期 IP、第三方废弃 IP、数据库/后台端口暴露。""",
    "CUSTOMER_SECURITY": """# 客户与运营安全巡检（只读，需结合业务日志/数据库只读接口）\ngrep -RInE 'export|delete|permission|role|merchant|customer|batch|retry|forbidden|unauthorized' <log_path> 2>/dev/null | tail -n 300 || true\n# 判定要点：客户白名单超配、高权限操作、批量提交/查询、异常注册、违规客户仍可访问。""",
    "BACKUP_SECURITY": """# 项目备份巡检（只读，需替换 <backup_path>）\nls -lah <backup_path> 2>/dev/null || true\nfind <backup_path> -maxdepth 3 -type f -mtime -2 -printf '%TY-%Tm-%Td %TH:%TM %s %p\\n' 2>/dev/null | sort | tail -n 100 || true\nfind <backup_path> -type f -size 0 -print 2>/dev/null || true\n# 判定要点：当日备份缺失、0KB/损坏文件、策略被改、异地同步失败、备份目录权限过宽。""",
    "RUNTIME_ENVIRONMENT": """# 项目运行环境巡检（只读，需替换 <main_port>/<runtime_user>/<deploy_path>）\nps -ef | grep -v grep | grep -E '<runtime_user>|<deploy_path>|java|node|python|gunicorn|uvicorn' || true\nss -ntulp 2>/dev/null | grep -E ':<main_port>\\b' || true\ndu -sh <deploy_path> <log_path> <backup_path> 2>/dev/null || true\n# 判定要点：项目进程缺失、端口未监听、运行用户不合规、日志/备份目录异常、关联服务器高危风险。""",
}

RISK_WEIGHT = {"HIGH": 15, "MEDIUM": 8, "LOW": 2, "NONE": 0}
# B7: 风险等级顺序权重（用于聚合时取最大等级），与 RISK_WEIGHT 解耦以避免互相干扰
RISK_ORDER = {"HIGH": 4, "MEDIUM": 3, "LOW": 2, "NONE": 1}
ISSUE_STATUSES = {"OPEN", "PROCESSING", "FIXED", "VERIFIED", "IGNORED"}

DEFAULT_BATCH_CONCURRENCY = int(os.getenv("INSPECTION_BATCH_CONCURRENCY", "20") or "20")
DEFAULT_BATCH_SIZE = int(os.getenv("INSPECTION_BATCH_SIZE", "71") or "71")
DEFAULT_COMMAND_TIMEOUT_SECONDS = int(os.getenv("INSPECTION_COMMAND_TIMEOUT_SECONDS", "20") or "20")
DEFAULT_RUN_TIMEOUT_SECONDS = int(os.getenv("INSPECTION_RUN_TIMEOUT_SECONDS", "180") or "180")
MAX_BATCH_CONCURRENCY = int(os.getenv("INSPECTION_MAX_BATCH_CONCURRENCY", "71") or "71")
MAX_BATCH_SIZE = int(os.getenv("INSPECTION_MAX_BATCH_SIZE", "71") or "71")
MAX_COMMAND_TIMEOUT_SECONDS = int(os.getenv("INSPECTION_MAX_COMMAND_TIMEOUT_SECONDS", "120") or "120")
MAX_RUN_TIMEOUT_SECONDS = int(os.getenv("INSPECTION_MAX_RUN_TIMEOUT_SECONDS", "1800") or "1800")
# 僵尸巡检回收：run 建库时即为 RUNNING，进程重启（部署）或后台任务异常会把它永久留在
# "执行中"。判定分两步：自身超过 INSPECTION_STALE_RUN_SECONDS 无更新，
# 且最近 INSPECTION_ACTIVE_HEARTBEAT_SECONDS 内没有任何 run 在推进（执行器已不在跑）。
INSPECTION_STALE_RUN_SECONDS = int(os.getenv("INSPECTION_STALE_RUN_SECONDS", str(2 * MAX_RUN_TIMEOUT_SECONDS + 1800)) or "5400")
INSPECTION_ACTIVE_HEARTBEAT_SECONDS = int(os.getenv("INSPECTION_ACTIVE_HEARTBEAT_SECONDS", "300") or "300")
NON_TERMINAL_RUN_STATUSES = ("RUNNING", "PENDING")
ACTIVE_SERVER_STATUSES = {"", "online", "enabled", "active", "ready", "up", "running"}
DISABLED_SERVER_STATUSES = {"disabled", "disable", "stopped", "stop", "offline", "inactive", "decommissioned", "停用", "离线"}

SENSITIVE_PATTERNS = [
    (re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*([^\s,;]+)"), r"\1=******"),
    (re.compile(r"(?i)(secret|token|access[_-]?key|secret[_-]?key|private[_-]?key)\s*[:=]\s*([^\s,;]+)"), r"\1=******"),
    (re.compile(r"(?i)(authorization|cookie)\s*[:=]\s*([^\r\n]+)"), r"\1=******"),
    (re.compile(r"(?i)(jdbc:[^\s]+://[^\s:]+:[^@\s]+@)"), "jdbc:******@"),
]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_run(**kwargs) -> InspectionRun:
    """Create an InspectionRun with both current and legacy scope fields.

    Some existing SQLite databases were created with a NOT NULL
    inspection_runs.scope_kind column before the current scope_type model was
    introduced. Always writing both values prevents NOT NULL failures and keeps
    old data/query tooling compatible.
    """
    scope = str(kwargs.get("scope_type") or kwargs.get("scope_kind") or SERVER_SCOPE).upper()
    kwargs["scope_type"] = scope
    kwargs["scope_kind"] = scope
    return InspectionRun(**kwargs)


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


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _server_status(server: Optional[Dict[str, Any]]) -> str:
    if not server:
        return "online"
    raw = server.get("status")
    if raw is None:
        raw = server.get("enabled")
    if isinstance(raw, bool):
        return "online" if raw else "disabled"
    text = str(raw or "online").strip().lower()
    if text in {"enable", "enabled", "active", "online", "ready", "running", "up"}:
        return "online"
    if text in DISABLED_SERVER_STATUSES:
        return "disabled" if text not in {"offline", "离线"} else "offline"
    return text or "online"


def _server_status_label(status: str) -> str:
    normalized = _server_status({"status": status})
    if normalized == "online":
        return "在线/启用"
    if normalized == "disabled":
        return "停用"
    if normalized == "offline":
        return "离线"
    return normalized


def _is_server_inspectable(server: Optional[Dict[str, Any]]) -> bool:
    return _server_status(server) in ACTIVE_SERVER_STATUSES


def _chunked(values: List[Any], size: int) -> List[List[Any]]:
    if size <= 0:
        return [values]
    return [values[i:i + size] for i in range(0, len(values), size)]


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
    parsed_facts: Optional[Dict[str, Any]] = None


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
        parsed_facts=result.parsed_facts,
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


def _run_remote(server_name: str, command: str, timeout: int = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> Dict[str, Any]:
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
        # F5 修复：SSH 返回空 stdout 时记录 warning，避免数据采集中断被静默吞掉
        if not (out or "").strip() and not (err or "").strip():
            try:
                import logging
                logging.getLogger("ops_ai.inspection").warning(
                    "[inspection] empty output: server=%s command_prefix=%r duration_ms=%d",
                    server_name, command[:120], int((time.time() - started) * 1000),
                )
            except Exception:
                pass
        return {"exit_code": code, "stdout": out or "", "stderr": err or "", "duration_ms": int((time.time() - started) * 1000)}
    finally:
        try:
            ssh.close()
        except Exception:
            pass


def _remote_check(server_name: str, category: str, item_code: str, item_name: str, command: str, analyze, timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS, thresholds: Optional[Dict[str, Any]] = None) -> CheckResult:
    try:
        result = _run_remote(server_name, command, timeout=timeout_seconds)
        output = f"$ {command}\nexit={result.get('exit_code')} duration_ms={result.get('duration_ms')}\n{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
        try:
            import inspect
            sig = inspect.signature(analyze)
            if "thresholds" in sig.parameters:
                analyze_result = analyze(result.get("stdout") or "", result.get("stderr") or "", result.get("exit_code"), thresholds)
            else:
                analyze_result = analyze(result.get("stdout") or "", result.get("stderr") or "", result.get("exit_code"))
        except TypeError:
            # Backward compatibility: analyzer without thresholds param
            analyze_result = analyze(result.get("stdout") or "", result.get("stderr") or "", result.get("exit_code"))
        # Support both 4-tuple (old) and 5-tuple (new with parsed_facts)
        if len(analyze_result) == 5:
            risk_level, status, message, suggestion, parsed_facts = analyze_result
        else:
            risk_level, status, message, suggestion = analyze_result
            parsed_facts = None
        return CheckResult(category, item_code, item_name, status, risk_level, message, suggestion, output, command, "COMMAND", parsed_facts)
    except TimeoutError as exc:
        return CheckResult(category, item_code, item_name, "ERROR", "MEDIUM", f"巡检项执行超时：{exc}", "缩小巡检范围或提高命令超时时间；确认服务器响应和 SSH 连接稳定。", str(exc), command)
    except Exception as exc:
        message = str(exc)
        if "timed out" in message.lower() or "timeout" in message.lower():
            return CheckResult(category, item_code, item_name, "ERROR", "MEDIUM", f"巡检项执行超时：{exc}", "缩小巡检范围或提高命令超时时间；确认服务器响应和 SSH 连接稳定。", str(exc), command)
        return CheckResult(category, item_code, item_name, "ERROR", "MEDIUM", f"巡检项执行失败：{exc}", "检查服务器连接、认证凭据与命令兼容性。", str(exc), command)





_DANGEROUS_SHELL_RE = re.compile(
    r"(?ix)"
    r"(\brm\b\s+-|\bmv\b\s+|\bcp\b\s+|\bchmod\b\s+|\bchown\b\s+|\bkill\b\s+|\bpkill\b\s+|\breboot\b|\bshutdown\b|"
    r"\bpoweroff\b|\binit\s+[06]\b|\biptables\b\s+-(F|A|I|D|X|P)|\bfirewall-cmd\b.*--(add|remove|reload|panic)|"
    r"\bufw\b\s+(allow|deny|delete|enable|disable)|\bsystemctl\b\s+(start|stop|restart|reload|enable|disable|mask|unmask)|"
    r"\bservice\b\s+\S+\s+(start|stop|restart|reload)|\bmysql\b\s+.*\b(update|delete|insert|drop|truncate|alter)\b|"
    r"\bredis-cli\b\s+.*\bflushall\b|\bmkfs\b|\bdd\b\s+if=|>\s*/etc/|>>\s*/etc/|"
    # 第 6 轮加固：原黑名单存在语法级绕过（关键字拆引号、${IFS}）与常见破坏命令漏项。
    # 逐条都要求"命令词 + 参数"，避免误伤只读用法（如 cat /etc/passwd、/proc/mounts、
    # find -printf/-ls/-exec tail、ps -ef | egrep a|b|c 这类只读统计）。
    r"\brm\b\s+(-{1,2}\w|[/~$.])|"
    r"\bfind\b[^\n]*\s-(delete|ok|okdir)\b|"
    r"\bfind\b[^\n]*\s-exec(dir)?\s+(sudo\s+)?(rm|mv|cp|chmod|chown|sh|bash|python[0-9.]*|perl|dd|truncate|sed|shred|wipefs)\b|"
    r"\bsed\b[^\n]*\s-i(\s|$)|"
    r"\btruncate\b\s|\bshred\b\s|\bwipefs\b|\bfdisk\b|\bparted\b|\bmdadm\b|\bchattr\b\s|"
    r"\bcrontab\b\s+-r\b|\bhistory\b\s+-c\b|(?<![\w/.-])userdel\b|\bgroupdel\b|\bchpasswd\b|(?<![\w/.-])passwd\s|"
    r"\bmount\b\s|\bumount\b\s|\bswapon\b|\bswapoff\b|\bmodprobe\b|\binsmod\b|\brmmod\b|\bsetenforce\b|"
    r"\s\|\s*(sudo\s+)?(ba|z|k|da)?sh\b|\s\|\s*(python[0-9.]*|perl|ruby|node)\b|"
    r"\b(python[0-9.]*|perl|ruby|node|php)\s+-(c|e)\b)"
)

# 关键字拆分绕过（st"op" / rebo\ot / rm${IFS}-rf）在"扫描文本"里做归一，
# 不改真正下发的命令；同时剔除注释行，避免注释里的示例被误判。
_SHELL_QUOTE_CHARS = str.maketrans("", "", "\"'\\")
_IFS_RE = re.compile(r"\$\{?IFS\}?", re.I)


def _normalize_for_scan(text: str) -> str:
    normalized = _IFS_RE.sub(" ", str(text or ""))
    return normalized.translate(_SHELL_QUOTE_CHARS)


def _sanitize_rule_shell(command: str) -> tuple[str, Optional[str]]:
    """Normalize a rule shell script and reject obvious destructive commands.

    The巡检中心第一阶段只允许只读巡检。规则内容可以是多行 shell，支持
    管道、grep/find/awk/tail/ss/df 等只读命令，但会拒绝删除、修改、重启、
    防火墙变更、DML 等高风险关键字。

    局限（有意为之，非安全边界）：这是**黑名单**策略，只能拦住"明显破坏性"写法，
    无法证明一条规则真的只读。真正的边界是"谁能编写规则"（管理端 + 审计）。
    第 6 轮加固内容：
      1. 扫描前把 ``$IFS``/``${IFS}`` 归一为空格并去掉引号/反斜杠，封堵
         ``st"op"``、``rebo\\ot``、``rm${IFS}-rf`` 这类语法级绕过；
      2. 补齐原漏项：``rm <路径>``、``find -delete/-exec``、``sed -i``、
         ``truncate``、``crontab -r``、``history -c``、``userdel``、``passwd``、
         ``mount/umount``、管道执行（``| sh``）与解释器 ``-c/-e`` 等。
    """
    cmd = str(command or "").replace("\r\n", "\n").strip()
    if not cmd:
        return "", "规则内容为空，无法生成可执行命令。"
    scan_text = _normalize_for_scan("\n".join(line for line in cmd.splitlines() if not line.strip().startswith("#")))
    if _DANGEROUS_SHELL_RE.search(scan_text):
        return cmd, "规则命令包含疑似高风险写入/变更操作，已按只读巡检策略拦截。"
    return cmd, None


def _extract_config_commands(config: Any) -> List[str]:
    if not isinstance(config, dict):
        return []
    value = config.get("commands") or config.get("shell") or config.get("cmd")
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _first_command_line(command: str) -> str:
    lines = [x.strip() for x in str(command or "").splitlines() if x.strip() and not x.strip().startswith("#")]
    return lines[0] if lines else ""


def _replace_project_tokens(command: str, project: Optional[Dict[str, Any]] = None) -> tuple[str, Optional[str]]:
    if not project:
        return command, None
    replacements = {
        "<deploy_path>": project.get("deploy_path") or "",
        "<config_path>": project.get("config_path") or project.get("deploy_path") or "",
        "<log_path>": project.get("log_path") or "",
        "<backup_path>": project.get("backup_path") or "",
        "<main_port>": project.get("main_port") or "",
        "<runtime_user>": project.get("runtime_user") or "",
    }
    missing = [key for key, value in replacements.items() if key in command and not str(value).strip()]
    rendered = command
    for token, value in replacements.items():
        # Paths and scalar values come from project_server_relations / project config;
        # quote them before injecting into shell snippets.
        safe_value = shlex.quote(str(value)) if str(value).strip() else token
        rendered = rendered.replace(token, safe_value)
    if missing:
        return rendered, "项目部署信息缺失：" + "、".join(missing) + "。请先在项目巡检页配置部署路径/日志路径/备份路径/主端口/运行用户。"
    return rendered, None


def _analyzer_for_category(category: str):
    cat = str(category or "").upper()
    mapping = {
        "LOGIN_SECURITY": _analyze_login,
        "ACCOUNT_SECURITY": _analyze_accounts,
        "COMMAND_HISTORY": _analyze_history,
        "PROCESS_PORT": _analyze_process_ports,
        "FIREWALL": _analyze_firewall,
        "DISK": _analyze_disk,
        "SERVICE_STATUS": _analyze_service,
        "BACKUP": _analyze_backup,
        "MEMORY": _analyze_memory,
        "FAIL2BAN": _analyze_fail2ban,
        "AUDITD": _analyze_auditd,
        "KEY_FILE_SECURITY": _analyze_key_file_security,
        "FILE_SECURITY": _analyze_project_files,
        "CONFIG_SECURITY": _analyze_project_api,
        "API_SECURITY": _analyze_project_api,
        "WHITELIST_SECURITY": _analyze_process_ports,
        "CUSTOMER_SECURITY": _analyze_project_api,
        "BACKUP_SECURITY": _analyze_project_backup,
        "RUNTIME_ENVIRONMENT": _analyze_project_runtime,
        "CUSTOM": _analyze_custom_command,
    }
    return mapping.get(cat, _analyze_custom_command)


def _rule_execution_specs(db: Session, *, scope_type: str, categories: Iterable[str], project: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    selected = {str(x).upper() for x in (categories or [])}
    if not selected:
        selected = {c["code"] for c in (SERVER_CATEGORIES if scope_type == SERVER_SCOPE else PROJECT_CATEGORIES)}
    try:
        rules = list_rules(db, scope_type=scope_type, enabled=True).get("items", [])
    except Exception:
        rules = _all_builtin_rules()
    specs: List[Dict[str, Any]] = []
    for rule in rules:
        category = str(rule.get("category") or "").upper()
        if category not in selected:
            continue
        if not bool(rule.get("enabled", True)) or bool(rule.get("deleted", False)):
            continue
        content = str(rule.get("rule_content") or "").strip()
        if not content:
            commands = _extract_config_commands(rule.get("config"))
            content = "\n".join(commands)
        if not content:
            continue
        rendered, missing_reason = _replace_project_tokens(content, project)
        rendered, blocked_reason = _sanitize_rule_shell(rendered)
        specs.append({
            "category": category,
            "item_code": str(rule.get("rule_code") or category).upper(),
            "item_name": str(rule.get("rule_name") or category),
            "command": rendered,
            "analyze": _analyzer_for_category(category),
            "risk_level": str(rule.get("risk_level") or "MEDIUM").upper(),
            "blocked_reason": blocked_reason or missing_reason,
            "description": rule.get("description") or "",
            "suggestion": rule.get("suggestion") or "",
        })
    return specs


def _start_progress_item(db: Session, run: InspectionRun, *, category: str, item_code: str, item_name: str, command: str) -> InspectionItemResult:
    command_text = _mask(command, limit=12000)
    ev = InspectionEvidence(
        id=uuid4().hex,
        run_id=run.id,
        source_type="COMMAND",
        command=command_text,
        content_snapshot=f"$ {command_text}\n# 等待执行...",
        content_hash=_hash(command_text),
        masked=True,
        created_at=_now(),
    )
    db.add(ev)
    db.flush()
    row = InspectionItemResult(
        id=uuid4().hex,
        run_id=run.id,
        scope_type=run.scope_type,
        server_id=run.server_id,
        project_id=run.project_id,
        category=category,
        item_code=item_code,
        item_name=item_name,
        status="RUNNING",
        risk_level="NONE",
        message="正在执行巡检步骤，Shell 命令已生成，可在执行过程查看。",
        suggestion="等待命令执行完成后查看判定结果。",
        evidence_id=ev.id,
        raw_output=f"$ {command_text}\n# RUNNING",
        started_at=_now(),
        created_at=_now(),
    )
    db.add(row)
    run.summary = f"巡检执行中：正在执行 {item_name}。"
    run.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def _finish_progress_item(db: Session, run: InspectionRun, row: InspectionItemResult, result: CheckResult) -> InspectionItemResult:
    output = _mask(result.evidence or result.message)
    row.status = result.status
    row.risk_level = result.risk_level
    row.message = result.message
    row.suggestion = result.suggestion
    row.raw_output = output
    row.parsed_facts = result.parsed_facts
    row.finished_at = _now()
    if row.evidence_id:
        ev = db.query(InspectionEvidence).filter(InspectionEvidence.id == row.evidence_id).first()
        if ev:
            ev.command = _mask(result.command or ev.command or "", limit=12000)
            ev.content_snapshot = output
            ev.content_hash = _hash(output)
            ev.masked = True
    if result.risk_level in {"HIGH", "MEDIUM", "LOW"} and result.status in {"RISK", "WARNING", "ERROR"}:
        exists = db.query(InspectionIssue).filter(InspectionIssue.item_result_id == row.id).first()
        if not exists:
            issue = InspectionIssue(
                id=uuid4().hex,
                run_id=run.id,
                item_result_id=row.id,
                scope_type=run.scope_type,
                server_id=run.server_id,
                project_id=run.project_id,
                title=f"{result.item_name}：{result.message[:80]}",
                description=result.message,
                risk_level=result.risk_level,
                status="OPEN",
                suggestion=result.suggestion,
                evidence_id=row.evidence_id,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(issue)
    _append_progress_counts_only(db, run)
    db.refresh(row)
    return row


def _blocked_rule_result(spec: Dict[str, Any]) -> CheckResult:
    return CheckResult(
        spec["category"],
        spec["item_code"],
        spec["item_name"],
        "SKIPPED",
        "LOW",
        spec.get("blocked_reason") or "规则命令未通过只读安全校验，已跳过执行。",
        "请修改规则内容，仅保留只读 shell 命令后重试。",
        f"$ {spec.get('command') or ''}\n# SKIPPED: {spec.get('blocked_reason') or ''}",
        spec.get("command") or "",
    )

def _server_check_specs(server_name: str, categories: Iterable[str]) -> List[Dict[str, Any]]:
    """Return ordered server checker specs.

    The executor uses these specs one by one so item results are committed after
    every check and the front-end can poll the run detail as real-time progress.

    内置 12 大类有 hardcoded command + analyze；用户通过 InspectionRule 自定义的
    category 会通过 DB 中 InspectionItemConfig 关联的 InspectionRule.rule_content
    动态构建 spec，确保新增规则立即可在巡检项中选用并执行。
    """
    builtin_codes = {c["code"] for c in SERVER_CATEGORIES}
    selected = set(categories or [c["code"] for c in SERVER_CATEGORIES])
    specs: List[Dict[str, Any]] = []
    for code in selected:
        if code in builtin_codes:
            continue  # 由下面的 hardcoded 分支处理
        # 动态从 DB 中查找自定义规则的命令（同一分类可挂多条规则，全部纳入）
        specs.extend(_build_custom_rule_specs(code, scope=SERVER_SCOPE))
    if "LOGIN_SECURITY" in selected:
        specs.append({
            "category": "LOGIN_SECURITY",
            "item_code": "SERVER_LOGIN_RECENT",
            "item_name": "近期登录与失败登录检查",
            "execution": "收集 last 近期成功登录（最多 80 条）+ lastb 过去 24 小时失败登录（最多 20 条，兼容降级为 lastb -n 20）",
            "criteria": "判定标准：24小时内失败登录 ≥10 触发 HIGH；≥3 触发 LOW；root 远程登录触发 MEDIUM（可配置）；其它情况通过。",
            "command": "(last -n 80 2>/dev/null || true); echo '---FAILED---'; (lastb --since \"24 hours ago\" 2>/dev/null | head -20 || lastb -n 20 2>/dev/null || true)",
            "analyze": _analyze_login,
        })
    if "ACCOUNT_SECURITY" in selected:
        specs.append({
            "category": "ACCOUNT_SECURITY",
            "item_code": "SERVER_ACCOUNT_PRIVILEGE",
            "item_name": "系统账号与特权账号检查",
            "execution": "解析 /etc/passwd 提取 UID=0 账号与可登录 shell 账号；解析 /etc/shadow 检测空密码账号",
            "criteria": "判定标准：UID=0 账号 >1 触发 HIGH；空密码账号触发 HIGH；可登录账号 >20 触发 MEDIUM；>10 触发 LOW；其它情况通过。",
            "command": "echo '---PASSWD---'; cat /etc/passwd 2>/dev/null | head -200; echo '---UID0---'; grep 'x:0:' /etc/passwd 2>/dev/null || true; echo '---SHADOW---'; (cat /etc/shadow 2>/dev/null | head -200 || echo 'PERMISSION_DENIED'); echo '---SHADOW_ERR---'; (cat /etc/shadow 2>/dev/null >/dev/null 2>&1 && echo 'OK' || echo 'ERR')",
            "analyze": _analyze_accounts,
        })
    if "COMMAND_HISTORY" in selected:
        specs.append({
            "category": "COMMAND_HISTORY",
            "item_code": "SERVER_HISTORY_DANGEROUS",
            "item_name": "高危命令历史检查",
            "execution": "扫描 ~/.bash_history 与 /root/.bash_history 中是否存在高危命令关键词（精确匹配）与管道攻击组合（curl/wget + |bash/|sh）；cat/less/tail /etc/shadow 视为 HIGH",
            "criteria": "判定标准：发现 history -c 触发 HIGH；rm -rf / / chmod 777 / > /dev/sd 触发 HIGH；curl/wget 管道到 shell 触发 HIGH；cat/less/tail /etc/shadow 触发 HIGH；其它危险命令触发 MEDIUM；其它情况通过。",
            "command": "echo '---HISTORY---'; (cat ~/.bash_history 2>/dev/null || true); echo '---ROOT---'; (cat /root/.bash_history 2>/dev/null || true); echo '---HISTORY---'",
            "analyze": _analyze_history,
        })
    if "PROCESS_PORT" in selected:
        specs.append({
            "category": "PROCESS_PORT",
            "item_code": "SERVER_PROCESS_PORT",
            "item_name": "进程与监听端口检查",
            "execution": "ps 取 CPU TOP 30 进程；ss/netstat 取全部监听端口；解析协议、绑定地址、进程名、PID；支持 cpu_sample_count>1 时的连续 2 次采样",
            "criteria": "判定标准：发现 xmrig/kinsing/minerd/kdevtmpfsi 等挖矿特征触发 HIGH；高危端口（3306/6379/27017/9200/11211/5432）对 0.0.0.0/::/* 暴露触发 MEDIUM；中危端口（22/23/445/3389/5900/8080 等）暴露触发 LOW；单进程 CPU≥80% 触发 MEDIUM（连续 2 次采样）。",
            "command": "echo '---TOP---'; ps -eo pid,ppid,user,comm,%cpu,%mem,args --sort=-%cpu 2>/dev/null | head -30; sleep 2; echo '---TOP---'; ps -eo pid,ppid,user,comm,%cpu,%mem,args --sort=-%cpu 2>/dev/null | head -30; echo '---PORTS---'; (ss -ntulp 2>/dev/null || netstat -ntulp 2>/dev/null || true)",
            "analyze": _analyze_process_ports,
        })
    if "FIREWALL" in selected:
        specs.append({
            "category": "FIREWALL",
            "item_code": "SERVER_FIREWALL_STATUS",
            "item_name": "防火墙状态检查",
            "execution": "检测 firewalld/ufw 状态与 iptables 规则（前 100 条），按行解析全局放行规则",
            "criteria": "判定标准：单条规则 -p all 且 0.0.0.0/0 触发 HIGH；防火墙 inactive 触发可配置等级（默认 MEDIUM，云环境可设为 LOW）；默认 INPUT 策略 ACCEPT 触发 LOW。",
            "command": "(systemctl is-active firewalld 2>/dev/null || true); (ufw status 2>/dev/null || true); (iptables -S 2>/dev/null | head -100 || true)",
            "analyze": _analyze_firewall,
        })
    if "DISK" in selected:
        specs.append({
            "category": "DISK",
            "item_code": "SERVER_DISK_USAGE",
            "item_name": "磁盘空间与 inode 使用率检查",
            "execution": "df -PTh 与 df -iPTh 两段式采集：空间按 fstype 过滤（跳过 overlay/squashfs/tmpfs），NFS ≥99% 单独识别为 stale",
            "criteria": "判定标准：空间 ≥90% → HIGH；≥75% → MEDIUM；inode ≥90% → HIGH；≥80% → MEDIUM；NFS≥99% → MEDIUM 单独提示。",
            "command": "echo '---SPACE---'; (df -PTh 2>/dev/null || df -hT 2>/dev/null || true) | head -100; echo '---INODE---'; (df -iPTh 2>/dev/null || df -ihT 2>/dev/null || true) | head -100",
            "analyze": _analyze_disk,
        })
    if "MEMORY" in selected:
        specs.append({
            "category": "MEMORY",
            "item_code": "SERVER_MEMORY_USAGE",
            "item_name": "内存与 Swap 使用率检查",
            "execution": "free 命令取内存与 Swap；同时输出 /proc/meminfo 用于 MemAvailable 算法（排除 buff/cache）",
            "criteria": "判定标准：内存 ≥95% → HIGH；≥85% → MEDIUM；Swap ≥80% → HIGH；≥50% → MEDIUM；其它情况通过。",
            "command": "(free 2>/dev/null || free -h 2>/dev/null || true); echo '---MEMINFO---'; (cat /proc/meminfo 2>/dev/null | head -10 || true)",
            "analyze": _analyze_memory,
        })
    if "SERVICE_STATUS" in selected:
        specs.append({
            "category": "SERVICE_STATUS",
            "item_code": "SERVER_SERVICE_STATUS",
            "item_name": "基础服务、PM2 与 Docker 进程检查",
            "execution": "systemctl --failed + watch_services systemctl is-active + PM2 (jlist/ping) 状态 + etcd 集群健康 + Docker 容器列表 + ps 关键字扫描（非 systemd 进程）",
            "criteria": "判定标准：PM2 自身 ping 失败 → HIGH；PM2 进程 errored → HIGH（已由 Docker 托管的同名服务除外）；PM2 进程 stopped（含 watch_services 期望）→ MEDIUM；etcd 集群 unhealthy → HIGH；Docker 容器 Restarting → MEDIUM、Exited → LOW；systemd 失败单元 ≥ 阈值 → HIGH，< 阈值 → MEDIUM；watch_services 中任一非 active → LOW（信息性）；caddy/非 systemd 关键字 ps 中缺失且非 Docker 容器托管 → MEDIUM。",
            "command": (
                "echo '---SYSTEMD_FAILED---'; "
                "(systemctl --failed --no-pager 2>/dev/null || true); "
                "echo '---SYSTEMD_ACTIVE---'; "
                "(for s in nginx caddy docker redis redis-server mysql mysqld postgresql pm2-node etcd; do "
                "  printf '%s: %s\\n' \"$s\" \"$(systemctl is-active $s 2>/dev/null || echo 'unknown')\"; "
                "done); "
                "echo '---PM2_JLIST---'; "
                "(pm2 jlist 2>/dev/null || echo 'PM2_NOT_FOUND'); "
                "echo '---PM2_PING---'; "
                "(pm2 ping 2>/dev/null && echo 'PM2_PING_OK' || echo 'PM2_PING_FAIL'); "
                "echo '---ETCD_HEALTH---'; "
                "(etcdctl endpoint health --cluster 2>/dev/null || etcdctl endpoint health 2>/dev/null || echo 'ETCD_NOT_FOUND'); "
                "echo '---DOCKER_PS---'; "
                "(docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' 2>/dev/null || echo 'DOCKER_NOT_FOUND'); "
                "echo '---PROCESS_KEYWORDS---'; "
                "(ps -eo pid,user,comm --no-headers 2>/dev/null | egrep -i 'nginx|caddy|mysql|postgres|redis|etcd|pm2' | head -50 || true)"
            ),
            "analyze": _analyze_service,
        })
    if "BACKUP" in selected:
        specs.append({
            "category": "BACKUP",
            "item_code": "SERVER_BACKUP_STATUS",
            "item_name": "备份任务与备份文件检查",
            "execution": "crontab -l + /etc/cron.*/ + /etc/cron.d/ + systemctl list-timers 中过滤 backup/dump/tar/rsync/mysqldump 任务；find 备份路径近 N 天文件",
            "criteria": "判定标准：无 cron/timer 且无文件触发 HIGH；无 cron 有文件触发 MEDIUM；有 cron 无文件触发 MEDIUM；0字节文件触发 MEDIUM；都有触发通过。",
            "command": "echo '---CRON---'; (crontab -l 2>/dev/null | grep -Ei 'backup|dump|tar|rsync|mysqldump' || true); echo '---CRON_D---'; (ls /etc/cron.d/ 2>/dev/null | head -30; grep -RhE 'backup|dump|tar|rsync|mysqldump' /etc/cron.d/ /etc/cron.daily/ /etc/cron.weekly/ /etc/cron.monthly/ 2>/dev/null | head -30 || true); echo '---TIMER---'; (systemctl list-timers --no-pager 2>/dev/null | head -20 || true); echo '---BACKUPS---'; (find /data/backups /backup /var/backups /data/db_backup /home/backup /srv/backup /var/lib/mysql/backup -maxdepth 3 -type f -mtime -40 -printf '%p %s %TY-%Tm-%TdT%TH:%TM\\n' 2>/dev/null | head -200 || true); echo '---NOW---'; (date -u +%s)",
            "analyze": _analyze_backup,
        })
    if "FAIL2BAN" in selected:
        specs.append({
            "category": "FAIL2BAN",
            "item_code": "SERVER_FAIL2BAN_STATUS",
            "item_name": "Fail2ban 暴力破解防护检查",
            "execution": "检测 fail2ban 二进制是否存在、服务状态、fail2ban-client status/jail 状态、jail.local 配置（maxretry/findtime/bantime/ignoreip）与当前封禁 IP",
            "criteria": "判定标准：fail2ban 二进制不存在 → HIGH（未安装）；已安装但服务未运行 → HIGH（防护未生效）；无 sshd jail → MEDIUM；maxretry>10 或 bantime 过短 → MEDIUM；已封禁 IP 数≥阈值 → LOW 提示；其余为通过。",
            "command": "echo '---SERVICE---'; (systemctl is-active fail2ban 2>/dev/null || echo 'UNKNOWN'); echo '---BIN---'; (command -v fail2ban-client 2>/dev/null || echo 'FAIL2BAN_BIN_NOT_FOUND'); echo '---STATUS---'; (fail2ban-client status 2>&1 || echo 'FAIL2BAN_DAEMON_DOWN'); echo '---JAIL_SSHD---'; (fail2ban-client status sshd 2>&1 || echo 'NO_SSHD_JAIL'); echo '---CONFIG---'; (cat /etc/fail2ban/jail.local 2>/dev/null || cat /etc/fail2ban/jail.conf 2>/dev/null || echo 'NO_FAIL2BAN_CONFIG'); echo '---BANNED---'; (fail2ban-client status sshd 2>/dev/null | grep -iE 'banned|currently failed' || true)",
            "analyze": _analyze_fail2ban,
        })
    if "AUDITD" in selected:
        specs.append({
            "category": "AUDITD",
            "item_code": "SERVER_AUDITD_STATUS",
            "item_name": "auditd 安全检查审计检查",
            "execution": "检测 auditd 服务状态、auditctl -s 审计状态、auditctl -l 审计规则、rules.d 规则文件与审计日志",
            "criteria": "判定标准：auditd 未安装/未启用或审计关闭 → HIGH；无审计规则 → HIGH；关键文件（passwd/shadow/ssh/cron/.ssh）未覆盖 → MEDIUM；危险命令审计缺失 → LOW；其余为通过。",
            "command": "echo '---SERVICE---'; (systemctl is-active auditd 2>/dev/null || echo 'UNKNOWN'); echo '---STATUS---'; (auditctl -s 2>/dev/null || echo 'AUDITCTL_NOT_FOUND'); echo '---RULES---'; (auditctl -l 2>/dev/null | head -150 || echo 'NO_RULES'); echo '---RULES_DIR---'; (ls -la /etc/audit/rules.d/ 2>/dev/null || true); (cat /etc/audit/rules.d/*.rules 2>/dev/null | head -150 || true); echo '---LOG---'; (ls -lh /var/log/audit/audit.log 2>/dev/null || echo 'NO_AUDIT_LOG')",
            "analyze": _analyze_auditd,
        })
    if "KEY_FILE_SECURITY" in selected:
        specs.append({
            "category": "KEY_FILE_SECURITY",
            "item_code": "SERVER_KEY_FILE_STATUS",
            "item_name": "关键文件权限与完整性检查",
            "execution": "stat 采集 passwd/shadow/gshadow/group/sshd_config/crontab/cron.d/.ssh/fail2ban 配置的属主与权限；解析 sshd_config 与 fail2ban 阈值",
            "criteria": "判定标准：shadow 全局可读 → HIGH；passwd/.ssh/cron 全局可写 → HIGH；关键文件属主异常 → MEDIUM；sshd 弱配置（PermitRootLogin yes 等）→ MEDIUM；关键文件缺失 → MEDIUM；其余为通过。",
            "command": "echo '---KEYFILES---'; for f in /etc/passwd /etc/shadow /etc/gshadow /etc/group /etc/ssh/sshd_config /etc/crontab /etc/cron.d /root/.ssh /etc/fail2ban/jail.local; do if [ -e \"$f\" ]; then stat -c '%n %U:%G %a %s' \"$f\" 2>/dev/null; else echo \"$f MISSING\"; fi; done; echo '---SSHD_CONFIG---'; (grep -viE '^\\s*#|^\\s*$' /etc/ssh/sshd_config 2>/dev/null | head -80 || echo 'NO_SSHD_CONFIG'); echo '---FAIL2BAN_CONF---'; (grep -iE 'maxretry|findtime|bantime|ignoreip' /etc/fail2ban/jail.conf /etc/fail2ban/jail.local 2>/dev/null | head -30 || true)",
            "analyze": _analyze_key_file_security,
        })
    return specs


def _build_custom_rule_specs(category_code: str, scope: str = SERVER_SCOPE) -> List[Dict[str, Any]]:
    """按 category 构造**该分类下所有启用规则**的 check spec（顺序 = sort_order）。

    与 ``_rule_execution_specs`` 对齐：一个巡检项可以挂多条规则，而此前这里只取
    ``InspectionItemRule`` 中 sort_order 最小的那一条，同分类的其余规则在项目组合巡检
    中被静默丢弃（run 里既没有结果行也没有错误，运维无从发现）。
    """
    from app.db.models import InspectionItemConfig, InspectionItemRule, InspectionRule as RuleModel
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        rule_codes: List[str] = []
        try:
            cfg = db.query(InspectionItemConfig).filter(
                InspectionItemConfig.item_code == category_code,
                InspectionItemConfig.scope_type == scope,
            ).first()
            if cfg:
                links = db.query(InspectionItemRule).filter(
                    InspectionItemRule.item_config_id == cfg.id,
                ).order_by(InspectionItemRule.sort_order, InspectionItemRule.id).all()
                for link in links:
                    code = str(getattr(link, "rule_code", "") or "").strip()
                    if code and code not in rule_codes:
                        rule_codes.append(code)
        except Exception:
            rule_codes = []

        # 兜底/补全：该 category 下所有启用且未删除的规则都纳入（含 scope_type=BOTH）。
        try:
            rows = db.query(RuleModel).filter(
                RuleModel.category == category_code,
                RuleModel.scope_type.in_([scope, "BOTH"]),
                RuleModel.deleted == False,  # noqa: E712
                RuleModel.enabled == True,  # noqa: E712
            ).order_by(RuleModel.rule_code).all()
            for row in rows:
                code = str(row.rule_code or "").strip()
                if code and code not in rule_codes:
                    rule_codes.append(code)
        except Exception:
            pass

        specs: List[Dict[str, Any]] = []
        for code in rule_codes:
            spec = _spec_from_rule_code(category_code, code)
            if spec:
                specs.append(spec)
        return specs
    finally:
        try:
            db.close()
        except Exception:
            pass


def _build_custom_rule_spec(category_code: str, scope: str = SERVER_SCOPE) -> Optional[Dict[str, Any]]:
    """（兼容保留）返回该 category 的第一条规则 spec；批量场景请用 ``_build_custom_rule_specs``。"""
    specs = _build_custom_rule_specs(category_code, scope=scope)
    return specs[0] if specs else None


def _spec_from_rule_code(category_code: str, rule_code: str) -> Optional[Dict[str, Any]]:
    """根据 rule_code 在 DB 中查找 InspectionRule 并构建 spec。"""
    from app.db.models import InspectionRule as RuleModel
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        try:
            rule = db.query(RuleModel).filter(
                RuleModel.rule_code == rule_code,
                RuleModel.deleted == False,  # noqa: E712
                RuleModel.enabled == True,  # noqa: E712
            ).first()
        except Exception:
            return None
        if not rule:
            return None
        rule_config = rule.config_json if isinstance(rule.config_json, dict) else {}
        command = _extract_command_from_content(rule.rule_content or "")
        if not command:
            # 与 _rule_execution_specs 对齐：命令也可能存放在 config.commands/shell/cmd。
            # 此前只读 rule_content，导致这类规则在组合巡检里被静默跳过（连结果行都没有），
            # 而单机/批量巡检却会正常执行它。
            command = "\n".join(_extract_config_commands(rule_config)).strip()
        if not command:
            return None
        # 与 _rule_execution_specs 保持同一只读策略：DB 自定义规则的命令必须过净化器，
        # 否则"项目组合巡检"会绕过只读校验直接执行规则内容（同一策略两个执行路径行为不一致）。
        sanitized, blocked_reason = _sanitize_rule_shell(command)
        return {
            "category": category_code,
            "item_code": f"SERVER_CUSTOM_{rule_code}",
            "item_name": rule.rule_name or category_code,
            "execution": rule.description or "执行 InspectionRule 中保存的 shell 命令",
            "criteria": rule.suggestion or "退出码/关键字由通用 analyzer 判定",
            "command": sanitized,
            "analyze": make_custom_rule_analyzer(rule_config, rule.risk_level or "MEDIUM"),
            "rule_config": rule_config,
            "blocked_reason": blocked_reason,
        }
    except Exception:
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass


def _server_checkers(server_name: str, categories: Iterable[str], thresholds: Optional[Dict[str, Any]] = None) -> List[CheckResult]:
    checks: List[CheckResult] = []
    for spec in _server_check_specs(server_name, categories):
        if spec.get("blocked_reason"):
            # 未通过只读安全校验的规则一律不执行，与 execute_server_inspection_run
            # 的 blocked_reason 语义一致（此前组合巡检会直接执行）。
            checks.append(_blocked_rule_result(spec))
            continue
        checks.append(_remote_check(server_name, spec["category"], spec["item_code"], spec["item_name"], spec["command"], spec["analyze"], thresholds=thresholds))
    return checks

# ============================================================
# 阈值配置：5 个 analyzer 的可量化阈值
# ============================================================

DEFAULT_THRESHOLDS: Dict[str, Any] = {
    "DISK": {
        "high_pct": 90,            # 空间使用率达到/超过 90% 视为 HIGH
        "medium_pct": 75,          # 空间使用率达到/超过 75% 视为 MEDIUM
        "inode_high_pct": 90,      # inode 使用率达到/超过 90% 视为 HIGH
        "inode_medium_pct": 80,    # inode 使用率达到/超过 80% 视为 MEDIUM
        "system_mounts": ["/", "/boot", "/boot/efi", "/root"],
        "system_pct_offset": 5,
        # P0-4: 容器/伪文件系统跳过，避免 overlay/squashfs 100% 误报
        "skip_fstypes": [
            "overlay", "overlayfs", "squashfs", "tmpfs", "devtmpfs",
            "proc", "sysfs", "cgroup", "cgroup2", "ramfs", "autofs",
            "fuse.gvfsd-fuse", "fuse.snapfuse",
        ],
        # NFS ≥99% 大概率是 stale mount，单独识别（不参与 max_pct 评分）
        "nfs_stale_threshold_pct": 99,
    },
    "BACKUP": {
        "backup_paths": [
            "/data/backups", "/backup", "/var/backups",
            "/data/db_backup", "/home/backup", "/srv/backup",
            "/var/lib/mysql/backup",
        ],
        "min_age_days": 1,         # 兼容旧字段；P2-3 引入按粒度版本
        "min_age_days_daily": 1,
        "min_age_days_weekly": 8,
        "min_age_days_monthly": 32,
        "scan_system_cron": True,
        "scan_systemd_timer": True,
    },
    "LOGIN_SECURITY": {
        # P1-3: 双指标 - 单 IP 失败 / 总数失败
        "failed_high_per_ip": 50,  # 单 IP 失败 ≥50 触发 HIGH
        "failed_high_total": 200,  # 总失败 ≥200 触发 HIGH（兜底）
        "failed_low_total": 30,    # 总失败 ≥30 触发 LOW
        "failed_window_hours": 24,
        "root_remote_warn": True,  # 默认仅 LOW
        "root_remote_ip_whitelist": [],  # 在白名单内的 root 登录不告警
        "degraded_command_note": True,  # lastb --since 失败时在 facts 标记降级
        # 兼容老字段（保留但不再使用，新代码用 per_ip/total）
        "failed_high": 10,
        "failed_low": 3,
        "root_remote_medium": True,
    },
    "PROCESS_PORT": {
        # P1-2: 端口风险分级
        "high_risk_ports_high":   [27017, 6379, 11211, 9200, 5432, 3306],
        "high_risk_ports_medium": [22, 23, 445, 3389, 5900, 8080, 8000, 8888, 9000],
        "suspicious_keywords":    ["xmrig", "kinsing", "minerd", "kdevtmpfsi", "perfctl", "c3pool", "tsm", "masscan"],
        "top_cpu_n": 10,
        "cpu_threshold": 80,
        "cpu_sample_count": 2,    # P2-2: 连续 N 次采样均 ≥阈值才计入
        # 兼容老字段
        "high_risk_ports": [3306, 6379, 27017, 9200, 11211, 5432, 23, 445, 3389, 5900],
    },
    "ACCOUNT_SECURITY": {
        "max_uid0": 1,             # UID=0 账号超过 N 触发 HIGH
        "max_login_users": 10,     # 可登录账号超过 N 触发 LOW
        "max_login_users_medium": 20, # 可登录账号超过 N 触发 MEDIUM
        "check_empty_password": True,  # 检查空密码账号
        "idle_days": 90,           # 超过 N 天未登录视为闲置账号
        "uid0_whitelist": ["root"],  # P3-3: 兼容白名单
    },
    "COMMAND_HISTORY": {
        # P2-1: 移除 "/etc/passwd", "/etc/shadow" 等自伤关键字
        "danger_keywords": [
            "rm -rf /", "history -c", "chmod 777", "chown root",
            "> /dev/sd", "dd if=", "iptables -F", "kill -9",
            "reboot", "shutdown",
        ],
        "pipe_combos": [["curl", "| bash"], ["curl", "| sh"], ["wget", "| bash"], ["wget", "| sh"]],
        "high_keywords": ["rm -rf /", "chmod 777", "history -c", "> /dev/sd"],
        # B8: 死字段清理——sensitive_read_re 改为 shadow_read_re，语义更准确且被实际使用
        "shadow_read_re": r"\b(cat|less|tail|head|more)\s+/etc/shadow\b",
    },
    "FIREWALL": {
        "inactive_level": "MEDIUM",  # 防火墙未启用时的风险等级（云环境可设为 LOW）
        "no_local_firewall_level": "LOW",  # P1-4: 云环境默认 LOW
    },
    "MEMORY": {
        "mem_high_pct": 95,        # 内存使用率达到/超过 95% 视为 HIGH
        "mem_medium_pct": 85,      # 内存使用率达到/超过 85% 视为 MEDIUM
        "swap_high_pct": 50,       # Swap 使用率达到/超过 50% 视为 MEDIUM
        "swap_critical_pct": 80,   # Swap 使用率达到/超过 80% 视为 HIGH
        "use_meminfo": True,       # P0-1: 优先 /proc/meminfo 的 MemAvailable
    },
    "SERVICE_STATUS": {
        # P0-2: core_services 重命名为 watch_services，不再作判定基准
        "watch_services": [
            "nginx", "caddy", "mysql", "mysqld", "mariadb",
            "postgresql", "redis", "redis-server", "pm2", "etcd", "docker",
        ],
        "failed_unit_medium_count": 3,  # 失败单元 <3 → MEDIUM；≥3 → HIGH
        # E2/E3 新增：PM2 进程管理与 etcd 健康
        "pm2_stopped_level": "MEDIUM",      # PM2 进程 stopped 时的等级（期望进程升 HIGH）
        "pm2_errored_level": "HIGH",        # PM2 进程 errored 时的等级
        "pm2_expected_processes": [
            # 默认期望由 PM2 管理的业务进程；用户可在 DB 中按需追加
            "etcd", "exchange", "exchange-02", "monitor", "promtail",
            "puller", "risk", "risk-02", "sender", "strategy", "strategy-02",
            "supplier", "system", "trader", "transaction", "transaction-02",
        ],
        "etcd_unhealthy_level": "HIGH",     # etcd 集群不健康时的等级
        # E5 新增：Docker 容器托管识别（PM2 服务迁移到 Docker 容器场景）
        "docker_restarting_level": "MEDIUM",  # Docker 容器 Restarting（崩溃循环）时的等级
        "docker_exited_level": "LOW",         # Docker 容器 Exited（非运行）时的等级
        "docker_expected_containers": [       # 期望常驻运行的容器名（可选，用于提高 Exited 判定）
            "exchange", "exchange-02", "monitor", "promtail",
            "puller", "risk", "risk-02", "sender", "strategy", "strategy-02",
            "supplier", "system", "trader", "transaction", "transaction-02",
        ],
        "process_keywords": [                # ps 输出中需检测的关键字
            "nginx", "caddy", "mysql", "postgres", "redis", "etcd", "pm2",
        ],
        # 兼容老字段
        "core_services": [
            "nginx", "caddy", "mysql", "mysqld", "mariadb",
            "postgresql", "redis", "redis-server", "pm2", "etcd",
        ],
    },
}

# ============================================================
# 阈值类型容错：避免前端误传字符串等导致整次巡检崩溃
# ============================================================

def _coerce_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _coerce_bool(v, default):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(v, (int, float)):
        return bool(v)
    return default


def _coerce_list(v, default):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return default


def _coerce_thresholds(category: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """对单一类目的阈值做类型容错。"""
    if not isinstance(raw, dict):
        return dict(DEFAULT_THRESHOLDS.get(category, {}))
    out = dict(DEFAULT_THRESHOLDS.get(category, {}))
    cat = category.upper()
    if cat == "DISK":
        for k in ("high_pct", "medium_pct", "inode_high_pct", "inode_medium_pct", "system_pct_offset", "nfs_stale_threshold_pct"):
            if k in raw:
                out[k] = _coerce_int(raw.get(k), out.get(k, 0))
        if "system_mounts" in raw:
            out["system_mounts"] = _coerce_list(raw.get("system_mounts"), out.get("system_mounts", []))
        if "skip_fstypes" in raw:
            out["skip_fstypes"] = _coerce_list(raw.get("skip_fstypes"), out.get("skip_fstypes", []))
    elif cat == "BACKUP":
        for k in ("min_age_days", "min_age_days_daily", "min_age_days_weekly", "min_age_days_monthly"):
            if k in raw:
                out[k] = _coerce_int(raw.get(k), out.get(k, 1))
        if "backup_paths" in raw:
            out["backup_paths"] = _coerce_list(raw.get("backup_paths"), out.get("backup_paths", []))
        for k in ("scan_system_cron", "scan_systemd_timer"):
            if k in raw:
                out[k] = _coerce_bool(raw.get(k), out.get(k, True))
    elif cat == "LOGIN_SECURITY":
        for k in ("failed_high", "failed_low", "failed_window_hours",
                  "failed_high_per_ip", "failed_high_total", "failed_low_total"):
            if k in raw:
                out[k] = _coerce_int(raw.get(k), out.get(k, 0))
        for k in ("root_remote_medium", "root_remote_warn", "degraded_command_note"):
            if k in raw:
                out[k] = _coerce_bool(raw.get(k), out.get(k, True))
        if "root_remote_ip_whitelist" in raw:
            out["root_remote_ip_whitelist"] = _coerce_list(raw.get("root_remote_ip_whitelist"), [])
    elif cat == "PROCESS_PORT":
        for k in ("top_cpu_n", "cpu_sample_count"):
            if k in raw:
                out[k] = _coerce_int(raw.get(k), out.get(k, 0))
        if "cpu_threshold" in raw:
            try:
                out["cpu_threshold"] = float(raw.get("cpu_threshold"))
            except (TypeError, ValueError):
                pass
        for k in ("high_risk_ports", "high_risk_ports_high", "high_risk_ports_medium", "suspicious_keywords"):
            if k in raw:
                out[k] = _coerce_list(raw.get(k), out.get(k, []))
    elif cat == "ACCOUNT_SECURITY":
        for k in ("max_uid0", "max_login_users", "max_login_users_medium", "idle_days"):
            if k in raw:
                out[k] = _coerce_int(raw.get(k), out.get(k, 0))
        if "check_empty_password" in raw:
            out["check_empty_password"] = _coerce_bool(raw.get("check_empty_password"), True)
        if "uid0_whitelist" in raw:
            out["uid0_whitelist"] = _coerce_list(raw.get("uid0_whitelist"), ["root"])
    elif cat == "COMMAND_HISTORY":
        for k in ("danger_keywords", "high_keywords", "pipe_combos"):
            if k in raw:
                out[k] = _coerce_list(raw.get(k), out.get(k, []))
        # B8: shadow_read_re 可由 DB 覆盖，必须为字符串
        if "shadow_read_re" in raw and isinstance(raw.get("shadow_read_re"), str):
            out["shadow_read_re"] = raw["shadow_read_re"]
    elif cat == "FIREWALL":
        for k in ("inactive_level", "no_local_firewall_level"):
            if k in raw and isinstance(raw.get(k), str):
                out[k] = raw[k].upper()
    elif cat == "MEMORY":
        for k in ("mem_high_pct", "mem_medium_pct", "swap_high_pct", "swap_critical_pct"):
            if k in raw:
                out[k] = _coerce_int(raw.get(k), out.get(k, 0))
        if "use_meminfo" in raw:
            out["use_meminfo"] = _coerce_bool(raw.get("use_meminfo"), True)
    elif cat == "SERVICE_STATUS":
        for k in ("watch_services", "core_services", "pm2_expected_processes", "process_keywords", "docker_expected_containers"):
            if k in raw:
                out[k] = _coerce_list(raw.get(k), out.get(k, []))
        if "failed_unit_medium_count" in raw:
            out["failed_unit_medium_count"] = _coerce_int(raw.get("failed_unit_medium_count"), 3)
        for k in ("pm2_stopped_level", "pm2_errored_level", "etcd_unhealthy_level",
                  "docker_restarting_level", "docker_exited_level"):
            if k in raw and isinstance(raw.get(k), str):
                v = raw[k].strip().upper()
                if v in {"HIGH", "MEDIUM", "LOW", "NONE"}:
                    out[k] = v
    return out


def _load_thresholds(db: Optional[Session] = None) -> Dict[str, Any]:
    """从 InspectionItemConfig.config_json 加载阈值；如果 db 为空则用默认值。

    合并策略（B5 修复）：
    - 以 DEFAULT_THRESHOLDS 为基线
    - 对每个 InspectionItemConfig.config_json.thresholds 做类型容错
    - 多个 cfg 共享同一 category 时，按 `(category, id)` 升序遍历（确定性）
    - 后到的 cfg 覆盖早到的 cfg（DB 顺序稳定即可重现）
    """
    thresholds: Dict[str, Any] = {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}
    if db is None:
        return thresholds
    try:
        # B5: 显式按 (category, id) 排序，避免依赖 SQLAlchemy 返回顺序
        rows = (
            db.query(InspectionItemConfig)
            .filter(InspectionItemConfig.enabled == True)  # noqa: E712
            .order_by(InspectionItemConfig.category.asc(), InspectionItemConfig.id.asc())
            .all()
        )
        for r in rows:
            cfg = r.config_json or {}
            t = cfg.get("thresholds") if isinstance(cfg, dict) else None
            if not t or not isinstance(t, dict):
                continue
            cat = r.category
            if not cat:
                continue
            # 对类目做容错
            coerced = _coerce_thresholds(cat, t)
            if cat not in thresholds:
                thresholds[cat] = coerced
            else:
                for k, v in coerced.items():
                    thresholds[cat][k] = v
    except Exception:
        pass
    return thresholds


def _analyze_login(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """P1-3 修复：失败登录双指标 - 单 IP 高 + 总数兜底；root 远程登录支持白名单。"""
    success_logins: List[Dict[str, Any]] = []
    attacker_map: Dict[str, Dict[str, Any]] = {}
    failed_total = 0
    in_failed = False
    degraded = False  # lastb 失败时降级标记
    for line in out.splitlines():
        s = line.strip()
        if s == "---FAILED---":
            in_failed = True
            continue
        if s.startswith("---"):
            in_failed = False
            continue
        if not s:
            continue

        if in_failed:
            failed_total += 1
            parts = s.split()
            if len(parts) >= 3:
                user = parts[0]
                ip_raw = parts[2]
                ip = ip_raw.strip("()")
                if not re.match(r'\d+\.\d+\.\d+\.\d+', ip):
                    continue
                if ip not in attacker_map:
                    attacker_map[ip] = {"ip": ip, "count": 0, "attempted_users": []}
                attacker_map[ip]["count"] += 1
                if user not in attacker_map[ip]["attempted_users"] and len(attacker_map[ip]["attempted_users"]) < 10:
                    attacker_map[ip]["attempted_users"].append(user)
        else:
            m = re.match(
                r'(\S+)\s+(\S+)\s+(\S+)\s+(.+?)\s+\((\d+:\d+|\d+\+?\d*:\d+)?\)',
                s,
            )
            if m:
                success_logins.append({
                    "user": m.group(1),
                    "tty": m.group(2),
                    "ip": m.group(3).strip("()"),
                    "start": m.group(4).strip(),
                    "duration": m.group(5) or "-",
                    "active": "still logged in" in s.lower(),
                })

    # lastb 失败时（如 /var/log/btmp 不可读）降级标记
    if (err or "").lower().startswith("lastb"):
        degraded = True

    top_attackers = sorted(attacker_map.values(), key=lambda x: x["count"], reverse=True)[:10]
    cfg = (thresholds or {}).get("LOGIN_SECURITY", {}) or {}
    # P1-3: 双指标 - 单 IP 高 + 总数兜底
    failed_high_per_ip = int(cfg.get("failed_high_per_ip", 50))
    failed_high_total = int(cfg.get("failed_high_total", 200))
    failed_low_total = int(cfg.get("failed_low_total", 30))
    failed_window_hours = int(cfg.get("failed_window_hours", 24))
    root_remote_warn = bool(cfg.get("root_remote_warn", True))
    root_remote_ip_whitelist = set(str(x).strip() for x in (cfg.get("root_remote_ip_whitelist") or []))
    # 兼容老字段（已废弃）。`failed_high`/`failed_low` 不再回退——用户必须显式给出新字段
    # （per_ip=50 / total=200 / low=30 安全默认值）才生效。
    failed_high_per_ip = int(cfg.get("failed_high_per_ip", 50))
    failed_high_total = int(cfg.get("failed_high_total", 200))
    failed_low_total = int(cfg.get("failed_low_total", 30))
    facts = {
        "success_logins": success_logins[:20],
        "top_attackers": top_attackers,
        "failed_total": failed_total,
        "degraded": degraded,
        "thresholds": {
            "failed_high_per_ip": failed_high_per_ip,
            "failed_high_total": failed_high_total,
            "failed_low_total": failed_low_total,
            "failed_window_hours": failed_window_hours,
            "root_remote_warn": root_remote_warn,
            "root_remote_ip_whitelist": sorted(root_remote_ip_whitelist),
        },
        "criteria": f"单 IP 失败 ≥{failed_high_per_ip} → HIGH；总失败 ≥{failed_high_total} → HIGH；≥{failed_low_total} → LOW（{failed_window_hours}h窗口）；root 远程登录（白名单外） → {'LOW' if root_remote_warn else 'INFO'}",
        "summary": f"成功登录 {len(success_logins)} 次，失败登录 {failed_total} 次（{failed_window_hours}h 窗口）{', 攻击源 ' + str(len(top_attackers)) + ' 个IP' if top_attackers else ''}{'（lastb 降级）' if degraded else ''}",
    }

    # A3 修复：root 远程登录（白名单外）作为附加事实，确保在爆破 HIGH 命中时仍能上报
    root_remote_extra = None
    if root_remote_warn and any(l["user"] == "root" for l in success_logins):
        non_whitelisted = [l for l in success_logins if l["user"] == "root" and l.get("ip") not in root_remote_ip_whitelist]
        if non_whitelisted:
            root_remote_extra = {
                "kind": "root_remote_login",
                "count": len(non_whitelisted),
                "ips": sorted({l.get("ip") for l in non_whitelisted if l.get("ip")})[:5],
            }
            facts["root_remote_login"] = root_remote_extra
            facts["criteria"] = (facts.get("criteria", "") +
                "；root 远程登录（白名单外）作为附加事实纳入 facts.root_remote_login").strip("；")

    # P1-3 优先级 1: 单 IP 失败 ≥阈值 → HIGH（把 root 远程作为附加事实带入）
    high_per_ip = [a for a in top_attackers if a["count"] >= failed_high_per_ip]
    if high_per_ip:
        top = high_per_ip[0]
        msg = f"单 IP 暴力破解 {top['ip']} 失败 {top['count']} 次（阈值 {failed_high_per_ip}）。"
        if root_remote_extra:
            msg += f" 同时存在 root 远程登录 {root_remote_extra['count']} 次（白名单外）。"
        sug = "立即封禁该 IP，启用 fail2ban；一并核查 root 远程登录合规性。"
        return ("HIGH", "RISK", msg, sug, facts)
    # P1-3 优先级 2: 总数兜底
    if failed_total >= failed_high_total:
        top_ip = top_attackers[0]["ip"] if top_attackers else "unknown"
        msg = f"近期 {failed_window_hours} 小时内失败登录 {failed_total} 条（阈值 {failed_high_total}），最高攻击源 {top_ip}。"
        if root_remote_extra:
            msg += f" 同时存在 root 远程登录 {root_remote_extra['count']} 次（白名单外）。"
        sug = "核查来源 IP，加入黑名单并收紧 SSH 白名单；启用 fail2ban；一并核查 root 远程登录合规性。"
        return ("HIGH", "RISK", msg, sug, facts)
    # P1-3 优先级 3: root 远程登录（白名单外）→ LOW
    if root_remote_extra:
        return ("LOW", "WARNING",
                f"检测到 root 远程登录（{root_remote_extra['count']} 次），不在白名单内。",
                "建议禁止 root 直接登录，使用个人账号 + sudo 审计。",
                facts)
    if failed_total >= failed_low_total:
        return ("LOW", "WARNING",
                f"近期 {failed_window_hours} 小时内有 {failed_total} 条失败登录（阈值 {failed_low_total}），存在暴力破解风险。",
                "核查来源 IP，必要时加入黑名单并收紧 SSH 白名单。",
                facts)
    return ("NONE", "PASS",
            "未发现明显登录异常。",
            "保持登录日志留存不少于 90 天。",
            facts)


def _analyze_accounts(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """P1-1 修复：shadow 不可读时返回 WARNING + fact flag，不静默 PASS。"""
    uid0_accounts: List[Dict[str, Any]] = []
    login_users: List[Dict[str, Any]] = []
    empty_password_users: List[str] = []
    total_users = 0
    in_passwd = False
    in_shadow = False
    in_shadow_err = False
    shadow_denied = False
    shadow_sections = 0
    for line in out.splitlines():
        s = line.strip()
        if s == "---PASSWD---":
            in_passwd = True
            in_shadow = False
            in_shadow_err = False
            continue
        if s == "---UID0---":
            in_passwd = False
            in_shadow = False
            in_shadow_err = False
            continue
        if s == "---SHADOW---":
            in_passwd = False
            in_shadow = True
            in_shadow_err = False
            shadow_sections += 1
            continue
        if s == "---SHADOW_ERR---":
            in_passwd = False
            in_shadow = False
            in_shadow_err = True
            continue
        if s.startswith("---"):
            in_passwd = False
            in_shadow = False
            in_shadow_err = False
            continue

        if in_shadow and ":" in s:
            parts = s.split(":")
            if len(parts) >= 2:
                user = parts[0]
                pw_hash = parts[1]
                if pw_hash == "PERMISSION_DENIED":
                    shadow_denied = True
                    continue
                # 空密码：hash 字段为空字符串（如 user::19000:0:...）
                if pw_hash == "":
                    empty_password_users.append(user)
            continue

        if in_shadow_err and s == "ERR":
            shadow_denied = True
            continue

        if not (in_passwd and ":" in s):
            continue
        parts = s.split(":")
        if len(parts) < 7:
            continue
        user, _, uid, _, _, home, shell = parts[:7]
        total_users += 1
        if uid == "0":
            uid0_accounts.append({"user": user, "uid": 0, "shell": shell, "home": home})
        if shell.rstrip().endswith(("bash", "sh", "zsh", "dash", "fish", "csh", "tcsh")):
            login_users.append({"user": user, "shell": shell})

    cfg = (thresholds or {}).get("ACCOUNT_SECURITY", {}) or {}
    max_uid0 = int(cfg.get("max_uid0", 1))
    max_login_users = int(cfg.get("max_login_users", 10))
    max_login_users_medium = int(cfg.get("max_login_users_medium", 20))
    uid0_whitelist = set(cfg.get("uid0_whitelist") or ["root"])
    uid0_violations = [a for a in uid0_accounts if a["user"] not in uid0_whitelist]
    facts = {
        "uid0_accounts": uid0_accounts,
        "login_users": login_users[:20],
        "login_user_count": len(login_users),
        "total_users": total_users,
        "empty_password_users": empty_password_users,
        "shadow_denied": shadow_denied,
        "uid0_whitelist": sorted(uid0_whitelist),
        "uid0_violations": uid0_violations,
        "thresholds": {"max_uid0": max_uid0, "max_login_users": max_login_users, "max_login_users_medium": max_login_users_medium},
        "criteria": f"UID=0 账号 >{max_uid0} → HIGH；空密码账号 → HIGH；可登录账号 >{max_login_users_medium} → MEDIUM；>{max_login_users} → LOW；shadow 不可读 → WARNING",
        "summary": f"{total_users} 个系统账号，{len(login_users)} 个可登录，{len(uid0_accounts)} 个 UID=0{', ' + str(len(empty_password_users)) + ' 个空密码' if empty_password_users else ''}{', shadow 不可读' if shadow_denied else ''}",
    }

    # H3 修复：数据采集失败兜底
    if total_users == 0 and not shadow_denied and len(login_users) == 0:
        return ("MEDIUM", "WARNING",
                "未获取到任何账号数据（/etc/passwd 解析为空，可能是 SSH 通道异常、命令被沙箱拦截或权限不足）。",
                "检查 SSH 连接、`cat /etc/passwd` 在该服务器是否可执行；确认非交互式 SSH PATH 包含 cat；非 root 用户可能无读权限。",
                facts)
    if len(uid0_violations) > max_uid0:
        names = ", ".join(a["user"] for a in uid0_violations)
        return ("HIGH", "RISK",
                f"发现 {len(uid0_violations)} 个非白名单 UID=0 特权账号（阈值 {max_uid0}）：{names}",
                "核查非 root 的 UID=0 账号，去除或停用，并排查入侵痕迹",
                facts)
    if empty_password_users:
        return ("HIGH", "RISK",
                f"发现空密码账号：{', '.join(empty_password_users[:10])}",
                "立即为空密码账号设置强密码或锁定账号",
                facts)
    if shadow_denied:
        return ("LOW", "WARNING",
                "/etc/shadow 不可读（无 root 权限或权限不足），无法检测空密码账号。",
                "以 root 用户执行巡检或授予读取 /etc/shadow 的权限。",
                facts)
    if len(login_users) > max_login_users_medium:
        return ("MEDIUM", "WARNING",
                f"可登录账号过多（{len(login_users)}，阈值 {max_login_users_medium}），存在权限蔓延风险",
                "梳理闲置账号，关闭非必要 shell",
                facts)
    if len(login_users) > max_login_users:
        return ("LOW", "WARNING",
                f"可登录账号偏多（{len(login_users)}，阈值 {max_login_users}），存在权限蔓延风险",
                "梳理闲置账号，关闭非必要 shell",
                facts)
    return ("NONE", "PASS",
            f"账号清单正常（{total_users} 个系统账号，{len(login_users)} 个可登录，{len(uid0_accounts)} 个 UID=0）",
            "保持定期审计",
            facts)


def _analyze_history(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """P2-1 修复：移除 /etc/passwd、/etc/shadow 等自伤关键字；新增 0 字节 history 检查。"""
    cfg = (thresholds or {}).get("COMMAND_HISTORY", {}) or {}
    # P2-1: 移除自伤关键字（cat /etc/passwd 在运维中是常见合法操作）
    danger_patterns = list(cfg.get("danger_keywords") or [
        "rm -rf /", "history -c", "chmod 777", "chown root",
        "> /dev/sd", "dd if=", "iptables -F", "kill -9",
        "reboot", "shutdown",
    ])
    pipe_combos = list(cfg.get("pipe_combos") or [("curl", "| bash"), ("curl", "| sh"), ("wget", "| bash"), ("wget", "| sh")])
    high_keywords = list(cfg.get("high_keywords") or ["rm -rf /", "chmod 777", "history -c", "> /dev/sd"])
    # B8 修复：shadow_read_re 替代原死字段；语义清晰且被实际使用
    shadow_read_re_pattern = cfg.get("shadow_read_re") or r"\b(cat|less|tail|head|more)\s+/etc/shadow\b"
    hit = [p for p in danger_patterns if p.lower() in out.lower()]
    out_lower = out.lower()
    for kw1, kw2 in pipe_combos:
        if kw1 in out_lower and kw2 in out_lower:
            hit.append(f"{kw1} + {kw2}")
    # P2-1: 检 0 字节 history（history -c 副作用：history 文件清空，但 ls 显示非 0 字节）
    history_empty = False
    history_total = 0
    in_history = False
    for line in out.splitlines():
        s = line.strip()
        if s == "---HISTORY---":
            in_history = True
            continue
        if s.startswith("---"):
            in_history = False
            continue
        if in_history and s:
            history_total += 1
    # B8: 使用可配置的 shadow_read_re 检测 /etc/shadow 读取
    if re.search(shadow_read_re_pattern, out):
        hit.append("sensitive_read_etc_shadow")
    passwd_read_re = r"\b(cat|less|tail|head|more)\s+/etc/passwd\b"
    passwd_read_detected = bool(re.search(passwd_read_re, out))
    facts = {
        "hit_keywords": hit[:10],
        "history_total": history_total,
        "history_empty": history_total == 0,
        "passwd_read_detected": passwd_read_detected,
        "thresholds": {"danger_keywords": danger_patterns, "pipe_combos": [f"{k1}+{k2}" for k1,k2 in pipe_combos], "high_keywords": high_keywords, "shadow_read_re": shadow_read_re_pattern},
        "criteria": f"history -c → HIGH；高危命令({high_keywords}) → HIGH；curl/wget 管道到 shell → HIGH；cat/less/tail /etc/shadow → HIGH；/etc/passwd 读取为常规操作不告警；其他危险命令 → MEDIUM",
        "summary": f"命中 {len(hit)} 个危险特征：{', '.join(hit[:5])}" if hit else f"未命中危险特征（history 共 {history_total} 条）",
    }
    if "history -c" in hit:
        return ("HIGH", "RISK", "发现 history 清空命令痕迹。", "立即核查操作人和时间窗口，结合登录日志排查入侵。", facts)
    if "sensitive_read_etc_shadow" in hit:
        return ("HIGH", "RISK", "发现读取 /etc/shadow 的命令。", "核查操作人，确认是否合规，必要时排查提权痕迹。", facts)
    if hit:
        level = "HIGH" if any(x in hit for x in high_keywords) or any("+" in x for x in hit) else "MEDIUM"
        return (level, "WARNING", f"发现高危命令特征：{', '.join(hit[:8])}。", "核查命令执行上下文，保留证据并确认是否为授权操作。", facts)
    return ("NONE", "PASS", "未发现明显高危命令痕迹。", "禁止手动清理 history，建议集中留存命令审计。", facts)


def _looks_like_port_line(s: str) -> bool:
    """J2 修复：检测一行是否像 ss/netstat 端口行（与 `---PORTS---` 标记解耦）。

    当 `---PORTS---` 标记在真实 SSH 输出中丢失（被 strip、head 截断、
    echo 失败等情况）时，状态机的 `in_ports` 标志无法正确置位，
    导致整个 ss 段被忽略、listening_count=0。本辅助函数在状态机
    `not in_top and not in_ports` 状态下，对每一行做特征检测。

    兼容两种格式：
    - ss 格式：Netid State Recv-Q Send-Q Local:Port Peer:Port users:(("svc",pid=N,fd=N))
      状态字在第 2 列（LISTEN/UNCONN/ESTAB 等）
    - netstat 格式：Proto Recv-Q Send-Q Local Foreign State PID/Program
      状态字在第 6 列（LISTEN/UNCONN/LISTENING）
    两者第一列都是 tcp/udp/raw/unix。

    ps top 行第一列是 PID 数字，第二列是 PPID 数字，第四列是 %CPU 浮点——不会误判。
    """
    if not s or s.startswith("---"):
        return False
    if s.startswith(("PID", "USER", "Proto", "Netid")):
        return False
    parts = s.split()
    if len(parts) < 5:
        return False
    proto = parts[0].lower()
    if proto not in ("tcp", "udp", "raw", "tcp6", "unix"):
        return False
    # 行内必须含 IP:Port 形式（`:数字`）
    if not re.search(r":\d+\b", s):
        return False
    # 行内必须含状态字（ss 在第 2 列、netstat 在第 6 列，统一行内匹配）
    valid_states = (
        "LISTEN", "LISTENING", "UNCONN", "ESTAB", "ESTABLISHED",
        "TIME-WAIT", "FIN-WAIT-1", "FIN-WAIT-2", "CLOSE-WAIT",
        "LAST-ACK", "CLOSING", "SYN-RECV", "SYN-SENT", "NEW",
        "CLOSE",
    )
    upper = s.upper()
    if not any(st in upper for st in valid_states):
        return False
    return True


def _analyze_process_ports(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """P1-2 修复：端口高/中风险分级，IPv6 通配符 ([::]) 正确识别。"""
    high_risk_ports: List[Dict[str, Any]] = []
    medium_risk_ports: List[Dict[str, Any]] = []
    all_listening: List[Dict[str, Any]] = []
    top_cpu_procs: List[Dict[str, Any]] = []
    suspicious: List[str] = []
    in_top = False
    in_ports = False
    cfg = (thresholds or {}).get("PROCESS_PORT", {}) or {}
    # P1-2: 端口风险分级
    if "high_risk_ports_high" in cfg or "high_risk_ports_medium" in cfg:
        high_risk_set = set(int(x) for x in (cfg.get("high_risk_ports_high") or []))
        medium_risk_set = set(int(x) for x in (cfg.get("high_risk_ports_medium") or []))
    else:
        # 兼容老字段：把原 high_risk_ports 全部视为 high，medium 默认为空
        high_risk_set = set(int(x) for x in (cfg.get("high_risk_ports") or DEFAULT_THRESHOLDS["PROCESS_PORT"].get("high_risk_ports", [])))
        medium_risk_set = set()
    suspicious_kw = [str(x).lower() for x in (cfg.get("suspicious_keywords") or DEFAULT_THRESHOLDS["PROCESS_PORT"]["suspicious_keywords"])]
    top_cpu_n = int(cfg.get("top_cpu_n", 10))
    cpu_threshold = float(cfg.get("cpu_threshold", 80))
    cpu_sample_count = int(cfg.get("cpu_sample_count", 1))  # P2-2: 连续 N 次 ≥阈值

    for line in out.splitlines():
        s = line.strip()
        if s == "---TOP---":
            in_top = True
            in_ports = False
            continue
        if s == "---PORTS---":
            in_top = False
            in_ports = True
            continue
        if s.startswith("---"):
            in_top = False
            in_ports = False
            continue

        if in_top:
            # ps header: PID PPID USER COMM %CPU %MEM ARGS
            parts = s.split(None, 6)
            if len(parts) >= 7 and parts[0] != "PID":
                try:
                    top_cpu_procs.append({
                        "pid": int(parts[0]),
                        "user": parts[2],
                        "cpu": parts[4],
                        "mem": parts[5],
                        "cmd": parts[6][:120],
                    })
                except (ValueError, IndexError):
                    pass

        if in_ports or _looks_like_port_line(s):
            # ss output: Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port  process
            # or netstat: Proto Recv-Q Send-Q Local Address Foreign Address State PID/Program
            if not s or s.startswith("Netid") or s.startswith("Proto"):
                continue
            # H1 修复 1：ss 完整格式（含 users 进程段，UDP UNCONN 也识别）
            m_ss = re.match(
                r'(\w+)\s+(\w+)\s+\d+\s+\d+\s+(\S+?):(\d+)\s+(\S+)\s+users:\(\("(\S+?)",[^)]*pid=(\d+)[^)]*\)\)',
                s,
            )
            entry: Optional[Dict[str, Any]] = None
            if m_ss:
                proto = m_ss.group(1).lower()
                state = m_ss.group(2).lower()
                bind = m_ss.group(3)
                port = int(m_ss.group(4))
                service = m_ss.group(6)
                pid = m_ss.group(7)
                entry = {
                    "port": port, "proto": proto, "state": state, "service": service,
                    "bind": f"{bind}:{port}", "pid": pid, "user": "",
                }
            # H1 修复 2：ss 半格式（无 users 段，UDP UNCONN/TCP LISTEN 都识别）
            elif (m_ss2 := re.match(
                r'(\w+)\s+(\w+)\s+\d+\s+\d+\s+(\S+?):(\d+)\s+\S+',
                s,
            )):
                proto = m_ss2.group(1).lower()
                state = m_ss2.group(2).lower()
                bind = m_ss2.group(3)
                port = int(m_ss2.group(4))
                if state in ("listening", "listen", "unconn", "established", "time-wait", "fin-wait-1", "fin-wait-2", "close-wait", "last-ack", "closing", "syn-recv", "syn-sent"):
                    entry = {
                        "port": port, "proto": proto, "state": state, "service": "",
                        "bind": f"{bind}:{port}", "pid": "", "user": "",
                    }
            # H1 修复 3：netstat 格式
            elif (m_ns := re.match(
                r'(\w+)\s+\d+\s+\d+\s+([\d.:\[\]]+):(\d+)\s+[\d.:\[\]*]+\s+(LISTEN|UNCONN|LISTENING)\s+(?:(\d+)/(\S+))?',
                s,
            )):
                proto = m_ns.group(1).lower()
                bind = m_ns.group(2)
                port = int(m_ns.group(3))
                pid = m_ns.group(5) or ""
                service = m_ns.group(6) or ""
                entry = {
                    "port": port, "proto": proto, "state": m_ns.group(4).lower(),
                    "service": service, "bind": f"{bind}:{port}", "pid": pid, "user": "",
                }

            if entry:
                # H1 修复 4：兼容 bind 中带 %interface 后缀（如 127.0.53%lo:53）
                bind_for_check = entry["bind"]
                if "%" in bind_for_check:
                    bind_for_check = bind_for_check.split("%")[0]
                bind_norm = bind_for_check.rstrip(":]").strip("[]")
                is_world = (
                    bind_norm in ("0.0.0.0", "::", "*", "")
                    or bind_for_check.startswith("0.0.0.0:")
                    or bind_for_check.startswith("[::]:")
                    or bind_for_check.startswith("*:")
                )
                entry["is_world"] = is_world
                all_listening.append(entry)
                service_lower = (entry.get("service") or "").lower()
                if service_lower in suspicious_kw:
                    suspicious.append(service_lower)
                if is_world and port in high_risk_set:
                    high_risk_ports.append(entry)
                elif is_world and port in medium_risk_set:
                    medium_risk_ports.append(entry)

    # P2-2 修复：连续 N 次采样均 ≥ 阈值才计入（计划文档 §2.3 P2-2：两次都 ≥80%）。
    # 解析策略：把 top_cpu_procs 按 sample_id 分组（每段 ---TOP--- 之间的列表是一次采样），
    # 用 (pid) 关联，每个 pid 收集所有样本的 cpu 值；只有连续样本全部 ≥ 阈值才记入。
    high_cpu_procs = []
    if cpu_sample_count > 1:
        # 重新按 ---TOP--- 分段采样
        samples: List[List[Dict[str, Any]]] = []
        current_sample: List[Dict[str, Any]] = []
        for line in out.splitlines():
            sl = line.strip()
            if sl == "---TOP---":
                if current_sample:
                    samples.append(current_sample)
                current_sample = []
                continue
            if sl.startswith("---"):
                continue
            parts = sl.split(None, 6)
            if len(parts) >= 7 and parts[0] != "PID":
                try:
                    current_sample.append({
                        "pid": int(parts[0]),
                        "user": parts[2],
                        "cpu": float(parts[4]),
                        "mem": float(parts[5]),
                        "cmd": parts[6][:120],
                    })
                except (ValueError, IndexError):
                    pass
        if current_sample:
            samples.append(current_sample)

        if samples:
            # 按 pid 聚合：每 pid 的 cpu 序列按样本顺序
            by_pid: Dict[int, List[float]] = {}
            for sample in samples[:cpu_sample_count]:  # 只取前 N 次采样
                for p in sample:
                    pid = int(p["pid"])
                    by_pid.setdefault(pid, []).append(float(p.get("cpu", 0)))
            # 取 top_cpu_n 个稳定高 CPU pid
            stable_pids = [
                pid for pid, cpus in by_pid.items()
                if len(cpus) >= cpu_sample_count
                and all(c >= cpu_threshold for c in cpus[:cpu_sample_count])
            ]
            # 从 top_cpu_procs 找匹配的完整记录
            stable_set = set(stable_pids)
            seen_cmds: set = set()
            for p in top_cpu_procs:
                pid = int(p.get("pid", 0))
                if pid in stable_set:
                    key = (pid, p.get("cmd", ""))
                    if key not in seen_cmds:
                        seen_cmds.add(key)
                        high_cpu_procs.append(p)
                        if len(high_cpu_procs) >= 5:
                            break
    else:
        for p in top_cpu_procs:
            try:
                if float(p.get("cpu", 0)) >= cpu_threshold:
                    high_cpu_procs.append(p)
            except (ValueError, TypeError):
                pass

    facts = {
        "high_risk_ports": high_risk_ports[:20],
        "medium_risk_ports": medium_risk_ports[:20],
        "all_listening": all_listening[:50],
        "top_cpu_procs": top_cpu_procs[:top_cpu_n],
        "high_cpu_procs": high_cpu_procs[:5],
        "listening_count": len(all_listening),
        "thresholds": {
            "high_risk_ports_high": sorted(high_risk_set),
            "high_risk_ports_medium": sorted(medium_risk_set),
            "suspicious_keywords": suspicious_kw,
            "top_cpu_n": top_cpu_n,
            "cpu_threshold": cpu_threshold,
            "cpu_sample_count": cpu_sample_count,
        },
        "criteria": f"公网暴露 high 端口 {sorted(high_risk_set)} → MEDIUM；公网暴露 medium 端口 {sorted(medium_risk_set)} → LOW；可疑进程 {suspicious_kw} → HIGH；CPU≥{cpu_threshold}% 连续 {cpu_sample_count} 次 → MEDIUM",
        "summary": f"监听 {len(all_listening)} 个端口，{len(high_risk_ports)} 个高危暴露，{len(medium_risk_ports)} 个中危暴露，{len(high_cpu_procs)} 个高CPU进程" + (f"，{len(suspicious)} 个可疑进程" if suspicious else ""),
    }

    if suspicious:
        return ("HIGH", "RISK",
                f"发现疑似恶意/挖矿进程特征：{', '.join(suspicious)}。",
                "立即隔离服务器，保留进程与网络证据后排查入侵。",
                facts)
    # D1 修复：high_risk_ports_high 中敏感端口（数据库/缓存）暴露公网 → HIGH
    # high_risk_ports_medium 暴露 → MEDIUM（保持原口径）
    # 暴露等级可通过 exposed_high_risk_level 配置（默认 HIGH，兼容性保留）
    exposed_high_level = str(cfg.get("exposed_high_risk_level", "HIGH")).upper()
    if exposed_high_level not in {"HIGH", "MEDIUM", "LOW"}:
        exposed_high_level = "HIGH"
    if high_risk_ports:
        port_list = ", ".join(f":{p['port']}" for p in high_risk_ports)
        if exposed_high_level == "HIGH":
            return (exposed_high_level, "RISK",
                    f"检测到敏感服务端口（数据库/缓存）公网暴露：{port_list}",
                    "立即将数据库/缓存端口限制为内网或白名单 IP 访问；启用安全组/防火墙策略。",
                    facts)
        return (exposed_high_level, "WARNING",
                f"检测到高风险端口公网暴露：{port_list}",
                "确认端口是否仅对白名单开放，避免数据库/缓存端口暴露外网。",
                facts)
    if medium_risk_ports:
        port_list = ", ".join(f":{p['port']}" for p in medium_risk_ports)
        return ("MEDIUM", "WARNING",
                f"检测到中风险端口监听：{port_list}",
                "建议通过防火墙/安全组限制为内网或白名单 IP 访问。",
                facts)
    if high_cpu_procs:
        proc_list = ", ".join(f"{p.get('cmd', '?')[:30]}({p.get('cpu', '?')}%)" for p in high_cpu_procs[:5])
        return ("MEDIUM", "WARNING",
                f"检测到高 CPU 占用进程（阈值 {cpu_threshold}%，连续 {cpu_sample_count} 次采样）：{proc_list}",
                "核查进程是否为正常业务，排查异常占用或挖矿可能。",
                facts)
    # F2 修复：空数据兜底 — 未获取到任何进程/端口时明确提示
    if not all_listening and not top_cpu_procs and not suspicious and not out.strip():
        return ("MEDIUM", "WARNING",
                "未获取到进程与端口数据（命令执行无输出，可能是 SSH 通道异常、命令路径缺失或非交互式 shell）。",
                "检查 SSH 连接、`ps`/`ss`/`netstat` 在该服务器是否可执行；确认非交互式 SSH PATH 包含相应命令。",
                facts)
    if not all_listening and not top_cpu_procs and not suspicious:
        return ("MEDIUM", "WARNING",
                f"未获取到任何进程或监听端口数据（已解析 0 个，原始输出 {len(out.splitlines())} 行），无法评估端口暴露与异常进程。",
                "检查命令模板 `ps -eo ...` / `ss -ntulp` / `netstat -ntulp` 在该服务器是否可执行；确认非交互式 SSH PATH 包含 ps/ss/netstat。",
                facts)
    return ("NONE", "PASS",
            "进程和监听端口未发现明显异常。",
            "保持端口最小暴露。",
            facts)


def _analyze_memory(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """P0-1 修复：优先 /proc/meminfo 的 MemAvailable，避免 buff/cache 误报。

    解析顺序：
    1) /proc/meminfo：MemTotal/MemAvailable/SwapTotal/SwapFree
    2) `free` 命令读 available 列（fallback）
    3) 兜底：输出 LOW + WARNING 提示指标缺失
    """
    mem_pct = 0
    swap_pct = 0
    mem_total_h = ""
    swap_total_h = ""
    source = "unknown"  # meminfo / free / none
    meminfo: Dict[str, int] = {}

    section = "free"
    for line in out.splitlines():
        s = line.strip()
        if s == "---MEMINFO---":
            section = "meminfo"
            continue
        if s.startswith("---"):
            section = "free"
            continue
        if section == "meminfo":
            parts = s.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                try:
                    meminfo[key] = int(parts[1])
                except ValueError:
                    pass
        elif s.startswith("Mem:") or s.startswith("Swap:"):
            # 记录 free 输出的 total 字符串（人类可读）
            parts = s.split()
            if len(parts) >= 2:
                if s.startswith("Mem:"):
                    mem_total_h = parts[1]
                elif s.startswith("Swap:"):
                    swap_total_h = parts[1]

    mem_total_kb = meminfo.get("MemTotal", 0)
    mem_avail_kb = meminfo.get("MemAvailable", 0)
    swap_total_kb = meminfo.get("SwapTotal", 0)
    swap_free_kb = meminfo.get("SwapFree", 0)
    if mem_total_kb > 0 and mem_avail_kb > 0:
        source = "meminfo"
        mem_pct = int(round((mem_total_kb - mem_avail_kb) / mem_total_kb * 100))
    else:
        # 兜底：解析 free 输出的 available 列
        # G4 修复：自动检测 free 输出的单位（默认 KB / -m 是 MB / -h 是带后缀）
        free_unit = ""  # K / M / G / T / ""
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Mem:") and not s.startswith("MemAvailable"):
                parts = s.split()
                if len(parts) >= 7:
                    # 探测单位：
                    # 1) `free -h` 输出：值带 K/M/G/T 后缀（e.g. "16G", "512M"）→ 用后缀
                    # 2) `free -m` 输出：纯数字（e.g. "3562"）→ 启发式判断
                    # 3) `free` 默认输出：纯数字但量级大（e.g. "16384000"）→ 视为 KB
                    sample = parts[1]
                    sample_u = sample.upper()
                    # 探测单位（兼容 IEC 二进制前缀 Gi/Mi/Ki/Ti）：
                    # 1) `free -h` 输出：值带 K/M/G/T 后缀（e.g. "16G", "512M", "16Gi"）→ 用后缀
                    # 2) `free -m` 输出：纯数字（e.g. "3562"）→ 启发式判断
                    # 3) `free` 默认输出：纯数字但量级大（e.g. "16384000"）→ 视为 KB
                    sample_norm = sample_u
                    if sample_norm.endswith("I") and len(sample_norm) > 1 and sample_norm[-2] in "KMGT":
                        sample_norm = sample_norm[:-1]
                    if sample_norm.endswith(("K", "M", "G", "T")) and len(sample) > 1:
                        free_unit = sample_norm[-1].upper()
                    else:
                        try:
                            n = float(sample)
                            # 启发式：>= 1e5 视为 KB（free 默认），
                            # 1e3 ~ 1e5 视为 MB（free -m），
                            # < 1e3 视为 GB（free -g）
                            if n >= 100000:
                                free_unit = "K"
                            elif n >= 1000:
                                free_unit = "M"
                            else:
                                free_unit = "G"
                        except ValueError:
                            free_unit = "K"
                    try:
                        mem_total_kb = _parse_mem_value_with_unit(parts[1], free_unit)
                        mem_avail_kb = _parse_mem_value_with_unit(parts[6], free_unit)
                        if mem_total_kb > 0 and mem_avail_kb > 0:
                            source = "free"
                            mem_pct = int(round((mem_total_kb - mem_avail_kb) / mem_total_kb * 100))
                    except (ValueError, IndexError):
                        pass
                    break
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Swap:") and not s.startswith("SwapTotal"):
                parts = s.split()
                if len(parts) >= 4:
                    try:
                        swap_total_kb = _parse_mem_value_with_unit(parts[1], free_unit)
                        swap_free_kb = _parse_mem_value_with_unit(parts[3], free_unit)
                    except (ValueError, IndexError):
                        pass

    if swap_total_kb > 0:
        swap_used_kb = max(swap_total_kb - swap_free_kb, 0)
        swap_pct = int(round(swap_used_kb / swap_total_kb * 100))
    elif source == "free":
        # free 命令的 swap used 在第 2 列
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Swap:") and not s.startswith("SwapTotal"):
                parts = s.split()
                if len(parts) >= 3:
                    try:
                        used = _parse_mem_value_with_unit(parts[2], free_unit)
                        if swap_total_kb > 0:
                            swap_pct = int(round(used / swap_total_kb * 100))
                    except (ValueError, IndexError):
                        pass

    cfg = (thresholds or {}).get("MEMORY", {}) or {}
    mem_high_pct = int(cfg.get("mem_high_pct", 95))
    mem_medium_pct = int(cfg.get("mem_medium_pct", 85))
    swap_high_pct = int(cfg.get("swap_high_pct", 50))
    swap_critical_pct = int(cfg.get("swap_critical_pct", 80))
    facts = {
        "mem_pct": mem_pct,
        "swap_pct": swap_pct,
        "mem_total": mem_total_h,
        "swap_total": swap_total_h,
        "source": source,
        "thresholds": {"mem_high_pct": mem_high_pct, "mem_medium_pct": mem_medium_pct, "swap_high_pct": swap_high_pct, "swap_critical_pct": swap_critical_pct},
        "criteria": f"内存 ≥{mem_high_pct}% → HIGH；≥{mem_medium_pct}% → MEDIUM；Swap ≥{swap_critical_pct}% → HIGH；≥{swap_high_pct}% → MEDIUM；优先用 MemAvailable",
        "summary": f"内存 {mem_pct}%{', Swap ' + str(swap_pct) + '%' if swap_pct > 0 else ''}（来源 {source}）",
    }

    # 兜底：指标完全缺失时返回 MEDIUM + WARNING（F4 修复：升级为 MEDIUM）
    # 数据采集中断是巡检的严重问题——可能错过内存爆涨/OOM 等致命事件
    if mem_total_kb <= 0 and source == "unknown":
        return ("MEDIUM", "WARNING",
                "未获取到内存使用率数据（meminfo/available 都未读到，命令可能失败或 /proc 不可读）。",
                "检查 SSH 连接、`free` / `cat /proc/meminfo` 在该服务器是否可执行；确认非交互式 SSH PATH 包含 free；Docker 容器需挂载 /proc。",
                facts)

    if mem_pct >= mem_high_pct:
        return ("HIGH", "RISK",
                f"内存使用率过高（{mem_pct}%，阈值 {mem_high_pct}%），可能触发 OOM。",
                "排查内存泄漏进程，考虑扩容或优化应用内存占用。",
                facts)
    if swap_pct >= swap_critical_pct:
        return ("HIGH", "RISK",
                f"Swap 使用率异常（{swap_pct}%，阈值 {swap_critical_pct}%），内存严重不足。",
                "立即检查 OOM 日志，增加物理内存或排查内存泄漏。",
                facts)
    if mem_pct >= mem_medium_pct:
        return ("MEDIUM", "WARNING",
                f"内存使用率偏高（{mem_pct}%，阈值 {mem_medium_pct}%）。",
                "关注内存增长趋势，确认是否存在未释放的缓存或泄漏。",
                facts)
    if swap_pct >= swap_high_pct:
        return ("MEDIUM", "WARNING",
                f"Swap 使用率偏高（{swap_pct}%，阈值 {swap_high_pct}%），可能存在内存压力。",
                "排查高内存消耗进程，检查 Swap 趋势。",
                facts)
    return ("NONE", "PASS",
            f"内存 {mem_pct}%{', Swap ' + str(swap_pct) + '%' if swap_pct > 0 else ''} 未发现明显异常。",
            "保持内存监控告警，定期核查进程资源占用。",
            facts)


def _parse_mem_value(val: str) -> int:
    """Parse memory value to KB.

    支持格式：
    - "16384000" → 16384000 (无后缀视为 KB，符合 `free` 默认输出)
    - "3562M" / "3562" (来自 `free -m`) → 3562 * 1024 = 3,646,208 KB
      实际通过 unit 参数显式指定，避免歧义
    - "1.5G" → 1572864
    - "1841924 kB" → 1841924 (meminfo 自动去掉单位)
    """
    val = val.strip().upper().rstrip("KMGTB")
    val = val.replace(",", "").strip()
    try:
        return int(float(val))
    except ValueError:
        return 0


def _parse_mem_value_with_unit(val: str, unit: str) -> int:
    """Parse memory value to KB, with explicit unit hint.

    unit: "K" / "M" / "G" / "T" / "" (auto)
    兼容 IEC 二进制前缀 Ki/Mi/Gi/Ti（B/KiB=1024）。
    """
    s = val.strip().upper().replace(",", "")
    # 去掉 IEC 后缀 i（如 Gi/Mi/Ki/Ti）
    if s.endswith("I") and len(s) > 1 and s[-2] in "KMGT":
        s = s[:-1]
    val_clean = s.rstrip("KMGTB")
    try:
        num = float(val_clean)
    except ValueError:
        return 0
    unit = (unit or "").upper().strip()
    if unit == "M":
        return int(num * 1024)
    if unit == "G":
        return int(num * 1024 * 1024)
    if unit == "T":
        return int(num * 1024 * 1024 * 1024)
    # 默认视为 KB（free 默认输出）
    return int(num)


def _analyze_firewall(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """P1-4 修复：云环境探测 firewalld/ufw/nft/iptables 都未启用时仅 LOW 提示。
    B3 修复：no_local_firewall 检测放宽——多发行版/容器均覆盖。
    """
    cfg = (thresholds or {}).get("FIREWALL", {}) or {}
    inactive_level = str(cfg.get("inactive_level", "MEDIUM")).upper()
    no_local_level = str(cfg.get("no_local_firewall_level", "LOW")).upper()
    text = out.lower()
    err_text = (err or "").lower()

    # 按行解析 iptables 规则
    global_pass_lines: List[str] = []
    default_policy_accept = False
    for line in out.splitlines():
        sl = line.strip().lower()
        if sl.startswith("-p input accept"):
            default_policy_accept = True
        if sl.startswith("-a") and "0.0.0.0/0" in sl and ("-p all" in sl or "-p ip" in sl):
            global_pass_lines.append(line.strip())

    # P1-4: 探测各类防火墙工具的活跃情况
    detected_tools: List[str] = []
    if "active (running)" in text or "active(running)" in text:
        if "firewalld" in text:
            detected_tools.append("firewalld")
        if "ufw" in text:
            detected_tools.append("ufw")
    if "firewalld is not running" in text or "inactive" in text and "firewalld" in text:
        pass  # firewalld 显式未运行
    if "ufw is inactive" in text or "status: inactive" in text:
        pass  # ufw 显式未启用

    # B3: 放宽"无本地防火墙"判定——多关键字覆盖 firewalld/ufw/nft/iptables/ip6tables
    # 注意：仅当工具是"运行中"或"有规则"才算存在；只在 systemctl 描述里出现 firewalld 名称不算
    fw_active_signatures = (
        "active (running)", "active(running)",  # 通用 active
        "ufw status", "status: active",  # ufw
        "nft list ruleset", "table inet",  # nftables
    )
    fw_command_outputs = (
        "firewall-cmd",  # firewall-cmd 命令输出
        "iptables -s", "iptables -l",  # iptables -S/-L
        "ip6tables",  # ip6tables
        "chain input", "policy accept", "policy drop",  # iptables 链/策略输出
        "-p tcp", "-p udp",  # iptables 规则
    )
    has_fw_active = any(sig in text for sig in fw_active_signatures)
    has_fw_output = any(sig in text for sig in fw_command_outputs)
    # firewalld 显式 inactive 时不算"有防火墙"
    firewalld_active = ("firewalld" in text and "active (running)" in text) or (
        "firewalld" in text and "inactive" not in text and "loaded" in text and "firewall-cmd" in text
    )
    ufw_active = "ufw" in text and ("status: active" in text or "active (running)" in text)
    has_fw_signature = has_fw_active or has_fw_output or firewalld_active or ufw_active
    no_local_firewall = not has_fw_signature and not detected_tools

    facts = {
        "raw_snippet": (out[:500] if out else ""),
        "global_pass_lines": global_pass_lines[:5],
        "default_policy_accept": default_policy_accept,
        "detected_tools": detected_tools,
        "no_local_firewall": no_local_firewall,
        "thresholds": {"inactive_level": inactive_level, "no_local_firewall_level": no_local_level},
        "criteria": f"全局放行规则 → HIGH；INPUT 策略 ACCEPT → LOW；本机防火墙全未启用 → {no_local_level}（云环境常见）",
        "summary": (
            f"{len(global_pass_lines)} 条全局放行规则" + ("，INPUT 策略 ACCEPT" if default_policy_accept else "")
            + (f"，本机防火墙全未启用（{no_local_level}）" if no_local_firewall else "")
            + (f"，已探测到 {','.join(detected_tools)}" if detected_tools else "")
        ),
    }

    if global_pass_lines:
        return ("HIGH", "RISK",
                f"发现 {len(global_pass_lines)} 条疑似全局放行规则：{global_pass_lines[0][:80]}...",
                "立即核查规则来源，收紧到必要端口和白名单 IP。",
                facts)
    # I1 修复：探测 iptables 实际规则数（除策略行外还有 -N/-A/-I 等行）
    iptables_rule_count = 0
    for sl_full in out.splitlines():
        sl_low = sl_full.strip().lower()
        if sl_low.startswith(("-n ", "-n\t", "-a ", "-a\t", "-i ", "-i\t", "-r ", "-d ")):
            iptables_rule_count += 1
    # I1 修复 1：firewalld/ufw 显式 inactive + iptables INPUT ACCEPT（高风险复合场景）
    if default_policy_accept and iptables_rule_count > 0 and (
        "inactive" in text or "not running" in text or "status: inactive" in text
    ):
        return ("MEDIUM", "WARNING",
                f"firewalld/ufw 未运行；iptables 已加载 {iptables_rule_count} 条规则但 INPUT 默认策略为 ACCEPT，等同于全量放行。",
                "建议：1) 启用 firewalld/ufw 接管；或 2) 执行 iptables -P INPUT DROP 并显式 ACCEPT 必要端口。",
                facts)
    # I1 修复 2：firewalld/ufw 显式 inactive + iptables INPUT DROP（有专用管理）
    if (not default_policy_accept) and iptables_rule_count > 0 and (
        "inactive" in text or "not running" in text or "status: inactive" in text
    ):
        return ("LOW", "INFO",
                f"firewalld/ufw 未运行；iptables 已配置 {iptables_rule_count} 条规则（默认策略非 ACCEPT）。",
                "若有专用 iptables 管理脚本，可保持现状；否则建议启用 firewalld 标准化管理。",
                facts)
    if no_local_firewall:
        level = no_local_level if no_local_level in {"LOW", "MEDIUM", "HIGH", "NONE"} else "LOW"
        return (level, "INFO" if level == "LOW" else "WARNING",
                "本机未探测到任何活跃的防火墙进程（firewalld/ufw/nft/iptables/ip6tables）。",
                "云服务器请确认安全组策略已正确配置；自建机房建议启用 firewalld/ufw/nftables。",
                facts)
    if "inactive" in text or "not running" in text or "status: inactive" in text:
        if inactive_level == "LOW":
            return ("LOW", "WARNING", "防火墙可能未启用或未运行（云环境可能使用安全组替代）。", "确认安全组策略是否覆盖防火墙功能。", facts)
        return (inactive_level, "WARNING", "防火墙可能未启用或未运行。", "确认安全组/防火墙策略，生产环境避免全局放行。", facts)
    if default_policy_accept:
        return ("LOW", "WARNING",
                "iptables 默认 INPUT 策略为 ACCEPT，建议改为 DROP 并显式放行必要端口。",
                "将默认策略改为 DROP：iptables -P INPUT DROP，然后添加必要的 ACCEPT 规则。",
                facts)
    return ("NONE", "PASS", "防火墙状态未发现明显异常。", "定期复核开放策略与黑白名单冲突。", facts)


def _worst_mount(filesystems: List[Dict[str, Any]]) -> str:
    """Return the mount point with the highest usage percentage."""
    if not filesystems:
        return "N/A"
    worst = max(filesystems, key=lambda x: x.get("pct", 0))
    return worst.get("mount", "?")


def _analyze_disk(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """P0-4 修复：跳过 overlay/squashfs/tmpfs 等容器/伪文件系统；NFS stale 单独识别。
    G1 修复：兼容 6 列（df -h，无 Type）和 7 列（df -PTh / df -hT，含 Type）输出格式。
    """
    filesystems: List[Dict[str, Any]] = []
    inodes: List[Dict[str, Any]] = []
    stale_mounts: List[Dict[str, Any]] = []
    skipped_fstypes: List[str] = []
    max_pct = 0
    max_inode_pct = 0
    section = "SPACE"
    for line in out.splitlines():
        if line.strip() == "---SPACE---":
            section = "SPACE"
            continue
        if line.strip() == "---INODE---":
            section = "INODE"
            continue
        parts = line.split()
        # G1 修复：6 列（df -h 无 Type）或 7 列（df -PTh / df -hT 含 Type）都接受
        if len(parts) < 6:
            continue
        if parts[0] == "Filesystem":
            continue
        # 倒数第二列是 Use% (e.g. "22%")；最后一列是 Mounted on
        pct_str = parts[-2]
        if not pct_str.endswith('%'):
            continue
        try:
            pct = int(pct_str.rstrip('%'))
        except ValueError:
            continue
        # G1 修复：根据列数判断 fstype 列位置
        # 6 列格式: Filesystem Size Used Avail Use% Mounted on → parts[1] 是 Size（不是 fstype）
        # 7 列格式: Filesystem Type Size Used Avail Use% Mounted on → parts[1] 是 fstype
        if len(parts) == 7 and section == "SPACE":
            fstype = parts[1].lower()
        else:
            # 6 列或 INODE 段：没有显式 fstype 列，留空（通过大小写推断）
            fstype = ""
        entry_mount = parts[-1]
        if section == "SPACE":
            # P0-4: 跳过容器/伪文件系统
            if fstype and fstype in _SKIP_FSTYPES:
                skipped_fstypes.append(fstype)
                continue
            # P0-4: NFS ≥99% 单独识别为 stale
            nfs_threshold = int(((thresholds or {}).get("DISK") or {}).get("nfs_stale_threshold_pct", 99))
            if fstype.startswith("nfs") and pct >= nfs_threshold:
                stale_mounts.append({"mount": entry_mount, "type": fstype, "pct": pct})
                continue
            max_pct = max(max_pct, pct)
            filesystems.append({
                "mount": entry_mount,
                "type": fstype or "unknown",
                "total": parts[2] if len(parts) == 7 else parts[1],
                "used": parts[3] if len(parts) == 7 else parts[2],
                "avail": parts[4] if len(parts) == 7 else parts[3],
                "pct": pct,
            })
        else:
            max_inode_pct = max(max_inode_pct, pct)
            inodes.append({
                "mount": entry_mount,
                "inodes_total": parts[2] if len(parts) == 7 else parts[1],
                "inodes_used": parts[3] if len(parts) == 7 else parts[2],
                "inodes_free": parts[4] if len(parts) == 7 else parts[3],
                "inode_pct": pct,
            })

    cfg = (thresholds or {}).get("DISK", {}) or {}
    high_pct = int(cfg.get("high_pct", 90))
    medium_pct = int(cfg.get("medium_pct", 75))
    inode_high_pct = int(cfg.get("inode_high_pct", 90))
    inode_medium_pct = int(cfg.get("inode_medium_pct", 80))
    system_mounts = set(cfg.get("system_mounts") or ["/", "/boot", "/boot/efi", "/root"])
    system_offset = int(cfg.get("system_pct_offset", 5))
    skip_fstypes = set(cfg.get("skip_fstypes") or _SKIP_FSTYPES)
    facts = {
        "filesystems": filesystems[:30],
        "max_pct": max_pct,
        "inodes": inodes[:30],
        "max_inode_pct": max_inode_pct,
        "stale_mounts": stale_mounts,
        "skipped_fstypes": sorted(set(skipped_fstypes)),
        "thresholds": {"high_pct": high_pct, "medium_pct": medium_pct, "inode_high_pct": inode_high_pct, "inode_medium_pct": inode_medium_pct, "system_mounts": sorted(system_mounts), "system_pct_offset": system_offset, "skip_fstypes": sorted(skip_fstypes)},
        "criteria": f"空间 ≥{high_pct}% → HIGH；≥{medium_pct}% → MEDIUM；inode ≥{inode_high_pct}% → HIGH；≥{inode_medium_pct}% → MEDIUM；NFS≥99% 单独识别为 stale；overlay/squashfs/tmpfs 已跳过",
        "summary": f"{len(filesystems)} 个挂载点，最高 {max_pct}% ({_worst_mount(filesystems)})" + (f"，inode 最高 {max_inode_pct}%" if max_inode_pct > 0 else "") + (f"，跳过 {len(skipped_fstypes)} 个 {','.join(sorted(set(skipped_fstypes)))} 挂载" if skipped_fstypes else ""),
    }

    # P0-4: NFS stale 独立判定（不参与 max_pct 评分）
    if stale_mounts:
        return ("MEDIUM", "WARNING",
                f"检测到疑似 stale NFS 挂载：{', '.join(m['mount'] for m in stale_mounts)}",
                "umount -f 后重新挂载，确认 NFS server 状态。",
                facts)

    # 先检查 inode 耗尽（最致命）
    # B2: 与磁盘块口径一致，系统分区按 system_pct_offset 提前告警
    def _inode_effective_threshold(mount: str, base_pct: int) -> int:
        return base_pct - (system_offset if mount in system_mounts else 0)

    high_inode = [
        (io["mount"], io["inode_pct"])
        for io in inodes
        if io["inode_pct"] >= _inode_effective_threshold(io["mount"], inode_high_pct)
    ]
    if high_inode:
        # B2 修复：仅当有系统分区被命中时才在 msg 中说明"系统分区"阈值
        has_system = any(m in system_mounts for m, _ in high_inode)
        high_strs = [f"{m} {p}%" for m, p in high_inode[:8]]
        if has_system:
            msg = f"inode 使用率过高（阈值 {inode_high_pct}%，系统分区 {inode_high_pct - system_offset}%）：{', '.join(high_strs)}。"
        else:
            msg = f"inode 使用率过高（阈值 {inode_high_pct}%）：{', '.join(high_strs)}。"
        return ("HIGH", "RISK", msg,
                "inode 耗尽将导致无法创建文件。清理大量小文件（如 session、缓存、日志）。",
                facts)
    medium_inode = [
        (io["mount"], io["inode_pct"])
        for io in inodes
        if io["inode_pct"] >= _inode_effective_threshold(io["mount"], inode_medium_pct)
    ]
    if medium_inode:
        has_system = any(m in system_mounts for m, _ in medium_inode)
        med_strs = [f"{m} {p}%" for m, p in medium_inode[:8]]
        if has_system:
            msg = f"inode 使用率偏高（阈值 {inode_medium_pct}%，系统分区 {inode_medium_pct - system_offset}%）：{', '.join(med_strs)}"
        else:
            msg = f"inode 使用率偏高（阈值 {inode_medium_pct}%）：{', '.join(med_strs)}"
        return ("MEDIUM", "WARNING", msg,
                "关注 inode 增长趋势，排查大量小文件来源。",
                facts)

    # 检查空间
    high = [(fs["mount"], fs["pct"]) for fs in filesystems
            if fs["pct"] >= (high_pct - (system_offset if fs["mount"] in system_mounts else 0))]
    if high:
        # D7 修复：与 INODE 路径一致——仅当有系统分区被命中时才在 msg 中说明"系统分区"阈值
        has_system = any(m in system_mounts for m, _ in high)
        high_strs = [f"{m} {p}%" for m, p in high[:8]]
        if has_system:
            msg = f"磁盘使用率过高（阈值 {high_pct}%，系统分区 {high_pct - system_offset}%）：{', '.join(high_strs)}。"
        else:
            msg = f"磁盘使用率过高（阈值 {high_pct}%）：{', '.join(high_strs)}。"
        return ("HIGH", "RISK", msg,
                "磁盘即将满可能导致系统崩溃或数据库损坏，立即清理过期日志/备份，确认备份目录和日志目录不会撑满磁盘。",
                facts)
    medium = [(fs["mount"], fs["pct"]) for fs in filesystems
              if fs["pct"] >= (medium_pct - (system_offset if fs["mount"] in system_mounts else 0))]
    if medium:
        has_system = any(m in system_mounts for m, _ in medium)
        med_strs = [f"{m} {p}%" for m, p in medium[:8]]
        if has_system:
            msg = f"部分挂载点使用率偏高（阈值 {medium_pct}%，系统分区 {medium_pct - system_offset}%）：{', '.join(med_strs)}。"
        else:
            msg = f"部分挂载点使用率偏高（阈值 {medium_pct}%）：{', '.join(med_strs)}。"
        return ("MEDIUM", "WARNING", msg,
                "关注磁盘增长趋势，提前规划扩容或清理。",
                facts)
    # F1 修复：空数据兜底 — 当 SSH 命令未返回任何挂载点/inode 时，
    # 不应误判为 "磁盘空间未发现明显异常"，而应明确提示"未获取到数据"
    if not filesystems and not inodes and not stale_mounts and not out.strip():
        return ("MEDIUM", "WARNING",
                "未获取到磁盘使用率数据（命令执行无输出，可能是 SSH 通道异常、命令路径缺失或非交互式 shell）。",
                "检查 SSH 连接、命令兼容性（`df -PTh` / `df -iPTh`），以及服务器 PATH 是否包含 df/awk。",
                facts)
    if not filesystems and not inodes and not stale_mounts:
        return ("MEDIUM", "WARNING",
                f"未获取到任何挂载点数据（已解析 0 个，原始输出 {len(out.splitlines())} 行），无法评估磁盘风险。",
                "检查命令模板 `df -PTh` / `df -iPTh` 在该服务器是否可执行；确认非交互式 SSH PATH 包含 df/awk。",
                facts)

    return ("NONE", "PASS",
            f"磁盘空间正常（最高使用率 {max_pct}%，低于阈值 {medium_pct}%）" if filesystems else "磁盘空间未发现明显异常。",
            "建议日志和备份目录纳入容量预警。",
            facts)


# P0-4: 容器/伪文件系统黑名单（兜底，DB 配置可覆盖）
_SKIP_FSTYPES = {
    "overlay", "overlayfs", "squashfs", "tmpfs", "devtmpfs",
    "proc", "sysfs", "cgroup", "cgroup2", "ramfs", "autofs",
    "fuse.gvfsd-fuse", "fuse.snapfuse",
}


def _parse_pm2_uptime_seconds(uptime: str) -> int:
    """J4 修复：解析 PM2 `pm2 l` 表格的 uptime 列为秒数。

    PM2 uptime 格式：
    - s = 秒（如 "5s"）
    - m = 分钟（如 "30m"）
    - h = 小时（如 "2h"）
    - D = 天（如 "3D"）
    - M = 月（如 "6M"）
    - Y = 年（如 "1Y"）
    - 纯数字 = 毫秒（如 "0"）

    返回秒数，无法解析返回 -1。
    """
    if not uptime or not isinstance(uptime, str):
        return -1
    u = uptime.strip()
    if not u:
        return -1
    # 纯数字（毫秒）
    if u.isdigit():
        return max(0, int(u) // 1000)
    suffix = u[-1]
    num_str = u[:-1]
    try:
        num = float(num_str)
    except (ValueError, TypeError):
        return -1
    if suffix == "s":
        return int(num)
    elif suffix == "m":
        return int(num * 60)
    elif suffix == "h":
        return int(num * 3600)
    elif suffix == "D":
        return int(num * 86400)
    elif suffix == "M":
        return int(num * 2592000)  # 30 天
    elif suffix == "Y":
        return int(num * 31536000)
    return -1


def _analyze_service(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """E2 修复：扩展支持 PM2 进程管理 + etcd 集群健康 + 非 systemd 进程关键字扫描。

    解析段（按 marker 切分）：
    - ---SYSTEMD_FAILED---: `systemctl --failed` 输出
    - ---SYSTEMD_ACTIVE---: `systemctl is-active <svc>` 行（key: value 格式）
    - ---PM2_JLIST---: `pm2 jlist` JSON 数组 或 `pm2 l` 表格
    - ---PM2_PING---: PM2_PING_OK / PM2_PING_FAIL
    - ---ETCD_HEALTH---: etcd endpoint health JSON 行（含 "is healthy"/"is unhealthy"）
    - ---DOCKER_PS---: `docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}'`（识别 PM2 迁移到 Docker 容器的服务）
    - ---PROCESS_KEYWORDS---: ps -eo pid,user,comm 输出（caddy 等非 systemd 进程）

    判定优先级（取最高等级）：
    1. PM2 ping 失败 + 有 PM2 守护 → HIGH（PM2 自身故障）
    2. PM2 进程 errored（非 Docker 托管）→ HIGH
    3. PM2 进程 stopped（非 Docker 托管）且（name ∈ watch_services 或 expected_processes）→ HIGH；否则 MEDIUM
    4. etcd 集群存在 unhealthy 端点 → HIGH
    4b. Docker 容器 Restarting（崩溃循环）→ MEDIUM；Exited（期望容器）→ LOW
    5. systemd 失败单元 ≥ failed_unit_medium_count → HIGH，< → MEDIUM
    6. caddy/非 systemd 关键字 ps 中缺失且非 Docker 托管 → MEDIUM
    7. watch_services 中任一非 active → LOW
    8. 全 OK → PASS
    """
    import json as _json

    cfg = (thresholds or {}).get("SERVICE_STATUS", {}) or {}
    watch_services = list(cfg.get("watch_services") or cfg.get("core_services") or [
        "nginx", "caddy", "mysql", "mysqld", "mariadb",
        "postgresql", "redis", "redis-server", "pm2", "etcd", "docker",
    ])
    failed_unit_medium_count = int(cfg.get("failed_unit_medium_count", 3))
    pm2_stopped_level = str(cfg.get("pm2_stopped_level", "MEDIUM")).upper()
    pm2_errored_level = str(cfg.get("pm2_errored_level", "HIGH")).upper()
    pm2_expected = set(cfg.get("pm2_expected_processes") or [
        "etcd", "exchange", "exchange-02", "monitor", "promtail",
        "puller", "risk", "risk-02", "sender", "strategy", "strategy-02",
        "supplier", "system", "trader", "transaction", "transaction-02",
    ])
    etcd_unhealthy_level = str(cfg.get("etcd_unhealthy_level", "HIGH")).upper()
    docker_restarting_level = str(cfg.get("docker_restarting_level", "MEDIUM")).upper()
    docker_exited_level = str(cfg.get("docker_exited_level", "LOW")).upper()
    docker_expected = set(cfg.get("docker_expected_containers") or [])
    process_keywords = list(cfg.get("process_keywords") or [
        "nginx", "caddy", "mysql", "postgres", "redis", "etcd", "pm2",
    ])

    # ===== 按段切分 =====
    section = ""
    sections: Dict[str, List[str]] = {}
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("---") and s.endswith("---"):
            section = s.strip("-")
            sections.setdefault(section, [])
            continue
        if section:
            sections[section].append(line)

    # ===== J3 修复：段丢失时 auto-detect 兜底 =====
    # 当 SSH 输出因 echo 失败/截断丢失 ---XXX--- marker 时，sections 字典中
    # 对应 key 缺失或为空，pm2_present/systemd_active/etcd_health 等全部空。
    # 解决：把整个 out 作为兜底输入重新解析（按 set 去重），不破坏已有段切分。
    def _autodetect_fallback(key: str) -> List[str]:
        if sections.get(key):
            return sections[key]
        return list(out.splitlines())

    # ===== 1. 解析 systemd 失败单元 =====
    failed_units: List[str] = []
    for line in sections.get("SYSTEMD_FAILED", []):
        s = line.strip()
        if not s or "UNIT" in s or "LOAD" in s:
            continue
        if s.startswith("0 loaded") or s.startswith("0 failed"):
            continue
        parts = s.split()
        unit_name = ""
        if parts:
            if parts[0].startswith("●") and len(parts) > 1:
                unit_name = parts[1]
            else:
                unit_name = parts[0].lstrip("● ").strip()
        if unit_name and "." in unit_name and unit_name not in failed_units:
            failed_units.append(unit_name)

    # ===== 2. 解析 SYSTEMD_ACTIVE（key: value） =====
    systemd_active: Dict[str, str] = {}
    for line in sections.get("SYSTEMD_ACTIVE", []):
        s = line.strip()
        if ":" not in s:
            continue
        k, v = s.split(":", 1)
        systemd_active[k.strip()] = v.strip()

    # ===== 3. 解析 PM2 jlist / pm2 l =====
    pm2_present = False
    pm2_procs: List[Dict[str, Any]] = []
    pm2_ping_ok = False
    pm2_ping_fail = False
    pm2_ping_lines = sections.get("PM2_PING", [])
    if any("PM2_PING_OK" in l for l in pm2_ping_lines):
        pm2_ping_ok = True
    if any("PM2_PING_FAIL" in l for l in pm2_ping_lines):
        pm2_ping_fail = True

    pm2_jlist_lines = sections.get("PM2_JLIST", [])
    pm2_jlist_text = "\n".join(pm2_jlist_lines).strip()
    # J3 修复：当 PM2_JLIST 段为空时，从整个 out 找 pm2 l 表格行（auto-detect 兜底）
    if not pm2_jlist_text or pm2_jlist_text == "PM2_NOT_FOUND":
        autodetect_lines: List[str] = []
        for line in out.splitlines():
            s = line.strip()
            # pm2 l 表格特征：以 │ 开头（数据/表头）、┌/├/└ 开头（边框）、Module 关键字（表格分隔）
            if s.startswith(("│", "┌", "├", "└")) or s == "Module":
                autodetect_lines.append(line)
        if autodetect_lines:
            pm2_jlist_lines = autodetect_lines
            pm2_jlist_text = "\n".join(pm2_jlist_lines).strip()

    if pm2_jlist_text and pm2_jlist_text != "PM2_NOT_FOUND":
        pm2_present = True
        # 尝试 JSON 解析
        parsed_ok = False
        if pm2_jlist_text.startswith("["):
            try:
                data = _json.loads(pm2_jlist_text)
                if isinstance(data, list):
                    for p in data:
                        if not isinstance(p, dict):
                            continue
                        # pm2 jlist 的 status/restart_time 嵌套在 pm2_env 下
                        env = p.get("pm2_env") or {}
                        pm2_procs.append({
                            "name": str(p.get("name", "")),
                            "status": str(env.get("status", "")).lower() or str(p.get("status", "")).lower(),
                            "pid": p.get("pid", 0) or 0,
                            "restarts": env.get("restart_time", 0) or p.get("restart_time", 0) or 0,
                        })
                    parsed_ok = True
            except (ValueError, TypeError):
                parsed_ok = False
        # 兜底：解析 pm2 l 表格行（以 │ 或 ┌ 开头）
        if not parsed_ok:
            for line in pm2_jlist_lines:
                s = line.strip()
                if not s or s.startswith("┌") or s.startswith("├") or s.startswith("└"):
                    continue
                if s.startswith("│") and "id" not in s and "Module" not in s and "────" not in s:
                    # 解析行：│ 1 │ etcd │ default │ N/A │ fork │ 52549 │ 4M │ 0 │ online │ 0% │ 3.3mb │ root │ disabled │
                    cells = [c.strip() for c in s.split("│") if c.strip()]
                    if len(cells) >= 5 and cells[0].isdigit():
                        # 标准 pm2 l 列序：id, name, namespace, version, mode, pid, uptime, ↺, status, cpu, mem, user, watching
                        # 兼容 5-13 列多种变体
                        status_idx = next((i for i, c in enumerate(cells) if c.lower() in ("online", "stopped", "errored", "error", "launching", "waiting")), -1)
                        # J4 修复：解析 uptime（第 6 列，索引 6）和 restarts（第 7 列，索引 7）
                        uptime_str = cells[6] if len(cells) > 6 else ""
                        restarts_str = cells[7] if len(cells) > 7 else "0"
                        try:
                            restarts = int(restarts_str) if restarts_str.isdigit() else 0
                        except (ValueError, TypeError):
                            restarts = 0
                        uptime_sec = _parse_pm2_uptime_seconds(uptime_str)
                        pm2_procs.append({
                            "name": cells[1] if len(cells) > 1 else "",
                            "status": cells[status_idx].lower() if status_idx >= 0 else "",
                            "pid": int(cells[status_idx - 1]) if status_idx > 0 and cells[status_idx - 1].isdigit() else 0,
                            "restarts": restarts,
                            "uptime": uptime_str,
                            "uptime_sec": uptime_sec,
                        })
    elif any("PM2_NOT_FOUND" in l for l in pm2_jlist_lines):
        pm2_present = False
    else:
        # 没有 PM2_JLIST 段，看 PM2_PING 段有无输出
        pm2_present = bool(pm2_ping_lines) and (pm2_ping_ok or pm2_ping_fail)

    pm2_online: List[Dict[str, Any]] = []
    pm2_stopped: List[Dict[str, Any]] = []
    pm2_errored: List[Dict[str, Any]] = []
    pm2_other: List[Dict[str, Any]] = []
    for p in pm2_procs:
        st = p.get("status", "")
        if st == "online":
            pm2_online.append(p)
        elif st == "stopped":
            pm2_stopped.append(p)
        elif st in ("errored", "error"):
            pm2_errored.append(p)
        else:
            pm2_other.append(p)

    # ===== 3b. J4 修复：统计一天内重启的 PM2 进程 =====
    recent_restart_hours = int(cfg.get("recent_restart_hours", 24))
    recent_restart_seconds = recent_restart_hours * 3600
    recent_restart_level = str(cfg.get("recent_restart_level", "MEDIUM")).upper()
    pm2_recent_restarts: List[Dict[str, Any]] = []
    pm2_total_recent_restarts = 0
    for p in pm2_procs:
        uptime_sec = p.get("uptime_sec", -1)
        restarts = p.get("restarts", 0)
        # uptime < 最近 N 小时且 restarts > 0 → 最近刚发生过重启
        if 0 <= uptime_sec < recent_restart_seconds and restarts > 0:
            pm2_recent_restarts.append({
                "name": p["name"],
                "restarts": restarts,
                "uptime": p.get("uptime", ""),
                "status": p.get("status", ""),
            })
            pm2_total_recent_restarts += restarts

    # ===== 4. 解析 ETCD_HEALTH =====
    etcd_healthy: List[str] = []
    etcd_unhealthy: List[str] = []
    etcd_present = False
    for line in sections.get("ETCD_HEALTH", []):
        s = line.strip()
        if not s or s == "ETCD_NOT_FOUND":
            continue
        etcd_present = True
        # 两种格式：
        # 1) JSON: {"ID":123,"endpoint":"...","status":{"health":"true"/"false","reason":...}}
        # 2) 纯文本: "http://localhost:2379 is healthy: successfully committed proposal"
        if s.startswith("{"):
            try:
                data = _json.loads(s)
                ep = data.get("endpoint", "?")
                health = data.get("status", {}).get("health", "true")
                if str(health).lower() in ("true", "1"):
                    etcd_healthy.append(ep)
                else:
                    etcd_unhealthy.append(ep)
                continue
            except (ValueError, TypeError):
                pass
        m = re.search(r"(\S+)\s+is\s+(healthy|unhealthy)", s, re.IGNORECASE)
        if m:
            ep = m.group(1)
            if m.group(2).lower() == "healthy":
                etcd_healthy.append(ep)
            else:
                etcd_unhealthy.append(ep)

    # ===== 4b. 解析 DOCKER_PS（docker ps -a 容器列表，识别 PM2 迁移到 Docker 的场景） =====
    docker_present = False
    docker_running_names: set = set()
    docker_restarting: List[str] = []
    docker_exited: List[str] = []
    for line in sections.get("DOCKER_PS", []):
        s = line.strip()
        if not s or s == "DOCKER_NOT_FOUND":
            continue
        docker_present = True
        # 格式: Name|Image|Status
        parts = [p.strip() for p in s.split("|")]
        if len(parts) < 3:
            continue
        name, _image, status = parts[0], parts[1], parts[2]
        status_l = status.lower()
        if status_l.startswith("up"):
            docker_running_names.add(name.lower())
        elif status_l.startswith("restarting"):
            docker_restarting.append(name)
        else:
            docker_exited.append(name)
    docker_not_running = docker_restarting + docker_exited
    # 已由 Docker 容器托管的服务名（用于排除 PM2 误报与 ps 缺失）
    docker_managed = {n.lower() for n in docker_running_names}

    # ===== 5. 解析 PROCESS_KEYWORDS（ps -eo pid,user,comm） =====
    ps_seen_keywords: set = set()
    for line in sections.get("PROCESS_KEYWORDS", []):
        s = line.strip()
        if not s:
            continue
        # 取最后一列 comm（或第二列 user 也算），简单按空白切
        parts = s.split()
        if len(parts) < 3:
            continue
        comm = parts[2].lower()
        for kw in process_keywords:
            if kw.lower() in comm:
                ps_seen_keywords.add(kw.lower())
    # 排除由 PM2 管理的服务（PM2 进程通常以 node 启动，comm 中不显示原始服务名）
    pm2_managed_names = {p["name"].lower() for p in pm2_procs}
    # watch_services 中的关键字在 ps 中是否存在
    missing_keywords = [
        kw for kw in watch_services
        if kw.lower() not in ps_seen_keywords
        and kw.lower() not in pm2_managed_names  # PM2 管理的不算缺失
        and kw.lower() not in docker_managed     # Docker 容器托管的不算缺失（PM2 迁移场景）
        and not any(s.lower() == kw.lower() and systemd_active.get(s, "active") == "active" for s in watch_services)
    ]
    # 排除 unknown 状态（systemctl 没装）
    missing_keywords = [
        kw for kw in missing_keywords
        if systemd_active.get(kw, "unknown") not in ("active",)
    ]

    # ===== 6. 判定（优先级从高到低） =====
    facts: Dict[str, Any] = {
        "failed_units": failed_units[:10],
        "systemd_active": systemd_active,
        "pm2_present": pm2_present,
        "pm2_online_count": len(pm2_online),
        "pm2_stopped": [{"name": p["name"], "pid": p["pid"]} for p in pm2_stopped[:10]],
        "pm2_errored": [{"name": p["name"], "pid": p["pid"]} for p in pm2_errored[:10]],
        "pm2_ping_ok": pm2_ping_ok,
        "pm2_ping_fail": pm2_ping_fail,
        "etcd_present": etcd_present,
        "etcd_healthy": etcd_healthy,
        "etcd_unhealthy": etcd_unhealthy,
        "docker_present": docker_present,
        "docker_running": sorted(docker_running_names),
        "docker_restarting": docker_restarting[:10],
        "docker_exited": docker_exited[:10],
        "docker_not_running": docker_not_running[:10],
        "missing_keywords": missing_keywords,
        "watch_services": watch_services,
        # J4 新增：最近 N 小时内重启统计
        "pm2_recent_restarts": pm2_recent_restarts,
        "pm2_total_recent_restarts": pm2_total_recent_restarts,
        "recent_restart_hours": recent_restart_hours,
        "thresholds": {
            "failed_unit_medium_count": failed_unit_medium_count,
            "pm2_stopped_level": pm2_stopped_level,
            "pm2_errored_level": pm2_errored_level,
            "etcd_unhealthy_level": etcd_unhealthy_level,
            "docker_restarting_level": docker_restarting_level,
            "docker_exited_level": docker_exited_level,
            "pm2_expected_processes": sorted(pm2_expected),
            "recent_restart_hours": recent_restart_hours,
            "recent_restart_level": recent_restart_level,
        },
        "criteria": (
            f"PM2 ping 失败 + 守护存活 → HIGH；PM2 进程 errored → {pm2_errored_level}；"
            f"PM2 进程 stopped（{pm2_stopped_level}，期望进程升 HIGH）；"
            f"PM2 最近 {recent_restart_hours}h 内重启 ≥1 次 → {recent_restart_level}；"
            f"etcd 集群 unhealthy → {etcd_unhealthy_level}；"
            f"Docker 容器 Restarting → {docker_restarting_level}、Exited → {docker_exited_level}；"
            f"systemd 失败单元 ≥{failed_unit_medium_count} → HIGH；"
            f"非 systemd 关键字 ps 中缺失且非 Docker 托管 → MEDIUM"
        ),
        "summary": "",  # 后填
    }

    # 候选风险（按等级排序，最终取最高）
    candidates: List[tuple] = []  # (level, status, msg, sug, facts_patch)

    # 1) PM2 守护自身故障
    if pm2_present and pm2_ping_fail:
        candidates.append(("HIGH", "RISK",
                           "PM2 守护进程 ping 失败（PM2 God Daemon 不响应）。",
                           "执行 `pm2 kill && pm2 resurrect` 恢复 PM2 守护；检查 /root/.pm2 日志。",
                           {"pm2_ping_fail_facts": True}))

    # 2) PM2 进程 errored（已迁移到 Docker 容器的同名服务除外）
    pm2_errored_nondocker = [p for p in pm2_errored if p["name"].lower() not in docker_managed]
    if pm2_errored_nondocker:
        names = ", ".join(p["name"] for p in pm2_errored_nondocker[:5])
        candidates.append((pm2_errored_level if pm2_errored_level in {"HIGH", "MEDIUM", "LOW"} else "HIGH",
                           "RISK" if pm2_errored_level == "HIGH" else "WARNING",
                           f"PM2 进程 errored（{len(pm2_errored_nondocker)} 个）：{names}",
                           f"执行 `pm2 logs {pm2_errored_nondocker[0]['name']}` 查看错误；`pm2 restart <name>` 重启。",
                           {"pm2_errored_count": len(pm2_errored_nondocker)}))

    # 3) PM2 进程 stopped（已迁移到 Docker 容器的同名服务除外）
    pm2_stopped_nondocker = [p for p in pm2_stopped if p["name"].lower() not in docker_managed]
    if pm2_stopped_nondocker:
        # 期望进程（来自 cfg 或默认列表）若在 stopped 中 → 升 HIGH
        expected_stopped = [p for p in pm2_stopped_nondocker if p["name"] in pm2_expected]
        if expected_stopped:
            names = ", ".join(p["name"] for p in expected_stopped[:5])
            candidates.append(("HIGH", "RISK",
                               f"PM2 期望进程 stopped（{len(expected_stopped)} 个）：{names}",
                               f"`pm2 start {expected_stopped[0]['name']}` 或 `pm2 resurrect` 恢复；检查进程依赖。",
                               {"expected_stopped_count": len(expected_stopped)}))
        else:
            names = ", ".join(p["name"] for p in pm2_stopped_nondocker[:5])
            candidates.append((pm2_stopped_level if pm2_stopped_level in {"HIGH", "MEDIUM", "LOW"} else "MEDIUM",
                               "WARNING",
                               f"PM2 进程 stopped（{len(pm2_stopped_nondocker)} 个）：{names}",
                               "确认进程是否有意停止；非预期 stopped 应 `pm2 start <name>` 恢复。",
                               {"pm2_stopped_count": len(pm2_stopped_nondocker)}))

    # 3b) J4 修复：PM2 最近 N 小时内重启的进程
    if pm2_recent_restarts:
        names_with_counts = [f"{p['name']}(x{p['restarts']})" for p in pm2_recent_restarts[:5]]
        level = recent_restart_level if recent_restart_level in {"HIGH", "MEDIUM", "LOW"} else "MEDIUM"
        # HIGH 等级对应 RISK，MEDIUM/LOW 等级对应 WARNING
        status = "RISK" if level == "HIGH" else "WARNING"
        candidates.append((level, status,
                           f"PM2 最近 {recent_restart_hours}h 内有 {len(pm2_recent_restarts)} 个进程重启（累计 {pm2_total_recent_restarts} 次）：{', '.join(names_with_counts)}",
                           "执行 `pm2 logs <name>` 查看错误日志，`pm2 monit` 实时监控；如频繁重启可能为代码 bug、内存不足或依赖服务异常。",
                           {"pm2_recent_restart_count": len(pm2_recent_restarts), "pm2_total_recent_restarts": pm2_total_recent_restarts}))

    # 4) etcd 健康
    if etcd_unhealthy:
        candidates.append((etcd_unhealthy_level if etcd_unhealthy_level in {"HIGH", "MEDIUM", "LOW"} else "HIGH",
                           "RISK" if etcd_unhealthy_level == "HIGH" else "WARNING",
                           f"etcd 集群 {len(etcd_unhealthy)}/{len(etcd_unhealthy) + len(etcd_healthy)} 端点 unhealthy：{', '.join(etcd_unhealthy[:3])}",
                           "检查 etcd 进程状态、leader 选举、磁盘空间；`etcdctl member list` 查看成员。",
                           {"etcd_unhealthy_count": len(etcd_unhealthy)}))

    # 4b) Docker 容器 Restarting（崩溃循环）
    if docker_restarting:
        names = ", ".join(docker_restarting[:5])
        candidates.append((docker_restarting_level if docker_restarting_level in {"HIGH", "MEDIUM", "LOW"} else "MEDIUM",
                           "RISK" if docker_restarting_level == "HIGH" else "WARNING",
                           f"Docker 容器崩溃循环 Restarting（{len(docker_restarting)} 个）：{names}",
                           "执行 `docker logs <name>`、`docker inspect <name>` 定位崩溃原因；修复后 `docker restart <name>` 或重启 compose 恢复。",
                           {"docker_restarting_count": len(docker_restarting)}))

    # 4c) Docker 容器 Exited（期望常驻容器未运行）
    docker_exited_expected = [c for c in docker_exited if (c.lower() in docker_expected or not docker_expected)]
    if docker_exited_expected:
        names = ", ".join(docker_exited_expected[:5])
        candidates.append((docker_exited_level if docker_exited_level in {"HIGH", "MEDIUM", "LOW"} else "LOW",
                           "WARNING",
                           f"Docker 容器未运行 Exited（{len(docker_exited_expected)} 个）：{names}",
                           "确认容器是否有意停止；期望常驻的容器用 `docker start <name>` 或重启 compose 恢复。",
                           {"docker_exited_count": len(docker_exited_expected)}))

    # 5) systemd 失败单元
    if failed_units:
        if len(failed_units) >= failed_unit_medium_count:
            candidates.append(("HIGH", "RISK",
                               f"systemd 失败单元 {len(failed_units)} 个（≥{failed_unit_medium_count}）：{', '.join(failed_units[:5])}",
                               "查看 journalctl -u <unit> 排查根因，重点关注依赖关系与启动顺序。",
                               {"failed_unit_count": len(failed_units)}))
        else:
            candidates.append(("MEDIUM", "WARNING",
                               f"systemd 失败单元 {len(failed_units)} 个：{', '.join(failed_units[:5])}",
                               "查看 journalctl -u <unit> 排查根因。",
                               {"failed_unit_count": len(failed_units)}))

    # 6) 非 systemd 关键字 ps 中缺失（caddy/etcd 二进制部署，且非 Docker 容器托管）
    if missing_keywords:
        candidates.append(("MEDIUM", "WARNING",
                           f"关键进程 ps 中未发现：{', '.join(missing_keywords[:5])}",
                           "确认进程是否用其他方式部署（容器、二进制、systemd），或已迁移到 PM2/Docker。",
                           {"missing_keywords": missing_keywords[:5]}))

    # 7) watch_services 中非 active（信息性）
    # 但 PM2 管理的服务不算 inactive（etcd 等由 PM2 进程托管，systemd 状态无关）
    # ps 关键字已扫到的服务（如 nginx 二进制部署）也不算 inactive
    inactive_watches = [
        s for s in watch_services
        if systemd_active.get(s) in ("inactive", "failed", "unknown")
        and s.lower() not in pm2_managed_names
        and s.lower() not in ps_seen_keywords
        and s.lower() not in docker_managed  # Docker 容器托管的不算 inactive（PM2 迁移场景）
    ]
    if inactive_watches and not candidates:
        candidates.append(("LOW", "WARNING",
                           f"watch_services 状态非 active：{', '.join(inactive_watches[:5])}",
                           "若进程由 PM2/容器管理可忽略；否则启动对应服务。",
                           {"inactive_watches": inactive_watches[:5]}))

    # ===== 汇总 =====
    risk_order = {"HIGH": 4, "MEDIUM": 3, "LOW": 2, "NONE": 1}
    candidates.sort(key=lambda c: -risk_order.get(c[0], 0))
    if not candidates:
        # F3 修复：空数据兜底 — 未获取到任何服务/PM2 数据时明确提示
        if not pm2_procs and not failed_units and not systemd_active and not etcd_healthy and not etcd_unhealthy and not missing_keywords and not out.strip():
            return ("MEDIUM", "WARNING",
                    "未获取到服务状态数据（命令执行无输出，可能是 SSH 通道异常、命令路径缺失或非交互式 shell）。",
                    "检查 SSH 连接、`systemctl`/`pm2`/`etcdctl`/`ps` 在该服务器是否可执行；确认非交互式 SSH PATH 包含相应命令。",
                    facts)
        if not pm2_procs and not failed_units and not systemd_active and not etcd_healthy and not etcd_unhealthy and not missing_keywords:
            return ("MEDIUM", "WARNING",
                    f"未获取到任何服务/PM2 数据（已解析 0 个，原始输出 {len(out.splitlines())} 行），无法评估服务运行状态。",
                    "检查命令模板 `systemctl --failed` / `pm2 jlist` / `etcdctl endpoint health` / `ps -eo ...` 在该服务器是否可执行。",
                    facts)
        return ("NONE", "PASS",
                f"服务与 PM2 状态正常（watch={len(watch_services)}，PM2 online={len(pm2_online)}{', etcd OK' if etcd_healthy else ''}{f'，{recent_restart_hours}h 重启 {pm2_total_recent_restarts} 次' if pm2_total_recent_restarts else ''}）。",
                "持续关注关键服务日志。",
                facts)

    top = candidates[0]
    # 合并所有 candidates 的 facts 补丁（包括 top）
    for c in candidates:
        if len(c) >= 5 and isinstance(c[4], dict):
            facts.update(c[4])
    # summary
    summary_parts = []
    if pm2_present:
        summary_parts.append(f"PM2 online={len(pm2_online)} stopped={len(pm2_stopped)} errored={len(pm2_errored)}")
        if pm2_total_recent_restarts:
            summary_parts.append(f"{recent_restart_hours}h 重启 {pm2_total_recent_restarts} 次")
    if failed_units:
        summary_parts.append(f"systemd 失败 {len(failed_units)}")
    if etcd_unhealthy:
        summary_parts.append(f"etcd unhealthy {len(etcd_unhealthy)}")
    if docker_present:
        summary_parts.append(f"Docker up={len(docker_running_names)} rest={len(docker_restarting)} exited={len(docker_exited)}")
    if missing_keywords:
        summary_parts.append(f"ps 缺 {','.join(missing_keywords[:3])}")
    facts["summary"] = "；".join(summary_parts) if summary_parts else f"watch={len(watch_services)}"
    return (top[0], top[1], top[2], top[3], facts)


def _analyze_backup(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """P2-3 增强：支持 cron.d / cron.daily/weekly/monthly / systemd timer；按粒度选择 min_age。"""
    cron_lines: List[str] = []
    cron_d_lines: List[str] = []
    timer_lines: List[str] = []
    backup_dirs: List[Dict[str, Any]] = []
    in_cron = False
    in_cron_d = False
    in_timer = False
    in_backups = False
    in_now = False
    current_dir = ""
    now_ts: Optional[int] = None

    for line in out.splitlines():
        s = line.strip()
        if s == "---CRON---":
            in_cron = True
            in_cron_d = False
            in_timer = False
            in_backups = False
            in_now = False
            continue
        if s == "---CRON_D---":
            in_cron = False
            in_cron_d = True
            in_timer = False
            in_backups = False
            in_now = False
            continue
        if s == "---TIMER---":
            in_cron = False
            in_cron_d = False
            in_timer = True
            in_backups = False
            in_now = False
            continue
        if s == "---BACKUPS---":
            in_cron = False
            in_cron_d = False
            in_timer = False
            in_backups = True
            in_now = False
            continue
        if s == "---NOW---":
            in_cron = False
            in_cron_d = False
            in_timer = False
            in_backups = False
            in_now = True
            continue
        if s.startswith("---"):
            in_cron = False
            in_cron_d = False
            in_timer = False
            in_backups = False
            in_now = False
            continue

        if in_now and s:
            try:
                now_ts = int(s.strip())
            except ValueError:
                now_ts = None
            continue

        if in_cron and s:
            cron_lines.append(s)
        if in_cron_d and s:
            cron_d_lines.append(s)
        if in_timer and s and "NEXT" not in s.upper() and "PASSED" not in s.upper() and "ACTIVATES" not in s.upper() and "UNIT" not in s.upper():
            timer_lines.append(s)
        if in_backups and s:
            # 新格式：path size mtime(YYYY-MM-DDTHH:MM)；老格式：path size
            parts = s.rsplit(None, 2)
            size = 0
            mtime_str = ""
            path = s
            if len(parts) == 3:
                p, sz, mt = parts
                path = p
                try:
                    size = int(sz)
                except ValueError:
                    size = 0
                mtime_str = mt
            elif len(parts) == 2:
                p, sz = parts
                path = p
                try:
                    size = int(sz)
                except ValueError:
                    size = 0
            dir_path = "/".join(path.split("/")[:3]) or path
            if not current_dir or dir_path != current_dir:
                current_dir = dir_path
                backup_dirs.append({"path": dir_path, "exists": True, "files": []})
            if backup_dirs:
                backup_dirs[-1]["files"].append({"name": path, "size": size, "mtime": mtime_str})

    cfg = (thresholds or {}).get("BACKUP", {}) or {}
    backup_paths = list(cfg.get("backup_paths") or DEFAULT_THRESHOLDS["BACKUP"]["backup_paths"])
    min_age_days_daily = int(cfg.get("min_age_days_daily", 1))
    min_age_days_weekly = int(cfg.get("min_age_days_weekly", 8))
    min_age_days_monthly = int(cfg.get("min_age_days_monthly", 32))
    # 兼容老字段
    if "min_age_days" in cfg and "min_age_days_daily" not in cfg:
        min_age_days_daily = int(cfg.get("min_age_days", 1))

    # 检测0字节备份文件
    zero_byte_files = []
    for d in backup_dirs:
        for f in (d.get("files") or []):
            if f.get("size", -1) == 0:
                zero_byte_files.append(f.get("name", "unknown"))

    # A4 修复：按粒度选择 min_age 判定"最近一次备份是否过期"
    # 粒度检测：
    #  - 出现 cron.daily 或 systemd timer 频次 < 2 天  → daily
    #  - 出现 cron.weekly 或 timer OnCalendar=weekly  → weekly
    #  - 出现 cron.monthly 或 timer OnCalendar=monthly → monthly
    #  - 兜底：按 cron_lines 中关键字（day/week/month）推断，否则 daily
    granularity = "daily"
    cron_d_blob = "\n".join(cron_d_lines).lower()
    timer_blob = "\n".join(timer_lines).lower()
    if "cron.monthly" in cron_d_blob or "monthly" in timer_blob or "monthly" in cron_d_blob:
        granularity = "monthly"
    elif "cron.weekly" in cron_d_blob or "weekly" in timer_blob or "weekly" in cron_d_blob:
        granularity = "weekly"
    elif "cron.daily" in cron_d_blob or "daily" in timer_blob or "daily" in cron_d_blob:
        granularity = "daily"

    min_age_days = {
        "daily": min_age_days_daily,
        "weekly": min_age_days_weekly,
        "monthly": min_age_days_monthly,
    }[granularity]

    # 找最近一次备份的 mtime
    latest_mtime_ts: Optional[int] = None
    latest_file: Optional[str] = None
    for d in backup_dirs:
        for f in (d.get("files") or []):
            mt = f.get("mtime", "")
            if not mt:
                continue
            try:
                ts = int(datetime.strptime(mt, "%Y-%m-%dT%H:%M").timestamp())
            except (ValueError, TypeError):
                continue
            if latest_mtime_ts is None or ts > latest_mtime_ts:
                latest_mtime_ts = ts
                latest_file = f.get("name")
    if now_ts is None:
        now_ts = int(time.time())
    if latest_mtime_ts is None:
        age_days = None
        age_stale = False
    else:
        age_days = (now_ts - latest_mtime_ts) / 86400.0
        age_stale = age_days > min_age_days

    facts = {
        "backup_dirs": backup_dirs[:10],
        "cron_lines": cron_lines[:20],
        "cron_d_lines": cron_d_lines[:20],
        "timer_lines": timer_lines[:10],
        "zero_byte_files": zero_byte_files[:10],
        "granularity": granularity,
        "min_age_days": min_age_days,
        "latest_backup": latest_file,
        "latest_age_days": round(age_days, 1) if age_days is not None else None,
        "thresholds": {"backup_paths": backup_paths, "min_age_days_daily": min_age_days_daily, "min_age_days_weekly": min_age_days_weekly, "min_age_days_monthly": min_age_days_monthly},
        "criteria": f"按粒度({granularity})选 min_age={min_age_days} 天；最近一次备份 > {min_age_days} 天 → MEDIUM；无 cron + 无文件 → HIGH",
        "summary": f"{len(cron_lines)} crontab + {len(cron_d_lines)} cron.d + {len(timer_lines)} timer；{sum(len(d.get('files', [])) for d in backup_dirs)} 个备份文件" + (f"；最近 {age_days:.1f} 天前" if age_days is not None else "") + (f"；{len(zero_byte_files)} 个空文件" if zero_byte_files else ""),
    }

    has_cron = bool(cron_lines or cron_d_lines or timer_lines)
    has_backups = any(d.get("files") for d in backup_dirs)

    # B6: 应用服务器（无 cron + 无备份）对偏严场景降级为 MEDIUM
    # 应用服务器的特征：watch_services 中存在业务服务，或显式 cfg 标记
    is_application_server = bool(cfg.get("is_application_server", False))

    if not has_cron and not has_backups:
        if is_application_server:
            return ("MEDIUM", "WARNING",
                    f"应用服务器未发现备份任务或近期备份文件（检查路径：{backup_paths}）。",
                    "应用服务器建议至少配置每日应用数据快照与异地同步；非数据库服务器可适度放宽。",
                    facts)
        return ("HIGH", "RISK",
                f"未发现备份任务或近期备份文件（检查路径：{backup_paths}），生产环境无备份是严重风险。",
                "立即配置备份任务和备份路径；生产项目需每日核查备份任务和文件有效性。",
                facts)
    if not has_cron:
        return ("MEDIUM", "WARNING",
                "未发现当前用户 crontab 备份任务（但存在备份文件）。",
                "确认备份任务是否由其他调度系统执行，否则需配置定时备份。",
                facts)
    if not has_backups:
        return ("MEDIUM", "WARNING",
                "发现备份 cron 任务，但未找到近期备份文件，备份可能未正常执行。",
                "核查备份任务是否正常执行，检查备份目标路径和执行日志。",
                facts)
    if zero_byte_files:
        return ("MEDIUM", "WARNING",
                f"发现 {len(zero_byte_files)} 个 0 字节备份文件（可能损坏）：{', '.join(zero_byte_files[:5])}",
                "核查0字节文件原因，确认备份任务输出是否正常，检查磁盘空间是否已满。",
                facts)
    if age_stale:
        return ("MEDIUM", "WARNING",
                f"最近一次备份 {age_days:.1f} 天前（粒度 {granularity}，阈值 {min_age_days} 天）：{latest_file}",
                f"核查备份任务调度（应为 {granularity} 频次），检查执行日志和目标路径写入权限。",
                facts)
    age_str = f"{age_days:.1f} 天前" if age_days is not None else "时间未知"
    return ("NONE", "PASS",
            f"备份任务正常，最近一次备份 {age_str}（粒度 {granularity}，阈值 {min_age_days} 天）。",
            "建议每周抽检备份文件可恢复性。",
            facts)


def _analyze_fail2ban(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """安全检查：Fail2ban 暴力破解防护状态与配置。

    参考服务器安全加固配置（Fail2ban 组件）：检测服务是否启用、ssh 相关
    jail 是否活动、maxretry/findtime/bantime/ignoreip 阈值是否过松、当前
    封禁 IP 数量。仅做只读解析，不修改 fail2ban 配置。
    """
    cfg = (thresholds or {}).get("FAIL2BAN", {}) or {}
    max_retry_medium = int(cfg.get("max_retry_medium", 10) or 10)
    min_bantime_minutes = int(cfg.get("min_bantime_minutes", 60) or 60)
    banned_high = int(cfg.get("banned_high", 20) or 20)
    text = out.lower()
    sections: Dict[str, str] = {}
    current = "service"
    for line in out.splitlines():
        if line.startswith("---") and line.endswith("---"):
            key = line.strip("-").strip().lower()
            sections[key] = ""
            current = key
        else:
            sections.setdefault(current, "")
            sections[current] += line + "\n"

    service = sections.get("service", "").strip()
    bin_check = sections.get("bin", "").strip()
    status = sections.get("status", "").strip()
    jail = sections.get("jail_sshd", "").strip()
    config = sections.get("config", "").strip()
    banned_out = sections.get("banned", "").strip()

    facts = {
        "raw_snippet": out[:500],
        "service": service or "UNKNOWN",
        # 二进制是否存在（command -v fail2ban-client）才判定"是否安装"
        "installed": bool(bin_check) and bin_check.lower() != "fail2ban_bin_not_found"
                     and "command not found" not in text and "no such file" not in text,
        "fail2ban_found": "fail2ban_daemon_down" not in text,
        "jails": [ln.strip() for ln in status.splitlines() if ln.strip().lower().startswith("- jail list")],
        "sshd_jail_active": jail and "NO_SSHD_JAIL" not in jail and "not found" not in jail.lower(),
        "banned_count": _parse_f2b_banned_count(banned_out),
        "thresholds": {
            "max_retry_medium": max_retry_medium,
            "min_bantime_minutes": min_bantime_minutes,
            "banned_high": banned_high,
        },
        "criteria": "fail2ban 二进制不存在 → HIGH（未安装）；已安装但 daemon 未运行 → HIGH；无 sshd jail → MEDIUM；maxretry>阈值或 bantime 过短 → MEDIUM；封禁 IP 数≥阈值 → LOW",
        "summary": "",
    }

    # 1) 真正未安装：二进制不存在
    if not facts["installed"]:
        facts["summary"] = "未安装 fail2ban-client"
        return ("HIGH", "RISK",
                "未检测到 Fail2ban（暴力破解防护未安装）。",
                "建议安装并启用 Fail2ban，配置 sshd jail 以自动封禁 SSH 暴力破解来源。参考：INSTALL_FAIL2BAN=yes。",
                facts)

    # 2) 已安装但服务/daemon 未运行：fail2ban-client 无法连接服务端，或 systemctl 非 active
    daemon_down = "fail2ban_daemon_down" in text or "no connection to fail2ban" in text or "is the fail2ban daemon running" in text
    service_up = service in {"active", "exited"} or (service and service not in {"", "unknown", "inactive", "dead", "stopped", "failed"})
    if daemon_down or not service_up:
        facts["summary"] = f"已安装 fail2ban 但服务未运行（service={service or 'unknown'}）"
        return ("HIGH", "RISK",
                f"Fail2ban 已安装但服务未启用或未运行（暴力破解防护未生效，状态：{service or 'unknown'}）。",
                "启用并启动 fail2ban 服务并设置为开机自启：systemctl enable --now fail2ban。",
                facts)

    if not facts["sshd_jail_active"]:
        facts["summary"] = "未发现活动的 sshd jail"
        return ("MEDIUM", "WARNING",
                "Fail2ban 已运行，但未发现活动的 sshd jail（SSH 暴力破解未受防护）。",
                "在 jail.local 中启用 sshd jail：[sshd] enabled = true。",
                facts)

    # 解析配置阈值
    maxretry = _get_f2b_config_int(config, "maxretry", default=None)
    findtime = _get_f2b_config_str(config, "findtime")
    bantime = _get_f2b_config_str(config, "bantime")
    bantime_minutes = _parse_bantime_minutes(bantime)
    if maxretry is not None and maxretry > max_retry_medium:
        facts["summary"] = f"maxretry={maxretry} 超过阈值 {max_retry_medium}"
        return ("MEDIUM", "WARNING",
                f"Fail2ban maxretry={maxretry} 过松（阈值 {max_retry_medium}），暴力破解需更多失败次数才触发封禁。",
                f"将 maxretry 收紧到 ≤{max_retry_medium}。",
                facts)
    if bantime_minutes is not None and bantime_minutes < min_bantime_minutes:
        facts["summary"] = f"bantime={bantime} 短于阈值 {min_bantime_minutes} 分钟"
        return ("MEDIUM", "WARNING",
                f"Fail2ban bantime={bantime or '未知'} 过短（阈值 {min_bantime_minutes} 分钟），封禁时效不足。",
                f"将 bantime 提高到 ≥{min_bantime_minutes} 分钟。",
                facts)

    banned_count = facts["banned_count"]
    findings = []
    if banned_count is not None and banned_count >= banned_high:
        findings.append(f"当前被封禁 IP {banned_count} 个（≥{banned_high}），疑似遭受暴力破解。")
        facts["summary"] = f"封禁 IP {banned_count} 个"
        return ("LOW", "INFO", "；".join(findings),
                "核查封禁 IP 来源，确认是否为恶意扫描；必要时扩展示弱口令/封禁范围。",
                facts)

    facts["summary"] = "Fail2ban 运行正常，sshd jail 活动，配置阈值合理"
    return ("NONE", "PASS",
            "Fail2ban 暴力破解防护运行正常，sshd jail 活动，阈值配置合理。",
            "定期复核 jail 状态与封禁名单，保持阈值合理收紧。",
            facts)


def _analyze_auditd(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """安全检查：auditd 安全审计服务与审计规则覆盖。

    参考服务器安全加固配置（auditd 组件）：检测 auditd 是否启用、审计是否
    开启、是否有关键文件（passwd/shadow/ssh 配置/cron/.ssh）与危险命令审计
    规则。仅做只读解析。
    """
    cfg = (thresholds or {}).get("AUDITD", {}) or {}
    text = out.lower()
    sections: Dict[str, str] = {}
    current = "service"
    for line in out.splitlines():
        if line.startswith("---") and line.endswith("---"):
            key = line.strip("-").strip().lower()
            sections[key] = ""
            current = key
        else:
            sections.setdefault(current, "")
            sections[current] += line + "\n"

    service = sections.get("service", "").strip()
    status = sections.get("status", "").strip()
    rules = sections.get("rules", "").strip()
    rules_dir = sections.get("rules_dir", "").strip()

    facts = {
        "raw_snippet": out[:500],
        "service": service or "UNKNOWN",
        "auditd_found": "AUDITCTL_NOT_FOUND" not in text,
        "enabled": "enabled 1" in status or "enabled  -1" in status or "enabled 1" in text,
        "rule_count": len([ln for ln in rules.splitlines() if ln.strip() and not ln.strip().lower().startswith("no rules")]),
        "key_files_covered": _audit_rules_cover_key_sources(rules + "\n" + rules_dir),
        "criteria": "auditd 未安装/未启用或审计关闭 → HIGH；无审计规则 → HIGH；关键文件未覆盖 → MEDIUM；危险命令审计缺失 → LOW",
        "summary": "",
    }
    if "AUDITCTL_NOT_FOUND" in text or "command not found" in text:
        facts["summary"] = "未安装或无法调用 auditctl"
        return ("HIGH", "RISK",
                "未检测到 auditd 审计工具（安全审计未安装）。",
                "建议安装并启用 auditd，配置关键文件与命令执行审计规则。参考：INSTALL_AUDITD=yes。",
                facts)
    if not service or service in {"", "unknown", "inactive", "dead", "stopped"}:
        facts["summary"] = f"auditd 服务状态：{service or 'unknown'}"
        return ("HIGH", "RISK",
                f"auditd 审计服务未运行（状态：{service or 'unknown'}）。",
                "启用 auditd 服务并设置为开机自启。",
                facts)
    if not facts["enabled"]:
        facts["summary"] = "审计未开启"
        return ("HIGH", "RISK",
                "auditd 服务已安装但审计未开启（auditctl enabled 非 1）。",
                "执行 auditctl -e 1 开启审计，并确认 rules.d 规则已加载。",
                facts)
    if facts["rule_count"] == 0:
        facts["summary"] = "无审计规则"
        return ("HIGH", "RISK",
                "auditd 审计已开启但未配置任何审计规则。",
                "在 /etc/audit/rules.d/security.rules 添加关键文件与命令执行审计规则。",
                facts)
    is_complete = facts["key_files_covered"]
    if not is_complete["complete"]:
        missing = "、".join(is_complete["missing"])
        facts["summary"] = f"关键文件审计未覆盖：{missing}"
        return ("MEDIUM", "WARNING",
                f"auditd 已启用但关键文件审计未完全覆盖（缺失：{missing}）。",
                "补充对 passwd/shadow/sshd_config/cron/.ssh 的 -w 审计规则。",
                facts)
    facts["summary"] = "auditd 运行正常，关键文件审计规则完整"
    return ("NONE", "PASS",
            "auditd 安全审计运行正常，审计规则已覆盖关键文件。",
            "定期复核审计规则与日志轮转，确保证据链完整。",
            facts)


def _analyze_key_file_security(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """安全检查：关键文件权限、属主与完整性。

    参考服务器安全加固配置（auditd 关键文件监控）中重点看守的文件：
    passwd / shadow / gshadow / group / sshd_config / crontab / cron.d / .ssh。
    检查权限过宽、属主异常、ssh 弱配置与文件缺失。仅做只读解析。
    """
    cfg = (thresholds or {}).get("KEY_FILE_SECURITY", {}) or {}
    text = out.lower()
    sections: Dict[str, str] = {}
    current = "keyfiles"
    for line in out.splitlines():
        if line.startswith("---") and line.endswith("---"):
            key = line.strip("-").strip().lower()
            sections[key] = ""
            current = key
        else:
            sections.setdefault(current, "")
            sections[current] += line + "\n"

    keyfile_text = sections.get("keyfiles", "")
    sshd_text = sections.get("sshd_config", "")
    f2b_conf = sections.get("fail2ban_conf", "")

    # 解析 stat 行：%n %U:%G %a %s
    files: Dict[str, Dict[str, str]] = {}
    for line in keyfile_text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            if line.strip().endswith("MISSING"):
                files[line.split()[0]] = {"missing": "true"}
            continue
        path = parts[0]
        files[path] = {
            "owner": parts[1].split(":")[0],
            "group": parts[1].split(":")[1] if ":" in parts[1] else "",
            "mode": parts[2],
            "size": parts[3],
        }

    facts = {
        "raw_snippet": out[:500],
        "files": dict(list(files.items())[:20]),
        "world_writable": [],
        "world_readable_shadow": False,
        "non_root_owner": [],
        "sshd_weak": [],
        "missing": [p for p, m in files.items() if m.get("missing")],
        "criteria": "shadow 全局可读 → HIGH；passwd/.ssh/cron 全局可写 → HIGH；属主异常/弱 sshd 配置/文件缺失 → MEDIUM",
        "summary": "",
    }

    # shadow 可读判定（取"其他用户"权限位，即八进制最后一位）
    shadow = files.get("/etc/shadow") or files.get("shadow")
    if shadow and not shadow.get("missing"):
        mode = shadow.get("mode", "")
        owner = shadow.get("owner", "")
        others = _perm_others(mode)
        if others in {"4", "5", "6", "7"}:
            facts["world_readable_shadow"] = True
        if others in {"2", "3", "6", "7"}:
            facts["world_writable"].append("/etc/shadow")
        if owner != "root":
            facts["non_root_owner"].append("/etc/shadow")

    for path, meta in files.items():
        if meta.get("missing"):
            continue
        mode = meta.get("mode", "")
        owner = meta.get("owner", "")
        others = _perm_others(mode)
        if others in {"2", "3", "6", "7"}:
            if path in {"/etc/passwd", "/etc/gshadow", "/etc/group", "/etc/crontab", "/root/.ssh", "/etc/cron.d"}:
                facts["world_writable"].append(path)
        if owner != "root" and path not in {"/root/.ssh"}:
            facts["non_root_owner"].append(path)

    # sshd 弱配置
    if sshd_text and sshd_text != "no_sshd_config":
        for line in sshd_text.splitlines():
            sl = line.strip().lower()
            if sl.startswith("permitrootlogin") and " no" not in sl and "prohibit" not in sl:
                facts["sshd_weak"].append("PermitRootLogin yes")
            if sl.startswith("passwordauthentication") and " no" not in sl:
                facts["sshd_weak"].append("PasswordAuthentication yes")
            if sl.startswith("permitemptypasswords") and " no" not in sl:
                facts["sshd_weak"].append("PermitEmptyPasswords yes")

    missing = [p for p in facts["missing"] if p not in {"/etc/fail2ban/jail.local"}]
    if facts["world_readable_shadow"]:
        facts["summary"] = "/etc/shadow 全局可读"
        return ("HIGH", "RISK",
                "/etc/shadow 权限过宽（其他用户可读），密码哈希存在泄露风险。",
                "收紧权限：chmod 640 /etc/shadow（建议 600，属主 root:shadow）。",
                facts)
    if facts["world_writable"]:
        ww = "、".join(facts["world_writable"])
        facts["summary"] = f"全局可写关键文件：{ww}"
        return ("HIGH", "RISK",
                f"关键文件权限过宽（全局可写）：{ww}。",
                "收紧写入权限：chmod 644 passwd/group、chmod 755 目录、chmod 600 .ssh 私钥。",
                facts)
    issues = []
    if facts["non_root_owner"]:
        issues.append(f"属主异常：{'、'.join(facts['non_root_owner'][:5])}")
    if facts["sshd_weak"]:
        issues.append(f"SSH 弱配置：{'、'.join(facts['sshd_weak'][:5])}")
    if missing:
        issues.append(f"关键文件缺失：{'、'.join(missing[:5])}")
    if issues:
        facts["summary"] = "、".join(issues)
        return ("MEDIUM", "WARNING", "；".join(issues),
                "修复属主为 root、收紧 sshd 弱配置（禁用 root 密码登录、关空密码）、确认关键文件存在。",
                facts)
    facts["summary"] = "关键文件权限与属主正常"
    return ("NONE", "PASS",
            "关键文件权限、属主与 SSH 配置未发现明显异常。",
            "定期复核关键文件权限与 sshd 配置，保持最小化暴露。",
            facts)


def _perm_others(mode: str) -> str:
    """取八进制权限字符串中"其他用户"权限位（最后一位），如 644 → '4'。"""
    m = str(mode or "").strip()
    if not m or not m[-1].isdigit():
        return ""
    return m[-1]


def _parse_f2b_banned_count(banned_out: str) -> Optional[int]:
    """从 fail2ban-client status <jail> 输出解析 Banned IP list 数量。"""
    for line in banned_out.splitlines():
        sl = line.strip()
        if sl.lower().startswith("banned ip list:"):
            rest = sl.split(":", 1)[1].strip()
            if not rest or rest.lower() in {"0", "none", "empty"}:
                return 0
            return len([x for x in rest.split() if x])
    return None


def _get_f2b_config_int(config: str, key: str, default: Optional[int] = None) -> Optional[int]:
    """从 fail2ban 配置文本提取整数型配置项。"""
    for line in config.splitlines():
        sl = line.strip()
        if sl.lower().startswith(key + " ") or sl.lower().startswith(key + "="):
            val = sl.split("=", 1)[-1].strip()
            for tok in re.split(r"[,\s]+", val):
                if tok.isdigit():
                    return int(tok)
    return default


def _get_f2b_config_str(config: str, key: str) -> Optional[str]:
    """从 fail2ban 配置文本提取字符串型配置项。"""
    for line in config.splitlines():
        sl = line.strip()
        if sl.lower().startswith(key + " ") or sl.lower().startswith(key + "="):
            val = sl.split("=", 1)[-1].strip()
            if val:
                return val.split()[0]
    return None


def _parse_bantime_minutes(bantime: Optional[str]) -> Optional[float]:
    """将 fail2ban bantime 字符串解析为分钟数（支持 -1/1h/1d/300 秒）。"""
    if bantime is None:
        return None
    bt = str(bantime).strip().lower()
    if bt in {"-1", "forever", "2147483647"}:
        return 24 * 60 * 365 * 10
    try:
        if bt.endswith("d"):
            return float(bt[:-1]) * 24 * 60
        if bt.endswith("h"):
            return float(bt[:-1]) * 60
        if bt.endswith("m"):
            return float(bt[:-1])
        if bt.endswith("s"):
            return float(bt[:-1]) / 60.0
        return float(bt) / 60.0
    except ValueError:
        return None


# 参考安全加固配置中 auditd 重点看守的关键文件特征
_AUDIT_KEY_SOURCES = {
    "/etc/passwd": ("passwd", "-w"),
    "/etc/shadow": ("shadow", "-w"),
    "/etc/ssh/sshd_config": ("sshd_config", "-w"),
    "/etc/crontab": ("crontab", "-w"),
    "/root/.ssh": (".ssh", "-w"),
}


def _audit_rules_cover_key_sources(rules_text: str) -> Dict[str, Any]:
    """检查审计规则文本是否覆盖关键文件与账号/命令审计。

    返回 {"complete": bool, "covered": list, "missing": list}。
    """
    text = rules_text.lower()
    missing = []
    covered = []
    for path, (_label, _kind) in _AUDIT_KEY_SOURCES.items():
        if path in text:
            covered.append(path)
        else:
            missing.append(path)
    # 关键目录出现即视为覆盖（如 -w /etc/passwd）
    if "/etc/passwd" in text:
        if "/etc/passwd" in covered:
            pass
    # 账号变更/命令执行审计（-w /etc 或 -a always,exit -S execve 等）
    has_account_audit = "account" in text or "usr" in text or "auth" in text
    has_exec_audit = ("execve" in text or "exec" in text or "clone" in text or "fork" in text)
    return {
        "complete": not missing,
        "covered": covered,
        "missing": missing,
        "account_audit": has_account_audit,
        "exec_audit": has_exec_audit,
    }


# ============================================================
# 自定义规则（来自 InspectionRule）的通用 analyzer
# ============================================================

def _extract_command_from_content(rule_content: Optional[str]) -> str:
    """从 InspectionRule.rule_content 中提取可执行的 shell 命令。

    rule_content 通常包含说明/注释（# 开头）和命令。
    解析逻辑：忽略空行、纯注释行（#），拼接剩余行；保留 `echo`/`cat` 等。
    """
    if not rule_content:
        return ""
    lines = []
    for raw in str(rule_content).splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


# 自定义命令：常用关键字与路径的噪点过滤 + 词边界正则
_NOISY_PATH_RE = re.compile(
    r"/(var/log|error[._-]?log|access[._-]?log|error\.log|nginx[._-]?error)\b",
    re.IGNORECASE,
)
_ERROR_WORD_RE = re.compile(
    r"\b(error|fail|critical|panic|fatal|denied|exception|traceback)\b",
    re.IGNORECASE,
)
_COUNT_ZERO_RE = re.compile(
    r"\b(error|fail|err|fatal|panic)[_\-]?(count|num|n|total)?\s*[:=]\s*0\b",
    re.IGNORECASE,
)
# B1: 脚本/命令不存在类硬错误 → 强制 HIGH
_MISSING_CMD_RE = re.compile(
    r"(command not found|No such file or directory|not found in|无法找到命令|命令未找到|没有那个文件或目录|:\s*command\s+not\s+found)",
    re.IGNORECASE,
)


def _analyze_custom_command(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """P0-3 修复：容忍 grep exit=1、词边界匹配、过滤噪点路径。
    B1 修复：脚本/命令不存在（command not found / No such file）→ 强制 HIGH。

    判定逻辑（优先级从高到低）：
    - stderr 命中 command not found / No such file → HIGH（脚本不存在，不应静默）
    - exit ≥ 2 → HIGH
    - 命中 error/fail 词边界 → MEDIUM
    - 仅 stderr 非空 → LOW/INFO
    - exit ∈ {0, 1} → PASS
    """
    # 退出码：0 与 1 都视为业务正常（grep 习惯），≥2 才视为执行失败
    exit_ok = code in (None, 0, 1)

    # 清洗输出：剥离日志路径与 count=0 类"虚假告警"
    out_clean = _NOISY_PATH_RE.sub(" ", out or "")
    out_clean = _COUNT_ZERO_RE.sub(" ", out_clean)
    err_clean = _NOISY_PATH_RE.sub(" ", err or "")
    err_clean = _COUNT_ZERO_RE.sub(" ", err_clean)

    matched_out = sorted({m.group(0).lower() for m in _ERROR_WORD_RE.finditer(out_clean)})
    matched_err = sorted({m.group(0).lower() for m in _ERROR_WORD_RE.finditer(err_clean)})
    matched = list(dict.fromkeys(matched_out + matched_err))[:10]  # 去重保序

    # B1: 命令/脚本不存在硬错误（覆盖 exit_ok 也强制 HIGH）
    missing_cmd = bool(_MISSING_CMD_RE.search(err or "") or _MISSING_CMD_RE.search(out or ""))

    out_lines = [l for l in (out or "").splitlines() if l.strip()]
    facts: Dict[str, Any] = {
        "exit_code": int(code) if code is not None else -1,
        "output_lines": len(out_lines),
        "output_bytes": len((out or "").encode("utf-8", errors="ignore")),
        "err_lines": len([l for l in (err or "").splitlines() if l.strip()]),
        "matched_keywords": matched,
        "missing_command": missing_cmd,
        "thresholds": {},
        "criteria": "stderr 命中 'command not found'/'No such file' → HIGH；exit ≥2 → HIGH；exit ∈ {0,1} 且无 error 词边界命中 → PASS；命中 error/fatal 词边界 → MEDIUM；仅 stderr 非空 → INFO",
        "summary": f"执行命令返回 {int(code) if code is not None else '未知'}，输出 {len(out_lines)} 行"
            + (f"，命中 {len(matched)} 个关键字" if matched else "")
            + ("，脚本/命令不存在" if missing_cmd else ""),
    }

    if missing_cmd:
        return ("HIGH", "RISK",
                "自定义命令对应的脚本/二进制不存在（command not found / No such file）。",
                "确认规则中引用的命令/路径在目标环境存在，修正 InspectionRule.rule_content 后重试。",
                facts)
    if not exit_ok:
        return ("HIGH", "RISK",
                f"自定义命令执行失败（exit={code}）。",
                "确认目标环境兼容性与必要变量替换。",
                facts)
    if matched:
        return ("MEDIUM", "WARNING",
                f"自定义命令输出命中错误关键字（词边界）：{', '.join(matched[:5])}",
                "核查输出内容，确认是否为预期告警。",
                facts)
    if (err or "").strip():
        return ("LOW", "INFO",
                f"自定义命令执行有 stderr 输出（{facts['err_lines']} 行）。",
                "查看 stderr 内容，确认是否存在隐性错误。",
                facts)
    return ("NONE", "PASS",
            f"自定义命令执行成功（{len(out_lines)} 行输出）。",
            "保持定期运行并复核输出。",
            facts)


# ============================================================
# 自定义规则：提取器 + 阈值判定引擎
# ============================================================

_RISK_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _max_risk(*levels: str) -> str:
    """在多个风险等级中取最大值。"""
    best = "NONE"
    for lv in levels:
        if _RISK_RANK.get(lv, 0) > _RISK_RANK.get(best, 0):
            best = lv
    return best


def _apply_comparator(value: float, threshold: float, comparator: str) -> bool:
    """应用比较符判断 value 是否触发阈值。"""
    if value is None:
        return False
    if comparator == ">":
        return value > threshold
    if comparator == ">=":
        return value >= threshold
    if comparator == "<":
        return value < threshold
    if comparator == "<=":
        return value <= threshold
    if comparator == "==":
        return value == threshold
    if comparator == "contains":
        return str(threshold) in str(value)
    return value > threshold


def _extract_value(out: str, err: str, extractor: Dict[str, Any]) -> Dict[str, Any]:
    """根据 extractor 配置从命令输出中提取数值。

    返回 {"value": float|None, "matched": str|None, "method": str}
    """
    if not extractor:
        return {"value": None, "matched": None, "method": "none"}
    mode = (extractor.get("mode") or "regex").lower()
    pattern = extractor.get("pattern") or ""
    text = (out or "") + "\n" + (err or "")

    if mode == "regex":
        if not pattern:
            return {"value": None, "matched": None, "method": "regex-no-pattern"}
        try:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        except re.error:
            return {"value": None, "matched": None, "method": "regex-invalid"}
        if not m:
            return {"value": None, "matched": None, "method": "regex-no-match"}
        # 优先用 group(1)（捕获组），否则用 group(0)
        raw = m.group(1) if m.lastindex and m.group(1) else m.group(0)
        # 从 raw 中抠出第一个数字
        num_m = re.search(r"-?\d+(?:\.\d+)?", str(raw))
        return {
            "value": float(num_m.group(0)) if num_m else None,
            "matched": m.group(0),
            "method": "regex",
        }

    if mode == "numeric":
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        return {
            "value": float(m.group(0)) if m else None,
            "matched": m.group(0) if m else None,
            "method": "numeric",
        }

    if mode == "keyword":
        keywords = extractor.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        count = 0
        matched_words: List[str] = []
        for kw in keywords:
            if not kw:
                continue
            c = text.count(kw)
            if c > 0:
                count += c
                matched_words.append(f"{kw}×{c}")
        return {"value": float(count), "matched": ",".join(matched_words), "method": "keyword"}

    if mode == "json":
        field = extractor.get("value_field") or "value"
        try:
            data = json.loads(out or "{}")
        except (ValueError, TypeError):
            return {"value": None, "matched": None, "method": "json-parse-fail"}
        # 简单路径提取：支持 a.b.c 或 $..key
        cur: Any = data
        if field.startswith("$"):
            # 极简 JSONPath：$..key
            key = field.lstrip("$.").split("..")[-1]
            found: List[Any] = []
            def _walk(node: Any) -> None:
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k == key:
                            found.append(v)
                        _walk(v)
                elif isinstance(node, list):
                    for x in node:
                        _walk(x)
            _walk(cur)
            if not found:
                return {"value": None, "matched": None, "method": "json-not-found"}
            num_m = re.search(r"-?\d+(?:\.\d+)?", str(found[0]))
            return {
                "value": float(num_m.group(0)) if num_m else None,
                "matched": str(found[0]),
                "method": "json",
            }
        else:
            for part in field.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    cur = None
                if cur is None:
                    break
            if cur is None:
                return {"value": None, "matched": None, "method": "json-not-found"}
            num_m = re.search(r"-?\d+(?:\.\d+)?", str(cur))
            return {
                "value": float(num_m.group(0)) if num_m else None,
                "matched": str(cur),
                "method": "json",
            }

    return {"value": None, "matched": None, "method": f"unknown-{mode}"}


def _evaluate_threshold(extracted: Optional[float], threshold: Dict[str, Any]) -> Optional[str]:
    """根据 extracted 值与 threshold 配置判定风险等级（None=无结论，使用规则基线）。"""
    if extracted is None or not threshold:
        return None
    comparator = threshold.get("comparator") or ">"
    high = threshold.get("high")
    medium = threshold.get("medium")
    low = threshold.get("low")
    # contains 比较符不强制 float
    if comparator == "contains":
        if high is not None and _apply_comparator(extracted, high, comparator):
            return "HIGH"
        if medium is not None and _apply_comparator(extracted, medium, comparator):
            return "MEDIUM"
        if low is not None and _apply_comparator(extracted, low, comparator):
            return "LOW"
        return "NONE"
    if high is not None and _apply_comparator(extracted, float(high), comparator):
        return "HIGH"
    if medium is not None and _apply_comparator(extracted, float(medium), comparator):
        return "MEDIUM"
    if low is not None and _apply_comparator(extracted, float(low), comparator):
        return "LOW"
    return "NONE"


def make_custom_rule_analyzer(rule_config: Dict[str, Any], base_risk_level: str = "MEDIUM"):
    """根据规则配置生成一个 analyzer：先调用 _analyze_custom_command 拿到基础风险，
    再用 extractor + threshold 推导 extracted_risk，最终风险 = max(base_risk, extracted_risk)。
    """
    extractor = rule_config.get("extractor") or {}
    threshold = rule_config.get("threshold") or {}
    unit = (threshold.get("unit") or "count") if threshold else "count"

    def analyzer(out: str, err: str, code: int, thresholds=None):
        base_result = _analyze_custom_command(out, err, code, thresholds)
        # 旧式 analyzer 返回 5 元组 (level, status, message, suggestion, facts)
        base_level, base_status, base_msg, base_sug, base_facts = base_result
        ext_info = _extract_value(out, err, extractor) if extractor else {"value": None, "matched": None, "method": "none"}
        extracted_risk = _evaluate_threshold(ext_info.get("value"), threshold) if threshold else None
        final_level = _max_risk(base_level, extracted_risk or "NONE", base_risk_level or "NONE")

        facts = dict(base_facts or {})
        facts["extractor"] = ext_info
        if threshold:
            facts["threshold"] = {
                **threshold,
                "extracted_value": ext_info.get("value"),
                "extracted_unit": unit,
                "extracted_matched": ext_info.get("matched"),
                "extracted_risk": extracted_risk,
            }
        facts["final_risk"] = final_level
        facts["base_risk"] = base_level
        facts["base_risk_level"] = base_risk_level
        facts["criteria"] = (
            f"基线风险={base_level}；规则基线={base_risk_level}"
            + (f"；提取值={ext_info.get('value')}{unit}（{ext_info.get('method')}）" if extractor else "")
            + (f"；阈值判定={extracted_risk}" if extracted_risk else "")
        )
        if extracted_risk and _RISK_RANK.get(extracted_risk, 0) > _RISK_RANK.get(base_level, 0):
            new_msg = base_msg + f"；阈值命中 {extracted_risk}（{ext_info.get('value')}{unit}）"
            return (final_level, base_status, new_msg, base_sug, facts)
        return (final_level, base_status, base_msg, base_sug, facts)

    return analyzer


def list_categories(db: Session, *, scope: str = "server") -> List[Dict[str, Any]]:
    """返回巡检项分类列表。

    - 内置分类（12 大类）来自 SERVER_CATEGORIES / PROJECT_CATEGORIES
    - 用户通过 InspectionRule 自定义的新分类（InspectionItemConfig 中）
      也会合并进来，确保新规则在前端能勾选执行。
    """
    from app.db.models import InspectionItemConfig
    import logging
    logger = logging.getLogger(__name__)

    builtin_map: Dict[str, List[Dict[str, Any]]] = {
        "server": list(SERVER_CATEGORIES),
        "project": list(PROJECT_CATEGORIES),
    }
    base = builtin_map.get(scope, list(SERVER_CATEGORIES))
    builtin_codes = {c["code"] for c in base}
    items: List[Dict[str, Any]] = list(base)

    try:
        scope_filter = "BOTH"
        if scope == "server":
            scope_filter = SERVER_SCOPE
        elif scope == "project":
            scope_filter = PROJECT_SCOPE
        # item_code 全局唯一，所以只能按 scope_type 过滤
        rows = db.query(InspectionItemConfig).filter(
            InspectionItemConfig.enabled == True,  # noqa: E712
            InspectionItemConfig.is_builtin == False,  # noqa: E712
        ).all()
        for row in rows:
            code = (row.item_code or "").strip().upper()
            if not code or code in builtin_codes:
                continue
            row_scope = (row.scope_type or "BOTH").strip().upper()
            if row_scope != "BOTH" and scope_filter != "BOTH" and row_scope != scope_filter:
                continue
            items.append({
                "code": code,
                "name": row.item_name or code,
                "description": row.description or "自定义巡检项",
                "category": scope,
                "custom": True,
                "rule_id": row.id,
            })
    except Exception as e:
        logger.warning("list_categories merge custom failed: %s", e)

    return items


def list_servers() -> List[Dict[str, Any]]:
    from app.domain.inventory import inventory
    items = []
    for srv in inventory.list_servers():
        status = _server_status(srv)
        name = srv.get("name") or srv.get("host") or srv.get("id")
        asset_id = srv.get("id") or name
        items.append({
            "id": asset_id,
            "asset_id": asset_id,
            "name": name,
            "host": srv.get("host"),
            "ip": srv.get("host"),
            "group": srv.get("group") or "",
            "env": srv.get("env") or srv.get("environment") or "",
            "status": status,
            "status_label": _server_status_label(status),
            "inspectable": _is_server_inspectable(srv),
        })
    if not items:
        items.append({"id": "local", "name": "local", "host": "127.0.0.1", "ip": "127.0.0.1", "group": "本机", "env": "local", "status": "online", "status_label": "在线/启用", "inspectable": True})
    return items


def list_server_groups() -> List[Dict[str, Any]]:
    """Aggregate server groups with counts and inspectable totals.

    Used by the API + MCP layer to expose grouping so callers can filter
    batch inspection / 资产 selection by group name.
    """
    summary: Dict[str, Dict[str, Any]] = {}
    for srv in list_servers():
        group = str(srv.get("group") or srv.get("env") or "").strip() or "未分组"
        bucket = summary.setdefault(group, {"name": group, "total": 0, "inspectable": 0, "online": 0})
        bucket["total"] += 1
        if srv.get("inspectable"):
            bucket["inspectable"] += 1
        if str(srv.get("status") or "").lower() == "online":
            bucket["online"] += 1
    return sorted(summary.values(), key=lambda x: x["name"])


def resolve_servers_for_inspection(server_ids: Optional[List[str]] = None, *, all_servers: bool = False, skip_disabled: bool = True, groups: Optional[List[str]] = None) -> Dict[str, Any]:
    """Resolve selected server IDs and skip disabled/offline servers.

    This helper is used by batch inspection endpoints so selecting "all" never
    accidentally runs checks against stopped/disabled assets.  IDs may be server
    name, host/IP, or the UI id field.  When ``groups`` is provided the server
    list is filtered to only those whose ``group`` (or ``env``) field matches
    one of the supplied group names (case-insensitive).
    """
    servers = list_servers()
    by_key: Dict[str, Dict[str, Any]] = {}
    for srv in servers:
        for key in {srv.get("id"), srv.get("asset_id"), srv.get("name"), srv.get("host"), srv.get("ip")}:
            if key:
                by_key[str(key)] = srv
    requested_raw = [str(x or "").strip() for x in (server_ids or []) if str(x or "").strip()]
    normalized_groups = [str(g or "").strip().lower() for g in (groups or []) if str(g or "").strip()]
    group_filtered = False
    if normalized_groups:
        group_filtered = True
        servers = [s for s in servers if str(s.get("group") or s.get("env") or "").strip().lower() in normalized_groups]
        by_key = {}
        for srv in servers:
            for key in {srv.get("id"), srv.get("asset_id"), srv.get("name"), srv.get("host"), srv.get("ip")}:
                if key:
                    by_key[str(key)] = srv
    if all_servers or not requested_raw or any(x in {"__ALL__", "ALL", "*"} for x in requested_raw):
        requested = [str(s.get("id") or s.get("name")) for s in servers if s.get("id") or s.get("name")]
    else:
        requested = requested_raw
    eligible: List[Dict[str, Any]] = []
    eligible_ids: List[str] = []
    skipped: List[Dict[str, Any]] = []
    seen = set()
    for sid in requested:
        if sid in seen:
            continue
        seen.add(sid)
        srv = by_key.get(sid)
        if not srv:
            skipped.append({"server_id": sid, "reason": "服务器不存在或未同步到资产表。", "status": "missing"})
            continue
        status = _server_status(srv)
        if skip_disabled and not _is_server_inspectable(srv):
            skipped.append({"server_id": sid, "name": srv.get("name"), "host": srv.get("host"), "status": status, "reason": f"服务器状态为{_server_status_label(status)}，已自动跳过。"})
            continue
        normalized_id = str(srv.get("name") or srv.get("host") or srv.get("id") or sid)
        if normalized_id not in eligible_ids:
            eligible_ids.append(normalized_id)
            eligible.append(srv)
    return {
        "total_requested": len(requested),
        "eligible_count": len(eligible_ids),
        "skipped_count": len(skipped),
        "eligible_ids": eligible_ids,
        "eligible": eligible,
        "skipped": skipped,
        "all_servers": bool(all_servers or not requested_raw or any(x in {"__ALL__", "ALL", "*"} for x in requested_raw)),
    }


def assert_server_inspectable(server_id: str) -> Dict[str, Any]:
    resolved = resolve_servers_for_inspection([server_id], skip_disabled=True)
    if resolved.get("eligible"):
        return resolved["eligible"][0]
    reason = (resolved.get("skipped") or [{}])[0].get("reason") or "服务器不可巡检。"
    raise HTTPException(status_code=400, detail=reason)


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


def _relation_to_dict(r: ProjectServerRelation) -> Dict[str, Any]:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "server_id": r.server_id,
        "deploy_role": r.deploy_role,
        "deploy_path": r.deploy_path or "",
        "config_path": r.config_path or "",
        "log_path": r.log_path or "",
        "backup_path": r.backup_path or "",
        "runtime_user": r.runtime_user or "",
        "main_port": r.main_port or "",
        "active": bool(r.active),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _merge_project_with_relations(project: Dict[str, Any], relations: List[ProjectServerRelation]) -> Dict[str, Any]:
    merged = dict(project)
    active_relations = [r for r in relations if getattr(r, "active", True)]
    if not active_relations:
        return merged
    first = active_relations[0]
    merged["servers"] = sorted({r.server_id for r in active_relations if r.server_id}) or merged.get("servers") or []
    merged["relations"] = [_relation_to_dict(r) for r in active_relations]
    merged["relation_configured"] = True
    # Use the first non-empty relation fields as effective project inspection paths.
    for key in ["deploy_path", "config_path", "log_path", "backup_path", "runtime_user", "main_port"]:
        value = getattr(first, key, None)
        if value:
            merged[key] = str(value)
    return merged


def _get_project_with_relations(db: Session, project_id: str) -> Dict[str, Any]:
    project = _get_project(project_id)
    try:
        rows = db.query(ProjectServerRelation).filter(
            ProjectServerRelation.project_id == project_id,
            ProjectServerRelation.active == True,  # noqa: E712
        ).order_by(ProjectServerRelation.updated_at.desc(), ProjectServerRelation.created_at.desc()).all()
        return _merge_project_with_relations(project, rows)
    except Exception:
        return project


def list_projects_with_relations(db: Session) -> List[Dict[str, Any]]:
    projects = list_projects()
    try:
        rows = db.query(ProjectServerRelation).filter(ProjectServerRelation.active == True).all()  # noqa: E712
    except Exception:
        rows = []
    by_project: Dict[str, List[ProjectServerRelation]] = {}
    for r in rows:
        by_project.setdefault(r.project_id, []).append(r)
    return [_merge_project_with_relations(p, by_project.get(p.get("id"), [])) for p in projects]


def upsert_project_server_relation(db: Session, payload: Dict[str, Any], *, updated_by: str = "") -> Dict[str, Any]:
    project_id = str(payload.get("project_id") or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    # Validate project exists and use its first configured server if server_id was not provided.
    project = _get_project(project_id)
    server_id = str(payload.get("server_id") or "").strip()
    if not server_id:
        servers = project.get("servers") or []
        server_id = str(servers[0]) if servers else ""
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id is required; configure the project deployment server first")
    rel_id = str(payload.get("id") or "").strip()
    row = None
    if rel_id and not rel_id.startswith("config::"):
        row = db.query(ProjectServerRelation).filter(ProjectServerRelation.id == rel_id).first()
    if not row:
        row = db.query(ProjectServerRelation).filter(
            ProjectServerRelation.project_id == project_id,
            ProjectServerRelation.server_id == server_id,
        ).first()
    now = _now()
    if not row:
        row = ProjectServerRelation(
            id=uuid4().hex,
            project_id=project_id,
            server_id=server_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    row.deploy_role = str(payload.get("deploy_role") or "APP").strip() or "APP"
    row.deploy_path = str(payload.get("deploy_path") or "").strip()
    row.config_path = str(payload.get("config_path") or "").strip()
    row.log_path = str(payload.get("log_path") or "").strip()
    row.backup_path = str(payload.get("backup_path") or "").strip()
    row.runtime_user = str(payload.get("runtime_user") or "").strip()
    row.main_port = str(payload.get("main_port") or "").strip()
    row.active = bool(payload.get("active", True))
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return _relation_to_dict(row)


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




def _project_rule_check_specs(db: Session, project: Dict[str, Any], selected: set[str]) -> List[Dict[str, Any]]:
    servers = project.get("servers") or []
    if not servers:
        return [{
            "category": "RUNTIME_ENVIRONMENT",
            "item_code": "PROJECT_NO_SERVER",
            "item_name": "项目部署服务器关联检查",
            "server_name": "",
            "command": "# 项目未配置部署服务器，无法执行远程巡检命令。",
            "analyze": _analyze_project_runtime,
            "blocked_reason": "项目未配置部署服务器，无法关联服务器执行项目巡检命令。",
        }]
    specs: List[Dict[str, Any]] = []
    base_specs = _rule_execution_specs(db, scope_type=PROJECT_SCOPE, categories=selected, project=project)
    for server_name in servers[:5]:
        for spec in base_specs:
            specs.append({
                **spec,
                "server_name": server_name,
                "item_name": f"{spec.get('item_name') or spec.get('item_code')}：{server_name}",
            })
    return specs

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


# 项目侧巡检项的执行内容与判断标准（供前端规则展示与审计）
PROJECT_ITEM_META: Dict[str, Dict[str, str]] = {
    "RUNTIME_ENVIRONMENT": {
        "execution": "ss/netstat 验证主端口监听；ls 验证 deploy_path / log_path 存在；du 取目录大小",
        "criteria": "判定标准：主端口未监听或端口配置不匹配触发 MEDIUM；目录不存在触发 MEDIUM；其它情况通过。",
    },
    "FILE_SECURITY": {
        "execution": "find 扫描 0777/4000 权限文件 + *.sh/*.exe/.* 等可疑脚本",
        "criteria": "判定标准：发现全局可写或 SUID 触发 MEDIUM；发现陌生脚本/可执行文件触发 LOW；其它情况通过。",
    },
    "API_SECURITY": {
        "execution": "grep 接口错误日志中的 4xx/5xx、SQL 注入、目录遍历、XSS 等攻击特征",
        "criteria": "判定标准：发现 union/select/<script>/../等攻击特征触发 HIGH；500 错误 ≥20 触发 MEDIUM；其它情况通过。",
    },
    "WHITELIST_SECURITY": {
        "execution": "复用进程端口分析器，检查数据库/后台端口暴露情况与白名单配置",
        "criteria": "判定标准：数据库/后台端口对全网监听触发 MEDIUM；其它情况通过。",
    },
    "CUSTOMER_SECURITY": {
        "execution": "复用接口日志分析器，识别客户白名单超配与异常操作",
        "criteria": "判定标准：发现异常高频/越权/批量操作触发 MEDIUM；其它情况通过。",
    },
    "CONFIG_SECURITY": {
        "execution": "复用接口日志分析器，扫描配置中的明文密码、debug、匿名访问等",
        "criteria": "判定标准：发现明文密钥/secret/token 触发 HIGH；debug=true 触发 MEDIUM；其它情况通过。",
    },
    "BACKUP_SECURITY": {
        "execution": "find 扫描项目备份目录近 2 天文件，输出修改时间与大小",
        "criteria": "判定标准：无近期备份触发 MEDIUM；0 字节文件触发 HIGH；其它情况通过。",
    },
}


def _analyze_project_runtime(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """项目运行环境巡检（B4：返回 5-tuple 契约，与服务端 analyzer 一致）。"""
    facts: Dict[str, Any] = {
        "has_port_section": "---PORT---" in out,
        "has_no_such_file": "No such file" in out,
        "criteria": "主端口未监听或部署目录不存在 → MEDIUM",
        "summary": "项目运行环境检查通过" if "---PORT---" in out and out.split("---PORT---", 1)[1].strip() and "No such file" not in out else "项目运行环境需要复核",
    }
    if "---PORT---" in out and not out.split("---PORT---", 1)[1].strip():
        return "MEDIUM", "WARNING", "项目主端口未检测到监听或端口配置不匹配。", "确认项目进程、端口配置和负载均衡转发规则。", facts
    if "No such file" in out:
        return "MEDIUM", "WARNING", "项目部署目录或日志目录不存在。", "核查项目部署路径、日志路径配置。", facts
    return "NONE", "PASS", "项目运行环境检查未发现明显异常。", "建议项目巡检前先完成关联服务器巡检。", facts


def _analyze_project_files(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """项目文件安全巡检（B4：返回 5-tuple 契约）。"""
    lines = out.splitlines()
    suid_or_world = [l for l in lines if " -rws" in l or " 777 " in l or "rwxrwxrwx" in l]
    scripts = [l for l in lines if l.strip().endswith(('.sh', '.exe'))]
    facts: Dict[str, Any] = {
        "suid_or_world_writable_count": len(suid_or_world),
        "script_count": len(scripts),
        "criteria": "全局可写/SUID 风险 → MEDIUM；脚本/可执行文件 → LOW",
        "summary": f"发现 {len(suid_or_world)} 个高危权限 / {len(scripts)} 个脚本",
    }
    if suid_or_world:
        return "MEDIUM", "WARNING", "项目目录存在全局可写或 SUID 权限风险。", "禁止 777 权限，业务程序不应使用 root/SUID 权限运行。", facts
    if scripts:
        return "LOW", "WARNING", f"项目目录存在脚本/可执行文件 {len(scripts)} 个，需要确认来源。", "对脚本文件建立基线，核查陌生脚本和隐藏文件。", facts
    return "NONE", "PASS", "项目文件权限未发现明显异常。", "后续建议建立 SHA256 文件完整性基线。", facts


def _analyze_project_api(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """项目接口日志巡检（B4：返回 5-tuple 契约）。"""
    text = out.lower()
    attack = [x for x in ["union", "../", "/etc/passwd", "<script", "xss", "sql"] if x in text]
    error_count = len([l for l in out.splitlines() if "500" in l])
    facts: Dict[str, Any] = {
        "attack_features": sorted(set(attack)),
        "error_500_count": error_count,
        "criteria": "攻击特征（union/<script/xss/sql/路径遍历）→ HIGH；500 错误 ≥20 → MEDIUM",
        "summary": f"命中 {len(attack)} 个攻击特征 / 500 错误 {error_count} 条",
    }
    if attack:
        return "HIGH", "RISK", f"接口日志出现疑似攻击特征：{', '.join(sorted(set(attack)))}。", "立即核查来源 IP、接口参数与 WAF/网关拦截策略。", facts
    if error_count >= 20:
        return "MEDIUM", "WARNING", f"接口 500 错误较多（采样 {error_count} 条）。", "定位高频报错接口并修复程序 BUG。", facts
    return "NONE", "PASS", "接口日志采样未发现明显攻击或错误激增。", "持续保留接口访问日志和错误日志。", facts


def _analyze_project_backup(out: str, err: str, code: int, thresholds: Optional[Dict[str, Any]] = None):
    """项目备份巡检（B4：返回 5-tuple 契约）。"""
    zero_byte = re.findall(r"\b0\s+/.+", out) if out else []
    facts: Dict[str, Any] = {
        "has_output": bool((out or "").strip()),
        "zero_byte_files": zero_byte[:5],
        "criteria": "未发现最近备份 → MEDIUM；0 字节文件 → HIGH",
        "summary": "项目近期备份检查通过" if (out or "").strip() and not zero_byte else "项目备份需要核查",
    }
    if not out.strip():
        return "MEDIUM", "WARNING", "未发现最近 2 天项目备份文件。", "确认项目备份任务、备份路径与异地同步状态。", facts
    if zero_byte:
        return "HIGH", "RISK", "备份目录存在 0 字节文件，可能备份失败或损坏。", "立即手动触发备份并验证可恢复性。", facts
    return "NONE", "PASS", "项目近期备份文件检查通过。", "建议每周抽检备份文件解压/恢复。", facts


def _auto_generate_report(db: Session, run: InspectionRun) -> None:
    """巡检完成后自动生成 HTML 格式报告，失败不影响巡检结果。"""
    try:
        if not run.id or run.status in {"RUNNING", "PENDING"}:
            return
        item_count = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).count()
        if item_count == 0:
            return
        from app.services.report_center import generate_report as svc_generate_report
        result = svc_generate_report(
            db, report_type="inspection", target_id=run.id,
            fmt="html", title="", created_by=run.created_by or "system",
        )
        report = result.get("report") or {}
        if report.get("id"):
            run.report_id = report.get("id")
            run.updated_at = _now()
            db.commit()
    except Exception:
        # 自动生成报告失败不影响巡检结果
        pass


def _finalize_run(db: Session, run: InspectionRun) -> InspectionRun:
    """P1-5 重构：按 (server, category) 取最高 + 总和封顶 60。"""
    rows = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).all()
    high, medium, low, normal = _aggregate_risk_counts(rows)
    # A5: 按 server 数平均
    distinct_servers = {r.server_id for r in rows if r.server_id}
    server_count = max(1, len(distinct_servers) or 1)
    score = _compute_score(high, medium, low, server_count=server_count)
    run.high_count = high
    run.medium_count = medium
    run.low_count = low
    run.normal_count = normal
    run.score = score
    run.status = "SUCCESS" if not any(r.status == "ERROR" for r in rows) else "PARTIAL_SUCCESS"
    run.summary = f"巡检完成：评分 {score}（最高风险制），高危 {high}，中危 {medium}，低危 {low}，通过 {normal}。"
    run.finished_at = _now()
    if run.started_at and run.finished_at:
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
    run.updated_at = _now()
    db.commit()
    db.refresh(run)

    # 巡检完成时自动生成 HTML 报告
    _auto_generate_report(db, run)

    return run


def _aggregate_risk_counts(rows) -> tuple:
    """P1-5: 按 (server_id, category) 取最高风险等级，再按 RISK_WEIGHT 计数。

    - 同 server 同 category 有多条结果时，仅取风险等级最高的一条
    - 失败一次就足以代表该类目的最大风险
    """
    # key -> max risk level
    by_key: Dict[tuple, str] = {}
    for r in rows:
        key = (r.server_id or "", r.category or "")
        cur = by_key.get(key)
        new_level = (r.risk_level or "NONE").upper()
        if not cur or RISK_ORDER.get(new_level, 0) > RISK_ORDER.get(cur, 0):
            by_key[key] = new_level
    high = sum(1 for lvl in by_key.values() if lvl == "HIGH")
    medium = sum(1 for lvl in by_key.values() if lvl == "MEDIUM")
    low = sum(1 for lvl in by_key.values() if lvl == "LOW")
    normal = sum(1 for r in rows if (r.risk_level or "NONE").upper() in {"NONE", ""} and r.status == "PASS")
    return high, medium, low, normal


def _risk_weight(level: str) -> int:
    """B7 修复：统一为 RISK_ORDER 单一来源，删除老的硬编码字典。
    保留函数签名（向后兼容）；返回等级顺序权重。
    """
    return RISK_ORDER.get(level.upper(), 0)


def _compute_score(high: int, medium: int, low: int, server_count: int = 1) -> int:
    """A5 修复：按计划文档 §2.2 P1-5 公式。

    score = max(0, 100 - min(60, avg_deduction))
    其中 avg_deduction = (high*15 + medium*8 + low*2) / server_count
    - 封顶 60 扣分：避免 0 分但保留风险提示
    - 按机器数平均：避免集群规模导致分数坍塌
    """
    if server_count <= 0:
        server_count = 1
    total = high * RISK_WEIGHT["HIGH"] + medium * RISK_WEIGHT["MEDIUM"] + low * RISK_WEIGHT["LOW"]
    avg = total / server_count
    deduct = min(60.0, avg)
    return max(0, int(round(100 - deduct)))



def _append_progress(db: Session, run: InspectionRun, result: CheckResult) -> InspectionItemResult:
    row = _save_result(db, run, result)
    # Update lightweight progress summary after every item so polling users can
    # see the execution stream while the run is still RUNNING.
    rows = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).all()
    high, medium, low, normal = _aggregate_risk_counts(rows)
    # A5: 按本次 run 涉及 server 数平均扣分（cluster 规模不敏感）
    distinct_servers = {r.server_id for r in rows if r.server_id}
    server_count = max(1, len(distinct_servers) or 1)
    run.high_count = high
    run.medium_count = medium
    run.low_count = low
    run.normal_count = normal
    run.score = _compute_score(high, medium, low, server_count=server_count)
    run.summary = f"巡检执行中：已完成 {len(rows)} 项，高危 {high}，中危 {medium}，低危 {low}。"
    run.updated_at = _now()
    db.commit()
    return row


def create_server_inspection_run(db: Session, *, server_id: str, categories: Optional[List[str]] = None, trigger_type: str = "MANUAL", created_by: str = "") -> InspectionRun:
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id is required")
    cats = categories or [c["code"] for c in SERVER_CATEGORIES]
    run = _new_run(
        id=uuid4().hex,
        scope_type=SERVER_SCOPE,
        server_id=server_id,
        trigger_type=trigger_type,
        status="RUNNING",
        score=100,
        categories=cats,
        summary="巡检任务已创建，等待执行。",
        created_by=created_by,
        started_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def mark_run_failed(db: Optional[Session], run_id: str, message: str) -> bool:
    """把巡检任务收尾为 FAILED（幂等，只对 RUNNING/PENDING 生效）。

    存在的意义：run 建库时状态就是 RUNNING，若执行器在收尾逻辑之外抛错（例如读取
    run 行本身遇到 SQLite 锁、阈值加载失败、后台任务被进程重启打断），该 run 会永久
    停在"执行中"，而全仓没有其它回收逻辑。这里用"当前会话 → 独立新会话"两级兜底，
    保证即使当前会话已损坏也能落库。返回是否真的写入。
    """
    normalized = str(run_id or "").strip()
    if not normalized:
        return False
    attempt_sessions: List[Optional[Session]] = [db, None]
    for candidate in attempt_sessions:
        session = candidate
        owns_session = False
        try:
            if session is None:
                from app.db.base import SessionLocal

                session = SessionLocal()
                owns_session = True
            row = session.query(InspectionRun).filter(InspectionRun.id == normalized).first()
            if row is None:
                return False
            if str(row.status or "").upper() not in NON_TERMINAL_RUN_STATUSES:
                # 已终结（SUCCESS/PARTIAL_SUCCESS/FAILED）的任务不覆盖，避免抹掉真实结果。
                return False
            now = _now()
            row.status = "FAILED"
            row.summary = str(message or "巡检执行失败")[:500]
            row.finished_at = now
            row.updated_at = now
            session.commit()
            return True
        except Exception:
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
            continue
        finally:
            if owns_session and session is not None:
                try:
                    session.close()
                except Exception:
                    pass
    return False


def reap_stale_inspection_runs(db: Session, *, stale_after_seconds: Optional[int] = None, heartbeat_seconds: Optional[int] = None) -> List[str]:
    """回收"执行器已死"的僵尸巡检，返回被标记为 FAILED 的 run_id 列表。

    判定需要**同时**满足：
      1. run 自身超过 ``stale_after_seconds``（默认 INSPECTION_STALE_RUN_SECONDS）没有更新；
      2. 最近 ``heartbeat_seconds``（默认 INSPECTION_ACTIVE_HEARTBEAT_SECONDS）内，
         巡检域内没有任何 RUNNING/PENDING run 被更新过（说明执行器进程已不在推进）。

    条件 2 是关键：批量巡检会把整批 run 预先建好再排队执行，排队中的 run 自身不会更新；
    只要同批还有 run 在推进（每个检查项都会刷新 updated_at），就不判定为僵尸，
    因此不会误杀"排队等待中"的任务。
    """
    threshold = int(stale_after_seconds if stale_after_seconds is not None else INSPECTION_STALE_RUN_SECONDS)
    heartbeat = int(heartbeat_seconds if heartbeat_seconds is not None else INSPECTION_ACTIVE_HEARTBEAT_SECONDS)
    now = _now()
    cutoff = now - timedelta(seconds=max(60, threshold))
    heart_cutoff = now - timedelta(seconds=max(30, heartbeat))

    active = (
        db.query(InspectionRun.id)
        .filter(
            InspectionRun.status.in_(list(NON_TERMINAL_RUN_STATUSES)),
            InspectionRun.updated_at >= heart_cutoff,
        )
        .first()
    )
    if active is not None:
        return []

    stale = (
        db.query(InspectionRun)
        .filter(
            InspectionRun.status.in_(list(NON_TERMINAL_RUN_STATUSES)),
            InspectionRun.updated_at < cutoff,
        )
        .all()
    )
    reaped: List[str] = []
    for run in stale:
        run.status = "FAILED"
        run.summary = "巡检执行中断（后端进程重启或后台任务异常），已自动标记为失败；如需结果请重新发起巡检。"
        run.finished_at = now
        run.updated_at = now
        reaped.append(run.id)
    if reaped:
        db.commit()
    return reaped


def execute_server_inspection_run(
    db: Session,
    *,
    run_id: str,
    server_id: Optional[str] = None,
    categories: Optional[List[str]] = None,
    command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    try:
        run = db.query(InspectionRun).filter(InspectionRun.id == run_id).first()
    except Exception as exc:
        # 连任务行都读不出来（例如 SQLite 锁）也必须收尾，否则永久 RUNNING。
        mark_run_failed(None, run_id, f"服务器巡检执行失败：无法读取巡检任务（{exc}）")
        raise
    if not run:
        raise HTTPException(status_code=404, detail="Inspection run not found")
    sid = server_id or run.server_id
    cats = categories or run.categories or [c["code"] for c in SERVER_CATEGORIES]
    cmd_timeout = _clamp_int(command_timeout_seconds, DEFAULT_COMMAND_TIMEOUT_SECONDS, 5, MAX_COMMAND_TIMEOUT_SECONDS)
    run_timeout = _clamp_int(run_timeout_seconds, DEFAULT_RUN_TIMEOUT_SECONDS, 30, MAX_RUN_TIMEOUT_SECONDS)
    started_monotonic = time.monotonic()
    try:
        thresholds = _load_thresholds(db)
        run.status = "RUNNING"
        run.summary = f"巡检执行中：开始服务器基础检查。命令超时 {cmd_timeout}s，单服务器总超时 {run_timeout}s。"
        run.updated_at = _now()
        db.commit()
        specs = _rule_execution_specs(db, scope_type=SERVER_SCOPE, categories=cats)
        for spec in specs:
            elapsed = time.monotonic() - started_monotonic
            if elapsed > run_timeout:
                _append_progress(db, run, CheckResult("SYSTEM", "SERVER_INSPECTION_TIMEOUT", "服务器巡检总超时", "ERROR", "MEDIUM", f"单服务器巡检超过 {run_timeout} 秒，剩余步骤已停止。", "可减少巡检分类、降低批量并发或提高单服务器总超时。", f"elapsed={elapsed:.1f}s timeout={run_timeout}s", source_type="TIMEOUT"))
                run.status = "PARTIAL_SUCCESS"
                run.summary = f"巡检部分完成：单服务器巡检超过 {run_timeout} 秒。"
                run.finished_at = _now()
                run.updated_at = _now()
                db.commit()
                return inspection_run_detail(db, run_id)
            step = _start_progress_item(db, run, category=spec["category"], item_code=spec["item_code"], item_name=spec["item_name"], command=spec["command"])
            if spec.get("blocked_reason"):
                result = _blocked_rule_result(spec)
            else:
                result = _remote_check(sid, spec["category"], spec["item_code"], spec["item_name"], spec["command"], spec["analyze"], timeout_seconds=cmd_timeout, thresholds=thresholds)
            _finish_progress_item(db, run, step, result)
        run = _finalize_run(db, run)
    except Exception as exc:
        try:
            _append_progress(db, run, CheckResult("SYSTEM", "SERVER_INSPECTION_UNHANDLED_ERROR", "服务器巡检执行异常", "ERROR", "MEDIUM", f"巡检执行异常：{exc}", "检查服务器连接、后端日志和命令兼容性。", str(exc), source_type="ERROR"))
        except Exception:
            # 进度写入失败不能掩盖原始异常，更不能让 run 停在 RUNNING。
            pass
        mark_run_failed(db, run_id, f"服务器巡检执行失败：{exc}")
    return inspection_run_detail(db, run_id)


def run_server_inspection(
    db: Session,
    *,
    server_id: str,
    categories: Optional[List[str]] = None,
    trigger_type: str = "MANUAL",
    created_by: str = "",
    command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    assert_server_inspectable(server_id)
    run = create_server_inspection_run(db, server_id=server_id, categories=categories, trigger_type=trigger_type, created_by=created_by)
    return execute_server_inspection_run(db, run_id=run.id, server_id=server_id, categories=categories, command_timeout_seconds=command_timeout_seconds, run_timeout_seconds=run_timeout_seconds)


def _run_one_server_in_new_session(*, server_id: str, categories: Optional[List[str]], trigger_type: str, created_by: str, generate_report: bool, command_timeout_seconds: int, run_timeout_seconds: int) -> Dict[str, Any]:
    from app.db.base import SessionLocal
    db2 = SessionLocal()
    run_id = ""
    try:
        assert_server_inspectable(server_id)
        run = create_server_inspection_run(db2, server_id=server_id, categories=categories, trigger_type=trigger_type, created_by=created_by)
        run_id = run.id
        result = execute_server_inspection_run(db2, run_id=run_id, server_id=server_id, categories=categories, command_timeout_seconds=command_timeout_seconds, run_timeout_seconds=run_timeout_seconds)
        if generate_report:
            try:
                result["report"] = generate_report_for_run(db2, result.get("run", {}).get("id", ""), fmt="md", created_by=created_by or "system").get("report")
            except Exception as exc:
                result["report_error"] = str(exc)
        return result
    except Exception as exc:
        # 批量执行里 worker 抛错时调用方只会记录 errors；若不在这里收尾，
        # 这个 run 会永久停在 RUNNING（概览一直显示"执行中"）。
        if run_id:
            mark_run_failed(None, run_id, f"服务器巡检执行失败：{exc}")
        raise
    finally:
        db2.close()


def execute_server_inspection_runs_batch(
    run_specs: List[Dict[str, Any]],
    *,
    concurrency: int = DEFAULT_BATCH_CONCURRENCY,
    batch_size: int = DEFAULT_BATCH_SIZE,
    command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Execute pre-created inspection runs in bounded batches.

    Used by async batch-start.  Each worker opens its own SQLAlchemy session so
    concurrent execution does not share DB session state.
    """
    from app.db.base import SessionLocal
    effective_concurrency = _clamp_int(concurrency, DEFAULT_BATCH_CONCURRENCY, 1, MAX_BATCH_CONCURRENCY)
    effective_batch_size = _clamp_int(batch_size, DEFAULT_BATCH_SIZE, 1, MAX_BATCH_SIZE)
    cmd_timeout = _clamp_int(command_timeout_seconds, DEFAULT_COMMAND_TIMEOUT_SECONDS, 5, MAX_COMMAND_TIMEOUT_SECONDS)
    run_timeout = _clamp_int(run_timeout_seconds, DEFAULT_RUN_TIMEOUT_SECONDS, 30, MAX_RUN_TIMEOUT_SECONDS)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    def worker(spec: Dict[str, Any]) -> Dict[str, Any]:
        db2 = SessionLocal()
        try:
            return execute_server_inspection_run(db2, run_id=spec["run_id"], server_id=spec.get("server_id"), categories=spec.get("categories"), command_timeout_seconds=cmd_timeout, run_timeout_seconds=run_timeout)
        finally:
            db2.close()

    for chunk in _chunked(run_specs, effective_batch_size):
        with ThreadPoolExecutor(max_workers=min(effective_concurrency, len(chunk) or 1)) as pool:
            future_map = {pool.submit(worker, spec): spec for spec in chunk}
            for future in as_completed(future_map):
                spec = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    spec_run_id = str(spec.get("run_id") or "")
                    errors.append({"server_id": spec.get("server_id"), "run_id": spec_run_id, "error": str(exc)})
                    if spec_run_id:
                        # 只要 worker 失败，对应 run 就必须收尾，否则永久 RUNNING。
                        mark_run_failed(None, spec_run_id, f"服务器巡检执行失败：{exc}")
    return {"success": len(results), "failed": len(errors), "results": results, "errors": errors}


def run_servers_batch_inspection(
    db: Session,
    *,
    server_ids: List[str],
    categories: Optional[List[str]] = None,
    trigger_type: str = "MANUAL",
    created_by: str = "",
    generate_report: bool = False,
    concurrency: int = DEFAULT_BATCH_CONCURRENCY,
    batch_size: int = DEFAULT_BATCH_SIZE,
    command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
    skip_disabled: bool = True,
    all_servers: bool = False,
    groups: Optional[List[str]] = None,
    job_id: str = "",
) -> Dict[str, Any]:
    """Run read-only server inspection for multiple servers with bounded concurrency.

    When ``job_id`` is provided, the unified task center job progress is updated
    after every completed server so users see live progress instead of a fixed
    35% stall while the batch runs synchronously.
    """
    resolved = resolve_servers_for_inspection(
        server_ids,
        all_servers=all_servers,
        skip_disabled=skip_disabled,
        groups=groups,
    )
    unique_ids = resolved["eligible_ids"]
    if not unique_ids:
        return {
            "summary": f"没有可巡检的在线/启用服务器，已跳过 {resolved.get('skipped_count', 0)} 台。",
            "total": resolved.get("total_requested", 0),
            "eligible": 0,
            "skipped": resolved.get("skipped", []),
            "success": 0,
            "failed": 0,
            "runs": [],
            "results": [],
            "errors": [],
        }

    effective_concurrency = _clamp_int(concurrency, DEFAULT_BATCH_CONCURRENCY, 1, MAX_BATCH_CONCURRENCY)
    effective_batch_size = _clamp_int(batch_size, DEFAULT_BATCH_SIZE, 1, MAX_BATCH_SIZE)
    cmd_timeout = _clamp_int(command_timeout_seconds, DEFAULT_COMMAND_TIMEOUT_SECONDS, 5, MAX_COMMAND_TIMEOUT_SECONDS)
    run_timeout = _clamp_int(run_timeout_seconds, DEFAULT_RUN_TIMEOUT_SECONDS, 30, MAX_RUN_TIMEOUT_SECONDS)

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    total_count = len(unique_ids)
    done_count = 0

    def _report_batch_progress() -> None:
        if not job_id:
            return
        try:
            from app.db.base import SessionLocal as _ProgressSessionLocal
            from app.db.models import OperationJob

            progress_db = _ProgressSessionLocal()
            try:
                row = progress_db.query(OperationJob).filter(OperationJob.id == job_id).first()
                if row is None or row.status != "running":
                    return
                # The generic tool worker reserves 0-35% for queueing and policy
                # checks, and 90-100% for handler return/finalization. Keep the
                # inspection progress inside the handler's 35-90% window.
                inner_percent = done_count / total_count * 100
                percent = 35 + int(round(inner_percent * 55 / 100))
                percent = max(35, min(90, percent))
                percent = max(int(row.progress or 0), percent)
                row.progress = percent
                row.result_json = {
                    "progress": {
                        "total_servers": total_count,
                        "completed_servers": done_count,
                        "succeeded_servers": len(results),
                        "failed_servers": len(errors),
                        "current_phase": "inspection",
                    }
                }
                row.updated_at = _now()
                progress_db.commit()
            finally:
                progress_db.close()
        except Exception:
            # Progress reporting is best-effort and must never fail the batch.
            pass

    for chunk in _chunked(unique_ids, effective_batch_size):
        with ThreadPoolExecutor(max_workers=min(effective_concurrency, len(chunk) or 1)) as pool:
            futures = {
                pool.submit(
                    _run_one_server_in_new_session,
                    server_id=sid,
                    categories=categories,
                    trigger_type=trigger_type,
                    created_by=created_by,
                    generate_report=generate_report,
                    command_timeout_seconds=cmd_timeout,
                    run_timeout_seconds=run_timeout,
                ): sid
                for sid in chunk
            }
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    errors.append({"server_id": sid, "error": str(exc)})
                done_count += 1
                _report_batch_progress()

    skipped_count = len(resolved.get("skipped", []))
    return {
        "summary": f"批量服务器巡检完成：在线/启用 {len(unique_ids)} 台，成功 {len(results)} 台，失败 {len(errors)} 台，跳过 {skipped_count} 台停用/离线服务器。",
        "total": resolved.get("total_requested", len(unique_ids) + skipped_count),
        "eligible": len(unique_ids),
        "skipped_count": skipped_count,
        "skipped": resolved.get("skipped", []),
        "success": len(results),
        "failed": len(errors),
        "concurrency": effective_concurrency,
        "batch_size": effective_batch_size,
        "command_timeout_seconds": cmd_timeout,
        "run_timeout_seconds": run_timeout,
        "runs": [r.get("run") for r in results if r.get("run")],
        "results": results,
        "errors": errors,
    }



def create_project_inspection_run(db: Session, *, project_id: str, categories: Optional[List[str]] = None, trigger_type: str = "MANUAL", created_by: str = "") -> InspectionRun:
    project = _get_project_with_relations(db, project_id)
    cats = categories or [c["code"] for c in PROJECT_CATEGORIES]
    run = _new_run(
        id=uuid4().hex,
        scope_type=PROJECT_SCOPE,
        project_id=project_id,
        trigger_type=trigger_type,
        status="RUNNING",
        score=100,
        categories=cats,
        metadata_json={"project": {k: v for k, v in project.items() if k != "config"}},
        summary="项目巡检任务已创建，等待执行。",
        created_by=created_by,
        started_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def execute_project_inspection_run(db: Session, *, run_id: str, project_id: Optional[str] = None, categories: Optional[List[str]] = None, include_server_summary: bool = True) -> Dict[str, Any]:
    try:
        run = db.query(InspectionRun).filter(InspectionRun.id == run_id).first()
    except Exception as exc:
        mark_run_failed(None, run_id, f"项目巡检执行失败：无法读取巡检任务（{exc}）")
        raise
    if not run:
        raise HTTPException(status_code=404, detail="Inspection run not found")
    try:
        pid = project_id or run.project_id
        project = _get_project_with_relations(db, pid)
        cats = categories or run.categories or [c["code"] for c in PROJECT_CATEGORIES]
        selected = set(cats)
        run.status = "RUNNING"
        run.summary = "项目巡检执行中：开始配置、文件、日志、备份检查。"
        run.updated_at = _now()
        db.commit()
        for result in _project_config_checks(project, selected):
            _append_progress(db, run, result)
        for spec in _project_rule_check_specs(db, project, selected):
            step = _start_progress_item(db, run, category=spec["category"], item_code=spec["item_code"], item_name=spec["item_name"], command=spec["command"])
            if spec.get("blocked_reason"):
                result = _blocked_rule_result(spec)
            else:
                result = _remote_check(spec["server_name"], spec["category"], spec["item_code"], spec["item_name"], spec["command"], spec["analyze"])
            _finish_progress_item(db, run, step, result)
        if include_server_summary:
            before = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).count()
            _append_server_summary(db, run, project)
            after_rows = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).order_by(InspectionItemResult.created_at.asc()).all()
            if len(after_rows) > before:
                # _append_server_summary uses _save_result directly; commit it and refresh progress.
                db.commit()
                _append_progress_counts_only(db, run)
        run = _finalize_run(db, run)
    except Exception as exc:
        try:
            _append_progress(db, run, CheckResult("SYSTEM", "PROJECT_INSPECTION_UNHANDLED_ERROR", "项目巡检执行异常", "ERROR", "MEDIUM", f"巡检执行异常：{exc}", "检查项目配置、部署路径、服务器连接与后端日志。", str(exc), source_type="ERROR"))
        except Exception:
            pass
        mark_run_failed(db, run_id, f"项目巡检执行失败：{exc}")
    return inspection_run_detail(db, run_id)


def _append_progress_counts_only(db: Session, run: InspectionRun) -> None:
    rows = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).all()
    high, medium, low, normal = _aggregate_risk_counts(rows)
    # D2 修复：progress 路径也按 server_count 平均，与 _finalize_run 对齐
    # 避免长巡检中 score 在 1 server 与 N server 之前反复跳变
    distinct_servers = {r.server_id for r in rows if r.server_id}
    server_count = max(1, len(distinct_servers) or 1)
    run.high_count = high
    run.medium_count = medium
    run.low_count = low
    run.normal_count = normal
    run.score = _compute_score(high, medium, low, server_count=server_count)
    run.summary = f"巡检执行中：已完成 {len(rows)} 项，高危 {high}，中危 {medium}，低危 {low}（{server_count} 台机器）。"
    run.updated_at = _now()
    db.commit()


def run_project_inspection(db: Session, *, project_id: str, categories: Optional[List[str]] = None, trigger_type: str = "MANUAL", created_by: str = "", include_server_summary: bool = True) -> Dict[str, Any]:
    run = create_project_inspection_run(db, project_id=project_id, categories=categories, trigger_type=trigger_type, created_by=created_by)
    return execute_project_inspection_run(db, run_id=run.id, project_id=project_id, categories=categories, include_server_summary=include_server_summary)

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


def list_runs(db: Session, *, scope_type: str = "", server_id: str = "", project_id: str = "", limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    q = db.query(InspectionRun)
    if scope_type:
        q = q.filter(InspectionRun.scope_type == scope_type.upper())
    if server_id:
        q = q.filter(InspectionRun.server_id == server_id)
    if project_id:
        q = q.filter(InspectionRun.project_id == project_id)
    total = q.count()
    rows = q.order_by(InspectionRun.created_at.desc()).offset(offset).limit(limit).all()
    return {"items": [_run_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def _run_to_dict(r: InspectionRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "scope_type": r.scope_type,
        "task_id": getattr(r, "task_id", None),
        "server_id": r.server_id,
        "project_id": r.project_id,
        "agent_id": getattr(r, "agent_id", None),
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
        "duration_ms": getattr(r, "duration_ms", 0) or 0,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _item_to_dict(r: InspectionItemResult) -> Dict[str, Any]:
    return {"id": r.id, "run_id": r.run_id, "scope_type": r.scope_type, "server_id": r.server_id, "project_id": r.project_id, "category": r.category, "item_code": r.item_code, "item_name": r.item_name, "status": r.status, "risk_level": r.risk_level, "message": r.message, "suggestion": r.suggestion, "evidence_id": r.evidence_id, "raw_output": r.raw_output, "parsed_facts": r.parsed_facts, "created_at": r.created_at.isoformat() if r.created_at else None}



def _item_log_dict(r: InspectionItemResult) -> Dict[str, Any]:
    raw = r.raw_output or ""
    command = ""
    if raw.startswith("$ "):
        marker = "\nexit="
        idx = raw.find(marker)
        command = raw[2:idx].strip() if idx > 0 else raw[2:].split("\n#", 1)[0].strip()
    return {
        "time": r.created_at.isoformat() if r.created_at else None,
        "category": r.category,
        "item_code": r.item_code,
        "item_name": r.item_name,
        "status": r.status,
        "risk_level": r.risk_level,
        "message": r.message,
        "suggestion": r.suggestion,
        "evidence_id": r.evidence_id,
        "command": command,
        "raw_output": raw,
    }


def _issue_to_dict(r: InspectionIssue) -> Dict[str, Any]:
    return {"id": r.id, "run_id": r.run_id, "item_result_id": r.item_result_id, "scope_type": r.scope_type, "server_id": r.server_id, "project_id": r.project_id, "title": r.title, "description": r.description, "risk_level": r.risk_level, "status": r.status, "owner_id": r.owner_id, "deadline_at": r.deadline_at.isoformat() if r.deadline_at else None, "fixed_at": r.fixed_at.isoformat() if r.fixed_at else None, "verified_at": r.verified_at.isoformat() if r.verified_at else None, "suggestion": r.suggestion, "evidence_id": r.evidence_id, "created_at": r.created_at.isoformat() if r.created_at else None, "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def inspection_run_detail(db: Session, run_id: str) -> Dict[str, Any]:
    run = db.query(InspectionRun).filter(InspectionRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Inspection run not found")
    items = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run_id).order_by(InspectionItemResult.created_at.asc()).all()
    issues = db.query(InspectionIssue).filter(InspectionIssue.run_id == run_id).order_by(InspectionIssue.created_at.asc()).all()
    return {"run": _run_to_dict(run), "items": [_item_to_dict(x) for x in items], "issues": [_issue_to_dict(x) for x in issues], "logs": [_item_log_dict(x) for x in items]}


def list_issues(db: Session, *, scope_type: str = "", risk_level: str = "", status: str = "", server_id: str = "", project_id: str = "", limit: int = 200, offset: int = 0) -> Dict[str, Any]:
    q = db.query(InspectionIssue)
    if scope_type:
        q = q.filter(InspectionIssue.scope_type == scope_type.upper())
    if risk_level:
        q = q.filter(InspectionIssue.risk_level == risk_level.upper())
    if status:
        # 支持逗号分隔的多状态（例如 status=OPEN,PROCESSING 表示"未闭环"），
        # 与 /inspection/overview 的 open_issue_count 口径保持一致。
        wanted = [s.strip().upper() for s in str(status).split(",") if s.strip()]
        if len(wanted) == 1:
            q = q.filter(InspectionIssue.status == wanted[0])
        elif wanted:
            q = q.filter(InspectionIssue.status.in_(wanted))
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


def _running_runs_with_progress(db: Session) -> List[Dict[str, Any]]:
    """概览页实时进度：当前执行中/等待中的巡检运行及其完成进度。

    - items_done：该 run 已写入的检查项结果数（实时）
    - items_total：按启用检查项配置估算的预期检查项数
    - progress_percent：按 items_done/items_total 估算的进度（RUNNING 封顶 99%）
    """
    # 先回收僵尸（进程重启/后台任务异常留下的永久 RUNNING），保证下面列出的
    # "执行中"任务确实还在推进，而不是永远挂着的历史残骸。
    reap_stale_inspection_runs(db)
    running = (db.query(InspectionRun)
               .filter(InspectionRun.status.in_(["RUNNING", "PENDING"]))
               .order_by(InspectionRun.created_at.desc())
               .limit(50).all())
    run_ids = [r.id for r in running]

    now = _now()
    items: List[Dict[str, Any]] = []
    if run_ids:
        done_map: Dict[str, int] = {}
        for (run_id,) in (db.query(InspectionItemResult.run_id)
                          .filter(InspectionItemResult.run_id.in_(run_ids)).all()):
            done_map[run_id] = done_map.get(run_id, 0) + 1

        expected_map: Dict[str, int] = {}
        for r in running:
            cats = list(r.categories or [])
            scope = r.scope_type or SERVER_SCOPE
            if not cats:
                continue
            expected_map[r.id] = db.query(InspectionItemConfig).filter(
                InspectionItemConfig.scope_type == scope,
                InspectionItemConfig.enabled == True,  # noqa: E712
                InspectionItemConfig.category.in_(cats),
            ).count()

        for r in running:
            run_id = r.id
            done = done_map.get(run_id, 0)
            expected = expected_map.get(run_id, 0)
            if expected > 0 and done >= expected:
                percent = 99
            elif expected > 0:
                percent = max(1, min(99, int(done / expected * 100)))
            elif done > 0:
                percent = 99
            else:
                percent = 1 if r.status == "RUNNING" else 0
            started = r.started_at or r.created_at
            duration_ms = r.duration_ms or 0
            if started and not r.finished_at:
                duration_ms = int((now - started).total_seconds() * 1000)
            item = _run_to_dict(r)
            item["items_done"] = done
            item["items_total"] = expected
            item["progress_percent"] = percent
            item["duration_ms"] = duration_ms
            items.append(item)

    # 追加 operation_jobs（MCP 后台工具任务，如安全日报巡检）运行中条目，
    # 使概览页「当前执行进度」也能实时展示工具任务进度。
    try:
        from app.db.models import OperationJob
        job_rows = (db.query(OperationJob)
                    .filter(OperationJob.status.in_(["queued", "pending", "running"]))
                    .order_by(OperationJob.created_at.desc())
                    .limit(20).all())
        for job in job_rows:
            started = job.started_at or job.created_at
            duration_ms = 0
            if started:
                duration_ms = int((now - started).total_seconds() * 1000)
            items.append({
                "id": job.id,
                "job_kind": "operation_job",
                "source_tool": job.source_tool or "",
                "title": job.title or job.source_tool or "工具任务",
                "scope_type": "TOOL",
                "status": (job.status or "running").upper(),
                "progress_percent": max(0, min(100, job.progress or 0)),
                "items_done": 0,
                "items_total": 0,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": started.isoformat() if started else None,
                "duration_ms": duration_ms,
            })
    except Exception:
        pass
    return items


def overview(db: Session) -> Dict[str, Any]:
    recent = db.query(InspectionRun).order_by(InspectionRun.created_at.desc()).limit(50).all()
    # 未闭环 = OPEN + PROCESSING。计数一律走聚合查询（不受列表 limit 影响），
    # 且与 GET /inspection/issues?status=OPEN,PROCESSING 的 total 完全一致。
    unclosed = InspectionIssue.status.in_(["OPEN", "PROCESSING"])
    status_counts = {
        str(state): int(count or 0)
        for state, count in db.query(InspectionIssue.status, func.count(InspectionIssue.id))
        .filter(unclosed).group_by(InspectionIssue.status).all()
    }
    level_counts = {
        str(level): int(count or 0)
        for level, count in db.query(InspectionIssue.risk_level, func.count(InspectionIssue.id))
        .filter(unclosed).group_by(InspectionIssue.risk_level).all()
    }
    recent_issues = (
        db.query(InspectionIssue).filter(unclosed)
        .order_by(InspectionIssue.created_at.desc()).limit(10).all()
    )
    pending_count = status_counts.get("OPEN", 0)
    processing_count = status_counts.get("PROCESSING", 0)
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
        # 未闭环口径（= 列表 status=OPEN,PROCESSING），保留旧字段名兼容既有调用方
        "open_issue_count": pending_count + processing_count,
        "pending_issue_count": pending_count,
        "processing_issue_count": processing_count,
        "high_issue_count": level_counts.get("HIGH", 0),
        "medium_issue_count": level_counts.get("MEDIUM", 0),
        "low_issue_count": level_counts.get("LOW", 0),
        "running_runs": _running_runs_with_progress(db),
        "recent_runs": [_run_to_dict(r) for r in recent[:10]],
        "recent_issues": [_issue_to_dict(i) for i in recent_issues],
    }



def _parse_dt(value: Any, fallback: Optional[datetime] = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    if value:
        text_value = str(value).strip()
        try:
            if len(text_value) == 10:
                return datetime.fromisoformat(text_value + "T00:00:00")
            parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
            # 全仓持久化时间统一为 naive UTC：带偏移的入参必须换算到 UTC，
            # 不能直接丢掉 tzinfo（否则 +08:00 会被当成 UTC，日期窗整体偏移 8 小时）。
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except Exception:
            pass
    return fallback or _now()


def _period_bounds(period: str = "daily", date_from: str = "", date_to: str = "") -> tuple[datetime, datetime, str]:
    now = _now()
    p = str(period or "daily").strip().lower()
    if date_from or date_to:
        start = _parse_dt(date_from, now.replace(hour=0, minute=0, second=0, microsecond=0))
        if date_to:
            end = _parse_dt(date_to, now)
            if len(str(date_to).strip()) == 10:
                end = end.replace(hour=23, minute=59, second=59, microsecond=999000)
        else:
            end = now
        return start, end, p
    if p in {"daily", "day"}:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
        return start, end, "daily"
    if p in {"weekly", "week"}:
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
        return start, end, "weekly"
    if p in {"monthly", "month"}:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
        return start, end, "monthly"
    # 未知周期兜底为"今日"。这里必须显式给 end 赋值：此前该分支直接返回未绑定的 end，
    # 任何非 daily/weekly/monthly 的 period（如 "quarterly"、"7d"）都会抛
    # UnboundLocalError，台账/报表/删除接口一律 500。
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now, "daily"


def _issue_status_counts(issues: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"OPEN": 0, "PROCESSING": 0, "FIXED": 0, "VERIFIED": 0, "IGNORED": 0}
    for issue in issues or []:
        status = str(issue.get("status") or "OPEN").upper()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _category_summary(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in items or []:
        cat = str(item.get("category") or "UNCATEGORIZED")
        row = grouped.setdefault(cat, {"category": cat, "total": 0, "pass": 0, "warning": 0, "risk": 0, "error": 0, "skipped": 0, "high": 0, "medium": 0, "low": 0, "none": 0})
        row["total"] += 1
        status = str(item.get("status") or "").lower()
        risk = str(item.get("risk_level") or "NONE").lower()
        if status in row:
            row[status] += 1
        if risk in row:
            row[risk] += 1
    return list(grouped.values())


def _ledger_row_from_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    run = detail.get("run") or {}
    items = detail.get("items") or []
    issues = detail.get("issues") or []
    issue_status = _issue_status_counts(issues)
    duration_ms = int(run.get("duration_ms") or 0)
    return {
        "run_id": run.get("id"),
        "inspection_time": run.get("created_at") or run.get("started_at"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "duration_ms": duration_ms,
        "duration_text": f"{round(duration_ms / 1000, 2)}s" if duration_ms else "-",
        "scope_type": run.get("scope_type"),
        "target": run.get("project_id") or run.get("server_id") or "-",
        "server_id": run.get("server_id"),
        "project_id": run.get("project_id"),
        "trigger_type": run.get("trigger_type"),
        "created_by": run.get("created_by"),
        "status": run.get("status"),
        "score": run.get("score"),
        "high_count": run.get("high_count") or 0,
        "medium_count": run.get("medium_count") or 0,
        "low_count": run.get("low_count") or 0,
        "normal_count": run.get("normal_count") or 0,
        "item_count": len(items),
        "issue_count": len(issues),
        "issue_status": issue_status,
        "open_issue_count": issue_status.get("OPEN", 0) + issue_status.get("PROCESSING", 0),
        "closed_issue_count": issue_status.get("FIXED", 0) + issue_status.get("VERIFIED", 0) + issue_status.get("IGNORED", 0),
        "category_summary": _category_summary(items),
        "summary": run.get("summary") or "-",
        "report_id": run.get("report_id"),
        "categories": run.get("categories") or [],
    }


def inspection_ledger(db: Session, *, period: str = "daily", date_from: str = "", date_to: str = "", scope_type: str = "", server_id: str = "", project_id: str = "", status: str = "", limit: int = 500, offset: int = 0) -> Dict[str, Any]:
    start, end, normalized_period = _period_bounds(period, date_from, date_to)
    limit = max(1, min(int(limit or 500), 1000))
    offset = max(0, int(offset or 0))
    q = db.query(InspectionRun).filter(InspectionRun.created_at >= start, InspectionRun.created_at <= end)
    if scope_type:
        q = q.filter(InspectionRun.scope_type == scope_type.upper())
    if server_id:
        q = q.filter(InspectionRun.server_id == server_id)
    if project_id:
        q = q.filter(InspectionRun.project_id == project_id)
    if status:
        q = q.filter(InspectionRun.status == status.upper())
    total = q.count()
    # Summary is computed from all matching rows in the current period, while
    # items below are paginated for UI rendering.  This keeps the ledger cards
    # meaningful even when the table is split across pages.
    summary_runs = q.order_by(InspectionRun.created_at.desc()).limit(5000).all()
    page_runs = q.order_by(InspectionRun.created_at.desc()).offset(offset).limit(limit).all()
    summary_rows: List[Dict[str, Any]] = []
    for r in summary_runs:
        try:
            summary_rows.append(_ledger_row_from_detail(inspection_run_detail(db, r.id)))
        except Exception:
            summary_rows.append(_ledger_row_from_detail({"run": _run_to_dict(r), "items": [], "issues": []}))
    rows: List[Dict[str, Any]] = []
    for r in page_runs:
        try:
            rows.append(_ledger_row_from_detail(inspection_run_detail(db, r.id)))
        except Exception:
            rows.append(_ledger_row_from_detail({"run": _run_to_dict(r), "items": [], "issues": []}))
    high = sum(int(x.get("high_count") or 0) for x in summary_rows)
    medium = sum(int(x.get("medium_count") or 0) for x in summary_rows)
    low = sum(int(x.get("low_count") or 0) for x in summary_rows)
    open_issues = sum(int(x.get("open_issue_count") or 0) for x in summary_rows)
    item_total = sum(int(x.get("item_count") or 0) for x in summary_rows)
    scores = [int(x.get("score") or 0) for x in summary_rows if x.get("score") is not None]
    by_scope: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for row in summary_rows:
        by_scope[str(row.get("scope_type") or "-")] = by_scope.get(str(row.get("scope_type") or "-"), 0) + 1
        by_status[str(row.get("status") or "-")] = by_status.get(str(row.get("status") or "-"), 0) + 1
    summary = {
        "period": normalized_period,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "run_count": total,
        "item_count": item_total,
        "avg_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "high_count": high,
        "medium_count": medium,
        "low_count": low,
        "open_issue_count": open_issues,
        "by_scope": by_scope,
        "by_status": by_status,
    }
    return {"summary": summary, "items": rows, "total": total, "limit": limit, "offset": offset, "schema_version": SCHEMA_VERSION}


def inspection_periodic_report_payload(db: Session, *, period: str = "daily", date_from: str = "", date_to: str = "", scope_type: str = "") -> Dict[str, Any]:
    ledger = inspection_ledger(db, period=period, date_from=date_from, date_to=date_to, scope_type=scope_type, limit=1000)
    rows = ledger.get("items") or []
    all_items: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []
    for row in rows:
        rid = row.get("run_id")
        if not rid:
            continue
        try:
            detail = inspection_run_detail(db, rid)
            all_items.extend(detail.get("items") or [])
            all_issues.extend(detail.get("issues") or [])
        except Exception:
            pass
    category = _category_summary(all_items)
    issue_status = _issue_status_counts(all_issues)
    period_key = (ledger.get("summary") or {}).get("period") or period
    title_map = {"daily": "每日巡检台账", "weekly": "每周巡检报表", "monthly": "月度安全巡检报告"}
    summary = {
        **(ledger.get("summary") or {}),
        "report_kind": period_key,
        "report_title": title_map.get(period_key, "巡检台账与报表"),
        "issue_status": issue_status,
        "closed_issue_count": issue_status.get("FIXED", 0) + issue_status.get("VERIFIED", 0) + issue_status.get("IGNORED", 0),
        "risk_total": len(all_issues),
    }
    return {
        "data": {
            "ledger": rows,
            "items": all_items,
            "issues": all_issues,
            "category_summary": category,
            "issue_status": issue_status,
        },
        "summary": summary,
        "metadata": {"period": period_key, "date_from": summary.get("date_from"), "date_to": summary.get("date_to"), "schema_version": SCHEMA_VERSION},
    }


def report_payload(db: Session, run_id: str) -> Dict[str, Any]:
    detail = inspection_run_detail(db, run_id)
    run = detail["run"]
    ledger_row = _ledger_row_from_detail(detail)
    summary = {
        "score": run.get("score"),
        "scope_type": run.get("scope_type"),
        "server_id": run.get("server_id"),
        "project_id": run.get("project_id"),
        "high_count": run.get("high_count"),
        "medium_count": run.get("medium_count"),
        "low_count": run.get("low_count"),
        "status": run.get("status"),
        "item_count": ledger_row.get("item_count"),
        "issue_count": ledger_row.get("issue_count"),
        "open_issue_count": ledger_row.get("open_issue_count"),
        "closed_issue_count": ledger_row.get("closed_issue_count"),
    }
    detail["ledger"] = ledger_row
    detail["category_summary"] = ledger_row.get("category_summary") or []
    detail["issue_status"] = ledger_row.get("issue_status") or {}
    return {"data": detail, "summary": summary, "metadata": {"run_id": run_id, "schema_version": SCHEMA_VERSION, "ledger_retained": True}}


def _combined_categories(categories: Optional[List[str]]) -> tuple[list[str], list[str]]:
    """Split incoming category list into server/project categories for combined inspection."""
    server_codes = {c["code"] for c in SERVER_CATEGORIES}
    project_codes = {c["code"] for c in PROJECT_CATEGORIES}
    if not categories:
        return [c["code"] for c in SERVER_CATEGORIES], [c["code"] for c in PROJECT_CATEGORIES]
    selected = set(categories)
    server = [c for c in selected if c in server_codes] or [c["code"] for c in SERVER_CATEGORIES]
    project = [c for c in selected if c in project_codes] or [c["code"] for c in PROJECT_CATEGORIES]
    return server, project


def run_project_combined_inspection(db: Session, *, project_id: str, categories: Optional[List[str]] = None, trigger_type: str = "MANUAL", created_by: str = "", generate_report: bool = False) -> Dict[str, Any]:
    """Execute a project combined inspection.

    Combined inspection is the project-level final conclusion: it runs server
    base checks for project deployment servers and project checks in a single
    run, then calculates a combined score and can generate a report center
    artifact. It remains read-only in phase 1.
    """
    project = _get_project_with_relations(db, project_id)
    server_cats, project_cats = _combined_categories(categories)
    all_categories = [f"SERVER::{c}" for c in server_cats] + [f"PROJECT::{c}" for c in project_cats]
    run = _new_run(
        id=uuid4().hex,
        scope_type=PROJECT_COMBINED_SCOPE,
        project_id=project_id,
        trigger_type=trigger_type,
        status="RUNNING",
        categories=all_categories,
        metadata_json={"project": {k: v for k, v in project.items() if k != "config"}, "server_categories": server_cats, "project_categories": project_cats},
        created_by=created_by,
        started_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    servers = project.get("servers") or []
    if not servers:
        _save_result(db, run, CheckResult("RUNTIME_ENVIRONMENT", "PROJECT_NO_SERVER", "项目部署服务器关联检查", "WARNING", "MEDIUM", "项目未配置部署服务器，无法执行服务器基础巡检。", "在系统/服务配置中维护项目部署服务器。", json.dumps(project, ensure_ascii=False), source_type="CONFIG"))
    else:
        thresholds = _load_thresholds(db)
        for server_name in servers[:5]:
            for result in _server_checkers(server_name, server_cats, thresholds=thresholds):
                # Preserve server identity in message/category evidence for combined run.
                result.category = f"SERVER_{result.category}"
                result.item_name = f"{result.item_name}：{server_name}"
                result.message = f"[{server_name}] {result.message}"
                _save_result(db, run, result)

    selected = set(project_cats)
    for result in _project_config_checks(project, selected):
        _save_result(db, run, result)
    for result in _project_remote_checks(project, selected):
        _save_result(db, run, result)
    _append_server_summary(db, run, project)
    run = _finalize_run(db, run)
    result = inspection_run_detail(db, run.id)
    if generate_report:
        try:
            report = generate_report_for_run(db, run.id, fmt="md", created_by=created_by).get("report")
            result["report"] = report
        except Exception as exc:
            result["report_error"] = str(exc)
    return result


def get_issue(db: Session, issue_id: str) -> Dict[str, Any]:
    row = db.query(InspectionIssue).filter(InspectionIssue.id == issue_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Inspection issue not found")
    return _issue_to_dict(row)


def delete_issue(db: Session, issue_id: str) -> Dict[str, Any]:
    """硬删除风险问题（仅在确认后调用，不做软删以便清理历史脏数据）。"""
    row = db.query(InspectionIssue).filter(InspectionIssue.id == issue_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Inspection issue not found")
    db.delete(row)
    db.commit()
    return {"id": issue_id, "deleted": True}


def get_evidence(db: Session, evidence_id: str) -> Dict[str, Any]:
    row = db.query(InspectionEvidence).filter(InspectionEvidence.id == evidence_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Inspection evidence not found")
    return {
        "id": row.id,
        "run_id": row.run_id,
        "source_type": row.source_type,
        "source_path": row.source_path,
        "command": row.command,
        "content_snapshot": row.content_snapshot,
        "content_hash": row.content_hash,
        "masked": row.masked,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }



def _ensure_inspection_rule_schema(db: Session) -> None:
    """Best-effort runtime compatibility for older SQLite databases.

    Some users already created ``inspection_rules`` before rule_content/deleted
    were added. SQLAlchemy will SELECT every mapped column, so a missing column
    makes ``GET /inspection/rules`` fail with 500 before normal migrations are
    noticed. Keep this guard local to rule APIs so old databases are repaired on
    first access as well as during startup migrations.
    """
    try:
        bind = getattr(db, "bind", None)
        dialect = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect != "sqlite":
            return
        db.execute(text("""CREATE TABLE IF NOT EXISTS inspection_rules (
            id VARCHAR(32) PRIMARY KEY,
            rule_code VARCHAR(128) NOT NULL UNIQUE,
            rule_name VARCHAR(255) NOT NULL,
            category VARCHAR(64) NOT NULL,
            scope_type VARCHAR(24) NOT NULL,
            risk_level VARCHAR(24) DEFAULT 'LOW',
            enabled BOOLEAN DEFAULT 1,
            deleted BOOLEAN DEFAULT 0,
            config_json JSON,
            rule_content TEXT,
            description TEXT,
            suggestion TEXT,
            version VARCHAR(64) DEFAULT 'inspection.center.v2',
            created_at DATETIME,
            updated_at DATETIME
        )"""))
        rows = db.execute(text("PRAGMA table_info(inspection_rules)")).fetchall()
        cols = {str(row[1]) for row in rows}
        if not cols:
            return
        statements = []
        if "deleted" not in cols:
            statements.append("ALTER TABLE inspection_rules ADD COLUMN deleted BOOLEAN DEFAULT 0")
        if "rule_content" not in cols:
            statements.append("ALTER TABLE inspection_rules ADD COLUMN rule_content TEXT")
        # Very old local databases may miss columns added by the base rule table.
        if "config_json" not in cols:
            statements.append("ALTER TABLE inspection_rules ADD COLUMN config_json JSON")
        if "version" not in cols:
            statements.append("ALTER TABLE inspection_rules ADD COLUMN version VARCHAR(64) DEFAULT 'inspection.center.v2'")
        if "updated_at" not in cols:
            statements.append("ALTER TABLE inspection_rules ADD COLUMN updated_at DATETIME")
        if "created_at" not in cols:
            statements.append("ALTER TABLE inspection_rules ADD COLUMN created_at DATETIME")
        for sql in statements:
            try:
                db.execute(text(sql))
            except Exception:
                # Another worker may have added it between PRAGMA and ALTER.
                db.rollback()
                continue
        if statements:
            db.commit()
    except Exception:
        db.rollback()


def _rule_command_policy(content: Any) -> Dict[str, Any]:
    """规则命令的只读策略判定（单一实现，供规则列表/详情与内置规则共用）。

    执行时真正的拦截在 ``_sanitize_rule_shell``；这里只把同一判定结果回传给
    前端/AI，让规则作者在保存或浏览时就能看到"该规则执行时会被跳过"。
    """
    _, reason = _sanitize_rule_shell(str(content or ""))
    return {"readonly_allowed": reason is None, "reason": reason}


def _rule_to_dict(r: InspectionRule, *, builtin: bool = False) -> Dict[str, Any]:
    # Compute execution/criteria by looking up the spec in builtin specs (if any)
    execution = ""
    criteria = ""
    cat = (r.category or "").upper()
    code = (r.rule_code or "").upper()
    for spec in _server_check_specs("", [cat]):
        if spec["category"] == cat and code.endswith(spec["item_code"]):
            execution = spec.get("execution", "")
            criteria = spec.get("criteria", "")
            break
    if not execution:
        meta = PROJECT_ITEM_META.get(cat) or {}
        execution = meta.get("execution", "")
        criteria = meta.get("criteria", "")
    # Override with user-stored values from config_json (custom rules)
    if isinstance(r.config_json, dict):
        if r.config_json.get("execution"):
            execution = r.config_json["execution"]
        if r.config_json.get("criteria"):
            criteria = r.config_json["criteria"]
    rule_content = getattr(r, "rule_content", None) or ((r.config_json or {}).get("content") if isinstance(r.config_json, dict) else None)
    if not str(rule_content or "").strip():
        rule_content = "\n".join(_extract_config_commands(r.config_json))
    return {
        "id": r.id,
        "rule_code": r.rule_code,
        "rule_name": r.rule_name,
        "category": r.category,
        "scope_type": r.scope_type,
        "risk_level": r.risk_level,
        "enabled": r.enabled,
        "deleted": bool(getattr(r, "deleted", False)),
        "config": r.config_json or {},
        "rule_content": rule_content,
        "execution": execution,
        "criteria": criteria,
        "description": r.description,
        "suggestion": r.suggestion,
        "version": r.version,
        "builtin": builtin,
        "command_policy": _rule_command_policy(rule_content),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _rule_dict_from_payload(payload: Dict[str, Any], *, fallback_code: str = "") -> Dict[str, Any]:
    code = str(payload.get("rule_code") or fallback_code or "").strip().upper().replace(" ", "_")
    if not code:
        name = str(payload.get("rule_name") or "CUSTOM").strip().upper().replace(" ", "_")
        code = f"CUSTOM_{name[:32] or uuid4().hex[:8]}"
    # Fold execution/criteria into config_json so the dict is safe for InspectionRule(**data)
    cfg = payload.get("config") if isinstance(payload.get("config"), dict) else (payload.get("config_json") if isinstance(payload.get("config_json"), dict) else {})
    cfg = dict(cfg or {})
    if payload.get("execution") is not None:
        cfg["execution"] = str(payload.get("execution"))
    if payload.get("criteria") is not None:
        cfg["criteria"] = str(payload.get("criteria"))
    return {
        "rule_code": code,
        "rule_name": str(payload.get("rule_name") or code).strip() or code,
        "category": str(payload.get("category") or "CUSTOM").strip().upper() or "CUSTOM",
        "scope_type": str(payload.get("scope_type") or "BOTH").strip().upper() or "BOTH",
        "risk_level": str(payload.get("risk_level") or "MEDIUM").strip().upper() or "MEDIUM",
        "enabled": bool(payload.get("enabled", True)),
        "description": str(payload.get("description") or "自定义巡检规则").strip(),
        "suggestion": str(payload.get("suggestion") or "请按巡检规范处理。").strip(),
        "rule_content": str(payload.get("rule_content") or payload.get("content") or "").strip(),
        "config_json": cfg,
        "version": str(payload.get("version") or SCHEMA_VERSION),
    }


def _all_builtin_rules() -> List[Dict[str, Any]]:
    builtin: List[Dict[str, Any]] = []
    for cat in SERVER_CATEGORIES:
        code = f"SERVER_{cat['code']}"
        command = SERVER_RULE_COMMANDS.get(cat["code"], cat["description"])
        spec_meta = next((s for s in _server_check_specs("", [cat["code"]]) if s["category"] == cat["code"]), {})
        builtin.append({
            "rule_code": code,
            "rule_name": cat["name"],
            "category": cat["code"],
            "scope_type": SERVER_SCOPE,
            "risk_level": "MEDIUM",
            "enabled": True,
            "description": cat["description"],
            "suggestion": "按服务器巡检规范核查并整改；如确认风险，登记风险问题并完成复查闭环。",
            "rule_content": command,
            "execution": spec_meta.get("execution", ""),
            "criteria": spec_meta.get("criteria", ""),
            "config_json": {"commands": [line.strip() for line in command.splitlines() if line.strip() and not line.strip().startswith("#")]},
            "version": SCHEMA_VERSION,
            "builtin": True,
            "command_policy": _rule_command_policy(command),
        })
    for cat in PROJECT_CATEGORIES:
        code = f"PROJECT_{cat['code']}"
        command = PROJECT_RULE_COMMANDS.get(cat["code"], cat["description"])
        meta = PROJECT_ITEM_META.get(cat["code"]) or {}
        builtin.append({
            "rule_code": code,
            "rule_name": cat["name"],
            "category": cat["code"],
            "scope_type": PROJECT_SCOPE,
            "risk_level": "MEDIUM",
            "enabled": True,
            "description": cat["description"],
            "suggestion": "按项目巡检规范核查并整改；涉及配置、接口、白名单、备份的问题需同步开发/运维/业务负责人。",
            "rule_content": command,
            "execution": meta.get("execution", ""),
            "criteria": meta.get("criteria", ""),
            "config_json": {"commands": [line.strip() for line in command.splitlines() if line.strip() and not line.strip().startswith("#")]},
            "version": SCHEMA_VERSION,
            "builtin": True,
            "command_policy": _rule_command_policy(command),
        })
    return builtin

def list_rules(db: Session, *, scope_type: str = "", category: str = "", enabled: Optional[bool] = None, include_deleted: bool = False, keyword: str = "", risk_level: str = "", limit: int = 0, offset: int = 0) -> Dict[str, Any]:
    """List inspection rules, merging built-in defaults with DB overrides.

    Older implementation only showed built-ins when DB had no rule rows. Once a
    single built-in was edited, the rest disappeared. This version always merges
    built-ins, DB overrides, custom rules, and soft-delete tombstones.
    """
    _ensure_inspection_rule_schema(db)
    try:
        db_rows = db.query(InspectionRule).order_by(InspectionRule.scope_type.asc(), InspectionRule.category.asc(), InspectionRule.rule_code.asc()).limit(1000).all()
    except OperationalError:
        db.rollback()
        _ensure_inspection_rule_schema(db)
        try:
            db_rows = db.query(InspectionRule).order_by(InspectionRule.scope_type.asc(), InspectionRule.category.asc(), InspectionRule.rule_code.asc()).limit(1000).all()
        except OperationalError as exc:
            db.rollback()
            items = [{"id": b["rule_code"], "deleted": False, "config": {}, **b} for b in _all_builtin_rules()]
            return {"items": items, "total": len(items), "warning": "inspection_rules table was not readable; returned built-in rules", "error": str(exc)}
    overrides = {r.rule_code: r for r in db_rows}
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for b in _all_builtin_rules():
        code = b["rule_code"]
        row = overrides.get(code)
        if row:
            if bool(getattr(row, "deleted", False)) and not include_deleted:
                seen.add(code)
                continue
            item = _rule_to_dict(row, builtin=True)
        else:
            item = {"id": code, "deleted": False, "config": {}, **b}
        seen.add(code)
        items.append(item)

    for row in db_rows:
        if row.rule_code in seen:
            continue
        if bool(getattr(row, "deleted", False)) and not include_deleted:
            continue
        items.append(_rule_to_dict(row, builtin=False))

    if scope_type:
        st = scope_type.upper()
        items = [x for x in items if str(x.get("scope_type") or "").upper() == st]
    if category:
        cat = category.upper()
        items = [x for x in items if str(x.get("category") or "").upper() == cat]
    if enabled is not None:
        items = [x for x in items if bool(x.get("enabled")) == bool(enabled)]
    if keyword:
        kw = keyword.lower()
        items = [x for x in items if kw in str(x.get("rule_name") or "").lower() or kw in str(x.get("rule_code") or "").lower()]
    if risk_level:
        rl = risk_level.upper()
        items = [x for x in items if str(x.get("risk_level") or "").upper() == rl]

    total = len(items)
    items.sort(key=lambda x: (str(x.get("scope_type") or ""), str(x.get("category") or ""), str(x.get("rule_code") or "")))
    if limit > 0:
        items = items[offset:offset + limit]
    return {"items": items, "total": total}


def _builtin_rule_by_code(rule_code: str) -> Optional[Dict[str, Any]]:
    code = str(rule_code or "").strip().upper()
    for item in _all_builtin_rules():
        if item["rule_code"] == code:
            # Strip non-model fields so the dict can be unpacked into InspectionRule(**base)
            return {k: v for k, v in item.items() if k not in {"builtin", "execution", "criteria", "command_policy"}}
    return None


def get_rule(db: Session, rule_code: str) -> Dict[str, Any]:
    _ensure_inspection_rule_schema(db)
    code = str(rule_code or "").strip().upper()
    row = db.query(InspectionRule).filter(InspectionRule.rule_code == code).first()
    if row:
        if bool(getattr(row, "deleted", False)):
            raise HTTPException(status_code=404, detail="Inspection rule deleted")
        return _rule_to_dict(row, builtin=bool(_builtin_rule_by_code(code)))
    builtin = _builtin_rule_by_code(code)
    if builtin:
        # builtin is the model-only dict; pull execution/criteria from the full builtin entry
        full = next((b for b in _all_builtin_rules() if b["rule_code"] == code), {})
        return {
            "id": code,
            "deleted": False,
            "config": {},
            "execution": full.get("execution", ""),
            "criteria": full.get("criteria", ""),
            "builtin": True,
            **builtin,
        }
    raise HTTPException(status_code=404, detail="Inspection rule not found")


def update_rule(db: Session, rule_code: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_inspection_rule_schema(db)
    """Create/update/rename an inspection rule.

    Supports complete content editing: code/name/category/scope/risk/enabled,
    description, suggestion, rule_content and config_json. Built-in rules are
    materialized into DB on first edit; renaming a built-in creates an editable
    custom rule and leaves the original built-in intact unless it is explicitly
    deleted.
    """
    old_code = str(rule_code or "").strip().upper()
    new_code = str(payload.get("rule_code") or old_code).strip().upper().replace(" ", "_")
    if not new_code:
        raise HTTPException(status_code=400, detail="rule_code is required")

    allowed_levels = {"HIGH", "MEDIUM", "LOW", "NONE"}
    allowed_scopes = {SERVER_SCOPE, PROJECT_SCOPE, PROJECT_COMBINED_SCOPE, "BOTH"}

    level = str(payload.get("risk_level") or "").upper()
    if level and level not in allowed_levels:
        raise HTTPException(status_code=400, detail="invalid risk_level")
    scope = str(payload.get("scope_type") or "").upper()
    if scope and scope not in allowed_scopes:
        raise HTTPException(status_code=400, detail="invalid scope_type")

    row = db.query(InspectionRule).filter(InspectionRule.rule_code == old_code).first() if old_code else None
    if not row and old_code:
        base = _builtin_rule_by_code(old_code)
        if base:
            row = InspectionRule(id=uuid4().hex, created_at=_now(), updated_at=_now(), **base)
            db.add(row)

    if row and new_code != row.rule_code:
        exists = db.query(InspectionRule).filter(InspectionRule.rule_code == new_code).first()
        if exists:
            raise HTTPException(status_code=409, detail="new rule_code already exists")
        row.rule_code = new_code
    elif not row:
        exists = db.query(InspectionRule).filter(InspectionRule.rule_code == new_code).first()
        if exists:
            raise HTTPException(status_code=409, detail="rule_code already exists")
        data = _rule_dict_from_payload(payload, fallback_code=new_code)
        data["deleted"] = False
        row = InspectionRule(id=uuid4().hex, created_at=_now(), updated_at=_now(), **data)
        db.add(row)

    # Apply editable fields.
    if "rule_name" in payload and payload["rule_name"] is not None:
        row.rule_name = str(payload["rule_name"]).strip() or row.rule_name
    if "category" in payload and payload["category"] is not None:
        row.category = str(payload["category"]).strip().upper() or row.category
    if "scope_type" in payload and payload["scope_type"] is not None:
        row.scope_type = str(payload["scope_type"]).upper()
    if "risk_level" in payload and payload["risk_level"] is not None:
        row.risk_level = str(payload["risk_level"]).upper()
    if "enabled" in payload:
        row.enabled = bool(payload["enabled"])
    if "deleted" in payload:
        row.deleted = bool(payload["deleted"])
    if "description" in payload and payload["description"] is not None:
        row.description = str(payload["description"])
    if "suggestion" in payload and payload["suggestion"] is not None:
        row.suggestion = str(payload["suggestion"])
    if "rule_content" in payload and payload["rule_content"] is not None:
        row.rule_content = str(payload["rule_content"])
    elif "content" in payload and payload["content"] is not None:
        row.rule_content = str(payload["content"])
    if "config" in payload and payload["config"] is not None:
        row.config_json = payload["config"] if isinstance(payload["config"], dict) else {}
    elif "config_json" in payload and payload["config_json"] is not None:
        row.config_json = payload["config_json"] if isinstance(payload["config_json"], dict) else {}
    if "execution" in payload and payload["execution"] is not None:
        # Stored under config_json for round-trip; top-level _rule_to_dict recomputes from specs
        cfg = dict(row.config_json or {})
        cfg["execution"] = str(payload["execution"])
        row.config_json = cfg
    if "criteria" in payload and payload["criteria"] is not None:
        cfg = dict(row.config_json or {})
        cfg["criteria"] = str(payload["criteria"])
        row.config_json = cfg
    if "version" in payload and payload["version"] is not None:
        row.version = str(payload["version"])
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    # 联动：若规则的 category 不在内置 SERVER_CATEGORIES / PROJECT_CATEGORIES 中，
    # 自动创建一条 InspectionItemConfig + InspectionItemRule，让新规则立刻出现在
    # 巡检项选择中并可被前端勾选执行。
    _sync_item_config_for_rule(db, row)
    return _rule_to_dict(row, builtin=bool(_builtin_rule_by_code(row.rule_code)))


def _sync_item_config_for_rule(db: Session, rule_row: InspectionRule) -> None:
    """根据 InspectionRule 自动创建/更新 InspectionItemConfig + InspectionItemRule。

    - 内置分类（LOGIN_SECURITY, DISK 等）不处理：由硬编码的 SERVER_CATEGORIES
      提供，并已有内置 InspectionItemConfig 记录。
    - 对于 category 不在 SERVER_CATEGORIES / PROJECT_CATEGORIES 中的新规则：
      - 若 InspectionItemConfig 中尚未有该 item_code 的条目，创建一条
      - 在 InspectionItemRule 中建立关联（rule_code + 顺序）
    """
    from app.db.models import InspectionItemConfig, InspectionItemRule
    import logging
    logger = logging.getLogger(__name__)

    try:
        category = (rule_row.category or "").strip().upper()
        rule_code = (rule_row.rule_code or "").strip().upper()
        if not category or not rule_code:
            return
        # 若是内置分类，不自动创建（避免污染）
        if category in {c["code"] for c in SERVER_CATEGORIES} or category in {c["code"] for c in PROJECT_CATEGORIES}:
            return
        # 是否已存在 config（item_code 全局唯一）
        cfg = db.query(InspectionItemConfig).filter(
            InspectionItemConfig.item_code == category,
        ).first()
        if not cfg:
            cfg = InspectionItemConfig(
                id=uuid4().hex,
                item_code=category,
                item_name=rule_row.rule_name or category,
                category=str(rule_row.scope_type or "BOTH").upper()[:64],
                scope_type=rule_row.scope_type or "BOTH",
                description=rule_row.description or "由自定义规则生成的巡检项",
                enabled=bool(rule_row.enabled),
                sort_order=900,
                is_builtin=False,
                config_json={},
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(cfg)
            try:
                db.commit()
                db.refresh(cfg)
            except Exception as e:
                logger.warning("sync_item_config commit failed: %s", e)
                db.rollback()
                cfg = db.query(InspectionItemConfig).filter(
                    InspectionItemConfig.item_code == category,
                ).first()
                if not cfg:
                    return
        # 关联 InspectionItemRule
        if cfg:
            link = db.query(InspectionItemRule).filter(
                InspectionItemRule.item_config_id == cfg.id,
                InspectionItemRule.rule_code == rule_code,
            ).first()
            if not link:
                link = InspectionItemRule(
                    id=uuid4().hex,
                    item_config_id=cfg.id,
                    rule_code=rule_code,
                    sort_order=0,
                    enabled=bool(rule_row.enabled),
                    config_override={},
                    created_at=_now(),
                )
                db.add(link)
                try:
                    db.commit()
                except Exception as e:
                    logger.warning("sync_item_rule commit failed: %s", e)
                    db.rollback()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("sync_item_config_for_rule error: %s", e)
        try:
            db.rollback()
        except Exception:
            pass


def create_rule(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_inspection_rule_schema(db)
    code = str(payload.get("rule_code") or "").strip().upper().replace(" ", "_")
    if not code:
        name = str(payload.get("rule_name") or "CUSTOM").strip().upper().replace(" ", "_")
        code = f"CUSTOM_{name[:24] or uuid4().hex[:8]}"
        payload = {**payload, "rule_code": code}
    exists = db.query(InspectionRule).filter(InspectionRule.rule_code == code).first()
    if exists and not bool(getattr(exists, "deleted", False)):
        raise HTTPException(status_code=409, detail="rule_code already exists")
    if exists and bool(getattr(exists, "deleted", False)):
        return update_rule(db, code, {**payload, "deleted": False})
    return update_rule(db, code, payload)


def delete_rule(db: Session, rule_code: str) -> Dict[str, Any]:
    _ensure_inspection_rule_schema(db)
    code = str(rule_code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="rule_code is required")
    row = db.query(InspectionRule).filter(InspectionRule.rule_code == code).first()
    builtin = _builtin_rule_by_code(code)
    if row:
        if builtin:
            row.deleted = True
            row.enabled = False
            row.updated_at = _now()
            db.commit()
            return {"rule_code": code, "deleted": True, "soft_deleted": True}
        db.delete(row)
        db.commit()
        return {"rule_code": code, "deleted": True, "soft_deleted": False}
    if builtin:
        tombstone = InspectionRule(id=uuid4().hex, created_at=_now(), updated_at=_now(), deleted=True, enabled=False, **builtin)
        db.add(tombstone)
        db.commit()
        return {"rule_code": code, "deleted": True, "soft_deleted": True}
    raise HTTPException(status_code=404, detail="Inspection rule not found")


def list_baselines(db: Session, *, scope_type: str = "", server_id: str = "", project_id: str = "", baseline_type: str = "") -> Dict[str, Any]:
    q = db.query(InspectionBaseline)
    if scope_type:
        q = q.filter(InspectionBaseline.scope_type == scope_type.upper())
    if server_id:
        q = q.filter(InspectionBaseline.server_id == server_id)
    if project_id:
        q = q.filter(InspectionBaseline.project_id == project_id)
    if baseline_type:
        q = q.filter(InspectionBaseline.baseline_type == baseline_type)
    rows = q.order_by(InspectionBaseline.created_at.desc()).limit(300).all()
    return {"items": [{
        "id": r.id,
        "scope_type": r.scope_type,
        "server_id": r.server_id,
        "project_id": r.project_id,
        "baseline_type": r.baseline_type,
        "version": r.version,
        "content": r.content_json or {},
        "content_hash": r.content_hash,
        "active": r.active,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows], "total": len(rows)}


def list_project_server_relations(db: Session, *, project_id: str = "") -> Dict[str, Any]:
    q = db.query(ProjectServerRelation).filter(ProjectServerRelation.active == True)  # noqa: E712
    if project_id:
        q = q.filter(ProjectServerRelation.project_id == project_id)
    rows = q.order_by(ProjectServerRelation.project_id.asc(), ProjectServerRelation.server_id.asc()).limit(500).all()
    items = [_relation_to_dict(r) for r in rows]
    if project_id and not items:
        try:
            project = _get_project(project_id)
            items = [{
                "id": f"config::{project_id}::{srv}",
                "project_id": project_id,
                "server_id": srv,
                "deploy_role": "APP",
                "deploy_path": project.get("deploy_path") or "",
                "config_path": project.get("config_path") or "",
                "log_path": project.get("log_path") or "",
                "backup_path": project.get("backup_path") or "",
                "runtime_user": project.get("runtime_user") or "",
                "main_port": str(project.get("main_port") or ""),
                "active": True,
                "source": "config",
            } for srv in (project.get("servers") or [])]
        except Exception:
            pass
    return {"items": items, "total": len(items)}


def report_payload_for_runs(db: Session, run_ids: List[str]) -> Dict[str, Any]:
    normalized: List[str] = []
    seen = set()
    for rid in run_ids or []:
        rid = str(rid or "").strip()
        if rid and rid not in seen:
            normalized.append(rid)
            seen.add(rid)
    if not normalized:
        raise HTTPException(status_code=400, detail="run_ids is required")
    details = []
    for rid in normalized:
        detail = inspection_run_detail(db, rid)
        run = detail.get("run") or {}
        if run.get("status") in {"RUNNING", "PENDING"}:
            raise HTTPException(status_code=409, detail=f"巡检 {rid} 仍在执行中，请等待完成后再生成合并报告")
        if not (detail.get("items") or []):
            raise HTTPException(status_code=409, detail=f"巡检 {rid} 结果为空，请重新执行巡检后再生成报告")
        details.append(detail)
    high = sum(int((d.get("run") or {}).get("high_count") or 0) for d in details)
    medium = sum(int((d.get("run") or {}).get("medium_count") or 0) for d in details)
    low = sum(int((d.get("run") or {}).get("low_count") or 0) for d in details)
    scores = [int((d.get("run") or {}).get("score") or 0) for d in details]
    summary = {
        "scope_type": "MULTI_RUN",
        "run_count": len(details),
        "score": round(sum(scores) / len(scores), 2) if scores else 0,
        "high_count": high,
        "medium_count": medium,
        "low_count": low,
        "status": "SUCCESS" if all((d.get("run") or {}).get("status") == "SUCCESS" for d in details) else "PARTIAL_SUCCESS",
    }
    ledger_rows = [_ledger_row_from_detail(d) for d in details]
    all_items = [item for d in details for item in (d.get("items") or [])]
    all_issues = [issue for d in details for issue in (d.get("issues") or [])]
    data = {
        "runs": [d.get("run") for d in details],
        "items": all_items,
        "issues": all_issues,
        "ledger": ledger_rows,
        "category_summary": _category_summary(all_items),
        "issue_status": _issue_status_counts(all_issues),
        "details": details,
    }
    return {"data": data, "summary": {**summary, "item_count": len(all_items), "issue_count": len(all_issues), "open_issue_count": data["issue_status"].get("OPEN", 0) + data["issue_status"].get("PROCESSING", 0)}, "metadata": {"run_ids": normalized, "schema_version": SCHEMA_VERSION, "ledger_retained": True}}


def generate_report_for_runs(db: Session, run_ids: List[str], *, fmt: str = "md", title: str = "", created_by: str = "") -> Dict[str, Any]:
    from app.services.report_center import generate_report_from_payload
    payload = report_payload_for_runs(db, run_ids)
    result = generate_report_from_payload(
        db,
        payload,
        report_type="inspection",
        target_id="multi_" + uuid4().hex[:12],
        fmt=fmt or "md",
        title=title or f"巡检合并报告（{payload.get('summary', {}).get('run_count', 0)} 条记录）",
        created_by=created_by or "system",
    )
    report = result.get("report") or {}
    if report.get("id"):
        for rid in payload.get("metadata", {}).get("run_ids") or []:
            row = db.query(InspectionRun).filter(InspectionRun.id == rid).first()
            if row:
                row.report_id = report.get("id")
                row.updated_at = _now()
        db.commit()
    return result



def generate_periodic_report(db: Session, *, period: str = "daily", date_from: str = "", date_to: str = "", scope_type: str = "", fmt: str = "md", title: str = "", created_by: str = "") -> Dict[str, Any]:
    from app.services.report_center import generate_report_from_payload
    payload = inspection_periodic_report_payload(db, period=period, date_from=date_from, date_to=date_to, scope_type=scope_type)
    summary = payload.get("summary") or {}
    if not (payload.get("data") or {}).get("ledger"):
        raise HTTPException(status_code=409, detail="当前时间范围没有巡检台账记录，无法生成报表")
    report_title = title or summary.get("report_title") or "巡检台账与报表"
    result = generate_report_from_payload(
        db,
        payload,
        report_type="inspection",
        target_id=f"ledger_{summary.get('report_kind') or period}_{uuid4().hex[:8]}",
        fmt=fmt or "md",
        title=report_title,
        created_by=created_by or "system",
    )
    return result


def _delete_report_artifact_if_orphan(db: Session, report_id: str) -> bool:
    """Delete an inspection report artifact only when no remaining run references it."""
    rid = str(report_id or "").strip()
    if not rid:
        return False
    remaining = db.query(InspectionRun).filter(InspectionRun.report_id == rid).first()
    if remaining:
        return False
    try:
        from app.db.models import ReportArtifact
        from app.services.report_center import report_download_path
        row = db.query(ReportArtifact).filter(ReportArtifact.id == rid, ReportArtifact.report_type == "inspection").first()
        if not row:
            return False
        try:
            path = report_download_path(row)
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        except Exception:
            pass
        db.delete(row)
        return True
    except Exception:
        return False


def delete_inspection_runs(db: Session, run_ids: List[str], *, delete_reports: bool = True, force: bool = False) -> Dict[str, Any]:
    """Delete inspection run history and derived ledger rows.

    Ledger data is derived from inspection_runs; deleting a run removes the
    corresponding ledger row, item results, issues and evidence snapshots.
    RUNNING/PENDING runs are skipped unless force=True to avoid racing with
    the background executor.
    """
    normalized: List[str] = []
    seen = set()
    for rid in run_ids or []:
        value = str(rid or "").strip()
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    if not normalized:
        raise HTTPException(status_code=400, detail="run_ids is required")

    rows = db.query(InspectionRun).filter(InspectionRun.id.in_(normalized)).all()
    found = {r.id: r for r in rows}
    missing = [rid for rid in normalized if rid not in found]
    skipped: List[Dict[str, Any]] = []
    deletable: List[InspectionRun] = []
    for run in rows:
        if not force and str(run.status or "").upper() in {"RUNNING", "PENDING"}:
            skipped.append({"id": run.id, "reason": f"巡检仍在执行中（{run.status}），未删除"})
        else:
            deletable.append(run)
    if not deletable:
        return {"deleted": 0, "requested": len(normalized), "missing": missing, "skipped": skipped, "deleted_reports": 0, "run_ids": []}

    delete_ids = [r.id for r in deletable]
    report_ids = [str(r.report_id or "").strip() for r in deletable if str(r.report_id or "").strip()]

    db.query(InspectionIssue).filter(InspectionIssue.run_id.in_(delete_ids)).delete(synchronize_session=False)
    db.query(InspectionItemResult).filter(InspectionItemResult.run_id.in_(delete_ids)).delete(synchronize_session=False)
    db.query(InspectionEvidence).filter(InspectionEvidence.run_id.in_(delete_ids)).delete(synchronize_session=False)
    db.query(InspectionRun).filter(InspectionRun.id.in_(delete_ids)).delete(synchronize_session=False)
    db.flush()

    deleted_reports = 0
    if delete_reports:
        for rid in sorted(set(report_ids)):
            if _delete_report_artifact_if_orphan(db, rid):
                deleted_reports += 1

    db.commit()
    return {
        "deleted": len(delete_ids),
        "requested": len(normalized),
        "missing": missing,
        "skipped": skipped,
        "deleted_reports": deleted_reports,
        "run_ids": delete_ids,
    }


def delete_inspection_history(db: Session, *, period: str = "daily", date_from: str = "", date_to: str = "", scope_type: str = "", server_id: str = "", project_id: str = "", status: str = "", delete_reports: bool = True, force: bool = False) -> Dict[str, Any]:
    """Delete ledger/history rows matching the same filters used by inspection_ledger."""
    start, end, normalized_period = _period_bounds(period, date_from, date_to)
    q = db.query(InspectionRun).filter(InspectionRun.created_at >= start, InspectionRun.created_at <= end)
    if scope_type:
        q = q.filter(InspectionRun.scope_type == scope_type.upper())
    if server_id:
        q = q.filter(InspectionRun.server_id == server_id)
    if project_id:
        q = q.filter(InspectionRun.project_id == project_id)
    if status:
        q = q.filter(InspectionRun.status == status.upper())
    run_ids = [r.id for r in q.order_by(InspectionRun.created_at.desc()).limit(5000).all()]
    result = delete_inspection_runs(db, run_ids, delete_reports=delete_reports, force=force) if run_ids else {"deleted": 0, "requested": 0, "missing": [], "skipped": [], "deleted_reports": 0, "run_ids": []}
    result.update({"period": normalized_period, "date_from": start.isoformat(), "date_to": end.isoformat()})
    return result

def generate_report_for_run(db: Session, run_id: str, *, fmt: str = "md", title: str = "", created_by: str = "") -> Dict[str, Any]:
    run = db.query(InspectionRun).filter(InspectionRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Inspection run not found")
    if run.status in {"RUNNING", "PENDING"}:
        raise HTTPException(status_code=409, detail="巡检仍在执行中，请等待完成后再生成报告")
    item_count = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run_id).count()
    if item_count == 0:
        raise HTTPException(status_code=409, detail="巡检结果为空，请重新执行巡检后再生成报告")
    from app.services.report_center import generate_report as svc_generate_report
    result = svc_generate_report(db, report_type="inspection", target_id=run_id, fmt=fmt or "md", title=title or "", created_by=created_by or "system")
    report = result.get("report") or {}
    if report.get("id"):
        run.report_id = report.get("id")
        run.updated_at = _now()
        db.commit()
    return result

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
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models import (
    InspectionBaseline,
    InspectionEvidence,
    InspectionIssue,
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


SERVER_RULE_COMMANDS: Dict[str, str] = {
    "LOGIN_SECURITY": """# 登录安全巡检（只读）\nlast -n 80\nlastb -n 80\n# CentOS/RHEL 登录安全日志\ntail -n 200 /var/log/secure 2>/dev/null || true\n# Ubuntu/Debian 登录安全日志\ntail -n 200 /var/log/auth.log 2>/dev/null || true\n# 判定要点：陌生 IP、连续失败登录、root 远程登录、非工作时间登录。""",
    "ACCOUNT_SECURITY": """# 账号安全巡检（只读）\ncat /etc/passwd\ngrep 'x:0' /etc/passwd\nawk -F: '($7 ~ /(bash|sh)$/){print $1,$3,$6,$7}' /etc/passwd\n# 判定要点：陌生账号、UID=0 特权账号、可登录账号、闲置账号。""",
    "COMMAND_HISTORY": """# 命令日志巡检（只读）\nls -la /root/.bash_history /home/*/.bash_history 2>/dev/null || true\nfind /root /home -maxdepth 2 -name '.bash_history' -type f -print -exec tail -n 120 {} \\; 2>/dev/null || true\n# 高危命令关键词：rm -rf、chmod 777、chown、wget、curl、scp、ftp、history -c、mysql、redis-cli、kill。""",
    "PROCESS_PORT": """# 进程端口巡检（只读）\nps -eo pid,ppid,user,pcpu,pmem,etime,cmd --sort=-pcpu | head -n 60\nss -ntulp 2>/dev/null || netstat -ntulp 2>/dev/null || netstat -an | grep LISTEN\n# 判定要点：未知进程、高占用、挖矿特征、未知监听端口、高危端口外网暴露。""",
    "FIREWALL": """# 防火墙巡检（只读）\nsystemctl is-active firewalld 2>/dev/null || true\nfirewall-cmd --list-all 2>/dev/null || true\niptables -S 2>/dev/null || true\nufw status verbose 2>/dev/null || true\n# 判定要点：防火墙关闭、全局放行、高危端口放行、黑白名单冲突。""",
    "DISK": """# 磁盘空间巡检（只读）\ndf -h\ndu -sh /var/log 2>/dev/null || true\ndu -sh /data /backup /opt 2>/dev/null || true\n# 判定要点：磁盘使用率超过阈值、日志目录异常膨胀、备份目录空间不足。""",
    "SERVICE_STATUS": """# 服务状态巡检（只读）\nsystemctl --failed 2>/dev/null || true\nsystemctl is-active nginx 2>/dev/null || true\nsystemctl is-active redis 2>/dev/null || true\nsystemctl is-active mysql mysqld mariadb postgresql 2>/dev/null || true\nps -ef | egrep 'nginx|redis|mysql|postgres|java|node|python|gunicorn|uvicorn' | grep -v grep || true\n# 判定要点：基础服务异常、项目进程缺失、异常重启或失败单元。""",
    "BACKUP": """# 备份任务巡检（只读）\ncrontab -l 2>/dev/null || true\nls -lah /backup /data/backups 2>/dev/null || true\nfind /backup /data/backups -maxdepth 2 -type f -mtime -2 -printf '%TY-%Tm-%Td %TH:%TM %s %p\\n' 2>/dev/null | tail -n 80 || true\n# 判定要点：当日备份缺失、0KB 文件、备份脚本失败、备份堆积、异地同步异常。""",
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

RISK_WEIGHT = {"HIGH": 20, "MEDIUM": 8, "LOW": 2, "NONE": 0}
ISSUE_STATUSES = {"OPEN", "PROCESSING", "FIXED", "VERIFIED", "IGNORED"}

DEFAULT_BATCH_CONCURRENCY = int(os.getenv("INSPECTION_BATCH_CONCURRENCY", "3") or "3")
DEFAULT_BATCH_SIZE = int(os.getenv("INSPECTION_BATCH_SIZE", "5") or "5")
DEFAULT_COMMAND_TIMEOUT_SECONDS = int(os.getenv("INSPECTION_COMMAND_TIMEOUT_SECONDS", "20") or "20")
DEFAULT_RUN_TIMEOUT_SECONDS = int(os.getenv("INSPECTION_RUN_TIMEOUT_SECONDS", "180") or "180")
MAX_BATCH_CONCURRENCY = int(os.getenv("INSPECTION_MAX_BATCH_CONCURRENCY", "8") or "8")
MAX_BATCH_SIZE = int(os.getenv("INSPECTION_MAX_BATCH_SIZE", "20") or "20")
MAX_COMMAND_TIMEOUT_SECONDS = int(os.getenv("INSPECTION_MAX_COMMAND_TIMEOUT_SECONDS", "120") or "120")
MAX_RUN_TIMEOUT_SECONDS = int(os.getenv("INSPECTION_MAX_RUN_TIMEOUT_SECONDS", "1800") or "1800")
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
        return {"exit_code": code, "stdout": out or "", "stderr": err or "", "duration_ms": int((time.time() - started) * 1000)}
    finally:
        try:
            ssh.close()
        except Exception:
            pass


def _remote_check(server_name: str, category: str, item_code: str, item_name: str, command: str, analyze, timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> CheckResult:
    try:
        result = _run_remote(server_name, command, timeout=timeout_seconds)
        output = f"$ {command}\nexit={result.get('exit_code')} duration_ms={result.get('duration_ms')}\n{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
        risk_level, status, message, suggestion = analyze(result.get("stdout") or "", result.get("stderr") or "", result.get("exit_code"))
        return CheckResult(category, item_code, item_name, status, risk_level, message, suggestion, output, command)
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
    r"\bredis-cli\b\s+.*\bflushall\b|\bmkfs\b|\bdd\b\s+if=|>\s*/etc/|>>\s*/etc/)"
)


def _extract_config_commands(config: Any) -> List[str]:
    if not isinstance(config, dict):
        return []
    value = config.get("commands") or config.get("shell") or config.get("cmd")
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _sanitize_rule_shell(command: str) -> tuple[str, Optional[str]]:
    """Normalize a rule shell script and reject obvious destructive commands.

    The巡检中心第一阶段只允许只读巡检。规则内容可以是多行 shell，支持
    管道、grep/find/awk/tail/ss/df 等只读命令，但会拒绝删除、修改、重启、
    防火墙变更、DML 等高风险关键字。
    """
    cmd = str(command or "").replace("\r\n", "\n").strip()
    if not cmd:
        return "", "规则内容为空，无法生成可执行命令。"
    scan_text = "\n".join(line for line in cmd.splitlines() if not line.strip().startswith("#"))
    if _DANGEROUS_SHELL_RE.search(scan_text):
        return cmd, "规则命令包含疑似高风险写入/变更操作，已按只读巡检策略拦截。"
    return cmd, None


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
        "FILE_SECURITY": _analyze_project_files,
        "CONFIG_SECURITY": _analyze_project_api,
        "API_SECURITY": _analyze_project_api,
        "WHITELIST_SECURITY": _analyze_process_ports,
        "CUSTOMER_SECURITY": _analyze_project_api,
        "BACKUP_SECURITY": _analyze_project_backup,
        "RUNTIME_ENVIRONMENT": _analyze_project_runtime,
    }
    return mapping.get(cat, lambda out, err, code: ("NONE", "PASS", "规则命令已执行，未配置专用判定器。", "请结合命令输出人工复核。"))


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
    """
    selected = set(categories or [c["code"] for c in SERVER_CATEGORIES])
    specs: List[Dict[str, Any]] = []
    if "LOGIN_SECURITY" in selected:
        specs.append({"category": "LOGIN_SECURITY", "item_code": "SERVER_LOGIN_RECENT", "item_name": "近期登录与失败登录检查", "command": "(last -n 20 2>/dev/null || true); echo '---FAILED---'; (lastb -n 20 2>/dev/null || true)", "analyze": _analyze_login})
    if "ACCOUNT_SECURITY" in selected:
        specs.append({"category": "ACCOUNT_SECURITY", "item_code": "SERVER_ACCOUNT_PRIVILEGE", "item_name": "系统账号与特权账号检查", "command": "echo '---PASSWD---'; cat /etc/passwd 2>/dev/null | head -200; echo '---UID0---'; grep 'x:0:' /etc/passwd 2>/dev/null || true", "analyze": _analyze_accounts})
    if "COMMAND_HISTORY" in selected:
        specs.append({"category": "COMMAND_HISTORY", "item_code": "SERVER_HISTORY_DANGEROUS", "item_name": "高危命令历史检查", "command": "(tail -n 200 ~/.bash_history 2>/dev/null || true); echo '---ROOT---'; (tail -n 200 /root/.bash_history 2>/dev/null || true)", "analyze": _analyze_history})
    if "PROCESS_PORT" in selected:
        specs.append({"category": "PROCESS_PORT", "item_code": "SERVER_PROCESS_PORT", "item_name": "进程与监听端口检查", "command": "echo '---TOP---'; ps -eo pid,ppid,user,comm,%cpu,%mem,args --sort=-%cpu 2>/dev/null | head -30; echo '---PORTS---'; (ss -ntulp 2>/dev/null || netstat -ntulp 2>/dev/null || true)", "analyze": _analyze_process_ports})
    if "FIREWALL" in selected:
        specs.append({"category": "FIREWALL", "item_code": "SERVER_FIREWALL_STATUS", "item_name": "防火墙状态检查", "command": "(systemctl is-active firewalld 2>/dev/null || true); (ufw status 2>/dev/null || true); (iptables -S 2>/dev/null | head -100 || true)", "analyze": _analyze_firewall})
    if "DISK" in selected:
        specs.append({"category": "DISK", "item_code": "SERVER_DISK_USAGE", "item_name": "磁盘空间检查", "command": "df -PTh 2>/dev/null | head -100", "analyze": _analyze_disk})
    if "SERVICE_STATUS" in selected:
        specs.append({"category": "SERVICE_STATUS", "item_code": "SERVER_SERVICE_STATUS", "item_name": "基础服务状态检查", "command": "(systemctl --failed --no-pager 2>/dev/null || true); echo '---COMMON---'; (systemctl is-active nginx docker redis redis-server mysql mysqld postgresql 2>/dev/null || true)", "analyze": _analyze_service})
    if "BACKUP" in selected:
        specs.append({"category": "BACKUP", "item_code": "SERVER_BACKUP_STATUS", "item_name": "备份任务与备份文件检查", "command": "echo '---CRON---'; (crontab -l 2>/dev/null | grep -Ei 'backup|dump|tar|rsync|mysqldump' || true); echo '---BACKUPS---'; (find /data/backups /backup /var/backups -maxdepth 2 -type f -mtime -2 -printf '%p %s\\n' 2>/dev/null | head -50 || true)", "analyze": _analyze_backup})
    return specs

def _server_checkers(server_name: str, categories: Iterable[str]) -> List[CheckResult]:
    checks: List[CheckResult] = []
    for spec in _server_check_specs(server_name, categories):
        checks.append(_remote_check(server_name, spec["category"], spec["item_code"], spec["item_name"], spec["command"], spec["analyze"]))
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
        status = _server_status(srv)
        items.append({
            "id": srv.get("name"),
            "name": srv.get("name"),
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
        for key in {srv.get("id"), srv.get("name"), srv.get("host"), srv.get("ip")}:
            if key:
                by_key[str(key)] = srv
    requested_raw = [str(x or "").strip() for x in (server_ids or []) if str(x or "").strip()]
    normalized_groups = [str(g or "").strip().lower() for g in (groups or []) if str(g or "").strip()]
    group_filtered = False
    if normalized_groups:
        group_filtered = True
        servers = [s for s in servers if str(s.get("group") or "").strip().lower() in normalized_groups]
        by_key = {}
        for srv in servers:
            for key in {srv.get("id"), srv.get("name"), srv.get("host"), srv.get("ip")}:
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
        normalized_id = str(srv.get("id") or srv.get("name") or sid)
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
    from config_manager import load_config_cached
    cfg = load_config_cached()
    systems = cfg.get("systems", {}) or {}
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
    if run.started_at and run.finished_at:
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
    run.updated_at = _now()
    db.commit()
    db.refresh(run)
    return run



def _append_progress(db: Session, run: InspectionRun, result: CheckResult) -> InspectionItemResult:
    row = _save_result(db, run, result)
    # Update lightweight progress summary after every item so polling users can
    # see the execution stream while the run is still RUNNING.
    rows = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).all()
    high = sum(1 for r in rows if r.risk_level == "HIGH")
    medium = sum(1 for r in rows if r.risk_level == "MEDIUM")
    low = sum(1 for r in rows if r.risk_level == "LOW")
    normal = sum(1 for r in rows if r.risk_level in {"NONE", ""} and r.status == "PASS")
    run.high_count = high
    run.medium_count = medium
    run.low_count = low
    run.normal_count = normal
    run.score = max(0, 100 - high * RISK_WEIGHT["HIGH"] - medium * RISK_WEIGHT["MEDIUM"] - low * RISK_WEIGHT["LOW"])
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


def execute_server_inspection_run(
    db: Session,
    *,
    run_id: str,
    server_id: Optional[str] = None,
    categories: Optional[List[str]] = None,
    command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    run = db.query(InspectionRun).filter(InspectionRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Inspection run not found")
    sid = server_id or run.server_id
    cats = categories or run.categories or [c["code"] for c in SERVER_CATEGORIES]
    cmd_timeout = _clamp_int(command_timeout_seconds, DEFAULT_COMMAND_TIMEOUT_SECONDS, 5, MAX_COMMAND_TIMEOUT_SECONDS)
    run_timeout = _clamp_int(run_timeout_seconds, DEFAULT_RUN_TIMEOUT_SECONDS, 30, MAX_RUN_TIMEOUT_SECONDS)
    started_monotonic = time.monotonic()
    try:
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
                result = _remote_check(sid, spec["category"], spec["item_code"], spec["item_name"], spec["command"], spec["analyze"], timeout_seconds=cmd_timeout)
            _finish_progress_item(db, run, step, result)
        run = _finalize_run(db, run)
    except Exception as exc:
        _append_progress(db, run, CheckResult("SYSTEM", "SERVER_INSPECTION_UNHANDLED_ERROR", "服务器巡检执行异常", "ERROR", "MEDIUM", f"巡检执行异常：{exc}", "检查服务器连接、后端日志和命令兼容性。", str(exc), source_type="ERROR"))
        run.status = "FAILED"
        run.summary = f"巡检执行失败：{exc}"
        run.finished_at = _now()
        run.updated_at = _now()
        db.commit()
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
    try:
        result = run_server_inspection(db2, server_id=server_id, categories=categories, trigger_type=trigger_type, created_by=created_by, command_timeout_seconds=command_timeout_seconds, run_timeout_seconds=run_timeout_seconds)
        if generate_report:
            try:
                result["report"] = generate_report_for_run(db2, result.get("run", {}).get("id", ""), fmt="md", created_by=created_by or "system").get("report")
            except Exception as exc:
                result["report_error"] = str(exc)
        return result
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
                    errors.append({"server_id": spec.get("server_id"), "run_id": spec.get("run_id"), "error": str(exc)})
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
) -> Dict[str, Any]:
    """Run read-only server inspection for multiple servers with bounded concurrency."""
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
    run = db.query(InspectionRun).filter(InspectionRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Inspection run not found")
    pid = project_id or run.project_id
    project = _get_project_with_relations(db, pid)
    cats = categories or run.categories or [c["code"] for c in PROJECT_CATEGORIES]
    selected = set(cats)
    try:
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
        _append_progress(db, run, CheckResult("SYSTEM", "PROJECT_INSPECTION_UNHANDLED_ERROR", "项目巡检执行异常", "ERROR", "MEDIUM", f"巡检执行异常：{exc}", "检查项目配置、部署路径、服务器连接与后端日志。", str(exc), source_type="ERROR"))
        run.status = "FAILED"
        run.summary = f"项目巡检执行失败：{exc}"
        run.finished_at = _now()
        run.updated_at = _now()
        db.commit()
    return inspection_run_detail(db, run_id)


def _append_progress_counts_only(db: Session, run: InspectionRun) -> None:
    rows = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).all()
    high = sum(1 for r in rows if r.risk_level == "HIGH")
    medium = sum(1 for r in rows if r.risk_level == "MEDIUM")
    low = sum(1 for r in rows if r.risk_level == "LOW")
    normal = sum(1 for r in rows if r.risk_level in {"NONE", ""} and r.status == "PASS")
    run.high_count = high
    run.medium_count = medium
    run.low_count = low
    run.normal_count = normal
    run.score = max(0, 100 - high * RISK_WEIGHT["HIGH"] - medium * RISK_WEIGHT["MEDIUM"] - low * RISK_WEIGHT["LOW"])
    run.summary = f"巡检执行中：已完成 {len(rows)} 项，高危 {high}，中危 {medium}，低危 {low}。"
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
    return {"id": r.id, "run_id": r.run_id, "scope_type": r.scope_type, "server_id": r.server_id, "project_id": r.project_id, "category": r.category, "item_code": r.item_code, "item_name": r.item_name, "status": r.status, "risk_level": r.risk_level, "message": r.message, "suggestion": r.suggestion, "evidence_id": r.evidence_id, "raw_output": r.raw_output, "created_at": r.created_at.isoformat() if r.created_at else None}



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


def list_issues(db: Session, *, scope_type: str = "", risk_level: str = "", status: str = "", server_id: str = "", project_id: str = "", limit: int = 200) -> Dict[str, Any]:
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
    rows = q.order_by(InspectionIssue.created_at.desc()).limit(max(1, min(limit, 500))).all()
    return {"items": [_issue_to_dict(x) for x in rows], "total": len(rows)}


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



def _parse_dt(value: Any, fallback: Optional[datetime] = None) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        text_value = str(value).strip()
        try:
            if len(text_value) == 10:
                return datetime.fromisoformat(text_value + "T00:00:00")
            return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    return fallback or _now()


def _period_bounds(period: str = "daily", date_from: str = "", date_to: str = "") -> tuple[datetime, datetime, str]:
    now = _now()
    p = str(period or "daily").lower()
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
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end, "daily"


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
        for server_name in servers[:5]:
            for result in _server_checkers(server_name, server_cats):
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


def _rule_to_dict(r: InspectionRule, *, builtin: bool = False) -> Dict[str, Any]:
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
        "rule_content": getattr(r, "rule_content", None) or ((r.config_json or {}).get("content") if isinstance(r.config_json, dict) else None),
        "description": r.description,
        "suggestion": r.suggestion,
        "version": r.version,
        "builtin": builtin,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _rule_dict_from_payload(payload: Dict[str, Any], *, fallback_code: str = "") -> Dict[str, Any]:
    code = str(payload.get("rule_code") or fallback_code or "").strip().upper().replace(" ", "_")
    if not code:
        name = str(payload.get("rule_name") or "CUSTOM").strip().upper().replace(" ", "_")
        code = f"CUSTOM_{name[:32] or uuid4().hex[:8]}"
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
        "config_json": payload.get("config") if isinstance(payload.get("config"), dict) else (payload.get("config_json") if isinstance(payload.get("config_json"), dict) else {}),
        "version": str(payload.get("version") or SCHEMA_VERSION),
    }


def _all_builtin_rules() -> List[Dict[str, Any]]:
    builtin: List[Dict[str, Any]] = []
    for cat in SERVER_CATEGORIES:
        code = f"SERVER_{cat['code']}"
        command = SERVER_RULE_COMMANDS.get(cat["code"], cat["description"])
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
            "config": {"commands": [line.strip() for line in command.splitlines() if line.strip() and not line.strip().startswith("#")]},
            "version": SCHEMA_VERSION,
            "builtin": True,
        })
    for cat in PROJECT_CATEGORIES:
        code = f"PROJECT_{cat['code']}"
        command = PROJECT_RULE_COMMANDS.get(cat["code"], cat["description"])
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
            "config": {"commands": [line.strip() for line in command.splitlines() if line.strip() and not line.strip().startswith("#")]},
            "version": SCHEMA_VERSION,
            "builtin": True,
        })
    return builtin

def list_rules(db: Session, *, scope_type: str = "", category: str = "", enabled: Optional[bool] = None, include_deleted: bool = False) -> Dict[str, Any]:
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

    items.sort(key=lambda x: (str(x.get("scope_type") or ""), str(x.get("category") or ""), str(x.get("rule_code") or "")))
    return {"items": items, "total": len(items)}


def _builtin_rule_by_code(rule_code: str) -> Optional[Dict[str, Any]]:
    code = str(rule_code or "").strip().upper()
    for item in _all_builtin_rules():
        if item["rule_code"] == code:
            return {k: v for k, v in item.items() if k not in {"builtin"}}
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
        return {"id": code, "deleted": False, "config": {}, "builtin": True, **builtin}
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
    if "version" in payload and payload["version"] is not None:
        row.version = str(payload["version"])
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return _rule_to_dict(row, builtin=bool(_builtin_rule_by_code(row.rule_code)))


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

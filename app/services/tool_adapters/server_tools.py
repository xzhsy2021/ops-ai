from __future__ import annotations

import shlex
from fastapi import HTTPException

from config_manager import get_server_by_name
from app.domain.inventory.services import InventoryReadService
from app.services.tool_registry import registry

_MAX_LINES = 300
_inventory = InventoryReadService()


def get_service_config(params: dict, ctx, db):
    system = params.get("system", "")
    service = params.get("service", "")
    try:
        config = _inventory.get_service(system, service) or {}
    except Exception:
        config = {}
    if not config:
        return {"found": False, "system": system, "service": service}
    config = dict(config)
    config["found"] = True
    return config


def _connect(server_name: str):
    srv = get_server_by_name(server_name)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server not found: {server_name}")
    from ssh_client import create_ssh_client
    ssh = create_ssh_client(srv)
    ssh.connect()
    return ssh, srv


def _service_dir(system: str, service: str, ctx, db) -> str:
    cfg = get_service_config({"system": system, "service": service}, ctx, db)
    if not cfg.get("found"):
        raise HTTPException(status_code=404, detail="Service config not found")
    path = cfg.get("service_dir") or cfg.get("deploy_path") or ""
    if not path:
        raise HTTPException(status_code=400, detail="Service directory is not configured")
    return path.rstrip("/")


def _safe_child_path(base: str, path: str) -> str:
    path = (path or "").strip()
    if not path or path == ".":
        return base
    if path.startswith("/"):
        if not (path == base or path.startswith(base.rstrip("/") + "/")):
            raise HTTPException(status_code=400, detail="Path must stay under service_dir/deploy_path")
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


@registry.register(
    name="ops.check_disk",
    description="查看服务器磁盘使用情况，只执行安全只读 df 命令。",
    scopes=["ops:read", "server:read"],
    risk="medium",
    category="server_read",
    input_schema={
        "type": "object",
        "properties": {"server": {"type": "string"}, "path": {"type": "string"}},
        "required": ["server"],
        "additionalProperties": False,
    },
)
def check_disk(args, ctx, db):
    ssh, _ = _connect(args.get("server"))
    try:
        path = args.get("path") or "/"
        code, out, err = ssh.exec(f"df -h {shlex.quote(path)}", timeout=15)
        return {"server": args.get("server"), "path": path, "exit_code": code, "stdout": out, "stderr": err}
    finally:
        ssh.close()


@registry.register(
    name="ops.check_process",
    description="按服务配置中的进程关键字查看进程。",
    scopes=["ops:read", "server:read"],
    risk="medium",
    category="server_read",
    input_schema={
        "type": "object",
        "properties": {"server": {"type": "string"}, "system": {"type": "string"}, "service": {"type": "string"}},
        "required": ["server", "system", "service"],
        "additionalProperties": False,
    },
)
def check_process(args, ctx, db):
    cfg = get_service_config({"system": args.get("system"), "service": args.get("service")}, ctx, db)
    tv = cfg.get("template_variables") or {}
    keyword = tv.get("process_keyword") or tv.get("pm2_name") or tv.get("service_name") or args.get("service")
    ssh, _ = _connect(args.get("server"))
    try:
        cmd = f"ps -ef | grep -v grep | grep -E {shlex.quote(str(keyword))} | head -20"
        code, out, err = ssh.exec(cmd, timeout=15)
        return {"server": args.get("server"), "keyword": keyword, "exit_code": code, "stdout": out, "stderr": err}
    finally:
        ssh.close()


@registry.register(
    name="ops.list_service_directory",
    description="列出服务目录下文件；路径必须在服务目录下。",
    scopes=["ops:read", "server:read"],
    risk="medium",
    category="server_read",
    input_schema={
        "type": "object",
        "properties": {"server": {"type": "string"}, "system": {"type": "string"}, "service": {"type": "string"}, "path": {"type": "string"}},
        "required": ["server", "system", "service"],
        "additionalProperties": False,
    },
)
def list_service_directory(args, ctx, db):
    base = _service_dir(args.get("system"), args.get("service"), ctx, db)
    path = _safe_child_path(base, args.get("path") or ".")
    ssh, _ = _connect(args.get("server"))
    try:
        code, out, err = ssh.exec(f"ls -lah {shlex.quote(path)} | sed -n '1,120p'", timeout=15)
        return {"server": args.get("server"), "path": path, "exit_code": code, "stdout": out, "stderr": err}
    finally:
        ssh.close()


@registry.register(
    name="ops.tail_service_log",
    description="查看服务日志；只允许服务目录下日志路径，最多 300 行。",
    scopes=["ops:read", "server:read"],
    risk="medium",
    category="server_read",
    input_schema={
        "type": "object",
        "properties": {"server": {"type": "string"}, "system": {"type": "string"}, "service": {"type": "string"}, "log_path": {"type": "string"}, "lines": {"type": "integer"}},
        "required": ["server", "system", "service"],
        "additionalProperties": False,
    },
)
def tail_service_log(args, ctx, db):
    cfg = get_service_config({"system": args.get("system"), "service": args.get("service")}, ctx, db)
    tv = cfg.get("template_variables") or {}
    base = _service_dir(args.get("system"), args.get("service"), ctx, db)
    log_path = args.get("log_path") or tv.get("log_path") or tv.get("log_dir") or "logs"
    path = _safe_child_path(base, log_path)
    lines = min(max(int(args.get("lines") or 100), 1), _MAX_LINES)
    ssh, _ = _connect(args.get("server"))
    try:
        code, out, err = ssh.exec(f"if [ -d {shlex.quote(path)} ]; then ls -t {shlex.quote(path)}/* 2>/dev/null | head -1 | xargs -r tail -n {lines}; else tail -n {lines} {shlex.quote(path)}; fi", timeout=20)
        return {"server": args.get("server"), "path": path, "lines": lines, "exit_code": code, "stdout": out, "stderr": err}
    finally:
        ssh.close()


@registry.register(
    name="ops.run_health_check",
    description="执行服务健康检查。优先使用 health_check_command；未配置时检查进程关键字。",
    scopes=["ops:read", "server:read"],
    risk="medium",
    category="server_read",
    input_schema={
        "type": "object",
        "properties": {"server": {"type": "string"}, "system": {"type": "string"}, "service": {"type": "string"}},
        "required": ["server", "system", "service"],
        "additionalProperties": False,
    },
)
def run_health_check(args, ctx, db):
    cfg = get_service_config({"system": args.get("system"), "service": args.get("service")}, ctx, db)
    tv = cfg.get("template_variables") or {}
    base = _service_dir(args.get("system"), args.get("service"), ctx, db)
    command = tv.get("health_check_command") or tv.get("check_command") or ""
    if command:
        # Force command execution under the configured service directory. The
        # command itself must come from OPS service config, never raw model input.
        command = f"cd {shlex.quote(base)} && ({command})"
    else:
        keyword = tv.get("process_keyword") or tv.get("pm2_name") or tv.get("service_name") or args.get("service")
        command = f"ps -ef | grep -v grep | grep -E {shlex.quote(str(keyword))} | head -20"
    ssh, _ = _connect(args.get("server"))
    try:
        code, out, err = ssh.exec(command, timeout=30)
        return {"server": args.get("server"), "command_source": "service_config" if (tv.get("health_check_command") or tv.get("check_command")) else "process_keyword", "exit_code": code, "healthy": code == 0 and bool((out or "").strip()), "stdout": out, "stderr": err}
    finally:
        ssh.close()

from __future__ import annotations

import shlex
from fastapi import HTTPException

from config_manager import get_server_by_name, resolve_server
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


def _resolve_server_or_404(server_key: str):
    """Resolve a server by name / host / full UUID / short UUID prefix.

    Returns the decrypted server dict. Raises 404 with a helpful hint
    when nothing matches.
    """
    if not server_key:
        raise HTTPException(status_code=404, detail="Server not found: <empty>")
    srv = resolve_server(server_key)
    if not srv:
        # Friendly hint: surface a couple of nearby names so callers can
        # recover from typos without spelunking through the inventory.
        hint = ""
        try:
            from config_manager import get_all_servers
            all_servers = get_all_servers() or []
            sample = [s.get("name") for s in all_servers[:5] if s.get("name")]
            if sample:
                hint = f" Sample names: {', '.join(sample)}."
        except Exception:
            pass
        raise HTTPException(
            status_code=404,
            detail=f"Server not found: {server_key}. "
                   f"Pass a server name, host, full UUID, or short UUID prefix.{hint}",
        )
    return srv


def _connect(server_key: str):
    """Connect to a server, accepting name / host / full UUID / short UUID prefix.

    Backward compatible: the previous signature accepted only a server name
    via ``get_server_by_name``. We now try multiple identifiers so that Path
    B (single-shot probes) stays usable as a fallback when callers pass the
    UUID returned by ``/api/v2/servers``.
    """
    srv = _resolve_server_or_404(server_key)
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
    data_sensitivity="sensitive",
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
    data_sensitivity="sensitive",
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
        # BUG #6 修复：ps | grep | head 管道最终 exit_code 由 head 决定，
        # 即使没有匹配项也是 0。空 stdout + exit 0 必须归一为"未找到"。
        found = bool((out or "").strip())
        return {
            "server": args.get("server"),
            "keyword": keyword,
            "exit_code": 0 if found else 1,
            "found": found,
            "process_count": len([ln for ln in (out or "").splitlines() if ln.strip()]),
            "stdout": out,
            "stderr": err,
        }
    finally:
        ssh.close()


@registry.register(
    name="ops.list_service_directory",
    description="列出服务目录下文件；路径必须在服务目录下。",
    scopes=["ops:read", "server:read"],
    risk="medium",
    category="server_read",
    data_sensitivity="sensitive",
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
    data_sensitivity="sensitive",
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
    risk="high",
    category="server_read",
    data_sensitivity="sensitive",
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


def _filter_items(items, keyword: str = "", limit: int = 100):
    keyword = str(keyword or "").strip().lower()
    result = []
    for item in items or []:
        if keyword:
            blob = " ".join(str(item.get(k, "")) for k in ("name", "host", "group", "system_name", "display_name", "id")).lower()
            if keyword not in blob:
                continue
        result.append(item)
    try:
        limit = max(1, min(int(limit or 100), 500))
    except Exception:
        limit = 100
    return result[:limit]


@registry.register(
    name="ops.list_servers",
    title="查询服务器资产",
    description="列出数据库中的服务器。支持按 group / env 分组筛选与 keyword 关键字过滤。group 字段可与 'ops.list_server_groups' 工具配合使用，先列出可选分组，再按 group 名称发起巡检。",
    scopes=["ops:read", "server:read"],
    risk="low",
    category="server_read",
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "group": {"type": "string", "description": "按服务器分组精确筛选（区分大小写不敏感）。例如 'crypto'。"},
            "limit": {"type": "integer", "default": 100},
        },
        "additionalProperties": False,
    },
)
def list_servers_tool(args, ctx, db):
    from app.maintenance.server_assets import list_server_assets
    items = list_server_assets(db)
    group = str(args.get("group") or "").strip().lower()
    if group:
        items = [x for x in items if str(x.get("group") or "").strip().lower() == group]
    items = _filter_items(items, args.get("keyword") or "", args.get("limit") or 100)
    return {"items": items, "total": len(items), "summary": f"查询到 {len(items)} 台服务器"}


@registry.register(
    name="ops.list_server_groups",
    title="查询服务器分组",
    description="列出所有可用的服务器分组（group / env），以及每组的服务器总数、可巡检数、在线数。AI 巡检前应先调用本工具取得分组清单，再通过 group 名称筛选服务器或发起分组巡检。",
    scopes=["ops:read", "server:read"],
    risk="low",
    category="server_read",
    ai_callable=True,
    ai_auto_callable=True,
    data_sensitivity="internal",
    related_tools=["ops.list_servers", "ops.inspection.run_servers_batch"],
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def list_server_groups_tool(args, ctx, db):
    from app.services.inspection_center import list_server_groups
    groups = list_server_groups()
    return {"items": groups, "total": len(groups), "summary": f"共 {len(groups)} 个服务器分组"}


@registry.register(
    name="ops.list_systems",
    title="查询系统/项目配置",
    description="列出配置中心中的系统/项目清单。",
    scopes=["ops:read"],
    risk="low",
    category="app",
    ai_callable=True,
    ai_auto_callable=True,
    input_schema={"type": "object", "properties": {"keyword": {"type": "string"}, "limit": {"type": "integer", "default": 100}}, "additionalProperties": False},
)
def list_systems_tool(args, ctx, db):
    systems = _inventory.list_systems() or {}
    items = []
    for name, cfg in systems.items():
        cfg = cfg or {}
        items.append({
            "name": name,
            "display_name": cfg.get("display_name") or name,
            "servers": cfg.get("servers") or [],
            "service_count": len(cfg.get("services") or []),
            "has_environments": bool(cfg.get("environments")),
        })
    items = _filter_items(items, args.get("keyword") or "", args.get("limit") or 100)
    return {"items": items, "total": len(items), "summary": f"查询到 {len(items)} 个系统/项目"}


@registry.register(
    name="ops.list_services",
    title="查询服务配置",
    description="列出数据库中的系统/项目服务配置。",
    scopes=["ops:read"],
    risk="low",
    category="app",
    ai_callable=True,
    ai_auto_callable=True,
    input_schema={
        "type": "object",
        "properties": {"system": {"type": "string"}, "keyword": {"type": "string"}, "limit": {"type": "integer", "default": 100}},
        "additionalProperties": False,
    },
)
def list_services_tool(args, ctx, db):
    system = str(args.get("system") or "").strip()
    items = []
    from app.db.models import Service
    query = db.query(Service)
    if system:
        query = query.filter(Service.system_name == system)
    for row in query.order_by(Service.system_name, Service.name).all():
        items.append({
            "system_name": row.system_name,
            "name": row.name,
            "display_name": row.display_name or row.name,
            "template": row.template or "",
            "servers": row.servers or [],
            "template_variables": row.template_variables or {},
            "source": "database",
        })
    items = _filter_items(items, args.get("keyword") or "", args.get("limit") or 100)
    return {"items": items, "total": len(items), "summary": f"查询到 {len(items)} 个服务"}


@registry.register(
    name="ops.list_environments",
    title="查询环境配置",
    description="列出系统/项目发布环境。",
    scopes=["ops:read"],
    risk="low",
    category="app",
    ai_callable=True,
    ai_auto_callable=True,
    input_schema={"type": "object", "properties": {"system": {"type": "string"}, "limit": {"type": "integer", "default": 100}}, "additionalProperties": False},
)
def list_environments_tool(args, ctx, db):
    system = str(args.get("system") or "").strip()
    items = []
    seen = set()
    from app.db.models import Environment, SystemEnvironment
    query = db.query(SystemEnvironment)
    if system:
        query = query.filter(SystemEnvironment.system_name == system)
    for row in query.order_by(SystemEnvironment.system_name, SystemEnvironment.name).all():
        seen.add((row.system_name, row.name))
        items.append({
            "system_name": row.system_name,
            "name": row.name,
            "display_name": row.display_name or row.name,
            "variables": row.variables or {},
            "source": "database",
        })
    for row in db.query(Environment).order_by(Environment.name).all():
        items.append({
            "name": row.name,
            "display_name": (row.variables or {}).get("display_name") or row.name,
            "variables": row.variables or {},
            "source": "database",
        })
    try:
        limit = max(1, min(int(args.get("limit") or 100), 500))
    except Exception:
        limit = 100
    return {"items": items[:limit], "total": len(items[:limit]), "summary": f"查询到 {len(items[:limit])} 个环境"}


# ─── Server CRUD ────────────────────────────────────────────────────────────


@registry.register(
    name="ops.get_server",
    description="获取单台服务器详情。Use when user asks about a specific server's configuration. 中文: 查看服务器详情/服务器配置.",
    scopes=["ops:read"],
    risk="low",
    category="server_read",
    write=False,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "服务器名称"},
        },
        "required": ["name"],
    },
)
def get_server_tool(args, ctx, db):
    from config_manager import get_server_by_name
    name = args.get("name", "")
    srv = get_server_by_name(name)
    if not srv:
        return {"found": False, "name": name}
    return {
        "found": True,
        "name": name,
        "host": srv.get("host", ""),
        "port": srv.get("port", 22),
        "user": srv.get("user", ""),
        "jump_host": srv.get("jump_host", ""),
        "status": srv.get("status", "unknown"),
    }


# ─── System CRUD ────────────────────────────────────────────────────────────


# ─── Service CRUD ───────────────────────────────────────────────────────────


# ─── Environment CRUD ───────────────────────────────────────────────────────


# ─── Server Group Write Operations ──────────────────────────────────────────


# ─── Service Control (restart / stop / start / update) ──────────────────────

def _env_prefix(env: dict | None = None) -> str:
    """将环境变量 dict 渲染为命令前缀，如 'SYSTEM_TRACE=true OTHER=1 '。"""
    if not env:
        return ""
    return " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items()) + " "


def _resolve_service_control_command(
    cfg: dict,
    action: str,
    compose_service: str = "",
    env: dict | None = None,
    compose_args: list[str] | None = None,
) -> str:
    """根据服务配置解析控制命令。

    优先级：
    1. 配置中显式指定的 restart_command / stop_command / start_command / update_command
    2. Docker Compose 服务：
       - restart: docker compose -f <file> restart [service]
       - stop:    docker compose -f <file> stop [service]
       - start:   docker compose -f <file> up -d [service] [compose_args]
       - update:  docker compose -f <file> pull [service] && docker compose -f <file> up -d --no-deps [service] [compose_args]
    3. PM2 服务：pm2 restart/stop/start/reload <pm2_name>
    4. process_keyword：pkill / 启动命令（不支持 update）

    `env`（dict[str,str]）渲染为整条命令的环境变量前缀（如 SYSTEM_TRACE=true），
    作用于该 shell 内所有的 docker compose 子命令；`compose_args`（list[str]）
    透传到 `up` / `restart` 子命令（如 --force-recreate）。两者都只影响命令
    生成，不允许直接注入任意命令行。
    """
    tv = cfg.get("template_variables") or {}
    cmd_key = f"{action}_command"
    explicit = tv.get(cmd_key)
    if explicit:
        return _env_prefix(env) + str(explicit)

    template = cfg.get("template") or ""
    compose_dir = tv.get("compose_dir") or tv.get("deploy_path") or tv.get("service_dir") or ""

    # compose_service 优先从参数取，否则从 template_variables 取
    svc_target = compose_service or tv.get("compose_service") or ""
    svc_arg = shlex.quote(svc_target) if svc_target else ""

    extra = (" " + " ".join(compose_args)) if compose_args else ""

    if template == "generic_frontend":
        if action == "update":
            deploy_path = tv.get("deploy_path") or tv.get("service_dir") or "."
            update_script = tv.get("update_script") or "./www.sh"
            script_prefix = _env_prefix(env)
            return f"cd {shlex.quote(str(deploy_path))} && {script_prefix}{update_script}".strip()

    if template in ("docker_compose", "crypto_docker_compose") or compose_dir:
        compose_file = tv.get("compose_file", "docker-compose.yml")
        prefix = _env_prefix(env)
        if action == "restart":
            return f"{prefix}docker compose -f {shlex.quote(compose_file)} restart {svc_arg}{extra}".strip()
        elif action == "stop":
            return f"{prefix}docker compose -f {shlex.quote(compose_file)} stop {svc_arg}".strip()
        elif action == "start":
            return f"{prefix}docker compose -f {shlex.quote(compose_file)} up -d {svc_arg}{extra}".strip()
        elif action == "update":
            # 拉取最新镜像并重建容器（--no-deps 避免影响依赖服务）
            pull = f"{prefix}docker compose -f {shlex.quote(compose_file)} pull {svc_arg}".strip()
            up = f"{prefix}docker compose -f {shlex.quote(compose_file)} up -d --no-deps {svc_arg}{extra}".strip()
            return pull + " && " + up

    pm2_name = tv.get("pm2_name") or ""
    if pm2_name:
        if action == "restart":
            return f"pm2 restart {shlex.quote(pm2_name)}"
        elif action == "stop":
            return f"pm2 stop {shlex.quote(pm2_name)}"
        elif action == "start":
            return f"pm2 start {shlex.quote(pm2_name)}"
        elif action == "update":
            # PM2 的 reload 实现零停机更新（需配合 cluster mode）
            return f"pm2 reload {shlex.quote(pm2_name)}"

    keyword = tv.get("process_keyword") or tv.get("service_name") or ""
    if keyword:
        if action == "restart":
            return f"pkill -f {shlex.quote(keyword)} && sleep 2 && cd {shlex.quote(compose_dir or '.')} && nohup {shlex.quote(keyword)} > /dev/null 2>&1 &"
        elif action == "stop":
            return f"pkill -f {shlex.quote(keyword)}"
        elif action == "start":
            return f"cd {shlex.quote(compose_dir or '.')} && nohup {shlex.quote(keyword)} > /dev/null 2>&1 &"
        elif action == "update":
            # process_keyword 部署方式不支持镜像更新，退化为 restart
            return f"pkill -f {shlex.quote(keyword)} && sleep 2 && cd {shlex.quote(compose_dir or '.')} && nohup {shlex.quote(keyword)} > /dev/null 2>&1 &"

    raise HTTPException(status_code=400, detail=f"无法确定 {action} 命令：请配置 {action}_command 或 compose_dir/pm2_name/process_keyword")


def _check_remote_dir_exists(ssh, base_dir: str) -> tuple:
    """执行控制命令前校验目标目录存在于远端服务器。

    与 full_cmd 拼装条件保持一致：base_dir 为空时跳过校验。
    返回 (ok, error_msg)；ok=False 时 error_msg 含路径上下文，供调用方决定抛异常或返回错误。
    """
    if not base_dir:
        return True, ""
    try:
        code, _, _ = ssh.exec(f"test -d {shlex.quote(base_dir)}", timeout=10)
    except Exception as exc:
        return False, f"校验 compose_dir 失败: {base_dir} ({exc})"
    if code != 0:
        return False, f"目标服务器上 compose_dir 不存在: {base_dir}"
    return True, ""


def _check_docker_status(ssh, base_dir: str, compose_file: str, compose_svc: str) -> dict:
    """通过 docker compose ps 检查容器状态，检测重启循环、架构错误等异常。

    返回 {'restart_loop': bool, 'status': str, 'containers': [...], 'restart_count': int}
    """
    result = {"restart_loop": False, "status": "unknown", "containers": [], "restart_count": 0}
    if not base_dir:
        return result

    try:
        svc_filter = f" {shlex.quote(compose_svc)}" if compose_svc else ""
        ps_cmd = f"cd {shlex.quote(base_dir)} && docker compose -f {shlex.quote(compose_file)} ps{svc_filter} 2>&1"
        code, out, err = ssh.exec(ps_cmd, timeout=15)
        if code != 0:
            result["status"] = f"ps_failed: {err[:200]}"
            return result

        result["ps_raw"] = out[:2000]
        lines = out.strip().split("\n")

        # 解析 docker compose ps 输出，检测状态关键字
        restarting_count = 0
        unhealthy_count = 0
        exited_count = 0
        running_count = 0
        for line in lines:
            lower = line.lower()
            if "restarting" in lower:
                restarting_count += 1
                result["containers"].append({"line": line.strip(), "status": "restarting"})
            elif "unhealthy" in lower:
                unhealthy_count += 1
                result["containers"].append({"line": line.strip(), "status": "unhealthy"})
            elif "exited" in lower or "dead" in lower:
                exited_count += 1
                result["containers"].append({"line": line.strip(), "status": "exited"})
            elif "up " in lower or "running" in lower:
                running_count += 1

        result["restart_count"] = restarting_count
        result["unhealthy_count"] = unhealthy_count
        result["exited_count"] = exited_count
        result["running_count"] = running_count

        if restarting_count > 0:
            result["restart_loop"] = True
            result["status"] = "RESTARTING"
        elif unhealthy_count > 0:
            result["status"] = "UNHEALTHY"
        elif exited_count > 0 and running_count == 0:
            result["status"] = "EXITED"
        elif running_count > 0:
            result["status"] = "RUNNING"
        else:
            result["status"] = "NO_CONTAINERS"

    except Exception as e:
        result["status"] = f"check_error: {e}"
    return result


def _get_container_logs(ssh, base_dir: str, compose_file: str, compose_svc: str) -> str:
    """获取容器最近日志，用于诊断重启原因。"""
    if not base_dir:
        return ""
    try:
        svc_filter = f" {shlex.quote(compose_svc)}" if compose_svc else ""
        logs_cmd = f"cd {shlex.quote(base_dir)} && docker compose -f {shlex.quote(compose_file)} logs --tail 30{svc_filter} 2>&1"
        code, out, err = ssh.exec(logs_cmd, timeout=15)
        if code != 0 and err:
            return f"logs_failed: {err[:200]}"
        return out[:3000] if out else (err[:2000] if err else "")
    except Exception as e:
        return f"logs_error: {e}"


def _execute_service_control(server_key: str, system: str, service: str, action: str, ctx, db, compose_service: str = "", env: dict | None = None, compose_args: list[str] | None = None) -> dict:
    """在指定服务器上执行服务控制操作（restart/stop/start/update）。

    返回执行结果，包含前后健康检查对比。
    Docker Compose 部署会在操作后通过 docker compose ps 验证容器状态，
    检测重启循环、架构不匹配等异常，并自动获取容器日志。
    env / compose_args 可选传递给命令生成器（trace 启动等）。
    """
    cfg = get_service_config({"system": system, "service": service}, ctx, db)
    if not cfg.get("found"):
        raise HTTPException(status_code=404, detail=f"服务配置未找到: {system}/{service}")

    command = _resolve_service_control_command(cfg, action, compose_service=compose_service, env=env, compose_args=compose_args)
    tv = cfg.get("template_variables") or {}
    base_dir = tv.get("compose_dir") or tv.get("deploy_path") or tv.get("service_dir") or ""
    template = cfg.get("template") or ""
    is_docker = template in ("docker_compose", "crypto_docker_compose") or bool(tv.get("compose_dir"))
    compose_file = tv.get("compose_file", "docker-compose.yml")
    compose_svc = compose_service or tv.get("compose_service") or ""

    ssh, srv = _connect(server_key)
    try:
        # 执行前健康检查
        pre_health = {}
        try:
            hc = run_health_check({"server": server_key, "system": system, "service": service}, ctx, db)
            pre_health = {"healthy": hc.get("healthy"), "stdout": hc.get("stdout", ""), "exit_code": hc.get("exit_code")}
        except Exception:
            pre_health = {"note": "pre-check skipped"}

        # 部署前路径校验：compose_dir 不存在时提前失败，避免 cd 脏错误混入 stdout
        ok, err_msg = _check_remote_dir_exists(ssh, base_dir)
        if not ok:
            raise HTTPException(status_code=400, detail=f"[{server_key}] {err_msg}")

        # 执行控制命令（update 操作需要更长超时，因为要拉取镜像）
        full_cmd = f"cd {shlex.quote(base_dir)} && {command}" if base_dir else command
        timeout = 300 if action == "update" else 120
        code, out, err = ssh.exec(full_cmd, timeout=timeout)

        # 执行后健康检查（重启/启动/更新后等待服务稳定）
        import time
        post_health = {}
        container_status = None
        if action in ("restart", "start", "update"):
            # 等待更长时间让服务稳定：update 需要拉镜像和重建容器，耗时更长
            wait_sec = 15 if action == "update" else 8
            time.sleep(wait_sec)

            # Docker Compose 部署：通过 docker compose ps 验证容器状态，
            # 检测重启循环、架构错误、OOM 等问题
            if is_docker:
                container_status = _check_docker_status(ssh, base_dir, compose_file, compose_svc)

            # 检测到重启循环时，自动获取容器日志辅助诊断
            if container_status and container_status.get("restart_loop"):
                container_status["logs_tail"] = _get_container_logs(
                    ssh, base_dir, compose_file, compose_svc
                )

            try:
                hc = run_health_check({"server": server_key, "system": system, "service": service}, ctx, db)
                post_health = {"healthy": hc.get("healthy"), "stdout": hc.get("stdout", ""), "exit_code": hc.get("exit_code")}
            except Exception:
                post_health = {"note": "post-check skipped"}

        return {
            "server": server_key,
            "system": system,
            "service": service,
            "action": action,
            "command": full_cmd,
            "exit_code": code,
            "stdout": out,
            "stderr": err,
            "pre_health": pre_health,
            "post_health": post_health,
            "container_status": container_status,
            "success": code == 0 and (not container_status or not container_status.get("restart_loop")),
        }
    finally:
        ssh.close()


@registry.register(
    name="ops.restart_service",
    title="重启服务",
    description="在指定服务器上重启服务。支持 Docker Compose / PM2 / process_keyword。高危操作，需要人工审批。中文: 重启服务/重启应用.",
    scopes=["ops:read", "server:read"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名称"},
            "system": {"type": "string", "description": "系统名称"},
            "service": {"type": "string", "description": "服务名称"},
            "confirm_text": {"type": "string", "description": "确认短语: RESTART <server>/<service>"},
        },
        "required": ["server", "system", "service", "confirm_text"],
        "additionalProperties": False,
    },
)
def restart_service(args, ctx, db):
    server = args.get("server", "")
    system = args.get("system", "")
    service = args.get("service", "")
    confirm = args.get("confirm_text", "")
    expected = f"RESTART {server}/{service}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    result = _execute_service_control(server, system, service, "restart", ctx, db)
    result["ok"] = result.get("success", False)
    return result


@registry.register(
    name="ops.stop_service",
    title="停止服务",
    description="在指定服务器上停止服务。支持 Docker Compose / PM2 / process_keyword。高危操作，需要人工审批。中文: 停止服务/关闭应用.",
    scopes=["ops:read", "server:read"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名称"},
            "system": {"type": "string", "description": "系统名称"},
            "service": {"type": "string", "description": "服务名称"},
            "confirm_text": {"type": "string", "description": "确认短语: STOP <server>/<service>"},
        },
        "required": ["server", "system", "service", "confirm_text"],
        "additionalProperties": False,
    },
)
def stop_service(args, ctx, db):
    server = args.get("server", "")
    system = args.get("system", "")
    service = args.get("service", "")
    confirm = args.get("confirm_text", "")
    expected = f"STOP {server}/{service}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    result = _execute_service_control(server, system, service, "stop", ctx, db)
    result["ok"] = result.get("success", False)
    return result


@registry.register(
    name="ops.start_service",
    title="启动服务",
    description="在指定服务器上启动服务。支持 Docker Compose / PM2 / process_keyword。高危操作，需要人工审批。中文: 启动服务/开启应用.",
    scopes=["ops:read", "server:read"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名称"},
            "system": {"type": "string", "description": "系统名称"},
            "service": {"type": "string", "description": "服务名称"},
            "confirm_text": {"type": "string", "description": "确认短语: START <server>/<service>"},
        },
        "required": ["server", "system", "service", "confirm_text"],
        "additionalProperties": False,
    },
)
def start_service(args, ctx, db):
    server = args.get("server", "")
    system = args.get("system", "")
    service = args.get("service", "")
    confirm = args.get("confirm_text", "")
    expected = f"START {server}/{service}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    result = _execute_service_control(server, system, service, "start", ctx, db)
    result["ok"] = result.get("success", False)
    return result


@registry.register(
    name="ops.update_service_runtime",
    title="拉取镜像并重新部署容器（docker pull + docker compose up）",
    description=(
        "拉取最新 Docker 镜像并重新部署容器：执行 docker pull + docker compose up -d 重建服务容器。"
        "用于更新服务到新版本、滚动更新、容器重启部署。"
        "Docker Compose: docker compose pull [service] && docker compose up -d --no-deps [service]；"
        "PM2: pm2 reload <name>。高危操作，需要人工审批。"
        "keywords: docker pull, 拉镜像, 重新部署, 重新部署容器, 重启容器, 滚动更新, container restart, redeploy, update image."
    ),
    scopes=["ops:read", "server:read"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    data_sensitivity="sensitive",
    keywords=[
        "docker pull", "拉镜像", "拉取镜像", "重新部署", "重新部署容器",
        "重启容器", "滚动更新", "container restart", "redeploy", "update image",
        "docker compose up", "重建容器", "更新服务",
    ],
    aliases=["docker_pull", "redeploy_container", "pull_and_restart"],
    input_schema={
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "服务器名称"},
            "system": {"type": "string", "description": "系统名称"},
            "service": {"type": "string", "description": "服务名称"},
            "compose_service": {
                "type": "string",
                "description": "Docker Compose 服务名（可选）。指定时只拉取并更新该服务，不指定则更新所有服务。",
            },
            "confirm_text": {"type": "string", "description": "确认短语: UPDATE <server>/<service>"},
        },
        "required": ["server", "system", "service", "confirm_text"],
        "additionalProperties": False,
    },
)
def update_service_runtime(args, ctx, db):
    server = args.get("server", "")
    system = args.get("system", "")
    service = args.get("service", "")
    confirm = args.get("confirm_text", "")
    compose_service = args.get("compose_service", "")
    expected = f"UPDATE {server}/{service}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    result = _execute_service_control(
        server, system, service, "update", ctx, db, compose_service=compose_service
    )
    result["ok"] = result.get("success", False)
    return result

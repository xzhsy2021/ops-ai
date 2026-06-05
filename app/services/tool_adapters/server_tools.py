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
        return {"server": args.get("server"), "keyword": keyword, "exit_code": code, "stdout": out, "stderr": err}
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
    description="列出已配置服务器（兼容数据库 servers 表和配置中心 config_kv）。支持按 group / env 分组筛选与 keyword 关键字过滤。group 字段可与 'ops.list_server_groups' 工具配合使用，先列出可选分组，再按 group 名称发起巡检。",
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
    description="列出系统/项目下的服务配置，兼容数据库 services 表和配置中心 config_kv。",
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
    seen = set()
    systems = _inventory.list_systems() or {}
    for sys_name, cfg in systems.items():
        if system and sys_name != system:
            continue
        for svc in (cfg or {}).get("services") or []:
            if not isinstance(svc, dict):
                continue
            name = svc.get("name") or svc.get("display_name")
            if not name:
                continue
            key = (sys_name, name)
            seen.add(key)
            items.append({
                "system_name": sys_name,
                "name": name,
                "display_name": svc.get("display_name") or name,
                "template": svc.get("template") or "",
                "servers": svc.get("servers") or (cfg or {}).get("servers") or [],
                "template_variables": svc.get("template_variables") or {},
                "source": "config_kv",
            })
    try:
        from app.db.models import Service
        query = db.query(Service)
        if system:
            query = query.filter(Service.system_name == system)
        for row in query.all():
            key = (row.system_name, row.name)
            if key in seen:
                continue
            items.append({
                "system_name": row.system_name,
                "name": row.name,
                "display_name": row.display_name or row.name,
                "template": row.template or "",
                "servers": row.servers or [],
                "template_variables": row.template_variables or {},
                "source": "database",
            })
    except Exception:
        pass
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
    systems = _inventory.list_systems() or {}
    for sys_name, cfg in systems.items():
        if system and sys_name != system:
            continue
        for env_name, env_cfg in ((cfg or {}).get("environments") or {}).items():
            seen.add(env_name)
            items.append({"system_name": sys_name, "name": env_name, "display_name": (env_cfg or {}).get("display_name") or env_name, "variables": (env_cfg or {}).get("variables") or {}, "source": "config_kv"})
    try:
        from app.db.models import Environment
        for row in db.query(Environment).all():
            if row.name in seen:
                continue
            items.append({"name": row.name, "display_name": (row.variables or {}).get("display_name") or row.name, "variables": row.variables or {}, "source": "database"})
    except Exception:
        pass
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


@registry.register(
    name="ops.create_server",
    description="创建服务器配置。High risk; requires human approval. 中文: 创建服务器/新增服务器.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "服务器名称（唯一标识）"},
            "host": {"type": "string", "description": "服务器地址"},
            "port": {"type": "integer", "description": "SSH端口", "default": 22},
            "user": {"type": "string", "description": "SSH用户名"},
            "auth_type": {"type": "string", "description": "认证方式: key/password", "enum": ["key", "password"]},
            "key": {"type": "string", "description": "SSH密钥名称（auth_type=key时）"},
            "password": {"type": "string", "description": "SSH密码（auth_type=password时）"},
            "jump_host": {"type": "string", "description": "跳板机名称"},
            "description": {"type": "string", "description": "服务器描述"},
            "tags": {"type": "string", "description": "标签，逗号分隔"},
            "group": {"type": "string", "description": "所属分组"},
        },
        "required": ["name", "host"],
    },
)
def create_server_tool(args, ctx, db):
    from config_manager import save_server
    name = args.get("name", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    server_data = {"name": name}
    for k in ["host", "port", "user", "auth_type", "key", "password", "jump_host", "description", "tags", "group"]:
        if args.get(k) is not None:
            server_data[k] = args[k]
    try:
        save_server(server_data)
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.update_server",
    description="更新服务器配置。High risk; requires human approval. 中文: 更新服务器/修改服务器配置.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "服务器名称"},
            "host": {"type": "string", "description": "服务器地址"},
            "port": {"type": "integer", "description": "SSH端口"},
            "user": {"type": "string", "description": "SSH用户名"},
            "auth_type": {"type": "string", "description": "认证方式: key/password", "enum": ["key", "password"]},
            "key": {"type": "string", "description": "SSH密钥名称"},
            "password": {"type": "string", "description": "SSH密码"},
            "jump_host": {"type": "string", "description": "跳板机名称"},
            "description": {"type": "string", "description": "服务器描述"},
            "tags": {"type": "string", "description": "标签，逗号分隔"},
            "group": {"type": "string", "description": "所属分组"},
        },
        "required": ["name"],
    },
)
def update_server_tool(args, ctx, db):
    from config_manager import save_server, get_server_by_name
    name = args.get("name", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    existing = get_server_by_name(name) or {}
    server_data = dict(existing)
    server_data["name"] = name
    for k in ["host", "port", "user", "auth_type", "key", "password", "jump_host", "description", "tags", "group"]:
        if args.get(k) is not None:
            server_data[k] = args[k]
    try:
        save_server(server_data)
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.delete_server",
    description="删除服务器配置。Critical risk; requires human approval. 中文: 删除服务器/移除服务器.",
    scopes=["server:write"],
    risk="critical",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "服务器名称"},
            "confirm_text": {"type": "string", "description": "确认短语: DELETE <name>"},
        },
        "required": ["name", "confirm_text"],
    },
)
def delete_server_tool(args, ctx, db):
    from config_manager import delete_server as cfg_delete_server
    name = args.get("name", "")
    confirm = args.get("confirm_text", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    expected = f"DELETE {name}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    try:
        cfg_delete_server(name, db=db)
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.batch_update_servers",
    description="批量更新服务器属性。High risk; requires human approval. 中文: 批量更新服务器/批量修改服务器.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "names": {"type": "array", "items": {"type": "string"}, "description": "服务器名称列表"},
            "updates": {"type": "object", "description": "要更新的属性"},
        },
        "required": ["names", "updates"],
    },
)
def batch_update_servers_tool(args, ctx, db):
    from config_manager import save_server, get_server_by_name
    names = args.get("names", [])
    updates = args.get("updates", {})
    if not names or not updates:
        return {"ok": False, "error": "names and updates are required"}
    results = []
    for name in names:
        try:
            existing = get_server_by_name(name) or {}
            server_data = dict(existing)
            server_data.update(updates)
            server_data["name"] = name
            save_server(server_data)
            results.append({"name": name, "ok": True})
        except Exception as e:
            results.append({"name": name, "ok": False, "error": str(e)})
    return {"results": results}


# ─── System CRUD ────────────────────────────────────────────────────────────


@registry.register(
    name="ops.create_system",
    description="创建系统/项目配置。High risk; requires human approval. 中文: 创建系统/新增项目.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "系统名称（唯一标识）"},
            "display_name": {"type": "string", "description": "显示名称"},
            "description": {"type": "string", "description": "系统描述"},
            "owner": {"type": "string", "description": "负责人"},
        },
        "required": ["name"],
    },
)
def create_system_tool(args, ctx, db):
    from config_manager import save_system, get_system_by_name
    name = args.get("name", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    if get_system_by_name(name):
        return {"ok": False, "error": f"system '{name}' already exists"}
    system_data = {}
    for k in ["display_name", "description", "owner"]:
        if args.get(k) is not None:
            system_data[k] = args[k]
    system_data.setdefault("servers", [])
    system_data.setdefault("services", [])
    system_data.setdefault("environments", {})
    system_data.setdefault("variables", {})
    try:
        save_system(name, system_data)
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.update_system",
    description="更新系统/项目配置。High risk; requires human approval. 中文: 更新系统/修改项目配置.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "系统名称"},
            "display_name": {"type": "string", "description": "显示名称"},
            "description": {"type": "string", "description": "系统描述"},
            "owner": {"type": "string", "description": "负责人"},
        },
        "required": ["name"],
    },
)
def update_system_tool(args, ctx, db):
    from config_manager import save_system, get_system_by_name
    name = args.get("name", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    existing = get_system_by_name(name) or {}
    system_data = dict(existing)
    for k in ["display_name", "description", "owner"]:
        if args.get(k) is not None:
            system_data[k] = args[k]
    try:
        save_system(name, system_data)
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.delete_system",
    description="删除系统/项目配置。Critical risk; requires human approval. 中文: 删除系统/移除项目.",
    scopes=["server:write"],
    risk="critical",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "系统名称"},
            "confirm_text": {"type": "string", "description": "确认短语: DELETE <name>"},
        },
        "required": ["name", "confirm_text"],
    },
)
def delete_system_tool(args, ctx, db):
    from config_manager import delete_system as cfg_delete_system
    name = args.get("name", "")
    confirm = args.get("confirm_text", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    expected = f"DELETE {name}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    try:
        cfg_delete_system(name)
        return {"ok": True, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── Service CRUD ───────────────────────────────────────────────────────────


@registry.register(
    name="ops.create_service",
    description="创建服务配置。High risk; requires human approval. 中文: 创建服务/新增服务.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string", "description": "所属系统名称"},
            "name": {"type": "string", "description": "服务名称（唯一标识）"},
            "display_name": {"type": "string", "description": "显示名称"},
            "deploy_type": {"type": "string", "description": "部署类型"},
            "health_check_command": {"type": "string", "description": "健康检查命令"},
            "description": {"type": "string", "description": "服务描述"},
        },
        "required": ["system", "name"],
    },
)
def create_service_tool(args, ctx, db):
    from app.db.models import Service
    system = args.get("system", "")
    name = args.get("name", "")
    if not system or not name:
        return {"ok": False, "error": "system and name are required"}
    existing = db.query(Service).filter(Service.system_name == system, Service.name == name).first()
    if existing:
        return {"ok": False, "error": f"service '{name}' already exists in system '{system}'"}
    svc = Service(
        system_name=system,
        name=name,
        display_name=args.get("display_name"),
        template=args.get("deploy_type"),
        template_variables={},
    )
    if args.get("health_check_command"):
        svc.template_variables["health_check_command"] = args.get("health_check_command")
    if args.get("description"):
        svc.template_variables["description"] = args.get("description")
    try:
        db.add(svc)
        db.commit()
        return {"ok": True, "system": system, "name": name}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.update_service",
    description="更新服务配置。High risk; requires human approval. 中文: 更新服务/修改服务配置.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string", "description": "所属系统名称"},
            "name": {"type": "string", "description": "服务名称"},
            "display_name": {"type": "string", "description": "显示名称"},
            "deploy_type": {"type": "string", "description": "部署类型"},
            "health_check_command": {"type": "string", "description": "健康检查命令"},
            "description": {"type": "string", "description": "服务描述"},
        },
        "required": ["system", "name"],
    },
)
def update_service_tool(args, ctx, db):
    from app.db.models import Service
    system = args.get("system", "")
    name = args.get("name", "")
    if not system or not name:
        return {"ok": False, "error": "system and name are required"}
    svc = db.query(Service).filter(Service.system_name == system, Service.name == name).first()
    if not svc:
        return {"ok": False, "error": f"service '{name}' not found in system '{system}'"}
    if args.get("display_name") is not None:
        svc.display_name = args["display_name"]
    if args.get("deploy_type") is not None:
        svc.template = args["deploy_type"]
    if args.get("health_check_command") is not None or args.get("description") is not None:
        tv = svc.template_variables or {}
        if args.get("health_check_command") is not None:
            tv["health_check_command"] = args["health_check_command"]
        if args.get("description") is not None:
            tv["description"] = args["description"]
        svc.template_variables = tv
    try:
        db.commit()
        return {"ok": True, "system": system, "name": name}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.delete_service",
    description="删除服务配置。Critical risk; requires human approval. 中文: 删除服务/移除服务.",
    scopes=["server:write"],
    risk="critical",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string", "description": "所属系统名称"},
            "name": {"type": "string", "description": "服务名称"},
            "confirm_text": {"type": "string", "description": "确认短语: DELETE <system>/<name>"},
        },
        "required": ["system", "name", "confirm_text"],
    },
)
def delete_service_tool(args, ctx, db):
    from app.db.models import Service
    system = args.get("system", "")
    name = args.get("name", "")
    confirm = args.get("confirm_text", "")
    if not system or not name:
        return {"ok": False, "error": "system and name are required"}
    expected = f"DELETE {system}/{name}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    svc = db.query(Service).filter(Service.system_name == system, Service.name == name).first()
    if not svc:
        return {"ok": False, "error": f"service '{name}' not found in system '{system}'"}
    try:
        db.delete(svc)
        db.commit()
        return {"ok": True, "system": system, "name": name}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}


# ─── Environment CRUD ───────────────────────────────────────────────────────


@registry.register(
    name="ops.create_environment",
    description="创建环境配置。High risk; requires human approval. 中文: 创建环境/新增环境.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string", "description": "所属系统名称"},
            "name": {"type": "string", "description": "环境名称（唯一标识）"},
            "display_name": {"type": "string", "description": "显示名称"},
            "description": {"type": "string", "description": "环境描述"},
        },
        "required": ["system", "name"],
    },
)
def create_environment_tool(args, ctx, db):
    from config_manager import save_environment, get_environment
    system = args.get("system", "")
    name = args.get("name", "")
    if not system or not name:
        return {"ok": False, "error": "system and name are required"}
    if get_environment(system, name):
        return {"ok": False, "error": f"environment '{name}' already exists in system '{system}'"}
    env_data = {}
    if args.get("display_name"):
        env_data["display_name"] = args["display_name"]
    variables = {}
    if args.get("description"):
        variables["description"] = args["description"]
    if variables:
        env_data["variables"] = variables
    try:
        save_environment(system, name, env_data)
        return {"ok": True, "system": system, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.update_environment",
    description="更新环境配置。High risk; requires human approval. 中文: 更新环境/修改环境配置.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string", "description": "所属系统名称"},
            "name": {"type": "string", "description": "环境名称"},
            "display_name": {"type": "string", "description": "显示名称"},
            "description": {"type": "string", "description": "环境描述"},
        },
        "required": ["system", "name"],
    },
)
def update_environment_tool(args, ctx, db):
    from config_manager import save_environment, get_environment
    system = args.get("system", "")
    name = args.get("name", "")
    if not system or not name:
        return {"ok": False, "error": "system and name are required"}
    existing = get_environment(system, name) or {}
    env_data = dict(existing)
    if args.get("display_name") is not None:
        env_data["display_name"] = args["display_name"]
    if args.get("description") is not None:
        variables = env_data.get("variables", {})
        variables["description"] = args["description"]
        env_data["variables"] = variables
    try:
        save_environment(system, name, env_data)
        return {"ok": True, "system": system, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.delete_environment",
    description="删除环境配置。Critical risk; requires human approval. 中文: 删除环境/移除环境.",
    scopes=["server:write"],
    risk="critical",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="sensitive",
    input_schema={
        "type": "object",
        "properties": {
            "system": {"type": "string", "description": "所属系统名称"},
            "name": {"type": "string", "description": "环境名称"},
            "confirm_text": {"type": "string", "description": "确认短语: DELETE <system>/<name>"},
        },
        "required": ["system", "name", "confirm_text"],
    },
)
def delete_environment_tool(args, ctx, db):
    from config_manager import delete_environment as cfg_delete_environment
    system = args.get("system", "")
    name = args.get("name", "")
    confirm = args.get("confirm_text", "")
    if not system or not name:
        return {"ok": False, "error": "system and name are required"}
    expected = f"DELETE {system}/{name}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    try:
        cfg_delete_environment(system, name)
        return {"ok": True, "system": system, "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── Server Group Write Operations ──────────────────────────────────────────


@registry.register(
    name="ops.create_server_group",
    description="创建服务器分组。Medium risk. 中文: 创建服务器分组/新增分组.",
    scopes=["server:write"],
    risk="medium",
    category="server_write",
    write=True,
    requires_confirmation=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "分组名称（唯一标识）"},
            "display_name": {"type": "string", "description": "显示名称"},
            "description": {"type": "string", "description": "分组描述"},
            "server_names": {"type": "array", "items": {"type": "string"}, "description": "服务器名称列表"},
        },
        "required": ["name"],
    },
)
def create_server_group_tool(args, ctx, db):
    from app.db.models import ServerGroup
    name = args.get("name", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    existing = db.query(ServerGroup).filter(ServerGroup.name == name).first()
    if existing:
        return {"ok": False, "error": f"server group '{name}' already exists"}
    group = ServerGroup(
        name=name,
        display_name=args.get("display_name"),
        description=args.get("description"),
        server_names=args.get("server_names", []),
    )
    try:
        db.add(group)
        db.commit()
        return {"ok": True, "name": name}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.assign_server_group",
    description="分配服务器到分组。Medium risk. 中文: 分配服务器到分组/设置分组服务器.",
    scopes=["server:write"],
    risk="medium",
    category="server_write",
    write=True,
    requires_confirmation=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "group_name": {"type": "string", "description": "分组名称"},
            "server_names": {"type": "array", "items": {"type": "string"}, "description": "服务器名称列表"},
        },
        "required": ["group_name", "server_names"],
    },
)
def assign_server_group_tool(args, ctx, db):
    from app.db.models import ServerGroup
    group_name = args.get("group_name", "")
    server_names = args.get("server_names", [])
    if not group_name or not server_names:
        return {"ok": False, "error": "group_name and server_names are required"}
    group = db.query(ServerGroup).filter(ServerGroup.name == group_name).first()
    if not group:
        return {"ok": False, "error": f"server group '{group_name}' not found"}
    group.server_names = server_names
    try:
        db.commit()
        return {"ok": True, "group_name": group_name, "server_names": server_names}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.rename_server_group",
    description="重命名服务器分组。Medium risk. 中文: 重命名服务器分组/修改分组名称.",
    scopes=["server:write"],
    risk="medium",
    category="server_write",
    write=True,
    requires_confirmation=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "old_name": {"type": "string", "description": "原分组名称"},
            "new_name": {"type": "string", "description": "新分组名称"},
        },
        "required": ["old_name", "new_name"],
    },
)
def rename_server_group_tool(args, ctx, db):
    from app.db.models import ServerGroup
    old_name = args.get("old_name", "")
    new_name = args.get("new_name", "")
    if not old_name or not new_name:
        return {"ok": False, "error": "old_name and new_name are required"}
    group = db.query(ServerGroup).filter(ServerGroup.name == old_name).first()
    if not group:
        return {"ok": False, "error": f"server group '{old_name}' not found"}
    existing = db.query(ServerGroup).filter(ServerGroup.name == new_name).first()
    if existing:
        return {"ok": False, "error": f"server group '{new_name}' already exists"}
    group.name = new_name
    try:
        db.commit()
        return {"ok": True, "old_name": old_name, "new_name": new_name}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}


@registry.register(
    name="ops.delete_server_group",
    description="删除服务器分组。High risk; requires human approval. 中文: 删除服务器分组/移除分组.",
    scopes=["server:write"],
    risk="high",
    category="server_write",
    write=True,
    requires_confirmation=True,
    requires_human_approval=True,
    ai_callable=True,
    ai_auto_callable=False,
    data_sensitivity="internal",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "分组名称"},
            "confirm_text": {"type": "string", "description": "确认短语: DELETE <name>"},
        },
        "required": ["name", "confirm_text"],
    },
)
def delete_server_group_tool(args, ctx, db):
    from app.db.models import ServerGroup
    name = args.get("name", "")
    confirm = args.get("confirm_text", "")
    if not name:
        return {"ok": False, "error": "name is required"}
    expected = f"DELETE {name}"
    if confirm != expected:
        return {"ok": False, "error": f"confirm_text must be '{expected}'"}
    group = db.query(ServerGroup).filter(ServerGroup.name == name).first()
    if not group:
        return {"ok": False, "error": f"server group '{name}' not found"}
    try:
        db.delete(group)
        db.commit()
        return {"ok": True, "name": name}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}

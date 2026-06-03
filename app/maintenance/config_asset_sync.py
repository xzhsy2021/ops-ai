"""Synchronize DB-backed asset tables from config_kv inventory.

The first configuration migration moved ``config/default_config.json`` into the
``config_kv`` table.  Some MCP/database tools, however, read normalized asset
models such as ``servers`` and ``services``.  This module bridges the two worlds
for the lightweight single-project deployment: keep config_kv as the source of
runtime configuration, and materialize safe asset rows into normalized tables so
MCP tools, selectors and reports see the same inventory.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_str(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _iter_config_servers(config: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    seen: set[str] = set()
    for source_name, items in (("servers", config.get("servers")), ("jump_hosts", config.get("jump_hosts"))):
        for raw in _as_list(items):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            name = _clean_str(item.get("name") or item.get("host"))
            host = _clean_str(item.get("host"))
            if not name and not host:
                continue
            if not name:
                name = host
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            item["name"] = name
            if source_name == "jump_hosts" and not item.get("group"):
                item["group"] = "jump-host"
            yield item


def _upsert_server(db: Session, raw: Dict[str, Any], *, overwrite: bool) -> str:
    from app.db.models import Server
    from app.db.repository import ServerRepository

    repo = ServerRepository(db)
    name = _clean_str(raw.get("name") or raw.get("host"))
    host = _clean_str(raw.get("host") or name)
    if not name or not host:
        return "skipped"
    try:
        port = int(raw.get("port") or 22)
    except Exception:
        port = 22
    user = _clean_str(raw.get("user") or raw.get("username") or "root", "root") or "root"
    key = _clean_str(raw.get("key") or raw.get("key_file") or "~/.ssh/id_rsa", "~/.ssh/id_rsa") or "~/.ssh/id_rsa"
    key_content = raw.get("key_content")
    password = raw.get("password")
    jump_host = _clean_str(raw.get("jump_host") or "") or None
    status = _clean_str(raw.get("status") or ("disabled" if raw.get("enabled") is False else "online"), "online").lower() or "online"
    if status not in {"online", "disabled", "offline"}:
        status = "online"

    existing = db.query(Server).filter(Server.name == name).first()
    if not existing:
        repo.create(
            name=name,
            host=host,
            port=port,
            user=user,
            key=key,
            key_content=key_content,
            password=password,
            jump_host=jump_host,
            status=status,
        )
        return "created"

    if not overwrite:
        changed = False
        # Fill only missing/sparse fields. Do not clobber credentials entered in UI.
        if not existing.host and host:
            existing.host = host; changed = True
        if not existing.port and port:
            existing.port = port; changed = True
        if not existing.user and user:
            existing.user = user; changed = True
        if not existing.key and key:
            existing.key = key; changed = True
        if not existing.jump_host and jump_host:
            existing.jump_host = jump_host; changed = True
        if not getattr(existing, "status", None) and status:
            existing.status = status; changed = True
        if changed:
            db.commit()
            return "updated_missing"
        return "unchanged"

    detached = Server(
        id=existing.id,
        name=name,
        host=host,
        port=port,
        user=user,
        key=key,
        key_content=key_content,
        password=password,
        jump_host=jump_host,
        status=status,
    )
    repo.update(detached)
    return "updated"


def _service_records_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    systems = _as_dict(config.get("systems"))
    for system_name, sys_cfg_any in systems.items():
        sys_cfg = _as_dict(sys_cfg_any)
        for svc in _as_list(sys_cfg.get("services")):
            if not isinstance(svc, dict):
                continue
            name = _clean_str(svc.get("name") or svc.get("service_name") or svc.get("display_name"))
            if not name:
                continue
            result.append({
                "system_name": str(system_name),
                "name": name,
                "display_name": svc.get("display_name"),
                "repo": svc.get("repo"),
                "build_cmd": svc.get("build_cmd"),
                "start_cmd": svc.get("start_cmd") or svc.get("update_script"),
                "template": svc.get("template"),
                "pipeline_id": svc.get("pipeline_id"),
                "template_variables": _json_safe(svc.get("template_variables") or {}),
                "servers": _json_safe(svc.get("servers") or sys_cfg.get("servers") or []),
            })
    return result


def _upsert_service(db: Session, raw: Dict[str, Any], *, overwrite: bool) -> str:
    from app.db.models import Service

    name = _clean_str(raw.get("name"))
    system_name = _clean_str(raw.get("system_name"))
    if not name or not system_name:
        return "skipped"
    existing = db.query(Service).filter(Service.name == name, Service.system_name == system_name).first()
    fields = ["display_name", "repo", "build_cmd", "start_cmd", "template", "pipeline_id", "template_variables", "servers"]
    if not existing:
        db.add(Service(**{k: raw.get(k) for k in ["name", "system_name"] + fields}))
        db.commit()
        return "created"
    changed = False
    for field in fields:
        value = raw.get(field)
        if overwrite or getattr(existing, field, None) in (None, "", [], {}):
            if value is not None and getattr(existing, field, None) != value:
                setattr(existing, field, value)
                changed = True
    if changed:
        db.commit()
        return "updated" if overwrite else "updated_missing"
    return "unchanged"


def _environment_records_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    systems = _as_dict(config.get("systems"))
    for _system_name, sys_cfg_any in systems.items():
        sys_cfg = _as_dict(sys_cfg_any)
        for env_name, env_cfg_any in _as_dict(sys_cfg.get("environments")).items():
            env_cfg = _as_dict(env_cfg_any)
            name = _clean_str(env_name)
            if not name:
                continue
            item = result.setdefault(name, {"name": name, "variables": {}})
            variables = dict(item.get("variables") or {})
            variables.update(_as_dict(env_cfg.get("variables")))
            if env_cfg.get("display_name"):
                variables.setdefault("display_name", env_cfg.get("display_name"))
            if env_cfg.get("base_path"):
                variables.setdefault("base_path", env_cfg.get("base_path"))
            item["variables"] = variables
    return list(result.values())


def _upsert_environment(db: Session, raw: Dict[str, Any], *, overwrite: bool) -> str:
    from app.db.models import Environment

    name = _clean_str(raw.get("name"))
    if not name:
        return "skipped"
    variables = _json_safe(raw.get("variables") or {})
    existing = db.query(Environment).filter(Environment.name == name).first()
    if not existing:
        db.add(Environment(name=name, variables=variables))
        db.commit()
        return "created"
    if overwrite or not existing.variables:
        existing.variables = variables
        db.commit()
        return "updated"
    return "unchanged"


def _server_group_records_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    systems = _as_dict(config.get("systems"))
    for system_name, sys_cfg_any in systems.items():
        sys_cfg = _as_dict(sys_cfg_any)
        servers = [str(s).strip() for s in _as_list(sys_cfg.get("servers")) if str(s).strip()]
        if servers:
            result[str(system_name)] = {
                "name": str(system_name),
                "display_name": sys_cfg.get("display_name") or str(system_name),
                "description": f"由配置中心同步的 {system_name} 默认服务器组",
                "server_names": servers,
                "tags": ["config-sync", "system"],
            }
        for group_code, group_cfg_any in _as_dict(sys_cfg.get("groups")).items():
            group_cfg = _as_dict(group_cfg_any)
            gservers = []
            if group_cfg.get("server"):
                gservers.append(str(group_cfg.get("server")))
            gservers.extend([str(s) for s in _as_list(group_cfg.get("servers"))])
            gservers = [s.strip() for s in gservers if s and s.strip()]
            if not gservers:
                continue
            name = f"{system_name}-{group_code}"
            result[name] = {
                "name": name,
                "display_name": group_cfg.get("display_name") or name,
                "description": f"由配置中心同步的 {system_name}/{group_code} 服务器组",
                "server_names": gservers,
                "tags": ["config-sync", "group", str(system_name)],
            }
    return list(result.values())


def _upsert_server_group(db: Session, raw: Dict[str, Any], *, overwrite: bool) -> str:
    from app.db.models import ServerGroup

    name = _clean_str(raw.get("name"))
    if not name:
        return "skipped"
    existing = db.query(ServerGroup).filter(ServerGroup.name == name).first()
    fields = ["display_name", "description", "server_names", "tags"]
    if not existing:
        db.add(ServerGroup(**{k: raw.get(k) for k in ["name"] + fields}))
        db.commit()
        return "created"
    changed = False
    for field in fields:
        value = raw.get(field)
        if overwrite or getattr(existing, field, None) in (None, "", [], {}):
            if value is not None and getattr(existing, field, None) != value:
                setattr(existing, field, value)
                changed = True
    if changed:
        db.commit()
        return "updated" if overwrite else "updated_missing"
    return "unchanged"


def sync_config_assets_to_db(db: Session, *, config: Optional[Dict[str, Any]] = None, overwrite: bool = False) -> Dict[str, Any]:
    """Materialize config_kv inventory into normalized asset tables.

    Returns a summary suitable for CLI output and diagnostics.
    """
    if config is None:
        from app.config.repository import load_config
        config = load_config()
    config = config or {}
    summary: Dict[str, Any] = {
        "servers": {"created": 0, "updated": 0, "updated_missing": 0, "unchanged": 0, "skipped": 0},
        "services": {"created": 0, "updated": 0, "updated_missing": 0, "unchanged": 0, "skipped": 0},
        "environments": {"created": 0, "updated": 0, "updated_missing": 0, "unchanged": 0, "skipped": 0},
        "server_groups": {"created": 0, "updated": 0, "updated_missing": 0, "unchanged": 0, "skipped": 0},
    }

    for server in _iter_config_servers(config):
        status = _upsert_server(db, server, overwrite=overwrite)
        summary["servers"][status] = summary["servers"].get(status, 0) + 1

    for service in _service_records_from_config(config):
        status = _upsert_service(db, service, overwrite=overwrite)
        summary["services"][status] = summary["services"].get(status, 0) + 1

    for env in _environment_records_from_config(config):
        status = _upsert_environment(db, env, overwrite=overwrite)
        summary["environments"][status] = summary["environments"].get(status, 0) + 1

    for group in _server_group_records_from_config(config):
        status = _upsert_server_group(db, group, overwrite=overwrite)
        summary["server_groups"][status] = summary["server_groups"].get(status, 0) + 1

    try:
        from app.db.models import Server, Service, Environment, ServerGroup
        summary["table_counts"] = {
            "servers": db.query(Server).count(),
            "services": db.query(Service).count(),
            "environments": db.query(Environment).count(),
            "server_groups": db.query(ServerGroup).count(),
        }
    except Exception:
        logger.exception("Failed to count synchronized config asset tables")
    return summary

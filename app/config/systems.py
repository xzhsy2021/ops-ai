"""Database-only system inventory composition and compatibility helpers."""
import copy
import logging
from typing import Any, Dict, List, Optional

from app.services.qclaw_routing import (
    normalize_message_routing_config as _normalize_message_routing_config,
)

logger = logging.getLogger(__name__)


def normalize_message_routing_config(value: Any) -> Dict[str, Any]:
    """Database configuration boundary for strict message-routing values."""
    return _normalize_message_routing_config(value)


# ── DB SSOT 读写 ──

def _service_row_to_dict(row) -> Dict[str, Any]:
    template_variables = dict(row.template_variables or {})
    return {
        "id": row.id,
        "name": row.name,
        "display_name": row.display_name or row.name,
        "system_name": row.system_name,
        "repo": row.repo or "",
        "build_cmd": row.build_cmd or "",
        "start_cmd": row.start_cmd or "",
        "template": row.template or "",
        "pipeline_id": row.pipeline_id or "",
        "template_variables": template_variables,
        "servers": list(row.servers or []),
        "source": "db",
    }


def _environment_row_to_dict(row) -> Dict[str, Any]:
    return {
        "display_name": row.display_name or row.name,
        "category": row.category or "custom",
        "description": row.description or "",
        "base_path": row.base_path or "",
        "servers": list(row.servers or []),
        "variables": dict(row.variables or {}),
        "service_overrides": dict(row.service_overrides or {}),
        "group_overrides": dict(row.group_overrides or {}),
    }


def _system_row_to_dict(row, services=None, environments=None) -> Dict[str, Any]:
    """ORM System row → legacy system cfg dict（保持向后兼容）。"""
    return {
        "display_name": row.display_name or row.name,
        "strategy": row.strategy or "WORKFLOW",
        "base_path": row.base_path or "/data/web/app",
        "description": row.description or "",
        "servers": list(row.servers or []),
        "services": [_service_row_to_dict(item) for item in (services or [])],
        "environments": {
            item.name: _environment_row_to_dict(item)
            for item in (environments or [])
        },
        "variables": dict(row.variables or {}),
        "message_routing": normalize_message_routing_config(
            row.message_routing if row.message_routing is not None else {}
        ),
        # groups 已迁移到 ServerGroup 表（Phase 3b），这里不返回
        "source": "db",
    }


def get_all_systems() -> Dict[str, Any]:
    """Read systems and their child resources from database SSOT tables."""
    from app.db.base import SessionLocal
    from app.db.repository import (
        ServiceRepository,
        SystemEnvironmentRepository,
        SystemRepository,
    )

    with SessionLocal() as db:
        systems = SystemRepository(db).list_all()
        service_repo = ServiceRepository(db)
        environment_repo = SystemEnvironmentRepository(db)
        return {
            row.name: _system_row_to_dict(
                row,
                service_repo.list_by_system(row.name),
                environment_repo.list_by_system(row.name),
            )
            for row in systems
        }


def get_system_by_name(name: str) -> Optional[Dict[str, Any]]:
    return get_all_systems().get(name)


def get_system_config(system_name: str) -> Optional[Dict[str, Any]]:
    return get_all_systems().get(system_name)


def get_servers_for_system(system_name: str, environment: str = None) -> List[Dict[str, Any]]:
    from app.config.servers import get_server_by_name
    cfg = get_system_config(system_name)
    if not cfg:
        return []
    if environment and environment in cfg.get("environments", {}):
        env_cfg = cfg["environments"][environment]
        return [get_server_by_name(n) for n in env_cfg.get("servers", []) if get_server_by_name(n)]
    return [get_server_by_name(n) for n in cfg.get("servers", []) if get_server_by_name(n)]


def save_system(name: str, system: Dict[str, Any]) -> bool:
    """Persist a system and its child resources to their domain tables."""
    if "groups" in system:
        logger.debug("groups field ignored (migrated to ServerGroup table, Phase 3b)")
    try:
        message_routing = normalize_message_routing_config(
            system.get("message_routing")
            if system.get("message_routing") is not None
            else {}
        )
        from app.db.base import SessionLocal
        from app.db.models import Service, System, SystemEnvironment
        from app.db.repository import SystemRepository
        with SessionLocal() as db:
            repo = SystemRepository(db)
            existing = repo.get_by_name(name)
            if existing:
                existing.display_name = system.get("display_name") or name
                existing.strategy = system.get("strategy", "WORKFLOW")
                existing.base_path = system.get("base_path", "/data/web/app")
                existing.description = system.get("description")
                existing.variables = system.get("variables", {}) or {}
                existing.servers = system.get("servers", []) or []
                existing.message_routing = message_routing
                existing.environments = {}
                existing.services = []
                logger.info(f"Updated system: {name}")
            else:
                existing = System(
                    name=name,
                    display_name=system.get("display_name") or name,
                    strategy=system.get("strategy", "WORKFLOW"),
                    base_path=system.get("base_path", "/data/web/app"),
                    description=system.get("description"),
                    variables=system.get("variables", {}) or {},
                    servers=system.get("servers", []) or [],
                    environments={},
                    services=[],
                    message_routing=message_routing,
                )
                db.add(existing)
                db.flush()
                logger.info(f"Added system: {name}")

            if "services" in system:
                incoming_services = {
                    str(item.get("name") or "").strip(): item
                    for item in (system.get("services") or [])
                    if isinstance(item, dict) and str(item.get("name") or "").strip()
                }
                stored_services = {
                    row.name: row
                    for row in db.query(Service).filter(Service.system_name == name).all()
                }
                for service_name, row in stored_services.items():
                    if service_name not in incoming_services:
                        db.delete(row)
                for service_name, payload in incoming_services.items():
                    row = stored_services.get(service_name)
                    if row is None:
                        row = Service(name=service_name, system_name=name)
                        db.add(row)
                    row.display_name = payload.get("display_name") or service_name
                    row.repo = payload.get("repo") or None
                    row.build_cmd = payload.get("build_cmd") or None
                    row.start_cmd = payload.get("start_cmd") or None
                    row.template = payload.get("template") or None
                    row.pipeline_id = payload.get("pipeline_id") or None
                    template_variables = dict(payload.get("template_variables") or {})
                    # 服务级 message_routing 已收敛到系统级，不再持久化
                    template_variables.pop("message_routing", None)
                    row.template_variables = template_variables
                    row.servers = payload.get("servers") or []

            if "environments" in system:
                incoming_environments = system.get("environments") or {}
                stored_environments = {
                    row.name: row
                    for row in db.query(SystemEnvironment).filter(
                        SystemEnvironment.system_name == name,
                    ).all()
                }
                for environment_name, row in stored_environments.items():
                    if environment_name not in incoming_environments:
                        db.delete(row)
                for environment_name, payload in incoming_environments.items():
                    if not isinstance(payload, dict):
                        payload = {}
                    row = stored_environments.get(environment_name)
                    if row is None:
                        row = SystemEnvironment(system_name=name, name=environment_name)
                        db.add(row)
                    row.display_name = payload.get("display_name") or environment_name
                    row.category = payload.get("category") or "custom"
                    row.description = payload.get("description") or ""
                    row.base_path = payload.get("base_path") or payload.get("deploy_path") or ""
                    row.servers = payload.get("servers") or []
                    row.variables = payload.get("variables") or {}
                    row.service_overrides = payload.get("service_overrides") or {}
                    row.group_overrides = payload.get("group_overrides") or {}
            db.commit()
        return True
    except Exception as e:
        logger.exception(f"Failed to save system '{name}' to DB")
        return False


def delete_system(name: str) -> bool:
    """Delete a system and its database-owned child resources."""
    try:
        from app.db.base import SessionLocal
        from app.db.models import Service, SystemEnvironment
        from app.db.repository import SystemRepository
        with SessionLocal() as db:
            repo = SystemRepository(db)
            row = repo.get_by_name(name)
            if row is None:
                return False
            db.query(Service).filter(Service.system_name == name).delete(synchronize_session=False)
            db.query(SystemEnvironment).filter(
                SystemEnvironment.system_name == name,
            ).delete(synchronize_session=False)
            db.delete(row)
            db.commit()
            logger.info(f"Deleted system: {name}")
            return True
    except Exception:
        logger.exception(f"Failed to delete system '{name}' from DB")
        return False


# ── deep-merge 逻辑（保持不变）──

def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _apply_service_overrides(sys_services: list, env_service_overrides: dict) -> list:
    if not env_service_overrides:
        return sys_services
    result = copy.deepcopy(sys_services)
    for svc in result:
        if not isinstance(svc, dict):
            continue
        svc_name = svc.get("name", "")
        if svc_name in env_service_overrides:
            override = env_service_overrides[svc_name]
            if isinstance(override, dict):
                for key, value in override.items():
                    if key == "template_variables" and "template_variables" in svc:
                        svc["template_variables"] = _deep_merge(svc["template_variables"], value)
                    else:
                        svc[key] = copy.deepcopy(value)
    return result


def _apply_group_overrides(sys_groups: dict, env_group_overrides: dict) -> dict:
    if not env_group_overrides:
        return sys_groups
    result = copy.deepcopy(sys_groups)
    for gcode, gcfg in result.items():
        if gcode in env_group_overrides:
            override = env_group_overrides[gcode]
            if isinstance(override, dict):
                for key, value in override.items():
                    result[gcode][key] = copy.deepcopy(value)
    return result


def resolve_system_config(system_name: str, environment: str = None) -> Dict[str, Any]:
    system = get_system_config(system_name)
    if not system:
        return {}
    result = {
        "name": system_name,
        "display_name": system.get("display_name", system_name),
        "strategy": system.get("strategy", "WORKFLOW"),
        "base_path": system.get("base_path", "/data/web/app"),
        "servers": system.get("servers", []),
        "services": copy.deepcopy(system.get("services", [])),
        "groups": copy.deepcopy(system.get("groups", system.get("regions", {})) or {}),
        "variables": copy.deepcopy(system.get("variables", {})),
        "source": "system",
    }
    if environment and environment in system.get("environments", {}):
        env_cfg = system["environments"][environment]
        result["display_name"] = env_cfg.get("display_name", result["display_name"])
        result["strategy"] = env_cfg.get("strategy", result["strategy"])
        result["base_path"] = env_cfg.get("base_path", result["base_path"])
        result["servers"] = env_cfg.get("servers", result["servers"])
        result["source"] = "environment"
        result["environment"] = environment
        if "variables" in env_cfg and isinstance(env_cfg["variables"], dict):
            result["variables"].update(env_cfg["variables"])
        if env_cfg.get("service_overrides"):
            result["services"] = _apply_service_overrides(result["services"], env_cfg["service_overrides"])
        elif "services" in env_cfg:
            result["services"] = copy.deepcopy(env_cfg["services"])
        if env_cfg.get("group_overrides"):
            result["groups"] = _apply_group_overrides(result["groups"], env_cfg["group_overrides"])
        elif "groups" in env_cfg:
            result["groups"] = copy.deepcopy(env_cfg.get("groups") or {})
    return result


def get_variable_inheritance(system_name: str, service_name: str = None, environment: str = None) -> Dict[str, Any]:
    system = get_system_config(system_name)
    if not system:
        return {}
    sys_vars = system.get("variables", {})
    if not isinstance(sys_vars, dict):
        sys_vars = {}
    chain = {}
    for var_name, var_value in sys_vars.items():
        chain[var_name] = {
            "value": var_value,
            "source": "system",
            "chain": [{"level": "system", "value": var_value}],
        }
    if environment and environment in system.get("environments", {}):
        env_cfg = system["environments"][environment]
        env_vars = env_cfg.get("variables", {})
        if isinstance(env_vars, dict):
            for var_name, var_value in env_vars.items():
                if var_name not in chain:
                    chain[var_name] = {"value": var_value, "source": f"environment:{environment}", "chain": []}
                chain[var_name]["value"] = var_value
                chain[var_name]["source"] = f"environment:{environment}"
                chain[var_name]["chain"].append({"level": f"environment:{environment}", "value": var_value})
    if service_name:
        resolved = resolve_system_config(system_name, environment=environment)
        for svc in resolved.get("services", []):
            if isinstance(svc, dict) and svc.get("name") == service_name:
                svc_vars = svc.get("template_variables", {})
                if isinstance(svc_vars, dict):
                    for var_name, var_value in svc_vars.items():
                        if var_name not in chain:
                            chain[var_name] = {"value": var_value, "source": f"service:{service_name}", "chain": []}
                        chain[var_name]["value"] = var_value
                        chain[var_name]["source"] = f"service:{service_name}"
                        chain[var_name]["chain"].append({"level": f"service:{service_name}", "value": var_value})
                break
    return chain


# ── ServerGroup SSOT（Phase 3b，保持不变）──

def get_all_groups(system_name: str, environment: str = None) -> Dict[str, Any]:
    """Phase 3b SSOT: 从 ServerGroup 表读取所有该 system 的分组。"""
    if environment:
        logger.debug("get_all_groups(%s) ignores environment=%r (Phase 3b flat model)",
                     system_name, environment)
    from app.db.base import SessionLocal
    from app.db.repository import ServerGroupRepository
    prefix = f"{system_name}-"
    db = SessionLocal()
    try:
        repo = ServerGroupRepository(db)
        result: Dict[str, Any] = {}
        for g in repo.list_all():
            if not g.name.startswith(prefix):
                continue
            code = g.name[len(prefix):]
            result[code] = _server_group_to_legacy_dict(g)
        return result
    finally:
        db.close()


def get_group(system_name: str, group_code: str, environment: str = None) -> Optional[Dict[str, Any]]:
    """Phase 3b SSOT: 从 ServerGroup 表读取单个分组。"""
    if environment:
        logger.debug("get_group(%s,%s) ignores environment=%r (Phase 3b flat model)",
                     system_name, group_code, environment)
    from app.db.base import SessionLocal
    from app.db.repository import ServerGroupRepository
    db = SessionLocal()
    try:
        repo = ServerGroupRepository(db)
        g = repo.get_by_name(f"{system_name}-{group_code}")
        return _server_group_to_legacy_dict(g) if g else None
    finally:
        db.close()


def save_group(system_name: str, group_code: str, group_cfg: Dict[str, Any], environment: str = None) -> bool:
    """Phase 3b SSOT: 写入 ServerGroup 表。"""
    if environment:
        logger.debug("save_group(%s,%s) ignores environment=%r (Phase 3b flat model)",
                     system_name, group_code, environment)
    from app.db.base import SessionLocal
    from app.db.models import ServerGroup
    from app.db.repository import ServerGroupRepository

    if not system_name or not group_code:
        logger.error("save_group: system_name=%r group_code=%r (both required)", system_name, group_code)
        return False

    name = f"{system_name}-{group_code}"
    cfg = group_cfg or {}
    server_names = list(cfg.get("servers") or [])
    if cfg.get("server") and cfg.get("server") not in server_names:
        server_names.append(cfg["server"])

    meta: Dict[str, Any] = {}
    for k, v in cfg.items():
        if k in ("servers", "display_name"):
            continue
        meta[k] = v

    db = SessionLocal()
    try:
        repo = ServerGroupRepository(db)
        existing = repo.get_by_name(name)
        if existing is None:
            repo.create(
                name=name,
                display_name=cfg.get("display_name") or group_code,
                description=meta.pop("description", None),
                server_names=server_names,
                tags=meta.pop("tags", None) or [],
            )
            existing = repo.get_by_name(name)
            existing.metadata_json = meta or None
            db.commit()
            db.refresh(existing)
        else:
            existing.display_name = cfg.get("display_name") or existing.display_name or group_code
            existing.server_names = server_names
            desc = meta.pop("description", None)
            if desc is not None:
                existing.description = desc
            existing.tags = meta.pop("tags", None) or existing.tags or []
            existing.metadata_json = meta or None
            repo.update(existing)
        logger.info("Saved group (DB SSOT): %s", name)
        return True
    except Exception:
        logger.exception("save_group failed for %s", name)
        try:
            db.rollback()
        except Exception:
            logger.debug("rollback failed in save_group", exc_info=True)
        return False
    finally:
        db.close()


def delete_group(system_name: str, group_code: str, environment: str = None) -> bool:
    """Phase 3b SSOT: 从 ServerGroup 表删除分组。"""
    if environment:
        logger.debug("delete_group(%s,%s) ignores environment=%r (Phase 3b flat model)",
                     system_name, group_code, environment)
    from app.db.base import SessionLocal
    from app.db.repository import ServerGroupRepository
    name = f"{system_name}-{group_code}"
    db = SessionLocal()
    try:
        repo = ServerGroupRepository(db)
        existing = repo.get_by_name(name)
        if existing is None:
            return False
        db.delete(existing)
        db.commit()
        logger.info("Deleted group (DB SSOT): %s", name)
        return True
    except Exception:
        logger.exception("delete_group failed for %s", name)
        try:
            db.rollback()
        except Exception:
            logger.debug("rollback failed in delete_group", exc_info=True)
        return False
    finally:
        db.close()


def _server_group_to_legacy_dict(g) -> Dict[str, Any]:
    """ORM ServerGroup row → legacy group cfg dict。"""
    meta = g.metadata_json or {}
    result: Dict[str, Any] = {
        "display_name": g.display_name or g.name,
        "server": meta.get("server", ""),
        "servers": list(g.server_names or []),
        "variables": meta.get("variables", {}) or {},
        "tags": list(g.tags or []) + list(meta.get("tags", []) or []),
        "description": g.description or meta.get("description", ""),
    }
    for k, v in meta.items():
        if k in result and not result[k]:
            result[k] = v
        elif k not in result:
            result[k] = v
    return result


def get_all_dovo_regions() -> Dict[str, Any]:
    return get_all_groups("dovo")


def get_dovo_region(region_code: str) -> Optional[Dict[str, Any]]:
    return get_group("dovo", region_code)


def save_dovo_region(region_code: str, region_cfg: Dict[str, Any]) -> bool:
    return save_group("dovo", region_code, region_cfg)


def delete_dovo_region(region_code: str) -> bool:
    return delete_group("dovo", region_code)

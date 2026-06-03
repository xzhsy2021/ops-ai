import copy
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

def get_all_systems() -> Dict[str, Any]:
    from app.config.cache import load_config_cached
    systems = load_config_cached().get("systems", {})
    for sys_cfg in systems.values():
        if "variables" not in sys_cfg:
            sys_cfg["variables"] = {}
    return systems

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
    from app.config.repository import load_config, save_config
    config = load_config()
    systems = config.get("systems", {})
    is_new = name not in systems
    systems[name] = system
    config["systems"] = systems
    logger.info(f"{'Added' if is_new else 'Updated'} system: {name}")
    return save_config(config)

def delete_system(name: str) -> bool:
    from app.config.repository import load_config, save_config
    config = load_config()
    systems = config.get("systems", {})
    if name in systems:
        del systems[name]
        config["systems"] = systems
        logger.info(f"Deleted system: {name}")
        return save_config(config)
    return False

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

def get_all_groups(system_name: str, environment: str = None) -> Dict[str, Any]:
    from app.config.repository import load_config
    config = load_config()
    system = config.get("systems", {}).get(system_name)
    if not system:
        return {}
    if environment:
        env_data = system.get("environments", {}).get(environment, {})
        return env_data.get("groups", {})
    return system.get("groups") or system.get("regions") or {}

def get_group(system_name: str, group_code: str, environment: str = None) -> Optional[Dict[str, Any]]:
    groups = get_all_groups(system_name, environment)
    return groups.get(group_code)

def save_group(system_name: str, group_code: str, group_cfg: Dict[str, Any], environment: str = None) -> bool:
    from app.config.repository import load_config, save_config
    config = load_config()
    system = config.get("systems", {}).get(system_name)
    if not system:
        logger.error(f"System '{system_name}' not found")
        return False
    if environment and environment in system.get("environments", {}):
        system["environments"][environment].setdefault("groups", {})[group_code] = group_cfg
        logger.info(f"Saved group '{group_code}' for system '{system_name}' environment '{environment}'")
    else:
        system.setdefault("groups", {})[group_code] = group_cfg
        logger.info(f"Saved group '{group_code}' for system '{system_name}'")
    config["systems"][system_name] = system
    return save_config(config)

def delete_group(system_name: str, group_code: str, environment: str = None) -> bool:
    from app.config.repository import load_config, save_config
    config = load_config()
    system = config.get("systems", {}).get(system_name)
    if not system:
        return False
    if environment and environment in system.get("environments", {}):
        env_groups = system["environments"][environment].get("groups", {})
        if group_code in env_groups:
            del env_groups[group_code]
            logger.info(f"Deleted group '{group_code}' from system '{system_name}' environment '{environment}'")
            return save_config(config)
    groups = system.get("groups", {})
    if group_code in groups:
        del groups[group_code]
        logger.info(f"Deleted group '{group_code}' from system '{system_name}'")
        return save_config(config)
    return False

def get_all_dovo_regions() -> Dict[str, Any]:
    return get_all_groups("dovo")

def get_dovo_region(region_code: str) -> Optional[Dict[str, Any]]:
    return get_group("dovo", region_code)

def save_dovo_region(region_code: str, region_cfg: Dict[str, Any]) -> bool:
    return save_group("dovo", region_code, region_cfg)

def delete_dovo_region(region_code: str) -> bool:
    return delete_group("dovo", region_code)

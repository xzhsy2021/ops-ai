import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

def get_environments(system_name: str) -> Dict[str, Any]:
    from app.config.systems import get_system_config
    cfg = get_system_config(system_name)
    if not cfg:
        return {}
    return cfg.get("environments", {})

def get_environment(system_name: str, env_name: str) -> Optional[Dict[str, Any]]:
    envs = get_environments(system_name)
    return envs.get(env_name)

def save_environment(system_name: str, env_name: str, env_cfg: Dict[str, Any]) -> bool:
    from app.config.repository import load_config, save_config
    config = load_config()
    system = config.get("systems", {}).get(system_name)
    if not system:
        logger.error(f"System '{system_name}' not found")
        return False
    system.setdefault("environments", {})[env_name] = env_cfg
    config["systems"][system_name] = system
    logger.info(f"Saved environment '{env_name}' for system '{system_name}'")
    return save_config(config)

def delete_environment(system_name: str, env_name: str) -> bool:
    from app.config.repository import load_config, save_config
    config = load_config()
    system = config.get("systems", {}).get(system_name)
    if not system:
        return False
    envs = system.get("environments", {})
    if env_name in envs:
        del envs[env_name]
        logger.info(f"Deleted environment '{env_name}' from system '{system_name}'")
        return save_config(config)
    return False

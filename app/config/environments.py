"""环境配置 — Phase 3e: 读写 System 表的 environments JSON 字段。

每个 system 的环境配置作为 JSON 存储在 systems.environments 字段中，
消除 blob 整体改写的并发问题。
"""
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
    """更新 System 表的 environments JSON 字段。"""
    try:
        from app.db.base import SessionLocal
        from app.db.repository import SystemRepository
        with SessionLocal() as db:
            repo = SystemRepository(db)
            system = repo.get_by_name(system_name)
            if not system:
                logger.error(f"System '{system_name}' not found")
                return False
            envs = dict(system.environments or {})
            envs[env_name] = env_cfg
            system.environments = envs
            repo.update(system)
            logger.info(f"Saved environment '{env_name}' for system '{system_name}'")
        try:
            from app.config.cache import invalidate_config_cache
            invalidate_config_cache()
        except Exception:
            pass
        return True
    except Exception:
        logger.exception(f"Failed to save environment '{env_name}' for system '{system_name}'")
        return False


def delete_environment(system_name: str, env_name: str) -> bool:
    """从 System 表的 environments JSON 字段中删除。"""
    try:
        from app.db.base import SessionLocal
        from app.db.repository import SystemRepository
        with SessionLocal() as db:
            repo = SystemRepository(db)
            system = repo.get_by_name(system_name)
            if not system:
                return False
            envs = dict(system.environments or {})
            if env_name in envs:
                del envs[env_name]
                system.environments = envs
                repo.update(system)
                logger.info(f"Deleted environment '{env_name}' from system '{system_name}'")
                try:
                    from app.config.cache import invalidate_config_cache
                    invalidate_config_cache()
                except Exception:
                    pass
                return True
            return False
    except Exception:
        logger.exception(f"Failed to delete environment '{env_name}' from system '{system_name}'")
        return False

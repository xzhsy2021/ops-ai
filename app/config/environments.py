"""Database-only per-system environment helpers."""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def get_environments(system_name: str) -> Dict[str, Any]:
    from app.config.systems import _environment_row_to_dict
    from app.db.base import SessionLocal
    from app.db.repository import SystemEnvironmentRepository

    with SessionLocal() as db:
        return {
            row.name: _environment_row_to_dict(row)
            for row in SystemEnvironmentRepository(db).list_by_system(system_name)
        }


def get_environment(system_name: str, env_name: str) -> Optional[Dict[str, Any]]:
    envs = get_environments(system_name)
    return envs.get(env_name)


def save_environment(system_name: str, env_name: str, env_cfg: Dict[str, Any]) -> bool:
    """Create or update a system environment row."""
    try:
        from app.db.base import SessionLocal
        from app.db.repository import SystemEnvironmentRepository, SystemRepository
        with SessionLocal() as db:
            system = SystemRepository(db).get_by_name(system_name)
            if not system:
                logger.error(f"System '{system_name}' not found")
                return False
            cfg = env_cfg or {}
            SystemEnvironmentRepository(db).upsert(
                system_name=system_name,
                name=env_name,
                display_name=cfg.get("display_name") or env_name,
                category=cfg.get("category") or "custom",
                description=cfg.get("description") or "",
                base_path=cfg.get("base_path") or cfg.get("deploy_path") or "",
                servers=cfg.get("servers") or [],
                variables=cfg.get("variables") or {},
                service_overrides=cfg.get("service_overrides") or {},
                group_overrides=cfg.get("group_overrides") or {},
            )
            db.commit()
            logger.info(f"Saved environment '{env_name}' for system '{system_name}'")
        return True
    except Exception:
        logger.exception(f"Failed to save environment '{env_name}' for system '{system_name}'")
        return False


def delete_environment(system_name: str, env_name: str) -> bool:
    """Delete a system environment row."""
    try:
        from app.db.base import SessionLocal
        from app.db.repository import SystemEnvironmentRepository
        with SessionLocal() as db:
            repo = SystemEnvironmentRepository(db)
            row = repo.get_by_name(system_name, env_name)
            if row is None:
                return False
            repo.delete(row.id)
            db.commit()
            logger.info(f"Deleted environment '{env_name}' from system '{system_name}'")
            return True
    except Exception:
        logger.exception(f"Failed to delete environment '{env_name}' from system '{system_name}'")
        return False

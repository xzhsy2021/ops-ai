import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InventoryReadService:
    """统一配置读取服务 — 第一阶段只收口读取，不迁移写入。"""

    def list_servers(self) -> List[Dict[str, Any]]:
        from app.config.servers import get_all_servers
        return get_all_servers()

    def get_server(self, name: str) -> Optional[Dict[str, Any]]:
        from app.config.servers import get_server_by_name
        return get_server_by_name(name)

    def list_systems(self) -> Dict[str, Any]:
        from app.config.systems import get_all_systems
        return get_all_systems()

    def get_system(self, name: str) -> Optional[Dict[str, Any]]:
        from app.config.systems import get_system_by_name
        return get_system_by_name(name)

    def resolve_system_config(self, system_name: str, environment: str = None) -> Dict[str, Any]:
        from app.config.systems import resolve_system_config
        return resolve_system_config(system_name, environment=environment)

    def get_service(self, system_name: str, service_name: str, environment: str = "") -> Optional[Dict[str, Any]]:
        if not service_name:
            return None
        try:
            from app.config.systems import resolve_system_config
            sys_cfg = resolve_system_config(system_name, environment=environment)
        except Exception:
            logger.exception("Failed to resolve service config for %s/%s/%s", system_name, service_name, environment)
            sys_cfg = self._load_system_raw(system_name)
        for svc in sys_cfg.get("services", []) or []:
            if isinstance(svc, dict) and self._service_matches(svc, service_name):
                return svc
        return None

    def list_groups(self, system_name: str, environment: str = None) -> Dict[str, Any]:
        from app.config.systems import get_all_groups
        return get_all_groups(system_name, environment)

    def get_group(self, system_name: str, group_code: str, environment: str = None) -> Optional[Dict[str, Any]]:
        from app.config.systems import get_group
        return get_group(system_name, group_code, environment)

    def get_variable_inheritance(self, system_name: str, service_name: str = None, environment: str = None) -> Dict[str, Any]:
        from app.config.systems import get_variable_inheritance
        return get_variable_inheritance(system_name, service_name, environment)

    def get_servers_for_system(self, system_name: str, environment: str = None) -> List[Dict[str, Any]]:
        from app.config.systems import get_servers_for_system
        return get_servers_for_system(system_name, environment)

    def _load_system_raw(self, system_name: str) -> Dict[str, Any]:
        try:
            from app.config.cache import load_config_cached
            return (load_config_cached().get("systems", {}) or {}).get(system_name, {}) or {}
        except Exception:
            logger.exception("Failed to load system config for %s", system_name)
            return {}

    @staticmethod
    def _norm_name(value: Any) -> str:
        return str(value or "").strip().lower().replace("_", "-")

    @classmethod
    def _service_matches(cls, svc: Dict[str, Any], service_name: str) -> bool:
        target = cls._norm_name(service_name)
        if not target:
            return False
        candidates = [
            svc.get("name"),
            svc.get("display_name"),
            (svc.get("template_variables") or {}).get("service_name"),
            (svc.get("template_variables") or {}).get("pm2_name"),
        ]
        for candidate in candidates:
            cn = cls._norm_name(candidate)
            if cn and (cn == target or cn.endswith(f"-{target}") or target.endswith(f"-{cn}")):
                return True
        return False


inventory = InventoryReadService()
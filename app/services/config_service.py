import copy
import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ConfigService:
    """配置服务 — 分离读写和迁移逻辑"""

    def __init__(self):
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_lock = threading.Lock()

    def initialize(self):
        """启动时调用一次：执行所有迁移并初始化缓存"""
        from config_manager import (
            _ensure_defaults, _migrate_json_to_db,
            _migrate_dovo_regions, _ensure_group_field_defaults,
            _ensure_group_servers, save_config, load_config,
        )
        _ensure_defaults()
        _migrate_json_to_db()
        config = load_config()
        config = _migrate_dovo_regions(config)
        _ensure_group_field_defaults(config.get("systems", {}))
        _ensure_group_servers(config)
        save_config(config)
        self._cache = None

    def get_config(self) -> Dict[str, Any]:
        """纯读操作，带缓存"""
        with self._cache_lock:
            if self._cache is not None:
                return copy.deepcopy(self._cache)
        from config_manager import load_config
        config = load_config()
        with self._cache_lock:
            self._cache = config
        return copy.deepcopy(config)

    def save_config(self, config: Dict[str, Any]):
        """写操作，清除缓存"""
        from config_manager import save_config
        save_config(config)
        with self._cache_lock:
            self._cache = None

    def invalidate_cache(self):
        """外部调用：手动清除缓存"""
        with self._cache_lock:
            self._cache = None

    def get_servers(self) -> List[Dict[str, Any]]:
        return self.get_config().get("servers", [])

    def get_server_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        for s in self.get_servers():
            if s.get("name") == name:
                return s
        return None

    def save_server(self, server: Dict[str, Any]) -> bool:
        from config_manager import save_server
        result = save_server(server)
        self.invalidate_cache()
        return result

    def delete_server(self, name: str) -> bool:
        from config_manager import delete_server
        result = delete_server(name)
        self.invalidate_cache()
        return result

    def get_systems(self) -> Dict[str, Any]:
        systems = self.get_config().get("systems", {})
        for sys_cfg in systems.values():
            if "variables" not in sys_cfg:
                sys_cfg["variables"] = {}
        return systems

    def get_system_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        return self.get_systems().get(name)

    def save_system(self, name: str, system: Dict[str, Any]) -> bool:
        from config_manager import save_system
        result = save_system(name, system)
        self.invalidate_cache()
        return result

    def delete_system(self, name: str) -> bool:
        from config_manager import delete_system
        result = delete_system(name)
        self.invalidate_cache()
        return result


config_service = ConfigService()

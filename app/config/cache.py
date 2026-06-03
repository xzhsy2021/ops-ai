import copy
import logging
import threading
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_cache_lock = threading.RLock()
_config_cache: Dict[str, Any] = {}
_config_cache_valid = False
_db_query_cache: Dict[str, tuple] = {}
_CACHE_TTL = 30

def invalidate_config_cache():
    global _config_cache_valid
    with _cache_lock:
        _config_cache_valid = False
        _db_query_cache.clear()

def _cache_get(key: str):
    with _cache_lock:
        entry = _db_query_cache.get(key)
        if entry and time.time() - entry[0] < _CACHE_TTL:
            return copy.deepcopy(entry[1])
        return None

def _cache_set(key: str, value: Any):
    with _cache_lock:
        _db_query_cache[key] = (time.time(), value)

def load_config_cached() -> Dict[str, Any]:
    global _config_cache_valid
    with _cache_lock:
        if _config_cache_valid and _config_cache:
            return copy.deepcopy(_config_cache)
        from app.config.repository import load_config
        cfg = load_config()
        _config_cache.clear()
        _config_cache.update(cfg)
        _config_cache_valid = True
        return cfg

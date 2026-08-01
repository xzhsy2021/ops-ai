"""配置缓存层 — 单层缓存 + TTL 安全网。

设计：
- _config_cache: 进程内全量配置缓存，无 TTL（由 invalidate_config_cache 显式失效）
- TTL 安全网：_CACHE_TTL_SECONDS 防止 invalidate 遗漏调用点时长时间陈旧
- 单进程部署（workers=1），无跨进程一致性问题
"""
import copy
import logging
import threading
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_cache_lock = threading.RLock()
_config_cache: Dict[str, Any] = {}
_config_cache_valid = False
_config_cache_ts = 0.0  # 缓存填充时间戳
_CACHE_TTL_SECONDS = 10  # 安全网 TTL：超过此时间自动失效，防止 invalidate 遗漏


def invalidate_config_cache():
    global _config_cache_valid
    with _cache_lock:
        _config_cache_valid = False
        _config_cache.clear()


def load_config_cached() -> Dict[str, Any]:
    global _config_cache_valid, _config_cache_ts
    with _cache_lock:
        now = time.time()
        # 缓存有效且未超过 TTL 安全网
        if _config_cache_valid and _config_cache and (now - _config_cache_ts) < _CACHE_TTL_SECONDS:
            return copy.deepcopy(_config_cache)
        from app.config.repository import load_config
        cfg = load_config()
        _config_cache.clear()
        _config_cache.update(cfg)
        _config_cache_valid = True
        _config_cache_ts = now
        return cfg

import threading
import time
import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)

_lock_store = {}
_lock_timestamps = {}
_lock_mutex = threading.Lock()
_LOCK_TIMEOUT_SECONDS = 3600


class DeployLock:
    @staticmethod
    def acquire(service_key: str, timeout: int = _LOCK_TIMEOUT_SECONDS) -> bool:
        with _lock_mutex:
            now = time.time()

            to_remove = []
            for k, ts in _lock_timestamps.items():
                if now - ts > _LOCK_TIMEOUT_SECONDS:
                    to_remove.append(k)
            for k in to_remove:
                _lock_store.pop(k, None)
                _lock_timestamps.pop(k, None)
                logger.warning(f"Deploy lock expired and cleaned: {k}")

            if service_key in _lock_store:
                return False
            _lock_store[service_key] = True
            _lock_timestamps[service_key] = now
            logger.info(f"Deploy lock acquired: {service_key}")
            return True

    @staticmethod
    def release(service_key: str):
        with _lock_mutex:
            _lock_store.pop(service_key, None)
            _lock_timestamps.pop(service_key, None)
            logger.info(f"Deploy lock released: {service_key}")

    @staticmethod
    def refresh(service_key: str):
        with _lock_mutex:
            if service_key in _lock_store:
                _lock_timestamps[service_key] = time.time()

    @staticmethod
    def is_locked(service_key: str) -> bool:
        with _lock_mutex:
            if service_key not in _lock_store:
                return False
            if time.time() - _lock_timestamps.get(service_key, 0) > _LOCK_TIMEOUT_SECONDS:
                _lock_store.pop(service_key, None)
                _lock_timestamps.pop(service_key, None)
                return False
            return True

    @staticmethod
    def list_locked() -> Set[str]:
        with _lock_mutex:
            now = time.time()
            to_remove = []
            for k, ts in _lock_timestamps.items():
                if now - ts > _LOCK_TIMEOUT_SECONDS:
                    to_remove.append(k)
            for k in to_remove:
                _lock_store.pop(k, None)
                _lock_timestamps.pop(k, None)
            return set(_lock_store.keys())

    @staticmethod
    def clear_all():
        with _lock_mutex:
            count = len(_lock_store)
            _lock_store.clear()
            _lock_timestamps.clear()
            logger.info(f"All deploy locks cleared ({count} locks)")

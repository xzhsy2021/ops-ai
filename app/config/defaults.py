import json
import logging
import os

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config", "default_config.json")

def _load_default_config() -> dict:
    try:
        with open(_DEFAULT_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("无法加载默认配置文件 %s: %s，使用空配置", _DEFAULT_CONFIG_PATH, e)
        return {"jump_hosts": [], "servers": [], "systems": {}}

DEFAULT_CONFIG = _load_default_config()

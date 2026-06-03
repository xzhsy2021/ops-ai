import logging
import os
from app.core.platform import safe_chmod
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

def _keys_dir() -> str:
    from app.core.config import get_runtime_path
    return get_runtime_path("KEYS_DIR", "keys")

def _validate_key_name(key_name: str) -> str:
    if not key_name:
        raise ValueError("Key name is empty")
    if os.path.sep in key_name or '/' in key_name or '\\' in key_name:
        raise ValueError(f"Invalid key name: {key_name}")
    if key_name.startswith('.') or key_name.startswith('~'):
        raise ValueError(f"Invalid key name: {key_name}")
    keys_dir = _keys_dir()
    resolved = os.path.realpath(os.path.join(keys_dir, key_name))
    if not resolved.startswith(os.path.realpath(keys_dir) + os.path.sep) and resolved != os.path.realpath(keys_dir):
        raise ValueError(f"Key path escapes keys directory: {key_name}")
    return resolved

def save_key_file(key_name: str, key_content: str) -> bool:
    try:
        key_path = _validate_key_name(key_name)
    except ValueError:
        return False
    os.makedirs(_keys_dir(), exist_ok=True)
    try:
        with open(key_path, 'w', encoding='utf-8') as f:
            f.write(key_content)
        safe_chmod(key_path, 0o600)
        return True
    except IOError:
        return False

def get_key_file_path(key_name: str) -> str:
    if not key_name:
        return key_name
    if os.path.isabs(key_name):
        return key_name
    if key_name.startswith('~'):
        return os.path.expanduser(key_name)
    try:
        validated = _validate_key_name(key_name)
        if os.path.exists(validated):
            return validated
    except ValueError:
        pass
    return os.path.join(_keys_dir(), key_name)

def delete_key_file(key_name: str) -> bool:
    try:
        key_path = _validate_key_name(key_name)
    except ValueError:
        return False
    if os.path.exists(key_path):
        try:
            os.remove(key_path)
            return True
        except IOError:
            return False
    return False

def list_key_files() -> List[Dict[str, Any]]:
    keys_dir = _keys_dir()
    if not os.path.exists(keys_dir):
        return []
    result = []
    for filename in os.listdir(keys_dir):
        filepath = os.path.join(keys_dir, filename)
        if os.path.isfile(filepath):
            stat = os.stat(filepath)
            result.append({"name": filename, "size": stat.st_size, "modified": stat.st_mtime})
    return result


def read_key_file(key_name: str) -> str:
    key_path = _validate_key_name(key_name)
    if not os.path.exists(key_path):
        raise FileNotFoundError(key_name)
    with open(key_path, 'r', encoding='utf-8') as f:
        return f.read()

def key_file_exists(key_name: str) -> bool:
    try:
        key_path = _validate_key_name(key_name)
    except ValueError:
        return False
    return os.path.isfile(key_path)

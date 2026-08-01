"""SSH 密钥管理 — DB 优先（加密存储）+ 文件兼容（过渡期双写）。

策略：
- 新密钥同时写入 ssh_keys 表（加密）和 KEYS_DIR 文件（兼容现有路径引用）
- 读取优先从 DB 获取（解密），回退到文件
- 删除同时清理 DB 和文件
- get_key_file_path 保持原行为（支持绝对路径/~/名称），SSH 连接路径不变
- 未来可逐步去掉文件写入，改为临时文件物化
"""
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


def _ssh_key_repo():
    """获取 SshKeyRepository 实例，失败返回 None（DB 不可用时回退到文件）。"""
    try:
        from app.db.base import SessionLocal
        from app.db.repository import SshKeyRepository
        db = SessionLocal()
        return SshKeyRepository(db), db
    except Exception as e:
        logger.debug(f"DB unavailable for SSH key, falling back to file: {e}")
        return None, None


def save_key_file(key_name: str, key_content: str) -> bool:
    """双写：DB 加密存储 + 文件兼容。DB 失败不影响文件写入。"""
    # 写 DB（加密）
    try:
        repo, db = _ssh_key_repo()
        if repo:
            try:
                existing = repo.get_by_name(key_name)
                if existing:
                    repo.update(key_name, private_key=key_content)
                else:
                    repo.create(key_name, private_key=key_content)
            finally:
                db.close()
    except Exception as e:
        logger.warning(f"Failed to save SSH key to DB: {e}")

    # 写文件（兼容）
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
    """保持原行为：支持绝对路径/~/名称。SSH 连接路径不变。"""
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
    """双删：DB + 文件。"""
    # 删 DB
    try:
        repo, db = _ssh_key_repo()
        if repo:
            try:
                repo.delete(key_name)
            finally:
                db.close()
    except Exception as e:
        logger.warning(f"Failed to delete SSH key from DB: {e}")

    # 删文件
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
    """合并 DB 和文件系统的密钥列表。"""
    result = []
    seen_names = set()

    # 先从 DB 获取
    try:
        repo, db = _ssh_key_repo()
        if repo:
            try:
                for row in repo.list_all(limit=500):
                    seen_names.add(row.name)
                    result.append({
                        "name": row.name,
                        "size": len(row.private_key_encrypted or ""),
                        "modified": row.updated_at.timestamp() if row.updated_at else 0,
                        "source": "db",
                    })
            finally:
                db.close()
    except Exception as e:
        logger.debug(f"Failed to list SSH keys from DB: {e}")

    # 合并文件系统的密钥
    keys_dir = _keys_dir()
    if os.path.exists(keys_dir):
        for filename in os.listdir(keys_dir):
            if filename in seen_names:
                continue
            filepath = os.path.join(keys_dir, filename)
            if os.path.isfile(filepath):
                stat = os.stat(filepath)
                result.append({"name": filename, "size": stat.st_size, "modified": stat.st_mtime, "source": "file"})
    return result


def read_key_file(key_name: str) -> str:
    """优先从 DB 读取（解密），回退到文件。"""
    # 先查 DB
    try:
        repo, db = _ssh_key_repo()
        if repo:
            try:
                decrypted = repo.get_decrypted(key_name)
                if decrypted and decrypted.get("private_key"):
                    return decrypted["private_key"]
            finally:
                db.close()
    except Exception as e:
        logger.debug(f"Failed to read SSH key from DB: {e}")

    # 回退到文件
    try:
        key_path = _validate_key_name(key_name)
    except ValueError:
        raise FileNotFoundError(key_name)
    if not os.path.exists(key_path):
        raise FileNotFoundError(key_name)
    with open(key_path, 'r', encoding='utf-8') as f:
        return f.read()


def key_file_exists(key_name: str) -> bool:
    """DB 或文件存在任一即返回 True。"""
    # 先查 DB
    try:
        repo, db = _ssh_key_repo()
        if repo:
            try:
                if repo.get_by_name(key_name):
                    return True
            finally:
                db.close()
    except Exception as e:
        logger.debug(f"Failed to check SSH key in DB: {e}")

    # 回退到文件
    try:
        key_path = _validate_key_name(key_name)
    except ValueError:
        return False
    return os.path.isfile(key_path)

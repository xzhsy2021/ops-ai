import json
import logging
import os
import copy
import sqlite3
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_FILE = os.path.join(_PROJECT_ROOT, "config_data.json")

_save_lock = threading.RLock()
_db_local = threading.local()

def get_db_file() -> str:
    from app.core.config import ensure_runtime_dirs, get_database_path
    ensure_runtime_dirs()
    return os.getenv(
        "OPS_CONFIG_DB",
        get_database_path()
    )

def get_db_connection() -> sqlite3.Connection:
    conn = getattr(_db_local, "conn", None)
    if conn is not None:
        try:
            conn.execute("SELECT 1").fetchone()
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                logger.debug("Failed to close stale DB connection", exc_info=True)
            del _db_local.conn
    conn = sqlite3.connect(get_db_file(), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    conn.execute("PRAGMA temp_store=MEMORY")
    _db_local.conn = conn
    return conn

_get_db = get_db_connection

def reset_db_connection():
    conn = getattr(_db_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            logger.debug("Failed to close DB connection during reset", exc_info=True)
        del _db_local.conn
    from app.config.cache import invalidate_config_cache
    invalidate_config_cache()

def _init_db():
    db_path = get_db_file()
    logger.info(f"Initializing database: {db_path}")
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config_kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deployment_records (
            id TEXT PRIMARY KEY,
            system TEXT NOT NULL,
            server TEXT NOT NULL,
            strategy TEXT NOT NULL DEFAULT 'DIRECT',
            status TEXT NOT NULL DEFAULT 'running',
            steps TEXT DEFAULT '[]',
            output TEXT DEFAULT '',
            message TEXT DEFAULT '',
            started_at TEXT,
            finished_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deploy_logs_old (
            id TEXT PRIMARY KEY,
            system TEXT NOT NULL,
            server TEXT NOT NULL,
            strategy TEXT NOT NULL DEFAULT 'DIRECT',
            status TEXT NOT NULL DEFAULT 'running',
            steps TEXT DEFAULT '[]',
            output TEXT DEFAULT '',
            message TEXT DEFAULT '',
            started_at TEXT,
            finished_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deploy_locks (
            lock_key TEXT PRIMARY KEY,
            locked_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_name TEXT,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deployment_records_system ON deployment_records(system, started_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deployment_records_status ON deployment_records(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_system ON deploy_logs_old(system, started_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_status ON deploy_logs_old(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC)")
    conn.commit()
    logger.info("Database tables ensured")
    from app.config.migration import _migrate_legacy_default_config_to_db, _migrate_json_to_db
    if os.getenv("OPS_SKIP_CONFIG_AUTO_MIGRATION") != "1":
        _migrate_legacy_default_config_to_db(remove_source=True)
        _migrate_json_to_db()

def load_config() -> Dict[str, Any]:
    conn = get_db_connection()
    result = {}
    try:
        rows = conn.execute("SELECT key, value FROM config_kv").fetchall()
    except sqlite3.OperationalError:
        logger.exception("Failed to load config from config_kv")
        raise
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Invalid config json for key=%s: %s", row["key"], e)
            from app.config.defaults import DEFAULT_CONFIG
            result[row["key"]] = copy.deepcopy(DEFAULT_CONFIG.get(row["key"], {}))
    from app.config.defaults import DEFAULT_CONFIG
    for key in DEFAULT_CONFIG:
        if key not in result:
            result[key] = copy.deepcopy(DEFAULT_CONFIG[key])
    from app.config.migration import _migrate_dovo_regions
    result = _migrate_dovo_regions(result)
    return result

def save_config(config: Dict[str, Any]) -> bool:
    # Phase 3d SSOT guard: 'systems' key is read-only legacy data after
    # Phase 3a/3b/3c. Detect & warn when callers attempt to mutate it.
    if "systems" in config:
        try:
            current_systems = _load_config_unsafe().get("systems", {})
        except Exception:
            current_systems = {}
        if config.get("systems") != current_systems:
            import traceback
            logger.warning(
                "Phase 3d: 'systems' key in config is read-only legacy. "
                "Diff detected (callers should migrate to DB tables). "
                "Stack trace:\n%s",
                "".join(traceback.format_stack(limit=6)),
            )
    # Phase 3.g SSOT guard: 'jump_hosts' key in config is read-only legacy
    # after the JumpHost table became SSOT. Detect & warn when callers attempt
    # to mutate it; CRUD must go through JumpHostRepository / /api/v2/jump-hosts.
    if "jump_hosts" in config:
        try:
            current_jh = _load_config_unsafe().get("jump_hosts", [])
        except Exception:
            current_jh = []
        if config.get("jump_hosts") != current_jh:
            import traceback
            logger.warning(
                "Phase 3.g: 'jump_hosts' key in config is read-only legacy. "
                "Diff detected (callers should use JumpHostRepository). "
                "Stack trace:\n%s",
                "".join(traceback.format_stack(limit=6)),
            )
    with _save_lock:
        # Re-load latest version and merge to avoid lost-update from concurrent writes
        current = _load_config_unsafe()
        merged = {**current, **config}
        return _save_config_unsafe(merged)


def _load_config_unsafe() -> Dict[str, Any]:
    conn = get_db_connection()
    result = {}
    try:
        rows = conn.execute("SELECT key, value FROM config_kv").fetchall()
    except sqlite3.OperationalError:
        logger.exception("Failed to load config from config_kv")
        raise
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Invalid config json for key=%s: %s", row["key"], e)
            from app.config.defaults import DEFAULT_CONFIG
            result[row["key"]] = copy.deepcopy(DEFAULT_CONFIG.get(row["key"], {}))
    from app.config.defaults import DEFAULT_CONFIG
    for key in DEFAULT_CONFIG:
        if key not in result:
            result[key] = copy.deepcopy(DEFAULT_CONFIG[key])
    from app.config.migration import _migrate_dovo_regions
    result = _migrate_dovo_regions(result)
    return result


def _save_config_unsafe(config: Dict[str, Any]) -> bool:
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing_keys = {row[0] for row in conn.execute("SELECT key FROM config_kv").fetchall()}
        new_keys = set(config.keys())
        for key in new_keys - existing_keys:
            conn.execute(
                "INSERT INTO config_kv (key, value) VALUES (?, ?)",
                (key, json.dumps(config[key], ensure_ascii=False))
            )
        for key in new_keys & existing_keys:
            conn.execute(
                "UPDATE config_kv SET value = ? WHERE key = ?",
                (json.dumps(config[key], ensure_ascii=False), key)
            )
        for key in existing_keys - new_keys:
            conn.execute("DELETE FROM config_kv WHERE key = ?", (key,))
        conn.commit()
        from app.config.cache import invalidate_config_cache
        invalidate_config_cache()
        logger.debug("Saved config with %d keys", len(config))
        return True
    except Exception as e:
        logger.error("Failed to save config: %s", e)
        try:
            conn.rollback()
        except Exception:
            logger.warning("Failed to rollback after save_config failure", exc_info=True)
        try:
            from app.config.cache import invalidate_config_cache
            invalidate_config_cache()
        except Exception:
            pass
        return False

import json
import logging
import os
import copy
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_DEFAULT_CONFIG_FILE = _PROJECT_ROOT / "config" / "default_config.json"


def _json_dumps(value):
    return json.dumps(value, ensure_ascii=False)


def _safe_backup_dir() -> Path:
    try:
        from app.core.config import get_runtime_path, ensure_runtime_dirs
        ensure_runtime_dirs()
        base = Path(get_runtime_path("BACKUP_DIR", "backups"))
    except Exception:
        base = _PROJECT_ROOT / "data" / "backups"
    path = base / "config_migrations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _config_kv_key_count(conn) -> int:
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM config_kv").fetchone()
        return int(row["c"] if hasattr(row, "keys") else row[0])
    except Exception:
        return 0


def _upsert_config_values(conn, data: dict, *, overwrite: bool) -> list[str]:
    changed = []
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            continue
        exists = conn.execute("SELECT 1 FROM config_kv WHERE key = ?", (key,)).fetchone() is not None
        if exists and not overwrite:
            continue
        if exists:
            conn.execute(
                "UPDATE config_kv SET value = ? WHERE key = ?",
                (_json_dumps(value), key),
            )
        else:
            conn.execute(
                "INSERT INTO config_kv (key, value) VALUES (?, ?)",
                (key, _json_dumps(value)),
            )
        changed.append(key)
    return changed


def _move_legacy_default_config(path: Path, *, suffix: str = "migrated") -> Path | None:
    if not path.exists():
        return None
    backup_dir = _safe_backup_dir()
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    target = backup_dir / f"default_config.{suffix}.{ts}.json"
    try:
        shutil.move(str(path), str(target))
        logger.info("Legacy default_config.json moved to %s", target)
        return target
    except Exception:
        logger.exception("Failed to move legacy default_config.json to backup directory")
        return None


def _migrate_legacy_default_config_to_db(*, remove_source: bool = True, overwrite: bool | None = None) -> list[str]:
    """Import legacy config/default_config.json into config_kv once.

    Rules:
    - If the database has no config rows, import every key from the file.
    - If the database already has runtime config, keep DB values and only fill
      missing keys from the file to avoid overwriting user changes.
    - After a successful read attempt, move the legacy file out of config/ so the
      application no longer saves or relies on file-based default config.
    """
    path = LEGACY_DEFAULT_CONFIG_FILE
    if not path.exists():
        return []
    try:
        legacy = _load_json_file(path)
    except Exception:
        logger.exception("Failed to read legacy default config file: %s", path)
        return []

    from app.config.repository import get_db_connection
    conn = get_db_connection()
    try:
        existing_count = _config_kv_key_count(conn)
        should_overwrite = existing_count == 0 if overwrite is None else bool(overwrite)
        changed = _upsert_config_values(conn, legacy, overwrite=should_overwrite)
        conn.commit()
        logger.info(
            "Migrated legacy default_config.json to config_kv: keys=%s mode=%s",
            changed,
            "overwrite" if should_overwrite else "insert-missing-only",
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("Failed to migrate legacy default_config.json into DB")
        return []

    if remove_source:
        _move_legacy_default_config(path)
    return changed


def _migrate_json_to_db():
    from app.config.repository import get_db_connection, CONFIG_FILE
    if not os.path.exists(CONFIG_FILE):
        logger.info("No config_data.json found, skipping migration")
        return
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            old_data = json.load(f)
        conn = get_db_connection()
        migrated_keys = []
        for key, value in old_data.items():
            conn.execute(
                "INSERT OR REPLACE INTO config_kv (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False))
            )
            migrated_keys.append(key)
        conn.commit()
        backup_path = CONFIG_FILE + ".bak"
        if os.path.exists(backup_path):
            os.remove(backup_path)
        os.rename(CONFIG_FILE, backup_path)
        logger.info(f"Migrated {len(migrated_keys)} keys from JSON to DB: {migrated_keys}")
        logger.info(f"JSON file renamed to {backup_path}")
    except Exception as e:
        logger.error(f"Failed to migrate JSON to DB: {e}")


def _ensure_defaults():
    """启动期补齐 DEFAULT_CONFIG 中缺失的 key 到 config_kv。

    Sentinel 策略：用 `__seeded` 标记 key 判断 DB 是否已初始化，
    不再依赖业务 key（如 jump_hosts）的存在性 —— 后者已迁移到 DB 表，
    KV 中的 legacy 桶可被安全清理而不会触发误判。
    """
    from app.config.repository import get_db_connection
    from app.config.defaults import DEFAULT_CONFIG
    _migrate_legacy_default_config_to_db(remove_source=True)
    conn = get_db_connection()
    seeded = conn.execute("SELECT 1 FROM config_kv WHERE key = '__seeded'").fetchone()
    if seeded is None:
        logger.info("No data in DB, inserting DEFAULT_CONFIG seed")
        for key, value in DEFAULT_CONFIG.items():
            conn.execute(
                "INSERT OR IGNORE INTO config_kv (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False))
            )
        conn.execute(
            "INSERT OR IGNORE INTO config_kv (key, value) VALUES (?, ?)",
            ("__seeded", json.dumps({"v": 1}, ensure_ascii=False))
        )
        conn.commit()
        logger.info(f"Inserted {len(DEFAULT_CONFIG)} default config keys into DB")
    else:
        missing_keys = [k for k in DEFAULT_CONFIG if conn.execute(
            "SELECT 1 FROM config_kv WHERE key = ?", (k,)
        ).fetchone() is None]
        if missing_keys:
            logger.info(f"Inserting missing default keys: {missing_keys}")
            for key in missing_keys:
                conn.execute(
                    "INSERT OR IGNORE INTO config_kv (key, value) VALUES (?, ?)",
                    (key, json.dumps(DEFAULT_CONFIG[key], ensure_ascii=False))
                )
            conn.commit()


def _migrate_dovo_regions(config: dict) -> dict:
    systems = config.get("systems", {})
    old_regions = {}
    for name in ["dovo-idn", "dovo-pak", "dovo-tha", "dovo-bgd"]:
        if name in systems:
            old_regions[name] = systems.pop(name)
    if "dovo" not in systems:
        systems["dovo"] = {
            "display_name": "Dovo",
            "strategy": "DOVO",
            "description": "Dovo 多区域部署系统",
            "groups": {},
        }
    dovo = systems.get("dovo", {})
    if "regions" in dovo:
        dovo["groups"] = dovo.pop("regions")
        for code, cfg in dovo.get("groups", {}).items():
            if "display_name" not in cfg:
                cfg["display_name"] = code.upper()
        systems["dovo"] = dovo
    if old_regions:
        groups = systems["dovo"].setdefault("groups", {})
        for old_name, old_cfg in old_regions.items():
            group_code = old_name.replace("dovo-", "")
            if group_code not in groups:
                groups[group_code] = {
                    "display_name": group_code.upper(),
                    "server": old_cfg.get("servers", [""])[0] if old_cfg.get("servers") else "",
                    "base_path": old_cfg.get("base_path", "/data/bin/ata"),
                    "instances": old_cfg.get("instances", [f"{group_code}1", f"{group_code}2"]),
                    "global_config_path": old_cfg.get("global_config_path", f"/data/bin/ata/config/global.yml"),
                }
    _ensure_group_field_defaults(systems)
    _ensure_group_servers(config)
    config["systems"] = systems
    return config


def _ensure_group_field_defaults(systems: dict):
    FIELD_DEFAULTS = {
        "update_script": "bash ./binupdate.sh",
        "switch_script": "bash ./portupdate.sh",
        "wait_after_update": 10,
    }
    for sys_name, sys_cfg in systems.items():
        groups = sys_cfg.get("groups", {})
        if not groups:
            continue
        for code, gcfg in groups.items():
            if "display_name" not in gcfg:
                gcfg["display_name"] = code.upper()
            for field, default_val in FIELD_DEFAULTS.items():
                if field not in gcfg:
                    gcfg[field] = default_val


def _ensure_group_servers(config: dict):
    SERVER_FIXUPS = {
        "dovo": {
            "pak": "pak-tecx",
            "tha": "tha-aliyun",
            "idn": "idn-1xbet",
            "bgd": "bgd-server",
        },
    }
    servers = config.get("servers", [])
    for sys_name, fixups in SERVER_FIXUPS.items():
        sys_cfg = config.get("systems", {}).get(sys_name, {})
        groups = sys_cfg.get("groups", {})
        for gcode, correct_server in fixups.items():
            if gcode in groups:
                current = groups[gcode].get("server", "")
                if not current or current == "prod-1":
                    groups[gcode]["server"] = correct_server
    config["servers"] = servers


def _apply_migrations_and_save():
    """Phase 3e: 已退役，dovo 区域数据由 ServerGroup 表管理，systems 数据由 systems 表管理。"""
    _ensure_defaults()
    _migrate_json_to_db()

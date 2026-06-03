import json
import logging
import os
import copy

logger = logging.getLogger(__name__)

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
    from app.config.repository import get_db_connection
    from app.config.defaults import DEFAULT_CONFIG
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM config_kv WHERE key = 'jump_hosts'").fetchone()
    if row is None:
        logger.info("No data in DB, inserting DEFAULT_CONFIG")
        for key, value in DEFAULT_CONFIG.items():
            conn.execute(
                "INSERT OR IGNORE INTO config_kv (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False))
            )
        conn.commit()
        logger.info(f"Inserted {len(DEFAULT_CONFIG)} default config keys")
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
    _ensure_defaults()
    _migrate_json_to_db()
    from app.config.repository import load_config, save_config
    config = load_config()
    config = _migrate_dovo_regions(config)
    _ensure_group_field_defaults(config.get("systems", {}))
    _ensure_group_servers(config)
    save_config(config)

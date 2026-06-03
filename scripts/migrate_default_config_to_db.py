#!/usr/bin/env python3
"""Synchronize legacy/default configuration into OPS database config_kv.

Default behavior is safe: import config/default_config.json only when present and
only fill missing keys if DB already has runtime values. Use --overwrite when you
intentionally want the source config to replace existing DB values.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _count_rows(db_path: str) -> int:
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS config_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = conn.execute("SELECT COUNT(*) FROM config_kv").fetchone()
            return int(row[0] if row else 0)
        finally:
            conn.close()
    except Exception:
        return 0


def _upsert_seed(*, overwrite: bool) -> list[str]:
    from app.config.repository import get_db_connection
    from app.config.defaults import DEFAULT_CONFIG

    conn = get_db_connection()
    changed: list[str] = []
    for key, value in DEFAULT_CONFIG.items():
        exists = conn.execute("SELECT 1 FROM config_kv WHERE key = ?", (key,)).fetchone() is not None
        if exists and not overwrite:
            continue
        data = json.dumps(value, ensure_ascii=False)
        if exists:
            conn.execute("UPDATE config_kv SET value = ? WHERE key = ?", (data, key))
        else:
            conn.execute("INSERT INTO config_kv (key, value) VALUES (?, ?)", (key, data))
        changed.append(key)
    conn.commit()
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync default configuration into database config_kv")
    parser.add_argument("--keep-source", action="store_true", help="Import but do not move config/default_config.json")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing DB config values from source/default seed")
    parser.add_argument("--seed", action="store_true", help="Also sync built-in default seed when legacy JSON is absent")
    parser.add_argument("--source", default="", help="Optional explicit JSON source path")
    parser.add_argument("--no-assets", action="store_true", help="Do not synchronize config inventory into normalized asset tables such as servers/services")
    args = parser.parse_args()

    # Prevent repository._init_db() from moving the legacy JSON before this script
    # can apply --overwrite/--keep-source semantics and print correct diagnostics.
    os.environ["OPS_SKIP_CONFIG_AUTO_MIGRATION"] = "1"

    from app.core.config import ensure_runtime_dirs, get_database_path
    from app.config.repository import _init_db, load_config, reset_db_connection, get_db_connection
    from app.db import init_db, SessionLocal
    from app.config.migration import _migrate_legacy_default_config_to_db, LEGACY_DEFAULT_CONFIG_FILE, _upsert_config_values, _load_json_file

    ensure_runtime_dirs()
    db_path = get_database_path()
    before_rows = _count_rows(db_path)
    default_source = Path(args.source).resolve() if args.source else LEGACY_DEFAULT_CONFIG_FILE
    source_before = default_source.exists()

    _init_db()

    changed: list[str] = []
    if args.source:
        if not default_source.exists():
            print(f"source_missing={default_source}")
        else:
            conn = get_db_connection()
            data = _load_json_file(default_source)
            changed = _upsert_config_values(conn, data, overwrite=args.overwrite or _count_rows(db_path) == 0)
            conn.commit()
    elif LEGACY_DEFAULT_CONFIG_FILE.exists():
        changed = _migrate_legacy_default_config_to_db(
            remove_source=not args.keep_source,
            overwrite=True if args.overwrite else None,
        )
    elif args.seed or before_rows == 0:
        changed = _upsert_seed(overwrite=args.overwrite or before_rows == 0)

    reset_db_connection()
    cfg = load_config()

    asset_sync = None
    if not args.no_assets:
        init_db()
        session = SessionLocal()
        try:
            from app.maintenance.config_asset_sync import sync_config_assets_to_db
            asset_sync = sync_config_assets_to_db(session, config=cfg, overwrite=args.overwrite)
        finally:
            session.close()

    after_rows = _count_rows(db_path)

    print(f"database={db_path}")
    print(f"source={default_source}")
    print(f"source_before={source_before}")
    print(f"source_after={default_source.exists()}")
    print(f"rows_before={before_rows}")
    print(f"rows_after={after_rows}")
    print(f"changed_keys={sorted(changed)}")
    print(f"config_keys={sorted(cfg.keys())}")
    print(f"servers={len(cfg.get('servers', []))} systems={len(cfg.get('systems', {}))}")
    if asset_sync is not None:
        print("asset_sync=" + json.dumps(asset_sync, ensure_ascii=False, sort_keys=True))

    if not changed:
        print("note=没有写入新数据。常见原因：源文件不存在、数据库已有同名配置且未使用 --overwrite、或当前命令连接的数据库不是后端正在使用的数据库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

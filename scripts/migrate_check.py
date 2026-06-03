#!/usr/bin/env python3
"""Offline upgrade sanity check for local OPS installs."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

REQUIRED_DIRS = ["uploads", "keys", "backups", "logs", "runtime"]
REQUIRED_TABLE_HINTS = ["users", "deployments", "deploy_tasks", "audit_records"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-data-dir", default=os.getenv("APP_DATA_DIR") or str(Path.cwd() / "data"))
    parser.add_argument("--db-path", default=os.getenv("OPS_DB_PATH") or "")
    args = parser.parse_args()
    data = Path(args.app_data_dir).resolve()
    db_path = Path(args.db_path).resolve() if args.db_path else data / "ops.db"
    failures: list[str] = []

    print(f"[check] APP_DATA_DIR={data}")
    if not data.exists():
        print("[WARN] data directory does not exist; first startup will create it")
    else:
        for name in REQUIRED_DIRS:
            p = data / name
            if not p.exists():
                print(f"[WARN] missing optional runtime dir: {p}")
        try:
            probe = data / ".ops_migrate_check"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            print("[OK] APP_DATA_DIR writable")
        except Exception as exc:
            failures.append(f"APP_DATA_DIR not writable: {exc}")

    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("SELECT 1").fetchone()
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                print(f"[OK] SQLite readable: {db_path}")
                missing = [t for t in REQUIRED_TABLE_HINTS if t not in tables]
                if missing:
                    print(f"[WARN] expected tables not found: {', '.join(missing)}")
            finally:
                conn.close()
        except Exception as exc:
            failures.append(f"SQLite check failed: {exc}")
    else:
        print(f"[WARN] database not found yet: {db_path}")

    if failures:
        for item in failures:
            print(f"[FAIL] {item}")
        return 1
    print("migrate_check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

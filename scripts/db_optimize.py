#!/usr/bin/env python3
"""Lightweight SQLite optimizer for local-team installs.

Usage:
  python scripts/db_optimize.py --check
  python scripts/db_optimize.py --analyze
  python scripts/db_optimize.py --vacuum
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ops.db"

INDEXES = [
    ("idx_deployments_system_status_started", "deployments", "system, status, started_at"),
    ("idx_deployments_env_status_started", "deployments", "environment, status, started_at"),
    ("idx_deploy_tasks_status_created", "deploy_tasks", "status, created_at"),
    ("idx_deploy_logs_task_created", "deploy_logs", "task_id, created_at"),
    ("idx_deploy_logs_deployment_created", "deploy_logs", "deployment_id, created_at"),
    ("idx_tool_call_logs_created", "tool_call_logs", "created_at"),
    ("idx_tool_call_logs_tool_created", "tool_call_logs", "tool_name, created_at"),
    ("idx_tool_plans_created", "tool_plans", "created_at"),
    ("idx_tool_plans_system_env", "tool_plans", "system, environment"),
    ("idx_audit_records_action_created", "audit_records", "action, created_at"),
    ("idx_sql_query_history_conn_created", "sql_query_history", "connection_id, created_at"),
    ("idx_cleanup_jobs_status_created", "cleanup_jobs", "status, created_at"),
]


def db_path() -> Path:
    return Path(os.getenv("DATABASE_PATH") or os.getenv("OPS_DB_PATH") or DEFAULT_DB).resolve()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def index_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone())


def ensure_indexes(conn: sqlite3.Connection, *, execute: bool) -> list[str]:
    actions: list[str] = []
    for name, table, columns in INDEXES:
        if not table_exists(conn, table):
            actions.append(f"skip missing table: {table}")
            continue
        if index_exists(conn, name):
            actions.append(f"ok index: {name}")
            continue
        sql = f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"
        actions.append(("create" if execute else "would create") + f" index: {name}")
        if execute:
            conn.execute(sql)
    if execute:
        conn.commit()
    return actions


def table_counts(conn: sqlite3.Connection) -> Iterable[tuple[str, int]]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    for (name,) in rows:
        try:
            yield name, int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] or 0)
        except sqlite3.Error:
            yield name, -1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="show missing indexes and table counts; no writes")
    parser.add_argument("--apply-indexes", action="store_true", help="create recommended indexes")
    parser.add_argument("--analyze", action="store_true", help="run SQLite ANALYZE")
    parser.add_argument("--vacuum", action="store_true", help="run SQLite VACUUM")
    parser.add_argument("--report-json", action="store_true", help="print a JSON summary after checks")
    args = parser.parse_args()

    path = db_path()
    print(f"database: {path}")
    if not path.exists():
        print("database file does not exist; nothing to optimize")
        return 0

    conn = sqlite3.connect(str(path))
    try:
        print(f"size_bytes: {path.stat().st_size}")
        actions = ensure_indexes(conn, execute=args.apply_indexes)
        for action in actions:
            print(action)
        if args.analyze:
            conn.execute("ANALYZE")
            conn.commit()
            print("ANALYZE completed")
        if args.vacuum:
            conn.execute("VACUUM")
            print("VACUUM completed")
        counts = list(table_counts(conn))
        print("table_counts:")
        for name, count in counts:
            print(f"  {name}: {count}")
        missing_indexes = [a.split(": ", 1)[1] for a in actions if a.startswith("would create index") or a.startswith("create index")]
        if args.report_json:
            print(json.dumps({
                "database": str(path),
                "size_bytes": path.stat().st_size,
                "missing_indexes": missing_indexes,
                "missing_index_count": len(missing_indexes),
                "table_counts": dict(counts),
            }, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

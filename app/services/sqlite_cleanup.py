from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_app_data_dir
from app.db import SessionLocal

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = {
    "tool_call_logs": 30,
    "deploy_logs": 90,
    "audit_records": 180,
    "command_execution_logs": 30,
}

TABLE_COLUMNS = {
    "tool_call_logs": [
        "id", "tool_name", "client_name", "token_id", "token_owner",
        "username", "input_args", "normalized_args", "result_preview",
        "status", "risk_level", "policy_result", "blocked_reason",
        "related_plan_id", "related_deployment_id", "related_job_id",
        "ip_address", "user_agent", "duration_ms", "created_at",
    ],
    "deploy_logs": [
        "id", "task_id", "deployment_id", "step_name", "level", "message", "created_at",
    ],
    "audit_records": [
        "id", "action", "target_type", "target_name", "details", "created_at",
    ],
    "command_execution_logs": [
        "id", "server_name", "username", "command", "exit_code",
        "stdout_preview", "stderr_preview", "duration_ms",
        "risk_level", "hop_context", "auth_mode", "is_terminal",
        "session_id", "created_at",
    ],
}


def _archive_dir() -> Path:
    p = Path(get_app_data_dir()) / "archive"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _archive_table_to_jsonl_gz(
    table_name: str,
    rows: List[Dict[str, Any]],
    cutoff: datetime,
) -> Optional[str]:
    if not rows:
        return None
    month_key = cutoff.strftime("%Y%m")
    archive_path = _archive_dir() / f"{table_name}-{month_key}.jsonl.gz"
    existing: List[Dict[str, Any]] = []
    if archive_path.exists():
        try:
            with gzip.open(archive_path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        existing.append(json.loads(line))
        except Exception:
            logger.warning("Corrupt archive %s, overwriting", archive_path)
            existing = []
    existing_ids = {r.get("id") for r in existing}
    new_rows = [r for r in rows if r.get("id") not in existing_ids]
    if not new_rows:
        return None
    with gzip.open(archive_path, "at", encoding="utf-8") as f:
        for row in new_rows:
            f.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
    logger.info("Archived %d rows to %s", len(new_rows), archive_path)
    return str(archive_path)


class LogRetentionPolicy:
    def __init__(
        self,
        retention_days: Optional[Dict[str, int]] = None,
        archive: bool = True,
    ):
        self.retention_days = {**DEFAULT_RETENTION_DAYS, **(retention_days or {})}
        self.archive = archive

    def preview(self, db: Session) -> Dict[str, Any]:
        result: Dict[str, Any] = {"tables": {}, "total_rows": 0, "estimated_bytes_freed": 0}
        for table_name, days in self.retention_days.items():
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            try:
                row = db.execute(
                    text(f"SELECT COUNT(*) as cnt FROM {table_name} WHERE created_at < :cutoff"),
                    {"cutoff": cutoff},
                ).first()
                count = row.cnt if row else 0
                total_row = db.execute(text(f"SELECT COUNT(*) as cnt FROM {table_name}")).first()
                total = total_row.cnt if total_row else 0
                avg_bytes = 0
                if total > 0:
                    page_row = db.execute(text(f"SELECT SUM(pgsize) as sz FROM dbstat WHERE name = :t"), {"t": table_name}).first()
                    if page_row and page_row.sz:
                        avg_bytes = max(1, page_row.sz // max(total, 1))
                estimated_bytes = count * avg_bytes
                result["tables"][table_name] = {
                    "retention_days": days,
                    "cutoff": cutoff,
                    "rows_to_delete": count,
                    "total_rows": total,
                    "estimated_bytes_freed": estimated_bytes,
                    "archive": self.archive,
                }
                result["total_rows"] += count
                result["estimated_bytes_freed"] += estimated_bytes
            except Exception as e:
                logger.warning("preview failed for %s: %s", table_name, e)
                result["tables"][table_name] = {"error": str(e)}
        return result

    def execute(self, db: Session, dry_run: bool = False) -> Dict[str, Any]:
        result: Dict[str, Any] = {"tables": {}, "archived": {}, "dry_run": dry_run}
        for table_name, days in self.retention_days.items():
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            try:
                from sqlalchemy import inspect as sa_inspect
                insp = sa_inspect(db.get_bind())
                if not insp.has_table(table_name):
                    result["tables"][table_name] = {"deleted": 0, "archived_to": None, "skipped": True, "reason": "table not found"}
                    continue
                actual_cols = [c["name"] for c in insp.get_columns(table_name)]
                if "created_at" not in actual_cols:
                    result["tables"][table_name] = {"deleted": 0, "archived_to": None, "skipped": True, "reason": "no created_at column"}
                    continue
                desired_cols = TABLE_COLUMNS.get(table_name, actual_cols)
                cols = [c for c in desired_cols if c in actual_cols]
                if not cols:
                    cols = actual_cols
                col_list = ", ".join(cols)
                rows_result = db.execute(
                    text(f"SELECT {col_list} FROM {table_name} WHERE created_at < :cutoff"),
                    {"cutoff": cutoff},
                )
                rows = [dict(row._mapping) for row in rows_result]
                count = len(rows)
                if count == 0:
                    result["tables"][table_name] = {"deleted": 0, "archived_to": None}
                    continue
                archive_path = None
                if self.archive and not dry_run:
                    archive_path = _archive_table_to_jsonl_gz(table_name, rows, datetime.now(timezone.utc) - timedelta(days=days))
                if not dry_run:
                    db.execute(
                        text(f"DELETE FROM {table_name} WHERE created_at < :cutoff"),
                        {"cutoff": cutoff},
                    )
                    db.commit()
                result["tables"][table_name] = {
                    "deleted": count,
                    "archived_to": archive_path,
                }
                result["archived"][table_name] = archive_path
            except Exception as e:
                db.rollback()
                logger.exception("retention execute failed for %s", table_name)
                result["tables"][table_name] = {"error": str(e)}
        return result


def run_cleanup() -> None:
    db: Session = SessionLocal()
    try:
        policy = LogRetentionPolicy(archive=True)
        result = policy.execute(db, dry_run=False)
        deleted_total = sum(t.get("deleted", 0) for t in result["tables"].values())
        logger.info("SQLite cleanup completed, %d rows deleted", deleted_total)
    except Exception:
        logger.exception("SQLite cleanup failed")
    finally:
        db.close()


def run_cleanup_legacy() -> None:
    db: Session = SessionLocal()
    try:
        _cleanup_deploy_logs(db)
        _cleanup_tool_call_logs(db)
        _cleanup_command_logs(db)
        db.commit()
        logger.info("SQLite cleanup completed")
    except Exception:
        db.rollback()
        logger.exception("SQLite cleanup failed")
    finally:
        db.close()


def _cleanup_deploy_logs(db: Session) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    deleted = db.execute(
        text("DELETE FROM deploy_logs WHERE created_at < :cutoff"),
        {"cutoff": cutoff},
    ).rowcount
    if deleted:
        logger.info("Cleaned %d deploy_logs older than %s", deleted, cutoff)


def _cleanup_tool_call_logs(db: Session) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    deleted = db.execute(
        text("DELETE FROM tool_call_logs WHERE created_at < :cutoff"),
        {"cutoff": cutoff},
    ).rowcount
    if deleted:
        logger.info("Cleaned %d tool_call_logs older than %s", deleted, cutoff)

    row = db.execute(text("SELECT COUNT(*) as cnt FROM tool_call_logs")).first()
    total = row.cnt if row else 0
    if total > 10000:
        excess = total - 10000
        db.execute(
            text("DELETE FROM tool_call_logs WHERE id IN (SELECT id FROM tool_call_logs ORDER BY created_at ASC LIMIT :excess)"),
            {"excess": excess},
        )
        logger.info("Trimmed %d tool_call_logs (total %d → kept ~10000)", excess, total)


def _cleanup_command_logs(db: Session) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    deleted = db.execute(
        text("DELETE FROM command_execution_logs WHERE created_at < :cutoff"),
        {"cutoff": cutoff},
    ).rowcount
    if deleted:
        logger.info("Cleaned %d command_execution_logs older than %s", deleted, cutoff)

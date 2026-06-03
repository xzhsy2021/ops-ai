import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

def save_deploy_log(log_dict: Dict[str, Any]) -> bool:
    try:
        from app.config.repository import get_db_connection
        conn = get_db_connection()
        conn.execute(
            """INSERT OR REPLACE INTO deployment_records
               (id, system, server, strategy, status, steps, output, message, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_dict.get("id", ""),
                log_dict.get("system", ""),
                log_dict.get("server", ""),
                log_dict.get("strategy", "DIRECT"),
                log_dict.get("status", "running"),
                json.dumps(log_dict.get("steps", []), ensure_ascii=False),
                log_dict.get("output", ""),
                log_dict.get("message", ""),
                log_dict.get("started_at", ""),
                log_dict.get("finished_at", ""),
            )
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to save deploy log: {e}")
        return False

def load_deploy_logs(limit: int = 1000) -> List[Dict[str, Any]]:
    try:
        from app.config.repository import get_db_connection
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM deployment_records ORDER BY started_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["steps"] = json.loads(d.get("steps", "[]"))
            except (json.JSONDecodeError, TypeError):
                d["steps"] = []
            result.append(d)
        return result
    except Exception as e:
        logger.error(f"Failed to load deploy logs: {e}")
        return []

def load_deploy_log_by_id(log_id: str) -> Optional[Dict[str, Any]]:
    try:
        from app.config.repository import get_db_connection
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM deployment_records WHERE id = ?", (log_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["steps"] = json.loads(d.get("steps", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["steps"] = []
        return d
    except Exception as e:
        logger.error(f"Failed to load deploy log {log_id}: {e}")
        return None

def cleanup_deploy_logs(max_age_hours: int = 168):
    try:
        from app.config.repository import get_db_connection
        conn = get_db_connection()
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        conn.execute("DELETE FROM deploy_logs_old WHERE finished_at < ?", (cutoff,))
        conn.commit()
    except Exception as e:
        logger.debug(f"Cleanup deploy logs skipped: {e}")

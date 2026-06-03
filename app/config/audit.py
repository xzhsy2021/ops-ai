import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

def save_audit_log(action: str, target_type: str = None, target_name: str = None, details: str = None) -> bool:
    try:
        from app.config.repository import get_db_connection
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO audit_logs (action, target_type, target_name, details) VALUES (?, ?, ?, ?)",
            (action, target_type, target_name, details)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to save audit log: {e}")
        return False

def load_audit_logs(limit: int = 1000) -> List[Dict[str, Any]]:
    try:
        from app.config.repository import get_db_connection
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to load audit logs: {e}")
        return []

def cleanup_audit_logs(max_age_hours: int = 720):
    try:
        from datetime import datetime, timedelta
        from app.config.repository import get_db_connection
        conn = get_db_connection()
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        conn.execute("DELETE FROM audit_logs WHERE created_at < ?", (cutoff,))
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to cleanup audit logs: {e}")

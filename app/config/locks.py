import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_EXPIRY_SECONDS = 3600

def acquire_db_lock(lock_key: str) -> bool:
    from app.config.repository import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = datetime.now().isoformat()
        conn.execute("DELETE FROM deploy_locks WHERE expires_at < ?", (now,))
        existing = conn.execute("SELECT 1 FROM deploy_locks WHERE lock_key = ?", (lock_key,)).fetchone()
        if existing:
            conn.rollback()
            return False
        expiry = (datetime.now() + timedelta(seconds=LOCK_EXPIRY_SECONDS)).isoformat()
        conn.execute(
            "INSERT INTO deploy_locks (lock_key, locked_at, expires_at) VALUES (?, ?, ?)",
            (lock_key, now, expiry)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to acquire lock {lock_key}: {e}")
        try:
            conn.rollback()
        except Exception:
            logger.warning("Failed to rollback after lock acquisition failure", exc_info=True)
        return False

def release_db_lock(lock_key: str) -> bool:
    from app.config.repository import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM deploy_locks WHERE lock_key = ?", (lock_key,))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to release lock {lock_key}: {e}")
        return False

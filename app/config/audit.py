"""审计日志 — SSOT: ORM audit_records 表。

原 audit_logs 原生表已废弃，由 schema migration 081_002 DROP。
所有读写统一走 ORM AuditRecord 模型。
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def save_audit_log(action: str, target_type: str = None, target_name: str = None, details: str = None) -> bool:
    try:
        from app.db.base import SessionLocal
        from app.db.models import AuditRecord
        with SessionLocal() as db:
            record = AuditRecord(
                action=action,
                target_type=target_type,
                target_name=target_name,
                details=details,
                created_at=datetime.now().isoformat(),
            )
            db.add(record)
            db.commit()
            return True
    except Exception as e:
        logger.error(f"Failed to save audit log: {e}")
        return False


def load_audit_logs(limit: int = 1000) -> List[Dict[str, Any]]:
    try:
        from app.db.base import SessionLocal
        from app.db.models import AuditRecord
        with SessionLocal() as db:
            rows = db.query(AuditRecord).order_by(AuditRecord.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": row.id,
                    "action": row.action,
                    "target_type": row.target_type,
                    "target_name": row.target_name,
                    "details": row.details,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Failed to load audit logs: {e}")
        return []


def cleanup_audit_logs(max_age_hours: int = 720):
    try:
        from app.db.base import SessionLocal
        from app.db.models import AuditRecord
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        with SessionLocal() as db:
            db.query(AuditRecord).filter(AuditRecord.created_at < cutoff).delete(synchronize_session=False)
            db.commit()
    except Exception as e:
        logger.error(f"Failed to cleanup audit logs: {e}")

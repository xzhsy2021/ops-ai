from __future__ import annotations

from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.deploy_lock import DeployLock
from app.db.models import DeploymentLockRecord
from app.deploy.schemas import DeployRequest

logger = logging.getLogger(__name__)


def deployment_lock_keys(req: DeployRequest) -> List[str]:
    base = f"{req.system}:{req.service or 'default'}:{req.environment or 'default'}"
    keys = [base]
    keys.extend(f"{base}:server:{server}" for server in (req.servers or []))
    return keys


def acquire_deployment_locks(req: DeployRequest, deployment_id: str, task_id: str, owner: str, db: Session) -> List[str]:
    keys = deployment_lock_keys(req)
    acquired: List[str] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for key in keys:
        if not DeployLock.acquire(key):
            release_deployment_locks(acquired, db)
            raise HTTPException(status_code=409, detail=f"发布锁冲突: {key}")
        acquired.append(key)
        try:
            record = db.query(DeploymentLockRecord).filter(DeploymentLockRecord.lock_key == key).first()
            if record:
                record.deployment_id = deployment_id
                record.task_id = task_id
                record.owner = owner
                record.status = "locked"
                record.updated_at = now
                record.expires_at = now + timedelta(hours=1)
            else:
                db.add(DeploymentLockRecord(
                    lock_key=key,
                    deployment_id=deployment_id,
                    task_id=task_id,
                    owner=owner,
                    status="locked",
                    created_at=now,
                    updated_at=now,
                    expires_at=now + timedelta(hours=1),
                ))
            db.commit()
        except Exception:
            logger.warning("Failed to persist deployment lock %s", key, exc_info=True)
    return acquired


def release_deployment_locks(lock_keys: List[str], db: Session) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for key in lock_keys or []:
        try:
            DeployLock.release(key)
            record = db.query(DeploymentLockRecord).filter(DeploymentLockRecord.lock_key == key).first()
            if record:
                record.status = "released"
                record.updated_at = now
            db.commit()
        except Exception:
            logger.warning("Failed to release deployment lock %s", key, exc_info=True)


def list_deployment_locks_payload(db: Session) -> List[Dict[str, Any]]:
    memory_keys = set(DeployLock.list_locked())
    records = db.query(DeploymentLockRecord).filter(DeploymentLockRecord.status == "locked").all()
    data: List[Dict[str, Any]] = []
    seen = set()
    for row in records:
        seen.add(row.lock_key)
        data.append({
            "lock_key": row.lock_key,
            "source": "db+memory" if row.lock_key in memory_keys else "db",
            "deployment_id": row.deployment_id,
            "task_id": row.task_id,
            "owner": row.owner,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        })
    for key in sorted(memory_keys - seen):
        data.append({"lock_key": key, "source": "memory", "deployment_id": "", "task_id": "", "owner": ""})
    return data

"""命令执行历史记录服务"""
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from app.db.models import CommandExecutionLog

logger = logging.getLogger(__name__)

_PREVIEW_LENGTH = 2000


def record_execution(
    db: Session,
    server_name: str,
    username: str,
    command: str,
    exit_code: int = None,
    stdout: str = None,
    stderr: str = None,
    duration_ms: int = None,
    risk_level: str = "safe",
    hop_context: Dict = None,
    auth_mode: str = None,
    is_terminal: bool = False,
    session_id: str = None,
) -> CommandExecutionLog:
    entry = CommandExecutionLog(
        server_name=server_name,
        username=username,
        command=command[:10000],
        exit_code=exit_code,
        stdout_preview=(stdout or "")[:_PREVIEW_LENGTH],
        stderr_preview=(stderr or "")[:_PREVIEW_LENGTH],
        duration_ms=duration_ms,
        risk_level=risk_level,
        hop_context=hop_context,
        auth_mode=auth_mode,
        is_terminal=is_terminal,
        session_id=session_id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def query_executions(
    db: Session,
    server_name: str = None,
    username: str = None,
    risk_level: str = None,
    limit: int = 50,
    offset: int = 0,
) -> List[CommandExecutionLog]:
    q = db.query(CommandExecutionLog)
    if server_name:
        q = q.filter(CommandExecutionLog.server_name == server_name)
    if username:
        q = q.filter(CommandExecutionLog.username == username)
    if risk_level:
        q = q.filter(CommandExecutionLog.risk_level == risk_level)
    q = q.order_by(CommandExecutionLog.created_at.desc())
    return q.offset(offset).limit(limit).all()


def get_execution(db: Session, log_id: str) -> Optional[CommandExecutionLog]:
    return db.query(CommandExecutionLog).filter(CommandExecutionLog.id == log_id).first()


def delete_execution(db: Session, log_id: str) -> Dict[str, Any]:
    result = delete_executions(db, [log_id])
    return {"id": log_id, "deleted": bool(result.get("deleted")), **result}


def delete_executions(db: Session, log_ids: List[str]) -> Dict[str, Any]:
    ids: List[str] = []
    seen = set()
    for raw in log_ids or []:
        log_id = str(raw or "").strip()
        if log_id and log_id not in seen:
            seen.add(log_id)
            ids.append(log_id)
    if not ids:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="log_ids is required")
    if len(ids) > 200:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Cannot delete more than 200 command executions at once")
    rows = db.query(CommandExecutionLog).filter(CommandExecutionLog.id.in_(ids)).all()
    by_id = {row.id: row for row in rows}
    missing = [log_id for log_id in ids if log_id not in by_id]
    if missing:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Execution log not found: {', '.join(missing[:5])}")
    for log_id in ids:
        db.delete(by_id[log_id])
    db.commit()
    return {
        "log_ids": ids,
        "deleted": len(ids),
    }


def count_executions(db: Session, server_name: str = None, username: str = None, risk_level: str = None) -> int:
    q = db.query(CommandExecutionLog)
    if server_name:
        q = q.filter(CommandExecutionLog.server_name == server_name)
    if username:
        q = q.filter(CommandExecutionLog.username == username)
    if risk_level:
        q = q.filter(CommandExecutionLog.risk_level == risk_level)
    return q.count()


def log_to_dict(entry: CommandExecutionLog) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "server_name": entry.server_name,
        "username": entry.username,
        "command": entry.command,
        "exit_code": entry.exit_code,
        "stdout_preview": entry.stdout_preview,
        "stderr_preview": entry.stderr_preview,
        "duration_ms": entry.duration_ms,
        "risk_level": entry.risk_level,
        "hop_context": entry.hop_context,
        "auth_mode": entry.auth_mode,
        "is_terminal": entry.is_terminal,
        "session_id": entry.session_id,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ToolToken

TOKEN_PREFIX = "ops_tool_"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_tool_token(
    db: Session,
    *,
    name: str,
    owner: str,
    scopes: List[str] | None = None,
    allow_write: bool = False,
    allow_prod: bool = False,
    expires_in_days: int = 90,
) -> Dict[str, Any]:
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ToolToken(
        name=name.strip() or "tool-token",
        token_hash=hash_token(raw),
        token_prefix=raw[:18],
        owner=owner,
        scopes=scopes or ["ops:read"],
        allow_write=bool(allow_write),
        allow_prod=bool(allow_prod),
        expires_at=_utcnow() + timedelta(days=max(1, int(expires_in_days or 90))),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return {"token": raw, "record": token}


def get_token_record(db: Session, raw_token: str) -> Optional[ToolToken]:
    if not raw_token:
        return None
    return db.query(ToolToken).filter(ToolToken.token_hash == hash_token(raw_token)).first()


def validate_tool_token(db: Session, raw_token: str) -> ToolToken:
    record = get_token_record(db, raw_token)
    if not record:
        raise HTTPException(status_code=401, detail="Invalid tool token")
    if record.revoked_at:
        raise HTTPException(status_code=401, detail="Tool token was revoked")
    if record.expires_at and record.expires_at < _utcnow():
        raise HTTPException(status_code=401, detail="Tool token expired")
    record.last_used_at = _utcnow()
    db.commit()
    return record


def token_to_dict(token: ToolToken, include_hash: bool = False) -> Dict[str, Any]:
    data = {
        "id": token.id,
        "name": token.name,
        "owner": token.owner,
        "scopes": token.scopes or [],
        "allow_write": token.allow_write,
        "allow_prod": token.allow_prod,
        "token_prefix": token.token_prefix,
        "created_at": token.created_at.isoformat() if token.created_at else None,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
    }
    if include_hash:
        data["token_hash"] = token.token_hash
    return data

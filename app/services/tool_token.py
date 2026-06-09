from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ToolToken

TOKEN_PREFIX = "ops_tool_"

# Recommended default scopes for an MCP/AI client that needs to do
# inspection (Path A) and ad-hoc single-shot probes (Path B). Both
# `ops.inspection.run_*` and `ops.check_disk` / `ops.check_process` /
# `ops.run_health_check` only require `ops:read` + `ops:write`, so this
# pair is the "just works" minimum for routine ops work.
DEFAULT_AI_TOKEN_SCOPES: List[str] = ["ops:read", "ops:write"]

# Scopes / categories that imply the token must be allowed to call
# write-side tools. We use this to keep `allow_write` consistent with the
# caller-declared scopes so a token scoped to "ops:write" doesn't end up
# silently read-only (which is the most common 403 cause we saw during
# the AI/MCP rollout).
WRITE_SCOPE_TOKENS = {"ops:write", "deploy:execute", "config:write", "server:write",
                      "package:write", "package:cleanup", "db:write", "*"}


def recommended_tool_token_templates() -> List[Dict[str, Any]]:
    """Return the canonical token templates used by UI/docs/AI onboarding."""
    return [
        {
            "key": "readonly-ai",
            "name": "Readonly AI",
            "description": "Read-only diagnostics, server inventory, reports, audit and risk context.",
            "scopes": ["ops:read", "audit:read", "server:read"],
            "allow_write": False,
            "allow_prod": False,
            "expires_in_days": 90,
            "notes": "Use for analysis-only MCP clients. Path A inspection execution is blocked.",
        },
        {
            "key": "inspection-ai",
            "name": "Inspection AI",
            "description": "Routine MCP inspection assistant with Path A run tools and read evidence tools.",
            "scopes": ["ops:read", "ops:write", "server:read", "audit:read"],
            "allow_write": True,
            "allow_prod": False,
            "expires_in_days": 90,
            "notes": "Path A inspection run tools still require exact confirm_text for execution.",
        },
        {
            "key": "operator-human",
            "name": "Operator Human",
            "description": "Human-operated token for planning, precheck and queued operational actions.",
            "scopes": ["ops:read", "ops:write", "server:read", "audit:read", "deploy:plan", "deploy:precheck"],
            "allow_write": True,
            "allow_prod": False,
            "expires_in_days": 30,
            "notes": "Does not include deploy:execute; use task center and human approval for high-risk work.",
        },
        {
            "key": "admin-breakglass",
            "name": "Admin Breakglass",
            "description": "Short-lived emergency token for admin-only operations.",
            "scopes": ["*"],
            "allow_write": True,
            "allow_prod": True,
            "expires_in_days": 7,
            "notes": "Use only for emergency windows; revoke immediately after use.",
        },
    ]


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _resolve_write_flag(scopes: List[str], allow_write: Optional[bool]) -> bool:
    """If the caller didn't pick a value, derive allow_write from scopes.

    This avoids the "scopes say write, but allow_write is False → 403" trap
    that bit us when AI/MCP tokens were first issued with the old default
    of `allow_write=False` while the workflow prompt asked for inspection
    (which is internally write=True because it creates inspection_runs).
    """
    if allow_write is None:
        return any(s in WRITE_SCOPE_TOKENS or s.endswith(":*") for s in (scopes or []))
    return bool(allow_write)


def _resolve_expires_at(expires_in_days: Optional[int]) -> Optional[datetime]:
    if expires_in_days is None:
        days = 90
    else:
        days = int(expires_in_days)
    if days <= 0:
        return None
    return _utcnow() + timedelta(days=min(max(days, 1), 3650))


def create_tool_token(
    db: Session,
    *,
    name: str,
    owner: str,
    description: str = "",
    scopes: List[str] | None = None,
    allow_write: Optional[bool] = None,
    allow_prod: bool = False,
    expires_in_days: Optional[int] = 90,
) -> Dict[str, Any]:
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    resolved_scopes = list(scopes) if scopes else list(DEFAULT_AI_TOKEN_SCOPES)
    resolved_allow_write = _resolve_write_flag(resolved_scopes, allow_write)
    token = ToolToken(
        name=name.strip() or "tool-token",
        token_hash=hash_token(raw),
        token_prefix=raw[:18],
        owner=owner,
        description=(description or "").strip()[:500],
        scopes=resolved_scopes,
        allow_write=resolved_allow_write,
        allow_prod=bool(allow_prod),
        expires_at=_resolve_expires_at(expires_in_days),
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
    now = _utcnow()
    if token.revoked_at:
        status = "revoked"
    elif token.expires_at and token.expires_at < now:
        status = "expired"
    else:
        status = "active"
    data = {
        "id": token.id,
        "name": token.name,
        "owner": token.owner,
        "description": token.description if isinstance(getattr(token, "description", ""), str) else "",
        "scopes": token.scopes or [],
        "allow_write": bool(token.allow_write),
        "allow_prod": bool(token.allow_prod),
        "status": status,
        "token_prefix": token.token_prefix,
        "created_at": token.created_at.isoformat() if token.created_at else None,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
    }
    if include_hash:
        data["token_hash"] = token.token_hash
    return data

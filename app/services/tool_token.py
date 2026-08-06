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
            "key": "claw-mcp",
            "name": "Claw Element MCP",
            "description": "通用 claw Element room integration token。Routes messages, prepares/approves/executes high-risk actions via human-approved short codes. Actual deploy/rollback/DML/cleanup runs through internal ApprovalExecutor, NOT the caller's scopes.",
            "scopes": ["ops:read"],
            "allow_write": False,
            "allow_prod": False,
            "expires_in_days": 30,
            "notes": "通用 claw 接入模板。Deliberately narrow: NO deploy:execute / package:write / package:cleanup / db:write / wildcard. Security gate is the one-time approval short_code (15min, room+event+content bound).",
        },
        {
            "key": "qclaw-mcp",
            "name": "qclaw Element MCP",
            "description": "qclaw Element room integration token (legacy key, use claw-mcp for new integrations). Routes messages, prepares/approves/executes high-risk actions via human-approved short codes. Actual deploy/rollback/DML/cleanup runs through internal ApprovalExecutor, NOT the caller's scopes.",
            "scopes": ["ops:read"],
            "allow_write": False,
            "allow_prod": False,
            "expires_in_days": 30,
            "notes": "Deliberately narrow: NO deploy:execute / package:write / package:cleanup / db:write / wildcard. Security gate is the one-time approval short_code (15min, room+event+content bound). See docs/qclaw-element-approval-integration.md.",
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


def _normalize_approver_ids(value: Any) -> List[str]:
    """Coerce arbitrary user input into a clean list[str] of Matrix user IDs.

    Mirrors _normalize_bound_room_ids: empty/None -> [], strings are split
    on newlines, list members must be strings, duplicates removed while
    preserving order. Any other type -> [] (not crash).
    """
    if not value:
        return []
    if isinstance(value, str):
        candidates = [p for p in value.splitlines() if p.strip()]
    elif isinstance(value, (list, tuple, set)):
        candidates = [p for p in value if isinstance(p, str)]
    else:
        return []
    seen: set[str] = set()
    result: List[str] = []
    for raw in candidates:
        user_id = raw.strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        result.append(user_id)
    return result


def _normalize_bound_room_ids(value: Any) -> List[str]:
    """Coerce arbitrary user input into a clean list[str] of Matrix room IDs.

    Empty / None / all-blank input returns an empty list (= no binding).
    Each entry is stripped; duplicates are removed while preserving order.
    Non-string items in list input are dropped (we never auto-stringify
    integers or None into bogus "123" / "None" room IDs).

    Accepted inputs:
    - None / [] / "" / "   " -> []
    - str -> one room ID per non-blank line. Comma is intentionally NOT
      treated as a separator because Matrix room IDs are themselves allowed
      to contain commas (and the canonical form `!opaque:server` never
      uses one in practice, so this is a safe simplification that mirrors
      the way the Web UI's chip editor works).
    - list / tuple / set of strings -> each non-blank string kept; non-
      string items (e.g. accidental ints) are dropped silently.
    - any other type -> [] (not crash).
    """
    if not value:
        return []
    if isinstance(value, str):
        # Newline-separated only. Tabs/spaces around lines are stripped.
        candidates = [p for p in value.splitlines() if p.strip()]
    elif isinstance(value, (list, tuple, set)):
        # Only accept string members; drop everything else.
        candidates = [p for p in value if isinstance(p, str)]
    else:
        return []
    seen: set[str] = set()
    result: List[str] = []
    for raw in candidates:
        room_id = raw.strip()
        if not room_id or room_id in seen:
            continue
        seen.add(room_id)
        result.append(room_id)
    return result


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
    bound_room_ids: Optional[List[str]] = None,
    approver_matrix_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    resolved_scopes = list(scopes) if scopes else list(DEFAULT_AI_TOKEN_SCOPES)
    resolved_allow_write = _resolve_write_flag(resolved_scopes, allow_write)
    resolved_bound_rooms = _normalize_bound_room_ids(bound_room_ids)
    resolved_approvers = _normalize_approver_ids(approver_matrix_ids)
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
        bound_room_ids=resolved_bound_rooms,
        approver_matrix_ids=resolved_approvers,
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


def enforce_room_binding(bound_room_ids: Any, room_id: str | None) -> None:
    """Reject the request if the caller's room is not on the allow list.

    Used by qclaw routing/approval tools to enforce Element room binding at
    the MCP layer. Only applies when the token was issued with a non-empty
    list of room IDs; an empty list (or None) means "no binding, allow any
    room" for backward compatibility with pre-binding tokens.

    Raises HTTPException(403) when the binding is configured and the call's
    room_id is missing or not on the list.
    """
    rooms = _normalize_bound_room_ids(bound_room_ids)
    if not rooms:
        return  # No binding configured -> pass through.
    if not room_id:
        raise HTTPException(
            status_code=403,
            detail="Tool token has room binding configured but request has no room_id",
        )
    if str(room_id).strip() not in rooms:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Tool token not allowed in room {room_id!r}; "
                f"allowed rooms: {','.join(rooms)}"
            ),
        )


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
        "bound_room_ids": _normalize_bound_room_ids(getattr(token, "bound_room_ids", None)),
        "approver_matrix_ids": _normalize_approver_ids(getattr(token, "approver_matrix_ids", None)),
    }
    if include_hash:
        data["token_hash"] = token.token_hash
    return data

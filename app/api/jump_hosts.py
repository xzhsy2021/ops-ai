"""Phase 3.g: 跳板机 (bastion / jump host) CRUD API.

Endpoints (all behind auth):
    GET    /api/v2/jump-hosts             list all
    GET    /api/v2/jump-hosts/{name}      fetch one
    POST   /api/v2/jump-hosts             create
    PUT    /api/v2/jump-hosts/{name}      update
    DELETE /api/v2/jump-hosts/{name}      delete (refuses if any server references it)

The `servers.jump_host` column continues to hold a *name* reference; the
actual host/port/user/key/password is resolved on demand by
`get_jump_host_by_name()` from this table.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.helpers import api_response
from app.core.auth_v2 import require_auth
from app.db import get_db
from app.db.repository import JumpHostRepository


jump_hosts_v2_router = APIRouter(prefix="/api/v2/jump-hosts", tags=["跳板机"])


# ─── Pydantic payloads ───────────────────────────────────────────────────────


class CreateJumpHostPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    host: str = Field(..., min_length=1)
    port: int = 22
    user: str = "root"
    key: str = "~/.ssh/id_rsa"
    key_content: Optional[str] = None
    password: Optional[str] = None
    status: str = "online"
    description: Optional[str] = None
    tags: List[str] = []


class UpdateJumpHostPayload(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    key: Optional[str] = None
    key_content: Optional[str] = None
    password: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None


# ─── Response helpers ────────────────────────────────────────────────────────


def _to_response(jh) -> dict:
    """ORM row → browser-safe dict. Secret values are never echoed; boolean
    markers only. Use the management pages (not this API) to read actual
    credentials back from the operator.
    """
    return {
        "id": jh.id,
        "name": jh.name,
        "host": jh.host,
        "port": jh.port,
        "user": jh.user,
        "key": jh.key,
        "key_content": None,                 # never sent to browser
        "password": None,                    # never sent to browser
        "has_password": bool(jh.password),
        "has_key_content": bool(jh.key_content),
        "status": jh.status or "online",
        "description": jh.description,
        "tags": jh.tags or [],
        "created_at": str(jh.created_at) if jh.created_at else None,
        "updated_at": str(jh.updated_at) if jh.updated_at else None,
    }


# ─── Routes ──────────────────────────────────────────────────────────────────


@jump_hosts_v2_router.get("")
def list_jump_hosts(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = JumpHostRepository(db)
    return api_response(data=[_to_response(jh) for jh in repo.list_all()])


@jump_hosts_v2_router.get("/{name}")
def get_jump_host(request: Request, name: str, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = JumpHostRepository(db)
    jh = repo.get_by_name(name)
    if jh is None:
        raise HTTPException(status_code=404, detail="Jump host not found")
    return api_response(data=_to_response(jh))


@jump_hosts_v2_router.post("")
def create_jump_host(request: Request, payload: CreateJumpHostPayload, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = JumpHostRepository(db)
    if repo.get_by_name(payload.name):
        raise HTTPException(status_code=409, detail="Jump host name already exists")
    jh = repo.create(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        user=payload.user,
        key=payload.key,
        key_content=payload.key_content,
        password=payload.password,
        status=payload.status,
        description=payload.description,
        tags=payload.tags,
    )
    return api_response(data={"id": jh.id, "name": jh.name}, message="Jump host created")


@jump_hosts_v2_router.put("/{name}")
def update_jump_host(request: Request, name: str, payload: UpdateJumpHostPayload, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = JumpHostRepository(db)
    existing = repo.get_by_name(name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Jump host not found")
    fields = payload.model_dump(exclude_unset=True)
    jh = repo.update(existing.id, **fields)
    if jh is None:
        raise HTTPException(status_code=404, detail="Jump host not found")
    return api_response(data={"id": jh.id, "name": jh.name}, message="Jump host updated")


@jump_hosts_v2_router.delete("/{name}")
def delete_jump_host(request: Request, name: str, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = JumpHostRepository(db)
    existing = repo.get_by_name(name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Jump host not found")
    n_refs = repo.count_referencing_servers(name)
    if n_refs > 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Jump host {name!r} is referenced by {n_refs} server(s); "
                "switch those servers to a different jump host before deletion."
            ),
        )
    repo.delete(existing.id)
    return api_response(message="Jump host deleted")

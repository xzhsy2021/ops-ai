from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_db, ServerGroupRepository
from app.core.auth_v2 import require_auth
from app.api.helpers import api_response

groups_v2_router = APIRouter(prefix="/api/v2/groups", tags=["服务器分组"])


class CreateGroupPayload(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    server_names: List[str] = []
    tags: List[str] = []


class UpdateGroupPayload(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    server_names: Optional[List[str]] = None
    tags: Optional[List[str]] = None


@groups_v2_router.get("")
def list_groups(request: Request, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = ServerGroupRepository(db)
    return api_response(data=[{
        "id": g.id, "name": g.name, "display_name": g.display_name,
        "description": g.description, "server_names": g.server_names or [],
        "tags": g.tags or [], "created_at": str(g.created_at) if g.created_at else None,
    } for g in repo.list_all()])


@groups_v2_router.get("/{group_id}")
def get_group(request: Request, group_id: str, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = ServerGroupRepository(db)
    g = repo.get_by_id(group_id) or repo.get_by_name(group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    return api_response(data={
        "id": g.id, "name": g.name, "display_name": g.display_name,
        "description": g.description, "server_names": g.server_names or [],
        "tags": g.tags or [], "created_at": str(g.created_at) if g.created_at else None,
    })


@groups_v2_router.post("")
def create_group(request: Request, payload: CreateGroupPayload, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = ServerGroupRepository(db)
    if repo.get_by_name(payload.name):
        raise HTTPException(status_code=409, detail="Group name already exists")
    g = repo.create(
        name=payload.name,
        display_name=payload.display_name or payload.name,
        description=payload.description,
        server_names=payload.server_names,
        tags=payload.tags,
    )
    return api_response(data={"id": g.id, "name": g.name}, message="Group created")


@groups_v2_router.put("/{group_id}")
def update_group(request: Request, group_id: str, payload: UpdateGroupPayload, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = ServerGroupRepository(db)
    g = repo.get_by_id(group_id) or repo.get_by_name(group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    if payload.display_name is not None:
        g.display_name = payload.display_name
    if payload.description is not None:
        g.description = payload.description
    if payload.server_names is not None:
        g.server_names = payload.server_names
    if payload.tags is not None:
        g.tags = payload.tags
    repo.update(g)
    return api_response(data={"id": g.id, "name": g.name}, message="Group updated")


@groups_v2_router.delete("/{group_id}")
def delete_group(request: Request, group_id: str, db: Session = Depends(get_db)):
    require_auth(request, db)
    repo = ServerGroupRepository(db)
    if not repo.delete(group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    return api_response(message="Group deleted")
"""权限系统 API v2"""
import os
from fastapi import APIRouter, Request, Response, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.helpers import api_response
from app.db import get_db, UserRepository
from app.core.auth_v2 import (
    hash_password,
    verify_password,
    create_session_token,
    verify_session_token,
    get_current_user,
    require_auth,
    require_admin,
    init_default_user,
)

auth_v2_router = APIRouter(prefix="/api/v2/auth", tags=["权限v2"])


class LoginPayload(BaseModel):
    username: str
    password: str


class CreateUserPayload(BaseModel):
    username: str
    password: str
    role: str = "developer"
    is_admin: bool = False
    can_deploy: bool = True


class ResetPasswordPayload(BaseModel):
    password: str


@auth_v2_router.post("/setup")
def setup_auth(db: Session = Depends(get_db)):
    init_default_user(db)
    return api_response(message="Setup complete")


@auth_v2_router.post("/login")
def login(payload: LoginPayload, response: Response, db: Session = Depends(get_db)):
    repo = UserRepository(db)
    user = repo.get_by_username(payload.username)
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_session_token(user.username, getattr(user, "session_version", 1) or 1)
    is_prod = os.getenv("ENV", os.getenv("APP_ENV", "development")).lower() in {"prod", "production"}
    response.set_cookie(
        key="ops_session_v2",
        value=token,
        httponly=True,
        secure=is_prod,
        max_age=86400,
        path="/",
        samesite="lax",
    )
    return api_response(data={
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_admin": user.is_admin,
        "can_deploy": user.can_deploy,
    }, message="Login successful")


@auth_v2_router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key="ops_session_v2", path="/")
    return api_response(message="Logged out")


@auth_v2_router.get("/me")
def get_me(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return api_response(data=user)


@auth_v2_router.get("/users")
def list_users(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    repo = UserRepository(db)
    users = repo.list_all()
    return api_response(data=[
        {"id": u.id, "username": u.username, "role": u.role, "is_admin": u.is_admin, "can_deploy": u.can_deploy}
        for u in users
    ])


@auth_v2_router.post("/users")
def create_user(request: Request, payload: CreateUserPayload, db: Session = Depends(get_db)):
    require_admin(request, db)
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    repo = UserRepository(db)
    if repo.get_by_username(payload.username):
        raise HTTPException(status_code=409, detail="Username already exists")
    user = repo.create(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_admin=payload.is_admin,
        can_deploy=payload.can_deploy,
    )
    return api_response(data={"id": user.id, "username": user.username}, message="User created")


@auth_v2_router.delete("/users/{user_id}")
def delete_user(request: Request, user_id: str, db: Session = Depends(get_db)):
    require_admin(request, db)
    repo = UserRepository(db)
    if not repo.delete(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return api_response(message="User deleted")


@auth_v2_router.put("/users/{user_id}/password")
def reset_user_password(request: Request, user_id: str, payload: ResetPasswordPayload, db: Session = Depends(get_db)):
    require_admin(request, db)
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    repo = UserRepository(db)
    user = repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(payload.password)
    user.session_version = int(getattr(user, "session_version", 1) or 1) + 1
    repo.update(user)
    return api_response(message="Password updated")

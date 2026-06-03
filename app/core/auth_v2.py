import os
import uuid
import hashlib
import hmac
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException
from app.db import get_db, UserRepository
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    if os.getenv("ENV") == "production":
        raise RuntimeError("SESSION_SECRET is required in production")
    SESSION_SECRET = secrets.token_hex(32)
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}${hashed.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    if "$" not in password_hash:
        return False
    salt, stored_hash = password_hash.split("$", 1)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return secrets.compare_digest(hashed.hex(), stored_hash)


def _sign_payload(payload: str) -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_session_token(username: str, session_version: int = 1) -> str:
    """Create a signed session token.

    Format is kept pipe-delimited for frontend/backward compatibility, but the
    signature is now a full HMAC-SHA256 instead of a truncated hash.
    """
    payload = f"{username}|{int(session_version or 1)}|{datetime.now(timezone.utc).isoformat()}|{secrets.token_urlsafe(24)}"
    sig = _sign_payload(payload)
    return f"{payload}|{sig}"


def verify_session_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        parts = token.rsplit("|", 1)
        if len(parts) != 2:
            return None
        payload, sig = parts
        expected = _sign_payload(payload)
        if not hmac.compare_digest(sig, expected):
            return None
        payload_parts = payload.split("|")
        if len(payload_parts) == 3:
            username, ts_str, _nonce = payload_parts
            session_version = 1
        elif len(payload_parts) == 4:
            username, version_str, ts_str, _nonce = payload_parts
            session_version = int(version_str or 1)
        else:
            return None
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - ts > timedelta(hours=SESSION_TTL_HOURS):
            return None
        return {"username": username, "session_version": session_version}
    except Exception:
        return None


def get_current_user(request: Request, db: Session) -> Optional[Dict[str, Any]]:
    token = request.cookies.get("ops_session_v2")
    if not token:
        return None
    token_data = verify_session_token(token)
    if not token_data:
        return None
    username = str(token_data.get("username") or "")
    token_session_version = int(token_data.get("session_version") or 1)
    repo = UserRepository(db)
    user = repo.get_by_username(username)
    if not user:
        return None
    if int(getattr(user, "session_version", 1) or 1) != token_session_version:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_admin": user.is_admin,
        "can_deploy": user.can_deploy,
    }


def require_auth(request: Request, db: Session) -> Dict[str, Any]:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_admin(request: Request, db: Session) -> Dict[str, Any]:
    user = require_auth(request, db)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin permission required")
    return user


ROLE_ORDER = {"readonly": 0, "viewer": 0, "developer": 1, "operator": 1, "admin": 2}

def normalize_role(role: str | None, is_admin: bool = False) -> str:
    if is_admin:
        return "admin"
    value = (role or "readonly").strip().lower()
    if value == "viewer":
        return "readonly"
    if value == "developer":
        return "operator"
    if value not in ROLE_ORDER:
        return "readonly"
    return value

def require_operator(request: Request, db: Session) -> Dict[str, Any]:
    user = require_auth(request, db)
    role = normalize_role(user.get("role"), bool(user.get("is_admin")))
    if ROLE_ORDER.get(role, 0) < ROLE_ORDER["operator"]:
        raise HTTPException(status_code=403, detail="Operator permission required")
    return user

def require_role(request: Request, db: Session, min_role: str = "readonly") -> Dict[str, Any]:
    user = require_auth(request, db)
    role = normalize_role(user.get("role"), bool(user.get("is_admin")))
    if ROLE_ORDER.get(role, 0) < ROLE_ORDER.get(min_role, 0):
        raise HTTPException(status_code=403, detail=f"{min_role} permission required")
    return user


def require_deploy(request: Request, db: Session, environment: str = "") -> Dict[str, Any]:
    user = require_auth(request, db)
    if not user.get("can_deploy") and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Deploy permission required")
    if environment and environment.lower() in ("prod", "production"):
        if not user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Only admin can deploy to production")
    return user


def require_deploy_for_env(user: Dict[str, Any], environment: str):
    role = normalize_role(user.get("role"), bool(user.get("is_admin")))
    is_admin = user.get("is_admin", False)
    can_deploy = user.get("can_deploy", False)

    if role == "readonly":
        raise HTTPException(status_code=403, detail="Readonly role cannot deploy")

    if is_admin:
        return user

    if not can_deploy:
        raise HTTPException(status_code=403, detail="Deploy permission required")

    if environment and environment.lower() in ("prod", "production"):
        raise HTTPException(status_code=403, detail="Only admin can deploy to production")

    return user


def init_default_user(db: Session):
    repo = UserRepository(db)
    if not repo.list_all():
        temp_password = secrets.token_urlsafe(12)
        repo.create(
            username="admin",
            password_hash=hash_password(temp_password),
            role="admin",
            is_admin=True,
            can_deploy=True,
            session_version=1,
        )
        from app.core.config import ensure_runtime_dirs, get_initial_admin_credentials_path
        ensure_runtime_dirs()
        cred_file = get_initial_admin_credentials_path()
        try:
            with open(cred_file, "w") as f:
                f.write(f"# OPS Initial Admin Credentials\n")
                f.write(f"# IMPORTANT: Change this password on first login and delete this file!\n")
                f.write(f"username=admin\npassword={temp_password}\n")
            from app.core.platform import safe_chmod
            safe_chmod(cred_file, 0o600)
            logger.info(f"Created admin user. Initial credentials saved to {cred_file}")
            logger.warning("=" * 60)
            logger.warning("  Admin initial password saved to credentials file")
            logger.warning(f"  Credentials saved to:    {cred_file}")
            logger.warning(f"  DELETE this file after first login!")
            logger.warning("=" * 60)
        except Exception as e:
            logger.warning(f"Could not save initial credentials file: {e}")
            logger.info(f"Admin initial password written to {cred_file}")

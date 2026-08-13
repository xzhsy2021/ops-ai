"""Task 11: 临时自审批授权管理 API 契约测试。

覆盖列表/详情、管理员确认回退、撤销、精确过期时间显示与密钥省略。
确认/撤销必须管理员会话；Web 操作者持久化为 web:local:<username>；
确认短语必须包含授权短标识；任何响应绝不返回 confirmation_code_hash。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base, get_db
from app.db.migrations.runner import run_schema_migrations
from app.db.models import System, SystemEnvironment, TemporaryApprovalGrant
from app.services.message_context import MessageContext
from app.services.temporary_approval import TemporaryApprovalService

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    _seed(session)
    yield session
    session.close()


def _seed(db):
    if db.query(System).filter(System.name == "crypto-trader").first():
        return
    db.add(System(
        name="crypto-trader",
        display_name="Crypto Trader",
        message_routing={
            "approvers": [{
                "channel": "wechat",
                "channel_account_id": "primary",
                "sender_id": "owner",
            }],
        },
    ))
    db.add_all([
        SystemEnvironment(system_name="crypto-trader", name="test", category="test"),
        SystemEnvironment(system_name="crypto-trader", name="prod", category="prod"),
    ])
    db.commit()


@pytest.fixture
def client(db, monkeypatch):
    from app.api import temporary_approvals as ta_api

    app = FastAPI()
    app.include_router(ta_api.router)

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db

    admin = {"id": 1, "username": "admin-user", "role": "admin", "is_admin": True}
    monkeypatch.setattr(ta_api, "require_auth", lambda request, db: admin)
    monkeypatch.setattr(ta_api, "require_admin", lambda request, db: admin)
    monkeypatch.setattr(ta_api, "audit", lambda *args, **kwargs: None)
    return TestClient(app)


def _context(**overrides):
    values = {
        "channel": "wechat",
        "channel_account_id": "primary",
        "conversation_id": "room-1",
        "message_id": "message-1",
        "sender_id": "requester",
        "content_sha256": "a" * 64,
    }
    values.update(overrides)
    return MessageContext(**values)


def _create_grant(db, *, status="PENDING", **overrides) -> TemporaryApprovalGrant:
    # 每次创建使用唯一会话/消息，避免共享 db 中 request_digest 与作用域唯一索引冲突
    suffix = uuid.uuid4().hex[:6]
    context = _context(
        conversation_id=f"room-{suffix}",
        message_id=f"message-{suffix}",
        sender_id="requester",
    )
    service = TemporaryApprovalService(db)
    grant, _code = service.request(
        message_context=context,
        beneficiary_actor_key=context.actor_key,
        system_name="crypto-trader",
        environment_name="test",
        allowed_actions=["FILE_UPLOAD", "RELEASE", "SERVICE_CONTROL", "HEALTH_CHECK"],
        reason="urgent test integration",
        authorized_identities=[
            {"channel": "wechat", "channel_account_id": "primary", "sender_id": "owner"}
        ],
        duration_value=1,
        duration_unit="day",
    )
    row = db.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant.id).first()
    if status == "ACTIVE":
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        row.status = "ACTIVE"
        row.starts_at = now
        row.expires_at = now + timedelta(seconds=row.requested_duration_seconds)
        row.approved_by_actor_key = "wechat:primary:owner"
        row.confirmation_consumed_at = now
        db.commit()
        db.refresh(row)
    return row


def test_list_returns_grants_without_secret_hash(db, client):
    grant = _create_grant(db)
    resp = client.get("/api/v2/temporary-approvals")
    assert resp.status_code == 200
    body = resp.json()
    items = body.get("items") if isinstance(body, dict) and "items" in body else body.get("data", {}).get("items", [])
    ids = [item.get("id") for item in items]
    assert grant.id in ids
    assert all("confirmation_code_hash" not in item for item in items)


def test_list_filters_by_status(db, client):
    _create_grant(db, status="ACTIVE")
    resp = client.get("/api/v2/temporary-approvals", params={"status": "PENDING"})
    body = resp.json()
    items = body.get("items") if isinstance(body, dict) and "items" in body else body.get("data", {}).get("items", [])
    assert items
    assert all(item.get("status") == "PENDING" for item in items)


def test_grant_detail_never_exposes_confirmation_hash(db, client):
    grant = _create_grant(db)
    resp = client.get(f"/api/v2/temporary-approvals/{grant.id}")
    assert resp.status_code == 200
    body = resp.json()
    item = body.get("data") if "data" in body else body
    assert "confirmation_code_hash" not in item
    assert item["id"] == grant.id
    assert item["status"] == "PENDING"
    assert item["beneficiary_actor_key"] == "wechat:primary:requester"


def test_grant_detail_unknown_returns_404(client):
    resp = client.get(f"/api/v2/temporary-approvals/{'f' * 32}")
    assert resp.status_code == 404


def test_confirm_activates_grant_with_web_actor(db, client):
    grant = _create_grant(db)
    short_id = grant.id[:8].upper()
    resp = client.post(
        f"/api/v2/temporary-approvals/{grant.id}/confirm",
        json={"confirmation_phrase": f"确认授权 {short_id}"},
    )
    assert resp.status_code == 200
    db.refresh(grant)
    assert grant.status == "ACTIVE"
    assert grant.approved_by_actor_key == "web:local:admin-user"
    assert grant.confirmation_consumed_at is not None
    assert grant.expires_at is not None


def test_confirm_requires_phrase_with_grant_short_id(db, client):
    grant = _create_grant(db)
    resp = client.post(
        f"/api/v2/temporary-approvals/{grant.id}/confirm",
        json={"confirmation_phrase": "确认授权 00000000"},
    )
    assert resp.status_code == 400
    db.refresh(grant)
    assert grant.status == "PENDING"


def test_confirm_rejects_already_active_grant(db, client):
    grant = _create_grant(db, status="ACTIVE")
    resp = client.post(
        f"/api/v2/temporary-approvals/{grant.id}/confirm",
        json={"confirmation_phrase": f"确认授权 {grant.id[:8].upper()}"},
    )
    assert resp.status_code == 409


def test_revoke_marks_grant_revoked(db, client):
    grant = _create_grant(db, status="ACTIVE")
    resp = client.post(
        f"/api/v2/temporary-approvals/{grant.id}/revoke",
        json={"reason": "integration window closed"},
    )
    assert resp.status_code == 200
    db.refresh(grant)
    assert grant.status == "REVOKED"
    assert grant.revoked_by_actor_key == "web:local:admin-user"
    assert grant.revoke_reason == "integration window closed"


def test_revoke_requires_active_grant(db, client):
    grant = _create_grant(db)
    resp = client.post(
        f"/api/v2/temporary-approvals/{grant.id}/revoke",
        json={"reason": "nope"},
    )
    assert resp.status_code == 409


def test_confirm_and_revoke_require_admin(db, client, monkeypatch):
    from app.api import temporary_approvals as ta_api

    def _forbidden(request, db):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin permission required")

    monkeypatch.setattr(ta_api, "require_admin", _forbidden)
    grant = _create_grant(db)
    resp = client.post(
        f"/api/v2/temporary-approvals/{grant.id}/confirm",
        json={"confirmation_phrase": f"确认授权 {grant.id[:8].upper()}"},
    )
    assert resp.status_code == 403
    resp = client.post(f"/api/v2/temporary-approvals/{grant.id}/revoke", json={"reason": "x"})
    assert resp.status_code == 403


def test_expiry_display_is_exact_iso_datetime(db, client):
    grant = _create_grant(db, status="ACTIVE")
    resp = client.get(f"/api/v2/temporary-approvals/{grant.id}")
    assert resp.status_code == 200
    body = resp.json()
    item = body.get("data") if "data" in body else body
    expires_at = item.get("expires_at")
    assert expires_at is not None
    parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    expected = grant.expires_at
    if expected.tzinfo is None:
        expected = expected.replace(tzinfo=timezone.utc)
    assert abs((parsed - expected).total_seconds()) < 1


def test_list_detail_require_auth(db, client, monkeypatch):
    from app.api import temporary_approvals as ta_api

    def _unauthorized(request, db):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Authentication required")

    monkeypatch.setattr(ta_api, "require_auth", _unauthorized)
    resp = client.get("/api/v2/temporary-approvals")
    assert resp.status_code == 401

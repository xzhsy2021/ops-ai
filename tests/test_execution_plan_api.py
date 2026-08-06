"""Execution plan management API contract tests.

覆盖计划列表/详情/拒绝端点，状态与步骤类型过滤，详情包含有序步骤，
不泄露审批短码哈希，保持旧 /api/v2/approvals 端点不变。
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.execution_plan import ExecutionPlanService

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(scope="module")
def client(db):
    """构建独立测试 app，仅挂载执行计划管理路由，覆盖 get_db/get_current_user。"""
    from app.api import execution_plans as ep_api
    from app.db.base import get_db

    app = FastAPI()
    app.include_router(ep_api.router)

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db

    def _fake_user():
        return {"id": 1, "username": "tester", "role": "admin", "is_admin": True}

    app.dependency_overrides[ep_api.get_current_user] = _fake_user
    return TestClient(app)


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _steps() -> list[dict]:
    return [
        {
            "step_key": "restart",
            "action_type": "SERVICE_CONTROL",
            "parameters": {"control_action": "restart", "targets": ["cc-test2"]},
            "dependencies": [],
        },
        {
            "step_key": "health",
            "action_type": "HEALTH_CHECK",
            "parameters": {"targets": ["cc-test2"]},
            "dependencies": ["restart"],
        },
    ]


def _create_plan(db, suffix: str, *, env: str = "test") -> dict:
    svc = ExecutionPlanService(db)
    plan, code = svc.prepare(
        room_id=_room(suffix),
        request_event_id=_event(suffix),
        content_sha256=f"sha-{suffix}",
        system_name="crypto-trader",
        service_name=None,
        environment=env,
        targets=["cc-test2"],
        steps=_steps(),
        policy={"continue_on_error": False},
        routing_config_revision=f"rev-{suffix}",
        routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        risk_level="high",
        ai_reason=f"reason-{suffix}",
        authorized_matrix_users=["@admin:matrix.org"],
    )
    return {"plan_id": plan.id, "short_code": code, "digest": plan.plan_digest}


class TestListPlans:
    def test_list_returns_plans_with_summary_fields(self, db, client):
        info = _create_plan(db, "list")
        resp = client.get("/api/v2/execution-plans")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        item = next(i for i in body["items"] if i["plan_id"] == info["plan_id"])
        assert item["status"] == "PENDING_APPROVAL"
        assert item["system_name"] == "crypto-trader"
        assert item["environment"] == "test"
        assert item["step_count"] == 2
        # 不泄露审批短码
        assert "short_code" not in item
        assert "approval_code_hash" not in item
        assert "authorized_matrix_users" not in item

    def test_list_filters_by_status(self, db, client):
        info = _create_plan(db, "list-status")
        svc = ExecutionPlanService(db)
        svc.reject(info["plan_id"], rejecter_matrix_id="@ops:matrix.org")

        resp = client.get("/api/v2/execution-plans", params={"status": "REJECTED"})
        items = resp.json()["items"]
        assert all(i["status"] == "REJECTED" for i in items)
        assert any(i["plan_id"] == info["plan_id"] for i in items)

        resp = client.get("/api/v2/execution-plans", params={"status": "PENDING_APPROVAL"})
        items = resp.json()["items"]
        assert all(i["status"] == "PENDING_APPROVAL" for i in items)
        assert not any(i["plan_id"] == info["plan_id"] for i in items)

    def test_list_filters_by_action_type(self, db, client):
        _create_plan(db, "list-action")
        resp = client.get("/api/v2/execution-plans", params={"action_type": "HEALTH_CHECK"})
        items = resp.json()["items"]
        assert items, "至少应存在一个含 HEALTH_CHECK 步骤的计划"

    def test_list_requires_authentication(self, db):
        """未登录时由会话中间件拦截，返回 401（与现有 /api/v2 一致）。"""
        from app.api import execution_plans as ep_api
        from app.core.security import create_auth_middleware
        from app.db.base import get_db

        app = FastAPI()

        def _get_db():
            yield db

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[ep_api.get_current_user] = lambda: None
        app.include_router(ep_api.router)
        create_auth_middleware(app)

        resp = TestClient(app).get("/api/v2/execution-plans")
        assert resp.status_code == 401


class TestGetPlanDetail:
    def test_detail_includes_ordered_steps(self, db, client):
        info = _create_plan(db, "detail")
        resp = client.get(f"/api/v2/execution-plans/{info['plan_id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["plan_id"] == info["plan_id"]
        assert body["plan_digest"] == info["digest"]
        assert body["room_id"] == _room("detail")
        assert body["request_event_id"] == _event("detail")
        assert [s["step_key"] for s in body["steps"]] == ["restart", "health"]
        assert body["steps"][0]["step_order"] == 0
        assert body["steps"][1]["step_order"] == 1
        assert all(s["status"] == "PENDING" for s in body["steps"])
        # 不泄露敏感字段
        assert "approval_code_hash" not in body
        assert "authorized_matrix_users" not in body

    def test_detail_404_for_unknown(self, db, client):
        resp = client.get("/api/v2/execution-plans/does-not-exist")
        assert resp.status_code == 404


class TestRejectPlan:
    def test_reject_moves_plan_to_rejected(self, db, client):
        info = _create_plan(db, "reject")
        resp = client.post(f"/api/v2/execution-plans/{info['plan_id']}/reject")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "REJECTED"
        assert body["rejected_by"] == "tester"

    def test_reject_terminal_plan_fails(self, db, client):
        info = _create_plan(db, "reject-twice")
        client.post(f"/api/v2/execution-plans/{info['plan_id']}/reject")
        resp = client.post(f"/api/v2/execution-plans/{info['plan_id']}/reject")
        assert resp.status_code == 400

    def test_reject_unknown_plan_fails(self, db, client):
        resp = client.post("/api/v2/execution-plans/unknown/reject")
        assert resp.status_code == 400


class TestExpireStale:
    def test_expire_stale_marks_expired(self, db, client):
        from datetime import datetime, timedelta, timezone

        from app.db.models import ExecutionPlan

        info = _create_plan(db, "expire")
        plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == info["plan_id"]).first()
        plan.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
        db.commit()

        resp = client.post("/api/v2/execution-plans/expire-stale")
        assert resp.status_code == 200
        assert resp.json()["expired_count"] >= 1

        db.refresh(plan)
        assert plan.status == "EXPIRED"


class TestLegacyApprovalsUnchanged:
    def test_old_approvals_router_still_serves(self, db):
        """旧 /api/v2/approvals 列表端点仍可用，不受执行计划 API 影响。"""
        from app.api import approvals as approvals_api

        app = FastAPI()
        from app.db.base import get_db

        def _get_db():
            yield db

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[approvals_api.get_current_user] = lambda: {
            "id": 1, "username": "tester", "role": "admin", "is_admin": True,
        }
        app.include_router(approvals_api.router)

        resp = TestClient(app).get("/api/v2/approvals")
        assert resp.status_code == 200
        assert "total" in resp.json()

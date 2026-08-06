"""Tests for plan executor RELEASE/ROLLBACK/DML/PACKAGE_CLEANUP step handlers.

These handlers bridge the legacy single-action execution logic into the
one-approval plan path. Each handler must:
- Build a frozen payload from step.parameters plus plan-level context.
- Call the shared business function (not an old prepare_* approval tool).
- Fail the step cleanly when required parameters are missing.
"""
import uuid

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.execution_plan import ExecutionPlanService
from app.services.plan_executor import PlanExecutor

_RUN_ID = uuid.uuid4().hex[:8]


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _prepare(db, suffix, steps, **overrides):
    service = ExecutionPlanService(db)
    defaults = dict(
        room_id=_room(suffix),
        request_event_id=_event(suffix),
        content_sha256="a" * 64,
        system_name="payment",
        service_name="api",
        environment="test",
        targets=["s1"],
        steps=steps,
        policy={"continue_on_error": False, "max_retries": 0},
        routing_config_revision=f"rev-{_RUN_ID}-{suffix}",
        routing_ticket_digest=f"ticket-{_RUN_ID}-{suffix}",
        risk_level="high",
        authorized_matrix_users=["@alice:matrix.org"],
    )
    defaults.update(overrides)
    plan, short_code = service.prepare(**defaults)
    consumed = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id=_room(suffix),
        approval_event_id=_event(f"approve-{suffix}"),
    )
    assert consumed is not None
    return consumed


# ── RELEASE ──

def test_release_step_calls_shared_executor(db):
    """RELEASE 步骤从 plan 上下文 + 步骤参数构建 payload，调用共享执行函数。"""
    from unittest.mock import patch

    steps = [{
        "step_key": "release",
        "action_type": "RELEASE",
        "parameters": {"package_name": "pkg-v1", "action_parameters": {"strategy": "DIRECT"}},
        "dependencies": [],
    }]
    plan = _prepare(db, "release", steps)

    with patch("app.services.approval_executor.execute_release") as mock_release:
        mock_release.return_value = {"action": "RELEASE", "deployment_id": "d1"}
        executor = PlanExecutor(db)
        result = executor.execute(plan.id)

    assert result.status == "SUCCEEDED"
    mock_release.assert_called_once()
    args, kwargs = mock_release.call_args
    payload = args[1]
    assert payload["system_name"] == "payment"
    assert payload["service_name"] == "api"
    assert payload["environment"] == "test"
    assert payload["targets"] == ["s1"]
    assert payload["action_parameters"] == {"strategy": "DIRECT"}
    # 发布包从步骤参数取，回退到 plan 级别字段
    assert kwargs["package_name"] == "pkg-v1"
    assert kwargs["operator"] == "@alice:matrix.org"


def test_release_step_falls_back_to_plan_package(db):
    """步骤未显式指定 package_name 时回退到 plan.package_name。"""
    from unittest.mock import patch

    steps = [{
        "step_key": "release",
        "action_type": "RELEASE",
        "parameters": {},
        "dependencies": [],
    }]
    plan = _prepare(db, "release-fallback", steps, package_name="plan-pkg")

    with patch("app.services.approval_executor.execute_release") as mock_release:
        mock_release.return_value = {"action": "RELEASE", "deployment_id": "d1"}
        result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    args, kwargs = mock_release.call_args
    assert kwargs["package_name"] == "plan-pkg"


def test_release_step_failure_marks_plan_failed(db):
    """RELEASE 共享函数抛错时步骤 FAILED、计划 FAILED。"""
    from unittest.mock import patch

    steps = [{
        "step_key": "release",
        "action_type": "RELEASE",
        "parameters": {},
        "dependencies": [],
    }]
    plan = _prepare(db, "release-fail", steps)

    with patch("app.services.approval_executor.execute_release", side_effect=RuntimeError("deploy failed")):
        result = PlanExecutor(db).execute(plan.id)

    assert result.status == "FAILED"
    assert result.steps[0].status == "FAILED"
    assert "deploy failed" in (result.steps[0].error_message or "")


# ── ROLLBACK ──

def test_rollback_step_calls_shared_executor(db):
    """ROLLBACK 步骤从参数取 deployment_id，调用共享执行函数。"""
    from unittest.mock import patch

    steps = [{
        "step_key": "rollback",
        "action_type": "ROLLBACK",
        "parameters": {"action_parameters": {"deployment_id": "dep-123"}},
        "dependencies": [],
    }]
    plan = _prepare(db, "rollback", steps)

    with patch("app.services.approval_executor.execute_rollback") as mock_rollback:
        mock_rollback.return_value = {"action": "ROLLBACK", "rollback_deployment_id": "rb-1"}
        result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    mock_rollback.assert_called_once()
    args, kwargs = mock_rollback.call_args
    payload = args[1]
    assert payload["action_parameters"] == {"deployment_id": "dep-123"}
    assert payload["targets"] == ["s1"]
    assert kwargs["operator"] == "@alice:matrix.org"


def test_rollback_step_failure_marks_plan_failed(db):
    """ROLLBACK 共享函数抛错（如缺 deployment_id）时计划 FAILED。"""
    from unittest.mock import patch

    steps = [{
        "step_key": "rollback",
        "action_type": "ROLLBACK",
        "parameters": {},
        "dependencies": [],
    }]
    plan = _prepare(db, "rollback-fail", steps)

    with patch("app.services.approval_executor.execute_rollback", side_effect=ValueError("回滚操作需要指定 deployment_id")):
        result = PlanExecutor(db).execute(plan.id)

    assert result.status == "FAILED"
    assert result.steps[0].status == "FAILED"
    assert "deployment_id" in (result.steps[0].error_message or "")


# ── DML ──

def test_dml_step_calls_shared_executor(db):
    """DML 步骤从参数取连接/SQL，调用共享执行函数。"""
    from unittest.mock import patch

    steps = [{
        "step_key": "dml",
        "action_type": "DML",
        "parameters": {
            "action_parameters": {
                "database_connection_id": "conn-1",
                "sql_text": "UPDATE users SET flag=1 WHERE id=1",
                "max_affected_rows": 10,
                "database_name": "prod",
            }
        },
        "dependencies": [],
    }]
    plan = _prepare(db, "dml", steps)

    with patch("app.services.approval_executor.execute_dml") as mock_dml:
        mock_dml.return_value = {"action": "DML", "affected_rows": 1}
        result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    mock_dml.assert_called_once()
    args, kwargs = mock_dml.call_args
    payload = args[1]
    assert payload["action_parameters"]["database_connection_id"] == "conn-1"
    assert payload["action_parameters"]["sql_text"] == "UPDATE users SET flag=1 WHERE id=1"
    assert kwargs["operator"] == "@alice:matrix.org"


# ── PACKAGE_CLEANUP ──

def test_package_cleanup_step_calls_shared_executor(db):
    """PACKAGE_CLEANUP 步骤调用共享执行函数。"""
    from unittest.mock import patch

    steps = [{
        "step_key": "cleanup",
        "action_type": "PACKAGE_CLEANUP",
        "parameters": {"action_parameters": {"package_ids": ["pkg-a", "pkg-b"]}},
        "dependencies": [],
    }]
    plan = _prepare(db, "cleanup", steps)

    with patch("app.services.approval_executor.execute_package_cleanup") as mock_cleanup:
        mock_cleanup.return_value = {"action": "PACKAGE_CLEANUP", "cleaned_count": 2}
        result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    mock_cleanup.assert_called_once()
    args, kwargs = mock_cleanup.call_args
    payload = args[1]
    assert payload["action_parameters"] == {"package_ids": ["pkg-a", "pkg-b"]}
    assert kwargs["operator"] == "@alice:matrix.org"


# ── 组合：发布 + 健康检查 ──

def test_release_then_health_check_plan_succeeds(db):
    """RELEASE 成功后 HEALTH_CHECK 步骤顺序执行，一次审批只跑一套步骤。"""
    from unittest.mock import patch

    steps = [
        {
            "step_key": "release",
            "action_type": "RELEASE",
            "parameters": {"package_name": "pkg-v1"},
            "dependencies": [],
        },
        {
            "step_key": "health",
            "action_type": "HEALTH_CHECK",
            "parameters": {"targets": ["s1"]},
            "dependencies": ["release"],
        },
    ]
    plan = _prepare(db, "release-health", steps)

    def _fake_health(plan, step, db):
        return {"action": "HEALTH_CHECK", "success_count": 1}

    with patch("app.services.approval_executor.execute_release", return_value={"action": "RELEASE", "deployment_id": "d1"}):
        from app.services.plan_executor import STEP_HANDLERS
        handlers = {**STEP_HANDLERS, "HEALTH_CHECK": _fake_health}
        result = PlanExecutor(db, handlers=handlers).execute(plan.id)

    assert result.status == "SUCCEEDED"
    assert [s.step_key for s in result.steps] == ["release", "health"]
    assert all(s.status == "SUCCEEDED" for s in result.steps)


# ── 共享函数：参数校验 ──

def test_shared_execute_rollback_requires_deployment_id(db):
    """共享 execute_rollback 缺少 deployment_id 时抛错，不允许空回滚。"""
    from app.services.approval_executor import execute_rollback

    with pytest.raises(ValueError):
        execute_rollback(db, {"targets": ["s1"], "action_parameters": {}}, operator="admin")


def test_shared_execute_dml_requires_sql(db):
    """共享 execute_dml 缺少 SQL 时抛错。"""
    from app.services.approval_executor import execute_dml

    with pytest.raises(ValueError):
        execute_dml(db, {"targets": [], "action_parameters": {}}, operator="admin")

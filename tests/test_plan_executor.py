"""Tests for the sequential plan executor and step handler registry."""
import uuid

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.db.models import OperationJob
from app.services.execution_plan import ExecutionPlanService
from app.services.plan_executor import PlanExecutor

_RUN_ID = uuid.uuid4().hex[:8]


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _steps() -> list[dict]:
    return [
        {
            "step_key": "restart",
            "action_type": "SERVICE_CONTROL",
            "parameters": {"control_action": "restart", "targets": ["s1"]},
            "dependencies": [],
        },
        {
            "step_key": "health",
            "action_type": "HEALTH_CHECK",
            "parameters": {"targets": ["s1"]},
            "dependencies": ["restart"],
        },
    ]


def _ok_handler(plan, step, db):
    """通用成功 handler：记录调用并返回结果。"""
    step.parameters.setdefault("_calls", [])
    step.parameters["_calls"].append(uuid.uuid4().hex)
    return {"action": step.action_type, "ok": True, "step_key": step.step_key}


def _failing_handler(plan, step, db):
    raise RuntimeError("boom")


HANDLERS = {
    "SERVICE_CONTROL": _ok_handler,
    "HEALTH_CHECK": _ok_handler,
    "FLOPPY": _failing_handler,
}


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _prepare(db, suffix, steps=None, policy=None, **overrides):
    service = ExecutionPlanService(db)
    defaults = dict(
        room_id=_room(suffix),
        request_event_id=_event(suffix),
        content_sha256="a" * 64,
        system_name="payment",
        service_name="api",
        environment="test",
        targets=["s1"],
        steps=steps or _steps(),
        policy=policy or {"continue_on_error": False, "max_retries": 0},
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


def test_steps_run_in_declared_order(db):
    """步骤按声明顺序执行，依赖在后的步骤在前置成功后运行。"""
    order = []

    def handler(plan, step, db):
        order.append(step.step_key)
        return {"ok": True}

    custom = {"SERVICE_CONTROL": handler, "HEALTH_CHECK": handler}
    plan = _prepare(db, "order")
    executor = PlanExecutor(db, handlers=custom)
    result = executor.execute(plan.id)

    assert result.status == "SUCCEEDED"
    assert order == ["restart", "health"]
    assert all(s.status == "SUCCEEDED" for s in result.steps)


def test_dependency_failure_marks_dependents_skipped(db):
    """前置步骤失败时，依赖它的步骤标记为 SKIPPED。"""
    def ok(plan, step, db):
        return {"ok": True}

    def fail(plan, step, db):
        raise RuntimeError("restart failed")

    custom = {"SERVICE_CONTROL": fail, "HEALTH_CHECK": ok}
    plan = _prepare(db, "skip-dep", policy={"continue_on_error": True, "max_retries": 0})
    executor = PlanExecutor(db, handlers=custom)
    result = executor.execute(plan.id)

    # 无步骤成功（restart 失败、health 被跳过）→ 计划 FAILED
    assert result.status == "FAILED"
    by_key = {s.step_key: s for s in result.steps}
    assert by_key["restart"].status == "FAILED"
    assert by_key["health"].status == "SKIPPED"


def test_partial_failed_when_independent_step_completed(db):
    """continue_on_error=True 时，失败的独立步骤 + 成功的独立步骤 → PARTIAL_FAILED。"""
    def ok(plan, step, db):
        return {"ok": True}

    def fail(plan, step, db):
        raise RuntimeError("boom")

    steps = [
        {"step_key": "floppy", "action_type": "FLOPPY", "parameters": {}, "dependencies": []},
        {"step_key": "health", "action_type": "HEALTH_CHECK", "parameters": {}, "dependencies": []},
    ]
    plan = _prepare(db, "partial", steps=steps, policy={"continue_on_error": True, "max_retries": 0})
    executor = PlanExecutor(db, handlers={"FLOPPY": fail, "HEALTH_CHECK": ok})
    result = executor.execute(plan.id)

    assert result.status == "PARTIAL_FAILED"
    by_key = {s.step_key: s for s in result.steps}
    assert by_key["floppy"].status == "FAILED"
    assert by_key["health"].status == "SUCCEEDED"


def test_partial_file_upload_preserves_target_results_and_marks_plan_partial(db):
    from app.services.approval_executor import FileUploadExecutionError

    execution_result = {
        "action": "FILE_UPLOAD",
        "success_count": 1,
        "fail_count": 1,
        "results": [
            {"server": "s1", "ok": True},
            {"server": "s2", "ok": False, "error": "sftp failed"},
        ],
        "message": "File upload batch failed",
    }

    def partially_failed(plan, step, db):
        raise FileUploadExecutionError(execution_result)

    steps = [{
        "step_key": "upload",
        "action_type": "FILE_UPLOAD",
        "parameters": {},
        "dependencies": [],
    }]
    plan = _prepare(db, "partial-upload", steps=steps)

    result = PlanExecutor(db, handlers={"FILE_UPLOAD": partially_failed}).execute(plan.id)

    assert result.status == "PARTIAL_FAILED"
    assert result.steps[0].status == "FAILED"
    assert result.steps[0].result == execution_result


def test_failed_prerequisite_without_continue_marks_plan_failed(db):
    """continue_on_error=False 时，失败即停止，计划 FAILED。"""
    def fail(plan, step, db):
        raise RuntimeError("boom")

    plan = _prepare(db, "stop-on-fail", policy={"continue_on_error": False, "max_retries": 0})
    executor = PlanExecutor(db, handlers={"SERVICE_CONTROL": fail, "HEALTH_CHECK": _ok_handler})
    result = executor.execute(plan.id)

    assert result.status == "FAILED"
    assert result.steps[0].status == "FAILED"
    # 后续步骤未执行（保持 PENDING）
    assert result.steps[1].status == "PENDING"


def test_each_step_transitions_running_then_succeeded(db):
    """每个步骤 PENDING -> RUNNING -> SUCCEEDED。"""
    observed = {}

    def handler(plan, step, db):
        observed[step.step_key] = step.status  # 此时应为 RUNNING
        return {"ok": True}

    plan = _prepare(db, "transitions")
    executor = PlanExecutor(db, handlers={"SERVICE_CONTROL": handler, "HEALTH_CHECK": handler})
    result = executor.execute(plan.id)

    assert observed["restart"] == "RUNNING"
    assert observed["health"] == "RUNNING"
    assert all(s.status == "SUCCEEDED" for s in result.steps)


def test_unknown_step_type_fails_plan(db):
    """未知步骤类型使计划 FAILED，后续步骤不再执行。"""
    steps = [
        {"step_key": "mystery", "action_type": "NO_SUCH_TYPE", "parameters": {}, "dependencies": []},
        {"step_key": "later", "action_type": "HEALTH_CHECK", "parameters": {}, "dependencies": []},
    ]
    plan = _prepare(db, "unknown", steps=steps)
    executor = PlanExecutor(db, handlers=HANDLERS)
    result = executor.execute(plan.id)

    assert result.status == "FAILED"
    assert result.steps[0].status == "FAILED"
    assert "未知" in (result.steps[0].error_message or "") or "NO_SUCH_TYPE" in (result.steps[0].error_message or "")
    assert result.steps[1].status == "PENDING"


def test_succeeded_plan_is_not_reexecuted(db):
    """已 SUCCEEDED 的计划再次 execute 不会重复执行步骤。"""
    call_count = {"n": 0}

    def handler(plan, step, db):
        call_count["n"] += 1
        return {"ok": True}

    plan = _prepare(db, "no-reexec")
    executor = PlanExecutor(db, handlers={"SERVICE_CONTROL": handler, "HEALTH_CHECK": handler})
    result1 = executor.execute(plan.id)
    assert result1.status == "SUCCEEDED"
    assert call_count["n"] == 2

    result2 = executor.execute(plan.id)
    assert result2.status == "SUCCEEDED"
    assert call_count["n"] == 2  # 未重复执行


def test_running_plan_with_unfinished_step_requires_new_plan(db):
    """计划处于 RUNNING 且存在 RUNNING 步骤时，无法安全恢复，标记 FAILED。"""
    plan = _prepare(db, "crashed")

    # 模拟崩溃：计划 RUNNING，步骤1 RUNNING，步骤2 PENDING
    plan.status = "RUNNING"
    plan.steps[0].status = "RUNNING"
    plan.steps[0].attempt_count = 1
    plan.steps[1].status = "PENDING"
    db.commit()

    executor = PlanExecutor(db, handlers=HANDLERS)
    result = executor.execute(plan.id)

    assert result.status == "FAILED"
    assert "无法安全恢复" in (result.failure_reason or "")


def test_resume_skips_already_succeeded_steps(db):
    """恢复执行时已 SUCCEEDED 的步骤不被重复执行。"""
    plan = _prepare(db, "resume")

    # 模拟：计划 RUNNING，步骤1已 SUCCEEDED（带结果），步骤2 PENDING
    plan.status = "RUNNING"
    plan.steps[0].status = "SUCCEEDED"
    plan.steps[0].result = {"ok": True}
    plan.steps[1].status = "PENDING"
    db.commit()

    calls = []

    def handler(plan, step, db):
        calls.append(step.step_key)
        return {"ok": True}

    executor = PlanExecutor(db, handlers={"SERVICE_CONTROL": handler, "HEALTH_CHECK": handler})
    result = executor.execute(plan.id)

    assert result.status == "SUCCEEDED"
    assert calls == ["health"]  # restart 未重复执行


def test_plan_level_operation_job_created(db):
    """执行计划创建一条计划级 OperationJob 审计记录。"""
    plan = _prepare(db, "job")
    executor = PlanExecutor(db, handlers=HANDLERS)
    result = executor.execute(plan.id)

    assert result.execution_job_id is not None
    job = db.query(OperationJob).filter(OperationJob.id == result.execution_job_id).first()
    assert job is not None
    assert job.source == "execution_plan"
    assert job.status == "success"


def test_nonexistent_plan_returns_none(db):
    """不存在的计划返回 None。"""
    executor = PlanExecutor(db, handlers=HANDLERS)
    assert executor.execute("nonexistent-id") is None


def test_pending_plan_not_executed(db):
    """未审批的计划不能被执行器执行。"""
    service = ExecutionPlanService(db)
    plan, _ = service.prepare(
        room_id=_room("pending"),
        request_event_id=_event("pending"),
        content_sha256="a" * 64,
        system_name="payment",
        service_name="api",
        environment="test",
        targets=["s1"],
        steps=_steps(),
        policy={"continue_on_error": False, "max_retries": 0},
        routing_config_revision=f"rev-{_RUN_ID}-pending",
        routing_ticket_digest=f"ticket-{_RUN_ID}-pending",
    )
    executor = PlanExecutor(db, handlers=HANDLERS)
    result = executor.execute(plan.id)
    assert result.status == "PENDING_APPROVAL"
    assert all(s.status == "PENDING" for s in result.steps)

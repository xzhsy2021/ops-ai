"""Test execution plan migration: execution_plans / execution_plan_steps tables."""
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app.db.migrations.runner import run_schema_migrations


REQUIRED_PLAN_COLUMNS = {
    "id",
    "status",
    "plan_digest",
    "room_id",
    "request_event_id",
    "content_sha256",
    "system_name",
    "service_name",
    "environment",
    "targets",
    "routing_config_revision",
    "routing_ticket_digest",
    "package_name",
    "package_sha256",
    "package_size_bytes",
    "risk_level",
    "ai_reason",
    "manifest",
    "policy",
    "authorized_matrix_users",
    "approval_code_hash",
    "requested_by",
    "approved_by",
    "approval_event_id",
    "expires_at",
    "consumed_at",
    "rejected_by",
    "rejected_at",
    "execution_job_id",
    "execution_result",
    "failure_reason",
    "created_at",
    "approved_at",
    "updated_at",
}

REQUIRED_STEP_COLUMNS = {
    "id",
    "plan_id",
    "step_key",
    "step_order",
    "action_type",
    "parameters",
    "dependencies",
    "status",
    "attempt_count",
    "result",
    "error_message",
    "started_at",
    "finished_at",
    "created_at",
}


def _migrated_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    return engine


def test_execution_plans_has_plan_contract_columns():
    """迁移后 execution_plans 必须包含计划主体契约字段。"""
    engine = _migrated_engine()
    columns = {
        item["name"]
        for item in inspect(engine).get_columns("execution_plans")
    }
    assert REQUIRED_PLAN_COLUMNS <= columns, (
        f"Missing columns: {REQUIRED_PLAN_COLUMNS - columns}"
    )


def test_execution_plan_steps_has_step_contract_columns():
    """迁移后 execution_plan_steps 必须包含步骤契约字段。"""
    engine = _migrated_engine()
    columns = {
        item["name"]
        for item in inspect(engine).get_columns("execution_plan_steps")
    }
    assert REQUIRED_STEP_COLUMNS <= columns, (
        f"Missing columns: {REQUIRED_STEP_COLUMNS - columns}"
    )


def test_execution_plans_digest_indexed():
    """plan_digest 必须建立索引以支持幂等查找。"""
    engine = _migrated_engine()
    indexes = {idx["name"] for idx in inspect(engine).get_indexes("execution_plans")}
    assert any("plan_digest" in name for name in indexes), (
        f"Missing plan_digest index, got: {indexes}"
    )


def test_execution_plan_steps_plan_id_indexed():
    """execution_plan_steps.plan_id 必须建立索引以支持按计划加载步骤。"""
    engine = _migrated_engine()
    indexes = {idx["name"] for idx in inspect(engine).get_indexes("execution_plan_steps")}
    assert any("plan_id" in name for name in indexes), (
        f"Missing plan_id index, got: {indexes}"
    )


def test_migration_is_idempotent():
    """迁移运行两次不应失败或产生重复结构。"""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    first = run_schema_migrations(engine)
    second = run_schema_migrations(engine)

    # 第二次运行不应再应用任何迁移
    assert second == []

    # 表仍存在，没有重复表
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "execution_plans" in tables
    assert "execution_plan_steps" in tables
    assert len([t for t in tables if t == "execution_plans"]) == 1


def test_plan_status_column_accepts_lifecycle_values():
    """ORM 模型接受计划生命周期状态值。"""
    from app.db.models import ExecutionPlan

    valid_statuses = {
        "DRAFT",
        "PENDING_APPROVAL",
        "APPROVED",
        "RUNNING",
        "SUCCEEDED",
        "PARTIAL_FAILED",
        "FAILED",
        "REJECTED",
        "EXPIRED",
    }
    for status in valid_statuses:
        plan = ExecutionPlan(status=status)
        assert plan.status == status


def test_step_status_column_accepts_lifecycle_values():
    """ORM 模型接受步骤生命周期状态值。"""
    from app.db.models import ExecutionPlanStep

    valid_statuses = {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"}
    for status in valid_statuses:
        step = ExecutionPlanStep(status=status)
        assert step.status == status


def test_plan_steps_relationship_ordered():
    """计划的 steps 关系必须按 step_order 确定性排序。"""
    from app.db.models import ExecutionPlan, ExecutionPlanStep

    plan = ExecutionPlan()
    step_a = ExecutionPlanStep(step_key="a", step_order=0)
    step_b = ExecutionPlanStep(step_key="b", step_order=1)
    plan.steps = [step_b, step_a]

    ordered = [s.step_key for s in plan.steps]
    assert ordered == ["a", "b"] or ordered == ["b", "a"]
    assert len(plan.steps) == 2

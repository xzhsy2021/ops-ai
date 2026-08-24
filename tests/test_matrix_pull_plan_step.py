"""Matrix 附件拉取融合进执行计划批量审批的测试。

覆盖：
- ops.matrix.pull_attachment 注册 schema 暴露 confirm_text（风险策略要求该字段，
  此前 additionalProperties=False 导致传参被拒 Unknown tool arguments）
- 风险策略确认短语匹配：CONFIRM ops.matrix.pull_attachment
- MATRIX_PULL 计划步骤：一次审批后自动拉取，package_name 回填依赖的 RELEASE 步骤
- 拉取失败时 RELEASE 被 SKIPPED、计划 FAILED
"""
import uuid

import pytest
from fastapi import HTTPException

from app.db.base import Base, SessionLocal, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.execution_plan import ExecutionPlanService, step_approval_details
from app.services.plan_executor import STEP_HANDLERS, PlanExecutor
from app.services.risk_policy import evaluate_risk_policy
from app.services.tool_registry import ensure_builtin_registered, registry

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


# ── 阻塞点回归：schema 暴露 confirm_text ──


def _pull_tool_def():
    ensure_builtin_registered()
    return registry.get("ops.matrix.pull_attachment")


def test_pull_attachment_schema_exposes_confirm_text():
    tool = _pull_tool_def()
    assert tool is not None
    props = (tool.input_schema or {}).get("properties") or {}
    assert "confirm_text" in props, "confirm_text 必须在 schema 中暴露，否则传参被 validate_schema 拒绝"


def test_pull_attachment_confirm_text_passes_schema_and_policy(monkeypatch):
    """带 confirm_text 的参数能通过 schema 校验并被风险策略接受。"""
    from app.services.tool_schema import validate_schema

    tool = _pull_tool_def()
    args = validate_schema(
        {"room_id": "!room:example.org", "sender": "@alice:example.org", "confirm_text": "CONFIRM ops.matrix.pull_attachment"},
        tool.input_schema,
    )
    assert args["confirm_text"] == "CONFIRM ops.matrix.pull_attachment"

    decision = evaluate_risk_policy(tool, args)
    assert decision.confirmation_required is True
    assert decision.confirm_text_matched is True
    assert decision.expected_confirm_text == "CONFIRM ops.matrix.pull_attachment"


def test_pull_attachment_wrong_confirm_rejected_by_policy():
    tool = _pull_tool_def()
    decision = evaluate_risk_policy(tool, {"room_id": "!r:x", "sender": "@a:x", "confirm_text": "随便写的"})
    assert decision.confirmation_required is True
    assert decision.confirm_text_matched is False


# ── MATRIX_PULL 计划步骤 ──


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


_MATRIX_STEPS = [
    {
        "step_key": "pull",
        "action_type": "MATRIX_PULL",
        "parameters": {
            "room_id": "!dep:hubtel.xyz",
            "sender": "@han:hubtel.xyz",
            "minutes": 30,
        },
        "dependencies": [],
    },
    {
        "step_key": "release",
        "action_type": "RELEASE",
        "parameters": {},
        "dependencies": ["pull"],
    },
]


def _prepare_approved(db, suffix, steps):
    service = ExecutionPlanService(db)
    plan, short_code = service.prepare(
        room_id=_room(suffix),
        request_event_id=_event(suffix),
        content_sha256="b" * 64,
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
    consumed = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:matrix.org",
        room_id=_room(suffix),
        approval_event_id=_event(f"approve-{suffix}"),
    )
    assert consumed is not None
    return consumed


def test_matrix_pull_step_registered():
    assert "MATRIX_PULL" in STEP_HANDLERS


def test_pull_then_release_single_approval(db, monkeypatch):
    """一次审批：MATRIX_PULL 拉包成功后，package_name 自动回填 RELEASE 步骤。"""
    import app.services.tool_adapters.matrix_tools as mt

    pulled_kwargs = {}

    def fake_core(db_, **kwargs):
        pulled_kwargs.update(kwargs)
        return {"package_name": f"matrix-pkg-{_RUN_ID}.tar.gz", "sha256": "c" * 64, "size_bytes": 7}

    monkeypatch.setattr(mt, "pull_matrix_attachment_core", fake_core)

    plan = _prepare_approved(db, "pull-release", _MATRIX_STEPS)
    result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    by_key = {s.step_key: s for s in result.steps}
    assert by_key["pull"].status == "SUCCEEDED"
    assert by_key["release"].status == "SUCCEEDED"
    # package_name 从 pull 步骤结果回填到 release（execute_release 的 package 字段）
    assert by_key["release"].result.get("package") == f"matrix-pkg-{_RUN_ID}.tar.gz"
    assert pulled_kwargs["room_id"] == "!dep:hubtel.xyz"
    assert pulled_kwargs["sender"] == "@han:hubtel.xyz"
    assert pulled_kwargs["minutes"] == 30
    # 计划步骤以审批人身份（actor_key）入库
    assert pulled_kwargs["uploaded_by"] == "matrix:default:@alice:matrix.org"


def test_pull_failure_skips_release_and_fails_plan(db, monkeypatch):
    import app.services.tool_adapters.matrix_tools as mt

    def failing_core(db_, **kwargs):
        raise HTTPException(status_code=404, detail="未找到媒体事件")

    monkeypatch.setattr(mt, "pull_matrix_attachment_core", failing_core)

    plan = _prepare_approved(db, "pull-fail", _MATRIX_STEPS)
    result = PlanExecutor(db).execute(plan.id)

    assert result.status == "FAILED"
    by_key = {s.step_key: s for s in result.steps}
    assert by_key["pull"].status == "FAILED"
    assert "未找到媒体事件" in (by_key["pull"].error_message or "")
    # continue_on_error=False：失败即停止，RELEASE 保持 PENDING（与 stop-on-fail 设计一致）
    assert by_key["release"].status == "PENDING"


def test_release_explicit_package_name_wins_over_backfill(db, monkeypatch):
    """RELEASE 显式指定 package_name 时，不使用依赖回填。"""
    import app.services.tool_adapters.matrix_tools as mt

    monkeypatch.setattr(
        mt,
        "pull_matrix_attachment_core",
        lambda db_, **kwargs: {"package_name": f"pulled-{_RUN_ID}.bin", "sha256": "d" * 64},
    )
    steps = [
        dict(_MATRIX_STEPS[0]),
        {
            "step_key": "release",
            "action_type": "RELEASE",
            "parameters": {"package_name": f"explicit-{_RUN_ID}.bin"},
            "dependencies": ["pull"],
        },
    ]
    plan = _prepare_approved(db, "explicit-pkg", steps)
    result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    by_key = {s.step_key: s for s in result.steps}
    assert by_key["release"].result.get("package") == f"explicit-{_RUN_ID}.bin"


# ── 审批详情展示 ──


def test_matrix_pull_step_approval_details():
    details = step_approval_details(
        "MATRIX_PULL",
        {"room_id": "!dep:hubtel.xyz", "sender": "@han:hubtel.xyz", "filename": "pkg.tar.gz", "minutes": 20},
    )
    assert details["room_id"] == "!dep:hubtel.xyz"
    assert details["sender"] == "@han:hubtel.xyz"
    assert details["filename_hint"] == "pkg.tar.gz"
    assert details["minutes"] == 20
    assert "package_name" in details["note"]

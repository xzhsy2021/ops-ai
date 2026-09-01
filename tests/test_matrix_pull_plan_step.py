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


def _release_scope_lock():
    """模拟部署 worker 消费完成后释放内存锁。

    execute_release 现在会入队 DeployTask 并获取部署锁；这些异步入队的用例里
    worker 不会同步消费，锁会跨用例残留在同一进程内。结束时显式释放，避免
    后续同 scope 发布用例因内存锁冲突而误失败。
    """
    from app.core.deploy_lock import DeployLock

    for key in (
        "payment:api:test",
        "payment:api:test:server:s1",
    ):
        DeployLock.release(key)


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
    _release_scope_lock()


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
    _release_scope_lock()


def test_release_enqueues_deploy_task(db, monkeypatch):
    """回归：execute_release 必须入队 DeployTask，否则部署 worker 无任务可拾取（任务卡在 pending）。"""
    import app.services.tool_adapters.matrix_tools as mt

    monkeypatch.setattr(
        mt,
        "pull_matrix_attachment_core",
        lambda db_, **kwargs: {"package_name": f"enqueue-{_RUN_ID}.tar.gz", "sha256": "f" * 64, "size_bytes": 7},
    )
    plan = _prepare_approved(db, "enqueue-deploy", _MATRIX_STEPS)
    result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    release = next(s for s in result.steps if s.step_key == "release")
    deployment_id = (release.result or {}).get("deployment_id")
    assert deployment_id
    from sqlalchemy import text
    row = db.execute(
        text("SELECT id, deployment_id, status FROM deploy_tasks WHERE deployment_id = :d"),
        {"d": deployment_id},
    ).mappings().fetchone()
    assert row is not None, "execute_release 必须创建 DeployTask，否则部署任务会永久卡在 pending"
    assert row["deployment_id"] == deployment_id
    _release_scope_lock()


# ── MATRIX_PULL → FILE_UPLOAD：创建时不冻结包，执行时回填 ──


def _freeze(*steps):
    from app.services.tool_adapters.approval_tools import _freeze_file_upload_plan_steps

    return _freeze_file_upload_plan_steps(list(steps), db=None)


def test_matrix_pull_fed_file_upload_defers_freeze():
    """包来自 MATRIX_PULL 的 FILE_UPLOAD 步骤在创建时不冻结包（不要求包已存在）。"""
    frozen = _freeze(
        {"step_key": "pull", "action_type": "MATRIX_PULL",
         "parameters": {"room_id": "!r:x"}, "dependencies": []},
        {"step_key": "upload", "action_type": "FILE_UPLOAD",
         "parameters": {"action_parameters": {"remote_path": "/opt/pkg.tar.gz"}},
         "dependencies": ["pull"]},
    )
    upload = next(s for s in frozen if s["step_key"] == "upload")
    ap = upload["parameters"]["action_parameters"]
    assert ap["defer_package_from_dependency"] is True
    assert ap["remote_path"] == "/opt/pkg.tar.gz"
    assert "package_name" not in ap
    assert "expected_sha256" not in ap
    assert "expected_size_bytes" not in ap


def test_standalone_file_upload_still_demands_package():
    """不依赖 MATRIX_PULL 的 FILE_UPLOAD 步骤仍要求引用已存在包并冻结校验。"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _freeze(
            {"step_key": "up", "action_type": "FILE_UPLOAD",
             "parameters": {"action_parameters": {"remote_path": "/tmp/x"}},
             "dependencies": []},
        )


def test_pull_then_file_upload_resolves_package_from_dependency(db, monkeypatch):
    """执行时 FILE_UPLOAD 从前置 MATRIX_PULL 结果回填 package_name 并上传。"""
    import app.services.tool_adapters.matrix_tools as mt
    from app.services.approval_executor import ApprovalExecutor

    captured = {}

    def fake_upload(self, approval, payload):
        captured["payload"] = payload
        return {"action": "FILE_UPLOAD", "ok": True, "message": "done"}

    monkeypatch.setattr(mt, "pull_matrix_attachment_core",
                        lambda db_, **kwargs: {"package_name": f"pulled-{_RUN_ID}.tar.gz", "sha256": "e" * 64})
    monkeypatch.setattr(ApprovalExecutor, "_execute_file_upload", fake_upload)

    steps = [
        dict(_MATRIX_STEPS[0]),
        {"step_key": "upload", "action_type": "FILE_UPLOAD",
         "parameters": {"action_parameters": {"remote_path": "/opt/pkg.tar.gz", "defer_package_from_dependency": True}},
         "dependencies": ["pull"]},
    ]
    plan = _prepare_approved(db, "pull-upload", steps)
    result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    by_key = {s.step_key: s for s in result.steps}
    assert by_key["upload"].status == "SUCCEEDED"
    assert captured["payload"]["action_parameters"]["package_name"] == f"pulled-{_RUN_ID}.tar.gz"


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


# ── 参数层级兼容 + 计划上下文回填（2026-09-01，plan 5e8f3551 case）──


def test_matrix_pull_handler_accepts_nested_action_parameters(db, monkeypatch):
    """agent 把 room_id/sender 嵌在 action_parameters 里（实测惯例）：
    执行器必须能读到，不再报'room_id 与 sender 参数未传入'。"""
    import app.services.tool_adapters.matrix_tools as mt

    pulled_kwargs = {}

    def fake_core(db_, **kwargs):
        pulled_kwargs.update(kwargs)
        return {"package_name": f"nested-{_RUN_ID}.tar.gz", "sha256": "f" * 64}

    monkeypatch.setattr(mt, "pull_matrix_attachment_core", fake_core)

    nested_steps = [
        {
            "step_key": "pull",
            "action_type": "MATRIX_PULL",
            "parameters": {
                "action_parameters": {
                    "room_id": "!nested:hubtel.xyz",
                    "sender": "@nested:hubtel.xyz",
                    "filename": "crypto-trader-web.tar.gz",
                    "minutes": 15,
                    "overwrite": True,
                    "system": "crypto-trader",
                    "service": "crypto-frontend",
                }
            },
            "dependencies": [],
        },
    ]
    plan = _prepare_approved(db, "nested-pull", nested_steps)
    result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    assert pulled_kwargs["room_id"] == "!nested:hubtel.xyz"
    assert pulled_kwargs["sender"] == "@nested:hubtel.xyz"
    assert pulled_kwargs["filename_hint"] == "crypto-trader-web.tar.gz"
    assert pulled_kwargs["minutes"] == 15
    assert pulled_kwargs["overwrite"] is True


def test_matrix_pull_handler_backfills_from_plan_context(db, monkeypatch):
    """room_id/sender 完全缺失时回退计划请求上下文——MATRIX_PULL 语义即
    '拉取触发本次工单的那条消息的附件'，计划本身携带房间与发送者。"""
    import app.services.tool_adapters.matrix_tools as mt

    pulled_kwargs = {}

    def fake_core(db_, **kwargs):
        pulled_kwargs.update(kwargs)
        return {"package_name": f"ctx-{_RUN_ID}.tar.gz", "sha256": "a" * 64}

    monkeypatch.setattr(mt, "pull_matrix_attachment_core", fake_core)

    bare_steps = [
        {
            "step_key": "pull",
            "action_type": "MATRIX_PULL",
            "parameters": {"minutes": 20},
            "dependencies": [],
        },
    ]
    plan = _prepare_approved(db, "ctx-pull", bare_steps)
    result = PlanExecutor(db).execute(plan.id)

    assert result.status == "SUCCEEDED"
    # 房间与发送者来自计划冻结的请求上下文
    assert pulled_kwargs["room_id"] == _room("ctx-pull")
    assert pulled_kwargs["sender"]  # 非空即可（计划 request_sender_id）


def test_prepare_plan_normalizes_matrix_pull_parameters():
    """prepare_plan 阶段就把 action_parameters 里的 room_id/sender 提升到
    顶层并用请求上下文补全缺失值——避免批准后执行时才失败。"""
    from app.services.tool_adapters.approval_tools import _normalize_matrix_pull_steps
    from app.services.message_context import MessageContext

    mctx = MessageContext(
        channel="matrix",
        channel_account_id="default",
        conversation_id="!ctx-room:hubtel.xyz",
        message_id="$ctx-evt:hubtel.xyz",
        sender_id="@ctx-sender:hubtel.xyz",
        content_sha256="1" * 64,
    )
    steps = [
        {
            "step_key": "step-1-pull",
            "action_type": "MATRIX_PULL",
            "parameters": {
                "action_parameters": {
                    "filename": "crypto-trader-web.tar.gz",
                    "minutes": 15,
                    "room_id": "!explicit:hubtel.xyz",
                }
            },
            "dependencies": [],
        },
        {
            "step_key": "step-2-update",
            "action_type": "SERVICE_CONTROL",
            "parameters": {"action_parameters": {"control_action": "update"}},
            "dependencies": ["step-1-pull"],
        },
    ]
    normalized = _normalize_matrix_pull_steps(steps, mctx)

    pull_params = normalized[0]["parameters"]
    # 显式 room_id 保留；sender 从计划上下文补全；其余 action_parameters 提升
    assert pull_params["room_id"] == "!explicit:hubtel.xyz"
    assert pull_params["sender"] == "@ctx-sender:hubtel.xyz"
    assert pull_params["filename"] == "crypto-trader-web.tar.gz"
    assert pull_params["minutes"] == 15
    assert "action_parameters" not in pull_params
    # 非 MATRIX_PULL 步骤不动
    assert normalized[1]["parameters"] == {"action_parameters": {"control_action": "update"}}

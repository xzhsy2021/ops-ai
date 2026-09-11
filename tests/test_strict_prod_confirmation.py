"""第 4 层生产额外确认（strict_prod_confirmation）与审批工具映射契约测试。

背景（2026-09-11 生产发版审计，两个真实缺陷）：

1. `strict_prod_confirmation` 之前只有默认值定义与 describe_capabilities 的对外
   暴露，**代码里没有任何强制点**——客户端看到 features.strict_prod_confirmation
   =true，实际却得不到任何额外校验。本文件锁定修复后的行为：生产环境下审批人
   除一次性短语外还必须给出固定从句 `我确认生产操作`，缺则该次执行被拒。

2. APPROVAL_TOOL_MAP / APPROVAL_TOOL_NAME_MAP 曾把 deploy_execute /
   package_cleanup / db_write / execute_rollback_plan / execute_deploy_plan 指向
   ops.approval.prepare_release / prepare_rollback / prepare_package_cleanup /
   prepare_dml——这四个工具从未注册。AI 客户端被 403 后照 guidance 调用不存在
   的工具，链路断在生产发布前，且报错是"工具不存在"而非"需要审批"。本文件
   锁定：映射只能指向真实注册的工具。

覆盖：
- 生产计划缺从句 → 拒绝，且 last_consume_error 指名从句；
- 生产计划带从句（专用参数 / 整行照抄）→ 通过；
- 测试环境不需要从句；
- 开关关闭时生产也不需要从句；
- 单动作审批（ActionApprovalService）同样受控；
- 审批工具映射不含未注册工具。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.services.approval_phrase import PROD_CONFIRM_CLAUSE  # noqa: E402
from app.services.execution_plan import ExecutionPlanService  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'prod_confirm.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    yield session
    session.close()
    engine.dispose()


def _seed_env(db, env="prod", server_ids=("prod-host-1",)):
    from app.db.models import SystemEnvironment

    row = SystemEnvironment(system_name="t-sys", name=env, category=env)
    row.servers = [{"id": sid} for sid in server_ids]
    db.add(row)
    db.commit()
    return row


def _steps():
    return [
        {
            "step_key": "release",
            "action_type": "RELEASE",
            "parameters": {"targets": ["prod-host-1"]},
            "dependencies": [],
        }
    ]


def _prepare_plan(db, suffix="a", environment="prod", targets=("prod-host-1",)):
    service = ExecutionPlanService(db)
    return service, service.prepare(
        room_id=f"!room-{suffix}:x",
        request_event_id=f"$evt-{suffix}:x",
        content_sha256="a" * 64,
        system_name="t-sys",
        service_name=None,
        environment=environment,
        targets=list(targets),
        steps=_steps(),
        policy={},
        routing_config_revision=f"rev-{suffix}",
        routing_ticket_digest=f"digest-{suffix}",
        risk_level="high",
        authorized_matrix_users=["@alice:x"],
    )


# ── 计划链路 ────────────────────────────────────────────────────────────

def test_prod_plan_requires_extra_clause(db):
    _seed_env(db)
    service, (plan, short_code) = _prepare_plan(db)

    assert service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:x",
        room_id="!room-a:x",
        approval_event_id="$evt-a:x",
    ) is None
    assert PROD_CONFIRM_CLAUSE in service.last_consume_error
    assert plan.status == "PENDING_APPROVAL"


def test_prod_plan_accepts_dedicated_clause_param(db):
    _seed_env(db)
    service, (plan, short_code) = _prepare_plan(db, suffix="b")

    result = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:x",
        room_id="!room-b:x",
        approval_event_id="$evt-b:x",
        prod_confirm_text=PROD_CONFIRM_CLAUSE,
    )
    assert result is not None
    assert result.status == "APPROVED"


def test_prod_plan_accepts_whole_line_copy(db):
    """审批人把「批准 <短语> <从句>」整行照抄时，从句被剥离后短语仍能校验。"""
    _seed_env(db)
    service, (plan, short_code) = _prepare_plan(db, suffix="c")

    result = service.consume(
        plan_id=plan.id,
        short_code=f"{short_code} {PROD_CONFIRM_CLAUSE}",
        approver_matrix_id="@alice:x",
        room_id="!room-c:x",
        approval_event_id="$evt-c:x",
    )
    assert result is not None
    assert result.status == "APPROVED"


def test_lone_clause_is_not_enough(db):
    """只给从句、不给一次性短语 → 仍然拒绝（从句不能替代短语）。"""
    _seed_env(db)
    service, (plan, short_code) = _prepare_plan(db, suffix="d")

    result = service.consume(
        plan_id=plan.id,
        short_code=PROD_CONFIRM_CLAUSE,
        approver_matrix_id="@alice:x",
        room_id="!room-d:x",
        approval_event_id="$evt-d:x",
        prod_confirm_text=PROD_CONFIRM_CLAUSE,
    )
    assert result is None


def test_test_env_needs_no_clause(db):
    _seed_env(db, env="test", server_ids=("test-host-1",))
    service, (plan, short_code) = _prepare_plan(
        db, suffix="e", environment="test", targets=("test-host-1",)
    )

    result = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:x",
        room_id="!room-e:x",
        approval_event_id="$evt-e:x",
    )
    assert result is not None
    assert result.status == "APPROVED"


def test_setting_off_disables_clause_for_prod(db):
    from app.services.tool_policy import save_capability_settings

    save_capability_settings(db, {"strict_prod_confirmation": False})
    _seed_env(db)
    service, (plan, short_code) = _prepare_plan(db, suffix="f")

    result = service.consume(
        plan_id=plan.id,
        short_code=short_code,
        approver_matrix_id="@alice:x",
        room_id="!room-f:x",
        approval_event_id="$evt-f:x",
    )
    assert result is not None
    assert result.status == "APPROVED"


# ── 单动作审批链路 ──────────────────────────────────────────────────────

def _prepare_action(db, environment="prod"):
    from app.services.action_approval import ActionApprovalService

    service = ActionApprovalService(db)
    approval, short_code = service.prepare(
        action_type="SERVICE_CONTROL",
        tool_name="ops.approval.prepare_service_control",
        room_id="!action:x",
        request_event_id="$action:x",
        content_sha256="b" * 64,
        system_name="t-sys",
        service_name="api",
        environment=environment,
        targets=["prod-host-1"],
        action_parameters={"control_action": "restart"},
        authorized_matrix_users=["@alice:x"],
    )
    return service, approval, short_code


def test_action_approval_requires_clause_in_prod(db):
    service, approval, short_code = _prepare_action(db)

    assert service.consume(
        approval_id=approval.id,
        short_code=short_code,
        approver_matrix_id="@alice:x",
        room_id="!action:x",
        approval_event_id="$action:x",
    ) is None
    assert PROD_CONFIRM_CLAUSE in service.last_consume_error

    result = service.consume(
        approval_id=approval.id,
        short_code=short_code,
        approver_matrix_id="@alice:x",
        room_id="!action:x",
        approval_event_id="$action:x",
        prod_confirm_text=PROD_CONFIRM_CLAUSE,
    )
    assert result is not None
    assert result.status in ("APPROVED", "EXECUTING")


def test_action_approval_test_env_unaffected(db):
    service, approval, short_code = _prepare_action(db, environment="test")

    result = service.consume(
        approval_id=approval.id,
        short_code=short_code,
        approver_matrix_id="@alice:x",
        room_id="!action:x",
        approval_event_id="$action:x",
    )
    assert result is not None
    assert result.status in ("APPROVED", "EXECUTING")


# ── 审批工具映射（问题 2）──────────────────────────────────────────────

def test_approval_tool_map_only_targets_registered_tools():
    from app.services.tool_policy import APPROVAL_TOOL_MAP, APPROVAL_TOOL_NAME_MAP
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    mapped = list(APPROVAL_TOOL_MAP.values()) + list(APPROVAL_TOOL_NAME_MAP.values())
    for name in mapped:
        if not name:
            continue  # 空值 = 无审批工具可解锁，走通用提示
        registry.get(name)  # 未注册会抛异常 → 测试失败


def test_deploy_execute_points_at_plan_tool_not_missing_release_tool():
    from app.services.tool_policy import _find_approval_tool_for
    from app.services.tool_registry import register_builtin_tools

    register_builtin_tools()
    tool = SimpleNamespace(
        name="ops.execute_deploy_plan", category="deploy_execute",
        risk="critical", write=True, requires_human_approval=True,
    )
    assert _find_approval_tool_for(tool) == "ops.approval.prepare_plan"


def test_unregistered_approval_tool_is_never_advertised():
    """防御：映射若日后指向未注册工具，guidance 必须退化为通用提示而非死链。"""
    from app.services.tool_policy import _approval_tool_registered, _find_approval_tool_for
    from app.services.tool_registry import register_builtin_tools

    register_builtin_tools()
    assert _approval_tool_registered("ops.approval.prepare_release") is False
    assert _approval_tool_registered("ops.approval.prepare_rollback") is False
    tool = SimpleNamespace(name="ops.cancel_deployment", category="deploy_execute")
    assert _find_approval_tool_for(tool) == ""


# ── 回执模板 ────────────────────────────────────────────────────────────

def test_reply_template_includes_clause_when_prod():
    from app.services.tool_adapters.approval_tools import _approval_reply_template

    text = _approval_reply_template(
        kind="plan", status="PENDING_APPROVAL", object_id="p1",
        short_code="批准发布 t-sys@prod ABCD1234",
        system_name="t-sys", service_name="", environment="prod",
        targets=["prod-host-1"], expires_at=None,
        steps=[{"action_type": "RELEASE"}], approvers=["@alice:x"],
        prod_confirm_text=PROD_CONFIRM_CLAUSE,
    )
    assert PROD_CONFIRM_CLAUSE in text
    # 可整行照抄：短语与从句出现在同一行
    assert f"批准 批准发布 t-sys@prod ABCD1234 {PROD_CONFIRM_CLAUSE}" in text


def test_reply_template_omits_clause_when_not_prod():
    from app.services.tool_adapters.approval_tools import _approval_reply_template

    text = _approval_reply_template(
        kind="plan", status="PENDING_APPROVAL", object_id="p1",
        short_code="批准发布 t-sys@test ABCD1234",
        system_name="t-sys", service_name="", environment="test",
        targets=["test-host-1"], expires_at=None,
        steps=[{"action_type": "RELEASE"}], approvers=["@alice:x"],
    )
    assert PROD_CONFIRM_CLAUSE not in text

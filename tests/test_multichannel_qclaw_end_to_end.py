"""Task 12: 多通道 qclaw 端到端验收测试。

对 matrix/wechat/telegram 三个通道参数化同一工作流：
帮助发现 → 路由票据绑定 → 通道包上传 → 单次执行计划审批（含 FILE_UPLOAD）
→ 受益人自审批消费 → 有序执行 → 结果检索；
并覆盖授权过期回退、跨通道/跨会话拒绝与静态 schema 契约。
"""
import hashlib
import json
import uuid
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.db.models import System, SystemEnvironment, ExecutionPlan
from app.services.message_context import MessageContext
from app.services.qclaw_routing import compute_routing_revision, issue_ticket
from app.services.tool_context import ToolContext
from app.services.tool_registry import registry, register_builtin_tools
from app.services.tool_token import resolve_approver_identities, resolve_channel_bindings
from app.services.temporary_approval import TemporaryApprovalService

_RUN_ID = uuid.uuid4().hex[:8]

register_builtin_tools()

CHANNEL_APPROVERS = {
    "matrix": ("default", "@owner:matrix.org"),
    "wechat": ("primary", "owner-wechat"),
    "telegram": ("primary", "owner-telegram"),
}


@pytest.fixture(autouse=True)
def _strong_signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        "multichannel-e2e-test-key-0123456789abcdef",
    )


@pytest.fixture(autouse=True)
def _system_config(monkeypatch):
    """Phase 2：审批人/房间统一到系统级 message_routing。

    注入干净的三通道配置（不设 rooms，避免共享测试库真实 rooms 拦截测试会话），
    并让临时授权服务的审批人解析与此同源，保证确定性。
    """
    from app.services.tool_adapters import approval_tools
    from app.services.temporary_approval import TemporaryApprovalService

    config = {
        "crypto-trader": {
            "name": "crypto-trader",
            "message_routing": {
                "enabled": True,
                "aliases": ["量化"],
                "keywords": [],
                "priority": 10,
                "approvers": [
                    {"channel": "matrix", "channel_account_id": "default", "sender_id": "@owner:matrix.org"},
                    {"channel": "wechat", "channel_account_id": "primary", "sender_id": "owner-wechat"},
                    {"channel": "telegram", "channel_account_id": "primary", "sender_id": "owner-telegram"},
                ],
            },
            "services": [],
        }
    }
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: config)

    def _configured(self, system, context):
        routing = config.get(system.name, {}).get("message_routing", {})
        return [
            item for item in routing.get("approvers", [])
            if item["channel"] == context.channel
            and item["channel_account_id"] == context.channel_account_id
        ]

    monkeypatch.setattr(
        TemporaryApprovalService, "_configured_approvers", _configured,
    )


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    existing = session.query(System).filter(System.name == "crypto-trader").first()
    if existing is None:
        session.add(System(
            name="crypto-trader",
            display_name="Crypto Trader",
            message_routing=_three_channel_routing(),
        ))
        session.add_all([
            SystemEnvironment(system_name="crypto-trader", name="test", category="test"),
            SystemEnvironment(system_name="crypto-trader", name="prod", category="prod"),
        ])
    else:
        # 全局库可能已有历史系统行（例如仅 matrix 审批人），合并补全三通道审批人
        routing = dict(existing.message_routing or {})
        existing_approvers = list((routing.get("approvers") if isinstance(routing.get("approvers"), list) else None) or [])
        wanted = _three_channel_routing()["approvers"]
        existing_keys = {
            (item.get("channel"), item.get("channel_account_id"), item.get("sender_id"))
            for item in existing_approvers if isinstance(item, dict)
        }
        for item in wanted:
            key = (item["channel"], item["channel_account_id"], item["sender_id"])
            if key not in existing_keys:
                existing_approvers.append(item)
        routing["approvers"] = existing_approvers
        existing.message_routing = routing
        # test 环境必须存在且 category 为 test（真实库可能把已有环境标为 custom/prod）
        test_env = session.query(SystemEnvironment).filter(
            SystemEnvironment.system_name == "crypto-trader",
            SystemEnvironment.name == "test",
        ).first()
        if test_env is None:
            session.add(SystemEnvironment(system_name="crypto-trader", name="test", category="test"))
        elif str(test_env.category or "").strip().lower() != "test":
            test_env.category = "test"
    session.commit()
    yield session
    session.rollback()
    session.close()


def _three_channel_routing() -> dict:
    return {
        "enabled": True,
        "aliases": ["量化"],
        "keywords": [],
        "priority": 10,
        "approvers": [
            {"channel": "matrix", "channel_account_id": "default", "sender_id": "@owner:matrix.org"},
            {"channel": "wechat", "channel_account_id": "primary", "sender_id": "owner-wechat"},
            {"channel": "telegram", "channel_account_id": "primary", "sender_id": "owner-telegram"},
        ],
    }


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _context(channel: str, *, suffix: str = "e2e") -> MessageContext:
    account, _ = CHANNEL_APPROVERS[channel]
    return MessageContext(
        channel=channel,
        channel_account_id=account,
        conversation_id=_room(f"{channel}-{suffix}"),
        message_id=_event(f"{channel}-{suffix}"),
        sender_id=f"requester-{channel}",
        content_sha256=_content_hash(f"message-{channel}-{suffix}"),
    )


def _approver_context(channel: str, context: MessageContext, *, suffix: str = "approve") -> MessageContext:
    _, approver = CHANNEL_APPROVERS[channel]
    return MessageContext(
        channel=context.channel,
        channel_account_id=context.channel_account_id,
        conversation_id=context.conversation_id,
        message_id=_event(f"{channel}-{suffix}"),
        sender_id=approver,
        content_sha256="a" * 64,
    )


def _ctx(channel: str, context: MessageContext):
    return ToolContext(
        auth_type="tool_token",
        token_name="e2e-token",
        token_owner="qclaw",
        allow_write=True,
        scopes=["ops:read", "ops:write"],
        channel_bindings=[{
            "channel": context.channel,
            "channel_account_id": context.channel_account_id,
            "conversation_id": context.conversation_id,
        }],
        approver_identities=[
            {
                "channel": channel,
                "channel_account_id": CHANNEL_APPROVERS[channel][0],
                "sender_id": CHANNEL_APPROVERS[channel][1],
            }
        ],
    )


def _activate_grant(db, context: MessageContext) -> str:
    """通过服务层创建并确认一个 ACTIVE 临时审批授权，返回 grant id。"""
    _, approver = CHANNEL_APPROVERS[context.channel]
    service = TemporaryApprovalService(db)
    grant, code = service.request(
        message_context=context,
        beneficiary_actor_key=context.actor_key,
        system_name="crypto-trader",
        environment_name="test",
        allowed_actions=["FILE_UPLOAD", "RELEASE", "SERVICE_CONTROL", "HEALTH_CHECK"],
        reason="urgent test integration",
        authorized_identities=[
            {
                "channel": context.channel,
                "channel_account_id": context.channel_account_id,
                "sender_id": approver,
            }
        ],
    )
    confirmation = _approver_context(context.channel, context, suffix="grant-confirm")
    active = service.confirm(
        grant.id,
        code,
        actor_key=confirmation.actor_key,
        message_context=confirmation,
    )
    assert active is not None and active.status == "ACTIVE"
    return grant.id


def _steps(package_name: str) -> list[dict]:
    return [
        {
            "step_key": "upload",
            "action_type": "FILE_UPLOAD",
            "parameters": {
                "action_parameters": {
                    "package_name": package_name,
                    "remote_path": "/srv/releases/frontend.tar.gz",
                    "overwrite": False,
                }
            },
            "dependencies": [],
        },
        {
            "step_key": "restart",
            "action_type": "SERVICE_CONTROL",
            "parameters": {"control_action": "restart", "targets": ["s1"]},
            "dependencies": ["upload"],
        },
        {
            "step_key": "health",
            "action_type": "HEALTH_CHECK",
            "parameters": {"targets": ["s1"]},
            "dependencies": ["restart"],
        },
    ]


def _prepare_args(context: MessageContext, package_name: str, *, with_grant: bool = False, **overrides):
    from app.services.tool_adapters.approval_tools import _routing_systems

    ticket = issue_ticket(
        context,
        "crypto-trader",
        "api",
        compute_routing_revision(_routing_systems()),
    )
    defaults = dict(
        message_context=context.to_dict(),
        routing_ticket=ticket.ticket,
        system_name="crypto-trader",
        service_name="api",
        environment="test",
        targets=["s1"],
        steps=_steps(package_name),
        policy={"continue_on_error": False, "max_retries": 0},
        risk_level="high",
        ai_reason="multichannel e2e self-approval",
    )
    defaults.update(overrides)
    return defaults


def _upload_channel_package(db, context: MessageContext, monkeypatch, tmp_path) -> str:
    """通过 /api/v2/tools/packages/upload 端点逻辑上传通道包，返回 package_name。"""
    from app.api import tools as tools_api
    from app.services import package_retention
    from app.services.tool_adapters import file_transfer_tools

    uploads = tmp_path / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    ctx = SimpleNamespace(
        auth_type="tool_token",
        scopes=["ops:read"],
        channel_bindings=[{
            "channel": context.channel,
            "channel_account_id": context.channel_account_id,
            "conversation_id": context.conversation_id,
        }],
        username="qclaw",
        token_owner="qclaw",
        client_name="qclaw",
        has_scope=lambda scope: scope == "ops:read",
    )
    monkeypatch.setattr(tools_api, "get_tool_context", lambda request, db: ctx)
    monkeypatch.setattr(tools_api, "register_builtin_tools", lambda: None)
    monkeypatch.setattr(tools_api, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        package_retention,
        "get_runtime_path",
        lambda env, default: str(uploads),
    )
    monkeypatch.setattr(
        file_transfer_tools,
        "get_runtime_path",
        lambda env, default: str(uploads),
    )

    content = b"e2e-package-content"
    package_sha256 = hashlib.sha256(content).hexdigest()

    class _Upload:
        def __init__(self, filename: str, payload: bytes):
            self.filename = filename
            self.file = BytesIO(payload)

        async def seek(self, offset: int):
            self.file.seek(offset)

    import asyncio
    response = asyncio.run(tools_api.upload_package_by_tool_token(
        request=SimpleNamespace(),
        file=_Upload("frontend.tar.gz", content),
        system="crypto-trader",
        service="crypto-frontend",
        overwrite=False,
        approval_intake=True,
        message_context=json.dumps(context.to_dict()),
        package_sha256=package_sha256,
        db=db,
    ))
    assert response["data"]["result"]["approval_intake"] is True
    return response["data"]["result"]["package_name"]


def _consume(db, plan_id: str, short_code: str, context: MessageContext, *, suffix: str = "consume"):
    from app.services.execution_plan import ExecutionPlanService

    service = ExecutionPlanService(db)
    return service.consume(
        plan_id=plan_id,
        short_code=short_code,
        approver_matrix_id=context.sender_id,
        room_id=context.conversation_id,
        approval_event_id=_event(f"{context.channel}-{suffix}"),
        approval_context=context,
    )


# ── 端到端验收：三通道同一工作流 ──

@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_channel_update_uses_one_self_approval(channel, db, monkeypatch, tmp_path):
    from app.services.tool_adapters.approval_tools import approval_prepare_plan

    context = _context(channel, suffix="update")
    grant_id = _activate_grant(db, context)
    package_name = _upload_channel_package(db, context, monkeypatch, tmp_path)
    ctx = _ctx(channel, context)

    prepared = approval_prepare_plan(
        _prepare_args(context, package_name, with_grant=True),
        ctx=ctx,
        db=db,
    )
    assert prepared["temporary_grant_id"] == grant_id
    assert prepared["step_count"] == 3

    approved = _consume(db, prepared["plan_id"], prepared["short_code"], context)
    assert approved is not None
    assert approved.status == "APPROVED"
    assert approved.temporary_grant_id == grant_id
    assert approved.approved_by == context.actor_key
    file_upload_steps = [s for s in approved.steps if s.action_type == "FILE_UPLOAD"]
    assert len(file_upload_steps) == 1
    assert file_upload_steps[0].parameters["action_parameters"]["package_name"] == package_name


@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_ordered_execution_and_result_retrieval(channel, db, monkeypatch, tmp_path):
    from app.services.plan_executor import PlanExecutor
    from app.services.tool_adapters.approval_tools import approval_prepare_plan

    context = _context(channel, suffix="exec")
    _activate_grant(db, context)
    package_name = _upload_channel_package(db, context, monkeypatch, tmp_path)
    ctx = _ctx(channel, context)
    prepared = approval_prepare_plan(
        _prepare_args(context, package_name),
        ctx=ctx,
        db=db,
    )
    approved = _consume(db, prepared["plan_id"], prepared["short_code"], context)
    assert approved is not None and approved.status == "APPROVED"

    order: list[str] = []

    def handler(plan, step, db):
        order.append(step.step_key)
        return {"ok": True}

    custom = {"FILE_UPLOAD": handler, "SERVICE_CONTROL": handler, "HEALTH_CHECK": handler}
    result = PlanExecutor(db, handlers=custom).execute(prepared["plan_id"])
    assert result is not None
    assert result.status == "SUCCEEDED"
    assert order == ["upload", "restart", "health"]
    assert all(s.status == "SUCCEEDED" for s in result.steps)

    # 结果检索：从数据库读取已执行计划与步骤结果
    row = db.query(ExecutionPlan).filter(ExecutionPlan.id == prepared["plan_id"]).one()
    assert row.status == "SUCCEEDED"
    assert row.consumed_at is not None
    assert row.execution_job_id is not None
    assert all(step.status == "SUCCEEDED" and step.result is not None for step in row.steps)


@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_cross_channel_consume_rejected(channel, db, monkeypatch, tmp_path):
    from app.services.tool_adapters.approval_tools import approval_prepare_plan

    context = _context(channel, suffix="cross")
    _activate_grant(db, context)
    package_name = _upload_channel_package(db, context, monkeypatch, tmp_path)
    ctx = _ctx(channel, context)
    prepared = approval_prepare_plan(
        _prepare_args(context, package_name),
        ctx=ctx,
        db=db,
    )

    other = "wechat" if channel != "wechat" else "telegram"
    other_context = _context(other, suffix="intruder")
    # 跨通道/跨会话消费必须失败
    assert _consume(db, prepared["plan_id"], prepared["short_code"], other_context, suffix="intruder") is None
    # 同通道但另一发送者（非受益人、非审批人）消费也必须失败
    same_channel_intruder = MessageContext(
        channel=context.channel,
        channel_account_id=context.channel_account_id,
        conversation_id=context.conversation_id,
        message_id=_event(f"{channel}-intruder-2"),
        sender_id="mallory",
        content_sha256="a" * 64,
    )
    assert _consume(db, prepared["plan_id"], prepared["short_code"], same_channel_intruder, suffix="intruder2") is None
    plan = db.query(ExecutionPlan).filter(ExecutionPlan.id == prepared["plan_id"]).one()
    assert plan.status == "PENDING_APPROVAL"


@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_grant_expiry_falls_back_to_original_approver(channel, db, monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    from app.db.models import TemporaryApprovalGrant
    from app.services.tool_adapters.approval_tools import approval_prepare_plan

    context = _context(channel, suffix="expiry")
    grant_id = _activate_grant(db, context)
    package_name = _upload_channel_package(db, context, monkeypatch, tmp_path)
    ctx = _ctx(channel, context)
    prepared = approval_prepare_plan(
        _prepare_args(context, package_name),
        ctx=ctx,
        db=db,
    )

    # 使授权过期 → 受益人不能消费
    grant = db.query(TemporaryApprovalGrant).filter(TemporaryApprovalGrant.id == grant_id).one()
    grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    assert _consume(db, prepared["plan_id"], prepared["short_code"], context) is None
    # 原始审批人仍可消费已有计划
    owner_context = _approver_context(channel, context, suffix="owner-consume")
    approved = _consume(db, prepared["plan_id"], prepared["short_code"], owner_context, suffix="owner")
    assert approved is not None
    assert approved.status == "APPROVED"


def test_help_discovery_lists_self_approval_capability(db):
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "topic": "临时审批",
            "message_context": _context("matrix", suffix="help").to_dict(),
        },
        ctx=_ctx("matrix", _context("matrix", suffix="help")),
        db=db,
    )
    assert result["ok"] is True
    caps = result.get("capabilities") or []
    names = {item.get("name") for item in caps if isinstance(item, dict)}
    assert "ops.approval.temporary_access" in names
    assert "ops.approval.prepare_plan" in names


def test_help_output_never_leaks_secrets(db):
    from app.services.tool_adapters.help_tools import help_query

    context = _context("matrix", suffix="secret-check")
    _activate_grant(db, context)
    result = help_query(
        args={"include_all": True, "message_context": context.to_dict()},
        ctx=_ctx("matrix", context),
        db=db,
    )
    payload = json.dumps(result, ensure_ascii=False)
    for marker in ("confirmation_code_hash", "token=", "-----BEGIN", "private_key"):
        assert marker not in payload, marker
    assert "ops.ai." not in payload


def test_routing_ticket_binds_full_message_context(db):
    context = _context("wechat", suffix="ticket")
    other = _context("wechat", suffix="ticket-other", )
    from app.services.qclaw_routing import verify_ticket

    from app.services.tool_adapters.approval_tools import _routing_systems
    ticket = issue_ticket(
        context,
        "crypto-trader",
        "api",
        compute_routing_revision(_routing_systems()),
    )
    assert verify_ticket(
        ticket.ticket,
        expected_message_context=context,
        expected_system_name="crypto-trader",
        expected_service_name="api",
        expected_revision=compute_routing_revision(_routing_systems()),
    )
    assert not verify_ticket(
        ticket.ticket,
        expected_message_context=other,
        expected_system_name="crypto-trader",
        expected_service_name="api",
        expected_revision=compute_routing_revision(_routing_systems()),
    )


def test_routing_approval_package_schemas_are_channel_neutral():
    """路由/审批/包工具 schema 包含 message_context，且不引入新的 matrix_* 字段。"""
    for name in (
        "ops.routing.resolve_message_target",
        "ops.approval.prepare_plan",
        "ops.approval.prepare_file_upload",
        "ops.approval.prepare_service_control",
        "ops.approval.temporary_access",
        "ops.help.query",
    ):
        tool = registry.get(name)
        assert tool is not None, name
        props = (tool.input_schema or {}).get("properties") or {}
        assert "message_context" in props, f"{name} schema 缺少 message_context"
        for key in props:
            assert not key.startswith("matrix_"), f"{name} schema 引入了新的 matrix_* 字段: {key}"

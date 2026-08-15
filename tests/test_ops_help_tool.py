"""Contract tests for the contextual OPS help tool (ops.help.query)."""
import json
import uuid

import pytest

from app.db.base import SessionLocal, Base, engine
from app.db.migrations.runner import run_schema_migrations
from app.services.tool_registry import registry, register_builtin_tools
from app.services.tool_context import ToolContext
from app.db.models import System, SystemEnvironment, TemporaryApprovalGrant
from app.services.message_context import MessageContext
from app.services.tool_token import (
    resolve_approver_identities,
    resolve_channel_bindings,
)

_RUN_ID = uuid.uuid4().hex[:8]

register_builtin_tools()


@pytest.fixture(autouse=True)
def _strong_signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        "temp-self-approval-help-test-key-0123456789",
    )


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    existing = session.query(System).filter(System.name == "crypto-trader").first()
    # 测试需要 crypto-trader 系统的 matrix/default 审批人是 @owner:matrix.org。
    # 若系统已存在（例如本机 data/ops.db 已有真实配置），备份原 routing 并覆盖，
    # teardown 时恢复，保证测试不依赖本地数据库状态。
    original_routing = None
    if existing is None:
        session.add(System(
            name="crypto-trader",
            display_name="Crypto Trader",
            message_routing={
                "approvers": [{
                    "channel": "matrix",
                    "channel_account_id": "default",
                    "sender_id": "@owner:matrix.org",
                }],
            },
        ))
        session.add_all([
            SystemEnvironment(system_name="crypto-trader", name="test", category="test"),
            SystemEnvironment(system_name="crypto-trader", name="prod", category="prod"),
        ])
    else:
        original_routing = existing.message_routing
        routing = dict(existing.message_routing or {})
        routing["approvers"] = [{
            "channel": "matrix",
            "channel_account_id": "default",
            "sender_id": "@owner:matrix.org",
        }]
        existing.message_routing = routing
    session.commit()
    yield session
    if original_routing is not None:
        row = session.query(System).filter(System.name == "crypto-trader").first()
        if row is not None:
            row.message_routing = original_routing
            session.commit()
    session.rollback()
    session.close()


def _room(suffix: str) -> str:
    return f"!room-{_RUN_ID}-{suffix}:matrix.org"


def _event(suffix: str) -> str:
    return f"$evt-{_RUN_ID}-{suffix}:matrix.org"


def _context(*, message: str, sender="@requester:matrix.org") -> MessageContext:
    return MessageContext(
        channel="matrix",
        channel_account_id="default",
        conversation_id=_room(f"ctx-{message}"),
        message_id=message or _event("ctx"),
        sender_id=sender,
        content_sha256="a" * 64,
    )


def _ctx(approver_matrix_ids=None):
    return ToolContext(
        auth_type="tool_token",
        token_name="test-token",
        token_owner="test",
        allow_write=True,
        scopes=["ops:*"],
        channel_bindings=resolve_channel_bindings(legacy=[]),
        approver_identities=resolve_approver_identities(
            legacy=approver_matrix_ids or []
        ),
    )


# ── 注册契约 ──

def test_help_query_tool_is_registered():
    """ops.help.query 已注册为只读工具。"""
    tool = registry.get("ops.help.query")
    assert tool is not None
    assert tool.write is False
    props = tool.input_schema["properties"]
    for field in ("topic", "include_all", "system_name", "environment", "message_context"):
        assert field in props, f"ops.help.query schema 缺少 {field}"
    assert tool.input_schema["additionalProperties"] is False


# ── 上下文能力 ──

def test_help_returns_only_available_capabilities_in_context(db):
    """默认（非 include_all）只返回当前 token/channel 上下文可用的能力。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-context").to_dict(),
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    assert "capabilities" in result
    caps = result["capabilities"]
    assert isinstance(caps, list) and len(caps) > 0
    # 所有默认条目必须是可用的
    for cap in caps:
        assert cap.get("status") in ("available", "requires_authorization"), (
            f"默认帮助不应包含 forbidden 能力: {cap.get('name')} -> {cap.get('status')}"
        )


def test_help_include_all_marks_unavailable_capabilities(db):
    """include_all=true 时包含不可用能力并标注状态。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-all").to_dict(),
            "include_all": True,
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    caps = result["capabilities"]
    assert isinstance(caps, list) and len(caps) > 0
    statuses = {cap.get("status") for cap in caps}
    assert statuses <= {"available", "requires_authorization", "forbidden"}, statuses


# ── 主题过滤 ──

def test_help_topic_filter_temporary_approval(db):
    """topic=临时审批 只返回临时审批相关能力。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-topic-temp").to_dict(),
            "topic": "临时审批",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    names = [cap["name"] for cap in result["capabilities"]]
    assert any("temporary" in n or "approval" in n for n in names), names
    # 临时审批主题必须包含工具级指引
    assert result.get("topic") == "临时审批"


def test_help_topic_filter_temporary_grant(db):
    """topic=临时授权 必须命中临时授权相关能力（ops.approval.temporary_access）。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-topic-grant").to_dict(),
            "topic": "临时授权",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    names = [cap["name"] for cap in result["capabilities"]]
    assert "ops.approval.temporary_access" in names, names
    assert result.get("topic") == "临时授权"


def test_help_topic_filter_grant(db):
    """topic=授权 同样命中临时授权/审批能力。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-topic-grant-2").to_dict(),
            "topic": "授权",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    names = [cap["name"] for cap in result["capabilities"]]
    assert any("grant" in n or "approval" in n for n in names), names


def test_help_temporary_grant_returns_application_example(db):
    """topic=临时授权 的帮助输出包含完整的申请示例（自然语言消息 + 工具参数）。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-example-grant").to_dict(),
            "topic": "临时授权",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    examples = result.get("examples")
    assert isinstance(examples, list) and examples, "临时授权主题应返回申请示例"
    example = examples[0]
    # 自然语言申请消息
    assert "申请" in example.get("message", ""), example.get("message")
    assert example.get("tool") == "ops.approval.temporary_access"
    assert example.get("operation") == "request"
    args = example.get("arguments", {})
    assert args.get("system_name") == "crypto-trader"
    assert args.get("environment") == "test"
    assert "RELEASE" in args.get("allowed_actions", [])
    assert "SERVICE_CONTROL" in args.get("allowed_actions", [])
    assert args.get("duration_unit") == "week"
    assert "beneficiary_identity" in args
    # 示例不得泄露任何敏感信息
    payload = json.dumps(result, ensure_ascii=False)
    for marker in ("confirmation_code_hash", "short_code", "token=", "-----BEGIN", "private_key"):
        assert marker not in payload, marker


def test_help_topic_filter_deployment(db):
    """topic=部署 返回部署相关能力（deploy/plan/execution）。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-topic-deploy").to_dict(),
            "topic": "部署",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    names = [cap["name"] for cap in result["capabilities"]]
    assert any("deploy" in n or "plan" in n for n in names), names


def test_help_system_environment_scope(db):
    """指定 system_name/environment 时返回该系统的环境与审批人信息。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-sys-env").to_dict(),
            "system_name": "crypto-trader",
            "environment": "test",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    assert result.get("system_name") == "crypto-trader"
    assert result.get("environment") == "test"
    # 环境必须是 test 类（临时审批可用）
    envs = result.get("environments") or []
    assert any(env.get("name") == "test" for env in envs)


def test_help_missing_system_returns_guidance_not_plan(db):
    """缺失系统映射时返回所需信息而非创建计划。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-missing-sys").to_dict(),
            "system_name": "does-not-exist-xyz",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert result["ok"] is True
    assert result.get("system_found") is False
    assert result.get("required") is not None
    # 绝不应出现 plan_id
    assert "plan_id" not in result


# ── 秘密保护 ──

_SECRET_MARKERS = (
    "confirmation_code_hash",
    "pbkdf2_sha256",
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "access_token",
    "secret",
    "password",
)


def test_help_output_never_leaks_secrets(db):
    """输出绝不包含 token 值、凭据、私钥、审批码哈希或机密命令。"""
    from app.services.tool_adapters.help_tools import help_query

    for args in (
        {"message_context": _context(message="help-secret-1").to_dict()},
        {"message_context": _context(message="help-secret-2").to_dict(), "include_all": True},
        {"message_context": _context(message="help-secret-3").to_dict(), "topic": "临时审批"},
    ):
        result = help_query(args=args, ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]), db=db)
        rendered = str(result)
        for marker in _SECRET_MARKERS:
            assert marker.lower() not in rendered.lower(), f"帮助输出泄露敏感标记 {marker}: {args}"


def test_help_never_lists_ai_analysis_tools(db):
    """AI 分析工具永远不出现在帮助输出中。"""
    from app.services.tool_adapters.help_tools import help_query

    result = help_query(
        args={
            "message_context": _context(message="help-ai-absent").to_dict(),
            "include_all": True,
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    rendered = str(result)
    assert "ops.ai." not in rendered
    assert "analyze_diagnostics" not in rendered
    assert "ai_analysis" not in rendered


# ── 活跃临时授权可见性 ──

def test_help_shows_active_temporary_grants(db, monkeypatch):
    """指定系统时展示活跃临时授权（不含确认码）。"""
    from app.services.tool_adapters import approval_tools
    from app.services.tool_adapters.approval_tools import temporary_access
    from app.services.tool_adapters.help_tools import help_query

    # Phase 2：作用域由系统级 message_routing 决定；注入干净配置（不设 rooms），
    # 避免共享测试库真实 rooms 拦截临时授权申请。
    monkeypatch.setattr(
        approval_tools,
        "get_all_systems",
        lambda: {
            "crypto-trader": {
                "name": "crypto-trader",
                "message_routing": {
                    "enabled": True,
                    "aliases": ["量化"],
                    "keywords": [],
                    "priority": 10,
                    "approvers": [
                        {"channel": "matrix", "channel_account_id": "default", "sender_id": "@owner:matrix.org"},
                    ],
                },
                "services": [],
            }
        },
    )

    context = _context(message="help-grant")
    requested = temporary_access(
        args={
            "operation": "request",
            "message_context": context.to_dict(),
            "beneficiary_identity": context.actor_key,
            "system_name": "crypto-trader",
            "environment": "test",
            "allowed_actions": ["SERVICE_CONTROL"],
            "reason": "urgent restart",
            "duration_value": 1,
            "duration_unit": "day",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert requested["ok"] is True
    grant_id = requested["grant_id"]
    owner_ctx = MessageContext(
        channel=context.channel,
        channel_account_id=context.channel_account_id,
        conversation_id=context.conversation_id,
        message_id="help-grant-confirm",
        sender_id="@owner:matrix.org",
        content_sha256="a" * 64,
    )
    confirmed = temporary_access(
        args={
            "operation": "confirm",
            "message_context": owner_ctx.to_dict(),
            "grant_id": grant_id,
            "short_code": requested["short_code"],
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    assert confirmed["ok"] is True

    result = help_query(
        args={
            "message_context": _context(message="help-grant-view").to_dict(),
            "system_name": "crypto-trader",
            "environment": "test",
        },
        ctx=_ctx(approver_matrix_ids=["@owner:matrix.org"]),
        db=db,
    )
    grants = result.get("active_grants") or []
    assert any(g.get("id") == grant_id for g in grants), grants
    assert "confirmation_code_hash" not in str(result)

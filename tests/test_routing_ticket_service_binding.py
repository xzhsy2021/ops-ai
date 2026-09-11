"""路由票据的 system/service 绑定校验，以及失败原因的精确化。

背景（2026-09-11 17:48–17:51 生产实测）：agent 连续三次 prepare_plan 被
「Routing ticket is invalid, expired, or bound to another message target」拒绝。
从 tool_call_logs 解码票据后逐字段比对，结论是：

* message_context 六个字段（channel/account/conversation/message_id/sender/digest）**全部一致**；
* 票据是刚签发、远未过期（expires_at 在 15 分钟后）；
* 唯一差异是 **service_name**：票据绑定 `transaction`/`trader`，调用却传了 `system`。

原因是用户的请求（「测试环境拉取 system, transaction, strategy…」）覆盖多个服务，
关键词路由只绑定其中一个，agent 想按自己的目标服务传参，于是被判"绑定了另一个目标"。
旧错误信息把「无效 / 过期 / 绑定到别的目标」三种可能混在同一句里，调用方无法定位该改
哪个字段，只能盲试。

本文件锁定修复后的行为：
1. resolve 在 next_step 里显式提示票据绑定的 service_name 与正确用法；
2. prepare_* 遇到 service_name 不一致时，403 信息指名该字段、两个取值与修正方法；
3. 不传 service_name（系统级/多服务计划）时，票据自动沿用绑定值，可以正常建单；
4. 过期票据与上下文不一致也各自给出可定位的原因。
"""
import base64
import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException

from app.services import qclaw_routing
from app.services.tool_adapters import approval_tools
from app.services.tool_registry import register_builtin_tools
from tests.test_multichannel_routing_tools import _identity
from tests.test_routing_sha_autocompute import (
    STRONG_KEY,
    _ctx,
    _seed_test_env,
    _systems_kw,
)

register_builtin_tools()

ROOM = "!LXnIFCTfJIqeErqyaf:hubtel.xyz"
EVENT = "$cmKQ44UU4gz2j6gpSahxfW1y3-bbZ9mIHXhRfpNKFkk"
SENDER = "@leo:hubtel.xyz"
# 命中服务级关键词：消息里出现 "transaction"，而它正好是服务规范名
MSG = "智能助手AIbot 量化测试 transaction 拉取配置"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.qclaw_routing.QCLAW_APPROVAL_SIGNING_KEY",
        STRONG_KEY,
    )


def _approver():
    return _identity("matrix", SENDER, account="default")


def _systems_with_service(approvers=None):
    """关键词里含服务规范名 → 命中 system_keyword_service → 服务级票据。"""
    systems = _systems_kw([approvers or _approver()])
    routing = systems["crypto-trader"]["message_routing"]
    routing["keywords"] = ["量化测试", "transaction"]
    systems["crypto-trader"]["services"] = [{"name": "transaction"}, {"name": "system"}]
    return systems


def _message_context(message_text, *, message_id=EVENT):
    return {
        "channel": "matrix",
        "channel_account_id": "default",
        "conversation_id": ROOM,
        "message_id": message_id,
        "sender_id": SENDER,
        "content_sha256": hashlib.sha256(message_text.encode("utf-8")).hexdigest(),
    }


def _resolve(monkeypatch, *, message_text=MSG, message_id=EVENT):
    systems = _systems_with_service()
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: systems)
    monkeypatch.setattr(
        "app.services.tool_adapters.approval_tools._routing_systems",
        lambda: list(systems.values()),
    )
    return approval_tools.routing_resolve_message_target(
        {
            "message_text": message_text,
            "message_context": _message_context(message_text, message_id=message_id),
        },
        _ctx(),
        None,
    )


def _resign(ticket, mutate):
    """解码票据、改字段后用同一签名密钥重签（用于构造过期等场景）。"""
    payload_b64, _sig = ticket.rsplit(".", 1)
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    mutate(payload)
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    key = qclaw_routing._get_signing_key().encode("utf-8")
    sig = hmac.new(key, raw.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _prepare(monkeypatch, *, service_name="__from_resolve__", message_context=None, ticket=None):
    from app.db.base import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    resolved = _resolve(monkeypatch)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    _seed_test_env(db, resolved["system_name"])
    args = {
        "message_context": dict(message_context or resolved["message_context"]),
        "routing_ticket": ticket or resolved["ticket"],
        "system_name": resolved["system_name"],
        "environment": "test",
        "steps": [
            {
                "step_key": "restart",
                "action_type": "SERVICE_CONTROL",
                "parameters": {"action": "restart", "targets": ["transaction"]},
            }
        ],
        "policy": {"continue_on_error": False},
    }
    if service_name == "__from_resolve__":
        if resolved["service_name"]:
            args["service_name"] = resolved["service_name"]
    elif service_name is not None:
        args["service_name"] = service_name
    try:
        return approval_tools.approval_prepare_plan(args=args, ctx=_ctx(), db=db), resolved
    finally:
        db.close()


def test_resolve_binds_service_from_keyword(monkeypatch):
    """消息里的服务名关键词会绑定进票据（复现生产场景的前提）。"""
    resolved = _resolve(monkeypatch)
    assert resolved["outcome"] == "RESOLVED"
    assert resolved["service_name"] == "transaction"
    assert resolved["matched_by"] == "system_keyword_service"


def test_resolve_next_step_warns_about_service_binding(monkeypatch):
    """resolve 必须提前讲清 service_name 的传参规则，避免下一次盲撞 403。"""
    resolved = _resolve(monkeypatch)
    next_step = resolved["next_step"]
    assert "transaction" in next_step
    assert "不传" in next_step
    assert "403" in next_step


def test_prepare_plan_rejects_other_service_with_specific_reason(monkeypatch):
    """复现 17:48–17:51：票据绑 transaction、请求传 system → 403 且指名该字段。"""
    with pytest.raises(HTTPException) as exc:
        _prepare(monkeypatch, service_name="system")
    assert exc.value.status_code == 403
    detail = str(exc.value.detail)
    assert "service_name 不一致" in detail
    assert "transaction" in detail and "system" in detail
    assert "不要" in detail  # 修正指引：多服务/系统级计划不要传 service_name


def test_prepare_plan_accepts_omitted_service_name(monkeypatch):
    """不传 service_name 时票据自动沿用绑定值 → 系统级/多服务计划可正常建单。"""
    prepared, resolved = _prepare(monkeypatch, service_name=None)
    assert prepared["plan_id"]
    assert resolved["service_name"] == "transaction"


def test_prepare_plan_accepts_matching_service_name(monkeypatch):
    """传了与票据一致的服务名同样通过。"""
    prepared, _ = _prepare(monkeypatch, service_name="transaction")
    assert prepared["plan_id"]


def test_prepare_plan_reason_names_expired_ticket(monkeypatch):
    """过期票据单独报"已过期"，不再与"绑定到别的目标"混为一谈。"""
    resolved = _resolve(monkeypatch)
    expired = _resign(
        resolved["ticket"], lambda p: p.update({"expires_at": "2026-01-01T00:00:00+00:00"})
    )
    with pytest.raises(HTTPException) as exc:
        _prepare(monkeypatch, ticket=expired)
    assert exc.value.status_code == 403
    assert "已过期" in str(exc.value.detail)


def test_prepare_plan_reason_names_message_context_field(monkeypatch):
    """上下文不一致时指名具体字段（这里是 message_id）。"""
    resolved = _resolve(monkeypatch)
    wrong = dict(resolved["message_context"])
    wrong["message_id"] = "$another-event:hubtel.xyz"
    with pytest.raises(HTTPException) as exc:
        _prepare(monkeypatch, message_context=wrong)
    assert exc.value.status_code == 403
    detail = str(exc.value.detail)
    assert "message_id" in detail


# ──────────────────────────────────────────────────────────────
# 批量发版：一条消息提到多个服务时的解析
# ──────────────────────────────────────────────────────────────

BATCH_MSG = "智能助手AIbot: 测试环境拉取system, transaction, strategy 最新镜像，按顺序部署，transaction，strategy 两台都部署"


def _systems_multi(approvers=None):
    systems = _systems_kw([approvers or _approver()])
    routing = systems["crypto-trader"]["message_routing"]
    routing["keywords"] = ["量化测试", "system", "transaction", "strategy"]
    systems["crypto-trader"]["services"] = [
        {"name": "system"},
        {"name": "transaction"},
        {"name": "strategy"},
    ]
    return systems


def _resolve_multi(monkeypatch, message_text=BATCH_MSG):
    systems = _systems_multi()
    monkeypatch.setattr(approval_tools, "get_all_systems", lambda: systems)
    monkeypatch.setattr(
        "app.services.tool_adapters.approval_tools._routing_systems",
        lambda: list(systems.values()),
    )
    return approval_tools.routing_resolve_message_target(
        {"message_text": message_text, "message_context": _message_context(message_text)},
        _ctx(),
        None,
    )


def test_resolve_multi_service_message_issues_system_level_ticket(monkeypatch):
    """批量发版消息（提到 3 个服务）→ 系统级票据，不再钉住最长命中的那个服务。"""
    resolved = _resolve_multi(monkeypatch)
    assert resolved["outcome"] == "RESOLVED"
    assert resolved["system_name"] == "crypto-trader"
    assert resolved["service_name"] is None
    assert resolved["matched_by"] == "system_keyword_multi_service"
    assert resolved["matched_services"] == ["strategy", "system", "transaction"]


def test_resolve_multi_service_next_step_guides_batch_plan(monkeypatch):
    """next_step 要点明：不要传 service_name，按服务各建一个 step。"""
    resolved = _resolve_multi(monkeypatch)
    next_step = resolved["next_step"]
    for name in ("system", "transaction", "strategy"):
        assert name in next_step
    assert "不要" in next_step and "step" in next_step


def test_resolve_single_service_message_still_binds_service(monkeypatch):
    """只提到一个服务时仍然是服务级票据（不因本次改动放宽）。"""
    resolved = _resolve(monkeypatch)
    assert resolved["service_name"] == "transaction"
    assert resolved["matched_by"] == "system_keyword_service"
    assert resolved["matched_services"] == ["transaction"]


def test_resolve_single_service_without_other_keyword_hits(monkeypatch):
    """消息只含一个服务名（无其它关键词）时同样绑定该服务。"""
    resolved = _resolve(monkeypatch, message_text="transaction")
    assert resolved["service_name"] == "transaction"
    assert resolved["matched_by"] in {"service_name", "system_keyword_service"}


def test_prepare_plan_batch_multi_service_plan_succeeds(monkeypatch):
    """批量计划端到端：系统级票据 + 每个服务一个 step + 不传 service_name。"""
    from app.db.base import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    resolved = _resolve_multi(monkeypatch)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    _seed_test_env(db, resolved["system_name"])
    args = {
        "message_context": dict(resolved["message_context"]),
        "routing_ticket": resolved["ticket"],
        "system_name": resolved["system_name"],
        "environment": "test",
        "steps": [
            {
                "step_key": f"restart-{svc}",
                "action_type": "SERVICE_CONTROL",
                "parameters": {"action": "restart", "targets": ["cc-test2"],
                               "service_name": svc},
            }
            for svc in resolved["matched_services"]
        ],
        "policy": {"continue_on_error": False},
    }
    try:
        prepared = approval_tools.approval_prepare_plan(args=args, ctx=_ctx(), db=db)
    finally:
        db.close()
    assert prepared["plan_id"]
    assert prepared.get("service_name") in (None, "")

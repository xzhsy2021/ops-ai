"""Web（REST）生产发版路径的第 4 层：strict_prod_confirmation。

背景（2026-09-11 生产发版审计）：对话链路的第 4 层落地后，Web/REST 这条同样能推
生产的路径仍是"摆设"——

* `_assert_strict_deploy_confirmation` 接受 `confirm_production: true` 或
  `confirm_text == "CONFIRM"` 两个旁路；
* 前端 `useDeployActions.confirmRiskRelease` 恰恰**自动**发送
  `confirm_production: isProdRelease()`，并且用户不输入时回落到正确短语。

两者叠加 → Web 端生产发布"严格确认"实际零人工输入。本文件锁定修复后的行为：
生产 + 开关开启时，必须逐字给出确认短语 **且** 给出确认从句「我确认生产操作」，
legacy 旁路一律失效；非生产与开关关闭时保持兼容。
"""
import pytest
from fastapi import HTTPException

from app.api.deploy._shared import (
    _assert_strict_deploy_confirmation,
    _build_confirmation,
    _deploy_confirm_text,
    _requires_prod_confirm,
)
from app.db.base import Base, SessionLocal, engine
from app.db.migrations.runner import run_schema_migrations
from app.deploy.schemas import DeployRequest
from app.services.approval_phrase import PROD_CONFIRM_CLAUSE
from app.services.tool_policy import is_prod_environment

SYSTEM = "crypto-trader"
SERVICE = "transaction"
EXPECTED = f"确认发布 {SYSTEM}/{SERVICE} 到 prod"


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _req(environment: str = "prod") -> DeployRequest:
    return DeployRequest(system=SYSTEM, service=SERVICE, environment=environment,
                         servers=["主节点"], reason="单元测试")


def _call(payload, environment="prod", db=None):
    req = _req(environment)
    confirmation = {"required_confirmation": _deploy_confirm_text(req),
                    "confirm_text": _deploy_confirm_text(req)}
    _assert_strict_deploy_confirmation(req, payload, confirmation, db)


# ── 严格模式（生产 + strict_prod_confirmation 开启）──

def test_prod_without_clause_is_rejected(db):
    """只有正确短语、没有从句 → 拒绝，且明确点名从句。"""
    with pytest.raises(HTTPException) as exc:
        _call({"confirm_text": EXPECTED, "reason": "r"}, db=db)
    assert exc.value.status_code == 400
    assert PROD_CONFIRM_CLAUSE in exc.value.detail


def test_prod_legacy_confirm_flag_no_longer_bypasses(db):
    """前端历史行为 confirm_production=true 必须失效（这正是旧的旁路）。"""
    with pytest.raises(HTTPException) as exc:
        _call({"confirm_text": EXPECTED, "confirm_production": True, "reason": "r"}, db=db)
    assert exc.value.status_code == 400
    assert PROD_CONFIRM_CLAUSE in exc.value.detail


def test_prod_literal_confirm_string_no_longer_bypasses(db):
    """confirm_text == "CONFIRM" 也必须失效。"""
    with pytest.raises(HTTPException) as exc:
        _call({"confirm_text": "CONFIRM", "confirm_production": True, "reason": "r"}, db=db)
    assert exc.value.status_code == 400


def test_prod_clause_alone_is_not_enough(db):
    """只给从句、短语不对 → 仍然拒绝（短语要逐字）。"""
    with pytest.raises(HTTPException) as exc:
        _call({"confirm_text": "确认发布 别的/服务 到 prod",
               "prod_confirm_text": PROD_CONFIRM_CLAUSE, "reason": "r"}, db=db)
    assert exc.value.status_code == 400
    assert "逐字" in exc.value.detail


def test_prod_phrase_with_separate_clause_passes(db):
    _call({"confirm_text": EXPECTED, "prod_confirm_text": PROD_CONFIRM_CLAUSE,
           "reason": "r"}, db=db)


def test_prod_phrase_with_inline_clause_passes(db):
    """整行照抄（短语 + 从句）也要能用——与对话链路一致。"""
    _call({"confirm_text": f"{EXPECTED} {PROD_CONFIRM_CLAUSE}", "reason": "r"}, db=db)


def test_prod_still_requires_reason(db):
    """从句齐全但缺 reason → 仍然拒绝（生产必须留变更原因）。"""
    with pytest.raises(HTTPException) as exc:
        _call({"confirm_text": EXPECTED, "prod_confirm_text": PROD_CONFIRM_CLAUSE}, db=db)
    assert exc.value.status_code == 400
    assert "reason" in exc.value.detail


# ── 非生产 / 开关关闭：保持兼容 ──

def test_test_env_unaffected(db):
    _call({"confirm_text": "随便什么"}, environment="test", db=db)


def test_legacy_bypass_kept_when_setting_off(db, monkeypatch):
    """开关关闭时保留 legacy 兼容（不破坏既有客户端）。"""
    import app.services.tool_policy as tp

    monkeypatch.setattr(tp, "strict_prod_confirmation_required", lambda *_a, **_k: False)
    _call({"confirm_text": EXPECTED, "confirm_production": True, "reason": "r"}, db=db)
    _call({"confirm_text": "CONFIRM", "reason": "r"}, db=db)


# ── 回执要给前端足够信息 ──

def test_confirmation_exposes_prod_confirm_fields(db):
    """前端据 requires_prod_confirm 切换成"必须逐字输入"，并渲染从句输入框。"""
    resp = _build_confirmation(_req("prod"), db, {"username": "tester"})
    assert resp["requires_prod_confirm"] is True
    assert resp["prod_confirm_text"] == PROD_CONFIRM_CLAUSE


def test_confirmation_hides_prod_confirm_for_test_env(db):
    resp = _build_confirmation(_req("test"), db, {"username": "tester"})
    assert resp["requires_prod_confirm"] is False
    assert resp["prod_confirm_text"] == ""


def test_requires_prod_confirm_helpers():
    assert _requires_prod_confirm(None, "prod") in (True, False)  # 无 db 时不抛
    assert _requires_prod_confirm(None, "test") is False
    assert is_prod_environment("prod") is True
    assert is_prod_environment("test") is False

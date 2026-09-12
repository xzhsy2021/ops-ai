"""MCP 工具层 dry_run（预览）契约回归（2026-09-12 复盘第 4 轮）。

缺陷：``ops.upload_package`` 只在 ``local_path`` 分支检查 dry_run
--------------------------------------------------------------------------------
``app/services/tool_adapters/file_tools.py::upload_package`` 的结构是：

    if args.get("content_base64"):
        meta = save_package_base64(...)     # ← 直接落盘 + 写元数据，从不看 dry_run
        return meta
    local_path = args.get("local_path") or ""
    if local_path:
        if args.get("dry_run"):             # ← dry_run 只在这个分支生效
            return inspect_package_file(...)
        ...save_package_fileobj(...)

后果：调用方显式传 ``dry_run=true`` + ``content_base64`` 时，包会被**真实暂存**进
文件中心（文件 + DeployPackage 元数据 + 保留策略记账），与"只检查不落盘"的语义相反。
且风险策略用 ``_is_dry_run(args)`` 判断"非破坏性"（跳过确认、置 can_auto_execute），
所以这类调用会被策略与日志当成预览，实际却产生了写入。

本文件锁定：dry_run 必须对两种入参形态都成立（不落盘、不改元数据），
同时 dry_run=false 的正常上传不能被修复破坏。
"""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def pkg_env(monkeypatch, tmp_path):
    """隔离的 uploads 目录 + 临时数据库（含保留策略/包元数据表）。"""
    from app.db.base import Base

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    engine = create_engine(f"sqlite:///{tmp_path / 'pkg.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield SimpleNamespace(upload_dir=upload_dir, db=session)
    finally:
        session.close()
        engine.dispose()


def _ctx():
    return SimpleNamespace(username="tester", token_owner="", allow_write=True, is_admin=True)


def _package_rows(db) -> int:
    from app.db.models import DeployPackage

    return db.query(DeployPackage).count()


def test_upload_package_dry_run_with_base64_writes_nothing(pkg_env):
    """核心回归：dry_run=true + content_base64 不得落盘、不得写元数据。"""
    from app.services.tool_adapters import file_tools

    content = b"PK\x03\x04 pretend zip payload"
    before_rows = _package_rows(pkg_env.db)

    result = file_tools.upload_package(
        {
            "filename": "dryrun_probe.zip",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "dry_run": True,
            "system": "demo",
            "service": "api",
        },
        _ctx(),
        pkg_env.db,
    )

    assert result.get("dry_run") is True, "dry_run 响应必须显式标注"
    assert list(pkg_env.upload_dir.iterdir()) == [], "dry_run 不得在文件中心落盘"
    assert _package_rows(pkg_env.db) == before_rows, "dry_run 不得写入包元数据"
    # 仍应给出可用的预检信息（体积/摘要/策略判定）
    assert result.get("size") == len(content)
    assert result.get("package_name") == "dryrun_probe.zip"
    assert "sha256" in result


def test_upload_package_dry_run_with_base64_reports_policy_blockers(pkg_env):
    """dry_run 仍要能给出预检结论（例如非白名单后缀 → blockers，而不是抛异常）。"""
    from app.services.tool_adapters import file_tools

    result = file_tools.upload_package(
        {
            "filename": "notes.txt",
            "content_base64": base64.b64encode(b"not a package").decode("ascii"),
            "dry_run": True,
        },
        _ctx(),
        pkg_env.db,
    )

    assert result.get("ok") is False
    assert any("extension" in item.lower() for item in result.get("blockers", []))
    assert list(pkg_env.upload_dir.iterdir()) == []


def test_upload_package_without_dry_run_still_stages_base64(pkg_env):
    """正向对照：dry_run=false 时正常上传（修复不得把真实上传一起挡掉）。"""
    from app.services.tool_adapters import file_tools

    content = b"PK\x03\x04 real zip payload"
    result = file_tools.upload_package(
        {
            "filename": "real_probe.zip",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "system": "demo",
            "service": "api",
        },
        _ctx(),
        pkg_env.db,
    )

    staged = pkg_env.upload_dir / "real_probe.zip"
    assert staged.is_file(), "非 dry_run 上传应当落盘"
    assert staged.read_bytes() == content
    assert _package_rows(pkg_env.db) == 1
    assert result.get("dry_run") is not True


def test_upload_package_dry_run_with_local_path_writes_nothing(pkg_env):
    """既有正确行为（local_path 分支）回归锁定：dry_run 只检查不落盘。"""
    from app.services.tool_adapters import file_tools

    source = pkg_env.upload_dir / "source.zip"
    source.write_bytes(b"PK\x03\x04 local zip payload")
    before_rows = _package_rows(pkg_env.db)

    result = file_tools.upload_package(
        {"local_path": str(source), "filename": "source.zip", "dry_run": True},
        _ctx(),
        pkg_env.db,
    )

    assert result.get("ok") is True
    assert result.get("dry_run") is True and result.get("staged") is False
    assert result.get("size") == source.stat().st_size
    assert [p.name for p in pkg_env.upload_dir.iterdir()] == ["source.zip"]
    assert _package_rows(pkg_env.db) == before_rows


def test_upload_package_dry_run_invalid_base64_is_400(pkg_env):
    """错误契约：非法 base64 在 dry_run 下也要报 400（不能变成 500）。"""
    from app.services.tool_adapters import file_tools

    with pytest.raises(HTTPException) as excinfo:
        file_tools.upload_package(
            {"filename": "broken.zip", "content_base64": "!!!not-base64!!!", "dry_run": True},
            _ctx(),
            pkg_env.db,
        )

    assert excinfo.value.status_code == 400
    assert list(pkg_env.upload_dir.iterdir()) == []


def test_prepare_release_dry_run_with_base64_returns_real_preflight(pkg_env):
    """兄弟缺陷：prepare_release dry_run + content_base64 必须给出真实预检，
    而不是一份空白的"dry-run 成功"；同时不得落盘、不得建计划。"""
    from app.db.models import ToolPlan
    from app.services.tool_adapters import deploy_tools

    content = b"PK\x03\x04 release payload"
    result = deploy_tools.prepare_release_from_local_package(
        {
            "system": "demo",
            "service": "api",
            "environment": "dev",
            "filename": "release_probe.zip",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "dry_run": True,
        },
        _ctx(),
        pkg_env.db,
    )

    assert result.get("dry_run") is True
    inspection = result.get("local_inspection") or {}
    assert inspection.get("dry_run") is True, "必须附带真实预检结果"
    assert inspection.get("size") == len(content)
    assert inspection.get("package_name") == "release_probe.zip"
    assert list(pkg_env.upload_dir.iterdir()) == [], "dry_run 不得落盘"
    assert pkg_env.db.query(ToolPlan).count() == 0, "dry_run 不得创建发布计划"


# ---------------------------------------------------------------------------
# 闸门不变量：未声明 dry_run 的写工具，不能被"多传一个 dry_run 参数"跳过人工确认
# ---------------------------------------------------------------------------

def _write_tool(*, declares_dry_run: bool, requires_confirmation: bool = True):
    from app.services.tool_registry import ToolDefinition

    properties = {"deploy_id": {"type": "string"}}
    if declares_dry_run:
        properties["dry_run"] = {"type": "boolean"}
    return ToolDefinition(
        name="ops.probe_write",
        description="probe",
        handler=lambda args, ctx, db: {},
        scopes=["ops:write"],
        risk="high",
        category="deploy_execute",
        write=True,
        requires_confirmation=requires_confirmation,
        input_schema={"type": "object", "properties": properties, "additionalProperties": False},
    )


def test_undeclared_dry_run_does_not_skip_confirmation():
    """核心不变量：dry_run 只对声明了该参数的工具生效（否则是通用跳确认开关）。"""
    from app.services.risk_policy import evaluate_risk_policy

    tool = _write_tool(declares_dry_run=False)
    decision = evaluate_risk_policy(tool, {"deploy_id": "d1", "dry_run": True}, settings={"require_confirmation": True})

    assert decision.confirmation_required is True, "未声明 dry_run 的工具不得因此免确认"
    assert decision.can_auto_execute is False
    assert decision.confirm_text_matched is False


def test_undeclared_dry_run_still_raises_428():
    """执行闸门：这类调用必须 428 要求确认短语，而不是悄悄放行。"""
    from app.services.risk_policy import enforce_risk_policy

    tool = _write_tool(declares_dry_run=False)
    with pytest.raises(HTTPException) as excinfo:
        enforce_risk_policy(tool, {"deploy_id": "d1", "dry_run": True}, settings={"require_confirmation": True})

    assert excinfo.value.status_code == 428
    assert excinfo.value.detail["code"] == "CONFIRMATION_REQUIRED"


def test_declared_dry_run_still_skips_confirmation():
    """正向对照：确实实现了预览语义的工具（声明 dry_run）仍可免确认。"""
    from app.services.risk_policy import evaluate_risk_policy

    tool = _write_tool(declares_dry_run=True)
    decision = evaluate_risk_policy(tool, {"deploy_id": "d1", "dry_run": True}, settings={"require_confirmation": True})

    assert decision.confirmation_required is False
    assert decision.can_auto_execute is True


def test_real_registry_write_tools_only_allow_declared_dry_run():
    """注册表级回归：只有声明 dry_run 的工具才接受 dry_run 语义。"""
    from app.services.risk_policy import _is_dry_run
    from app.services.tool_registry import ensure_builtin_registered, registry

    ensure_builtin_registered()
    with_dry = []
    for tool in registry._tools.values():
        if not getattr(tool, "write", False):
            continue
        declares = "dry_run" in ((getattr(tool, "input_schema", None) or {}).get("properties") or {})
        assert _is_dry_run(tool, {"dry_run": True}) is declares, tool.name
        if declares:
            with_dry.append(tool.name)
    assert sorted(with_dry) == [
        "ops.prepare_release_from_local_package",
        "ops.upload_package",
    ]

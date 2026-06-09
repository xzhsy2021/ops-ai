from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'inspection_profiles.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _inventory(monkeypatch, rows):
    def _list_servers(self):
        return rows

    monkeypatch.setattr("app.domain.inventory.services.InventoryReadService.list_servers", _list_servers)


def test_default_profiles_preview_crypto_test_targets_and_confirmation(monkeypatch, tmp_path):
    from app.services.inspection_profiles import get_profile, list_profiles, preview_profile

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    _inventory(
        monkeypatch,
        [
            {"id": "uuid-a", "name": "43.106.4.251-量化测试 -3", "host": "43.106.4.251", "group": "crypto", "status": "online"},
            {"id": "uuid-b", "name": "203.0.113.20-量化测试 - 2", "host": "203.0.113.20", "group": "crypto", "status": "online"},
            {"id": "uuid-c", "name": "crypto-prod-1", "host": "10.0.0.10", "group": "crypto", "status": "online"},
            {"id": "uuid-d", "name": "disabled-test", "host": "10.0.0.11", "group": "crypto", "status": "disabled"},
        ],
    )

    try:
        listed = list_profiles(db)
        ids = {item["id"] for item in listed["items"]}
        assert {"daily-lite", "weekly-security", "monthly-full", "crypto-test-daily"} <= ids

        profile = get_profile(db, "crypto-test-daily")
        assert profile["target"]["groups"] == ["crypto"]
        assert "测试" in profile["target"]["include_keywords"]

        preview = preview_profile(db, "crypto-test-daily")
        assert preview["eligible_count"] == 2
        assert [item["asset_id"] for item in preview["eligible"]] == ["uuid-a", "uuid-b"]
        assert preview["filtered_count"] == 1
        assert preview["skipped_count"] == 1
        assert preview["confirmation"]["confirm_text"].startswith("RUN crypto-test-daily 2 ")
        assert preview["confirmation"]["legacy_confirm_text"].startswith("RUN INSPECTION crypto-test-daily 2 ")
        assert preview["confirmation"]["accepted_confirm_texts"][0] == preview["confirmation"]["confirm_text"]
        assert preview["confirmation"]["fingerprint"]
        assert "请完整复制确认短语" in preview["confirmation"]["description"]
        assert "目标服务器、巡检项和执行参数" in preview["confirmation"]["description"]
        assert "重新预览" in preview["confirmation"]["description"]
        assert "DISK" in preview["categories"]
    finally:
        db.close()
        engine.dispose()


def test_confirmation_phrase_changes_when_target_set_changes(monkeypatch, tmp_path):
    from app.services.inspection_profiles import preview_profile

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        _inventory(monkeypatch, [{"id": "uuid-a", "name": "test-a", "host": "1.1.1.1", "group": "crypto", "status": "online"}])
        first = preview_profile(db, "crypto-test-daily")["confirmation"]["confirm_text"]

        _inventory(
            monkeypatch,
            [
                {"id": "uuid-a", "name": "test-a", "host": "1.1.1.1", "group": "crypto", "status": "online"},
                {"id": "uuid-b", "name": "test-b", "host": "1.1.1.2", "group": "crypto", "status": "online"},
            ],
        )
        second = preview_profile(db, "crypto-test-daily")["confirmation"]["confirm_text"]

        assert first != second
        assert first.startswith("RUN crypto-test-daily 1 ")
        assert second.startswith("RUN crypto-test-daily 2 ")
    finally:
        db.close()
        engine.dispose()


def test_run_profile_requires_current_confirmation_and_generates_combined_report(monkeypatch, tmp_path):
    from app.services.inspection_profiles import preview_profile, run_profile

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    _inventory(
        monkeypatch,
        [
            {"id": "uuid-a", "name": "test-a", "host": "1.1.1.1", "group": "crypto", "status": "online"},
            {"id": "uuid-b", "name": "test-b", "host": "1.1.1.2", "group": "crypto", "status": "online"},
        ],
    )

    captured = {}

    def _fake_batch(db_arg, **kwargs):
        captured["batch"] = kwargs
        return {
            "summary": "done",
            "eligible": 2,
            "success": 2,
            "failed": 0,
            "runs": [{"id": "run-a", "status": "COMPLETED"}, {"id": "run-b", "status": "COMPLETED"}],
            "results": [],
            "errors": [],
        }

    def _fake_report(db_arg, run_ids, **kwargs):
        captured["report"] = {"run_ids": run_ids, **kwargs}
        return {"report": {"id": "report-a", "title": kwargs.get("title")}}

    try:
        confirm_text = preview_profile(db, "crypto-test-daily")["confirmation"]["confirm_text"]
        with pytest.raises(HTTPException) as excinfo:
            run_profile(db, "crypto-test-daily", confirm_text="RUN crypto-test-daily 2 stale", created_by="tester")
        assert excinfo.value.status_code == 428

        with (
            patch("app.services.inspection_center.run_servers_batch_inspection", _fake_batch),
            patch("app.services.inspection_center.generate_report_for_runs", _fake_report),
        ):
            result = run_profile(db, "crypto-test-daily", confirm_text=confirm_text, created_by="tester")

        assert captured["batch"]["server_ids"] == ["test-a", "test-b"]
        assert captured["batch"]["groups"] == ["crypto"]
        assert captured["batch"]["generate_report"] is False
        assert captured["batch"]["created_by"] == "tester"
        assert captured["report"]["run_ids"] == ["run-a", "run-b"]
        assert result["report"]["id"] == "report-a"
        assert result["profile"]["id"] == "crypto-test-daily"
        assert result["confirmation"]["confirm_text"] == confirm_text

        legacy_confirm_text = preview_profile(db, "crypto-test-daily")["confirmation"]["legacy_confirm_text"]
        with (
            patch("app.services.inspection_center.run_servers_batch_inspection", _fake_batch),
            patch("app.services.inspection_center.generate_report_for_runs", _fake_report),
        ):
            legacy_result = run_profile(db, "crypto-test-daily", confirm_text=legacy_confirm_text, created_by="tester")
        assert legacy_result["run_ids"] == ["run-a", "run-b"]
    finally:
        db.close()
        engine.dispose()


def test_profile_mcp_tools_schema_and_risk_policy_confirmation(tmp_path):
    from app.services.risk_policy import evaluate_risk_policy, expected_confirmation_text
    from app.services.tool_registry import register_builtin_tools, registry
    from app.services.tool_schema import validate_schema

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        for name in {
            "ops.inspection.profile.list",
            "ops.inspection.profile.preview",
            "ops.inspection.profile.run",
            "ops.inspection.profile.retry_issues",
        }:
            assert registry.get(name), name

        run_tool = registry.get("ops.inspection.profile.run")
        args = {"profile_id": "crypto-test-daily", "expected_count": 2, "fingerprint": "abc123", "confirm_text": "RUN crypto-test-daily 2 abc123"}
        validate_schema(args, run_tool.input_schema)
        assert expected_confirmation_text(run_tool, args, db) == "RUN crypto-test-daily 2 abc123"
        legacy_args = {**args, "confirm_text": "RUN INSPECTION crypto-test-daily 2 abc123"}
        assert expected_confirmation_text(run_tool, legacy_args, db) == "RUN crypto-test-daily 2 abc123"
        decision = evaluate_risk_policy(run_tool, legacy_args, db=db, settings={"require_confirmation": True})
        assert decision.confirm_text_matched is True
        assert decision.expected_confirm_text == "RUN crypto-test-daily 2 abc123"

        retry_tool = registry.get("ops.inspection.profile.retry_issues")
        retry_args = {
            "profile_id": "crypto-test-daily",
            "expected_count": 2,
            "fingerprint": "retry123",
            "confirm_text": "RUN crypto-test-daily 2 retry123",
        }
        validate_schema(retry_args, retry_tool.input_schema)
        assert expected_confirmation_text(retry_tool, retry_args, db) == "RUN crypto-test-daily 2 retry123"
        retry_legacy_args = {**retry_args, "confirm_text": "RUN INSPECTION crypto-test-daily 2 retry123"}
        retry_decision = evaluate_risk_policy(retry_tool, retry_legacy_args, db=db, settings={"require_confirmation": True})
        assert retry_decision.confirm_text_matched is True
        assert retry_decision.expected_confirm_text == "RUN crypto-test-daily 2 retry123"
    finally:
        db.close()
        engine.dispose()


def test_profile_mcp_run_handler_returns_preview_when_missing_confirmation(monkeypatch, tmp_path):
    from app.services.tool_adapters import inspection_tools

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    _inventory(monkeypatch, [{"id": "uuid-a", "name": "test-a", "host": "1.1.1.1", "group": "crypto", "status": "online"}])
    ctx = SimpleNamespace(username="tester", token_owner="")
    try:
        result = inspection_tools.profile_run({"profile_id": "crypto-test-daily"}, ctx, db)
        assert result["status"] == "confirmation_required"
        assert result["confirmation"]["confirm_text"].startswith("RUN crypto-test-daily 1 ")
        assert result["confirmation"]["legacy_confirm_text"].startswith("RUN INSPECTION crypto-test-daily 1 ")
        assert result["next_actions"][0]["tool"] == "ops.inspection.profile.run"
    finally:
        db.close()
        engine.dispose()


def test_retry_open_issues_preview_targets_only_unclosed_issue_servers(monkeypatch, tmp_path):
    from app.db.models import InspectionIssue
    from app.services.inspection_profiles import preview_issue_retry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    _inventory(
        monkeypatch,
        [
            {"id": "srv-a", "name": "test-a", "host": "1.1.1.1", "group": "crypto", "status": "online"},
            {"id": "srv-b", "name": "test-b", "host": "1.1.1.2", "group": "crypto", "status": "online"},
            {"id": "srv-c", "name": "test-c", "host": "1.1.1.3", "group": "crypto", "status": "online"},
            {"id": "srv-d", "name": "prod-d", "host": "1.1.1.4", "group": "crypto", "status": "online"},
            {"id": "srv-e", "name": "test-e", "host": "1.1.1.5", "group": "other", "status": "online"},
        ],
    )
    try:
        db.add_all([
            InspectionIssue(id="issue-a", run_id="run-a", item_result_id="item-a", scope_type="SERVER", server_id="srv-a", title="disk", risk_level="HIGH", status="OPEN"),
            InspectionIssue(id="issue-b", run_id="run-b", item_result_id="item-b", scope_type="SERVER", server_id="srv-b", title="service", risk_level="MEDIUM", status="PROCESSING"),
            InspectionIssue(id="issue-c", run_id="run-c", item_result_id="item-c", scope_type="SERVER", server_id="srv-c", title="fixed", risk_level="HIGH", status="FIXED"),
            InspectionIssue(id="issue-d", run_id="run-d", item_result_id="item-d", scope_type="SERVER", server_id="srv-d", title="prod", risk_level="HIGH", status="OPEN"),
            InspectionIssue(id="issue-e", run_id="run-e", item_result_id="item-e", scope_type="SERVER", server_id="srv-e", title="other group", risk_level="HIGH", status="OPEN"),
            InspectionIssue(id="issue-project", run_id="run-d", item_result_id="item-d", scope_type="PROJECT", project_id="ops", title="project", risk_level="HIGH", status="OPEN"),
        ])
        db.commit()

        preview = preview_issue_retry(db, profile_id="crypto-test-daily")

        assert preview["profile_id"] == "crypto-test-daily"
        assert preview["mode"] == "issue_retry"
        assert preview["eligible_count"] == 2
        assert preview["eligible_ids"] == ["test-a", "test-b"]
        assert preview["issue_summary"]["issue_count"] == 2
        assert preview["issue_summary"]["server_count"] == 2
        assert preview["confirmation"]["confirm_text"].startswith("RUN crypto-test-daily 2 ")
    finally:
        db.close()
        engine.dispose()


def test_retry_open_issues_run_uses_profile_categories_and_generates_report(monkeypatch, tmp_path):
    from app.db.models import InspectionIssue
    from app.services.inspection_profiles import preview_issue_retry, run_issue_retry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    _inventory(monkeypatch, [{"id": "srv-a", "name": "test-a", "host": "1.1.1.1", "group": "crypto", "status": "online"}])
    db.add(InspectionIssue(id="issue-a", run_id="run-a", item_result_id="item-a", scope_type="SERVER", server_id="srv-a", title="disk", risk_level="HIGH", status="OPEN"))
    db.commit()
    captured = {}

    def _fake_batch(db_arg, **kwargs):
        captured["batch"] = kwargs
        return {"summary": "retry done", "runs": [{"id": "retry-run-a", "status": "COMPLETED"}], "results": [], "errors": []}

    def _fake_report(db_arg, run_ids, **kwargs):
        captured["report"] = {"run_ids": run_ids, **kwargs}
        return {"report": {"id": "retry-report-a"}}

    try:
        confirm_text = preview_issue_retry(db, profile_id="crypto-test-daily")["confirmation"]["confirm_text"]
        with (
            patch("app.services.inspection_center.run_servers_batch_inspection", _fake_batch),
            patch("app.services.inspection_center.generate_report_for_runs", _fake_report),
        ):
            result = run_issue_retry(db, profile_id="crypto-test-daily", confirm_text=confirm_text, created_by="tester")

        assert captured["batch"]["server_ids"] == ["test-a"]
        assert captured["batch"]["categories"] == ["DISK", "MEMORY", "SERVICE_STATUS", "BACKUP"]
        assert captured["batch"]["created_by"] == "tester"
        assert captured["report"]["run_ids"] == ["retry-run-a"]
        assert result["mode"] == "issue_retry"
        assert result["report"]["id"] == "retry-report-a"
    finally:
        db.close()
        engine.dispose()


def test_issue_retry_mcp_tool_returns_preview_without_confirmation(monkeypatch, tmp_path):
    from app.db.models import InspectionIssue
    from app.services.tool_adapters import inspection_tools

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    _inventory(monkeypatch, [{"id": "srv-a", "name": "test-a", "host": "1.1.1.1", "group": "crypto", "status": "online"}])
    db.add(InspectionIssue(id="issue-a", run_id="run-a", item_result_id="item-a", scope_type="SERVER", server_id="srv-a", title="disk", risk_level="HIGH", status="OPEN"))
    db.commit()
    ctx = SimpleNamespace(username="tester", token_owner="")
    try:
        result = inspection_tools.profile_retry_issues({"profile_id": "crypto-test-daily"}, ctx, db)

        assert result["status"] == "confirmation_required"
        assert result["mode"] == "issue_retry"
        assert result["next_actions"][0]["tool"] == "ops.inspection.profile.retry_issues"
        assert result["next_actions"][0]["arguments"]["confirm_text"].startswith("RUN crypto-test-daily 1 ")
    finally:
        db.close()
        engine.dispose()


def test_profile_http_routes_list_preview_and_run(monkeypatch, tmp_path):
    from app.api import inspection as inspection_api

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    _inventory(
        monkeypatch,
        [
            {"id": "uuid-a", "name": "test-a", "host": "1.1.1.1", "group": "crypto", "status": "online"},
            {"id": "uuid-b", "name": "test-b", "host": "1.1.1.2", "group": "crypto", "status": "online"},
        ],
    )
    monkeypatch.setattr(inspection_api, "require_auth", lambda request, db_arg: {"username": "alice"})

    def _fake_batch(db_arg, **kwargs):
        return {
            "summary": "done",
            "eligible": 2,
            "success": 2,
            "failed": 0,
            "runs": [{"id": "run-a", "status": "COMPLETED"}, {"id": "run-b", "status": "COMPLETED"}],
            "results": [],
            "errors": [],
        }

    def _fake_report(db_arg, run_ids, **kwargs):
        return {"report": {"id": "report-a", "title": kwargs.get("title")}}

    try:
        listed = inspection_api.profiles(SimpleNamespace(), db=db)
        assert listed["data"]["total"] >= 4

        preview_resp = inspection_api.profile_preview({"profile_id": "crypto-test-daily"}, SimpleNamespace(), db=db)
        preview = preview_resp["data"]
        assert preview["eligible_count"] == 2
        confirm_text = preview["confirmation"]["confirm_text"]

        with (
            patch("app.services.inspection_center.run_servers_batch_inspection", _fake_batch),
            patch("app.services.inspection_center.generate_report_for_runs", _fake_report),
        ):
            run_resp = inspection_api.profile_run({"profile_id": "crypto-test-daily", "confirm_text": confirm_text}, SimpleNamespace(), db=db)

        assert run_resp["data"]["run_ids"] == ["run-a", "run-b"]
        assert run_resp["data"]["report"]["id"] == "report-a"
        assert run_resp["message"]
    finally:
        db.close()
        engine.dispose()


def test_profile_frontend_contract_exposes_preview_confirmation_workflow():
    page = open("frontend/src/pages/InspectionCenterPage.tsx", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    css = open("frontend/src/index.css", encoding="utf-8").read()

    assert "profiles: () => api.get('/inspection/profiles')" in api
    assert "profilePreview: (data: { profile_id: string }) => api.post('/inspection/profiles/preview'" in api
    assert "profileRun: (data: { profile_id: string; confirm_text: string; expected_count?: number; fingerprint?: string }) => api.post('/inspection/profiles/run'" in api
    assert "profileRetryIssues: (data: { profile_id?: string; risk_level?: string; status?: string; confirm_text?: string; expected_count?: number; fingerprint?: string }) => api.post('/inspection/profiles/retry-issues'" in api
    assert "profilePreview" in page
    assert "previewIssueRetry" in page
    assert "runInspectionProfile" in page
    assert "const confirmText = (profileConfirmValue.trim() || confirmation.confirm_text || '').trim()" in page
    assert "profile-confirm-hint" in page
    assert "confirmMode=\"one-click\"" in page
    assert "showConfirmTextInOneClick={false}" in page
    assert "无需手动输入字符串" in page
    assert "RiskConfirmDialog" in page
    assert ".inspection-profile-strip" in css

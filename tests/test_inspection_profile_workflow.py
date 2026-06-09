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
        assert preview["confirmation"]["confirm_text"].startswith("RUN INSPECTION crypto-test-daily 2 ")
        assert preview["confirmation"]["fingerprint"]
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
        assert first.startswith("RUN INSPECTION crypto-test-daily 1 ")
        assert second.startswith("RUN INSPECTION crypto-test-daily 2 ")
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
            run_profile(db, "crypto-test-daily", confirm_text="RUN INSPECTION crypto-test-daily 2 stale", created_by="tester")
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
    finally:
        db.close()
        engine.dispose()


def test_profile_mcp_tools_schema_and_risk_policy_confirmation(tmp_path):
    from app.services.risk_policy import expected_confirmation_text
    from app.services.tool_registry import register_builtin_tools, registry
    from app.services.tool_schema import validate_schema

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        for name in {"ops.inspection.profile.list", "ops.inspection.profile.preview", "ops.inspection.profile.run"}:
            assert registry.get(name), name

        run_tool = registry.get("ops.inspection.profile.run")
        args = {"profile_id": "crypto-test-daily", "expected_count": 2, "fingerprint": "abc123", "confirm_text": "RUN INSPECTION crypto-test-daily 2 abc123"}
        validate_schema(args, run_tool.input_schema)
        assert expected_confirmation_text(run_tool, args, db) == "RUN INSPECTION crypto-test-daily 2 abc123"
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
        assert result["confirmation"]["confirm_text"].startswith("RUN INSPECTION crypto-test-daily 1 ")
        assert result["next_actions"][0]["tool"] == "ops.inspection.profile.run"
    finally:
        db.close()
        engine.dispose()

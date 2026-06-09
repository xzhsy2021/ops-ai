from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp_inspection_workflow.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def _inventory(monkeypatch):
    rows = [
        {"id": "srv-a", "name": "crypto-test-a", "host": "10.0.0.1", "group": "crypto", "status": "online"},
        {"id": "srv-b", "name": "crypto-test-b", "host": "10.0.0.2", "group": "crypto", "status": "online"},
        {"id": "srv-c", "name": "crypto-prod-c", "host": "10.0.0.3", "group": "crypto", "status": "disabled"},
        {"id": "srv-d", "name": "ops-test-d", "host": "10.0.0.4", "group": "ops", "status": "online"},
    ]

    def _list_servers(self):
        return rows

    monkeypatch.setattr("app.domain.inventory.services.InventoryReadService.list_servers", _list_servers)


def test_batch_preview_returns_short_chinese_confirmation_and_next_action(monkeypatch, tmp_path):
    from app.services.tool_adapters import inspection_tools
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    _inventory(monkeypatch)
    ctx = SimpleNamespace(username="ai-agent", token_owner="")
    try:
        register_builtin_tools()
        assert registry.get("ops.inspection.preview_servers_batch")

        preview = inspection_tools.preview_servers_batch({
            "groups": ["crypto"],
            "categories": ["DISK", "MEMORY"],
            "concurrency": 3,
            "batch_size": 8,
        }, ctx, db)

        assert preview["status"] == "confirmation_required"
        assert preview["eligible_count"] == 2
        assert preview["skipped_count"] == 1
        assert preview["confirmation"]["confirm_text"].startswith("确认巡检 ")
        assert any("ops.inspection.run_servers_batch" in item for item in preview["confirmation"]["accepted_confirm_texts"])
        assert preview["confirmation"]["mode"] == "one-click-friendly"
        assert preview["execution_plan"]["batch_count"] == 1
        assert preview["next_actions"][0]["tool"] == "ops.inspection.run_servers_batch"
        assert preview["next_actions"][0]["arguments"]["confirm_text"] == preview["confirmation"]["confirm_text"]
    finally:
        db.close()
        engine.dispose()


def test_batch_run_requires_preview_confirmation_not_generic_phrase(monkeypatch, tmp_path):
    from app.services.tool_adapters import inspection_tools

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    _inventory(monkeypatch)
    ctx = SimpleNamespace(username="ai-agent", token_owner="")
    captured = {}

    def _fake_batch(db_arg, **kwargs):
        captured["batch"] = kwargs
        return {
            "summary": "batch done",
            "eligible": 2,
            "success": 2,
            "failed": 0,
            "runs": [{"id": "run-a", "status": "COMPLETED"}, {"id": "run-b", "status": "COMPLETED"}],
        }

    try:
        preview = inspection_tools.preview_servers_batch({
            "groups": ["crypto"],
            "categories": ["DISK", "MEMORY"],
            "concurrency": 3,
            "batch_size": 8,
        }, ctx, db)

        with pytest.raises(HTTPException) as excinfo:
            inspection_tools.run_servers_batch({
                "groups": ["crypto"],
                "categories": ["DISK", "MEMORY"],
                "concurrency": 3,
                "batch_size": 8,
                "confirm_text": "CONFIRM ops.inspection.run_servers_batch",
            }, ctx, db)
        assert excinfo.value.status_code == 428
        assert excinfo.value.detail["expected_confirm_text"] == preview["confirmation"]["confirm_text"]

        with patch("app.services.inspection_center.run_servers_batch_inspection", _fake_batch):
            result = inspection_tools.run_servers_batch({
                "groups": ["crypto"],
                "categories": ["DISK", "MEMORY"],
                "concurrency": 3,
                "batch_size": 8,
                "confirm_text": preview["confirmation"]["confirm_text"],
            }, ctx, db)

        assert captured["batch"]["groups"] == ["crypto"]
        assert captured["batch"]["server_ids"] == ["crypto-test-a", "crypto-test-b"]
        assert captured["batch"]["categories"] == ["DISK", "MEMORY"]
        assert result["run_ids"] == ["run-a", "run-b"]
        assert result["confirmation"]["confirm_text"] == preview["confirmation"]["confirm_text"]
    finally:
        db.close()
        engine.dispose()


def test_mcp_docs_prefer_preview_first_batch_inspection_flow():
    examples = open("docs/runbooks/AI_TOOL_MCP_EXAMPLES.md", encoding="utf-8").read()
    matrix = open("docs/runbooks/mcp-capability-matrix.md", encoding="utf-8").read()
    prompt = open("app/agent/prompts/inspection_workflow.md", encoding="utf-8").read()

    assert "ops_inspection_preview_servers_batch" in examples
    assert "确认巡检 <fingerprint>" in examples
    assert "ops.inspection.preview_servers_batch" in matrix
    assert "preview_servers_batch" in prompt

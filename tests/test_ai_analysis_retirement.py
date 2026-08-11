from __future__ import annotations

import importlib.util

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


RETIRED_ANALYSIS_MODULES = {
    "app.api.ai_analysis",
    "app.services.ai_analysis",
    "app.services.ai_diagnostics",
    "app.services.ai_evidence",
    "app.services.ai_workflows",
    "app.services.tool_adapters.ai_tools",
    "app.services.tool_adapters.ai_analysis_tools",
}

RETIRED_ANALYSIS_PROMPTS = {
    "ops_failure_analysis",
    "ops_diagnostic_triage",
    "ops_project_health_brief",
    "ops_risk_triage",
    "ops_monthly_ops_report",
}


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'ai_analysis_retirement.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_ai_analysis_tools_resources_prompts_and_report_types_are_absent(tmp_path):
    from app.services.mcp_capability_service import mcp_prompt_items, mcp_resource_items
    from app.services.report_center import REPORT_TYPES
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(
            username="tester",
            auth_type="session",
            is_admin=True,
            scopes=["*"],
            allow_write=True,
        )
        listed = registry.list_tools(
            db,
            ctx,
            include_disabled=True,
            include_schema=False,
            limit=1000,
        )
        names = {tool["name"] for tool in listed["tools"]}
        assert "ops.analyze_diagnostics" not in names
        assert not any(name.startswith("ops.ai.") for name in names)
        assert {
            "ops.run_diagnostics",
            "ops.list_reports",
            "ops.inspection.list_runs",
            "ops.risk.list",
        } <= names

        resource_uris = {item["uri"] for item in mcp_resource_items()}
        assert "ops://ai-diagnostics" not in resource_uris
        assert "ops://ai-workflows" not in resource_uris
        assert {
            "ops://diagnosis/recent",
            "ops://reports",
            "ops://inspection/recent",
            "ops://risks/open",
        } <= resource_uris

        prompt_names = {item["name"] for item in mcp_prompt_items()}
        assert RETIRED_ANALYSIS_PROMPTS.isdisjoint(prompt_names)
        assert {"ops_release_plan", "ops_operation_replay", "ops_inspection_workflow"} <= prompt_names

        capabilities = registry.describe_capabilities(
            db,
            ctx,
            include_schema=False,
            include_disabled=True,
            profile="ai_full",
        )
        capability_resources = {item["uri"] for item in capabilities["resources"]}
        capability_prompts = {item["name"] for item in capabilities["prompts"]}
        assert {"ops://ai-diagnostics", "ops://ai-workflows"}.isdisjoint(capability_resources)
        assert RETIRED_ANALYSIS_PROMPTS.isdisjoint(capability_prompts)

        assert "ai_analysis" not in REPORT_TYPES
        assert "ai_diagnostics" not in REPORT_TYPES
        assert {"diagnostics", "operation_chain", "deployment", "inspection"} <= set(REPORT_TYPES)
    finally:
        db.close()
        engine.dispose()


def test_ai_analysis_routes_are_not_mounted():
    from main import app

    paths = {route.path for route in app.routes}

    assert "/api/v2/system/ai-diagnostics" not in paths
    assert not any(path == "/api/v2/ai/analysis" or path.startswith("/api/v2/ai/analysis/") for path in paths)
    assert "/api/v2/system/diagnostics" in paths
    assert "/api/v2/reports" in paths


def test_ai_analysis_models_and_modules_are_absent():
    from app.db import models

    assert not hasattr(models, "AiAnalysisRun")
    assert not hasattr(models, "AiAnalysisFinding")
    assert hasattr(models, "AiActionApproval")
    assert "ai_analysis_runs" not in models.Base.metadata.tables
    assert "ai_analysis_findings" not in models.Base.metadata.tables

    for module_name in RETIRED_ANALYSIS_MODULES:
        assert importlib.util.find_spec(module_name) is None, module_name


def test_ai_analysis_tables_are_dropped_by_forward_idempotent_migration(tmp_path):
    from app.db.migrations.runner import run_schema_migrations
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'ai_analysis_migration.db'}",
        connect_args={"check_same_thread": False},
    )
    try:
        Base.metadata.create_all(engine)

        first = run_schema_migrations(engine)
        second = run_schema_migrations(engine)

        names = set(inspect(engine).get_table_names())
        assert "ai_analysis_findings" not in names
        assert "ai_analysis_runs" not in names
        assert "ai_action_approvals" in names
        assert "084_001_drop_ai_analysis_tables" in first
        assert "084_001_drop_ai_analysis_tables" not in second

        with engine.connect() as conn:
            versions = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT version FROM schema_migrations "
                        "WHERE version IN ('059_001_ai_analysis_runs', "
                        "'059_002_ai_analysis_findings', "
                        "'084_001_drop_ai_analysis_tables')"
                    )
                ).fetchall()
            }
        assert versions == {
            "059_001_ai_analysis_runs",
            "059_002_ai_analysis_findings",
            "084_001_drop_ai_analysis_tables",
        }
    finally:
        engine.dispose()


def test_sensitive_masking_remains_available_outside_analysis_subsystem():
    from app.services.sensitive_data import mask_sensitive

    masked = mask_sensitive("password=hunter2 token=abc123 safe=value")

    assert "hunter2" not in masked
    assert "abc123" not in masked
    assert "safe=value" in masked
    assert mask_sensitive("abcdef", limit=3) == "abc"

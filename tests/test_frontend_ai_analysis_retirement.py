from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend" / "src"


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_frontend_has_no_ai_analysis_routes_or_clients():
    app = _read("frontend/src/App.tsx")
    routes = _read("frontend/src/routes.ts")
    api = _read("frontend/src/api.ts")
    diagnostics_api = _read("frontend/src/api/diagnostics.ts")
    combined = "\n".join((app, routes, api, diagnostics_api))

    for marker in (
        "AiAnalysis",
        "AiWorkflows",
        "aiAnalysis",
        "aiDiagnostics",
        "/ai/analysis",
        "/ai/workflows",
        "/system/ai-diagnostics",
    ):
        assert marker not in combined, marker


def test_retired_pages_component_and_diagnostic_types_are_absent():
    retired_files = (
        "pages/AiWorkflowsPage.tsx",
        "pages/AiAnalysisPage.tsx",
        "pages/AiAnalysisDetailPage.tsx",
        "components/AiEvidenceView.tsx",
    )
    for relative_path in retired_files:
        assert not (FRONTEND_ROOT / relative_path).exists(), relative_path

    diagnostic_types = _read("frontend/src/types/diagnostics.ts")
    diagnostics_page = _read("frontend/src/pages/SystemDiagnosticsPage.tsx")
    for marker in (
        "AiDiagnosticFinding",
        "AiDiagnosticToolchainStep",
        "AiDiagnosticsPayload",
        "AiDiagnosticsPanel",
        "analyzeWithAi",
        "safe_mcp_toolchain",
        "AI 诊断助手",
    ):
        assert marker not in diagnostic_types + diagnostics_page, marker

    for raw_diagnostics_marker in (
        "diagnosticsApi.get()",
        "diagnosticsApi.exportUrl()",
        "recent_errors",
        "Recommendations",
    ):
        assert raw_diagnostics_marker in diagnostics_page, raw_diagnostics_marker


def test_console_wording_describes_supported_reports_and_external_agent_audit():
    report_page = _read("frontend/src/pages/ReportCenterPage.tsx")
    audit_page = _read("frontend/src/pages/AuditLogPage.tsx")
    routes = _read("frontend/src/routes.ts")

    assert "ai_diagnostics" not in report_page
    assert "AI 分析" not in report_page
    for marker in ("系统诊断", "发布报告", "巡检报告", "操作链路"):
        assert marker in report_page, marker

    assert "AI 分析了什么" not in audit_page
    assert "外部 Agent" in audit_page
    assert "MCP 调用了什么" in audit_page

    assert "AI 辅助分析" not in routes
    for marker in ("MCP 工具", "审批", "安全策略"):
        assert marker in routes, marker


def test_frontend_release_check_asserts_retired_surfaces_are_absent():
    source = _read("scripts/frontend_route_check.js")

    assert "diagnostics page AI assistant UI missing" not in source
    assert "frontend API aiDiagnostics method missing" not in source
    assert "retiredFrontendMarkers" in source
    assert "frontendDiagnosticsApi" in source
    assert "diagnosticsTypes" in source
    for marker in (
        "/system/ai-diagnostics",
        "/ai/analysis",
        "AiWorkflowsPage",
        "aiDiagnostics",
        "AiDiagnosticsPayload",
    ):
        assert marker in source, marker

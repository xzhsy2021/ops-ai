from __future__ import annotations

from fastapi.testclient import TestClient


def test_frontend_status_endpoint_returns_dist_freshness(monkeypatch):
    from main import app

    monkeypatch.setattr(
        "app.core.security.PUBLIC_PREFIXES",
        (
            "/api/v2/system/login",
            "/api/v2/system/health",
            "/api/v2/system/frontend-status",
            "/login",
            "/api/v2/tools",
            "/api/v2/mcp",
            "/api/v2/capabilities",
        ),
    )
    monkeypatch.setattr(
        "app.api.system.get_frontend_build_check",
        lambda: {
            "status": "warn",
            "message": "dist stale",
            "dist_stale": True,
            "latest_source_file": "frontend/src/App.tsx",
            "latest_source_mtime": "2026-05-28T10:00:00",
            "latest_dist_mtime": "2026-05-28T09:00:00",
        },
    )
    monkeypatch.setattr("app.api.system.require_auth", lambda request, db: {"username": "tester"})

    client = TestClient(app)
    response = client.get("/api/v2/system/frontend-status")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["dist_stale"] is True
    assert body["status"] == "warn"
    assert body["hint"] == "dist stale"


def test_deploy_frontend_treats_partial_failed_as_terminal_status():
    polling = open("frontend/src/pages/deploy/useDeploymentPolling.ts", encoding="utf-8").read()
    stream = open("frontend/src/pages/deploy/useDeploymentStream.ts", encoding="utf-8").read()
    panel = open("frontend/src/pages/deploy/DeploymentRunPanel.tsx", encoding="utf-8").read()
    history = open("frontend/src/pages/deploy/DeploymentHistoryTable.tsx", encoding="utf-8").read()

    assert "partial_failed" in polling
    assert "partial_failed" in stream


def test_deploy_stream_reset_clears_visible_run_state():
    stream = open("frontend/src/pages/deploy/useDeploymentStream.ts", encoding="utf-8").read()

    assert "setTaskId('')" in stream
    assert "setDeploymentId('')" in stream
    assert "deploymentIdRef.current = ''" in stream
    assert "setLogs([])" in stream
    assert "setStatus(nextStatus)" in stream
    assert "setTaskDetails(null)" in stream


def test_deploy_frontend_partial_failed_labels_are_localized():
    panel = open("frontend/src/pages/deploy/DeploymentRunPanel.tsx", encoding="utf-8").read()
    history = open("frontend/src/pages/deploy/DeploymentHistoryTable.tsx", encoding="utf-8").read()

    assert "部分失败" in panel
    assert "部分失败" in history

from __future__ import annotations


def test_release_retention_preview_returns_candidate_counts(monkeypatch):
    from app.services import release_retention

    monkeypatch.setattr(release_retention, "get_retention_policy", lambda db: {"deploy_keep_days": 90})
    monkeypatch.setattr(release_retention, "_deployment_candidates", lambda db, policy: (["d1"], {"count": 1, "total": 3}))
    monkeypatch.setattr(release_retention, "_deployment_record_candidates", lambda db, policy: (["r1"], {"count": 1, "total": 2}))
    monkeypatch.setattr(release_retention, "_audit_record_candidates", lambda db, policy: (["a1"], {"count": 1, "total": 2}))
    monkeypatch.setattr(release_retention, "_audit_log_candidates", lambda policy: (["l1"], {"count": 1, "total": 2}))
    monkeypatch.setattr(release_retention, "_tool_call_candidates", lambda db, policy: (["t1"], {"count": 1, "total": 2}))
    monkeypatch.setattr(release_retention, "_tool_plan_candidates", lambda db, policy: (["p1"], {"count": 1, "total": 2}))

    result = release_retention.preview_release_cleanup(db=None)
    assert result["candidate_counts"]["deployments"] == 1
    assert result["candidate_counts"]["deployment_records"] == 1
    assert result["candidate_counts"]["audit_records"] == 1
    assert result["candidate_counts"]["audit_logs"] == 1
    assert result["candidate_counts"]["tool_call_logs"] == 1
    assert result["candidate_counts"]["tool_plans"] == 1


def test_cleanup_release_history_dry_run_returns_no_deleted(monkeypatch):
    from app.services import release_retention

    monkeypatch.setattr(release_retention, "get_retention_policy", lambda db: {"deploy_keep_days": 90})
    monkeypatch.setattr(release_retention, "_deployment_candidates", lambda db, policy: (["d1", "d2"], {"count": 2, "total": 2}))
    monkeypatch.setattr(release_retention, "_deployment_record_candidates", lambda db, policy: ([], {"count": 0, "total": 0}))
    monkeypatch.setattr(release_retention, "_audit_record_candidates", lambda db, policy: ([], {"count": 0, "total": 0}))
    monkeypatch.setattr(release_retention, "_audit_log_candidates", lambda policy: ([], {"count": 0, "total": 0}))
    monkeypatch.setattr(release_retention, "_tool_call_candidates", lambda db, policy: ([], {"count": 0, "total": 0}))
    monkeypatch.setattr(release_retention, "_tool_plan_candidates", lambda db, policy: ([], {"count": 0, "total": 0}))

    result = release_retention.cleanup_release_history(db=None, dry_run=True)
    assert result["dry_run"] is True
    assert result["deployment_count"] == 2
    assert result["deleted"] == {}
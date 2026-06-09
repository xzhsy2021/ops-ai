from __future__ import annotations


def test_cleanup_stale_artifacts_dry_run_matches_only_known_transient_files(tmp_path):
    from scripts.cleanup_stale_artifacts import collect_stale_artifacts, cleanup_stale_artifacts

    (tmp_path / "inspect_report_abc123.md").write_text("report", encoding="utf-8")
    (tmp_path / "inspect_out.txt").write_text("out", encoding="utf-8")
    (tmp_path / "token_debug.txt").write_text("debug", encoding="utf-8")
    (tmp_path / "README.md").write_text("keep", encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "uvicorn7.log").write_text("local log", encoding="utf-8")

    candidates = collect_stale_artifacts(tmp_path)
    names = {item.path.relative_to(tmp_path).as_posix() for item in candidates}

    assert names == {
        "inspect_report_abc123.md",
        "inspect_out.txt",
        "token_debug.txt",
        "logs/uvicorn7.log",
    }

    result = cleanup_stale_artifacts(tmp_path, apply=False)
    assert result["deleted"] == []
    assert (tmp_path / "inspect_report_abc123.md").exists()
    assert (tmp_path / "README.md").exists()


def test_cleanup_stale_artifacts_apply_deletes_empty_logs_directory(tmp_path):
    from scripts.cleanup_stale_artifacts import cleanup_stale_artifacts

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "uvicorn7.err.log").write_text("local log", encoding="utf-8")

    result = cleanup_stale_artifacts(tmp_path, apply=True)

    assert result["deleted"] == ["logs/uvicorn7.err.log"]
    assert not logs.exists()

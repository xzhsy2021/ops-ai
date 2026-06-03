import tempfile
import types
from pathlib import Path

import pytest


class _DummyTransport:
    def is_active(self):
        return True


class _DummyParamikoClient:
    def get_transport(self):
        return _DummyTransport()


def test_ssh_pool_uses_stable_pool_key(monkeypatch):
    import ssh_client

    def fake_connect(self, *args, **kwargs):
        self._client = _DummyParamikoClient()

    monkeypatch.setattr(ssh_client.SSHClient, "connect", fake_connect)
    pool = ssh_client.SSHConnectionPool(max_idle_time=300)
    cfg = {"host": "127.0.0.1", "port": 2222, "user": "ops", "password": "secret"}

    client = pool.get(cfg)

    assert client is pool.get(cfg)
    assert "ops@127.0.0.1:2222:direct" in pool._pool
    assert "secret" not in pool._pool
    assert None not in pool._pool


def test_notification_settings_default_and_normalization(monkeypatch):
    import app.db
    from app.api import deploy_v2

    class FakeConfigRepository:
        def __init__(self, db):
            pass

        def get(self, key):
            assert key == "notification_settings"
            return {
                "enabled": 1,
                "webhook_urls": " https://a.example/hook ， https://a.example/hook\nhttps://b.example/hook ",
                "events": "deploy.success,deploy.failed",
                "timeout": "999",
            }

    monkeypatch.setattr(app.db, "ConfigRepository", FakeConfigRepository)

    settings = deploy_v2._notification_settings(object())

    assert settings["enabled"] is True
    assert settings["webhook_urls"] == ["https://a.example/hook", "https://b.example/hook"]
    assert settings["events"] == ["deploy.success", "deploy.failed"]
    assert settings["timeout"] == 60


def test_distribution_target_uses_step_specific_defaults():
    from app.api import deploy_v2

    variables = {"service_dir": "/srv/backend", "deploy_path": "/srv/web"}

    assert deploy_v2._distribution_target_for_step(
        {"type": "scripted_service_update", "config": {}},
        "app.tar.gz",
        variables,
    ) == "/srv/backend/app.tar.gz"

    assert deploy_v2._distribution_target_for_step(
        {"type": "web_script_update", "config": {"remote_path": "${deploy_path}/packages/"}},
        "web.zip",
        variables,
    ) == "/srv/web/packages/web.zip"

    assert deploy_v2._distribution_target_for_step(
        {"type": "dovo_bluegreen_update", "config": {}},
        "server",
        variables,
    ) == "/tmp/server"


def test_task_cancel_requested_reads_fresh_state():
    from app.api.deploy import _shared as deploy_shared

    class FakeDB:
        expired = False

        def expire_all(self):
            self.expired = True

    class FakeRepo:
        def __init__(self, db):
            self.db = db

        def get_by_id(self, task_id):
            assert task_id == "task-1"
            return types.SimpleNamespace(cancel_requested=True, status="running")

    db = FakeDB()
    original_repo = deploy_shared.DeployTaskRepository
    try:
        deploy_shared.DeployTaskRepository = FakeRepo
        assert deploy_shared._task_cancel_requested(db, "task-1") is True
        assert db.expired is True
    finally:
        deploy_shared.DeployTaskRepository = original_repo


def test_distribution_noops_for_generic_upload_pipeline():
    from app.api import deploy_v2

    assert deploy_v2._distributable_step([
        {"type": "upload", "config": {}},
        {"type": "deploy", "config": {}},
    ]) is None


def test_runtime_resources_resolve_runtime_dirs_after_env_change(monkeypatch):
    from app.services import runtime_resources

    class _FakeQuery:
        def scalar(self):
            return 0

    class _FakeDB:
        def query(self, *args, **kwargs):
            return _FakeQuery()

    monkeypatch.setattr(runtime_resources, "get_runtime_retention_policy", lambda db: {
        "log_file_keep_days": 30,
        "backup_keep_days": 90,
        "backup_keep_max": 7,
        "runtime_tmp_keep_hours": 24,
        "dry_run": True,
    })
    monkeypatch.setattr(runtime_resources, "preview_release_cleanup", lambda db: {"candidate_counts": {}})
    monkeypatch.setattr(
        runtime_resources,
        "preview_package_cleanup",
        lambda db: {"summary": {"cleanup_size_bytes": 0}},
    )

    with tempfile.TemporaryDirectory(dir="C:\\tmp") as root_dir:
        root = Path(root_dir)
        app_data = root / "app-data"
        uploads = root / "custom-uploads"
        logs = root / "custom-logs"
        backups = root / "custom-backups"
        runtime = root / "custom-runtime"
        keys = root / "custom-keys"
        reports = root / "custom-reports"

        for path in [uploads, logs, backups, runtime, keys, reports]:
            path.mkdir(parents=True)
        (uploads / "package.zip").write_bytes(b"pkg")
        (logs / "old.log").write_text("log", encoding="utf-8")
        (backups / "ops_backup_old.db").write_bytes(b"db")
        (runtime / "temp.bin").write_bytes(b"tmp")

        monkeypatch.setenv("APP_DATA_DIR", str(app_data))
        monkeypatch.setenv("UPLOAD_DIR", str(uploads))
        monkeypatch.setenv("LOG_DIR", str(logs))
        monkeypatch.setenv("BACKUP_DIR", str(backups))
        monkeypatch.setenv("RUNTIME_DIR", str(runtime))
        monkeypatch.setenv("KEYS_DIR", str(keys))
        monkeypatch.setenv("REPORT_DIR", str(reports))
        runtime_resources.clear_runtime_resource_cache()

        storage = runtime_resources._build_storage_usage(_FakeDB())
        preview = runtime_resources.preview_runtime_cleanup(
            _FakeDB(),
            {
                "log_file_keep_days": 0,
                "backup_keep_days": 0,
                "backup_keep_max": 1,
                "runtime_tmp_keep_hours": 0,
                "dry_run": True,
            },
        )

        assert storage["buckets"]["uploads"]["path"] == str(uploads)
        assert storage["buckets"]["logs"]["path"] == str(logs)
        assert storage["buckets"]["backups"]["path"] == str(backups)
        assert storage["buckets"]["runtime"]["path"] == str(runtime)
        assert storage["buckets"]["keys"]["path"] == str(keys)
        assert storage["buckets"]["reports"]["path"] == str(reports)
        assert preview["files"]["logs"]["count"] == 1
        assert preview["files"]["runtime_tmp"]["count"] == 1

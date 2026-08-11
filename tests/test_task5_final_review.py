import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import approvals as approvals_api
from app.config import systems as systems_config
from app.core import auth_v2


ROOT = Path(__file__).resolve().parents[1]


class _EmptyQuery:
    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def limit(self, *args):
        return self

    def all(self):
        return []


class _EndpointDb:
    def query(self, *args):
        return _EmptyQuery()


def _routing_client(monkeypatch, user):
    monkeypatch.setattr(auth_v2, "get_current_user", lambda request, db: user)
    monkeypatch.setattr(approvals_api, "get_all_systems", lambda: {})

    class ApprovalService:
        def __init__(self, db):
            pass

        def get(self, approval_id):
            return None

        def reject(self, **kwargs):
            return None

        def expire_stale(self):
            return 2

    class Executor:
        def __init__(self, db):
            pass

        def execute(self, approval_id):
            return None

    monkeypatch.setattr(approvals_api, "ActionApprovalService", ApprovalService)
    monkeypatch.setattr(approvals_api, "ApprovalExecutor", Executor)
    app = FastAPI()
    app.include_router(approvals_api.router)

    def get_db_override():
        yield _EndpointDb()

    app.dependency_overrides[approvals_api.get_db] = get_db_override
    return TestClient(app, raise_server_exceptions=False)


def test_routing_read_rejects_unauthenticated_user(monkeypatch):
    response = _routing_client(monkeypatch, None).get("/api/v2/approvals/routing/systems")
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["", "/missing", "/routing/systems"])
def test_approval_reads_require_auth_but_allow_non_admin(monkeypatch, path):
    anonymous = _routing_client(monkeypatch, None).get(f"/api/v2/approvals{path}")
    assert anonymous.status_code == 401
    authenticated = _routing_client(
        monkeypatch, {"username": "operator", "is_admin": False}
    ).get(f"/api/v2/approvals{path}")
    assert authenticated.status_code in {200, 404}


@pytest.mark.parametrize("role", [None, "operator", "admin"])
def test_reject_preserves_authenticated_user_semantics(monkeypatch, role):
    user = None if role is None else {"username": role, "is_admin": role == "admin"}
    response = _routing_client(monkeypatch, user).post(
        "/api/v2/approvals/missing/reject"
    )
    assert response.status_code == (401 if role is None else 400)


@pytest.mark.parametrize(
    "path,admin_status",
    [("/expire-stale", 200), ("/missing/execute", 404)],
)
def test_maintenance_mutations_require_admin(monkeypatch, path, admin_status):
    assert _routing_client(monkeypatch, None).post(f"/api/v2/approvals{path}").status_code == 401
    assert _routing_client(
        monkeypatch, {"username": "operator", "is_admin": False}
    ).post(f"/api/v2/approvals{path}").status_code == 403
    assert _routing_client(
        monkeypatch, {"username": "admin", "is_admin": True}
    ).post(f"/api/v2/approvals{path}").status_code == admin_status


@pytest.mark.parametrize(
    "user,expected",
    [(None, 401), ({"username": "operator", "is_admin": False}, 403)],
)
@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/approvals/routing/systems/crypto-trader",
        "/api/v2/approvals/routing/systems/crypto-trader/services/risk",
    ],
)
def test_routing_mutations_require_admin(monkeypatch, user, expected, path):
    response = _routing_client(monkeypatch, user).put(path, json={"enabled": True})
    assert response.status_code == expected


def test_dotenv_loader_parses_without_execution_and_preserves_exported_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "APPROVAL_SIGNING_KEY=generated-value-with=equals\n"
        "QUOTED=\"value with spaces\"\n"
        "SINGLE='literal value'\n"
        "KEEP=from-file\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["KEEP"] = "already-exported"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/load_dotenv.py"), "--format", "json", str(env_file)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    values = json.loads(result.stdout)
    assert values == {
        "APPROVAL_SIGNING_KEY": "generated-value-with=equals",
        "QUOTED": "value with spaces",
        "SINGLE": "literal value",
    }


def test_dotenv_run_passes_root_value_to_child_and_preserves_exported_value(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PREFLIGHT_KEY=from-root\nEXPORTED_KEY=from-root\n", encoding="utf-8")
    env = os.environ.copy()
    env["EXPORTED_KEY"] = "from-process"
    command = [
        sys.executable,
        str(ROOT / "scripts/load_dotenv.py"),
        str(env_file),
        "--run",
        sys.executable,
        "-c",
        "import os; print(os.environ['PREFLIGHT_KEY'] + ':' + os.environ['EXPORTED_KEY'])",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    assert result.stdout.strip() == "from-root:from-process"


def test_launchers_load_root_dotenv_before_consuming_runtime_config():
    expectations = {
        "scripts/start_single_process.ps1": "running startup preflight",
        "scripts/start_single_process.sh": "running startup preflight",
        "scripts/dev.sh": 'DEV_MODE="${DEV_MODE',
    }
    for filename, later_marker in expectations.items():
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert "load_dotenv.py" in source
        assert source.index("load_dotenv.py") < source.index(later_marker)


def test_documented_launch_chains_have_one_dotenv_loading_boundary():
    loaders = (
        "start.bat",
        "start.sh",
        "scripts/start_single_process.ps1",
        "scripts/start_single_process.sh",
        "scripts/preflight_start_check.ps1",
        "scripts/preflight_start_check.sh",
    )
    for filename in loaders:
        assert "load_dotenv.py" in (ROOT / filename).read_text(encoding="utf-8")

    delegates = (
        "start_dev.bat",
        "start_dev.sh",
        "start_diag.bat",
        "start_diag.sh",
        "start_prod.bat",
        "start_prod.sh",
    )
    for filename in delegates:
        assert "load_dotenv.py" not in (ROOT / filename).read_text(encoding="utf-8")


@pytest.mark.parametrize("value", [[], "", 0, False])
def test_falsey_malformed_persisted_routing_is_not_defaulted(monkeypatch, value):
    row = SimpleNamespace(
        name="bad",
        display_name="Bad",
        strategy="DIRECT",
        base_path=None,
        description=None,
        servers=[],
        variables={},
        message_routing=value,
    )
    with pytest.raises(ValueError):
        systems_config._system_row_to_dict(row)

    opened = False

    def session_local():
        nonlocal opened
        opened = True
        raise AssertionError("database must not open for invalid routing")

    monkeypatch.setattr("app.db.base.SessionLocal", session_local)
    assert systems_config.save_system("bad", {"message_routing": value}) is False
    assert opened is False


def test_message_routing_put_model_rejects_none():
    with pytest.raises(ValidationError):
        approvals_api.MessageRoutingConfig.model_validate(None)

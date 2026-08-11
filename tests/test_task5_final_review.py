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


def _routing_client(monkeypatch, user):
    monkeypatch.setattr(auth_v2, "get_current_user", lambda request, db: user)
    app = FastAPI()
    app.include_router(approvals_api.router)

    def get_db_override():
        yield object()

    app.dependency_overrides[approvals_api.get_db] = get_db_override
    app.dependency_overrides[approvals_api.get_current_user] = lambda: user
    return TestClient(app)


def test_routing_read_rejects_unauthenticated_user(monkeypatch):
    response = _routing_client(monkeypatch, None).get("/api/v2/approvals/routing/systems")
    assert response.status_code == 401


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

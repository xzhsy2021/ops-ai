import json
import os
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess
import sys
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import approvals as approvals_api
from app.config import systems as systems_config
from app.core import auth_v2


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_POSIX_LAUNCHERS = (
    "start.sh",
    "start_prod.sh",
    "start_dev.sh",
    "start_diag.sh",
    "scripts/dev.sh",
    "scripts/preflight_start_check.sh",
    "scripts/start_single_process.sh",
)


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


@pytest.mark.parametrize(
    "launcher",
    [
        "start.sh",
        "scripts/dev.sh",
        "scripts/preflight_start_check.sh",
        "scripts/start_single_process.sh",
    ],
)
def test_posix_launchers_reenter_through_bash_with_dotenv_precedence(tmp_path, launcher):
    bash = shutil.which("bash")
    if not bash and os.name == "nt":
        candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(candidate) if candidate.exists() else None
    if not bash:
        pytest.skip("bash is unavailable")

    for relative in (
        "start.sh",
        "scripts/dev.sh",
        "scripts/preflight_start_check.sh",
        "scripts/start_single_process.sh",
        "scripts/load_dotenv.py",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    (tmp_path / "frontend/dist").mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "PREFLIGHT_KEY=from-root\nEXPORTED_KEY=from-root\nDEV_MODE=single\n",
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python_shim = bin_dir / "python3"
    python_shim.write_text(
        "#!/usr/bin/env bash\n"
        "case \"${1:-}\" in\n"
        "  *load_dotenv.py) exec py -3 \"$@\" ;;\n"
        "esac\n"
        "printf 'PRECHECK:%s:%s\\n' \"$PREFLIGHT_KEY\" \"$EXPORTED_KEY\" >&2\n"
        "exit 73\n",
        encoding="utf-8",
    )
    python_shim.chmod(0o755)

    env = os.environ.copy()
    env.pop("OPS_DOTENV_LOADED", None)
    env["EXPORTED_KEY"] = "from-process"
    env["PATH"] = (
        f"{bin_dir}{os.pathsep}{Path(bash).parent}{os.pathsep}{env.get('PATH', '')}"
    )
    result = subprocess.run(
        [bash, str(tmp_path / launcher)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    output = result.stdout + result.stderr
    assert "PRECHECK:from-root:from-process" in output
    assert "PermissionError" not in output


def test_public_posix_launchers_are_executable_and_wrappers_use_bash():
    stage = subprocess.run(
        ["git", "ls-files", "--stage", *PUBLIC_POSIX_LAUNCHERS],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    modes = {line.split(maxsplit=3)[3]: line.split(maxsplit=1)[0] for line in stage.splitlines()}
    assert modes == {path: "100755" for path in PUBLIC_POSIX_LAUNCHERS}

    delegates = {
        "start_prod.sh": 'exec bash "$DIR/scripts/start_single_process.sh" "$@"',
        "start_dev.sh": 'exec bash "$DIR/start.sh" "$@"',
        "start_diag.sh": 'exec bash "$DIR/scripts/preflight_start_check.sh" "$@"',
    }
    for filename, command in delegates.items():
        assert command in (ROOT / filename).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "wrapper,target",
    [
        ("start_prod.sh", "scripts/start_single_process.sh"),
        ("start_dev.sh", "start.sh"),
        ("start_diag.sh", "scripts/preflight_start_check.sh"),
    ],
)
def test_posix_wrappers_delegate_to_non_executable_target_via_bash(tmp_path, wrapper, target):
    bash = shutil.which("bash")
    if not bash and os.name == "nt":
        candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(candidate) if candidate.exists() else None
    if not bash:
        pytest.skip("bash is unavailable")

    wrapper_path = tmp_path / wrapper
    shutil.copyfile(ROOT / wrapper, wrapper_path)
    target_path = tmp_path / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        "#!/usr/bin/env bash\nprintf 'DELEGATED:%s\\n' \"${1:-}\"\n",
        encoding="utf-8",
    )
    target_path.chmod(0o644)

    result = subprocess.run(
        [bash, str(wrapper_path), "probe"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.stdout.strip() == "DELEGATED:probe"


def test_separated_dev_invokes_setup_scripts_through_bash(tmp_path):
    bash = shutil.which("bash")
    if not bash and os.name == "nt":
        candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(candidate) if candidate.exists() else None
    if not bash:
        pytest.skip("bash is unavailable")

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copyfile(ROOT / "scripts/dev.sh", scripts_dir / "dev.sh")
    for name, marker in (("check_env.sh", "CHECK_ENV"), ("init_db.sh", "INIT_DB")):
        hook = scripts_dir / name
        hook.write_text(f"#!/usr/bin/env bash\nprintf '{marker}\\n'\n", encoding="utf-8")
        hook.chmod(0o644)

    (tmp_path / "frontend").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("python3", "npm"):
        shim = bin_dir / command
        shim.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        shim.chmod(0o755)

    env = os.environ.copy()
    env["DEV_MODE"] = "separated"
    env["OPS_DOTENV_LOADED"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{Path(bash).parent}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        [bash, str(scripts_dir / "dev.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "CHECK_ENV" in output
    assert "INIT_DB" in output
    assert "Permission denied" not in output

    source = (ROOT / "scripts/dev.sh").read_text(encoding="utf-8")
    assert 'bash "$ROOT_DIR/scripts/check_env.sh"' in source
    assert 'bash "$ROOT_DIR/scripts/init_db.sh"' in source


def test_package_zip_preserves_posix_launcher_modes(tmp_path):
    package_dir = tmp_path / "ops-package"
    for relative in PUBLIC_POSIX_LAUNCHERS:
        destination = package_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    archive = tmp_path / "ops-package.zip"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/create_package_zip.py"),
            "--root",
            str(ROOT),
            "--package-dir",
            str(package_dir),
            "--output",
            str(archive),
        ],
        check=True,
    )
    with zipfile.ZipFile(archive) as bundle:
        for relative in PUBLIC_POSIX_LAUNCHERS:
            mode = bundle.getinfo(f"{package_dir.name}/{relative}").external_attr >> 16
            assert mode & 0o111

    for package_script in ("scripts/package.sh", "scripts/package.ps1"):
        assert "create_package_zip.py" in (ROOT / package_script).read_text(encoding="utf-8")


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

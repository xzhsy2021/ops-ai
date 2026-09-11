"""EXEC_REMOTE 白名单（allowlist）契约测试。

背景（2026-09-11 用户反馈）：页面上找不到 exec 白名单的配置入口；且按默认模板，
`docker ps` 能过、`docker compose ps` 被拒——用户举例的正是这两类只读命令。

本测试锁住三件事：
1. 默认模板覆盖 docker / docker compose 只读查询（含 `-f <compose 文件>`），
   而有界性/边界不放宽：无 `--tail` 的日志、`-f` 越界路径、up/down/rm -f 仍被拒；
2. 不匹配时的报错列出当前环境可用模板 id（AI 可照着改，不用试错）；
3. `GET /api/v2/tools/exec-policy` 返回**生效**白名单（默认 vs 覆盖来源可辨），
   且仅管理员可读——这是管理页展示白名单的数据来源。
"""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base, get_db
from app.db.migrations.runner import run_schema_migrations
from app.services.exec_command_policy import (
    DEFAULT_EXEC_TEMPLATES,
    policy_from_settings,
    validate_exec_command,
)


def _check(command, *, environment="prod", **settings):
    """用与生产同一条链路校验命令（policy_from_settings → validate_exec_command）。"""
    policy = policy_from_settings(settings)
    return validate_exec_command(
        command,
        mode=policy["mode"],
        templates=policy["templates"],
        deny_patterns=policy["deny_patterns"],
        destructive_patterns=policy["destructive_patterns"],
        environment=environment,
        max_length=policy["max_length"],
        allow_multi_line=policy["allow_multi_line"],
        target_count=1,
        max_targets=policy["max_targets"],
    )


# ── 1. 默认模板：docker / docker compose 只读查询 ──

@pytest.mark.parametrize("command", [
    "docker ps",
    "docker ps -a",
    "docker ps --all",
    "docker images",
    "docker compose ps",
    "docker compose ps -a",
    "docker compose ps --all",
    "docker compose top",
    "docker compose images",
    "docker compose port web",
    "docker compose version",
    "docker compose ls",
    "docker compose -f /data/crypto-trader/docker-compose.yml ps",
    "docker compose logs --tail=50",
    "docker compose logs --tail 50 web",
    "docker logs --tail=100 crypto-trader",
])
def test_default_allowlist_permits_readonly_docker_queries(command):
    verdict = _check(command)
    assert verdict["ok"] is True, f"{command} 应被放行，实际：{verdict['reason']}"
    assert verdict["template_id"] == "docker_readonly"


@pytest.mark.parametrize("command", [
    "docker compose up -d",             # 变更类子命令
    "docker compose down",
    "docker compose restart",
    "docker compose logs",              # 无 --tail：不限制输出量，拒绝
    "docker logs crypto-trader",
    "docker compose logs -f --tail=10",  # 跟随输出（长驻）不允许
    "docker rm -f crypto-trader",
    "docker compose ps --format json",   # 未在白名单内的参数组合
])
def test_default_allowlist_still_rejects_non_readonly(command):
    verdict = _check(command)
    assert verdict["ok"] is False, f"{command} 不应被放行"
    assert "不匹配任何白名单模板" in verdict["reason"]


def test_compose_file_path_must_stay_in_allowed_prefix():
    """`-f` 引入的路径受 params.file.prefix 约束，且不许 .. 穿越。"""
    outside = _check("docker compose -f /tmp/docker-compose.yml ps")
    assert outside["ok"] is False
    assert "路径不在允许范围内" in outside["reason"] or "不匹配" in outside["reason"]

    traversal = _check("docker compose -f /data/../../etc/shadow ps")
    assert traversal["ok"] is False


def test_rejection_reason_lists_available_templates():
    """报错要能让 AI 自查可用模板，而不是反复试错。"""
    verdict = _check("docker compose logs")
    assert verdict["ok"] is False
    for template_id in (
        "apt_update", "apt_install_packages", "docker_readonly",
        "systemctl_service_status", "system_status_probe", "docker_install_official_repo",
    ):
        assert template_id in verdict["reason"], f"报错应列出模板 {template_id}"


def test_env_scoped_templates_are_respected():
    """只放行 test 的模板不得在 prod 命中（沿用既有 env 语义）。"""
    only_test = [{
        "id": "compose_ps_test_only",
        "description": "仅测试环境",
        "pattern": r"docker\s+compose\s+ps",
        "env": ["test"],
    }]
    assert _check("docker compose ps", environment="test", exec_remote_templates=only_test)["ok"] is True
    assert _check("docker compose ps", environment="prod", exec_remote_templates=only_test)["ok"] is False


# ── 2. 管理接口：生效白名单可读、可辨来源 ──

@pytest.fixture(scope="module")
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def tools_api_client(db, monkeypatch):
    from app.api import tools as tools_api

    app = FastAPI()
    app.include_router(tools_api.tools_router)
    app.dependency_overrides[get_db] = lambda: db
    admin = {"id": 1, "username": "admin-user", "role": "admin", "is_admin": True}
    monkeypatch.setattr(tools_api, "require_admin", lambda request, db: admin)
    return TestClient(app)


def test_exec_policy_endpoint_exposes_default_templates(tools_api_client):
    resp = tools_api_client.get("/api/v2/tools/exec-policy")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["templates_source"] == "default"
    assert data["templates_overridden"] is False
    ids = [t["id"] for t in data["templates"]]
    assert ids == [t["id"] for t in DEFAULT_EXEC_TEMPLATES]
    assert "docker_readonly" in ids
    # 管理页要能显示每条模板放行的命令特征与适用环境
    docker = next(t for t in data["templates"] if t["id"] == "docker_readonly")
    assert docker["pattern"] and docker["env"] == ["test", "prod"]
    # 展示用默认值，便于「恢复默认」按钮比对
    assert data["default_templates"] == DEFAULT_EXEC_TEMPLATES


def test_exec_policy_endpoint_marks_overridden_templates(tools_api_client, monkeypatch):
    from app.api import tools as tools_api
    from app.services.tool_policy import DEFAULT_CAPABILITY_SETTINGS

    override = [{
        "id": "compose_ps_only",
        "description": "只放行 compose ps",
        "pattern": r"docker\s+compose\s+ps",
        "env": ["test", "prod"],
    }]
    settings = dict(DEFAULT_CAPABILITY_SETTINGS)
    settings["exec_remote_templates"] = override
    monkeypatch.setattr(tools_api, "get_capability_settings", lambda db: settings)

    data = tools_api_client.get("/api/v2/tools/exec-policy").json()["data"]
    assert data["templates_source"] == "override"
    assert data["templates_overridden"] is True
    assert [t["id"] for t in data["templates"]] == ["compose_ps_only"]
    # 覆盖时仍带默认模板，便于一键恢复
    assert [t["id"] for t in data["default_templates"]] == [t["id"] for t in DEFAULT_EXEC_TEMPLATES]


def test_exec_policy_endpoint_requires_admin(db, monkeypatch):
    from app.api import tools as tools_api

    app = FastAPI()
    app.include_router(tools_api.tools_router)
    app.dependency_overrides[get_db] = lambda: db

    def _forbid(request, db):
        raise HTTPException(status_code=403, detail="Admin required")

    monkeypatch.setattr(tools_api, "require_admin", _forbid)
    resp = TestClient(app).get("/api/v2/tools/exec-policy")
    assert resp.status_code == 403

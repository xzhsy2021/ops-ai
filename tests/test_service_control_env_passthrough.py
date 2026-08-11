"""Tests for env prefix and compose_args passthrough in service-control command building.

Covers the trace-mode launch requirement for message-level plans:
- `_resolve_service_control_command` renders `env` as an env-var prefix and appends
  `compose_args` to docker compose up/restart/start subcommands.
- Empty env / compose_args keeps the original command (regression).
- `_service_control_handler` forwards env/compose_args from step.parameters to
  `ApprovalExecutor._control_single_server`.
"""
import uuid

import pytest

from app.services.tool_adapters.server_tools import _resolve_service_control_command

_RUN_ID = uuid.uuid4().hex[:8]


def _compose_cfg(compose_file: str = "docker-compose.yml", **tv_overrides):
    tv = {"compose_dir": "/srv/app", "compose_file": compose_file}
    tv.update(tv_overrides)
    return {"template": "docker_compose", "template_variables": tv}


# ── _resolve_service_control_command ──

def test_update_command_default_unchanged():
    """无 env/compose_args 时，update 命令与既有约定一致（回归）。"""
    cmd = _resolve_service_control_command(_compose_cfg(), "update", compose_service="system")
    assert cmd.startswith("docker compose -f docker-compose.yml pull system")
    assert "up -d --no-deps system" in cmd
    assert "SYSTEM_TRACE" not in cmd


def test_update_command_with_env_prefix():
    """env 渲染为环境变量前缀，作用于 pull 与 up 两段。"""
    cmd = _resolve_service_control_command(
        _compose_cfg(), "update", compose_service="system", env={"SYSTEM_TRACE": "true"}
    )
    assert cmd.startswith("SYSTEM_TRACE=true docker compose -f docker-compose.yml pull system")
    pull, _, up = cmd.partition(" && ")
    assert pull.startswith("SYSTEM_TRACE=true docker compose")
    assert up.startswith("SYSTEM_TRACE=true docker compose")
    assert "up -d --no-deps system" in up


def test_update_command_with_compose_args():
    """compose_args 追加到 up 子命令（--force-recreate）。"""
    cmd = _resolve_service_control_command(
        _compose_cfg(), "update", compose_service="system", compose_args=["--force-recreate"]
    )
    assert "--force-recreate" in cmd
    pull, _, up = cmd.partition(" && ")
    assert "--force-recreate" not in pull
    assert "up -d --no-deps system --force-recreate" in up


def test_update_command_with_env_and_compose_args():
    """env 与 compose_args 同时生效，trace 启动完整形态。"""
    cmd = _resolve_service_control_command(
        _compose_cfg(),
        "update",
        compose_service="system",
        env={"SYSTEM_TRACE": "true"},
        compose_args=["--force-recreate"],
    )
    assert cmd.startswith("SYSTEM_TRACE=true docker compose")
    assert "up -d --no-deps system --force-recreate" in cmd


def test_start_command_env_prefix_and_args():
    """start（up -d）也支持 env 与 compose_args。"""
    cmd = _resolve_service_control_command(
        _compose_cfg(), "start", compose_service="system",
        env={"SYSTEM_TRACE": "true"}, compose_args=["--force-recreate"],
    )
    assert cmd.startswith("SYSTEM_TRACE=true docker compose -f docker-compose.yml up -d system --force-recreate")


def test_env_prefix_quotes_value():
    """env 值经 shlex 引号包裹，防御值内空格。"""
    cmd = _resolve_service_control_command(
        _compose_cfg(), "update", compose_service="system", env={"MODE": "a b"}
    )
    assert "MODE=a b" in cmd or "MODE='a b'" in cmd
    assert cmd.startswith("MODE=")


def test_explicit_command_gets_env_prefix():
    """显式配置的 update_command 也支持 env 前缀。"""
    cfg = _compose_cfg(update_command="docker compose up -d --force-recreate system")
    cmd = _resolve_service_control_command(
        {"template": "custom", "template_variables": cfg["template_variables"]},
        "update",
        env={"SYSTEM_TRACE": "true"},
    )
    assert cmd.startswith("SYSTEM_TRACE=true docker compose up -d --force-recreate system")


def test_generic_frontend_update_runs_configured_script_from_deploy_path():
    cfg = {
        "template": "generic_frontend",
        "template_variables": {
            "deploy_path": "/data/www",
            "update_script": "./www.sh",
        },
    }
    cmd = _resolve_service_control_command(
        cfg,
        "update",
        env={"DEPLOY_MODE": "test"},
    )
    assert cmd == "cd /data/www && DEPLOY_MODE=test ./www.sh"


# ── _service_control_handler passthrough ──

def test_service_control_handler_forwards_env_and_compose_args():
    """SERVICE_CONTROL 步骤把 env/compose_args 透传给 _control_single_server。"""
    from unittest.mock import patch

    from app.db.base import SessionLocal, Base, engine
    from app.db.migrations.runner import run_schema_migrations
    from app.services.execution_plan import ExecutionPlanService
    from app.services.plan_executor import _service_control_handler

    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    with SessionLocal() as session:
        room = f"!room-{_RUN_ID}-sc:matrix.org"
        event = f"$evt-{_RUN_ID}-sc:matrix.org"
        service = ExecutionPlanService(session)
        steps = [{
            "step_key": "deploy",
            "action_type": "SERVICE_CONTROL",
            "parameters": {
                "control_action": "update",
                "service_name": "system",
                "compose_service": "system",
                "env": {"SYSTEM_TRACE": "true"},
                "compose_args": ["--force-recreate"],
            },
            "dependencies": [],
        }]
        plan, short = service.prepare(
            room_id=room, request_event_id=event, content_sha256="a" * 64,
            system_name="quant", service_name="system", environment="test",
            targets=["q1"], steps=steps,
            policy={"continue_on_error": False},
            routing_config_revision="rev-x", routing_ticket_digest="ticket-x",
            risk_level="high", authorized_matrix_users=["@alice:matrix.org"],
        )
        plan = service.consume(
            plan_id=plan.id, short_code=short, approver_matrix_id="@alice:matrix.org",
            room_id=room, approval_event_id=f"{event}-approve",
        )
        assert plan is not None

        # 直接调用 handler，验证透传参数
        from app.services.plan_executor import _service_control_handler
        with patch("app.services.approval_executor.ApprovalExecutor._control_single_server") as mock_ctl:
            mock_ctl.return_value = {"server": "q1", "ok": True}
            result = _service_control_handler(plan, plan.steps[0], session)
        assert result["action"] == "SERVICE_CONTROL"

        assert result["action"] == "SERVICE_CONTROL"
        args, kwargs = mock_ctl.call_args
        assert kwargs["compose_service"] == "system"
        assert kwargs["env"] == {"SYSTEM_TRACE": "true"}
        assert kwargs["compose_args"] == ["--force-recreate"]

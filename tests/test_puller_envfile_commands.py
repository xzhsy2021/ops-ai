"""puller 服务配置 + env_file 命令生成回归。

2026-09-07 puller 迁移配套：验证 (1) env_file 只影响配置了它的服务，
现有服务命令逐字符不变；(2) 新 puller 服务的四类命令带 --env-file。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("_isolated_test_db")


@pytest.fixture()
def db():
    from app.db.base import Base, SessionLocal, engine
    from app.db.migrations.runner import run_schema_migrations

    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


# ── 命令生成（不依赖 DB）──

def _cfg(template: str, tv: dict) -> dict:
    return {"template": template, "template_variables": tv, "found": True}


class TestEnvFileCommandGeneration:
    def test_no_env_file_commands_unchanged(self):
        """无 env_file 的 docker_compose 服务：命令与迁移前逐字符一致。"""
        from app.services.tool_adapters.server_tools import _resolve_service_control_command as resolve

        cfg = _cfg("docker_compose", {
            "compose_dir": "/data/crypto-trader",
            "compose_file": "docker-compose.yml",
            "compose_service": "",
        })
        # 迁移前的生成逻辑（硬编码期望，锁定回归）
        assert resolve(cfg, "restart") == "docker compose -f docker-compose.yml restart"
        assert resolve(cfg, "stop") == "docker compose -f docker-compose.yml stop"
        assert resolve(cfg, "start") == "docker compose -f docker-compose.yml up -d"
        assert resolve(cfg, "update") == (
            "docker compose -f docker-compose.yml pull && "
            "docker compose -f docker-compose.yml up -d --no-deps"
        )

    def test_env_file_in_all_subcommands(self):
        """配置 env_file 的服务：ps/stop/up/pull 全部带 --env-file。"""
        from app.services.tool_adapters.server_tools import _resolve_service_control_command as resolve

        cfg = _cfg("docker_compose", {
            "compose_dir": "/data/crypto-trader",
            "compose_file": "docker-compose.puller.yml",
            "env_file": "env.puller",
            "compose_service": "puller-query",
        })
        assert resolve(cfg, "restart") == (
            "docker compose --env-file env.puller -f docker-compose.puller.yml restart puller-query"
        )
        assert resolve(cfg, "stop") == (
            "docker compose --env-file env.puller -f docker-compose.puller.yml stop puller-query"
        )
        assert resolve(cfg, "start") == (
            "docker compose --env-file env.puller -f docker-compose.puller.yml up -d puller-query"
        )
        assert resolve(cfg, "update") == (
            "docker compose --env-file env.puller -f docker-compose.puller.yml pull puller-query && "
            "docker compose --env-file env.puller -f docker-compose.puller.yml up -d --no-deps puller-query"
        )

    def test_explicit_command_still_wins(self):
        """显式 restart_command 等仍最高优先（不受 env_file 影响）。"""
        from app.services.tool_adapters.server_tools import _resolve_service_control_command as resolve

        cfg = _cfg("docker_compose", {
            "compose_dir": "/x",
            "env_file": "env.x",
            "restart_command": "systemctl restart foo",
        })
        assert resolve(cfg, "restart") == "systemctl restart foo"

    def test_generic_backend_unaffected(self):
        """generic_backend_direct（无 compose 配置）：完全不走 compose 分支。"""
        from app.services.tool_adapters.server_tools import _resolve_service_control_command as resolve

        cfg = _cfg("generic_backend_direct", {
            "pm2_name": "exchange",
        })
        assert resolve(cfg, "restart") == "pm2 restart exchange"


# ── DB 配置形态（migrate 后）──

class TestPullerServiceConfig:
    def test_puller_services_registered(self, db):
        """4 个新服务 + 改造后的 puller：docker_compose + test1 绑定。"""
        import os

        from app.db.models import Service

        # scripts.migrate_puller_services 是"导入即执行"的一次性迁移脚本，导入期会
        # 做 os.environ.setdefault("APPROVAL_SIGNING_KEY", ...)（它需要该变量才能
        # 导入 app 模块，故不改脚本）。这里导入后立即还原，避免把该键泄漏给同进程
        # 后续测试——曾使 test_task5_final_review 的 dotenv 用例误判（该用例依赖
        # "只有未导出的键才回填"的语义）。
        prev_signing_key = os.environ.get("APPROVAL_SIGNING_KEY")
        from scripts.migrate_puller_services import COMPOSE_TV_BASE, NEW_SERVICES, TEST1  # noqa

        if prev_signing_key is None:
            os.environ.pop("APPROVAL_SIGNING_KEY", None)
        else:
            os.environ["APPROVAL_SIGNING_KEY"] = prev_signing_key

        # 模拟迁移（复用脚本的逻辑而非直接调脚本 main）
        for name, compose_svc in NEW_SERVICES.items():
            db.add(Service(
                system_name="crypto-trader", name=name, template="docker_compose",
                template_variables={**COMPOSE_TV_BASE, "compose_service": compose_svc,
                                    "servers_by_env": {"test": [TEST1]}},
            ))
        db.commit()

        from app.services.tool_adapters.server_tools import _resolve_service_control_command as resolve
        svc = db.query(Service).filter_by(system_name="crypto-trader", name="puller-query").first()
        cfg = _cfg(svc.template, svc.template_variables)
        cmd = resolve(cfg, "update")
        assert "--env-file env.puller" in cmd
        assert "puller-query" in cmd
        assert "--no-deps" in cmd, "单容器更新不得连带重建依赖服务"

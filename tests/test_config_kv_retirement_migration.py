import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.migrations import runner


def _legacy_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE config_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        ))
    return engine


def test_retirement_migrates_recognized_config_and_inventory_then_drops_table():
    retire = getattr(runner, "retire_config_kv", None)
    assert retire is not None
    engine = _legacy_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO systems(id, name, display_name, strategy, environments, services) "
                "VALUES('sys-1', 'crypto-trader', 'Crypto Trader', 'WORKFLOW', :envs, :services)"
            ), {
                "envs": json.dumps({
                    "test": {
                        "display_name": "Test",
                        "category": "test",
                        "servers": ["test-1"],
                        "variables": {"TRACE": "true"},
                    }
                }),
                "services": json.dumps([
                    {"name": "risk", "display_name": "Risk", "template": "generic_backend"}
                ]),
            })
            conn.execute(text(
                "INSERT INTO services(id, name, system_name) VALUES('svc-1', 'crypto-system', 'crypto')"
            ))
            conn.execute(text(
                "INSERT INTO services(id, name, system_name, template_variables) "
                "VALUES('svc-2', 'system', 'crypto-trader', '{\"update_command\": \"new\"}')"
            ))
            legacy = {
                "capability_server": {"enabled": False},
                "release_retention": {"deploy_keep_days": 30},
                "package_retention": {"keep_days": 14},
                "runtime_retention": {"log_file_keep_days": 7},
                "notification_settings": {"enabled": True},
                "inspection_profiles": {"items": [{"id": "nightly", "enabled": True}]},
                "workflow_templates": {"custom": {"steps": [{"type": "wait"}]}},
                "deploy_defaults": {"strategy": "DIRECT"},
                "global_variables": {"region": "ap-east", "replicas": 2},
            }
            for key, value in legacy.items():
                conn.execute(
                    text("INSERT INTO config_kv(key, value) VALUES(:key, :value)"),
                    {"key": key, "value": json.dumps(value)},
                )

        retire(engine)

        assert "config_kv" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert json.loads(conn.execute(text(
                "SELECT settings FROM capability_settings WHERE id='default'"
            )).scalar_one()) == {"enabled": False}
            retention = {
                row[0]: json.loads(row[1])
                for row in conn.execute(text(
                    "SELECT policy_type, settings FROM retention_policies ORDER BY policy_type"
                )).fetchall()
            }
            assert retention == {
                "package": {"keep_days": 14},
                "release": {"deploy_keep_days": 30},
                "runtime": {"log_file_keep_days": 7},
            }
            assert conn.execute(text(
                "SELECT system_name FROM services WHERE name='system'"
            )).scalar_one() == "crypto-trader"
            assert conn.execute(text(
                "SELECT COUNT(*) FROM services WHERE name='crypto-system'"
            )).scalar_one() == 0
            assert conn.execute(text(
                "SELECT COUNT(*) FROM services WHERE system_name='crypto-trader' AND name='risk'"
            )).scalar_one() == 1
            env = conn.execute(text(
                "SELECT display_name, variables FROM system_environments "
                "WHERE system_name='crypto-trader' AND name='test'"
            )).one()
            assert env[0] == "Test"
            assert json.loads(env[1]) == {"TRACE": "true"}
            assert conn.execute(text("SELECT COUNT(*) FROM inspection_profiles")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM workflow_templates")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM global_variables")).scalar_one() == 2

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO services(id, name, system_name) "
                    "VALUES('duplicate', 'system', 'crypto-trader')"
                ))

        retire(engine)
    finally:
        engine.dispose()


def test_retirement_rejects_unknown_orphan_service_without_dropping_legacy_data():
    retire = getattr(runner, "retire_config_kv", None)
    assert retire is not None
    engine = _legacy_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO services(id, name, system_name) VALUES('svc-1', 'worker', 'unknown-system')"
            ))
            conn.execute(text(
                "INSERT INTO config_kv(key, value) VALUES('capability_server', '{\"enabled\": true}')"
            ))

        with pytest.raises(RuntimeError, match="unknown-system"):
            retire(engine)

        assert "config_kv" in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM config_kv")).scalar_one() == 1
    finally:
        engine.dispose()


def test_retirement_migrates_legacy_asset_buckets():
    cleanup = next(
        item for item in runner.MIGRATIONS
        if item["version"] == "081_001_cleanup_legacy_kv"
    )
    assert cleanup["sql"] == "SELECT 1"

    engine = _legacy_engine()
    try:
        legacy = {
            "systems": {
                "demo": {
                    "display_name": "Demo",
                    "servers": ["demo-1"],
                    "services": [{"name": "api", "template": "generic_backend"}],
                    "environments": {"test": {"servers": ["demo-1"]}},
                    "groups": {"blue": {"servers": ["demo-1"]}},
                }
            },
            "servers": [{"name": "demo-1", "host": "10.0.0.10", "user": "ops"}],
            "jump_hosts": [{"name": "bastion", "host": "10.0.0.1", "user": "ops"}],
            "server_groups": {
                "shared": {"display_name": "Shared", "servers": ["demo-1"]}
            },
            "pipelines": {
                "pipe-1": {
                    "name": "Demo Pipeline",
                    "system_name": "demo",
                    "steps": [{"name": "Restart", "type": "command", "config": {"cmd": "restart"}}],
                }
            },
            "settings": {
                "release_retention": {"deploy_keep_days": 45},
                "package_retention": {"package_keep_days": 30},
                "notification_settings": {"enabled": False},
            },
        }
        with engine.begin() as conn:
            for key, value in legacy.items():
                conn.execute(text(
                    "INSERT INTO config_kv(key, value) VALUES(:key, :value)"
                ), {"key": key, "value": json.dumps(value)})

        runner.retire_config_kv(engine)

        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM systems WHERE name='demo'")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM services WHERE system_name='demo' AND name='api'")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM system_environments WHERE system_name='demo' AND name='test'")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM servers WHERE name='demo-1'")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM jump_hosts WHERE name='bastion'")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM server_groups WHERE name='demo-blue'")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM server_groups WHERE name='shared'")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM pipelines WHERE id='pipe-1'")).scalar_one() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM pipeline_steps WHERE pipeline_id='pipe-1'")).scalar_one() == 1
            assert json.loads(conn.execute(text(
                "SELECT settings FROM retention_policies WHERE policy_type='release'"
            )).scalar_one()) == {"deploy_keep_days": 45}
            assert json.loads(conn.execute(text(
                "SELECT settings FROM notification_settings WHERE id='default'"
            )).scalar_one()) == {"enabled": False}
    finally:
        engine.dispose()

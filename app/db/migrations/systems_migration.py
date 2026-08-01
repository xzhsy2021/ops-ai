"""Phase 3e SSOT: 将 config_kv['systems'] blob 迁移到 systems 表。

幂等：已存在的 system 不会被覆盖。
迁移成功后删除 config_kv['systems']，防止后续读 blob 回退。
如果 config_kv['systems'] 不存在（已被清理），从 DEFAULT_CONFIG_SEED 恢复。
"""
import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 内置 seed（从 git 历史的 DEFAULT_CONFIG_SEED 恢复，用于 config_kv 已被清理的场景）
_BUILTIN_SYSTEMS_SEED: Dict[str, Any] = {
    "buoy": {
        "display_name": "Buoy 浮标",
        "servers": ["prod-1"],
        "services": [
            {"name": "buoy-frontend", "display_name": "前端", "template": "generic_frontend", "servers": ["prod-1"], "template_variables": {"deploy_path": "/data/www", "update_script": "./www.sh"}},
            {"name": "buoy-core", "display_name": "Core 端", "template": "generic_backend_bluegreen", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/buoy/core", "service_name": "core", "pm2_name": "core", "caddy_config": "/etc/caddy/buoy.conf", "caddy_old_port": "8080", "caddy_new_port": "8081"}},
            {"name": "buoy-order", "display_name": "Order 端", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/buoy/order1", "service_name": "order", "update_script": "pm2 restart order", "pm2_name": "order"}},
            {"name": "buoy-module", "display_name": "Module 端", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/buoy/module1.2", "service_name": "module", "update_script": "pm2 restart module", "pm2_name": "module"}},
        ],
    },
    "crypto-trader": {
        "display_name": "Crypto Trader 量化",
        "servers": ["prod-1"],
        "services": [
            {"name": "crypto-docker-compose", "display_name": "Docker Compose 统一部署", "template": "docker_compose", "servers": ["prod-1"], "template_variables": {"compose_dir": "/data/crypto-trader", "compose_file": "docker-compose.yml", "wait_after_up": 10, "log_tail_lines": 30, "server_keywords": ["cc-test", "量化测试", "测试"], "servers_by_env": {"test": ["量化测试服务器", "量化测试服务器2"]}}},
            {"name": "crypto-frontend", "display_name": "前端", "template": "generic_frontend", "servers": ["prod-1"], "template_variables": {"deploy_path": "/data/www", "update_script": "./www.sh", "server_keywords": ["frontend", "web", "www", "前端"], "servers_by_env": {}}},
            {"name": "crypto-exchange", "display_name": "Exchange", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/exchange", "service_name": "exchange", "update_script": "./updatebin.sh", "pm2_name": "exchange", "server_keywords": ["exchange"], "servers_by_env": {}}},
            {"name": "crypto-monitor", "display_name": "Monitor", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/monitor", "service_name": "monitor", "update_script": "./updatebin.sh", "pm2_name": "monitor", "server_keywords": ["monitor"], "servers_by_env": {}}},
            {"name": "crypto-puller", "display_name": "Puller", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/puller", "service_name": "puller", "update_script": "./updatebin.sh", "pm2_name": "puller", "server_keywords": ["puller"], "servers_by_env": {}}},
            {"name": "crypto-risk", "display_name": "Risk", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/risk", "service_name": "risk", "update_script": "./updatebin.sh", "pm2_name": "risk", "server_keywords": ["risk"], "servers_by_env": {}}},
            {"name": "crypto-sender", "display_name": "Sender", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/sender", "service_name": "sender", "update_script": "./updatebin.sh", "pm2_name": "sender", "server_keywords": ["sender"], "servers_by_env": {}}},
            {"name": "crypto-strategy", "display_name": "Strategy", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/strategy", "service_name": "strategy", "update_script": "./updatebin.sh", "pm2_name": "strategy", "server_keywords": ["strategy"], "servers_by_env": {}}},
            {"name": "crypto-supplier", "display_name": "Supplier", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/supplier", "service_name": "supplier", "update_script": "./updatebin.sh", "pm2_name": "supplier", "server_keywords": ["supplier"], "servers_by_env": {}}},
            {"name": "crypto-system", "display_name": "System", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/system", "service_name": "system", "update_script": "./updatebin.sh", "pm2_name": "system", "server_keywords": ["system", "main", "master", "主节点", "主"], "servers_by_env": {}}},
            {"name": "crypto-trader", "display_name": "Trader", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/trader", "service_name": "trader", "update_script": "./updatebin.sh", "pm2_name": "trader", "server_keywords": ["trader"], "servers_by_env": {}}},
            {"name": "crypto-transaction", "display_name": "Transaction", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/crypto-trader/transaction", "service_name": "transaction", "update_script": "./updatebin.sh", "pm2_name": "transaction", "server_keywords": ["transaction"], "servers_by_env": {}}},
        ],
        "environments": {
            "prod": {"display_name": "线上环境", "base_path": "/data/bin/crypto-trader", "servers": [], "variables": {"environment": "prod"}},
            "test": {"display_name": "测试环境", "base_path": "/data/crypto-trader", "servers": [], "variables": {"environment": "test", "compose_dir": "/data/crypto-trader", "compose_file": "docker-compose.yml"}},
        },
        "variables": {
            "default_server_group": "量化",
            "prod_server_exclude_keywords": ["测试", "test", "dev", "qa", "stage", "staging", "uat"],
            "test_server_include_keywords": ["测试", "test", "dev", "qa", "stage", "staging", "uat"],
        },
    },
    "insider": {
        "display_name": "Insider 报表",
        "servers": ["prod-1"],
        "services": [
            {"name": "insider-frontend", "display_name": "前端", "template": "generic_frontend", "servers": ["prod-1"], "template_variables": {"deploy_path": "/data/www", "update_script": "./www.sh"}},
            {"name": "insider-backend", "display_name": "后端", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/insider", "service_name": "server", "update_script": "pm2 restart insider", "pm2_name": "insider"}},
        ],
    },
    "sleuther": {
        "display_name": "Sleuther 风控",
        "servers": ["prod-1"],
        "services": [
            {"name": "sleuther-frontend", "display_name": "前端", "template": "generic_frontend", "servers": ["prod-1"], "template_variables": {"deploy_path": "/data/www", "update_script": "./www.sh"}},
            {"name": "sleuther-backend", "display_name": "后端", "template": "generic_backend_bluegreen", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/sleuther/core", "service_name": "sleuther", "pm2_name": "sleuther", "caddy_config": "/etc/caddy/sleuther.conf", "caddy_old_port": "8080", "caddy_new_port": "8081"}},
        ],
    },
    "bot-hub": {
        "display_name": "Bot Hub 机器人",
        "servers": ["prod-1"],
        "services": [
            {"name": "bot-hub-update", "display_name": "程序更新", "template": "generic_backend_direct", "servers": ["prod-1"], "template_variables": {"service_dir": "/data/bin/bot-hub", "service_name": "core", "update_script": "pm2 restart bot-hub", "pm2_name": "bot-hub"}},
        ],
    },
    "dovo": {
        "display_name": "Dovo",
        "strategy": "DOVO",
        "description": "Dovo 多区域部署系统，每个区域独立蓝绿发布",
        "groups": {},
    },
}


def _load_systems_blob() -> Dict[str, Any]:
    """从 config_kv 读 systems blob，失败返回空 dict。"""
    try:
        from app.config.repository import get_db_connection
        conn = get_db_connection()
        row = conn.execute("SELECT value FROM config_kv WHERE key = 'systems'").fetchone()
        if not row:
            return {}
        return json.loads(row["value"]) or {}
    except Exception as e:
        logger.debug(f"Failed to load systems blob: {e}")
        return {}


def _delete_systems_kv():
    """迁移成功后删除 config_kv['systems']。"""
    try:
        from app.config.repository import get_db_connection
        conn = get_db_connection()
        conn.execute("DELETE FROM config_kv WHERE key = 'systems'")
        conn.commit()
        logger.info("Cleaned up config_kv['systems'] after migration to DB table")
    except Exception as e:
        logger.warning(f"Failed to cleanup config_kv['systems']: {e}")


def migrate_systems_blob_to_table():
    """将 config_kv['systems'] blob 迁移到 systems 表。

    幂等：已存在的 system name 跳过，不覆盖。
    迁移后删除 config_kv['systems']。
    如果 config_kv['systems'] 不存在，从内置 seed 恢复（开发环境兼容）。
    """
    from app.db.base import SessionLocal
    from app.db.models import System

    # 先从 config_kv 读 blob
    blob = _load_systems_blob()
    if not blob:
        # config_kv['systems'] 不存在（可能已被清理），从内置 seed 恢复
        logger.info("config_kv['systems'] not found, using builtin seed for recovery")
        blob = _BUILTIN_SYSTEMS_SEED

    migrated = 0
    with SessionLocal() as db:
        for sys_name, sys_cfg in blob.items():
            if not isinstance(sys_cfg, dict):
                continue
            existing = db.query(System).filter(System.name == sys_name).first()
            if existing:
                continue
            row = System(
                name=sys_name,
                display_name=sys_cfg.get("display_name") or sys_name,
                strategy=sys_cfg.get("strategy", "WORKFLOW"),
                base_path=sys_cfg.get("base_path", "/data/web/app"),
                description=sys_cfg.get("description"),
                variables=sys_cfg.get("variables", {}) or {},
                servers=sys_cfg.get("servers", []) or [],
                environments=sys_cfg.get("environments", {}) or {},
                services=sys_cfg.get("services", []) or [],
            )
            db.add(row)
            migrated += 1
            logger.info(f"Migrated system '{sys_name}' to DB table")
        if migrated:
            db.commit()

    # 迁移成功后删除 config_kv['systems']
    if _load_systems_blob():
        _delete_systems_kv()

    return migrated

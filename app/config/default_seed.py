# Auto-generated from legacy config/default_config.json.
# Runtime configuration is stored in the database table config_kv.
# This module is only a bootstrap seed for fresh databases and does not persist changes.
# Note: 'servers' and 'jump_hosts' removed — Server/JumpHost DB tables are SSOT (Phase 3a/3.g).
# Note: 'settings' removed — retention/notification 独立 KV 键为 SSOT，settings 桶已废弃。
# Note: 'systems' removed — System DB table is SSOT (Phase 3e). Seed 由 systems_migration.py 负责。

DEFAULT_CONFIG_SEED = {
    'capability_server': {
        'enabled': True,
        'http_tools_enabled': True,
        'mcp_enabled': True,
        'read_only': False,
        'allow_deploy_plan': True,
        'allow_deploy_execute': False,
        'allow_prod_deploy': False,
        'allow_config_write': False,
        'allow_server_read': True,
        'allow_server_write': False,
        'allow_db_read_tools': True,
        'allow_db_export_tools': True,
        'allow_db_write_tools': False,
        'allow_high_risk_tools': True,
        'allow_critical_risk_tools': True,
        'allow_ai_token_to_run_inspection_execute': True,
        'require_confirmation': True,
        'taskize_high_risk_tools': True,
        'strict_prod_confirmation': True,
        'token_expire_days': 90,
        'allow_rollback': False,
        'allow_backup_write': False,
        'allow_backup_restore': False,
        'allow_package_write': False,
        'allow_package_cleanup': False,
        'allow_runtime_cleanup': False,
    },
}

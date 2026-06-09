"""Lightweight schema migration runner for the embedded SQLite-first deployment."""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List
from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

MIGRATIONS: List[Dict[str, str]] = [
    {
        "version": "071_001_ssh_keys",
        "name": "Create SSH keys table",
        "table": "ssh_keys",
        "sql": """CREATE TABLE IF NOT EXISTS ssh_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) UNIQUE NOT NULL,
            private_key_encrypted TEXT NOT NULL,
            passphrase_encrypted TEXT,
            description TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "071_002_read_only_default",
        "name": "Ensure read_only default is explicitly set for existing deployments",
        "sql": "SELECT 1",
        "note": "Migration applied via Python hook in migrate_read_only_default()",
    },


    {
        "version": "060_001_project_server_relations",
        "name": "Create project server relation table",
        "table": "project_server_relations",
        "sql": """CREATE TABLE IF NOT EXISTS project_server_relations (
            id VARCHAR(32) PRIMARY KEY,
            project_id VARCHAR(255) NOT NULL,
            server_id VARCHAR(255) NOT NULL,
            deploy_role VARCHAR(64),
            deploy_path VARCHAR(1024),
            config_path VARCHAR(1024),
            log_path VARCHAR(1024),
            backup_path VARCHAR(1024),
            runtime_user VARCHAR(128),
            main_port VARCHAR(32),
            active BOOLEAN DEFAULT 1,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "060_002_inspection_tasks",
        "name": "Create inspection task table",
        "table": "inspection_tasks",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_tasks (
            id VARCHAR(32) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            scope_type VARCHAR(24) NOT NULL,
            server_id VARCHAR(255),
            project_id VARCHAR(255),
            frequency VARCHAR(24) DEFAULT 'MANUAL',
            cron_expression VARCHAR(128),
            rule_group_id VARCHAR(64),
            enabled BOOLEAN DEFAULT 1,
            created_by VARCHAR(128),
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "060_003_inspection_runs",
        "name": "Create inspection run table",
        "table": "inspection_runs",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_runs (
            id VARCHAR(32) PRIMARY KEY,
            task_id VARCHAR(32),
            scope_kind VARCHAR(24) NOT NULL DEFAULT 'SERVER',
            scope_type VARCHAR(24) NOT NULL,
            server_id VARCHAR(255),
            project_id VARCHAR(255),
            agent_id VARCHAR(64),
            trigger_type VARCHAR(24) DEFAULT 'MANUAL',
            status VARCHAR(24) DEFAULT 'PENDING',
            score INTEGER DEFAULT 100,
            high_count INTEGER DEFAULT 0,
            medium_count INTEGER DEFAULT 0,
            low_count INTEGER DEFAULT 0,
            normal_count INTEGER DEFAULT 0,
            summary TEXT,
            categories JSON,
            metadata_json JSON,
            report_id VARCHAR(32),
            created_by VARCHAR(128),
            started_at DATETIME,
            finished_at DATETIME,
            duration_ms INTEGER DEFAULT 0,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "060_004_inspection_evidence",
        "name": "Create inspection evidence table",
        "table": "inspection_evidence",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_evidence (
            id VARCHAR(32) PRIMARY KEY,
            run_id VARCHAR(32) NOT NULL,
            source_type VARCHAR(32) DEFAULT 'COMMAND',
            source_path VARCHAR(1024),
            command TEXT,
            content_snapshot TEXT,
            content_hash VARCHAR(128),
            masked BOOLEAN DEFAULT 1,
            created_at DATETIME
        )""",
    },
    {
        "version": "060_005_inspection_item_results",
        "name": "Create inspection item result table",
        "table": "inspection_item_results",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_item_results (
            id VARCHAR(32) PRIMARY KEY,
            run_id VARCHAR(32) NOT NULL,
            scope_type VARCHAR(24) NOT NULL,
            server_id VARCHAR(255),
            project_id VARCHAR(255),
            category VARCHAR(64) NOT NULL,
            item_code VARCHAR(128) NOT NULL,
            item_name VARCHAR(255) NOT NULL,
            status VARCHAR(24) DEFAULT 'PASS',
            risk_level VARCHAR(24) DEFAULT 'NONE',
            message TEXT,
            suggestion TEXT,
            evidence_id VARCHAR(32),
            raw_output TEXT,
            started_at DATETIME,
            finished_at DATETIME,
            created_at DATETIME
        )""",
    },
    {
        "version": "060_006_inspection_issues",
        "name": "Create inspection issue table",
        "table": "inspection_issues",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_issues (
            id VARCHAR(32) PRIMARY KEY,
            run_id VARCHAR(32) NOT NULL,
            item_result_id VARCHAR(32),
            scope_type VARCHAR(24) NOT NULL,
            server_id VARCHAR(255),
            project_id VARCHAR(255),
            title VARCHAR(255) NOT NULL,
            description TEXT,
            risk_level VARCHAR(24) DEFAULT 'LOW',
            status VARCHAR(24) DEFAULT 'OPEN',
            owner_id VARCHAR(128),
            deadline_at DATETIME,
            fixed_at DATETIME,
            verified_at DATETIME,
            suggestion TEXT,
            evidence_id VARCHAR(32),
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "060_007_inspection_rules",
        "name": "Create inspection rule table",
        "table": "inspection_rules",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_rules (
            id VARCHAR(32) PRIMARY KEY,
            rule_code VARCHAR(128) NOT NULL UNIQUE,
            rule_name VARCHAR(255) NOT NULL,
            category VARCHAR(64) NOT NULL,
            scope_type VARCHAR(24) NOT NULL,
            risk_level VARCHAR(24) DEFAULT 'LOW',
            enabled BOOLEAN DEFAULT 1,
            deleted BOOLEAN DEFAULT 0,
            config_json JSON,
            rule_content TEXT,
            description TEXT,
            suggestion TEXT,
            version VARCHAR(64) DEFAULT 'inspection.v1',
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "060_008_inspection_baselines",
        "name": "Create inspection baseline table",
        "table": "inspection_baselines",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_baselines (
            id VARCHAR(32) PRIMARY KEY,
            scope_type VARCHAR(24) NOT NULL,
            server_id VARCHAR(255),
            project_id VARCHAR(255),
            baseline_type VARCHAR(64) NOT NULL,
            version VARCHAR(64) DEFAULT 'v1',
            content_json JSON,
            content_hash VARCHAR(128),
            active BOOLEAN DEFAULT 1,
            created_by VARCHAR(128),
            created_at DATETIME
        )""",
    },
    {
        "version": "060_009_inspection_runs_task_id",
        "name": "Add task id to inspection runs",
        "table": "inspection_runs",
        "column": "task_id",
        "sql": "ALTER TABLE inspection_runs ADD COLUMN task_id VARCHAR(32)",
    },
    {
        "version": "060_010_inspection_runs_agent_id",
        "name": "Add agent id to inspection runs",
        "table": "inspection_runs",
        "column": "agent_id",
        "sql": "ALTER TABLE inspection_runs ADD COLUMN agent_id VARCHAR(64)",
    },
    {
        "version": "060_011_inspection_runs_duration_ms",
        "name": "Add duration ms to inspection runs",
        "table": "inspection_runs",
        "column": "duration_ms",
        "sql": "ALTER TABLE inspection_runs ADD COLUMN duration_ms INTEGER DEFAULT 0",
    },
    {
        "version": "060_012_inspection_runs_scope_kind",
        "name": "Add legacy-compatible scope kind to inspection runs",
        "table": "inspection_runs",
        "column": "scope_kind",
        "sql": "ALTER TABLE inspection_runs ADD COLUMN scope_kind VARCHAR(24) NOT NULL DEFAULT 'SERVER'",
    },
    {
        "version": "060_013_inspection_runs_scope_kind_backfill",
        "name": "Backfill inspection run scope kind from scope type",
        "table": "inspection_runs",
        "sql": "UPDATE inspection_runs SET scope_kind = COALESCE(scope_type, scope_kind, 'SERVER') WHERE scope_kind IS NULL OR scope_kind = ''",
    },

    {
        "version": "060_014_inspection_rules_rule_content",
        "name": "Add editable rule content to inspection rules",
        "table": "inspection_rules",
        "column": "rule_content",
        "sql": "ALTER TABLE inspection_rules ADD COLUMN rule_content TEXT",
    },
    {
        "version": "060_015_inspection_rules_deleted",
        "name": "Add soft delete flag to inspection rules",
        "table": "inspection_rules",
        "column": "deleted",
        "sql": "ALTER TABLE inspection_rules ADD COLUMN deleted BOOLEAN DEFAULT 0",
    },

    {
        "version": "059_001_ai_analysis_runs",
        "name": "Create AI analysis run table",
        "table": "ai_analysis_runs",
        "sql": """CREATE TABLE IF NOT EXISTS ai_analysis_runs (
            id VARCHAR(32) PRIMARY KEY,
            analysis_type VARCHAR(64) NOT NULL,
            target_type VARCHAR(64),
            target_id VARCHAR(255),
            source_type VARCHAR(64),
            source_id VARCHAR(255),
            prompt_name VARCHAR(128),
            input_refs TEXT,
            output_json JSON,
            summary TEXT,
            confidence VARCHAR(32),
            created_by VARCHAR(128),
            created_at DATETIME
        )""",
    },
    {
        "version": "059_002_ai_analysis_findings",
        "name": "Create AI analysis finding table",
        "table": "ai_analysis_findings",
        "sql": """CREATE TABLE IF NOT EXISTS ai_analysis_findings (
            id VARCHAR(32) PRIMARY KEY,
            analysis_run_id VARCHAR(32) NOT NULL,
            title VARCHAR(255),
            finding_type VARCHAR(64),
            severity VARCHAR(32),
            claim TEXT,
            evidence_json JSON,
            suggestion TEXT,
            confidence VARCHAR(32),
            created_at DATETIME
        )""",
    },
    {
        "version": "059_003_ai_action_approvals",
        "name": "Create AI action approval table",
        "table": "ai_action_approvals",
        "sql": """CREATE TABLE IF NOT EXISTS ai_action_approvals (
            id VARCHAR(32) PRIMARY KEY,
            action_type VARCHAR(64) NOT NULL,
            tool_name VARCHAR(128) NOT NULL,
            target_type VARCHAR(64),
            target_id VARCHAR(255),
            request_payload JSON,
            risk_level VARCHAR(32),
            ai_reason TEXT,
            status VARCHAR(32) DEFAULT 'PENDING',
            requested_by VARCHAR(128),
            approved_by VARCHAR(128),
            created_at DATETIME,
            approved_at DATETIME,
            executed_at DATETIME
        )""",
    },
    {
        "version": "058_001_services_pipeline_id",
        "name": "Add pipeline id to services",
        "table": "services",
        "column": "pipeline_id",
        "sql": "ALTER TABLE services ADD COLUMN pipeline_id VARCHAR(64)",
    },
    {
        "version": "054_001_deployments_server_group",
        "name": "Add server group to deployments",
        "table": "deployments",
        "column": "server_group",
        "sql": "ALTER TABLE deployments ADD COLUMN server_group VARCHAR(128)",
    },
    {
        "version": "054_002_servers_jump_host",
        "name": "Add jump host to servers",
        "table": "servers",
        "column": "jump_host",
        "sql": "ALTER TABLE servers ADD COLUMN jump_host VARCHAR(64)",
    },
    {
        "version": "055_001_cleanup_jobs_fk_set_null",
        "name": "Cleanup jobs FK ON DELETE SET NULL marker",
        "table": "cleanup_jobs",
        "column": "connection_id",
        "sql": "SELECT 1",
        "note": "Model-level FK changed to ondelete=SET NULL. Existing SQLite FKs are not altered; ORM ensures cleanup_jobs.connection_id is set to NULL before deleting connections.",
    },
    {
        "version": "056_001_users_session_version",
        "name": "Add users session version for password reset invalidation",
        "table": "users",
        "column": "session_version",
        "sql": "ALTER TABLE users ADD COLUMN session_version INTEGER DEFAULT 1",
    },
    {
        "version": "053_007_deployment_server_tasks_current_step",
        "name": "Add current step to deployment server tasks",
        "table": "deployment_server_tasks",
        "column": "current_step",
        "sql": "ALTER TABLE deployment_server_tasks ADD COLUMN current_step VARCHAR(128)",
    },
    {
        "version": "053_008_deployment_server_tasks_updated_at",
        "name": "Add updated at to deployment server tasks",
        "table": "deployment_server_tasks",
        "column": "updated_at",
        "sql": "ALTER TABLE deployment_server_tasks ADD COLUMN updated_at DATETIME",
    },
    {
        "version": "053_014_deployment_server_tasks_duration_ms",
        "name": "Add duration ms to deployment server tasks",
        "table": "deployment_server_tasks",
        "column": "duration_ms",
        "sql": "ALTER TABLE deployment_server_tasks ADD COLUMN duration_ms INTEGER DEFAULT 0",
    },
    {
        "version": "053_009_deployment_step_tasks_sort_order",
        "name": "Add sort order to deployment step tasks",
        "table": "deployment_step_tasks",
        "column": "sort_order",
        "sql": "ALTER TABLE deployment_step_tasks ADD COLUMN sort_order INTEGER DEFAULT 0",
    },
    {
        "version": "053_010_deployment_step_tasks_updated_at",
        "name": "Add updated at to deployment step tasks",
        "table": "deployment_step_tasks",
        "column": "updated_at",
        "sql": "ALTER TABLE deployment_step_tasks ADD COLUMN updated_at DATETIME",
    },
    {
        "version": "053_015_deployment_step_tasks_duration_ms",
        "name": "Add duration ms to deployment step tasks",
        "table": "deployment_step_tasks",
        "column": "duration_ms",
        "sql": "ALTER TABLE deployment_step_tasks ADD COLUMN duration_ms INTEGER DEFAULT 0",
    },
    {
        "version": "053_013_deployment_step_tasks_captured_config",
        "name": "Add captured config snapshot to deployment step tasks",
        "table": "deployment_step_tasks",
        "column": "captured_config",
        "sql": "ALTER TABLE deployment_step_tasks ADD COLUMN captured_config TEXT",
    },
    {
        "version": "053_011_deployment_package_distributions_started_at",
        "name": "Add started at to deployment package distributions",
        "table": "deployment_package_distributions",
        "column": "started_at",
        "sql": "ALTER TABLE deployment_package_distributions ADD COLUMN started_at DATETIME",
    },
    {
        "version": "053_012_deployment_package_distributions_finished_at",
        "name": "Add finished at to deployment package distributions",
        "table": "deployment_package_distributions",
        "column": "finished_at",
        "sql": "ALTER TABLE deployment_package_distributions ADD COLUMN finished_at DATETIME",
    },
    {
        "version": "053_001_deployment_server_tasks_task_id",
        "name": "Add task id to deployment server tasks",
        "table": "deployment_server_tasks",
        "column": "task_id",
        "sql": "ALTER TABLE deployment_server_tasks ADD COLUMN task_id VARCHAR(32)",
    },
    {
        "version": "053_002_deployment_step_tasks_task_id",
        "name": "Add task id to deployment step tasks",
        "table": "deployment_step_tasks",
        "column": "task_id",
        "sql": "ALTER TABLE deployment_step_tasks ADD COLUMN task_id VARCHAR(32)",
    },
    {
        "version": "053_003_deployment_package_distributions_task_id",
        "name": "Add task id to deployment package distributions",
        "table": "deployment_package_distributions",
        "column": "task_id",
        "sql": "ALTER TABLE deployment_package_distributions ADD COLUMN task_id VARCHAR(32)",
    },
    {
        "version": "053_004_deployment_package_distributions_reused",
        "name": "Add reused flag to deployment package distributions",
        "table": "deployment_package_distributions",
        "column": "reused",
        "sql": "ALTER TABLE deployment_package_distributions ADD COLUMN reused BOOLEAN DEFAULT 0",
    },
    {
        "version": "053_005_deployment_package_distributions_duration_ms",
        "name": "Add duration ms to deployment package distributions",
        "table": "deployment_package_distributions",
        "column": "duration_ms",
        "sql": "ALTER TABLE deployment_package_distributions ADD COLUMN duration_ms INTEGER",
    },
    {
        "version": "053_006_deployment_package_distributions_updated_at",
        "name": "Add updated at to deployment package distributions",
        "table": "deployment_package_distributions",
        "column": "updated_at",
        "sql": "ALTER TABLE deployment_package_distributions ADD COLUMN updated_at DATETIME",
    },

    {
        "version": "052_001_database_connection_ssh_target_server_name",
        "name": "Add selected target host reference for database SSH proxy",
        "table": "database_connections",
        "column": "ssh_target_server_name",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_target_server_name VARCHAR(128)",
    },
    {
        "version": "052_002_database_connection_ssh_target_host",
        "name": "Add target host for two-hop database SSH proxy",
        "table": "database_connections",
        "column": "ssh_target_host",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_target_host VARCHAR(255)",
    },
    {
        "version": "052_003_database_connection_ssh_target_port",
        "name": "Add target SSH port for two-hop database SSH proxy",
        "table": "database_connections",
        "column": "ssh_target_port",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_target_port INTEGER DEFAULT 22",
    },
    {
        "version": "052_004_database_connection_ssh_target_username",
        "name": "Add target SSH username for two-hop database SSH proxy",
        "table": "database_connections",
        "column": "ssh_target_username",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_target_username VARCHAR(64)",
    },
    {
        "version": "052_005_database_connection_ssh_target_password",
        "name": "Add target SSH password for two-hop database SSH proxy",
        "table": "database_connections",
        "column": "ssh_target_password_encrypted",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_target_password_encrypted TEXT",
    },
    {
        "version": "052_006_database_connection_ssh_target_key_path",
        "name": "Add target SSH key path for two-hop database SSH proxy",
        "table": "database_connections",
        "column": "ssh_target_key_path",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_target_key_path VARCHAR(255)",
    },
    {
        "version": "052_007_database_connection_ssh_target_key_passphrase",
        "name": "Add target SSH key passphrase for two-hop database SSH proxy",
        "table": "database_connections",
        "column": "ssh_target_key_passphrase_encrypted",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_target_key_passphrase_encrypted TEXT",
    },

    {
        "version": "051_001_database_connection_ssh_server_name",
        "name": "Add selected server reference for database SSH proxy",
        "table": "database_connections",
        "column": "ssh_server_name",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_server_name VARCHAR(128)",
    },
    {
        "version": "048_001_dml_execution_logs_table",
        "name": "Create DML execution history table",
        "table": "dml_execution_logs",
        "sql": """CREATE TABLE IF NOT EXISTS dml_execution_logs (
            id VARCHAR(32) PRIMARY KEY,
            preview_id VARCHAR(64),
            source VARCHAR(32) DEFAULT 'local_ops_db',
            connection_id VARCHAR(32),
            connection_name VARCHAR(128),
            database_name VARCHAR(128),
            environment VARCHAR(64),
            statement_type VARCHAR(16) NOT NULL,
            table_name VARCHAR(128),
            history_sql TEXT NOT NULL,
            where_summary TEXT,
            changed_columns JSON,
            before_sample_json JSON,
            estimated_affected_rows INTEGER,
            affected_rows INTEGER,
            max_affected_rows INTEGER,
            risk_level VARCHAR(24) DEFAULT 'high',
            status VARCHAR(24) DEFAULT 'success',
            reason TEXT,
            operator VARCHAR(128),
            duration_ms INTEGER,
            audit_id VARCHAR(64),
            error_message TEXT,
            created_at DATETIME
        )""",
    },
    {
        "version": "048_002_database_connection_allow_dml",
        "name": "Add controlled DML policy columns to database connections",
        "table": "database_connections",
        "column": "allow_dml",
        "sql": "ALTER TABLE database_connections ADD COLUMN allow_dml BOOLEAN DEFAULT 0",
    },
    {
        "version": "048_003_database_connection_allowed_dml_types",
        "name": "Add allowed DML types to database connections",
        "table": "database_connections",
        "column": "allowed_dml_types",
        "sql": "ALTER TABLE database_connections ADD COLUMN allowed_dml_types JSON",
    },
    {
        "version": "048_004_database_connection_allowed_tables",
        "name": "Add allowed DML tables to database connections",
        "table": "database_connections",
        "column": "allowed_tables",
        "sql": "ALTER TABLE database_connections ADD COLUMN allowed_tables JSON",
    },
    {
        "version": "048_005_database_connection_blocked_tables",
        "name": "Add blocked DML tables to database connections",
        "table": "database_connections",
        "column": "blocked_tables",
        "sql": "ALTER TABLE database_connections ADD COLUMN blocked_tables JSON",
    },
    {
        "version": "048_006_database_connection_dml_row_cap",
        "name": "Add default DML affected row cap",
        "table": "database_connections",
        "column": "max_affected_rows_default",
        "sql": "ALTER TABLE database_connections ADD COLUMN max_affected_rows_default INTEGER DEFAULT 100",
    },
    {
        "version": "048_007_database_connection_require_dml_reason",
        "name": "Add DML reason requirement flag",
        "table": "database_connections",
        "column": "require_dml_reason",
        "sql": "ALTER TABLE database_connections ADD COLUMN require_dml_reason BOOLEAN DEFAULT 1",
    },
    {
        "version": "048_008_database_connection_ssh_mode",
        "name": "Add SSH mode selection for database connections",
        "table": "database_connections",
        "column": "ssh_mode",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_mode VARCHAR(16) DEFAULT 'manual'",
    },
    {
        "version": "048_009_database_connection_ssh_server_id",
        "name": "Add SSH server asset ID reference",
        "table": "database_connections",
        "column": "ssh_server_id",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_server_id VARCHAR(32)",
    },
    {
        "version": "048_010_database_connection_ssh_server_name",
        "name": "Add SSH server asset name reference",
        "table": "database_connections",
        "column": "ssh_server_name",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_server_name VARCHAR(64)",
    },
    {
        "version": "048_011_database_connection_ssh_key_content",
        "name": "Add encrypted SSH key content storage",
        "table": "database_connections",
        "column": "ssh_key_content_encrypted",
        "sql": "ALTER TABLE database_connections ADD COLUMN ssh_key_content_encrypted TEXT",
    },
    {
        "version": "039_001_report_artifacts_table",
        "name": "Create report artifacts table",
        "table": "report_artifacts",
        "sql": """CREATE TABLE IF NOT EXISTS report_artifacts (
            id VARCHAR(32) PRIMARY KEY,
            report_type VARCHAR(64) NOT NULL,
            title VARCHAR(255) NOT NULL,
            target_type VARCHAR(64),
            target_id VARCHAR(255),
            status VARCHAR(24) DEFAULT 'ready',
            format VARCHAR(16) DEFAULT 'json',
            file_path VARCHAR(1024),
            size_bytes INTEGER DEFAULT 0,
            sha256 VARCHAR(128),
            summary TEXT,
            metadata_json JSON,
            created_by VARCHAR(128),
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "035_001_operation_jobs_table",
        "name": "Create unified operation jobs table",
        "table": "operation_jobs",
        "sql": """CREATE TABLE IF NOT EXISTS operation_jobs (
            id VARCHAR(32) PRIMARY KEY,
            job_type VARCHAR(64) DEFAULT 'mcp_tool',
            source VARCHAR(64) DEFAULT 'tool',
            source_tool VARCHAR(128),
            title VARCHAR(255) NOT NULL,
            status VARCHAR(32) DEFAULT 'queued',
            progress INTEGER DEFAULT 0,
            risk_level VARCHAR(24) DEFAULT 'low',
            operator VARCHAR(128),
            target VARCHAR(255),
            request_json JSON,
            result_json JSON,
            error_message TEXT,
            audit_id VARCHAR(32),
            worker_id VARCHAR(128),
            created_at DATETIME,
            started_at DATETIME,
            finished_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "035_002_tool_call_logs_related_job_id",
        "name": "Add related job id to tool call logs",
        "table": "tool_call_logs",
        "column": "related_job_id",
        "sql": "ALTER TABLE tool_call_logs ADD COLUMN related_job_id VARCHAR(32)",
    },
    {
        "version": "025_001_deploy_packages_table",
        "name": "Create deploy package metadata table",
        "table": "deploy_packages",
        "sql": """CREATE TABLE IF NOT EXISTS deploy_packages (
            id VARCHAR(32) PRIMARY KEY,
            package_name VARCHAR(255) UNIQUE NOT NULL,
            file_path VARCHAR(1024),
            size_bytes INTEGER DEFAULT 0,
            sha256 VARCHAR(128),
            system VARCHAR(64),
            service VARCHAR(128),
            service_hint VARCHAR(128),
            version_hint VARCHAR(128),
            uploaded_by VARCHAR(128),
            uploaded_at DATETIME,
            last_used_at DATETIME,
            used_count INTEGER DEFAULT 0,
            protected BOOLEAN DEFAULT 0,
            deleted BOOLEAN DEFAULT 0,
            deleted_at DATETIME,
            delete_reason TEXT
        )""",
    },
    {
        "version": "025_002_deploy_package_refs_table",
        "name": "Create deploy package reference table",
        "table": "deploy_package_refs",
        "sql": """CREATE TABLE IF NOT EXISTS deploy_package_refs (
            id VARCHAR(32) PRIMARY KEY,
            package_id VARCHAR(32),
            package_name VARCHAR(255) NOT NULL,
            deployment_id VARCHAR(32),
            system VARCHAR(64),
            service VARCHAR(128),
            environment VARCHAR(64),
            server_name VARCHAR(128),
            usage_type VARCHAR(32) DEFAULT 'deploy',
            created_at DATETIME
        )""",
    },
    {
        "version": "013_001_deploy_task_payload",
        "name": "Add deploy task payload and cancel fields",
        "table": "deploy_tasks",
        "column": "payload_json",
        "sql": "ALTER TABLE deploy_tasks ADD COLUMN payload_json TEXT",
    },
    {
        "version": "013_002_deploy_task_lock_key",
        "name": "Add deploy task lock key",
        "table": "deploy_tasks",
        "column": "lock_key",
        "sql": "ALTER TABLE deploy_tasks ADD COLUMN lock_key VARCHAR(512)",
    },
    {
        "version": "013_003_deploy_task_cancel_requested",
        "name": "Add deploy task cancel flag",
        "table": "deploy_tasks",
        "column": "cancel_requested",
        "sql": "ALTER TABLE deploy_tasks ADD COLUMN cancel_requested BOOLEAN DEFAULT 0",
    },
    {
        "version": "070_001_servers_status",
        "name": "Add status to servers",
        "table": "servers",
        "column": "status",
        "sql": "ALTER TABLE servers ADD COLUMN status VARCHAR(24) DEFAULT 'online'",
    },
    {
        "version": "070_002_inspection_item_results_parsed_facts",
        "name": "Add parsed_facts JSON column to inspection_item_results",
        "table": "inspection_item_results",
        "column": "parsed_facts",
        "sql": "ALTER TABLE inspection_item_results ADD COLUMN parsed_facts JSON",
    },
    {
        "version": "080_001_servers_metadata_json",
        "name": "Add metadata_json column to servers for Phase 3a SSOT migration",
        "table": "servers",
        "column": "metadata_json",
        "sql": "ALTER TABLE servers ADD COLUMN metadata_json JSON",
    },
    {
        "version": "080_002_server_groups_metadata_json",
        "name": "Add metadata_json column to server_groups for Phase 3b SSOT migration",
        "table": "server_groups",
        "column": "metadata_json",
        "sql": "ALTER TABLE server_groups ADD COLUMN metadata_json JSON",
    },
    {
        "version": "080_003_jump_hosts",
        "name": "Create jump_hosts table for Phase 3.g SSOT migration",
        "table": "jump_hosts",
        "sql": """CREATE TABLE IF NOT EXISTS jump_hosts (
            id VARCHAR(32) PRIMARY KEY,
            name VARCHAR(64) UNIQUE NOT NULL,
            host VARCHAR(255) NOT NULL,
            port INTEGER DEFAULT 22,
            "user" VARCHAR(64) DEFAULT 'root',
            key VARCHAR(255) DEFAULT '~/.ssh/id_rsa',
            key_content TEXT,
            password TEXT,
            status VARCHAR(24) DEFAULT 'online',
            description TEXT,
            tags JSON DEFAULT '[]',
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        # 修复 ToolToken ETag 缓存陷阱：之前没有 updated_at，ETag 仅基于 id+created_at，
        # PATCH 修改 scopes/allow_write/prod 后 ETag 不变，浏览器/axios 拿到 304 后回放旧数据，
        # 导致前端看似"保存成功，再次编辑又没权限了"。
        "version": "080_004_tool_tokens_updated_at",
        "name": "Add updated_at to tool_tokens for correct ETag/304 invalidation",
        "table": "tool_tokens",
        "column": "updated_at",
        "sql": "ALTER TABLE tool_tokens ADD COLUMN updated_at DATETIME",
    },
    {
        # 日/周/月三级巡检调度表：把 3j 设计落库。
        # tier: DAILY / WEEKLY / MONTHLY / MANUAL
        # next_run_at: 下一次应执行时间（worker 轮询这个字段）
        # last_run_at / last_run_id: 上次执行情况，用于断点续跑和告警去重
        "version": "080_005_inspection_tier_schedules",
        "name": "Create inspection_tier_schedules for 3-tier (daily/weekly/monthly) inspection",
        "table": "inspection_tier_schedules",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_tier_schedules (
            id VARCHAR(32) PRIMARY KEY,
            name VARCHAR(128) UNIQUE NOT NULL,
            tier VARCHAR(16) NOT NULL,
            cron_expression VARCHAR(64) NOT NULL,
            timezone VARCHAR(64) DEFAULT 'Asia/Shanghai',
            enabled BOOLEAN DEFAULT 1,
            timeout_minutes INTEGER DEFAULT 60,
            concurrency INTEGER DEFAULT 3,
            target_filter JSON DEFAULT '{}',
            categories JSON DEFAULT '[]',
            thresholds JSON DEFAULT '{}',
            notification JSON DEFAULT '{}',
            retention JSON DEFAULT '{}',
            report JSON DEFAULT '{}',
            require_approval BOOLEAN DEFAULT 0,
            next_run_at DATETIME,
            last_run_at DATETIME,
            last_run_id VARCHAR(32),
            last_status VARCHAR(24),
            last_error TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        # 巡检"通知路由"表：把 severity → channels/recipients/SLA 落库，
        # 而不是写死在 yaml 里，方便运行时改。
        "version": "080_006_inspection_notification_routes",
        "name": "Create inspection_notification_routes for severity-based routing",
        "table": "inspection_notification_routes",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_notification_routes (
            id VARCHAR(32) PRIMARY KEY,
            severity VARCHAR(16) NOT NULL,
            tier VARCHAR(16),
            channels JSON DEFAULT '[]',
            recipients JSON DEFAULT '[]',
            sla_minutes INTEGER,
            enabled BOOLEAN DEFAULT 1,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        # 静默期 + 跨级联策略：避免轰炸 + 高危自动升级
        "version": "080_007_inspection_silence_and_cascade",
        "name": "Create inspection_silence_and_cascade policies",
        "table": "inspection_cascade_policies",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_cascade_policies (
            id VARCHAR(32) PRIMARY KEY,
            name VARCHAR(128) UNIQUE NOT NULL,
            trigger_type VARCHAR(32) NOT NULL,
            trigger_config JSON DEFAULT '{}',
            action VARCHAR(64) NOT NULL,
            action_config JSON DEFAULT '{}',
            enabled BOOLEAN DEFAULT 1,
            last_triggered_at DATETIME,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "080_012_tool_tokens_description",
        "name": "Add operator-facing description to tool_tokens",
        "table": "tool_tokens",
        "column": "description",
        "sql": "ALTER TABLE tool_tokens ADD COLUMN description TEXT",
    },

]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _ensure_schema_table(conn):
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version VARCHAR(64) PRIMARY KEY, "
        "name VARCHAR(255) NOT NULL, "
        "applied_at DATETIME NOT NULL, "
        "checksum VARCHAR(128))"
    ))


def _checksum(mig: Dict[str, str]) -> str:
    raw = f"{mig.get('version')}|{mig.get('name')}|{mig.get('sql')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _is_applied(conn, version: str) -> bool:
    row = conn.execute(text("SELECT version FROM schema_migrations WHERE version=:v"), {"v": version}).fetchone()
    return bool(row)


def _mark_applied(conn, mig: Dict[str, str]):
    conn.execute(
        text("INSERT OR REPLACE INTO schema_migrations(version, name, applied_at, checksum) VALUES(:v, :n, :a, :c)"),
        {"v": mig["version"], "n": mig.get("name", mig["version"]), "a": _now_iso(), "c": _checksum(mig)},
    )


def run_schema_migrations(engine) -> List[str]:
    """Run idempotent migrations and return applied version list."""
    applied: List[str] = []
    with engine.begin() as conn:
        _ensure_schema_table(conn)
        for mig in MIGRATIONS:
            version = mig["version"]
            if _is_applied(conn, version):
                continue
            table = mig.get("table")
            column = mig.get("column")
            insp = inspect(conn)
            if table and not insp.has_table(table):
                # CREATE TABLE migrations should run when the table is missing;
                # ALTER COLUMN migrations should be skipped until the base table exists.
                if str(mig.get("sql") or "").lstrip().upper().startswith("CREATE TABLE"):
                    logger.info("Applying schema migration %s", version)
                    conn.execute(text(mig["sql"]))
                    _mark_applied(conn, mig)
                    applied.append(version)
                continue
            if table and column:
                existing = [c["name"] for c in insp.get_columns(table)]
                if column in existing:
                    _mark_applied(conn, mig)
                    applied.append(version)
                    continue
            if mig.get("sql"):
                logger.info("Applying schema migration %s", version)
                conn.execute(text(mig["sql"]))
                _mark_applied(conn, mig)
                applied.append(version)
    return applied


def migrate_read_only_default():
    """Backfill missing capability_server switches for existing deployments.

    Only keys that are absent in the stored config are filled in with the
    current DEFAULT_CAPABILITY_SETTINGS. Existing user values are preserved
    so an admin who intentionally enabled a stricter mode is not overridden.
    This is the same belt-and-suspenders migration we use elsewhere: the
    goal is "no 403 just because a new key was added in code", not "rewrite
    admin choices".
    """
    try:
        from config_manager import load_config, save_config
        from app.services.tool_policy import DEFAULT_CAPABILITY_SETTINGS
        config = load_config()
        if "capability_server" not in config:
            return
        caps = config["capability_server"]
        if not isinstance(caps, dict):
            return
        changed = False
        for key, default in DEFAULT_CAPABILITY_SETTINGS.items():
            if key not in caps:
                caps[key] = default
                changed = True
        if changed:
            save_config(config)
            logger.info("Backfilled capability_server defaults for keys: %s",
                        [k for k in DEFAULT_CAPABILITY_SETTINGS if k in caps and caps[k] == DEFAULT_CAPABILITY_SETTINGS[k]])
    except Exception:
        logger.exception("Failed to apply capability_server default backfill")

"""Lightweight schema migration runner for the embedded SQLite-first deployment."""
import hashlib
import json
import logging
import uuid
import re
from datetime import datetime, timezone
from typing import Any, Dict, List
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
    {
        "version": "081_001_cleanup_legacy_kv",
        "name": "Preserve legacy KV assets for terminal domain migration",
        "table": "config_kv",
        "sql": "SELECT 1",
    },
    {
        "version": "081_002_migrate_audit_logs_to_records",
        "name": "Migrate audit_logs data to ORM audit_records table before dropping legacy table",
        "table": "audit_logs",
        "sql": "INSERT OR IGNORE INTO audit_records (action, target_type, target_name, details, created_at) SELECT action, target_type, target_name, details, created_at FROM audit_logs",
    },
    {
        "version": "081_003_drop_legacy_tables",
        "name": "Drop legacy tables: audit_logs, deploy_logs_old, deploy_locks (migrated or dead code)",
        "sql": "DROP TABLE IF EXISTS audit_logs",
    },
    {
        "version": "081_004_drop_deploy_logs_old",
        "name": "Drop legacy table deploy_logs_old (dead code, no INSERT/SELECT)",
        "sql": "DROP TABLE IF EXISTS deploy_logs_old",
    },
    {
        "version": "081_005_drop_deploy_locks",
        "name": "Drop legacy table deploy_locks (dead code, locks use ORM deployment_lock_records)",
        "sql": "DROP TABLE IF EXISTS deploy_locks",
    },
    {
        "version": "082_001_create_systems_table",
        "name": "Create systems table for Phase 3e SSOT (replace config_kv['systems'] blob)",
        "sql": """CREATE TABLE IF NOT EXISTS systems (
            id VARCHAR(32) PRIMARY KEY,
            name VARCHAR(64) UNIQUE NOT NULL,
            display_name VARCHAR(128),
            strategy VARCHAR(32) DEFAULT 'WORKFLOW',
            base_path VARCHAR(255) DEFAULT '/data/web/app',
            description TEXT,
            variables JSON DEFAULT '{}',
            servers JSON DEFAULT '[]',
            environments JSON DEFAULT '{}',
            services JSON DEFAULT '[]',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
    },
    {
        "version": "082_002_migrate_systems_blob_to_table",
        "name": "Migrate systems data from config_kv blob to systems table (Phase 3e SSOT, Python-driven)",
        "table": "config_kv",
        "sql": "SELECT 1",  # placeholder, actual migration is data-driven via Python
    },

    # ── qclaw Element Approval: AiActionApproval extension ──
    {
        "version": "072_001_action_digest",
        "name": "Add action_digest to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "action_digest",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN action_digest VARCHAR(64)",
    },
    {
        "version": "072_002_approval_code_hash",
        "name": "Add approval_code_hash to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "approval_code_hash",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN approval_code_hash VARCHAR(128)",
    },
    {
        "version": "072_003_room_id",
        "name": "Add room_id to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "room_id",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN room_id VARCHAR(255)",
    },
    {
        "version": "072_004_request_event_id",
        "name": "Add request_event_id to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "request_event_id",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN request_event_id VARCHAR(255)",
    },
    {
        "version": "072_005_approval_event_id",
        "name": "Add approval_event_id to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "approval_event_id",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN approval_event_id VARCHAR(255)",
    },
    {
        "version": "072_006_content_sha256",
        "name": "Add content_sha256 to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "content_sha256",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN content_sha256 VARCHAR(64)",
    },
    {
        "version": "072_007_routing_ticket_digest",
        "name": "Add routing_ticket_digest to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "routing_ticket_digest",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN routing_ticket_digest VARCHAR(64)",
    },
    {
        "version": "072_008_routing_config_revision",
        "name": "Add routing_config_revision to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "routing_config_revision",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN routing_config_revision VARCHAR(64)",
    },
    {
        "version": "072_009_expires_at",
        "name": "Add expires_at to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "expires_at",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN expires_at DATETIME",
    },
    {
        "version": "072_010_consumed_at",
        "name": "Add consumed_at to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "consumed_at",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN consumed_at DATETIME",
    },
    {
        "version": "072_011_rejected_by",
        "name": "Add rejected_by to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "rejected_by",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN rejected_by VARCHAR(255)",
    },
    {
        "version": "072_012_rejected_at",
        "name": "Add rejected_at to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "rejected_at",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN rejected_at DATETIME",
    },
    {
        "version": "072_013_package_name",
        "name": "Add package_name to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "package_name",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN package_name VARCHAR(255)",
    },
    {
        "version": "072_014_package_sha256",
        "name": "Add package_sha256 to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "package_sha256",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN package_sha256 VARCHAR(64)",
    },
    {
        "version": "072_015_package_size_bytes",
        "name": "Add package_size_bytes to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "package_size_bytes",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN package_size_bytes BIGINT",
    },
    {
        "version": "072_016_execution_job_id",
        "name": "Add execution_job_id to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "execution_job_id",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN execution_job_id INTEGER",
    },
    {
        "version": "072_017_execution_result",
        "name": "Add execution_result to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "execution_result",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN execution_result JSON",
    },
    {
        "version": "072_018_failure_reason",
        "name": "Add failure_reason to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "failure_reason",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN failure_reason TEXT",
    },
    {
        "version": "072_019_updated_at",
        "name": "Add updated_at to ai_action_approvals",
        "table": "ai_action_approvals",
        "column": "updated_at",
        "sql": "ALTER TABLE ai_action_approvals ADD COLUMN updated_at DATETIME",
    },
    {
        "version": "072_020_approval_indexes",
        "name": "Create indexes for qclaw approval lookups",
        "sql": "CREATE INDEX IF NOT EXISTS ix_ai_approval_action_digest ON ai_action_approvals(action_digest)",
    },
    {
        "version": "072_021_approval_index_expires",
        "name": "Create expires_at index for qclaw approval",
        "sql": "CREATE INDEX IF NOT EXISTS ix_ai_approval_expires_at ON ai_action_approvals(expires_at)",
    },
    {
        "version": "072_022_approval_index_job",
        "name": "Create execution_job_id index for qclaw approval",
        "sql": "CREATE INDEX IF NOT EXISTS ix_ai_approval_job_id ON ai_action_approvals(execution_job_id)",
    },
    {
        "version": "072_023_approval_index_room_event",
        "name": "Create room+event index for qclaw approval",
        "sql": "CREATE INDEX IF NOT EXISTS ix_ai_approval_room_event ON ai_action_approvals(room_id, request_event_id)",
    },
    {
        "version": "073_001_tool_token_bound_rooms",
        "name": "Add bound_room_ids column to tool_tokens for qclaw room binding",
        # Runner uses `table` + `column` to make ALTER TABLE idempotent: if
        # the column already exists (e.g. schema was rebuilt manually), the
        # migration is marked applied without re-running the ALTER. Using
        # TEXT instead of JSON for cross-database portability (MySQL < 5.7.7
        # rejects JSON DEFAULT; SQLite treats JSON as TEXT anyway). The
        # SQLAlchemy model declares `Column(JSON)` which maps to TEXT on
        # SQLite and JSON on MySQL — both read/write the column fine.
        "table": "tool_tokens",
        "column": "bound_room_ids",
        "sql": (
            "ALTER TABLE tool_tokens ADD COLUMN bound_room_ids TEXT DEFAULT '[]' NOT NULL"
        ),
    },
    {
        "version": "073_002_tool_token_approvers",
        "name": "Add approver_matrix_ids column to tool_tokens for qclaw approver whitelist",
        # Same idempotent ALTER pattern as 073_001. Existing tokens default
        # to '[]' (no token-level approver restriction) so pre-existing
        # credentials keep working unchanged.
        "table": "tool_tokens",
        "column": "approver_matrix_ids",
        "sql": (
            "ALTER TABLE tool_tokens ADD COLUMN approver_matrix_ids TEXT DEFAULT '[]' NOT NULL"
        ),
    },
    # NOTE: deliberately no separate index migration for bound_room_ids.
    # Indexing a JSON/TEXT column directly is not portable (MySQL requires
    # a generated column + index). Room-binding lookups are point reads on
    # token_hash (already indexed) followed by an in-Python membership
    # check against bound_room_ids, so a DB index on the column itself
    # would not help the hot path.

    # ── Message Execution Plan: 一条消息 = 一个执行计划 = 一次审批 ──
    {
        "version": "074_001_execution_plans",
        "name": "Create execution_plans table for message-level one-approval plans",
        "table": "execution_plans",
        "sql": """CREATE TABLE IF NOT EXISTS execution_plans (
            id VARCHAR(32) PRIMARY KEY,
            status VARCHAR(32) DEFAULT 'PENDING_APPROVAL',
            plan_digest VARCHAR(64) NOT NULL,
            risk_level VARCHAR(32) DEFAULT 'high',
            room_id VARCHAR(255) NOT NULL,
            request_event_id VARCHAR(255) NOT NULL,
            content_sha256 VARCHAR(64),
            system_name VARCHAR(64),
            service_name VARCHAR(128),
            environment VARCHAR(64),
            targets JSON,
            routing_ticket_digest VARCHAR(64),
            routing_config_revision VARCHAR(64),
            package_name VARCHAR(255),
            package_sha256 VARCHAR(64),
            package_size_bytes BIGINT,
            manifest JSON NOT NULL,
            policy JSON,
            ai_reason TEXT,
            authorized_matrix_users JSON,
            approval_code_hash VARCHAR(128),
            requested_by VARCHAR(128),
            approved_by VARCHAR(255),
            approval_event_id VARCHAR(255),
            expires_at DATETIME,
            consumed_at DATETIME,
            rejected_by VARCHAR(255),
            rejected_at DATETIME,
            approved_at DATETIME,
            execution_job_id VARCHAR(32),
            execution_result JSON,
            failure_reason TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "074_002_execution_plan_steps",
        "name": "Create execution_plan_steps table for ordered plan steps",
        "table": "execution_plan_steps",
        "sql": """CREATE TABLE IF NOT EXISTS execution_plan_steps (
            id VARCHAR(32) PRIMARY KEY,
            plan_id VARCHAR(32) NOT NULL,
            step_key VARCHAR(128) NOT NULL,
            step_order INTEGER DEFAULT 0,
            action_type VARCHAR(64) NOT NULL,
            parameters JSON,
            dependencies JSON,
            status VARCHAR(24) DEFAULT 'PENDING',
            attempt_count INTEGER DEFAULT 0,
            result JSON,
            error_message TEXT,
            started_at DATETIME,
            finished_at DATETIME,
            created_at DATETIME
        )""",
    },
    {
        "version": "074_003_execution_plan_indexes",
        "name": "Create indexes for execution plan lookups",
        "sql": "CREATE INDEX IF NOT EXISTS ix_execution_plan_digest ON execution_plans(plan_digest)",
    },
    {
        "version": "074_004_execution_plan_status_index",
        "name": "Create status index for execution plans",
        "sql": "CREATE INDEX IF NOT EXISTS ix_execution_plan_status ON execution_plans(status)",
    },
    {
        "version": "074_005_execution_plan_room_event_index",
        "name": "Create room+event index for execution plans",
        "sql": "CREATE INDEX IF NOT EXISTS ix_execution_plan_room_event ON execution_plans(room_id, request_event_id)",
    },
    {
        "version": "074_006_execution_plan_step_plan_index",
        "name": "Create plan_id index for execution plan steps",
        "sql": "CREATE INDEX IF NOT EXISTS ix_execution_plan_steps_plan_id ON execution_plan_steps(plan_id)",
    },
    {
        "version": "083_001_system_environments",
        "name": "Create system environments table",
        "table": "system_environments",
        "sql": """CREATE TABLE IF NOT EXISTS system_environments (
            id VARCHAR(32) PRIMARY KEY,
            system_name VARCHAR(64) NOT NULL,
            name VARCHAR(64) NOT NULL,
            display_name VARCHAR(128),
            category VARCHAR(32) DEFAULT 'custom',
            description TEXT,
            base_path VARCHAR(255),
            servers JSON,
            variables JSON,
            service_overrides JSON,
            group_overrides JSON,
            created_at DATETIME,
            updated_at DATETIME,
            CONSTRAINT uq_system_environment_name UNIQUE(system_name, name),
            FOREIGN KEY(system_name) REFERENCES systems(name) ON DELETE CASCADE
        )""",
    },
    {
        "version": "083_002_system_message_routing",
        "name": "Add system message routing",
        "table": "systems",
        "column": "message_routing",
        "sql": "ALTER TABLE systems ADD COLUMN message_routing JSON",
    },
    {
        "version": "083_003_capability_settings",
        "name": "Create capability settings table",
        "table": "capability_settings",
        "sql": """CREATE TABLE IF NOT EXISTS capability_settings (
            id VARCHAR(32) PRIMARY KEY,
            settings JSON NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "083_004_retention_policies",
        "name": "Create retention policies table",
        "table": "retention_policies",
        "sql": """CREATE TABLE IF NOT EXISTS retention_policies (
            policy_type VARCHAR(32) PRIMARY KEY,
            settings JSON NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "083_005_notification_settings",
        "name": "Create notification settings table",
        "table": "notification_settings",
        "sql": """CREATE TABLE IF NOT EXISTS notification_settings (
            id VARCHAR(32) PRIMARY KEY,
            settings JSON NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "083_006_named_domain_settings",
        "name": "Create named domain settings tables",
        "sql": """CREATE TABLE IF NOT EXISTS inspection_profiles (
            name VARCHAR(128) PRIMARY KEY,
            settings JSON NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "083_007_workflow_templates",
        "name": "Create workflow templates table",
        "table": "workflow_templates",
        "sql": """CREATE TABLE IF NOT EXISTS workflow_templates (
            name VARCHAR(128) PRIMARY KEY,
            template JSON NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "083_008_deployment_defaults",
        "name": "Create deployment defaults table",
        "table": "deployment_defaults",
        "sql": """CREATE TABLE IF NOT EXISTS deployment_defaults (
            id VARCHAR(32) PRIMARY KEY,
            settings JSON NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "083_009_global_variables",
        "name": "Create global variables table",
        "table": "global_variables",
        "sql": """CREATE TABLE IF NOT EXISTS global_variables (
            name VARCHAR(128) PRIMARY KEY,
            value JSON,
            created_at DATETIME,
            updated_at DATETIME
        )""",
    },
    {
        "version": "084_002_tool_token_channel_bindings",
        "name": "Add generic channel bindings to tool tokens",
        "table": "tool_tokens",
        "column": "channel_bindings",
        "sql": (
            "ALTER TABLE tool_tokens ADD COLUMN channel_bindings "
            "TEXT DEFAULT '[]' NOT NULL"
        ),
    },
    {
        "version": "084_003_tool_token_approver_identities",
        "name": "Add generic approver identities to tool tokens",
        "table": "tool_tokens",
        "column": "approver_identities",
        "sql": (
            "ALTER TABLE tool_tokens ADD COLUMN approver_identities "
            "TEXT DEFAULT '[]' NOT NULL"
        ),
    },
]


CONFIG_KV_RETIREMENT_MIGRATION: Dict[str, str] = {
    "version": "083_010_retire_config_kv",
    "name": "Migrate domain settings and retire config_kv",
    "sql": "python:retire_config_kv",
}

SERVICE_IDENTITY_MIGRATION: Dict[str, str] = {
    "version": "083_011_service_identity",
    "name": "Enforce unique service identity per system",
    "sql": "CREATE UNIQUE INDEX IF NOT EXISTS uq_services_system_name ON services(system_name, name)",
}

AI_ANALYSIS_RETIREMENT_MIGRATION: Dict[str, str] = {
    "version": "084_001_drop_ai_analysis_tables",
    "name": "Drop retired built-in AI analysis tables",
    "sql": "python:retire_ai_analysis_tables",
}

AI_ANALYSIS_TABLE_DROPS = (
    ("ai_analysis_findings", "DROP TABLE IF EXISTS ai_analysis_findings"),
    ("ai_analysis_runs", "DROP TABLE IF EXISTS ai_analysis_runs"),
)

TOOL_TOKEN_BINDING_BACKFILL_MIGRATION: Dict[str, str] = {
    "version": "084_004_tool_token_binding_backfill",
    "name": "Backfill generic tool token bindings from Matrix aliases",
    "sql": "python:backfill_tool_token_bindings",
}

APPROVAL_CONTEXT_MIGRATION: Dict[str, str] = {
    "version": "084_005_approval_context",
    "name": "Add and backfill channel-neutral approval context",
    "sql": "python:backfill_approval_context",
}

APPROVAL_CONTEXT_COLUMNS = {
    "channel": "VARCHAR(32)",
    "channel_account_id": "VARCHAR(128)",
    "conversation_id": "VARCHAR(255)",
    "request_message_id": "VARCHAR(255)",
    "request_sender_id": "VARCHAR(255)",
    "approval_message_id": "VARCHAR(255)",
    "authorized_identities": "JSON",
    "temporary_grant_id": "VARCHAR(32)",
}


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


def retire_ai_analysis_tables(engine) -> List[str]:
    """Drop the retired analysis tables in dependency order."""
    dropped: List[str] = []
    with engine.begin() as conn:
        _ensure_schema_table(conn)
        if _is_applied(conn, AI_ANALYSIS_RETIREMENT_MIGRATION["version"]):
            return dropped

        names = set(inspect(conn).get_table_names())
        for table_name, drop_sql in AI_ANALYSIS_TABLE_DROPS:
            if table_name in names:
                conn.execute(text(drop_sql))
                dropped.append(table_name)
        _mark_applied(conn, AI_ANALYSIS_RETIREMENT_MIGRATION)

    if dropped:
        logger.info("Retired built-in AI analysis tables: %s", ", ".join(dropped))
    return dropped


def _migration_json_list(value: Any, *, token_id: str, column: str) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid legacy ToolToken JSON in {column} for token {token_id}"
            ) from exc
    else:
        raise RuntimeError(
            f"Invalid legacy ToolToken JSON in {column} for token {token_id}"
        )
    if not isinstance(parsed, list):
        raise RuntimeError(
            f"Invalid legacy ToolToken JSON in {column} for token {token_id}"
        )
    return parsed


def _migration_legacy_strings(value: Any, *, token_id: str, column: str) -> List[str]:
    parsed = _migration_json_list(value, token_id=token_id, column=column)
    result: List[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(
                f"Invalid legacy ToolToken JSON in {column} for token {token_id}"
            )
        normalized = item.strip()
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def backfill_tool_token_bindings(engine) -> bool:
    """Convert legacy Matrix token policy fields exactly once and atomically."""
    migration = TOOL_TOKEN_BINDING_BACKFILL_MIGRATION
    with engine.begin() as conn:
        _ensure_schema_table(conn)
        if _is_applied(conn, migration["version"]):
            return False
        inspector = inspect(conn)
        if not inspector.has_table("tool_tokens"):
            return False
        columns = {item["name"] for item in inspector.get_columns("tool_tokens")}
        required = {
            "channel_bindings",
            "approver_identities",
            "bound_room_ids",
            "approver_matrix_ids",
        }
        if not required.issubset(columns):
            return False

        rows = conn.execute(text(
            "SELECT id, channel_bindings, approver_identities, "
            "bound_room_ids, approver_matrix_ids FROM tool_tokens"
        )).fetchall()
        for row in rows:
            token_id = str(row[0])
            channel_bindings = _migration_json_list(
                row[1], token_id=token_id, column="channel_bindings"
            )
            approver_identities = _migration_json_list(
                row[2], token_id=token_id, column="approver_identities"
            )
            updates: Dict[str, str] = {}
            if not channel_bindings:
                room_ids = _migration_legacy_strings(
                    row[3], token_id=token_id, column="bound_room_ids"
                )
                if room_ids:
                    updates["channel_bindings"] = json.dumps(
                        [
                            {
                                "channel": "matrix",
                                "channel_account_id": "default",
                                "conversation_id": room_id,
                            }
                            for room_id in room_ids
                        ],
                        ensure_ascii=False,
                    )
            if not approver_identities:
                approver_ids = _migration_legacy_strings(
                    row[4], token_id=token_id, column="approver_matrix_ids"
                )
                if approver_ids:
                    updates["approver_identities"] = json.dumps(
                        [
                            {
                                "channel": "matrix",
                                "channel_account_id": "default",
                                "sender_id": sender_id,
                            }
                            for sender_id in approver_ids
                        ],
                        ensure_ascii=False,
                    )
            if "channel_bindings" in updates:
                conn.execute(
                    text(
                        "UPDATE tool_tokens SET channel_bindings=:value WHERE id=:id"
                    ),
                    {"value": updates["channel_bindings"], "id": token_id},
                )
            if "approver_identities" in updates:
                conn.execute(
                    text(
                        "UPDATE tool_tokens SET approver_identities=:value WHERE id=:id"
                    ),
                    {"value": updates["approver_identities"], "id": token_id},
                )
        _mark_applied(conn, migration)
    return True


def _json_list_or_empty(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


_MIGRATION_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _migration_json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _migration_content_sha256(value: Any) -> str:
    raw = str(value or "")
    if _MIGRATION_SHA256_RE.fullmatch(raw):
        return raw.lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _migration_sender_id(value: Any) -> str:
    raw = str(value or "").strip()
    prefix = "matrix:default:"
    return raw[len(prefix):] if raw.startswith(prefix) else (raw or "legacy-requester")


def _migration_context(room_id: Any, message_id: Any, content_sha256: Any, sender: Any) -> dict:
    return {
        "channel": "matrix",
        "channel_account_id": "default",
        "conversation_id": str(room_id or "").strip(),
        "message_id": str(message_id or "").strip(),
        "sender_id": _migration_sender_id(sender),
        "content_sha256": _migration_content_sha256(content_sha256),
    }


def _migration_digest(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _backfill_action_digest(row: Any, context: dict, request_payload: dict) -> str | None:
    if not row.get("action_type") or not row.get("room_id") or not row.get("request_event_id"):
        return None
    return _migration_digest({
        "action_type": row["action_type"],
        "message_context": context,
        "system_name": request_payload.get("system_name") or "",
        "service_name": request_payload.get("service_name"),
        "environment": request_payload.get("environment") or "",
        "targets": sorted(request_payload.get("targets") or []),
        "action_parameters": request_payload.get("action_parameters") or {},
        "routing_config_revision": row.get("routing_config_revision") or "",
    })


def _backfill_plan_manifest(row: Any, context: dict) -> tuple[dict, str] | None:
    manifest = _migration_json_object(row.get("manifest"))
    steps = manifest.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    normalized = {
        "message_context": context,
        "system_name": manifest.get("system_name") or row.get("system_name") or "",
        "service_name": manifest.get("service_name") if "service_name" in manifest else row.get("service_name"),
        "environment": manifest.get("environment") or row.get("environment") or "",
        "targets": sorted(manifest.get("targets") or row.get("targets") or []),
        "steps": steps,
        "policy": manifest.get("policy") or row.get("policy") or {},
        "routing_config_revision": manifest.get("routing_config_revision") or row.get("routing_config_revision") or "",
        "routing_ticket_digest": manifest.get("routing_ticket_digest") or row.get("routing_ticket_digest") or "",
    }
    return normalized, _migration_digest(normalized)


def backfill_approval_context(engine) -> bool:
    """Add generic approval fields and backfill legacy Matrix data once."""
    migration = APPROVAL_CONTEXT_MIGRATION
    changed = False
    with engine.begin() as conn:
        _ensure_schema_table(conn)
        if _is_applied(conn, migration["version"]):
            return False
        inspector = inspect(conn)
        for table in ("ai_action_approvals", "execution_plans"):
            if not inspector.has_table(table):
                continue
            columns = {item["name"] for item in inspector.get_columns(table)}
            for name, sql_type in APPROVAL_CONTEXT_COLUMNS.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                    changed = True

            rows = conn.execute(text(f"SELECT * FROM {table}")).mappings().all()
            for row in rows:
                if row.get("channel") and row.get("conversation_id") and row.get("request_message_id"):
                    continue
                room_id = row.get("room_id")
                request_message_id = row.get("request_event_id")
                if not room_id or not request_message_id:
                    continue
                payload = _json_list_or_empty(row.get("authorized_matrix_users"))
                if not payload:
                    request_payload = row.get("request_payload")
                    if isinstance(request_payload, str):
                        try:
                            request_payload = json.loads(request_payload)
                        except json.JSONDecodeError:
                            request_payload = {}
                    if isinstance(request_payload, dict):
                        payload = _json_list_or_empty(request_payload.get("authorized_matrix_users"))
                identities = [
                    {"channel": "matrix", "channel_account_id": "default", "sender_id": sender}
                    for sender in payload if isinstance(sender, str) and sender.strip()
                ]
                updates = {
                    "channel": "matrix",
                    "channel_account_id": "default",
                    "conversation_id": room_id,
                    "request_message_id": request_message_id,
                    "request_sender_id": _migration_sender_id(row.get("requested_by")),
                    "authorized_identities": json.dumps(identities, ensure_ascii=False),
                }
                request_payload = _migration_json_object(row.get("request_payload"))
                action_digest = _backfill_action_digest(row, _migration_context(
                    room_id,
                    request_message_id,
                    row.get("content_sha256"),
                    row.get("requested_by"),
                ), request_payload)
                if table == "ai_action_approvals" and action_digest:
                    updates["action_digest"] = action_digest
                plan_context = _migration_context(
                    room_id,
                    request_message_id,
                    row.get("content_sha256"),
                    row.get("requested_by"),
                )
                if table == "execution_plans":
                    plan_result = _backfill_plan_manifest(row, plan_context)
                    if plan_result:
                        updates["manifest"], updates["plan_digest"] = plan_result
                if row.get("approval_event_id"):
                    updates["approval_message_id"] = row["approval_event_id"]
                fields = [
                    "channel=:channel",
                    "channel_account_id=:channel_account_id",
                    "conversation_id=:conversation_id",
                    "request_message_id=:request_message_id",
                    "request_sender_id=:request_sender_id",
                    "approval_message_id=:approval_message_id",
                    "authorized_identities=:authorized_identities",
                ]
                params = {
                    "approval_message_id": None,
                    **updates,
                    "id": row["id"],
                }
                if table == "ai_action_approvals":
                    fields.append("action_digest=:action_digest")
                    params["action_digest"] = row.get("action_digest")
                else:
                    fields.extend(["manifest=:manifest", "plan_digest=:plan_digest"])
                    params["manifest"] = row.get("manifest")
                    params["plan_digest"] = row.get("plan_digest")
                conn.execute(
                    text(f"UPDATE {table} SET {', '.join(fields)} WHERE id=:id"),
                    params,
                )
                changed = True
        for table, name in (("ai_action_approvals", "ix_ai_approval_context"), ("execution_plans", "ix_execution_plan_context")):
            if inspector.has_table(table):
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {name} ON {table} "
                    "(channel, channel_account_id, conversation_id, request_message_id)"
                ))
        _mark_applied(conn, migration)
        changed = True
    return changed


def _json_object(value: Any, *, key: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError(f"Legacy config key '{key}' must contain a JSON object")
    return value


def _upsert_singleton(conn, table: str, payload: Dict[str, Any], now: str) -> None:
    conn.execute(text(
        f"INSERT INTO {table}(id, settings, created_at, updated_at) "
        "VALUES('default', :settings, :now, :now) "
        "ON CONFLICT(id) DO UPDATE SET settings=excluded.settings, updated_at=excluded.updated_at"
    ), {"settings": json.dumps(payload, ensure_ascii=False), "now": now})


def _legacy_records(value: Any, *, key: str) -> List[Dict[str, Any]]:
    """Normalize legacy list/dict asset buckets without discarding their keys."""
    if value is None:
        return []
    if isinstance(value, list):
        records = value
    elif isinstance(value, dict):
        records = []
        for record_key, raw_record in value.items():
            if not isinstance(raw_record, dict):
                raise RuntimeError(
                    f"Legacy config key '{key}' item '{record_key}' must contain a JSON object"
                )
            record = dict(raw_record)
            record.setdefault("name", str(record_key))
            record.setdefault("_legacy_key", str(record_key))
            records.append(record)
    else:
        raise RuntimeError(f"Legacy config key '{key}' must contain a list or JSON object")
    if any(not isinstance(record, dict) for record in records):
        raise RuntimeError(f"Legacy config key '{key}' must contain JSON objects")
    return [dict(record) for record in records]


def _migrate_legacy_systems(conn, value: Any, now: str) -> None:
    if value is None:
        return
    systems = _json_object(value, key="systems")
    for system_name, raw_system in systems.items():
        if not isinstance(raw_system, dict):
            raise RuntimeError(f"Legacy system '{system_name}' must contain a JSON object")
        system_name = str(system_name).strip()
        if not system_name:
            raise RuntimeError("Legacy systems contains an empty system name")
        environments = raw_system.get("environments") or {}
        services = raw_system.get("services") or []
        if not isinstance(environments, dict):
            raise RuntimeError(f"System '{system_name}' environments must be a JSON object")
        if not isinstance(services, list):
            raise RuntimeError(f"System '{system_name}' services must be a JSON list")

        existing = conn.execute(text(
            "SELECT environments, services, message_routing FROM systems WHERE name=:name"
        ), {"name": system_name}).fetchone()
        if existing is None:
            conn.execute(text(
                "INSERT INTO systems("
                "id, name, display_name, strategy, base_path, description, variables, servers, "
                "message_routing, environments, services, created_at, updated_at"
                ") VALUES("
                ":id, :name, :display_name, :strategy, :base_path, :description, :variables, "
                ":servers, :message_routing, :environments, :services, :now, :now)"
            ), {
                "id": uuid.uuid4().hex,
                "name": system_name,
                "display_name": raw_system.get("display_name") or system_name,
                "strategy": raw_system.get("strategy") or "WORKFLOW",
                "base_path": raw_system.get("base_path") or "/data/web/app",
                "description": raw_system.get("description"),
                "variables": json.dumps(raw_system.get("variables") or {}, ensure_ascii=False),
                "servers": json.dumps(raw_system.get("servers") or [], ensure_ascii=False),
                "message_routing": json.dumps(raw_system.get("message_routing") or {}, ensure_ascii=False),
                "environments": json.dumps(environments, ensure_ascii=False),
                "services": json.dumps(services, ensure_ascii=False),
                "now": now,
            })
        else:
            stored_environments = json.loads(existing[0] or "{}")
            stored_services = json.loads(existing[1] or "[]")
            stored_routing = json.loads(existing[2] or "{}")
            if not isinstance(stored_environments, dict) or not isinstance(stored_services, list):
                raise RuntimeError(f"System '{system_name}' contains invalid legacy inventory")
            merged_environments = dict(environments)
            merged_environments.update(stored_environments)
            stored_names = {
                str(item.get("name") or "").strip()
                for item in stored_services
                if isinstance(item, dict)
            }
            merged_services = list(stored_services)
            merged_services.extend(
                item for item in services
                if isinstance(item, dict)
                and str(item.get("name") or "").strip() not in stored_names
            )
            conn.execute(text(
                "UPDATE systems SET environments=:environments, services=:services, "
                "message_routing=:message_routing, updated_at=:now WHERE name=:name"
            ), {
                "name": system_name,
                "environments": json.dumps(merged_environments, ensure_ascii=False),
                "services": json.dumps(merged_services, ensure_ascii=False),
                "message_routing": json.dumps(
                    stored_routing or raw_system.get("message_routing") or {},
                    ensure_ascii=False,
                ),
                "now": now,
            })

        groups = raw_system.get("groups") or raw_system.get("regions") or {}
        if groups and not isinstance(groups, dict):
            raise RuntimeError(f"System '{system_name}' groups must be a JSON object")
        for group_code, raw_group in groups.items():
            if not isinstance(raw_group, dict):
                raise RuntimeError(
                    f"System '{system_name}' group '{group_code}' must contain a JSON object"
                )
            group_name = f"{system_name}-{group_code}"
            server_names = raw_group.get("servers") or []
            if not server_names and raw_group.get("server"):
                server_names = [raw_group["server"]]
            metadata = {
                key: value for key, value in raw_group.items()
                if key not in {"name", "display_name", "description", "servers", "tags"}
            }
            conn.execute(text(
                "INSERT INTO server_groups("
                "id, name, display_name, description, server_names, tags, metadata_json, created_at, updated_at"
                ") VALUES("
                ":id, :name, :display_name, :description, :server_names, :tags, :metadata, :now, :now"
                ") ON CONFLICT(name) DO NOTHING"
            ), {
                "id": uuid.uuid4().hex,
                "name": group_name,
                "display_name": raw_group.get("display_name") or str(group_code),
                "description": raw_group.get("description"),
                "server_names": json.dumps(server_names, ensure_ascii=False),
                "tags": json.dumps(raw_group.get("tags") or [], ensure_ascii=False),
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "now": now,
            })


def _migrate_legacy_servers(conn, value: Any, now: str) -> None:
    from app.core.secret_store import encrypt_secret

    core_fields = {
        "id", "name", "host", "port", "user", "username", "key", "key_file",
        "key_content", "password", "jump_host", "status", "created_at", "updated_at",
    }
    for server in _legacy_records(value, key="servers"):
        name = str(server.get("name") or "").strip()
        host = str(server.get("host") or "").strip()
        if not name or not host:
            raise RuntimeError("Legacy server requires non-empty name and host")
        jump_host = server.get("jump_host")
        if isinstance(jump_host, dict):
            jump_host_name = jump_host.get("name") or jump_host.get("host")
        else:
            jump_host_name = jump_host
        metadata = {key: value for key, value in server.items() if key not in core_fields}
        if isinstance(jump_host, dict):
            metadata["inline_jump_host"] = jump_host
        conn.execute(text(
            "INSERT INTO servers("
            "id, name, host, port, user, key, key_content, password, jump_host, status, "
            "metadata_json, created_at, updated_at"
            ") VALUES("
            ":id, :name, :host, :port, :user, :key, :key_content, :password, :jump_host, "
            ":status, :metadata, :now, :now) ON CONFLICT(name) DO NOTHING"
        ), {
            "id": str(server.get("id") or uuid.uuid4().hex),
            "name": name,
            "host": host,
            "port": int(server.get("port") or 22),
            "user": server.get("user") or server.get("username") or "root",
            "key": server.get("key") or server.get("key_file") or "~/.ssh/id_rsa",
            "key_content": encrypt_secret(server.get("key_content")),
            "password": encrypt_secret(server.get("password")),
            "jump_host": jump_host_name,
            "status": server.get("status") or "online",
            "metadata": json.dumps(metadata, ensure_ascii=False),
            "now": now,
        })


def _migrate_legacy_jump_hosts(conn, value: Any, now: str) -> None:
    from app.core.secret_store import encrypt_secret

    for jump_host in _legacy_records(value, key="jump_hosts"):
        name = str(jump_host.get("name") or "").strip()
        host = str(jump_host.get("host") or "").strip()
        if not name or not host:
            raise RuntimeError("Legacy jump host requires non-empty name and host")
        conn.execute(text(
            "INSERT INTO jump_hosts("
            "id, name, host, port, user, key, key_content, password, status, description, tags, "
            "created_at, updated_at"
            ") VALUES("
            ":id, :name, :host, :port, :user, :key, :key_content, :password, :status, "
            ":description, :tags, :now, :now) ON CONFLICT(name) DO NOTHING"
        ), {
            "id": str(jump_host.get("id") or uuid.uuid4().hex),
            "name": name,
            "host": host,
            "port": int(jump_host.get("port") or 22),
            "user": jump_host.get("user") or jump_host.get("username") or "root",
            "key": jump_host.get("key") or jump_host.get("key_file") or "~/.ssh/id_rsa",
            "key_content": encrypt_secret(jump_host.get("key_content")),
            "password": encrypt_secret(jump_host.get("password")),
            "status": jump_host.get("status") or "online",
            "description": jump_host.get("description"),
            "tags": json.dumps(jump_host.get("tags") or [], ensure_ascii=False),
            "now": now,
        })


def _migrate_legacy_server_groups(conn, value: Any, now: str) -> None:
    for group in _legacy_records(value, key="server_groups"):
        name = str(group.get("name") or "").strip()
        if not name:
            raise RuntimeError("Legacy server group requires a non-empty name")
        server_names = group.get("server_names") or group.get("servers") or []
        if not server_names and group.get("server"):
            server_names = [group["server"]]
        metadata = {
            key: value for key, value in group.items()
            if key not in {
                "id", "name", "display_name", "description", "server_names", "servers",
                "tags", "created_at", "updated_at", "_legacy_key",
            }
        }
        conn.execute(text(
            "INSERT INTO server_groups("
            "id, name, display_name, description, server_names, tags, metadata_json, created_at, updated_at"
            ") VALUES("
            ":id, :name, :display_name, :description, :server_names, :tags, :metadata, :now, :now"
            ") ON CONFLICT(name) DO NOTHING"
        ), {
            "id": str(group.get("id") or uuid.uuid4().hex),
            "name": name,
            "display_name": group.get("display_name") or name,
            "description": group.get("description"),
            "server_names": json.dumps(server_names, ensure_ascii=False),
            "tags": json.dumps(group.get("tags") or [], ensure_ascii=False),
            "metadata": json.dumps(metadata, ensure_ascii=False),
            "now": now,
        })


def _migrate_legacy_pipelines(conn, value: Any, now: str) -> None:
    for pipeline in _legacy_records(value, key="pipelines"):
        pipeline_id = str(pipeline.get("id") or pipeline.get("_legacy_key") or "").strip()
        pipeline_name = str(pipeline.get("name") or pipeline_id).strip()
        system_name = str(pipeline.get("system_name") or pipeline.get("system") or "").strip()
        if not pipeline_id or not pipeline_name or not system_name:
            raise RuntimeError("Legacy pipeline requires id, name, and system_name")
        if conn.execute(text(
            "SELECT 1 FROM systems WHERE name=:name"
        ), {"name": system_name}).fetchone() is None:
            raise RuntimeError(
                f"Legacy pipeline '{pipeline_id}' references unknown system '{system_name}'"
            )
        conn.execute(text(
            "INSERT INTO pipelines("
            "id, name, system_name, description, strategy, created_at, updated_at"
            ") VALUES("
            ":id, :name, :system_name, :description, :strategy, :now, :now"
            ") ON CONFLICT(id) DO NOTHING"
        ), {
            "id": pipeline_id,
            "name": pipeline_name,
            "system_name": system_name,
            "description": pipeline.get("description"),
            "strategy": pipeline.get("strategy") or "DIRECT",
            "now": now,
        })
        steps = pipeline.get("steps") or []
        if not isinstance(steps, list):
            raise RuntimeError(f"Legacy pipeline '{pipeline_id}' steps must be a JSON list")
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise RuntimeError(f"Legacy pipeline '{pipeline_id}' steps must contain JSON objects")
            step_name = str(step.get("name") or f"step-{index + 1}").strip()
            sort_order = int(step.get("sort_order", index))
            exists = conn.execute(text(
                "SELECT 1 FROM pipeline_steps WHERE pipeline_id=:pipeline_id "
                "AND sort_order=:sort_order AND name=:name"
            ), {
                "pipeline_id": pipeline_id,
                "sort_order": sort_order,
                "name": step_name,
            }).fetchone()
            if exists:
                continue
            conn.execute(text(
                "INSERT INTO pipeline_steps(id, pipeline_id, name, step_type, config, sort_order) "
                "VALUES(:id, :pipeline_id, :name, :step_type, :config, :sort_order)"
            ), {
                "id": str(step.get("id") or uuid.uuid4().hex),
                "pipeline_id": pipeline_id,
                "name": step_name,
                "step_type": step.get("step_type") or step.get("type") or "command",
                "config": json.dumps(step.get("config") or {}, ensure_ascii=False),
                "sort_order": sort_order,
            })


def _migrate_legacy_assets(conn, config: Dict[str, Any], now: str) -> None:
    _migrate_legacy_systems(conn, config.get("systems"), now)
    _migrate_legacy_servers(conn, config.get("servers"), now)
    _migrate_legacy_jump_hosts(conn, config.get("jump_hosts"), now)
    _migrate_legacy_server_groups(conn, config.get("server_groups"), now)
    _migrate_legacy_pipelines(conn, config.get("pipelines"), now)


def _expand_legacy_settings(config: Dict[str, Any]) -> None:
    """Promote the pre-SSOT settings bucket while preserving newer top-level values."""
    if "settings" not in config:
        return
    settings = _json_object(config.pop("settings"), key="settings")
    supported = {
        "capability_server",
        "release_retention",
        "package_retention",
        "runtime_retention",
        "notification_settings",
        "inspection_profiles",
        "workflow_templates",
        "deploy_defaults",
        "global_variables",
    }
    unknown = sorted(set(settings) - supported)
    if unknown:
        raise RuntimeError("Unmapped legacy settings keys: " + ", ".join(unknown))
    for key, value in settings.items():
        config.setdefault(key, value)


def _migrate_system_inventory(conn, now: str) -> None:
    system_names = {row[0] for row in conn.execute(text("SELECT name FROM systems")).fetchall()}
    aliases = {"crypto": "crypto-trader"}
    for alias, canonical in aliases.items():
        if alias not in system_names and canonical in system_names:
            conn.execute(text(
                "UPDATE services SET system_name=:canonical WHERE system_name=:alias"
            ), {"canonical": canonical, "alias": alias})

    # Prefer the canonical short service names, which contain the newer
    # environment mappings and explicit update commands in existing installs.
    if "crypto-trader" in system_names:
        conn.execute(text(
            "DELETE FROM services AS prefixed "
            "WHERE prefixed.system_name='crypto-trader' "
            "AND prefixed.name LIKE 'crypto-%' "
            "AND EXISTS ("
            "  SELECT 1 FROM services AS canonical "
            "  WHERE canonical.system_name='crypto-trader' "
            "  AND canonical.name=substr(prefixed.name, 8)"
            ")"
        ))

    orphan_names = sorted({
        row[0]
        for row in conn.execute(text(
            "SELECT DISTINCT service.system_name "
            "FROM services AS service "
            "LEFT JOIN systems AS system ON system.name=service.system_name "
            "WHERE system.name IS NULL"
        )).fetchall()
    })
    if orphan_names:
        raise RuntimeError(
            "Services reference unknown systems: " + ", ".join(orphan_names)
        )

    system_rows = conn.execute(text(
        "SELECT name, environments, services FROM systems ORDER BY name"
    )).fetchall()
    for system_name, raw_environments, raw_services in system_rows:
        environments = json.loads(raw_environments or "{}")
        if not isinstance(environments, dict):
            raise RuntimeError(f"System '{system_name}' environments must be a JSON object")
        for environment_name, raw_environment in environments.items():
            environment = raw_environment if isinstance(raw_environment, dict) else {}
            conn.execute(text(
                "INSERT INTO system_environments("
                "id, system_name, name, display_name, category, description, base_path, "
                "servers, variables, service_overrides, group_overrides, created_at, updated_at"
                ") VALUES("
                ":id, :system_name, :name, :display_name, :category, :description, :base_path, "
                ":servers, :variables, :service_overrides, :group_overrides, :now, :now"
                ") ON CONFLICT(system_name, name) DO NOTHING"
            ), {
                "id": uuid.uuid4().hex,
                "system_name": system_name,
                "name": str(environment_name),
                "display_name": environment.get("display_name") or str(environment_name),
                "category": environment.get("category") or "custom",
                "description": environment.get("description"),
                "base_path": environment.get("base_path") or environment.get("deploy_path"),
                "servers": json.dumps(environment.get("servers") or [], ensure_ascii=False),
                "variables": json.dumps(environment.get("variables") or {}, ensure_ascii=False),
                "service_overrides": json.dumps(environment.get("service_overrides") or {}, ensure_ascii=False),
                "group_overrides": json.dumps(environment.get("group_overrides") or {}, ensure_ascii=False),
                "now": now,
            })

        services = json.loads(raw_services or "[]")
        if not isinstance(services, list):
            raise RuntimeError(f"System '{system_name}' services must be a JSON list")
        for raw_service in services:
            if not isinstance(raw_service, dict):
                continue
            service_name = str(raw_service.get("name") or "").strip()
            if not service_name:
                continue
            semantic_name = (
                service_name[7:]
                if system_name == "crypto-trader" and service_name.startswith("crypto-")
                else service_name
            )
            exists = conn.execute(text(
                "SELECT 1 FROM services "
                "WHERE system_name=:system_name AND name IN (:name, :semantic_name) LIMIT 1"
            ), {
                "system_name": system_name,
                "name": service_name,
                "semantic_name": semantic_name,
            }).fetchone()
            if exists:
                continue
            conn.execute(text(
                "INSERT INTO services("
                "id, name, display_name, system_name, repo, build_cmd, start_cmd, template, "
                "pipeline_id, template_variables, servers, created_at, updated_at"
                ") VALUES("
                ":id, :name, :display_name, :system_name, :repo, :build_cmd, :start_cmd, "
                ":template, :pipeline_id, :template_variables, :servers, :now, :now)"
            ), {
                "id": uuid.uuid4().hex,
                "name": service_name,
                "display_name": raw_service.get("display_name") or service_name,
                "system_name": system_name,
                "repo": raw_service.get("repo"),
                "build_cmd": raw_service.get("build_cmd"),
                "start_cmd": raw_service.get("start_cmd"),
                "template": raw_service.get("template"),
                "pipeline_id": raw_service.get("pipeline_id"),
                "template_variables": json.dumps(raw_service.get("template_variables") or {}, ensure_ascii=False),
                "servers": json.dumps(raw_service.get("servers") or [], ensure_ascii=False),
                "now": now,
            })

    conn.execute(text("UPDATE systems SET environments='{}', services='[]'"))


def _migrate_domain_settings(conn, config: Dict[str, Any], now: str) -> None:
    singleton_keys = {
        "capability_server": "capability_settings",
        "notification_settings": "notification_settings",
        "deploy_defaults": "deployment_defaults",
    }
    for key, table in singleton_keys.items():
        if key in config:
            _upsert_singleton(conn, table, _json_object(config[key], key=key), now)

    retention_keys = {
        "release_retention": "release",
        "package_retention": "package",
        "runtime_retention": "runtime",
    }
    for key, policy_type in retention_keys.items():
        if key not in config:
            continue
        payload = _json_object(config[key], key=key)
        conn.execute(text(
            "INSERT INTO retention_policies(policy_type, settings, created_at, updated_at) "
            "VALUES(:policy_type, :settings, :now, :now) "
            "ON CONFLICT(policy_type) DO UPDATE SET settings=excluded.settings, updated_at=excluded.updated_at"
        ), {
            "policy_type": policy_type,
            "settings": json.dumps(payload, ensure_ascii=False),
            "now": now,
        })

    profiles = config.get("inspection_profiles", {})
    if isinstance(profiles, dict):
        profiles = profiles.get("items", [])
    if profiles and not isinstance(profiles, list):
        raise RuntimeError("Legacy config key 'inspection_profiles' must contain a list")
    for profile in profiles or []:
        if not isinstance(profile, dict):
            continue
        name = str(profile.get("id") or profile.get("name") or "").strip()
        if not name:
            continue
        conn.execute(text(
            "INSERT INTO inspection_profiles(name, settings, created_at, updated_at) "
            "VALUES(:name, :settings, :now, :now) "
            "ON CONFLICT(name) DO UPDATE SET settings=excluded.settings, updated_at=excluded.updated_at"
        ), {"name": name, "settings": json.dumps(profile, ensure_ascii=False), "now": now})

    templates = _json_object(config.get("workflow_templates"), key="workflow_templates")
    for name, template in templates.items():
        if not isinstance(template, dict):
            raise RuntimeError(f"Workflow template '{name}' must contain a JSON object")
        conn.execute(text(
            "INSERT INTO workflow_templates(name, template, created_at, updated_at) "
            "VALUES(:name, :template, :now, :now) "
            "ON CONFLICT(name) DO UPDATE SET template=excluded.template, updated_at=excluded.updated_at"
        ), {"name": str(name), "template": json.dumps(template, ensure_ascii=False), "now": now})

    variables = _json_object(config.get("global_variables"), key="global_variables")
    for name, value in variables.items():
        conn.execute(text(
            "INSERT INTO global_variables(name, value, created_at, updated_at) "
            "VALUES(:name, :value, :now, :now) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
        ), {"name": str(name), "value": json.dumps(value, ensure_ascii=False), "now": now})


def _create_service_identity_index(conn) -> None:
    duplicates = conn.execute(text(
        "SELECT system_name, name, COUNT(*) AS total FROM services "
        "GROUP BY system_name, name HAVING COUNT(*) > 1"
    )).fetchall()
    if duplicates:
        labels = [f"{row[0]}/{row[1]} ({row[2]})" for row in duplicates]
        raise RuntimeError("Duplicate service identities remain: " + ", ".join(labels))
    conn.execute(text(SERVICE_IDENTITY_MIGRATION["sql"]))


def ensure_service_identity_index(engine) -> bool:
    with engine.begin() as conn:
        _ensure_schema_table(conn)
        if _is_applied(conn, SERVICE_IDENTITY_MIGRATION["version"]):
            return False
        if "services" not in inspect(conn).get_table_names():
            return False
        _create_service_identity_index(conn)
        _mark_applied(conn, SERVICE_IDENTITY_MIGRATION)
        return True


def retire_config_kv(engine) -> bool:
    """Copy recognized legacy data to domain tables and drop config_kv atomically."""
    with engine.begin() as conn:
        _ensure_schema_table(conn)
        if _is_applied(conn, CONFIG_KV_RETIREMENT_MIGRATION["version"]):
            return False
        if "config_kv" not in inspect(conn).get_table_names():
            _mark_applied(conn, CONFIG_KV_RETIREMENT_MIGRATION)
            return False

        config: Dict[str, Any] = {}
        for key, raw_value in conn.execute(text("SELECT key, value FROM config_kv")).fetchall():
            try:
                config[key] = json.loads(raw_value)
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Invalid JSON in legacy config key '{key}'") from exc

        _expand_legacy_settings(config)

        recognized = {
            "__seeded",
            "systems",
            "servers",
            "jump_hosts",
            "server_groups",
            "pipelines",
            "capability_server",
            "release_retention",
            "package_retention",
            "runtime_retention",
            "notification_settings",
            "inspection_profiles",
            "workflow_templates",
            "deploy_defaults",
            "global_variables",
        }
        unknown = sorted(set(config) - recognized)
        if unknown:
            raise RuntimeError("Unmapped legacy config keys: " + ", ".join(unknown))

        now = _now_iso()
        _migrate_legacy_assets(conn, config, now)
        _migrate_system_inventory(conn, now)
        _migrate_domain_settings(conn, config, now)
        conn.execute(text("DROP TABLE config_kv"))
        _create_service_identity_index(conn)
        _mark_applied(conn, CONFIG_KV_RETIREMENT_MIGRATION)
        _mark_applied(conn, SERVICE_IDENTITY_MIGRATION)
        logger.info("Migrated domain settings and retired config_kv")
        return True


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
    if retire_config_kv(engine):
        applied.append(CONFIG_KV_RETIREMENT_MIGRATION["version"])
    if ensure_service_identity_index(engine):
        applied.append(SERVICE_IDENTITY_MIGRATION["version"])
    if retire_ai_analysis_tables(engine):
        applied.append(AI_ANALYSIS_RETIREMENT_MIGRATION["version"])
    if backfill_tool_token_bindings(engine):
        applied.append(TOOL_TOKEN_BINDING_BACKFILL_MIGRATION["version"])
    if backfill_approval_context(engine):
        applied.append(APPROVAL_CONTEXT_MIGRATION["version"])
    return applied

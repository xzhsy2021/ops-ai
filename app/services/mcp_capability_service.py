from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List


SAFE_TOOL_NAMES = os.getenv("OPS_MCP_SAFE_TOOL_NAMES", "1").lower() not in {"0", "false", "no", "off"}
ASCII_DESCRIPTIONS = os.getenv("OPS_MCP_ASCII_DESCRIPTIONS", "1").lower() not in {"0", "false", "no", "off"}
# Keep the Chinese "中文: kw1/kw2" keyword-routing tail while still stripping other
# non-ASCII (emoji, accented latin). This preserves the strongest intent->tool map
# for Chinese LLM agents. Set OPS_MCP_CHINESE_KEYWORDS=0 to fully strip non-ASCII.
CHINESE_KEYWORDS = os.getenv("OPS_MCP_CHINESE_KEYWORDS", "1").lower() not in {"0", "false", "no", "off"}
MCP_ALIAS_TO_TOOL: Dict[str, str] = {}

# HTTP MCP tools/list is expensive (register + describe every tool + schema).
# Cache the resolved payload briefly so high-frequency discovery calls don't
# recompute, while the short TTL guarantees newly registered tools surface
# quickly (no permanent staleness). Set OPS_MCP_TOOLS_LIST_TTL=0 to disable.
def _ttl_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


TOOLS_LIST_TTL = _ttl_env("OPS_MCP_TOOLS_LIST_TTL", 5.0)
_tools_list_cache: Dict[str, Any] = {}
_tools_list_cache_lock = threading.Lock()


def _tools_list_cache_entry(ts: float, etag: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"ts": ts, "etag": etag, "payload": payload}


def _tools_payload_etag(payload: Dict[str, Any]) -> str:
    try:
        return hashlib.sha256(
            json.dumps(payload["tools"], sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    except Exception:
        return ""


# MCP tools/list 默认单页容量：ai_full 全量工具约 120 个，设 200 确保一次性全部
# 返回，避免按字母排序落在分页边界上的工具（如 ops.routing.resolve_message_target）被截断。
MCP_TOOLS_LIST_DEFAULT_LIMIT = 200


def _tools_list_profile(mcp_tools_list_params: Dict[str, Any]) -> tuple:
    return (
        str(mcp_tools_list_params.get("profile") or "ai_full"),
        str(mcp_tools_list_params.get("category") or ""),
        int(mcp_tools_list_params.get("limit") or MCP_TOOLS_LIST_DEFAULT_LIMIT),
        int(mcp_tools_list_params.get("cursor") or 0),
        hashlib.sha256(
            json.dumps(list(MCP_TOOL_DESCRIPTION_OVERRIDES.items()), sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )


# Single source of truth for MCP tool descriptions.
# Used by HTTP MCP (POST /api/v2/mcp tools/list, /api/v2/tools).
MCP_TOOL_DESCRIPTION_OVERRIDES: Dict[str, str] = {
    'ops.inspection.preview_servers_batch': 'Preview server batch inspection targets before execution. Resolves server_ids, groups, group, or all_servers; returns skipped targets, batch_size/concurrency plan, and the exact Chinese confirmation phrase.',
    'ops.inspection.run_servers_batch': "Run audited server inspections for multiple servers after preview confirmation. High risk; requires the exact confirm_text returned by ops.inspection.preview_servers_batch, for example confirm phrase '确认巡检 <fingerprint>'.",
    'ops.inspection.generate_report': 'Generate one inspection report from a single inspection run id.',
    'ops.inspection.generate_report_for_runs': 'Generate one merged inspection report from multiple run ids. Use after batch or grouped inspections.',
    'ops.list_server_groups': 'List server groups with total, inspectable, and online counts. Use before grouped inspections.',
    'ops.describe_capabilities': "Describe the OPS tools, resources, prompts, risk levels, and schemas available to the current token. Use this FIRST when you need to discover what OPS can do or when a user asks 'what capabilities do you have'. 中文: 能力发现/查看可用工具/有哪些功能.",
    'ops.get_tool_risk_policy': 'View OPS MCP tool risk policies, confirmation rules, and task-queue suggestions. Read-only. 中文: 查看工具风险策略/安全规则.',
    'ops.get_system_status': 'Read system health, database, runtime directory, backup, disk, and deploy worker summaries. 中文: 系统健康检查/运行状态/查看系统状况.',
    'ops.run_diagnostics': 'Run local OPS diagnostics for frontend build, static assets, runtime dirs, database, worker, MCP, and resource usage. 中文: 运行诊断/诊断排查/健康检测.',
    'ops.get_build_info': 'Read frontend and backend build metadata, including dist freshness and backend startup time. 中文: 查看构建信息/前端构建状态/版本信息.',
    'ops.get_recent_errors': 'Read recent local error and warning log summaries. Read-only. 中文: 查看最近错误/日志错误/报错信息.',
    'ops.export_diagnostics_report': 'Generate a JSON diagnostics report with overview, health, build info, recent errors, MCP self-check, and recommendations. 中文: 导出诊断报告/生成诊断报告.',
    'ops.list_backups': 'List local OPS SQLite database backups with file names, sizes, timestamps, types, and confirmation phrases. 中文: 查看备份列表/有哪些备份.',
    'ops.verify_backup': 'Verify one local SQLite database backup using sqlite quick_check and optional SHA256 checksum. Read-only. 中文: 校验备份/验证备份完整性.',
    'ops.create_backup': 'Create and verify a local OPS SQLite database backup. Medium risk; write permission and confirmation policy required. 中文: 创建备份/备份数据库.',
    'ops.restore_backup': 'Restore the OPS SQLite database from a backup. Critical risk; requires confirm_text RESTORE <file> and creates a safety backup first. 中文: 恢复备份/还原数据库.',
    'ops.delete_backup': 'Delete one local database backup. High risk; requires confirm_text DELETE <file>. 中文: 删除备份/清理备份.',
    'ops.list_jobs': 'List unified operation jobs for high-risk MCP/tool executions. Read-only. 中文: 查看任务列表/查看后台任务.',
    'ops.get_job_status': 'Get one unified operation job by id. Read-only. 中文: 查看任务状态/任务进度.',
    'ops.list_systems': "List OPS systems that can be queried or used for release planning. Use when user asks 'what systems are available' or 'list systems'. 中文: 查看系统列表/有哪些系统.",
    'ops.list_services': "List services under a system, including display names and release metadata. Use when user asks 'what services under system X' or 'list services'. 中文: 查看服务列表/系统下有哪些服务.",
    'ops.get_service_config': 'Get release configuration for a service, including directories, scripts, environments, and server mappings. 中文: 查看服务配置/服务详情.',
    'ops.list_environments': 'List release environments or custom scenes for a system. 中文: 查看环境列表/有哪些发布环境.',
    'ops.list_servers': "List server assets that match filters such as group, name, or keyword. Use when user asks 'list servers' or 'what servers are configured'. 中文: 查看服务器列表/已配置的服务器/有哪些服务器.",
    'ops.list_packages': 'List local release packages managed by OPS File Center. 中文: 查看发布包列表/有哪些发布包.',
    'ops.get_package_checksum': 'Get package checksum and metadata for a local release package. 中文: 查看发布包校验/包验证.',
    'ops.upload_package': 'Upload a local deploy package into OPS File Center. HTTP clients pass content_base64, or local_path when the package is on the OPS backend host. 中文: 上传发布包/上传部署包.',
    'ops.upload_file': 'Upload a validated package from the controlled OPS upload/staging directory to an allowed server path over SFTP. High risk; requires human approval. 中文: 通过 SFTP 上传部署包到目标服务器.',
    'ops.inspect_local_package': 'Inspect a local deploy package path before uploading it into OPS File Center. local_path must be readable by the OPS backend host; remote clients can use the OPS page upload. 中文: 检查本地发布包/查看本地包信息.',
    'ops.prepare_release_from_local_package': 'Inspect and upload a local package, then create a release plan and run precheck. This never executes deployment. 中文: 从本地包准备发布/打包发布.',
    'ops.get_package_retention_preview': 'Preview package cleanup candidates without deleting files. Protects running, failed, rollback, and latest successful packages. 中文: 预览包清理/查看可清理的发布包.',
    'ops.cleanup_packages': 'Clean local deploy packages according to retention policy. High risk; requires write permission and confirmation. Defaults to dry run. 中文: 清理发布包/清理旧包.',
    'ops.protect_package': 'Manually protect or unprotect a deploy package from cleanup. 中文: 保护发布包/标记保护.',
    'ops.matrix.scan_media_events': 'Scan a Matrix room for media events (m.file/m.image/m.video/m.audio) from a sender within a time window, without downloading. Returns event ids, filenames, mxc urls and encryption status. 中文: 查看Matrix房间媒体/扫描房间附件/房间发了什么包.',
    'ops.matrix.pull_attachment': 'Pull the latest media attachment from a Matrix room (by sender, within N minutes, optional filename match) into the OPS File Center: download mxc media, compute SHA256, write DeployPackage metadata and return package_name for later deployment. E2EE encrypted rooms are decrypted automatically when Matrix E2EE is configured (matrix-nio[e2e] + persistent crypto store); failures return an explicit reason. 中文: 从Matrix拉附件/拉取Matrix部署包/Matrix附件入库.',
    'ops.matrix.deploy_from_matrix': 'Full Matrix deploy chain: pull the latest media attachment from a Matrix room, save it into the File Center (SHA256), then queue the existing deploy flow (checksum/artifact store/server selection/release/audit). Production requires admin or allow_prod token plus confirm_text. 中文: Matrix发布/从Matrix发版/拉取Matrix附件并发布.',
    'ops.select_latest_package': 'Select the latest package that matches a system or service hint. 中文: 选择最新发布包/查找最新包.',
    'ops.create_deploy_plan': "Create a release plan and confirmation data. This does not execute deployment. Use when user asks 'create a release plan' or 'prepare deployment'. 中文: 创建发布计划/制定发布方案.",
    'ops.get_deploy_confirmation': 'Get the confirmation details for an existing release plan. 中文: 查看发布确认/发布确认信息.',
    'ops.run_precheck': 'Run release precheck for an existing plan and return blockers, warnings, and report rows. 中文: 发布预检/预检查/发布前检查.',
    'ops.execute_deploy_plan': 'Execute an already confirmed release plan through the OPS worker. High risk; requires confirmation and policy approval. 中文: 执行发布/执行部署.',
    'ops.cancel_deployment': 'Request cancellation for a running or queued deployment. 中文: 取消发布/取消部署.',
    'ops.get_deployment_status': 'Get deployment status by deployment id. 中文: 查看发布状态/发布进度.',
    'ops.get_deployment_tasks': 'Get server tasks and step tasks for a deployment. 中文: 查看发布任务/发布步骤.',
    'ops.get_deployment_logs': 'Get deployment logs, usually the most recent lines. 中文: 查看发布日志/部署日志.',
    'ops.get_deployment_report': 'Get the structured deployment report including tasks, package distribution, and log summary. 中文: 查看发布报告/部署报告.',
    'ops.list_deployments': "List recent deployments with filters. Use when user asks 'list recent deployments' or 'show deployment history'. 中文: 查看发布历史/最近发布列表.",
    'ops.list_deploy_plans': 'List created deploy plans with status filters. Read-only. 中文: 查看发布计划列表/发布方案列表.',
    'ops.get_deploy_plan': 'Get one deploy plan by id including steps, environments, and server targets. 中文: 查看发布计划详情.',
    'ops.generate_release_runbook': 'Generate a release runbook guide for a deploy plan. Read-only. 中文: 生成发布操作手册/发布指南.',
    'ops.get_rollback_readiness': 'Check rollback readiness for a deployment including backup existence and rollback plan availability. 中文: 检查回滚就绪/回滚准备状态.',
    'ops.create_rollback_plan': 'Create a rollback plan from an existing deployment. This does not execute rollback. 中文: 创建回滚计划/制定回滚方案.',
    'ops.execute_rollback_plan': 'Execute an approved rollback plan through the OPS worker. High risk; requires confirmation and policy approval. 中文: 执行回滚/回滚操作.',
    'ops.create_config_change_plan': 'Create a configuration change plan with diff. This does not apply changes. 中文: 创建配置变更计划/配置变更方案.',
    'ops.apply_config_change_plan': 'Apply an approved configuration change plan. Requires confirmation and policy approval. 中文: 应用配置变更/修改配置.',
    'ops.check_disk': '【路径 B 补充】Check disk usage on allowed servers through OPS server tools. 中文: 检查磁盘/磁盘使用率/磁盘空间. — 单点探针，不替代系统巡检。',
    'ops.check_process': '【路径 B 补充】Check whether a service process is running on allowed servers. 中文: 检查进程/服务是否运行/进程状态. — 单点探针，不替代系统巡检。',
    'ops.list_service_directory': '【路径 B 补充】List files in an allowed service directory. Does not modify files. 中文: 查看服务目录/列出文件.',
    'ops.tail_service_log': '【路径 B 补充】Read recent lines from an allowed service log path. 中文: 查看服务日志/读取日志尾行.',
    'ops.run_health_check': '【路径 B 补充】Run configured health checks for a service or deployment target. 中文: 运行健康检查/服务健康检测.',
    'ops.restart_service': 'Restart a service on a specified server. Supports Docker Compose / PM2 / process_keyword. High risk; requires human approval. 中文: 重启服务/重启应用.',
    'ops.stop_service': 'Stop a service on a specified server. Supports Docker Compose / PM2 / process_keyword. High risk; requires human approval. 中文: 停止服务/关闭应用.',
    'ops.start_service': 'Start a service on a specified server. Supports Docker Compose / PM2 / process_keyword. High risk; requires human approval. 中文: 启动服务/开启应用.',
    'ops.update_service_runtime': 'Update service runtime: pull latest image and recreate container (Docker Compose) or reload (PM2). Optional compose_service targets a single Docker Compose service. High risk; requires human approval. 中文: 更新服务/拉取镜像/重新部署容器/滚动更新.',
    'ops.list_audit_logs': 'List audit logs with filters. 中文: 查看审计日志/操作审计/操作记录.',
    'ops.list_tool_calls': 'List historical OPS tool calls and their audit status. 中文: 查看工具调用历史/AI操作记录.',
    'ops.list_operation_chains': 'List recent OPS/MCP/AI operation chains for audit replay. 中文: 查看操作链路/操作回放列表.',
    'ops.get_operation_chain': 'Read one OPS/MCP/AI operation chain by chain_id with all events and audit evidence. 中文: 查看操作链路详情/操作回放.',
    'ops.list_reports': 'List reports in the OPS Report Center including diagnostics, deployment, backup, and operation chain reports. 中文: 查看报告列表/报告中心.',
    'ops.get_report': 'Get report metadata and download URL by report_id. 中文: 查看报告详情/下载报告.',
    'ops.get_report_summary': 'View report center summary including counts by type and total size. 中文: 查看报告概览/报告统计.',
    'ops.list_report_types': 'List available report types that can be generated. 中文: 查看可生成报告类型.',
    'ops.generate_report': 'Generate a report artifact (diagnostics, operation chain, operation-chain index, deployment, or inspection). Low risk. 中文: 生成报告/创建报告.',
    'ops.connection_status': 'Check whether the local MCP bridge can reach the OPS API using the configured base URL and token. 中文: 连接状态/MCP连通性检查.',
    'ops.db.list_tables': "List OPS local database tables OR tables from a configured remote database connection. Use this when the user asks 'what tables are in database X' or 'list tables'. Do NOT write standalone Python scripts to list tables - use this OPS tool instead. 中文: 查看数据库表列表/有哪些表/列出表.",
    'ops.db.describe_table': "Describe database table columns, types, and sensitive-field flags. Use when the user asks 'what columns does table X have' or 'show table structure'. Do NOT write standalone Python scripts to query information_schema - use this OPS tool instead. 中文: 查看表结构/表字段/有哪些字段.",
    'ops.db.query_readonly': "Execute a guarded SELECT/WITH read-only database query with row limits and sensitive-field masking. Use when the user asks 'query data from table X' or 'select records'. Supports PostgreSQL and MySQL via configured connections. Do NOT write standalone Python database scripts - use this OPS tool instead. 中文: 查询数据/SELECT查询/查数据.",
    'ops.db.export_query_result': "Export a guarded read-only query result to CSV, JSON, XLSX, Markdown, or SQL query artifact and save to the report center. Use when the user asks 'export table X as CSV' or 'download data as Excel'. Do NOT write standalone Python export scripts - use this OPS tool instead. 中文: 导出数据为CSV/导出Excel/下载查询结果.",
    'ops.db.preview_execute_sql': 'Preview a controlled UPDATE/DELETE/INSERT operation before execution. Supports fast/standard/full preview levels. Shows estimated affected rows, risk level, and WHERE summary. 中文: 预检SQL操作/预检查写入/预览DML.',
    'ops.db.execute_sql': 'Execute a controlled UPDATE/DELETE/INSERT operation after preview and confirmation. Returns summary, next_actions and verification_sql. 中文: 执行SQL写入/执行DML.',
    'ops.db.preview_dml': 'Recommended two-step DML first phase. Preview INSERT/UPDATE/DELETE with risk, target table, estimated rows and verification_sql. This is the preferred approach for write operations. 中文: DML预检/数据库写入预检.',
    'ops.db.execute_dml': 'Recommended two-step DML second phase. Execute after user confirmation and return execution_id, summary and next_actions. 中文: 执行DML/确认写入.',
    'ops.db.list_exports': 'List generated database query export artifacts in the report center. 中文: 查看导出历史/导出文件列表.',
    'ops.db.get_export': 'Get metadata and download URL for one database query export artifact. 中文: 查看导出详情/下载导出文件.',
    'ops.db.list_dml_history': 'List controlled DML execution history with operator, connection, target table, affected rows and status. 中文: 查看DML执行历史/写入操作记录.',
    'ops.db.get_dml_execution': 'Get one DML execution detail by execution_id including before-sample rows and audit fields. 中文: 查看DML执行详情.',
    'ops.inspection.list_runs': "【路径 A 主】List inspection run records for servers or projects. Use when user asks 'recent inspections' or 'inspection history'. 中文: 查看巡检记录/巡检历史.",
    'ops.inspection.get_run': '【路径 A 主】Get detailed inspection run results by run_id. 中文: 查看巡检详情/巡检结果.',
    'ops.inspection.list_issues': "【路径 A 主】List inspection issues/risks with filters. Use when user asks 'what issues were found' or 'list risks'. 中文: 查看巡检问题/风险列表.",
    'ops.inspection.get_issue': '【路径 A 主】Get one inspection issue detail by issue_id. 中文: 查看巡检问题详情/风险详情.',
    'ops.inspection.summarize_run': '【路径 A 主】Summarize an inspection run in FIRE structure (Findings, Impact, Recommendations, Evidence). 中文: 巡检摘要/巡检总结.',
    'ops.inspection.run_server': '【路径 A 主】PRIMARY PATH A. Run an inspection on a single server. High risk; requires confirmation. 中文: 执行服务器巡检/巡检服务器. — 用户提到「巡检」时默认选我。',
    'ops.inspection.run_project': '【路径 A 主】PRIMARY PATH A. Run an inspection for a project. High risk; requires confirmation. 中文: 执行项目巡检/巡检项目.',
    'ops.inspection.run_combined': '【路径 A 主】PRIMARY PATH A. Run a combined project inspection. High risk; requires confirmation. 中文: 执行综合巡检/项目综合巡检.',
    'ops.inspection.list_item_configs': '【路径 A 主】List inspection item configurations. 中文: 查看巡检项配置/巡检配置列表.',
    'ops.inspection.get_item_config': '【路径 A 主】Get one inspection item configuration detail. 中文: 查看巡检项配置详情.',
    'ops.inspection.update_item_config': '【路径 A 主】Update an inspection item configuration. Medium risk. 中文: 更新巡检项配置/修改巡检配置.',
    'ops.inspection.toggle_item_config': '【路径 A 主】Enable or disable an inspection item. Medium risk; requires confirmation. 中文: 启用禁用巡检项/切换巡检项.',
    'ops.inspection.update_item_rules': '【路径 A 主】Update rules associated with an inspection item. Medium risk. 中文: 更新巡检规则/修改巡检规则.',
    'ops.inspection.get_run_raw_output': '【路径 A 主】Get raw output data from an inspection run. 中文: 查看巡检原始输出/巡检原始数据.',
    'ops.inspection.delete_runs': '【路径 A 主】Delete inspection run records. High risk; requires confirmation. 中文: 删除巡检记录/清理巡检历史.',
    'ops.inspection.delete_issue': '【路径 A 主】Delete an inspection issue. Medium risk; requires confirmation. 中文: 删除巡检问题/清理巡检问题.',
    'ops.risk.list': "List open risk issues with filters. Use when user asks 'what risks exist' or 'list open risks'. 中文: 查看风险列表/风险问题.",
    'ops.risk.get': 'Get one risk issue detail by risk_id. 中文: 查看风险详情/风险问题详情.',
    'ops.risk.triage': 'Triage open risks by severity and urgency. Read-only. 中文: 风险分流/风险优先级排序.',
    'ops.risk.generate_fix_plan': 'Generate a remediation plan for a risk issue. Read-only. 中文: 生成风险整改计划/修复方案.',
    'ops.risk.update_status': 'Update a risk issue status. High risk; requires human approval. 中文: 更新风险状态/修改风险状态.',
    'ops.risk.verify': 'Verify a risk issue resolution. High risk; requires human approval. 中文: 验证风险/复查风险.',
    'ops.risk.ignore': 'Ignore a risk issue. High risk; requires human approval. 中文: 忽略风险/标记忽略.',
    'ops.log.tail': 'Read the tail of OPS application log files. Medium risk. 中文: 查看日志尾行/读取日志.',
    'ops.log.search': 'Search OPS application logs for keywords. Medium risk. 中文: 搜索日志/日志关键词搜索.',
    'ops.log.summarize_errors': 'Summarize error patterns in OPS application logs. Medium risk. 中文: 汇总错误日志/错误统计.',
    'ops.log.find_patterns': 'Find security and error patterns in OPS application logs. Medium risk. 中文: 识别日志模式/日志模式分析.',
    'ops.log.get_recent_exceptions': 'Get recent exception summaries from OPS application logs. Medium risk. 中文: 查看近期异常/异常摘要.',
    'ops.list_connections': 'List database connections with optional keyword and environment filters. 中文: 查看数据库连接列表/连接列表.',
    'ops.get_connection': 'Get database connection detail by ID. Password fields are masked. 中文: 查看数据库连接详情/连接详情.',
    'ops.create_connection': 'Create a new database connection. High risk; requires human approval. 中文: 创建数据库连接/新增连接.',
    'ops.update_connection': 'Update a database connection configuration. High risk; requires human approval. 中文: 更新数据库连接/修改连接.',
    'ops.delete_connection': 'Delete a database connection. High risk; requires human approval and confirm_text. 中文: 删除数据库连接/移除连接.',
    'ops.test_connection': 'Test database connection connectivity. Medium risk. 中文: 测试数据库连接/连接测试.',
    'ops.get_server': 'Get single server detail by name. 中文: 查看服务器详情/服务器配置.',
    'ops.create_server': 'Create a new server configuration. High risk; requires human approval. 中文: 创建服务器/新增服务器.',
    'ops.update_server': 'Update server configuration. High risk; requires human approval. 中文: 更新服务器/修改服务器配置.',
    'ops.delete_server': 'Delete a server configuration. Critical risk; requires human approval and confirm_text. 中文: 删除服务器/移除服务器.',
    'ops.batch_update_servers': 'Batch update server attributes. High risk; requires human approval. 中文: 批量更新服务器/批量修改.',
    'ops.create_system': 'Create a new system/project. High risk; requires human approval. 中文: 创建系统/新增项目.',
    'ops.update_system': 'Update system/project configuration. High risk; requires human approval. 中文: 更新系统/修改项目.',
    'ops.delete_system': 'Delete a system/project. Critical risk; requires human approval and confirm_text. 中文: 删除系统/移除项目.',
    'ops.create_service': 'Create a new service under a system. High risk; requires human approval. 中文: 创建服务/新增服务.',
    'ops.update_service': 'Update service configuration. High risk; requires human approval. 中文: 更新服务/修改服务.',
    'ops.delete_service': 'Delete a service. Critical risk; requires human approval and confirm_text. 中文: 删除服务/移除服务.',
    'ops.create_environment': 'Create a new environment under a system. High risk; requires human approval. 中文: 创建环境/新增环境.',
    'ops.update_environment': 'Update environment configuration. High risk; requires human approval. 中文: 更新环境/修改环境.',
    'ops.delete_environment': 'Delete an environment. Critical risk; requires human approval and confirm_text. 中文: 删除环境/移除环境.',
    'ops.create_server_group': 'Create a server group. Medium risk; requires confirmation. 中文: 创建服务器分组/新增分组.',
    'ops.assign_server_group': 'Assign servers to a group. Medium risk; requires confirmation. 中文: 分配服务器到分组/分组分配.',
    'ops.rename_server_group': 'Rename a server group. Medium risk; requires confirmation. 中文: 重命名服务器分组/分组重命名.',
    'ops.delete_server_group': 'Delete a server group. High risk; requires human approval and confirm_text. 中文: 删除服务器分组/移除分组.',
    'ops.list_ssh_keys': 'List registered SSH keys. 中文: 查看SSH密钥列表/密钥列表.',
    'ops.get_ssh_key': 'Get SSH key metadata (no private key content). 中文: 查看SSH密钥详情/密钥信息.',
    'ops.create_ssh_key': 'Register a new SSH key. High risk; requires human approval. 中文: 创建SSH密钥/新增密钥.',
    'ops.update_ssh_key': 'Update an SSH key. High risk; requires human approval. 中文: 更新SSH密钥/修改密钥.',
    'ops.delete_ssh_key': 'Delete an SSH key. High risk; requires human approval and confirm_text. 中文: 删除SSH密钥/移除密钥.',
    'ops.list_pipelines': 'List deployment pipeline configurations. 中文: 查看Pipeline列表/流程列表.',
    'ops.get_pipeline': 'Get single pipeline detail by ID. 中文: 查看Pipeline详情/流程详情.',
    'ops.create_pipeline': 'Create a new deployment pipeline. High risk; requires human approval. 中文: 创建Pipeline/新增流程.',
    'ops.update_pipeline': 'Update pipeline configuration. High risk; requires human approval. 中文: 更新Pipeline/修改流程.',
    'ops.delete_pipeline': 'Delete a pipeline. Critical risk; requires human approval and confirm_text. 中文: 删除Pipeline/移除流程.',

    # ── Approval Workflow ──
    'ops.approval.prepare_plan': "Create an immutable execution plan for an entire Element message flow and return a one-time descriptive confirm phrase, e.g. 批准发布+健康检查 crypto-trader@test A3F9C2D1 (verbs = plan actions, target = system@env, suffix = content fingerprint). After the approver approves once, all steps run in order automatically. Step types include MATRIX_PULL (pull latest Matrix room attachment into File Center; its result package_name auto-feeds a dependent RELEASE step) plus SERVICE_CONTROL / FILE_UPLOAD / HEALTH_CHECK / RELEASE / ROLLBACK / DML / PACKAGE_CLEANUP — so pull+release needs only ONE approval. Same plan content (plan_digest) reuses the pending plan idempotently. 中文: 准备执行计划/发起审批/部署审批/计划审批.",
    'ops.approval.execute_plan': "Consume the one-time confirm phrase returned by prepare_plan, mark the execution plan ready, and run its frozen steps in order. The security gate is the phrase itself (one-time, 15-min expiry, bound to room+event, content fingerprint prevents cross-plan reuse), so only ops:read is needed. 中文: 执行审批计划/消费审批码/确认执行.",
    'ops.approval.reject_plan': "Officially mark a pending (PENDING_APPROVAL) execution plan as REJECTED to stop it from running. Use when a work order was declined/abandoned and the system state must be synced. Idempotent: a plan already in a terminal state is returned as-is with no side effect; only PENDING_APPROVAL can be rejected. Requires ops:write; the caller identity is recorded as rejected_by for audit, and an optional reason is persisted to failure_reason. 中文: 拒绝计划/拒绝工单/驳回审批/取消计划/标记已拒绝.",
    'ops.approval.prepare_file_upload': 'Create a one-time Element approval for uploading a validated package to one or more configured servers. Checksum, size, targets, and remote path are frozen; execution performs the SFTP upload only. 中文: 准备文件上传审批/上传部署包审批/文件上传审批.',
    'ops.approval.prepare_service_control': 'Create an immutable approval ticket for service control (restart/stop/start/update) and return a one-time descriptive confirm phrase, e.g. 批准服务控制 crypto-trader@test A3F9C2D1. An approver replies with that phrase in the bound room. 中文: 准备服务控制审批/重启审批/停止审批/服务操作审批.',
    'ops.approval.prepare_exec': 'Create an immutable approval ticket for ad-hoc remote command execution (package install / service bring-up / one-off troubleshooting) and return a one-time confirm phrase plus a ready-to-forward reply_template. The command, targets, and timeout are frozen with a command SHA256; the approval card shows the verbatim command. Execution runs the exact command per target, serially, with no auto-retry and no rollback. Default allowlist mode: the command must match an admin-managed whitelist template; destructive commands (recursive delete, mkfs, shutdown, passwd, ...) are always refused in production. Requires an admin to enable allow_exec_remote_tool first. 中文: 准备命令执行审批/远程命令审批/ad-hoc执行审批/服务器装包起服务/批量执行命令审批.',
    'ops.approval.temporary_access': 'Create/confirm/revoke/query bounded time-limited self-approval for test-environment changes. Identity is derived only from message_context.sender_id; only the configured original approver can confirm/revoke; query returns the scoped grant list including beneficiary actor keys. 中文: 临时授权/临时审批/测试环境自批授权/查询临时授权/授权受益人.',
    'ops.approval.list': 'List approval tickets with optional status and action-type filters. Read-only. 中文: 查看审批列表/审批记录/待审批/审批历史.',
    'ops.approval.execute': 'Consume the one-time confirm phrase returned by prepare, mark the ticket EXECUTING, and trigger the real action (deploy/rollback/DML/package cleanup). Security is the phrase itself, so only ops:read is required. 中文: 执行已审批操作/消费审批码/确认操作.',

    # ── Deployment Summary ──
    'ops.deploy.aggregate_status': 'Get the deployment center aggregated health status: recent deployments, active jobs, running deployments, rollback count, worker liveness, and precheck toggle. Filterable by system/environment. Read-only. 中文: 发布聚合状态/发布中心健康/最近发布/发布概况.',

    # ── Remote Server Ad-hoc Ops ──
    'ops.exec_remote': 'Execute an arbitrary shell command on a target server through OPS SSH infrastructure (incl. jump hosts). Use for docker ops, file lookup, process management, config checks. High risk; requires human approval. 中文: 执行远程命令/远程执行/运行命令/跑脚本/远程shell.',
    'ops.file_read': 'Read a remote server file content. Use for configs, logs, scripts. Read-only, no confirmation phrase. 中文: 读文件/查看配置/远程文件/读取远程文件/cat文件.',
    'ops.file_write': 'Write content to a remote server file, auto-backing up the original if present. For editing configs or deploy scripts. High risk; requires human approval. 中文: 写文件/修改配置/写入远程文件/编辑远程文件/更新配置.',

    # ── Help ──
    'ops.help.query': 'Return the OPS capabilities, approver policy, and active temporary approvals available to the current token/channel/system context. Supports topic filter and include_all. 中文: 查看帮助/上下文帮助/我能做什么/有哪些能力.',

    # ── Reusable Inspection Profiles ──
    'ops.inspection.profile.list': 'List reusable server inspection profiles for daily/weekly/monthly or grouped flows. 中文: 查询巡检方案/巡检方案列表/内置巡检方案.',
    'ops.inspection.profile.preview': 'Resolve the real target servers, inspection items, and confirmation phrase of an inspection profile. Call before executing. 中文: 预览巡检方案/查看巡检方案目标/巡检方案确认短语.',
    'ops.inspection.profile.retry_issues': 'Re-run inspection on servers with unclosed issues under an inspection profile. Requires the confirmation phrase from profile.preview. High risk. 中文: 重试未闭环巡检问题/重跑问题服务器巡检.',
    'ops.inspection.profile.run': 'Run batched server inspection using a saved profile. Requires the RUN INSPECTION confirmation phrase from profile.preview. High risk. 中文: 执行巡检方案/批量巡检/按方案巡检.',

    # ── QClaw Message Routing ──
    'ops.routing.resolve_message_target': 'Deterministically route a QClaw channel message to an OPS system/service and issue a routing ticket bound to the full message context. content_sha256 is auto-computed from the message text (UTF-8) when omitted. 中文: 解析消息路由/消息路由目标/路由解析.',
}


def to_mcp_tool_name(name: str) -> str:
    if not SAFE_TOOL_NAMES:
        return name
    alias = re.sub(r"[^A-Za-z0-9_-]", "_", str(name or "")).strip("_")
    if not alias:
        alias = "ops_tool"
    MCP_ALIAS_TO_TOOL[alias] = name
    return alias


def from_mcp_tool_name(name: str) -> str:
    if name in MCP_ALIAS_TO_TOOL:
        return MCP_ALIAS_TO_TOOL[name]
    if SAFE_TOOL_NAMES and isinstance(name, str):
        try:
            from app.services.tool_registry import register_builtin_tools, registry

            register_builtin_tools()
            for tool_name in sorted(getattr(registry, "_tools", {}).keys()):
                if to_mcp_tool_name(tool_name) == name:
                    return tool_name
        except Exception:
            pass
        if name.startswith("ops_"):
            return "ops." + name[len("ops_"):]
    return name


def ascii_only(value: Any, fallback: str = "") -> str:
    text = str(value or fallback or "")
    if not ASCII_DESCRIPTIONS:
        return text
    text = re.sub(r"[^\x20-\x7E]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


# Characters we strip on MCP exposure: anything that is neither ASCII-printable,
# nor Han (CJK 0x3400-0x9fff), nor CJK punctuation. Emojis/accented latin go away;
# English main body and the Chinese keyword tail survive.
_MCP_STRIP = r"[^\x20-\x7E\u3400-\u4dbf\u4e00-\u9fff\u3000\u3001\u3002\uff01\uff08\uff09\uff0c\uff1a\uff1b\uff1f\uff5e\u201c\u201d\u2018\u2019]+"


def mcp_safe_description(value: Any, fallback: str = "") -> str:
    """Clean a string for MCP exposure while preserving Chinese keyword routing.

    - ASCII_DESCRIPTIONS=0        -> return value unchanged.
    - CHINESE_KEYWORDS=0          -> legacy behavior: drop ALL non-ASCII.
    - default                     -> keep ASCII "main body" + Han + CJK punct so
      the '中文: kw1/kw2' intent->tool tail survives; drop emojis/accented latin.
    """
    text = str(value or fallback or "")
    if not ASCII_DESCRIPTIONS:
        return text
    if not CHINESE_KEYWORDS:
        return ascii_only(text, fallback)
    text = re.sub(_MCP_STRIP, " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def english_tool_description(
    tool: Dict[str, Any],
    original_name: str,
    alias: str,
    description_overrides: Dict[str, str] | None = None,
) -> str:
    if not ASCII_DESCRIPTIONS:
        return str(tool.get("description") or "")
    if description_overrides and original_name in description_overrides:
        description = description_overrides[original_name]
        if alias != original_name:
            description += f" Original HTTP tool: {original_name}."
        return mcp_safe_description(description, fallback=f"OPS tool {alias}")
    annotations = tool.get("annotations") or {}
    category = tool.get("category") or annotations.get("x_ops_category") or annotations.get("category") or "ops"
    risk = tool.get("risk") or annotations.get("x_ops_risk") or annotations.get("risk") or "low"
    description = f"OPS capability tool {original_name}. Category: {category}. Risk: {risk}."
    if alias != original_name:
        description += f" Original HTTP tool: {original_name}."
    return ascii_only(description, fallback=f"OPS tool {alias}")


def mcp_tool_payload(tool: Dict[str, Any], description_overrides: Dict[str, str] | None = None) -> Dict[str, Any]:
    payload = dict(tool or {})
    original = str(payload.get("name") or "")
    alias = to_mcp_tool_name(original)
    payload["name"] = alias
    payload["description"] = english_tool_description(payload, original, alias, description_overrides)
    annotations = dict(payload.get("annotations") or {})
    annotations["title"] = mcp_safe_description(annotations.get("title"), fallback=alias)
    if alias != original:
        annotations["ops.originalToolName"] = original

    # Inject ai_level + approval_hint so AI agents can discover authorization
    # requirements at discovery time, not just at call time.
    existing_annotations = payload.get("annotations") or {}
    ai_level = existing_annotations.get("x_ops_ai_level") or payload.get("ai_level") or ""
    approval_hint = existing_annotations.get("x_ops_approval_hint") or payload.get("approval_hint") or ""
    if ai_level:
        annotations["x_ops_ai_level"] = ai_level
    if approval_hint:
        annotations["x_ops_approval_hint"] = mcp_safe_description(approval_hint)

    if ASCII_DESCRIPTIONS:
        for key, value in list(annotations.items()):
            if isinstance(value, str):
                annotations[key] = mcp_safe_description(value, fallback=alias if key == "title" else "")
    payload["annotations"] = annotations
    return payload


def mcp_tools_list(db, ctx, params: Dict[str, Any] | None = None, description_overrides: Dict[str, str] | None = None) -> Dict[str, Any]:
    from app.services.tool_registry import register_builtin_tools, registry

    params = params or {}
    cache_key = _tools_list_profile(params)
    if description_overrides:
        cache_key = cache_key + (hashlib.sha256(
            json.dumps(list(description_overrides.items()), sort_keys=True).encode("utf-8")
        ).hexdigest(),)
    cached = None
    if TOOLS_LIST_TTL > 0:
        with _tools_list_cache_lock:
            entry = _tools_list_cache.get(cache_key)
            if entry and (time.monotonic() - entry["ts"]) < TOOLS_LIST_TTL:
                cached = entry["payload"]
    if cached is not None:
        return copy.deepcopy(cached)

    register_builtin_tools()
    try:
        limit = int(params.get("limit") or MCP_TOOLS_LIST_DEFAULT_LIMIT)
    except Exception:
        limit = MCP_TOOLS_LIST_DEFAULT_LIMIT
    try:
        cursor = int(params.get("cursor") or 0)
    except Exception:
        cursor = 0
    listed = registry.list_tools(
        db,
        ctx,
        category=str(params.get("category") or ""),
        profile=str(params.get("profile") or "ai_full"),
        include_disabled=False,
        include_schema=True,
        output_format="mcp",
        limit=max(1, min(limit, 200)),
        cursor=max(0, cursor),
    )
    effective_overrides = dict(MCP_TOOL_DESCRIPTION_OVERRIDES)
    if description_overrides:
        effective_overrides.update(description_overrides)
    tools = [mcp_tool_payload(t, effective_overrides) for t in (listed.get("tools") or [])]
    result: Dict[str, Any] = {"tools": tools}
    next_cursor = (listed.get("pagination") or {}).get("next_cursor")
    if next_cursor is not None:
        result["nextCursor"] = str(next_cursor)
    if TOOLS_LIST_TTL > 0:
        with _tools_list_cache_lock:
            _tools_list_cache[cache_key] = _tools_list_cache_entry(
                time.monotonic(), _tools_payload_etag(result), copy.deepcopy(result)
            )
    return result


def mcp_call_tool(db, ctx, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    params = params or {}
    tool_name = from_mcp_tool_name(str(params.get("name") or params.get("tool") or ""))
    args = params.get("arguments") or {}
    if not tool_name:
        raise ValueError("tools/call requires params.name")
    result = registry.call(db, tool_name, args, ctx)
    is_error = not bool(result.get("ok", True)) if isinstance(result, dict) else False
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, default=str, indent=2),
            }
        ],
        "isError": is_error,
    }


def mcp_call_tool_stream(db, ctx, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    params = params or {}
    tool_name = from_mcp_tool_name(str(params.get("name") or params.get("tool") or ""))
    args = params.get("arguments") or {}
    if not tool_name:
        raise ValueError("tools/call.stream requires params.name")

    events: List[Dict[str, Any]] = []

    def _on_chunk(chunk: Any):
        events.append(chunk if isinstance(chunk, dict) else {"event": "chunk", "data": chunk})

    tool = registry.get(tool_name)
    result = registry.call(
        db,
        tool_name,
        args,
        ctx,
        stream_callback=_on_chunk if getattr(tool, "streamable", False) else None,
    )
    data = {"stream": events, "result": result}
    is_error = not bool(result.get("ok", True)) if isinstance(result, dict) else False
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False, default=str, indent=2),
            }
        ],
        "isError": is_error,
    }


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mcp_resource_items() -> List[Dict[str, str]]:
    return [
        {"uri": "ops://capabilities", "name": "Capability Manifest", "description": "OPS capability manifest", "mimeType": "application/json"},
        {"uri": "ops://systems", "name": "OPS Systems", "description": "OPS system list", "mimeType": "application/json"},
        {"uri": "ops://deployments/recent", "name": "Recent Deployments", "description": "Recent deployment history", "mimeType": "application/json"},
        {"uri": "ops://tools", "name": "Tool Catalog", "description": "OPS tool catalog", "mimeType": "application/json"},
        {"uri": "ops://operation-chains", "name": "Recent Operation Chains", "description": "Read-only audit replay index for OPS/MCP/AI actions", "mimeType": "application/json"},
        {"uri": "ops://reports", "name": "Report Center", "description": "Generated OPS diagnostic/release/audit reports", "mimeType": "application/json"},
        {"uri": "ops://db/exports", "name": "Database Exports", "description": "Read-only database query export artifacts", "mimeType": "application/json"},
        {"uri": "ops://servers", "name": "Servers", "description": "Servers summary", "mimeType": "application/json"},
        {"uri": "ops://projects", "name": "Projects", "description": "Project/system summary", "mimeType": "application/json"},
        {"uri": "ops://status/overview", "name": "Status Overview", "description": "Status overview", "mimeType": "application/json"},
        {"uri": "ops://risks/open", "name": "Open Risks", "description": "Open risks", "mimeType": "application/json"},
        {"uri": "ops://inspection/recent", "name": "Recent Inspection Runs", "description": "Recent inspection runs", "mimeType": "application/json"},
        {"uri": "ops://diagnosis/recent", "name": "Recent Diagnosis Runs", "description": "Recent diagnosis runs", "mimeType": "application/json"},
        {"uri": "ops://reports/recent", "name": "Recent Reports", "description": "Recent reports", "mimeType": "application/json"},
        {"uri": "ops://backups/status", "name": "Backup Status", "description": "Backup status", "mimeType": "application/json"},
        {"uri": "ops://deployments/failed", "name": "Failed Deployments", "description": "Recent failed deployments", "mimeType": "application/json"},
        {"uri": "ops://tool-risk-policy", "name": "Tool Risk Policy", "description": "Tool risk policy", "mimeType": "application/json"},
    ]


def mcp_resources_list() -> Dict[str, Any]:
    return {"resources": mcp_resource_items()}


def mcp_resource_read(db, ctx, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from app.services.risk_policy import risk_policy_manifest
    from app.services.tool_policy import get_capability_settings
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    uri = (params or {}).get("uri") or ""
    generated_at = _utcnow().isoformat()
    if uri == "ops://capabilities":
        data = registry.describe_capabilities(db, ctx, include_schema=True, include_disabled=False, profile="ai_full")
    elif uri == "ops://systems":
        data = registry.call(db, "ops.list_systems", {}, ctx)["result"]
    elif uri == "ops://deployments/recent":
        data = registry.call(db, "ops.list_deployments", {"limit": 20}, ctx)["result"]
    elif uri == "ops://tools":
        data = registry.list_tools(db, ctx, include_schema=True, profile="ai_full").get("tools", [])
    elif uri == "ops://operation-chains":
        from app.services.audit_chain import list_operation_chains
        data = list_operation_chains(db, limit=50)
    elif uri == "ops://reports":
        from app.services.report_center import list_reports, report_summary
        data = {"summary": report_summary(db), "reports": list_reports(db, limit=50)}
    elif uri == "ops://db/exports":
        from app.services.db_query_export import DbQueryExportService
        data = DbQueryExportService(db).list_exports(limit=50)
    elif uri == "ops://servers":
        data = registry.call(db, "ops.list_servers", {"limit": 50}, ctx).get("result")
    elif uri == "ops://projects":
        data = registry.call(db, "ops.list_systems", {}, ctx).get("result")
    elif uri == "ops://status/overview":
        data = registry.call(db, "ops.get_system_status", {}, ctx).get("result")
    elif uri == "ops://risks/open":
        data = registry.call(db, "ops.risk.list", {"status": "OPEN", "limit": 50}, ctx).get("result")
    elif uri == "ops://inspection/recent":
        data = registry.call(db, "ops.inspection.list_runs", {"limit": 20}, ctx).get("result")
    elif uri == "ops://diagnosis/recent":
        try:
            data = registry.call(db, "ops.run_diagnostics", {"mode": "summary"}, ctx).get("result")
        except Exception as exc:
            data = {"summary": f"Diagnostics summary unavailable: {exc}", "items": []}
    elif uri == "ops://reports/recent":
        data = registry.call(db, "ops.list_reports", {"limit": 20}, ctx).get("result")
    elif uri == "ops://backups/status":
        data = registry.call(db, "ops.list_backups", {"limit": 20}, ctx).get("result")
    elif uri == "ops://deployments/failed":
        data = registry.call(db, "ops.list_deployments", {"limit": 50, "status": "failed"}, ctx).get("result")
    elif uri == "ops://tool-risk-policy":
        data = {"settings": get_capability_settings(db), "risk_policy": risk_policy_manifest()}
    else:
        raise ValueError("Resource not found")
    wrapped = {"generated_at": generated_at, "source": "ops-platform", "uri": uri, "data": data}
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(wrapped, ensure_ascii=False, default=str, indent=2),
            }
        ]
    }


def mcp_prompt_items() -> List[Dict[str, Any]]:
    return [
        {"name": "ops_release_plan", "description": "Create a safe OPS release plan.", "arguments": [{"name": "request", "description": "Natural language release request", "required": True}]},
        {"name": "ops_operation_replay", "description": "Replay an OPS/MCP/AI operation chain from audit evidence.", "arguments": [{"name": "chain_id", "description": "tool:/job:/plan:/deployment:/audit id", "required": True}]},
        {"name": "ops_report_brief", "description": "Summarize a generated OPS report artifact.", "arguments": [{"name": "report_id", "description": "Report artifact id", "required": True}]},
        {"name": "ops_db_export_request", "description": "Plan a safe database workflow: query, export, or maintain tables.", "arguments": [{"name": "request", "description": "Natural language query/export request", "required": True}]},
        {"name": "ops_inspection_workflow", "description": "Run OPS server inspection from natural language, including all-server grouped batch inspection.", "arguments": [{"name": "request", "description": "Natural language inspection request", "required": True}]},
        {"name": "ops_server_management", "description": "Manage OPS server assets.", "arguments": [{"name": "request", "description": "Natural language server management request", "required": True}]},
        {"name": "ops_backup_workflow", "description": "Safe OPS database backup workflow.", "arguments": [{"name": "request", "description": "Natural language backup request", "required": True}]},
    ]


def mcp_prompts_list() -> Dict[str, Any]:
    return {"prompts": mcp_prompt_items()}


def mcp_prompt_get(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    params = params or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    if name == "ops_release_plan":
        text = (
            "CRITICAL RULE: Always use OPS tools for OPS-related tasks. Never write standalone scripts instead. "
            "Use ops.list_systems, ops.list_services, ops.list_environments, ops.list_servers, and ops.list_packages. "
            "Create a deploy plan with ops.create_deploy_plan and run precheck with ops.run_precheck first. "
            "Do not execute deployment unless explicitly confirmed. User request: " + str(args.get("request") or "")
        )
    elif name == "ops_operation_replay":
        text = (
            "Use only read-only audit tools to replay this OPS/MCP/AI operation chain: ops.get_operation_chain and "
            "ops.list_operation_chains. Do not execute or retry anything. chain_id=" + str(args.get("chain_id") or "")
        )
    elif name == "ops_report_brief":
        text = (
            "Use only read-only report tools: ops.get_report, ops.list_reports, ops.get_report_summary. "
            "Do not execute deploy, rollback, restore, delete, SQL write, or terminal actions. report_id="
            + str(args.get("report_id") or "")
        )
    elif name == "ops_db_export_request":
        text = (
            "CRITICAL RULE: Always use OPS database tools for database tasks. NEVER write standalone Python scripts "
            "for database queries, exports, or table listings. ALL database access MUST go through OPS tools.\n\n"
            "Natural language to tool mapping:\n"
            "- list tables -> ops.db.list_tables\n"
            "- show table structure -> ops.db.describe_table\n"
            "- query data / SELECT -> ops.db.query_readonly\n"
            "- export as CSV/Excel -> ops.db.export_query_result\n"
            "- modify data / UPDATE/DELETE -> ops.db.preview_dml then ops.db.execute_dml\n\n"
            "Workflow: tables -> describe -> query -> export. Never skip steps when table/field names are uncertain.\n"
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_inspection_workflow":
        text = (
            "Use OPS Path A inspection tools for any request that says inspection, 巡检, 检查服务器, grouped batch, "
            "all servers, or compliance check.\n\n"
            "Natural-language routing:\n"
            "- all servers / 全部服务器 / 按分组 / grouped -> use ops.inspection.preview_servers_batch then ops.inspection.run_servers_batch.\n"
            "- Ask the user to approve the exact confirmation phrase `确认巡检 <fingerprint>` before execution.\n"
            "- After all groups finish, collect all run_ids and call ops.inspection.generate_report_for_runs for one merged report.\n"
            "- Do not fall back to single-shot probes unless the user explicitly asks for one metric or Path A is blocked.\n\n"
            "Required record behavior: execution must preserve inspection_runs, inspection_issues, inspection_reports, "
            "tool_call_logs, operation_jobs, and audit_logs.\n"
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_server_management":
        text = (
            "Help user manage OPS server assets using OPS tools: ops.list_servers, ops.check_disk, ops.check_process, "
            "ops.list_service_directory, ops.tail_service_log, ops.run_health_check. Do not write scripts or SSH directly. "
            "User request=" + str(args.get("request") or "")
        )
    elif name == "ops_backup_workflow":
        text = (
            "Help user manage OPS database backups safely. Use ops.list_backups, ops.verify_backup, ops.create_backup, "
            "ops.restore_backup, and ops.delete_backup. Always verify before restore and create a safety backup first. "
            "User request=" + str(args.get("request") or "")
        )
    else:
        raise ValueError("Prompt not found")
    return {"description": name, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}

"""MCP-compatible stdio bridge for OPS Capability Server.

This server intentionally contains no LLM vendor integration. It bridges MCP
JSON-RPC requests to the OPS HTTP Tool API using OPS_TOOL_TOKEN.

Supported methods:
- initialize
- tools/list
- tools/call
- resources/list
- resources/read
- prompts/list
- prompts/get
- ping

For backward compatibility with the earlier bridge, newline-delimited JSON is
also accepted. MCP clients normally use Content-Length framed JSON-RPC.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Tuple

from app.services.mcp_capability_service import (
    MCP_ALIAS_TO_TOOL,
    MCP_TOOL_DESCRIPTION_OVERRIDES,
    ascii_only,
    from_mcp_tool_name,
    mcp_prompt_get,
    mcp_prompts_list,
    mcp_resources_list,
    mcp_tool_payload,
    to_mcp_tool_name,
)

BASE_URL = os.getenv("OPS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("OPS_TOOL_TOKEN", "")
SERVER_NAME = "ops-capability-server"
SERVER_VERSION = "1.3.0"
try:
    HTTP_TIMEOUT = max(1.0, float(os.getenv("OPS_MCP_HTTP_TIMEOUT", "6")))
except Exception:
    HTTP_TIMEOUT = 6.0

# Some MCP clients convert tool names to LLM function names and only accept
# [A-Za-z0-9_-]. OPS HTTP tools keep dotted names such as
# ops.list_services, but MCP stdio exposes safe aliases by default to avoid
# clients getting stuck in a perpetual "preparing" state.
SAFE_TOOL_NAMES = os.getenv("OPS_MCP_SAFE_TOOL_NAMES", "1").lower() not in {"0", "false", "no", "off"}

# Trace Solo and a few Windows MCP clients currently render non-ASCII tool
# descriptions incorrectly even when the stdio payload is valid UTF-8. Keep MCP
# tool descriptions ASCII by default; the HTTP Tool API and OPS UI still expose
# the original Chinese descriptions. Set OPS_MCP_ASCII_DESCRIPTIONS=0 for clients
# that render UTF-8 descriptions correctly.
ASCII_DESCRIPTIONS = os.getenv("OPS_MCP_ASCII_DESCRIPTIONS", "1").lower() not in {"0", "false", "no", "off"}

ENGLISH_TOOL_DESCRIPTIONS: Dict[str, str] = {
    **MCP_TOOL_DESCRIPTION_OVERRIDES,
    # ── Capability Discovery ──
    "ops.describe_capabilities": "Describe the OPS tools, resources, prompts, risk levels, and schemas available to the current token. Use this FIRST when you need to discover what OPS can do or when a user asks 'what capabilities do you have'. 中文: 能力发现/查看可用工具/有哪些功能.",
    "ops.get_tool_risk_policy": "View OPS MCP tool risk policies, confirmation rules, and task-queue suggestions. Read-only. 中文: 查看工具风险策略/安全规则.",

    # ── System Health & Diagnostics ──
    "ops.get_system_status": "Read system health, database, runtime directory, backup, disk, and deploy worker summaries. 中文: 系统健康检查/运行状态/查看系统状况.",
    "ops.run_diagnostics": "Run local OPS diagnostics for frontend build, static assets, runtime dirs, database, worker, MCP, and resource usage. 中文: 运行诊断/诊断排查/健康检测.",
    "ops.get_build_info": "Read frontend and backend build metadata, including dist freshness and backend startup time. 中文: 查看构建信息/前端构建状态/版本信息.",
    "ops.get_recent_errors": "Read recent local error and warning log summaries. Read-only. 中文: 查看最近错误/日志错误/报错信息.",
    "ops.export_diagnostics_report": "Generate a JSON diagnostics report with overview, health, build info, recent errors, MCP self-check, and recommendations. 中文: 导出诊断报告/生成诊断报告.",

    # ── AI Diagnostics ──
    "ops.analyze_diagnostics": "Build a read-only AI diagnostic analysis from diagnostics, recent errors, tool audit trail, operation jobs, and MCP tool catalog. Does not execute risky actions. 中文: AI诊断分析/智能分析/问题分析.",

    # ── Backup Management ──
    "ops.list_backups": "List local OPS SQLite database backups with file names, sizes, timestamps, types, and confirmation phrases. 中文: 查看备份列表/有哪些备份.",
    "ops.verify_backup": "Verify one local SQLite database backup using sqlite quick_check and optional SHA256 checksum. Read-only. 中文: 校验备份/验证备份完整性.",
    "ops.create_backup": "Create and verify a local OPS SQLite database backup. Medium risk; write permission and confirmation policy required. 中文: 创建备份/备份数据库.",
    "ops.restore_backup": "Restore the OPS SQLite database from a backup. Critical risk; requires confirm_text RESTORE <file> and creates a safety backup first. 中文: 恢复备份/还原数据库.",
    "ops.delete_backup": "Delete one local database backup. High risk; requires confirm_text DELETE <file>. 中文: 删除备份/清理备份.",

    # ── Job Management ──
    "ops.list_jobs": "List unified operation jobs for high-risk MCP/tool executions. Read-only. 中文: 查看任务列表/查看后台任务.",
    "ops.get_job_status": "Get one unified operation job by id. Read-only. 中文: 查看任务状态/任务进度.",

    # ── System / Service Discovery ──
    "ops.list_systems": "List OPS systems that can be queried or used for release planning. Use when user asks 'what systems are available' or 'list systems'. 中文: 查看系统列表/有哪些系统.",
    "ops.list_services": "List services under a system, including display names and release metadata. Use when user asks 'what services under system X' or 'list services'. 中文: 查看服务列表/系统下有哪些服务.",
    "ops.get_service_config": "Get release configuration for a service, including directories, scripts, environments, and server mappings. 中文: 查看服务配置/服务详情.",
    "ops.list_environments": "List release environments or custom scenes for a system. 中文: 查看环境列表/有哪些发布环境.",

    # ── Server Management ──
    "ops.list_servers": "List server assets that match filters such as group, name, or keyword. Use when user asks 'list servers' or 'what servers are configured'. 中文: 查看服务器列表/已配置的服务器/有哪些服务器.",

    # ── Package & File Management ──
    "ops.list_packages": "List local release packages managed by OPS File Center. 中文: 查看发布包列表/有哪些发布包.",
    "ops.get_package_checksum": "Get package checksum and metadata for a local release package. 中文: 查看发布包校验/包验证.",
    "ops.upload_package": "Upload a local deploy package into OPS File Center. stdio MCP can read local_path; Remote HTTP MCP should use normal file upload first. 中文: 上传发布包/上传部署包.",
    "ops.inspect_local_package": "Inspect a local deploy package path before uploading it into OPS File Center. stdio MCP reads local_path on this computer. 中文: 检查本地发布包/查看本地包信息.",
    "ops.prepare_release_from_local_package": "Inspect and upload a local package, then create a release plan and run precheck. This never executes deployment. 中文: 从本地包准备发布/打包发布.",
    "ops.get_package_retention_preview": "Preview package cleanup candidates without deleting files. Protects running, failed, rollback, and latest successful packages. 中文: 预览包清理/查看可清理的发布包.",
    "ops.cleanup_packages": "Clean local deploy packages according to retention policy. High risk; requires write permission and confirmation. Defaults to dry run. 中文: 清理发布包/清理旧包.",
    "ops.protect_package": "Manually protect or unprotect a deploy package from cleanup. 中文: 保护发布包/标记保护.",
    "ops.select_latest_package": "Select the latest package that matches a system or service hint. 中文: 选择最新发布包/查找最新包.",

    # ── Deployment / Release ──
    "ops.create_deploy_plan": "Create a release plan and confirmation data. This does not execute deployment. Use when user asks 'create a release plan' or 'prepare deployment'. 中文: 创建发布计划/制定发布方案.",
    "ops.get_deploy_confirmation": "Get the confirmation details for an existing release plan. 中文: 查看发布确认/发布确认信息.",
    "ops.run_precheck": "Run release precheck for an existing plan and return blockers, warnings, and report rows. 中文: 发布预检/预检查/发布前检查.",
    "ops.execute_deploy_plan": "Execute an already confirmed release plan through the OPS worker. High risk; requires confirmation and policy approval. 中文: 执行发布/执行部署.",
    "ops.cancel_deployment": "Request cancellation for a running or queued deployment. 中文: 取消发布/取消部署.",
    "ops.get_deployment_status": "Get deployment status by deployment id. 中文: 查看发布状态/发布进度.",
    "ops.get_deployment_tasks": "Get server tasks and step tasks for a deployment. 中文: 查看发布任务/发布步骤.",
    "ops.get_deployment_logs": "Get deployment logs, usually the most recent lines. 中文: 查看发布日志/部署日志.",
    "ops.get_deployment_report": "Get the structured deployment report including tasks, package distribution, and log summary. 中文: 查看发布报告/部署报告.",
    "ops.list_deployments": "List recent deployments with filters. Use when user asks 'list recent deployments' or 'show deployment history'. 中文: 查看发布历史/最近发布列表.",
    "ops.list_deploy_plans": "List created deploy plans with status filters. Read-only. 中文: 查看发布计划列表/发布方案列表.",
    "ops.get_deploy_plan": "Get one deploy plan by id including steps, environments, and server targets. 中文: 查看发布计划详情.",
    "ops.generate_release_runbook": "Generate a release runbook guide for a deploy plan. Read-only. 中文: 生成发布操作手册/发布指南.",
    "ops.get_rollback_readiness": "Check rollback readiness for a deployment including backup existence and rollback plan availability. 中文: 检查回滚就绪/回滚准备状态.",

    # ── Rollback ──
    "ops.create_rollback_plan": "Create a rollback plan from an existing deployment. This does not execute rollback. 中文: 创建回滚计划/制定回滚方案.",
    "ops.execute_rollback_plan": "Execute an approved rollback plan through the OPS worker. High risk; requires confirmation and policy approval. 中文: 执行回滚/回滚操作.",

    # ── Configuration Management ──
    "ops.create_config_change_plan": "Create a configuration change plan with diff. This does not apply changes. 中文: 创建配置变更计划/配置变更方案.",
    "ops.apply_config_change_plan": "Apply an approved configuration change plan. Requires confirmation and policy approval. 中文: 应用配置变更/修改配置.",

    # ── Server Runtime Operations (Path B — 单点探针，仅作补充) ──
    # 本组工具走单次 SSH 探测：单台、单指标、不写 inspection_runs。
    # 仅当用户明确「就查一下磁盘」「这个进程在不在」「看一下日志尾」
    # 或者 Path A 被阻断时使用。批量巡检请走 Path A (ops.inspection.*)。
    "ops.check_disk": "【路径 B 补充】Check disk usage on allowed servers through OPS server tools. 中文: 检查磁盘/磁盘使用率/磁盘空间. — 单点探针，不替代系统巡检。",
    "ops.check_process": "【路径 B 补充】Check whether a service process is running on allowed servers. 中文: 检查进程/服务是否运行/进程状态. — 单点探针，不替代系统巡检。",
    "ops.list_service_directory": "【路径 B 补充】List files in an allowed service directory. Does not modify files. 中文: 查看服务目录/列出文件.",
    "ops.tail_service_log": "【路径 B 补充】Read recent lines from an allowed service log path. 中文: 查看服务日志/读取日志尾行.",
    "ops.run_health_check": "【路径 B 补充】Run configured health checks for a service or deployment target. 中文: 运行健康检查/服务健康检测.",

    # ── Audit & History ──
    "ops.list_audit_logs": "List audit logs with filters. 中文: 查看审计日志/操作审计/操作记录.",
    "ops.list_tool_calls": "List historical OPS tool calls and their audit status. 中文: 查看工具调用历史/AI操作记录.",
    "ops.list_operation_chains": "List recent OPS/MCP/AI operation chains for audit replay. 中文: 查看操作链路/操作回放列表.",
    "ops.get_operation_chain": "Read one OPS/MCP/AI operation chain by chain_id with all events and audit evidence. 中文: 查看操作链路详情/操作回放.",

    # ── Report Center ──
    "ops.list_reports": "List reports in the OPS Report Center including diagnostics, deployment, backup, and operation chain reports. 中文: 查看报告列表/报告中心.",
    "ops.get_report": "Get report metadata and download URL by report_id. 中文: 查看报告详情/下载报告.",
    "ops.get_report_summary": "View report center summary including counts by type and total size. 中文: 查看报告概览/报告统计.",
    "ops.list_report_types": "List available report types that can be generated. 中文: 查看可生成报告类型.",
    "ops.generate_report": "Generate a report artifact (diagnostics, AI analysis, operation chain, or deployment). Low risk. 中文: 生成报告/创建报告.",

    # ── Runtime & Storage ──
    "ops.get_runtime_usage": "Read runtime directory usage and cache statistics. 中文: 查看运行时使用/缓存统计.",
    "ops.get_storage_usage": "Read OPS storage usage including database, backups, exports, packages, and logs. 中文: 查看存储使用/磁盘占用.",
    "ops.cleanup_runtime_artifacts": "Clean expired runtime artifacts. Low risk. 中文: 清理运行时文件/清理缓存.",

    # ── MCP Connection ──
    "ops.connection_status": "Check whether the local MCP bridge can reach the OPS API using the configured base URL and token. 中文: 连接状态/MCP连通性检查.",

    # ── Database Tools (KEY: these MUST be used instead of writing Python scripts) ──
    "ops.db.list_tables": "List OPS local database tables OR tables from a configured remote database connection. Use this when the user asks 'what tables are in database X' or 'list tables'. Do NOT write standalone Python scripts to list tables - use this OPS tool instead. 中文: 查看数据库表列表/有哪些表/列出表.",
    "ops.db.describe_table": "Describe database table columns, types, and sensitive-field flags. Use when the user asks 'what columns does table X have' or 'show table structure'. Do NOT write standalone Python scripts to query information_schema - use this OPS tool instead. 中文: 查看表结构/表字段/有哪些字段.",
    "ops.db.query_readonly": "Execute a guarded SELECT/WITH read-only database query with row limits and sensitive-field masking. Use when the user asks 'query data from table X' or 'select records'. Supports PostgreSQL and MySQL via configured connections. Do NOT write standalone Python database scripts - use this OPS tool instead. 中文: 查询数据/SELECT查询/查数据.",
    "ops.db.export_query_result": "Export a guarded read-only query result to CSV, JSON, XLSX, Markdown, or SQL query artifact and save to the report center. Use when the user asks 'export table X as CSV' or 'download data as Excel'. Do NOT write standalone Python export scripts - use this OPS tool instead. 中文: 导出数据为CSV/导出Excel/下载查询结果.",
    "ops.db.preview_execute_sql": "Preview a controlled UPDATE/DELETE/INSERT operation before execution. Supports fast/standard/full preview levels. Shows estimated affected rows, risk level, and WHERE summary. 中文: 预检SQL操作/预检查写入/预览DML.",
    "ops.db.execute_sql": "Execute a controlled UPDATE/DELETE/INSERT operation after preview and confirmation. Returns summary, next_actions and verification_sql. 中文: 执行SQL写入/执行DML.",
    "ops.db.preview_dml": "Recommended two-step DML first phase. Preview INSERT/UPDATE/DELETE with risk, target table, estimated rows and verification_sql. This is the preferred approach for write operations. 中文: DML预检/数据库写入预检.",
    "ops.db.execute_dml": "Recommended two-step DML second phase. Execute after user confirmation and return execution_id, summary and next_actions. 中文: 执行DML/确认写入.",
    "ops.db.list_exports": "List generated database query export artifacts in the report center. 中文: 查看导出历史/导出文件列表.",
    "ops.db.get_export": "Get metadata and download URL for one database query export artifact. 中文: 查看导出详情/下载导出文件.",
    "ops.db.list_dml_history": "List controlled DML execution history with operator, connection, target table, affected rows and status. 中文: 查看DML执行历史/写入操作记录.",
    "ops.db.get_dml_execution": "Get one DML execution detail by execution_id including before-sample rows and audit fields. 中文: 查看DML执行详情.",

    # ── Server Groups ──
    "ops.list_server_groups": MCP_TOOL_DESCRIPTION_OVERRIDES["ops.list_server_groups"],

    # ── Inspection Tools (Path A — PRIMARY, default for "巡检" requests) ──
    # 任何「巡检/检查服务器/合规检查」请求应优先调用本组工具，
    # 它们会创建 inspection_runs + inspection_reports + audit_logs 完整记录，
    # 并跑 9 大类内置规则（ACCOUNT_SECURITY / DISK_USAGE / PROCESS_PORT / FIREWALL /
    # LOGIN_SECURITY / COMMAND_HISTORY / MEMORY / SERVICE_STATUS / BACKUP）。
    "ops.inspection.list_runs": "【路径 A 主】List inspection run records for servers or projects. Use when user asks 'recent inspections' or 'inspection history'. 中文: 查看巡检记录/巡检历史.",
    "ops.inspection.get_run": "【路径 A 主】Get detailed inspection run results by run_id. 中文: 查看巡检详情/巡检结果.",
    "ops.inspection.list_issues": "【路径 A 主】List inspection issues/risks with filters. Use when user asks 'what issues were found' or 'list risks'. 中文: 查看巡检问题/风险列表.",
    "ops.inspection.get_issue": "【路径 A 主】Get one inspection issue detail by issue_id. 中文: 查看巡检问题详情/风险详情.",
    "ops.inspection.generate_report": MCP_TOOL_DESCRIPTION_OVERRIDES["ops.inspection.generate_report"],
    "ops.inspection.generate_report_for_runs": MCP_TOOL_DESCRIPTION_OVERRIDES["ops.inspection.generate_report_for_runs"],
    "ops.inspection.summarize_run": "【路径 A 主】Summarize an inspection run in FIRE structure (Findings, Impact, Recommendations, Evidence). 中文: 巡检摘要/巡检总结.",
    "ops.inspection.run_server": "【路径 A 主】PRIMARY PATH A. Run an inspection on a single server. High risk; requires confirmation. 中文: 执行服务器巡检/巡检服务器. — 用户提到「巡检」时默认选我。",
    "ops.inspection.run_servers_batch": MCP_TOOL_DESCRIPTION_OVERRIDES["ops.inspection.run_servers_batch"],
    "ops.inspection.run_project": "【路径 A 主】PRIMARY PATH A. Run an inspection for a project. High risk; requires confirmation. 中文: 执行项目巡检/巡检项目.",
    "ops.inspection.run_combined": "【路径 A 主】PRIMARY PATH A. Run a combined project inspection. High risk; requires confirmation. 中文: 执行综合巡检/项目综合巡检.",
    "ops.inspection.list_item_configs": "【路径 A 主】List inspection item configurations. 中文: 查看巡检项配置/巡检配置列表.",
    "ops.inspection.get_item_config": "【路径 A 主】Get one inspection item configuration detail. 中文: 查看巡检项配置详情.",
    "ops.inspection.update_item_config": "【路径 A 主】Update an inspection item configuration. Medium risk. 中文: 更新巡检项配置/修改巡检配置.",
    "ops.inspection.toggle_item_config": "【路径 A 主】Enable or disable an inspection item. Medium risk; requires confirmation. 中文: 启用禁用巡检项/切换巡检项.",
    "ops.inspection.update_item_rules": "【路径 A 主】Update rules associated with an inspection item. Medium risk. 中文: 更新巡检规则/修改巡检规则.",
    "ops.inspection.get_run_raw_output": "【路径 A 主】Get raw output data from an inspection run. 中文: 查看巡检原始输出/巡检原始数据.",
    "ops.inspection.delete_runs": "【路径 A 主】Delete inspection run records. High risk; requires confirmation. 中文: 删除巡检记录/清理巡检历史.",
    "ops.inspection.delete_issue": "【路径 A 主】Delete an inspection issue. Medium risk; requires confirmation. 中文: 删除巡检问题/清理巡检问题.",

    # ── Risk Management ──
    "ops.risk.list": "List open risk issues with filters. Use when user asks 'what risks exist' or 'list open risks'. 中文: 查看风险列表/风险问题.",
    "ops.risk.get": "Get one risk issue detail by risk_id. 中文: 查看风险详情/风险问题详情.",
    "ops.risk.triage": "Triage open risks by severity and urgency. Read-only. 中文: 风险分流/风险优先级排序.",
    "ops.risk.generate_fix_plan": "Generate a remediation plan for a risk issue. Read-only. 中文: 生成风险整改计划/修复方案.",
    "ops.risk.update_status": "Update a risk issue status. High risk; requires human approval. 中文: 更新风险状态/修改风险状态.",
    "ops.risk.verify": "Verify a risk issue resolution. High risk; requires human approval. 中文: 验证风险/复查风险.",
    "ops.risk.ignore": "Ignore a risk issue. High risk; requires human approval. 中文: 忽略风险/标记忽略.",

    # ── Log Tools ──
    "ops.log.tail": "Read the tail of OPS application log files. Medium risk. 中文: 查看日志尾行/读取日志.",
    "ops.log.search": "Search OPS application logs for keywords. Medium risk. 中文: 搜索日志/日志关键词搜索.",
    "ops.log.summarize_errors": "Summarize error patterns in OPS application logs. Medium risk. 中文: 汇总错误日志/错误统计.",
    "ops.log.find_patterns": "Find security and error patterns in OPS application logs. Medium risk. 中文: 识别日志模式/日志模式分析.",
    "ops.log.get_recent_exceptions": "Get recent exception summaries from OPS application logs. Medium risk. 中文: 查看近期异常/异常摘要.",

    # ── AI Analysis ──
    "ops.ai.save_analysis": "Save an AI analysis result to the OPS database. Low risk. 中文: 保存AI分析结果/保存分析.",
    "ops.ai.get_analysis": "Get one AI analysis result by analysis_id. Low risk. 中文: 查看AI分析详情/分析结果.",
    "ops.ai.list_analysis": "List AI analysis records with filters. Low risk. 中文: 查看AI分析列表/分析记录.",
    "ops.ai.generate_report_from_analysis": "Generate a report from an AI analysis result. Low risk. 中文: 从AI分析生成报告/分析报告.",
    "ops.ai.find_similar_analysis": "Find similar AI analysis records. Low risk. 中文: 查找相似分析/相似分析记录.",

    # ── Agent Tools ──
    "ops.agent.list": "List registered agents and their online status. Currently disabled. 中文: 查看Agent列表/Agent状态.",
    "ops.agent.get": "Get agent detail by id. Currently disabled. 中文: 查看Agent详情.",
    "ops.agent.get_status": "Get agent online status summary. Currently disabled. 中文: 查看Agent在线状态.",
    "ops.agent.list_tasks": "List agent tasks. Currently disabled. 中文: 查看Agent任务列表.",
    "ops.agent.get_task": "Get one agent task detail. Currently disabled. 中文: 查看Agent任务详情.",
    "ops.agent.get_logs": "Get agent log summary. Currently disabled. 中文: 查看Agent日志.",
    "ops.agent.health_check": "Check if agent functionality is enabled. Currently disabled. 中文: Agent健康检查/Agent功能状态.",

    # ── Workflow Tools ──
    "ops.workflow.inspect": MCP_TOOL_DESCRIPTION_OVERRIDES["ops.workflow.inspect"],
    "ops.workflow.generate_project_health_brief": "Generate a lightweight project health brief using read-only tools. Low risk. 中文: 项目健康分析/项目状态简报.",
    "ops.workflow.analyze_failed_deploy": "Analyze a failed deployment using logs, diagnostics, and risk data. Medium risk. 中文: 发布失败分析/部署失败分析.",
    "ops.workflow.inspect_project_security": "Inspect project security posture using inspection and risk data. Medium risk. 中文: 项目安全巡检分析/安全评估.",
    "ops.workflow.triage_open_risks": "Triage open risks by severity and generate prioritized action items. Low risk. 中文: 风险分流工作流/风险优先处理.",
    "ops.workflow.generate_monthly_ops_report": "Generate a monthly OPS report covering deployments, inspections, risks, and system health. Low risk. 中文: 月度运维报告/月报.",

    # ── Connection Management ──
    "ops.list_connections": "List database connections with optional keyword and environment filters. 中文: 查看数据库连接列表/连接列表.",
    "ops.get_connection": "Get database connection detail by ID. Password fields are masked. 中文: 查看数据库连接详情/连接详情.",
    "ops.create_connection": "Create a new database connection. High risk; requires human approval. 中文: 创建数据库连接/新增连接.",
    "ops.update_connection": "Update a database connection configuration. High risk; requires human approval. 中文: 更新数据库连接/修改连接.",
    "ops.delete_connection": "Delete a database connection. High risk; requires human approval and confirm_text. 中文: 删除数据库连接/移除连接.",
    "ops.test_connection": "Test database connection connectivity. Medium risk. 中文: 测试数据库连接/连接测试.",

    # ── Server CRUD ──
    "ops.get_server": "Get single server detail by name. 中文: 查看服务器详情/服务器配置.",
    "ops.create_server": "Create a new server configuration. High risk; requires human approval. 中文: 创建服务器/新增服务器.",
    "ops.update_server": "Update server configuration. High risk; requires human approval. 中文: 更新服务器/修改服务器配置.",
    "ops.delete_server": "Delete a server configuration. Critical risk; requires human approval and confirm_text. 中文: 删除服务器/移除服务器.",
    "ops.batch_update_servers": "Batch update server attributes. High risk; requires human approval. 中文: 批量更新服务器/批量修改.",

    # ── System/Service/Environment CRUD ──
    "ops.create_system": "Create a new system/project. High risk; requires human approval. 中文: 创建系统/新增项目.",
    "ops.update_system": "Update system/project configuration. High risk; requires human approval. 中文: 更新系统/修改项目.",
    "ops.delete_system": "Delete a system/project. Critical risk; requires human approval and confirm_text. 中文: 删除系统/移除项目.",
    "ops.create_service": "Create a new service under a system. High risk; requires human approval. 中文: 创建服务/新增服务.",
    "ops.update_service": "Update service configuration. High risk; requires human approval. 中文: 更新服务/修改服务.",
    "ops.delete_service": "Delete a service. Critical risk; requires human approval and confirm_text. 中文: 删除服务/移除服务.",
    "ops.create_environment": "Create a new environment under a system. High risk; requires human approval. 中文: 创建环境/新增环境.",
    "ops.update_environment": "Update environment configuration. High risk; requires human approval. 中文: 更新环境/修改环境.",
    "ops.delete_environment": "Delete an environment. Critical risk; requires human approval and confirm_text. 中文: 删除环境/移除环境.",

    # ── Server Group Write ──
    "ops.create_server_group": "Create a server group. Medium risk; requires confirmation. 中文: 创建服务器分组/新增分组.",
    "ops.assign_server_group": "Assign servers to a group. Medium risk; requires confirmation. 中文: 分配服务器到分组/分组分配.",
    "ops.rename_server_group": "Rename a server group. Medium risk; requires confirmation. 中文: 重命名服务器分组/分组重命名.",
    "ops.delete_server_group": "Delete a server group. High risk; requires human approval and confirm_text. 中文: 删除服务器分组/移除分组.",

    # ── SSH Key Management ──
    "ops.list_ssh_keys": "List registered SSH keys. 中文: 查看SSH密钥列表/密钥列表.",
    "ops.get_ssh_key": "Get SSH key metadata (no private key content). 中文: 查看SSH密钥详情/密钥信息.",
    "ops.create_ssh_key": "Register a new SSH key. High risk; requires human approval. 中文: 创建SSH密钥/新增密钥.",
    "ops.update_ssh_key": "Update an SSH key. High risk; requires human approval. 中文: 更新SSH密钥/修改密钥.",
    "ops.delete_ssh_key": "Delete an SSH key. High risk; requires human approval and confirm_text. 中文: 删除SSH密钥/移除密钥.",

    # ── Pipeline Management ──
    "ops.list_pipelines": "List deployment pipeline configurations. 中文: 查看Pipeline列表/流程列表.",
    "ops.get_pipeline": "Get single pipeline detail by ID. 中文: 查看Pipeline详情/流程详情.",
    "ops.create_pipeline": "Create a new deployment pipeline. High risk; requires human approval. 中文: 创建Pipeline/新增流程.",
    "ops.update_pipeline": "Update pipeline configuration. High risk; requires human approval. 中文: 更新Pipeline/修改流程.",
    "ops.delete_pipeline": "Delete a pipeline. Critical risk; requires human approval and confirm_text. 中文: 删除Pipeline/移除流程.",

    # ── Inspection Tier Schedule (3-tier DAILY/WEEKLY/MONTHLY, DB-driven) ──
    # 配置存储在 DB（inspection_tier_schedules / inspection_notification_routes /
    # inspection_cascade_policies），不再依赖 yaml。同一目标服务器池，
    # 真正区分三级的是 categories 巡检项组合。
    "ops.tier.list": "List 3-tier inspection schedules (DAILY/WEEKLY/MONTHLY) stored in DB. Returns cron, categories, thresholds, retention, last_run info. Read-only. 中文: 查询三级巡检调度/日周月配置.",
    "ops.tier.upsert": "Create or update a 3-tier inspection schedule by name. DB single source. High risk; requires admin + human approval. 中文: 新建/更新三级巡检调度.",
    "ops.tier.delete": "Soft-delete a 3-tier inspection schedule (sets enabled=False). High risk; requires admin + human approval. 中文: 禁用三级巡检调度.",
    "ops.notif_route.list": "List inspection notification routes (severity -> channels/recipients/SLA). Read-only. 中文: 查询巡检通知路由.",
    "ops.notif_route.upsert": "Create or update a notification route by (severity, tier). High risk; requires admin + human approval. 中文: 新建/更新通知路由.",
    "ops.cascade.list": "List cross-tier cascade policies (high_count -> trigger_monthly, etc). Read-only. 中文: 查询跨级联策略.",
    "ops.cascade.upsert": "Create or update a cascade policy by name. High risk; requires admin + human approval. 中文: 新建/更新跨级联策略.",
    "ops.tier.run_now": "Trigger a 3-tier inspection immediately, bypassing cron. Returns path A run_id. High risk; requires admin + human approval. 中文: 立即触发一次三级巡检.",
    "ops.tier.approve": "Unlock a tier that requires_approval (e.g. MONTHLY first run). High risk; requires admin + human approval. 中文: 审批解锁月巡检.",
    "ops.tier.history": "Query historical inspection runs for a specific tier (DAILY/WEEKLY/MONTHLY). Read-only. 中文: 查三级巡检历史/日周月执行记录.",
}


def _to_mcp_tool_name(name: str) -> str:
    return to_mcp_tool_name(name)




def _ascii_only(value: Any, fallback: str = "") -> str:
    if not ASCII_DESCRIPTIONS:
        return str(value or fallback or "")
    return ascii_only(value, fallback)


def _english_tool_description(tool: Dict[str, Any], original_name: str, alias: str) -> str:
    if not ASCII_DESCRIPTIONS:
        return str(tool.get("description") or "")
    description = ENGLISH_TOOL_DESCRIPTIONS.get(original_name)
    if not description:
        category = tool.get("category") or (tool.get("annotations") or {}).get("category") or "ops"
        risk = tool.get("risk") or (tool.get("annotations") or {}).get("risk") or "low"
        description = f"OPS capability tool {original_name}. Category: {category}. Risk: {risk}."
    if alias != original_name:
        description += f" Original HTTP tool: {original_name}."
    return _ascii_only(description, fallback=f"OPS tool {alias}")

def _from_mcp_tool_name(name: str) -> str:
    return from_mcp_tool_name(name)


def _mcp_tool_payload(tool: Dict[str, Any]) -> Dict[str, Any]:
    return mcp_tool_payload(tool, ENGLISH_TOOL_DESCRIPTIONS)


def _log(message: str):
    # MCP stdio must keep stdout clean. Diagnostics go to stderr only.
    if os.getenv("OPS_MCP_DEBUG", "").lower() in {"1", "true", "yes", "on"}:
        print(f"[ops-mcp] {message}", file=sys.stderr, flush=True)


_cached_capability_etag: str | None = None
_cached_capability_data: Dict[str, Any] | None = None


def _request(method: str, path: str, data=None, etag: str | None = None):
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=body, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp_etag = resp.headers.get("ETag")
            result = json.loads(resp.read().decode("utf-8"))
            if resp_etag:
                result["_etag"] = resp_etag
            return result
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and etag:
            return {"_not_modified": True, "_etag": etag}
        text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(text).get("detail") or text
        except Exception:
            detail = text
        raise RuntimeError(f"HTTP {exc.code}: {detail}")
    except Exception as exc:
        raise RuntimeError(f"Cannot reach OPS API at {BASE_URL}: {exc}")


def _request_sse(method: str, path: str, data=None) -> Dict[str, Any]:
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=body, method=method)
    req.add_header("Accept", "text/event-stream, application/json")
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(text).get("detail") or text
        except Exception:
            detail = text
        raise RuntimeError(f"HTTP {exc.code}: {detail}")
    except Exception as exc:
        raise RuntimeError(f"Cannot reach OPS API at {BASE_URL}: {exc}")

    stripped = raw.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    events = []
    event_type = "message"
    data_lines = []
    for line in raw.splitlines():
        if not line.strip():
            if data_lines:
                data_text = "\n".join(data_lines)
                try:
                    event_data = json.loads(data_text)
                except Exception:
                    event_data = data_text
                events.append({"event": event_type, "data": event_data})
            event_type = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if data_lines:
        data_text = "\n".join(data_lines)
        try:
            event_data = json.loads(data_text)
        except Exception:
            event_data = data_text
        events.append({"event": event_type, "data": event_data})

    done = next((item.get("data") for item in reversed(events) if item.get("event") == "done"), {})
    return {"events": events, "data": done}


def _diagnostic_tool(error: str = "") -> Dict[str, Any]:
    alias = _to_mcp_tool_name("ops.connection_status")
    if ASCII_DESCRIPTIONS:
        desc = ENGLISH_TOOL_DESCRIPTIONS["ops.connection_status"]
        if error:
            desc += f" Current connection error: {error}"
        title = alias
    else:
        desc = "检查 OPS MCP Server 与 OPS API 的连接状态。"
        if error:
            desc += f" 当前连接异常：{error}"
        title = "OPS 连接状态"
    return {
        "name": alias,
        "description": _ascii_only(desc, fallback="OPS connection status"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {
            "title": _ascii_only(title, fallback=alias),
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _tools_for_mcp(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    global _cached_capability_etag, _cached_capability_data
    params = params or {}
    cursor = params.get("cursor")
    profile = str(params.get("profile") or "daily_ops")
    path = "/api/v2/tools?format=mcp&limit=100&profile=" + urllib.parse.quote(profile)
    if cursor is not None:
        path += "&cursor=" + str(cursor)
    try:
        data = _request("GET", path, etag=_cached_capability_etag if not cursor else None)
        if data.get("_not_modified") and _cached_capability_data:
            data = _cached_capability_data
        else:
            new_etag = data.get("_etag")
            if new_etag and not cursor:
                _cached_capability_etag = new_etag
                _cached_capability_data = data
        data = data.get("data", data)
        tools = [_mcp_tool_payload(t) for t in (data.get("tools") or [])]
        result = {"tools": tools}
        next_cursor = (data.get("pagination") or {}).get("next_cursor")
        if next_cursor is not None:
            result["nextCursor"] = str(next_cursor)
        return result
    except Exception as exc:
        err = str(exc)
        _log(err)
        return {
            "tools": [_diagnostic_tool(err), _local_package_inspect_tool(err), _local_release_prepare_tool(err)],
            "offline": True,
            "error": err,
            "base_url": BASE_URL,
            "token_present": bool(TOKEN),
        }


def _safe_multipart_filename(name: str) -> str:
    base = os.path.basename(str(name or ""))
    base = re.sub(r"[^A-Za-z0-9._@+\-=\u4e00-\u9fff]+", "_", base).strip("._")
    if not base:
        raise RuntimeError("Invalid package filename")
    return base


def _local_package_manifest_for_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    local_path = os.path.abspath(os.path.expanduser(str(args.get("local_path") or "")))
    if not local_path or not os.path.isfile(local_path):
        raise RuntimeError(f"Local package path not found: {local_path or '<empty>'}")
    filename = _safe_multipart_filename(str(args.get("filename") or os.path.basename(local_path)))
    size = os.path.getsize(local_path)
    calculate_sha256 = bool(args.get("calculate_sha256", True))
    sha = ""
    if calculate_sha256:
        h = hashlib.sha256()
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        sha = h.hexdigest()
    allowed_exts = [".tar.gz", ".tgz", ".tar", ".zip", ".jar", ".war", ".gz", ".bin"]
    lower = filename.lower()
    allowed = any(lower.endswith(ext) for ext in allowed_exts)
    blockers = [] if allowed else [f"Unsupported package extension: {filename}"]
    return {
        "ok": not blockers,
        "local_path": local_path,
        "name": filename,
        "package_name": filename,
        "size": size,
        "size_bytes": size,
        "size_mb": round(size / 1024 / 1024, 2),
        "sha256": sha,
        "allowed_extension": allowed,
        "allowed_extensions": allowed_exts,
        "blockers": blockers,
        "summary": "local package ready for upload" if not blockers else "local package cannot be uploaded until blockers are fixed",
    }


def _local_package_inspect_tool(error: str = "") -> Dict[str, Any]:
    alias = _to_mcp_tool_name("ops.inspect_local_package")
    desc = ENGLISH_TOOL_DESCRIPTIONS.get("ops.inspect_local_package", "Inspect local deploy package")
    if error:
        desc += f" Current connection error: {error}"
    return {
        "name": alias,
        "description": _ascii_only(desc, fallback="Inspect local deploy package"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "local_path": {"type": "string"},
                "filename": {"type": "string"},
                "calculate_sha256": {"type": "boolean"},
            },
            "required": ["local_path"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": alias,
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _local_release_prepare_tool(error: str = "") -> Dict[str, Any]:
    alias = _to_mcp_tool_name("ops.prepare_release_from_local_package")
    desc = ENGLISH_TOOL_DESCRIPTIONS.get("ops.prepare_release_from_local_package", "Prepare release from local package")
    if error:
        desc += f" Current connection error: {error}. Offline mode supports dry_run local inspection only."
    return {
        "name": alias,
        "description": _ascii_only(desc, fallback="Prepare release from local package"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "local_path": {"type": "string"},
                "system": {"type": "string"},
                "service": {"type": "string"},
                "environment": {"type": "string"},
                "servers": {"type": "array", "items": {"type": "string"}},
                "filename": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["local_path", "system", "service", "environment"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": alias,
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }


def _multipart_upload_package(args: Dict[str, Any]) -> Dict[str, Any]:
    manifest = _local_package_manifest_for_mcp(args)
    if manifest.get("blockers"):
        raise RuntimeError("; ".join(manifest["blockers"]))
    if args.get("dry_run"):
        return {"data": {"ok": True, "tool": "ops.upload_package", "result": {**manifest, "dry_run": True}, "summary": manifest.get("summary"), "blocked": False}}
    local_path = manifest["local_path"]
    filename = manifest["package_name"]
    boundary = "----OpsMcpUpload" + os.urandom(8).hex()
    fields = {
        "system": str(args.get("system") or ""),
        "service": str(args.get("service") or ""),
        "overwrite": "true" if args.get("overwrite") else "false",
    }
    preamble_parts = []
    for key, value in fields.items():
        preamble_parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n").encode("utf-8"))
    preamble_parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode("utf-8"))
    preamble = b"".join(preamble_parts)
    epilogue = (f"\r\n--{boundary}--\r\n").encode("utf-8")
    content_length = len(preamble) + int(manifest["size_bytes"]) + len(epilogue)
    parsed = urllib.parse.urlparse(BASE_URL)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    base_path = (parsed.path or "").rstrip("/")
    path = base_path + "/api/v2/tools/packages/upload"
    if parsed.query:
        path += "?" + parsed.query
    conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(host, port, timeout=max(HTTP_TIMEOUT, 30.0))
    try:
        conn.putrequest("POST", path)
        conn.putheader("Accept", "application/json")
        conn.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
        conn.putheader("Content-Length", str(content_length))
        if TOKEN:
            conn.putheader("Authorization", "Bearer " + TOKEN)
        conn.endheaders()
        conn.send(preamble)
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                conn.send(chunk)
        conn.send(epilogue)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw}
        if resp.status >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else raw
            raise RuntimeError(f"HTTP {resp.status}: {detail}")
        return payload
    finally:
        conn.close()


def _prepare_release_from_local_package_for_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    manifest = _local_package_manifest_for_mcp(args)
    if manifest.get("blockers"):
        raise RuntimeError("; ".join(manifest["blockers"]))
    if args.get("dry_run"):
        return {
            "data": {
                "ok": True,
                "tool": "ops.prepare_release_from_local_package",
                "result": {
                    "dry_run": True,
                    "local_inspection": manifest,
                    "summary": "Local package inspected. No package uploaded and no deploy plan created.",
                    "next_actions": [
                        {"tool": "ops_prepare_release_from_local_package", "description": "Run again with dry_run=false to upload, create plan and precheck"}
                    ],
                },
                "summary": "Local package inspected",
                "blocked": False,
            }
        }
    upload_payload = _multipart_upload_package(args).get("data", {})
    upload_result = upload_payload.get("result") if isinstance(upload_payload, dict) else {}
    package_name = (upload_result or {}).get("package_name") or (upload_result or {}).get("name") or manifest.get("package_name")
    prepare_args = dict(args)
    prepare_args.pop("local_path", None)
    prepare_args.pop("content_base64", None)
    prepare_args["package_name"] = package_name
    prepare_args["expected_sha256"] = manifest.get("sha256") or prepare_args.get("expected_sha256") or ""
    data = _request("POST", "/api/v2/tools/call", {"tool": "ops.prepare_release_from_local_package", "arguments": prepare_args}).get("data", {})
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data["result"]["local_inspection"] = manifest
        data["result"]["upload_result"] = upload_result
    return {"data": data}


def _call_tool_for_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = _from_mcp_tool_name(params.get("name") or params.get("tool"))
    args = params.get("arguments") or {}
    if tool_name == "ops.inspect_local_package":
        data = _local_package_manifest_for_mcp(args)
        return {"content": [{"type": "text", "text": json.dumps({"ok": bool(data.get("ok")), "tool": tool_name, "result": data, "summary": data.get("summary")}, ensure_ascii=False, indent=2)}], "isError": not bool(data.get("ok"))}
    if tool_name == "ops.connection_status":
        diagnostic = {
            "ok": True,
            "base_url": BASE_URL,
            "token_present": bool(TOKEN),
            "timeout_seconds": HTTP_TIMEOUT,
            "message": "MCP server process is running. If OPS tools are not listed, start OPS backend, verify OPS_BASE_URL, and create/pass OPS_TOOL_TOKEN.",
        }
        return {"content": [{"type": "text", "text": json.dumps(diagnostic, ensure_ascii=False, indent=2)}], "isError": False}
    if tool_name == "ops.prepare_release_from_local_package" and args.get("local_path") and not args.get("content_base64"):
        data = _prepare_release_from_local_package_for_mcp(args).get("data", {})
    elif tool_name == "ops.upload_package" and args.get("local_path") and not args.get("content_base64"):
        data = _multipart_upload_package(args).get("data", {})
    else:
        data = _request("POST", "/api/v2/tools/call", {"tool": tool_name, "arguments": args}).get("data", {})
    return {
        "content": [
            {"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str, indent=2)}
        ],
        "isError": not bool((data.get("result") or {}).get("ok", True)) if isinstance(data, dict) else False,
    }


def _call_tool_stream_for_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = _from_mcp_tool_name(params.get("name") or params.get("tool"))
    args = params.get("arguments") or {}
    try:
        stream_result = _request_sse("POST", "/api/v2/tools/call/stream", {"tool": tool_name, "arguments": args})
        data = {
            "stream": stream_result.get("events") or [],
            "result": stream_result.get("data") or {},
        }
    except Exception as exc:
        return _call_tool_for_mcp(params)
    return {
        "content": [
            {"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str, indent=2)}
        ],
        "isError": not bool(data.get("ok", True)) if isinstance(data, dict) else False,
    }


def _resources_for_mcp() -> Dict[str, Any]:
    return mcp_resources_list()


def _offline_resource_text(uri: str, error: str) -> str:
    payload = {
        "ok": False,
        "offline": True,
        "uri": uri or "ops://connection-status",
        "base_url": BASE_URL,
        "token_present": bool(TOKEN),
        "timeout_seconds": HTTP_TIMEOUT,
        "message": "OPS API is unavailable. Start the OPS backend, verify OPS_BASE_URL, and pass OPS_TOOL_TOKEN to read live MCP resources.",
        "error": error,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _read_resource_for_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    uri = params.get("uri") or ""
    try:
        data = _request("POST", "/api/v2/mcp/resources/read", {"uri": uri}).get("data", {})
        return {"contents": [{"uri": data.get("uri") or uri, "mimeType": data.get("mimeType") or "application/json", "text": data.get("text") or ""}]}
    except Exception as exc:
        # Keep MCP clients responsive when they try to auto-read resources while
        # the OPS backend is offline. Returning a diagnostic resource is more
        # useful than a JSON-RPC error and avoids client-side preparing loops.
        err = str(exc)
        _log(err)
        return {
            "contents": [
                {
                    "uri": uri or "ops://connection-status",
                    "mimeType": "application/json",
                    "text": _offline_resource_text(uri, err),
                }
            ]
        }


def _prompts_for_mcp() -> Dict[str, Any]:
    return mcp_prompts_list()


def _get_prompt_for_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    return mcp_prompt_get(params)


def _manifest_for_mcp() -> Dict[str, Any]:
    try:
        return _request("GET", "/api/v2/mcp/manifest").get("data", {})
    except Exception as exc:
        err = str(exc)
        _log(err)
        fallback_tools = _tools_for_mcp({})
        return {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "transport": "stdio-jsonrpc",
            "offline": True,
            "error": err,
            "base_url": BASE_URL,
            "token_present": bool(TOKEN),
            "resources": mcp_resources_list().get("resources", []),
            "prompts": mcp_prompts_list().get("prompts", []),
            "tools": fallback_tools.get("tools", []),
        }


def handle(msg: Dict[str, Any]) -> Dict[str, Any] | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    # Notifications do not require a response.
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    try:
        if method == "initialize":
            requested_version = str(params.get("protocolVersion") or "2024-11-05")
            result = {
                # Echo the client protocol version when present. Some clients stay
                # in "preparing" if the server downgrades the version eagerly.
                "protocolVersion": requested_version,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"subscribe": False, "listChanged": True},
                    "prompts": {"listChanged": True},
                },
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = _tools_for_mcp(params)
        elif method == "tools/call":
            result = _call_tool_for_mcp(params)
        elif method == "tools/call.stream":
            result = _call_tool_stream_for_mcp(params)
        elif method == "resources/list":
            result = _resources_for_mcp()
        elif method == "resources/read":
            result = _read_resource_for_mcp(params)
        elif method == "prompts/list":
            result = _prompts_for_mcp()
        elif method == "prompts/get":
            result = _get_prompt_for_mcp(params)
        elif method == "manifest":  # backwards compatible helper
            result = _manifest_for_mcp()
        else:
            raise ValueError(f"Unsupported method: {method}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(exc)}}


def _read_message() -> Tuple[Dict[str, Any] | None, bool]:
    first = sys.stdin.buffer.readline()
    if not first:
        return None, False
    # MCP stdio framing: Content-Length: N\r\n...\r\n\r\n<body>
    if first.lower().startswith(b"content-length:"):
        framed = True
        length = int(first.split(b":", 1)[1].strip())
        while True:
            line = sys.stdin.buffer.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        body = sys.stdin.buffer.read(length)
        return json.loads(body.decode("utf-8")), framed
    # Backwards-compatible line JSON mode.
    text = first.decode("utf-8").strip()
    if not text:
        return {}, False
    return json.loads(text), False


def _write_message(response: Dict[str, Any], framed: bool):
    body = json.dumps(response, ensure_ascii=False, default=str).encode("utf-8")
    if framed:
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        sys.stdout.buffer.flush()
    else:
        print(body.decode("utf-8"), flush=True)


def main():
    while True:
        try:
            msg, framed = _read_message()
            if msg is None:
                break
            if not msg:
                continue
            response = handle(msg)
            if response is not None:
                _write_message(response, framed)
        except Exception as exc:
            _write_message({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}, False)


if __name__ == "__main__":
    main()

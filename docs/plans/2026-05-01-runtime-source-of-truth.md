# OPS Runtime Source Of Truth

> Created: 2026-05-01
> Updated: 2026-06-08
> Status: Active

## Purpose

This document declares the single runtime source of truth for current OPS development.

When anyone asks "which runtime path should new work use?", this file is the answer.

## Canonical Runtime Path

```text
React SPA (frontend/)
  -> /api/v2/* (FastAPI routers)
    -> SQLAlchemy/SQLite (app/db/)
      -> PipelineEngine (app/pipeline/)
        -> SSH executor (paramiko)
```

## Frontend Routes In Scope

| Path | Component | Description |
|---|---|---|
| `/login` | `LoginPage` | Public route, no auth probing |
| `/` | `DashboardPage` | Default landing / dashboard |
| `/dashboard` | `DashboardPage` | Dashboard alias |
| `/systems` | `SystemListPage` | System list, system detail panel, service list |
| `/systems/create` | `SystemEditPage` | Create system |
| `/systems/:name/edit` | `SystemEditPage` | Edit system |
| `/systems/:systemName/services/create` | `ServiceEditPage` | Create service under system |
| `/systems/:systemName/services/:serviceName/edit` | `ServiceEditPage` | Edit service under system |
| `/deploy` | `DeployPage` | Deployment execution, resolution preview, deployment history |
| `/tasks` | `TaskCenterPage` | Unified task center for deploy/sql/cleanup/high-risk jobs |
| `/system` | `SystemStatusPage` | Backend/runtime status |
| `/system/status` | `SystemStatusPage` | System status alias |
| `/system/diagnostics` | `SystemDiagnosticsPage` | Diagnostics page |
| `/servers` | `ServerListPage` | Server list |
| `/servers/:name` | `ServerDetailPage` | Server workbench (Overview/Command/Terminal/Files) |
| `/files` | `FileCenterPage` | File center |
| `/pipelines` | `PipelinePage` | Pipeline management, field binding editor, templates |
| `/maintenance` | `AdminMaintenancePage` | Database connections + cleanup jobs (admin only) |
| `/database` | `DatabaseToolsPage` | SQL query/export/DML workbench |
| `/sql-query` | redirect to `/database?tab=query` | Legacy alias only |
| `/audit` | `AuditLogPage` | Audit log with CSV export (admin only) |
| `/reports` | `ReportCenterPage` | Report center |
| `/inspection` | `InspectionCenterPage` | Inspection center |
| `/tools` | `ToolAccessPage` | AI/HTTP Tool access |
| `/mcp-tools` | `McpToolsPage` | MCP tools page |
| `/mcp-audit` | `McpAuditPage` | MCP audit page |
| `/ai-workflows` | `AiWorkflowsPage` | AI workflow page |
| `/ai-analysis` | `AiAnalysisPage` | AI analysis list |
| `/ai-analysis/:id` | `AiAnalysisDetailPage` | AI analysis detail |

## Mounted Backend API

The current runtime mounts v2 routers only.

| Prefix | Router | Module | Notes |
|---|---|---|---|
| `/api/v2/auth` | `auth_v2_router` | `app.api.auth_v2` | Auth/session |
| `/api/v2/deploy` | `deploy_v2_router` | `app.api.deploy_v2` | Deploy plans, execution, history, precheck |
| `/api/v2/pipelines` | `pipeline_v2_router` | `app.api.deploy_v2` | Pipeline management |
| `/api/v2` | `resource_v2_router` | `app.api.deploy_v2` | Systems/services/environments/groups resource APIs |
| `/api/v2/groups` | `groups_v2_router` | `app.api.groups` | Server groups |
| `/api/v2/servers` | `servers_v2_router` | `app.api.servers` | Server workbench API |
| `/api/v2/jump-hosts` | `jump_hosts_v2_router` | `app.api.jump_hosts` | Jump hosts |
| `/api/v2/admin` | `admin_ops_router` | `app.api.config` | Admin config and maintenance helpers |
| `/api/v2/servers` | `terminal_ws_router` | `app.api.terminal` | Terminal websocket/API |
| `/api/v2/servers` | `sftp_router` | `app.api.sftp` | SFTP file operations |
| `/api/v2/maintenance` | `maintenance_router` | `app.api.maintenance` | Database maintenance and controlled execution |
| `/api/v2/tasks` | `task_center_router` | `app.api.task_center` | Unified task center |
| `/api/v2/audit` | `audit_router` | `app.api.task_center` | Audit API |
| `/api/v2/tools` | `tools_router` | `app.api.tools` | HTTP Tool API |
| `/api/v2/mcp` | `mcp_router` / `mcp_gateway_router` | `app.api.tools`, `app.api.mcp_gateway` | MCP-compatible endpoints |
| `/api/v2` | `capabilities_router` | `app.api.tools` | `/api/v2/capabilities` |
| `/api/v2/system` | `system_router` | `app.api.system` | Runtime/system status |
| `/api/v2/reports` | `reports_router` | `app.api.reports` | Report center |
| `/api/v2/inspection` | `inspection_router` | `app.api.inspection` | Inspection center |
| `/api/v2/db` | `db_tools_router` | `app.api.db_tools` | Database query/export/DML APIs |
| `/api/v2/dashboard` | `dashboard_router` | `app.api.dashboard` | Dashboard overview |
| `/api/v2/status` | `status_aggregate_router` | `app.api.v2.status` | Aggregate deploy status |
| `/api/v2/ai/analysis` | `ai_analysis_router` | `app.api.ai_analysis` | AI analysis APIs |

### Resource API Canonical Names

The canonical resource model is:

- System: `/api/v2/systems`, `/api/v2/systems/{system_name}`
- Service under system: `/api/v2/systems/{system_name}/services`
- Environment under system: `/api/v2/systems/{system_name}/environments`
- Group under system: `/api/v2/systems/{system_name}/groups`
- Cross-system service list: `/api/v2/services`

Do not introduce new `/apps` routes or legacy application-page flows. Historical Application wording in older plans is context only.

## Current Verification Commands

Run the following before declaring a runtime change complete:

```bash
python -m compileall -q app scripts main.py config_manager.py ssh_client.py
python -c "import main"
python -m pytest tests -q
cd frontend && npm run typecheck && npm run build
node scripts/frontend_syntax_check.js
node scripts/frontend_route_check.js
```

For narrow MCP/tooling changes, at minimum run the targeted contract tests plus `compileall` and `python -c "import main"`.

## Legacy Status

Current repo facts:

- `main.py` does not mount any `/api/v1/*` router.
- `app/api/workflow.py` does not exist in the current repo.
- v1 routers from the original remediation are no longer part of `main.py`.
- Application-layer UI routes have been replaced by the System/Service runtime flow.
- Historical references to old v1/template/Application concepts are context only.

Rule for new work:

- Add new backend behavior only on the v2 runtime path.
- Prefer System/Service naming in new UI, API, docs, tests, and prompts.
- Treat older plan references to v1/template/Application flows as historical context only.

## Default Deploy Workflow

The maintained default deploy workflow is:

```text
checkout -> build -> upload -> deploy -> health_check -> switch -> restart
```

This order means:

1. Source is checked out and built before upload is considered complete.
2. Health check runs before the current symlink is switched.
3. A failed health check prevents the switch step.
4. Restart happens after a successful switch.

## Auth Model

- Session cookie: `ops_session_v2` (`HttpOnly`)
- Password hashing: `PBKDF2-SHA256` with salt
- Roles: `readonly`, `operator`, `admin` (legacy `viewer` -> `readonly`, `developer` -> `operator` via `normalize_role()`)
- Role order: `readonly(0) < operator(1) < admin(2)`
- Permission gates: `require_auth`, `require_admin`, `require_operator`, `require_role`, `require_deploy`, `require_deploy_for_env`
- RBAC: `require_confirmed_high_risk()` for production deploys/rollbacks, `explain_operation_risk()` for risk labeling
- Prod deploy: restricted to `admin` role only, requires `confirm_production: true`
- Frontend auth behavior: route-aware probing, no `/login` loop
- First-run bootstrap: random password written to `.initial_admin_password`

## Core Data Objects

- `User` in `app.db.models.User`
- `Service` in `app.db.models.Service`
- `ServerGroup` in `app.db.models.ServerGroup`
- `Pipeline` in `app.db.models.Pipeline`
- `PipelineStep` in `app.db.models.PipelineStep`
- `Deployment` in `app.db.models.Deployment`
- `DeployTask` in `app.db.models.DeployTask`
- `DeployLog` in `app.db.models.DeployLog`
- `DatabaseConnection` in `app.db.models.DatabaseConnection`
- `CleanupJob` / `CleanupJobBatch` / `CleanupJobEvent` in `app.db.models`
- `SqlQueryHistory` in `app.db.models.SqlQueryHistory`
- `SavedSql` in `app.db.models.SavedSql`
- `AuditRecord` in `app.db.models.AuditRecord`
- `CommandExecutionLog` in `app.db.models.CommandExecutionLog`
- `InspectionRun`, `InspectionIssue`, `InspectionTierSchedule`, `InspectionNotificationRoute`, `InspectionCascadePolicy` in `app.db.models`
- Server and system configuration from `config_manager.py` and `app.config.systems`

## Infrastructure

- Docker Compose deployment: `docker-compose.yml` (backend + frontend + nginx)
- Health endpoints: `/healthz` (liveness), `/readyz` (readiness with DB check)
- Smoke test: `python scripts/smoke_test.py`
- Build orchestration: `Makefile` (install/build/test/smoke/docker-up/docker-down)
- Environment template: `.env.example`

## Rule

If any other document disagrees with this file about the runtime path, this file wins.

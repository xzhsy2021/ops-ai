# OPS Runtime Source Of Truth

> Created: 2026-05-01
> Updated: 2026-05-08
> Status: Active

## Purpose

This document declares the single runtime source of truth for current OPS
development.

When anyone asks "which runtime path should new work use?", this file is the
answer.

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
| `/apps` | `ApplicationListPage` | Application list |
| `/apps/:id` | `ApplicationDetailPage` | Application detail, variable source display |
| `/deploy` | `DeployPage` | Deployment execution, resolution preview |
| `/tasks` | `TaskCenterPage` | Unified task center (deploy/sql/cleanup) |
| `/servers` | `ServerListPage` | Server list |
| `/servers/:name` | `ServerDetailPage` | Server workbench (Overview/Command/Terminal/Files) |
| `/files` | `FileCenterPage` | File center |
| `/pipelines` | `PipelinePage` | Pipeline management, field binding editor, templates |
| `/maintenance` | `AdminMaintenancePage` | Database connections + cleanup jobs (admin only) |
| `/sql-query` | `SqlQueryPage` | SQL read-only query workbench (admin only) |
| `/audit` | `AuditLogPage` | Audit log with CSV export (admin only) |

## Mounted Backend API

The current runtime mounts only v2 routers.

| Prefix | Router | Module |
|---|---|---|
| `/api/v2/auth` | `auth_v2_router` | `app.api.auth_v2` |
| `/api/v2/deploy` | `deploy_v2_router` | `app.api.deploy_v2` |
| `/api/v2/pipelines` | `pipeline_v2_router` | `app.api.deploy_v2` |
| `/api/v2` | `resource_v2_router` | `app.api.deploy_v2` |
| `/api/v2/apps` | `apps_v2_router` | `app.api.apps` |
| `/api/v2/groups` | `groups_v2_router` | `app.api.apps` |
| `/api/v2/servers` | `servers_v2_router` | `app.api.servers` |
| `/api/v2/servers` | `terminal_ws_router` | `app.api.terminal` |
| `/api/v2/servers` | `sftp_router` | `app.api.sftp` |
| `/api/v2/admin` | `admin_ops_router` | `app.api.config` |
| `/api/v2/maintenance` | `maintenance_router` | `app.api.maintenance` |
| `/api/v2/tasks` | `router` | `app.api.task_center` |
| `/api/v2/audit` | `audit_router` | `app.api.task_center` |

## Current Verification Snapshot

Observed on 2026-05-08:

- `python -m pytest tests -q` -> `277 passed, 1 skipped` (0 failed)
- `npx tsc --noEmit` -> pass
- `npm run build` -> pass

No remaining backend test failures.

No remaining frontend build blockers.

## Legacy Status

Current repo facts:

- `main.py` does not mount any `/api/v1/*` router
- `app/api/workflow.py` does not exist in the current repo
- all v1 routers from the original remediation have been removed from `main.py`
- historical references to old v1/template concepts are context only

Rule for new work:

- add new backend behavior only on the v2 runtime path
- treat older plan references to v1/template flows as historical context only

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
- Roles: `readonly`, `operator`, `admin` (legacy `viewer` → `readonly`, `developer` → `operator` via `normalize_role()`)
- Role order: `readonly(0) < operator(1) < admin(2)`
- Permission gates: `require_auth`, `require_admin`, `require_operator`, `require_role`, `require_deploy`, `require_deploy_for_env`
- RBAC: `require_confirmed_high_risk()` for production deploys/rollbacks, `explain_operation_risk()` for risk labeling
- Prod deploy: restricted to `admin` role only, requires `confirm_production: true`
- Frontend auth behavior: route-aware probing, no `/login` loop
- First-run bootstrap: random password written to `.initial_admin_password`

## Core Data Objects

- `User` in `app.db.models.User`
- `Application` in `app.db.models.Application`
- `ServerGroup` in `app.db.models.ServerGroup`
- `Pipeline` in `app.db.models.Pipeline`
- `PipelineStep` in `app.db.models.PipelineStep` (normalized `config` JSON with `fields`/`defaults` structure)
- `Deployment` in `app.db.models.Deployment`
- `DeployTask` in `app.db.models.DeployTask` (FK to `deployments.id`)
- `DeployLog` in `app.db.models.DeployLog` (FK to both `deploy_tasks.id` and `deployments.id`)
- `DatabaseConnection` in `app.db.models.DatabaseConnection` (with SSH tunnel fields)
- `CleanupJob` / `CleanupJobBatch` / `CleanupJobEvent` in `app.db.models`
- `SqlQueryHistory` in `app.db.models.SqlQueryHistory`
- `SavedSql` in `app.db.models.SavedSql`
- `AuditRecord` in `app.db.models.AuditRecord`
- `CommandExecutionLog` in `app.db.models.CommandExecutionLog`
- Server configuration from `config_manager.py`

## Infrastructure

- Docker Compose deployment: `docker-compose.yml` (backend + frontend + nginx)
- Health endpoints: `/healthz` (liveness), `/readyz` (readiness with DB check)
- Smoke test: `python scripts/smoke_test.py`
- Build orchestration: `Makefile` (install/build/test/smoke/docker-up/docker-down)
- Environment template: `.env.example`

## Rule

If any other document disagrees with this file about the runtime path, this file
wins.

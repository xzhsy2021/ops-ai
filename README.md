# OPS Command Center v2.1.8

OPS Command Center is a local operations console for small internal teams. It provides service release, server assets, file management, system maintenance, database workbench, audit/reporting, and AI Agent/MCP tool access.

The current baseline focuses on stable local operation, single-process deployment, resource governance, and practical troubleshooting. It does not introduce external queues, multi-tenant architecture, or large framework migrations.

## Recommended Startup

Windows:

```bat
start_prod.bat
start_dev.bat
start_diag.bat
```

Linux/macOS:

```bash
./start_prod.sh
./start_dev.sh
./start_diag.sh
```

Default URL:

```text
http://localhost:8000
```

## Current Capabilities

- System, service, and server asset management.
- Deployment precheck, execution, history, and rollback planning.
- File center and server file workbench.
- Unified task center for high-risk and long-running jobs.
- System status, installation diagnostics, database backup, and restore support.
- Database workbench for read-only query, controlled SQL/DML execution, connection config, cleanup, and local OPS DB export.
- Audit logs and report center.
- Inspection center with Path A inspection runs, issues, reports, item config, and three-tier DAILY/WEEKLY/MONTHLY schedules.
- HTTP Tool API and MCP-compatible access.
- Risk policy, confirmation text, taskization, and audit fallback for high-risk operations.
- Channel-neutral qclaw approval integration (Matrix / WeChat / Telegram) with a normalized `message_context`, routing tickets, one-time execution-plan approvals, and temporary self-approval grants for test environments.
- Matrix deploy package integration – Bot/Service account pulls room events, finds the latest media (`m.file`/`m.image`/`m.video`/`m.audio`) from the sender within a time window, downloads `mxc://` media to the File Center, then queues the existing deploy flow (checksum, artifact store, server selection, release, audit). Full audit trail: `roomId`, `mediaEventId`, `triggerEventId`, `sender`, `environment`, `service`.

## AI / MCP Safety Baseline

- Low-risk read tools may be auto-called when token scopes allow them.
- Path A inspection execution tools can be called by AI/MCP tool tokens only after the user provides exact `confirm_text`; calls still go through backend risk policy, task center, and audit.
- Agent runtime tools (`ops.agent.*`) are hidden by default unless `agent_runtime_enabled=true`.
- Deploy execution, rollback, destructive deletes, terminal dangerous commands, config write, package cleanup, runtime cleanup, and database DML remain gated by scopes, capability settings, and human confirmation.
- DML should use `ops.db.preview_dml` -> `ops.db.execute_dml`.

Recommended inspection assistant token:

```json
{
  "description": "Routine MCP inspection assistant",
  "scopes": ["ops:read", "ops:write"],
  "allow_write": true,
  "allow_prod": false,
  "expires_in_days": 90
}
```

Set `expires_in_days` to `0` only for reviewed non-expiring tokens. The token description is persisted and returned by the Tool Token API so operators can see why a token exists.

Use a separate, explicitly reviewed admin token for deploy execution, rollback, DB write, package cleanup, and schedule administration.

## Local Artifact Cleanup

One-shot inspection/debug files in the repository root are not product artifacts. Preview and clean them with:

```bash
python scripts/cleanup_stale_artifacts.py --json
python scripts/cleanup_stale_artifacts.py --apply --json
```

The cleanup script only matches known transient patterns such as root `inspect_report_*.md`, `inspect_out*.txt`, `token_debug*.txt`, `logs/*.log`, and `scripts/_*.json`.

## Database Execution Boundary

The database workbench supports:

- Business DB `SELECT` / `SHOW` / `DESCRIBE` / `EXPLAIN` / `WITH` queries.
- Fast / standard / full precheck levels for controlled SQL execution.
- DML verification SQL generation after execution.
- Cleanup dry run, review, batch execution, pause, and resume.
- Local OPS DB query/export/maintenance.

Write operations must go through the SQL execution or cleanup workflows. The backend enforces single-statement execution, `WHERE` constraints, max affected rows, and blocks DDL, privilege changes, locking statements, multi-statement payloads, and long-running functions where applicable.

## Team Usage

1. Check system status before upgrades: backup, disk, worker, and frontend build state.
2. Enter database functions from the Database page; do not split write paths across maintenance pages.
3. For inspection requests, prefer Path A tools such as `ops.inspection.run_servers_batch`, not one-off server probes.
4. Precheck before release. Production release and rollback should keep human confirmation enabled.
5. Run `scripts/backup_before_upgrade.*` before each upgrade and keep the most recent usable backup.

## Verification

Fast local check:

```bash
python -m compileall -q app scripts main.py config_manager.py ssh_client.py
node scripts/frontend_syntax_check.js
node scripts/frontend_route_check.js
```

Full check:

```bash
cd frontend && npm ci && npm run typecheck && npm run build
cd ..
pip install -r requirements.txt
python -m pytest tests -q
```

For MCP/tooling-only changes, also run:

```bash
python -m pytest tests/test_mcp_contract_sync.py -q
python -c "import main"
```

## Documentation

- **Swapping / onboarding AI agents (operator runbook): `docs/agent-swap-runbook.md`** — MCP mount, meta-instruction AGENTS.md template, host approval-gate allowlist, verification checklist, and troubleshooting. OPS holds no agent-specific business facts; the Agent Context Layer (`ops.integration.get_context_pack` and friends) serves them to any MCP client.
- Agent Context Layer design and implementation record: `docs/agent-integration-abstraction-layer.md`
- Runtime routes and API mounts: `docs/plans/2026-05-01-runtime-source-of-truth.md`
- MCP/HTTP tool matrix: `docs/runbooks/mcp-capability-matrix.md`
- Current plans index: `docs/plans/README.md`

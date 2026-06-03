# Agent Development Checklist

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this checklist task-by-task.

**Goal:** Close the remaining gaps between the active iteration plan and the current implementation without reworking already-completed features.

**Context:** The active plan remains [2026-06-01-concurrent-deploy-and-mcp-maturity-iteration-plan.md](/D:/code/ops-ai/docs/plans/2026-06-01-concurrent-deploy-and-mcp-maturity-iteration-plan.md). This checklist is stored under `archive/` on purpose so `docs/plans/` keeps only the single active iteration plan.

**Execution Rules:**
- Use `venv\Scripts\python.exe` for all Python commands on Windows.
- Do not redesign or refactor completed plan items unless a listed task explicitly requires touching them.
- Keep behavior backward-compatible unless the checklist says otherwise.

---

## Status Summary

- Completed already: `P0-1`, `P0-2`, `P0-3`, `P1-1`, `P1-2`, `P1-3`, `P2-2`, `P2-3`
- Partially complete: `P0-5`, `P1-4`, `P2-1`
- Not complete: `P0-4`

## Do Not Rework

- Concurrent deploy request fields and execution flow
- `partial_failed` deployment status handling
- MCP `ETag` / `If-None-Match` caching behavior
- `POST /api/v2/tools/call/stream`
- Skeleton loading states already wired into high-frequency pages
- Lazy-loaded `TerminalTab`
- SSE subscriber TTL and max-subscriber guard
- SQLite index creation in `app/db/base.py`

---

### Task 1: Finish `P0-4` Tool Audit Async Write Path

**Goal:** Remove synchronous request-path audit writes for normal tool success/queued flows and make audit persistence consistently async with safe fallback behavior.

**Files:**
- Modify: [app/services/tool_registry.py](/D:/code/ops-ai/app/services/tool_registry.py)
- Modify: [app/services/tool_audit.py](/D:/code/ops-ai/app/services/tool_audit.py)
- Modify: [app/services/audit_writer.py](/D:/code/ops-ai/app/services/audit_writer.py)
- Verify startup/shutdown hooks: [main.py](/D:/code/ops-ai/main.py)
- Add tests: `tests/test_tool_audit_async_contract.py`

**Current Gap:**
- Success and queued paths still call synchronous `record_tool_call(...)`.
- Only blocked/failed paths use `record_tool_call_async(...)`.

**Checklist:**
- Add async entry points for normal success and queued audit writes.
- Preserve queue-full fallback to synchronous write.
- Preserve shutdown drain behavior.
- Decide whether `record_plan_event(...)` also needs an async writer path; if yes, implement it in the same task.
- Keep returned API payloads unchanged unless an `audit_id` dependency forces a minimal contract update.

**Acceptance:**
- Normal successful tool calls no longer block on direct `db.commit()` in the request path.
- Queued high-risk tool calls also avoid direct synchronous audit writes in the request path.
- No audit records are lost when the queue is full or on shutdown.

**Verification:**
- `venv\Scripts\python.exe -m pytest tests/test_tool_audit_async_contract.py -q`
- `venv\Scripts\python.exe -c "import main"`

---

### Task 2: Finish `P0-5` Pipeline Snapshot Isolation

**Goal:** Complete the remaining Stage C work so running deployments use frozen step config snapshots and the UI can show snapshot-vs-live differences.

**Files:**
- Modify: [app/api/deploy/_shared.py](/D:/code/ops-ai/app/api/deploy/_shared.py)
- Modify: [app/domain/runtime/snapshots.py](/D:/code/ops-ai/app/domain/runtime/snapshots.py)
- Modify: [app/deploy/logs.py](/D:/code/ops-ai/app/deploy/logs.py)
- Modify: [frontend/src/pages/deploy/DeploymentRunPanel.tsx](/D:/code/ops-ai/frontend/src/pages/deploy/DeploymentRunPanel.tsx)
- Add tests: `tests/test_pipeline_snapshot_isolation_contract.py`

**Current Gap:**
- `captured_config` is stored on step-task creation.
- The runtime snapshot/detail path exposes `captured_config`.
- The execution path does not clearly prove that later pipeline edits cannot affect an already-running deployment.
- No visible snapshot-vs-live diff is exposed in the deployment UI.

**Checklist:**
- Freeze step config at deployment execution start and make the execution path explicitly use the frozen snapshot.
- Make deployment snapshot/detail APIs return both captured step config and current live step config where appropriate.
- Show a compact diff in the deployment UI when live config differs from captured config.
- Ensure older deployments without `captured_config` degrade gracefully.

**Acceptance:**
- Editing a pipeline after a deployment is queued/running does not affect that deployment's step behavior.
- Deployment detail UI clearly shows captured config and highlights drift from current config.

**Verification:**
- `venv\Scripts\python.exe -m pytest tests/test_pipeline_snapshot_isolation_contract.py -q`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run build`

---

### Task 3: Align `P1-4` Dist-Stale Banner with the Planned API

**Goal:** Keep the existing banner UX, but move it to a dedicated backend endpoint instead of piggybacking on `/api/v2/system/health`.

**Files:**
- Modify: [app/api/system.py](/D:/code/ops-ai/app/api/system.py)
- Modify: [frontend/src/components/DistStaleBanner.tsx](/D:/code/ops-ai/frontend/src/components/DistStaleBanner.tsx)
- Modify if useful: [frontend/src/api.ts](/D:/code/ops-ai/frontend/src/api.ts)
- Add tests: `tests/test_frontend_status_contract.py`

**Current Gap:**
- Banner exists and works.
- It currently reads `frontend_dist_stale` from `/api/v2/system/health`.
- The plan called for a dedicated `/api/v2/system/frontend-status` endpoint.

**Checklist:**
- Add `GET /api/v2/system/frontend-status`.
- Return a small payload dedicated to frontend build freshness.
- Switch the banner component to the dedicated endpoint.
- Keep the 24h dismiss localStorage behavior unchanged.

**Acceptance:**
- Banner still appears when frontend build output is stale.
- Banner no longer depends on the larger system health payload.

**Verification:**
- `venv\Scripts\python.exe -m pytest tests/test_frontend_status_contract.py -q`
- `cd frontend && npm run typecheck`
- `cd frontend && npm run build`

---

### Task 4: Unify `P2-1` Retention Entry Points

**Goal:** Reduce overlap between release-history retention and SQLite log-retention flows so operators have one clear retention path per concern.

**Files:**
- Modify: [app/services/release_retention.py](/D:/code/ops-ai/app/services/release_retention.py)
- Modify: [app/services/sqlite_cleanup.py](/D:/code/ops-ai/app/services/sqlite_cleanup.py)
- Modify: [app/api/deploy/history.py](/D:/code/ops-ai/app/api/deploy/history.py)
- Modify: [app/api/maintenance.py](/D:/code/ops-ai/app/api/maintenance.py)
- Modify: [frontend/src/pages/deploy/RetentionPolicyPanel.tsx](/D:/code/ops-ai/frontend/src/pages/deploy/RetentionPolicyPanel.tsx)
- Add tests: `tests/test_retention_unification_contract.py`

**Current Gap:**
- `release_retention.py` manages deploy/audit/tool-call cleanup policy.
- `sqlite_cleanup.py` separately implements archive + execute for log-like tables.
- Operators can reach overlapping retention behavior from different places.

**Checklist:**
- Decide and document ownership:
  - release-history retention
  - tool-call/audit retention
  - archived JSONL/GZ output
- Either merge the overlapping paths or make one path the explicit orchestration layer.
- Ensure preview and execute responses consistently report:
  - candidate counts
  - deleted counts
  - archive file paths
  - skipped reasons
- Keep backward compatibility for existing API consumers where practical.

**Acceptance:**
- There is one clear operator-facing entry point for each retention workflow.
- Archive output location and response structure are consistent.
- No duplicated cleanup logic remains ambiguous.

**Verification:**
- `venv\Scripts\python.exe -m pytest tests/test_retention_unification_contract.py -q`
- `venv\Scripts\python.exe -c "import main"`

---

## Final Verification

Run after all checklist tasks are complete:

```powershell
venv\Scripts\python.exe -m compileall -q app scripts main.py config_manager.py ssh_client.py
venv\Scripts\python.exe -c "import main"
venv\Scripts\python.exe -m pytest tests -q
cd frontend; npm run typecheck
cd frontend; npm run build
node scripts/frontend_syntax_check.js
node scripts/frontend_route_check.js
```

## Notes for the Agent

- Keep changes tightly scoped to the four tasks above.
- Do not reopen completed work unless tests force a minimal compatibility fix.
- Prefer adding focused contract tests over broad refactors.

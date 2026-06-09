# 2026-06-01 Concurrent Deploy And MCP Maturity Iteration

> Created: 2026-05-21
> Updated: 2026-06-08
> Status: Baseline closed; remaining items tracked by focused tests/runbooks

This document used to be the active iteration plan for concurrent deploy and MCP maturity work. The previous copy had become stale and partially unreadable, so it is now reduced to the current delivered baseline and the remaining follow-up list.

For canonical runtime routes and API mounts, read:

- [2026-05-01-runtime-source-of-truth.md](./2026-05-01-runtime-source-of-truth.md)

For current MCP/tool capability mapping, read:

- [../runbooks/mcp-capability-matrix.md](../runbooks/mcp-capability-matrix.md)

## Current Baseline

Runtime constraints remain unchanged:

- Local single-process FastAPI + SQLite WAL + React SPA.
- No Redis, queue service, Kubernetes, or external database requirement.
- New backend behavior goes through `/api/v2/*`.
- System/Service is the canonical resource model; Application-layer routes are historical only.
- Agent runtime is not enabled by default; server-side autonomous agent tools remain hidden unless explicitly enabled.

## Delivered Or Current Behavior

### Deployment

- Deployment uses the v2 deploy API under `/api/v2/deploy`.
- Pipeline and resource APIs stay under `/api/v2/pipelines` and `/api/v2`.
- The maintained deployment workflow remains:

```text
checkout -> build -> upload -> deploy -> health_check -> switch -> restart
```

### MCP / AI Tooling

- HTTP Tool API is under `/api/v2/tools`.
- MCP-compatible endpoints are under `/api/v2/mcp`.
- Capability discovery is under `/api/v2/capabilities`.
- `capability_version` is exposed to clients and should be treated as the cache invalidation key.
- Large-payload tools can declare `streamable=True`.
- `POST /api/v2/tools/call/stream` returns SSE events.
- MCP private extension `tools/call.stream` wraps streamed events into a `stream` + `result` payload for clients that opt in.

Current streamable tools:

- `ops.db.export_query_result`
- `ops.get_deployment_logs`
- `ops.export_diagnostics_report`

### Inspection / MCP

- Path A inspection tools (`ops.inspection.run_server`, `ops.inspection.run_servers_batch`, `ops.inspection.run_project`, `ops.inspection.run_combined`) are the primary path for inspection requests.
- These tools require explicit `confirm_text` in their schemas and are not auto-callable.
- Tool-token calls may execute Path A inspection only through the scoped carve-out in policy plus the second-layer confirmation gate.
- `ops.agent.*` tools are hidden when `agent_runtime_enabled=false`.
- Three-tier schedule tools are DB-backed:
  - `ops.tier.list`, `ops.tier.upsert`, `ops.tier.delete`
  - `ops.notif_route.list`, `ops.notif_route.upsert`
  - `ops.cascade.list`, `ops.cascade.upsert`
  - `ops.tier.run_now`, `ops.tier.approve`, `ops.tier.history`

## Remaining Follow-Ups

These are not active broad-plan commitments; implement them as focused tasks with their own tests.

| Area | Follow-up | Notes |
|---|---|---|
| Concurrent deploy | Keep or add contract tests around `parallelism=1` compatibility before changing execution semantics | Do not alter deploy concurrency without a failing contract test first |
| MCP streaming | Expand streaming to additional truly large result tools only after proving payload pressure | Keep normal `tools/call` behavior compatible |
| Frontend UX | Skeleton screens, xterm lazy load, and dist-stale banner are UI polish tasks | Verify with frontend typecheck/build and route checks |
| Resource governance | SQLite retention, SSE subscriber TTL, and missing indexes remain maintenance work | Keep archive/audit recovery paths explicit |
| Documentation | Keep MCP matrix and runtime source-of-truth updated when tool names/routes change | Tests now guard the most error-prone names |

## Verification Gate

For MCP/tooling changes:

```bash
python -m compileall -q app scripts main.py config_manager.py ssh_client.py
python -m pytest tests/test_mcp_contract_sync.py -q
python -c "import main"
```

For deployment or frontend changes, also run:

```bash
python -m pytest tests -q
cd frontend && npm run typecheck && npm run build
node scripts/frontend_syntax_check.js
node scripts/frontend_route_check.js
```

## Documentation Maintenance Rule

- Keep only one active status tracker in `docs/plans/`.
- Treat this file as the 2026-06 baseline summary, not as an evergreen implementation source.
- When route/tool facts disagree, `2026-05-01-runtime-source-of-truth.md` and `docs/runbooks/mcp-capability-matrix.md` win.

# Plans README

Updated: 2026-08-10

## Current Sources Of Truth

Use these files for current development decisions:

- [2026-05-01-runtime-source-of-truth.md](./2026-05-01-runtime-source-of-truth.md) - canonical runtime routes, API mounts, resource model, and verification gate.
- [../runbooks/mcp-capability-matrix.md](../runbooks/mcp-capability-matrix.md) - current HTTP Tool / MCP tool names, aliases, risk gates, inspection workflow, and streaming-tool status.
- [../runbooks/HIGH_RISK_CAPABILITY_ASSESSMENT.md](../runbooks/HIGH_RISK_CAPABILITY_ASSESSMENT.md) - high-risk / write tool assessment and recommended disposition (review document, no code).
- [2026-06-08-mcp-efficiency-permission-ui-implementation.md](./2026-06-08-mcp-efficiency-permission-ui-implementation.md) - implemented MCP efficiency, permission preview, stdio fallback, Tool Access UI, pagination, and task/report/ledger layout changes.
- [2026-06-01-concurrent-deploy-and-mcp-maturity-iteration-plan.md](./2026-06-01-concurrent-deploy-and-mcp-maturity-iteration-plan.md) - closed baseline summary for the 2026-06 concurrent deploy/MCP iteration.
- [2026-07-22-qclaw-element-approval-ops-design.md](./2026-07-22-qclaw-element-approval-ops-design.md) - qclaw Element approval integration design (single-action approval).
- [2026-07-22-qclaw-element-approval-ops-implementation.md](./2026-07-22-qclaw-element-approval-ops-implementation.md) - implementation plan for the qclaw approval integration.
- [2026-08-06-message-execution-plan-design.md](./2026-08-06-message-execution-plan-design.md) - design of the message-level execution plan (鏂规 2, one approval per message).
- [2026-08-06-message-execution-plan.md](./2026-08-06-message-execution-plan.md) - implementation plan and task status for the message execution plan.
- [2026-08-10-execution-plan-first-deploy-bugfix-audit.md](./2026-08-10-execution-plan-first-deploy-bugfix-audit.md) - first real deployment of multi-service execution plan (F57E0F29): env-param stale-code fix, service-control failure propagation fix, and audit confirming no out-of-band deployment.

## Keep In Root

Keep only these document types in `docs/plans/`:

- Source-of-truth references.
- Approved long-lived design references.
- The current plans index.
- One active iteration plan only when an iteration is explicitly opened.

## Supporting References

These remain in root because they are still valid design/reference documents, not live status trackers:

- `2026-05-01-runtime-source-of-truth.md`
- `2026-05-02-ops-remote-workbench-design.md`
- `2026-05-03-pipeline-variable-binding-design.md`
- `2026-06-01-inspection-center-development.md`
- `2026-06-01-concurrent-deploy-and-mcp-maturity-iteration-plan.md` - baseline summary, not an active queue
- `2026-06-08-mcp-efficiency-permission-ui-implementation.md` - implemented MCP/UI/pagination summary, not an active queue

## Archived / Superseded

Superseded plans live in `docs/plans/archive/`:

- `2026-05-21-response-speed-and-interaction-iteration-plan.md` - response-speed + interaction iteration; remaining ideas folded into later focused work.
- `2026-05-21-remove-application-layer-design.md` - Application entity removal; superseded by the System/Service runtime path.
- `2026-05-21-remove-application-layer-implementation-plan.md` - execution plan for the above; superseded.
- `2026-05-20-ops-platform-ai-agent-iteration-development-plan.md` - platform-convergence/resource-optimization/MCP-maturation main line; superseded.
- `2026-05-20-ops-platform-ai-agent-iteration-execution-plan.md` - task-by-task execution plan for the above; superseded.

Older legacy archive entries are retained under `archive/` for history.

## Cleanup Rule

- Keep one active plan set only.
- Move superseded status trackers and old execution plans to `archive/`.
- Keep design references out of status-tracking duty unless they are intentionally maintained.
- Do not auto-delete `docs/runbooks/`; treat runbooks as operational history unless explicitly reviewed for archival.

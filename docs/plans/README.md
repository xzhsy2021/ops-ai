# Plans README

## Active Plan

The current iteration plan is:

- `2026-06-01-concurrent-deploy-and-mcp-maturity-iteration-plan.md`

This is the only active iteration plan in `docs/plans/`.

Baseline at the time of writing (2026-05-21):

- `python -c "import main"` → OK
- Backend tests: `python -m pytest tests -q` — 56 pass / 5 pre-existing release-worker flakes (unrelated, deferred to a future iteration)
- Frontend typecheck (`npx tsc --noEmit`) → OK
- Frontend build (`npm run build`) → OK; `ApplicationDetailPage-*.js` / `ApplicationListPage-*.js` no longer present in `frontend/dist`

Completed in the prior iteration (2026-05-21 response-speed plan):

- P0-1 startup unblock + Application residue cleanup
- P0-2 tool registry caching (`ensure_builtin_registered()`)
- P0-4 incremental preflight (`build_info._CHECK_CACHE` + `--deep-scan`)
- P1-1 deploy log SSE (`app/deploy/stream.py` + `useDeploymentStream.ts`)

This iteration's headline themes:

1. **Multi-server concurrent deploy** (P0-1 / P0-2) — new feature; `parallelism` defaults to 1 (backward compatible)
2. **Speed carry-overs** (P0-3 cursor + ETag, P0-4 async tool-audit)
3. **MCP / AI Agent maturity** (P1-1 capability ETag both ways + immediate invalidation, P1-2 streaming tool results)
4. **UX polish** (P1-3 skeleton + xterm lazy, P1-4 dist banner)
5. **Resource governance** (P2-1 SQLite retention, P2-2 SSE subscriber TTL, P2-3 missing indexes)

### 2026-06 巡检中心子线（并行交付，独立 runbook）

巡检中心为独立子线，规范与交付文档位于：

- 技术方案 / 落地状态：[../inspection_center_upgrade_spec.md](../inspection_center_upgrade_spec.md)（2026-06-03 已追加 §6 实际落地状态）
- 完整开发文档：[2026-06-01-inspection-center-development.md](2026-06-01-inspection-center-development.md)
- oncall / 已知缺陷：[../runbooks/INSPECTION_TROUBLESHOOTING.md](../runbooks/INSPECTION_TROUBLESHOOTING.md)
- MCP 工具矩阵：[../runbooks/mcp-capability-matrix.md](../runbooks/mcp-capability-matrix.md)（Server & Inspection 章节）

## Keep in Root

Keep only these document types in `docs/plans/`:

- The single active iteration plan
- Approved design references (long-lived)
- Source-of-truth references
- This index file

## Supporting References

These remain in root because they are still valid design references, not status trackers:

- `2026-05-01-runtime-source-of-truth.md`
- `2026-05-02-ops-remote-workbench-design.md`
- `2026-05-03-pipeline-variable-binding-design.md`
- `2026-06-01-inspection-center-development.md` — 巡检中心完整开发文档（2026-06-01 交付口径）

## Archived / Superseded

Superseded plans live in `docs/plans/archive/`:

- `2026-05-21-response-speed-and-interaction-iteration-plan.md` — response-speed + interaction iteration; P0-1 / P0-2 / P0-4 / P1-1 delivered, remaining items folded into the 2026-06-01 plan
- `2026-05-21-remove-application-layer-design.md` — Application entity removal; superseded after the 2026-05-21 P0-1 cleanup
- `2026-05-21-remove-application-layer-implementation-plan.md` — execution plan for the above; superseded
- `2026-05-20-ops-platform-ai-agent-iteration-development-plan.md` — platform-convergence + resource-optimization + MCP-maturation main line; most actions delivered, follow-ups carried forward in 2026-05-21 then 2026-06-01
- `2026-05-20-ops-platform-ai-agent-iteration-execution-plan.md` — task-by-task execution plan for the above; superseded

Older legacy archive entries (pre-2026-05) are retained under `archive/` for history.

## Cleanup Rule

- Keep one active plan set only.
- Move superseded status trackers and old execution plans to `archive/`.
- Keep design references out of status-tracking duty unless they are intentionally maintained.
- Do not auto-delete `docs/runbooks/` from this rule; treat runbooks as operational history unless explicitly reviewed for archival.

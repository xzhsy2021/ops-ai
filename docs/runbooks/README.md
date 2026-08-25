# Runbooks README

## Active Runbooks

The files kept in `docs/runbooks/` are current operational references that still have direct usage value:

- `AI_CAPABILITY_DISCOVERY.md`
- `AI_TOOL_MCP_EXAMPLES.md`
- `CONCURRENCY_ISSUES_REVIEW.md` — 并发问题全量复盘：阻塞修复 + SQLite 锁竞争 + 真并行并发审查结论（2026-08-25）
- `EVENT_LOOP_BLOCKING_FIXES.md` — 单进程事件循环阻塞修复逐项清单
- `INSPECTION_TROUBLESHOOTING.md` — 巡检中心 oncall / 已知缺陷 / 修复记录
- `MCP_PACKAGE_UPLOAD_AND_RETENTION.md`
- `RELEASE_AUDIT_RETENTION_POLICY.md`
- `SYSTEM_RELEASE_FLOW_ACTUAL.md`
- `SYSTEM_SERVICE_MANAGEMENT.md`
- `mcp-capability-matrix.md` — 包含 2026-06-03 新增的 Server & Inspection 工具章节

## What Belongs Here

Keep only documents that are still useful as one of:

- current operator runbooks
- MCP / Tool usage references
- release flow references
- retention or governance references

## What Does Not Belong Here

Do not keep these in root:

- one-off hotfix notes
- page-level UI tweak records
- completed iteration delivery notes
- obsolete implementation snapshots

Those belong in `docs/runbooks/archive/`.

## Cleanup Rule

- Keep the root runbooks small and operational.
- Archive historical delivery notes instead of deleting them.
- Promote a document back to root only if it becomes a maintained operational reference again.

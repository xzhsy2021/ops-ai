# Temporary Self-Approval and Multichannel Progress

Updated: 2026-08-12 (Asia/Shanghai)

## Resume Point

- Repository: `D:\code\ops-ai`
- Worktree: `D:\code\ops-ai\.worktrees\temp-self-approval-multichannel`
- Branch: `codex/temp-self-approval-multichannel`
- Implementation plan: `docs/plans/2026-08-11-temporary-self-approval-multichannel-implementation.md`
- Design: `docs/plans/2026-08-11-temporary-self-approval-multichannel-design.md`
- Last implementation commit before this checkpoint: `da89b9a`
- Task 6 follow-up fixes are currently being validated.

Do not continue in the root worktree. Resume in the dedicated worktree above.

## Task Status

| Task | Status | Notes |
|---|---|---|
| 1. Remove backend AI analysis | Complete, spec and quality approved | Built-in analysis routes, services, tools, report types, models and tables retired. Raw facts/tools and external Agent metadata remain. |
| 2. Remove frontend AI analysis | Complete, spec and quality approved | Old pages, routes, clients and diagnostics panel removed. Production build passed. |
| 3. Generic message context | Complete, spec and quality approved | Matrix, WeChat and Telegram context, strict identity validation and collision-safe actor keys added. |
| 4. Generic Tool Token bindings | Complete, spec and quality approved | Generic conversation/approver policy is the runtime source; Matrix fields are compatibility aliases. Central tool guard covers dispatch paths. |
| 5. Generic routing tickets and approvers | Complete, spec and quality approved | Fresh focused review `tests\test_qclaw_routing.py tests\test_multichannel_routing_tools.py tests\test_task5_quality_review.py tests\test_task5_final_quality.py tests\test_task5_final_review.py` passed 81/81 on this checkpoint. |
| 6. Generic approvals and execution plans | In progress, follow-up review | Added generic persisted context, actor keys, approval identities, temporary grant reference, idempotent Matrix backfill/digest migration, generic MCP/API context, and non-mutating unauthorized consumption. Follow-up fixes cover migration digest preservation and fail-closed empty approvers. Current regression: `139 passed`. |
| 7. Temporary self-approval grant service | Not started | Next task. |
| 8. MCP grant and plan integration | Not started | |
| 9. Contextual OPS help | Not started | |
| 10. Multichannel package intake | Not started | |
| 11. Temporary grant API and UI | Not started | |
| 12. End-to-end coverage, docs and verification | Not started | |

## Implemented Commits

From plan base `69d5387`:

```text
ca9c6b4 refactor: remove built-in AI analysis subsystem
dba3370 fix: remove stale AI retirement checks
67a0ff3 refactor: remove AI analysis console pages
7084663 feat: add channel-neutral message context
a9c097f fix: prevent message context key collisions
de1118c feat: generalize tool token channel bindings
bc729f6 fix: enforce generic token channel policy
761310d fix: centralize tool conversation binding guard
b6270c9 fix: scope approval reads and audit token bindings
72f0484 fix: scope explicit approval list contexts
8450517 feat: bind routing tickets to generic channel context
b8b062b fix: stabilize routing ticket revisions
e9ae7a9 fix: enforce routing tickets across approval prepares
00d410d fix: complete Task 5 routing configuration
0939343 fix: close Task 5 final review gaps
d956817 fix: secure approval APIs and launcher env loading
a55dac2 fix: reenter POSIX launchers through bash
bf74f97 fix: preserve POSIX launcher execution modes
d0c1be8 fix: invoke dev setup scripts through bash
```

## Task 5 Final State

Implemented behavior includes:

- Routing tickets sign the complete generic `message_context` and target/revision data.
- Every approval prepare path verifies the full ticket server-side before creating a record; the server computes the persisted ticket digest.
- Routing revision is deterministic, includes service data used by resolution, and changes when effective approver policy changes.
- Ambiguous aliases fail as `AMBIGUOUS`; invalid or non-object ticket payloads fail closed.
- Structured approvers round-trip through system/service APIs and the system editor for Matrix, WeChat and Telegram.
- Approval APIs require authentication; configuration and maintenance mutations require admin permission.
- Approval signing has no public fallback. Startup preflight requires a strong `APPROVAL_SIGNING_KEY`.
- Root, dev, production, single-process and diagnostic launchers load `.env` without overriding exported environment values.
- POSIX launchers re-enter/delegate through `bash`, public launch scripts carry executable Git modes, package ZIP creation preserves those modes, and separated dev setup invokes helper scripts through `bash`.

The last fresh independent reviewer was interrupted before returning a final verdict. Its intended review scope was the final Task 5 state only. Start by running that review again; do not reopen already fixed historical findings unless the final code still demonstrates them.

## Verification Record

Most recent focused results reported by the implementation/review loops:

- Task 1 full Python suite: `539 passed, 5 failed`; the 5 failures matched the pre-existing baseline.
- Task 2 retirement tests: `12 passed`; frontend production build passed (`1771` modules).
- Task 3 focused tests: `43 passed`.
- Task 4 focused integration tests reached `126 passed`; final focused quality check passed.
- Task 5 routing/security focused tests reached `112 passed` before launcher-only fixes.
- Task 5 launcher suite after final fixes: `32 passed`; separated-mode preflight check passed.
- Frontend structured approver behavior tests: `4 passed`.
- Frontend production build passed after the Task 5 UI changes.
- Task 6 approval/plan regression: `137 passed`.
- `git diff --check` passed after every recorded task commit.

Known baseline issues that were not introduced here:

- Frontend typecheck has 10 existing errors. Task 2 and Task 5 reported no new errors.
- `frontend_route_check.js` has 4 existing failures for missing backup/report tools: `ops.create_backup`, `ops.restore_backup`, `ops.delete_backup`, and `ops.generate_report`.
- The original Python baseline had 5 existing frontend source-contract failures:
  - dashboard workbench clock hero copy
  - inspection profile preview/confirmation workflow (two contracts)
  - tool catalog inline detail/compact interaction
  - Tool Token Chinese permission labels
- `npm audit` reported 10 existing dependency findings: 1 low, 4 moderate and 5 high. No dependency upgrade was performed.

## Required Resume Sequence

1. Confirm repository state:

   ```powershell
   cd D:\code\ops-ai\.worktrees\temp-self-approval-multichannel
   git status --short
   git log -3 --oneline
   ```

2. Run a fresh focused Task 5 spec and quality review over `72f0484..d0c1be8`. At minimum exercise:

   ```powershell
   venv\Scripts\python.exe -m pytest tests\test_qclaw_routing.py tests\test_multichannel_routing_tools.py tests\test_task5_quality_review.py tests\test_task5_final_quality.py tests\test_task5_final_review.py -q
   npm --prefix frontend run test:approvers
   git diff --check
   ```

3. If Task 5 is approved, mark it complete and start Task 6 from the implementation plan. Task 6 must add generic channel fields and backfill for `AiActionApproval` and `ExecutionPlan`, make unauthorized consume attempts non-mutating, and retain legacy Matrix fields only as boundary aliases.

4. Continue the established gate for every task: implementer, specification review, code-quality review, then mark complete. Do not advance with an open finding.

## Security Invariants To Preserve

- Same channel, account instance and conversation for one approval chain.
- No cross-channel, cross-account or cross-conversation fallback.
- A configured approver policy with no current-channel match means no authorized approver.
- Generic Token fields are the policy source; legacy Matrix fields are boundary aliases only.
- Route tickets are verified by OPS, not trusted via caller-provided digests.
- Missing or weak approval signing keys fail startup preflight.
- Temporary self-approval, when implemented, applies only to the requester, exact test system/environment, allowed action set and grant lifetime.
- Production, DML, rollback and package cleanup remain outside temporary self-approval.

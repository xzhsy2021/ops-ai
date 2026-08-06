# Message Execution Plan Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将一条 Element/qclaw 消息触发的完整执行流程建模为一个独立执行计划，只产生一次人工审批，审批通过后按冻结的步骤清单顺序执行。

**Architecture:** 新增 `ExecutionPlan` 与 `ExecutionPlanStep` 两个持久化实体，使用不可变 manifest digest 做幂等和审批后完整性校验。新增 `PlanExecutor` 负责步骤状态、依赖、恢复和结果；保留现有单动作审批接口作为兼容路径，消息触发链路迁移到统一 `prepare_plan`/`execute_plan`。

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, SQLite migrations, pytest, existing MCP tool registry and OperationJob audit model.

---

### Task 1: Establish the plan data model and migration

**Files:**
- Modify: `app/db/models.py` near `AiActionApproval` and `OperationJob` models
- Modify: `app/db/migrations/runner.py` migration registry
- Test: `tests/test_execution_plan_migration.py`

**Step 1: Write the failing schema contract tests**

Add tests that create the schema, run migrations twice, and assert:

- `execution_plans` exists with plan identity, message binding, digest, approval lifecycle, status, risk, and execution result fields.
- `execution_plan_steps` exists with `plan_id`, stable `step_key`, ordering, dependency JSON, parameters JSON, status, attempt count, and result fields.
- `plan_digest` is indexed/unique as required for idempotent lookup.
- Running the migration twice does not fail or duplicate schema changes.

**Step 2: Run the focused tests to verify failure**

Run:

```powershell
pytest tests/test_execution_plan_migration.py -q
```

Expected: FAIL because the models and migration do not exist.

**Step 3: Implement the SQLAlchemy models**

Add `ExecutionPlan` and `ExecutionPlanStep` models with:

- UUID string primary keys.
- Relationship from plan to steps with deterministic ordering.
- Explicit indexes for `plan_digest`, `status`, `room_id`, `request_event_id`, and `plan_id`.
- JSON defaults that do not share mutable Python objects between rows.
- Timestamps compatible with existing naive UTC model conventions.

Keep plan-level approval fields on `ExecutionPlan`; do not reuse `AiActionApproval` for the new path.

**Step 4: Add an idempotent migration**

Register a migration in `app/db/migrations/runner.py` that creates both tables and indexes. Follow the existing migration runner conventions and ensure a partially applied migration can be safely rerun.

**Step 5: Run the focused tests**

Run:

```powershell
pytest tests/test_execution_plan_migration.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add app/db/models.py app/db/migrations/runner.py tests/test_execution_plan_migration.py
git commit -m "feat: add execution plan persistence"
```

### Task 2: Implement immutable plan manifest and idempotent plan service

**Files:**
- Create: `app/services/execution_plan.py`
- Test: `tests/test_execution_plan_service.py`

**Step 1: Write failing manifest and lifecycle tests**

Cover:

- Canonical manifest ordering makes equivalent target/step ordering produce the same digest.
- Changing environment, target, package digest, step parameters, step order, or routing revision changes the digest.
- `prepare()` creates one `PENDING_APPROVAL` plan and one plaintext short code.
- Repeating `prepare()` with the same digest returns the existing plan and an empty short code.
- A terminal plan is not reused for a new request.
- The stored plan snapshot contains the complete steps and frozen parameters.

**Step 2: Run tests to verify failure**

```powershell
pytest tests/test_execution_plan_service.py -q
```

Expected: FAIL because the service does not exist.

**Step 3: Implement canonicalization and digest calculation**

Add a stable canonical manifest containing:

- Original message binding.
- Route target and routing revision.
- Environment and targets.
- Package identity/digest when applicable.
- Ordered step list with `step_key`, action type, parameters, and dependencies.
- Plan execution policy.

Do not accept a caller-provided digest as authoritative; compute it server-side.

**Step 4: Implement plan creation**

Implement `ExecutionPlanService.prepare()` to:

- Validate required message and route bindings.
- Normalize step keys and reject duplicates.
- Validate dependency references and ordering.
- Resolve approvers using the existing token/system/service precedence.
- Store the approver snapshot on the plan.
- Generate and hash a one-time short code.
- Set the configured approval expiry.
- Insert plan and steps in one transaction.

**Step 5: Run focused tests**

```powershell
pytest tests/test_execution_plan_service.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add app/services/execution_plan.py tests/test_execution_plan_service.py
git commit -m "feat: add immutable execution plan service"
```

### Task 3: Implement plan approval consumption and rejection

**Files:**
- Modify: `app/services/execution_plan.py`
- Test: `tests/test_execution_plan_approval.py`

**Step 1: Write failing approval tests**

Cover:

- Correct code, room, event context, and authorized approver moves a plan to `APPROVED` or `RUNNING` exactly once.
- Wrong code leaves the plan pending.
- Wrong room, wrong request event, wrong content digest, and expired plan are rejected.
- Unauthorized Matrix user is rejected and recorded without executing steps.
- Two consume attempts result in one success.
- A changed stored/current manifest digest cannot execute.
- Rejecting a pending plan is terminal.

**Step 2: Run focused tests**

```powershell
pytest tests/test_execution_plan_approval.py -q
```

Expected: FAIL before implementation.

**Step 3: Implement atomic consume/reject operations**

Use a conditional update on `status == PENDING_APPROVAL` and `consumed_at IS NULL`. Preserve the existing constant-time short-code verification and approver whitelist behavior. Bind approval execution to the original request event and content digest, not only the room.

**Step 4: Run focused tests**

```powershell
pytest tests/test_execution_plan_approval.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add app/services/execution_plan.py tests/test_execution_plan_approval.py
git commit -m "feat: add one-time execution plan approval"
```

### Task 4: Add step executor registry and sequential plan execution

**Files:**
- Create: `app/services/plan_executor.py`
- Test: `tests/test_plan_executor.py`
- Inspect/modify as needed: existing action executors in `app/services/approval_executor.py`

**Step 1: Write failing executor tests**

Cover:

- Steps run in declared order.
- Dependencies block steps until prerequisites succeed.
- A failed prerequisite marks dependent steps `SKIPPED`.
- Each step transitions `PENDING -> RUNNING -> SUCCEEDED/FAILED`.
- The plan becomes `SUCCEEDED` only when all required steps succeed.
- Partial success becomes `PARTIAL_FAILED` where independent steps completed.
- Unknown step type fails the plan without executing later dependent steps.
- Re-running the executor does not repeat successful idempotent steps.
- A plan already `RUNNING`/terminal is handled safely.

**Step 2: Run focused tests**

```powershell
pytest tests/test_plan_executor.py -q
```

Expected: FAIL before implementation.

**Step 3: Implement the executor registry**

Create a registry mapping stable step action types to callables. Adapt existing release, rollback, DML, package cleanup, and service-control implementations behind the registry instead of calling old approval preparation tools recursively.

Each handler receives the frozen step parameters plus plan context and returns a serializable result. The executor must create/link one plan-level OperationJob and step-level audit records without issuing a second approval.

**Step 4: Implement recovery and idempotency**

Before executing a step, inspect its persisted status. Successful steps are not re-run. A `RUNNING` step from a crashed process must use its step idempotency key and existing domain records to avoid duplicate side effects; if safe recovery cannot be proven, mark the plan failed and require a new plan rather than guessing.

**Step 5: Run focused tests**

```powershell
pytest tests/test_plan_executor.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add app/services/plan_executor.py app/services/approval_executor.py tests/test_plan_executor.py
git commit -m "feat: execute approved plans sequentially"
```

### Task 5: Add MCP plan preparation and execution tools

**Files:**
- Modify: `app/services/tool_adapters/approval_tools.py`
- Test: `tests/test_execution_plan_tools_contract.py`

**Step 1: Write failing MCP contract tests**

Assert that:

- `ops.approval.prepare_plan` is registered with required message, route, plan, and step fields.
- `ops.approval.execute_plan` is registered with plan ID, short code, approver, room, and approval event.
- `prepare_plan` enforces token room binding and captures approvers.
- `execute_plan` consumes exactly once and invokes `PlanExecutor`.
- The returned response exposes one plan ID, one short code, plan status, and step summary.
- No plan tool calls an old `prepare_*` approval tool internally.

**Step 2: Run tests to verify failure**

```powershell
pytest tests/test_execution_plan_tools_contract.py -q
```

Expected: FAIL because the tools are not registered.

**Step 3: Implement `prepare_plan`**

Validate and normalize the complete step list server-side. Reuse `_lookup_approvers`, `enforce_room_binding`, and routing-ticket verification. Return the existing plan without a new code for duplicate pending manifests.

**Step 4: Implement `execute_plan`**

Use the plan service consume method, then call `PlanExecutor`. Return plan and step outcomes. Preserve safe error messages that do not expose stored approval hashes or sensitive command parameters.

**Step 5: Run focused tests**

```powershell
pytest tests/test_execution_plan_tools_contract.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add app/services/tool_adapters/approval_tools.py tests/test_execution_plan_tools_contract.py
git commit -m "feat: expose one-approval plan tools"
```

### Task 6: Migrate the message-triggered qclaw flow to plan creation

**Files:**
- Modify: message/qclaw integration entrypoint identified during implementation; likely `app/services/tool_adapters/approval_tools.py` and related qclaw adapter
- Modify: `app/services/qclaw_routing.py` only if route result needs plan metadata
- Test: `tests/test_qclaw_message_execution_plan.py`

**Step 1: Write failing end-to-end contract tests**

Model one complete message that resolves to a multi-step plan and assert:

- One route decision is produced.
- One execution plan is created.
- Exactly one pending approval and one short code exist.
- Approval consumes once.
- Multiple declared steps execute in order without new approval records.
- A duplicate message/plan request is idempotent.

Also test that changing the environment, package digest, target server, or step list requires a new plan and approval.

**Step 2: Run focused tests**

```powershell
pytest tests/test_qclaw_message_execution_plan.py -q
```

Expected: FAIL until the message trigger uses the plan path.

**Step 3: Replace step-by-step approval calls**

At the message-triggered orchestration boundary, build the complete plan before asking for approval. Do not call `prepare_service_control` or other old approval tools for each plan step. Keep the old tools available for external legacy callers.

**Step 4: Run focused tests**

```powershell
pytest tests/test_qclaw_message_execution_plan.py -q
```

Expected: PASS with exactly one approval per message plan.

**Step 5: Commit**

```powershell
git add app/services tests/test_qclaw_message_execution_plan.py
git commit -m "feat: route qclaw messages through one approval plan"
```

### Task 7: Add plan management API and audit visibility

**Files:**
- Create or modify: `app/api/execution_plans.py`
- Modify: API router registration file where `app/api/approvals.py` is included
- Test: `tests/test_execution_plan_api.py`

**Step 1: Write failing API tests**

Cover list/detail/reject endpoints, status and action filters, plan steps in detail response, and authorization requirements.

**Step 2: Implement read/reject endpoints**

Expose plan-level status, digest, message binding summary, target/environment, approval metadata, and ordered step results. Do not expose approval code hashes or secrets. Keep old `/api/v2/approvals` endpoints unchanged for compatibility.

**Step 3: Run focused tests**

```powershell
pytest tests/test_execution_plan_api.py -q
```

Expected: PASS.

**Step 4: Commit**

```powershell
git add app/api tests/test_execution_plan_api.py
git commit -m "feat: add execution plan management API"
```

### Task 8: Full regression and migration verification

**Files:**
- Modify: documentation/runbook files only if behavior or operational recovery needs documenting
- Test: existing approval, routing, migration, and tool contract suites

**Step 1: Run focused suites**

```powershell
pytest tests/test_execution_plan_migration.py tests/test_execution_plan_service.py tests/test_execution_plan_approval.py tests/test_plan_executor.py tests/test_execution_plan_tools_contract.py tests/test_qclaw_message_execution_plan.py tests/test_execution_plan_api.py -q
```

Expected: all new tests pass.

**Step 2: Run existing approval/routing regressions**

```powershell
pytest tests/test_qclaw_approval_service.py tests/test_qclaw_approval_executor.py tests/test_qclaw_routing.py tests/test_qclaw_end_to_end_contract.py tests/test_tool_token_approver_binding_contract.py -q
```

Expected: existing behavior remains green, except any explicitly documented baseline failures.

**Step 3: Run the complete backend test suite**

```powershell
pytest -q
```

Expected: no new failures attributable to execution plans.

**Step 4: Build the frontend and typecheck**

```powershell
npm run build
npm run typecheck
```

Expected: production build succeeds. Existing typecheck baseline errors must be recorded separately and no new errors may be introduced.

**Step 5: Verify migration idempotency against the real local database**

Run the application migration path twice and query `schema_migrations`, `execution_plans`, and `execution_plan_steps`. Confirm no duplicate migration records or schema errors.

**Step 6: Commit documentation and final test updates**

```powershell
git add docs/plans tests app services frontend
git commit -m "test: verify one-approval message execution flow"
```

# OPS Temporary Self-Approval and Multichannel Protocol Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add time-boxed self-approval for test updates, make all QClaw-triggered OPS workflows channel-neutral for Matrix/WeChat/Telegram, add contextual help, and remove the unused built-in AI analysis subsystem.

**Architecture:** QClaw remains responsible for channel adapters, attachment download, message interpretation, and outbound replies. OPS receives one normalized `message_context`, binds it into routing tickets, files, approvals, grants, plans, and audit records, then enforces database-backed policy before execution. Temporary approval is a separate scoped grant layered over the existing approver policy; it never mutates the configured approver list or bypasses plan approval.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, SQLite-compatible migrations, pytest, React 18, TypeScript, Vite.

---

## Execution Rules

- Start in a dedicated worktree from design commit `2a33a48`; do not use the current dirty worktree.
- Use `@test-driven-development` for every behavior change.
- Use `@security-review` before completing Tasks 5-9.
- Use `@verification-before-completion` before the final commit.
- Keep old Matrix request fields only in one API compatibility normalizer. New services and database writes use the generic channel model.
- Stage only files listed by the current task and commit after each task.

### Task 1: Remove the backend AI analysis subsystem

**Files:**
- Create: `tests/test_ai_analysis_retirement.py`
- Modify: `app/services/tool_registry.py`
- Modify: `app/services/mcp_capability_service.py`
- Modify: `app/mcp/server.py`
- Modify: `app/services/report_center.py`
- Modify: `app/api/system.py`
- Modify: `main.py`
- Modify: `app/db/models.py`
- Modify: `app/db/migrations/runner.py`
- Modify: `app/services/tool_adapters/log_tools.py`
- Create: `app/services/sensitive_data.py`
- Delete: `app/api/ai_analysis.py`
- Delete: `app/services/ai_analysis.py`
- Delete: `app/services/ai_diagnostics.py`
- Delete: `app/services/ai_evidence.py`
- Delete: `app/services/ai_workflows.py`
- Delete: `app/services/tool_adapters/ai_tools.py`
- Delete: `app/services/tool_adapters/ai_analysis_tools.py`
- Delete: `tests/test_iter37_ai_diagnostics_contract.py`
- Modify: `tests/test_mcp_contract_sync.py`
- Modify: `tests/test_iter39_report_center_contract.py`

**Step 1: Write retirement contract tests**

Add assertions that the runtime no longer exposes AI analysis tools, resources, report types, endpoints, models, or tables:

```python
def test_ai_analysis_tools_and_resources_are_absent(db):
    register_builtin_tools()
    ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"])
    catalog = registry.list_tools(db, ctx, include_disabled=True, limit=500)
    names = {tool["name"] for tool in catalog["tools"]}
    assert "ops.analyze_diagnostics" not in names
    assert not any(name.startswith("ops.ai.") for name in names)
    assert "ops://ai-diagnostics" not in {x["uri"] for x in mcp_resource_items()}
    assert "ops://ai-workflows" not in {x["uri"] for x in mcp_resource_items()}
    assert "ai_analysis" not in REPORT_TYPES
    assert "ai_diagnostics" not in REPORT_TYPES


def test_ai_analysis_tables_are_dropped(migrated_engine):
    names = set(inspect(migrated_engine).get_table_names())
    assert "ai_analysis_runs" not in names
    assert "ai_analysis_findings" not in names
```

Also assert `GET /api/v2/system/ai-diagnostics` and `/api/v2/ai/analysis` are not mounted.

**Step 2: Run the focused tests and verify failure**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_ai_analysis_retirement.py tests\test_mcp_contract_sync.py tests\test_iter39_report_center_contract.py -q
```

Expected: FAIL because the tools, resources, report types, routes, models, and tables still exist.

**Step 3: Remove the subsystem**

- Remove AI adapter imports from `register_builtin_tools()`.
- Remove AI analysis resources and prompts from both MCP catalog implementations.
- Remove `ai_analysis` and `ai_diagnostics` from `REPORT_TYPES`, `_payload_for_report()`, `_format_summary()`, and report rendering branches.
- Remove `/system/ai-diagnostics` and the `/api/v2/ai/analysis` router registration.
- Remove `AiAnalysisRun` and `AiAnalysisFinding` ORM classes.
- Add forward migration `084_001_drop_ai_analysis_tables`, dropping findings before runs.
- Move the generic `mask_sensitive()` helper from `app/services/ai_evidence.py` to `app/services/sensitive_data.py`, update `log_tools.py`, then delete `ai_evidence.py`.
- Keep `AiActionApproval`; it is an execution approval entity, not an analysis record.

The migration must be idempotent:

```python
def retire_ai_analysis_tables(engine) -> list[str]:
    dropped = []
    with engine.begin() as conn:
        names = set(inspect(conn).get_table_names())
        for table in ("ai_analysis_findings", "ai_analysis_runs"):
            if table in names:
                conn.execute(text(f"DROP TABLE {table}"))
                dropped.append(table)
    return dropped
```

Do not interpolate user input into this helper; table names are fixed constants.

**Step 4: Run focused and registry tests**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_ai_analysis_retirement.py tests\test_mcp_contract_sync.py tests\test_iter39_report_center_contract.py tests\test_tool_permission_matrix_contract.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add app tests
git commit -m "refactor: remove built-in AI analysis subsystem"
```

### Task 2: Remove the frontend AI analysis surfaces

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/routes.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/SystemDiagnosticsPage.tsx`
- Modify: `frontend/src/pages/ReportCenterPage.tsx`
- Modify: `frontend/src/pages/AuditLogPage.tsx`
- Modify: `frontend/src/pages/ToolAccessPage.tsx`
- Delete: `frontend/src/pages/AiWorkflowsPage.tsx`
- Delete: `frontend/src/pages/AiAnalysisPage.tsx`
- Delete: `frontend/src/pages/AiAnalysisDetailPage.tsx`
- Delete: `frontend/src/components/AiEvidenceView.tsx`
- Create: `tests/test_frontend_ai_analysis_retirement.py`

**Step 1: Write the source contract test**

```python
def test_frontend_has_no_ai_analysis_routes_or_clients():
    app = Path("frontend/src/App.tsx").read_text(encoding="utf-8")
    routes = Path("frontend/src/routes.ts").read_text(encoding="utf-8")
    api = Path("frontend/src/api.ts").read_text(encoding="utf-8")
    combined = app + routes + api
    assert "AiAnalysis" not in combined
    assert "AiWorkflows" not in combined
    assert "/ai/analysis" not in combined
    assert "/system/ai-diagnostics" not in combined
```

**Step 2: Verify the test fails**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_frontend_ai_analysis_retirement.py -q
```

Expected: FAIL on existing routes and API clients.

**Step 3: Remove pages and stale wording**

- Remove lazy imports and routes for AI workflows and analysis.
- Remove the AI diagnostics panel and request state from `SystemDiagnosticsPage` while preserving raw diagnostics.
- Remove `aiAnalysis` and `system.aiDiagnostics` API functions.
- Remove AI report filters and descriptions.
- Change the navigation label description from AI analysis to MCP tools, approvals, and policy.
- Keep MCP tool metadata and external Agent integration UI.

**Step 4: Verify frontend and source contracts**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_frontend_ai_analysis_retirement.py -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: all commands PASS.

**Step 5: Commit**

```powershell
git add frontend tests/test_frontend_ai_analysis_retirement.py
git commit -m "refactor: remove AI analysis console pages"
```

### Task 3: Add the generic channel message context

**Files:**
- Create: `app/services/message_context.py`
- Create: `tests/test_message_context.py`
- Modify: `app/services/tool_context.py`
- Modify: `app/services/job_service.py`

**Step 1: Write failing value-object tests**

Parameterize Matrix, WeChat, and Telegram:

```python
@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_message_context_builds_stable_keys(channel):
    ctx = MessageContext.from_dict({
        "channel": channel,
        "channel_account_id": "primary",
        "conversation_id": "room-1",
        "message_id": "message-1",
        "sender_id": "user-1",
        "content_sha256": "a" * 64,
    })
    assert ctx.actor_key == f"{channel}:primary:user-1"
    assert ctx.conversation_key == f"{channel}:primary:room-1"


def test_legacy_matrix_fields_normalize_at_boundary_only():
    ctx = normalize_message_context({
        "room_id": "!ops:example.org",
        "request_event_id": "$event",
        "sender_matrix_id": "@alice:example.org",
        "content_sha256": "a" * 64,
    })
    assert ctx.channel == "matrix"
    assert ctx.channel_account_id == "default"
```

Add negative tests for unsupported channels and missing stable identity fields.

**Step 2: Run and verify failure**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_message_context.py -q
```

Expected: FAIL because `MessageContext` does not exist.

**Step 3: Implement the value object and schema helper**

Implement:

```python
SUPPORTED_CHANNELS = frozenset({"matrix", "wechat", "telegram"})

@dataclass(frozen=True)
class MessageContext:
    channel: str
    channel_account_id: str
    conversation_id: str
    message_id: str
    sender_id: str
    content_sha256: str

    @property
    def actor_key(self) -> str:
        return f"{self.channel}:{self.channel_account_id}:{self.sender_id}"

    @property
    def conversation_key(self) -> str:
        return f"{self.channel}:{self.channel_account_id}:{self.conversation_id}"
```

Expose `message_context_schema()`, `normalize_message_context()`, `normalize_identity()`, and `normalize_conversation_binding()`. Add `channel_bindings` and `approver_identities` to `ToolContext`; keep legacy fields only until Task 4 completes migration.

**Step 4: Run tests**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_message_context.py tests\test_iter35_job_center_contract.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add app/services/message_context.py app/services/tool_context.py app/services/job_service.py tests/test_message_context.py
git commit -m "feat: add channel-neutral message context"
```

### Task 4: Migrate Tool Token channel and approver bindings

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/db/migrations/runner.py`
- Modify: `app/services/tool_token.py`
- Modify: `app/api/tools.py`
- Modify: `app/services/tool_context.py`
- Create: `tests/test_tool_token_channel_bindings.py`
- Modify: `tests/test_tool_token_bound_rooms_contract.py`
- Modify: `tests/test_tool_token_approver_binding_contract.py`

**Step 1: Write migration and API compatibility tests**

Test that legacy Matrix rows backfill to:

```json
{
  "channel": "matrix",
  "channel_account_id": "default",
  "conversation_id": "!ops:example.org"
}
```

and approvers backfill to:

```json
{
  "channel": "matrix",
  "channel_account_id": "default",
  "sender_id": "@alice:example.org"
}
```

Test that new create/update payloads round-trip `channel_bindings` and `approver_identities`, while legacy `bound_room_ids` and `approver_matrix_ids` are accepted and normalized at the API boundary.

**Step 2: Verify tests fail**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_tool_token_channel_bindings.py tests\test_tool_token_bound_rooms_contract.py tests\test_tool_token_approver_binding_contract.py -q
```

Expected: FAIL because generic columns and payloads do not exist.

**Step 3: Add database columns and one-time backfill**

Add JSON columns:

```python
channel_bindings = Column(JSON, default=list)
approver_identities = Column(JSON, default=list)
```

Add migrations `084_002_tool_token_channel_bindings` and `084_003_tool_token_approver_identities`, followed by a Python backfill that converts legacy Matrix lists only when the new field is empty. Mark the backfill with its own schema migration version so it cannot run twice.

**Step 4: Switch runtime reads to generic fields**

- Replace `enforce_room_binding()` with `enforce_conversation_binding()`.
- Populate generic bindings in `ToolContext`.
- Keep legacy request/response aliases for Matrix clients, derived from generic fields rather than read as the policy source.
- Treat an explicitly configured approver list with no identity matching the current channel as “no authorized approver,” not “anyone may approve.”

**Step 5: Run tests**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_tool_token_channel_bindings.py tests\test_tool_token_bound_rooms_contract.py tests\test_tool_token_approver_binding_contract.py tests\test_tool_token_templates_contract.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add app/db app/services/tool_token.py app/services/tool_context.py app/api/tools.py tests
git commit -m "feat: generalize tool token channel bindings"
```

### Task 5: Generalize routing tickets and configured approvers

**Files:**
- Modify: `app/services/qclaw_routing.py`
- Modify: `app/services/tool_adapters/approval_tools.py`
- Modify: `app/api/approvals.py`
- Modify: `app/config/systems.py`
- Modify: `tests/test_qclaw_routing.py`
- Create: `tests/test_multichannel_routing_tools.py`

**Step 1: Write failing three-channel routing tests**

```python
@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_routing_ticket_binds_full_message_context(channel):
    context = message_context(channel)
    ticket = issue_ticket(context, "crypto-trader", None, "revision")
    assert verify_ticket(
        ticket.ticket,
        expected_message_context=context,
        expected_system_name="crypto-trader",
        expected_service_name=None,
        expected_revision="revision",
    )


def test_ticket_cannot_cross_channel_or_conversation():
    assert verify_ticket(ticket, expected_message_context=other_context, ...) is False
```

Test structured configured approvers and legacy Matrix string conversion.

**Step 2: Verify tests fail**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_qclaw_routing.py tests\test_multichannel_routing_tools.py -q
```

Expected: FAIL on the Matrix-only ticket contract.

**Step 3: Replace Matrix-specific ticket payload fields**

The signed ticket payload must contain:

```python
payload = {
    "message_context": context.to_dict(),
    "system_name": system_name,
    "service_name": service_name,
    "routing_config_revision": revision,
    "issued_at": issued_at.isoformat(),
    "expires_at": expires_at.isoformat(),
    "nonce": nonce,
}
```

Update `ops.routing.resolve_message_target` to accept `message_context`, enforce token conversation binding, and return generic approver actor keys for the current channel. Preserve legacy Matrix input normalization in this tool handler only.

Change `MessageRoutingConfig.approvers` to structured identities; accept legacy strings during migration and normalize them to Matrix/default objects before saving to the database.

**Step 4: Run routing tests**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_qclaw_routing.py tests\test_multichannel_routing_tools.py tests\test_qclaw_end_to_end_contract.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add app/services/qclaw_routing.py app/services/tool_adapters/approval_tools.py app/api/approvals.py app/config/systems.py tests
git commit -m "feat: bind routing tickets to generic channel context"
```

### Task 6: Generalize action approvals and execution plans

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/db/migrations/runner.py`
- Modify: `app/services/action_approval.py`
- Modify: `app/services/execution_plan.py`
- Modify: `app/services/plan_executor.py`
- Modify: `app/api/approvals.py`
- Modify: `app/api/execution_plans.py`
- Modify: `tests/test_qclaw_approval_service.py`
- Modify: `tests/test_execution_plan_service.py`
- Modify: `tests/test_execution_plan_approval.py`
- Create: `tests/test_multichannel_approval_contract.py`

**Step 1: Write failing lifecycle tests**

Cover:

- `requested_by` is the normalized source actor;
- all three channels can prepare and consume in the same conversation;
- cross-channel, cross-account, cross-conversation, and another sender fail;
- unauthorized attempts leave the legitimate request `PENDING_APPROVAL`;
- existing Matrix parameters still normalize at the API boundary;
- empty effective original approvers fail closed during prepare.

```python
def test_unauthorized_consume_does_not_reject_pending_plan(db):
    plan, code = prepare_plan(db, authorized=["matrix:default:@alice:example.org"])
    assert consume(plan.id, code, actor="matrix:default:@mallory:example.org") is None
    db.refresh(plan)
    assert plan.status == "PENDING_APPROVAL"
```

**Step 2: Verify tests fail**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_qclaw_approval_service.py tests\test_execution_plan_service.py tests\test_execution_plan_approval.py tests\test_multichannel_approval_contract.py -q
```

Expected: FAIL because models and services are Matrix-specific and unauthorized attempts currently reject plans.

**Step 3: Add generic persisted fields and backfill**

Add generic channel fields to `AiActionApproval` and `ExecutionPlan`:

```text
channel, channel_account_id, conversation_id,
request_message_id, request_sender_id, approval_message_id,
authorized_identities
```

Add `temporary_grant_id` and retain `requested_by`, `approved_by`, and `rejected_by` as actor keys. Add indexes on `(channel, channel_account_id, conversation_id, request_message_id)`.

Backfill existing Matrix rows from legacy columns. Keep legacy columns physically during this release for data safety, but stop reading them in core services after backfill.

**Step 4: Refactor digests and lifecycle methods**

- `compute_action_digest()` and `compute_plan_digest()` accept `MessageContext` and include its canonical dictionary.
- `prepare()` writes the source actor and generic context.
- `consume()` accepts `approval_context`, compares channel/account/conversation, checks the actor key, expiry, and digest, then performs the atomic update.
- Unauthorized attempts call the audit writer but do not mutate the request status.
- API responses expose `message_context`; legacy Matrix fields are compatibility aliases only.

**Step 5: Run lifecycle and executor tests**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_qclaw_approval_service.py tests\test_execution_plan_service.py tests\test_execution_plan_approval.py tests\test_multichannel_approval_contract.py tests\test_plan_executor.py tests\test_execution_plan_api.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add app/db app/services/action_approval.py app/services/execution_plan.py app/services/plan_executor.py app/api/approvals.py app/api/execution_plans.py tests
git commit -m "refactor: make approvals channel-neutral"
```

### Task 7: Add the temporary self-approval grant service

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/db/migrations/runner.py`
- Create: `app/services/temporary_approval.py`
- Create: `tests/test_temporary_approval_service.py`
- Create: `tests/test_temporary_approval_migration.py`

**Step 1: Write failing grant lifecycle tests**

Cover:

- default 1 day, day/week units, maximum 4 weeks;
- only `SystemEnvironment.category == "test"` can be granted;
- beneficiary is a fixed actor key;
- only configured original approvers can confirm/revoke;
- confirmation code is valid for 15 minutes and consumed once;
- same channel/account/conversation is mandatory;
- a duplicate active scope does not stack;
- no background worker is required; `get_active_grant()` treats past `expires_at` as expired.

```python
def test_grant_is_scoped_to_fixed_requester(db):
    grant, code = request_grant(..., beneficiary="wechat:primary:user-a")
    confirm_grant(grant.id, code, actor="wechat:primary:owner", context=same_room)
    assert service.is_self_approval_allowed(
        actor_key="wechat:primary:user-a",
        system_name="crypto-trader",
        environment="test",
        action_types=["FILE_UPLOAD", "SERVICE_CONTROL", "HEALTH_CHECK"],
    )
    assert not service.is_self_approval_allowed(actor_key="wechat:primary:user-b", ...)
```

**Step 2: Verify tests fail**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_temporary_approval_service.py tests\test_temporary_approval_migration.py -q
```

Expected: FAIL because the model and service do not exist.

**Step 3: Add the table and service**

Implement `TemporaryApprovalGrant` with the fields and statuses from the approved design. Add indexes for status, expiry, beneficiary, and resource scope.

Define the fixed self-approval allowlist:

```python
TEMPORARY_SELF_APPROVAL_ACTIONS = frozenset({
    "FILE_UPLOAD",
    "RELEASE",
    "SERVICE_CONTROL",
    "HEALTH_CHECK",
})
```

Explicitly reject `DML`, `ROLLBACK`, `PACKAGE_DELETE`, `PACKAGE_CLEANUP`, unknown actions, and any non-test environment.

Methods:

```text
request(...)
confirm(...)
revoke(...)
get_active_grant(...)
can_self_approve_plan(...)
expire_if_needed(...)
list(...)
```

Use PBKDF2 confirmation-code hashing consistent with plan approvals and include the immutable request digest.

**Step 4: Run service tests**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_temporary_approval_service.py tests\test_temporary_approval_migration.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add app/db app/services/temporary_approval.py tests/test_temporary_approval_service.py tests/test_temporary_approval_migration.py
git commit -m "feat: add scoped temporary self-approval grants"
```

### Task 8: Integrate temporary grants with MCP approvals and plans

**Files:**
- Modify: `app/services/tool_adapters/approval_tools.py`
- Modify: `app/services/execution_plan.py`
- Modify: `app/services/action_approval.py`
- Modify: `app/services/plan_executor.py`
- Modify: `app/services/tool_registry.py`
- Modify: `app/mcp/server.py`
- Create: `tests/test_temporary_approval_tools.py`
- Create: `tests/test_temporary_self_approval_plan.py`

**Step 1: Write failing MCP and plan-policy tests**

Test the single tool with operations `request`, `confirm`, and `revoke`. Test two initiation paths: beneficiary requests self access; original approver requests access for another fixed identity. Both remain `PENDING` until the original approver confirms.

Test plan behavior:

- active grant adds the beneficiary self-approval path and records `temporary_grant_id`;
- original approvers remain authorized but are not marked as notification targets;
- package upload and all deployment steps remain one immutable plan and one code;
- grant expiry/revocation prevents beneficiary consumption;
- original approver can still consume the existing plan;
- changing a step or package hash invalidates the digest.

**Step 2: Verify tests fail**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_temporary_approval_tools.py tests\test_temporary_self_approval_plan.py -q
```

Expected: FAIL because the tool and plan integration do not exist.

**Step 3: Register the minimal management tool**

Register:

```text
ops.approval.temporary_access
```

Input includes `operation`, `message_context`, request or grant ID, duration, reason, system, environment, beneficiary identity, short code, and revoke reason as applicable. Keep `additionalProperties: false` and validate operation-specific required fields in the handler.

The tool handler must derive the acting identity from `message_context.sender_id`, resolve original approvers from database/token policy, and never trust a username embedded in message text.

**Step 4: Integrate effective approval policy**

At plan prepare:

```python
effective = approval_policy.resolve(
    original_approvers=original_approvers,
    requested_by=context.actor_key,
    system_name=system_name,
    environment=environment,
    action_types=[step["action_type"] for step in steps],
    context=context,
)
```

Persist original approvers, optional temporary grant, self-approval actor, allowed action snapshot, and notification targets separately in `policy`.

At consume, if the actor uses the temporary path, re-read and validate the grant. If the actor is an original approver, do not require the temporary grant to remain active.

**Step 5: Run approval and package regression tests**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_temporary_approval_tools.py tests\test_temporary_self_approval_plan.py tests\test_execution_plan_tools_contract.py tests\test_file_upload_approval.py tests\test_plan_executor.py -q
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add app/services app/mcp/server.py tests
git commit -m "feat: apply temporary grants to execution plan approval"
```

### Task 9: Add contextual OPS help

**Files:**
- Create: `app/services/ops_help.py`
- Create: `app/services/tool_adapters/help_tools.py`
- Modify: `app/services/tool_registry.py`
- Modify: `app/mcp/server.py`
- Create: `tests/test_ops_help_tool.py`

**Step 1: Write failing help tests**

Cover:

- `帮助` returns only capabilities available in current token/channel/project context;
- `帮助 全部` includes unavailable capabilities with `available`, `requires_authorization`, or `forbidden` status;
- topic filters for temporary approval, package upload, deployment, and a system/environment;
- missing project mapping returns required information instead of creating a plan;
- output never contains token values, credentials, private keys, approval-code hashes, or raw secret-bearing commands;
- AI analysis tools never appear.

**Step 2: Verify tests fail**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_ops_help_tool.py -q
```

Expected: FAIL because `ops.help.query` is not registered.

**Step 3: Implement help from live facts**

Register read-only `ops.help.query` with:

```json
{
  "topic": "临时审批",
  "include_all": false,
  "system_name": "crypto-trader",
  "environment": "test",
  "message_context": {}
}
```

Build results from `registry.list_tools(..., include_disabled=True)`, tool permission summaries, database system/environment rows, original approver policy, and active temporary grants. Store only concise examples in tool metadata; do not create another config store.

**Step 4: Run help and registry tests**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_ops_help_tool.py tests\test_mcp_contract_sync.py tests\test_tool_permission_matrix_contract.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add app/services/ops_help.py app/services/tool_adapters/help_tools.py app/services/tool_registry.py app/mcp/server.py tests/test_ops_help_tool.py
git commit -m "feat: add contextual OPS help tool"
```

### Task 10: Bind channel attachments to package intake and plans

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/db/migrations/runner.py`
- Modify: `app/api/tools.py`
- Modify: `app/services/package_intake.py`
- Modify: `app/services/tool_adapters/approval_tools.py`
- Modify: `tests/test_qclaw_package_intake.py`
- Modify: `tests/test_file_upload_approval.py`
- Create: `tests/test_multichannel_package_intake.py`

**Step 1: Write failing package binding tests**

For every channel, upload a fake attachment with a JSON `message_context` multipart field and assert:

- token conversation binding is enforced;
- actual SHA-256 equals the declared package SHA-256;
- package metadata stores channel/account/conversation/message/sender and source-message key;
- retrying the same message and same hash reuses the existing package;
- same message with a different hash returns 409;
- the later `FILE_UPLOAD` step references the File Center package and stays inside the single execution plan approval.

**Step 2: Verify tests fail**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_multichannel_package_intake.py tests\test_qclaw_package_intake.py tests\test_file_upload_approval.py -q
```

Expected: FAIL on Matrix-only multipart fields.

**Step 3: Add generic package source metadata**

Add `source_context` JSON and indexed `source_message_key` to `DeployPackage`. Compute the key from canonical channel context plus package SHA-256 so one message can safely contain multiple attachments.

Change `/api/v2/tools/packages/upload` to accept `message_context` as a JSON multipart field. Normalize legacy Matrix form fields only at this endpoint. QClaw supplies locally downloaded bytes; OPS continues to own validation, File Center persistence, and plan binding.

Do not add channel credentials or channel download clients to OPS.

**Step 4: Run package tests**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_multichannel_package_intake.py tests\test_qclaw_package_intake.py tests\test_file_upload_approval.py tests\test_temporary_self_approval_plan.py -q
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add app/db app/api/tools.py app/services/package_intake.py app/services/tool_adapters/approval_tools.py tests
git commit -m "feat: bind channel attachments to approved packages"
```

### Task 11: Add temporary grant APIs and management UI

**Files:**
- Create: `app/api/temporary_approvals.py`
- Modify: `main.py`
- Modify: `app/api/tools.py`
- Create: `tests/test_temporary_approval_api.py`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/ToolAccessPage.tsx`
- Modify: `frontend/src/pages/tools/ToolTokenPanel.tsx`
- Create: `frontend/src/pages/tools/TemporaryApprovalPanel.tsx`
- Create: `tests/test_frontend_temporary_approval_contract.py`

**Step 1: Write failing API and UI contract tests**

API tests cover list/detail, admin confirmation fallback, revoke, exact expiry display, and secret omission. Source contracts assert the Tool Token editor uses generic channel bindings and that the temporary approval panel is mounted once without interval polling.

```python
def test_grant_detail_never_exposes_confirmation_hash(client):
    body = client.get(f"/api/v2/temporary-approvals/{grant_id}").json()
    assert "confirmation_code_hash" not in body


def test_temporary_panel_has_no_polling_loop():
    source = Path("frontend/src/pages/tools/TemporaryApprovalPanel.tsx").read_text("utf-8")
    assert "setInterval" not in source
```

**Step 2: Verify tests fail**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_temporary_approval_api.py tests\test_frontend_temporary_approval_contract.py -q
```

Expected: FAIL because API and UI do not exist.

**Step 3: Implement the admin fallback API**

Add:

```text
GET  /api/v2/temporary-approvals
GET  /api/v2/temporary-approvals/{id}
POST /api/v2/temporary-approvals/{id}/confirm
POST /api/v2/temporary-approvals/{id}/revoke
```

Require admin session for confirm/revoke. Persist Web actors as `web:local:<username>` and audit them as high-privilege fallback actions. Require an explicit confirmation phrase containing the grant short ID; never return the code hash.

**Step 4: Implement the compact management panel**

- Add tabs or a status filter for pending, active, revoked, and expired grants.
- Display beneficiary, channel, conversation, system/environment, reason, approver, and exact expiry.
- Use a confirmation modal for approve and revoke reason input for revoke.
- Fetch on page load and after user actions only; no polling.
- Update Tool Token editor from Matrix-only room/user chips to rows containing channel, account, conversation or sender.
- Preserve the existing stable modal portal pattern to avoid reintroducing the token-editor flicker.

**Step 5: Run API, type, and build checks**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_temporary_approval_api.py tests\test_frontend_temporary_approval_contract.py tests\test_frontend_tooling_contract.py -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add app/api/temporary_approvals.py app/api/tools.py main.py frontend tests
git commit -m "feat: add temporary approval management UI"
```

### Task 12: Add end-to-end multichannel coverage and integration documentation

**Files:**
- Create: `tests/test_multichannel_qclaw_end_to_end.py`
- Modify: `tests/test_qclaw_end_to_end_contract.py`
- Modify: `tests/test_mcp_contract_sync.py`
- Create: `docs/qclaw-channel-approval-integration.md`
- Modify: `docs/qclaw-element-approval-integration.md`
- Modify: `docs/runbooks/mcp-capability-matrix.md`
- Modify: `README.md`

**Step 1: Write the end-to-end acceptance tests**

Parameterize the same workflow for Matrix, WeChat, and Telegram:

```python
@pytest.mark.parametrize("channel", ["matrix", "wechat", "telegram"])
def test_channel_update_uses_one_self_approval(channel, db, client):
    context = message_context(channel)
    grant = activate_test_grant(db, context)
    package = upload_channel_package(client, context)
    plan, code = prepare_batch_plan(db, context, package)
    approved = consume_as_requester(db, plan, code, context)
    assert approved.temporary_grant_id == grant.id
    assert [s.action_type for s in approved.steps].count("FILE_UPLOAD") == 1
```

Include help discovery, route ticket binding, package upload, one plan approval, ordered execution, result retrieval, expiry fallback, and all cross-channel rejection cases.

Add a static contract that new schemas under routing/approval/package tools contain `message_context` and do not introduce new `matrix_*` fields outside the compatibility normalizer.

**Step 2: Verify tests fail before final wiring**

Run:

```powershell
venv\Scripts\python.exe -m pytest tests\test_multichannel_qclaw_end_to_end.py tests\test_qclaw_end_to_end_contract.py tests\test_mcp_contract_sync.py -q
```

Expected: any missing final integration is exposed; fix only the failing wiring, not unrelated modules.

**Step 3: Write integration documentation**

Document:

- the normalized `message_context` contract;
- stable identity requirements for all three channels;
- same-channel/same-conversation approval rule;
- temporary grant request, confirm, revoke, and help examples;
- QClaw attachment handoff to OPS;
- legacy Matrix field compatibility and deprecation;
- prohibited actions and fail-closed behavior;
- the external-AI/OPS responsibility boundary.

Turn the old Element-only document into a short compatibility pointer to the new channel-neutral guide rather than maintaining two divergent runbooks.

**Step 4: Run full verification**

Run:

```powershell
venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
venv\Scripts\python.exe scripts\startup_preflight.py
git diff --check
```

Expected:

- Full pytest suite PASS.
- TypeScript typecheck PASS.
- Vite production build PASS.
- Startup preflight reports 0 errors.
- `git diff --check` reports no whitespace errors.

Start the app on an unused local port and inspect `/tools` at desktop and mobile widths. Confirm the Tool Token modal and temporary approval panel remain stable, text does not overlap, and no background refresh causes flicker.

**Step 5: Run final security checks**

Verify manually and with targeted tests:

- no empty approver set can approve a write plan;
- no temporary actor can grant, extend, transfer, or revoke access;
- no cross-channel/account/conversation approval succeeds;
- no production, DML, rollback, package delete, or package cleanup enters the temporary path;
- no secret, code hash, private path outside the controlled root, or channel credential appears in API/help output;
- no AI analysis endpoint, tool, resource, report type, page, model, or table remains.

**Step 6: Commit**

```powershell
git add tests docs README.md
git commit -m "test: verify multichannel temporary self-approval flow"
```

## Completion Criteria

- The full acceptance criteria in `docs/plans/2026-08-11-temporary-self-approval-multichannel-design.md` pass.
- Every write path is backed by an immutable plan and explicit approval.
- Matrix, WeChat, and Telegram use one generic OPS implementation.
- QClaw-specific channel processing remains outside OPS.
- Built-in AI analysis code and empty tables are removed.
- No unrelated worktree changes are staged or committed.

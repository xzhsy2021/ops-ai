# qclaw Element Approval OPS Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let qclaw turn encrypted Element room messages into deterministic, human-approved OPS actions without granting qclaw direct package, deployment, rollback, cleanup, or DML capabilities.

**Architecture:** qclaw remains the Matrix E2EE boundary and identity attester. OPS owns deterministic message-to-system routing, approval policy, immutable action manifests, controlled package intake, one-time approval consumption, and fixed-domain execution. The MCP surface exposes only routing, approval preparation, approval decision, execution, and status operations; approved actions run through an internal executor rather than caller-selected raw tools.

**Tech Stack:** Python, FastAPI, SQLAlchemy, SQLite/PostgreSQL-compatible migrations, pytest, React, TypeScript, Matrix/Element via qclaw, existing OPS MCP tool registry and background job services.

---

**Design reference:** `docs/plans/2026-07-22-qclaw-element-approval-ops-design.md`

**Implementation rules:**

- Use `@test-driven-development` for every task: add one failing assertion, run it, implement only enough to pass, then refactor.
- Use `@verification-before-completion` before reporting the implementation complete.
- Do not grant qclaw any existing raw `deploy`, `rollback`, `package_write`, `package_cleanup`, or `db_write` scope.
- Preserve existing user changes in the worktree. Stage only files named by the current task.
- Treat Matrix display names and room power levels as untrusted metadata. Authorization uses exact Matrix User IDs from OPS policy.
- Never accept a caller-provided tool name, arbitrary filesystem path, or unbound SQL at execution time.

## Task 1: Persist Immutable Approval Records

**Files:**

- Modify: `app/db/models.py`
- Modify: `app/db/migrations/runner.py`
- Create: `tests/test_qclaw_approval_migration.py`

**Step 1: Write the failing migration test**

Create a test that runs the migration set against an empty test database and asserts that `ai_action_approvals` contains the complete approval contract:

```python
REQUIRED_COLUMNS = {
    "action_digest",
    "approval_code_hash",
    "room_id",
    "request_event_id",
    "approval_event_id",
    "content_sha256",
    "routing_ticket_digest",
    "routing_config_revision",
    "expires_at",
    "consumed_at",
    "rejected_by",
    "rejected_at",
    "package_name",
    "package_sha256",
    "package_size_bytes",
    "execution_job_id",
    "execution_result",
    "failure_reason",
    "updated_at",
}


def test_ai_action_approval_has_qclaw_contract_columns(migrated_engine):
    columns = {
        item["name"]
        for item in sa.inspect(migrated_engine).get_columns("ai_action_approvals")
    }
    assert REQUIRED_COLUMNS <= columns
```

Add a model test asserting that the ORM accepts the lifecycle states `PENDING_APPROVAL`, `REJECTED`, `EXPIRED`, `STALE`, `EXECUTING`, `BLOCKED`, `RUNNING`, `SUCCEEDED`, and `FAILED`.

**Step 2: Run the test to verify it fails**

Run:

```powershell
pytest tests/test_qclaw_approval_migration.py -q
```

Expected: failure listing the missing columns.

**Step 3: Add model fields and migrations**

Extend `AiActionApproval` with typed fields matching the column list. Keep the immutable action manifest in the existing `request_payload` JSON column and the final structured result in `execution_result`.

Add `072_001` through `072_019` idempotent column migrations in `app/db/migrations/runner.py`:

| Column | SQL type |
| --- | --- |
| `action_digest` | `VARCHAR(64)` |
| `approval_code_hash` | `VARCHAR(128)` |
| `room_id` | `VARCHAR(255)` |
| `request_event_id` | `VARCHAR(255)` |
| `approval_event_id` | `VARCHAR(255)` |
| `content_sha256` | `VARCHAR(64)` |
| `routing_ticket_digest` | `VARCHAR(64)` |
| `routing_config_revision` | `VARCHAR(64)` |
| `expires_at` | `DATETIME` |
| `consumed_at` | `DATETIME` |
| `rejected_by` | `VARCHAR(255)` |
| `rejected_at` | `DATETIME` |
| `package_name` | `VARCHAR(255)` |
| `package_sha256` | `VARCHAR(64)` |
| `package_size_bytes` | `BIGINT` |
| `execution_job_id` | `INTEGER` |
| `execution_result` | `JSON` or project-compatible JSON text |
| `failure_reason` | `TEXT` |
| `updated_at` | `DATETIME` |

Add indexes for `action_digest`, `expires_at`, `execution_job_id`, and `(room_id, request_event_id)` using the migration runner's existing index pattern.

**Step 4: Run the focused test**

Run:

```powershell
pytest tests/test_qclaw_approval_migration.py -q
```

Expected: pass.

**Step 5: Commit**

```powershell
git add app/db/models.py app/db/migrations/runner.py tests/test_qclaw_approval_migration.py
git commit -m "feat: persist qclaw approval lifecycle"
```

## Task 2: Resolve Messages Deterministically and Issue Routing Tickets

**Files:**

- Create: `app/services/qclaw_routing.py`
- Modify: `app/core/config.py`
- Create: `tests/test_qclaw_routing.py`

**Step 1: Write failing resolver tests**

Cover these cases with plain service-level fixtures:

```python
def test_exact_system_name_wins_over_keyword(...): ...
def test_exact_system_alias_resolves(...): ...
def test_unique_service_alias_resolves_parent_system(...): ...
def test_unique_highest_priority_keyword_resolves(...): ...
def test_equal_priority_keyword_match_is_ambiguous(...): ...
def test_unknown_message_is_unmatched(...): ...
def test_disabled_routing_entry_is_ignored(...): ...
def test_ticket_is_bound_to_room_event_content_and_revision(...): ...
def test_ticket_expires_after_fifteen_minutes(...): ...
def test_ticket_rejects_modified_content_hash(...): ...
```

Use a routing input shaped like:

```python
systems = [{
    "name": "crypto-trader",
    "message_routing": {
        "enabled": True,
        "aliases": ["量化", "量化交易"],
        "keywords": ["btc strategy", "crypto deploy"],
        "priority": 100,
    },
    "services": [{
        "name": "trader-api",
        "template_variables": {
            "message_routing": {
                "aliases": ["交易接口"],
                "keywords": ["trader api"],
                "priority": 80,
            }
        },
    }],
}]
```

**Step 2: Run the tests to verify failure**

```powershell
pytest tests/test_qclaw_routing.py -q
```

Expected: import failure for `app.services.qclaw_routing`.

**Step 3: Implement normalization and deterministic matching**

Create value objects and pure functions:

```python
class RoutingOutcome(str, Enum):
    RESOLVED = "RESOLVED"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class RoutingDecision:
    outcome: RoutingOutcome
    system_name: str | None = None
    service_name: str | None = None
    matched_by: str | None = None
    routing_config_revision: str | None = None
    candidates: tuple[str, ...] = ()
```

Normalize with Unicode NFKC, lowercase Latin text, collapse whitespace, and retain CJK text. Apply precedence exactly as approved:

1. Exact canonical system name.
2. Exact system alias.
3. Unique service name or service alias, returning its parent system.
4. Unique highest-priority keyword.
5. Otherwise `UNMATCHED` or `AMBIGUOUS`.

Hash canonical normalized routing JSON to produce `routing_config_revision`.

**Step 4: Implement signed 15-minute tickets**

Add `QCLAW_APPROVAL_SIGNING_KEY` to configuration. Issue HMAC-SHA256 tickets over canonical JSON containing:

```python
{
    "room_id": room_id,
    "event_id": event_id,
    "content_sha256": content_sha256,
    "system_name": system_name,
    "service_name": service_name,
    "routing_config_revision": revision,
    "issued_at": issued_at.isoformat(),
    "expires_at": expires_at.isoformat(),
    "nonce": secrets.token_hex(16),
}
```

Use URL-safe base64 for `payload.signature`. Verification must use `hmac.compare_digest`, enforce expiry, and compare all expected binding fields supplied by the prepare operation.

**Step 5: Run the focused tests**

```powershell
pytest tests/test_qclaw_routing.py -q
```

Expected: pass.

**Step 6: Commit**

```powershell
git add app/services/qclaw_routing.py app/core/config.py tests/test_qclaw_routing.py
git commit -m "feat: resolve qclaw messages with signed routing tickets"
```

## Task 3: Manage Message Routing in OPS System Configuration

**Files:**

- Modify: `app/api/deploy_v2.py`
- Modify: `app/deploy/schemas.py`
- Modify: `frontend/src/pages/SystemEditPage.tsx`
- Modify: `frontend/src/pages/ServiceEditPage.tsx`
- Create: `tests/test_qclaw_routing_api.py`
- Create: `tests/test_qclaw_routing_ui_contract.py`

**Step 1: Write failing API validation tests**

Assert that system create/update normalizes and stores:

```json
{
  "message_routing": {
    "enabled": true,
    "aliases": ["量化", "量化交易"],
    "keywords": ["btc strategy"],
    "priority": 100
  }
}
```

Test rejection of blank aliases, duplicate normalized values, priorities outside `0..1000`, and non-boolean `enabled`. Assert that service routing is accepted only beneath `template_variables.message_routing` and is returned unchanged by the read API after normalization.

**Step 2: Run the API tests to verify failure**

```powershell
pytest tests/test_qclaw_routing_api.py -q
```

Expected: validation assertions fail.

**Step 3: Add backend schemas and normalization**

Add a shared schema:

```python
class MessageRoutingConfig(BaseModel):
    enabled: bool = False
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=1000)
```

Call the normalization helper from Task 2 in both system create and update handlers. Ensure system reads expose `message_routing`, and service writes preserve unrelated `template_variables` keys.

**Step 4: Add failing frontend contract assertions**

Assert that both edit pages expose controls for routing enablement, aliases, keywords, and priority, and that save payloads use the backend keys above.

```powershell
pytest tests/test_qclaw_routing_ui_contract.py -q
```

Expected: fail before UI controls exist.

**Step 5: Add restrained routing controls**

Use existing form components. Add:

- A toggle for routing enablement.
- Token/chip inputs for aliases and keywords.
- A numeric input for priority.
- Inline duplicate and blank validation.

Do not add a new page or dashboard. Preserve all existing system and service fields when saving.

**Step 6: Verify backend and frontend**

```powershell
pytest tests/test_qclaw_routing_api.py tests/test_qclaw_routing_ui_contract.py -q
cd frontend
npm run typecheck
```

Expected: all pass.

**Step 7: Commit**

```powershell
git add app/api/deploy_v2.py app/deploy/schemas.py frontend/src/pages/SystemEditPage.tsx frontend/src/pages/ServiceEditPage.tsx tests/test_qclaw_routing_api.py tests/test_qclaw_routing_ui_contract.py
git commit -m "feat: manage message routing for deployment systems"
```

## Task 4: Implement Approval Policy and Lifecycle

**Files:**

- Create: `app/services/action_approval.py`
- Modify: `app/api/tools.py`
- Create: `tests/test_qclaw_approval_service.py`
- Create: `tests/test_qclaw_approval_api.py`

**Step 1: Write failing lifecycle tests**

Cover:

```python
def test_prepare_requires_valid_routing_ticket(...): ...
def test_prepare_generates_one_time_short_code_and_hash_only(...): ...
def test_prepare_is_idempotent_for_same_action_digest(...): ...
def test_changed_manifest_creates_different_action_digest(...): ...
def test_unauthorized_matrix_user_cannot_consume(...): ...
def test_wrong_room_or_reply_event_cannot_consume(...): ...
def test_expired_approval_transitions_to_expired(...): ...
def test_consume_is_atomic_under_two_concurrent_calls(...): ...
def test_reject_is_terminal(...): ...
```

The persisted record must contain only a password hash of the short approval code. Return the plaintext code once from preparation so qclaw can include it in the room confirmation message.

**Step 2: Run the tests to verify failure**

```powershell
pytest tests/test_qclaw_approval_service.py -q
```

Expected: import failure for `ActionApprovalService`.

**Step 3: Implement canonical action manifests**

Use canonical JSON with sorted keys and compact separators. The action digest must bind:

```python
{
    "action_type": action_type,
    "room_id": room_id,
    "request_event_id": request_event_id,
    "content_sha256": content_sha256,
    "system_name": system_name,
    "service_name": service_name,
    "environment": environment,
    "targets": sorted(targets),
    "action_parameters": action_parameters,
    "routing_config_revision": routing_config_revision,
}
```

Generate an unambiguous eight-character short code, hash it with the project's password hashing utility, set a 15-minute expiry, and store status `PENDING_APPROVAL`.

**Step 4: Implement authorization and atomic consumption**

Store approval policy in the existing config store under `qclaw_approval_policy`:

```json
{
  "authorized_matrix_user_ids": ["@release-admin:example.org"],
  "approval_ttl_seconds": 900
}
```

The consume update must include `WHERE id = :id AND status = 'PENDING_APPROVAL' AND consumed_at IS NULL`. Verify the Matrix user ID, room ID, reply-to approval event ID, code hash, expiry, and current action freshness before the update. Set `approved_by`, `approval_event_id`, `approved_at`, `consumed_at`, and `EXECUTING` in the same transaction.

**Step 5: Add admin-only policy endpoints**

Add read/update endpoints alongside existing tool access administration. Validate exact Matrix IDs with a conservative syntax check, normalize duplicates, and audit old/new values without logging secrets or approval codes.

**Step 6: Verify service and API tests**

```powershell
pytest tests/test_qclaw_approval_service.py tests/test_qclaw_approval_api.py -q
```

Expected: pass.

**Step 7: Commit**

```powershell
git add app/services/action_approval.py app/api/tools.py tests/test_qclaw_approval_service.py tests/test_qclaw_approval_api.py
git commit -m "feat: add matrix-backed approval lifecycle"
```

## Task 5: Intake Room Packages Through a Controlled Path

**Files:**

- Create: `app/services/package_intake.py`
- Modify: `app/services/package_retention.py`
- Modify: `app/core/config.py`
- Modify: `app/pipeline/steps.py`
- Modify: `app/api/deploy/_shared.py`
- Create: `tests/test_qclaw_package_intake.py`
- Create: `tests/test_qclaw_package_resolution.py`

**Step 1: Write failing path-security tests**

Use temporary staging and upload roots. Cover:

```python
def test_accepts_regular_file_beneath_staging_root(...): ...
def test_rejects_path_traversal(...): ...
def test_rejects_file_outside_staging_root(...): ...
def test_rejects_symlink_junction_or_reparse_point(...): ...
def test_rejects_part_file(...): ...
def test_recalculates_hash_instead_of_trusting_qclaw(...): ...
def test_rejects_disallowed_extension_and_oversize_file(...): ...
def test_import_uses_temp_then_atomic_rename(...): ...
def test_same_name_same_hash_reuses_canonical_package(...): ...
def test_same_name_different_hash_requires_overwrite_approval(...): ...
def test_source_change_after_approval_is_stale(...): ...
```

Add a regression test proving a release manifest cannot use `_local_upload_path`, the current working directory, or a caller-supplied override when `approved_package_sha256` is present.

**Step 2: Run the tests to verify failure**

```powershell
pytest tests/test_qclaw_package_intake.py tests/test_qclaw_package_resolution.py -q
```

Expected: import/behavior failures.

**Step 3: Add staging configuration and inspection**

Add `QCLAW_STAGING_DIR`, defaulting to `<APP_DATA_DIR>/qclaw-staging`. Implement:

```python
@dataclass(frozen=True)
class StagedPackage:
    path: Path
    name: str
    size_bytes: int
    sha256: str


def inspect_staged_package(path: str | Path) -> StagedPackage:
    ...
```

Resolve both root and file, require a regular file beneath the root, reject any path component carrying Windows `FILE_ATTRIBUTE_REPARSE_POINT`, reject `.part`, apply existing extension/size policy, and stream SHA-256 from disk.

**Step 4: Implement canonical import**

Implement `import_approved_package(staged, expected_sha256, allow_overwrite=False)`:

1. Re-inspect and compare the staged SHA.
2. Copy to `<UPLOAD_DIR>/<name>.import-<uuid>.tmp` while hashing copied bytes.
3. Compare the copied hash to the approved hash.
4. Resolve same-name behavior by hash.
5. Atomically rename to `package_path(name)`.
6. Update `DeployPackage` metadata only after rename succeeds.

Delete only the import temp file on failure. Do not delete the room staging source until the whole approved action reaches a terminal state.

**Step 5: Add one canonical deployment resolver**

Add to `package_retention.py`:

```python
def resolve_deploy_package(
    package_name: str,
    expected_sha256: str,
) -> Path:
    path = package_path(package_name)
    info = inspect_package_file(path)
    if info.sha256 != expected_sha256:
        raise PackageChangedError(package_name)
    return path
```

In pipeline and specialized deployment code, select this resolver whenever an approved action supplies `approved_package_sha256`. Ignore and reject `local_path`, current-directory, and legacy fallback values in this mode.

**Step 6: Verify focused tests**

```powershell
pytest tests/test_qclaw_package_intake.py tests/test_qclaw_package_resolution.py -q
```

Expected: pass.

**Step 7: Commit**

```powershell
git add app/services/package_intake.py app/services/package_retention.py app/core/config.py app/pipeline/steps.py app/api/deploy/_shared.py tests/test_qclaw_package_intake.py tests/test_qclaw_package_resolution.py
git commit -m "feat: secure qclaw package intake and canonical resolution"
```

## Task 6: Expose a Minimal qclaw Approval MCP Profile

**Files:**

- Create: `app/services/tool_adapters/approval_tools.py`
- Modify: `app/services/tool_registry.py`
- Modify: `app/services/tool_policy.py`
- Modify: `app/services/tool_token.py`
- Modify: `app/services/tool_context.py`
- Create: `tests/test_qclaw_mcp_contract.py`
- Modify: `tests/test_tool_permission_matrix_contract.py`
- Modify: `tests/test_mcp_contract_sync.py`

**Step 1: Write failing capability tests**

Assert that the `qclaw_approval` profile exposes exactly:

```python
QCLAW_APPROVAL_TOOLS = {
    "ops.routing.resolve_message_target",
    "ops.approval.prepare_release",
    "ops.approval.prepare_rollback",
    "ops.approval.prepare_package_cleanup",
    "ops.approval.prepare_dml",
    "ops.approval.execute",
    "ops.approval.reject",
    "ops.approval.get",
}
```

Allow only explicitly required existing read/status tools after listing them in the test. Assert that the profile never exposes `ops.upload_package`, raw deploy/rollback executors, `ops.cleanup_packages`, or `ops.db_execute_dml`.

Test a token template containing only:

```python
{"approval:prepare", "approval:execute", "approval:read"}
```

**Step 2: Run tests to verify failure**

```powershell
pytest tests/test_qclaw_mcp_contract.py tests/test_tool_permission_matrix_contract.py tests/test_mcp_contract_sync.py -q
```

Expected: missing profile and tools.

**Step 3: Add self-managed job metadata**

Extend `ToolDefinition` with:

```python
manages_own_job: bool = False
```

Keep `ops.approval.execute` marked high/critical risk and `requires_confirmation=True`, but skip the registry's generic pre-handler job enqueue only when `manages_own_job` is true. This allows the handler to validate and atomically consume the approval before creating its own job.

Add `approval_id` and `approved_by_matrix_id` to `ToolContext`, including safe audit/job serialization. These fields are populated only by the internal approval executor context, never directly from MCP arguments.

**Step 4: Register the narrow tools and scopes**

Register `approval_tools` from `tool_registry.py`, add the explicit `qclaw_approval` profile, and add a `qclaw-approval` token template. Add approval scopes to the admin-controlled scope set without mapping them to any existing raw write scope.

Policy behavior must be:

- qclaw token may call the eight approval/routing tools.
- `ops.approval.execute` remains high risk in discovery and audit.
- ordinary tool tokens remain blocked from all existing high-risk tools.
- `auth_type="approval_executor"` is accepted only in the internal executor path.

**Step 5: Run contract tests**

```powershell
pytest tests/test_qclaw_mcp_contract.py tests/test_tool_permission_matrix_contract.py tests/test_mcp_contract_sync.py -q
```

Expected: pass.

**Step 6: Commit**

```powershell
git add app/services/tool_adapters/approval_tools.py app/services/tool_registry.py app/services/tool_policy.py app/services/tool_token.py app/services/tool_context.py tests/test_qclaw_mcp_contract.py tests/test_tool_permission_matrix_contract.py tests/test_mcp_contract_sync.py
git commit -m "feat: expose minimal qclaw approval mcp profile"
```

## Task 7: Prepare Domain-Specific Immutable Actions

**Files:**

- Modify: `app/services/tool_adapters/approval_tools.py`
- Modify: `app/services/action_approval.py`
- Modify: `app/services/package_intake.py`
- Modify: `app/services/tool_adapters/db_tools.py`
- Create: `tests/test_qclaw_prepare_actions.py`

**Step 1: Write failing preparation tests**

Cover each approved action:

```python
def test_unmatched_routing_never_creates_approval(...): ...
def test_ambiguous_routing_never_creates_approval(...): ...
def test_prepare_release_binds_package_system_service_env_targets(...): ...
def test_prepare_release_runs_precheck_before_requesting_approval(...): ...
def test_prepare_rollback_requires_existing_deployment_id(...): ...
def test_prepare_cleanup_accepts_retention_candidates_not_paths(...): ...
def test_prepare_dml_binds_sql_hash_connection_database_table_and_limit(...): ...
def test_prepare_dml_rejects_ddl_multi_statement_lock_and_missing_where(...): ...
```

**Step 2: Run the tests to verify failure**

```powershell
pytest tests/test_qclaw_prepare_actions.py -q
```

Expected: unimplemented handler failures.

**Step 3: Implement the routing MCP handler**

`ops.routing.resolve_message_target` accepts only room/event/content metadata and normalized message text. It loads OPS system configuration itself, calls the Task 2 resolver, and returns a ticket only for `RESOLVED`. For `UNMATCHED` and `AMBIGUOUS`, return a terminal non-actionable result; do not create approval records.

**Step 4: Implement release preparation**

`prepare_release` must:

1. Verify the routing ticket against room/event/content/system/service.
2. Inspect the staged package and calculate its SHA.
3. Validate environment and targets against the resolved system/service.
4. Build the existing release plan and run non-mutating prechecks.
5. Create one immutable `RELEASE` manifest binding import and deploy.
6. Return approval ID, short code, expiry, risk summary, package SHA, target summary, and confirmation text for qclaw to post.

**Step 5: Implement rollback and cleanup preparation**

Rollback accepts an existing deployment ID, then resolves and stores exact previous package SHA, target IDs, strategy, and environment. Cleanup accepts package record IDs from the retention candidate service only; resolve those IDs to canonical package names and SHAs and bind the candidate snapshot. Never accept filesystem paths.

**Step 6: Implement DML preparation through the existing preview service**

Refactor the reusable preview logic from `db_tools.py` into an internal callable if needed. Bind exact SQL hash, connection, database, table set, expected affected rows, sample rows, maximum permitted rows, and reason. Preserve all existing prohibitions. Preparation is read/preview only and must not reuse the raw DML execution tool.

**Step 7: Verify preparation tests**

```powershell
pytest tests/test_qclaw_prepare_actions.py -q
```

Expected: pass.

**Step 8: Commit**

```powershell
git add app/services/tool_adapters/approval_tools.py app/services/action_approval.py app/services/package_intake.py app/services/tool_adapters/db_tools.py tests/test_qclaw_prepare_actions.py
git commit -m "feat: prepare immutable approved ops actions"
```

## Task 8: Execute Approved Actions Through a Fixed Internal Dispatcher

**Files:**

- Create: `app/services/approval_executor.py`
- Modify: `app/services/job_service.py`
- Modify: `app/services/tool_adapters/approval_tools.py`
- Modify: `main.py`
- Create: `tests/test_qclaw_approval_executor.py`
- Create: `tests/test_qclaw_approval_jobs.py`

**Step 1: Write failing execution-boundary tests**

Cover:

```python
def test_executor_dispatches_only_known_action_types(...): ...
def test_execute_requires_authorized_matrix_identity_attestation(...): ...
def test_execute_rejects_caller_selected_tool_name(...): ...
def test_execute_consumes_before_job_creation(...): ...
def test_second_execute_cannot_create_second_job(...): ...
def test_execution_context_contains_server_side_approval_identity(...): ...
def test_queued_approved_job_recovers_after_restart(...): ...
def test_running_job_is_reconciled_after_restart(...): ...
```

**Step 2: Run the tests to verify failure**

```powershell
pytest tests/test_qclaw_approval_executor.py tests/test_qclaw_approval_jobs.py -q
```

Expected: import/behavior failures.

**Step 3: Implement the fixed dispatcher**

Create a server-owned dispatch table:

```python
class ApprovalExecutor:
    _handlers = {
        "RELEASE": "_execute_release",
        "ROLLBACK": "_execute_rollback",
        "PACKAGE_CLEANUP": "_execute_package_cleanup",
        "DML": "_execute_dml",
    }

    def execute(self, approval_id: int) -> dict[str, Any]:
        approval = self._load_consumed_approval(approval_id)
        handler_name = self._handlers.get(approval.action_type)
        if not handler_name:
            raise UnsupportedApprovedAction(approval.action_type)
        return getattr(self, handler_name)(approval)
```

There must be no `tool_name` argument. Build `ToolContext(auth_type="approval_executor", approval_id=..., approved_by_matrix_id=...)` from the database record.

**Step 4: Create an approved-action job only after consumption**

In `ops.approval.execute`:

1. Authenticate the narrow qclaw token.
2. Validate qclaw's Matrix attestation fields and call atomic consume.
3. Create one `OperationJob(job_type="approved_action")` linked from `execution_job_id`.
4. Transition `EXECUTING` to `RUNNING` when the worker starts.
5. Return the approval and job IDs immediately.

Enforce a uniqueness check so one approval cannot own two jobs.

**Step 5: Add startup recovery**

Add a dedicated worker entrypoint that loads the manifest from the database. During application lifespan startup, resume queued approved-action jobs and reconcile stale running jobs using the same conventions as existing deployment workers. Never reconstruct an approved action from MCP input.

**Step 6: Verify executor and recovery tests**

```powershell
pytest tests/test_qclaw_approval_executor.py tests/test_qclaw_approval_jobs.py -q
```

Expected: pass.

**Step 7: Commit**

```powershell
git add app/services/approval_executor.py app/services/job_service.py app/services/tool_adapters/approval_tools.py main.py tests/test_qclaw_approval_executor.py tests/test_qclaw_approval_jobs.py
git commit -m "feat: execute approvals through internal domain dispatcher"
```

## Task 9: Implement Release, Rollback, Cleanup, and DML Handlers

**Files:**

- Modify: `app/services/approval_executor.py`
- Modify: `app/services/tool_adapters/deploy_tools.py`
- Modify: `app/services/tool_adapters/file_tools.py`
- Modify: `app/services/tool_adapters/db_tools.py`
- Create: `tests/test_qclaw_domain_execution.py`

**Step 1: Write failing release execution tests**

Assert:

- The staged package is rehashed before import.
- Import lands at canonical `UPLOAD_DIR` before final precheck.
- Final precheck resolves the package by name and approved SHA.
- A package changed after approval transitions to `STALE` and does not deploy.
- A final precheck failure transitions to `BLOCKED`, retains the imported package, and does not deploy.
- Deployment failure transitions to `FAILED` and never auto-rolls back.

**Step 2: Implement release execution**

The `RELEASE` handler performs:

```text
re-inspect staging -> import canonical package -> final precheck -> create deployment -> run existing deployment worker
```

Reuse domain services extracted from `deploy_tools.py`; do not invoke the public raw tool through `ToolRegistry.call()`. Pass `approved_package_sha256` so all package resolution is canonical. Persist deployment ID and package import result into `execution_result`.

**Step 3: Write failing rollback and cleanup tests**

Assert rollback re-resolves the source deployment and exact target/package binding. Assert cleanup re-runs retention protections and may remove candidates that became protected, but may never add packages absent from the approved manifest.

**Step 4: Implement rollback and cleanup execution**

Extract internal domain functions from existing adapters as needed. The approved executor supplies only the persisted manifest. Map stale bindings to `STALE`, policy changes to `BLOCKED`, runtime errors to `FAILED`, and successful jobs to `SUCCEEDED`.

**Step 5: Write failing DML execution tests**

Assert the executor:

- Re-previews the exact persisted SQL.
- Rejects a SQL hash or connection/database mismatch.
- Blocks if expected rows now exceed the approved maximum.
- Executes once, without automatic retry.
- Records affected rows and a redacted result.

**Step 6: Implement DML execution**

Call an internal database execution service with exact manifest values and internal approval context. Preserve transaction handling and row limits. Never pass through a caller-provided replacement SQL string.

**Step 7: Run domain tests**

```powershell
pytest tests/test_qclaw_domain_execution.py -q
```

Expected: pass.

**Step 8: Commit**

```powershell
git add app/services/approval_executor.py app/services/tool_adapters/deploy_tools.py app/services/tool_adapters/file_tools.py app/services/tool_adapters/db_tools.py tests/test_qclaw_domain_execution.py
git commit -m "feat: execute approved ops domains with bound inputs"
```

## Task 10: Add Approval Administration and Observability

**Files:**

- Modify: `frontend/src/pages/ToolAccessPage.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `app/api/tools.py`
- Modify: `app/services/action_approval.py`
- Create: `tests/test_qclaw_approval_observability.py`
- Create: `tests/test_qclaw_approval_ui_contract.py`

**Step 1: Write failing observability tests**

Assert that approval status responses expose:

```json
{
  "id": 123,
  "action_type": "RELEASE",
  "status": "RUNNING",
  "system_name": "crypto-trader",
  "service_name": "trader-api",
  "approved_by": "@release-admin:example.org",
  "expires_at": "...",
  "execution_job_id": 456,
  "result": {}
}
```

Ensure responses never expose approval code hashes, signing keys, token secrets, unrestricted SQL samples, or filesystem paths. Add an audit assertion for prepare, approve, reject, expire, execute, and terminal transitions.

**Step 2: Run the tests to verify failure**

```powershell
pytest tests/test_qclaw_approval_observability.py tests/test_qclaw_approval_ui_contract.py -q
```

Expected: missing API/UI behavior.

**Step 3: Add approval list/detail APIs**

Add paginated admin endpoints and make `ops.approval.get` return a redacted version of the same DTO. Support filters for status, action type, system, Matrix approver, and date. Keep room and event IDs visible only to authorized administrators and the owning qclaw token.

**Step 4: Add compact policy and audit UI**

Extend the existing Tool Access page with:

- Authorized Matrix User ID allowlist editing.
- Approval TTL display/editing.
- A compact approval table with status, action, target system, requester room, approver, time, and job link.
- Read-only detail for the immutable action summary and terminal result.

Use existing table, dialog, and form patterns. Do not expose action execution controls in the web UI in this task.

**Step 5: Verify API, UI, and type safety**

```powershell
pytest tests/test_qclaw_approval_observability.py tests/test_qclaw_approval_ui_contract.py -q
cd frontend
npm run typecheck
npm run build
```

Expected: all pass.

**Step 6: Commit**

```powershell
git add frontend/src/pages/ToolAccessPage.tsx frontend/src/api.ts app/api/tools.py app/services/action_approval.py tests/test_qclaw_approval_observability.py tests/test_qclaw_approval_ui_contract.py
git commit -m "feat: administer and audit qclaw approvals"
```

## Task 11: Document qclaw Integration and Verify the Whole Boundary

**Files:**

- Create: `docs/qclaw-element-approval-integration.md`
- Create: `tests/test_qclaw_end_to_end_contract.py`
- Modify: `README.md`

**Step 1: Write an end-to-end contract test**

Build a fixture flow that does not need a live Matrix server:

```text
room event attestation
  -> deterministic route
  -> staged package inspection
  -> immutable release approval
  -> authorized reply attestation
  -> one-time consume
  -> canonical import
  -> deployment job creation
  -> terminal status
```

Also assert the negative flow: an unrelated message yields `UNMATCHED`, creates no approval, imports no package, and creates no operation job.

**Step 2: Run the end-to-end test to verify failure or missing coverage**

```powershell
pytest tests/test_qclaw_end_to_end_contract.py -q
```

Expected: fail until fixture integration is complete.

**Step 3: Complete the fixture wiring**

Use fake Matrix attestations and local temporary directories. Do not weaken production verification hooks to make the test pass. Assert that only the `qclaw_approval` tool profile is used.

**Step 4: Write the integration runbook**

Document:

- qclaw owns Matrix login, E2EE keys, sync token, message parsing, attachment decryption, `.part` download, hash calculation, and atomic staging rename.
- Trigger syntax (`@ops-bot` or configured command prefix).
- The exact MCP call sequence and request/response fields.
- How qclaw posts the approval summary and accepts only a reply containing `批准 <short-code>`.
- Local token storage, token rotation, and separate OS-account recommendation.
- Staging cleanup rules for unmatched, rejected, expired, stale, succeeded, and failed actions.
- Recovery after qclaw or OPS restart.
- A dry-run rollout using a non-production system and a package with a known SHA.

Add a concise README link to the runbook and design document.

**Step 5: Run the focused security and contract suite**

```powershell
pytest tests/test_qclaw_*.py tests/test_tool_permission_matrix_contract.py tests/test_mcp_contract_sync.py tests/test_risk_policy_contract.py -q
```

Expected: all pass.

**Step 6: Run full backend and frontend verification**

```powershell
pytest -q
cd frontend
npm run typecheck
npm run build
```

Expected: all pass. Record any pre-existing unrelated failures separately; do not mask them.

**Step 7: Inspect the final permission matrix manually**

Verify these invariants from tool discovery and a real qclaw token:

```text
qclaw can resolve, prepare, execute an already-approved record, reject, and read status
qclaw cannot upload directly
qclaw cannot deploy directly
qclaw cannot rollback directly
qclaw cannot cleanup directly
qclaw cannot execute DML directly
unmatched and ambiguous messages cannot create jobs
approved package paths always resolve under UPLOAD_DIR
```

**Step 8: Commit**

```powershell
git add docs/qclaw-element-approval-integration.md tests/test_qclaw_end_to_end_contract.py README.md
git commit -m "docs: add qclaw element approval integration runbook"
```

## Completion Gate

Before merging or enabling the qclaw token in production:

1. Rotate and load `QCLAW_APPROVAL_SIGNING_KEY` outside source control.
2. Create the `qclaw-approval` token as an administrator and verify its discovered tool set.
3. Configure exact authorized Matrix User IDs.
4. Configure at least one explicit OPS system routing rule and confirm unrelated messages are abandoned.
5. Confirm `QCLAW_STAGING_DIR` and `UPLOAD_DIR` are distinct real directories with least-privilege ACLs.
6. Execute one non-production package release, rollback, cleanup, and transactionally rolled-back DML test.
7. Confirm duplicate approval replies cannot create duplicate jobs.
8. Confirm audit records correlate Matrix room/event IDs, approval ID, operation job ID, deployment ID, package SHA, and approving Matrix user.

## Implementation Simplifications (vs Original Design)

以下设计项在实现时有意简化，原因是对齐项目"最小复杂度"约束：

### 1. `manages_own_job` on ToolDefinition (Task 6 Step 3)
**设计意图**: 扩展 `ToolDefinition` 增加 `manages_own_job: bool = False`，让 `ops.approval.execute` 跳过 registry 的通用 pre-handler job enqueue，自行管理 job 生命周期。

**实现简化**: 未添加此字段。当前实现采用同步执行模式（consume → execute 在同一调用中完成），不需要异步 job 队列。审批通过后 `approval_execute` 直接调用 `ApprovalExecutor.execute()` 并返回结果，Job 的创建和状态更新在 executor 内部完成。

### 2. `approval_id` / `approved_by_matrix_id` on ToolContext (Task 6 Step 3)
**设计意图**: 在 `ToolContext` 上增加 `approval_id` 和 `approved_by_matrix_id` 字段，用于安全审计和 job 序列化。

**实现简化**: 未添加这些字段。执行上下文直接从数据库中的 `AiActionApproval` 记录派生（`approval.approved_by`、`approval.id`），无需通过 ToolContext 传递。Job 的 `operator` 和 `request_json` 字段直接从审批记录填充。

### 3. Startup Recovery for Approved-Action Jobs (Task 8 Step 5)
**设计意图**: 在应用启动时恢复排队中的 approved-action job，对账 stale running job。

**实现简化**: 未实现。当前同步执行模式意味着不存在"排队中"的审批 job——审批通过后立即执行，不会有待恢复的 job。如果 executor 执行失败，状态直接标记为 FAILED，不需要重启后对账。

### 简化理由
- 项目为单机部署，不需要异步 job 队列的复杂性
- 审批操作本身是低频事件（人工在 Element 房间批准），同步执行不会阻塞
- 减少代码路径和测试表面积，降低维护成本
- 如未来需要异步执行，可在 `approval_execute` 中改为创建 job 并返回 EXECUTING 状态，由 worker 异步消费

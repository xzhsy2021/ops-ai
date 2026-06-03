# Remove Application Layer Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the Application entity and make system/service/environment the only deployment and configuration flow.

**Architecture:** Backend deploy resolution stops reading application state and resolves only from system, environment, service, and runtime overrides. Frontend removes app pages and routes, consolidates configuration under systems, and simplifies the deploy form to service-driven defaults.

**Tech Stack:** FastAPI, SQLAlchemy, React, TypeScript, Vite, pytest

---

### Task 1: Lock the target data model in docs

**Files:**
- Modify: `docs/plans/2026-05-21-remove-application-layer-design.md`
- Modify: `docs/plans/2026-05-21-remove-application-layer-implementation-plan.md`

**Step 1: Review the approved design and extract final keep/drop rules**

Check:
- `docs/plans/2026-05-21-remove-application-layer-design.md`

Expected:
- Exact decisions exist for application deletion, field retention, server precedence, and variable precedence.

**Step 2: Update the design doc if approval feedback changed any field decision**

Edit the approved doc only if the user requested changes after review.

**Step 3: Re-read the implementation plan before coding**

Check:
- This file

Expected:
- Task order still matches the final design.

### Task 2: Write regression tests for deploy resolution without applications

**Files:**
- Modify: `tests/test_release_reliability_contract.py`
- Modify: `tests/test_iter36_release_orchestration_contract.py`
- Modify: `tests/test_dashboard_iteration_contract.py`

**Step 1: Write a failing test for variable precedence without application fallback**

Add a test that asserts deploy variable merge order is:
- runtime overrides
- service template variables
- environment variables
- system variables

And does not read:
- `Application.default_variables`
- `Application.deploy_path`
- `Application.health_url`

**Step 2: Run the targeted test to verify failure**

Run:

```bash
pytest tests/test_release_reliability_contract.py -k "application or precedence" -v
```

Expected:
- FAIL because the current code still reads application context.

**Step 3: Write a failing test for server resolution without app/server/system hidden fallback**

Assert server resolution order is:
- request.servers
- service.servers_by_env
- request.server_group
- dovo group
- service.servers

And release is blocked if nothing resolves.

**Step 4: Run the targeted server-resolution test**

Run:

```bash
pytest tests/test_iter36_release_orchestration_contract.py -k "server resolution" -v
```

Expected:
- FAIL against current fallback behavior.

### Task 3: Remove backend `Application` model and repository usage

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/db/repository.py`
- Modify: `app/db/__init__.py`
- Modify: `app/api/apps.py`
- Modify: `app/api/config.py`

**Step 1: Delete the `Application` ORM model and related repository**

Remove:
- `Application` from `app/db/models.py`
- `ApplicationRepository` from `app/db/repository.py`
- exports from `app/db/__init__.py`

**Step 2: Remove app API router and references**

Delete or detach:
- `/api/v2/apps`
- server-group helpers that only exist for application binding if no longer needed

**Step 3: Remove application import/export support**

Update config import/export so it no longer serializes or restores applications.

**Step 4: Run focused backend tests**

Run:

```bash
pytest tests/test_release_reliability_contract.py tests/test_iter36_release_orchestration_contract.py -v
```

Expected:
- Previous failures move from missing model cleanup to deploy-resolution assertions.

### Task 4: Remove deploy API dependence on `app_id`

**Files:**
- Modify: `app/deploy/schemas.py`
- Modify: `app/api/deploy/_shared.py`
- Modify: `app/api/deploy_v2.py`
- Modify: `app/pipeline/variables.py`

**Step 1: Delete `app_id` from deploy and resolution request schemas**

Remove:
- `DeployRequest.app_id`
- `ResolutionPreviewRequest.app_id`

**Step 2: Delete application lookup helpers and fallback merge logic**

Remove:
- `_get_app_context`
- app-based branches in `_derive_servers`
- app-based branches in `_merge_release_variables`

**Step 3: Simplify variable defaults**

Keep:
- system vars
- dovo/group vars
- service vars
- runtime overrides

Ensure default `deploy_path` and `update_script` still resolve safely.

**Step 4: Update pipeline variable registry**

Remove app-only binding assumptions and keep only variables that still have a real source.

**Step 5: Run focused tests**

Run:

```bash
pytest tests/test_release_reliability_contract.py tests/test_iter36_release_orchestration_contract.py -v
```

Expected:
- PASS for deploy-resolution behavior.

### Task 5: Add service-level default pipeline support

**Files:**
- Modify: `app/deploy/schemas.py`
- Modify: `app/api/deploy/_shared.py`
- Modify: `app/api/deploy_v2.py`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/ServiceEditPage.tsx`

**Step 1: Write a failing test for service default pipeline**

Add a test that a service-level `pipeline_id` is returned by the system/service API and is used as deploy default when the operator has not manually selected one.

**Step 2: Run the focused test**

Run:

```bash
pytest tests/test_iter36_release_orchestration_contract.py -k "service pipeline" -v
```

Expected:
- FAIL because services do not yet own pipeline defaults.

**Step 3: Extend service config schema and response payload**

Add `pipeline_id` to normalized service payloads and API responses.

**Step 4: Update frontend service editor**

Expose:
- optional default pipeline selector

Do not reintroduce application-like metadata.

**Step 5: Run the focused test again**

Run:

```bash
pytest tests/test_iter36_release_orchestration_contract.py -k "service pipeline" -v
```

Expected:
- PASS

### Task 6: Remove frontend application pages, routes, and nav

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/routes.ts`
- Delete: `frontend/src/pages/ApplicationListPage.tsx`
- Delete: `frontend/src/pages/ApplicationDetailPage.tsx`
- Modify: `frontend/src/api.ts`

**Step 1: Remove routes and navigation entries**

Delete:
- `ROUTES.apps`
- app route registrations
- nav item for “应用”

**Step 2: Remove app API client**

Delete:
- `apps.*`
- `appVariableSources.*`

**Step 3: Remove app page imports and references**

Delete application page usage from `App.tsx`.

**Step 4: Run frontend typecheck**

Run:

```bash
cd frontend
npm run typecheck
```

Expected:
- FAIL with remaining app references.

### Task 7: Refactor deploy page to be service-driven only

**Files:**
- Modify: `frontend/src/pages/DeployPage.tsx`
- Modify: `frontend/src/pages/deploy/useDeployFormState.ts`
- Modify: `frontend/src/pages/deploy/useDeployOptionsLoader.ts`
- Modify: `frontend/src/pages/deploy/useDeployActions.ts`
- Modify: `frontend/src/pages/deploy/DeployForm.tsx`
- Modify: `frontend/src/pages/deploy/useDeployServerSelection.ts`

**Step 1: Remove app-context loading and state**

Delete:
- `appId`
- `appContext`
- `appDefaultsApplied`
- any `/deploy?app_id=...` bootstrap logic

**Step 2: Rebuild runtime-variable defaults from system/service/environment only**

Use:
- service template variables
- environment variables
- system variables

Do not read any deleted application field.

**Step 3: Remove service edit back-link to application pages**

Replace any “查看/编辑服务” link to point to system service config directly.

**Step 4: Block deploy when service target servers cannot be resolved**

Do not silently fall back to removed application/system defaults.

**Step 5: Run frontend typecheck again**

Run:

```bash
cd frontend
npm run typecheck
```

Expected:
- PASS

### Task 8: Simplify system and service forms to the minimal field set

**Files:**
- Modify: `frontend/src/pages/SystemEditPage.tsx`
- Modify: `frontend/src/pages/SystemListPage.tsx`
- Modify: `frontend/src/pages/ServiceEditPage.tsx`
- Modify: `frontend/src/pages/deploy/PreflightPanel.tsx`

**Step 1: Remove nonessential system fields from primary UI**

Hide or remove as primary controls:
- system default servers
- freeform description if not explicitly retained

Keep advanced variables only in an advanced section if still needed.

**Step 2: Promote structured service fields**

Show first-class controls for:
- pipeline
- repo
- service_dir/deploy_path
- update_script
- health_url/health_cmd
- process identity
- servers / servers_by_env

Keep raw JSON only as advanced escape hatch.

**Step 3: Run frontend typecheck**

Run:

```bash
cd frontend
npm run typecheck
```

Expected:
- PASS

### Task 9: Remove application references from docs, reports, and examples

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/runbooks/SYSTEM_SERVICE_MANAGEMENT.md`
- Modify: `frontend/src/pages/DatabaseToolsPage.tsx`

**Step 1: Replace “应用 -> 服务配置” descriptions**

Rewrite docs so configuration starts from systems.

**Step 2: Remove application-oriented SQL snippets and examples**

Delete or replace snippets querying the `applications` table.

**Step 3: Run quick text search**

Run:

```bash
rg -n "应用详情|/apps|Application|applications table|app_id|default_variables.release_service" README.md ARCHITECTURE.md docs frontend/src app
```

Expected:
- No stale app-layer references except migration notes.

### Task 10: Add migration and compatibility tests

**Files:**
- Modify: `tests/test_release_reliability_contract.py`
- Modify: `tests/test_iter36_release_orchestration_contract.py`
- Modify: `tests/test_backup_restore_contract.py`

**Step 1: Add a migration test for legacy configs**

Assert legacy application-backed data is either:
- normalized into system/service config
- or rejected with a clear conflict report

**Step 2: Add a backup/restore coverage test**

Assert exported config no longer includes `applications`.

**Step 3: Run the focused tests**

Run:

```bash
pytest tests/test_release_reliability_contract.py tests/test_iter36_release_orchestration_contract.py tests/test_backup_restore_contract.py -v
```

Expected:
- PASS

### Task 11: Run full verification

**Files:**
- Modify: any files needed from prior tasks
- Test: `tests/`

**Step 1: Run Python compile check**

Run:

```bash
python -m compileall -q app scripts main.py config_manager.py ssh_client.py
```

Expected:
- No output

**Step 2: Run frontend checks**

Run:

```bash
cd frontend
npm run typecheck
npm run build
```

Expected:
- PASS

**Step 3: Run backend tests**

Run:

```bash
cd ..
pytest tests -q
```

Expected:
- PASS

**Step 4: Commit**

If git metadata is available:

```bash
git add app frontend tests docs/plans README.md ARCHITECTURE.md
git commit -m "refactor: remove application layer from deploy flow"
```

If git metadata is not available in the workspace:

- Skip commit and record that limitation in the delivery note.

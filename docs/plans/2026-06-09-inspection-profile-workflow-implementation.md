# Inspection Profile Workflow Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add reusable inspection profiles with preview, context-bound confirmation, MCP run support, and automatic combined reports.

**Architecture:** Store first-version profiles in `config_kv` with seeded defaults. Reuse `resolve_servers_for_inspection`, `run_servers_batch_inspection`, and `generate_report_for_runs`. Add HTTP routes and MCP adapters as thin wrappers over service functions.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, React/TypeScript.

---

### Task 1: Backend Profile Service

**Files:**
- Create: `app/services/inspection_profiles.py`
- Test: `tests/test_inspection_profile_workflow.py`

**Steps:**
1. Write failing tests for default profile listing and preview.
2. Implement profile normalization, default seeding, target filtering, and confirmation phrase generation.
3. Run the targeted tests.

### Task 2: Profile Run + Report

**Files:**
- Modify: `app/services/inspection_profiles.py`
- Test: `tests/test_inspection_profile_workflow.py`

**Steps:**
1. Write failing tests that run a profile with mocked batch execution and report generation.
2. Validate confirmation text before execution.
3. Generate a combined report from successful run ids.
4. Run targeted tests.

### Task 3: HTTP and MCP Surface

**Files:**
- Modify: `app/api/inspection.py`
- Modify: `app/services/tool_adapters/inspection_tools.py`
- Modify: `app/services/risk_policy.py`
- Test: `tests/test_inspection_profile_workflow.py`
- Test: `tests/test_mcp_contract_sync.py`

**Steps:**
1. Add HTTP list/preview/run routes.
2. Add MCP profile list/preview/run tools.
3. Teach risk policy to use context-bound profile confirmation phrases.
4. Run MCP and profile tests.

### Task 4: Frontend Workflow

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/InspectionCenterPage.tsx`

**Steps:**
1. Add API client methods.
2. Load profiles on the inspection page.
3. Add common profile cards to the server tab.
4. Preview target count and show/copy the confirmation phrase before execution.

### Task 5: Verification

Run:

```bash
py -3 -m pytest tests/test_inspection_profile_workflow.py tests/test_mcp_contract_sync.py -q
py -3 -m pytest -q
```

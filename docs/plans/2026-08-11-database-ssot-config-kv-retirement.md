# Database SSOT And config_kv Retirement Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make database domain tables the only runtime source, repair `GET /api/v2/systems`, migrate legacy settings, and retire `config_kv` completely.

**Architecture:** Existing inventory tables become strict SSOTs. Small domain-specific settings tables replace generic KV storage, and one terminal forward migration copies legacy rows before dropping the old table.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, SQLite, pytest

---

### Task 1: Reproduce And Fix The Systems List Bug

**Files:**
- Modify: `app/api/deploy_v2.py`
- Test: `tests/test_systems_database_ssot.py`

**Steps:**
1. Add an API-level test that inserts `System`, `Service`, and environment rows without a `config_kv["systems"]` value.
2. Run the test and verify the endpoint returns an empty list before the fix.
3. Query `SystemRepository`, `ServiceRepository`, and the environment repository from the request session.
4. Run the focused test and verify names and counts come from database rows.

### Task 2: Add Domain Models And Repositories

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/db/repository.py`
- Modify: `app/db/__init__.py`
- Test: `tests/test_domain_settings_repository.py`

**Steps:**
1. Add failing repository tests for capability settings, retention policies,
   notifications, inspection profiles, workflow templates, deployment defaults,
   global variables, and system environments.
2. Add the dedicated models and narrow repositories.
3. Run repository tests and verify independent CRUD behavior.

### Task 3: Add And Verify The Terminal Migration

**Files:**
- Modify: `app/db/migrations/runner.py`
- Modify: `app/db/base.py`
- Test: `tests/test_config_kv_retirement_migration.py`

**Steps:**
1. Add failing migration tests using a temporary legacy SQLite database.
2. Add schema migrations for domain tables and `systems.message_routing`.
3. Add the idempotent data migration, explicit `crypto` to `crypto-trader`
   mapping, legacy asset/settings-bucket migration, reference validation, and
   terminal `DROP TABLE config_kv`.
4. Test successful copy, fresh database behavior, repeat execution, and unknown
   orphan rejection.

### Task 4: Switch Inventory Runtime Paths

**Files:**
- Modify: `app/config/systems.py`
- Modify: `app/config/environments.py`
- Modify: `app/domain/inventory/services.py`
- Modify: `app/api/deploy_v2.py`
- Modify: `app/services/tool_adapters/server_tools.py`
- Test: `tests/test_inventory_database_ssot.py`

**Steps:**
1. Add failing tests proving no config fallback is used.
2. Read and write systems, services, environments, and pipelines through their
   repositories only.
3. Preserve existing response shapes and remove config source labels.
4. Run inventory, deployment resolution, and MCP contract tests.

### Task 5: Switch Domain Settings Runtime Paths

**Files:**
- Modify: `app/services/tool_policy.py`
- Modify: `app/services/release_retention.py`
- Modify: `app/services/package_retention.py`
- Modify: `app/services/runtime_resources.py`
- Modify: `app/api/deploy/_shared.py`
- Modify: `app/api/deploy/history.py`
- Modify: `app/services/inspection_profiles.py`
- Modify: `config_manager.py`
- Test: related focused service tests

**Steps:**
1. Update tests to expect each domain repository.
2. Replace every `ConfigRepository` and generic config loader call.
3. Keep defaults in the owning domain module and persist only explicit changes.
4. Run focused policy, retention, notification, inspection, and workflow tests.

### Task 6: Retire Generic Config Infrastructure

**Files:**
- Modify: `main.py`
- Modify: `app/config/__init__.py`
- Modify: `app/api/config.py`
- Delete: `app/config/cache.py`
- Delete: `app/config/repository.py`
- Remove legacy runtime/maintenance migration modules and scripts
- Test: `tests/test_config_kv_retirement_contract.py`

**Steps:**
1. Add a failing static contract for forbidden runtime references.
2. Replace config export/import with domain repository serialization.
3. Remove startup config initialization, model/repository exports, caches,
   compatibility fallback paths, and obsolete maintenance jobs.
4. Verify no runtime source references `config_kv`, `ConfigKV`,
   `ConfigRepository`, `load_config`, `save_config`, or `load_config_cached`.

### Task 7: Validate Local Data And Commit

**Files:**
- Modify: relevant runbooks if behavior changed

**Steps:**
1. Back up the local SQLite database and run startup migration.
2. Verify six systems are returned and service/environment counts are correct.
3. Verify `config_kv` no longer exists and domain rows match migrated values.
4. Run focused tests, backend regression tests, startup preflight, and diff checks.
5. Commit the complete reviewed work and confirm the worktree contains no
   generated or temporary artifacts.

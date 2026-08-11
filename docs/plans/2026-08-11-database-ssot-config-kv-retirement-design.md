# Database SSOT And config_kv Retirement Design

## Context

`GET /api/v2/systems` still reads `config_kv["systems"]`, while migration
`082_003_cleanup_systems_kv` already removed that key. The database contains
six `systems` rows, so the endpoint returns an empty list even though the
inventory exists. Other runtime paths still read or write `config_kv` for
capability settings, retention policies, notifications, inspection profiles,
workflow templates, global variables, and deployment defaults.

## Decision

SQLite remains the only runtime datastore. Inventory uses the existing domain
tables, and mutable settings use small domain-specific tables with JSON payloads
where a fully typed schema would add churn without improving safety.

### Inventory ownership

- `systems` owns system metadata, variables, default servers, and
  `message_routing`.
- `services` owns services. Runtime readers no longer merge `System.services`.
- `system_environments` owns per-system environments and overrides. Runtime
  readers no longer use `System.environments`.
- Existing `servers`, `jump_hosts`, `server_groups`, and `pipelines` remain the
  authoritative sources for their domains.
- The known orphan alias `crypto` is migrated to `crypto-trader`. Migration
  fails on unknown service system names instead of silently orphaning data.

### Settings ownership

- `capability_settings`: singleton capability-server payload.
- `retention_policies`: one row for each of `release`, `package`, and `runtime`.
- `notification_settings`: singleton notification payload.
- `inspection_profiles`: one row per user-defined profile.
- `workflow_templates`: one row per user-defined template; preset templates
  remain code constants.
- `deployment_defaults`: singleton deployment-default payload.
- `global_variables`: one row per variable.

These are domain tables, not a renamed generic key/value store.

## Migration

Startup creates the new schema, then a one-time forward migration reads an
existing `config_kv` table, copies recognized keys, backfills system
environments and services, and imports legacy `systems`, `servers`,
`jump_hosts`, `server_groups`, and `pipelines` asset buckets. The older nested
`settings` bucket is expanded into its domain settings before validation.
Existing domain rows take precedence over stale legacy assets. The migration
then validates references and drops `config_kv` in the same transaction. Fresh
databases skip the legacy copy and receive domain defaults directly.

Historical migrations may mention `config_kv`, but application, maintenance,
and startup code must not import or query it after the terminal migration.
`ConfigKV`, `ConfigRepository`, generic config cache/load/save APIs, and legacy
reconciliation jobs are removed.

## API Behavior

- `GET /api/v2/systems` queries `SystemRepository`, including service and
  environment counts from their domain tables.
- System detail, service, environment, deployment resolution, and MCP inventory
  tools use the same database repositories.
- Database failures are surfaced and logged. They must not be converted to an
  empty successful response.
- Existing API payload shapes are preserved where practical.

## Resource And Security Impact

No new process, cache, poller, or external database is introduced. The design
uses the existing SQLite connection and small JSON rows, which is appropriate
for a single-user local deployment. Existing authentication and authorization
boundaries remain unchanged.

## Verification

- Reproduce the systems-list bug with a database-only API test.
- Test migration from representative legacy keys and assert `config_kv` is
  dropped.
- Test service alias migration and rejection of unknown orphan systems.
- Test domain setting repositories and system environment CRUD.
- Add a static retirement contract that rejects runtime references to
  `ConfigKV`, `ConfigRepository`, and generic config loaders.
- Run focused tests, migration against a copied local database, broader backend
  tests, and startup preflight.

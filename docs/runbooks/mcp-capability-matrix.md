# MCP Capability Matrix

Updated: 2026-06-09

This matrix is the current OPS MCP/HTTP tool capability map for local small-team operations.

Naming rule:

- HTTP Tool API and backend registry use dotted names, for example `ops.inspection.run_servers_batch`.
- MCP stdio/JSON-RPC exposes MCP-safe aliases by replacing dots with underscores, for example `ops_inspection_run_servers_batch`.
- Remote HTTP MCP accepts the same MCP payload shape, while `/api/v2/tools/call` keeps dotted names.

## Default Safety Baseline

The default capability profile is no longer "read-only only".

- Low-risk read tools are auto-callable when token scopes allow them.
- Path A inspection execution (`ops.inspection.run_*`) is enabled for AI/MCP tool tokens only when `confirm_text` matches the backend confirmation phrase. High-risk calls are still taskized/audited.
- Inspection profile execution (`ops.inspection.profile.run` and `ops.inspection.profile.retry_issues`) is preview-first and requires the returned `RUN <profile> <count> <fingerprint>` confirmation phrase.
- Agent runtime tools (`ops.agent.*`) stay hidden unless `agent_runtime_enabled=true`.
- Deploy execution, rollback, config write, server write, package cleanup, runtime cleanup, DB write/DML, and destructive deletes remain behind explicit capability gates, scopes, and confirmation.

## Release And Deployment Tools

| HTTP tool | MCP alias | Type | Scope(s) | Needs backend | stdio offline | Risk | Recommended before | Recommended after |
|---|---|---:|---|---:|---:|---|---|---|
| `ops.connection_status` | `ops_connection_status` | read | none | no | yes | low | - | start backend / pass token if offline |
| `ops.inspect_local_package` | `ops_inspect_local_package` | read | `ops:read` | no for stdio, yes for HTTP | yes | low | - | `ops.upload_package` |
| `ops.upload_package` | `ops_upload_package` | write | `package:write` | yes | dry-run only | high | `ops.inspect_local_package` | `ops.get_package_checksum`, `ops.create_deploy_plan` |
| `ops.prepare_release_from_local_package` | `ops_prepare_release_from_local_package` | write / plan | `package:write`, `deploy:plan`, `deploy:precheck` | yes | dry-run inspect only | high | `ops.inspect_local_package` | `ops.execute_deploy_plan` after explicit confirmation |
| `ops.create_deploy_plan` | `ops_create_deploy_plan` | plan | `ops:read`, `deploy:plan` | yes | no | medium | package uploaded | `ops.run_precheck` |
| `ops.run_precheck` | `ops_run_precheck` | plan | `ops:read`, `deploy:precheck` | yes | no | medium | `ops.create_deploy_plan` | `ops.get_deploy_confirmation` |
| `ops.get_deploy_confirmation` | `ops_get_deploy_confirmation` | read / plan | `ops:read`, `deploy:plan` | yes | no | medium | `ops.run_precheck` | user confirmation, then `ops.execute_deploy_plan` |
| `ops.execute_deploy_plan` | `ops_execute_deploy_plan` | execute | `deploy:execute` | yes | no | high | exact confirmation text | `ops.get_deployment_status`, `ops.get_deployment_report` |
| `ops.get_deployment_status` | `ops_get_deployment_status` | read | `ops:read` | yes | no | low | deployment id | `ops.get_deployment_logs` |
| `ops.get_deployment_logs` | `ops_get_deployment_logs` | read / stream | `ops:read` | yes | no | low | deployment id | `ops.get_deployment_report` |
| `ops.get_deployment_report` | `ops_get_deployment_report` | read | `ops:read` | yes | no | low | deployment id | failure analysis / rollback planning |
| `ops.create_rollback_plan` | `ops_create_rollback_plan` | plan | `ops:read`, `deploy:plan` | yes | no | high | deployment report | `ops.execute_rollback_plan` after explicit confirmation |
| `ops.execute_rollback_plan` | `ops_execute_rollback_plan` | execute | `deploy:execute` | yes | no | critical | rollback plan confirmation text | `ops.get_deployment_report` |

### Recommended Local Package Release Workflow

1. `ops.inspect_local_package` with `local_path` and `calculate_sha256=true`.
2. `ops.prepare_release_from_local_package` with `local_path`, `system`, `service`, `environment`, and optional `servers` / `server_group`.
3. Review returned `package.checksum`, `precheck`, `confirmation`, `blockers`, `warnings`, and `confirm_text`.
4. Only after explicit user approval, call `ops.execute_deploy_plan` with `plan_id` and the exact confirmation text.
5. Use `ops.get_deployment_status`, `ops.get_deployment_logs`, and `ops.get_deployment_report` for follow-up.

## Server And Inspection Tools

Path A inspection tools are the primary path for requests that mention "巡检", "批量巡检", "合规检查", or "检查一组服务器". They create `inspection_runs`, issues, reports, and audit records.

Path B server probes (`ops.check_disk`, `ops.check_process`, `ops.tail_service_log`, and similar tools) are single-shot read probes. Use them for targeted troubleshooting, not as a replacement for Path A inspection.

| HTTP tool | MCP alias | Type | Scope(s) | Needs backend | stdio offline | Risk | Recommended before | Recommended after |
|---|---|---:|---|---:|---:|---|---|---|
| `ops.list_servers` | `ops_list_servers` | read | `ops:read`, `server:read` | yes | no | low | - | `ops.list_server_groups`, `ops.inspection.run_servers_batch` |
| `ops.list_server_groups` | `ops_list_server_groups` | read | `ops:read`, `server:read` | yes | no | low | - | filter by `group` in `ops.list_servers` |
| `ops.inspection.overview` | `ops_inspection_overview` | read | `ops:read` | yes | no | low | - | choose inspection target |
| `ops.inspection.categories` | `ops_inspection_categories` | read | `ops:read` | yes | no | low | - | select category codes |
| `ops.inspection.profile.list` | `ops_inspection_profile_list` | read | `ops:read` | yes | no | low | - | `ops.inspection.profile.preview` |
| `ops.inspection.profile.preview` | `ops_inspection_profile_preview` | read | `ops:read` | yes | no | low | profile id | `ops.inspection.profile.run` |
| `ops.inspection.profile.run` | `ops_inspection_profile_run` | execute | `ops:read`, `ops:write` | yes | no | high | preview result, exact `RUN <profile> <count> <fingerprint>` | `ops.inspection.get_run`, `ops.inspection.generate_report` |
| `ops.inspection.profile.retry_issues` | `ops_inspection_profile_retry_issues` | execute | `ops:read`, `ops:write` | yes | no | high | open inspection issues, preview result, exact `RUN <profile> <count> <fingerprint>` | `ops.inspection.get_run`, `ops.inspection.list_issues` |
| `ops.inspection.list_item_configs` | `ops_inspection_list_item_configs` | read | `ops:read` | yes | no | low | - | `ops.inspection.toggle_item_config`, `ops.inspection.update_item_config` |
| `ops.inspection.update_item_config` | `ops_inspection_update_item_config` | write | `ops:read`, `ops:write` | yes | no | medium | `ops.inspection.list_item_configs` | `ops.inspection.run_servers_batch` |
| `ops.inspection.toggle_item_config` | `ops_inspection_toggle_item_config` | write | `ops:read`, `ops:write` | yes | no | medium | `ops.inspection.list_item_configs` | `ops.inspection.run_servers_batch` |
| `ops.inspection.update_item_rules` | `ops_inspection_update_item_rules` | write | `ops:read`, `ops:write` | yes | no | medium | `ops.inspection.get_item_config` | `ops.inspection.run_servers_batch` |
| `ops.inspection.run_server` | `ops_inspection_run_server` | execute | `ops:read`, `ops:write` | yes | no | high | `ops.list_servers`, exact `confirm_text` | `ops.inspection.get_run` |
| `ops.inspection.run_servers_batch` | `ops_inspection_run_servers_batch` | execute | `ops:read`, `ops:write` | yes | no | high | `ops.list_server_groups`, exact `confirm_text` | `ops.inspection.get_run` |
| `ops.inspection.run_project` | `ops_inspection_run_project` | execute | `ops:read`, `ops:write` | yes | no | high | project target, exact `confirm_text` | `ops.inspection.get_run` |
| `ops.inspection.run_combined` | `ops_inspection_run_combined` | execute | `ops:read`, `ops:write` | yes | no | high | project target, exact `confirm_text` | `ops.inspection.get_run` |
| `ops.inspection.list_runs` | `ops_inspection_list_runs` | read | `ops:read` | yes | no | low | - | `ops.inspection.get_run` |
| `ops.inspection.get_run` | `ops_inspection_get_run` | read | `ops:read` | yes | no | low | run id | `ops.inspection.get_run_raw_output` |
| `ops.inspection.get_run_raw_output` | `ops_inspection_get_run_raw_output` | read | `ops:read` | yes | no | low | run id | diagnose risk evidence |
| `ops.inspection.list_issues` | `ops_inspection_list_issues` | read | `ops:read` | yes | no | low | run id | `ops.inspection.generate_report` |
| `ops.inspection.update_issue` | `ops_inspection_update_issue` | write | `ops:read`, `ops:write` | yes | no | medium | issue id | `ops.inspection.generate_report` |
| `ops.inspection.delete_runs` | `ops_inspection_delete_runs` | write | `ops:read`, `ops:write` | yes | no | high | run ids, exact `confirm_text` | cleanup only |
| `ops.inspection.delete_issue` | `ops_inspection_delete_issue` | write | `ops:read`, `ops:write` | yes | no | medium | issue id, exact `confirm_text` | cleanup only |
| `ops.inspection.generate_report` | `ops_inspection_generate_report` | write | `ops:read`, `ops:write` | yes | no | medium | run id | archive report |

### Recommended Inspection Workflow

1. For routine grouped inspections, call `ops.inspection.profile.list`, choose a profile such as `crypto-test-daily`, then call `ops.inspection.profile.preview`.
2. Ask the user for the returned confirmation phrase, then call `ops.inspection.profile.run` with `confirm_text`.
3. For ad-hoc target checks, use `ops.list_server_groups`, `ops.list_servers(group="crypto")`, and `ops.inspection.list_item_configs(scope_type="SERVER")`, then call `ops.inspection.run_servers_batch` with exact `confirm_text`.
4. `ops.inspection.get_run(run_id=...)` until `status` is `SUCCESS`, `PARTIAL_SUCCESS`, or `FAILED`.
5. `ops.inspection.list_issues(run_id=...)` to triage HIGH / MEDIUM / LOW issues.
6. `ops.inspection.generate_report(run_id=..., format="md")` to archive the report.
7. After remediation, call `ops.inspection.profile.retry_issues` without `confirm_text` to preview only OPEN / PROCESSING issue servers, then rerun it with the returned `RUN <profile> <count> <fingerprint>` phrase.

Notes:

- `ops.inspection.run_servers_batch` accepts `server_ids`, `groups`, `group`, and `all_servers`, then merges targets.
- `ops.inspection.profile.retry_issues` keeps the selected profile categories and runtime knobs, but replaces targets with servers that still have OPEN / PROCESSING inspection issues.
- Category values must use full uppercase codes such as `LOGIN_SECURITY`, `ACCOUNT_SECURITY`, `PROCESS_PORT`, `DISK_USAGE`, and `BACKUP`.
- Servers with `inspectable=false` or `status != online` are skipped by default; pass `skip_disabled=false` only when debugging.

## 3-Tier Inspection Schedule Tools

These tools manage DB-backed DAILY / WEEKLY / MONTHLY inspection schedules. The DB tables are `inspection_tier_schedules`, `inspection_notification_routes`, and `inspection_cascade_policies`; YAML is not the source of truth for these policies.

| HTTP tool | MCP alias | Type | Scope(s) | Risk | Purpose |
|---|---|---:|---|---|---|
| `ops.tier.list` | `ops_tier_list` | read | `ops:read` | low | List DAILY/WEEKLY/MONTHLY schedules with cron, categories, retention, report, and last run status |
| `ops.tier.upsert` | `ops_tier_upsert` | write | `ops:write`, `ops:admin` | high | Create or update one tier schedule by `name` |
| `ops.tier.delete` | `ops_tier_delete` | write | `ops:write`, `ops:admin` | high | Soft-delete one tier schedule by setting `enabled=false` |
| `ops.notif_route.list` | `ops_notif_route_list` | read | `ops:read` | low | List severity/tier notification routes |
| `ops.notif_route.upsert` | `ops_notif_route_upsert` | write | `ops:write`, `ops:admin` | high | Upsert a notification route by severity and optional tier |
| `ops.cascade.list` | `ops_cascade_list` | read | `ops:read` | low | List cross-tier cascade policies |
| `ops.cascade.upsert` | `ops_cascade_upsert` | write | `ops:write`, `ops:admin` | high | Upsert one cascade policy by `name` |
| `ops.tier.run_now` | `ops_tier_run_now` | execute | `ops:write` | high | Trigger a tier inspection immediately and return the Path A run result |
| `ops.tier.approve` | `ops_tier_approve` | write | `ops:write`, `ops:admin` | high | Approve/unlock a tier that requires approval, usually MONTHLY first run |
| `ops.tier.history` | `ops_tier_history` | read | `ops:read` | low | Query historical inspection runs for one tier |

## Streaming Tool Results

The normal `tools/call` path is still available for all tools. Large-payload tools additionally support stream metadata and can be called through `POST /api/v2/tools/call/stream` or the MCP private extension `tools/call.stream`.

Current streamable tools:

- `ops.db.export_query_result`
- `ops.get_deployment_logs`
- `ops.export_diagnostics_report`

Streaming response shape:

- SSE events from `/api/v2/tools/call/stream`: `chunk`, `done`, `error`.
- MCP `tools/call.stream` content: text JSON with `stream` and `result` fields.

## Tool Token Recommendation

For an inspection assistant token, use:

```json
{
  "description": "Routine MCP inspection assistant",
  "scopes": ["ops:read", "ops:write"],
  "allow_write": true,
  "allow_prod": false,
  "expires_in_days": 90
}
```

Set `expires_in_days` to `0` only for reviewed non-expiring tokens. The Tool Token API persists and returns `description` so operators can distinguish routine inspection tokens from emergency or human-operated tokens.

For read-only inventory/diagnostic usage, restrict scopes to `["ops:read"]`.

For deploy execution, rollback, DB write, package cleanup, or admin schedule changes, use a separate admin-reviewed token and keep production permissions explicit.

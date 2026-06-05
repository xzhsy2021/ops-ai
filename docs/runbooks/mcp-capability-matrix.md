# MCP Capability Matrix

This matrix captures the local OPS MCP tools that matter most for a small-team local deployment workflow. Tool names below use MCP-safe aliases; the HTTP Tool API keeps the original dotted names.

| MCP tool | Type | Scope(s) | Needs OPS backend | stdio offline support | Risk | Recommended before | Recommended after |
|---|---:|---|---:|---:|---|---|---|
| `ops_connection_status` | read | none | no | yes | low | - | start backend / pass token if offline |
| `ops_inspect_local_package` | read | `ops:read` | no for stdio, yes for HTTP | yes | low | - | `ops_upload_package` or `ops_prepare_release_from_local_package` |
| `ops_upload_package` | write | `package:write` | yes | dry-run only without backend | high | `ops_inspect_local_package` | `ops_get_package_checksum`, `ops_create_deploy_plan` |
| `ops_get_package_checksum` | read | `ops:read` | yes | no | low | `ops_upload_package` | `ops_create_deploy_plan` |
| `ops_prepare_release_from_local_package` | write / plan | `package:write`, `deploy:plan`, `deploy:precheck` | yes | dry-run local inspect only | high | `ops_inspect_local_package` | `ops_execute_deploy_plan` only after explicit user confirmation |
| `ops_create_deploy_plan` | read / plan | `ops:read`, `deploy:plan` | yes | no | medium | package uploaded | `ops_run_precheck` |
| `ops_run_precheck` | read / plan | `ops:read`, `deploy:precheck` | yes | no | medium | `ops_create_deploy_plan` | `ops_get_deploy_confirmation` |
| `ops_get_deploy_confirmation` | read / plan | `ops:read`, `deploy:plan` | yes | no | medium | `ops_run_precheck` | user confirmation, then `ops_execute_deploy_plan` |
| `ops_execute_deploy_plan` | write / execute | `deploy:execute` | yes | no | high | confirmation text from backend | `ops_get_deployment_status`, `ops_get_deployment_report` |
| `ops_get_deployment_status` | read | `ops:read` | yes | no | low | deployment id | `ops_get_deployment_logs` |
| `ops_get_deployment_logs` | read | `ops:read` | yes | no | low | deployment id | `ops_get_deployment_report` |
| `ops_get_deployment_tasks` | read | `ops:read` | yes | no | low | deployment id | `ops_get_deployment_report` |
| `ops_get_deployment_report` | read | `ops:read` | yes | no | low | deployment id | failure analysis / rollback planning |
| `ops_create_rollback_plan` | read / plan | `ops:read`, `deploy:plan` | yes | no | high | deployment report | `ops_execute_rollback_plan` only after explicit user confirmation |
| `ops_execute_rollback_plan` | write / execute | `deploy:execute` | yes | no | critical | rollback plan confirmation text | `ops_get_deployment_report` |

## Recommended local package release workflow

1. `ops_inspect_local_package` with `local_path` and `calculate_sha256=true`.
2. `ops_prepare_release_from_local_package` with `local_path`, `system`, `service`, `environment`, and optional `servers` / `server_group`.
3. Review returned `package.checksum`, `precheck`, `confirmation`, `blockers`, `warnings`, and `confirm_text`.
4. Only after explicit user approval, call `ops_execute_deploy_plan` with `plan_id` and the exact confirmation text.
5. Use `ops_get_deployment_status`, `ops_get_deployment_logs`, and `ops_get_deployment_report` for follow-up.

## Policy gates

Real upload and prepare workflows require all of the following:

- OPS backend running and reachable through `OPS_BASE_URL`.
- `OPS_TOOL_TOKEN` or web session with matching permissions.
- Token scopes include `package:write`, `deploy:plan`, and `deploy:precheck` for `ops_prepare_release_from_local_package`.
- Capability settings: `read_only=false`, `allow_package_write=true`, `allow_deploy_plan=true`.
- Deployment execution remains separate and additionally requires `deploy:execute`, `allow_deploy_execute=true`, exact confirmation text, and production permissions for production environments.

---

## Server & Inspection Tools (added 2026-06-03)

These tools cover the read-only inspection workflow; they are recommended for AI agents that need to enumerate servers by group and run read-only security checks before deciding on a deployment.

| MCP tool | Type | Scope(s) | Needs OPS backend | stdio offline support | Risk | Recommended before | Recommended after |
|---|---:|---|---:|---:|---|---|---|
| `ops.list_servers` | read | `ops:read`, `server:read` | yes | no | low | - | `ops.list_server_groups` / `ops.inspection.run_servers_batch` |
| `ops.list_server_groups` | read | `ops:read`, `server:read` | yes | no | low | - | filter by `group` in `ops.list_servers` or `ops.inspection.run_servers_batch` |
| `ops.inspection.overview` | read | `ops:read` | yes | no | low | - | start an inspection |
| `ops.inspection.categories` | read | `ops:read` | yes | no | low | - | start an inspection |
| `ops.inspection.list_item_configs` | read | `ops:read` | yes | no | low | - | toggle / reorder items |
| `ops.inspection.toggle_item` / `reorder_items` | write | `ops:read`, `ops:write` | yes | no | medium | `ops.inspection.list_item_configs` | `ops.inspection.run_servers_batch` |
| `ops.inspection.run_server` | write / execute | `ops:read`, `ops:write` | yes | no | high | `ops.list_servers` | `ops.inspection.get_run` |
| `ops.inspection.run_servers_batch` | write / execute | `ops:read`, `ops:write` | yes | no | high | `ops.list_server_groups` / `ops.list_servers` | `ops.inspection.get_run` |
| `ops.inspection.get_run` / `list_runs` | read | `ops:read` | yes | no | low | run id | `ops.inspection.get_raw_output` |
| `ops.inspection.get_raw_output` | read | `ops:read` | yes | no | low | run id | diagnose risk issues |
| `ops.inspection.list_issues` / `update_issue` | read / write | `ops:read`, `ops:write` | yes | no | low / medium | run id | `ops.inspection.generate_report` |
| `ops.inspection.delete_runs` / `delete_issue` | write | `ops:read`, `ops:write` | yes | no | medium | run / issue id | only for cleaning orphaned RUNNING records or stale risk issues |
| `ops.inspection.generate_report` | write | `ops:read`, `ops:write` | yes | no | medium | run id | archive report |

### Recommended inspection workflow

1. `ops.list_server_groups` → choose target group name (e.g. `crypto`).
2. `ops.list_servers(group="crypto")` → confirm the servers you are about to inspect.
3. `ops.inspection.list_item_configs(scope_type="SERVER")` → confirm enabled categories.
4. `ops.inspection.run_servers_batch(groups=["crypto"], categories=["LOGIN_SECURITY","ACCOUNT_SECURITY","PROCESS_PORT","DISK","BACKUP"])`.
5. `ops.inspection.get_run(run_id=...)` → poll until `status` is `SUCCESS` / `PARTIAL_SUCCESS` / `FAILED`.
6. `ops.inspection.list_issues(run_id=...)` → triage HIGH / MEDIUM / LOW.
7. `ops.inspection.generate_report(run_id=..., format="md")` → archive the report.

### Notes

- `ops.inspection.run_servers_batch` always requires **human confirmation** (the MCP layer refuses auto-call). It accepts `server_ids / groups / group / all_servers` and merges them.
- Categories must use the full uppercase codes (`LOGIN_SECURITY`, `ACCOUNT_SECURITY`, `PROCESS_PORT`, `DISK`, `BACKUP` …). Abbreviations like `["login","account","process","disk","backup"]` will silently match only the last two.
- Servers with `inspectable=false` or `status != online` are skipped by default; pass `skip_disabled=false` only when debugging.

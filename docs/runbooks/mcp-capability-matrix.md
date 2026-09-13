# MCP Capability Matrix

Updated: 2026-09-12（第 13 轮：按注册表逐行核对，删除未注册工具的矩阵行）

This matrix is the current OPS MCP/HTTP tool capability map for local small-team operations. Every tool name listed below is verified against the live tool registry; capabilities that only exist in the OPS web UI / HTTP API are labelled as such instead of being listed as tools.

> For risk disposition of high-risk / write tools, see [HIGH_RISK_CAPABILITY_ASSESSMENT.md](./HIGH_RISK_CAPABILITY_ASSESSMENT.md). For the message-level execution plan (`ops.approval.prepare_plan` / `ops.approval.execute_plan`) and its step types, see [2026-08-06-message-execution-plan-design.md](../plans/2026-08-06-message-execution-plan-design.md). For the channel-neutral qclaw approval integration (Matrix / WeChat / Telegram), temporary self-approval, and the normalized `message_context` contract, see [qclaw-channel-approval-integration.md](../qclaw-channel-approval-integration.md).

Naming rule:

- HTTP Tool API and backend registry use dotted names, for example `ops.inspection.run_servers_batch`.
- MCP stdio/JSON-RPC exposes MCP-safe aliases by replacing dots with underscores, for example `ops_inspection_run_servers_batch`.
- Remote HTTP MCP accepts the same MCP payload shape, while `/api/v2/tools/call` keeps dotted names.

## Tool Exposure Profiles

OPS keeps the full registered tool catalog for administration, but AI/MCP discovery defaults to a smaller natural-language operations profile:

- `daily_ops` is the default for `/api/v2/tools`, `/api/v2/capabilities`, MCP `tools/list`, `ops://tools`, and stdio MCP. It exposes workflow-first inspection, reports, risk triage, diagnostics, read-only inventory, and guarded inspection execution.
- `expert` expands non-destructive troubleshooting tools while still hiding destructive/admin write families.
- `admin_full` exposes the full registered catalog for the OPS web tool-management page and explicit administrator maintenance.

This is a visibility profile only. It does not delete backend tools. Tool calls are still governed by token scopes, capability switches, risk policy, confirmation phrases, taskization, and audit logging.

## Default Safety Baseline

The default capability profile is no longer "read-only only".

- Low-risk read tools are auto-callable when token scopes allow them.
- Path A inspection execution (`ops.inspection.run_*`) is enabled for AI/MCP tool tokens only when `confirm_text` matches the backend confirmation phrase. For ad-hoc batch inspection, call `ops.inspection.preview_servers_batch` first and use its short Chinese phrase (`确认巡检 <fingerprint>`). High-risk calls are still taskized/audited.
- Inspection profile execution (`ops.inspection.profile.run` and `ops.inspection.profile.retry_issues`) is preview-first and requires the returned `RUN <profile> <count> <fingerprint>` confirmation phrase.
- Agent runtime capabilities (page/API only, no registered MCP tool) stay hidden unless `agent_runtime_enabled=true`.
- Deploy execution, rollback, config write, server write, package cleanup, runtime cleanup, DB write/DML, and destructive deletes remain behind explicit capability gates, scopes, and confirmation.
- `ops.inspection.run_servers_batch` is the preferred inspection entry for AI agents. It works with `ops.inspection.preview_servers_batch` for preview-first confirmation. All inspection execution is audited through `inspection_runs`, issues, reports, operation jobs, tool-call logs, and audit logs.

## Release And Deployment Tools

| HTTP tool | MCP alias | Type | Scope(s) | Needs backend | stdio offline | Risk | Recommended before | Recommended after |
|---|---|---:|---|---:|---:|---|---|---|
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
| `ops.inspection.profile.list` | `ops_inspection_profile_list` | read | `ops:read` | yes | no | low | - | `ops.inspection.profile.preview` |
| `ops.inspection.profile.preview` | `ops_inspection_profile_preview` | read | `ops:read` | yes | no | low | profile id | `ops.inspection.profile.run` |
| `ops.inspection.profile.run` | `ops_inspection_profile_run` | execute | `ops:read`, `ops:write` | yes | no | high | preview result, exact `RUN <profile> <count> <fingerprint>` | `ops.inspection.get_run_raw_output`, `ops.inspection.generate_report` |
| `ops.inspection.profile.retry_issues` | `ops_inspection_profile_retry_issues` | execute | `ops:read`, `ops:write` | yes | no | high | open inspection issues, preview result, exact `RUN <profile> <count> <fingerprint>` | `ops.inspection.get_run_raw_output`, `ops.inspection.list_runs` |
| `ops.inspection.list_item_configs` | `ops_inspection_list_item_configs` | read | `ops:read` | yes | no | low | - | `ops.inspection.list_runs` (item configs are edited in the OPS web inspection page, not over MCP) |
| `ops.inspection.run_server` | `ops_inspection_run_server` | execute | `ops:read`, `ops:write` | yes | no | high | `ops.list_servers`, exact `confirm_text` | `ops.inspection.get_run_raw_output` |
| `ops.inspection.preview_servers_batch` | `ops_inspection_preview_servers_batch` | read / preview | `ops:read` | yes | no | low | group/server target | `ops.inspection.run_servers_batch` |
| `ops.inspection.run_servers_batch` | `ops_inspection_run_servers_batch` | execute | `ops:read`, `ops:write` | yes | no | high | `ops.inspection.preview_servers_batch`, exact `确认巡检 <fingerprint>` | `ops.inspection.get_run_raw_output` |
| `ops.inspection.list_runs` | `ops_inspection_list_runs` | read | `ops:read` | yes | no | low | - | `ops.inspection.get_run_raw_output` |
| `ops.inspection.get_run_raw_output` | `ops_inspection_get_run_raw_output` | read | `ops:read` | yes | no | low | run id | diagnose risk evidence |
| `ops.inspection.generate_report` | `ops_inspection_generate_report` | write | `ops:read`, `audit:read` | yes | no | low | run id | archive one-run report |
| `ops.inspection.generate_report_for_runs` | `ops_inspection_generate_report_for_runs` | write | `ops:read`, `audit:read` | yes | no | low | run ids | archive merged batch/group report |

### Recommended Inspection Workflow

0. For natural-language requests such as "巡检 crypto 测试服务器并生成报告", call `ops.inspection.preview_servers_batch(groups=["crypto"])` first. If it returns a confirmation phrase, ask the user to confirm, then call `ops.inspection.run_servers_batch` with `confirm_text`.
1. For all-server requests such as "巡检全部服务器，按分组分批巡检并输出报告", use `ops.inspection.preview_servers_batch(all_servers=True)` and `ops.inspection.run_servers_batch` with appropriate batch_size and concurrency.
2. For routine grouped inspections, call `ops.inspection.profile.list`, choose a profile such as `crypto-test-daily`, then call `ops.inspection.profile.preview`.
3. Ask the user for the returned confirmation phrase, then call `ops.inspection.profile.run` with `confirm_text`.
4. For ad-hoc target checks, use `ops.list_server_groups`, `ops.list_servers(group="crypto")`, and `ops.inspection.list_item_configs(scope_type="SERVER")`, then call `ops.inspection.preview_servers_batch`.
5. Ask the user to approve the previewed target count, skipped servers, batch plan, and returned `确认巡检 <fingerprint>` phrase; call `ops.inspection.run_servers_batch` with that exact `confirm_text`.
6. `ops.inspection.get_run_raw_output(run_id=...)` until `status` is `SUCCESS`, `PARTIAL_SUCCESS`, or `FAILED`.
7. `ops.inspection.get_run_raw_output(run_id=...)` to read raw evidence, then `ops.inspection.generate_report(run_id=..., format="md")` to archive findings (inspection issues are triaged in the OPS web inspection page — there is no MCP issue tool).
8. Use `ops.inspection.generate_report(run_id=..., format="md")` for one run, or `ops.inspection.generate_report_for_runs(run_ids=[...], format="md")` for grouped/batch runs.
9. After remediation, call `ops.inspection.profile.retry_issues` without `confirm_text` to preview only OPEN / PROCESSING issue servers, then rerun it with the returned `RUN <profile> <count> <fingerprint>` phrase.

Notes:

- `ops.inspection.preview_servers_batch` accepts `server_ids`, `groups`, `group`, and `all_servers`, then merges targets and returns the exact short Chinese confirmation phrase for `ops.inspection.run_servers_batch`.
- `ops.inspection.run_servers_batch` now requires the preview phrase so AI agents do not have to ask users to type a long tool-name string.
- For all-server grouped inspections, use `ops.inspection.preview_servers_batch(all_servers=True)` and `ops.inspection.run_servers_batch` with groups to ensure each group has a visible target count, skipped count, confirmation phrase, execution record, and report path.
- `ops.inspection.profile.retry_issues` keeps the selected profile categories and runtime knobs, but replaces targets with servers that still have OPEN / PROCESSING inspection issues.
- UI one-click confirmation only changes the browser workflow. AI agents must keep using the preview-first MCP flow: read `confirmation.confirm_text` from `ops.inspection.profile.preview`, `ops.inspection.profile.retry_issues`, or `ops.inspection.preview_servers_batch`; get explicit user approval; then pass that exact value as `confirm_text` to the execution call.
- Category values must use full uppercase codes such as `LOGIN_SECURITY`, `ACCOUNT_SECURITY`, `PROCESS_PORT`, `DISK_USAGE`, and `BACKUP`.
- Servers with `inspectable=false` or `status != online` are skipped by default; pass `skip_disabled=false` only when debugging.

## Security Daily Report Tools

每日安全巡检日报工具：从已启用安全监控模块的服务器现场采集日报（aureport 登录记录/登录失败/账户变更/fail2ban 封禁/系统负载），解析判险后写入风险台账并归档到报告中心（HTML + Markdown 双格式报告，报告含巡检到的服务器清单）。

| HTTP tool | MCP alias | Type | Scope(s) | Risk | Purpose |
|---|---|---:|---|---|---|
| `ops.security_report.summarize` | `ops_security_report_summarize` | read | `ops:read`, `server:read` | low | 汇总已采集的每日安全巡检日报，返回结构化风险项（风险等级/登录失败/封禁IP/负载）供 AI 分析 |
| `ops.security_report.collect` | `ops_security_report_collect` | write | `ops:read`, `server:read` | medium | 触发一次每日日报现场采集（需确认短语）；也可用 `ops.inspection.run_security_daily` 走任务中心后台采集 |
| `ops.security_report.get_daily_report` | `ops_security_report_get_daily_report` | read | `ops:read`, `server:read` | low | 按报告日期获取安全日报归档报告（HTML/Markdown 双格式）：report_id、在线查看/下载链接与文件正文（含巡检到的服务器清单）；`report_date` 缺省返回最近一期 |
| `ops.security_module.probe` | `ops_security_module_probe` | read | `ops:read`, `server:read` | low | 只读诊断服务器上安全监控模块/日报脚本/fail2ban/auditd 状态 |
| `ops.security_module.install` | `ops_security_module_install` | write | `ops:write`, `server:read` | medium | 将服务器标记为「已启用每日安全日报采集」，可立即现场采集一次 |

### Recommended Security Daily Flow

1. `ops.security_report.summarize` 查看已采集日报与风险项；`ops.security_report.collect`（或 `ops.inspection.run_security_daily`，后台任务化）触发采集。
2. `ops.security_report.get_daily_report(report_date="YYYY-MM-DD", format="html"|"md")` 获取某期报告的正文内容与下载链接；`format` 缺省返回 html + md 双格式。
3. 高危项可通过 `ops.risk.list` 查询风险台账。

## 3-Tier Inspection Schedules (web UI / HTTP only, no MCP tools)

DAILY / WEEKLY / MONTHLY inspection schedules, notification routes, and cascade policies are managed in the **OPS web UI and HTTP API**. 第 13 轮按注册表逐行核对：`ops.tier.*` / `ops.notif_route.*` / `ops.cascade.*` 没有任何注册定义，因此不再作为 MCP 能力列出。The DB tables are `inspection_tier_schedules`, `inspection_notification_routes`, and `inspection_cascade_policies`; YAML is not the source of truth for these policies. AI/MCP clients should use the inspection profile tools (`ops.inspection.profile.*`) instead.

| HTTP tool | MCP alias | Type | Scope(s) | Risk | Purpose |
|---|---|---:|---|---|---|

## Matrix Package Tools

Matrix 文件对接：从 Matrix 房间（qclaw 所在群/私聊）拉取用户发送的文件附件，经 E2EE 自动解密后入库文件中心并按需发布。**支持任意普通文件格式**（.txt/.pdf/.log 等与部署包均可入库）；发版流程强制校验制品格式（见下方「入库自由、发布受限」）。加密房间由 OPS 专用设备（`MATRIX_DEVICE_ID`）解密；仅能解密该设备创建之后新发送的媒体（room key 不补发）。详见 [matrix-deploy-integration.md](../matrix-deploy-integration.md) 与 [MATRIX_E2EE_SETUP.md](./MATRIX_E2EE_SETUP.md)。

| HTTP tool | MCP alias | Type | Scope(s) | Risk | Purpose |
|---|---|---:|---|---|---|
| `ops.matrix.scan_media_events` | `ops_matrix_scan_media_events` | read | `ops:read` | low | 预览房间内匹配的媒体事件（sender / msgtype / 时间窗口 / 文件名），任意文件类型均可见；返回 event_id、文件名与是否加密；不下载 |
| `ops.matrix.pull_attachment` | `ops_matrix_pull_attachment` | write | `ops:read`, `package:write` | medium | 拉取发送者最新附件到文件中心（下载 → E2EE 解密 → SHA256 → 入库），**不限文件格式**；需确认短语 `CONFIRM ops.matrix.pull_attachment`（schema 已暴露 `confirm_text`）。普通文件的 `next_actions` 会提示仅入库留存 |
| `ops.matrix.deploy_from_matrix` | `ops_matrix_deploy_from_matrix` | execute | `ops:read`, `package:write`, `deploy:execute` | high | 完整链路：拉附件 → 文件中心 → 排队发布，一次调用 + 一次确认。**仅接受部署包格式制品** |

### 入库自由、发布受限

- **入库**：Matrix 拉取不限扩展名，`.txt` / `.pdf` / `.log` / `.conf` 等普通文件均可留存（大小上限 `max_upload_size_mb` 仍生效）。开关：`MATRIX_PULL_ALLOW_ANY_EXTENSION=0` 恢复部署包白名单。
- **发布**：所有发版入口（执行计划 RELEASE 步骤 / `deploy_from_matrix` / Web 发布表单）强制校验制品格式——仅接受 `.tar.gz` / `.tgz` / `.tar` / `.zip` / `.jar` / `.war` / `.gz` / `.bin`；普通文件被拒绝且 RELEASE 步骤转 FAILED 并提示原因。
- Agent 判断依据：pull 结果的 `next_actions` 字段（部署包→给出发布建议；普通文件→提示仅留存）。

### Recommended Matrix Deploy Flow

独立调用路径（每步单独确认）：

1. `ops_matrix_scan_media_events(room_id=..., sender=...)` 确认房间内最新媒体事件。
2. `ops_matrix_pull_attachment(room_id=..., sender=..., confirm_text="CONFIRM ops.matrix.pull_attachment")` → `{package_name, sha256}`。
3. `ops_create_deploy_plan(package_name=...)` → `ops_execute_deploy_plan(plan_id=..., confirm_text=...)`。

**推荐：执行计划批量审批**（qclaw/Element 消息流，一次审批完成拉取 + 发布）：

```json
ops_approval_prepare_plan(steps=[
  {"step_key": "pull", "action_type": "MATRIX_PULL",
   "parameters": {"room_id": "!room:x", "sender": "@alice:x", "minutes": 30},
   "dependencies": []},
  {"step_key": "release", "action_type": "RELEASE",
   "parameters": {}, "dependencies": ["pull"]}
])
```

- 授权人回复描述性确认短语（批准<动作> <system>@<env> <指纹>）后步骤自动顺序执行；`MATRIX_PULL` 结果的 `package_name` 自动回填到依赖的 `RELEASE` 步骤（显式指定则优先）。
- `MATRIX_PULL` 支持任意文件格式；但 RELEASE 强制校验制品格式——普通文件会被拒绝（步骤 FAILED）。拉取普通文件时不要编排 RELEASE 步骤。
- 拉取失败时计划 FAILED，后续 RELEASE 不执行；计划内步骤不再要求各工具的 `confirm_text`。
- 也可用 `ops_matrix_deploy_from_matrix` 一步到位（拉取 + 发布单次调用，仅部署包格式）。



## Approval And Execution Plan Tools

Message-level execution plans consolidate many high-risk actions into one approval. See the [design doc](../plans/2026-08-06-message-execution-plan-design.md) for the manifest/digest and step model.

Registered approval tools (all gated by the one-time short code, not by token scope):

| HTTP tool | MCP alias | Type | Scope(s) | Risk | Purpose |
|---|---:|---|---|---|---|
| `ops.approval.prepare_plan` | `ops_approval_prepare_plan` | plan | `ops:read` | low | Create one immutable execution plan from steps (SERVICE_CONTROL, HEALTH_CHECK, FILE_UPLOAD, RELEASE, ROLLBACK, DML, PACKAGE_CLEANUP, MATRIX_PULL) and emit a one-time approval code; stdio local FILE_UPLOAD inputs are staged through a room-bound intake and remain covered by this single approval. A MATRIX_PULL step pulls the latest Matrix room attachment into the File Center and its result package_name auto-feeds a dependent RELEASE or FILE_UPLOAD step — pull+release needs only ONE approval. A FILE_UPLOAD step that depends (directly or transitively) on MATRIX_PULL is deferred: its package and checksum are backfilled at execution time from the pull result, so it must NOT carry `expected_sha256` / `expected_size_bytes`, and its `package_name` must equal the MATRIX_PULL `filename` or be omitted (a mismatched or frozen checksum is rejected with 409 — this closes the 2026-09-09 stale-checksum failure where a plan froze the File Center's old same-name package and then 409'd against the freshly pulled one) |
| `ops.approval.execute_plan` | `ops_approval_execute_plan` | execute | `ops:read` | low | Consume the approved code and run the frozen steps in order |
| `ops.approval.prepare_service_control` | `ops_approval_prepare_service_control` | plan | `ops:read` | low | Legacy single-action service-control approval (restart/stop/start/update) |
| `ops.approval.execute` | `ops_approval_execute` | execute | `ops:read` | low | Legacy single-action approval consume (deploy/rollback/DML/package-cleanup) |
| `ops.approval.prepare_file_upload` | `ops_approval_prepare_file_upload` | plan | `ops:read` | low | Single-action FILE_UPLOAD approval: freezes package name, SHA256, size, targets and remote path; execution performs the SFTP upload only |
| `ops.approval.prepare_exec` | `ops_approval_prepare_exec` | plan | `ops:read` | low | Single-action EXEC_REMOTE approval for ad-hoc remote commands (package install / service bring-up / one-off troubleshooting). Freezes the verbatim command, targets and timeout with a command SHA256; the approval card shows the command verbatim. Allowlist mode requires the command to match an admin-managed template, and destructive commands are always refused in production. Admin must enable `allow_exec_remote_tool` first (default off) |
| `ops.approval.reject_plan` | `ops_approval_reject_plan` | reject | `ops:write` | medium | Mark a PENDING_APPROVAL execution plan as REJECTED (terminal). Idempotent; optional reason is stored in `failure_reason`, rejecter recorded as `rejected_by` |
| `ops.approval.reject` | `ops_approval_reject` | reject | `ops:write` | medium | Mark a PENDING_APPROVAL single-action ticket (SERVICE_CONTROL / EXEC_REMOTE / FILE_UPLOAD) as REJECTED (terminal). Counterpart of `reject_plan` for non-plan tickets. Idempotent; rejecter identity derived from `message_context.sender_id`, with `rejecter_matrix_id` / caller identity as fallback |
| `ops.approval.list` | `ops_approval_list` | read | `ops:read` | low | List approvals with filters |
| `ops.approval.temporary_access` | `ops_approval_temporary_access` | plan | `ops:read` | low | Request / confirm / revoke / query a time-boxed test-environment self-approval grant (FILE_UPLOAD / RELEASE / SERVICE_CONTROL / HEALTH_CHECK only); actor identity comes only from `message_context.sender_id`, confirmation by configured original approvers in the same conversation; `operation=query` returns the scoped grant list (beneficiary, actions, validity window) filterable by `grant_id` / `system_name` / `environment` / `beneficiary_identity` / `status`, never returns the confirmation code or its hash |
| `ops.help.query` | `ops_help_query` | read | `ops:read` | low | Contextual help built from live registry, permission summaries, system/environment rows, approver policy, and active grants; never leaks tokens, credentials, private keys, or code hashes |

Legacy single-action prepare paths for release / rollback / DML / package-cleanup are routed through `ops.approval.prepare_release` / `ops.approval.prepare_rollback` / `ops.approval.prepare_dml` / `ops.approval.prepare_package_cleanup` as approval-hint names in `app/services/tool_policy.py` (`APPROVAL_TOOL_MAP` / `APPROVAL_TOOL_NAME_MAP`); the actual registered entry point is `ops.approval.execute`, and the business logic is shared with the plan steps via `approval_executor.execute_*`.

All routing / approval / package / help tools accept the normalized `message_context` object (channel, channel_account_id, conversation_id, message_id, sender_id, content_sha256). Legacy Matrix form fields (`room_id`, `request_event_id`, `sender_matrix_id`, `content_sha256`) remain accepted only at the API compatibility boundary and are normalized to `channel=matrix`, `channel_account_id=default`. The channel-attachment package intake at `POST /api/v2/tools/packages/upload` accepts a JSON `message_context` multipart field, enforces token conversation binding, verifies the declared `package_sha256`, and stores `source_context` / `source_message_key` on the package (same message + same hash reuses; same message + different hash returns 409). See [qclaw-channel-approval-integration.md](../qclaw-channel-approval-integration.md).

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

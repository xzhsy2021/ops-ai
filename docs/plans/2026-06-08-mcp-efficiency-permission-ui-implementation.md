# MCP Efficiency, Permission, and Tool UI Implementation Summary

Updated: 2026-06-09

This document records the implemented MCP/tool-access changes. It is a review
reference, not an active task queue.

## Scope

The work keeps the existing FastAPI Tool Registry, stdio MCP bridge, Streamable
HTTP MCP endpoint, and React Tool Access page. It improves discovery
performance, permission explainability, MCP source-of-truth boundaries, and
offline behavior without introducing an in-process LLM runtime.

## Backend Changes

- `app/services/mcp_capability_service.py` is now the shared MCP capability
  layer for tool aliases, MCP payload formatting, tool calls, streamed calls,
  resource catalog/read wrappers, and prompt catalog/get wrappers.
- `app/api/tools.py` delegates MCP JSON-RPC tools/resources/prompts to the
  capability service. It no longer imports private helpers from
  `app.mcp.server`, and duplicated resource/prompt branches were removed.
- `app/mcp/server.py` keeps stdio-specific responsibilities only: JSON-RPC
  framing, HTTP bridge calls for dynamic tools, local package helpers, offline
  diagnostics, and fallback behavior.
- `/api/v2/tools` and `/api/v2/capabilities` use stable capability versions,
  `ETag`, and `If-None-Match` handling to avoid unnecessary full catalog
  reloads.
- Tool token APIs support recommended templates, descriptions, purge, and cache
  invalidation through capability version bumps.
- `/api/v2/tools/policy-preview` previews effective tool access for custom
  scopes, token templates, or an existing token, including a summarized
  permission matrix from `app/services/tool_permission_matrix.py`.

## MCP Behavior

- HTTP MCP and stdio MCP share alias/payload/prompt/resource behavior through
  the capability service.
- MCP-safe aliases map dotted tool names such as
  `ops.inspection.run_servers_batch` to `ops_inspection_run_servers_batch`.
- stdio `resources/list`, `prompts/list`, and `prompts/get` are served from the
  local capability service and remain available even when the OPS backend is
  offline.
- stdio `tools/list` still fetches the dynamic backend tool catalog. When the
  backend is unavailable, it returns `offline=true`, an `error`, `base_url`,
  `token_present`, and local fallback tools:
  `ops_connection_status`, `ops_inspect_local_package`, and
  `ops_prepare_release_from_local_package`.
- stdio `resources/read` keeps HTTP-first dynamic reads and returns a diagnostic
  JSON resource instead of a JSON-RPC error when the backend is offline.
- Tool descriptions are ASCII-safe by default for Windows/MCP clients that
  misrender UTF-8. `OPS_MCP_ASCII_DESCRIPTIONS=0` preserves UTF-8 descriptions.

## Frontend Changes

- `frontend/src/pages/ToolAccessPage.tsx` separates access setup, token
  management, catalog browsing, debugging, and audit views to reduce initial
  page load pressure.
- The page loads core capability data first and defers tab-specific datasets.
- Frontend capability cache invalidation runs after settings and token
  mutations.
- `frontend/src/pages/tools/ToolTokenPanel.tsx` exposes recommended token
  templates, token descriptions, and permission-oriented guidance for MCP/AI
  access.
- `frontend/src/pages/tools/ToolCatalogPanel.tsx` now uses an inline master /
  detail layout for the tool directory. Operators can search/filter on the
  left, inspect schema and permissions on the right, copy tool names, and jump
  directly to debugging without opening a separate drawer.
- Tool Token scope display keeps the real scope strings as tooltips while
  showing Chinese permission labels such as 运维读取、运维写入、服务器读取、
  审计读取、发布预检、数据库写入 and 全部权限.
- Report Center uses backend pagination (`limit`/`offset`) instead of loading a
  large fixed list.
- Inspection Center risk issues use backend pagination and accurate `total`.
  The ledger/report tab now treats inspection reports as the primary work area,
  with ledger filters, statistics, table rows, and report-scope notes as
  auxiliary panels.
- Task Center uses backend pagination and a compact toolbar that combines page
  summary, filters, auto-refresh state, and refresh action while keeping the
  task table as the main content.

## Permission Model

- Global capability switches decide whether a tool family is available.
- Token scopes and token flags decide actual MCP/AI access for a client.
- Low-risk read tools are candidates for auto-call when scopes permit.
- Write, production, destructive, database write/DML, deploy execution,
  rollback, package cleanup, runtime cleanup, and delete flows remain behind
  scopes, risk policy, and confirmation gates.
- Path A inspection execution is available to AI/MCP tool tokens only with
  required scopes/write permission and the backend `confirm_text`.

## Verification

Key regression coverage:

- `tests/test_capability_version_contract.py`
- `tests/test_tool_policy_preview_contract.py`
- `tests/test_tool_permission_matrix_contract.py`
- `tests/test_tool_token_templates_contract.py`
- `tests/test_frontend_tooling_contract.py`
- `tests/test_frontend_pagination_contract.py`
- `tests/test_mcp_contract_sync.py`

Latest targeted verification in this thread:

```text
py -3 -m pytest tests/test_frontend_tooling_contract.py tests/test_frontend_pagination_contract.py tests/test_mcp_contract_sync.py -q
29 passed

py -3 -m pytest tests/test_iter39_report_center_contract.py tests/test_iter35_job_center_contract.py tests/test_tool_token_templates_contract.py tests/test_tool_permission_matrix_contract.py -q
12 passed

node scripts/frontend_syntax_check.js
[frontend] syntax check passed (132 TS/TSX files)

node scripts/frontend_route_check.js
frontend_route_check passed
```

## Review Notes

- The current implementation intentionally keeps dynamic `tools/list` backend
  backed, because tool availability depends on registry state, token policy,
  scopes, and feature switches.
- Static MCP resources and prompts are local service data so stdio clients do
  not lose basic guidance when the backend is restarting.
- `scripts/frontend_route_check.js` now validates MCP prompts/resources against
  the shared MCP capability service plus `app/api/tools.py`, matching the
  current source-of-truth split instead of the older API-only implementation.

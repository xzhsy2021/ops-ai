# AI Capability Discovery

Updated: 2026-06-08

This runbook explains how AI/MCP clients discover the OPS capabilities that are actually available for the current token/session.

## HTTP Capability Entry

```http
GET /api/v2/capabilities
Authorization: Bearer <OPS_TOOL_TOKEN>
```

The response includes:

- `server.capability_version`: cache invalidation key for tool/policy/scope changes.
- `auth`: current token/session identity and scopes.
- `features`: current capability switches.
- `tools`: tools available in the current context.
- `pagination`: cursor/limit data.
- `categories`: tool categories.
- `policies`: confirmation, production, shell, and risk policy notes.
- `resources`: MCP resources.
- `prompts`: MCP prompts.

## HTTP Tool List

```http
GET /api/v2/tools?category=inspection&include_schema=true&limit=100
Authorization: Bearer <OPS_TOOL_TOKEN>
```

Useful parameters:

- `category`: filter by tool category.
- `risk`: filter by risk level.
- `include_disabled`: include blocked tools and blocking reasons.
- `include_schema`: include input/output schema.
- `limit` / `cursor`: pagination.
- `format`: `native`, `mcp`, `openai`, or `anthropic`.

## Tool Detail

```http
GET /api/v2/tools/detail/ops.inspection.run_servers_batch
Authorization: Bearer <OPS_TOOL_TOKEN>
```

Tool detail includes description, input/output schema, risk, scopes, current availability, and blocking reason when unavailable.

## Model-Compatible Formats

```http
GET /api/v2/tools?format=native
GET /api/v2/tools?format=mcp
GET /api/v2/tools?format=openai
GET /api/v2/tools?format=anthropic
```

Use:

- `native`: OPS-native catalog for frontend and custom agents.
- `mcp`: MCP `tools/list` compatible shape.
- `openai`: OpenAI tools/function-calling compatible shape.
- `anthropic`: Claude tools compatible shape.

## MCP Discovery

The stdio bridge started by `scripts/mcp-server.bat` or `scripts/mcp-server.sh` declares:

```json
{
  "capabilities": {
    "tools": { "listChanged": true },
    "resources": { "subscribe": false, "listChanged": true },
    "prompts": { "listChanged": true }
  }
}
```

Recommended client startup:

```text
initialize
tools/list
resources/list
prompts/list
```

`tools/list` supports `cursor` pagination and returns `nextCursor`.

For stdio clients, `resources/list` and `prompts/list` are served from the local
capability service and stay available while the OPS backend is offline.
If `tools/list` cannot reach the backend, it returns `offline=true`, an `error`
message, and the local fallback tools `ops_connection_status`,
`ops_inspect_local_package`, and `ops_prepare_release_from_local_package`.
The backwards-compatible stdio `manifest` method follows the same offline
pattern and includes local resources, prompts, and fallback tools instead of
returning a JSON-RPC error.

MCP tool names are MCP-safe aliases. Replace dots with underscores:

- HTTP Tool API: `ops.describe_capabilities`
- MCP JSON-RPC: `ops_describe_capabilities`
- HTTP Tool API: `ops.inspection.run_servers_batch`
- MCP JSON-RPC: `ops_inspection_run_servers_batch`

## Capability Discovery Tool

Agents that do not use MCP can call the HTTP tool:

```json
{
  "tool": "ops.describe_capabilities",
  "arguments": {
    "category": "inspection",
    "include_schema": true,
    "limit": 100
  }
}
```

MCP clients call the alias:

```json
{
  "name": "ops_describe_capabilities",
  "arguments": {
    "category": "inspection",
    "include_schema": true,
    "limit": 100
  }
}
```

## Streaming Results

Large-payload tools can be called through:

```http
POST /api/v2/tools/call/stream
```

MCP clients that support the OPS private extension can use:

```text
tools/call.stream
```

Current streamable tools:

- `ops.db.export_query_result`
- `ops.get_deployment_logs`
- `ops.export_diagnostics_report`

## Security Principles

Capability discovery returns what the current token/session can actually use, not every registered backend tool.

Filtering order:

```text
Tool Registry
-> capability settings
-> token scopes
-> user role
-> risk policy
-> currently available tools
```

To troubleshoot blocked tools:

```http
GET /api/v2/tools?include_disabled=true
Authorization: Bearer <OPS_TOOL_TOKEN>
```

## Recommended Agent Flow

1. Read `/api/v2/capabilities` or MCP `tools/list`.
2. Choose a tool that matches user intent and current policy.
3. Prefer read tools to gather real systems, services, servers, packages, and inspection state.
4. For inspection, prefer Path A `ops.inspection.*` tools.
5. For high-risk operations, ask the user for the exact backend confirmation text.
6. Call the execution tool only after confirmation.
7. Follow returned `next_actions`, task ids, and audit ids.

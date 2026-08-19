# AI Tool MCP Examples

Updated: 2026-08-19

This runbook provides client snippets for connecting to OPS through MCP.
OPS exposes a single HTTP-only MCP surface (`POST /api/v2/mcp`); the legacy
stdio bridge has been removed.

## Remote HTTP MCP

```json
{
  "mcpServers": {
    "ops": {
      "url": "http://127.0.0.1:8000/api/v2/mcp",
      "headers": {
        "Authorization": "Bearer <OPS_TOOL_TOKEN>"
      }
    }
  }
}
```

## JSON-RPC Examples

List tools:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": { "cursor": null }
}
```

Call capability discovery through MCP alias:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "ops_describe_capabilities",
    "arguments": { "include_schema": true }
  }
}
```

Call HTTP Tool API with dotted name:

```http
POST /api/v2/tools/call
Authorization: Bearer <OPS_TOOL_TOKEN>
Content-Type: application/json

{
  "tool": "ops.describe_capabilities",
  "arguments": { "include_schema": true }
}
```

## Inspection Execution Example

For a natural-language all-server grouped inspection, prefer the workflow tool:

```json
{
  "jsonrpc": "2.0",
  "id": 30,
  "method": "tools/call",
  "params": {
    "name": "ops_workflow_inspect",
    "arguments": {
      "request": "巡检全部服务器，数量较多，按分组分批巡检，分析并输出巡检报告",
      "concurrency": 4,
      "batch_size": 8,
      "generate_report": true
    }
  }
}
```

If the response is `mode=grouped_preview`, follow its per-group `next_actions`.
After all groups finish, collect all returned `run_ids` and call
`ops_inspection_generate_report_for_runs` to create one merged report.

For grouped or large-batch inspection, preview first. The preview returns the
resolved targets, skipped servers, batch plan, and a short Chinese confirmation
phrase such as `确认巡检 <fingerprint>`. Use that exact phrase after the user
approves the target list.

Preview with the MCP-safe alias:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "ops_inspection_preview_servers_batch",
    "arguments": {
      "groups": ["crypto"],
      "categories": ["LOGIN_SECURITY", "ACCOUNT_SECURITY", "PROCESS_PORT", "DISK_USAGE", "BACKUP"],
      "concurrency": 4,
      "batch_size": 8
    }
  }
}
```

Then execute with the returned `confirmation.confirm_text`:

```json
{
  "tool": "ops.inspection.run_servers_batch",
  "arguments": {
    "groups": ["crypto"],
    "categories": ["LOGIN_SECURITY", "ACCOUNT_SECURITY", "PROCESS_PORT", "DISK_USAGE", "BACKUP"],
    "concurrency": 4,
    "batch_size": 8,
    "confirm_text": "确认巡检 <fingerprint>"
  }
}
```

## Streaming Example

HTTP SSE:

```http
POST /api/v2/tools/call/stream
Authorization: Bearer <OPS_TOOL_TOKEN>
Content-Type: application/json

{
  "tool": "ops.get_deployment_logs",
  "arguments": { "deployment_id": "<deployment_id>", "limit": 200 }
}
```

MCP private extension:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call.stream",
  "params": {
    "name": "ops_get_deployment_logs",
    "arguments": { "deployment_id": "<deployment_id>", "limit": 200 }
  }
}
```

## Security Notes

MCP examples still use `OPS_TOOL_TOKEN`. The actual tool set is controlled by token scopes, capability switches, role/session context, risk policy, and confirmation gates.

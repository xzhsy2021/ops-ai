# AI Tool MCP Examples

Updated: 2026-06-08

This runbook provides client snippets for connecting to OPS through MCP.

## Stdio MCP

```json
{
  "mcpServers": {
    "ops": {
      "command": "cmd",
      "args": ["/c", "<OPS_PROJECT_DIR>\\scripts\\mcp-server.bat"],
      "env": {
        "OPS_BASE_URL": "http://127.0.0.1:8000",
        "OPS_TOOL_TOKEN": "<OPS_TOOL_TOKEN>"
      }
    }
  }
}
```

Linux/macOS:

```json
{
  "mcpServers": {
    "ops": {
      "command": "bash",
      "args": ["<OPS_PROJECT_DIR>/scripts/mcp-server.sh"],
      "env": {
        "OPS_BASE_URL": "http://127.0.0.1:8000",
        "OPS_TOOL_TOKEN": "<OPS_TOOL_TOKEN>"
      }
    }
  }
}
```

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

For MCP clients, use the MCP-safe alias:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "ops_inspection_run_servers_batch",
    "arguments": {
      "groups": ["crypto"],
      "categories": ["LOGIN_SECURITY", "ACCOUNT_SECURITY", "PROCESS_PORT", "DISK_USAGE", "BACKUP"],
      "confirm_text": "CONFIRM ops.inspection.run_servers_batch"
    }
  }
}
```

For HTTP Tool API, use the dotted backend name:

```json
{
  "tool": "ops.inspection.run_servers_batch",
  "arguments": {
    "groups": ["crypto"],
    "categories": ["LOGIN_SECURITY", "ACCOUNT_SECURITY", "PROCESS_PORT", "DISK_USAGE", "BACKUP"],
    "confirm_text": "CONFIRM ops.inspection.run_servers_batch"
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

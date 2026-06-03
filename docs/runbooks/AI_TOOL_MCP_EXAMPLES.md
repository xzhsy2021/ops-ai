# AI 工具页 MCP 调用示例增强

## 背景

AI 工具页原有“HTTP 调用示例”只覆盖 `/api/v2/capabilities` 和 `/api/v2/tools/call`，对支持 MCP 的客户端不够直接。为了方便 Cursor、Claude Desktop、OpenAI Agents SDK、自研 MCP Client 等接入，本次在页面中补充 MCP 客户端示例。

## 页面变化

位置：

```text
AI 工具 → 概览与接入 → MCP 客户端示例
```

新增内容：

1. Cursor / Claude Desktop 的 stdio MCP 配置示例。
2. Remote HTTP MCP 配置示例。
3. MCP JSON-RPC `tools/list` 示例。
4. MCP JSON-RPC `tools/call` 示例。

同时在：

```text
AI 工具 → 工具目录 → 工具详情 → 调用示例
```

将原“调用示例”拆成：

1. HTTP 调用示例。
2. MCP `tools/call` 示例。

## 示例

### stdio MCP

```json
{
  "mcpServers": {
    "ops": {
      "command": "cmd",
      "args": ["/c", "<OPS_PROJECT_DIR>\\scripts\\mcp-server.bat"],
      "env": {
        "OPS_BASE_URL": "http://127.0.0.1:3000",
        "OPS_TOOL_TOKEN": "<OPS_TOOL_TOKEN>"
      }
    }
  }
}
```

### HTTP MCP

```json
{
  "mcpServers": {
    "ops": {
      "url": "http://127.0.0.1:3000/api/v2/mcp",
      "headers": {
        "Authorization": "Bearer <OPS_TOOL_TOKEN>"
      }
    }
  }
}
```

### MCP JSON-RPC

```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": { "cursor": null } }
{ "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": { "name": "ops.describe_capabilities", "arguments": { "include_schema": true } } }
```

## 安全说明

MCP 示例仍然使用 `OPS_TOOL_TOKEN`，能力范围由 Token scopes、能力开关、工具风险策略共同控制。页面只是提供接入示例，不改变后端权限、安全策略和审计逻辑。

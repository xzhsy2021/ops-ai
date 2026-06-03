# AI 自动发现 OPS 可用能力

本轮实现目标：让不同大模型客户端接入 OPS 后，无需写死提示词或工具列表，可以自动发现当前 Token 可用能力。

## 核心入口

### HTTP 能力清单

```http
GET /api/v2/capabilities
Authorization: Bearer <OPS_TOOL_TOKEN>
```

返回内容包括：

- `server.capability_version`：能力版本，工具/权限/开关变化后会变化
- `auth`：当前 Token/会话身份和 scopes
- `features`：当前能力开关
- `tools`：当前上下文可调用工具
- `pagination`：分页信息
- `categories`：工具分类
- `policies`：确认、生产、shell 等安全策略
- `resources`：MCP resources
- `prompts`：MCP prompts

### HTTP 工具列表

```http
GET /api/v2/tools?category=deploy_plan&include_schema=true&limit=100
Authorization: Bearer <OPS_TOOL_TOKEN>
```

支持参数：

- `category`：按分类过滤
- `risk`：按风险过滤
- `include_disabled`：显示被策略禁用或权限不足的能力及原因
- `include_schema`：是否返回输入/输出 schema
- `limit` / `cursor`：分页
- `format`：`native` / `mcp` / `openai` / `anthropic`

### 工具详情

```http
GET /api/v2/tools/detail/ops.create_deploy_plan
```

返回工具描述、输入输出 schema、风险等级、权限要求、当前上下文是否可用及阻断原因。

## 模型兼容格式

`/api/v2/tools` 支持多种输出格式：

```http
GET /api/v2/tools?format=native
GET /api/v2/tools?format=mcp
GET /api/v2/tools?format=openai
GET /api/v2/tools?format=anthropic
```

用途：

- `native`：OPS 原生工具目录，适合前端和自研 Agent
- `mcp`：MCP tools/list 兼容格式
- `openai`：OpenAI tools/function calling 兼容格式
- `anthropic`：Claude tools 兼容格式

## MCP 自动发现

`scripts/mcp-server.bat` / `scripts/mcp-server.sh` 启动的 MCP bridge，其 `initialize` 响应已声明：

```json
{
  "capabilities": {
    "tools": { "listChanged": true },
    "resources": { "subscribe": false, "listChanged": true },
    "prompts": { "listChanged": true }
  }
}
```

MCP 客户端应在连接后调用：

```text
initialize
tools/list
resources/list
prompts/list
```

`tools/list` 支持 `cursor` 分页，返回 `nextCursor`。

## 兼容普通工具调用的能力发现

新增工具：

```text
ops.describe_capabilities
```

不支持 MCP 的 Agent 可以先调用这个工具获取可用能力，例如：

```json
{
  "tool": "ops.describe_capabilities",
  "arguments": {
    "category": "deploy_plan",
    "include_schema": true,
    "limit": 100
  }
}
```

## 安全原则

AI 自动发现到的是“当前 Token 真正可用的能力”，不是 OPS 后端注册的所有工具。

过滤顺序：

```text
Tool Registry 全量工具
→ 系统能力开关
→ Token scopes
→ 用户角色
→ 工具风险策略
→ 返回当前可用工具
```

如需排查禁用原因，可在管理员页面或 HTTP 参数中启用：

```http
GET /api/v2/tools?include_disabled=true
```

## 前端 Capability Explorer

`AI 工具` 页面新增 Capability Explorer：

- 展示 `capability_version`
- 展示当前 Token/会话可用能力
- 按分类、风险、关键词过滤
- 查看工具详情、input schema、output schema
- 生成 HTTP 调用示例
- 显示被禁用工具的阻断原因

## 推荐客户端流程

```text
1. 读取 /api/v2/capabilities 或 MCP tools/list
2. 选择匹配用户意图的工具
3. 调用只读工具获取真实系统、服务、服务器、包信息
4. 创建发布计划或配置变更计划
5. 预检或展示 diff
6. 高风险操作要求用户确认
7. 调用执行工具
8. 根据返回的 next_actions 和 audit_id 继续跟踪
```

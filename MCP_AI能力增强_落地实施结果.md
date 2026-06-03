# MCP + AI 能力增强落地实施结果

## 实施范围

本次严格依据《MCP_AI能力增强_Agent落地开发文档.md》完成首版代码落地，目标是将现有 MCP 从工具调用接口升级为 AI 运维能力层，并补齐 AI 结合所需的上下文、工具、工作流、证据链、分析入库和前端入口。

## 已完成内容

### P0：MCP 工具治理与元数据增强

- 扩展 `ToolDefinition`：
  - `ai_callable`
  - `ai_auto_callable`
  - `requires_human_approval`
  - `data_sensitivity`
  - `output_masking`
  - `recommended_use_cases`
  - `example_prompts`
  - `related_tools`
- 增强 `to_public_dict()`、`to_mcp_dict()`、`to_openai_dict()`、`to_anthropic_dict()`。
- 新增 AI 工具等级：L1 / L2 / L3 / L4。
- MCP token 调用高风险审批工具时直接阻断，避免 AI 自动执行高风险动作。

### P1：MCP Resources 增强

新增资源：

- `ops://servers`
- `ops://projects`
- `ops://agents`
- `ops://status/overview`
- `ops://risks/open`
- `ops://inspection/recent`
- `ops://diagnosis/recent`
- `ops://reports/recent`
- `ops://backups/status`
- `ops://deployments/failed`
- `ops://tool-risk-policy`
- `ops://ai-workflows`

并同步增强 MCP HTTP resources/read 与 JSON-RPC resources/read。

### P2：新增 MCP Tool Adapters

新增文件：

- `app/services/tool_adapters/inspection_tools.py`
- `app/services/tool_adapters/risk_tools.py`
- `app/services/tool_adapters/agent_tools.py`
- `app/services/tool_adapters/log_tools.py`

新增工具类型：

- `ops.inspection.*`
- `ops.risk.*`
- `ops.agent.*`
- `ops.log.*`

其中日志工具已加入限量、脱敏和禁止全量读取的保护。

### P3：新增 MCP Workflow 工具

新增文件：

- `app/services/ai_workflows.py`
- `app/services/tool_adapters/workflow_tools.py`

新增工作流：

- `ops.workflow.generate_project_health_brief`
- `ops.workflow.analyze_failed_deploy`
- `ops.workflow.inspect_project_security`
- `ops.workflow.triage_open_risks`
- `ops.workflow.generate_monthly_ops_report`

所有 Workflow 输出统一包含：

- `facts`
- `inferences`
- `recommendations`
- `evidence`
- `summary`
- `requires_human_action`

### P4：AI 分析结果入库与证据链

新增模型：

- `AiAnalysisRun`
- `AiAnalysisFinding`
- `AiActionApproval`

新增服务：

- `app/services/ai_analysis.py`
- `app/services/ai_evidence.py`

新增 API：

- `GET /api/v2/ai/analysis`
- `GET /api/v2/ai/analysis/{id}`
- `POST /api/v2/ai/analysis`
- `POST /api/v2/ai/analysis/{id}/generate-report`

新增 MCP 工具：

- `ops.ai.save_analysis`
- `ops.ai.get_analysis`
- `ops.ai.list_analysis`
- `ops.ai.generate_report_from_analysis`
- `ops.ai.find_similar_analysis`

报告中心新增 `ai_analysis` 报告类型，并支持从 AI 分析结果生成报告。

### P5：前端 AI 能力中心

新增页面：

- `frontend/src/pages/McpToolsPage.tsx`
- `frontend/src/pages/McpAuditPage.tsx`
- `frontend/src/pages/AiWorkflowsPage.tsx`
- `frontend/src/pages/AiAnalysisPage.tsx`
- `frontend/src/pages/AiAnalysisDetailPage.tsx`

新增组件：

- `frontend/src/components/AiEvidenceView.tsx`
- `frontend/src/components/ToolRiskTag.tsx`
- `frontend/src/components/RiskLevelTag.tsx`

新增菜单：

- `AI 能力`

新增路由：

- `/mcp/tools`
- `/mcp/audit`
- `/ai/workflows`
- `/ai/analysis`
- `/ai/analysis/:id`

### P6：安全策略增强

- 高风险工具新增 `requires_human_approval` 标记。
- MCP token 直接调用高风险审批工具会被后端策略拒绝。
- AI 只能通过计划、分析、审批入口推动后续操作。
- 审批模型 `AiActionApproval` 已落库，后续可继续接前端审批流。

## 关键新增工具清单

### 巡检工具

- `ops.inspection.list_runs`
- `ops.inspection.get_run`
- `ops.inspection.list_issues`
- `ops.inspection.get_issue`
- `ops.inspection.generate_report`
- `ops.inspection.summarize_run`
- `ops.inspection.run_server`
- `ops.inspection.run_project`
- `ops.inspection.run_combined`

### 风险工具

- `ops.risk.list`
- `ops.risk.get`
- `ops.risk.triage`
- `ops.risk.generate_fix_plan`
- `ops.risk.update_status`
- `ops.risk.verify`
- `ops.risk.ignore`

### Agent 工具

- `ops.agent.list`
- `ops.agent.get`
- `ops.agent.get_status`
- `ops.agent.list_tasks`
- `ops.agent.get_task`
- `ops.agent.get_logs`
- `ops.agent.health_check`

### 日志工具

- `ops.log.tail`
- `ops.log.search`
- `ops.log.summarize_errors`
- `ops.log.find_patterns`
- `ops.log.get_recent_exceptions`

### Workflow 工具

- `ops.workflow.generate_project_health_brief`
- `ops.workflow.analyze_failed_deploy`
- `ops.workflow.inspect_project_security`
- `ops.workflow.triage_open_risks`
- `ops.workflow.generate_monthly_ops_report`

### AI 分析工具

- `ops.ai.save_analysis`
- `ops.ai.get_analysis`
- `ops.ai.list_analysis`
- `ops.ai.generate_report_from_analysis`
- `ops.ai.find_similar_analysis`

## 主要改动文件

### 后端

- `app/services/tool_registry.py`
- `app/services/tool_policy.py`
- `app/api/tools.py`
- `app/api/ai_analysis.py`
- `app/services/ai_analysis.py`
- `app/services/ai_evidence.py`
- `app/services/ai_workflows.py`
- `app/services/report_center.py`
- `app/db/models.py`
- `app/db/migrations/runner.py`
- `app/services/tool_adapters/inspection_tools.py`
- `app/services/tool_adapters/risk_tools.py`
- `app/services/tool_adapters/agent_tools.py`
- `app/services/tool_adapters/log_tools.py`
- `app/services/tool_adapters/workflow_tools.py`
- `app/services/tool_adapters/ai_analysis_tools.py`
- `main.py`

### 前端

- `frontend/src/api.ts`
- `frontend/src/routes.ts`
- `frontend/src/App.tsx`
- `frontend/src/pages/McpToolsPage.tsx`
- `frontend/src/pages/McpAuditPage.tsx`
- `frontend/src/pages/AiWorkflowsPage.tsx`
- `frontend/src/pages/AiAnalysisPage.tsx`
- `frontend/src/pages/AiAnalysisDetailPage.tsx`
- `frontend/src/components/AiEvidenceView.tsx`
- `frontend/src/components/ToolRiskTag.tsx`
- `frontend/src/components/RiskLevelTag.tsx`

## 验证情况

已执行：

```bash
python -m compileall app
```

结果：通过。

未执行：

```bash
pytest
cd frontend && npm run typecheck && npm run build
```

原因：当前沙箱环境未安装项目 Python 依赖，例如 `sqlalchemy`；前端目录未包含 `node_modules`。请在真实开发环境执行完整测试。

## 本地验证建议

```bash
python -m compileall app
pytest
python scripts/capability_tools_smoke.py
python scripts/mcp_diagnose.py

cd frontend
npm ci
npm run typecheck
npm run build
```

## 启动后重点验证

1. 打开 `/mcp/tools`，确认 MCP 工具目录可显示新增 AI 元数据。
2. 打开 `/ai/workflows`，执行项目健康分析。
3. 打开 `/ai/analysis`，确认 AI 分析结果可以入库和查看。
4. 调用 `/api/v2/mcp/resources`，确认新增 resources 可见。
5. 调用 `/api/v2/mcp/resources/read` 读取 `ops://risks/open`、`ops://inspection/recent`。
6. 用 MCP token 调用高风险工具，确认会被人工审批策略阻断。

## 后续建议

1. 完成真实 Agent 表和 Agent 任务中心后，将 `ops.agent.*` 从兼容空实现切换为真实查询。
2. 将诊断中心数据模型化，补齐 `ops.diagnosis.*` 工具。
3. 将风险中心从巡检风险扩展为统一风险模型，接入状态、诊断、日志、备份、Agent 离线等来源。
4. 增加 AI Action Approval 前端审批页面。
5. 增加 Prompt 模板管理和版本化。
6. 增加 AI 相似问题检索和月度报告自动生成任务。

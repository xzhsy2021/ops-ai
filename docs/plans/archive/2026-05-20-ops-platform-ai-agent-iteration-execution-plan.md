# OPS Platform AI Agent Iteration Execution Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 按“平台收敛 -> 资源优化 -> MCP 成熟化”的顺序，把当前 OPS Platform 落成一套可由 AI Agent 按任务连续执行、又不破坏现有稳定基线的增量实施计划。

**Architecture:** 继续采用模块化单体。所有任务都遵循“先加新模块、再接旧入口、最后替换读取方”的兼容式改造方式，避免一次性重构。测试优先围绕现有契约测试，确保发布、任务中心、风险策略、AI 诊断和维护场景全程可回归。

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, SQLite(WAL), React 18, TypeScript, Vite, Zustand, Paramiko, MCP Streamable HTTP, stdio MCP bridge.

---

## 执行原则

- 每个任务只做一个明确动作。
- 每次改动必须有对应验证命令。
- 先保护兼容，再做结构迁移。
- 不在本轮引入 Redis、消息队列、微服务或 K8s。
- 不修改现有 Tool name 与关键 API path。
- 所有高风险链路以现有契约测试为准。

## 现有关键验证基线

- 发布可靠性：`tests/test_release_reliability_contract.py`
- 发布编排：`tests/test_iter36_release_orchestration_contract.py`
- 任务中心：`tests/test_iter35_job_center_contract.py`
- 风险策略：`tests/test_risk_policy_contract.py`
- AI 诊断：`tests/test_iter37_ai_diagnostics_contract.py`
- Dashboard/状态页：`tests/test_dashboard_iteration_contract.py`
- 认证与维护回归：`tests/test_auth_and_maintenance_regressions.py`

## 任务顺序

严格顺序：

1. P0-1 发布域拆分
2. P0-2 配置源读取收口
3. P0-3 统一任务视图
4. P0-4 统一观测标识与日志上下文
5. P1-1 发布聚合状态接口
6. P1-2 Runtime 资源快照
7. P1-3 前端轮询链路收敛
8. P2-1 MCP manifest 单一生成源
9. P2-2 Prompt Registry 与 Tool Access 工作面

---

### Task 1: 拆分发布 API 壳层

**Files:**

- Create: `app/api/deploy/__init__.py`
- Create: `app/api/deploy/plans.py`
- Create: `app/api/deploy/precheck.py`
- Create: `app/api/deploy/executions.py`
- Create: `app/api/deploy/history.py`
- Modify: `app/api/deploy_v2.py`
- Test: `tests/test_release_reliability_contract.py`
- Test: `tests/test_iter36_release_orchestration_contract.py`

**Step 1: 写失败测试或定位现有保护用例**

使用现有契约测试作为保护壳，先记录当前覆盖点：

- `execute_deploy_plan` 任务化
- `release_runbook` 质量门禁
- `deploy worker` 异常失败清理
- `cancelled/canceled` 兼容

**Step 2: 运行目标测试确认当前基线通过**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_release_reliability_contract.py tests\test_iter36_release_orchestration_contract.py -q
```

Expected:

- PASS

**Step 3: 最小实现**

- 在 `app/api/deploy/` 下创建四个子模块。
- 先只搬运纯函数和 handler 分组，不改行为。
- `app/api/deploy_v2.py` 暂时保留 router，对外 path 不变。
- 新模块通过导入原函数或承接原函数实现，避免一次性切断依赖。

**Step 4: 运行测试确认通过**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_release_reliability_contract.py tests\test_iter36_release_orchestration_contract.py -q
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add app/api/deploy app/api/deploy_v2.py tests/test_release_reliability_contract.py tests/test_iter36_release_orchestration_contract.py
git commit -m "refactor: split deploy api shell into submodules"
```

---

### Task 2: 下沉发布计划与预检逻辑

**Files:**

- Modify: `app/api/deploy/plans.py`
- Modify: `app/api/deploy/precheck.py`
- Modify: `app/api/deploy_v2.py`
- Modify: `app/services/release_plan.py`
- Test: `tests/test_iter36_release_orchestration_contract.py`
- Test: `tests/test_dashboard_iteration_contract.py`

**Step 1: 写失败测试**

补一个最小测试，确保拆分后：

- `release_runbook()` 输出的 `quality_gates`、`mcp_flow` 不变
- 发布确认信息仍可被 dashboard / precheck 消费

建议新增测试文件：

- `tests/test_release_plan_split_contract.py`

测试骨架：

```python
def test_release_runbook_keeps_summary_shape(tmp_path):
    from app.services.release_plan import release_runbook
    ...
    payload = release_runbook(plan, include_events=True, db=db)
    assert "summary" in payload
    assert "quality_gates" in payload
    assert "mcp_flow" in payload
```

**Step 2: 运行测试确认失败或确认待保护范围**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_iter36_release_orchestration_contract.py tests\test_dashboard_iteration_contract.py -q
```

Expected:

- 当前通过，作为保护基线

**Step 3: 最小实现**

- 将计划查询、runbook 组装、rollback readiness 的路由处理搬到新模块。
- `release_plan.py` 保持只读、纯组装职责。
- `deploy_v2.py` 只保留兼容导出与路由挂载。

**Step 4: 运行测试确认通过**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_iter36_release_orchestration_contract.py tests\test_dashboard_iteration_contract.py -q
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add app/api/deploy/plans.py app/api/deploy/precheck.py app/api/deploy_v2.py app/services/release_plan.py tests/test_iter36_release_orchestration_contract.py tests/test_dashboard_iteration_contract.py
git commit -m "refactor: separate deploy planning and precheck handlers"
```

---

### Task 3: 引入 Inventory 统一读取服务

**Files:**

- Create: `app/domain/inventory/__init__.py`
- Create: `app/domain/inventory/services.py`
- Modify: `app/api/servers.py`
- Modify: `app/api/deploy_v2.py`
- Modify: `app/services/tool_adapters/deploy_tools.py`
- Test: `tests/test_release_reliability_contract.py`
- Test: `tests/test_auth_and_maintenance_regressions.py`

**Step 1: 写失败测试**

新增最小测试验证统一读取服务可返回服务器和服务配置，不依赖调用方直接拼 `config_manager`：

```python
def test_inventory_service_returns_server_snapshot(monkeypatch):
    from app.domain.inventory.services import InventoryReadService
    monkeypatch.setattr(...)
    data = InventoryReadService().list_servers()
    assert data[0]["name"] == "s1"
```

**Step 2: 运行测试确认失败**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_auth_and_maintenance_regressions.py -q
```

Expected:

- FAIL with import/module missing if new service not yet added

**Step 3: 最小实现**

- 新建 `InventoryReadService`，封装：
  - `list_servers()`
  - `get_server(name)`
  - `list_systems()`
  - `get_service(system, service, environment)`
  - `list_groups()`
- 第一阶段只收口读取，不迁移写入。
- `servers.py` 和 `deploy_tools.py` 改为优先调用该 service。

**Step 4: 运行测试确认通过**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_release_reliability_contract.py tests\test_auth_and_maintenance_regressions.py -q
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add app/domain/inventory app/api/servers.py app/api/deploy_v2.py app/services/tool_adapters/deploy_tools.py tests/test_release_reliability_contract.py tests/test_auth_and_maintenance_regressions.py
git commit -m "refactor: unify inventory reads behind service layer"
```

---

### Task 4: 建立统一任务视图层

**Files:**

- Create: `app/domain/runtime/__init__.py`
- Create: `app/domain/runtime/jobs.py`
- Modify: `app/services/job_service.py`
- Modify: `app/api/tools.py`
- Modify: `app/api/task_center.py`
- Test: `tests/test_iter35_job_center_contract.py`
- Test: `tests/test_iter38_audit_chain_contract.py`

**Step 1: 写失败测试**

新增测试验证统一任务视图至少能兼容 `OperationJob` 查询：

```python
def test_runtime_job_view_exposes_operation_job_fields(tmp_path):
    from app.domain.runtime.jobs import list_runtime_jobs
    ...
    rows = list_runtime_jobs(db)
    assert rows[0]["source_tool"] == "ops.delete_backup"
```

**Step 2: 运行测试确认失败**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_iter35_job_center_contract.py -q
```

Expected:

- FAIL if new runtime job view not implemented

**Step 3: 最小实现**

- 新建统一 job view 层：
  - `list_runtime_jobs()`
  - `get_runtime_job()`
  - `list_runtime_job_summary()`
- 第一阶段仅统一读取模型，不统一执行器。
- `task_center` 与 tools read endpoints 改成读统一视图。

**Step 4: 运行测试确认通过**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_iter35_job_center_contract.py tests\test_iter38_audit_chain_contract.py -q
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add app/domain/runtime/jobs.py app/services/job_service.py app/api/tools.py app/api/task_center.py tests/test_iter35_job_center_contract.py tests/test_iter38_audit_chain_contract.py
git commit -m "refactor: add unified runtime job read model"
```

---

### Task 5: 统一 request_id / operation_id 日志上下文

**Files:**

- Modify: `main.py`
- Modify: `app/api/helpers.py`
- Modify: `app/services/tool_audit.py`
- Modify: `app/services/job_service.py`
- Modify: `app/deploy/report.py`
- Test: `tests/test_iter39_report_center_contract.py`

**Step 1: 写失败测试**

新增轻量测试验证响应或记录对象中带统一标识：

```python
def test_api_response_contains_request_or_operation_id():
    ...
```

**Step 2: 运行测试确认失败**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_iter39_report_center_contract.py -q
```

Expected:

- FAIL if identifiers not yet surfaced

**Step 3: 最小实现**

- `main.py` 加 request-level context id 生成。
- 将 `request_id` / `operation_id` 贯穿：
  - API response headers
  - tool audit
  - job result
  - deployment report metadata

**Step 4: 运行测试确认通过**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_iter39_report_center_contract.py tests\test_iter35_job_center_contract.py -q
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add main.py app/api/helpers.py app/services/tool_audit.py app/services/job_service.py app/deploy/report.py tests/test_iter39_report_center_contract.py
git commit -m "feat: propagate request and operation identifiers"
```

---

### Task 6: 新增发布聚合状态接口

**Files:**

- Create: `app/domain/runtime/snapshots.py`
- Modify: `app/api/deploy/history.py`
- Modify: `app/api/deploy/executions.py`
- Modify: `app/deploy/logs.py`
- Test: `tests/test_dashboard_iteration_contract.py`
- Test: `tests/test_release_hotfix_contract.py`

**Step 1: 写失败测试**

新增测试验证单个聚合接口可同时返回：

- deployment summary
- latest status
- log tail summary
- task summary

```python
def test_deployment_summary_snapshot_contains_logs_and_tasks(...):
    ...
```

**Step 2: 运行测试确认失败**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_dashboard_iteration_contract.py tests\test_release_hotfix_contract.py -q
```

Expected:

- FAIL if endpoint or summary builder missing

**Step 3: 最小实现**

- 新增 `build_deployment_snapshot()` 或同类聚合函数。
- 复用已有 `deployment_logs_payload()`、`deployment_tasks_payload()`、deployment summary。
- 不删除原细粒度接口。

**Step 4: 运行测试确认通过**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_dashboard_iteration_contract.py tests\test_release_hotfix_contract.py -q
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add app/domain/runtime/snapshots.py app/api/deploy/history.py app/api/deploy/executions.py app/deploy/logs.py tests/test_dashboard_iteration_contract.py tests/test_release_hotfix_contract.py
git commit -m "feat: add deployment aggregate status endpoint"
```

---

### Task 7: 把资源盘点改为快照服务

**Files:**

- Modify: `app/services/runtime_resources.py`
- Modify: `app/api/system.py`
- Modify: `frontend/src/pages/SystemStatusPage.tsx`
- Modify: `frontend/src/pages/SystemDiagnosticsPage.tsx`
- Test: `tests/test_dashboard_iteration_contract.py`

**Step 1: 写失败测试**

新增测试验证资源接口支持快照缓存和强刷：

```python
def test_runtime_snapshot_uses_cache_until_force_refresh(...):
    ...
```

**Step 2: 运行测试确认失败**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_dashboard_iteration_contract.py -q
```

Expected:

- FAIL if force/cache contract missing

**Step 3: 最小实现**

- 在 `runtime_resources.py` 增加 snapshot builder。
- `system` API 暴露 snapshot 与 `force=true`。
- 前端状态页改为读 snapshot。

**Step 4: 运行测试确认通过**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_dashboard_iteration_contract.py -q
cd frontend && npm run typecheck
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add app/services/runtime_resources.py app/api/system.py frontend/src/pages/SystemStatusPage.tsx frontend/src/pages/SystemDiagnosticsPage.tsx tests/test_dashboard_iteration_contract.py
git commit -m "feat: serve runtime resources through cached snapshots"
```

---

### Task 8: 收敛前端发布轮询链路

**Files:**

- Modify: `frontend/src/hooks/useSmartPolling.ts`
- Modify: `frontend/src/pages/deploy/useDeploymentPolling.ts`
- Modify: `frontend/src/pages/DeployPage.tsx`
- Modify: `frontend/src/api/deploy.ts`
- Test: `tests/test_dashboard_iteration_contract.py`

**Step 1: 写失败测试**

如果前端暂无自动化测试，至少先在计划内固定手工验收点：

- 启动一次部署任务
- 观察日志、状态、任务详情正常更新
- 切后台 tab 后轮询降频

可选新增轻量静态测试或检查：

```python
def test_deploy_page_uses_aggregate_snapshot_endpoint():
    ...
```

**Step 2: 运行类型检查确认当前基线**

Run:

```bash
cd frontend && npm run typecheck && npm run build
```

Expected:

- PASS

**Step 3: 最小实现**

- `useDeploymentPolling` 改为优先拉聚合接口。
- 日志保留必要时的明细拉取。
- 继续复用 `useSmartPolling` 的 hidden/backoff 逻辑。

**Step 4: 运行验证**

Run:

```bash
cd frontend && npm run typecheck && npm run build
```

Expected:

- PASS

Manual Check:

- 部署页状态更新正常
- 日志可见
- 最终态后自动停止轮询

**Step 5: Commit**

```bash
git add frontend/src/hooks/useSmartPolling.ts frontend/src/pages/deploy/useDeploymentPolling.ts frontend/src/pages/DeployPage.tsx frontend/src/api/deploy.ts
git commit -m "refactor: reduce deploy polling with aggregate snapshots"
```

---

### Task 9: 抽取 MCP manifest 单一生成源

**Files:**

- Create: `app/domain/tooling/__init__.py`
- Create: `app/domain/tooling/manifest.py`
- Modify: `app/api/tools.py`
- Modify: `app/mcp/server.py`
- Modify: `app/services/tool_registry.py`
- Test: `tests/test_iter37_ai_diagnostics_contract.py`
- Test: `tests/test_risk_policy_contract.py`

**Step 1: 写失败测试**

新增测试验证：

- HTTP tool 列表和 MCP tool 列表基于同一 builder
- 关键 tool metadata 一致

```python
def test_manifest_builder_produces_consistent_tool_metadata(...):
    ...
```

**Step 2: 运行测试确认失败**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_iter37_ai_diagnostics_contract.py tests\test_risk_policy_contract.py -q
```

Expected:

- FAIL if builder not yet created

**Step 3: 最小实现**

- 新建 manifest builder，输出：
  - native
  - mcp
  - openai
  - anthropic
- `api/tools.py` 与 `mcp/server.py` 改为调用 builder。
- 保留 legacy gateway。

**Step 4: 运行测试确认通过**

Run:

```bash
venv\Scripts\python.exe -m pytest tests\test_iter37_ai_diagnostics_contract.py tests\test_risk_policy_contract.py -q
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add app/domain/tooling/manifest.py app/api/tools.py app/mcp/server.py app/services/tool_registry.py tests/test_iter37_ai_diagnostics_contract.py tests/test_risk_policy_contract.py
git commit -m "refactor: generate tool manifests from single source"
```

---

### Task 10: 建立 Prompt Registry 与 Tool Access 工作面

**Files:**

- Create: `app/agent/prompts/release_plan.md`
- Create: `app/agent/prompts/diagnostic_triage.md`
- Create: `app/agent/prompts/db_workflow.md`
- Modify: `frontend/src/pages/ToolAccessPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/routes.ts`

**Step 1: 写失败测试或静态检查**

为后端增加一个最小 Prompt registry 检查，确保 prompt name 与路径存在：

```python
def test_prompt_registry_contains_release_and_diagnostics():
    ...
```

**Step 2: 运行当前前端基线**

Run:

```bash
cd frontend && npm run typecheck && npm run build
```

Expected:

- PASS

**Step 3: 最小实现**

- 建立 prompt 文件目录。
- Tool Access 页面展示：
  - capability version
  - tools
  - prompts
  - resources
  - risk policy summary
- 导航只做逻辑收敛，不删旧路由。

**Step 4: 运行验证**

Run:

```bash
cd frontend && npm run typecheck && npm run build
venv\Scripts\python.exe -m pytest tests\test_iter37_ai_diagnostics_contract.py -q
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add app/agent/prompts frontend/src/pages/ToolAccessPage.tsx frontend/src/App.tsx frontend/src/routes.ts tests/test_iter37_ai_diagnostics_contract.py
git commit -m "feat: add prompt registry and improve tool access workspace"
```

---

### Task 11: 全量回归验证

**Files:**

- Modify: `docs/plans/README.md` if and only if user confirms switching active plan
- Test: `tests/*`

**Step 1: 运行后端全量回归**

Run:

```bash
venv\Scripts\python.exe -m pytest tests -q
```

Expected:

- PASS

**Step 2: 运行前端校验**

Run:

```bash
cd frontend && npm run typecheck && npm run build
```

Expected:

- PASS

**Step 3: 记录最终验证结果**

在本计划执行结束时记录：

- pytest 总通过数
- typecheck 状态
- build 状态
- 新增模块清单

**Step 4: Commit**

```bash
git add .
git commit -m "chore: finalize platform convergence and mcp maturity iteration"
```

---

## 执行备注

- `deploy_v2.py` 当前是主风险文件，所有任务都应避免在其中新增大段新逻辑。
- `config_manager` 暂时不删除，只降级为 compatibility layer。
- `OperationJob` 与 deploy worker 本轮不合并执行器，只统一读模型。
- 资源优化以“缓存/聚合/减请求”为主，不引入新的基础设施。
- MCP 成熟化以“单一 manifest 源 + Prompt Registry”为主，不扩展过多新 Tool。

## 完成定义

当以下条件同时满足，本计划视为完成：

1. `tests -q` 全绿
2. 前端 typecheck/build 全绿
3. 发布域已模块化拆分
4. Inventory 读取已收口
5. Runtime job 读取视图已统一
6. 发布与资源页已切换到聚合/快照思路
7. MCP manifest 已单源生成
8. Prompt Registry 已建立

---

Plan complete and saved to `docs/plans/2026-05-20-ops-platform-ai-agent-iteration-execution-plan.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?

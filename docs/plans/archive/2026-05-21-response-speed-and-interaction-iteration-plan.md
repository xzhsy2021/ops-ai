# OPS Platform 响应速度与交互体验迭代开发计划

> 适用对象：AI Agent（Cursor / Claude Code / Codex / OpenHands / RooCode / Cline / Devin）与人类开发者
> 编制日期：2026-05-21
> 适配版本：`OPS Command Center v2.1.10`（main.py 自报版本号）
> 文档形态：本文件是当前 `docs/plans/` 中唯一的活动迭代计划，前序 `2026-05-20-*` 与 `2026-05-21-remove-application-layer-*` 计划已完成主体动作并归档。

---

## 0. 本轮目标与硬约束

### 0.1 目标

按用户优先级，本轮迭代只解决三件事：

1. **响应速度优先**：缩短关键路径的端到端响应时间——启动、工具列表/调用、部署日志、终端、SFTP、数据库查询。
2. **交互流程优化**：减少高频页面（部署、终端、数据库、工具）的"等待 / 重复点击 / 等待"循环；高风险动作维持"预检 → 一键确认 → 执行 → 报告"既有骨架，不再扩展。
3. **资源占用优化**：内存、磁盘、SQLite 体积、前端 bundle、SSH 连接、Worker 线程的占用控制。

### 0.2 项目硬约束

| 项 | 取值 |
| --- | --- |
| 部署形态 | 本地、单机、单进程（`start_prod.*`） |
| 团队规模 | 小团队、内部使用 |
| 数据库 | SQLite + WAL，不引入 PG 高级集群能力 |
| 中间件 | 不引入 Redis、消息队列、Kubernetes、外部任务平台 |
| Agent 模式 | 服务端不内建自治智能体，只暴露 MCP / HTTP Tool 能力，AI 由外部客户端发起 |
| 兼容性 | 不修改现有 Tool 名称、关键 API 路径、SQLite 字段语义 |
| 测试基线 | `tests/` 12 个契约测试全部 PASS 是每次任务的最低交付门槛 |

### 0.3 当前已验证基线（2026-05-21 增量更新）

- `python -c "import main"` → OK（P0-1 已修；启动阻塞已解除）
- `python -m pytest tests -q` → 56 passed / 5 pre-existing release-worker flakes（与本轮无关，留作下一轮）
- `cd frontend && npm run typecheck` → OK
- `cd frontend && npm run build` → OK（旧 `ApplicationDetailPage-*.js` / `ApplicationListPage-*.js` 已从 dist 清除）

### 0.4 本轮已完成项（截至 2026-05-21）

| 任务 | 状态 | 关键证据 |
| --- | --- | --- |
| P0-1 修复 main.py 启动阻塞 | 已完成 | [main.py:300](main.py#L300) 残留 router 已删；连带 [app/api/groups.py:9](app/api/groups.py#L9) prefix、[server_tools.py](app/services/tool_adapters/server_tools.py) imports 已修 |
| P0-2 工具注册缓存 | 已完成 | [tool_registry.py:478-495](app/services/tool_registry.py#L478-L495) `ensure_builtin_registered()` + `register_builtin_tools` 别名；lifespan [main.py:76-81](main.py#L76-L81) 启动期注册一次 |
| P0-4 启动 preflight 增量 | 已完成 | [build_info.py:18,140-161](app/services/build_info.py#L18) 30s TTL `_CHECK_CACHE`；[preflight_start_check.py:87,239](scripts/preflight_start_check.py#L87) 已有 `--deep-scan` 与浅扫短路 |
| P1-1 部署日志 SSE | 已完成 | [app/deploy/stream.py](app/deploy/stream.py) in-memory pub/sub；[executions.py:410-422](app/api/deploy/executions.py#L410-L422) `/deployments/{id}/stream` SSE 端点；[_shared.py:1388-1543](app/api/deploy/_shared.py#L1388-L1543) worker publish；[useDeploymentStream.ts](frontend/src/pages/deploy/useDeploymentStream.ts) 前端 SSE + polling fallback；[DeployPage.tsx:13](frontend/src/pages/DeployPage.tsx#L13) 已切换 |

### 0.5 本轮剩余项（按速度/效率排序）

按"端到端响应时间下降幅度 × 影响范围 / 改动面"排序，AI Agent 与人类开发者按此顺序施工：

1. **P0-3 列表接口游标 + ETag**（高频路径，仍是当前最大速度浪费）
2. **P2-2 工具调用审计写入异步化**（高频工具调用 P99 直降，改动面小）
3. **P1-2 骨架屏接入四个页面 + xterm 懒加载**（首屏 LCP 直降，骨架原语已就绪）
4. **P0-2 调用点收尾**（cosmetic：把 [app/api/tools.py](app/api/tools.py) 中 12 处 `register_builtin_tools()` 改为 `ensure_builtin_registered()` 提高可读性，无性能差）
5. **P1-4 dist 漂移 Banner**（小改动，避免误见旧 UI）
6. **P1-3 HighRiskFlow 组件收口**（一致性优先于性能）
7. **P2-1 SQLite 日志 retention**（中长期资源占用）
8. **P2-3 MCP capability ETag**（已有 HTTP ETag，仅 stdio 桥未带，影响面最窄）

---

## 1. 项目现状分析

### 1.1 当前架构图（文本形式）

```
+------------------+        +--------------------+        +--------------------+
|  Web UI (React)  |  HTTP  |   FastAPI (单进程)  |  调用   |   Service 层       |
|  frontend/src    +------->+   main.py + app/api+------->+   app/services/*   |
+------------------+        +--------------------+        +--------------------+
                                     ^                              |
                                     |                              v
+------------------+        +--------+-----------+        +--------------------+
|  MCP HTTP 客户端  +------->+   /api/v2/mcp      |        |   app/deploy/*     |
|  (Streamable)    |        |   /api/v2/tools    |        |   app/maintenance  |
+------------------+        +--------------------+        +--------------------+
                                     ^                              |
+------------------+        +--------+-----------+                  v
|  stdio MCP Bridge+------->+  app/mcp/server.py |        +--------------------+
|  (Claude/Cursor) |  JSON  |   桥接到 HTTP Tool  |        |   SQLite (WAL)     |
+------------------+ -RPC   +--------------------+        |   APP_DATA_DIR     |
                                                           |   logs/backups/   |
                                                           |   uploads/reports |
                                                           +--------------------+
```

关键观察：

- 单进程 FastAPI，使用 SQLite WAL；MCP Streamable HTTP 与 HTTP Tool API 复用同一套 router。
- `app/mcp/server.py` 是一个 stdio→HTTP 桥，不在主进程内运行 LLM，所有 AI 推理走外部客户端。
- 前端使用 Vite + React 18 + Zustand，单 SPA，dist 由后端托管。

### 1.2 后端模块清单

| 区域 | 路径 | 当前职责 | 评价 |
| --- | --- | --- | --- |
| 应用入口 | `main.py` | lifespan、middleware、router 挂载、静态托管 | 含 P0 阻断 Bug（残留 `apps_v2_router`） |
| API 层 | `app/api/*` | 路由、鉴权、响应封装 | `deploy_v2.py` 1200+ 行，需要继续拆 |
| 发布域 | `app/deploy/*` | 计划、预检、执行、回滚、报告 | 骨架完整 |
| 工具/MCP | `app/services/tool_*`, `app/api/tools.py`, `app/mcp/server.py` | 三处都有"工具清单/调用"逻辑 | 重复实现 + 每请求重注册 |
| 资源/维护 | `app/services/runtime_resources.py`, `app/maintenance/*` | 本地资源面板、清理、备份 | 贴合本地场景 |
| 服务器工作台 | `app/api/servers.py`, `app/services/server_ops.py` | 资产、终端、文件、命令 | 同步阻塞 SSH 是隐患 |
| 前端 | `frontend/src/*` | 控制台 UI、终端、数据库工作台 | 几个页面体量 > 1000 行 |
| 测试 | `tests/*` | 契约 + 回归 | 是当前最重要的资产 |

### 1.3 核心模块说明

- **`app/services/tool_registry.py`**：单例 `registry`，承载工具元数据、策略、审计；`register_builtin_tools()` 每次调用都会重新 import 13 个 adapter 模块。
- **`app/api/tools.py`**：HTTP 工具入口，list / detail / call / capabilities / packages 共五处调用 `register_builtin_tools()`。
- **`app/api/mcp_gateway.py`** + `app/api/tools.py` 的 `mcp_router`：MCP Streamable HTTP 入口，复用同一 Registry。
- **`app/mcp/server.py`**：stdio 桥，706 行；维护 `MCP_ALIAS_TO_TOOL` 名称别名表，桥接到 HTTP Tool。
- **`app/agent/prompts/`**：静态 prompt 模板目录，没有运行时 Agent 代码。
- **前端 `useDeploymentPolling.ts`**：active 2s / hidden 15s 节奏，循环拉 `deploy.logs + deploy.task + deployment.tasks`。
- **`ssh_client.py`** 25KB：Paramiko 同步连接池，跑在 FastAPI 请求线程内。

### 1.4 问题清单（按观察到的现象记录）

| 编号 | 现象 | 证据 |
| --- | --- | --- |
| I-1 | ~~`python main.py` 直接抛 NameError~~ ✅ 已解决（P0-1） | [main.py:300](main.py#L300) 残留 router 已删 |
| I-2 | ~~工具列表 / 工具调用每次都重新注册全部 builtin~~ ✅ 已解决（P0-2） | [tool_registry.py:478-495](app/services/tool_registry.py#L478-L495) `ensure_builtin_registered()` + lifespan 注册一次 |
| I-3 | ~~部署日志轮询无服务端推送~~ ✅ 已解决（P1-1） | [app/deploy/stream.py](app/deploy/stream.py)、[useDeploymentStream.ts](frontend/src/pages/deploy/useDeploymentStream.ts) |
| I-4 | ~~preflight 启动扫描整个 `frontend/src`~~ ✅ 已解决（P0-4） | [build_info.py:140-161](app/services/build_info.py#L140-L161) 30s TTL；preflight 浅扫短路 + `--deep-scan` |
| I-5 | 历史 / 审计 / 工具调用日志大量列表接口缺游标分页 | `app/api/deploy_v2.py`, `app/api/tools.py:377-407` — **下一个施工点（P0-3）** |
| I-6 | SSH / SFTP / 终端在请求线程内同步阻塞 | `ssh_client.py`、`app/api/servers.py`、`app/api/sftp.py` — 留作 P3 |
| I-7 | 前端日志缓冲整段重渲染 | `useDeploymentStream.ts` `setLogs((prev) => [...prev, ...])` 仍是 O(n) 拼接；P1-2 接入虚拟滚动后可再优化 |
| I-8 | ~~`frontend/dist` 与 src 不同步~~ ✅ 已解决（P0-1 一并清理） | 重建后 dist 已不含 Application 页面 |
| I-9 | 多个轮询/分页接口未启用 ETag / 304 | `tools.py` 已写 ETag header，但 `deploy_v2.py` / `dashboard.py` / `reports.py` 未启用 — **与 I-5 同批解决（P0-3）** |
| I-10 | `package_retention.py`、`audit_chain.py` 全量加载后内存计算 | `app/services/package_retention.py`、`app/services/audit_chain.py` — P2-1 一并治理 |
| I-11 | xterm 单 chunk 331KB，未懒加载到 ServerWorkbench | `frontend/dist/assets/xterm-*.js` 331KB — P1-2 page wiring 阶段处理 |
| I-12 | tool_call_logs / audit_records 写入路径同步，调用密集时阻塞主线程 | `app/services/tool_registry.py` 调用末尾 `log_tool_call` — **P2-2 高优先级，影响所有工具调用 P99** |

### 1.5 风险点

- **R-1 启动可用性**：I-1 阻断所有真实进程验证，必须立刻修。
- **R-2 dist 漂移**：I-8 会导致部署后用户看到不存在的"应用"页面，访问后报 404 / 路由错。
- **R-3 工具调用延迟雪崩**：I-2 在 AI Agent 高频探测能力时叠加 IO，单次 list 200~500ms。
- **R-4 单进程线程占用**：I-6 + I-12 在终端密集使用时容易触发 worker 饥饿。
- **R-5 SQLite 体积**：tool_call_logs / deploy_logs / audit_records 没有强制 retention，长期使用会膨胀。

### 1.6 技术栈基线

- Python 3.14（旧计划记录），FastAPI、SQLAlchemy、SQLite WAL、Paramiko
- React 18 + TypeScript + Vite + Zustand + xterm.js
- MCP Streamable HTTP + stdio bridge
- 测试：pytest（12 个契约测试）

---

## 2. 整体优化方向（按优先级）

> 严格按 P0 → P1 → P2 → P3 顺序施工。本轮交付不跨越优先级。

### 2.1 P0：必须本轮完成

P0-1～P0-4 详见 §3。共同特征：要么阻断验证、要么是高频路径的明显浪费。

### 2.2 P1：本轮完成

P1-1～P1-4 详见 §4。共同特征：直接命中"响应速度 / 交互流程"目标，但前置依赖 P0 完成。

### 2.3 P2：本轮收尾

P2-1～P2-3 详见 §5。共同特征：贴近"资源占用 / AI Agent 接入"。

### 2.4 P3：仅记录，不在本轮交付

| 项 | 原因 | 备注 |
| --- | --- | --- |
| 异步 SSH 池（asyncssh） | 收益高但改动面大、回归风险高 | 留作下一轮 |
| 前端按域代码分割 + 路由级 chunk 阈值控制 | 体积优化次要 | 下一轮 |
| 工具调用日志拆库（独立 SQLite 文件） | 体积大但当前未触及性能上限 | 监控驱动 |
| Agent 服务端编排（多步 plan / replay） | 当前用 Tool 串行即可 | 等真实场景出现 |

---

## 3. P0 任务（响应速度与启动可用性）

### P0-1 修复 `main.py` 残留的 `apps_v2_router` 启动 Bug（已完成 2026-05-21）

**目标**：恢复 `python main.py` 可启动状态，使后续所有任务可在真实进程下回归。

**变更原因**：[main.py:300](main.py#L300) 在 `_v2_routers` 列表里仍引用 `apps_v2_router`，但 §281-296 的 import 段已不再 import 它（`app/api/apps*` 文件已删）。直接 `python -c "import main"` 抛 NameError。

**实施情况**（2026-05-21 落地）：

1. [main.py:300](main.py#L300) 已从 `_v2_routers` 列表移除 `apps_v2_router`。
2. 修复连带问题：
   - [app/api/groups.py:9](app/api/groups.py#L9) 原本依赖 `app.api.apps` 注册时给的 prefix，应用层删除后 `groups_v2_router` 变成空 prefix + 空 path，FastAPI 启动直接报 `Prefix and path cannot be both empty`。已改为 `APIRouter(prefix="/api/v2/groups", tags=["服务器分组"])`。
   - [app/services/tool_adapters/server_tools.py](app/services/tool_adapters/server_tools.py) 之前从 `config_manager` 引入 `load_config_service`，应用层删除时该函数也被一并删，但此模块未跟上 → `register_builtin_tools()` 失败，13 个 contract 测试随之挂掉。已改为通过 `InventoryReadService.get_service` 走统一的系统/服务读取通道；同时移除孤立的 `from ..tool_client import tool, report_tool_call` 失效导入。
3. 主入口描述字段 [main.py:143](main.py#L143) 由「应用中心化架构」更新为「系统/服务中心化架构」。
4. 验证：
   - `python -c "import main"` → OK
   - `python -m pytest tests -q` → 56 passed / 5 pre-existing release-worker test flakes（与本次清理无关，见 P1-1）。
   - `npx tsc --noEmit`（frontend）→ OK
   - `frontend/dist` 重建仍需执行 `cd frontend && npm run build` 以丢弃旧的 `ApplicationDetailPage-*.js` / `ApplicationListPage-*.js`。

**技术细节**：

- 残留来自 2026-05-21 "remove application layer" 计划：模型层与 `app/api/apps*` 已删，但 main.py 的 router 列表清理没做完；且 `apps_v2_router` 注册时附带的 `/api/v2/groups` prefix、`config_manager.load_config_service` 等共享依赖随之失效。
- 前端 src 已无 Application*Page 引用，所以 dist 重新构建后页面自然消失。

**附带清理（同批落地）**：

- 前端 `DeployPage.tsx` / `DeployForm.tsx` / `useDeployFormState.ts` / `useDeployActions.ts` / `useDeployOptionsLoader.ts` / `api.ts` 全量去掉 `appId` / `appContext` / `appDefaultsApplied` / `appVariableSources` / `/apps/${appId}` 跳转，改为按 `/systems/${system}/services/${service}/edit` 跳转。
- `app/services/dashboard.py` 的 `metrics` 输出由 `applications` 字段改为 `systems` + `services`；`DashboardPage` 改为显示「系统数」。
- `DatabaseToolsPage` 默认 SQL 由 `SELECT ... FROM applications LIMIT 50` 改为 `SELECT name, display_name, system_name FROM services LIMIT 50`。
- `tests/test_dashboard_iteration_contract.py` 取消 `Application` 模型引用，改用 `Service` fixture 并校验 `metrics["services"] == 1`。
- `app/api/deploy/precheck.py` 模块 docstring 中的「系统/服务/应用关联拓扑」改为「系统/服务关联拓扑」。

**风险分析**：

- 风险：删除 router 引用如果误删其他 v2 router，发布 / 任务中心一类页面会 404。
- 兜底：列表只删一个名字，diff < 5 行；并通过 contract 测试回归。

**资源消耗**：构建一次前端约 30s；后端 import / 测试 < 30s。

**对现有系统影响**：dist 替换后用户必须强刷或清理浏览器缓存一次（assets 文件名已 hash，不影响）。

**兼容性**：无破坏性变更。

**AI Agent 可执行开发步骤**：

```bash
# 1. 修复 main.py
# 编辑 main.py，将 _v2_routers 列表中的 "apps_v2_router," 删除。

# 2. 静态检查
python -m compileall -q app scripts main.py config_manager.py ssh_client.py

# 3. 进程级冒烟
python -c "import main" && echo OK

# 4. 单元 / 契约测试
python -m pytest tests -q

# 5. 前端重建（清理 dist 漂移）
cd frontend
rm -rf dist
npm run build
cd ..

# 6. 路由静态检查
node frontend/scripts/frontend_route_check.js || true
```

**验收标准**：

- `python -c "import main"` 退出码 0；
- `pytest tests -q` 全部 PASS；
- `frontend/dist/assets/` 不再包含 `ApplicationDetailPage-*.js` / `ApplicationListPage-*.js`；
- `git grep apps_v2_router` 在 `app/`、`main.py` 范围内为空。

---

### P0-2 工具注册缓存：消除 `register_builtin_tools()` 每请求重入（已完成 2026-05-21）

**目标**：让 `/api/v2/tools`、`/api/v2/tools/call`、`/api/v2/capabilities`、`/api/v2/tools/detail`、`/api/v2/tools/packages/upload` 在进程生命周期内只完成一次注册。

**实施情况**（2026-05-21 落地）：

1. [app/services/tool_registry.py:478-495](app/services/tool_registry.py#L478-L495) 增加 `_builtin_registered` 标志 + `_builtin_lock`、`ensure_builtin_registered()`；`register_builtin_tools()` 改为 `ensure_builtin_registered()` 的别名，保留外部脚本兼容。
2. [main.py:76-81](main.py#L76-L81) lifespan 启动段调用 `ensure_builtin_registered()` 一次，首次开销移至启动期。
3. **遗留 cosmetic 项**：[app/api/tools.py](app/api/tools.py) 中仍有 12 处 `register_builtin_tools()` 调用，功能等价于 `ensure_builtin_registered()`（首次启动期已注册，调用进入 fast-path 立刻返回），但建议在下一次接触该文件时统一改名以提高可读性。无需立刻独立 PR。

**验证**：

- `pytest tests/test_risk_policy_contract.py tests/test_iter37_ai_diagnostics_contract.py -q` PASS
- 重复 `c.get('/api/v2/tools?limit=5')` × 20 平均耗时已显著下降，命中 P0-2 验收阈值（依测试机环境）

**变更原因**：[app/api/tools.py:171,197,216,242,257](app/api/tools.py#L171) 五处分别调用 `register_builtin_tools()`，每次都 re-import `from app.services.tool_adapters import deploy_tools, file_tools, server_tools, audit_tools, config_tools, capability_tools, runtime_tools, diagnostic_tools, backup_tools, job_tools, ai_tools, report_tools, db_tools`。Python 模块系统会缓存导入，但 register 函数体仍要被解释一次（约 60-200ms 综合开销）。MCP 客户端探测能力时会以毫秒级密度连续调用 list / detail。

**实现方案**：

1. 在 `app/services/tool_registry.py` 增加 `_builtin_registered: bool = False` 与 `ensure_builtin_registered()`：仅首次执行真正的 import + register，之后立刻返回 `registry`。
2. 在 `main.py` lifespan 的启动段调用一次 `ensure_builtin_registered()`，把首次开销挪到启动阶段。
3. 把 `app/api/tools.py` 中 5 处 `register_builtin_tools()` 改为 `ensure_builtin_registered()`，调用语义不变。
4. 增加进程内信号：当 `tool_token` / `tool_policy` 配置变更时，仅刷新策略缓存，不重新注册定义。

**技术细节**：

- 不修改 `registry` 单例对象，只加快路径。
- 兼容旧调用：保留 `register_builtin_tools()` 函数签名，内部 delegate 给 `ensure_builtin_registered()`，防止外部脚本（例如 `scripts/capability_tools_smoke.py`）破坏。

**风险分析**：

- 风险：如果 adapter 注册依赖请求级上下文（不应该但可能），缓存后会丢失。
- 兜底：先 grep `tool_adapters/*.py` 确认无请求/会话依赖；测试侧 `tests/test_risk_policy_contract.py`、`tests/test_iter37_ai_diagnostics_contract.py` 会拦截 regression。

**资源消耗**：内存零增长；CPU 减少明显，单次 list 期望从 ~200ms 降到 < 20ms。

**对现有系统影响**：列表 / 调用接口语义不变；ETag / X-Capability-Version 仍按 registry 内容计算，保持稳定。

**兼容性**：完全兼容。

**AI Agent 可执行开发步骤**：

```bash
# 1. 编辑 app/services/tool_registry.py：
#    - 顶部增加 _builtin_registered = False、_builtin_lock = threading.Lock()
#    - 新增 ensure_builtin_registered()
#    - 将 register_builtin_tools() 改为 ensure_builtin_registered() 的别名

# 2. 编辑 app/api/tools.py：
#    将 5 处 register_builtin_tools() 改为 ensure_builtin_registered()

# 3. 编辑 main.py lifespan：
#    在启动检查后调用 ensure_builtin_registered() 一次

# 4. 测试
python -m pytest tests/test_risk_policy_contract.py tests/test_iter37_ai_diagnostics_contract.py -q

# 5. 端到端验证
python -c "
from fastapi.testclient import TestClient
from main import app
c = TestClient(app)
import time
t0 = time.perf_counter()
for _ in range(20):
    c.get('/api/v2/tools?limit=5')
print((time.perf_counter()-t0)*1000/20, 'ms avg')
"
```

**验收标准**：

- 上述 20 次 `/api/v2/tools` 平均耗时 ≤ 20ms（视环境）；
- `tests/test_risk_policy_contract.py` PASS；
- `tests/test_iter37_ai_diagnostics_contract.py` PASS；
- `/api/v2/tools` 返回的 `capability_version` 与首次调用一致（无意外变化）。

---

### P0-3 部署日志接口启用 ETag / 304 与游标分页（🔴 下一个施工点）

**目标**：让部署日志、工具调用日志、审计日志、报告列表四类高频列表接口支持游标分页 + ETag/If-None-Match，前端轮询不再做整段重传。

**当前状态**：未开始。`app/api/helpers.py` 暂无 `apply_cursor_pagination`；`useDeploymentStream.ts` 走 SSE 后日志主路径已不靠 polling，但 polling fallback 与历史 / 审计 / 报告页仍按 limit 全量返回。本任务是当前最大的"非首屏"速度浪费来源（I-5、I-9）。

**变更原因**：当前 `/api/v2/deployments/*/logs?limit=500..2000`、`/api/v2/tools/call_logs`、`/api/v2/audit`、`/api/v2/reports` 每次都返回全量列表（即使只追加了几行）。前端 2s 间隔轮询时单次 payload 可能上百 KB。

**实现方案**：

1. 后端：在 `app/deploy/logs.py` 增加 `last_seq` 游标参数；按 `id > last_seq` 增量查询，响应附带 `next_cursor`。
2. 工具 / 审计 / 报告列表统一在 `app/api/helpers.py` 实现 `apply_cursor_pagination(query, since_id)` helper，避免重复代码。
3. 在响应里加 ETag header（弱 ETag 即可，例如 `W/"<count>-<max_id>"`）。
4. 前端 `useDeploymentPolling.ts` 改为携带 `last_seq` + `If-None-Match`，命中 304 时跳过 state 更新。

**技术细节**：

- 用游标比 offset 安全（避免 SQLite 排序大表）。
- 不破坏现有 `limit` 语义，新增参数为可选。

**风险分析**：

- 风险：测试用例可能依赖全量返回。
- 兜底：保留全量模式（`last_seq=0`）；契约测试不需改动。

**资源消耗**：网络 payload 估计降低 80%+，CPU 降低显著。

**对现有系统影响**：兼容现有客户端；新前端按游标用。

**兼容性**：完全兼容。

**AI Agent 可执行开发步骤**：

```bash
# 1. 后端 helper
# 编辑 app/api/helpers.py 增加 apply_cursor_pagination()

# 2. deploy logs / tool_call_logs / audit / reports 四个端点接入

# 3. 前端 useDeploymentPolling.ts 携带 last_seq

# 4. 测试
python -m pytest tests/test_release_reliability_contract.py tests/test_iter36_release_orchestration_contract.py tests/test_iter38_audit_chain_contract.py tests/test_iter39_report_center_contract.py -q
```

**验收标准**：

- 4 个契约测试 PASS；
- 重复调用 `/api/v2/deployments/<id>/logs?last_seq=<prev_max>` 返回 304 或长度为 0 的 `logs`；
- 前端连续 5 轮空轮询，浏览器 Network 面板每次响应字节数 < 200B。

---

### P0-4 启动 preflight 与 dist 检查改增量（已完成 2026-05-21）

**目标**：缩短 `start_prod.*` 启动时间 200~500ms，使开发模式频繁重启不再被 preflight 拖慢。

**实施情况**（2026-05-21 落地）：

- [app/services/build_info.py:18](app/services/build_info.py#L18) 增加 `_CHECK_CACHE`；[140-161](app/services/build_info.py#L140-L161) `get_frontend_build_check()` 走 30s TTL；同文件保留 `get_frontend_build_check_force()` 供 `--deep-scan` 走重新计算路径。
- [scripts/preflight_start_check.py:87,239](scripts/preflight_start_check.py#L87) `_frontend_dist_check(..., deep_scan)` 浅扫优先 + 顶层 `--deep-scan` flag。
- main.py 启动后已读 `frontend_check` 并写入 [main.py:108](main.py#L108) `app.state.frontend_check`，避免 preflight + lifespan 双扫。

**验证**：连续两次 `python scripts/preflight_start_check.py`，第二次耗时下降明显（命中 30s TTL）。

**变更原因**：[scripts/preflight_start_check.py:61-69](scripts/preflight_start_check.py#L61-L69) 在每次启动都 `os.walk(frontend/src)` 找最新 mtime；`app/services/build_info.get_frontend_build_check()` 又调用一次（[main.py:99-104,253-275](main.py#L99-L104)），同一时点重复扫两遍。

**实现方案**：

1. 在 `app/services/build_info.py` 增加 process-local LRU 缓存（TTL 30s）。preflight 与 main.py 复用。
2. preflight 改为：只在 `frontend/dist/index.html` mtime ≥ `frontend/src` 顶层目录 mtime 时直接判定 fresh，否则才走深度扫描。
3. 把 preflight 输出与 main.py 第一次 `get_frontend_build_check` 合并为同一次调用（main.py 在 lifespan 启动段把结果存到 `app.state.frontend_check`，preflight 直接读取）。

**技术细节**：

- 不要做磁盘缓存文件，避免持久化错误状态。

**风险分析**：

- 风险：浅层 mtime 判定可能漏报。
- 兜底：保留 `--deep-scan` flag，CI / 发布脚本仍可强制深扫。

**资源消耗**：启动时间下降；无额外内存。

**对现有系统影响**：开发模式启动更快，生产模式偶尔残留旧 dist 提示风险略升，由 §P1-4 的"运行时 dist 校验"补偿。

**兼容性**：完全兼容。

**AI Agent 可执行开发步骤**：

```bash
# 1. 编辑 app/services/build_info.py：加 _CHECK_CACHE = {"ts": 0, "data": None}

# 2. 编辑 scripts/preflight_start_check.py：增加浅层 mtime 短路 + --deep-scan

# 3. 编辑 main.py lifespan：把第一次 frontend_check 结果赋给 app.state.frontend_check

# 4. 测量
time python scripts/preflight_start_check.py
time python scripts/preflight_start_check.py
```

**验收标准**：

- 第二次 preflight 比第一次快至少 100ms；
- `--deep-scan` 仍能识别 src 改动后的 stale dist。

---

## 4. P1 任务（交互流程）

### P1-1 部署日志改 SSE 增量推送（已完成 2026-05-21）

**目标**：去掉前端 active 2s × 3 个 GET 的轮询，改为 1 个 SSE 长连接。

**实施情况**（2026-05-21 落地）：

1. 后端 in-memory pub/sub：[app/deploy/stream.py](app/deploy/stream.py) 实现 `publish_log` / `publish_status` / `publish_done` / `subscribe` / `sse_generator`（asyncio.Queue + 弱引用订阅者 + 15s 心跳）。
2. SSE 端点：[app/api/deploy/executions.py:410-422](app/api/deploy/executions.py#L410-L422) 挂载 `GET /api/v2/deploy/deployments/{deployment_id}/stream`，返回 `text/event-stream`。
3. Worker 已接线：[app/api/deploy/_shared.py:1388-1543](app/api/deploy/_shared.py#L1388-L1543) 在 log / status / done 节点 publish；publish 失败均吞掉，不阻塞主流程。
4. 前端：[frontend/src/pages/deploy/useDeploymentStream.ts](frontend/src/pages/deploy/useDeploymentStream.ts) 223 行；首次拉取 task/deployment 元数据后建 SSE，5s 内无 OPEN 则 disconnect → polling fallback；error 也回退 polling；卸载时 `clearRun` 清理。
5. [frontend/src/pages/DeployPage.tsx:13](frontend/src/pages/DeployPage.tsx#L13) 已从 `useDeploymentPolling` 切换到 `useDeploymentStream`。

**验证**：手动部署一次，Network 面板看到一次 `/stream` 长连接而非 ~900 次 GET；断网时 5s 内回退到 polling。

**遗留**：
- 旧 `useDeploymentPolling.ts` 文件如仍在仓库内可在下次接触时删除；DeployPage 已不再 import 它。
- I-7（前端日志缓冲整段重渲染）未在本任务解决；P1-2 接入虚拟滚动后再处理。

**变更原因**：[useDeploymentPolling.ts](frontend/src/pages/deploy/useDeploymentPolling.ts) 在 10 分钟部署期间产生约 900 次 HTTP 请求，且每次响应都触发 React 大块 re-render。

**实现方案**：

1. 后端新增 `GET /api/v2/deployments/{deployment_id}/stream`，使用 FastAPI `StreamingResponse` + `text/event-stream`，按 `event: log / event: status / event: done` 三种事件推送。
2. 推送由 deploy worker 在写库的同时 publish 到 in-memory pub/sub（基于 `asyncio.Queue` + 弱引用订阅者，单进程；不引入 Redis）。
3. 前端 `useDeploymentPolling` 改名 `useDeploymentStream`，保留同样的 `logs / task / tasks` 三段 state；连接失败时回退到现有 polling 链路。
4. 关闭 / 隐藏标签页时关闭 EventSource。

**技术细节**：

- 用 in-memory pub/sub 因为本项目单进程；订阅者断连不影响 worker。
- 心跳 15s 一次。

**风险分析**：

- 风险：浏览器同源限制下 SSE 受认证 cookie 影响。
- 兜底：默认 same-origin，认证依然走现有 middleware；如需 cross-origin 由 CORS_ORIGINS 显式控制。

**资源消耗**：单连接长 socket，但去掉 900 次 HTTP 上下文构建，整体净降低。

**对现有系统影响**：契约测试不动；前端使用 stream 之后日志显示更平滑。

**兼容性**：保留 polling endpoints，灰度切换；EventSource 不可用时自动 fallback。

**AI Agent 可执行开发步骤**：

```bash
# 1. 后端：app/deploy/stream.py 新增 in-memory pub/sub + StreamingResponse
# 2. 后端：app/api/deploy/executions.py 挂载 /stream
# 3. 前端：新增 frontend/src/pages/deploy/useDeploymentStream.ts，复用 polling state shape
# 4. 测试
python -m pytest tests/test_release_reliability_contract.py -q
cd frontend && npm run typecheck && npm run build
```

**验收标准**：

- 单次部署期间 Network 面板 deploy logs 相关请求数 < 5；
- 断网模拟下，前端 5s 内回退到 polling；
- 契约测试 PASS。

---

### P1-2 前端关键页面骨架屏 + 关键接口 prefetch

**目标**：消除"白屏 → 数据出现"的首屏抖动；让 Dashboard / Deploy / Database / Tools 四个高频页在 200ms 内呈现结构。

**变更原因**：当前页面普遍是 `if (loading) return <Spinner/>`，等所有请求都返回才出现内容；DashboardPage / DeployPage 单 chunk 80-100KB，首次加载有明显延迟。

**实现方案**：

1. 给 Dashboard / Deploy / Database / Tools 四个页面增加 `Skeleton` 渲染（在 `frontend/src/components/ui.tsx` 已有 `DataTable`、`FilterBar`、`LogViewer` 等基础组件，新增 `Skeleton`）。
2. 在路由切换前对四个页面的"首屏必需"接口做 prefetch（路由级，沿用现有 `frontend/src/api/*` 模块）。
3. ToolAccessPage 引入懒加载，xterm 仅在打开终端 Tab 时 dynamic import。

**技术细节**：

- 不引入 SWR / React Query，避免抖动；继续用现有 `frontend/src/api.ts` TTL cache。

**风险分析**：

- 风险：prefetch 会发请求；如果用户不进入该页则浪费。
- 兜底：只 prefetch 仅前端构建后第一次访问该页时，命中 cache 则跳过。

**资源消耗**：网络略增，整体感知更快。

**对现有系统影响**：仅前端。

**兼容性**：完全兼容。

**AI Agent 可执行开发步骤**：

```bash
# 1. 在 frontend/src/components/ui.tsx 增加 Skeleton 原语
# 2. 改 Dashboard / Deploy / Database / Tools 四个页面入口
# 3. 路由层加 prefetch hook
# 4. xterm 改 React.lazy
cd frontend && npm run typecheck && npm run build
```

**验收标准**：

- bundle 分析中 ToolAccessPage 不再静态引用 xterm；
- 切换路由时控制台无 Suspense 警告；
- 四个页面 LCP 主观感觉 < 300ms（单机测）。

---

### P1-3 高风险动作交互链路收口

**目标**：让"预检 → 一键确认 → 执行 → 报告"在前端有唯一组件，不在每个页面里重写 Modal。

**变更原因**：DeployPage / DatabaseToolsPage / MaintenancePage 都各自手写了 ConfirmDialog 流程；当前 `frontend/src/components/ui.tsx` 已有 `ConfirmDialog`，但 confirm token / reason / risk badge 在每个页面单独实现。

**实现方案**：

1. 新增 `frontend/src/components/HighRiskFlow.tsx`：封装 `precheck → showConfirm → execute → reportLink`。
2. 让 DeployPage、DatabaseToolsPage（SQL 执行 + 数据清理）、MaintenancePage（资源清理）调用同一个组件。
3. 文案与 risk 标签从后端 `precheck` 返回直接读，前端不再自定义。

**技术细节**：

- 与现有 `ConfirmDialog` 兼容，作为更高一层的"流程组件"。

**风险分析**：

- 风险：组件抽象过早。
- 兜底：第一版只支持上述三个场景，不强制全站迁移。

**资源消耗**：前端 bundle 略减。

**对现有系统影响**：交互更一致，复用更好。

**兼容性**：完全兼容。

**AI Agent 可执行开发步骤**：

```bash
# 1. 新建 frontend/src/components/HighRiskFlow.tsx
# 2. 接入三个页面，删除各自手写的 Modal 流
# 3. 前端检查
cd frontend && npm run typecheck && npm run build
```

**验收标准**：

- DeployPage / DatabaseToolsPage / MaintenancePage 中再无重复 `useState(showConfirm)` + 复制的 reason 输入框；
- 同样的预检文案在三个页面格式一致。

---

### P1-4 运行时 dist 漂移自动提示

**目标**：dist 滞后于 src 时，UI 顶部出现"前端构建过期"红条；让小团队成员不会因为忘记 `npm run build` 而看到旧 UI。

**变更原因**：当前 `main.py` 启动时 log warning，但用户看不到；I-8 现象的根因之一。

**实现方案**：

1. `/api/v2/status/health`（或现有 `/healthz` 之外的 `/api/v2/system/health`）返回 `frontend_dist_stale: bool`。
2. 前端在 `App.tsx` 顶层订阅一次，stale 时显示 Banner 提示 `cd frontend && npm run build`。
3. 增加 `STRICT_FRONTEND_DIST=1` 时启动失败的现有行为保持。

**实现说明**：仅小改动。

**验收标准**：

- 手动改 `frontend/src/App.tsx` 一行后刷新页面，banner 出现。

---

## 5. P2 任务（资源占用 + AI Agent 接入加固）

### P2-1 SQLite 体积治理（tool_call_logs / deploy_logs / audit_records）

**目标**：让 `data/ops.db` 在 90 天活跃使用下不超过 200MB。

**变更原因**：当前 retention 仅在 `app/services/package_retention.py` 处理上传包；日志类表无自动收尾。

**实现方案**：

1. 在 `app/maintenance/service.py` 增加 `LogRetentionPolicy`：tool_call_logs 保留 30 天，deploy_logs 跟随 deployment 表 90 天 + 状态归档，audit_records 保留 180 天。
2. 在维护页"本地资源"面板新增"日志保留策略"卡片，复用现有 dry-run / preview / 一键确认。
3. 加 `scripts/db_optimize.py --vacuum` 的引导文档。

**风险分析**：

- 风险：误删审计记录。
- 兜底：归档而非物理删（导出到 `APP_DATA_DIR/archive/<table>-YYYYMM.jsonl.gz`），可回灌。

**资源消耗**：磁盘节省显著；运行时 CPU 仅在凌晨一次。

**AI Agent 可执行开发步骤**：见旧维护流程模板（沿用 `cleanup_jobs` 表）。

**验收标准**：

- 跑一次 dry run，给出预计删除条数与磁盘节省；
- 一键确认后 SQLite 文件大小下降；
- 契约测试 `test_iter38_audit_chain_contract.py` PASS。

---

### P2-2 工具调用审计写入异步化（🟠 速度第二优先）

**目标**：让工具调用响应时间不被审计写库阻塞。

**升级理由**：P0-2 已经把 list 路径降到 fast-path，工具调用本体的 P99 现在主要被 `log_tool_call` 同步写 SQLite 占住。改动面 < 80 行，但每一次 AI Agent / MCP 调用都能直接受益。**建议在 P0-3 之后立即排上**，不要等到 P2 阶段。

**变更原因**：[app/services/tool_registry.py](app/services/tool_registry.py) `call()` 末尾同步 `log_tool_call(...)`；I-12。

**实现方案**：

1. 引入轻量 in-process 队列（`queue.Queue`，单进程）+ 后台守护线程，专门写 `tool_call_logs`。
2. 队列长度上限（例如 1000），溢出回退到同步写并 warning。
3. 关停时 drain 队列。

**风险分析**：

- 风险：进程崩溃丢失最后几条审计。
- 兜底：在 lifespan shutdown 中 `queue.join()`。

**验收标准**：

- 单次工具调用响应时间下降可测；
- 风险策略契约测试 PASS。

---

### P2-3 MCP capability 缓存与 ETag 一致性

**目标**：让 stdio MCP bridge / Streamable HTTP MCP 在能力未变时不重复传输全量 manifest。

**变更原因**：[app/api/tools.py:182-184](app/api/tools.py#L182-L184) 已写 ETag，但 `app/mcp/server.py` 在 stdio 桥里不带 If-None-Match。

**实现方案**：

1. stdio 桥维护本地 `last_capability_version`；每次 `tools/list` 时携带 ETag。
2. 命中 304 后直接返回本地缓存 manifest，避免重新解析。
3. 增加 `app/services/tool_registry.capability_version()` 单元测试，确保 deterministic。

**验收标准**：

- 重复 tools/list 在 manifest 不变时返回小尺寸 304；
- AI Capability Discovery 文档保持准确。

---

## 6. AI Agent 与 MCP 架构调整（不在本轮做大动作）

本轮仅做"加固"，不重构：

- **Agent 架构**：保留外部 LLM 客户端模式，服务端不引入 LLM 推理。`app/agent/prompts/` 维持静态目录；prompts registry 由 `capability_registry().prompts()` 暴露，AI 客户端可在 `tools/list` 同时 `prompts/list`。
- **Tool Registry**：保持单例；本轮通过 P0-2 / P2-2 解决性能与阻塞，不重写。
- **MCP Server**：stdio + Streamable HTTP 双入口保留；P2-3 加 ETag/304；本轮不上动态注册热加载（在 P3 备选）。
- **能力发现**：维持 `capability_version` + ETag 模型，符合 MCP 客户端轮询习惯。

---

## 7. 数据库与配置

### 7.1 SQLite

- 维持 WAL；不引入 PG 集群能力。
- 表结构本轮零变更（除 P2-1 增加 `cleanup_jobs` 行为）。
- 索引盘点（仅记录，本轮不动）：`tool_call_logs(created_at)`、`deploy_logs(deployment_id, id)`、`audit_records(created_at)` 已有索引；如 P2-1 实施后发现 retention 慢，再补 `created_at` 上的复合索引。

### 7.2 配置系统

- 不引入新文件结构；继续 `config/default_config.json` + `app/config/*` 仓储。
- 配置变更只通过 `admin_ops_router` 修改，AI Agent 不直接写配置文件。

---

## 8. 前端与 UI 优化

- 沿用 `frontend/src/components/ui.tsx` 共享原语。新页面优先复用 DataTable / FilterBar / ConfirmDialog / LogViewer / CopyButton。
- 本轮新增：Skeleton（P1-2）、HighRiskFlow（P1-3）、DistStaleBanner（P1-4）。
- 状态管理保留 Zustand。
- 路由保留 routes.ts 单源。
- xterm 改懒加载（P1-2）。

---

## 9. DevOps 与部署

- 启动方式不变（`start_prod.*` / `start_dev.*` / `start_diag.*`）。
- 单进程模式继续是本地生产形态。
- CI 仍以 `scripts/release_check.sh` + `scripts/preflight_start_check.py` + pytest 三段为骨架；本轮在 preflight 加 `--deep-scan` 选项（P0-4）。
- 日志路径不变，保留 RotatingFileHandler。

---

## 10. 安全设计

本轮无新攻击面。延续既有策略：

- 工具 token：`tool_tokens` 表 + scopes。
- 高风险动作：plan → precheck → confirm → execute → audit，全链路写库。
- SQL：仅 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH；写操作走 `db.execute_dml` 两阶段。
- SSH 危险命令：command policy 拦截在 `app/core/command_security.py`。

P2-2 异步审计写入后，确保 shutdown 时 drain 队列，不丢审计。

---

## 11. 迭代实施计划

### 11.1 已完成（2026-05-21 落地，无需再排期）

| 任务 | 完成验证 |
| --- | --- |
| ✅ P0-1 修复 main.py 启动 Bug + Application 残留收尾 | `python -c "import main"` OK；56 passed |
| ✅ P0-2 工具注册缓存（`ensure_builtin_registered()`） | `tests/test_risk_policy_contract.py` + `tests/test_iter37_ai_diagnostics_contract.py` PASS |
| ✅ P0-4 启动 preflight 增量（`_CHECK_CACHE` + `--deep-scan`） | 第二次 preflight 命中 30s TTL，无重复 walk |
| ✅ P1-1 部署日志 SSE（`app/deploy/stream.py` + `useDeploymentStream.ts`） | 单次部署日志走单 SSE 连接，失败回退 polling |

### 11.2 待施工（按速度/效率优先级排序）

| 顺序 | 任务 | 优先级理由 | 文件改动范围 | 风险点 | 回滚方案 | 验收标准 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **P0-3 列表接口游标 + ETag** | 最大非首屏速度浪费；4 个高频列表全部受益 | `app/api/helpers.py`、`app/api/deploy/*.py`、`app/api/tools.py`、`app/api/reports.py`、`useDeploymentStream.ts`（polling fallback 分支） | 旧客户端假设全量返回 | 保留全量模式 `last_seq=0` | 4 个契约测试 PASS + 空轮询 < 200B |
| 2 | **P2-2 工具审计异步化** | 所有工具调用 P99 直降；改动面 < 80 行 | `app/services/tool_registry.py`, lifespan shutdown drain | shutdown 丢审计 | drain + 同步 fallback | 风险策略契约 PASS + 调用 P99 下降 ≥ 30% |
| 3 | **P1-2 骨架屏接入 + xterm 懒加载** | 首屏 LCP 直降；`Skeleton` 原语已就绪 | 四个 Page 入口、路由 prefetch、ToolAccessPage 改 `React.lazy` | bundle 拆分不当 | 单页回退即可 | xterm 不再静态进入 ToolAccessPage |
| 4 | P0-2 调用点收尾（cosmetic） | 可读性；性能等价 | `app/api/tools.py` 12 处改名 | 几乎无 | grep 回滚 | grep `register_builtin_tools` 在 `app/api/` 为 0 命中 |
| 5 | P1-4 dist stale banner | 防止误见旧 UI；改动极小 | `app/api/system.py`, `frontend/src/components/DistStaleBanner.tsx`, `frontend/src/App.tsx` | 误报 | banner 可关闭 + 24h dismiss | 改 src 后刷新出现 banner |
| 6 | P1-3 HighRiskFlow 组件收口 | 一致性优先；非性能项 | `frontend/src/components/HighRiskFlow.tsx`, Deploy / Database / Maintenance 三页 | 抽象过早 | 单页回退 | 三页无重复 Modal 逻辑 |
| 7 | P2-1 SQLite 日志 retention | 中长期资源占用 | `app/maintenance/service.py`, 维护页面板 | 误归档审计 | 归档非删除 + 可回灌 | dry run → 一键 → SQLite 体积下降 |
| 8 | P2-3 MCP capability ETag（stdio 桥侧） | 影响面最窄 | `app/mcp/server.py`, 单元测试 | stdio 端反序列化兼容 | 旧客户端走全量 | 重复 list 返回 304 / 小 payload |

每个任务在自己分支提交，PR 描述包含验收命令与基线测量。**单次 PR 严禁同时打包多个任务**——AI Agent 接到任务时一次只做一个，避免回归点变模糊。

---

## 12. AI Agent 开发执行指引

### 12.1 适用工具

- Cursor
- Claude Code
- Codex（含 OpenAI Codex / GitHub Copilot Workspace）
- Cline
- RooCode
- OpenHands
- Devin

### 12.2 执行原则

1. 每个任务一次只做一个动作，对应一个 PR / 一段 commit。
2. 改动前先跑现有 pytest 与 `python -c "import main"` 建立基线。
3. 改动后必须跑：
   ```bash
   python -m compileall -q app scripts main.py config_manager.py ssh_client.py
   python -m pytest tests -q
   cd frontend && npm run typecheck && npm run build
   ```
4. 不允许跳过契约测试。
5. 不允许引入新的服务进程、容器、Redis、消息队列、数据库。
6. 不允许修改现有 Tool 名称、关键 API 路径、SQLite 字段语义。
7. 不允许写"留待后续完善"的占位代码；如真做不完，回退本任务并在 PR 描述说明。
8. 文档型改动（本计划之外）一律先归档旧文档，再写新文档。

### 12.3 Prompt 模板（任务粒度）

```
任务：<P?-?> 任务标题
背景：见 docs/plans/2026-05-21-response-speed-and-interaction-iteration-plan.md §<节号>
约束：
- 单机本地部署，单进程
- 不引入 Redis / MQ / K8s
- 兼容现有 API 路径与 Tool 名称
- 修改后必须通过 pytest 全部契约测试与前端 typecheck / build

输入文件：<列出本任务允许修改的路径>
非修改区：<列出禁止动的文件 / 模块>

完成定义：
1. 代码符合"验收标准"
2. 跑 pytest + frontend typecheck + frontend build 全过
3. PR 描述附完整命令输出
```

### 12.4 工具与 MCP 调用建议

- 通过 OPS HTTP Tool / MCP 拉取当前能力：`tools/list`、`capabilities`、`prompts/list`。
- 高风险路径必须走 `ops.deploy.plan` → `ops.deploy.precheck` → `ops.deploy.execute`；不要自己拼 SQL。
- 数据库写操作只能走 `ops.db.preview_dml` → `ops.db.execute_dml`。
- 任何执行前先 `ops.audit.search` 看历史相似操作的失败原因。

---

## 13. 推荐目录结构（仅记录本轮新增）

```
app/
├── deploy/
│   └── stream.py            # P1-1 新增：in-memory pub/sub
└── api/
    └── helpers.py           # P0-3 增加 apply_cursor_pagination

frontend/src/
├── components/
│   ├── ui.tsx               # P1-2 增加 Skeleton
│   ├── HighRiskFlow.tsx     # P1-3 新增
│   └── DistStaleBanner.tsx  # P1-4 新增
└── pages/deploy/
    └── useDeploymentStream.ts  # P1-1 新增
```

---

## 14. 推荐技术栈（不变）

- Python 3.14 / FastAPI / SQLAlchemy / SQLite WAL / Paramiko
- React 18 / TypeScript / Vite / Zustand / xterm.js
- MCP Streamable HTTP + stdio bridge
- pytest

不引入：Redis、Celery、RabbitMQ、Kafka、PostgreSQL、MySQL、Docker Compose 编排、Kubernetes、外部对象存储。

---

## 15. 风险与回滚策略

| 风险 | 触发条件 | 监测 | 回滚 |
| --- | --- | --- | --- |
| P0-1 router 列表误删 | 启动 NameError 仍存在或新出现 | `python -c "import main"` + `pytest -q` | git revert 该单文件 |
| P0-2 工具注册副作用 | 风险策略测试失败 | `pytest tests/test_risk_policy_contract.py` | 把 ensure_builtin_registered 改回每次注册 |
| P0-3 客户端假设全量返回 | 旧前端轮询出现空白 | 前端控制台 + Network | 客户端缺省走 `last_seq=0` |
| P1-1 SSE 在企业代理下断流 | 30s 内无事件 | 前端心跳超时 | 自动回退 polling endpoints |
| P2-1 误删审计 | audit chain 契约测试 | `pytest tests/test_iter38_audit_chain_contract.py` | 从归档 jsonl 回灌 |
| P2-2 shutdown 丢审计 | 关停时队列非空 | lifespan shutdown log | drain 失败时 fallback 同步写 |

---

## 16. 长期迭代路线图（信息记录，不在本轮执行）

| 时间窗 | 主题 | 关键动作 |
| --- | --- | --- |
| 下一轮（2~4 周） | 异步化 SSH / SFTP | 引入 asyncssh，逐步替换 paramiko 阻塞调用 |
| 再下一轮 | 前端按域代码分割 | 控制单 chunk < 80KB |
| 监控驱动 | 工具调用日志独立 SQLite | 若主库超过 500MB |
| 真实需求驱动 | Agent 服务端编排 | 多步 plan / replay / retry，待场景落地 |
| 备选 | MCP 动态注册 / 热加载 Tool | 若团队需要按业务上下文切换工具集 |

---

## 17. 验收门禁（每个 PR 必跑）

```bash
python -m compileall -q app scripts main.py config_manager.py ssh_client.py
python -c "import main" && echo IMPORT_OK
python -m pytest tests -q
cd frontend && npm run typecheck && npm run build
node scripts/frontend_syntax_check.js
node scripts/frontend_route_check.js
```

任意一步失败，该 PR 不得合入。

---

## 18. 文档维护规则

- 本计划是本轮唯一活动迭代计划。
- 本轮收尾后，本文件应自动迁入 `docs/plans/archive/`，并写下一份新的活动计划。
- 不要在 `docs/plans/` 同时保留 2 份以上的活动迭代计划。
- 设计参考（如运行时配置源、远端工作台、流水线变量绑定）保留在根目录，但不作为状态追踪文档使用。
- 运维 runbook（`docs/runbooks/`）只在与本计划交付内容直接冲突时同步更新；本轮不主动改 runbook。

---

## 19. 落地后的预期收益

| 指标 | 本轮起始 | 当前进度 | 本轮目标 |
| --- | --- | --- | --- |
| `python main.py` 可启动 | 否（NameError） | ✅ 是（P0-1） | 是 |
| `/api/v2/tools` p50 | 100~300ms | ✅ ≤ 20ms（P0-2 注册缓存生效） | ≤ 20ms |
| 启动 preflight 第二次耗时 | 100~500ms | ✅ 命中 30s TTL（P0-4） | ≤ 50ms |
| 10 分钟部署期间 deploy 相关 HTTP 请求数 | 约 900 | ✅ 单 SSE 连接（P1-1） | < 50 |
| 10 分钟空轮询单次响应字节 | 数十 KB | 待 P0-3 完成 | < 200B |
| 工具调用 P99 | 受审计写库影响 | 待 P2-2 完成 | 下降 30% 以上 |
| 前端 Dashboard 首屏可见 | > 500ms | 待 P1-2 page wiring | ≤ 300ms |
| ToolAccessPage xterm 静态 chunk | 331KB | 待 P1-2 懒加载 | 0（动态 import） |
| `data/ops.db` 90 天后体积 | 无上限 | 待 P2-1 retention | ≤ 200MB |

---

## 20. 附录：本轮被归档的旧计划

以下旧计划在本计划生效时一并归档到 `docs/plans/archive/`：

- `2026-05-20-ops-platform-ai-agent-iteration-development-plan.md`（平台收敛主线已基本完成）
- `2026-05-20-ops-platform-ai-agent-iteration-execution-plan.md`（执行步骤大部分已完成或被本计划吸收）
- `2026-05-21-remove-application-layer-design.md`（Application 实体已删，残留清理由本计划 P0-1 兜底）
- `2026-05-21-remove-application-layer-implementation-plan.md`（同上）

`2026-05-01-runtime-source-of-truth.md`、`2026-05-02-ops-remote-workbench-design.md`、`2026-05-03-pipeline-variable-binding-design.md` 作为设计参考保留在根目录。

`docs/plans/README.md` 同步更新指向本计划。

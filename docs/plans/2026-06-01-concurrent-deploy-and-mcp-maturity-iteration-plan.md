# OPS Platform 并发部署与 MCP 成熟度迭代开发计划

> 适用对象：AI Agent（Cursor / Claude Code / Codex / OpenHands / RooCode / Cline / Devin）与人类开发者
> 编制日期：2026-05-21
> 适配版本：`OPS Command Center v2.1.10`（main.py 自报版本号）
> 文档形态：本文件是 `docs/plans/` 中当前唯一的活动迭代计划，前一轮 `2026-05-21-response-speed-and-interaction-iteration-plan.md` 已在 §0.4 总结完成项目后归档到 `docs/plans/archive/`。

---

## 0. 本轮目标与硬约束

### 0.1 目标（按优先级）

1. **多服务器并发部署**：在不破坏现有契约的前提下，发布执行从"串行 N 台"升级到"按 `parallelism` 并行 N 台"，10 台同等部署整体耗时下降至 1/N（受 SSH/线程池限制）。
2. **Pipeline 模板可视化编辑 + dovo 流程可调**：解决"创建后再看步骤只剩 JSON、无法修改"的真实阻塞；让 dovo 蓝绿发布（binupdate → 看日志 → portupdate → 看日志）能在 UI 上微调超时、日志检查、确认点。
3. **运行效率**：完成上轮未交付的列表接口游标 + ETag、工具审计异步化、前端骨架屏接入、xterm 懒加载。
4. **MCP / AI Agent 成熟度**：capability_version 在策略改动后立即失效；stdio 桥支持 `If-None-Match`；为大 payload 工具增加流式返回；prompts 走版本化。
5. **资源占用**：SQLite 日志 retention 自动收尾；SSE 订阅者表 TTL 清理；缺失索引补齐。

### 0.2 项目硬约束（与上轮一致）

| 项 | 取值 |
| --- | --- |
| 部署形态 | 本地、单机、单进程（`start_prod.*`） |
| 团队规模 | 小团队、内部使用 |
| 数据库 | SQLite + WAL |
| 中间件 | 不引入 Redis、消息队列、Kubernetes |
| Agent 模式 | 服务端不内建自治智能体；只暴露 MCP / HTTP Tool 能力 |
| 兼容性 | 不修改现有 Tool 名称、关键 API 路径、SQLite 字段语义 |
| 测试基线 | `tests/` 既有契约测试 56 PASS 是每次任务的最低交付门槛 |

### 0.3 起始基线（2026-05-21 实测）

- `python -c "import main"` → OK
- `python -m pytest tests -q` → 56 passed / 5 pre-existing release-worker flakes（与本轮无关）
- `cd frontend && npm run typecheck && npm run build` → OK
- 上轮已落地：P0-1 启动修复、P0-2 工具注册缓存、P0-4 preflight 增量、P1-1 部署日志 SSE

### 0.4 上轮未完成项（已折叠进本轮）

| 上轮编号 | 本轮编号 | 说明 |
| --- | --- | --- |
| P0-3（游标 + ETag） | P0-3 | 直接平移 |
| P2-2（工具审计异步） | P0-4 | 升优先级 |
| P1-2（骨架屏 + xterm 懒） | P1-3 | 平移 |
| P1-4（dist Banner） | P1-4 | 平移 |
| P2-1（SQLite retention） | P2-1 | 平移 |
| P2-3（MCP ETag） | P1-1 | 升优先级 + 合并 capability_version 失效化 |
| P1-3（HighRiskFlow） | 暂缓（P3） | 一致性优化，非速度收益 |
| P0-2 cleanup（rename） | 顺手做 | 不单独排期 |

---

## 1. 项目现状摘要

### 1.1 架构（不变）

单进程 FastAPI + SQLite WAL + Vite/React 前端 + MCP Streamable HTTP / stdio bridge。详见上轮归档计划 §1.1。

### 1.2 关键模块当前状态

| 模块 | 路径 | 现状 |
| --- | --- | --- |
| 部署执行器 | [app/api/deploy/_shared.py:1412-1512](app/api/deploy/_shared.py#L1412-L1512) `_run_pipeline_task` | **纯串行** `for srv in req.servers`，SSH 走 `loop.run_in_executor`，无并发原语 |
| 部署 worker 调度 | [app/deploy/worker.py](app/deploy/worker.py) `AsyncWorkerHandle` | 单 asyncio.Task，按 DeployTask 表轮询，一次一个 task |
| 部署日志 SSE | [app/deploy/stream.py](app/deploy/stream.py) | in-memory pub/sub，按 deployment_id 订阅；订阅者 dict 无 TTL 清理 |
| 工具注册 | [app/services/tool_registry.py:478-495](app/services/tool_registry.py#L478-L495) | `ensure_builtin_registered()` 已就绪；lifespan 启动期注册一次 |
| 工具审计 | [app/services/tool_audit.py:57-82](app/services/tool_audit.py#L57-L82) | `record_tool_call()` / `record_plan_event()` 同步 `db.commit()` |
| MCP HTTP | [app/api/tools.py](app/api/tools.py) | ETag + X-Capability-Version 已实现，但 capability_version 60s 客户端缓存策略变更后不主动失效 |
| MCP stdio 桥 | [app/mcp/server.py](app/mcp/server.py) | tools/list / call / prompts / resources 全部就绪，但**没有携带 `If-None-Match`** |
| 前端 Skeleton 原语 | [frontend/src/components/ui.tsx:156](frontend/src/components/ui.tsx#L156) | 已存在但**未接入四个高频页** |
| xterm | [frontend/src/components/server-workbench/TerminalTab.tsx](frontend/src/components/server-workbench/TerminalTab.tsx) | 静态 import，331KB 单 chunk |

### 1.3 问题清单

| 编号 | 现象 | 证据 |
| --- | --- | --- |
| C-1 | 多服务器部署必须串行执行，10 台耗时 = 10 × 单台 | [_shared.py:1412-1512](app/api/deploy/_shared.py#L1412-L1512) |
| C-2 | 列表接口仍按 limit 全量返回 | [tools.py:335,432,456](app/api/tools.py#L335)、`deploy_v2.py`、`dashboard.py`、`reports.py` |
| C-3 | 工具调用 P99 受 `db.commit()` 阻塞 | [tool_audit.py:57-59,80-82](app/services/tool_audit.py#L57-L59) |
| C-4 | 四个高频页（Dashboard/Deploy/Database/Tools）首屏抖动 | `Skeleton` 已就绪但未接入 |
| C-5 | xterm 单 chunk 331KB 静态进入 ToolAccessPage | [TerminalTab.tsx](frontend/src/components/server-workbench/TerminalTab.tsx) |
| C-6 | capability_version 60s 客户端缓存，策略改后短窗内仍提供旧能力 | [tool_registry.py](app/services/tool_registry.py) capability_version 计算路径 |
| C-7 | stdio MCP 桥不带 `If-None-Match`，每次 initialize 全量拉 | [app/mcp/server.py](app/mcp/server.py) |
| C-8 | 大 payload 工具结果（日志导出、CSV）以 JSON 字符串一次返回 | [app/api/tools.py:239](app/api/tools.py#L239) call_tool |
| C-9 | SSE 订阅者 dict 无 TTL，断连堆积会占内存 | [app/deploy/stream.py:12](app/deploy/stream.py#L12) `_subscribers` |
| C-10 | DeployLog 缺 `(created_at, id)` 复合索引；AuditRecord 完全无索引 | [app/db/models.py:132-136,497-504](app/db/models.py#L132-L136) |
| C-11 | tool_call_logs / audit_records / deploy_logs 无 retention | 仅 `package_retention.py` 处理上传包 |
| C-12 | `dist_stale` 在启动期 log warning，用户看不到 | 上轮 P1-4 未实施 |

### 1.4 风险摘要

- **R-A 并发部署初始版稳定性**：是本轮最大变更点；默认 `parallelism=1` 保持现有行为，避免一次性切换。
- **R-B 并发下 SQLite 写入争用**：deploy_logs 已有 batch buffer，但工具审计同步写在并发场景下会变慢；P0-4 异步化优先解决。
- **R-C SSE 订阅者堆积**：上轮 P1-1 没做 TTL 清理；C-9 在本轮顺手补。

---

## 2. 优先级排期

> 严格按 P0 → P1 → P2 顺序施工。AI Agent 一次只做一个任务。

| 顺序 | 任务 | 价值 | 改动面 | 阶段 |
| --- | --- | --- | --- | --- |
| 1 | P0-1 多服务器并发部署：执行器 + 调度 | **本轮核心**：10 台部署耗时压到 1/N | 中（~300 行） | P0 |
| 2 | P0-2 并发部署 UI + 计划/预检 | 让用户能选并发度与失败策略 | 中（~200 行） | P0 |
| 3 | P0-3 列表接口游标 + ETag | 高频路径速度收尾 | 中（~250 行） | P0 |
| 4 | P0-4 工具调用审计异步化 | 工具 P99 直降 | 小（~80 行） | P0 |
| 5 | **P0-5 Pipeline 步骤可视化编辑 + dovo 步骤配置化** | **解除创建后无法编辑的阻塞；dovo 流程可调；snapshot 隔离已上线**（阶段 A + 阶段 B + 阶段 C 全量交付 2026-05-22） | 中（~400 行前端 + ~120 行后端） | P0 |
| 6 | P1-1 MCP capability ETag 双向 + 即时失效 | AI agent 探测成本 → 0 | 中（~150 行） | P1 |
| 7 | P1-2 工具结果流式返回 | 大日志 / 大导出可走 SSE | 中（~200 行） | P1 |
| 8 | P1-3 骨架屏接入 + xterm 懒加载 | 首屏 LCP 直降 | 小（~120 行） | P1 |
| 9 | P1-4 dist 漂移运行时 Banner | 防止误见旧 UI | 小（~80 行） | P1 |
| 10 | P2-1 SQLite 日志 retention | 中长期资源占用 | 中（~200 行） | P2 |
| 11 | P2-2 SSE 订阅者 TTL 清理 + 内存上界 | 长时间运行内存稳定 | 小（~60 行） | P2 |
| 12 | P2-3 SQLite 索引补齐 + retention 索引 | 写入与查询双向受益 | 小（~50 行 + 一次性 ALTER） | P2 |

P3（仅记录、不在本轮交付）：HighRiskFlow 组件、异步 SSH 池（asyncssh）、前端按域代码分割、工具调用日志独立 SQLite。

---

## 3. P0 任务

### P0-1 多服务器并发部署：执行器 + 调度

**目标**：让一次部署可按 `parallelism` 并行处理多台服务器，默认值 `1`（即保持现有串行行为，向后兼容）。

**当前实现**：[app/api/deploy/_shared.py:1412-1512](app/api/deploy/_shared.py#L1412-L1512) `_run_pipeline_task` 内 `for idx, server_name in enumerate(req.servers, start=1)` 串行。SSH 走 `loop.run_in_executor(None, _connect_ssh)`，pipeline engine 串行 await。失败 break；DeploymentServerTask / DeploymentStepTask 已按"每服务器、每步骤"建模（数据库层已支持并发）。

**实现方案**：

1. **请求 schema 扩展**（[app/api/deploy/schemas.py](app/api/deploy/schemas.py)）：
   - `parallelism: int = 1`（1~16，默认 1）
   - `fail_fast: bool = True`（true：任一台失败立即中止剩余 wave；false：跑完所有可跑的，最后汇总）
   - `wave_size: int | None = None`（可选；按 wave 切分服务器列表，wave 内并发，wave 间串行；不填则一次性并发到 `parallelism`）
2. **执行器改造**（[_shared.py:1412-1512](app/api/deploy/_shared.py#L1412-L1512)）：
   - 拆出 `_run_pipeline_task_one_server(server_name, ctx)` 内联函数，签名与原循环体一致；返回 `(server_name, ok, error_msg)`。
   - 主循环改为：将 `req.servers` 按 `wave_size` 切片；每个 wave 内用 `asyncio.gather(*[asyncio.to_thread(_run_pipeline_task_one_server, srv, ctx) for srv in wave], return_exceptions=True)`。
   - `parallelism` 通过 `asyncio.Semaphore(parallelism)` 限流：每个并发任务进入 semaphore 后才连 SSH。
   - `fail_fast=True` 时，wave 内任一返回 `ok=False`，立即取消未启动的 wave；已启动任务自然完成。
3. **状态聚合**：
   - 全部服务器 `ok=True` → `deployment.status = "success"`。
   - `fail_fast=True` 且任一失败 → `deployment.status = "failed"`，未启动 wave 标记 `skipped`。
   - `fail_fast=False` → 全跑完后统计：全 OK = success，部分 OK = `partial_failed`（新状态值），全失败 = failed。
4. **日志与 SSE**：
   - [_DeployLogBuffer](app/api/deploy/_shared.py#L1323-L1365) 已是 `threading.RLock()` thread-safe，并发写直接复用。
   - `publish_log` / `publish_status` 在每个并发任务里独立 publish；前端按 `step_name` + `task_id` 分组（taskDetails 已有 server 维度），SSE 不变。
5. **取消传播**：[_shared.py:cancel](app/api/deploy/_shared.py) 已有 cancel flag；并发版每个子任务进入步骤前检查 `task_cancel_requested(...)`，主任务在 cancel 后等所有 in-flight 子任务 `await`（不强制 kill，已开始的 SSH 命令服从远端）。
6. **风险提升**：当 `parallelism > 1` 且 `env == "prod"` 时，[risk_policy.py](app/services/risk_policy.py) 自动把 risk_level 从 `medium` 升到 `high`，要求二次确认（"CONFIRM PARALLEL DEPLOY"）。

**技术细节**：

- 不引入 `asyncssh`；继续 Paramiko + 线程池。FastAPI 的默认线程池上限通常 40，足够本地 10~16 并发场景。
- 每个并发子任务持有独立 SSH connection，不共享 paramiko Transport（[ssh_client.py SSHConnectionPool](ssh_client.py) 已是按 `(host, user)` 缓存，复用安全）。
- 数据库写：SQLite WAL 允许并发读、串行写。`_DeployLogBuffer` batch 0.5s flush 在 16 并发下经验值不会瓶颈。

**风险分析**：

- 风险 1：并发下 SSH 连接池被打满 → 在执行器入口 `min(parallelism, len(servers), 16)` 截断，保留单进程稳定性。
- 风险 2：状态汇总丢失中间事件 → 子任务通过 `DeploymentServerTask.status` 写库，主任务最后只读库聚合，不丢。
- 风险 3：取消时 in-flight SSH 命令不可中断 → 与现有行为一致，仅减小 blast radius；UI 提示"已请求取消，等待 in-flight 任务结束"。

**资源消耗**：内存增量 ≈ `parallelism × (1 SSH conn + 几 KB 日志 buffer)` ≈ < 5MB；CPU 线性增长。

**对现有系统影响**：默认 `parallelism=1` 保持完全相同行为；契约测试不动。

**兼容性**：

- 旧客户端不传 `parallelism` 时按 1 处理；旧 deployments_v2 history 不受影响。
- `deployment.status` 新增枚举值 `partial_failed`；前端历史页需要兼容（在 P0-2 一并改）。

**AI Agent 可执行开发步骤**：

```bash
# 1. schema 加字段（不破坏旧客户端）
#    编辑 app/api/deploy/schemas.py，DeployRequest 增加 parallelism/fail_fast/wave_size

# 2. 拆解执行器
#    编辑 app/api/deploy/_shared.py，把 _run_pipeline_task 的 for 循环体提取为 _run_pipeline_task_one_server

# 3. 并发循环
#    引入 asyncio.Semaphore + asyncio.gather，按 wave 切片调用

# 4. 状态聚合 + partial_failed
#    deployment.status 在 wave 全部完成后由主任务统一写入

# 5. 测试
python -m pytest tests/test_release_reliability_contract.py tests/test_iter36_release_orchestration_contract.py -q

# 6. 新增并发契约测试
#    tests/test_parallel_deploy_contract.py：
#    - 3 台并发 parallelism=3 全成功 → success
#    - 3 台并发 fail_fast=True 中间台失败 → failed + 剩余 skipped
#    - 3 台并发 fail_fast=False 中间台失败 → partial_failed

# 7. 端到端验证
python -c "import main" && echo OK
```

**验收标准**：

- `parallelism=1` 时所有现有契约测试 PASS（行为不变）；
- 新增 `tests/test_parallel_deploy_contract.py` 三个用例 PASS；
- 手动跑 3 台部署 `parallelism=3`，部署总耗时 ≤ 1.5 × 单台耗时；
- 取消并发部署时，UI 显示"等待 in-flight 任务结束"，最终状态可达 `canceled`。

---

### P0-2 并发部署 UI + 计划/预检

**目标**：让前端可设置并发度与失败策略，并在预检中显式回显并发风险。

**实现方案**：

1. **DeployForm**（[frontend/src/pages/deploy/DeployForm.tsx](frontend/src/pages/deploy/DeployForm.tsx)）：
   - 服务器多选下方新增"并发"字段（默认 1，select：1/2/4/8/16，超过服务器数自动 cap）。
   - "失败策略" 单选（fail_fast / continue_on_error，默认 fail_fast）。
   - `parallelism > 1 && env === 'PROD'` 时 UI 顶部出现警告条："并发发布生产环境，已自动升级为高风险确认"。
2. **Plan / Precheck**：
   - [app/api/deploy/precheck.py](app/api/deploy/precheck.py) 在结果里新增 `parallel_summary: { servers, parallelism, expected_waves, fail_fast }`。
   - [RiskConfirmDialog](frontend/src/components/ui) 已存在；details 数组追加"并发配置"行。
3. **DeploymentHistoryTable**：
   - 显示 `partial_failed` 状态（新颜色：橙色）；点击进入详情时按服务器维度展示成功/失败。
4. **DeploymentRunPanel**：
   - [serverExecutionRows](frontend/src/pages/deploy) 已按服务器分组，并发执行时多行同时进入 `running` 状态；只需保证 SSE 事件按 server_name 正确路由（已实现）。

**风险分析**：

- 抽象过早：本任务仅做表单 + 预检文案 + 历史状态颜色，不动 HighRiskFlow（留 P3）。

**资源消耗**：纯前端 + 后端 schema 透传；零额外计算。

**AI Agent 可执行开发步骤**：

```bash
# 1. 前端表单
#    编辑 frontend/src/pages/deploy/DeployForm.tsx，加 parallelism/fail_fast 控件

# 2. precheck 回显
#    编辑 app/api/deploy/precheck.py，append parallel_summary

# 3. 历史表
#    编辑 frontend/src/pages/deploy/DeploymentHistoryTable.tsx，处理 partial_failed

# 4. 前端检查
cd frontend && npm run typecheck && npm run build
```

**验收标准**：

- 选 parallelism=3 + prod 时弹出"并发发布"警告；
- precheck 返回中包含 `parallel_summary`；
- 历史页能区分 success / failed / partial_failed / canceled。

---

### P0-3 列表接口游标 + ETag

**目标**：deploy logs / tool_call_logs / audit / reports 四类高频列表接口支持游标分页与 ETag / 304；前端轮询不再做整段重传。

**变更原因**：[tools.py:335](app/api/tools.py#L335) `list_tokens()`、[tools.py:432,456](app/api/tools.py#L432) ToolPlanEvent 等仍走 `.all()`；[task_center.py:105-109](app/api/task_center.py#L105-L109) `list_audit()` 先 limit fetch 再内存过滤。前端 polling fallback 路径在 SSE 失败时仍发整段 GET，单次 payload 可上百 KB。

**实现方案**：

1. **后端 helper**：[app/api/helpers.py](app/api/helpers.py) 新增 `apply_cursor_pagination(query, since_id, limit, order_by_id_desc=True)`，统一参数：`?last_seq=<id>&limit=<n>`，response header 附弱 ETag `W/"<count>-<max_id>"`。
2. **接入接口**：
   - `GET /api/v2/deploy/deployments/{id}/logs` — deploy logs 增量
   - `GET /api/v2/tools/call_logs` — 工具调用历史
   - `GET /api/v2/audit/*` — 审计记录
   - `GET /api/v2/reports` — 报告列表
   - `GET /api/v2/tools/tokens`（list_tokens）— 改为显式 `limit + last_seq`
3. **前端**：[useDeploymentStream.ts](frontend/src/pages/deploy/useDeploymentStream.ts) polling fallback 携带 `last_seq` + `If-None-Match`，命中 304 时跳过 state 更新。

**验收标准**：

- 4 个契约测试（release_reliability / iter36 / iter38 / iter39）PASS；
- 重复 `last_seq=<prev_max>` 返回 304 或长度为 0 的 logs；
- 前端连续 5 轮空轮询，单次响应字节数 < 200B。

---

### P0-4 工具调用审计写入异步化

**目标**：让 `tools/call` 响应时间不被 `db.commit()` 阻塞。

**变更原因**：[tool_audit.py:57-59,80-82](app/services/tool_audit.py#L57-L59) `record_tool_call()` 与 `record_plan_event()` 在请求路径里同步 `db.commit()`。AI Agent 高频探测 + 调用时是热点。

**实现方案**：

1. 引入 in-process 队列（`queue.Queue(maxsize=2048)`）+ 后台守护线程，专门写 `tool_call_logs` / `tool_plan_events`。
2. 入队失败（背压）则回退同步写并 `logger.warning("audit queue full, sync fallback")`，保证零丢失。
3. lifespan shutdown 阶段 `queue.join()` drain 队列，再关数据库；超时阈值 5s。
4. 既有 `record_tool_call_async()` 已在某些 fail 路径用过；本任务统一收口为一个 writer 线程。

**风险分析**：

- 进程崩溃丢最后几条审计：tool_call_logs 不是不可重建的强一致来源，可接受 drain 失败时退回同步。
- 顺序：单 writer 线程保证 FIFO，audit chain 顺序不乱。

**验收标准**：

- 单次工具调用响应时间下降可测（基线 vs 改造后 1000 次平均 / P99 对比）；
- `tests/test_risk_policy_contract.py` PASS；
- shutdown 时 queue.size 必须为 0（lifespan 日志可见）。

---

### P0-5 Pipeline 步骤可视化编辑 + dovo 步骤配置化

**目标**：解决"流程创建后查看步骤为只读 JSON、无法在 UI 上修改"的实际阻塞；让 dovo 蓝绿发布（binupdate.sh → 第一次日志检查 → portupdate.sh → 第二次日志检查）的各超时 / 等待时间 / 日志检查命令在 UI 上可调。

**变更原因**：

- **前端阻塞**：[frontend/src/pages/PipelinePage.tsx:188-190](frontend/src/pages/PipelinePage.tsx#L188-L190) 把 step.config 渲染成只读 `<pre>{JSON.stringify(...)}</pre>`；用户进入"查看"面板只能看见 JSON，没有表单可改。
- **保存路径不安全**：[PipelinePage.tsx:462-467](frontend/src/pages/PipelinePage.tsx#L462-L467) `handleSave` 在编辑模式下先 `deleteStep` 全部、再逐条 `addStep`，导致：
  1. 每次保存所有 `pipeline_steps.id` 都会变，`deployment_step_tasks` FK 失去意义；
  2. O(N) DELETE + O(N) INSERT，10 步骤 = 20 次 HTTP；
  3. 后端 [deploy_v2.py:375-376](app/api/deploy_v2.py#L375-L376) 已有 `PUT /api/v2/pipelines/{id}/steps/{step_id}`，但前端从未调用。
- **dovo 步骤不可调**：[app/pipeline/steps.py:462-554](app/pipeline/steps.py#L462-L554) `DovoBlueGreenUpdateStep` 已完整实现"识别旧实例 → binupdate → 日志检查 → portupdate → 日志检查"五段流程，但：
  - `detect_cmd`、第一/第二次 `log_cmd`、两次 `asyncio.sleep` 全部 hard-code 在 Python 里；
  - `wait_after_update` 是唯一可配项；超时（180s）、日志检查命令、portupdate 后 5s sleep 都不可调；
  - 没有"日志中必须出现某 pattern 才算成功"的失败判定，第二次日志检查目前只 log 不验证。

**实现方案**：

#### 阶段 A：前端可视化编辑（本任务最小可交付片段） — **2026-05-22 已交付**

1. **替换只读 JSON 视图为结构化表单**：
   - 把 [PipelinePage.tsx](frontend/src/pages/PipelinePage.tsx) 中 `StepExecutionPreview` 改造为 `StepEditor`：按 step_type 渲染表单，每个字段渲染成现有 `StepField` / `BindingField`（变量绑定）；用 `STEP_FIELD_LABELS`（已存在）做 label，用 `STEP_RUNBOOKS`（已存在）做 inline 帮助。
   - 保留底部"高级 / JSON 编辑"折叠区给 power user。
2. **save 路径切到 PUT**：
   - 每个 step 跟踪 `dirty` 状态（初始 false，字段变更 → true）。
   - `handleSave` 编辑模式下：新增的 step 走 `addStep`、已存在且 dirty 的走 `PUT /api/v2/pipelines/{id}/steps/{step_id}`、删除的走 `deleteStep`；不再"全删再全建"。
   - 保留 `reorderStepsApiAfterSave` 用现有 `PUT /api/v2/pipelines/{id}/steps/reorder`（deploy_v2.py:365）做位置变更。
3. **保存反馈**：每个 step 行显示"已保存 / 待保存 / 保存失败"标记；整体 toast 概括"N 步已更新 / M 步新增 / K 步删除"。

> 实际交付的最小集合（2026-05-22 PipelinePage.tsx 编辑后落地）：
>
> - `StepConfig` 新增 `id?` / `originalType?`；`loadPipeline` 捕获服务端 step id 并通过新的 `normalizeConfigForEdit` 还原 `{fields:{...}}` 绑定/字面值，使 `BindingField` 能正确显示已保存的绑定（修复 dovo/scripted/web 三种类型加载后字段空白的潜在问题）；
> - 在 step 渲染块为 `dovo_bluegreen_update` / `scripted_service_update` / `web_script_update` 加入结构化表单，按 `STEP_TYPES.defaults ∪ step.config` 的字段集合生成 `StepField` / `BindingField`，仍保留 `STEP_FIELD_LABELS` / `STEP_RUNBOOKS` inline 帮助；
> - `handleSave` 现走"diff 后 DELETE 仅移除 / PUT 未变 id 的步骤 / POST 新建 / type 变更视为先 DELETE 再 POST / 最终 reorder"，无再"全删全建"；
> - `StepExecutionPreview` 中原始 JSON `<pre>` 收进二级 `<details>`（默认折叠），结构化字段网格成为主要视图；
> - 删除了不再需要的 `reorderStepsApiAfterSave`；`npx tsc --noEmit` / `npm run build` 全部通过，`PipelinePage` chunk 由 27.4 kB → 29.4 kB（gz 8.58 kB）。
> - 第 3 项的"每行 dirty 标记 + 概括 toast"未在本次最小集合内（仍是原有单一 toast）；如有需要在阶段 B 之前补 follow-up。

#### 阶段 B：dovo 步骤配置面 + 日志断言 — **2026-05-22 已交付**

> 实际交付摘要（[app/pipeline/steps.py:462-562](app/pipeline/steps.py#L462-L562)）：
>
> - `DovoBlueGreenUpdateStep` 新支持的 config 字段：`detect_command`、`detect_timeout`、`binupdate_timeout`、`portupdate_timeout`、`wait_after_binupdate`（旧 `wait_after_update` 仍兼容）、`wait_after_portupdate`、`log_check_command`（支持 `${standby_dir}` 占位）、`log_check_timeout`、`log_must_contain`、`log_must_not_contain`。全部 nullable，旧 pipeline 行为不变。
> - 新增模块级 `_assert_log_patterns(output, must_contain, must_not_contain, where)`；在第一次和第二次日志检查后各执行一次，命中缺失/禁止片段直接 `raise RuntimeError(...)` 让 step 失败。
> - 前端 [PipelinePage.tsx](frontend/src/pages/PipelinePage.tsx) 已同步：`STEP_TYPES.dovo_bluegreen_update.defaults` 把新字段加入默认 schema；`STEP_FIELD_LABELS.dovo_bluegreen_update` 加上新 label；`STEP_RUNBOOKS.dovo_bluegreen_update` 描述更新到 6 步可调流程。结构化编辑器（阶段 A）会自动渲染这些新字段。
> - 验证：`python -c "import main"` OK；`python -m pytest tests -q` → 74 PASS / 5 FAIL（5 个失败均为 [test_release_*_contract.py](tests/test_release_reliability_contract.py) 中预先存在的 release-worker 抖动，与 dovo 改动无关，基线照旧）；`npx tsc --noEmit` clean；`npm run build` 通过，PipelinePage chunk 29.4 kB → 30.19 kB (gz 8.93 kB)。

4. **扩展 `dovo_bluegreen_update` 的 config schema**（[app/pipeline/steps.py:462-554](app/pipeline/steps.py#L462-L554)）：

| 新配置字段 | 默认值 | 含义 |
| --- | --- | --- |
| `detect_command` | 现有内联脚本 | 探测 standby 实例的脚本（输出最后一行 = standby instance） |
| `binupdate_script` | `./binupdate.sh` | 已存在（alias `update_script`） |
| `portupdate_script` | `./portupdate.sh` | 已存在（alias `switch_script`） |
| `binupdate_timeout` | `180` | binupdate 单次执行超时 |
| `portupdate_timeout` | `180` | portupdate 单次执行超时 |
| `wait_after_binupdate` | `10` | 已存在（alias `wait_after_update`） |
| `wait_after_portupdate` | `5` | 当前 hard-code 5s |
| `log_check_command` | 当前内联 `log_cmd` | 用户可换成自家 tail 命令 |
| `log_must_contain` | `[]` | string 列表；第二次日志检查后，若 stdout 不包含任一项 → 步骤失败 |
| `log_must_not_contain` | `[]` | string 列表；若 stdout 命中任一项 → 步骤失败 |
| `log_check_timeout` | `60` | 已存在隐式 60s |

5. **执行器加日志断言**：[steps.py:553](app/pipeline/steps.py#L553) `await _run_required(..., "第二次日志检查", ...)` 拿到 stdout 后在 Python 侧做包含 / 不包含检查；命中失败条件 → `raise RuntimeError("Dovo 第二次日志检查失败: 缺少 pattern X")`。
6. **STEP_FIELD_LABELS + STEP_RUNBOOKS 同步加**（[frontend/src/pages/pipeline/stepMeta.ts](frontend/src/pages/pipeline/stepMeta.ts) 或同等文件）：把新字段在 UI 上加 label + runbook 描述。

#### 阶段 C：模板与运行时分离（防止改模板影响在跑的 deploy） — 2026-05-22 已交付

7. **deployment_step_tasks 增加 `captured_config` 列**（[app/db/models.py:247](app/db/models.py#L247)）：可空 TEXT 列存 JSON；迁移条目 `053_013_deployment_step_tasks_captured_config` 已加在 [app/db/migrations/runner.py:75-81](app/db/migrations/runner.py#L75-L81)。
8. **写 snapshot**：[app/pipeline/engine.py:55](app/pipeline/engine.py#L55) 在 step 开始时把 `config` 透传给 step_callback；[_shared.py:_step_callback](app/api/deploy/_shared.py#L1456) 在 `event == "start"` 时 `json.dumps` 后通过 `captured_config=` 传给 `create_step_task`（[repository.py:725](app/db/repository.py#L725)），向后兼容 callback 旧签名（捕获 `TypeError` 回退到 5 参调用）。
9. **执行器读取**：本轮采用最小可用方案——`engine.run(steps=...)` 仍从内存中的 step 列表执行（已是 deploy 触发瞬间快照的 in-memory 副本），因此模板编辑不会影响在跑 deploy；同时把 `captured_config` 写入 step_task 用于审计；后续若执行链路改成从 DB 重读步骤定义，再走 `captured_config or step.config` 兜底。
10. **审计 / UI**：[app/deploy/logs.py:90](app/deploy/logs.py#L90) 已把 `captured_config` 字段加入 `step_tasks` payload，前端可在 deployment 详情页消费做 snapshot vs live diff（前端 UI 留给后续 P1 增量）。

**技术细节**：

- `captured_config` 列是 SQLite TEXT (JSON)；新建索引零；旧 deployments_v2 历史记录的 `captured_config` 为 NULL，执行器在 NULL 时退回到 live 表读，向后兼容。
- 前端 `dirty` 跟踪：用 useRef + Map<step_id, bool>，避免无谓 re-render。
- 阶段 A 即可独立交付并解决用户最痛的"无法修改"；阶段 B 让 dovo 流程在 UI 可调；阶段 C 提升审计可靠性。**三阶段允许分 PR 提交**。

**风险分析**：

- 风险 1：PUT 与 reorder 顺序错乱 → 先 PUT 所有 dirty step（保 id 稳定），再统一 reorder。
- 风险 2：阶段 C 加 `captured_config` 后旧 deploy 历史读 NULL 触发 KeyError → 严格走 `captured_config or step.config` 兜底。
- 风险 3：dovo 日志断言把现有"只看日志不报错"的部署判失败 → 默认 `log_must_contain=[]`、`log_must_not_contain=[]`，保持现有行为；用户主动配置才生效。

**资源消耗**：阶段 A 纯前端，零额外计算；阶段 B 增加少量正则 / substring 检查（< 10ms）；阶段 C 增加每步骤一次 JSON 序列化（< 1ms × N steps）。

**对现有系统影响**：

- 默认 dovo 行为完全不变（新配置字段都有 backward-compat 默认值）。
- 已存在的 pipeline 模板在阶段 A 上线后立刻可改；阶段 C 上线后新触发的 deploy 才走 snapshot，历史不动。

**兼容性**：完全兼容；新增列 nullable，新增字段全部可选。

**AI Agent 可执行开发步骤**：

```bash
# 阶段 A — 前端编辑器（一个 PR）
# 1. 编辑 frontend/src/pages/PipelinePage.tsx：
#    - StepExecutionPreview → StepEditor，按 step_type 渲染表单
#    - handleSave 分 dirty / new / deleted 走 PUT / POST / DELETE
# 2. 检查
cd frontend && npm run typecheck && npm run build

# 阶段 B — dovo 配置 + 日志断言（一个 PR）
# 3. 编辑 app/pipeline/steps.py DovoBlueGreenUpdateStep：
#    - 接受 detect_command / log_check_command / log_must_contain / log_must_not_contain 等字段
#    - 第二次日志检查后做断言
# 4. 编辑 frontend 步骤元数据 / labels 文件：加新字段 label + runbook
# 5. 测试
python -m pytest tests -q
cd frontend && npm run typecheck && npm run build

# 阶段 C — snapshot 隔离（一个 PR，可选）
# 6. 编辑 app/db/models.py DeploymentStepTask：加 captured_config JSON 列
# 7. 编辑 _shared.py _run_pipeline_task_one_server：启动时写 captured_config；执行时读 captured_config or step.config
# 8. 编辑 deployment 详情页：显示 snapshot vs live diff
python -m pytest tests -q
```

**验收标准**：

- 阶段 A：进入已存在的 pipeline 详情，能看到每个 step 的字段表单；改 binupdate_script 字段保存后，`PUT /api/v2/pipelines/{id}/steps/{step_id}` 被命中，step.id 不变；
- 阶段 B：在 dovo 步骤配置面把 `log_must_contain=["server started"]` 设上，远端日志不含该字符串时部署判 failed；
- 阶段 C：编辑模板时正在运行的 deploy 不受影响，详情页能 diff 出新旧配置差异；
- 三阶段都跑：`pytest tests -q` PASS + `cd frontend && npm run typecheck && npm run build` PASS。

---

## 4. P1 任务

### P1-1 MCP capability ETag 双向 + 即时失效

**目标**：让 stdio 桥与 HTTP 客户端在能力未变时不重复传输全量 manifest；策略 / token 变更时立即失效 capability_version。

**变更原因**：[tools.py:182-184,227-229](app/api/tools.py#L182-L184) HTTP 已发 ETag，但 [app/mcp/server.py](app/mcp/server.py) stdio 桥每次 `tools/list` 都全量拉。capability_version 含 60s 客户端缓存窗口（[tool_registry.py](app/services/tool_registry.py)），策略改后短窗内可能仍按旧能力。

**实现方案**：

1. **stdio 桥**：[app/mcp/server.py](app/mcp/server.py) 维护本地 `last_capability_version`；`tools/list` / `prompts/list` / `resources/list` 携带 `If-None-Match`；命中 304 返回本地缓存。
2. **服务端**：[tool_registry.py](app/services/tool_registry.py) 在以下事件主动 bump capability_version：
   - tool_tokens 写入 / 撤销
   - tool_policy 写入
   - 启动期 `ensure_builtin_registered()` 完成
3. **单元测试**：增加 `tests/test_capability_version_contract.py`，验证策略改后立刻新版本号，且空闲时多次拉取返回 304。

**验收标准**：

- 重复 `tools/list` 在 manifest 不变时返回 304 / 小尺寸；
- 写 `tool_token` 后 `/api/v2/capabilities` 立刻返回新 capability_version；
- stdio 桥 `initialize` 时缓存命中下不重新解析 manifest。

---

### P1-2 工具结果流式返回（大 payload）

**目标**：让"日志导出 / DB 大查询 / 诊断包"等大 payload 工具不再以一次性 JSON 字符串返回；改为 `text/event-stream` 分段。

**变更原因**：[tools.py:239](app/api/tools.py#L239) `call_tool` 当前同步返回；大返回值阻塞 worker、占内存。

**实现方案**：

1. 在 [tool_registry.py](app/services/tool_registry.py) 新增 `streamable: bool` tool 元数据；声明可流式的工具：`ops.export_db_query`、`ops.deploy.tail_logs`、`ops.diagnostics.dump`。
2. 新增路由 `POST /api/v2/tools/call/stream` → 走 `StreamingResponse(media_type="text/event-stream")`，事件 `event: chunk` + `event: done` + `event: error`。
3. 非流式工具仍走 `/api/v2/tools/call`，行为不变。
4. stdio 桥：增加 `tools/call.stream`（OPS 私有扩展）—— MCP 标准目前不强制；客户端不支持时走原路径。

**风险分析**：

- 复杂度集中在前端结果展示。本任务仅做后端 + stdio 桥；前端在 P3 接入。

**验收标准**：

- `ops.export_db_query` 输出 5MB CSV 时，服务端内存峰值 < 50MB（vs 当前一次性约 ≥ payload 大小）；
- 现有 `tools/call` 行为不变。

---

### P1-3 骨架屏接入 + xterm 懒加载

**目标**：消除 Dashboard / Deploy / Database / Tools 四页"白屏 → 数据出现"的首屏抖动；ToolAccessPage 不再静态引入 xterm。

**变更原因**：[ui.tsx:156](frontend/src/components/ui.tsx#L156) `Skeleton` 已就绪但未接入；[TerminalTab.tsx](frontend/src/components/server-workbench/TerminalTab.tsx) 静态 import xterm（331KB）。

**实现方案**：

1. 四个 Page 在 `loading && data == null` 状态渲染 `<Skeleton type="card" count={3}/>` 而非 `<Spinner/>`。
2. ToolAccessPage / ServerWorkbench 的 `TerminalTab` 改 `const TerminalTab = React.lazy(() => import('./TerminalTab'))`；外层 `<Suspense fallback={<Skeleton type="block"/>}>` 包裹。
3. 路由层在 `Link hover` 时 prefetch 四个高频页第一屏接口（沿用 `frontend/src/api.ts` TTL cache）。

**验收标准**：

- bundle 分析中 ToolAccessPage 不再静态引用 xterm；
- 切换路由时无 Suspense 警告；
- 四个页面 LCP 主观 < 300ms（本地）。

---

### P1-4 dist 漂移运行时 Banner

**目标**：dist 比 src 旧时 UI 顶部出现"前端构建过期"提示。

**实现方案**：

1. [app/api/system.py](app/api/system.py) 新增 `GET /api/v2/system/frontend-status`，返回 `{ dist_stale: bool, hint: str }`（复用 `build_info.get_frontend_build_check()`）。
2. 前端 [App.tsx](frontend/src/App.tsx) 启动后调用一次，stale 时 mount [DistStaleBanner.tsx](frontend/src/components/DistStaleBanner.tsx)（新组件）；24h dismiss 通过 localStorage 控制。

**验收标准**：

- 改 `frontend/src/App.tsx` 一行后刷新页面，banner 出现；
- 点击关闭按钮后 24h 内不再出现。

---

## 5. P2 任务

### P2-1 SQLite 日志 retention

**目标**：`data/ops.db` 在 90 天活跃使用下不超过 200MB。

**实现方案**：

1. [app/maintenance/service.py](app/maintenance/service.py) 增加 `LogRetentionPolicy`：tool_call_logs 30 天、deploy_logs 90 天 + 状态归档、audit_records 180 天。
2. 维护页"本地资源"面板新增"日志保留策略"卡片（复用 dry-run / preview / 一键确认）。
3. 归档到 `APP_DATA_DIR/archive/<table>-YYYYMM.jsonl.gz`，可回灌。

**验收标准**：

- dry run 给出预计删除条数 + 磁盘节省；
- 一键确认后 SQLite 体积下降；
- `tests/test_iter38_audit_chain_contract.py` PASS。

---

### P2-2 SSE 订阅者 TTL 清理 + 内存上界

**目标**：[app/deploy/stream.py:12](app/deploy/stream.py#L12) `_subscribers` dict 不会因为客户端断连而无界增长。

**实现方案**：

1. 每个 queue 入表时打 `created_at` 时间戳。
2. `publish()` 在 wake up queue 时检查"connected 后 > 30min 仍未被消费 ≥ 0 次"则强制 unsubscribe + close。
3. 全局上界：单 deployment_id 最多 16 订阅者，超出拒绝订阅返回 503，前端按 polling fallback。
4. lifespan shutdown 时遍历清空。

**验收标准**：

- 模拟 100 个断连客户端，30 分钟后 `_subscribers` 长度回到 0；
- 单 deploy 16 客户端并发订阅正常工作，第 17 个收到 503。

---

### P2-3 SQLite 索引补齐

**目标**：补齐 DeployLog `(created_at DESC, id DESC)` 复合索引；为 AuditRecord 增加 `created_at`、`actor`、`tool_name` 索引。

**实现方案**：

1. 在 `app/db/migrations/`（如有）或启动期 `init_db()` 内增量 `CREATE INDEX IF NOT EXISTS`。
2. 不引入新迁移框架；SQLite `CREATE INDEX IF NOT EXISTS` 幂等。
3. 加 retention 配合的覆盖索引：`tool_call_logs(tool_name, created_at)`、`audit_records(actor, created_at)`。

**验收标准**：

- 重启后 `PRAGMA index_list(...)` 可看到新索引；
- 1000 条 audit 数据下 `ORDER BY created_at DESC LIMIT 50` 查询耗时 < 5ms。

---

## 6. 数据库与配置

- SQLite + WAL 不变。本轮新增表零；P0-1 / P0-2 仅给 `deployments_v2.status` 增加合法枚举值 `partial_failed`（字段类型仍是 TEXT，零迁移）。
- 配置不引入新文件结构。

---

## 7. 前端

- 沿用 [frontend/src/components/ui.tsx](frontend/src/components/ui.tsx) 共享原语。
- 新增组件：[DistStaleBanner.tsx](frontend/src/components/DistStaleBanner.tsx)（P1-4）。
- P1-3 阶段把 xterm / TerminalTab 改 lazy。
- HighRiskFlow 组件本轮不做，沿用现有 RiskConfirmDialog。

---

## 8. DevOps 与部署

- 启动方式不变（`start_prod.*` / `start_dev.*` / `start_diag.*`）。
- preflight / build_info 已在上轮加好缓存与 `--deep-scan`，本轮不再改。
- 单进程模式继续是本地生产形态。

---

## 9. 安全设计

- P0-1 并发部署在 prod + parallelism > 1 时强制升级到 high 风险，要求专属 confirm token `CONFIRM PARALLEL DEPLOY`。
- P0-4 异步审计：drain 失败回退同步写，保证零丢失。
- P1-1 capability ETag：策略 / token 变更立即 bump capability_version，避免 stdio 桥 cache 读到旧 scope。

---

## 10. AI Agent 开发执行指引

### 10.1 适用工具

Cursor / Claude Code / Codex / OpenHands / RooCode / Cline / Devin。

### 10.2 执行原则

1. 一个 PR 一个任务；不打包多任务。
2. 改前先跑：
   ```bash
   python -c "import main" && echo IMPORT_OK
   python -m pytest tests -q
   cd frontend && npm run typecheck && npm run build
   ```
3. 改后跑：
   ```bash
   python -m compileall -q app scripts main.py config_manager.py ssh_client.py
   python -c "import main"
   python -m pytest tests -q
   cd frontend && npm run typecheck && npm run build
   node scripts/frontend_syntax_check.js
   node scripts/frontend_route_check.js
   ```
4. 不引入新服务进程、容器、Redis、MQ、外部数据库。
5. 不修改现有 Tool 名称、关键 API 路径、SQLite 字段语义。
6. 文档改动先归档旧文档再写新文档。
7. **本轮一项硬规则**：`parallelism=1` 必须保持与 2026-05-21 行为完全一致，所有现有契约测试 PASS。

### 10.3 Prompt 模板

```
任务：<P?-?> 任务标题
背景：见 docs/plans/2026-06-01-concurrent-deploy-and-mcp-maturity-iteration-plan.md §<节号>
约束：
- 本地单机单进程
- 不引入 Redis / MQ / K8s
- parallelism=1 行为不变
- 跑通 pytest + frontend typecheck + frontend build

输入文件：<列出本任务允许修改的路径>
非修改区：<列出禁止动的文件/模块>

完成定义：
1. 代码符合"验收标准"
2. 全部门禁命令通过
3. PR 描述附完整命令输出
```

---

## 11. 推荐目录结构（仅本轮新增）

```
app/
├── api/
│   └── deploy/
│       └── _shared.py            # P0-1 拆出 _run_pipeline_task_one_server
└── deploy/
    └── stream.py                  # P2-2 _subscribers TTL 清理

frontend/src/
├── components/
│   └── DistStaleBanner.tsx        # P1-4 新增
└── pages/deploy/
    └── DeployForm.tsx             # P0-2 加并发字段

tests/
└── test_parallel_deploy_contract.py   # P0-1 新增并发契约
└── test_capability_version_contract.py # P1-1 新增 ETag 契约
```

---

## 12. 风险与回滚策略

| 风险 | 触发条件 | 监测 | 回滚 |
| --- | --- | --- | --- |
| P0-1 并发下死锁 | 部署卡在 running 不动 | UI / `/api/v2/deploy` 状态超时 | 把 `parallelism` 默认值改回 1 / 紧急时禁用 `/stream` 与并发路径 |
| P0-1 SQLite 写争用 | log 写入延迟 > 1s | `_DeployLogBuffer.flush()` 耗时 | 调小 `parallelism` 上限 |
| P0-3 旧客户端整段重传 | 前端控制台空白 | Network 面板 | `last_seq=0` fallback |
| P0-4 shutdown 丢审计 | lifespan shutdown 日志显示 drain 失败 | drain 失败回退同步 | queue.size > 0 时 sync drain |
| P1-1 capability_version 抖动 | 客户端反复刷 manifest | `/api/v2/capabilities` 命中率 | 短期回到 60s 客户端缓存策略 |
| P1-2 SSE 工具结果断流 | 前端 5s 无 chunk | 前端心跳 | 回到非流式 `tools/call` |
| P2-1 误归档审计 | audit chain 契约失败 | `pytest tests/test_iter38_audit_chain_contract.py` | 从归档 jsonl 回灌 |

---

## 13. 验收门禁（每个 PR 必跑）

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

## 14. 落地后的预期收益

| 指标 | 当前值 | 本轮目标 |
| --- | --- | --- |
| 10 台部署总耗时（parallelism=4） | 10 × 单台 | ≤ 3 × 单台 |
| 工具调用 P99 | 受审计写库影响 | 下降 ≥ 30% |
| 列表接口空轮询单次响应字节 | 数十 KB | < 200B |
| stdio MCP `tools/list` 重复调用 | 全量返回 | 304 / 小尺寸 |
| `ops.export_db_query` 5MB CSV 服务端内存峰值 | 一次性 ≥ payload | < 50MB |
| Dashboard / Deploy / Database / Tools 四页 LCP | > 500ms | ≤ 300ms |
| ToolAccessPage xterm 静态 chunk | 331KB | 0（lazy） |
| `data/ops.db` 90 天后体积 | 无上限 | ≤ 200MB |
| SSE 订阅者堆积（断连 100 客户端 30min 后） | 无界增长 | 回到 0 |

---

## 15. 长期路线图（仅记录，不在本轮执行）

| 时间窗 | 主题 | 关键动作 |
| --- | --- | --- |
| 下一轮（2~4 周） | 异步 SSH 池 | 引入 asyncssh，逐步替换 paramiko 阻塞调用 |
| 再下一轮 | 前端按域代码分割 | 控制单 chunk < 80KB；HighRiskFlow 组件收口 |
| 监控驱动 | 工具调用日志独立 SQLite | 若主库超 500MB |
| 真实需求驱动 | Agent 服务端编排 | 多步 plan / replay / retry |
| 备选 | MCP 动态注册 / 热加载 Tool | 若团队需要按业务上下文切换工具集 |
| 备选 | 部署回滚批量化 | 跟随并发部署，回滚也支持并发 |

---

## 16. 文档维护规则

- 本计划是本轮唯一活动迭代计划。
- 本轮收尾后，本文件迁入 `docs/plans/archive/`，并写下一份新的活动计划。
- 不在 `docs/plans/` 同时保留 2 份以上活动迭代计划。
- 设计参考（`2026-05-01-runtime-source-of-truth.md`、`2026-05-02-ops-remote-workbench-design.md`、`2026-05-03-pipeline-variable-binding-design.md`）保留在根目录，不作为状态追踪文档使用。
- 运维 runbook（`docs/runbooks/`）只在与本计划交付内容直接冲突时同步更新。

---

## 17. 本轮归档的旧计划

- `2026-05-21-response-speed-and-interaction-iteration-plan.md`（响应速度与交互体验迭代）：P0-1 / P0-2 / P0-4 / P1-1 已完成；未完成项已在本计划 §0.4 折叠并继续推进。

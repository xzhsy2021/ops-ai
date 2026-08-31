# 并发问题 · 全量复盘与修改实现情况

> 更新：2026-08-25。适用 OPS 默认单进程部署形态
> （`scripts/start_single_process.ps1`，uvicorn 单 worker，端口 8000）。
>
> 本文是并发专题的总纲：事件循环阻塞（batch 1/2）、SQLite 写锁竞争、以及
> 「工具由串行改真并行」之后引入的并发风险审查结论。
> 阻塞修改的逐项清单已在 [EVENT_LOOP_BLOCKING_FIXES.md](./EVENT_LOOP_BLOCKING_FIXES.md)，
> 本文聚焦并发修改本身带来的问题、排查与结论。

---

## 一、背景：为什么并发是这里的核心风险

OPS 以单进程 uvicorn 承载全部能力：所有 HTTP、SSE、部署 worker、MCP 工具调用
共享一个 asyncio 事件循环和**同一个 SQLite 文件库**。因此并发安全有两层含义：

1. **阻塞层**：任一同步调用压在事件循环上 → 全站 + Agent MCP 一起冻结（灾难级）。
2. **写锁层**：多个 worker/线程并发写同一 SQLite（WAL 单写者）→ 竞争超时
   报 `database is locked`。

本轮修改把工具从「事件循环上隐式串行」改为「线程池真并行」，化解了阻塞，
但也**新引入了真正的数据竞争面**——这是本轮复盘审查的重点。

---

## 二、问题全景与修改实现

### A 类：阻塞主循环 → 全站冻结（架构性）

| 批次 | 修改 | 提交 | 状态 |
|---|---|---|---|
| 第一批 | servers.py 进程动作/执行、发版链路、deploy worker 协程内阻塞 → `run_in_threadpool`/`run_in_executor` | `4c07673`（合并前批次） | ✅ 生效 |
| 第二批 | MCP 边界 `_handle_mcp_http_message`、call_tool_stream、上传/清理/校验/保护/预览/upload_remote、matrix.py 落盘 → `run_in_threadpool` | `4c07673` | ✅ 生效 |

**关键决策**：在 MCP Streamable HTTP 唯一入口统一卸载（`mcp_streamable_http_endpoint`
批量+单个两处 `await run_in_threadpool(...)`），一次性解决全部同步工具，后续新增工具自动受益。
配套 `_run_coroutine_sync` 同步↔异步桥，E2EE 会话按运行 loop 弱引用缓存。

### B 类：SQLite 写锁竞争 → `database is locked`

真实故障：安全日报批量采集默认 16 并发 worker 各自写同一 SQLite，
某台服务器 `DELETE security_risks` 竞争超时报 locked（经 XShell 验证服务器本身无恙）。

| 层 | 修改 | 提交 | 状态 |
|---|---|---|---|
| 进程内写段串行化 | `_PERSIST_LOCK` 串行采集落库段（SSH 读保持并行） | `4cd2e9b` | ✅ 生效 |
| 锁冲突自动重试 | `_run_db_write` 回滚 + 指数退避重试 3 次，兜底跨组件写者 | `4cd2e9b` | ✅ 生效 |
| 锁余量提升 | `busy_timeout` 5000 → 15000ms（`app/db/base.py` PRAGMA） | `4cd2e9b` | ✅ 生效 |
| 锁按次获取（优化） | 重试退避睡眠移到锁外，消除持锁放大尾延迟 | `9e1f61b` | ✅ 生效 |

---

## 三、并发审查：真并行引入的风险面全查

工具改线程池真并行后，逐一核查以下风险点。结论分三档。

### ✅ 已确认安全（附证据）

| # | 风险点 | 结论 |
|---|---|---|
| 1 | DB 会话跨线程使用 | `get_db` 主线程创建 → 线程池内**顺序**使用 → 主线程关闭，符合 SQLAlchemy 会话模型（禁止并发用、允许跨线程顺序用） |
| 2 | MCP 批量消息复用同会话 | `for item in payload` 逐条 `await run_in_threadpool(...)`，批内天然串行 |
| 3 | 采集进度回调竞争 | `_report_progress` 仅在主线程 `as_completed` 循环调用，worker 只返回结果 |
| 4 | 工具层模块级可变状态 | 全扫：仅常量 + 无状态服务（`InventoryReadService` 纯委托无缓存） |
| 5 | Matrix E2EE 会话缓存 | 双锁：`_build_lock`（建会话）+ `_SESSIONS_LOCK`（缓存读写） |
| 6 | 审批短码并发碰撞 | 碰撞无害：入库 hash 含每行独立 salt（`token_hex(16)`），短码重复不破 DB 唯一性；job id 为 uuid4 |
| 7 | 并发同名包上传 | `os.replace` 原子 last-wins |
| 8 | StaticPool 测试夹具 | 仅存在于 tests/，不泄漏到生产 |

### 🔧 审查中发现问题并已修复

| 问题 | 影响 | 修复 |
|---|---|---|
| **重试持锁放大尾延迟** | `_run_db_write` 退避睡眠在持有 `_PERSIST_LOCK` 期间，单个 worker 与外部写者缠斗时（最坏 ~3.5s×N）拖住其余 worker 写段 | 改为锁按次获取：每次尝试独占毫秒级即释放，睡眠在锁外（`9e1f61b`） |

### 📋 观察项（记录在案，当前无需改动）

| 观察点 | 现状评估 | 触发条件 |
|---|---|---|
| anyio 全站共享线程池默认 40 tokens | 当前规模余量充足：MCP 并发调用 <10，最长持有者（matrix pull ~26s、SSH 上限 60s）可容纳；16 路巡检用独立 ThreadPoolExecutor 不占此池 | Agent 并发数显著增长时调 CapacityLimiter |
| `aggregate_and_archive` 归档写无重试 | 单写者时机 + busy_timeout 15s 已覆盖常规竞争 | 出现归档 locked 报错时补同款重试 |
| busy_timeout 提到 15s 的代价 | 极端锁下请求侧写等待变长——系统无长事务设计，实际不可达 | 无 |
| E2EE 会话跨调用重建成本 | 每调用 ~20-26s rebuild（临时 loop）| 长期建议：持久专用 Matrix worker 线程复用会话 |

---

## 四、复盘结论

1. **抓住根因而非表面症状**：B 类故障定位到 OPS 中心库并发写冲突（非服务器问题），
   修在正确层——并发是 SQLite 特性，用「串行化 + 重试 + 余量」三层而非改服务器。
2. **架构优于打点**：阻塞问题一次在 MCP 边界解决全部工具，而非逐个工具打补丁。
3. **真并行需专项审查**：工具改线程并发后，暴露的唯一性能隐患（持锁重试）已在当轮修复；
   数据正确性类风险全部核查为安全。
4. **测试契约随签名演进**：并发卸载/参数化后同步更新契约测试（如 `883499a`），
   保 380~460 相关用例全绿、零契约破坏。

---

## 五、验证记录（2026-08-25 重启后）

- 服务健康：HTTP 200；`PRAGMA journal_mode=wal`、`busy_timeout=15000ms` 生效
- 故障服务器 `47.84.90.139-BI101-pak` 实采（`concurrency=4`）：`status=ok`，
  26.9s 完成，无锁错误
- 相关测试：`tests/test_security_daily_lock_retry.py`（重试语义 3 例）
  + security/daily/inspection/matrix 等 329+ 用例全绿

---

## 七、并行重构协作规范（2026-08-27 补充）

背景：并行 Agent 提交 `4ee754b`「删除服务级 message_routing 并完善执行计划链路」时，
删除了服务级路由端点/提取逻辑，但漏同步 3 个测试文件（`test_task5_final_review.py`、
`test_qclaw_message_execution_plan.py`、`test_task5_quality_review.py`），造成
4 处测试回归（404 vs 401/403、UNMATCHED、引用已删符号）。修复见 `49d5e1d`、`72c054f`、`d08ea12`。

**规范（提交前检查清单，删功能类重构强制）：**

1. **`git grep` 全库检索已删符号**：删除/重命名端点、工具、函数、配置结构后，
   `git grep <已删符号>` 覆盖 `app/`、`tests/`、`docs/`，逐一清理残留引用。
   重点查：测试参数化旧路径、不可达 else/死代码分支、旧字段名断言、docs 描述。
2. **契约测试随重构同步演进**：删除一个功能 ≠ 只删功能代码，关联测试要么删除、
   要么改为断言新契约（如"该字段不再保留"），不允许留下引用已删符号的测试。
3. **提交信息注明重构范围**：`refactor:` 提交写明删除/收敛了什么，方便后人 grep 溯源。
4. **不动他人文件**：并行环境下 `git status` 中他人未提交改动不碰、不提交；
   发现他人改动破坏测试时，只修自己文件或报告，不越权修改他人源码。

---

## 六、相关文档

- [EVENT_LOOP_BLOCKING_FIXES.md](./EVENT_LOOP_BLOCKING_FIXES.md) — 阻塞修改逐项清单
- [matrix-deploy-integration.md](../matrix-deploy-integration.md) — Matrix 拉取/发布集成
- [INSPECTION_TROUBLESHOOTING.md](./INSPECTION_TROUBLESHOOTING.md) — 巡检排障
# 单进程运行 · 事件循环阻塞修复梳理

> 更新：2026-08-22（第二批）。适用 OPS 默认部署形态 `scripts/start_single_process.ps1`
> （uvicorn 单进程单 worker，端口 8000，无 --reload / 无多 worker）。

## ⚠️ 生效状态

第一批修复（发版链路 + servers.py）已随 09:01 启动的进程生效。
**第二批修复（16:36-16:40，见下表）晚于进程启动 → 需重启 OPS 才生效。**

## 二·B、第二批修复：MCP 边界 + 文件/包类端点（2026-08-22 下午）

系统性结论：此前只有 Matrix 工具做了线程桥接，其余所有同步工具
（服务控制、健康检查、巡检、DB 查询导出等）经 MCP Streamable HTTP
入口时都直接跑在主事件循环上——任一慢工具 = 全站冻结。本轮在边界统一卸载：

| # | 文件 / 位置 | 问题 | 修复 |
|---|---|---|---|
| B1 | `app/api/tools.py` `mcp_streamable_http_endpoint`（批量+单个两处） | **MCP 唯一入口**：`_handle_mcp_http_message` 同步跑在循环上，覆盖全部工具调用 | `await run_in_threadpool(...)` 边界卸载——一次修复所有同步工具 |
| B2 | `app/api/tools.py` `call_tool_stream` 非流式分支 | SSE 工具端点对非 streamable 工具同样在循环上 `registry.call` | `run_in_threadpool` |
| B3 | `app/api/tools.py` `upload_package_by_tool_token` | qclaw 包接收入口：磁盘写入+SHA256 在循环上（大包=冻结） | `run_in_threadpool`；409 冲突路径的 `sha256_file` 同样卸载 |
| B4 | `app/api/deploy_v2.py` `upload_file` | 文件中心上传：写盘+哈希在循环上 | `run_in_threadpool` |
| B5 | `app/api/deploy_v2.py` `cleanup_package_api` / `preview_package_cleanup_api` | 清理/预览会 `sync_package_metadata` 遍历磁盘全量包并哈希新文件 | `run_in_threadpool` |
| B6 | `app/api/deploy_v2.py` `upload_remote` | SSH 连接+SFTP 写入在循环上 | 抽同步 helper → `run_in_threadpool` |
| B7 | `app/api/deploy_v2.py` `checksum_file` / `protect_package_api` | `upsert_package_metadata` 未显式传 sha256 时会对整个文件做哈希 | `run_in_threadpool` |
| B8 | `app/api/matrix.py` `matrix_deploy_core` 落盘段 | 该核心被 async 端点直接 await（主循环）也走媒体落盘 | `save_package_fileobj` → `run_in_threadpool` |

### 测试夹具配套修正

线程池卸载后，`sqlite:///:memory:` 测试库默认 SingletonThreadPool
（每线程独立空连接）会报 `no such table`。生产为文件库不受影响；
受影响测试夹具改用 `StaticPool` 共享单连接：
`tests/test_multichannel_package_intake.py`、`tests/test_matrix_tools.py`。


## 一、为什么单进程下这是致命问题

OPS 全部 HTTP 请求、SSE 推送、**部署 worker** 都跑在同一个 asyncio 事件循环上。
循环上任何一次同步阻塞调用（SSH 命令、SFTP 传输、大文件哈希）期间：

- 所有页面/接口无响应（不是变慢，是完全冻结）；
- Agent 的 MCP 调用同样挂起；
- 直到该阻塞调用自然结束才恢复。

历史故障现象：执行发版后整站卡死，发版步骤完成页面才恢复。

## 二、修复清单（全部已落地并通过测试）

| # | 文件 / 位置 | 问题 | 修复 | 验证 |
|---|---|---|---|---|
| 1 | `app/api/deploy/_shared.py` `_run_pipeline_task_one_server` | **主犯**：`distribute_package` 同步执行——整包 SFTP 上传 + 本地/远端 SHA256，直接跑在主循环 | `await loop.run_in_executor(...)` 包裹（与同函数 connect_ssh 处理一致） | 源码守卫 + watchdog 行为测试 |
| 2 | `app/api/deploy/precheck.py` `deploy_precheck` 端点 | async 端点内逐台 SSH 连接 + 多项远程探测（单项超时 10-15s，不可达更久） | 收集器整体 `run_in_threadpool` | 测试套件 |
| 3 | `app/api/servers.py` `server_process_action` | async 端点直连 SSH 执行进程操作（timeout=60s） | SSH 段抽同步 helper → `run_in_threadpool` | 语法 + 回归套件 |
| 4 | `app/api/servers.py` `server_exec` | async 端点远程执行命令（timeout 可至上限） | 同上 | 同上 |
| 5 | `app/api/deploy_v2.py` `browse_remote` | async 端点 SSH 连接 + 远程 ls | 抽 `_browse_remote_sync` → `run_in_threadpool` | 同上 |

### 附带修复的相邻缺陷

| 文件 | 问题 | 修复 |
|---|---|---|
| `app/services/tool_adapters/matrix_tools.py` | MCP 异步端点内同步工具裸调 `asyncio.run` → 协程未执行（-32000） | `_run_coroutine_sync` 桥接：无 loop 直接跑；有 loop 转线程私有 loop |
| `app/services/matrix_e2ee.py` | 会话缓存含可变 user_id 致永远未命中 → 每次解密新建客户端（秒级密钥操作 ×N = 挂死） | 身份比对剔除 user_id；缓存改 WeakKeyDictionary（按 loop 弱引用） |
| `app/services/matrix_e2ee.py` | 每个新会话强制 full_state 全量同步（账号房间多时数十秒） | 仅 crypto store 首次运行做一次 |
| `tests/test_multichannel_package_intake.py` | retry/409 用例依赖其他用例回滚副作用，`-k` 过滤运行即失败 | 用例开始显式清理 DeployPackage 行 |

## 三、确认无阻塞的环节（排查过、无需改动）

| 环节 | 结论 |
|---|---|
| Pipeline 各步骤 SSH | `ctx.ssh_exec / ctx.ssh_upload` 均走 executor |
| 回滚路径 | connect / exec 均已 run_in_executor（rollback.py） |
| SSE 日志流 | `asyncio.wait_for(q.get(), timeout)` 非阻塞 |
| 发布通知 | 仅写 NotificationEvent 记录，无外呼 |
| 入队包校验 | `_package_service_match` 不做大文件哈希 |
| `server_info` / `server_processes` 等 | **同步 def** 端点，FastAPI 自动进 anyio 线程池 |
| Matrix scan/pull API | aiohttp/httpx 异步客户端 |

## 四、Agent（MCP）链路约定

同步工具在异步上下文中被调用是常态（MCP Streamable HTTP / SSE）。规则：

1. **边界卸载已内建**：MCP HTTP 入口与 `/call/stream` 非流式分支统一
   `run_in_threadpool`——新工具处理器默认就在工作线程执行，可直接写
   同步阻塞代码；不要再在处理器里自行开线程；
2. 处理器需要调 async 逻辑时用 `_run_coroutine_sync(coroutine)` 桥接
  （无运行 loop → asyncio.run；有 → 线程私有 loop，finally 关闭会话）；
3. E2EE 会话经 `get_e2ee_session()` 惰性解析（按运行中 loop 缓存，
   线程桥的临时 loop 销毁后自动蒸发）；
4. **async 端点内仍禁止直连 SSH/SFTP/磁盘哈希/全目录扫描** —— 用
   `fastapi.concurrency.run_in_threadpool`（边界只保护工具调用，
   不保护普通 async 端点）。

### 测试注意

凡测试直接构造 `sqlite:///:memory:` 引擎且被测路径含线程池卸载时，
引擎必须加 `poolclass=StaticPool`（否则工作线程拿到空连接）。

## 五、运行中服务核对（2026-08-22 09:26）

- 进程：PID 25432 监听 0.0.0.0:8000（父 30556 由 start_single_process.ps1 启动）
- 启动时间 09:01:41；全部修复文件的最后修改时间 ≤ 08:56
  → **运行中的服务已包含上述所有修复，无需重启**
- 存活探测：`GET /health` → 200；`GET /api/v2/health` → 401（鉴权正常）
- 注意：无 `--reload`，**此后任何代码改动都需要重启进程才生效**

## 六、回归测试锚点

- `tests/test_deploy_loop_offload.py`
  - 源码守卫：`distribute_package` 禁止直接同步调用
  - 行为测试：模拟 0.45s 阻塞分发期间事件循环持续调度（watchdog ≥5 tick）
- `tests/test_matrix_e2ee.py`
  - `test_try_build_decryptor_runs_inside_running_loop`（MCP 循环嵌套回归）
  - `test_scan_media_events_tool_inside_running_loop`
- `tests/test_matrix_tools.py::test_pull_attachment_encrypted_event_via_async_decryptor`
  （拉取必须走异步解密路径）

真实环境验证记录：
- 加密媒体往返自检 SHA256 一致（2026-08-22）；
- Agent 形态探测：scan 5.5s 正常返回 / pull 无新包返回结构化 404（7.3s），全程站点无冻结。

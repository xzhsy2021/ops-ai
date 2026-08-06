# 高风险能力评估清单（MCP / 后端工具）

更新：2026-08-06

> 本文档是**审查产物，不包含代码改动**。逐项评估 OPS 注册表里的高风险/可写
> 工具，标注现有门禁与建议处置（保留 / 收紧 / 考虑禁用），供管理员裁量。
> 配套参考：`docs/runbooks/mcp-capability-matrix.md`（暴露面）、
> `docs/plans/2026-08-06-message-execution-plan-design.md`（方案 2 一次审批边界）。

## 分级统计（`app/services/tool_registry.py` 注册表）

| 风险 | 数量 | 说明 |
|---|---:|---|
| low | 71 | 只读 / 计划预览 / 无副作用 |
| medium | 19 | 读取探针、计划创建、导出、日志搜索 |
| high | 18 | 发布执行、服务控制、远程写、巡检执行、风险写 |
| critical | 1 | `ops.execute_rollback_plan` |

## 现有安全门禁（四层）

1. **Capability 开关**（`tool_policy.py::DEFAULT_CAPABILITY_SETTINGS`）：写类默认关闭，
   需管理员显式开启。默认 `allow_deploy_execute=False`、`allow_rollback=False`、
   `allow_server_write=False`、`allow_package_cleanup=False`、`allow_db_write_tools=False`。
2. **Token 作用域**：工具声明 scopes，token 无 scope 则 403。
3. **风险策略**（`risk_policy.py::enforce_risk_policy`）：`confirm_text` 确认短语 +
   OperationJob 任务化。
4. **人工审批**（`approval_executor.py` / 方案 2 `plan_executor.py`）：高危写操作走
   `ops.approval.prepare_*` / `ops.approval.prepare_plan` 一次性短码审批，审批通过后才执行。

此外，方案 2 对消息级操作额外收敛：
- 一条消息只产生**一个**执行计划、**一次**审批、**一个**短码；
- `approver_matrix_ids` token 级授权人白名单 + MCP 层房间绑定（`mcp_capability_service.py`）；
- 审批后 manifest digest 冻结，计划内容变化必须重新审批。

## 高风险 / 关键工具逐项评估

风险等级取自注册表 `risk` 字段；`write` 表示该工具会改变系统状态。

### 1. 发布执行

| 工具 | 风险 | Scope | 现有门禁 | 建议处置 |
|---|---|---:|---|---|
| `ops.prepare_release_from_local_package` | high | package:write, deploy:plan, deploy:precheck | 能力开关 + confirm_text + 审批 | **保留**：是发布主链路的预览/计划入口 |
| `ops.create_deploy_plan` | medium | ops:read, deploy:plan | 能力开关 | 保留 |
| `ops.run_precheck` | medium | ops:read, deploy:precheck | 能力开关 | 保留 |
| `ops.execute_deploy_plan` | high | deploy:execute | `allow_deploy_execute`（默认 False）+ confirm_text + 审批 | **保留**：必须保持人工审批门禁 |
| `ops.cancel_deployment` | high | deploy:execute | 同上 | 保留 |
| `ops.get_deploy_confirmation` | medium | ops:read, deploy:plan | 只读 | 保留 |

**风险点**：`execute_deploy_plan` 是生产变更的最终触发器。当前依赖能力开关 +
确认短语 + 审批短码三重门禁。**建议**：确保 `allow_deploy_execute` 保持默认关闭，
只在管理员显式开启时放开；不要在 default settings 中打开。

### 2. 回滚（critical）

| 工具 | 风险 | Scope | 现有门禁 | 建议处置 |
|---|---|---:|---|---|
| `ops.create_rollback_plan` | high | ops:read, deploy:plan | 能力开关 | 保留 |
| `ops.execute_rollback_plan` | **critical** | deploy:execute | `allow_rollback`（默认 False）+ confirm_text + 审批 | **保留但强调**：唯一 critical 工具，任何情况下不得移除审批门禁；保持默认关闭 |

**风险点**：回滚直接推翻线上状态。方案 2 已将其纳入计划步骤（`ROLLBACK`），
要求 `deployment_id` 必填、manifest 冻结、一次审批。**建议**：不单独暴露
`ops.execute_rollback_plan` 给 AI/MCP 直接调用；统一走审批计划。

### 3. 服务器 / 远程写

| 工具 | 风险 | Scope | 现有门禁 | 建议处置 |
|---|---|---:|---|---|
| `ops.file_write` | high | ops:read, server:read | `allow_server_write`（默认 False） | **收紧**：默认关闭；仅 admin 显式开启时可用 |
| `ops.exec_remote` | high | ops:read, server:read | `allow_server_write`（默认 False） | **考虑禁用**：任意远程命令执行面最大，风险/收益比最差；建议默认保持关闭并评估是否保留 |
| `ops.restart_service` | high | ops:read, server:read | `allow_server_write` + 审批 | **保留**：服务控制是方案 2 已支持的计划步骤（SERVICE_CONTROL） |
| `ops.start_service` / `ops.stop_service` | high | ops:read, server:read | 同上 | 保留 |
| `ops.update_service_runtime` | high | ops:read, server:read | `allow_server_write` | 保留（更新=拉镜像并重启，语义同 update） |
| `ops.run_health_check` | high | ops:read, server:read | `allow_server_read`（默认 True） | 保留：健康检查实际是读探针，风险字段偏高 |

**风险点**：
- `ops.exec_remote` 无参数校验白名单，任意命令。**建议**：作为首要收紧对象——
  要么保持 `allow_server_write` 关闭并声明为“预留”，要么补命令白名单后再放开。
- `ops.file_write` 写任意路径。与 `exec_remote` 同理，建议默认关闭。

### 4. 巡检执行（高并发写）

| 工具 | 风险 | Scope | 现有门禁 | 建议处置 |
|---|---|---:|---|---|
| `ops.inspection.run_server` | high | ops:read, ops:write | preview-first + `确认巡检 <fingerprint>` 短语 | 保留 |
| `ops.inspection.run_servers_batch` | high | ops:read, ops:write | `preview_servers_batch` + 确认短语 | 保留 |
| `ops.inspection.profile.run` / `retry_issues` | high | ops:read, ops:write | profile preview + `RUN <profile> <count> <fingerprint>` | 保留 |
| `ops.inspection.preview_servers_batch` | low | ops:read | 只读 | 保留（确认短语的唯一来源） |

**风险点**：巡检会批量 SSH 执行只读脚本并写库（inspection_runs/issues/reports）。
虽为只读探针，但目标面大、可打满服务器。**建议**：保持 preview-first 确认短语
门禁不变；如服务器数量大，可限制单批次 size。

### 5. 风险记录写

| 工具 | 风险 | Scope | 现有门禁 | 建议处置 |
|---|---|---:|---|---|
| `ops.risk.ignore` | high | ops:write | `allow_write` | 保留 |
| `ops.risk.update_status` | high | ops:write | `allow_write` | 保留 |
| `ops.risk.verify` | high | ops:write | `allow_write` | 保留 |

**风险点**：误 ignore / verify 会污染风险态势。仅改库状态，不触达服务器。
**建议**：保留，维持 `ops:write` scope 门槛。

### 6. DB 写 / DML

| 工具 | 风险 | Scope | 现有门禁 | 建议处置 |
|---|---|---:|---|---|
| `ops.db.query_readonly` | medium | ops:read | `allow_db_read_tools`（默认 True） | 保留 |
| `ops.db.export_query_result` | medium | ops:read | `allow_db_export_tools`（默认 True） | 保留 |
| `ops.db.get_export` / `list_exports` / `list_tables` / `describe_table` | low | ops:read | 只读 | 保留 |

> 注：DML 写本身不在注册表直接暴露为独立工具；通过 `approval_executor.execute_dml`
> （旧路径）或方案 2 `DML` 计划步骤执行，SQL 走 `DbQueryExportService.execute_sql`
> 预检 + 影响行数上限。`allow_db_write_tools` 默认 False。

**建议**：DML 只允许通过人工审批路径执行（旧审批或方案 2 计划步骤），
不新增直连 AI/MCP 的裸 DML 工具。

### 7. 包清理

| 工具 | 风险 | Scope | 现有门禁 | 建议处置 |
|---|---|---:|---|---|
| `ops.get_package_retention_preview` | medium | ops:read | 只读 | 保留 |
| `ops.upload_package` | high | package:write | `allow_package_write`（默认 False） | 保留（上传本身无破坏性，删除才是） |
| `ops.select_latest_package` | medium | ops:read, deploy:plan | 只读 | 保留 |

> 包清理通过 `approval_executor.execute_package_cleanup`（旧路径）或方案 2
> `PACKAGE_CLEANUP` 计划步骤执行，按保留策略清理，`allow_package_cleanup` 默认 False。

**建议**：包清理只走人工审批路径，保持默认关闭。

### 8. 其他写 / 管理

| 工具 | 风险 | Scope | 现有门禁 | 建议处置 |
|---|---|---:|---|---|
| `ops.upload_package`（见上） | high | package:write | 能力开关 | 保留 |
| `ops.list_backups` / `verify_backup` | low/medium | ops:read | 只读 | 保留 |
| tier / notif_route / cascade 写（`ops.tier.upsert` 等） | high | ops:write, ops:admin | `allow_write` | 保留：调度策略，影响面可控 |

## 结论与建议摘要

1. **保留**（默认关闭 + 人工审批即可）：发布执行、回滚、服务控制、巡检执行、包清理、DML。
2. **优先收紧**（建议保持 `allow_server_write=False` 不动，暂不放开）：
   - `ops.exec_remote` — 命令白名单未落实前，建议维持默认禁用并评估是否移除；
   - `ops.file_write` — 默认禁用；如需启用需限制可写路径。
3. **无需调整**：所有只读 / 预览 / 计划工具。
4. **方案 2 边界**：高危动作（RELEASE/ROLLBACK/DML/PACKAGE_CLEANUP/SERVICE_CONTROL）
   已可作为计划步骤，但**只**在执行器内部调用共享业务函数（`approval_executor.execute_*`），
   `prepare_plan` 的调用方仍只接触 `ops.approval.prepare_plan` / `execute_plan`，
   不直接接触底层危险 scope。
5. **后续可选项**（本次未实施，仅供裁量）：
   - 为 `ops.exec_remote` 增加命令/路径白名单；
   - 为 `ops.file_write` 增加路径约束；
   - 将 `daily_ops` 默认暴露面进一步收窄（例如把 `server_write` 工具移出
     `daily_ops` 之外，仅 `expert`/`admin_full` 可见）。

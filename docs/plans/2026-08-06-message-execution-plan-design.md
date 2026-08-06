# 消息级执行计划与一次审批设计

> **实施状态（2026-08-06）**：方案 2 的后端主链路已落地并通过专项测试。执行器注册 `SERVICE_CONTROL`、`HEALTH_CHECK`、`RELEASE`、`ROLLBACK`、`DML`、`PACKAGE_CLEANUP`；发布、回滚、DML、包清理的执行业务逻辑与旧单动作审批路径共享 `approval_executor.execute_*` 函数。

## 目标

将一条 Element/qclaw 消息触发的完整工单流程收敛为一次人工审批。消息解析、路由、计划生成和风险评估完成后，系统创建一个独立的执行计划；授权人批准该计划后，计划内已声明的步骤按顺序执行，不再为每个步骤重复审批。

## 核心架构

新增两个独立实体：

- `ExecutionPlan`：消息级审批主体、完整计划 manifest、审批生命周期和总体执行状态。
- `ExecutionPlanStep`：计划内有序步骤、依赖、冻结参数、执行状态和结果。

流程：

```text
Element message
  -> resolve_message_target
  -> build complete execution plan
  -> create one pending approval
  -> approver consumes one short code
  -> execute declared steps in order
```

不再把多步骤执行状态塞进 `AiActionApproval.request_payload`。现有 `AiActionApproval` 和 `prepare_*` 接口保留给旧客户端兼容；消息触发主链路迁移到计划模型。

## 计划内容

计划 manifest 必须冻结并参与 `plan_digest` 计算：

- 原始房间、消息 event 和内容摘要。
- 系统、服务、环境和目标服务器。
- 包名、版本和包摘要。
- 有序步骤、步骤类型、步骤参数和依赖关系。
- 路由票据摘要和路由配置 revision。
- 计划风险等级和执行策略。

服务端负责规范化并重新计算 digest，不能信任调用方直接传入的 digest。相同 digest 且仍处于 `PENDING_APPROVAL` 的计划幂等复用，不生成第二个短码。

## 审批与安全边界

一次审批确认完整计划，而不是泛化的“批准操作”。审批消费必须校验：

- 一次性短码和有效期。
- room、原始 request event、内容摘要和路由票据绑定。
- Token/系统/服务授权人白名单。
- 计划仍为待审批状态。
- 存储 manifest 与当前执行 manifest digest 一致。

审批后禁止追加未声明步骤。以下变化必须新建计划并重新审批：目标、环境、服务器、包摘要、步骤类型、步骤顺序、步骤参数、依赖关系或路由配置 revision 变化。

房间绑定、Token 级授权人白名单、系统/服务级 approvers、短码哈希和审批事件绑定全部保留。

## 执行状态

计划状态：

```text
DRAFT -> PENDING_APPROVAL -> APPROVED -> RUNNING -> SUCCEEDED
                                      \-> PARTIAL_FAILED / FAILED
PENDING_APPROVAL -> REJECTED / EXPIRED
```

步骤状态独立记录为 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED` 或 `SKIPPED`。前置步骤失败时，依赖步骤跳过；已成功步骤恢复执行时不得重复产生外部副作用。

计划执行器通过步骤类型注册表调用现有业务执行器，不在计划链路内部再次调用旧的 `prepare_*` 审批工具。

## API

新增 MCP 工具：

- `ops.approval.prepare_plan`
- `ops.approval.execute_plan`

当前没有新增 `ops.approval.get_plan`、`ops.approval.list_plans` 或 `ops.approval.reject_plan` MCP 工具。

管理 API 提供计划列表、详情、拒绝和过期清理：

- `GET /api/v2/execution-plans`
- `GET /api/v2/execution-plans/{plan_id}`
- `POST /api/v2/execution-plans/{plan_id}/reject`
- `POST /api/v2/execution-plans/expire-stale`

详情包含步骤状态和结果，但不暴露短码哈希或授权人白名单。旧 `/api/v2/approvals` 接口继续工作。

## 数据迁移与兼容

新增 `execution_plans`、`execution_plan_steps` 表及 digest、状态、房间和计划关联索引。迁移必须幂等。历史 `AiActionApproval` 不强制迁移，继续走旧执行路径。

## 验收标准

- 一条完整消息只生成一个执行计划、一次计划级审批和一个短码；不额外创建旧 `AiActionApproval` 记录。
- 审批通过后，计划内多个步骤自动顺序执行，不重复触发审批。
- 重复消息或重复 prepare 请求不会生成重复待审批计划。
- 任何实质计划变化都要求重新审批。
- 错误房间、事件、摘要、授权人、短码和过期计划均不能执行。
- 执行中断恢复时不会重复已成功步骤。
- 旧单动作审批接口回归测试保持通过。

## 当前限制

- qclaw 消息路由目前由外部 Agent 通过 MCP 工具编排；仓库内没有独立的 qclaw 消息消费进程。仓库中的端到端契约测试覆盖“路由 → prepare_plan → execute_plan”边界。
- `PlanExecutor` 当前注册 `SERVICE_CONTROL`、`HEALTH_CHECK`、`RELEASE`、`ROLLBACK`、`DML`、`PACKAGE_CLEANUP` 六类步骤。发布/回滚/DML/包清理的执行业务逻辑已从 `ApprovalExecutor` 抽为共享 `execute_*` 函数，旧单动作审批与新计划步骤共用同一实现（`bb4e5d8`）。
- 高危动作作为计划步骤时**只**在执行器内部调用共享业务函数；`prepare_plan` 的调用方仍只接触 `ops.approval.prepare_plan` / `execute_plan`，不直接接触底层危险 scope。
- 方案 2 的专项测试已通过；完整后端测试仍有与本方案无关的既有基线失败，主要来自未提交的前端重构和风险策略测试。

# 多服务一次审批部署：方案（system 带 trace 启动）

> 日期：2026-08-07
> 状态：✅ 已实现（提交记录见 8 节）
> 关联：方案 2 消息级执行计划（`2026-08-06-message-execution-plan-design.md`），
> 本方案在其基础上验证"一次审批收敛多服务部署"并补齐唯一缺口。

## 1. 需求

一条消息：量化测试环境，拉下 system、supplier、transaction、risk、STRATEGY
并部署，按顺序部署；后三个（transaction、risk、STRATEGY）两台都部；system
带 trace 启动，示例命令：

```
SYSTEM_TRACE=true docker compose --env-file .env -f docker-compose.yml up -d --force-recreate system
```

当前外部 Agent 会为每个服务创建独立工单、各自审批。需求：

1. 能否收敛工单创建（不限制工单数）；
2. 能否只审批一次；
3. 输出实现方案。

## 2. 结论

| 需求 | 是否可满足 | 依据 |
|---|---|---|
| 只审批一次 | ✅（已实现） | 方案 2 `ops.approval.prepare_plan` / `execute_plan`：一个计划含多步骤 + 一个短码 + 一次审批 |
| 收敛为单个工单 | ✅ | 把 5 个服务并成同一个计划内的 5 个步骤，逻辑上自然是 1 个工单 |
| 按顺序部署 | ✅（已实现） | 步骤 `dependencies` 链式声明先后；`PlanExecutor` 顺序执行 |
| 部分服务两台 | ✅（已实现） | 每个步骤独立 `targets` |
| system 带 trace（`SYSTEM_TRACE=true` + `--force-recreate`） | ✅（本方案落地） | 命令生成器已支持 `env` 前缀 + `compose_args` 透传（见 4 节） |

**关键点：需求并不需要"放宽工单数限制"，而是同一消息只应有 1 个工单。** 方案 2
已按"一消息一计划"建模，正确用法是让外部 Agent 不再为每个服务调一次
`prepare_release`/`prepare_service_control`，而是组装成一个包含 5 个
SERVICE_CONTROL 步骤的 `prepare_plan`。

## 3. 现状（已核实）

### 3.1 一次审批链路可用

- 注册工具：`ops.approval.prepare_plan`、`ops.approval.execute_plan`（`tool_adapters/approval_tools.py`）。
- 服务：`ExecutionPlanService.prepare/consume`（`execution_plan.py`）；执行器 `PlanExecutor`（`plan_executor.py`）。
- 步骤类型：`SERVICE_CONTROL`、`HEALTH_CHECK`、`RELEASE`、`ROLLBACK`、`DML`、`PACKAGE_CLEANUP`。
- 端到端测试：`tests/test_qclaw_message_execution_plan.py::test_full_message_flow_creates_one_plan_one_approval`
  已断言一个消息只有一个计划、一个 8 位短码、`step_count==2`。

### 3.2 PlanExecutor.SERVICE_CONTROL 步骤
`plan_executor.py::_service_control_handler` 已支持多 target、指定 compose_service，
并按步骤参数分发到 `execute_release` / `_control_single_server`。

### 3.3 命令生成现状（差距所在）
`server_tools.py::_resolve_service_control_command` 对 docker_compose update 生成：

```
docker compose -f <file> pull <svc> && docker compose -f <file> up -d --no-deps <svc>
```

- 无环境变量前缀注入能力（如 `SYSTEM_TRACE=true`）；
- 无 `--force-recreate` / `--env-file` 透传；
- 契约：来自 `compose_file`（默认 docker-compose.yml）、compose 目录来自
  `template_variables.compose_dir/deploy_path/service_dir`。

## 4. 实现方案（改动点）

目标：让下调度 SERVICE_CONTROL 步骤能表达"带前缀环境变量的任意控制命令启动"，
并覆盖 trace 启动。**不改变 PlanExecutor 的步骤模型**，只扩展命令解析器与参数通道。

### 4.1 扩展步骤参数（`action_parameters`）

`ops.approval.prepare_plan` 的 step.parameters 增加两个可选字段（`additionalProperties` 放开）：

```json
{
  "step_key": "deploy-system",
  "action_type": "SERVICE_CONTROL",
  "parameters": {
    "system_name": "quant",
    "service_name": "system",
    "control_action": "update",
    "targets": ["quant-1"],
    "env": {"SYSTEM_TRACE": "true"},
    "compose_args": ["--force-recreate"]
  },
  "dependencies": []
}
```

- `env: dict[str,str]`：渲染为命令前缀 `K1=V1 K2=V2 <command>`（每条 target 相同）；
- `compose_args: list[str]`：追加到 docker compose `up` 子命令，如 `--force-recreate`。

### 4.2 改 `server_tools.py::_resolve_service_control_command`

签名增加 `env: dict | None = None, compose_args: list[str] | None = None`：
- compose `up -d` 分支改为 `docker compose -f <file> up -d <svc> <compose_args.join(' ')>`；
- 新增 `_env_prefix(env)`：非空时渲染为 `K1=V1 K2=V2 ` 前缀（值经 `shlex.quote`），
  同时作用于 `pull` 与 `up` 两段，使 env 对整个 shell 生效；
- `pull` 段不带 `compose_args`，`up` 段追加（`update` 为 `pull && up` 组合）。

### 4.3 透传参数通道
- `plan_executor.py::_service_control_handler`：把 `params.get("env")`/`params.get("compose_args")`
  传给 `executor._control_single_server(...)`（新增两个可选可传递参数）。
- `approval_executor.py::_execute_service_control` / `_control_single_server` 同样透传，
  保证旧单动作审批与新计划步骤共用同一能力 (`execute_*` 共享函数)。

### 4.4（安全）不放开命令注入
只允许通过 `env` / `compose_args` 两个**独立参数**影响命令，不允许把整条任意命令行
作为参数传进来（避免任意命令执行劫持审批）。命令拼接仍由 `_resolve_service_control_command`
受控完成。

## 5. 测试计划（已实现 → `tests/test_service_control_env_passthrough.py`，8 个用例全部通过）

- `_resolve_service_control_command` 单元测试：
  - docker update 叠加 `env={"SYSTEM_TRACE":"true"}` → 前缀同时出现在 `pull` 与 `up` 前；
  - `compose_args=["--force-recreate"]` → `up -d system --force-recreate`；
  - `env` + `compose_args` 同时生效（trace 启动完整形态）；
  - start（`up -d`）同样支持 env/compose_args；
  - env 值经 `shlex.quote`（防御值内空格）；
  - 显式配置的 `update_command` 也支持 env 前缀；
  - 无 env/compose_args 时命令与现有保持一致（回归）。
- `_service_control_handler` 步骤级测试：构造 `parameters` 含 env/compose_args，
  断言传给 `_control_single_server` 的参数正确（`env`、`compose_args`、`compose_service`）。
- 全量回归：494 passed / 6 failed（6 个均为既有基线失败，与本次改动无关）。

## 6. 验收标准

- 一条消息只产生 1 个执行计划、1 个短码、1 次审批，不再为每个服务各建工单。
- 计划内 5 个 SERVICE_CONTROL 步骤按 dependencies 顺序执行；后四个步骤各自 target
  （后三个 target=2 台）。
- system 步骤实际执行命令以 `SYSTEM_TRACE=true` 为前缀、含 `--force-recreate`。
- 旧单动作审批路径行为不变（回归通过）。

## 7. 对外提示（不改代码仍可用）

即便不加代码，外部 Agent **现在**就可以用方案 2 收敛为一次审批——
只要把 5 个服务作为 5 个 SERVICE_CONTROL 步骤传给 `ops.approval.prepare_plan`。
本方案的改动仅为支持 **system 的 trace/--force-recreate 启动方式**。

## 8. 实现记录

- `app/services/tool_adapters/server_tools.py`：
  - 新增 `_env_prefix(env)`（值经 `shlex.quote`）；
  - `_resolve_service_control_command` 签名扩展 `env`/`compose_args`，update/start/restart
    分支透传；`update_command` 显式配置同样支持 env 前缀；
  - `_execute_service_control` 增加可选 `env`/`compose_args` 参数（向后兼容）。
- `app/services/approval_executor.py`：
  - `_execute_service_control` 从 `action_parameters` 读取 `env`/`compose_args`；
  - `_control_single_server` 新增两个可选参数并传给命令生成器。
- `app/services/plan_executor.py`：`_service_control_handler` 从 `step.parameters` 读取
  `env`/`compose_args` 透传给 `_control_single_server`。
- 测试：`tests/test_service_control_env_passthrough.py`（8 用例）。
- 全量回归：494 passed / 6 failed（与改动前基线一致）。
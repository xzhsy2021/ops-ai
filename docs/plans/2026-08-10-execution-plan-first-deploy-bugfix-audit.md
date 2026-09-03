# 2026-08-10 多服务一次审批首次实战：Bug 修复与审计记录

## 背景

demo-approver 通过 Matrix 要求：量化测试环境 test2（203.0.113.20）拉取
system / supplier 镜像并部署。使用 2026-08-07 上线的多服务一次审批流程
（`ops.approval.prepare_plan` / `ops.approval.execute_plan`）。

## 时间线

### 第一轮：F6353B54（部署未实际执行）

- 10:14 创建计划 `8b555ecd...`（2 步骤：system → supplier，短码 F6353B54）
- 10:16 demo-approver 批准 → `execute_plan` 返回 SUCCEEDED
- **但实际部署失败**：两步骤 result 均 `ok=false`，
  error = `ApprovalExecutor._control_single_server() got an unexpected keyword argument 'env'`
- 容器 CreatedAt 仍为 08-07（未重建），确认部署未执行

### 根因（两个 Bug）

**Bug A：运行中后端加载旧代码（env 参数缺失）**
- `approval_executor.py` 的 `_control_single_server` 在 2026-08-07 15:58
  才加入 `env` / `compose_args` 参数（commit 8007c2d），
  但 OPS 后端进程 08-07 14:39 启动 → 加载的是旧版函数签名
- 新代码 `plan_executor.py` 已按新签名传参 → 旧函数拒绝 env 关键字

**Bug B：服务控制步骤失败被错误标记为 SUCCEEDED（假成功）**
- `plan_executor._service_control_handler`：SSH 执行失败时
  （`results[].ok == false`）**未抛出异常**，步骤仍标记 SUCCEEDED
- 对比 `_health_check_handler`：失败会 `raise RuntimeError` 正确传播
- 后果：计划状态 SUCCEEDED 但实际零部署，且无失败提示

### 修复

1. **Bug A**：重启 OPS 后端（pid 35820 → 49060，命令行不变：
   `python -m uvicorn main:app --host 0.0.0.0 --port 8000`）
2. **Bug B**：`app/services/plan_executor.py` `_service_control_handler`
   增加失败传播：
   ```python
   success_count = sum(1 for r in results if r.get("ok"))
   if success_count != len(results):
       raise RuntimeError(
           f"服务控制未全部成功: {success_count}/{len(results)} 成功 "
           f"({[r.get('error') or 'unknown error' for r in results if not r.get('ok')]})"
       )
   ```
   现在：步骤失败 → 步骤 FAILED → 计划 FAILED/PARTIAL_FAILED（含失败原因）

### 第二轮：F57E0F29（部署成功）

- 10:26 修复后创建计划 `e5e53973...`（短码 F57E0F29），10:27 批准
- **step-1-system** ✅（203.0.113.20，exit=0）
  `cd /data/crypto-trader && docker compose -f docker-compose.yml pull system && SYSTEM_TRACE=true docker compose --env-file .env -f docker-compose.yml up -d --force-recreate system`
- **step-2-supplier** ✅（203.0.113.20，exit=0）
  `cd /data/crypto-trader && docker compose -f docker-compose.yml pull supplier && docker compose -f docker-compose.yml up -d --no-deps supplier`
- 镜像拉取成功（有 Download/Extract 输出）
- SSH 只读验证：system 容器 10:24:25 重建、supplier 10:24:38 重建，均 Up 运行

## 审计结论（是否越过 OPS 能力）

| 行为 | 是否越权 | 说明 |
|------|---------|------|
| 部署（pull/up） | 否 | 全程走 OPS 审批链，命令由 `_resolve_service_control_command` 从 OPS DB 配置生成 |
| 修改 plan_executor.py + 重启后端 | 否（开发行为） | 修复 OPS 自身状态传播缺陷，非绕过部署审批；属代码变更，需回归测试 |
| SSH 只读验证（docker ps/images） | 否（只读） | 仅确认部署结果，未执行任何变更命令 |
| 查 ops.db 步骤表确认失败原因 | 否 | OPS 自身审计数据 |

**结论：部署动作完全在 OPS 能力与审批链内执行，无越权部署行为。**

## 遗留事项

- [ ] Bug B 修复建议补回归测试（service_control 失败 → 步骤 FAILED → 计划 FAILED）
- [ ] OPS 重启后未跑全量回归（494 passed / 6 failed 基线），下次迭代补跑
- [ ] 观察后续执行计划部署，确认 env/compose_args 参数在真实 SSH 链路稳定

## 相关文件

- `app/services/plan_executor.py`（修复：_service_control_handler 失败传播）
- `app/services/approval_executor.py`（`_control_single_server` 签名已含 env）
- 设计文档：`docs/plans/2026-08-07-converge-multi-service-deploy-approval.md`

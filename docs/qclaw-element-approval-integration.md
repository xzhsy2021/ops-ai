# qclaw Element Approval OPS 集成文档

> **版本:** 1.0
> **日期:** 2026-07-27
> **设计参考:** [docs/plans/2026-07-22-qclaw-element-approval-ops-design.md](plans/2026-07-22-qclaw-element-approval-ops-design.md)
> **实施计划:** [docs/plans/2026-07-22-qclaw-element-approval-ops-implementation.md](plans/2026-07-22-qclaw-element-approval-ops-implementation.md)

---

## 1. 概述

qclaw 作为 Matrix/Element E2EE 边界和身份证明方，OPS 作为审批策略和执行的拥有者。本文档描述 qclaw 与 OPS 通过 MCP 工具集成的完整流程、调用序列、安全注意事项和运维操作。

**核心原则:**
- qclaw **不直接持有** `deploy:execute`、`package:write`、`package:cleanup`、`db:write` 等危险 scope
- 所有高风险操作必须由 OPS 内部执行器（`ApprovalExecutor`）在审批通过后执行
- 审批码一次性使用，15 分钟过期，绑定房间+事件+内容哈希
- 路由决策由 OPS 做出，qclaw 信任 OPS 的路由结果

---

## 2. 架构与组件

```
┌────────────────┐    MCP    ┌──────────────────────────────┐
│   qclaw        │ ────────▶ │  OPS MCP Tool Registry       │
│  (Element E2EE)│           │  (ops.routing.* /            │
│                │ ◀──────── │   ops.approval.*)            │
└────────────────┘           └──────────────┬───────────────┘
       │                                     │
       │ staging dir                         ▼
       │ (SHA-256 + 大小)         ┌──────────────────────────┐
       └─────────────────────────▶│ package_intake           │
                                 │ ActionApprovalService    │
                                 │ ApprovalExecutor         │
                                 └──────────────────────────┘
                                            │
                                            ▼
                                 ┌──────────────────────────┐
                                 │  OPS 内部执行器          │
                                 │  (deploy/rollback/DML/   │
                                 │   package_cleanup)       │
                                 └──────────────────────────┘
```

### 2.1 核心模块

| 模块 | 路径 | 职责 |
|---|---|---|
| 路由解析 | [app/services/qclaw_routing.py](../app/services/qclaw_routing.py) | 消息→系统/服务，签发 HMAC 路由票据 |
| 审批生命周期 | [app/services/action_approval.py](../app/services/action_approval.py) | prepare / consume / reject / expire |
| 包接收 | [app/services/package_intake.py](../app/services/package_intake.py) | staging→upload，SHA-256 校验，元数据落库 |
| 执行器 | [app/services/approval_executor.py](../app/services/approval_executor.py) | 分发 RELEASE / ROLLBACK / DML / PACKAGE_CLEANUP |
| MCP 工具 | [app/services/tool_adapters/approval_tools.py](../app/services/tool_adapters/approval_tools.py) | 10 个 MCP 工具入口 |
| API 路由 | [app/api/approvals.py](../app/api/approvals.py) | Web UI 管理接口 |
| 数据模型 | [app/db/models.py](../app/db/models.py) `AiActionApproval` | 持久化审批工单 |

---

## 3. 端到端调用序列

### 3.1 正常发布流程

```
Element 用户 → qclaw → OPS → Element 审批 → qclaw → OPS 执行 → qclaw → Element 反馈
```

详细步骤:

#### 步骤 1: qclaw 读取消息并下载附件
1. qclaw 监听受信任的 E2EE 房间
2. 用户发送 `@qclaw-bot 部署 量化 包:release-1.2.3.tar.gz 到 prod`
3. qclaw 解密消息和附件，下载到 staging 目录
4. qclaw 计算 `content_sha256 = SHA256(message_text)`
5. qclaw 计算 `package_sha256 = SHA256(package_file)` 和 `package_size_bytes`

#### 步骤 2: qclaw 调用 OPS 路由解析
```python
# MCP 工具: ops.routing.resolve_message_target
result = await mcp.call("ops.routing.resolve_message_target", {
    "message_text": "@qclaw-bot 部署 量化 包:release-1.2.3.tar.gz 到 prod",
    "room_id": "!room123:matrix.org",
    "event_id": "$evt456:matrix.org",
    "content_sha256": "abc123...def456",
})
# result.outcome = "RESOLVED"
# result.system_name = "crypto-trader"
# result.service_name = null
# result.ticket = "eyJhbGciOi..."
# result.ticket_digest = "sha256-of-ticket"
# result.routing_config_revision = "rev-sha256"
```

若 `outcome` 为 `UNMATCHED` 或 `AMBIGUOUS`，qclaw 在房间提示"无法识别目标系统"并终止。

#### 步骤 3: qclaw 调用包接收（仅 RELEASE 操作）
```python
# 内部 API: app.services.package_intake.intake_package
result = intake_package(
    db=db,
    staging_path="/var/lib/ops/qclaw-staging/release-1.2.3.tar.gz",
    expected_sha256=package_sha256,
    expected_size=package_size_bytes,
    system_name="crypto-trader",
    operator="qclaw",
)
# result.package_id = "uuid"
# result.package_name = "release-1.2.3.tar.gz"
# result.sha256 = package_sha256
```

#### 步骤 4: qclaw 调用 prepare 审批
```python
# MCP 工具: ops.approval.prepare_release
result = await mcp.call("ops.approval.prepare_release", {
    "room_id": "!room123:matrix.org",
    "request_event_id": "$evt456:matrix.org",
    "content_sha256": "abc123...def456",
    "system_name": "crypto-trader",
    "service_name": null,
    "environment": "prod",
    "routing_config_revision": "rev-sha256",
    "routing_ticket_digest": "ticket-digest",
    "targets": ["server1", "server2"],
    "package_name": "release-1.2.3.tar.gz",
    "package_sha256": "pkg-sha256",
    "package_size_bytes": 12345678,
    "action_parameters": {"pipeline": "default"},
    "ai_reason": "用户在 Element 房间请求发布 v1.2.3 到 prod"
})
# result.approval_id = "uuid"
# result.short_code = "A1B2C3D4"  ← 一次性明文短码
# result.expires_at = "2026-07-27T10:15:00"
```

#### 步骤 5: qclaw 在 Element 房间发布审批请求
```
🤖 审批请求 #A1B2C3D4
操作: RELEASE 发布
系统: crypto-trader / 服务: (all)
环境: prod
目标: server1, server2
包: release-1.2.3.tar.gz (12.3 MB, sha256: pkg-sha256...)
理由: 用户在 Element 房间请求发布 v1.2.3 到 prod

授权审批者: @alice:matrix.org, @bob:matrix.org
15 分钟内有效，回复「批准 A1B2C3D4」执行
```

#### 步骤 6: 授权用户在 Element 房间回复
```
@alice:matrix.org: 批准 A1B2C3D4
```

#### 步骤 7: qclaw 验证事件关系并调用 consume
qclaw 必须验证：
- 回复事件的 `m.relates_to` 指向原始请求事件
- 回复者 Matrix User ID 在 OPS 返回的授权列表中
- 短码与 prepare 返回的短码一致

```python
# MCP 工具: ops.approval.consume (内部由 execute 调用)
# 或直接调用 ops.approval.execute 触发完整流程
result = await mcp.call("ops.approval.execute", {
    "approval_id": "uuid",
    "short_code": "A1B2C3D4",
    "approver_matrix_id": "@alice:matrix.org",
    "room_id": "!room123:matrix.org",
    "approval_event_id": "$evt789:matrix.org",
})
# result.status = "SUCCEEDED"
# result.execution_result = {"action": "RELEASE", "deployment_id": "123", ...}
```

#### 步骤 8: qclaw 在房间反馈结果
```
✅ 审批 #A1B2C3D4 已执行成功
部署 ID: 123
状态: 部署 worker 已启动
```

### 3.2 回滚 / DML / 包清理流程

调用 `ops.approval.prepare_rollback` / `prepare_dml` / `prepare_package_cleanup`，参数不同但流程一致。

**ROLLBACK 参数:** `deployment_id`, `targets`
**DML 参数:** `database_connection_id`, `sql_text`, `max_affected_rows`
**PACKAGE_CLEANUP 参数:** `package_ids`

### 3.3 拒绝流程

授权用户回复「拒绝 A1B2C3D4」时，qclaw 调用:
```python
await mcp.call("ops.approval.reject", {
    "approval_id": "uuid",
    "rejecter_matrix_id": "@bob:matrix.org",
})
```
工单进入终态 `REJECTED`，不能再消费。

### 3.4 过期流程

OPS 内部定时任务（每 5 分钟）调用 `ActionApprovalService.expire_stale()`，将过期的 `PENDING_APPROVAL` 工单标记为 `EXPIRED`。qclaw 轮询 `ops.approval.get` 发现状态变更后在房间反馈"审批已过期"。

---

## 4. MCP 工具清单

| 工具名 | Scope | Risk | 用途 |
|---|---|---|---|
| `ops.routing.resolve_message_target` | `ops:read` | low | 路由解析 + 签发票据 |
| `ops.approval.prepare_release` | `ops:read` | low | 准备发布审批 |
| `ops.approval.prepare_rollback` | `ops:read` | low | 准备回滚审批 |
| `ops.approval.prepare_dml` | `ops:read` | low | 准备 DML 审批 |
| `ops.approval.prepare_package_cleanup` | `ops:read` | low | 准备包清理审批 |
| `ops.approval.execute` | `ops:read` | low | 消费短码并执行 |
| `ops.approval.reject` | `ops:read` | low | 拒绝审批 |
| `ops.approval.get` | `ops:read` | low | 查询审批详情 |
| `ops.approval.list` | `ops:read` | low | 查询审批列表 |
| `ops.approval.expire_stale` | `ops:read` | low | 清理过期审批 |

**重要:** 所有 `ops.approval.*` 工具只需要 `ops:read` scope，因为:
1. `prepare_*` 只创建审批工单，不执行实际操作
2. `execute` 内部通过 `ApprovalExecutor` 调度，由 OPS 内部凭据执行，不受调用方 scope 限制

---

## 5. 安全注意事项

### 5.1 qclaw MCP 令牌配置

**禁止**给 qclaw 的 MCP 令牌配置以下 scope:
- `deploy:execute` - 直接部署
- `package:write` - 直接写包
- `package:cleanup` - 直接清包
- `db:write` - 直接 DML
- `*` - 通配符

**推荐**配置:
```json
{
  "name": "qclaw-mcp",
  "scopes": ["ops:read"],
  "allow_write": false,
  "allow_prod": false,
  "expires_in_days": 30
}
```

### 5.2 路由票据绑定

路由票据（`RoutingTicket`）绑定以下字段:
- `room_id` - Matrix 房间 ID
- `event_id` - 请求事件 ID
- `content_sha256` - 消息内容哈希
- `system_name` / `service_name` - 路由目标
- `routing_config_revision` - 路由配置版本
- `issued_at` / `expires_at` - 时间窗口（15 分钟）
- `nonce` - 随机数防重放

票据使用 HMAC-SHA256 签名，签名密钥配置在 `QCLAW_APPROVAL_SIGNING_KEY`。

### 5.3 审批码安全

- 短码格式: 8 字符大写十六进制（32 位熵）
- 存储格式: `pbkdf2_sha256$salt$hash`（100000 轮迭代）
- 明文短码只在 `prepare` 响应中返回一次
- `consume` 使用 `WHERE status='PENDING_APPROVAL' AND consumed_at IS NULL` 保证原子性
- 验证使用 `hmac.compare_digest` 防时序攻击

### 5.4 授权 Matrix 用户策略

存储在 `config_kv.qclaw_approval_policy`:
```json
{
  "authorized_matrix_user_ids": [
    "@alice:matrix.org",
    "@bob:matrix.org"
  ]
}
```

**注意:** 不信任 Matrix display name 或房间 power level，只信任精确 Matrix User ID。

### 5.5 staging 目录安全

- 目录: `QCLAW_STAGING_DIR`（默认 `runtime/qclaw-staging`）
- qclaw 写入后 OPS 验证 SHA-256 和大小才移动到上传目录
- 文件名通过 `safe_package_name` 规范化，防止路径穿越
- 包接收完成后 qclaw 负责 staging 文件清理

### 5.6 Tool Token 房间绑定（MCP 层强制）

每个 Tool Token 可以绑定一组允许的 Element 房间 ID。绑定后，qclaw 只能从
列表内的房间调用 `ops.routing.*` 与 `ops.approval.*` 工具；其他房间的调用会
在 MCP 入口被拒绝（403）。

**Web 入口：** 能力接入 → Tool Token 标签 → 创建/编辑 Token →
「绑定 Element 房间 ID」区域。一个 Token 绑定一个或多个房间 ID（如
`!ops:matrix.org`），留空表示不限制（向后兼容旧 Token）。

**生效工具：**
| 工具 | 校验 room_id 来源 |
|---|---|
| `ops.routing.resolve_message_target` | `args.room_id` |
| `ops.approval.prepare_release` | `args.room_id` |
| `ops.approval.prepare_rollback` | `args.room_id` |
| `ops.approval.prepare_dml` | `args.room_id` |
| `ops.approval.prepare_package_cleanup` | `args.room_id` |
| `ops.approval.execute` | `args.room_id`（execute 来自同一房间） |

**API 字段：**
```http
POST /api/v2/tools/tokens
{
  "name": "qclaw-prod",
  "scopes": ["ops:read"],
  "bound_room_ids": ["!ops:matrix.org"]
}
```

**迁移：** `073_001_tool_token_bound_rooms`（列）+ `073_002_tool_token_bound_rooms_index`（索引）。
空列表 = 无限制（默认），非空 = 严格按列表匹配。

---

## 6. 部署与配置

### 6.1 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `QCLAW_APPROVAL_SIGNING_KEY` | (空，使用 dev fallback) | 路由票据 HMAC 签名密钥，**生产必须配置** |
| `QCLAW_APPROVAL_TTL_SECONDS` | `900` (15 分钟) | 审批码有效期 |
| `QCLAW_STAGING_DIR` | `runtime/qclaw-staging` | qclaw 上传 staging 目录 |

### 6.2 数据库迁移

qclaw 集成依赖 23 个迁移（`072_001` ~ `072_023`），扩展 `ai_action_approvals` 表。迁移幂等，对已有数据库安全。

验证迁移状态:
```powershell
python -c "from app.db.base import engine; from app.db.migrations.runner import run_schema_migrations; run_schema_migrations(engine); print('OK')"
```

### 6.3 配置审批策略

通过 Web UI 或 API 设置授权 Matrix 用户:
```powershell
# 通过 API（需管理员 token）
curl -X PUT http://localhost:8000/api/v2/configs/qclaw_approval_policy `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"value": {"authorized_matrix_user_ids": ["@alice:matrix.org", "@bob:matrix.org"]}}'
```

### 6.4 配置系统消息路由

通过 Web UI（系统编辑页 → 消息路由配置）或 API:
```powershell
curl -X PUT http://localhost:8000/api/v2/approvals/routing/systems/crypto-trader `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"enabled": true, "aliases": ["量化", "量化交易"], "keywords": ["btc strategy"], "priority": 100}'
```

---

## 7. 运维操作

### 7.1 查看审批列表
```
GET /api/v2/approvals?status=PENDING_APPROVAL&limit=50
```

### 7.2 手动拒绝审批
```
POST /api/v2/approvals/{approval_id}/reject
```

### 7.3 清理过期审批
```
POST /api/v2/approvals/expire-stale
```
建议通过 cron 每 5 分钟调用一次。

### 7.4 手动触发执行（调试）
```
POST /api/v2/approvals/{approval_id}/execute
```
**仅用于调试**，正常流程由 `ops.approval.execute` MCP 工具触发。

### 7.5 查看路由配置
```
GET /api/v2/approvals/routing/systems
```

---

## 8. 故障排查

### 8.1 路由解析失败（UNMATCHED）
- 检查系统 `message_routing.enabled = true`
- 检查消息文本是否包含别名或关键词
- 多系统匹配同优先级关键词会返回 `AMBIGUOUS`，需调整 `priority`

### 8.2 审批码验证失败
- 检查短码是否在 15 分钟内提交
- 检查 `room_id` 是否与 prepare 时一致
- 检查工单是否已被消费（`status != PENDING_APPROVAL`）

### 8.3 包接收 SHA-256 不匹配
- 检查 qclaw 下载是否完整
- 检查 staging 文件是否被篡改
- qclaw 应重新下载并重新计算

### 8.4 执行失败
- 查看 `approval.failure_reason` 和 `execution_result.traceback`
- 检查 `OperationJob` 表 `job_type=qclaw_approval_*` 的记录
- 部署失败**不会**自动回滚，需用户决定是否触发 ROLLBACK 审批

---

## 9. 测试

### 9.1 单元测试
```powershell
pytest tests/test_qclaw_routing.py tests/test_qclaw_approval_service.py tests/test_qclaw_approval_executor.py tests/test_qclaw_package_intake.py tests/test_qclaw_approval_migration.py -q
```

### 9.2 端到端契约测试
```powershell
pytest tests/test_qclaw_end_to_end_contract.py -q
```

验证完整流程:
1. 路由解析 → 票据签发
2. prepare → 短码返回
3. consume → 状态 EXECUTING
4. execute → OperationJob 创建 + SUCCEEDED/FAILED
5. 幂等性、过期、拒绝等边界场景

---

## 10. 后续演进

### 10.1 已实现（v1.0）
- 路由解析 + HMAC 票据
- 4 类操作审批（RELEASE/ROLLBACK/DML/PACKAGE_CLEANUP）
- 一次性短码 + 原子消费
- 包接收与 SHA-256 校验
- 内部执行器 + OperationJob 记录
- Web UI 管理 API

### 10.2 待实现
- **前端 UI 控件:** 系统编辑页消息路由配置表单
- **API 契约测试:** `test_qclaw_routing_api.py` / `test_qclaw_approval_api.py`
- **多用户审批策略:** 按操作类型和环境区分授权用户
- **审计联动:** 审批工单与 `audit_chain` 集成
- **回滚自动化:** 部署失败后自动建议回滚审批（仍需人工批准）
- **包保留策略联动:** qclaw 上传的包自动应用保留策略

---

**文档维护者:** OPS 团队
**最后更新:** 2026-07-27

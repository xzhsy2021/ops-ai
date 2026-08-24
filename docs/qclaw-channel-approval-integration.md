# qclaw Channel Approval Integration（通道中立）

> **版本:** 2.0
> **日期:** 2026-08-13
> **设计参考:** [docs/plans/2026-08-11-temporary-self-approval-multichannel-design.md](plans/2026-08-11-temporary-self-approval-multichannel-design.md)
> **实施计划:** [docs/plans/2026-08-11-temporary-self-approval-multichannel-implementation.md](plans/2026-08-11-temporary-self-approval-multichannel-implementation.md)
> **旧版:** [docs/qclaw-element-approval-integration.md](qclaw-element-approval-integration.md)（仅 Element/Matrix 兼容指针）

本文档是 qclaw 与 OPS 通道审批集成的当前唯一权威说明，覆盖 Matrix（Element）、微信（WeChat）、Telegram 三个通道。三个通道共用同一套 OPS 实现：规范化 `message_context`、路由票据、执行计划审批、临时自审批授权与上下文帮助。

---

## 1. 责任边界

- **qclaw 负责**：通道适配器、附件下载、消息解读、外发回复；向 OPS 发送一个规范化的 `message_context` 与本地已下载的附件字节。
- **OPS 负责**：把 `message_context` 绑定进路由票据、文件、审批、授权、计划与审计记录；用数据库策略决定是否放行；校验附件 SHA-256；持久化包元数据；执行冻结后的计划。
- OPS **不持有**任何通道凭据，也不做通道侧下载；附件必须由 qclaw 下载后以 multipart 上传。

```
┌────────────────────┐    message_context + bytes    ┌──────────────────────────┐
│  qclaw (Matrix /   │ ────────────────────────────▶ │  OPS                    │
│  WeChat / Telegram)│                              │  - 路由票据绑定          │
│  通道适配 + 附件下载│                              │  - 审批/授权/计划         │
└────────────────────┘ ◀──────────────────────────── └──────────────────────────┘
         回复（批准 <短码> / 拒绝 / 帮助结果）
```

---

## 2. 规范化 message_context 契约

所有路由 / 审批 / 包上传 / 授权 / 帮助工具的输入 schema 均包含 `message_context`，其结构固定：

```json
{
  "channel": "matrix | wechat | telegram",
  "channel_account_id": "通道账号标识（1-128 位 ASCII slug）",
  "conversation_id": "会话 ID（房间 / 群 / 会话唯一标识）",
  "message_id": "触发消息 ID（全通道唯一）",
  "sender_id": "发送者 ID（通道内稳定身份）",
  "content_sha256": "消息内容的 SHA-256（64 位小写十六进制）"
}
```

稳定身份要求（三通道一致）：

- `channel` 只能取 `matrix` / `wechat` / `telegram` 三者之一。
- `channel_account_id` 是 OPS 中该通道账号的配置名（例如 qclaw 机器人的 `primary`、Element 的 `default`），必须稳定。
- `conversation_id` 是**会话级**稳定标识；同一会话的后续消息必须复用同一个值。
- `message_id` 是**消息级**稳定标识；同一消息重试必须复用同一个值。
- `sender_id` 是发送者在通道内的**固定身份**（例如 Matrix 全限定 ID、微信 openid、Telegram user id）。
- `content_sha256` 用于绑定消息内容，防止跨消息重放。

同一 `message_context` 上**禁止同时出现**通用字段与旧 Matrix 字段（见第 8 节兼容规则）。

`actor_key` 与 `conversation_key` 的规范形式：

```text
actor_key        = <channel>:<channel_account_id>:<sender_id>
conversation_key = <channel>:<channel_account_id>:<conversation_id>
```

---

## 3. 同通道 / 同会话审批规则

审批与临时授权的**消费方必须是授权审批人本人**，并且：

- 通道相同（`channel` 相等）；
- 通道账号相同（`channel_account_id` 相等）；
- 会话相同（`conversation_id` 相等）；
- 请求方==消费方时（自审批路径）必须存在绑定该受益人的**活跃**临时授权，且仍处于有效期。

跨通道、跨账号、跨会话、另一发送者发起的消费一律失败，且**不会**改变原请求的状态（保持 `PENDING_APPROVAL`）。失败尝试写入审计日志，但不消耗审批码。

---

## 4. 临时自审批授权（temporary_access）

### 4.1 适用范围

| 允许 | 禁止 |
|---|---|
| `FILE_UPLOAD` | `DML` |
| `RELEASE` | `ROLLBACK` |
| `SERVICE_CONTROL` | `PACKAGE_DELETE` / `PACKAGE_CLEANUP` |
| `HEALTH_CHECK` | 未知动作 / 生产环境（非 `test`） |

授权只允许绑定**固定受益人**（固定 `actor_key`）、**测试环境**（`SystemEnvironment.category == "test"`）、**固定动作集**与 **15 分钟有效、一次性消费**的确认码。

### 4.2 流程

1. **请求**：受益人（或原始审批人代其发起）调用 `ops.approval.temporary_access`，`operation=request`，携带 `message_context`、`beneficiary_identity`、`system_name`、`environment`、`allowed_actions`、`reason`、时长（默认 1 天，支持 day/week，上限 4 周）。
2. **确认**：原始审批人在同一会话回复确认码（15 分钟内有效，仅能消费一次）。未确认前状态为 `PENDING`。
3. **生效**：确认后状态 `ACTIVE`，受益人在 `expires_at` 前可通过自审批路径消费计划。
4. **撤销**：原始审批人调用 `revoke`（需 `revoke_reason`）；撤销后授权立即失效。
5. **过期**：`get_active_grant()` 把已过 `expires_at` 的授权视作过期，无需后台任务。

重复的活跃授权**不会叠加**：同一作用域同时只允许一个 `ACTIVE` 授权（唯一索引强制）。

### 4.3 管理 UI / API

Web 管理员可在「AI 工具接入 → Tool Token / 临时自审批授权」面板查看列表并按状态过滤，或调用：

```text
GET  /api/v2/temporary-approvals
GET  /api/v2/temporary-approvals/{id}
POST /api/v2/temporary-approvals/{id}/confirm   # 仅管理员
POST /api/v2/temporary-approvals/{id}/revoke    # 仅管理员
```

管理员回退确认时，Web 操作者持久化为 `web:local:<username>` 并作为高权限回退动作审计；确认短语必须包含授权短标识（`id` 前 8 位大写）；任何 API 响应都不返回 `confirmation_code_hash`。

### 4.4 帮助发现

`ops.help.query`（只读）可按 `topic`（如「临时审批」「包上传」「部署」）或 `system_name` / `environment` 返回当前 token/channel 上下文可用的能力；`include_all=true` 时列出不可用能力并标注 `available` / `requires_authorization` / `forbidden`。帮助输出**永不包含** token 值、凭据、私钥、确认码哈希或携带密钥的命令。

---

## 5. 附件转交与包入库

QClaw 下载附件后，通过 `POST /api/v2/tools/packages/upload`（`approval_intake=true`）把本地字节交给 OPS：

- multipart 字段：`file`、`message_context`（JSON）、`package_sha256`、`system`、`service`。
- OPS 校验：token 会话绑定（`conversation_id` 必须在 token `channel_bindings` 内）→ 实际文件 SHA-256 必须等于声明的 `package_sha256` → 落盘到 File Center 并创建 `DeployPackage` 元数据。
- 包元数据记录 `source_context`（规范化后的完整上下文）与 `source_message_key = <channel>:<account>:<conversation>:<message_id>:<package_sha256>`（同一消息可安全携带多个附件，按包哈希区分）。
- **同一消息 + 同一哈希重试**：复用已有包（幂等）；**同一消息 + 不同哈希**：返回 409。
- 后续 `FILE_UPLOAD` 计划步骤引用 File Center 的 `package_name`，与上传/部署步骤保持在**同一个执行计划审批**内。

---

## 6. 消息级执行计划（一次审批）

多步骤消息使用一次 `ops.approval.prepare_plan`（冻结 manifest + 一次性短码）与一次 `ops.approval.execute_plan`（消费短码后按声明顺序执行）：

- 支持的步骤类型：`SERVICE_CONTROL`、`HEALTH_CHECK`、`FILE_UPLOAD`、`RELEASE`、`ROLLBACK`、`DML`、`PACKAGE_CLEANUP`、`MATRIX_PULL`（从 Matrix 房间拉取最新附件入库，参数 `room_id` + `sender` 必填；其结果 `package_name` 自动回填依赖的 `RELEASE` 步骤，「拉包 + 发布」一次审批完成）。
- 计划不可变：任何步骤或包哈希的变更都会使计划摘要失效，受益人无法消费。`MATRIX_PULL` 的 `package_name` 是运行时产物，不参与 manifest 冻结。
- 临时授权生效时：受益人获得自审批路径，计划记录 `temporary_grant_id`；原始审批人仍在授权身份中，但**不作为通知目标**。
- 授权过期/撤销后：受益人不能消费；**原始审批人仍可消费已有计划**。
- 空授权身份集合**fail-closed**：没有任何授权人可以批准写计划。

---

## 7. 示例

### 7.1 请求临时授权（受益人）

```json
{
  "tool": "ops.approval.temporary_access",
  "arguments": {
    "operation": "request",
    "message_context": {
      "channel": "wechat",
      "channel_account_id": "primary",
      "conversation_id": "group-42",
      "message_id": "wx-msg-1001",
      "sender_id": "requester-1",
      "content_sha256": "aaaa...（64 位）"
    },
    "beneficiary_identity": "wechat:primary:requester-1",
    "system_name": "crypto-trader",
    "environment": "test",
    "allowed_actions": ["FILE_UPLOAD", "RELEASE", "SERVICE_CONTROL", "HEALTH_CHECK"],
    "reason": "urgent test integration",
    "duration_value": 1,
    "duration_unit": "day"
  }
}
```

### 7.2 确认（原始审批人，同一会话）

```json
{
  "tool": "ops.approval.temporary_access",
  "arguments": {
    "operation": "confirm",
    "message_context": {
      "channel": "wechat",
      "channel_account_id": "primary",
      "conversation_id": "group-42",
      "message_id": "wx-msg-1002",
      "sender_id": "owner",
      "content_sha256": "aaaa...（64 位）"
    },
    "grant_id": "<grant-id>",
    "short_code": "1F2A3B4C"
  }
}
```

### 7.3 撤销

```json
{
  "tool": "ops.approval.temporary_access",
  "arguments": {
    "operation": "revoke",
    "message_context": { "...同一会话..." },
    "grant_id": "<grant-id>",
    "revoke_reason": "集成窗口已关闭"
  }
}
```

### 7.4 帮助

```json
{
  "tool": "ops.help.query",
  "arguments": {
    "topic": "临时审批",
    "include_all": false,
    "system_name": "crypto-trader",
    "environment": "test",
    "message_context": { "...当前上下文..." }
  }
}
```

---

## 8. 旧 Matrix 字段兼容与弃用

旧 Element 客户端仍可发送 `room_id` / `request_event_id` / `event_id` / `sender_matrix_id` / `content_sha256`，OPS **只在兼容规范化器/端点边界**把这些字段转换成通用 `message_context`：

- 转换规则固定：`channel=matrix`、`channel_account_id=default`、`conversation_id=room_id`、`message_id=<request_event_id|event_id>`、`sender_id=sender_matrix_id`。
- 通用字段与旧字段**不可混用**（`message_context` 与 `room_id` 等同时出现会报 400）。
- 新服务与数据库写入只使用通用模型；旧字段仅作为 API 兼容别名保留，不再作为策略来源。
- 新 schema 不引入任何新的 `matrix_*` 字段。

---

## 9. 禁止动作与 fail-closed 行为

- 空授权审批人集合不能批准写计划；显式配置的授权人列表在**当前通道没有匹配身份**时视为「无授权审批人」，而不是「任何人都可以批准」。
- 临时授权**不能**用于生产、DML、回滚、包删除、包清理或未知动作。
- 临时授权受益人**不能**给自己授予、延长、转移或撤销授权（只能由原始审批人操作）。
- 任何响应（API / 工具 / 帮助）不泄露 token 值、凭据、私钥、确认码哈希或受控根目录外的私有路径。

---

## 10. 测试

```powershell
venv\Scripts\python.exe -m pytest tests\test_multichannel_qclaw_end_to_end.py -q
venv\Scripts\python.exe -m pytest tests\test_multichannel_package_intake.py -q
venv\Scripts\python.exe -m pytest tests\test_temporary_approval_api.py -q
venv\Scripts\python.exe -m pytest tests\test_frontend_temporary_approval_contract.py -q
```

端到端测试对 matrix / wechat / telegram 参数化同一工作流：帮助发现 → 路由票据绑定 → 通道包上传 → 单次执行计划审批（含 `FILE_UPLOAD`）→ 受益人自审批消费 → 有序执行 → 结果检索，并覆盖授权过期回退、跨通道/跨会话拒绝与 schema 契约。

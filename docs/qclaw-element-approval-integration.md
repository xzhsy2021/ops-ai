# qclaw Element Approval OPS 集成文档（兼容指针）

> **版本:** 2.0（已归档为兼容指针）
> **日期:** 2026-08-13
> **当前权威文档:** [docs/qclaw-channel-approval-integration.md](qclaw-channel-approval-integration.md)

本文档仅保留 Element/Matrix 通道的兼容说明。所有新接入（Matrix、微信 WeChat、Telegram）请以
**[qclaw-channel-approval-integration.md](qclaw-channel-approval-integration.md)** 为准，避免维护两份分叉的集成说明。

---

## 状态

OPS 已把审批集成从 Element-only 升级为通道中立（channel-neutral）：

- 所有路由 / 审批 / 包上传 / 授权 / 帮助工具统一接收规范化 `message_context`（`channel` / `channel_account_id` / `conversation_id` / `message_id` / `sender_id` / `content_sha256`）。
- Matrix（Element）客户端仍可发送旧字段 `room_id` / `request_event_id` / `event_id` / `sender_matrix_id` / `content_sha256`；OPS **只在兼容规范化器/端点边界**将其转换为通用 `message_context`（`channel=matrix`、`channel_account_id=default`）。
- 通用字段与旧 Matrix 字段**不可混用**；新 schema 不引入任何新的 `matrix_*` 字段。
- 审批与临时授权的消费规则：同通道、同账号、同会话，消费方必须是授权审批人本人；请求方自审批必须有活跃临时授权。
- 临时自审批授权仅限测试环境，动作限 `FILE_UPLOAD` / `RELEASE` / `SERVICE_CONTROL` / `HEALTH_CHECK`，禁止 `DML` / `ROLLBACK` / `PACKAGE_DELETE` / `PACKAGE_CLEANUP` 与生产环境。

## 历史章节映射

| 旧章节 | 新位置 |
|---|---|
| 1. 概述 / 责任边界 | qclaw-channel-approval-integration.md §1 |
| 2. 架构与组件 | qclaw-channel-approval-integration.md §1、§6 |
| 3. 端到端调用序列 | qclaw-channel-approval-integration.md §5、§6 |
| 4. MCP 工具清单 | qclaw-channel-approval-integration.md §5、§6 |
| 5. 安全注意事项 | qclaw-channel-approval-integration.md §2、§3、§9 |
| 6. 部署与配置 | 保持不变（环境变量 / 迁移 / 审批策略 / 系统路由配置） |
| 7. 运维操作 | 保持不变（审批列表 / 拒绝 / 过期清理 / 路由配置） |
| 8. 故障排查 | 保持不变 |
| 9. 测试 | qclaw-channel-approval-integration.md §10 |
| 10. 后续演进 | qclaw-channel-approval-integration.md §4、§9 |

## 兼容性保证（给存量 Element 客户端）

1. 旧的 `ops.approval.prepare_*` / `ops.approval.execute` 单动作审批仍可用（作为兼容流程），新接入优先使用 `ops.approval.prepare_plan` / `ops.approval.execute_plan`。
2. 旧 Matrix 表单字段（`room_id`、`request_event_id`、`content_sha256`、`sender_matrix_id`）在 API 边界仍被接受并规范化。
3. `enforce_room_binding` 保留为 Matrix/default 兼容包装；核心服务已切换到 `enforce_conversation_binding`。

## 环境变量（保持不变）

| 变量 | 说明 |
|---|---|
| `QCLAW_APPROVAL_SIGNING_KEY` | 路由票据 HMAC 签名密钥，至少 32 字符随机串（如 `secrets.token_urlsafe(48)` 或 `openssl rand -hex 32` 生成）；必须在所有 OPS 进程间一致，泄露即可伪造路由票据 |
| `QCLAW_STAGING_DIR` / `APPROVAL_STAGING_DIR` | qclaw 附件暂存目录 |

签名密钥生成示例：

```python
import secrets
print(secrets.token_urlsafe(48))
```

```bash
openssl rand -hex 32
```

> 注意：生产环境必须显式配置签名密钥，禁止依赖任何开发环境默认值。

## 已实现的能力（2026-08-13）

- 三通道（Matrix / WeChat / Telegram）共用的消息上下文、路由票据、执行计划审批。
- 临时自审批授权（`ops.approval.temporary_access`）+ 管理 API `/api/v2/temporary-approvals` + Web 管理面板。
- 通道附件绑定包入库（`source_context` / `source_message_key`，幂等复用与 409 冲突检测）。
- 上下文帮助 `ops.help.query`（只读，不泄露凭据）。
- 端到端多通道验收测试 `tests/test_multichannel_qclaw_end_to_end.py`。

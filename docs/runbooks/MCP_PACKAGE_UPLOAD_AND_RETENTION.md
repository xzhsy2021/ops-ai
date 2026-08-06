# MCP 上传发布包与发布包清理策略

本迭代补齐了“外部 AI / MCP 客户端上传本地包到 OPS，然后选择服务创建发布计划、预检、执行发布”的闭环，同时新增本地发布包清理策略。

## 1. 能力边界

- OPS 仍不内置大模型，不保存模型厂商 Key。
- MCP / HTTP Tool 只暴露白名单能力。
- `ops.upload_package` 可将发布包上传到 OPS 文件中心。
- `ops.get_package_retention_preview` 只预览清理候选，不删除文件。
- `ops.cleanup_packages` 是高风险写操作，默认不开放，默认 dry-run。
- 清理只处理 OPS 本地文件中心包；不自动清理远程服务器包。

## 2. stdio MCP 上传本地包

stdio MCP 进程运行在用户本机，因此可以读取本地路径：

```json
{
  "tool": "ops_upload_package",
  "arguments": {
    "local_path": "D:\\packages\\system-20263108153148.tar.gz",
    "system": "crypto-trader",
    "service": "crypto-system",
    "overwrite": false
  }
}
```

内部流程：

```text
MCP stdio bridge 读取 local_path
→ multipart 上传到 /api/v2/tools/packages/upload
→ OPS 写入 File Center
→ 计算 SHA256
→ 写入 deploy_packages 元数据
→ 返回 package_name / sha256 / size / service_hint / version_hint
```

Remote HTTP MCP 不能读取用户电脑上的 `D:\...` 路径。如果使用 Remote HTTP MCP，请先通过 OPS 页面上传，或使用 HTTP/base64 工具上传小文件。

## 3. 发布链路

> 本节中的 `ops_upload_package`、`ops_create_deploy_plan` 等名称是旧流程/内部 next-action 别名，不是当前 MCP 注册名。远程 Agent 应使用已注册的 `ops.prepare_release_from_local_package`，或使用 `ops.approval.prepare_plan` 组装支持的消息级计划步骤。

建议 Agent 执行顺序：

```text
ops.prepare_release_from_local_package
→ 查询服务配置（使用 ops.list_services）
→ 预检/确认发布计划
→ 等待用户确认
→ 兼容发布路径：ops.approval.prepare_release → ops.approval.execute
→ 查询发布状态/报告
```

发布计划和执行会记录 `deploy_package_refs`，用于后续清理保护。

## 4. 清理策略

新增默认策略：

```json
{
  "package_keep_days": 90,
  "package_keep_max": 500,
  "test_success_keep_days": 60,
  "prod_success_keep_days": 180,
  "failed_package_keep_days": 180,
  "rollback_package_keep_days": 365,
  "unused_package_keep_days": 30,
  "keep_latest_success_per_service": 3,
  "keep_latest_prod_success_per_service": 5,
  "min_keep_days": 7,
  "protect_running_deployments": true,
  "protect_failed_deployments": true,
  "protect_rollback_candidates": true,
  "keep_metadata_after_file_delete": true,
  "dry_run": true
}
```

## 5. 保护规则

清理时不会删除以下包：

- 正在发布中的包。
- 失败发布保留期内的包，用于重试。
- 回滚候选包。
- 每个 system/service/environment 最近 N 个成功发布包。
- 生产服务最近 N 个成功发布包。
- 手动标记 protected 的包。
- 最短保护期内的新包。

文件可删除，但元数据、SHA256 和发布引用会保留，用于审计追溯。

## 6. 新增接口

```text
GET  /api/v2/files/packages/retention
PUT  /api/v2/files/packages/retention
POST /api/v2/files/packages/cleanup/preview
POST /api/v2/files/packages/cleanup
POST /api/v2/files/packages/{package_name}/protect
POST /api/v2/tools/packages/upload
```

## 7. 新增 MCP / HTTP Tools

```text
ops.upload_package
ops.get_package_retention_preview
ops.cleanup_packages
ops.protect_package
```

需要开启能力开关：

```json
{
  "read_only": false,
  "allow_package_write": true,
  "allow_package_cleanup": true
}
```

同时 Tool Token 需要包含：

```text
package:write
package:cleanup
```

## 8. 页面入口

```text
文件中心 → 发布包
文件中心 → 清理策略
AI 工具 → 能力开关 → allow_package_write / allow_package_cleanup
```

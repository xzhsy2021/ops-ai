# 发布历史与审计日志清理策略

## 目标

发布平台运行后会持续产生发布历史、任务日志、包分发记录、审计日志、AI 工具调用记录和操作计划。清理策略用于控制这些数据的保留周期和最大条数，避免数据库无限增长，同时保留生产、高风险、失败和回滚类记录更长时间。

## 默认策略

| 类型 | 默认保留 |
|---|---:|
| 非生产成功发布 | 90 天 |
| 生产成功发布 | 180 天 |
| 失败发布 | 180 天 |
| 取消发布 | 90 天 |
| 回滚记录 | 365 天 |
| 发布历史最大条数 | 1000 条 |
| 普通审计日志 | 180 天 |
| 高风险审计日志 | 365 天 |
| 审计日志最大条数 | 5000 条 |
| AI 工具调用 | 180 天 / 10000 条 |
| AI 工具计划 | 180 天 / 5000 条 |
| 最短保护期 | 7 天 |

## 高风险审计判定

包含以下关键词的审计动作会使用高风险保留周期：

- deploy
- rollback
- server.exec
- server.terminal
- server.file.delete
- server.file.edit
- sql.query.execute
- key
- token
- tool.
- config.import
- db.restore
- retention

## 清理范围

发布历史清理会级联清理：

- deployments
- deploy_tasks
- deploy_logs
- deployment_server_tasks
- deployment_step_tasks
- deployment_package_distributions
- deployment_records

审计清理会处理：

- audit_logs
- audit_records
- tool_call_logs
- tool_plans
- tool_plan_events

## 安全规则

1. `running`、`pending`、`queued` 发布不会被清理。
2. `min_keep_days` 内的数据不会被清理。
3. 清理前建议先执行 dry-run 预览。
4. 真实清理需要二次确认。
5. 清理动作自身会写入审计：`release.retention.cleanup`。
6. 生产、失败、回滚记录默认保留更久。

## API

```http
GET /api/v2/deploy/retention
PUT /api/v2/deploy/retention
POST /api/v2/deploy/retention/preview
POST /api/v2/deploy/retention/cleanup
```

真实清理：

```json
{
  "dry_run": false
}
```

## 页面入口

- 发布 → 部署历史 → 发布历史 / 审计日志清理策略
- 审计 → 审计日志清理策略

发布页可以配置完整策略，审计页提供审计相关策略和清理入口。

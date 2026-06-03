# Remove Application Layer Design

## Goal

彻底删除“应用”实体与相关 UI/API/数据库依赖，把发布和配置主链路统一为：

`系统 -> 服务 -> 环境 -> 服务器`

同时做字段收缩，不做机械平移。只有运行时真实使用、或操作者必须理解的字段才保留。

## Current Problem

当前项目存在两套并行认知模型：

- UI 有“应用”和“系统”两个配置入口。
- 发布实际执行主链路是“系统 + 服务 + 环境 + 服务器”。
- `Application` 只承担默认变量、默认服务、默认服务器组、Pipeline 绑定和快捷发布入口。
- 发布运行时存在多级 fallback：系统变量 -> 应用字段 / 应用默认变量 -> 服务模板变量 -> 运行时覆盖。

这会带来三个问题：

- 操作者不知道应该在“应用”还是“系统/服务”里改配置。
- 同一类配置可能在多处重复，产生冲突和漂移。
- 删除应用时如果简单迁移字段，会把旧的隐式 fallback 一起保留下来，继续增加复杂度。

## Design Principles

1. 服务是唯一发布主实体。
2. 能删则删，不能解释清楚的默认值不保留。
3. 去掉隐式 fallback，改为少量、稳定、可解释的优先级。
4. UI 只暴露常用字段；高级 JSON 只作为兜底入口。
5. 兼容旧配置可以短期存在，但不再进入主交互。

## Target Model

### System

系统只保留跨服务共享的配置：

- `name`
- `display_name`
- `strategy`
- `environments`
- `groups`
- `variables`
- `base_path` 仅在策略确实需要时保留

系统不再承担默认发布目标，也不再承担单服务默认配置。

### Environment

环境只保留场景定义和跨服务环境变量：

- `name`
- `display_name`
- `category`
- `variables`
- `base_path` 仅在策略需要时保留

环境不再保存目标服务器列表。目标服务器由服务决定。

### Service

服务是唯一部署单元，也是大部分运行字段的归属地。

建议最小字段集合：

- `name`
- `display_name`
- `template`
- `pipeline_id` 可选
- `repo` 可选
- `servers` 可选，作为所有环境一致时的默认目标
- `template_variables`

其中 `template_variables` 在 UI 中应按“服务变量”展示，主要承载：

- `service_dir` 或 `deploy_path`
- `update_script`
- `rollback_script`
- `health_url`
- `health_cmd`
- `process_keyword`
- `service_name`
- `pm2_name`
- `server_keywords`
- `servers_by_env`
- `log_path`
- 其他确实被 Pipeline 或发布逻辑读取的服务级变量

### Group

分组保留，但降级为策略相关能力，不再作为所有系统的默认配置中心。

适用场景：

- Dovo/蓝绿/Region 类发布
- 需要按分组切换实例、根目录、脚本的系统

普通 Direct 系统不应依赖 group 才能完成基础发布。

## Field Decision

### Application Fields To Delete Without Migration

以下字段直接删除，不迁移：

- `Application.id`
- `Application.name`
- `Application.display_name`
- `Application.app_type`
- `Application.description`
- `default_variables.release_service`
- `server_group_id`

原因：

- 服务已成为显式选择项，不再需要“默认发布服务”。
- 默认服务器组会制造额外隐式跳转，应该由 `servers_by_env` / `servers` 或操作时手动选择完成。
- 应用名称、类型、描述不参与部署执行，也不再是交互主实体。

### Application Fields To Delete After Normalization

以下字段不保留原形态，迁移为更具体的服务或系统配置：

- `pipeline_id`
- `repo`
- `deploy_path`
- `health_url`
- `restart_command`
- `build_command`
- `default_variables`

处理规则：

- `pipeline_id` 迁移到服务级 `pipeline_id`。
- `repo` 优先迁移到服务；只有明确是跨服务共享值时，才进入系统 `variables`。
- `deploy_path` / `health_url` / `restart_command` / `build_command` 迁移到服务变量。
- `default_variables` 不整体保留为同名字段，必须拆分到 `system.variables` 或 `service.template_variables`。

### Application Fields To Drop Entirely

- `artifact_type`

原因：

- 当前后端已能根据包名和扩展名推导制品类型，不需要再维护一个独立应用级字段。

## Field Retention Rules

### Keep At System Level

只保留满足“多个服务共用”条件的字段：

- 跨服务共享变量
- 环境定义
- 策略定义
- 分组定义

不满足共享条件的字段一律不能放在系统层。

### Keep At Service Level

凡是会影响单个服务如何发布、发到哪、发完怎么验证的字段，都归服务：

- 发布路径
- 服务目录
- 发布脚本
- 回滚脚本
- 进程标识
- 健康检查
- 服务专属变量
- 环境目标服务器映射
- 默认 Pipeline

### Keep As Advanced Only

以下字段可以保留兼容，但不应继续占据主表单位置：

- 系统 `description`
- 系统原始 `variables` JSON
- 服务原始 `template_variables` JSON
- 分组 `variables`

主交互应该先展示结构化字段，再提供“高级配置”展开区。

## Simplified Precedence

### Variable Precedence

删除应用层后，变量优先级收敛为：

`运行时覆盖 > 服务变量 > 环境变量 > 系统变量`

取消：

- 应用字段 fallback
- 应用默认变量 fallback

### Server Resolution Precedence

目标服务器解析收敛为：

`手动选择 > 服务.servers_by_env > 发布页临时 server_group > Dovo group > 服务.servers > 阻断发布`

取消：

- 应用默认服务器组
- 系统默认服务器作为通用兜底
- 环境级服务器列表作为常规发布目标

如果服务没有可解释的目标服务器，发布页应直接阻断，而不是继续猜测。

## Interaction Changes

### Navigation

删除：

- 左侧导航“应用”
- `/apps`
- `/apps/:id`

保留：

- “系统”
- “发布”
- “流程”
- “服务器”

### System Page

系统页成为唯一配置入口，建议拆成四块：

- 系统概览
- 服务配置
- 环境配置
- 分组配置

不再通过“应用详情”进入服务配置。

### Deploy Page

发布页改为从系统开始：

1. 选择系统
2. 选择服务
3. 选择环境
4. 自动带出服务默认值
5. 必要时人工覆盖服务器或 Pipeline

删除：

- `app_id` 上下文
- 从应用带入的默认服务和默认变量
- “查看/编辑服务”跳回应用页

保留：

- 解析预览
- 预检
- 风险确认
- 历史记录

### Variable Source View

应用变量来源页删除，不做一比一替代页面。

替代方式：

- 发布页保留“解析预览”
- 系统/服务页提供“变量继承预览”

这样更贴近真实执行入口。

## Migration Strategy

### Migration Unit

以“服务”为迁移目标，不以“应用”为保留对象。

### Automatic Mapping

应用到服务的自动映射优先级：

1. `default_variables.release_service`
2. `deploy_path` 与服务目录匹配
3. 同系统仅有一个服务时自动归并

无法确定时停止自动迁移，要求人工确认。

### Variable Merge Rules

同一服务下多个应用的字段归并规则：

- 值完全一致：自动合并
- 一部分为空、一部分有值：取非空值
- 多个不同非空值：标记冲突，人工处理

### Unknown Default Variables

应用中的未知 `default_variables` 处理规则：

- 如果同系统所有应用都一致，迁移到系统 `variables`
- 如果只影响某个服务，迁移到服务变量
- 如果无法判断作用域且当前代码未读取，直接放弃，不做兼容保留

## Out Of Scope

本轮不处理：

- 对 group 模型做彻底重构
- 重命名 `template_variables` 的持久化字段名
- 重新设计 Pipeline 引擎
- 多租户、权限域拆分

## Implementation Notes

落地时应同时删除：

- `Application` ORM / repository
- `/api/v2/apps`
- 导入导出中的 application 段
- Deploy API 中 `app_id` 相关逻辑
- 前端 `ApplicationListPage` / `ApplicationDetailPage`
- 导航、路由、查询、SQL 示例中的 application 引用

## Approval Checkpoint

如果这版设计通过，下一步按实施计划执行：

- 先做后端字段与 fallback 清理
- 再做前端入口合并
- 最后做迁移、验证、文档更新

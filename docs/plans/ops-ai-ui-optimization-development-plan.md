# ops-ai UI 优化开发文档

> 目标：在不做大规模架构重写、不引入重型 UI 框架的前提下，围绕“响应速度优先、交互流程清晰、资源占用可控、AI Agent/MCP 可观测可控”对前端 UI 进行渐进式优化。

## 1. 项目背景与约束

### 1.1 项目背景

- 项目类型：本地部署的小团队内部运维平台。
- 使用场景：系统/服务/服务器管理、发布部署、任务中心、日志查看、数据库工具、维护备份、AI Agent / MCP 工具接入。
- 前端技术栈：React 18、Vite 5、TypeScript、React Router、Zustand、Axios、lucide-react、xterm。
- 后端交互：REST API、发布日志轮询 / SSE 相关逻辑、MCP / HTTP Tool 接入能力。

### 1.2 当前 UI 现状简述

当前前端已经具备较好的基础能力：

- 路由级 `lazy` 加载。
- 全局命令面板。
- 深色 / 浅色主题。
- `Skeleton`、`ErrorState`、`EmptyState`、`DataTable`、`FilterBar`、`RiskConfirmDialog`、`LogViewer` 等基础组件。
- `useSmartPolling` 已具备可见性检测、失败退避和单请求在途控制。
- 发布页已经拆出多个子模块，例如 `DeployForm`、`DeploymentRunPanel`、`PreflightPanel`、`ReleasePlanPanel`、`DeploymentHistoryTable` 等。
- `ToolAccessPage` 已包含 MCP / HTTP Tool 接入、token、工具目录、调试、审计与计划等功能。

但也存在明显的优化空间：

- 若干页面过大，维护和渲染压力较高，例如：
  - `ServerListPage.tsx`，约 1326 行。
  - `PipelinePage.tsx`，约 1291 行。
  - `DatabaseToolsPage.tsx`，约 1013 行。
  - `ToolAccessPage.tsx`，约 671 行。
  - `SystemListPage.tsx`，约 620 行。
- 发布流程虽然功能完整，但仍偏“表单 + 面板堆叠”，需要进一步流程化。
- 日志组件已有筛选和复制能力，但还缺少自动滚动控制、服务端游标体验、分组过滤、虚拟渲染和大日志降载。
- MCP 工具页功能密集，信息架构需要围绕“能力、风险、审计、调试”重新组织。
- 表格能力分散，分页、筛选、排序、批量操作、空状态、错误状态等需要统一。
- 部分视觉效果适合展示型后台，但在低配机器、远程桌面、长日志场景下可能增加资源占用。

## 2. UI 优化目标

### 2.1 产品目标

1. 让用户更快找到要做的操作。
2. 让发布、回滚、SQL、MCP 写操作等高风险流程更安全。
3. 让日志、任务、审计、报告更可观察、可追踪。
4. 让 AI Agent / MCP 能力从“黑盒工具”变成“可查看、可调试、可审计的能力中心”。
5. 让小团队内部使用时减少重复输入、减少误操作、减少等待焦虑。

### 2.2 性能目标

| 指标 | 当前问题 | 目标 |
|---|---|---|
| 首屏体感 | 部分页面模块较重 | 登录后 1 秒内出现可操作骨架或缓存数据 |
| 页面切换 | 大页面加载后等待明显 | 路由切换尽量无白屏，保留旧数据并后台刷新 |
| 长日志渲染 | 大量 DOM 行可能卡顿 | 前端默认仅渲染最近 500～1000 行，可查看完整日志 |
| 表格渲染 | 大列表页面一次性渲染压力大 | 超过 200 行分页或虚拟滚动 |
| 终端加载 | xterm 包体和实例较重 | 仅进入终端 Tab 时加载和初始化 |
| 轮询资源 | 多页面轮询可能重复 | 单页面一类资源一条轮询，隐藏标签页降频或暂停 |

### 2.3 安全与可控目标

- 所有高风险操作统一走风险确认体验。
- 所有 MCP 写操作可以看到：工具名、风险等级、参数、触发者、耗时、结果、审计记录。
- 高风险操作尽量支持 dry run / 预检 / 计划预览。
- 审计和报告可以从执行界面一键跳转。

## 3. 设计原则

1. **先提升流程效率，再做视觉美化。**
2. **优先复用现有组件，不引入重型 UI 框架。**
3. **列表、日志、终端是性能重点。**
4. **高风险操作必须有统一交互，不允许页面各自实现确认逻辑。**
5. **AI Agent 操作必须可解释、可复查、可追踪。**
6. **本地小团队场景优先，避免为大规模多租户复杂化。**
7. **允许渐进式改造，每个阶段都要能独立上线。**

## 4. 总体信息架构优化

建议保持当前主导航结构，但调整各入口的职责：

```text
工作台
├─ 今日健康
├─ 正在运行任务
├─ 最近失败任务
├─ 待确认/待处理操作
├─ 最近 MCP 高风险调用
└─ 常用快捷操作

发布
├─ 发布向导
├─ 预检结果
├─ 执行控制台
├─ 发布历史
└─ 回滚/报告

任务
├─ 运行中
├─ 最近失败
├─ 历史任务
└─ 任务详情

服务器
├─ 服务器资产
├─ 分组视图
├─ 服务器详情
├─ 文件
└─ 终端

数据库
├─ 连接
├─ SQL 查询
├─ 表结构
├─ 导出
└─ 清理任务

AI 工具
├─ 能力概览
├─ 工具目录
├─ MCP / HTTP 接入
├─ Tool Token
├─ 调试 Playground
├─ 风险策略
└─ 审计时间线
```

## 5. 重点页面优化方案

## 5.1 工作台 Dashboard 优化

### 当前定位

当前工作台更偏概览页。建议改成“今日运维驾驶舱”，优先回答：

- 当前系统是否健康？
- 有没有正在运行或失败的任务？
- 有没有需要人工处理的发布、回滚、清理、MCP 操作？
- 有没有高风险操作刚刚发生？
- 用户下一步最可能做什么？

### 改造内容

#### 5.1.1 新增顶部健康摘要

展示：

- 后端连接状态。
- Worker 状态。
- 发布任务运行数。
- 最近失败任务数。
- MCP 高风险调用数。
- 本地磁盘/备份/数据库健康提醒。

#### 5.1.2 新增“待处理事项”区

待处理事项包括：

- 待审批发布。
- 待确认高风险 MCP 工具调用。
- 失败发布待查看报告。
- 清理任务失败。
- 备份异常。

#### 5.1.3 新增“快捷操作”区

建议固定展示：

- 发起发布。
- 查看任务中心。
- 查看服务器。
- 打开 AI 工具中心。
- 打开数据库工具。
- 运行系统诊断。

### 验收标准

- 进入工作台 1 秒内能看到健康状态或骨架屏。
- 至少 5 个常用入口可一键到达。
- 失败任务、运行任务、高风险 MCP 调用有明确入口。
- 后端离线时展示明确提示，不出现空白页。

---

## 5.2 发布页 Deploy 优化

### 当前问题

发布页已经拆分出多个组件，但用户操作路径仍然偏复杂。对于内部运维平台，发布页应该从“配置表单”升级成“任务向导 + 实时控制台”。

### 目标流程

```text
选择目标 → 选择发布包与流程 → 选择服务器 → 预检 → 风险确认 → 执行发布 → 查看日志/报告/回滚
```

### 改造内容

#### 5.2.1 发布页改成 StepWizard

新增组件：

```text
frontend/src/components/StepWizard.tsx
frontend/src/pages/deploy/DeployTargetStep.tsx
frontend/src/pages/deploy/DeployPackageStep.tsx
frontend/src/pages/deploy/DeployServerStep.tsx
frontend/src/pages/deploy/DeployPrecheckStep.tsx
frontend/src/pages/deploy/DeployConfirmStep.tsx
frontend/src/pages/deploy/DeployRunStep.tsx
```

每一步只展示当前必需信息，高级选项折叠。

#### 5.2.2 新增发布摘要卡片

在预检和确认前展示：

- 系统。
- 服务。
- 环境。
- 发布包。
- Pipeline。
- 服务器数量。
- 并发数。
- fail fast 策略。
- 是否生产环境。
- 风险等级。

组件建议：

```text
frontend/src/pages/deploy/DeploySummaryCard.tsx
```

#### 5.2.3 预检结果分级展示

将预检结果分成：

- 阻断项：必须修复。
- 警告项：允许继续但需确认。
- 建议项：可优化。
- 通过项：展示简要成功状态。

组件建议：

```text
frontend/src/pages/deploy/PrecheckResultBoard.tsx
```

#### 5.2.4 执行控制台固定核心信息

发布执行时顶部固定展示：

- 当前状态。
- 当前步骤。
- 成功服务器数。
- 失败服务器数。
- 已耗时。
- 取消发布。
- 查看报告。
- 查看审计。

组件建议：

```text
frontend/src/pages/deploy/DeploymentRunHeader.tsx
frontend/src/pages/deploy/DeploymentServerGrid.tsx
```

#### 5.2.5 发布历史支持快速复用

发布历史表增加操作：

- 复用本次配置。
- 查看报告。
- 查看日志。
- 查看回滚计划。
- 复制 Markdown 报告。

### 验收标准

- 用户从进入发布页到发起预检，常见场景不超过 4 次主要点击。
- 发布执行中不需要切换页面即可看到状态、日志、失败服务器、报告入口。
- 生产环境发布必须显示风险摘要。
- 失败发布必须有“查看失败归因”和“下一步建议”。

---

## 5.3 日志控制台 LogConsole 优化

### 当前基础

当前 `LogViewer` 已支持：

- 搜索日志。
- 按 level 筛选。
- 复制错误上下文。
- 手动刷新。

### 需要增强的能力

新增统一组件：

```text
frontend/src/components/LogConsole.tsx
```

建议逐步替换或增强现有 `LogViewer`。

### 功能设计

#### 5.3.1 基础能力

- 自动滚动开关。
- 暂停自动刷新。
- 只看错误。
- 按服务器筛选。
- 按步骤筛选。
- 按关键词搜索。
- 复制全部可见日志。
- 复制错误上下文。
- 下载完整日志。
- 跳转到第一条错误。

#### 5.3.2 性能控制

- 默认只保留最近 1000 行。
- 超过上限显示提示：`已隐藏较早日志，可下载完整日志`。
- 只渲染可见区域，或至少分片渲染。
- 搜索输入 debounce 200～300ms。
- 日志行组件 memo 化。

#### 5.3.3 数据字段标准

建议统一日志字段：

```ts
export type LogConsoleEntry = {
  id?: string
  time?: string
  created_at?: string
  level?: 'debug' | 'info' | 'warning' | 'error' | 'success' | string
  server_name?: string
  step_name?: string
  message?: string
  raw?: unknown
}
```

#### 5.3.4 Props 设计

```ts
export type LogConsoleProps = {
  entries: LogConsoleEntry[]
  title?: string
  maxRows?: number
  maxHeight?: number
  loading?: boolean
  hasMoreBefore?: boolean
  autoScrollDefault?: boolean
  onRefresh?: () => void
  onLoadMoreBefore?: () => void
  onDownload?: () => void
}
```

### 验收标准

- 5000 行日志输入时页面仍可操作。
- 日志搜索、筛选不会明显卡顿。
- 自动滚动可以关闭，用户查看历史日志时不会被强制滚到底部。
- 发布失败时可以一键复制错误上下文。

---

## 5.4 AI 工具 / MCP 能力中心优化

### 当前问题

`ToolAccessPage` 功能完整但信息密度偏高。建议从“工具接入页面”升级为“AI Agent 能力中心”。

### 新页面结构

```text
AI 工具中心
├─ 能力概览
├─ 工具目录
├─ 风险策略
├─ Token 管理
├─ MCP / HTTP 接入指南
├─ Playground
└─ 审计时间线
```

### 组件拆分建议

```text
frontend/src/pages/tools/ToolOverviewPanel.tsx
frontend/src/pages/tools/ToolCatalogPanel.tsx
frontend/src/pages/tools/ToolDetailDrawer.tsx
frontend/src/pages/tools/ToolRiskPolicyPanel.tsx
frontend/src/pages/tools/ToolTokenPanel.tsx
frontend/src/pages/tools/ToolPlaygroundPanel.tsx
frontend/src/pages/tools/ToolAuditTimeline.tsx
frontend/src/pages/tools/McpAccessGuide.tsx
frontend/src/pages/tools/useToolAccessData.ts
frontend/src/pages/tools/useToolPlayground.ts
```

### 重点交互

#### 5.4.1 工具目录卡片化

每个工具展示：

- 工具名称。
- 中文标题。
- 分类。
- 风险等级。
- 是否写操作。
- 是否需要确认。
- 是否可用。
- 所需 scopes。
- 最近调用耗时和成功率。

#### 5.4.2 工具详情 Drawer

点击工具后展示：

- 工具说明。
- 输入 schema。
- 输出 schema。
- 示例参数。
- 最近调用记录。
- 风险说明。
- 是否支持 dry run。
- 一键填入 Playground。

#### 5.4.3 Playground 强化

- JSON 参数编辑区保留。
- 增加 schema 表单模式。
- 高风险工具调用前展示统一风险确认。
- 调用结果支持复制 JSON、复制摘要、查看审计。
- 失败时显示错误原因和建议检查项。

#### 5.4.4 审计时间线

展示字段：

- 调用时间。
- 工具名。
- 触发者。
- 来源：MCP stdio / MCP HTTP / HTTP Tool / UI。
- 风险等级。
- 耗时。
- 结果状态。
- 参数摘要。
- 审计详情入口。

### 验收标准

- 工具列表可以按分类、风险、读写、可用性筛选。
- 高风险工具必须出现风险确认。
- 任意工具可以 2 次点击进入调试。
- 任意调用记录可以追溯到工具、参数摘要和结果。

---

## 5.5 服务器页面优化

### 当前问题

`ServerListPage.tsx` 文件较大，服务器页面适合从 CRUD 列表升级为“运维资产视图”。

### 改造方向

#### 5.5.1 服务器列表分区

增加视图切换：

- 列表视图。
- 环境分组视图。
- 系统/服务关联视图。
- 异常服务器视图。

#### 5.5.2 快速筛选

支持：

- 环境。
- 系统。
- 服务。
- 在线状态。
- 标签。
- 关键词。

搜索需 debounce。

#### 5.5.3 服务器详情页工作台

服务器详情建议展示：

- 基本信息。
- 健康检查。
- 最近发布。
- 最近任务。
- 文件入口。
- 终端入口。
- 可执行 MCP 操作。

#### 5.5.4 终端延迟加载

当前项目使用 xterm。建议：

- 终端 Tab 被点击时再动态 import xterm 相关模块。
- 关闭终端时释放实例和连接。
- 限制同时打开的终端数量。
- 低资源模式下默认不自动连接终端。

### 验收标准

- 服务器列表加载时不会阻塞页面导航。
- 大于 200 台服务器时使用分页或虚拟滚动。
- 终端不进入 Tab 不加载 xterm 实例。
- 服务器异常状态可以从列表页直接筛出。

---

## 5.6 Pipeline 页面优化

### 当前问题

`PipelinePage.tsx` 文件较大，流程编排属于高复杂 UI。需要拆分编辑、预览、模板、步骤管理。

### 拆分建议

```text
frontend/src/pages/pipeline/PipelineList.tsx
frontend/src/pages/pipeline/PipelineEditor.tsx
frontend/src/pages/pipeline/PipelineStepList.tsx
frontend/src/pages/pipeline/PipelineStepEditor.tsx
frontend/src/pages/pipeline/PipelineTemplatePanel.tsx
frontend/src/pages/pipeline/PipelinePreviewPanel.tsx
frontend/src/pages/pipeline/usePipelineData.ts
frontend/src/pages/pipeline/usePipelineActions.ts
```

### 交互优化

- 左侧流程列表，右侧流程详情。
- 步骤支持折叠编辑。
- 步骤变更后显示“未保存”状态。
- 支持复制流程、从模板创建、预览执行计划。
- 高风险步骤标识清楚。
- 发布页选择 pipeline 时可预览步骤摘要。

### 验收标准

- Pipeline 列表和编辑器逻辑分离。
- 修改步骤不会导致整个页面大范围重渲染。
- 新建流程可以从模板开始。
- 删除流程/步骤走统一确认。

---

## 5.7 数据库工具和 SQL 页面优化

### 当前问题

数据库工具页功能复杂，SQL 操作属于高风险场景，尤其 DML、清理、导出等操作需要更明确的风险控制。

### 改造方向

#### 5.7.1 SQL Workbench 优化

- 查询输入区和结果区固定布局。
- 查询历史可搜索。
- 查询结果分页展示。
- 大结果集默认限制行数。
- 导出需展示结果行数和文件大小预估。

#### 5.7.2 DML 风险确认

对以下操作统一使用高风险流程：

- `UPDATE`
- `DELETE`
- `INSERT`
- `DROP`
- `ALTER`
- 清理任务执行
- 大批量导出

确认内容包括：

- 数据源。
- SQL 摘要。
- 影响表。
- 是否有 where 条件。
- 预计影响行数。
- 操作原因。

#### 5.7.3 表结构浏览

- 表列表可搜索。
- 表字段可展开。
- 表大小、行数、最近更新时间可展示。
- 常用查询模板一键填入。

### 验收标准

- 查询大表不会一次性渲染所有结果。
- DML 操作必须明确风险确认。
- 查询历史、导出、表结构浏览入口清晰。

---

## 6. 通用组件开发计划

## 6.1 PageHeader

统一页面顶部标题、描述、操作按钮、状态标签。

```ts
export type PageHeaderProps = {
  title: string
  description?: string
  badge?: React.ReactNode
  actions?: React.ReactNode
  breadcrumbs?: Array<{ label: string; href?: string }>
}
```

适用页面：所有主页面。

---

## 6.2 EnhancedDataTable

在现有 `DataTable` 基础上增强。

能力：

- loading / empty / error。
- 分页。
- 排序。
- 行选择。
- 行展开。
- 批量操作。
- 横向滚动。
- sticky header。
- 可选虚拟滚动。

建议文件：

```text
frontend/src/components/EnhancedDataTable.tsx
```

---

## 6.3 LogConsole

替代或增强 `LogViewer`。

能力见 5.3。

建议文件：

```text
frontend/src/components/LogConsole.tsx
```

---

## 6.4 OperationDrawer

用于工具详情、服务器详情、任务详情、审计详情。

```ts
export type OperationDrawerProps = {
  open: boolean
  title: string
  width?: number | string
  onClose: () => void
  children: React.ReactNode
  footer?: React.ReactNode
}
```

---

## 6.5 RiskActionGuard

统一封装高风险操作确认流程，避免各页面重复实现。

```ts
export type RiskActionGuardProps = {
  riskLevel: 'low' | 'medium' | 'high' | 'critical'
  title: string
  target: string
  description?: string
  details?: Array<{ label: string; value: React.ReactNode }>
  requireReason?: boolean
  confirmText?: string
  onConfirm: (reason?: string) => Promise<void> | void
  children: (open: () => void) => React.ReactNode
}
```

内部可复用现有 `RiskConfirmDialog`。

---

## 6.6 EntityPicker

统一系统、服务、环境、服务器、Pipeline 等选择体验。

能力：

- 搜索。
- 最近使用。
- 推荐项。
- 禁用原因。
- 多选。
- 分组。

适用于发布页、服务器页、数据库页、工具页。

---

## 7. 状态管理和请求优化

## 7.1 API 层改造

当前 `api.ts` 的 axios 拦截器默认返回 `res.data`，这让读取 `ETag`、`304`、响应头不够方便。建议新增 `rawApi` 或 `requestRaw`，专门用于需要响应头的接口。

```ts
export const rawApi = axios.create({
  baseURL: getApiBaseUrl(),
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
})
```

适用接口：

- 部署日志。
- 发布历史。
- 审计记录。
- 大型表格。
- 报告下载。

## 7.2 数据缓存策略

建议新增轻量 hook：

```text
frontend/src/hooks/useCachedResource.ts
frontend/src/hooks/useDebouncedValue.ts
frontend/src/hooks/useUrlQueryState.ts
```

能力：

- 先展示旧数据。
- 后台刷新。
- 支持 TTL。
- 支持 AbortController。
- 支持错误保留旧数据。
- 查询条件同步 URL。

## 7.3 轮询策略

基于现有 `useSmartPolling` 强化：

- 页面隐藏时降频或暂停。
- 同一资源避免重复轮询。
- 运行中任务高频，完成后低频或停止。
- 失败后指数退避。
- 页面卸载主动 abort。

## 7.4 搜索和筛选

所有搜索框统一：

- debounce 200～300ms。
- Enter 立即搜索。
- 清空按钮。
- URL 参数同步。
- 空结果提示。

## 8. 资源占用优化

## 8.1 代码拆分

已有路由级 lazy，下一步做页面内部重组件 lazy：

- xterm 终端。
- SQL 编辑器/结果表。
- 发布报告详情。
- MCP Playground。
- Pipeline 编辑器。

## 8.2 大列表优化

策略：

- 后端支持分页的页面必须默认分页。
- 无分页接口时，前端超过 200 行启用简单虚拟列表或分片渲染。
- 表格列渲染避免复杂 inline 函数。
- `useMemo` 缓存过滤结果。

## 8.3 低资源模式

新增应用级设置：

```text
性能模式：标准 / 低资源
```

低资源模式效果：

- 减少背景渐变和 blur。
- 关闭非必要动画。
- 日志默认保留更少行数。
- 表格默认更小分页。
- 终端不自动连接。
- 首页减少实时刷新频率。

建议 localStorage key：

```text
ops-performance-mode = standard | low-resource
```

## 8.4 CSS 动画控制

项目已有 `prefers-reduced-motion` 相关基础，可继续强化：

```css
html[data-performance='low-resource'] * {
  animation-duration: 0.001ms !important;
  transition-duration: 0.001ms !important;
}

html[data-performance='low-resource'] .glass-card,
html[data-performance='low-resource'] .glass-panel {
  backdrop-filter: none;
  box-shadow: none;
}
```

## 9. 高风险操作统一规范

### 9.1 适用范围

必须统一确认的操作：

- 生产发布。
- 回滚。
- 取消发布。
- 删除系统 / 服务 / 服务器。
- 删除发布包。
- SQL DML / DDL。
- 数据清理任务。
- MCP 写操作。
- 高风险 MCP 只读但敏感操作。
- 释放锁。
- 删除 token。

### 9.2 确认内容

统一展示：

- 操作名称。
- 风险等级。
- 影响对象。
- 影响范围。
- 是否可回滚。
- 参数摘要。
- 操作原因。
- 确认短语。

### 9.3 审计联动

执行后展示：

- 审计 ID。
- 任务 ID。
- 报告入口。
- 重试入口。
- 回滚入口，若支持。

## 10. 文件拆分优先级

### P0：先拆会明显影响开发效率的页面

| 页面 | 当前问题 | 拆分目标 |
|---|---|---|
| `ServerListPage.tsx` | 文件过大、筛选/操作/表格耦合 | 列表、筛选、批量操作、详情抽屉拆分 |
| `PipelinePage.tsx` | 流程编辑复杂 | 列表、编辑器、步骤、模板拆分 |
| `DatabaseToolsPage.tsx` | 数据库连接、查询、清理、导出混杂 | 按 Tab 和 hook 拆分 |
| `ToolAccessPage.tsx` | MCP 能力中心信息密集 | 概览、目录、Token、Playground、审计拆分 |
| `DeployForm.tsx` | 发布表单仍偏大 | 按步骤拆分 |

### P1：再拆支撑组件

```text
components/LogConsole.tsx
components/EnhancedDataTable.tsx
components/OperationDrawer.tsx
components/RiskActionGuard.tsx
components/EntityPicker.tsx
components/PageHeader.tsx
hooks/useCachedResource.ts
hooks/useDebouncedValue.ts
hooks/useUrlQueryState.ts
```

## 11. 分阶段实施计划

## Phase 1：UI 基础组件和低风险体验优化

### 目标

先补齐通用组件，减少重复代码，快速提升页面体感。

### 任务

1. 新增 `PageHeader`。
2. 增强 `DataTable` 或新增 `EnhancedDataTable`。
3. 新增 `useDebouncedValue`。
4. 新增 `OperationDrawer`。
5. 新增 `RiskActionGuard`，内部复用 `RiskConfirmDialog`。
6. 为主要页面统一 loading / empty / error 状态。
7. 增加低资源模式开关和 CSS 降级。

### 影响文件

```text
frontend/src/components/ui.tsx
frontend/src/components/PageHeader.tsx
frontend/src/components/EnhancedDataTable.tsx
frontend/src/components/OperationDrawer.tsx
frontend/src/components/RiskActionGuard.tsx
frontend/src/hooks/useDebouncedValue.ts
frontend/src/App.tsx
frontend/src/index.css
```

### 验收标准

- 新组件通过 TypeScript 检查。
- 至少 3 个页面接入 `PageHeader`。
- 至少 2 个高风险操作接入 `RiskActionGuard`。
- 低资源模式可切换并持久化。

---

## Phase 2：发布页和日志控制台优化

### 目标

优化最核心路径：发布和日志。

### 任务

1. 新增 `LogConsole`。
2. 发布执行面板接入 `LogConsole`。
3. 发布页新增摘要卡片。
4. 预检结果按阻断/警告/建议/通过分组。
5. 发布历史增加“复用配置”。
6. 运行面板顶部固定状态摘要。
7. 日志保留上限和完整日志下载入口。

### 影响文件

```text
frontend/src/components/LogConsole.tsx
frontend/src/pages/DeployPage.tsx
frontend/src/pages/deploy/DeploymentRunPanel.tsx
frontend/src/pages/deploy/DeployForm.tsx
frontend/src/pages/deploy/PreflightPanel.tsx
frontend/src/pages/deploy/DeploymentHistoryTable.tsx
frontend/src/pages/deploy/DeploySummaryCard.tsx
```

### 验收标准

- 发布页常规流程更清晰，不影响原有发布能力。
- 5000 行日志输入时页面不卡死。
- 用户查看历史日志时不会被自动滚动打断。
- 发布失败可以快速复制错误上下文。

---

## Phase 3：MCP / AI 工具中心重组

### 目标

把 AI 工具页从“大杂烩”改成能力中心。

### 任务

1. 拆分 `ToolAccessPage`。
2. 新增工具详情 Drawer。
3. 工具目录支持分类、风险、读写、可用性筛选。
4. Playground 支持风险确认。
5. 审计时间线支持按工具、风险、结果筛选。
6. 接入 token 权限最小化提示。
7. 增加 MCP 接入示例复制按钮。

### 影响文件

```text
frontend/src/pages/ToolAccessPage.tsx
frontend/src/pages/tools/ToolOverviewPanel.tsx
frontend/src/pages/tools/ToolCatalogPanel.tsx
frontend/src/pages/tools/ToolDetailDrawer.tsx
frontend/src/pages/tools/ToolTokenPanel.tsx
frontend/src/pages/tools/ToolPlaygroundPanel.tsx
frontend/src/pages/tools/ToolAuditTimeline.tsx
frontend/src/pages/tools/McpAccessGuide.tsx
frontend/src/pages/tools/useToolAccessData.ts
```

### 验收标准

- 工具详情、调试、审计路径清楚。
- 高风险工具调用必须确认。
- 工具列表 300 条以内筛选流畅。
- Token 创建、撤销、权限说明清晰。

---

## Phase 4：服务器、Pipeline、数据库大页面拆分

### 目标

降低大页面维护成本，优化列表和编辑体验。

### 任务

1. 拆分 `ServerListPage`。
2. 服务器列表接入统一表格和筛选。
3. 服务器详情终端延迟加载。
4. 拆分 `PipelinePage`。
5. Pipeline 编辑器支持步骤折叠和预览。
6. 拆分 `DatabaseToolsPage`。
7. SQL 查询结果分页和 DML 风险确认。

### 验收标准

- 大页面单文件尽量控制在 400 行以内。
- 大列表渲染明显更流畅。
- 终端未打开时不初始化 xterm。
- SQL 高风险操作走统一确认。

---

## Phase 5：体验收尾和内部产品化

### 目标

形成小团队长期可用的内部运维工作台。

### 任务

1. 用户偏好保存：默认系统、默认环境、默认发布模式、表格列配置。
2. 常用操作收藏：常用服务器、常用 SQL、常用 MCP 工具、常用发布模板。
3. 首页按用户角色展示不同快捷入口。
4. 审计、任务、报告互相跳转。
5. 全局命令面板支持搜索服务器、工具、任务、报告。
6. 增加操作成功后的下一步建议。

### 验收标准

- 用户常用流程能被快速复用。
- 页面间跳转形成闭环。
- 新成员可以通过首页和工具中心理解系统能力。

## 12. 建议开发排期

| 周期 | 重点 | 产出 |
|---|---|---|
| 第 1 周 | 通用组件、低资源模式、发布摘要、日志控制台一期 | 可感知的响应和交互提升 |
| 第 2 周 | 发布页流程化、MCP 工具中心拆分一期 | 核心业务路径优化 |
| 第 3 周 | 服务器、Pipeline、数据库大页面拆分 | 降低维护成本和渲染压力 |
| 第 4 周 | 首页驾驶舱、偏好保存、命令面板增强 | 内部产品化体验 |

## 13. 测试与验收方案

### 13.1 静态检查

执行：

```bash
cd frontend
npm run typecheck
npm run build
```

### 13.2 本地冒烟

重点检查：

- 登录 / 退出。
- 工作台加载。
- 发布预检。
- 发布执行日志展示。
- 发布取消确认。
- 发布历史打开。
- AI 工具目录加载。
- 高风险工具调试确认。
- 服务器列表筛选。
- 数据库 SQL 查询。
- DML 风险确认。
- 低资源模式切换。

### 13.3 性能验收

建议准备模拟数据：

- 300 台服务器。
- 300 个 MCP 工具。
- 5000 行发布日志。
- 1000 条审计记录。
- 1000 行 SQL 查询结果。

验收点：

- 页面不白屏。
- 筛选输入无明显卡顿。
- 长日志滚动可用。
- 路由切换可恢复。
- 后台接口失败时保留可读错误状态。

## 14. 风险与规避

| 风险 | 说明 | 规避方式 |
|---|---|---|
| 一次性重构过大 | 大页面多，容易影响已有功能 | 按页面逐步拆，保持原接口不变 |
| 组件抽象过度 | 小团队不需要复杂设计系统 | 只抽业务高频组件 |
| 引入新依赖增加包体 | UI 库或虚拟列表库可能变重 | 优先自研轻量组件 |
| 高风险流程被绕过 | 页面各自实现确认容易遗漏 | 使用统一 `RiskActionGuard` |
| 日志优化影响排障 | 截断日志可能丢信息 | 前端截断只影响展示，完整日志保留下载入口 |
| MCP 页面信息过多 | 用户不知道从哪里开始 | 按“概览→目录→详情→调试→审计”分层 |

## 15. 交付清单

### 必交付

- `PageHeader`
- `EnhancedDataTable`
- `LogConsole`
- `OperationDrawer`
- `RiskActionGuard`
- 发布摘要卡片
- 发布执行控制台优化
- MCP 工具详情 Drawer
- 低资源模式
- 大页面拆分计划落地至少 2 个页面

### 可选增强

- 全局命令面板搜索服务器 / 工具 / 任务。
- 表格列配置持久化。
- 常用发布模板收藏。
- SQL 模板收藏。
- MCP 工具收藏。
- 首页自定义卡片。

## 16. 最终推荐优先级

按收益排序，建议先做：

1. `LogConsole`：直接改善发布、任务、审计、诊断体验。
2. 发布页摘要和流程化：降低误操作，提高发布效率。
3. `RiskActionGuard`：统一高风险操作，减少安全隐患。
4. MCP 工具中心拆分：让 AI Agent 能力可见、可控、可审计。
5. 大页面拆分和表格统一：降低后续维护成本。
6. 低资源模式和终端懒加载：改善本地低配和远程桌面体验。

## 17. 一句话结论

本项目 UI 优化不应追求“大而炫”的后台模板，而应围绕内部运维场景做成：

> 快速响应、流程清楚、风险可控、日志可查、Agent 可审计的轻量运维控制台。

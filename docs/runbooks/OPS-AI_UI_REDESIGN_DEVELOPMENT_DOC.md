# OPS-AI UI 重设计开发文档

## 1. 范围与目标

### 1.1 范围
- 删除独立 `/lab` 页面及其所有相关资源（神经突触/尘埃/动画主题/本地命令面板），不再保留为生产路由。
- 将 Lab 提供的能力（实时态势、风险联动、只读探针、运行状态提示）融合到既有页面：
  - 首页（Dashboard）
  - 任务中心（Jobs）
  - 发布执行面板（Deploy）
  - 诊断页（Diagnostics / System）
- 全站重构视觉规范（配色、背景、动效、玻璃化），使 UI 更接近“极简、可读、信息优先级清晰”的 Enterprise AI Command Center。

### 1.2 非目标
- 不引入新的 UI 组件库或大型框架（不重构 Popover/Drawer 基础组件）
- 不修改后端接口协议（仅前端适配与联动）
- 不保留 Lab 的主题切换光效与场景渲染能力

---

## 2. 信息架构（IA）重构

### 2.1 导航收敛
保持现有左侧导航不变，但路由与页面结构按下述规则整理：

- `Dashboard /` —— 总览监控总入口
- `Deploy /deploy` —— 发布创建与执行面板
- `Jobs /tasks` —— 统一任务中心（包含历史/运行/失败/重试入口）
- `Fleet /servers` —— 服务器/资产总览
- `Data /databases/tools` —— 数据库工具入口（复用数据库与工具页面）
- `AI /ai-tools` —— MCP / AI 能力中心
- `System /system` —— 诊断/状态/巡检/系统设置

同时移除 Lab 的导航点、依赖项加载、lab 专用 class，以及 `index.css` 中 `neural-lab` 的排版链路。

### 2.2 Lab 能力融合映射
| Lab 能力 | 融合到页面 | 融合方式（从视觉到联动） | 备注 |
|---|---|---|---|
| 站点实时态势（score、running、failed、blocked、risks） | Dashboard | 新增 SiteStatusPanel；事件/风险/任务数据从现有 widget 聚合 | 与 “活动流/待处理风险” 打通，支持跳转到 Jobs/Deploy/Fleet 详情 |
| 风险联动（节点/状态变化突出显示） | Jobs、Dashboard | 用状态徽章 + 颜色码 + 时间戳即可；不使用光效或粒子特效 | 适合 UI 的“风险计数/卡片”最低成本适配 |
| 只读探针（inspect/metrics/log/…）与错误预览 | Diagnostics + Dashboard入口 | 诊断页提供“ProbeDropdown”，Dashboard 区加同跳入口 | 必须区分只读与写操作，写操作进入确认/审计流 |
| 日志/时间联动 | Deploy | 保留 LogConsole 与现有 timeline，但在发布面板被聚合出最终失败原因 | 不保留 Lab 的动画滚动或“脉冲”表现形式 |
| 命令面板（⌘K） | 全局现有命令面板（不再属 lab） | 统一 ⌘K 打开时三类数据：导航 + 服务器 + 工具 + 任务 | Lab 内单独提及的 `applyFxMode` 等全局控制会撤下 |

---

## 3. 页面与交互规格

### 3.1 命令面板（⌘K）
- 触发：`Ctrl/Cmd + K`
- 目的：快速跳转入口（不影响 Lab 删除后的跳转逻辑）
- 数据源：
  - 导航（navItems 现有列表）
  - 服务器（`serverManagement.list`）
  - 工具（`capabilityTools.list(...)`）
  - 任务（`deployment.list(...)`）
- 面板内容：
  1. 搜索分组（Navigation / Servers / Tools / Tasks），每组至少 8 条
  2. TrackBar：顶部一个状态的摘要胶囊（来自 Dashboard 的站点态势计数），允许在当前面板内筛选“导航类别”
  3. 快速操作：切换主题、刷新页面、打开系统诊断（不进入 Lab）

### 3.2 Dashboard（首页）
- 在现有 Dashboard layout 上增加一个截面：`SiteStatusPanel`
  - 指标：score、running、failed、blocked 数（可视化小卡片或简明 counters）
  - 标签：最近风险统计（high/critical）
  - 行为：点击指标卡 -> 路由到对应详情（Tasks / DeploymentRunPanel / Fleet）
- 现有 widgets（ActivityWidget、RisksWidget、TasksWidget、DeployWidget、StorageWidget、FavoritesWidget、SearchHistoryWidget）维持功能但 require：
  - 明确颜色语义（gonna “failure” ≠ lab 样式的光晕色；而是从 CSS token 映射 badge 颜色）
  - 更稳的布局切换（拖拽与布局保存不因 Theme 切位导致卡顿）

### 3.3 Jobs（任务中心）
- 保留 TaskCenter 数据列表逻辑，但加入：
  - 行顶部风险标记（RiskBadge），如果有 high/critical 风险（来自 audit / tool call 数据），展示出来
  - 失败原因摘要（若最后一步失败，从 task detail 或 from current log entry 摘要）
  - 状态过滤：当前支持 kind/status，基础上增加 `risk` 过滤参数到任务查询 API（与 taskCenter 现有 API 兼容）
- 点击行：进入详情（`openDetail` 现有实现保持，但确保在无 Lab 页的情况下仍可用）
- 批量选择/删除：沿用现有逻辑，但展示更显眼的二次确认（RiskConfirmDialog 而不是 toast）

### 3.4 Deploy（发布页面）
- Release Page 布局重构：
  - Create/Info 区域 + Run（执行面板，DeploymentRunPanel 翻转式，不再额外隔出 lab）
  - 当面板打开时，右侧显示 ServiceGrid（健康徽章 + status badges） + timeline（无 backlog 光效）
  - 运行日志：LogConsole 必须显示最新的运行状态（不限于 metadata），且支持 `Previous/Next` 模式选择，而不是纯滚动
- 失败归因：通过 `deployment()` + `LogConsole` 的 status/timeline 聚合展示（可以读取 `task_details`/`step_tasks` 等字段）

### 3.5 Diagnostics（诊断/系统）
- 增加 `ProbeDropdown` 模块（但此功能在 Dashboard 上也可以一键进入）
- 数据来源：巡检日志、运维快照、以及 Dashboard 的 LRU 动态更新
- 探针只读目前不需要新的后端接口（复用现有 dashboard / taskCenter / inspection）

---

## 4. 视觉系统（Theming & Layout）

### 4.1 色彩/语义
- 必须包含：背景、面板、文字三级色阶、强调色、风险颜色等级
- 建议命名（仅用 CSS 变量，不再依赖 CSS-in-JS 的类名变化）：
  - `--bg-base`、`--bg-panel`、`--bg-glass`、`--border-hair`
  - `--text-hi/mid/low`
  - `--accent`、`--warn`、`--alert`、`--info`
  - Risk: `--risk-high/--risk-medium/--risk-low`

### 4.2 交互与动效
- 允许：layout 稳定、淡入淡出作为加载态、text brightness/tone changes
- 禁止：粒子/3D/WebGL/扫描线/blur 背景常驻、模糊玻璃拖拽作为默认视觉语言（可选设置打开，但默认关闭）
- 保留主题切换，但切换不再引起窗口闪烁（z-index/h-overlap 隐藏步骤要稳固）

### 4.3 布局基准
- 目录树：保持 2+ 列（窄 sidebar + 主内容区），最大宽度限制（例如 1320px）
- 卡片之间的清晰度来自颜色/边界，而不是投影

---

## 5. 前端代码迁移/落地步骤（分阶段）

### Phase 1：删除 Lab 全量能力但确保模式可复用
- 从 `src/pages` 撤掉 LabPage import & route registration
- 从 `src/App.tsx` 删除 lab 相关路由
- 从 `src/index.css` / `src/theme/tokens.css` 删除 `neural-lab` 相关样式链（保留全局整体主题但撤掉 lab 切换）
- 移除 lab 专属的 fx/density 输入与 LogConsole/Dialog 特性波及面
- 移除所有 `*.hooks` 依赖的 renderer engine / canvas 上下文（如存在特定场景）

### Phase 2：融合 Lab “实时态势” 到 Dashboard
- 在 Dashboard 顶部插入 SiteStatusPanel（running/failed/blocked/score）
- 重组现有 widgets 的样式（确保 badge 颜色与 token-value 对齐）但不改变数据接口
- 调整 `RisksWidget` 风险卡片按钮直接跳转到相关 Dashboard/详页

### Phase 3：Jobs / Deploy / Diagnostics 融合 lab 交互能力
- Jobs：添加 RiskBadge + 失败原因摘要到列表行 + 状态/risk 组合过滤
- Deploy：重新组织面板为 （server status + timeline + consolidated log）；LogConsole 显示真实最终失败原因
- Diagnostics：添加只读 ProbeDropdown（由 Dashboard 引入的互通入口）

### Phase 4：性能与清理
- 删除 lab 渲染相关 imports（如 `renderer`, `dust`, `synapse`, `shaders` 等仅在 lab 页使用的文件）
- 构建验证并 diff 产物，确保没有遗漏的 chunk 或 css bundle 增加
- 可访问性：⌘K 仍可打开全局命令面板，且支持 Esc 关闭；Dashboard/Jobs/Deploy 的 Tab 顺序不破坏

---

## 6. 构建验证与验收

- 构建命令：`npm run build` 能通过，且 CSS/JS 产物不包含 lab 页面 chunk
- 总 JS 对 bundle 的影响：应低于当前 Lab 叠加的有限产物（可接受增加一个站点态势指标面板的 DOM 变更）
- 命令面板行为符合预期：输入 `tools` / `servers` / `tasks` 能返回相应分组，并且点击能跳转
- 首页 SiteStatusPanel 与 风险联动可以一次跳转到对应历史详情（正确性测试）
- Jobs 列表的请求/失败/过滤参数与 taskCenter 现有 API 一致
- LogConsole 在发布面板里持续可用（不会因 timeline 切换而闪退）

---

## 7. 实施注意事项

1) 删除 Lab 页时确保错误页（NotFoundPage）仍然可访问  
2) 如果 Lab 页背后有缓存或命令面板状态，删除时同时清理（localStorage / keys）  
3) Dashboard 的 SiteStatusPanel 不应引入新的后端接口（只用现有 dashboard + taskCenter）  
4) 探针只做只读，写操作必须在任务中心串行化入口进入审计链路  

---

## 8. 回归用例

- 打开发布（deploy）某 service，能在执行面板面对失败时点选“重试”，并在 Dashboard 的风险中出现提升
- 任务中心筛选 running/failed + 打开详情面板，失败原因与日志时间顺序一致
- ⌘K 全功能应收发“导航、服务器、工具、任务”
- 点击 Dashboard 的 SiteStatusPanel 中“失败计数”或“运行计数”，能跳转到对应任务中心/服务器列表并成功预览详情

---

## 附录：以下为施工时的代码钩子参考（仅摘必要）

- Dashboard：`systemHealth.dashboard` + `taskCenter.list` + `deployment.list`
- 任务中心（Jobs）：`taskCenter.deleteMany` / `taskCenter.retry` / `taskCenter.detail` / `inspection.issues`
- 发布：`deployment.retry` / `deployment.list` + 现有 `useDeploymentPolling` / `useDeploymentStream` 数据流
- 诊断：`systemHealth.snapshot` / `inspection` 接口用于探针与巡检摘要
- 命令面板：`App.tsx 中的 CommandPalette props：navItems + onToggleTheme`（需替换 lab 专属的 palette 内容）

---

文档版本：v1
发布时间：2026-08-03
作者：OPS-AI UI 重构组

# OPS Command Center — 项目背景

> 单一事实来源，面向代码分析 / 二次开发 / AI Agent 上下文注入。
> 与 `README.md`（产品概览）和 `docs/runbooks/OPS-AI_UI_REDESIGN_DEVELOPMENT_DOC.md`（UI 重设计开发文档）配合使用。

## 1. 技术栈与运行方式

- **后端**：Python (FastAPI-like 单体)，入口 `main.py`，配置 `config_manager.py`，SSH 能力 `ssh_client.py`。本地单进程部署。
- **前端**：Vite + React 18 + TypeScript + React Router。位于 `frontend/`，无重型 UI 框架（自研组件 + CSS 变量）。
- **默认端口**：`http://localhost:8000`。
- **启动脚本**：Windows `start_prod.bat` / `start_dev.bat` / `start_diag.bat`；Linux/macOS `start_*.sh`。

## 2. 目录结构

```
D:\code\ops-ai\
├── main.py                  # 后端入口
├── config_manager.py        # 配置解析
├── ssh_client.py            # SSH 能力
├── app/                     # 后端业务模块
├── scripts/                 # 构建/工具脚本（frontend_syntax_check.js 等）
├── tests/                   # pytest 契约/回归测试（backend）
├── docs/
│   ├── plans/               # 计划与运行时 source-of-truth
│   ├── reviews/             # 评审
│   └── runbooks/            # 运维手册 + 开发规范（见 §7 开发文档）
└── frontend/
    └── src/
        ├── main.tsx         # 前端入口
        ├── App.tsx          # 顶层路由 + 命令面板 + 布局
        ├── routes.ts        # ROUTES 常量 + NAV_LINKS + SPA_PAGE_ROUTES
        ├── api/             # 前端 API 客户端（client.ts 等）
        ├── services/        # 领域服务（liveStatus.ts 只读探针等）
        ├── components/      # 共享组件（ui、LogConsole、v10、design-system）
        ├── pages/           # 页面（Dashboard、TaskCenter、Deploy、SystemDiagnostics 等）
        ├── types/           # TS 类型（deploy.ts、action.ts、agent.ts 等）
        ├── hooks/           # 自定义 hooks（useRuntime.ts 等）
        └── index.css        # 全局样式 + CSS 变量（主题 token）
```

## 3. 前端入口与路由

- 入口：`frontend/src/main.tsx` → `App.tsx`。
- 路由常量：`frontend/src/routes.ts` 的 `ROUTES`/`NAV_LINKS`/`SPA_PAGE_ROUTES` 是唯一权威定义。
- 关键路由：
  - `ROUTES.dashboard = '/'` 工作台
  - `ROUTES.deploy = '/deploy'` 发布
  - `ROUTES.tasks = '/tasks'` 任务中心（别名 `/task-center`）
  - `ROUTES.system = '/system'` 状态诊断；`ROUTES.diagnostics = '/system/diagnostics'`
  - `ROUTES.inspection = '/inspection'` 巡检中心
  - `ROUTES.servers = '/servers'` 服务器；`ROUTES.files = '/files'` 文件
  - `ROUTES.database = '/database'`（别名 `/sql-query`）
  - `ROUTES.tools = '/tools'`、`ROUTES.mcpTools`、`ROUTES.mcpAudit`、`ROUTES.aiWorkflows`、`ROUTES.aiAnalysis`
  - `ROUTES.reports`、`ROUTES.audit`、`ROUTES.maintenance`、`ROUTES.pipelines`、`ROUTES.systems`
- **Important**：`neuralLab`（`/lab`）已在 2026-08 重构中移除，`routes.ts` / `App.tsx` **不得再出现 Lab 引用**。

## 4. 前端验证命令（Windows PowerShell）

> `npx tsc` 在仓库根会提示 “This is not the tsc command you are looking for”，**不可用**。必须在 `frontend/` 内用本地 `.bin` 执行。

```powershell
# 语法/文件数检查（仓库根）
node scripts/frontend_syntax_check.js          # 期望 188 files passed

# TS 类型检查（frontend/ 目录内）
cd frontend
.\node_modules\.bin\tsc.cmd --noEmit -p tsconfig.json

# 生产构建（frontend/ 目录内）
npm run build
```

- **typecheck 基线 = 10 个预存错误（可容忍，非本次引入）**：
  - `App.tsx(20,43)` SettingsTheme 未使用；`App.tsx(66,7)` THEME_CYCLE 未使用。
  - `components/server-workbench/OverviewTab.tsx(323,43)` actionBusy 未使用；`(323,55)` onAction 未使用。
  - `design-system/index.ts(25/26/27)` DiagnosisCard / ActionButton / PipelineStage 导出名不匹配。
  - `main.tsx(15,5)`、`main.tsx(19,21)` ThemeMode 类型不匹配（`'light'|'dark'` → `ThemeMode`）。
  - `pages/tools/ToolTokenPanel.tsx(212,5)` getter 未使用。
- 修改时应保证新增错误为 **0**，基线 10 个保持即可。

## 5. 后端契约测试（Python）

- 解释器：`C:\Users\admin\AppData\Local\Python\bin\python.exe`（**不可用**裸 `python`，是 MS Store alias）。
- `configfile: pytest.ini`，rootdir 为仓库根。

```powershell
cd D:\code\ops-ai
& "C:\Users\admin\AppData\Local\Python\bin\python.exe" -m pytest <tests/...> -q
```

- 基线已知失败（可容忍，非本次引入）：
  - `test_dashboard_iteration_contract.py::test_workbench_clock_is_rendered_in_visible_hero_copy` —— 断言页面含 `dashboard-hero-side-fixed`，当前 Dashboard 未提供该 hero 容器。
- 前端契约集中仍有 3 个已知失败（均属既有缺陷，与本次 Lab 删除/能力融合无关）：
  - `test_frontend_pagination_contract.py::test_inspection_page_exposes_profile_preview_and_confirmation_workflow`（缺 `profile-confirm-hint`）
  - `test_frontend_tooling_contract.py::test_tool_catalog_has_inline_detail_and_compact_interaction_contract`（缺 `tool-directory-workspace`）
  - `test_frontend_tooling_contract.py::test_tool_token_panel_uses_chinese_permission_labels`（缺中文本地化标签）

## 6. Lab 能力融合（2026 重构落地状态）

依据 `docs/runbooks/OPS-AI_UI_REDESIGN_DEVELOPMENT_DOC.md`，Phase 1–4 已实现并验证：

- **Phase 1 — Lab 删除**：`pages/NeuralLabPage.tsx`、`components/RenderEngineProvider.tsx`、`components/visualization/*`、`renderer/*`（dust/engine/renderTokens/shaders/synapse/types/webgl）、`theme/neural-lab.css` 全部删除；`App.tsx`/`routes.ts` 已移除 `/lab` 引用；dist 产物无 lab chunk、无 “神经突触实验室” 字符串。
- **Phase 2 — Dashboard `SiteStatusPanel`**：新增站点实时态势面板（score/running/failed/blocked + 高风险/待确认计数 + 最近风险列表），插在首页 KPI 区；点击跳转对应详情（Jobs/Deploy/Fleet）。
- **Phase 3 — Jobs/Deploy/Diagnostics 融合**：
  - TaskCenterPage：`getTaskRiskLevel` → `RiskBadge`（high/medium/low）+ 客户端 risk 过滤（`RISK_FILTER_OPTIONS`）。
  - Deploy：`failureReasons`（`useMemo` 聚合 report.failure_analysis / summary_text / 失败服务器 / 失败 step）+ 失败归因卡；LogConsole 提供错误高亮与 `jumpToFirstError` / 错误计数，保留 `onlyErrors` 过滤。
  - Diagnostics `SystemDiagnosticsPage`：新增 `ProbeDropdown` 下拉模块（复用 `services/liveStatus.ts` 的 `runNodeProbe`/`NODE_ROUTES`），支持选择探针 → 执行 → 显示 StatusPill 结果 → 跳转详情。所有探针均为只读操作。
- **Phase 4 — 清理与验证**：`liveStatus.ts`（只读探针）迁移至 `services/`；`git rm` 已 staged 删除 16 个 lab 文件；构建验证通过。

> **重构中的并行 agent 风险**：存在一个并行开发 agent（`openclaw-gateway`）曾用**小写文件名重新创建整套 lab 文件**（`renderer/*`、`visualization/*`、`neurallabpage.tsx`、`renderengineprovider.tsx`、`theme/neural-lab.css`）。提交前需 `git status` 检查是否再次出现这些 untracked 的 lab 文件并清理。

## 7. 开发文档与相关规范

- 规范：`docs/runbooks/OPS-AI_UI_REDESIGN_DEVELOPMENT_DOC.md`（Lab 删除 + 能力融合 + 视觉系统 + 回归）。
- 依赖注意：`pages/TaskCenterPage.tsx` 的 `RiskBadge` 从 `components/ui`（`components/ui.tsx` 文件）导入，而非 `components/ui/index.ts`（后者只导出 GlassPanel/Button/Badge/MetricCard/ThemeToast/SettingsModal）。
- `frontend/src/api/client.ts` 与 `frontend/src/services/*` 是前端对后端调用层，改动后端协议需保持契约测试同步。

## 8. 常见验证命令速查

```powershell
# 后端完整回归
& "C:\Users\admin\AppData\Local\Python\bin\python.exe" -m pytest tests -q

# 只前端受影响时的快速自检
cd frontend; .\node_modules\.bin\tsc.cmd --noEmit -p tsconfig.json; node scripts/frontend_syntax_check.js

# 查看本次 UI 重构改动范围
git status --short
git diff --cached --stat   # staged：16 个 lab 删除
```
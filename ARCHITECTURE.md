# OPS Platform Architecture Notes

本项目面向本地部署、小团队内部使用，架构目标是：单进程可运行、SQLite 可维护、发布链路可回归、MCP 与 UI 复用同一套后端能力。

## 运行形态

- 开发模式：Vite 前端 + FastAPI 后端。
- 本地生产模式：FastAPI 直接托管 `frontend/dist`，使用 `start_single_process.*` 启动。
- 数据目录：默认集中到 `APP_DATA_DIR`，包含数据库、日志、上传包、备份和 runtime 临时文件。

## 后端分层

### API 层

`app/api/*` 只应负责：

- 鉴权与权限检查；
- 参数读取与响应包装；
- 调用 service；
- 审计必要的用户操作。

### 发布服务层

发布相关逻辑逐步收敛到 `app/deploy/`：

- `history.py`：发布历史分页、筛选、列表载荷；
- `logs.py`：发布日志、任务、服务器任务、步骤任务载荷；
- `preflight.py`：预检三态、风险等级和确认信息；
- `report.py`：发布报告、失败归因、Markdown 报告；
- `tool_response.py`：MCP 工具稳定返回结构；
- `worker.py`：轻量 worker handle；
- `locks.py`：发布锁；
- `rollback.py`：回滚运行时逻辑。

`app/deploy/query.py` 仅保留为兼容导出层，新代码优先直接使用 `history.py` 和 `logs.py`。

## MCP / Tool 能力

MCP 不应重新实现一套发布逻辑，应通过 `app/services/tool_adapters/*` 调用和 UI 相同的 service。高风险能力继续遵循：

- 默认只读；
- 写操作需要显式开启；
- 生产操作需要确认与 reason；
- 所有调用写入 ToolCallLog；
- 返回结构尽量包含 `ok / error_code / message / summary / data / suggestions`。

## 前端结构

- 旧入口 `frontend/src/api.ts` 保持兼容。
- 新增类型与轻量 API 包装：
  - `frontend/src/types/*`：跨页面共享 DTO；
  - `frontend/src/api/*`：新代码优先使用的小型 API 包装。
- 通用 UI 状态组件在 `frontend/src/components/ui.tsx`。

## 回归重点

每轮交付前至少运行：

```bash
bash scripts/release_check.sh
```

重点覆盖历史问题：

- `frontend/dist` 是否打包；
- `/assets/*.js` 是否被鉴权误拦截；
- Windows 单进程 SPA 路径是否正确；
- Worker 状态接口是否报错；
- 诊断包导出是否走 API；
- MCP 发布报告是否保持稳定结构。

## iter29 运维面收敛

iter29 将服务器、SFTP 文件、SQL 查询三个日常运维面补齐安全护栏：

- 服务器配置完整性与轻量健康检查放在 `app/services/server_ops.py`，REST 与 MCP 均复用。
- SFTP API 保持管理员专用，并强制路径白名单、删除确认、覆盖确认、日志 tail 和流式下载。
- SQL 查询工作台继续只读路线，新增风险分析与历史分页，不引入审批和外部队列。


## iter30 UI consistency

Frontend shared primitives now include DataTable, FilterBar, ConfirmDialog, LogViewer and CopyButton. New pages should reuse these primitives before adding page-local table, log or confirmation implementations.

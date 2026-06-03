# OPS Command Center v2.1.8

本项目是面向小团队内部使用的本地运维控制台，提供服务发布、服务器资产、文件管理、系统维护、数据库工作台、审计报告和 AI Agent/MCP 工具接入能力。

当前版本为团队稳定运行优化版，目标不是继续扩展审计或报告主线，而是提升新人启动、单进程运行、本地资源治理和排错效率。

## 推荐启动方式

### Windows

生产单进程模式：

```bat
start_prod.bat
```

开发模式：

```bat
start_dev.bat
```

诊断模式：

```bat
start_diag.bat
```

### Linux / macOS

生产单进程模式：

```bash
./start_prod.sh
```

开发模式：

```bash
./start_dev.sh
```

诊断模式：

```bash
./start_diag.sh
```

默认访问：

```text
http://localhost:8000
```


## v2.1.8 本地运行稳定性

本版本新增启动前自检和本地资源面板：

- `scripts/preflight_start_check.py`：检查 Python、Node/npm、frontend/dist、依赖声明、APP_DATA_DIR、SQLite 和端口占用。
- `start_prod.*`：生产单进程入口，启动前自动进行 preflight 检查。
- `start_dev.*`：开发模式入口，保留 Vite + FastAPI 双进程调试方式。
- `start_diag.*`：只执行诊断，不启动服务。
- 维护页新增“本地资源”面板，展示 SQLite、日志、上传包、备份、报告和 runtime 临时文件占用，并支持“扫描 → 预览 → 一键确认 → 清理”。
- 单进程模式下 `/assets/*` 使用长缓存，SPA 页面保持 `no-cache`，避免旧页面长期缓存。

## 当前稳定能力

- 服务、服务器资产管理
- 发布预检、发布执行、发布历史、回滚计划
- 文件中心与服务器文件工作台
- 统一任务中心与轻量轮询
- 系统状态、安装诊断、数据库备份与恢复
- 统一数据库工作台：业务库只读查询、SQL 执行、连接配置、数据清理、本地 OPS 库导出
- 审计日志、基础报告中心
- MCP Streamable HTTP / HTTP Tool 双入口
- 高风险操作一键确认与后端风险策略兜底

## 数据库受控执行边界

当前版本恢复数据库执行能力，但不开放无保护写入。数据库工作台包含：

- 业务库 SELECT / SHOW / DESCRIBE / EXPLAIN / WITH 查询
- SQL 执行 fast / standard / full 分级预检与一键确认执行
- DML 执行后自动生成验证 SQL，可回填到只读查询页
- 数据清理 Dry Run、复核、分批执行、暂停与继续
- 本地 OPS 库查询、导出和维护

写操作统一走“SQL 执行”或“数据清理”入口。后端会强制限制单语句、WHERE 条件、最大影响行数，并拦截 DDL、权限变更、锁表、多语句和长耗时函数。业务库是否能执行写操作取决于连接账号权限；生产连接建议使用最小权限维护账号。

## 团队使用建议

1. 先在“系统状态”页确认备份、磁盘、Worker 和前端构建状态。
2. 数据库能力统一从“数据库”入口进入，不再从维护页分散操作。
3. AI Agent 默认只开放低风险只读工具；发布、删除、终端危险命令必须走确认。数据库写操作必须走 SQL 执行或数据清理流程，先预检、再一键确认；DML 工具优先使用 `ops.db.preview_dml` → `ops.db.execute_dml` 两阶段。
4. 发布前先做预检；生产发布和回滚建议保留人工确认。
5. 每次升级前执行 `scripts/backup_before_upgrade.*`，并保留最近可用备份。

## 回归检查

无依赖快速检查：

```bash
python -m compileall -q app scripts main.py config_manager.py ssh_client.py
node scripts/frontend_syntax_check.js
node scripts/frontend_route_check.js
```

完整检查：

```bash
cd frontend && npm ci && npm run typecheck && npm run build
cd ..
pip install -r requirements.txt
python -m pytest tests -q
```

## 封板说明

本版本建议作为内部实际使用基线。后续只建议接受安全修复、阻断型 Bug 修复和小范围体验修补；不建议在当前基线上继续引入外部队列、复杂权限、多租户或大型框架迁移。

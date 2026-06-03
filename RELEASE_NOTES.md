
## v2.1.13 - DB Proxy Server Asset Selector Runtime Hotfix

- 修复数据库连接 SSH 跳板机 / 目标主机下拉无服务器选项的问题。
- 前端生产 dist 现在实际加载服务器资产与 SSH 密钥列表，不再只显示“手动填写”。
- 服务器资产接口读取 SQLite 管理服务器时不再依赖解密密钥/密码，避免 OPS_SECRET_KEY 缺失导致列表为空。
- 下拉数据会合并 /maintenance/ssh-server-assets 与 /servers，增强兼容性。



## v2.1.12 - 数据库 SSH 代理服务器资产选择修复

- 修复数据库连接表单中“从服务器资产选择跳板机 / 目标主机”无可选项的问题。
- 新增 `/api/v2/maintenance/ssh-server-assets`，合并 JSON 配置服务器与 SQLite 管理服务器。
- `/api/v2/servers` 同步兼容 DB-backed 管理服务器，避免服务器工作台与数据库代理选择器数据源不一致。
- 数据库 SSH 两跳执行时也可按名称解析 DB-backed 管理服务器。


## v2.1.11 - Database proxy SSH layout hotfix

- Fixed overlapping controls in the database connection SSH proxy form.
- Stacked saved-key selector, manual key path input, and key actions inside each key field.
- Added min-width and grid-span guards for the two-hop SSH proxy form so narrow columns do not overflow into adjacent inputs.
- Updated frontend package/build metadata to v2.1.11.

## v2.1.10 - Database proxy target-host UI dist hotfix

- 修复生产 `frontend/dist` 仍显示旧单跳 SSH 表单的问题。
- 数据库连接启用代理后，表单现在显示“SSH 跳板机 / 目标主机”。
- 支持从服务器资产选择跳板机和目标主机。
- 跳板机与目标主机的 SSH 私钥文件均支持选择已上传密钥、上传新密钥和跳转密钥管理。
- 保留手动覆盖主机、端口、用户名、密码、密钥名和本机路径能力。

# Release Notes - v2.1.8 Team Stable Hotfix

## v2.1.8 数据库代理密钥选择与上传补丁

本版本基于 v2.1.7 的“数据库代理跳板机可从服务器资产选择”继续收口手动覆盖能力：数据库连接启用 SSH 代理后，手动填写私钥不再只是路径输入，而是与服务器模块的 SSH 密钥管理保持一致。

重点变化：

- 数据库连接表单的“SSH 私钥文件”支持选择已保存 / 已上传密钥。
- 支持在数据库连接表单内直接上传新密钥，上传后自动回填 `ssh_key_path`。
- 提供“刷新密钥”和“密钥管理”入口，复用服务器模块的 `/admin/ssh-keys` 与 `/admin/upload-key` 能力。
- 继续保留手动输入绝对路径或 `~/.ssh/id_rsa` 的能力，兼容临时跳板机和特殊部署。
- 后端继续通过已保存密钥名解析项目 `keys/` 目录中的密钥文件，避免在数据库连接中重复维护私钥内容。

验证命令：

```bash
python -m compileall -q app scripts main.py config_manager.py ssh_client.py
node scripts/frontend_syntax_check.js
node scripts/frontend_route_check.js
python scripts/mcp_local_package_smoke.py
bash scripts/final_acceptance_check.sh
```

---

# Release Notes - v2.1.6 Team Stable Hotfix

## v2.1.6 本地运行稳定性与资源治理

本版本不继续扩展审计和报告主线，集中提升团队实际使用时的启动、运行、排错和本地资源治理体验。

重点变化：

- 新增 `scripts/preflight_start_check.py/.sh/.ps1/.bat`，启动前检查 Python、Node/npm、frontend/dist、依赖声明、APP_DATA_DIR、SQLite 和端口占用。
- 新增 `start_prod.*`、`start_dev.*`、`start_diag.*` 三类入口，降低新人接手时的启动理解成本。
- `scripts/start_single_process.*` 在启动前执行 preflight，并明确提示 dist 缺失、dist 过期、端口占用和目录不可写问题。
- 维护页新增“本地资源”面板，展示 SQLite、logs、uploads、backups、reports、runtime 等本地资源占用。
- 本地资源清理固定为“扫描 → 预览 → 一键确认 → 执行”，避免直接一键全删。
- 后端新增 `/api/v2/system/storage/summary` 和 `/api/v2/system/storage/scan`，支持前端按需刷新存储占用。
- 单进程模式为 `/assets/*` 增加长缓存，SPA 页面保持 `no-cache`，提升访问速度并降低旧页面风险。
- 启动日志输出 frontend/dist 状态，显式 `SERVE_FRONTEND=true` 时若 dist 过期会给出警告；可通过 `STRICT_FRONTEND_DIST=1` 强制阻断。

## v2.1.4 DML 受控执行治理闭环

本版本在 v2.1.3 数据库受控执行能力基础上补齐治理闭环：新增 DML 执行历史、连接级 DML 策略、UPDATE/DELETE 执行前样例、MCP 两阶段工具和执行复盘入口。

- 新增 `dml_execution_logs`。
- 数据库连接新增 `allow_dml`、允许语句类型、表白名单/黑名单、默认影响行阈值等策略字段。
- SQL 执行页展示执行前样例、更新字段、WHERE 摘要和最近 DML 历史。
- 新增 MCP 工具 `ops.db.preview_dml`、`ops.db.execute_dml`、`ops.db.list_dml_history`、`ops.db.get_dml_execution`。

## v2.1.3 数据库受控执行与报告维护热修复

本版本基于 v2.1.2 继续调整数据库边界：团队要求数据库处理具备执行能力，因此恢复并强化受控写操作，而不是仅保留只读评估。

### 主要变化

1. **数据库工作台新增 SQL 执行入口**
   - 新增“SQL 执行”页签。
   - 支持管理员执行单条 `UPDATE` / `DELETE` / `INSERT`。
   - 必须先预检影响范围，再一键确认执行。
   - 后端强制拦截 DDL、权限变更、锁表、多语句、无 WHERE 的 UPDATE / DELETE。
   - 默认最大影响行数 100，可配置但不超过后端阈值。

2. **数据清理恢复为受控分批执行**
   - “清理评估”恢复为“数据清理”。
   - Dry Run 后可提交复核、审批、开始执行。
   - 执行过程按批次 DELETE，记录批次日志、事件和审计。
   - 支持暂停、继续、取消。

3. **报告中心支持更新与删除**
   - 报告列表新增“编辑”和“删除”。
   - 可更新标题、状态、摘要。
   - 删除时会删除报告记录，并尝试删除本地文件。

4. **MCP / AI 工具补齐数据库执行能力**
   - `ops.db.preview_execute_sql`
   - `ops.db.execute_sql`
   - 仍标记为 high risk、write=true、requires_confirmation=true。
   - AI Agent 应先预检，再说明影响范围，最后等待用户确认后执行。

### 使用边界

- 查询入口仍只允许 SELECT / WITH 等只读语句。
- 写操作统一走“SQL 执行”或“数据清理”流程。
- 本地 OPS 库写操作仅开放报告、通知、审计、工具调用日志、发布日志等维护表。
- 业务库是否能执行写操作取决于连接账号权限；建议生产连接使用最小权限维护账号。
- 大批量业务变更仍建议走 DBA 流程，不建议直接在 OPS 中执行。

### 验证建议

```bash
python -m compileall -q app scripts main.py config_manager.py ssh_client.py
node scripts/frontend_syntax_check.js
node scripts/frontend_route_check.js
bash scripts/final_acceptance_check.sh
```

如开发环境已安装完整依赖，继续执行：

```bash
cd frontend && npm ci && npm run typecheck && npm run build
cd ..
pip install -r requirements.txt
python -m pytest tests -q
```

## v2.1.7 Database proxy bastion selection hotfix

- 数据库连接配置的“启用代理 / SSH 跳板机”支持从服务器资产中选择跳板机。
- 选择服务器后自动带出主机、端口、SSH 用户名和密钥路径；仍允许手动覆盖。
- 后端数据库 SSH tunnel 支持保存 `ssh_server_name`，并可复用服务器资产中的密码、密钥文件或内联密钥内容。
- `SSHTunnel` 支持项目已保存密钥名解析和内联私钥内容，避免数据库代理与服务器管理两套凭据割裂。
- 新增 SQLite 迁移 `051_001_database_connection_ssh_server_name`。

## v2.1.9 - 数据库代理两跳 SSH 拓扑补丁

本补丁调整数据库 SSH 代理的网络模型，适配实际拓扑：OPS 后端先 SSH 到跳板机，再从跳板机 SSH 到目标主机，最后由目标主机访问数据库地址。

### 变更

- 数据库连接新增目标主机配置：`ssh_target_server_name`、`ssh_target_host`、`ssh_target_port`、`ssh_target_username`、`ssh_target_key_path` 等。
- “数据库 → 连接配置 → SSH 跳板机”增加“从服务器资产选择目标主机”。
- 目标主机手动覆盖的密钥文件支持选择已上传密钥、上传新密钥，与服务器模块密钥管理一致。
- 后端 `SSHTunnel` 支持两跳 SSH：local → bastion → target host → database address。
- 兼容旧单跳模式：未配置目标主机时仍按 local → bastion → database address 处理。

### 使用说明

- “跳板机”选择公网或可从 OPS 访问的入口机。
- “目标主机”选择能从跳板机 SSH 到达、且能访问数据库地址的应用/内网主机。
- “数据库主机”填写目标主机视角下可访问的数据库地址。
- 如果目标主机访问数据库使用不同地址，可在“目标主机视角的数据库地址”中覆盖。

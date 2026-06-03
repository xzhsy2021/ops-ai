# 巡检中心故障与变更记录

> 本 runbook 用于沉淀 2026-06 期间巡检中心在「已上线」与「MCP 联调」阶段发现的问题、根因与处理方式，方便后续回归与新人 oncall。
>
> 关联规范：
> - [docs/inspection_center_upgrade_spec.md](../../docs/inspection_center_upgrade_spec.md)（技术方案 + 落地状态）
> - [docs/runbooks/mcp-capability-matrix.md](../../docs/runbooks/mcp-capability-matrix.md)（MCP 巡检工具矩阵）

---

## 1. 已知缺陷修复

### 1.1 巡检项页面 404
- **现象**：`GET /api/v2/inspection/item-configs?scope_type=SERVER` 返回 404。
- **根因**：FastAPI 进程已加载旧版本路由；同时 `item_configs_list` 端点形参 `request: Request = None` 与文件其它签名不一致。
- **修复**：[app/api/inspection.py](../../app/api/inspection.py) `item_configs_list` 改为 `request: Request,`；清空 `__pycache__` 后重启。
- **预防**：新增加路由后必须重启主进程（不会自动 reload）；`py` 启动脚本加 `--reload` 仅 dev 模式。

### 1.2 巡检记录 / 台账报表分页写死
- **现象**：分页条只能看到 10 条/页，且无法调整。
- **修复**：[InspectionCenterPage.tsx](../../frontend/src/pages/InspectionCenterPage.tsx) 用 `PAGE_SIZE_OPTIONS = [10,20,50,100,200]`，并支持首页/末页跳转。
- **回归**：`pageSize` 状态独立保存于 `runPageSize` / `ledgerPageSize` / `reportPageSize`。

### 1.3 RUNNING 记录无法删除
- **现象**：删除巡检记录时提示「关联其他信息」；二次确认后状态仍为 RUNNING。
- **根因**：`ops.inspection.delete_runs` 缺少 `force=true` 通道，状态机不允许删 RUNNING。
- **修复**：[InspectionCenterPage.tsx](../../frontend/src/pages/InspectionCenterPage.tsx) 检测到 RUNNING 时增加二次确认；服务层 `delete_runs` 支持 `force=true` 强制清理（仅限历史脏数据）。

### 1.4 风险问题无删除入口
- **现象**：「风险问题」Tab 没有删除按钮。
- **修复**：新增 [DELETE /api/v2/inspection/issues/{id}](../../app/api/inspection.py)；服务层 [delete_issue](../../app/services/inspection_center.py)；前端每行 + 批量 + 全选。
- **说明**：硬删除，仅用于清理脏数据；不替代状态机。

### 1.5 MCP 无法查询 / 按分组执行
- **现象**：`ops.list_servers` 没有 `group` 字段说明，`ops.inspection.run_server` 只接受单 server，没有按 group 触发的入口。
- **修复**：
  - 新增 `ops.list_server_groups`（聚合 total / inspectable / online）。
  - 新增 `ops.inspection.run_servers_batch`，支持 `server_ids / groups / group / all_servers`。
  - 后端 `/api/v2/inspection/server-groups` 提供同步 HTTP。
  - 前端「服务器巡检」Tab 顶部多选「按分组直接巡检」。
- **说明**：跳板机 (`tiaobanji 47.86.9.194:33890`) 是 crypto 分组的事实依赖，分组级调整需同步运维。

### 1.6 `categories` 简写导致只跑 2 个分类
- **现象**：调用 `run_servers_batch(categories=["login","account","process","disk","backup"])` 后，只跑了 BACKUP 与 DISK。
- **根因**：[inspection_center.py](../../app/services/inspection_center.py) `_server_check_specs` 把入参 `upper()` 后与 `LOGIN_SECURITY` / `ACCOUNT_SECURITY` / `PROCESS_PORT` 等全名比对，简称不匹配。
- **修复**：调用方改用全大写编码（`LOGIN_SECURITY` 等），并在 runbook 1.5 与 [mcp-capability-matrix.md](../../docs/runbooks/mcp-capability-matrix.md) 显式声明。

### 1.7 重复巡检记录
- **现象**：巡检记录 Tab 出现 8 条新 + 8 条旧，共 16 条。
- **处理**：直接 SQL 删除 8 条 03:55:xx~03:56:00 旧记录（仅 2 个分类），保留 8 条 03:59:31~04:00:01 新记录（5 个分类）。脚本已删除，仅作历史。

---

## 2. 服务端字段约定（调用前必读）

| 字段 | 正确值 | 错误示例 |
| --- | --- | --- |
| `categories` | `LOGIN_SECURITY`、`ACCOUNT_SECURITY`、`COMMAND_HISTORY`、`PROCESS_PORT`、`FIREWALL`、`DISK`、`SERVICE_STATUS`、`BACKUP` | `login` / `account` / `process` / `disk` / `backup` |
| `group` | 服务器 `group` 字段值（不区分大小写） | 中文别名 / 拼音 |
| `run_id` | UUID，由 `run_servers_batch` 返回 | 自造字符串 |
| `status` | `PENDING` / `RUNNING` / `SUCCESS` / `PARTIAL_SUCCESS` / `FAILED` | 中文 |

---

## 3. 巡检后处置流程（建议）

1. `ops.inspection.list_issues(run_id=...)` 取得全部 OPEN 问题。
2. 按 `risk_level` 排序：`HIGH` → `MEDIUM` → `LOW`。
3. 对每条 issue 走：`PROCESSING` → 修复 → `FIXED` → 复查 → `VERIFIED`；或 `IGNORED`（需说明）。
4. 修复完成后调用 `ops.inspection.run_servers_batch` 重跑验证。
5. 必要时 `ops.inspection.generate_report` 生成归档报告。

---

## 4. 端到端自检（oncall 用）

```bash
# 1) 后端语法
cd backend && py -c "import ast; [ast.parse(open(p,encoding='utf-8').read(), p) for p in ['app/api/inspection.py','app/services/inspection_center.py','app/services/tool_adapters/inspection_tools.py','app/services/tool_adapters/server_tools.py']]; print('OK')"

# 2) 清缓存（修改过上述文件后必做）
py -c "import shutil,os; [shutil.rmtree(os.path.join(r,d), ignore_errors=True) for r,_,ds in os.walk('.') for d in ds if d=='__pycache__']"

# 3) 重启后端（按部署文档）
./scripts/dev.sh   # 或 start_single_process.ps1

# 4) 路由 / MCP 冒烟
py scripts/capability_tools_smoke.py
py scripts/smoke_check.py

# 5) 拉一个真巡检（小规模）
py - <<'PY'
import json, http.cookiejar, urllib.request
API='http://127.0.0.1:8000'
cj=http.cookiejar.CookieJar()
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open(urllib.request.Request(f'{API}/api/v2/auth/login', data=json.dumps({'username':'admin','password':'admin'}).encode(), headers={'Content-Type':'application/json'})).read()
print(json.loads(op.open(f'{API}/api/v2/inspection/server-groups').read()))
PY
```

---

## 5. 变更记录

| 日期 | 变更 | 影响面 |
| --- | --- | --- |
| 2026-06-02 | 巡检项 CRUD + raw_output 上线 | 后端 / 前端 / MCP |
| 2026-06-02 | 修复 item-configs 404、签名一致性 | 后端 |
| 2026-06-02 | 分页可调 + RUNNING 强删 + 风险问题删除 | 前端 + 后端 |
| 2026-06-03 | 新增 `ops.list_server_groups` / `ops.inspection.run_servers_batch` | MCP + API + 前端 |
| 2026-06-03 | 清理 crypto 分组 8 条重复巡检记录 | 数据 |

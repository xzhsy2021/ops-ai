# 巡检中心升级技术方案

## 1. 需求概述

### 1.1 背景
当前巡检中心存在以下问题：
- 巡检项目是固定的8个分类，用户无法灵活选择或调整
- 巡检规则与巡检项目硬编码关联，不支持动态配置
- 巡检记录只展示结果描述，缺少原始执行数据(raw_output)
- 页面布局信息密度不够，操作路径较长

### 1.2 目标
1. **巡检项目与规则关联**：支持可选、可编辑、可调整
2. **巡检记录增强**：展示原始返回数据(raw_output)
3. **MCP能力同步**：更新MCP巡检工具支持新功能
4. **页面布局优化**：提升信息密度和操作效率

---

## 2. 技术方案

### 2.1 数据模型变更

#### 2.1.1 新增表：inspection_item_configs（巡检项目配置）

```sql
CREATE TABLE inspection_item_configs (
    id VARCHAR(32) PRIMARY KEY,
    item_code VARCHAR(128) NOT NULL UNIQUE,  -- 如: SERVER_LOGIN_RECENT
    item_name VARCHAR(255) NOT NULL,         -- 如: 近期登录与失败登录检查
    category VARCHAR(64) NOT NULL,           -- 如: LOGIN_SECURITY
    scope_type VARCHAR(24) NOT NULL,         -- SERVER / PROJECT
    description TEXT,
    enabled BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,            -- 执行顺序
    config_json JSON DEFAULT '{}',           -- 可配置参数
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

#### 2.1.2 新增表：inspection_item_rules（巡检项目规则关联）

```sql
CREATE TABLE inspection_item_rules (
    id VARCHAR(32) PRIMARY KEY,
    item_config_id VARCHAR(32) NOT NULL,
    rule_code VARCHAR(128) NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    config_override JSON DEFAULT '{}',       -- 覆盖规则默认配置
    FOREIGN KEY (item_config_id) REFERENCES inspection_item_configs(id),
    FOREIGN KEY (rule_code) REFERENCES inspection_rules(rule_code),
    UNIQUE(item_config_id, rule_code)
);
```

#### 2.1.3 现有表扩展

**inspection_item_results 表已包含 raw_output 字段**，无需变更。

**inspection_runs 表扩展：**
- 新增 `item_config_snapshot` JSON 字段：记录本次巡检使用的项目配置快照

---

### 2.2 后端API变更

#### 2.2.1 新增API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v2/inspection/item-configs | 获取巡检项目配置列表 |
| PUT | /api/v2/inspection/item-configs/{id} | 更新巡检项目配置 |
| POST | /api/v2/inspection/item-configs/{id}/toggle | 启用/禁用巡检项目 |
| PUT | /api/v2/inspection/item-configs/reorder | 调整巡检项目顺序 |
| GET | /api/v2/inspection/item-configs/{id}/rules | 获取项目关联的规则 |
| PUT | /api/v2/inspection/item-configs/{id}/rules | 更新项目规则关联 |
| GET | /api/v2/inspection/runs/{run_id}/raw-output | 获取巡检原始数据 |

#### 2.2.2 修改API

| 方法 | 路径 | 变更 |
|------|------|------|
| GET | /api/v2/inspection/categories | 返回动态配置而非硬编码 |
| POST | /api/v2/inspection/servers/{id}/start | 支持按配置过滤巡检项 |
| GET | /api/v2/inspection/runs/{run_id} | 返回中增加 item_config_snapshot |

---

### 2.3 核心服务变更

#### 2.3.1 inspection_center.py 变更

1. **初始化逻辑**：
   - 启动时将 `SERVER_CATEGORIES` / `PROJECT_CATEGORIES` 同步到 `inspection_item_configs` 表
   - 将内置规则同步到 `inspection_item_rules` 关联表

2. **巡检执行逻辑**：
   - 从数据库读取启用的巡检项目配置
   - 按 `sort_order` 排序执行
   - 每个项目读取关联的规则并执行
   - 保存 `item_config_snapshot` 到巡检记录

3. **规则引擎增强**：
   - 支持规则配置覆盖（`config_override`）
   - 支持动态阈值参数

---

### 2.4 前端页面变更

#### 2.4.1 服务器巡检页面（Tab: server）

**当前布局问题：**
- 巡检分类选择区占用了大量垂直空间
- 服务器选择按分组折叠，操作繁琐

**优化方案：**

```
┌─────────────────────────────────────────────────────────────┐
│ 服务器巡检                                                  │
├─────────────────────────────────────────────────────────────┤
│ [服务器选择 ▼]  [一键巡检]  [巡检设置 ▼]                     │
├─────────────────────────────────────────────────────────────┤
│ 巡检项目配置（可展开）                                       │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│ │ ☑ 登录安全   │ │ ☑ 账号安全   │ │ ☐ 命令日志   │           │
│ │   [配置 ▼]   │ │   [配置 ▼]   │ │   [配置 ▼]   │           │
│ └─────────────┘ └─────────────┘ └─────────────┘           │
├─────────────────────────────────────────────────────────────┤
│ 批量服务器选择                                               │
│ [分组1 ▼] [分组2 ▼] ...                                     │
└─────────────────────────────────────────────────────────────┘
```

**关键变更：**
1. 巡检项目以卡片形式展示，支持勾选启用/禁用
2. 每个卡片有"配置"按钮，可编辑该项目的规则和参数
3. 支持拖拽调整执行顺序

#### 2.4.2 巡检记录详情页面（Tab: runs）

**当前布局问题：**
- 结果表格列数过多，信息拥挤
- raw_output 未展示
- 执行过程以纯文本展示，可读性差

**优化方案：**

```
┌─────────────────────────────────────────────────────────────┐
│ 巡检记录详情                                                │
├─────────────────────────────────────────────────────────────┤
│ 基本信息：时间/对象/评分/状态                                 │
├─────────────────────────────────────────────────────────────┤
│ 巡检结果列表                                                 │
│ ┌──────────┬────────┬──────┬────────┬──────────┬────────┐ │
│ │ 分类     │ 巡检项 │ 等级 │ 结果   │ 命令     │ 操作   │ │
│ ├──────────┼────────┼──────┼────────┼──────────┼────────┤ │
│ │ 账号安全 │ ...    │ 高危 │ 发现...│ grep...  │ [详情] │ │
│ └──────────┴────────┴──────┴────────┴──────────┴────────┘ │
├─────────────────────────────────────────────────────────────┤
│ 选中项详情（点击[详情]展开）                                  │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 结果描述：发现多个 UID=0 特权账号                         │ │
│ │ 建议：立即核查陌生特权账号                                │ │
│ │ 可验证命令：grep 'x:0:' /etc/passwd                      │ │
│ │ 原始输出：                                               │ │
│ │ ┌─────────────────────────────────────────────────────┐ │ │
│ │ │ root:x:0:0:root:/root:/bin/bash                     │ │ │
│ │ │ daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin    │ │ │
│ │ └─────────────────────────────────────────────────────┘ │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**关键变更：**
1. 结果表格精简列数，增加"操作"列
2. 点击"详情"展开该行完整信息（含 raw_output）
3. raw_output 以代码块形式展示，支持复制

#### 2.4.3 巡检规则页面（Tab: rules）

**优化方案：**
1. 增加"关联项目"列，展示规则被哪些巡检项目使用
2. 规则编辑时增加"关联项目选择"功能
3. 支持批量调整规则与项目的关联

---

### 2.5 MCP工具变更

#### 2.5.1 新增MCP工具

| 工具名 | 功能 |
|--------|------|
| ops.inspection.list_item_configs | 获取巡检项目配置 |
| ops.inspection.update_item_config | 更新巡检项目配置 |
| ops.inspection.get_run_raw_output | 获取巡检原始输出 |

#### 2.5.2 修改MCP工具

| 工具名 | 变更 |
|--------|------|
| ops.inspection.run_server | 支持按配置过滤巡检项 |
| ops.inspection.get_run | 返回中增加 item_config_snapshot 和 raw_output |

---

## 3. 实施计划

### Phase 1: 数据层（1天）
1. 创建 `inspection_item_configs` 表
2. 创建 `inspection_item_rules` 表
3. 编写数据迁移脚本（将现有硬编码配置导入数据库）
4. 扩展 `inspection_runs` 表（增加 item_config_snapshot）

### Phase 2: 后端API（1天）
1. 实现巡检项目配置CRUD API
2. 修改巡检执行逻辑（读取数据库配置）
3. 修改巡检记录详情API（返回raw_output）
4. 实现规则与项目关联API

### Phase 3: 前端页面（2天）
1. 重构服务器巡检页面（卡片式巡检项目选择）
2. 重构巡检记录详情（展开式raw_output展示）
3. 优化巡检规则页面（关联项目展示）
4. 整体布局优化

### Phase 4: MCP同步（0.5天）
1. 新增MCP工具
2. 修改现有MCP工具
3. 测试验证

### Phase 5: 测试验证（0.5天）
1. 功能测试
2. 回归测试
3. 性能测试

---

## 4. 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 数据迁移失败 | 高 | 先备份数据库，迁移脚本支持回滚 |
| 巡检执行性能下降 | 中 | 配置数据缓存，避免频繁查询 |
| 前端兼容性 | 低 | 渐进式升级，保留旧API兼容 |
| MCP工具不兼容 | 中 | 保留旧工具，新增工具并行 |

---

## 5. 回滚方案

1. 数据库：保留旧表结构，新表可独立删除
2. API：保留旧接口，新接口以 `/v2/` 前缀区分
3. 前端：支持新旧布局切换

---

## 6. 实际落地状态（2026-06-03）

> 本节追踪本规范 5 个 Phase 的真实落地情况，以及上线后修复的若干缺陷。代码定位以最近一次改动为准。

### 6.1 Phase 完成情况

| Phase | 范围 | 状态 | 关键文件 |
| --- | --- | --- | --- |
| Phase 1 数据层 | `inspection_item_configs` / `inspection_item_rules` / 启动同步 | ✅ 完成 | [app/db/models.py](file:///D:/code/ops-ai/app/db/models.py) |
| Phase 2 后端 API | item-configs CRUD、`/raw-output`、分类动态化 | ✅ 完成 | [app/api/inspection.py](file:///D:/code/ops-ai/app/api/inspection.py) |
| Phase 3 前端 | 卡片化巡检项、raw_output 展开、规则关联 | ✅ 完成 | [frontend/src/pages/InspectionCenterPage.tsx](file:///D:/code/ops-ai/frontend/src/pages/InspectionCenterPage.tsx) |
| Phase 4 MCP 同步 | 17 个 `ops.inspection.*` 工具 + `ops.list_server_groups` | ✅ 完成 | [app/services/tool_adapters/inspection_tools.py](file:///D:/code/ops-ai/app/services/tool_adapters/inspection_tools.py) / [server_tools.py](file:///D:/code/ops-ai/app/services/tool_adapters/server_tools.py) |
| Phase 5 测试验证 | 路由冒烟 + 后端语法 check + 端到端跑通 crypto 分组 | ✅ 完成 | `scripts/smoke_check.py` / `scripts/capability_tools_smoke.py` |

### 6.2 上线后修复（2026-06-02 ~ 2026-06-03）

| 缺陷 | 根因 | 修复 |
| --- | --- | --- |
| 巡检项页面 404 (`/item-configs?scope_type=SERVER`) | 旧 FastAPI 进程缓存 + `request: Request = None` 与文件其它签名不一致 | 修签名为 `request: Request,` 并清 `__pycache__` 重启 |
| 巡检记录 / 台账报表分页写死 10 条 | `PAGE_SIZE` 常量 | 改为可选 10/20/50/100/200，并支持首页/末页跳转 |
| RUNNING 记录无法删除 | 唯一 `delete_runs` 没有强制删除通道 | 二次确认后强制删除（[InspectionCenterPage.tsx](file:///D:/code/ops-ai/frontend/src/pages/InspectionCenterPage.tsx)） |
| 风险问题无法删除 | 缺 `DELETE /issues/{id}` | 新增 [inspection.py](file:///D:/code/ops-ai/app/api/inspection.py) 路由 + 服务层 `delete_issue` + 前端单/批量删除按钮 |
| MCP 无法查询分组 | 缺分组聚合工具 | 新增 `ops.list_server_groups` + `GET /api/v2/inspection/server-groups` |
| MCP 巡检无法按分组执行 | `ops.inspection.run_server` 只接受单个 `server_id` | 新增 `ops.inspection.run_servers_batch`，支持 `server_ids / groups / group / all_servers` |
| 重复巡检记录 | 上线期用 `categories=["login", ...]` 旧简称触发，跑了 2 个分类就停 | 改为 `LOGIN_SECURITY` 等全大写编码；清理 8 条 03:55:xx~03:56:00 旧记录 |

### 6.3 本轮新增 / 变更 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v2/inspection/server-groups` | 列出服务器分组（total/inspectable/online） |
| POST | `/api/v2/inspection/servers/run` | `payload` 新增 `groups` / `group` 字段 |
| POST | `/api/v2/inspection/servers/batch-run` | 同上 + 透传 `groups` 到 `resolve_servers_for_inspection` |
| POST | `/api/v2/inspection/servers/batch-start` | 同上 |
| DELETE | `/api/v2/inspection/issues/{id}` | 硬删除风险问题（仅清理脏数据） |

### 6.4 当前 MCP 工具（17 + 2）

| 工具 | 类别 | 风险 |
| --- | --- | --- |
| `ops.list_servers` | server_read | low |
| `ops.list_server_groups` | server_read | low |
| `ops.inspection.overview` / `categories` / `list_item_configs` / `get_item_config` / `update_item_config` / `toggle_item_config` / `update_item_rules` | inspection_read / write | low / medium |
| `ops.inspection.run_server` / `run_servers_batch` | inspection_execute | **high**（需用户确认） |
| `ops.inspection.get_run` / `get_run_raw_output` / `list_runs` / `list_issues` / `update_issue` / `delete_runs` / `delete_issue` / `generate_report` | inspection_read / write | low / medium |

### 6.5 已知限制 / 后续

- 单实例 SQLite 写并发能力有限，批量巡检 `concurrency` 默认 4，可调至 20。
- 巡检 evidence 永久入库，如需 retention 走 `tool_call_logs` 同套清理（[runbooks/RELEASE_AUDIT_RETENTION_POLICY.md](file:///D:/code/ops-ai/docs/runbooks/RELEASE_AUDIT_RETENTION_POLICY.md)）。
- 跳板机 (`tiaobanji 47.86.9.194:33890`) 是当前 crypto 分组的事实依赖，任何分组级调整需同步运维。
- 报告生成依赖 `report_center` 流程；批量报告的 PDF 导出暂未实现。

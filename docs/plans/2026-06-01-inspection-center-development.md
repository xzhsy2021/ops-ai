# 巡检中心完整开发文档

版本：v1.0  
日期：2026-06-01  
适用项目：Ops Platform / ops-ai  
落地口径：巡检以服务器巡检为基础功能，以项目巡检为业务落点。

## 1. 背景与目标

依据《项目安全巡检方案》，平台需要建立常态化、标准化、可追溯的安全巡检机制，覆盖服务器系统层、项目程序层、配置文件层、接口访问层、网络权限层，并支持风险分级、闭环处置、报告归档。

本次落地目标不是新增一个孤立页面，而是将现有“状态页 / 诊断页 / 报告中心 / 服务器资产 / 系统服务配置”整合为统一的巡检中心：

- 服务器巡检回答：环境是否安全、稳定、可用。
- 项目巡检回答：部署在该环境上的项目是否安全、稳定、合规。
- 报告中心沉淀：服务器巡检报告、项目巡检报告、综合巡检报告。
- 风险问题闭环：高危、中危、低危问题从发现、处理、复查到归档全程留痕。

## 2. 当前系统现状分析

源码识别结果：

| 模块 | 现状 | 本次处理 |
|---|---|---|
| 后端框架 | FastAPI | 新增 `/api/v2/inspection` |
| 数据库 | SQLAlchemy + SQLite | 新增巡检相关模型，`create_all` 自动建表 |
| 服务器资产 | 已有 `/api/v2/servers`、inventory、SSH 能力 | 复用服务器资产与 SSH 执行能力 |
| 系统/服务配置 | 已有 `/api/v2/systems` 与服务配置 | 作为项目巡检项目来源 |
| 状态页 | 已有 SystemStatusPage | 后续可进一步内嵌到巡检总览 |
| 诊断页 | 已有 SystemDiagnosticsPage | 后续可逐步并入巡检中心 |
| 报告中心 | 已有 ReportArtifact 与 `/api/v2/reports` | 新增 `inspection` 报告类型 |
| 前端 | React + Vite | 新增 `InspectionCenterPage` |

## 3. 产品定位

最终定位：

```text
巡检中心 = 服务器巡检基础能力 + 项目巡检业务落点 + 报告中心结果沉淀 + 风险问题闭环
```

菜单结构：

```text
巡检中心
├── 巡检总览
├── 服务器巡检
├── 项目巡检
├── 巡检记录
└── 风险问题
```

报告中心：

```text
报告中心
└── 巡检报告 inspection
    ├── 服务器巡检报告
    ├── 项目巡检报告
    └── 项目综合巡检报告
```

## 4. 功能范围

### 4.1 服务器巡检

服务器巡检作为基础能力，支持独立执行。首版只执行只读命令，不做自动封禁、冻结、删除、修改防火墙等高风险动作。

| 分类 | 检查项 |
|---|---|
| 登录安全 | last、lastb、成功/失败登录、暴力破解、root 登录 |
| 账号安全 | `/etc/passwd`、UID=0 特权账号、可登录用户数量 |
| 命令日志 | `.bash_history`、高危命令、history 清理痕迹 |
| 进程端口 | ps、ss/netstat、高危端口、疑似挖矿进程 |
| 防火墙 | firewalld、ufw、iptables 状态与宽松规则 |
| 磁盘空间 | `df -PTh`，超过阈值提示 |
| 服务状态 | systemd failed、常见服务状态 |
| 备份任务 | crontab、常见备份目录最近文件 |

### 4.2 项目巡检

项目巡检以系统/服务配置为项目资产来源，并自动关联部署服务器。

| 分类 | 检查项 |
|---|---|
| 文件安全 | 部署目录权限、可疑脚本、全局可写/SUID |
| 配置安全 | 明文密码、密钥、token、debug、匿名访问 |
| 接口访问安全 | 4xx/5xx、SQL 注入、XSS、路径遍历、敏感接口异常 |
| 白名单与网络权限 | 0.0.0.0/0、通配符、宽松 allow 配置 |
| 客户与运营安全 | 首版预留，后续接业务客户日志和权限表 |
| 项目备份 | 备份目录最近文件、0 字节文件、备份缺失 |
| 运行环境 | 项目端口、部署目录、日志目录、关联服务器巡检摘要 |

## 5. 数据模型

新增模型位于 `app/db/models.py`：

- `InspectionRun`：巡检执行记录。
- `InspectionEvidence`：巡检证据快照，入库前脱敏。
- `InspectionItemResult`：巡检项结果。
- `InspectionIssue`：风险问题闭环。
- `InspectionRule`：规则配置预留。
- `InspectionBaseline`：文件/配置/白名单基线预留。

核心字段：

```text
InspectionRun
- scope_type: SERVER / PROJECT
- server_id
- project_id
- status: PENDING / RUNNING / SUCCESS / PARTIAL_SUCCESS / FAILED
- score
- high_count / medium_count / low_count / normal_count
- report_id
```

```text
InspectionIssue
- risk_level: HIGH / MEDIUM / LOW
- status: OPEN / PROCESSING / FIXED / VERIFIED / IGNORED
- evidence_id
```

## 6. API 设计

新增 API 模块：`app/api/inspection.py`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v2/inspection/overview` | 巡检总览 |
| GET | `/api/v2/inspection/categories` | 巡检分类 |
| GET | `/api/v2/inspection/servers` | 可巡检服务器 |
| GET | `/api/v2/inspection/projects` | 可巡检项目 |
| POST | `/api/v2/inspection/servers/run` | 执行服务器巡检 |
| POST | `/api/v2/inspection/projects/run` | 执行项目巡检 |
| GET | `/api/v2/inspection/runs` | 巡检记录 |
| GET | `/api/v2/inspection/runs/{run_id}` | 巡检详情 |
| GET | `/api/v2/inspection/issues` | 风险问题 |
| PATCH | `/api/v2/inspection/issues/{issue_id}` | 更新问题状态 |

## 7. 后端实现

新增服务模块：`app/services/inspection.py`

设计原则：

1. 统一服务层执行服务器/项目巡检。
2. 所有远程命令由服务端内置，前端不能传任意命令。
3. 命令输出、配置内容、证据快照统一脱敏。
4. 检查项失败只标记该项为 ERROR，不影响整体留痕。
5. 高/中/低风险自动生成 `InspectionIssue`。
6. 巡检完成后计算评分。

评分规则：

```text
基础分 100
高危 -20
中危 -8
低危 -2
最低 0
```

风险状态流转：

```text
OPEN → PROCESSING → FIXED → VERIFIED
OPEN → IGNORED
```

## 8. 报告中心集成

已在 `app/services/report_center.py` 中新增报告类型：

```python
"inspection": {
    "title": "巡检报告",
    "target_type": "inspection_run",
    "description": "服务器巡检、项目巡检与项目综合巡检结果报告。",
    "formats": ["json", "md"],
}
```

生成方式：

```http
POST /api/v2/reports/generate
{
  "report_type": "inspection",
  "target_id": "<inspection_run_id>",
  "format": "md"
}
```

## 9. 前端实现

新增页面：`frontend/src/pages/InspectionCenterPage.tsx`

新增 API：`frontend/src/api.ts` 中的 `inspection`。

新增路由与菜单：

- `ROUTES.inspection = '/inspection'`
- 导航菜单新增“巡检”。
- `App.tsx` 新增懒加载页面和 Route。
- `app/pages.py` 新增 SPA 分发路径 `/inspection`。

页面包含：

- 巡检总览。
- 服务器巡检。
- 项目巡检。
- 巡检记录。
- 风险问题。
- 生成巡检报告入口。

## 10. 安全边界

首版严格限制：

- 不做自动封禁 IP。
- 不做自动冻结账号。
- 不做自动删除文件。
- 不做自动修改防火墙。
- 不做自动修改项目配置。
- 不允许前端传任意 shell 命令。

仅做只读采集、分析、留痕、报告。

## 11. 已落地文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `app/services/inspection.py` | 新增 | 巡检核心服务 |
| `app/api/inspection.py` | 新增 | 巡检 API |
| `app/db/models.py` | 修改 | 新增巡检表模型 |
| `app/db/__init__.py` | 修改 | 导出巡检模型 |
| `app/services/report_center.py` | 修改 | 新增 inspection 报告类型 |
| `main.py` | 修改 | 注册巡检 API Router |
| `app/pages.py` | 修改 | SPA 路由支持 `/inspection` |
| `frontend/src/api.ts` | 修改 | 新增 inspection API 客户端 |
| `frontend/src/routes.ts` | 修改 | 新增巡检菜单/路由 |
| `frontend/src/App.tsx` | 修改 | 新增巡检页面 Route |
| `frontend/src/pages/InspectionCenterPage.tsx` | 新增 | 巡检中心页面 |
| `frontend/src/pages/ReportCenterPage.tsx` | 修改 | 巡检报告类型标签 |

## 12. 本地启动和验证

后端：

```bash
python main.py
```

访问：

```text
/api/v2/inspection/overview
/api/v2/inspection/categories
```

前端：

```bash
cd frontend
npm ci
npm run build
```

当前交付环境中未能完成前端构建，因为上传包未包含 `node_modules`，且内置 npm registry 中 `proxy-from-env` 包返回 404。代码已按现有 React/Vite 工程结构合入，需在项目实际可用 npm 环境执行构建。

Python 依赖同样未在当前沙箱安装，因此未运行完整 pytest；已执行 Python 语法编译检查。

## 13. 下一阶段建议

P1：

- 将原状态页卡片进一步嵌入巡检总览。
- 将原诊断页能力转为巡检项。
- 增加巡检定时任务。
- 增加文件 SHA256 基线生成与比对。
- 增加配置基线比对。

P2：

- 接入客户白名单、客户权限、客户操作日志。
- 增加接口访问日志聚合分析。
- 增加月度安全报告。
- 增加 AI 风险摘要和整改建议。

P3：

- 在人工确认后支持自动封禁、冻结、规则修复。
- 多服务器并发巡检。
- 巡检规则可视化配置。

## 14. 验收标准

1. 巡检中心菜单可见。
2. 可以查看巡检总览。
3. 可以执行服务器巡检。
4. 可以执行项目巡检。
5. 巡检记录可查询。
6. 风险问题可查询并更新状态。
7. 巡检结果可生成报告中心报告。
8. 服务器巡检作为基础能力存在。
9. 项目巡检展示关联服务器风险摘要。
10. 首版所有巡检均为只读，不执行破坏性动作。

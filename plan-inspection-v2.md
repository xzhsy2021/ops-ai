# 项目安全巡检 — 改造计划 v2

## 目标
将现有 SystemDiagnosticsPage / SystemStatusPage 改造为《项目安全巡检方案》的落地入口，在 ReportCenterPage 中支持巡检报告，复用已有前端组件和后端 Service 体系，不动现有 tool 名称、关键 API 路径、字段语义。

## 总览

| 改造项 | 方式 |
|--------|------|
| SystemDiagnosticsPage `→` 巡检作业页 | **改造现有页面**，底部追加"巡检结果"卡片槽，不新建独立巡检页 |
| SystemStatusPage `→` 巡检健康看板 | **改造现有页面**，追加巡检状态摘要模块 |
| ReportCenterPage | **追加 3 种报告类型**：inspection_daily / inspection_weekly / inspection_monthly |
| Sidebar "诊断" → "巡检" | routes.ts 改 label/desc，不改 path |
| 后端 | 新建 `app/services/inspection/` + `app/api/inspection.py`，保留 diagnostics 别名兼容 |
| 4 张新表 | inspection_policies / inspection_runs / inspection_findings / inspection_baselines |

---

## 继承的硬约束
- 本地单机单进程、SQLite+WAL
- 不引入 Redis/消息队列/K8s
- 不改现有 tool 名称、关键 API 路径、字段语义
- 56 条契约测试基线不变（新增巡检相关的即可）
- opencode NVIDIA provider API key 保持 `<YOUR_NVIDIA_API_KEY>` 占位符
- 巡检 SSH 命令白名单只读，禁止 rm/chmod/sed -i

---

## Phase 0 — 前端改名（最小侵入）
### 0.1 改 routes.ts
```diff
- { path: ROUTES.diagnostics, label: '诊断', icon: Stethoscope, desc: '安装自检与回归诊断' },
+ { path: ROUTES.diagnostics, label: '巡检', icon: Stethoscope, desc: '安全巡检与系统诊断' },
```
### 0.2 改 App.tsx（不改）
- Route 路径不变，仍指向 `SystemDiagnosticsPage`
- 页面内部按照 render props 方式增加"巡检模式"切换

### 0.3 改 SystemDiagnosticsPage.tsx
- 在页面底部追加 `<InspectionResultsCard>` 组件（同文件内实现，或拆分到 `components/`）
- 不删现有诊断内容，顶部新增 `<TabBar tabs={["系统诊断", "安全巡检"]} />`

---

## Phase 1 — 后端数据模型（新表）
### 1.1 添加 4 张模型到 `app/db/models.py`
```python
class InspectionPolicy(Base):
    """巡检策略 — 每个系统/域可绑定策略"""
    __tablename__ = "inspection_policies"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    domain = Column(String(64), nullable=False)       # 2.1~2.8 巡检域
    name = Column(String(128), nullable=False)
    check_type = Column(String(48), nullable=False)    # ssh_command / sql_query / log_scan
    command = Column(String(1024), nullable=False)     # SSH 白名单命令
    frequency = Column(String(16), nullable=False)     # daily / weekly / monthly
    risk_level = Column(String(16), default="medium")  # low / medium / high / critical
    enabled = Column(Boolean, default=True)
    conditions = Column(Text, default="{}")            # JSON 阈值/条件
    target_systems = Column(Text, default="[]")        # JSON 适用系统列表
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class InspectionRun(Base):
    """巡检运行记录"""
    __tablename__ = "inspection_runs"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    policy_id = Column(String(36), ForeignKey("inspection_policies.id"), nullable=False)
    status = Column(String(16), default="pending")     # pending / running / completed / failed
    started_at = Column(DateTime, default=func.now())
    finished_at = Column(DateTime, nullable=True)
    triggered_by = Column(String(64), default="schedule")  # schedule / manual / webhook
    total_checks = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    errors = Column(Integer, default=0)

class InspectionFinding(Base):
    """巡检发现项"""
    __tablename__ = "inspection_findings"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id = Column(String(36), ForeignKey("inspection_runs.id"), nullable=False)
    policy_id = Column(String(36), ForeignKey("inspection_policies.id"), nullable=False)
    domain = Column(String(64), nullable=False)
    check_name = Column(String(128), nullable=False)
    severity = Column(String(16), nullable=False)      # low / medium / high / critical
    status = Column(String(16), default="open")        # open / acknowledged / resolved / suppressed
    title = Column(String(256), nullable=False)
    description = Column(Text, default="")
    evidence = Column(Text, default="")                # 命令输出摘要
    evidence_hash = Column(String(64), default="")     # SHA256 防篡改
    recommendation = Column(Text, default="")
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(64), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(64), nullable=True)
    occurrence_count = Column(Integer, default=1)      # 重复发现折叠计数
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class InspectionBaseline(Base):
    """巡检基准线 — 用于比对变化"""
    __tablename__ = "inspection_baselines"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    policy_id = Column(String(36), ForeignKey("inspection_policies.id"), nullable=False)
    domain = Column(String(64), nullable=False)
    baseline_type = Column(String(32), default="command_output")  # command_output / file_hash / config_value
    content_hash = Column(String(64), nullable=False)   # SHA256
    content = Column(Text, default="")                  # 基线内容
    captured_at = Column(DateTime, default=func.now())
```

### 1.2  migrate 脚本
`alembic/versions/xxxx_add_inspection_tables.py`

---

## Phase 2 — 后端 Service 层

### 2.1 `app/services/inspection/runner.py` — SSH 巡检执行器
- 复用 `ssh_client.SSHConnectionPool`
- **白名单命令校验**：只允许 `df / ps / ss / netstat / journalctl / tail / cat / ls / uptime / free / who / last / docker ps / find / du / stat / md5sum / sha256sum / grep / awk / wc` 等
- 输出截断 4096 字符，计算 `evidence_hash = SHA256(output)`
- 并发限制：最多 4 个并行，10 秒超时

### 2.2 `app/services/inspection/engine.py` — 巡检调度引擎
- 按频次（daily/weekly/monthly）筛选可执行的 `InspectionPolicy`
- 批量执行 -> 写入 `InspectionRun` / `InspectionFinding`
- **去重折叠**：同 policy 同 evidence_hash 的 findings 只 increment `occurrence_count`，不重复插入
- 结果回写到 `InspectionRun`

### 2.3 `app/services/inspection/checks.py` — 8 个巡检域的具体 check 实现
```python
# 2.1 磁盘水位
def check_disk_usage(system_name) -> dict
# 2.2 系统资源
def check_system_resources() -> dict
# 2.3 进程/端口/服务
def check_services() -> dict
# 2.4 应用日志异常
def check_error_logs() -> dict
# 2.5 接口日志审计 -> 复用 audit_records/tool_call_logs
def check_audit_logs(db) -> dict
# 2.6 数据备份
def check_backups() -> dict
# 2.7 证书/密钥
def check_certificates() -> dict
# 2.8 运营专项 (disabled by default)
def check_operations_vitals(db) -> dict
```

### 2.4 `app/api/inspection.py` — 巡检 API
```
GET    /api/v2/inspection/policies       — 策略列表
POST   /api/v2/inspection/policies       — 创建策略
PATCH  /api/v2/inspection/policies/:id   — 更新策略
DELETE /api/v2/inspection/policies/:id   — 删除策略
POST   /api/v2/inspection/run            — 手动触发一次巡检
GET    /api/v2/inspection/runs           — 运行历史
GET    /api/v2/inspection/runs/:id       — 单次运行详情
GET    /api/v2/inspection/findings       — 发现项列表（支持 severity/status/domain 过滤）
PATCH  /api/v2/inspection/findings/:id   — 处置发现项（ack/resolve/suppress）
GET    /api/v2/inspection/summary        — 巡检总览（供前端看板用）
POST   /api/v2/inspection/baselines      — 创建/更新基线
GET    /api/v2/inspection/baselines/:policy_id  — 查询基线
```

### 2.5 `app/scheduler.py` 追加定时任务
- 每日巡检 (`daily_jobs`): 磁盘、系统资源、服务
- 每周巡检 (`weekly_jobs`): + 证书、审计、备份
- 每月巡检 (`monthly_jobs`): + 运营专项、全量基线比对
- **避免通知风暴**：同一个 finding 只 notification 一次（`occurrence_count > 1` 不进新 event）

---

## Phase 3 — 前端改造

### 3.1 SystemDiagnosticsPage.tsx 改造为"巡检首页"
**布局：**
```
┌──────────────────────────────────────────────┐
│  页头: 巡检  |  系统诊断  (TabBar 切换)        │
├──────────────────────────────────────────────┤
│  [Tab: 巡检模式]                               │
│  ┌───────────┬───────────┬───────────┐        │
│  │ 高危: 3   │ 中危: 7   │ 低危: 12  │        │
│  └───────────┴───────────┴───────────┘        │
│  ┌────────────── 8 域状态卡 ──────────────┐    │
│  │  2.1 磁盘  2.2 资源  2.3 服务  ...     │    │
│  └────────────────────────────────────────┘    │
│  [过去诊断记录列表] (保持原样)                  │
│  [巡检结果卡片槽] (InspectionResultsCard)      │
│    ┌─ 最近巡检运行列表 ──────────────────────┐ │
│    │  2026-06-01 每日巡检 ✓ 34/36 passed     │ │
│    │  2026-05-31 每日巡检 ✓ 33/36 passed     │ │
│    └─────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

**关键点：**
- 复用 `statusLabel` / `statusColor` / `CheckRow` / `StatusPill` 组件展示巡检结果
- 复用 `Recommendation` 面板展示巡检建议
- **不删除任何现有 diagnostics 代码**，通过 TabBar 切换

### 3.2 SystemStatusPage.tsx 改造为"巡检看板"
**布局：**
```
┌──────────────────────────────────────────────┐
│  系统健康卡片 (保留)  +  巡检摘要卡片 (新增)   │
│  ┌──────────┐  ┌────────────────────────────┐│
│  │ 系统状态  │  │  巡检状态                   ││
│  │ CPU: 23% │  │  上次巡检: 2026-06-01 08:00││
│  │ 内存: 45%│  │  通过率: 94% (34/36)      ││
│  │ ...      │  │  高危发现: 2 (未处置)      ││
│  └──────────┘  │  下次巡检: 2026-06-02 08:00││
│                └────────────────────────────┘│
│  [RecommendationPanel] (巡检建议融合)         │
└──────────────────────────────────────────────┘
```

### 3.3 ReportCenterPage.tsx — 追加巡检报告类型
```diff
+  inspection_daily: '每日巡检报告'
+  inspection_weekly: '每周巡检报告'
+  inspection_monthly: '每月巡检报告'
```
后端 REPORT_TYPES 对应追加，生成逻辑走 `_payload_for_report` 分支 `if report_type.startswith("inspection_")`

---

## Phase 4 — AI Agent 集成

### 4.1 新增 MCP Tool: `ops.run_inspection`
- 路由: `ops://run-inspection`
- 只读执行一次巡检，返回 JSON 结果摘要
- streams: `inspection.progress` (check-by-check)

### 4.2 新增 Agent Prompt
在 `triage` agent 的 system prompt 尾部追加：
```
你还可以使用 ops.run_inspection 安全巡检工具对系统做只读巡检。
巡检覆盖 8 个域：磁盘水位、系统资源、进程端口、日志异常、接口审计、备份、证书、运营专项。
巡检结果为 JSON，包含每个域的状态、发现项和建议。
```

---

## Phase 5 — 验证门禁
### 5.1 每次变更后运行
```bash
python -m compileall -q app
pytest tests -q --tb=short
cd frontend && npm run typecheck && npm run build
```
### 5.2 新增测试
- `tests/test_inspection_policies.py` — CRUD 策略
- `tests/test_inspection_runner.py` — 白名单校验 + SSH 模拟
- `tests/test_inspection_engine.py` — 调度、去重折叠
- `tests/test_inspection_api.py` — API 集成

---

## 提交计划 (PR 粒度)

| PR | 内容 | 文件数 | 测试 |
|----|------|--------|------|
| P0-1 | Phase 1 数据模型 + migrate | ~3 | 1 |
| P0-2 | Phase 2.1 SSH Runner + 白名单 | ~2 | 1 |
| P0-3 | Phase 2.4 Inspection API + 2.2 Engine | ~4 | 2 |
| P0-4 | Phase 3.1 TabBar + InspectionResultsCard | ~2 | 0 |
| P1-1 | Phase 3.2 SystemStatus 看板 | ~1 | 0 |
| P1-2 | Phase 3.3 ReportCenter + backend types | ~2 | 1 |
| P1-3 | Phase 4 AI Agent + MCP tool | ~3 | 1 |
| P2 | Phase 2.3 8 domain checks + 2.5 scheduler | ~3 | 2 |
| P3 | 存量巡检域基线比对、审计增强 | ~3 | 1 |

---

## 不做的事
- 不创建独立的前端 `inspection/` 目录
- 不新增独立前端路由（不走新页面）
- 不改现有 `build_diagnostics` 函数签名
- 不改现有 `system_router` 的 `/diagnostics` 路径（但新增 `/api/v2/inspection/` 命名空间）
- 不引入 websocket（巡检进度走轮询）
- 不上风险未知的命令到白名单

## 参考
- 《项目安全巡检方案》8 域 3 频次 3 风险等级闭环处置流
- SSH 命令速查表：见方案附件
- 巡检池设计原则：evidence_hash 去重、occurrence_count 折叠、inspection_runs 记录执行元信息

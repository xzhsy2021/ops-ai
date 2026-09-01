# Agent 更换 / 接入操作指南

> 2026-09-01 · 适用 OPS Command Center v2.1.x + Agent Context Layer（Phase 1-4 已上线）。
> 任何 MCP 客户端（zeroclaw / openclaw / qclaw 或其他自研 agent）接入 OPS 时，操作人员按本指南执行。
> 背景与设计：`docs/agent-integration-abstraction-layer.md`

## 核心认知（为什么换 agent 很便宜）

OPS 侧**不含任何 agent 专属业务事实**。服务名、房间绑定、审批人、流程编排、
踩坑教训全部在 OPS 数据库与 Agent Context Layer 中，由 `ops.integration.get_context_pack`
按需提供给任何 agent。因此换 agent 的全部工作 = **接入配置 + 元指令文件**，
不需要迁移任何业务知识。

```
新 agent 接入成本清单
├── MCP 挂载（分钟级）        ← 本文 §1
├── 元指令版 AGENTS.md        ← 本文 §2（模板即抄即用）
├── 宿主工具审批门白名单       ← 本文 §3（第九轮踩坑已机制化）
├── 会话启动拉 pack 自动对齐   ← 自动（教训/流程/事实随 pack 到达）
└── (可选) 审批催办 cron       ← 本文 §5
```

## §1 MCP 挂载（所有 agent 相同）

OPS 暴露标准 HTTP MCP 端点，任何 MCP 客户端零改造接入：

```
URL:       http://127.0.0.1:8000/api/v2/mcp
认证:      Bearer <tool_token>
```

**Token 签发**（操作人员在 OPS Web → Tool Tokens 页面，或 API）：

- 新 agent 一律用通用模板 `claw-mcp`（`qclaw-mcp` 为历史遗留 key，勿再用）
- scopes 仅需 `ops:read`：审批链路的安全门是一次性审批短码（15 分钟、房间+
  事件+摘要绑定），不需要给 agent 任何写 scope——OPS 后端用内部凭据执行
  实际部署，agent 永远拿不到 `deploy:execute` / `package:write` / `db:write`
- 若该 agent 需要 `ops.integration.save_lesson`（教训回写，推荐），token 需
  `ops:write` scope；接入层工具本身 risk=low、默认 pending 确认流防污染

**openclaw 家族挂载样例**（zeroclaw/qclaw/openclaw 同构，workspace 的
`config.toml` 或 MCP patch）：

```json
{
  "name": "ops-ai-http",
  "transport": "http",
  "url": "http://127.0.0.1:8000/api/v2/mcp",
  "headers": {"Authorization": "Bearer <token>"}
}
```

## §2 元指令版 AGENTS.md（每个 agent 一份，即抄即用）

在新 agent 的 workspace 根目录创建 `AGENTS.md`，内容只保留元指令——
**不要把任何 OPS 业务事实（服务名/房间/审批人/流程步骤）抄进去**，
那些内容会腐化（八轮故障的根源），一律让 agent 从 pack 实时拉取：

```markdown
# AGENTS.md — <agent 名>

## OPS 接入（唯一权威源）

1. **会话启动**：调用 `ops-ai-http__ops_integration_get_context_pack`
   （`agent_name=<agent 名>`、`channel=matrix`）。服务名等一切配置事实
   以返回的 facts 区块为准——本文件与你的记忆中的服务名一律不作数。
2. **按任务取流程**：调用 `ops-ai-http__ops_integration_get_flow_guide`
   （`flow_id=…`，跨系统发版传 `system_name=…` 获取实例化编排）。
3. **执行中**：每个工具返回的 `next_step` 字段是权威指令，优先级高于
   本文件与你的记忆。
4. **冲突裁决**：pack / flow guide / next_step 与本地记忆冲突时，以 OPS
   返回为准。
5. **踩坑回写**：失败或发现新坑时调用 `ops-ai-http__ops_integration_save_lesson`
   （pattern/guidance/evidence），不要静默吞掉。

## 固化原则（不变量）
- 先调用再下结论——未调用 OPS 就宣称"无法/会被拒"是已证伪的行为模式
- resolve 成功后同一轮内立即 prepare_plan；审批人批准后立即 execute_plan
  （4 参数：plan_id/short_code/room_id/approver_matrix_id）
- 拉取与部署必须一个计划一次审批；失败计划是终态，需用户重新触发
- [XXXXXX] approve 短码是宿主工具审批门，与 OPS 发版审批（中文完整短语
  「批准 <动作> <system>@<env> <指纹8>」）是两套体系，勿混淆

## 环境事实（仅宿主相关）
- 附件下载路径见消息 `[Document: 文件名] 路径` marker（openclaw 家族约定）
- <其他宿主特有环境事实，如附件目录、安全策略限制>
```

> 同步维护 `MEMORY.md` 头部：只写「旧记忆只作线索不作结论，冲突以 pack 为准」。

## §3 宿主工具审批门白名单（关键——第九轮故障点）

openclaw 家族 agent 默认对每个**不在 auto_approve 白名单**的 MCP 工具调用弹
`[XXXXXX] approve` 审批短码。新接入的工具若不加入白名单，agent 会在会话中被
反复打断，甚至把宿主短码误当 OPS 审批短语。

操作（以 zeroclaw 为例，`C:\Users\admin\.zeroclaw\config.toml`）：

```toml
auto_approve = [
  # ...既有 120+ 工具...
  "ops-ai-http__ops_integration_get_context_pack",
  "ops-ai-http__ops_integration_get_flow_guide",
  "ops-ai-http__ops_integration_get_heartbeat_ops",
  "ops-ai-http__ops_integration_save_lesson",
]
```

改完**必须重启 daemon**（指令文件与 config 都只在会话启动时读取）。

其他宿主（非 openclaw 家族）若有等价的工具审批机制，同样把四个
`ops_integration_*` 工具加入放行名单；没有审批门的宿主跳过本步。

### §3.1 放行粒度模式：任务链 vs 执行点（2026-09-01 追加）

宿主审批门按**单次工具调用**弹审批——一个任务过几个工具就弹几次。用户
一句「查一下数据库」曾连弹 5 次审批（连接→会话→结构→查询→关闭）。
放行策略分两层：

| 层 | 工具类型 | 处置 | 理由 |
|---|---|---|---|
| **管道层**（放行） | 列连接/开会话/看结构/开表/关会话 | 加入 auto_approve | 零副作用，只读元数据，放行后任务不再被无关环节打断 |
| **执行层**（留门） | 直连生产的执行类（dbx execute_query / execute_and_show / execute_redis_command） | 保留审批门 | 后面直连量化线上库，无 OPS 风控兜底（WHERE 约束/DDL 拦截全无） |

效果：一句话任务 = **恰好 1 次审批**（批任务里唯一有副作用的执行点），
与发版「一次拉取+部署 = 一次审批」同构。

判定法则：**该工具失败时是否可能已经改了外部状态？** 不会 → 管道层；
会 → 执行层。安全取舍优先于审批体验——放行面过大 = 生产库裸奔。

注意宿主命名转换：config 里 MCP 工具名的 `.` 转为 `_`（如
`ops.integration.get_context_pack` → `ops_integration_get_context_pack`；
dbx server 的 `dbx_list_connections` → `dbx__dbx_list_connections`，
双层下划线）。写白名单用宿主形式，不要照抄 MCP 原名。

## §4 验证清单（接入后逐项确认）

| # | 检查项 | 方法 | 通过标准 |
|---|---|---|---|
| 1 | MCP 连通 | agent 会话内任调一个只读工具（如 `ops_list_systems`） | 正常返回数据 |
| 2 | pack 拉取 | 会话启动自动触发（元指令第 1 条） | facts 含真实系统/服务/房间/审批人 |
| 3 | 审批门放行 | 观察 agent 调 `get_context_pack` 是否再弹 `[XXXXXX]` | 不弹（白名单生效） |
| 4 | 流程编排 | agent 按任务取 flow guide（可让用户发一条测试消息） | steps_template 已实例化真实服务名 |
| 5 | 端到端发版 | 用户在绑定房间发 附件+指令 一条消息 | resolve→prepare→批准→execute 全链路 |
| 6 | 教训生效 | 观察 agent 对 superseded 教训的态度 | 不再引用"无法生成摘要"等旧结论 |

若第 5 项失败：先查 OPS 审计（`audit_records` 表实际工具调用序列），再查
宿主会话 DB；agent 侧报告的"阻塞原因"在未实际调用工具时不可信（八轮教训：
4/5/6 轮都是 agent 未调用就编造阻塞）。

## §5 可选：审批催办 cron（heartbeat）

让 agent 每 N 分钟拉一次 OPS 巡检面，对待审批/即将超时的计划自动催办：

```
zeroclaw.exe cron add '*/5 * * * *' \
  '审批催办巡检：调用 ops-ai-http__ops_integration_get_heartbeat_ops（无参数）。
   has_work=false 时什么都不做（不要发任何消息）。has_work=true 时按 next_step
   指令催办。' --agent <agent别名> --prompt --tz Asia/Shanghai
```

无待审批时该调用是轻量响应（`has_work=false`），成本可忽略。过期计划在每次
心跳时自动转 EXPIRED 终态（数据卫生内置）。

## §6 OPS 侧无需做的事（明确）

- **不需要**为新 agent 改任何 OPS 代码或配置（token 之外）
- **不需要**迁移服务/房间/审批人配置——pack 从 DB 实时组装
- **不需要**重新教流程——flow guide 机器可读且已实例化
- **不需要**同步踩坑经验——教训库随 pack 自动到达（11 条实战教训在线）
- **不需要**为不同系统发版做适配——flow guide 传 `system_name` 即渲染该系统真实配置

## §7 故障速查（换 agent 后常见问题）

| 症状 | 根因 | 处置 |
|---|---|---|
| agent 每次调用工具都弹 `[短码] approve` | auto_approve 白名单缺新工具 | §3 加白名单 + 重启 daemon |
| 一个任务连弹多次审批（如 dbx 查询 5 连弹） | 宿主按工具调用粒度弹审批，任务链上的管道工具全在门内 | §3.1 分层放行：管道层进白名单，执行层留门 → 一任务一次审批 |
| agent 停在"票据已签发"不继续 | 旧指令文件残留中间步骤语义 | 换 §2 元指令版 AGENTS.md |
| agent 说"缺少 content_sha256 无法签票" | 旧记忆/旧指令误导 | OPS 已全自动；pack lessons 里 L001/L002 标 superseded，确认 agent 拉了 pack |
| agent 拿宿主短码映射 OPS 计划 | 两种审批体系混淆 | §2 固化原则第 4 条；L009 教训 |
| steps_template 出现 `<SERVICE>` 原样占位符 | 该系统在 OPS 无前端服务（服务表无匹配） | 核对服务配置后重试——这是防臆测的保护，不是故障 |
| 催办 cron 无消息 | has_work=false 正常沉默 | 有待审批计划时自然触发；可人为建测试计划验证 |
| file_write 被拒（报告附件发不出） | file_write/file_edit 不在白名单 | 加入白名单（仍受 workspace_only/forbidden_paths 约束） |

## 附：当前生产环境基准（2026-09-01）

- OPS：port 8000，Agent Context Layer 全量上线（Phase 1-4）
- 接入层工具：`ops.integration.get_context_pack / get_flow_guide /
  get_heartbeat_ops / save_lesson`
- 在用 agent：zeroclaw（workspace `D:\zeroclaw\workspace`，daemon
  `zeroclaw.exe daemon --log-level debug`），cron 306f8d8b 审批催办 */5min
- 流程：frontend-release（参数化，default_system=crypto-trader）/
  service-restart / package-pull-release
- 教训库：内置 9 条 + 生产库回写通道（`scripts/lesson_admin.py` 管理）

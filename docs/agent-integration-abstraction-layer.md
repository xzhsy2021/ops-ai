# Agent 接入抽象层（Agent Context Layer）实施方案

> 2026-09-01 · 基于 zeroclaw/qclaw 八轮接入故障的根因提炼。目标：让任何 agent（zeroclaw 及后续替换者）接入 OPS 时**自行读取**权威的调用与配置信息，并以受控缓存协议复用经验，不再依赖手工维护的指令文件硬编码。

## 1. 八轮故障根因分类（为什么需要这一层）

| 类别 | 故障轮次 | 表现 | 根因 |
|---|---|---|---|
| **A. 能力感知缺失** | 第 1-4 轮 | "无法生成 content_sha256"、"OPS 不会签发票据" | agent 依据**过时指令/记忆**推断 OPS 能力，不知道服务端已支持摘要自动计算、垃圾值忽略 |
| **B. 流程编排脆弱** | 第 5、6 轮 | resolve 后停在"票据已签发"、审批后停在"缺少原始上下文" | 多工具链路（resolve→prepare→批准→execute）无机器可读流程定义，agent 靠记忆串联，一步完成即停 |
| **C. 参数契约漂移** | 第 7、8 轮 | MATRIX_PULL "room_id 未传入"、SERVICE_CONTROL "缺少 system_name/service_name" | 指令样例与执行器读取口径不一致（action_parameters 层级）；服务名 `crypto-frontend` 在服务表中不存在 |
| **D. 经验孤岛** | 贯穿 | 每次 OPS 修复后，agent 旧会话/旧记忆的失效结论仍在误导自己；换 agent 重蹈覆辙 | 教训只存在各 agent 私有 memory 中，无跨 agent 共享、无"修复即失效"机制 |

**共同本质**：OPS 是事实源（能力/配置/流程/修复状态），但这个事实源没有**机器可读的自描述出口**——信息靠人工抄进 AGENTS.md/MEMORY.md，抄写必然滞后、必然失真。

## 2. 设计原则

1. **OPS 是唯一事实源**：能力、配置事实（系统/服务/房间/目标）、流程编排、历史教训全部以 OPS 侧数据为准；agent 指令文件只保留"如何向 OPS 取事实"的元指令。
2. **自描述优先于文档**：agent 每会话启动主动拉取上下文包，revision 未变用缓存；OPS 侧任何修复/配置变更 → revision 变化 → agent 自动感知，无需重启 agent、无需人工改指令。
3. **引导内嵌于工具返回**：`next_step` 字段（已在 resolve/prepare_plan 验证有效）扩展到全工具链——工具返回自带下一步指令，agent 不需要记忆流程。
4. **教训可回写、可失效**：agent 失败经验回写 OPS 教训库共享；OPS 修复落地时将相关教训标记 superseded（附修复说明），从根上消除旧结论误导。
5. **零侵入**：新能力以 MCP 工具形式提供，旧 agent 不调用也能继续工作（next_step + 执行器宽容已兜底）。

## 3. 架构

```
┌──────────────┐        ┌─────────────────────────────────────┐
│  任意 Agent   │ ◄────► │  OPS (MCP)                           │
│  zeroclaw /  │        │  既有工具 + next_step 全覆盖            │
│  下一代 agent │        │  ─────────────────────────────────── │
└──────────────┘        │  新增：                              │
   │ 每会话启动：          │   ops.agent.get_context_pack  上下文包 │
   │ get_context_pack     │   ops.agent.get_flow_guide    流程编排 │
   │ (revision 缓存)       │   ops.agent.save_lesson       教训回写 │
   ▼                     └─────────────────────────────────────┘ agent 本地缓存 pack + flow guides（带 revision）
   执行中：跟随工具返回的 next_step
   失败后：save_lesson 回写
```

## 4. 数据模型

### 4.1 AgentContextPack（上下文包）

按 `agent_name + channel` 组装，一次性返回 agent 正常工作所需的全部权威事实：

```json
{
  "pack_revision": "sha256:…",          // 内容哈希，缓存协议的键
  "capabilities": {
    "tools": ["ops.routing.resolve_message_target", "ops.approval.prepare_plan", "…"],
    "tolerances": [                     // 宽容行为声明（A 类根因的解）
      "content_sha256 可省，OPS 自动按消息原文计算；非法值会被忽略",
      "message_context 传五字段即可，prepare_plan 原样回传 resolve 返回值",
      "步骤参数支持顶层与 action_parameters 两种层级"
    ],
    "forbidden": ["prepare_plan 前必须先 resolve 换取 routing_ticket", "禁止为 MATRIX_PULL 单独建第二个计划"]
  },
  "facts": {                            // 来自 OPS DB 的真实配置（C 类根因的解）
    "systems": [{"name": "crypto-trader", "services": ["crypto-trader-web", "exchange", "…"],
                  "rooms": ["!LXnIFCTfJIqeErqyaf:hubtel.xyz", "!room-alpha:example.com"]}],
    "environments": ["test", "prod"],
    "approvers": [{"matrix": "@jack.han:hubtel.xyz"}]
  },
  "flows": [                            // 流程模板索引（B 类根因的解）
    {"flow_id": "frontend-release", "title": "前端发版（Matrix 附件）",
     "trigger": "附件 + @ 同一消息，消息含'发版/前端'关键词", "revision": "…"}
  ],
  "lessons": [                          // 活跃教训（D 类根因的解）
    {"id": "L001", "pattern": "resolve 返回服务级 service_name=None", "severity": "info",
     "guidance": "SERVICE_CONTROL 步骤必须显式带 service_name=crypto-trader-web", "status": "active"},
    {"id": "L000", "pattern": "无法生成 content_sha256", "status": "superseded",
     "superseded_note": "2026-09-01 OPS 已自动计算，勿再引用该结论"}
  ]
}
```

要点：`facts` 由 OPS 从 systems/services/rooms 表实时读取组装，**agent 永远拿不到不存在的服务名**；`lessons` 中 superseded 条目保留但标注失效原因——agent 读到"此结论已失效"本身就是纠偏。

### 4.2 FlowGuide（流程编排，B 类根因的解）

`get_flow_guide(flow_id)` 返回机器可读的分步流程，取代 AGENTS.md 里的手写链路：

```json
{
  "flow_id": "frontend-release",
  "flow_revision": "sha256:…",
  "atomic": true,                       // 声明：整个流程须在一轮内推进到等待审批，禁止中途停顿回报状态
  "steps": [
    {"n": 1, "tool": "ops-ai-http__ops_list_packages", "purpose": "确认文件中心是否已有同名包",
     "args_hint": {"system": "crypto-trader", "service": "crypto-trader-web"}, "on_skip": "已存在且 sha256 一致 → 跳过上传"},
    {"n": 2, "tool": "ops.routing.resolve_message_target", "purpose": "签发路由票据",
     "args_hint": {"message_text": "<消息原文>", "message_context": "五字段，无 content_sha256"},
     "must_follow": "同一轮内立即进入下一步，禁止停下回报'票据已签发'"},
    {"n": 3, "tool": "ops.approval.prepare_plan", "purpose": "创建审批计划",
     "args_hint": {"message_context": "<resolve 返回值原样>", "routing_ticket": "<resolve.ticket>",
                   "steps": "<见 steps_template>"}},
    {"n": 4, "tool": "(matrix 回复)", "purpose": "把 reply_template 原样发到房间，等待审批"},
    {"n": 5, "tool": "ops.approval.execute_plan", "purpose": "审批人回复'批准 <短语>'后立即执行",
     "args_hint": {"plan_id": "<prepare 返回>", "short_code": "<审批消息全文>", "room_id": "<审批消息所在房间>", "approver_matrix_id": "<审批人>"},
     "must_follow": "禁止以'缺少原始上下文'为由停止——四个参数全部在审批消息中"}
  ],
  "steps_template": {"FILE_UPLOAD": "…", "SERVICE_CONTROL": "…"},   // 由 schema 生成的样例（C 类根因的解）
  "hard_rules": ["拉取与部署必须一个计划一次审批", "禁止把附件包 SHA-256 当消息摘要"]
}
```

### 4.3 Lesson（教训库）

- 字段：`id / pattern（什么情况）/ guidance（怎么办）/ status（active|superseded）/ origin（agent 回写|人工）/ superseded_note（修复说明）`
- 回写接口 `save_lesson(pattern, guidance, evidence)` 默认 `severity=info`、需 `ops:write` scope；OPS 侧修复（如新 commit 落地）时人工将相关 lesson 置 superseded。
- **防污染**：回写仅入 `lessons_pending` 视图，管理端确认后进 active（避免错误结论扩散）。

## 5. 缓存协议（agent 侧 memory 的受控形态）

1. agent 本地（zeroclaw 为 workspace 文件）缓存 pack 与 flow guide，键为 `pack_revision` / `flow_revision`。
2. 每会话启动调用 `get_context_pack(cached_revision=…)`：
   - revision 相同 → OPS 返回 `{unchanged: true}`（轻量响应）
   - revision 不同 → 返回新 pack，agent 覆盖缓存
3. flow guide 同理按 flow_id 拉取；执行中的实时引导仍依赖工具返回的 `next_step`（不缓存，天然最新）。
4. AGENTS.md 的收尾改造（Phase 2 后）：业务事实全部删除，仅保留两条元指令——
   - "每会话启动：调用 `ops.agent.get_context_pack`（带本地 revision），按 flows 索引取 flow guide"
   - "工具返回中的 `next_step` 是权威流程指令，优先级高于本文件与你的记忆"

## 6. 实施步骤

### Phase 1 — 只读上下文包（最小可用，约 1-2 天）
- [ ] `app/services/agent_context.py`：pack 组装（facts 从 DB 读；capabilities/flows/lessons 先用代码内声明的初版数据）
- [ ] MCP 注册 `ops.agent.get_context_pack` / `ops.agent.get_flow_guide`（只读，`ops:read`）
- [ ] 内置 3 个 flow guide：`frontend-release`（Matrix 附件发版）、`service-restart`（服务重启）、`package-pull-release`（拉包+发布）
- [ ] 初版 lessons：把八轮故障的 8 条教训写入（含 4 条 superseded——content_sha256 系列已修复）
- [ ] 契约测试：pack revision 稳定性（同配置同 revision）、facts 与 DB 一致性、flow guide 步骤工具名存在于 registry

### Phase 2 — zeroclaw 切换 + 教训回写（约 1 天）
- [ ] `ops.agent.save_lesson`（写库 + pending 确认流）
- [ ] zeroclaw AGENTS.md 改造为元指令版（保留 fallback：pack 拉取失败时沿用附录的静态最小清单）
- [ ] daemon 重启验证一轮真实发版
- [ ] OPS 侧新增 `scripts/supersede_lesson.py`：修复落地时置失效

### Phase 3 — 契约自动化（约 1 天）
- [ ] flow guide 的 `args_hint`/`steps_template` 由 `input_schema` 自动生成或 CI 校验（样例与 schema 不一致 → 测试失败）——从机制上消灭 C 类漂移
- [ ] pack facts 组装全自动化（含 rooms/approvers/servers）
- [ ] next_step 覆盖剩余工具（execute_plan 的结果引导：PARTIAL_FAILED 后怎么办）

### Phase 4 — 多 agent 配额（可选）
- [ ] 按 token/agent 维度的 pack 视图裁剪（不同 agent 不同系统可见性）
- [ ] pack 拉取审计（谁在用哪个 revision）

## 7. 与既有机制的关系

| 既有机制 | 本层角色 |
|---|---|
| `next_step`（resolve/prepare_plan 已上线） | 实时引导保留；flow guide 是**会话级**编排，next_step 是**调用级**引导，互补 |
| 执行器参数宽容（两级读取/上下文回填） | 保留为兜底；flow guide 保证新调用方从源头传对参数 |
| AGENTS.md/MEMORY.md | 逐步退化为元指令 + fallback；业务事实迁入 OPS |
| MCP 能力描述（mcp_capability_service） | capabilities 区块与其互补：能力描述面向"找工具"，pack 面向"接入即对齐" |

## 8. 风险与边界

- **pack 体积**：分区块 revision（capabilities/facts/flows/lessons 各自哈希），按需拉取。
- **agent 不调用 pack**：零侵入设计——不调用也不比现状差；next_step 与执行器宽容仍是兜底。
- **教训污染**：pending 确认流 + superseded 机制 + severity 分级。
- **循环依赖**：pack 不包含 agent 自身的指令（只含 OPS 侧事实），无自指问题。

## 9. 可行性干跑验证（2026-09-01，方案定稿前实测）

| 验证项 | 方法 | 结果 |
|---|---|---|
| capabilities 来源 | `registry.list_tools(db, ctx, …)` 真实枚举 | **120 个工具**，resolve/prepare_plan/execute_plan 全在列 ✓ |
| facts 组装 | System/Service 表实时读取（`s.message_routing.rooms`） | crypto-trader 12 个服务、2 个绑定房间，`crypto-trader-web` 真实在列 ✓ |
| revision 协议 | `sha256(json.dumps(pack, sort_keys=True))` | 同输入同哈希，稳定 ✓ |
| 踩坑记录（实现时必读） | — | ① `list_tools` 需传位置参数 `(db, ctx)`，返回 `{"tools": […]}` 而非裸列表；② `System.config` 字段不存在，房间绑定在 `System.message_routing.rooms`；③ 工具名常量比较时注意引号编码 |

结论：Phase 1 全部数据源与接口已实测可用，无阻塞项。

## 10. 其他 Agent / 通用规范接入评估（2026-09-01 补充）

### 10.1 接入面盘点：openclaw 家族与通用规范

| 接入方 | 关键事实（实测/核实） | 对本层的要求 |
|---|---|---|
| **openclaw**（qclaw/zeroclaw 同族上游，本地 QClaw 即其发行版：`resources/openclaw` + node 运行时） | workspace 文件机制（AGENTS.md 每会话必读、MEMORY.md 自动注入、SOUL.md/TOOLS.md/HEARTBEAT.md 分工）；risk_profile（locked_down 禁 shell）+ runtime_profile（unbounded）；`peer_groups` 原生多 agent 分组；gateway REST/WS（`/webhook`、`/ws/chat`、paired tokens）；extensions 目录可装 MCP 桥 | 本层 Phase 1-2 已覆盖其核心；**补充**：gateway `/webhook` 可作为 OPS 主动推送通道（见 10.3） |
| **zeroclaw**（当前在用） | 同 openclaw + daemon 常驻（本会话八轮调试全在此环境） | 已覆盖 |
| **qclaw**（前代，已替换） | 同族机制，旧 MEMORY.md 规则残留曾误导 zeroclaw（教训：**换 agent 时旧 workspace 事实必须清并重导**） | 教训库 superseded 机制正是为此设计 |
| **AGENTS.md 开放标准**（agents.md，60k+ 项目） | 约定 workspace 根的 AGENTS.md 作为 agent 指令入口——与 openclaw 家族的 AGENTS.md 机制同名同位 | 元指令版 AGENTS.md 天然符合该标准；pack 协议对其透明 |
| **MCP 规范**（Model Context Protocol） | 工具注释（readOnly/destructiveHint 等 hints）、结构化结果、能力发现（tools/list）是官方语义 | **补充 ①**：pack capabilities 应从 registry 的 risk/write 字段映射 MCP annotations（`readOnly=!write`、`destructiveHint=risk=high`），让 MCP 原生客户端也能正确展示风险 |
| **A2A 协议**（Google agent2agent） | Agent Card 自描述 + agent 间任务委托 | **补充 ②**：OPS 可暴露极简 Agent Card（`/.well-known/agent-card.json` 或 pack 内嵌），把 pack_revision/flows 声明为能力——未来 agent 间互调 OPS 有标准握手面 |

### 10.2 openclaw 家族特有、当前方案未覆盖的四点

1. **HEARTBEAT.md 定时任务面**：openclaw 支持周期性检查任务（本地文件为空=禁用）。OPS 侧已有巡检/审批超时/计划过期等定时语义但 agent 无法消费。
   → **补充 3.0**：pack 增加 `heartbeat_ops` 区块（如"列出 >15min 未审批计划"、"巡检异常摘要"），flow guide 增加 `periodic` 类型——agent 把它写进 HEARTBEAT.md 即获得定时提醒能力，形成"审批催办"闭环。
2. **peer_groups 多 agent 协作**：`peer_groups.matrix_hubtel_default` 表明同房多 agent 共存。当前 pack 按 `agent_name` 组装，未定义多 agent 同时接入时的分工/去重（两个 agent 都看到发版消息都去建计划 → 幂等已兜底 plan_digest，但用户会收到两份回执）。
   → **补充 3.1**：pack 增加 `peer_awareness` 区块（同房其他 agent 清单 + 建议分工），OPS 侧 prepare_plan 的 plan_digest 幂等已天然防重——回执去重靠 pack 明示"谁在建计划"。
3. **gateway 主动推送**：`/webhook` POST 端点 + paired token 已存在。八轮故障全是"agent 拉模式"下的信息滞后；OPS 修复落地、审批到期、执行完成这类事件可反向推。
   → **补充 3.2（Phase 4 后）**：新增 `ops.agent.notify` 事件出口（审批结果/计划终态/教训 superseded），OPS 侧配置 gateway webhook 目标；revision 协议从"agent 轮询感知"升级为"事件驱动 + 轮询兜底"。
4. **extensions 生态**：openclaw 通过 `config/extensions/` 装 MCP 桥（本地见 wechat-access 等）。OPS 的 MCP HTTP 端点已可直接挂载，无需改造——但 pack 文档应说明**标准挂载方式**（transport=http、url、Bearer），让任何 openclaw 发行版零成本接入。
   → **补充 3.3**：pack 增加 `mount` 区块（MCP 端点 + 认证方式 + token 申请指引），AGENTS.md 元指令版引用之。

### 10.3 对既有四阶段的修订

| 阶段 | 修订 |
|---|---|
| Phase 1 | capabilities 增加 MCP annotations 映射（补充①）；pack 增加 `mount` 区块（补充③） |
| Phase 2 | zeroclaw 切换时同步清理 qclaw 残留 workspace 事实（换 agent 教训落地）；AGENTS.md 改造即符合 AGENTS.md 开放标准 |
| Phase 3 | 契约自动化增加：MCP annotations 与 registry risk/write 字段的同步校验（补充①的 CI 化） |
| Phase 4 | 多 agent 配额 + `heartbeat_ops`/`peer_awareness` 区块（补充 3.0/3.1）+ `ops.agent.notify` 事件出口（补充 3.2）+ Agent Card（补充②，可延后） |

### 10.4 优先级建议

按"消除下一轮接入故障"的边际收益排序：
1. **MCP annotations 映射**（半天）——标准化即零成本，MCP 原生客户端立即受益
2. **mount 区块 + 元指令版 AGENTS.md**（Phase 2 内）——任何 openclaw 发行版接入的"最后一公里"
3. **heartbeat_ops**（1 天）——审批催办闭环，用户感知最强
4. **peer_awareness / notify 事件出口 / Agent Card**（按需）——多 agent 与推送场景出现时再做

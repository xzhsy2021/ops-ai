# 代码审查报告 · 2026-06-04

- **审查范围**：当前工作树相对 `HEAD` 的全部改动（`git diff HEAD`）
- **改动规模**：59 个文件，+8048 / -2042，diff 共 12104 行
- **审查模式**：extra-high effort（偏 recall，宁可报多不漏）
- **审查方法**：原计划 9 角度并行 finder fan-out 因 API 用量上限/上游错误失败，改为主上下文逐文件直接审查；重点覆盖新增 tool adapter、注册/策略/审计层、db_query_export、inspection_center 与高影响前端
- **未完全覆盖**：`app/services/inspection_center.py`（3184 行 diff）与 `app/services/report_center.py`（1040 行 diff）只做了抽样核查；`app/services/tool_adapters/server_tools.py`（+989）仅抽查；前端 `ConnectionsTab.tsx`、`ToolTokenPanel.tsx` 抽查；建议后续对这几处补一次专项 sweep

---

## 严重度分级

| 级别 | 含义 | 处理建议 |
|---|---|---|
| CRITICAL | 已发布即触发的阻塞性 bug | 合并前必须修 |
| HIGH | 在常见路径下必现，或破坏关键不变式 | 合并前必须修 |
| MEDIUM | 触发条件偏窄，但用户可达 | 合并前修，或登记跟踪 |
| LOW | 半成品/清理项，对功能正确性影响小 | 下个迭代修 |

---

## 1. [CRITICAL] `SshKey` ORM 模型缺失，所有 SSH 密钥工具不可用

- **位置**：[app/services/tool_adapters/ssh_key_tools.py:22](app/services/tool_adapters/ssh_key_tools.py#L22) 起，5 个工具函数均含 `from app.db.models import SshKey`
- **症状**：5 个工具 (`ops.list_ssh_keys` / `ops.get_ssh_key` / `ops.create_ssh_key` / `ops.update_ssh_key` / `ops.delete_ssh_key`) 在 `ensure_builtin_registered` 加载时**注册成功**（import 写在函数体内），但任何客户端调用都会立刻抛 `ImportError: cannot import name 'SshKey' from 'app.db.models'`
- **证据**：`Grep ^class SshKey` 仅命中 `app/api/maintenance.py:298` 的 `SshKeyUpload`（Pydantic schema，非 ORM 模型）；`app/db/models.py` 内没有任何 `class SshKey(Base)`
- **失败场景**：MCP/AI 调用方读 `ops://capabilities` 看到工具可见 → 实际调用 100% 失败 → 误以为是配置/权限问题
- **修复建议**：要么在 `app/db/models.py` 新建 SshKey 表 + 加密落库（参考 #11），要么删除整个 `ssh_key_tools.py` 直到模型就绪

## 2. [CRITICAL] `InventoryReadService` 缺 `list_pipelines` / `get_pipeline`

- **位置**：[app/services/tool_adapters/pipeline_tools.py:28](app/services/tool_adapters/pipeline_tools.py#L28)、[:58](app/services/tool_adapters/pipeline_tools.py#L58)
- **症状**：`ops.list_pipelines` / `ops.get_pipeline` 调用即抛 `AttributeError: 'InventoryReadService' object has no attribute 'list_pipelines'`
- **证据**：`app/domain/inventory/services.py` 仅有 `list_servers / get_server / list_systems / get_system / list_groups / get_service / get_variable_inheritance / get_servers_for_system` 等方法
- **修复建议**：要么在 `InventoryReadService` 中补 `list_pipelines / get_pipeline`，要么改走与写工具一致的 `config_manager.load_config().get('pipelines', {})`

## 3. [HIGH] `html` 导出格式只声明未实现

- **位置**：[app/services/db_query_export.py:43](app/services/db_query_export.py#L43)（加入 `EXPORT_FORMATS`）、[:889](app/services/db_query_export.py#L889)（加入 media type）
- **症状**：`_write_export_file` (line 187-222) 没有 `if fmt == "html":` 分支；用户带 `fmt=html` 调用导出时，文件名/路径已生成，最后落到 fallthrough 抛 `HTTPException(400, "Unsupported export format: html")`
- **修复建议**：补 html writer，或从 `EXPORT_FORMATS` 移除并删除 media type 分支。注意 `report_center.py` 中**确实**实现了 `_inspection_html`，但那是巡检报告专用，不复用到 db_query_export

## 4. [HIGH] `db_type` enum 比 executor 支持的类型多

- **位置**：[app/services/tool_adapters/connection_tools.py:154](app/services/tool_adapters/connection_tools.py#L154)
- **症状**：input_schema 允许 `db_type ∈ {mysql, postgresql, sqlite, mongodb, redis}`，但 [app/maintenance/service.py:783](app/maintenance/service.py#L783) 的 `_connection_executor` 只在 `postgresql / mysql` 之间二选一
- **失败场景**：
  - 创建 `db_type=mongodb` 的连接 ✅
  - 调用 `ops.test_connection` → fallback 到 `MySQLExecutor` → `pymysql.connect(host=..., port=27017)` 失败时给出令人误解的 MySQL 协议错误
- **修复建议**：把 enum 临时收窄到 `{mysql, postgresql}`，或为其它类型补 Executor 子类

## 5. [HIGH] `load_config → mutate → save_config` 的丢失更新

- **位置**：[app/services/tool_adapters/pipeline_tools.py:93](app/services/tool_adapters/pipeline_tools.py#L93)（`create_pipeline_tool` / `update_pipeline_tool` / `delete_pipeline_tool`），以及 `app/services/tool_adapters/server_tools.py` 新增的 `create_environment / update_environment / delete_environment / create_server / ...` 大量经 `config_manager` 的写工具
- **机制**：[app/config/repository.py:166-167](app/config/repository.py#L166-L167) 的 `save_config` 会**显式 DELETE 不在新 dict 里的键**。两个并发调用者各自 `load_config()` → 在内存中加自己的 key → `save_config(...)`，后落盘的会把先落盘者的 key 一并删掉
- **失败场景**：并发 `ops.create_pipeline(A)` 与 `ops.create_pipeline(B)`：A 保存（含 A 不含 B）→ B 保存（含 B 不含 A）→ A 凭空消失
- **修复建议**：
  - 短期：在 `config_manager` 层加 `RLock` 串行化 load+save
  - 中期：把 pipeline / server / environment 等独立资源从大 config dict 拆出，走自己的 CRUD repository，避免整 dict 替换

## 6. [MEDIUM] `read_only` 默认值由 `False` 翻转为 `True`

- **位置**：[app/services/tool_policy.py:14](app/services/tool_policy.py#L14)
- **影响**：升级后未在 `config_kv.capability_server` 中显式覆盖该字段的部署，所有写类工具被 `enforce_tool_policy` 直接 403 `"Tool token does not allow write operations"`
- **修复建议**：在 `app/db/migrations/runner.py` 中显式写入 `read_only=False` 的迁移，或在 `UPGRADE.md` 中显著标注该行为变化与回滚配置

## 7. [MEDIUM] 审计脱敏正则把 `key` 当子串匹配，过度脱敏

- **位置**：[app/services/tool_audit.py:14](app/services/tool_audit.py#L14)
- **问题**：`re.compile(r"(password|passwd|pwd|secret|token|key|credential|auth_header|cookie|session)", re.IGNORECASE)` 会命中 `primary_key / foreign_key / table_key / license_key / keyword / monkey` 等常见非敏感字段名
- **后果**：审计日志中 `{"primary_key": "uuid-x"}` / `{"keyword": "search"}` 都会变成 `"***MASKED***"`，事故复盘时丢失关键定位信息
- **修复建议**：改为 `\b(...)\b` 词边界匹配，或把 `key` 换成 `(api[_-]?key|private[_-]?key|access[_-]?key|secret[_-]?key|ssh_key)` 等更精确的形式

## 8. [MEDIUM] `_remote_check` 的宽口 `except TypeError` 吞掉分析器内部异常

- **位置**：[app/services/inspection_center.py:81-83](app/services/inspection_center.py#L81-L83)
- **问题**：试图用 `try / except TypeError` 区分新旧签名（带不带 `thresholds`），但分析器 body 内任何 `int(None) / len(None) / a.b on None` 引发的 `TypeError` 都会触发 fallback，**静默丢弃 thresholds 参数**
- **后果**：用户在 InspectionItemConfig 配的阈值被静默忽略 → 仍以默认阈值判定 → 报告结论错误且难以察觉
- **修复建议**：
  - 用 `inspect.signature(analyze).parameters` 精确判断签名
  - 或仅对 `signature` 阶段的 TypeError 兜底，分析器 body 异常不在此处吞

## 9. [MEDIUM] `pipeline_tools.list_pipelines_tool` 在 `limit: null` 时崩溃

- **位置**：[app/services/tool_adapters/pipeline_tools.py:26](app/services/tool_adapters/pipeline_tools.py#L26)
- **代码**：`limit = min(int(args.get("limit", 100)), 500)`
- **问题**：JSON 中显式 `null` 让 `args.get("limit", 100)` 返回 `None` 而非默认值；`int(None)` 抛 `TypeError`
- **对照**：`connection_tools.py:100` 的 `args.get("limit") or 100` 才是正确写法
- **建议同步修复**：`ssh_key_tools.py:23`（同样模式）

## 10. [MEDIUM] `_all_builtin_rules` 把分类描述当成可执行命令

- **位置**：[app/services/inspection_center.py:4483](app/services/inspection_center.py#L4483)
- **代码**：`command = SERVER_RULE_COMMANDS.get(cat["code"], cat["description"])`
- **问题**：新增的 `MEMORY` 分类在 `SERVER_CATEGORIES` 注册了、`_server_check_specs` 也有专用 spec，但 `SERVER_RULE_COMMANDS` 字典中没有 MEMORY 条目；`.get(..., cat["description"])` 把 `"内存使用率、Swap 使用率与 OOM 风险"` 这一段中文判定要点当成 `rule_content` 暴露
- **后果**：用户 fork 该 builtin 规则后看到的“命令”不是 shell 可执行的，复制后会一头雾水
- **修复建议**：
  - 在 `SERVER_RULE_COMMANDS` 中补 MEMORY 命令
  - 或让 `_all_builtin_rules` 直接读 `_server_check_specs(...).command` 作为权威来源

## 11. [LOW] `ssh_key_tools.py` 假设的明文落库违反加密-at-rest 约定

- **位置**：[app/services/tool_adapters/ssh_key_tools.py:87](app/services/tool_adapters/ssh_key_tools.py#L87)
- **代码**：`row = SshKey(name=name, private_key=private_key, passphrase=passphrase or None)`
- **问题**：即使 SshKey 模型存在，私钥与口令也是**明文**写入；同项目 `DatabaseConnection` 使用 `ssh_key_content_encrypted` / `ssh_key_passphrase_encrypted` 字段 + `decrypt_secret` 工具链（见 `app/db/models.py:589-591`）
- **风险**：DB 备份/导出/任意 admin SELECT 都会泄露私钥
- **修复建议**：修 #1 时必须一并接入既有 `_encrypt_server_fields` / `decrypt_secret` 工具链

## 12. [LOW] `package.sh` / `package.ps1` 仍引用已删除的 `ARCHITECTURE.md`

- **位置**：[scripts/package.sh:51](scripts/package.sh#L51)、[scripts/package.ps1:46](scripts/package.ps1#L46)
- **影响**：`copy_path` 用 `[ -e ]` 测试静默跳过，**不会报错**，但发布包从此不再带 ARCHITECTURE.md 而无人提示
- **修复建议**：删除引用，或在新的 `docs/` 树里指向当前架构文档

---

## 复用 / 简化 / 效率方向（未在主表中列）

- `tool_audit.py:30` 序列化前先深拷贝整个 value 做 mask，再 `json.dumps` — 对大入参/返回值会双倍内存占用；可在序列化的 `default` 回调中按字段名 mask
- 多个新 adapter 重复实现 `_filter_items(items, keyword, limit)` 风格的过滤；`server_tools.py:_filter_items`、`connection_tools.py:list_connections_tool`、`pipeline_tools.py:list_pipelines_tool` 都各自写一遍，应抽出公共 helper
- `ToolTokenPanel.tsx` 内 `TOKEN_TEMPLATES`、`DANGEROUS_SCOPES`、`scopesToText / textToScopes` 是纯静态数据/纯函数，可移到独立 `tokenTemplates.ts`，便于在新建/编辑两个表单间复用
- `_all_builtin_rules` 已经在循环里调用 `_server_check_specs("", [cat["code"]])`，每次都新建整张表的 specs，O(N²)；可在循环外预构建 `{code: spec}` 字典

---

## 建议的合并门槛

- **阻塞合并**：#1、#2、#3、#4、#5
- **合并前需要 follow-up issue**：#6、#7、#8、#9、#10
- **下个迭代清理**：#11、#12 与上面的复用/效率项

合并 #1 与 #11 必须同步进行：仅修 #1 而不接入加密落库，会引入更严重的密钥明文落库问题。

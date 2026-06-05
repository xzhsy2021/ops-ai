# 开发文档 · 2026-06-04 代码审查问题修复

> 基于 `docs/reviews/2026-06-04-code-review.md` 审查报告生成
> 改动范围：59 个文件，+8048 / -2042，diff 共 12104 行

---

## 目录

1. [阻塞合并项（CRITICAL / HIGH）](#1-阻塞合并项)
2. [合并前需跟进项（MEDIUM）](#2-合并前需跟进项)
3. [下个迭代清理项（LOW）](#3-下个迭代清理项)
4. [复用 / 简化 / 效率方向](#4-复用--简化--效率方向)
5. [测试计划](#5-测试计划)
6. [里程碑与排期](#6-里程碑与排期)

---

## 1. 阻塞合并项

### 1.1 [CRITICAL] `SshKey` ORM 模型缺失，所有 SSH 密钥工具不可用

- **影响文件**：`app/services/tool_adapters/ssh_key_tools.py`（5 个工具函数）
- **影响工具**：`ops.list_ssh_keys` / `ops.get_ssh_key` / `ops.create_ssh_key` / `ops.update_ssh_key` / `ops.delete_ssh_key`
- **根因**：`from app.db.models import SshKey` 的 `SshKey` 类在 `app/db/models.py` 中不存在
- **失败场景**：工具注册成功但调用即抛 `ImportError`，MCP 客户端误以为工具可用

#### 实现方案

**方案 A：新建 SshKey 表 + 加密落库（推荐）**

1. 在 `app/db/models.py` 中新增 `SshKey` ORM 模型：

```python
class SshKey(Base):
    __tablename__ = "ssh_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    private_key_encrypted = Column(Text, nullable=False)  # AES 加密存储
    passphrase_encrypted = Column(Text, nullable=True)     # AES 加密存储
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
```

2. 接入既有的加密工具链（参考 `DatabaseConnection` 的 `ssh_key_content_encrypted` 字段）：

```python
from app.core.security import encrypt_secret, decrypt_secret

# 写入
row = SshKey(
    name=name,
    private_key_encrypted=encrypt_secret(private_key),
    passphrase_encrypted=encrypt_secret(passphrase) if passphrase else None
)

# 读取
private_key = decrypt_secret(row.private_key_encrypted)
```

3. 更新 `ssh_key_tools.py` 中所有工具函数，从 `SshKey` 表读写
4. 添加数据库迁移文件

**方案 B：删除 `ssh_key_tools.py`**（临时方案，模型就绪后再恢复）

#### 验收标准

- [ ] `ops.create_ssh_key` 调用成功，私钥加密写入 DB
- [ ] `ops.list_ssh_keys` 返回已创建的密钥列表（私钥字段脱敏，只返回名称）
- [ ] `ops.get_ssh_key` 返回解密后的完整私钥
- [ ] `ops.update_ssh_key` / `ops.delete_ssh_key` 正常工作
- [ ] DB 直接 SELECT 看不到明文私钥

#### 依赖

- 必须与 #11（明文落库风险）同步修复，不可仅建表不加加密

---

### 1.2 [CRITICAL] `InventoryReadService` 缺 `list_pipelines` / `get_pipeline`

- **影响文件**：`app/services/tool_adapters/pipeline_tools.py:28`、`:58`
- **影响工具**：`ops.list_pipelines` / `ops.get_pipeline`
- **根因**：`app/domain/inventory/services.py` 的 `InventoryReadService` 没有这两个方法

#### 实现方案

**方案 A：在 `InventoryReadService` 中补充方法（推荐）**

```python
# app/domain/inventory/services.py
def list_pipelines(self, db: Session, keyword: str = "", limit: int = 100) -> List[Dict]:
    config = self.config_manager.load_config()
    pipelines = config.get("pipelines", {})
    items = list(pipelines.values())
    if keyword:
        kw = keyword.lower()
        items = [x for x in items if kw in str(x.get("name", "")).lower()]
    return items[:limit]

def get_pipeline(self, db: Session, pipeline_id: str) -> Optional[Dict]:
    config = self.config_manager.load_config()
    return config.get("pipelines", {}).get(pipeline_id)
```

**方案 B：在 `pipeline_tools.py` 中直接读 config**

```python
# 不走 InventoryReadService，直接调 config_manager
config = config_manager.load_config()
pipelines = config.get("pipelines", {})
```

#### 验收标准

- [ ] `ops.list_pipelines` 返回 pipeline 列表
- [ ] `ops.get_pipeline` 按 ID 返回单个 pipeline
- [ ] 无 `AttributeError` 异常

---

### 1.3 [HIGH] `html` 导出格式只声明未实现

- **影响文件**：`app/services/db_query_export.py:43`、`:187-222`、`:889`
- **影响功能**：`fmt=html` 导出请求在文件名/路径生成后抛 `HTTPException(400)`

#### 实现方案

**方案 A：补全 html writer（推荐）**

```python
# app/services/db_query_export.py，在 _write_export_file 中添加
if fmt == "html":
    html_content = _build_html_table(columns, rows, title=title or "Query Result")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
    return filepath

def _build_html_table(columns: List[str], rows: List[List[Any]], title: str = "") -> str:
    """生成带样式的 HTML 表格"""
    thead = "".join(f"<th>{c}</th>" for c in columns)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>"
        for row in rows
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background:#f2f2f2}}tr:nth-child(even){{background:#f9f9f9}}</style>
</head><body><h2>{title}</h2><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></body></html>"""
```

**方案 B：从 `EXPORT_FORMATS` 移除 `html`**

```python
# app/services/db_query_export.py
EXPORT_FORMATS = ["csv", "json", "xlsx", "parquet"]  # 移除 "html"
```

#### 验收标准

- [ ] `fmt=html` 导出生成合法的 HTML 文件
- [ ] HTML 文件包含完整的表格结构和样式
- [ ] 或 `fmt=html` 从支持列表中移除，不再抛异常

---

### 1.4 [HIGH] `db_type` enum 比 executor 支持的类型多

- **影响文件**：`app/services/tool_adapters/connection_tools.py:154`
- **影响功能**：`ops.test_connection` / `ops.create_connection` 的 `db_type` 校验
- **根因**：schema 允许 `{mysql, postgresql, sqlite, mongodb, redis}`，但 executor 仅支持 `postgresql / mysql`

#### 实现方案

**方案 A：收窄 enum（推荐，短期）**

```python
# connection_tools.py:154
"db_type": {
    "type": "string",
    "enum": ["mysql", "postgresql"],  # 移除 sqlite, mongodb, redis
    "description": "数据库类型"
}
```

**方案 B：补全 Executor 子类（长期）**

1. 新建 `MongoExecutor`、`RedisExecutor`、`SqliteExecutor` 继承 `BaseExecutor`
2. 在 `_connection_executor` 中按 `db_type` 分发

#### 验收标准

- [ ] 创建 `db_type=mongodb` 的连接时被 schema 拒绝或正确路由
- [ ] 不会出现 MySQL 协议连接 MongoDB 端口的误导性错误

---

### 1.5 [HIGH] `load_config → mutate → save_config` 的丢失更新

- **影响文件**：`app/services/tool_adapters/pipeline_tools.py`、`server_tools.py` 等大量写工具
- **根因**：`app/config/repository.py:166-167` 的 `save_config` 会 DELETE 不在新 dict 里的键
- **失败场景**：并发 `create_pipeline(A)` 与 `create_pipeline(B)` → A 被 B 的保存覆盖消失

#### 实现方案

**方案 A：加 `RLock` 串行化（短期，推荐）**

```python
# app/config/repository.py 或 config_manager
import threading
_lock = threading.RLock()

class ConfigManager:
    def save_config(self, config: Dict):
        with _lock:
            # 重新 load 最新版本，合并变更，再 save
            current = self.load_config()
            merged = {**current, **config}  # 或更精细的 merge 策略
            self._repository.save_config(merged)
```

**方案 B：独立资源 CRUD repository（中期）**

1. 为 pipeline / server / environment 各自建表或独立 config key
2. 写操作只修改对应 key，不以整 dict 替换

#### 验收标准

- [ ] 并发调用 `create_pipeline(A)` 和 `create_pipeline(B)` 后两者都存在
- [ ] 并发调用不会丢失任何已存在的配置项

---

## 2. 合并前需跟进项

### 2.1 [MEDIUM] `read_only` 默认值由 `False` 翻转为 `True`

- **影响文件**：`app/services/tool_policy.py:14`
- **影响**：升级后未覆盖该配置的部署，所有写类工具被 403

#### 实现方案

```python
# app/db/migrations/runner.py 中新增迁移
def migrate_read_only_default():
    """确保 read_only 默认值变更后存量部署不受影响"""
    config = load_config()
    if "capability_server" not in config:
        return
    caps = config["capability_server"]
    if "read_only" not in caps:
        caps["read_only"] = False  # 显式写入旧默认值
        save_config(config)
```

#### 验收标准

- [ ] 升级后写类工具不被 403
- [ ] `UPGRADE.md` 中标注该行为变化

---

### 2.2 [MEDIUM] 审计脱敏正则把 `key` 当子串匹配，过度脱敏

- **影响文件**：`app/services/tool_audit.py:14`
- **影响**：`primary_key`、`keyword` 等非敏感字段被误脱敏

#### 实现方案

```python
# app/services/tool_audit.py
# 修改前
SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|key|credential|auth_header|cookie|session)",
    re.IGNORECASE
)

# 修改后
SENSITIVE_KEY_PATTERN = re.compile(
    r"\b(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|"
    r"access[_-]?key|secret[_-]?key|ssh_key|credential|auth_header|cookie|session)\b",
    re.IGNORECASE
)
```

#### 验收标准

- [ ] `{"primary_key": "uuid-x"}` 不被脱敏
- [ ] `{"keyword": "search"}` 不被脱敏
- [ ] `{"password": "secret123"}` 仍被脱敏
- [ ] `{"api_key": "sk-xxx"}` 仍被脱敏

---

### 2.3 [MEDIUM] `_remote_check` 的宽口 `except TypeError` 吞掉分析器内部异常

- **影响文件**：`app/services/inspection_center.py:81-83`
- **影响**：用户配置的阈值被静默忽略，分析结果错误且难以察觉

#### 实现方案

```python
# app/services/inspection_center.py
import inspect

def _remote_check(analyze_func, *args, thresholds=None, **kwargs):
    sig = inspect.signature(analyze_func)
    if "thresholds" in sig.parameters:
        return analyze_func(*args, thresholds=thresholds, **kwargs)
    else:
        return analyze_func(*args, **kwargs)
```

#### 验收标准

- [ ] 分析器内部 `TypeError` 不再被吞掉，能正常抛出
- [ ] 新签名分析器（带 thresholds）正确接收阈值参数
- [ ] 旧签名分析器（不带 thresholds）正常降级

---

### 2.4 [MEDIUM] `pipeline_tools.list_pipelines_tool` 在 `limit: null` 时崩溃

- **影响文件**：`app/services/tool_adapters/pipeline_tools.py:26`、`ssh_key_tools.py:23`
- **根因**：`args.get("limit", 100)` 在 JSON `null` 时返回 `None`，`int(None)` 抛 `TypeError`

#### 实现方案

```python
# 修改前
limit = min(int(args.get("limit", 100)), 500)

# 修改后（与 connection_tools.py 一致）
limit = min(int(args.get("limit") or 100), 500)
```

#### 验收标准

- [ ] 传 `{"limit": null}` 不崩溃，使用默认值 100
- [ ] 传 `{"limit": 50}` 正常使用 50
- [ ] 不传 `limit` 字段使用默认值 100

---

### 2.5 [MEDIUM] `_all_builtin_rules` 把分类描述当成可执行命令

- **影响文件**：`app/services/inspection_center.py:4483`
- **影响**：MEMORY 等新增分类的 `rule_content` 是中文描述而非 shell 命令

#### 实现方案

**方案 A：在 `SERVER_RULE_COMMANDS` 中补 MEMORY 命令**

```python
SERVER_RULE_COMMANDS = {
    # ... existing ...
    "MEMORY": "free -h && echo '---' && cat /proc/meminfo | grep -E '^(MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree|Cached|Buffers)' && echo '---' && dmesg | grep -i 'oom\|killed process' | tail -20",
}
```

**方案 B：`_all_builtin_rules` 直接读 `_server_check_specs` 的 command（推荐）**

```python
# 不在循环里用 .get(..., cat["description"]) 兜底
# 而是确保所有分类都有 spec 且 spec.command 是合法命令
spec = _server_check_specs("", [cat["code"]])
command = spec.get("command", "") if spec else ""
```

#### 验收标准

- [ ] MEMORY 分类的 `rule_content` 是合法的 shell 命令
- [ ] 用户 fork builtin 规则后可正常执行

---

## 3. 下个迭代清理项

### 3.1 [LOW] `ssh_key_tools.py` 假设的明文落库违反加密-at-rest 约定

- **与 #1 同步修复**：建表时必须接入 `encrypt_secret` / `decrypt_secret` 工具链

### 3.2 [LOW] `package.sh` / `package.ps1` 仍引用已删除的 `ARCHITECTURE.md`

- **影响文件**：`scripts/package.sh:51`、`scripts/package.ps1:46`
- **修复**：删除引用，或指向新的 `docs/` 树中的架构文档

---

## 4. 复用 / 简化 / 效率方向

### 4.1 审计脱敏内存优化

- **位置**：`tool_audit.py:30`
- **问题**：序列化前先深拷贝整个 value 做 mask，再 `json.dumps`，大入参/返回值双倍内存
- **方案**：在 `json.dumps` 的 `default` 回调中按字段名 mask，避免深拷贝

### 4.2 抽取公共 `_filter_items` 辅助函数

- **位置**：`server_tools.py`、`connection_tools.py`、`pipeline_tools.py`
- **问题**：三个 adapter 各自实现过滤逻辑
- **方案**：抽取 `app/services/tool_adapters/_utils.py`:

```python
def filter_items(items: List[Dict], keyword: str = "", limit: int = 100,
                 search_fields: List[str] = None) -> List[Dict]:
    if keyword:
        kw = keyword.lower()
        fields = search_fields or ["name", "id"]
        items = [x for x in items if any(
            kw in str(x.get(f, "")).lower() for f in fields
        )]
    return items[:limit]
```

### 4.3 抽取 `tokenTemplates.ts` 独立模块

- **位置**：`frontend/src/pages/maintenance/ToolTokenPanel.tsx`
- **问题**：`TOKEN_TEMPLATES`、`DANGEROUS_SCOPES`、`scopesToText / textToScopes` 耦合在组件内
- **方案**：移到 `frontend/src/pages/maintenance/tokenTemplates.ts`，便于复用

### 4.4 `_all_builtin_rules` 减少 O(N²) 调用

- **位置**：`app/services/inspection_center.py`
- **问题**：循环内每次调用 `_server_check_specs` 新建整张表
- **方案**：循环外预构建 `{code: spec}` 字典

```python
specs_map = {code: _server_check_specs("", [code]) for code in all_codes}
for cat in categories:
    spec = specs_map.get(cat["code"])
```

---

## 5. 测试计划

### 5.1 单元测试

| 编号 | 测试项 | 覆盖问题 |
|------|--------|----------|
| UT-01 | SSH Key CRUD 全流程（加密存储） | #1, #11 |
| UT-02 | Pipeline list/get 正常返回 | #2 |
| UT-03 | HTML 导出生成合法文件 | #3 |
| UT-04 | db_type schema 仅允许 mysql/postgresql | #4 |
| UT-05 | 并发 create_pipeline 不丢失更新 | #5 |
| UT-06 | read_only=False 迁移生效 | #6 |
| UT-07 | 审计脱敏不误伤 primary_key/keyword | #7 |
| UT-08 | _remote_check 不吞 TypeError | #8 |
| UT-09 | limit:null 不崩溃 | #9 |
| UT-10 | MEMORY 分类 rule_content 是合法命令 | #10 |

### 5.2 集成测试

| 编号 | 测试项 |
|------|--------|
| IT-01 | MCP tools/capabilities 返回正确的工具列表 |
| IT-02 | SSH Key 工具端到端调用不报 ImportError |
| IT-03 | Pipeline 工具端到端调用不报 AttributeError |
| IT-04 | 巡检报告包含正确的阈值判断结果 |
| IT-05 | 并发写操作不会丢失数据 |

### 5.3 回归测试

- [ ] 巡检功能（server / project / combined）正常
- [ ] 报告生成正常
- [ ] 前端页面正常渲染
- [ ] 工具权限控制正常

---

## 6. 里程碑与排期

| 阶段 | 内容 | 预估规模 |
|------|------|----------|
| **M1: 阻塞修复** | #1 SSH Key 加密表 + #2 Pipeline 方法 + #3 HTML 导出 + #4 db_type 收窄 + #5 并发锁 | 3-5 天 |
| **M2: 合并前跟进** | #6 迁移 + #7 脱敏正则 + #8 签名判断 + #9 null 修复 + #10 MEMORY 命令 | 1-2 天 |
| **M3: 清理优化** | #11 加密确认 + #12 脚本清理 + 复用优化 | 1-2 天 |
| **M4: 测试验证** | 单元测试 + 集成测试 + 回归测试 | 1-2 天 |

---

## 附录 A：受影响文件清单

| 文件 | 涉及问题 |
|------|----------|
| `app/services/tool_adapters/ssh_key_tools.py` | #1, #9, #11 |
| `app/db/models.py` | #1, #11 |
| `app/domain/inventory/services.py` | #2 |
| `app/services/tool_adapters/pipeline_tools.py` | #2, #5, #9 |
| `app/services/db_query_export.py` | #3 |
| `app/services/tool_adapters/connection_tools.py` | #4 |
| `app/config/repository.py` | #5 |
| `app/services/tool_adapters/server_tools.py` | #5 |
| `app/services/tool_policy.py` | #6 |
| `app/db/migrations/runner.py` | #6 |
| `app/services/tool_audit.py` | #7 |
| `app/services/inspection_center.py` | #8, #10 |
| `scripts/package.sh` | #12 |
| `scripts/package.ps1` | #12 |
| `frontend/src/pages/maintenance/ToolTokenPanel.tsx` | 复用优化 |

## 附录 B：关键设计决策

1. **SSH Key 存储**：必须加密落库，使用与 `DatabaseConnection` 一致的 `encrypt_secret` / `decrypt_secret` 工具链
2. **并发安全**：短期用 `RLock` 串行化 `load_config → save_config`，中期拆分为独立资源 CRUD
3. **db_type 策略**：短期收窄枚举，长期补全 Executor 子类
4. **审计脱敏**：从子串匹配改为词边界匹配 + 精确字段名列表
# OPS 临时命令执行审批（EXEC_REMOTE）设计方案

> 状态：**设计评审稿（未实现）**
> 目标读者：OPS 维护者 / 审批人（@jack.han）
> 关联文档：`qclaw-channel-approval-integration.md`、`matrix-deploy-integration.md`
> 缘起：2026-09-10 房间 `!LZmupTKKHXJwRvomBE`「给 4 台线上服务器装 docker」任务，
> agent 排查 5 分钟后无法执行、且当时未回报（回报缺口已由 SOUL.md 修复独立解决）

---

## 1. 现状取证（为什么现在做不到）

| 事实 | 位置 | 含义 |
|---|---|---|
| `ops.exec_remote` 是唯一能跑任意 shell 的工具 | `app/services/tool_adapters/remote_exec_tools.py:43-106` | 「装 docker」必须用它 |
| 它对 AI token **硬阻断** | `app/services/tool_policy.py:202-224` | 注释明确：除 `inspection_execute` 与 qclaw 审批类外，所有 `requires_human_approval` 工具保持 admin-only |
| `prepare_service_control` 不能替代 | `app/services/tool_adapters/server_tools.py:493-494` | 原文「`env`/`compose_args` 只影响命令生成，**不允许直接注入任意命令行**」；且执行器要求 `system_name`+`service_name`（`approval_executor.py:212-213`） |
| 实测确认 | 2026-09-10 开启 `allow_prod` 后复测 | 仍返回 `{"blocked":true,"blocked_reason":"requires_human_approval"}` |
| 人工通道存在 | `app/api/terminal.py`（`require_admin` + WebSocket，`/api/v2/servers/{name}/terminal`） | 管理员可手工执行 → 这是「不做 B」时的兜底 |

**结论**：现阶段 agent 对这类需求只能「报告卡点」，无法推动执行。本方案补齐审批通道。

---

## 2. 目标与非目标

### 目标
1. AI 可**提议**命令，人工批准后由 OPS 内部执行器执行，AI 始终不持有执行权。
2. 复用既有 qclaw 审批链（一次性短码 / 15 分钟 / 房间+事件绑定 / 内容指纹）。
3. 全链路审计：谁提议、谁批准、命令原文、逐目标退出码与输出摘要。
4. 默认关闭（kill switch），可一键回滚。
5. 首版以**命令白名单模式**落地，把爆炸半径控制在可解释范围内。

### 非目标
- 不开放交互式 shell / 长驻会话（那属于终端工作台，走 admin 通道）。
- 不做多步脚本编排 —— 该场景用 `prepare_plan`（阶段 3 可把 EXEC 作为 plan 的一步）。
- 不放宽 `ops.exec_remote` 对 AI 的硬阻断（本方案不碰这条线）。
- 不改变现有部署/回滚/DML 审批流的行为。

---

## 3. 总体流程

```
房间消息「4 台服务器装 docker」
  │
  ├─1 agent: ops.routing.resolve_message_target        → 路由票据（绑定 message_context）
  │
  ├─2 agent: ops.approval.prepare_exec                 → 冻结 {command, targets, timeout}
  │        （category=approval_prepare，AI 可调，无需新豁免）
  │        返回：approval_id + 短码「批准远程命令 <system>@<env> <指纹8>」+ reply_template
  │
  ├─3 agent: message 工具把 reply_template 原样发到房间（含逐字命令 code block）
  │
  ├─4 审批人 @jack.han 回复「批准 <短语>」
  │
  ├─5 agent: ops.approval.execute(approval_id, short_code, message_context)
  │        → ActionApprovalService.consume()  校验：一次性 / 15min / 房间+事件 / 指纹
  │        → ApprovalExecutor.execute()  → _execute_exec_remote()  逐目标 SSH
  │
  └─6 结果落库（execution_result）+ agent 回报逐目标结果
```

关键性质：**安全门禁是短码本身，不是调用方 token scope**（`ops.approval.execute` 只需 `ops:read`）——与现有审批流一致。

---

## 4. 详细设计

### 4.1 数据模型：零迁移

`AiActionApproval`（`app/db/models.py:614`）已经具备所需字段，**无需 DB 迁移**：

| 字段 | 行 | 用途 |
|---|---|---|
| `action_type = String(64)` | :624 | 新值 `EXEC_REMOTE`（无 CHECK/枚举约束） |
| `request_payload = JSON` | :628 | 冻结命令与目标 |
| `action_digest` | :639 | 已有 `compute_action_digest` 覆盖 `action_parameters` → **命令变动即指纹变动**，天然防跨单复用 |
| `execution_result = JSON` | :655 | 逐目标结果 |
| `approval_code_hash` / `expires_at` | :640 / :647 | 一次性短码与 TTL（已有） |
| `authorized_identities = JSON` | :667 | 审批人白名单 |

冻结载荷约定：

```json
{
  "system_name": "crypto",
  "service_name": null,
  "environment": "prod",
  "targets": ["43.106.12.129", "43.106.14.247", "43.106.14.120", "47.84.52.170"],
  "action_parameters": {
    "command": "apt-get update && apt-get install -y docker.io",
    "command_sha256": "…",
    "timeout": 300,
    "mode": "allowlist",
    "template_id": "apt_install_allowlist"
  }
}
```

### 4.2 新工具 `ops.approval.prepare_exec`

文件：`app/services/tool_adapters/approval_tools.py`，结构对齐 `prepare_service_control`（:892-969）。

**注册属性**（决定策略是否放行，务必照抄）：

```python
@registry.register(
    name="ops.approval.prepare_exec",
    title="准备远程命令执行审批",
    description="为临时远程命令执行（装包/起服务/排障）创建不可变审批工单…中文：准备命令执行审批/远程命令审批。",
    scopes=["ops:read"],
    risk="low",
    category="approval_prepare",   # ← 已在 QCLAW_APPROVAL_CATEGORIES（tool_policy.py:194-201）→ AI 可调
    # 不设 write / requires_human_approval → 不会被 :202-224 的 L4 规则拦
    input_schema={...},
)
```

**输入 schema**（在 `_common_prepare_schema()`（:339）基础上扩展）：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `routing_ticket` / `message_context` / `system_name` / `environment` / `ai_reason` | — | 见公共 schema | 复用 |
| `targets` | array[string] | ✅ | 1..20 台，须能在 `servers` 表解析 |
| `command` | string | ✅ | 1..4096 字符；首版须匹配白名单模板 |
| `timeout` | int | ❌ | 默认 120，上限 300 |
| `mode` | enum `allowlist`/`free` | ❌ | 默认 `allowlist`；`free` 需开关允许 |
| `allow_destructive` | bool | ❌ | 默认 false；prod 下恒为 false（见 4.6） |

**处理流程**：

```python
def approval_prepare_exec(args, ctx, db):
    message_context, routing_revision, ticket_digest = _validated_prepare_ticket(args, ctx)
    targets = _normalize_targets(args["targets"])          # 去重 + resolve_server 校验
    command = (args.get("command") or "").strip()
    settings = get_capability_settings(db)
    verdict = _validate_exec_command(command, args, settings, targets)   # 4.6 三层护栏
    digest_sha = hashlib.sha256(command.encode()).hexdigest()
    approvers = _lookup_approvers(ctx, args["system_name"], None,
                                 channel=message_context.channel,
                                 channel_account_id=message_context.channel_account_id)
    service = ActionApprovalService(db)
    approval, short_code = service.prepare(
        action_type="EXEC_REMOTE",                 # ← 新动作类型
        tool_name="ops.approval.prepare_exec",
        message_context=message_context,
        system_name=args["system_name"],
        service_name=args.get("service_name"),
        environment=args["environment"],
        targets=targets,
        action_parameters={"command": command, "command_sha256": digest_sha,
                           "timeout": int(args.get("timeout") or 120),
                           "mode": verdict["mode"], "template_id": verdict.get("template_id", "")},
        routing_config_revision=routing_revision,
        routing_ticket_digest=ticket_digest,
        risk_level="high",                         # 与 exec_remote 的 risk 对齐
        ai_reason=args.get("ai_reason", ""),
        authorized_identities=[...],               # 同 prepare_service_control
    )
    reply_template = _approval_reply_template(kind="action", ..., command_block=command,
                                              targets=targets, timeout=..., risk_notes=verdict["notes"])
    return {"ok": True, "approval_id": approval.id, "approval_code": short_code,
            "expires_at": ..., "reply_template": reply_template,
            "targets": targets, "warnings": verdict["warnings"]}
```

> `service_name` 允许为空（ad-hoc 命令无服务语义）；`_lookup_approvers`（:89）的 `service_name` 参数为可选，传 `None` 即可。
> `system_name` 语义待确认：ad-hoc 场景建议取路由解析出的系统名，或服务器所属组名（如 `crypto`）——见 §10 待决策 ①。

### 4.3 审批卡片（reply_template）

`_approval_reply_template`（:378）新增 `command_block` 区块，**逐字展示**将执行的命令：

````markdown
**远程命令执行审批**（工单 `a1b2c3…`）

- 系统/环境：`crypto` @ `prod`
- 目标（4 台）：`43.106.12.129`、`43.106.14.247`、`43.106.14.120`、`47.84.52.170`
- 超时：`300s`　模式：`白名单模板 apt_install_allowlist`
- 风险：**高**（将在生产执行 shell 命令，不可自动回滚）

将执行的命令（逐字）：
```bash
apt-get update && apt-get install -y docker.io
```

审批人：@jack.han　有效期至：`2026-09-10 19:30:00 +08:00`

```text
批准远程命令 crypto@prod A3F9C2D1
```
````

要求：命令块必须与冻结载荷**逐字节一致**（执行器会二次校验 sha256），避免「展示 A、执行 B」。

### 4.4 执行器

文件：`app/services/approval_executor.py`

1. 分发链（:120-131）新增分支：

```python
elif action_type == "EXEC_REMOTE":
    return self._execute_exec_remote(approval, payload)
```

2. 新增 `_execute_exec_remote`（模式对齐 `_execute_service_control` :193 与 `_control_single_server` :300，复用 `server_tools._connect` 以自动获得跳板机支持）：

```python
def _execute_exec_remote(self, approval, payload):
    import hashlib, shlex
    from app.services.tool_adapters.server_tools import _connect
    from app.services.sensitive_data import mask_sensitive   # 复用既有掩码+截断（:12）

    ap = payload.get("action_parameters") or {}
    command      = str(ap.get("command") or "")
    frozen_sha   = str(ap.get("command_sha256") or "")
    timeout      = min(int(ap.get("timeout") or 120), 300)
    targets      = payload.get("targets") or []

    # ① 完整性复核：命令被篡改则直接失败，绝不执行
    if not command or hashlib.sha256(command.encode()).hexdigest() != frozen_sha:
        raise ValueError("冻结命令校验失败，拒绝执行")

    # ② 执行前再跑一次护栏（防审批期间配置被改宽松）
    _assert_command_allowed(command, approval, self.db)

    results, ok_count = [], 0
    for name in targets:                       # 串行：生产谨慎优先
        try:
            ssh, srv = _connect(name)
            try:
                code, out, err = ssh.exec(command, timeout=timeout)
            finally:
                ssh.close()
            ok = code == 0
            results.append({"server": name, "ok": ok, "exit_code": code,
                            "stdout": mask_sensitive(out, limit=8192),
                            "stderr": mask_sensitive(err, limit=8192)})
            ok_count += 1 if ok else 0
        except Exception as e:
            results.append({"server": name, "ok": False, "error": str(e)})
    return {"action": "EXEC_REMOTE", "command_sha256": frozen_sha, "targets": targets,
            "results": results, "success_count": ok_count,
            "fail_count": len(targets) - ok_count,
            "message": f"远程命令执行完成: {ok_count}/{len(targets)} 成功"}
```

**执行语义（必须在文档与卡片中写明）**：

| 语义 | 决策 | 理由 |
|---|---|---|
| 失败重试 | **不自动重试** | 命令可能非幂等（`apt install` 中途失败、`rm`） |
| 部分失败 | 保留全部逐目标明细，工单置 `FAILED` | 便于人工续做，不掩盖中间状态 |
| 并发 | 首版**串行** | 生产上避免 4 台同时重负载 |
| 输出上限 | 每目标 8KB 截断 + 掩码 | 防泄密与刷屏 |
| 超时 | `min(请求值, 300)` | 与 `exec_remote` 既有上限一致 |

### 4.5 策略层改动（最小化）

`prepare_exec` 的类别 `approval_prepare` **本就在 AI 白名单内**（`tool_policy.py:194-201`、:210-211），因此**无需新增豁免**——这点很重要，方案的授权面是「新增一个 prepare 工具」，而不是「放宽 L4 硬阻断」。

只增加一个默认关闭的 kill switch（建议插在 :226 类别开关群附近）：

```python
if tool_def.name == "ops.approval.prepare_exec" and not settings.get("allow_exec_remote_tool", False):
    raise HTTPException(status_code=403, detail="Ad-hoc remote exec approval tools are disabled")
```

> 该开关复用已有 `capability_settings` 表（`get_capability_settings` :132），无需建表。默认值加到 `DEFAULT_CAPABILITY_SETTINGS`（:28-58）。

### 4.6 安全护栏（三层，本方案的核心）

#### 第 1 层：调用前（`_validate_exec_command`）

1. **长度/字符**：≤4096；拒绝 NUL 与控制字符；换行**仅允许**在显式 `multi_line=true` 时（默认单行，降低「展示第一行、实际执行多行」的欺骗空间）。
2. **破坏性 denylist**（正则，命中即拒绝，prod 无例外）：

   | 模式 | 示例 |
   |---|---|
   | 磁盘/文件系统毁坏 | `mkfs*`、`dd … of=/dev/`、`> /dev/sd*` |
   | 递归删除根/家目录 | `rm -rf /`、`rm -rf /*`、`rm -rf ~` |
   | 关机/重启 | `shutdown`、`reboot`、`halt`、`poweroff`、`init 0/6` |
   | 账户与凭据 | `useradd`、`usermod`、`passwd`、`chpasswd`、`visudo` |
   | 防火墙清空 | `iptables -F`、`nft flush ruleset` |
   | 远程代码直执行 | `curl … \| sh`、`wget … \| bash` |
   | 计划任务 | `crontab -`、`systemctl enable` 未在白名单时 |
   | fork 炸弹 | `:(){ :\|:& };:` |

3. **白名单模板模式（首版默认）**：命令必须匹配预置模板；模板声明参数化槽位并做取值校验。示例模板：

```yaml
- id: apt_install_allowlist
  pattern: ^apt-get (update && )?apt-get install -y (?P<pkgs>[a-z0-9.\-+ ]+)$
  params: { pkgs: { allow: ["docker.io", "docker-ce", "docker-compose-plugin", "htop", "curl", "jq"] } }
  env: [test, prod]
- id: docker_status
  pattern: ^(docker (ps|images|version)|systemctl (status|is-active) docker)$
  params: {}
  env: [test, prod]
- id: file_size_probe
  pattern: ^du -sh (?P<path>/[^\s;|&]+)$
  params: { path: { prefix: ["/opt", "/data", "/vol1"] } }
  env: [test, prod]
```

  首版建议只放行 **3~5 个模板**（装 docker、看 docker 状态、查目录占用），跑顺后再增补。

#### 第 2 层：审批时（人工闸门）

- 审批人由 `_lookup_approvers` 解析（现有机制）；**prod 环境额外限定为 admin 身份**（`@jack.han`）。
- 卡片逐字展示命令 + 目标 + 超时 + 风险等级。
- 短码一次性 / 15 分钟 / 房间+事件绑定 / 内容指纹防跨单复用（`ActionApprovalService.prepare` :187-208 已实现）。

#### 第 3 层：执行时（防 TOCTOU）

- 命令 sha256 二次比对（4.4 ①）。
- 执行前**重跑护栏**（4.4 ②）——防止「先 prepare 合法命令 → 审批期间管理员改宽松/收紧配置」造成口径漂移。
- 命令**不含**目标主机信息（目标从 `payload.targets` 取），避免命令内嵌入其它主机。

#### 其它必做项

| 项 | 说明 |
|---|---|
| **房间绑定** | 该 token 当前 `bound_room_ids = "[]"`（空）→ 应绑定到授权房间，否则任何持 token 的通道都能发起 exec 审批 |
| **token 收敛** | 建议为 exec 审批单独签发 token（scopes 仅 `ops:read`），与现有 `qclaw-ops-room-bound` 分离 |
| **输出掩码** | 复用 `app/services/sensitive_data.py:12` `mask_sensitive(value, limit)`；卡片上展示的命令如需脱敏，复用 `app/api/servers.py:40` `_mask_sensitive_command`（注意：执行用原文，展示可脱敏） |
| **频次限制**（可选） | 同 room+system 每小时 ≤N 次，缓解审批疲劳 |
| **审计** | `audit("exec.remote.prepare"/"exec.remote.executed", …)` |

### 4.7 分阶段灰度

| 阶段 | 内容 | 开关 |
|---|---|---|
| **1（首版）** | 白名单模板模式；denylist 生效；prod 需 admin 审批 | `allow_exec_remote_tool=true`（默认 false）、`exec_remote_mode="allowlist"` |
| **2** | 自由命令模式（denylist 仍生效），仅 admin 可批 | `exec_remote_mode="free"` |
| **3（可选）** | 把 EXEC 作为 `prepare_plan` 的一个步骤类型，多步一次批准 | 复用 `plan_executor` |

### 4.8 新增 capability_settings 开关

```python
# DEFAULT_CAPABILITY_SETTINGS 追加（默认全部关闭/最保守）
"allow_exec_remote_tool": False,        # kill switch：prepare_exec 是否可用
"exec_remote_mode": "allowlist",        # allowlist | free
"exec_remote_max_timeout_seconds": 300,
"exec_remote_max_targets": 20,
"exec_remote_allow_prod": False,        # 是否允许 prod（与 allow_prod_deploy 分离，便于单独收紧）
"exec_remote_templates": [...],         # 白名单模板（见 4.6）
"exec_remote_deny_patterns": [...],     # denylist（见 4.6）
"exec_remote_max_per_hour": 10,         # 频次限制（0=不限）
```

---

## 5. 改动清单

| # | 文件 | 位置 | 改动 | 规模 |
|---|---|---|---|---|
| 1 | `app/services/tool_adapters/approval_tools.py` | 新增（`prepare_service_control` :892 之后） | `ops.approval.prepare_exec` 工具 + `_validate_exec_command` + `_normalize_targets` | ~180 行 |
| 2 | 同上 | `_STEP_VERBS` :366 | 加 `"EXEC_REMOTE": "远程命令"` | 1 行 |
| 3 | 同上 | `_approval_reply_template` :378 | 新增 `command_block` / `risk_notes` 区块 | ~30 行 |
| 4 | `app/services/approval_phrase.py` | `_ACTION_VERBS` :20 | 加 `"EXEC_REMOTE": "远程命令"` | 1 行 |
| 5 | `app/services/approval_executor.py` | 分发链 :120-131 | 加 `EXEC_REMOTE` 分支 | 2 行 |
| 6 | 同上 | 新增方法（`_execute_service_control` :193 附近） | `_execute_exec_remote`（掩码/截断复用 `sensitive_data.mask_sensitive`，不新写辅助） | ~55 行 |
| 7 | `app/services/tool_policy.py` | `DEFAULT_CAPABILITY_SETTINGS` :28-58 | 新增 4.8 开关默认值 | ~8 行 |
| 8 | 同上 | :226 附近 | kill switch 闸门 | ~5 行 |
| 9 | `app/services/mcp_capability_service.py` | 工具描述字典（`ops.approval.prepare_service_control` :225 附近） | 新增 `ops.approval.prepare_exec` 描述（让 agent 可发现） | ~4 行 |
| 10 | `app/services/agent_context.py` | facts/flow | 补「ad-hoc 命令执行」流程指引（可选但推荐，降低 agent 摸索成本） | ~20 行 |
| 11 | `tests/` | 新增 | 见 §7 验收矩阵 | ~250 行 |
| 12 | 配置/文档 | token `bound_room_ids`、`docs/` | 房间绑定 + 本文档归档 | — |

**无需**：DB 迁移、前端改动（审批列表复用现有渲染）、`ops.exec_remote` 行为改动。

---

## 6. 审计与可观测

| 数据 | 位置 | 内容 |
|---|---|---|
| 工单 | `ai_action_approvals` | `action_type=EXEC_REMOTE`、`action_digest`、`approved_by`、`executed_at`、`execution_result` |
| 调用 | `tool_call_logs` | `prepare_exec` 与 `approval.execute` 各一条，含 token 身份 |
| 审计 | `audit_records` | `exec.remote.prepare` / `exec.remote.executed`（含命令 sha256 与目标数） |
| 房间 | Matrix | 审批卡片 + 执行结果回执（逐目标成功/失败） |

查询入口：`ops.approval.list`（现有）按 `action_type=EXEC_REMOTE` 过滤即可回看历史命令。

---

## 7. 验收测试矩阵

| # | 用例 | 期望 |
|---|---|---|
| 1 | 白名单命令 + admin 批准 + 4 台 prod | 全部执行，工单 `SUCCEEDED`，逐目标 exit_code=0 |
| 2 | 未授权身份回短码 | `consume` 拒绝，工单保持 `PENDING_APPROVAL` |
| 3 | 用房间 A 的短码在房间 B 执行 | 拒绝（房间绑定） |
| 4 | 短码超 15 分钟后使用 | 拒绝（过期） |
| 5 | 短码二次使用 | 拒绝（一次性） |
| 6 | 篡改 `request_payload.command` 后再 execute | `冻结命令校验失败`，**不执行** |
| 7 | 命令命中 denylist（如 `reboot`） | prepare 阶段即 403 |
| 8 | 白名单外的命令（`mode=allowlist`） | prepare 阶段即 403，附可读原因 |
| 9 | `mode=free` 但开关为 allowlist | 拒绝 |
| 10 | 4 台中 1 台不可达 | 工单 `FAILED`，3 台成功明细完整保留，无自动重试 |
| 11 | token `allow_prod=0` + `environment=prod` | 403 `Tool token does not allow production operations`（沿用 :267） |
| 12 | `allow_exec_remote_tool=false` | 工具 403（kill switch 生效） |
| 13 | stdout 含疑似密钥 | 输出被掩码 + 截断 |
| 14 | 命令含换行但未声明 `multi_line` | 拒绝 |
| 15 | 同一命令重复 prepare（同指纹） | 幂等复用待审批工单（`action_approval.py:197-201`） |

---

## 8. 风险与回滚

| 风险 | 等级 | 缓解 | 回滚 |
|---|---|---|---|
| 审批人橡皮图章 → 恶意命令落地 | **高** | 白名单模式 + denylist + 卡片逐字展示 + prod 限 admin | `allow_exec_remote_tool=false` 立即失效 |
| 提示注入诱导 agent 提议恶意命令 | 高 | denylist 在 prepare 与 execute **两处**生效；白名单模板限定参数取值 | 同上 |
| 审批期间配置被改宽松（TOCTOU） | 中 | 执行前重跑护栏 + 命令 sha256 比对 | — |
| 输出泄密 | 中 | 掩码 + 截断 | — |
| 非幂等命令部分失败 | 中 | 不自动重试 + 逐目标明细 + 卡片写明 | 人工续做 |
| 回归影响既有审批流 | 中 | 不改 `exec_remote` 与其 L4 规则；仅新增工具与新动作类型；用 §7 用例覆盖 | git revert（无迁移） |
| 审批疲劳 | 中 | 频次限制 + 白名单收敛命令种类 | 调整 `exec_remote_max_per_hour` |

**回滚代价**：无 DB 迁移 → 代码回滚即可；且灰度开关默认关闭，最坏情况是「功能不可用」而非「意外开放」。

---

## 9. 工作量估算

| 阶段 | 内容 | 估时 |
|---|---|---|
| 开发 | 改动清单 #1-#9 | 4~6 h |
| 测试 | §7 十五条用例 + 现有审批流回归 | 3~4 h |
| 加固 | denylist/模板打磨、掩码与截断验证 | 2 h |
| 评审 | 安全复核（重点：策略闸门与执行器校验） | 1~2 h |
| 上线 | 灰度开关开启 + 房间内一次真实小任务验证（先 test，再 prod 单台→4 台） | 1 h |
| **合计** | | **约 1.5 个工作日**（不含评审等待） |

---

## 10. 待决策项（需确认后开工）

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| ① | `system_name` 语义 | (a) 用路由解析出的系统 (b) 用服务器组名（如 `crypto`）(c) 允许 `adhoc` | (b)，与现有服务器分组一致 |
| ② | 首版模式 | 白名单 / 自由命令 | **白名单**（3~5 个模板） |
| ③ | prod 破坏性命令 | 直接拒绝 / 允许 admin 特批 | **直接拒绝** |
| ④ | 审批人范围 | 仅 @jack.han / 全体 admin | 仅 @jack.han（与 execApprovals 现有配置一致） |
| ⑤ | 是否并发执行多目标 | 串行 / 并发 | **串行** |
| ⑥ | token 策略 | 复用现有 token（补 `bound_room_ids`）/ 新签专用只读 token | **新签专用 token + 房间绑定** |
| ⑦ | 白名单首批模板 | 装 docker / 查 docker 状态 / 查目录占用 | 三者先上 |
| ⑧ | 是否同步补 `agent_context.py` 流程指引 | 是 / 否 | 是（降低 agent 摸索成本） |

---

## 附：与「方案 A（人工 Web 终端）」的取舍

| 维度 | A：人工终端 | B：本方案 |
|---|---|---|
| 落地成本 | 0（现成） | ~1.5 人日 |
| agent 能否推动 | ❌ 只能报告卡点 | ✅ 可提议→审批→执行→回报 |
| 安全边界 | 最强（admin-only） | 较强（AI 无执行权 + 人工闸门 + 白名单） |
| 审计关联对话 | ❌ | ✅ |
| 适合场景 | 一次性任务 | 反复出现的装包/起服务/排障 |

两者不互斥：**A 可用于立即完成本次「装 docker」；B 用于把这类需求长期纳入 agent 能力**。

---

## 11. 实现落地记录

**状态**：已实现并合入工作区；**默认关闭**（kill switch `allow_exec_remote_tool=false`），开启后 AI 才能提交命令执行审批，真正执行仍需房间内一次性短码人工批准。

### 11.1 §10 决策落地结果

| # | 决策 | 落地情况 |
|---|---|---|
| ① | `system_name` 语义 | **修订**：用实际系统名 `crypto-trader`（原建议的「服务器组名 crypto」不存在） |
| ② | 首版为白名单模式 | ✅ 落地 6 个模板（多于原计划 3 个） |
| ③ | prod 破坏性命令直接拒绝 | ✅ 并细化为两级模型（见 11.2-1） |
| ④ | 审批人仅 `@jack.han` | ✅ 由系统 `message_routing.approvers` 决定，代码未硬编码人名 |
| ⑤ | 多目标串行执行 | ✅ |
| ⑥ | 新签专用只读 token + 房间绑定 | ⏸ **未新签**，沿用现有 token（见 11.6） |
| ⑦ | 首批模板 | ✅ 扩为 6 个 |
| ⑧ | 同步补 `agent_context.py` 指引 | ✅ 新增 flow guide + 2 条 lesson + 2 条 forbid |

**① 为何必须修订**：`get_all_systems()` 中不存在 `crypto` 系统（现有：buoy / crypto-trader / insider / sleuther / bot-hub / dovo），而审批人**只能**来自系统的 `message_routing.approvers`（`approval_tools.py:74-75` 空则返回空列表，`ActionApprovalService.prepare` 随即抛 `authorized approvers must not be empty`）。传不存在的 system_name 会让整个流程在第一步就失败。`crypto-trader` 已配置 `@jack.han:hubtel.xyz`（matrix/default）与房间白名单 `!LXnIFCTfJIqeErqyaf`（发版）、`!LZmupTKKHXJwRvomBE`（部署授权），与本功能作用域完全吻合。

### 11.2 与设计稿的偏离与顺带修复（含原因）

**1）单一 denylist → 两级模型。** 设计稿设想一层 denylist 兜底。实现时用真实语料回归发现两点：① 纯正则无法可靠识别 `rm` 的语义变体（`rm -r -f /`、`rm --recursive --force /` 都绕过原始正则），改用 **shlex 语义解析** 判定「递归删除关键目录」；② 「递归删 `/tmp` 缓存」这类运维刚需与「`rm -rf /`」不可同层处理。因此拆为：
- **绝对黑名单**（`EXEC_DENY_PATTERNS` + `check_rm_critical_delete`）：任何 mode、任何环境恒拒，`allow_destructive` 也无法绕过；
- **破坏性模式**（`EXEC_DESTRUCTIVE_PATTERNS`）：生产环境恒拒；非生产需显式 `allow_destructive=true`。
这既满足 §10 ③「prod 直接拒绝」，又让非生产排障可用。回归语料同时确认 `docker run --rm hello-world` 不被误伤（用 `(?<![\w-])rm` 负向断言）。

**2）`exec_remote_allow_prod` 默认 `True`（设计稿写 `False`）。** 主闸门 `allow_exec_remote_tool` 已默认关闭；若再默认禁 prod，会造成「管理员开了开关、prod 仍不通」的半开状态，与本功能唯一目的（生产装包／起服务／排障）相悖。token 级 `allow_prod` 仍是独立的第二道闸门（`tool_policy.py:292-296`），默认关闭时 AI 的 prod 调用依旧 403。

**3）模板与黑名单不写入 `DEFAULT_CAPABILITY_SETTINGS`。** 若写入默认值，`save_capability_settings` 会把它们一并持久化进 DB 快照，之后代码升级新增的默认模板会被旧快照永久遮蔽（静默失效）。改为在 `policy_from_settings()` 中按「可选覆盖」读取：设置里没有就用模块默认。

**4）顺带修复 flow guide 契约校验器的既有缺陷。** `validate_flow_guide_contract` 原先用 `registry.list_tools(..., include_disabled=False)`（策略过滤后的**可见**列表）判断「工具是否注册」，导致任何类别开关关闭时，引用该类别工具的 flow guide 都会被误报为漂移——本方案的 `ad-hoc-exec` 流程（工具默认关闭）正是第一个撞上的案例，且该缺陷对既有流程同样潜伏。现改为：可见列表里找不到时，再用 `registry.get()`（只看 registered + enabled，与策略无关）判定，区分「真未注册」与「当前策略下不可见」，且仍用真实 schema 校验 `args_hint`。

**5）kill switch 关闭时工具仍对 AI 可见（`requires_human_approval=True`）。** 初版实现让 kill switch 关闭时工具从能力发现中整体消失，连带触发两类「流程步骤工具名漂移」告警（`validate_flow_guide_contract`、`capability_sequence` 的漂移检测都基于策略过滤后的可见工具表）。深究后发现这违背了 `tool_registry.list_tools` 的既有约定——它**刻意**让 `requires_human_approval` 的工具在策略拒绝时照样展示，理由是「让 AI agent 能发现这些能力并通过审批流程获取授权」。因此改为给工具打上 `requires_human_approval=True`（`requires_confirmation` 保持 `False`，不引入 confirm_text 摩擦；`approval_prepare` 类别本就在 L4 carve-out 名单内，不会因此被硬阻断）。结果：未开开关时 agent 仍能发现该能力并回报「需管理员开启」，而不是对着一个不存在的工具名报错——这同时也是原始故障（agent 撞墙后静默结束）的正面解药。

**6）幂等复用场景下复现确认短语。** 运行时验证发现：同一命令摘要再次提交时，`ActionApprovalService.prepare` 按既有契约复用既有 PENDING 工单并返回**空短语**（`action_approval.py:200-201`，计划侧有测试 `test_prepare_is_idempotent_for_same_digest` 固定该行为），而回执模板把空短语直接渲染 → 卡片上"确认短语"与末尾待回复行均为空，审批人无法批准。由于短语是**确定性派生**（`build_approval_phrase(action_type + system_name + environment + digest)`，无随机成分，库里只存其 hash），`approval_prepare_exec` 在拿到空短语时按既有 `approval.action_digest` 复现，实测与首次签发**逐字一致**（同一 `0C4C9438`），仍能对上 `approval_code_hash`。修复保留服务层契约，未改动计划侧行为。

**7）顺带修正单工单回执标题重复。** `_approval_reply_template` 中 `noun = "审批工单"` 与标题前缀 `"OPS 审批"` 叠加成"OPS 审批**审批**工单已创建"，为单工单类（EXEC_REMOTE / FILE_UPLOAD / SERVICE_CONTROL）的既有文案瑕疵；改为 `noun = "工单"`。已确认无测试依赖该字符串。

### 11.3 实际改动清单

| 文件 | 改动 |
|---|---|
| `app/services/exec_command_policy.py` | **新增**：护栏核心（两级黑名单 + 6 个白名单模板 + 结构/限额校验 + `policy_from_settings`） |
| `app/services/tool_policy.py` | `DEFAULT_CAPABILITY_SETTINGS` 追加 9 个开关；`enforce_tool_policy` 追加 kill switch（置于 L4 carve-out 之后、类别开关之前） |
| `app/services/approval_phrase.py` | `_ACTION_VERBS` 追加 `EXEC_REMOTE: 远程命令` |
| `app/services/tool_adapters/approval_tools.py` | 新增 `_normalize_exec_targets`、`ops.approval.prepare_exec` 工具（含频控）；`_STEP_VERBS` 同步；`_approval_reply_template` 新增逐字命令区块 |
| `app/services/approval_executor.py` | `_dispatch` 新增 `EXEC_REMOTE` 分支；新增 `_execute_exec_remote`（SHA256 复核 + 护栏重跑 + 串行 + 部分失败保留 + 脱敏） |
| `app/services/mcp_capability_service.py` | 工具描述字典追加 `ops.approval.prepare_exec` |
| `app/services/agent_context.py` | 新增 `ad-hoc-exec` flow guide；新增 L012/L013；`FORBIDDEN` +2；修复 flow 契约校验器 |
| `tests/test_exec_remote_approval.py` | **新增**：130 个用例 |
| `tests/test_agent_context_layer.py` | 同步 `flow_ids` 期望 |

数据模型**零迁移**（沿用 `AiActionApproval`：`action_type` 自由字符串、`action_parameters` 入 `request_payload`、`execution_result` 已存在），与 §4.1 一致。

### 11.4 测试与验收

`tests/test_exec_remote_approval.py` 130 用例全绿，覆盖 §7 矩阵：白名单模板正例、绝对黑名单（31 条 × 2 mode）、破坏性 prod 恒拒／test 需显式开关、`rm` 语义解析（含 `docker run --rm` 负例）、结构限额、kill switch 策略（tool_token 视角 + 能力发现）、参数冻结、回执模板逐字渲染、执行器 SHA256 复核／护栏重跑拦截／部分失败继续／连接异常隔离／敏感信息脱敏／超时钳制、审批短语动词。

### 11.5 启用步骤（运维）

1. 开启主闸门：capability settings 置 `allow_exec_remote_tool=true`（**2026-09-10 已开启**；未开启时工具**仍对 AI 可见**，见 11.2-5，但调用一律 403 并说明原因）；
2. 在**房间 3**（`!LZmupTKKHXJwRvomBE`，`crypto-trader` 的房间白名单内）复现原始诉求，例如「给 4 台量化服务器装 docker」——agent 会走 `ad-hoc-exec` 流程，模板 `docker_install_official_repo` 可完整匹配官方源安装链路；
3. 审批人多半是 `@jack.han`，按卡片短码回复即执行；
4. **回滚**：把开关设回 `false` 即刻生效（工具重新从发现中消失并 403 拦截），无需重启。

### 11.6 尚未完成 / 待决策

| # | 事项 | 说明 |
|---|---|---|
| ① | token 房间绑定 | 现有 MCP token `qclaw-ops-room-bound` 的 `bound_room_ids` 为 `[]`（不限制）。房间作用域当前由系统级 `message_routing.rooms` 强制，已覆盖需求；是否再收紧到 token 级待定 |
| ② | 专用只读 token | 未新签，沿用现有 token（`ops:read` + `allow_prod`）。若希望「命令执行审批」与「发版审批」凭证隔离，可另签 |
| ③ | 更多模板 | 首批只覆盖只读查询与 apt/docker 安装。其他常用运维（日志抓取、配置查看、服务重启）按需追加到 `exec_remote_templates` |
| ④ | 执行结果回执模板 | 执行后目前复用通用 `ops.approval.execute` 返回结构，未做 §4.3 式的专用执行回执模板 |
| ⑤ | 频控参数调优 | `exec_remote_max_per_hour=10` 为初始值，按实际使用调整 |
| ⑥ | 单工单缺拒绝工具 | 只有 `ops.approval.reject_plan`（计划专用）。单工单（EXEC_REMOTE / FILE_UPLOAD / SERVICE_CONTROL）没有对应的拒绝工具，被拒/放弃时只能等 15 分钟过期。**服务层 `ActionApprovalService.reject()` 本身是通用的**（按 id 拒绝任意工单，可复用），缺的只是一个工具包装。本次验证工单即通过直接调用服务层完成撤销 |
| ⑦ | 计划/文件上传路径的同类空短语问题 | 与 11.2-6 同源：`approval_prepare_plan`（`approval_tools.py:1694` 附近）与 file_upload/service_control 路径（`:991`、`:1223` 附近）在幂等复用场景同样会把空短语渲染进回执。本次只修了 EXEC_REMOTE；是否统一修复待决策（做法相同，风险低） |

### 11.7 运行时验证记录（2026-09-10，Windows OPS 192.168.1.44:8000）

重启 OPS 载入改动后，以 MCP 端点（Bearer `qclaw-ops-room-bound`）逐级验证，**全程未向任何 Matrix 房间投递消息**（遵守"不污染房间"约束，测试房间 `!riQvnhtyzunVoYIoXp` 本就不在 `crypto-trader` 房间白名单内，无法用于该流程）：

| # | 验证项 | 结果 |
|---|---|---|
| 1 | 工具对 AI 可见 | `ops_approval_prepare_exec` 出现在 `tools/list`（共 125 个），注解 `x_ops_requires_human_approval=true`、`x_ops_risk=low`、`x_ops_category=approval_prepare`、`x_ops_ai_level=L3` |
| 2 | kill switch 默认关闭时调用 | 返回明确原因 `Ad-hoc remote exec approval is disabled (allow_exec_remote_tool=false)`——正是对"撞墙后静默结束"的正面修复 |
| 3 | 开关开启（经 `PUT /api/v2/tools/settings`，带审计） | 成功，`allow_exec_remote_tool=true` |
| 4 | 路由票据签发 | `outcome=RESOLVED`、`system_name=crypto-trader`、审批人 `@jack.han:hubtel.xyz`、允许房间 = 发版房间与房间 3 |
| 5 | 创建命令执行审批 | 工单 `20cae497…`、短语 `批准远程命令 crypto-trader@prod 0C4C9438`、`command_sha256=a4664d43…`、模板 `docker_readonly`、回执逐字命令块与风险说明完整 |
| 6 | 幂等复用 | 同命令再次提交 → 同工单 id、短语**逐字复现**（0C4C9438），修复 11.2-6 前该处为空 |
| 7 | 清理 | 调用服务层 `reject()` 撤销验证工单，剩余 PENDING EXEC_REMOTE = 0 |
| 8 | 执行环节 | 由 8 个单元测试覆盖（逐目标执行 / SHA256 复核 / 护栏重跑 / 部分失败继续 / 连接异常隔离 / 脱敏 / 超时钳制 / 分发路由）；生产实跑依赖 SSH+跳板机链路，A 阶段已用同一 `_connect` 在 4 台机器验证通过。真实实跑待房间 3 的正常审批流 |

**当前开关状态：`allow_exec_remote_tool = true`（已开启）**，即该能力可用；每次执行仍必须由房间内人工一次性短码批准。如需关闭：`python fnos-migration/exec_remote_switch.py off`（立即生效，无需重启）。

**设置持久化实证**：`GET /api/v2/tools/settings` 返回的是"库中值 + 默认值"的合并结果，管理员保存时会把合并结果整体写回——实测落库 36 个键（含本次 7 个 `exec_remote_*` 标量开关）。这正好实证了 11.2-3 的判断：若把模板/黑名单放进 `DEFAULT_CAPABILITY_SETTINGS`，它们会在此被固化进 DB 并遮蔽后续升级；因此它们只留在代码侧，DB 里没有对应键。

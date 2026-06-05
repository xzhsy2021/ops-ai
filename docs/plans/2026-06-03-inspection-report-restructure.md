# 巡检报告改造方案

> 日期：2026-06-03
> 范围：巡检中心生成的单 run / 多 run 合并报告
> 目标读者：运维人员，需要在最短时间内看清楚"哪台机器、什么问题、怎么处理"

---

## 1. 问题诊断（基于代码核查）

### 1.1 根本症结

| # | 问题 | 代码位置 |
|---|---|---|
| 1 | **数据未结构化**：`InspectionItemResult.message` 只是一句话总结；`raw_output` 是 bash 原始输出截断 4000 字符。关键事实（哪些账号是 UID=0、哪个磁盘多满、哪个 IP 在爆破）从未被提取成结构化字段 | `app/db/models.py` `InspectionItemResult` |
| 2 | **渲染器是平表硬拼**：把所有 item 倒成一张大表，按服务器顺序简单拼接。8 台机器 = 8 段重复表，一屏 24 个 OPEN 风险无主次 | `app/services/report_center.py:126` `_inspection_markdown()` |
| 3 | **判定逻辑只看阈值不展示原料**：`_analyze_accounts()` 算出 "2 个 UID=0" 后只写 message，没保留账号名；其他 analyzer 同理 | `app/services/inspection_center.py:537-630` |
| 4 | **前端只是 markdown viewer**：直接渲染 md，没有结构化卡片。改 md 即可立刻见效，不用动前端 | `frontend/src/pages/ReportCenterPage.tsx` |

### 1.2 示例报告的具体劣化点

| 巡检项 | 现状 | 应该长什么样 |
|---|---|---|
| 账号安全 | "发现多个 UID=0 特权账号：2 个" | 列出账号名 / UID / Shell / Home |
| 磁盘 | "磁盘空间未发现明显异常" | 列出每个挂载点使用率（即使全绿也展示） |
| 端口 | "检测到高风险端口监听：:22" | 列端口 + 进程 + 绑定 IP + 是否外网 |
| 登录 | "检测到 root 远程登录记录" | Top 攻击 IP 表 + root 登录历史表 |
| 备份 | "备份任务及近期备份文件未发现明显异常" | 列出找到的备份目录 / 文件 + 大小 / 时间 |
| 8 台机器 | 同样的表重复 8 次 | 折叠式：默认只展开有风险的 |
| 评分 70 | 所有机器全是 70 | 提示判定模型粒度不足（本期不修复，记录） |

---

## 2. 改造目标

1. 每个巡检项都呈现 **"判定 + 关键事实表 + 建议"** 三段式，而不是单句结论
2. **按风险严重度排序、按服务器折叠**，运维一眼看出"哪几台要立刻处理"
3. 保留并改进现有评分 / 风险等级机制，不破坏 OPEN/PROCESSING/FIXED 闭环流转
4. **老报告兼容**：不重新生成历史报告，但下次巡检后立刻能看到新格式
5. **不改前端**（先期）：markdown 自包含足够清晰，前端改造放后期

---

## 3. 数据模型增量

### 3.1 `InspectionItemResult` 新增 1 列

```python
parsed_facts = Column(JSON, nullable=True, default=dict)
```

按 category 定义 schema：

| Category | parsed_facts 结构 |
|---|---|
| `ACCOUNT_SECURITY` | `{uid0_accounts: [{user, uid, shell, home}], login_users: [{user, shell}], login_user_count, total_users}` |
| `DISK` | `{filesystems: [{mount, type, total, used, avail, pct}], dirs_usage: [{path, size}], max_pct}` |
| `BACKUP` | `{backup_dirs: [{path, exists, files: [{name, size, mtime}]}], cron_lines: [...]}` |
| `LOGIN_SECURITY` | `{success_logins: [{user, ip, tty, start, duration, active}], top_attackers: [{ip, count, attempted_users}], failed_total}` |
| `PROCESS_PORT` | `{high_risk_ports: [{port, proto, service, bind, is_world, pid, user}], top_cpu_procs: [{pid, user, cpu, mem, cmd}], listening_count}` |

### 3.2 迁移

走 `app/db/migrations/runner.py` 加一条：

```python
("058_002", "alter_inspection_item_results_add_parsed_facts",
 "ALTER TABLE inspection_item_results ADD COLUMN parsed_facts JSON"),
```

幂等：已存在列时跳过。SQLite JSON 列零风险。

### 3.3 `InspectionRun.summary` 不动

保持 "巡检完成：评分 X" 句式，兼容老 UI。

---

## 4. 解析器实现（核心工作量）

每个 `_analyze_*()` 函数除了输出 `(status, risk_level, message, suggestion)`，多输出 `parsed_facts: dict`。

### 4.1 `_analyze_accounts()` 改造示例

```python
def _analyze_accounts(raw: str) -> tuple[str, str, str, str, dict]:
    uid0_accounts, login_users = [], []
    total_users = 0
    in_passwd = False
    for line in raw.splitlines():
        s = line.strip()
        if s == "---PASSWD---":
            in_passwd = True; continue
        if s.startswith("---"):
            in_passwd = False; continue
        if not (in_passwd and ":" in s):
            continue
        parts = s.split(":")
        if len(parts) < 7:
            continue
        user, _, uid, _, _, home, shell = parts[:7]
        total_users += 1
        if uid == "0":
            uid0_accounts.append({"user": user, "uid": 0, "shell": shell, "home": home})
        if shell.rstrip().endswith(("bash", "sh", "zsh")):
            login_users.append({"user": user, "shell": shell})

    facts = {
        "uid0_accounts": uid0_accounts,
        "login_users": login_users[:20],
        "login_user_count": len(login_users),
        "total_users": total_users,
    }

    if len(uid0_accounts) > 1:
        names = ", ".join(a["user"] for a in uid0_accounts)
        return ("RISK", "HIGH",
                f"发现 {len(uid0_accounts)} 个 UID=0 特权账号：{names}",
                "核查非 root 的 UID=0 账号，去除或停用，并排查入侵痕迹",
                facts)
    if len(login_users) > 10:
        return ("WARNING", "LOW",
                f"可登录账号偏多（{len(login_users)}），存在权限蔓延风险",
                "梳理闲置账号，关闭非必要 shell",
                facts)
    return ("PASS", "NONE",
            f"账号清单正常（{total_users} 个系统账号，{len(login_users)} 个可登录，{len(uid0_accounts)} 个 UID=0）",
            "保持定期审计",
            facts)
```

### 4.2 5 个 analyzer 改造工作量

| Analyzer | 关键解析点 | 工时 |
|---|---|---|
| `_analyze_accounts` | 解析 `/etc/passwd`，UID=0 + 可登录 shell 列表 | 0.5h |
| `_analyze_disk` | 解析 `df -PTh`，挂载点清单 + 使用率 + du 目录大小 | 0.5h |
| `_analyze_backup` | 解析 `ls -lah` + `find` 时间排序，备份目录命中 + 最新时间 | 1h |
| `_analyze_login` | 解析 `last -n 80` + `lastb -n 80`，Top 攻击 IP 聚合 | 1.5h |
| `_analyze_port` | 解析 `ss -ntulp`，端口/进程/绑定 IP；判定 0.0.0.0 vs 127.0.0.1 | 1h |
| **合计** | | **4.5h** |

---

## 5. 渲染器重写

### 5.1 新报告结构

```markdown
# 巡检报告 - {名称或合并标识}

## 📊 概览
- 时间: 2026-06-03 04:00 → 04:01
- 范围: 8 台服务器
- 总评分: 70/100 · 🔴 8 高 / 🟠 8 中 / 🟢 8 低
- 未闭环: 24 项

## 🚨 风险摘要（按严重度排序，只列非 PASS）

### 🔴 高危 (8)
| 服务器 | 巡检项 | 关键事实 | 建议 |
|---|---|---|---|
| 8.216.33.60-日志-阿里 | 账号安全 | UID=0: root, special_root | 立即核查特权账号 |
| ... | | | |

### 🟠 中危 (8)
| 服务器 | 巡检项 | 关键事实 | 建议 |
|---|---|---|---|
| 203.0.113.10-阿里测试 | 进程端口 | 高危外网: :22, :27017, :6379 | 限制白名单 |
| ... | | | |

### 🟢 低危 (8)
...

## 📦 按服务器展开

<details open>
<summary>🔴 8.216.33.60-日志-阿里 · 评分 70 · 1高/1中/1低</summary>

#### 🔴 账号安全 [HIGH]
**判定**: 发现 2 个 UID=0 特权账号

| 账号 | UID | Shell | Home |
|---|---|---|---|
| root | 0 | /bin/bash | /root |
| special_root | 0 | /bin/bash | /home/special_root |

**建议**: 立即核查并停用非必要 root 账号
**证据**: `evidence_id=6e0338...`

#### 🟢 磁盘空间 [PASS]
**判定**: 所有挂载点使用率正常 (最高 10%)

| 挂载点 | 类型 | 总量 | 已用 | 可用 | 使用率 |
|---|---|---|---|---|---|
| / | ext4 | 197G | 19G | 170G | 10% |
| /boot/efi | vfat | 189M | 12M | 177M | 7% |

**目录占用**: /var/log 2.0G · /data 13G · /opt 2.0M

#### 🟠 进程端口 [MEDIUM]
**判定**: 检测到 1 个高风险端口监听

| 端口 | 协议 | 进程 | 绑定地址 | 暴露面 |
|---|---|---|---|---|
| 22 | tcp | sshd | 0.0.0.0:22 | 🌐 公网 |

**Top CPU 进程**:
| PID | 用户 | %CPU | %MEM | 命令 |
|---|---|---|---|---|
| 984 | root | 2.5 | 0.9 | AliYunDunMonitor |
| 236164 | root | 0.7 | 19.7 | loki-linux-amd64 |

**建议**: 22 端口加白名单 / 改非默认端口

#### 🟢 登录安全 [LOW]
**判定**: root 有远程登录历史，并检测到大量 SSH 爆破尝试

**root 登录** (最近 5 次):
| 时间 | 来源 IP | 终端 | 时长 |
|---|---|---|---|
| 2026-05-29 09:03 | 47.86.9.194 | pts/0 | 00:47 |
| 2026-03-18 13:50 | 47.86.9.194 | pts/0 | 20:51 |

**失败登录 Top 攻击源** (近 80 条):
| 攻击 IP | 失败次数 | 主要尝试账号 |
|---|---|---|
| 60.10.50.90 | 78 | deploy, ubuntu, test, ftpuser, jenkins, ... |
| 80.94.92.164 | 3 | lighthou |
| 80.94.92.187 | 2 | solana |

**建议**: 禁用 root SSH 直登；启用 fail2ban；SSH 端口加白名单

#### 🟢 备份任务 [PASS]
**判定**: 未发现备份目录或备份脚本（如有备份需求，请配置）

**已检查路径**: /backup（不存在）、/data/backups（不存在）
**cron 备份任务**: 无
**建议**: 如有重要数据，配置定时备份脚本

</details>

<details>
<summary>🔴 203.0.113.10-阿里测试 · 评分 70 · 1高/1中/1低</summary>
... (同结构)
</details>
```

### 5.2 折叠策略

- 用 GitHub-flavored markdown `<details>` + `<summary>`，所有主流 viewer 支持
- 含 HIGH 风险的服务器：`<details open>`（默认展开）
- 全 PASS 的服务器：`<details>`（默认折叠）
- 在 `<summary>` 行内带评分和 H/M/L 计数，不展开也能扫到要点

### 5.3 重写函数

| 函数 | 文件 | 改动 |
|---|---|---|
| `_inspection_markdown()` | `app/services/report_center.py` | 单 run 报告 |
| `_inspection_markdown_for_runs()` | `app/services/report_center.py` | 多 run 合并报告 |
| `_render_category_*()` | 新增 5 个辅助 | 每个 category 独立渲染 |

---

## 6. 向后兼容

| 场景 | 处理 |
|---|---|
| 老 `InspectionItemResult.parsed_facts = NULL` | 渲染器降级为旧版表（仅显示 message/suggestion），不报错 |
| 历史 8 份报告 | 不重新生成，标记为 `legacy` 即可 |
| 用户重跑巡检 | 新 analyzer 自动写入 `parsed_facts`，新报告立刻是新格式 |
| 前端 ReportCenterPage | 不动，markdown 渲染即可 |

---

## 7. 任务拆分

| # | 任务 | 工时 | 依赖 |
|---|---|---|---|
| T1 | 加 `parsed_facts` 列 + 迁移条目 + 模型 `to_dict()` 包含此字段 | 0.5h | — |
| T2 | 重写 5 个 `_analyze_*()`，返回 `parsed_facts` | 4.5h | T1 |
| T3 | 调整 `inspection_center.py` 写 `InspectionItemResult` 的位置，把 facts 写入新列 | 0.5h | T2 |
| T4 | 重写 `_inspection_markdown()` 与 `_inspection_markdown_for_runs()` 按新结构输出 | 3h | T2 |
| T5 | 编写 5 个 category 专用 markdown 模板辅助函数 | 1.5h | T4 |
| T6 | 单元测试：每个 analyzer 给 1 份真实 raw_output 样本，断言 facts 字段 | 1.5h | T2 |
| T7 | 契约测试：单 run + 多 run 报告渲染输出，包含关键字段 | 1h | T4 |
| T8 | 手工跑一次 8 台机器的合并巡检，校验新报告 | 0.5h | T4 |
| **合计** | | **~13h** | |

### 7.1 推荐执行顺序

1. **Day 1**：T1 → T2（只先做 accounts + disk 两个，最快见效）→ T3 → T4 雏形 → T8 验证
2. **Day 2**：T2 剩余 3 个 analyzer（backup / login / port）→ T5 模板细化 → T6 单测
3. **Day 3**：T7 契约测试 → T8 全量回归 → 文档归档

---

## 8. 验收标准

### 8.1 功能验收

跑一次单服务器巡检，新报告里能看到：

- [ ] 账号安全项有 UID=0 账号清单表（哪怕只 root 一个也展示）
- [ ] 磁盘项有所有挂载点的表，最高使用率单独高亮
- [ ] 端口项有端口/服务/绑定/暴露面四列表
- [ ] 登录项有 root 登录 + Top 攻击 IP 两张表
- [ ] 备份项明确说明检查了哪些路径，发现/未发现什么

跑一次 8 服务器合并巡检：

- [ ] 报告头部有"按严重度排序"的风险摘要表，一屏看完
- [ ] 每台服务器一个 `<details>` 折叠块
- [ ] 同样的"账号安全"问题不再重复 8 段散文
- [ ] 含 HIGH 的服务器默认展开，全 PASS 的默认折叠

### 8.2 兼容性验收

- [ ] 老报告点开仍能正常显示（降级模式）
- [ ] `pytest tests/test_inspection_*.py` 全通过
- [ ] 数据库迁移幂等：第二次启动不报错

---

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 解析 `last/lastb` 输出格式跨发行版有差异 | 解析器"尽力而为"——解析失败时降级为 raw 摘要 |
| `parsed_facts` JSON 字段过大 | 限制每个列表最多 20 项，超出标 `truncated=true` |
| 同份评分 70 是固定模板算的 | **不在本次范围**——评分模型优化另起一轮，本次只改"展示" |
| markdown `<details>` 在某些渲染器不展开 | 提供 `[展开/折叠]` 文字提示作为 fallback |
| 老 run 没有 `parsed_facts` 怎么展示 | 渲染器检测到 NULL 时走旧渲染路径，确保历史报告不破 |

---

## 10. 不做的事（明确边界）

- ❌ 不改前端 `ReportCenterPage.tsx`（markdown 渲染足够清晰，前端改造放后期）
- ❌ 不改评分公式（70 分一致的问题留待下一轮"评分模型优化"专项）
- ❌ 不改 `InspectionIssue` 表 / Issue 流转（保持现状）
- ❌ 不引入图表（md 文档天然不支持，要画图表得做前端）
- ❌ 不重写已生成的历史报告（标记 legacy 即可）

---

## 11. 附录：相关代码位置速查

| 用途 | 文件 | 行 |
|---|---|---|
| 报告渲染入口 | `app/services/report_center.py` | `_inspection_markdown` 126 |
| 多 run 合并 | `app/services/inspection_center.py` | `report_payload_for_runs` 2275 |
| 单 run 详情 | `app/services/inspection_center.py` | `report_payload` 1755 |
| 服务器巡检规约 | `app/services/inspection_center.py` | `_server_check_specs` 527-551 |
| Account analyzer | `app/services/inspection_center.py` | `_analyze_accounts` 571-578 |
| Disk analyzer | `app/services/inspection_center.py` | `_analyze_disk` 613-626 |
| 模型 InspectionItemResult | `app/db/models.py` | 796-816 |
| 模型 InspectionEvidence | `app/db/models.py` | 781-793 |
| 迁移注册表 | `app/db/migrations/runner.py` | — |
| 前端报告页（不改） | `frontend/src/pages/ReportCenterPage.tsx` | — |

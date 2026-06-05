# 巡检规则与判定标准合理性整改计划

> 日期：2026-06-04
> 范围：[app/services/inspection_center.py](../../app/services/inspection_center.py) 全部 11 个 analyzer + DEFAULT_THRESHOLDS + 评分模型
> 关联：[2026-06-03 巡检报告改造方案](./2026-06-03-inspection-report-restructure.md)
> 目标读者：负责巡检中心的研发与运维，期望在 1-2 个工作日内根治"所有机器都 70 分 / 全量误报"问题

---

## 0. TL;DR

巡检中心当前的判定规则在 11 个 analyzer 上存在不同程度的"误报、漏报、阈值口径偏差"问题：

- **内存** 用 `used` 列含 cache，几乎所有跑过一段时间的机器都会被误报 ≥85%
- **基础服务** 写死 `[nginx, mysql, redis, etcd, ...]` 一刀切，单跑 Web 的机器会因没装 MySQL 报 HIGH
- **自定义命令** 把 `grep` 无匹配（exit=1）误判 HIGH
- **磁盘** 不过滤 overlay/squashfs，容器宿主必报 100%
- **shadow 不可读** 时静默 PASS，空密码检测形同虚设
- **登录失败阈值** 不分单 IP，任何公网 22 端口的机器一晚上 ≥10 必报 HIGH

整改按 **风险半径 × 修复成本** 分 P0/P1/P2/P3 四档。**P0+P1 ≈ 5h 可消除当前 80% 的误报**，能直接解决 [改造方案文档](./2026-06-03-inspection-report-restructure.md) 提到的"评分 70 一致"问题。

---

## 1. 修复优先级总览

| 优先级 | 缺陷 | 修复方向 | 工时 | 依赖 |
|---|---|---|---|---|
| P0-1 | 内存 used 列包含 buff/cache | 改用 `available` 列 / `/proc/meminfo` 的 `MemAvailable` | 0.5h | — |
| P0-2 | core_services 写死 → 全机器 HIGH | 改为只报 `systemctl --failed` 真名单，core_services 退化为"建议监控"列表 | 0.5h | — |
| P0-3 | 自定义命令 exit=1 误判 HIGH | 0/1 视为正常；用词边界匹配；区分 stdout/stderr | 0.5h | — |
| P0-4 | 磁盘 overlay/squashfs 未过滤 | 按 fstype 黑名单跳过；NFS stale 单独标识 | 0.5h | — |
| P1-1 | shadow 不可读静默 PASS | 检测 `permission denied` → SKIPPED + LOW | 0.3h | — |
| P1-2 | 端口表口径与文档不一致 | 22/8080 入表；27017/6379 升 HIGH；IPv6 通配修复 | 0.5h | — |
| P1-3 | 登录失败阈值不分单 IP | 双指标（单 IP 次数 + IP 数）；降级路径口径对齐 | 1h | — |
| P1-4 | 云环境 firewalld 不存在静默 PASS | `unknown/not loaded` → INFO 提示 | 0.3h | — |
| P1-5 | 评分模型触底 | 改"每类取最高扣分 + 加权" | 1h | — |
| P2-1 | history 检测策略 | 移除自伤关键字 + 检 0 字节 history | 0.5h | — |
| P2-2 | CPU 单次采样 | 改持续两次采样均 ≥阈值 | 0.5h | — |
| P2-3 | 备份路径过窄 + 仅查用户 cron | 接受自定义路径 + 扫 `/etc/cron.*` + systemd timer | 1.5h | — |
| P3-1 | root 直登一刀切 MEDIUM | 阈值化 + 白名单 IP | 0.5h | — |
| P3-2 | 受限 shell 误判 + 200 行截断 | 完整路径匹配；移除 `head -200` | 0.3h | — |
| P3-3 | UID=0 白名单 | 接入 [InspectionBaseline](../../app/db/models.py) `baseline_type=ACCOUNT_UID0` | 1h | T0 |
| P3-4 | 缺口巡检项（NTP/SSH 配置/CVE/sudoers/SELinux） | 新增 5-6 个 analyzer | 4-6h | — |
| **合计** | | | **~13h** | |

P0 ≈ 2h | P1 ≈ 3h | P2 ≈ 2.5h | P3 ≈ 5.5h（其中 P3-4 占 4-6h）

---

## 2. 具体修复方案

### 2.1 P0 项（必须先修，否则评分模型继续失真）

#### P0-1 · 内存使用率算法

**位置**：[inspection_center.py:1226-1248](../../app/services/inspection_center.py#L1226-L1248)

**问题**：`mem_used = _parse_mem_value(parts[2])` 取 `free` 输出第 3 列（`used`），Linux 上该列**包含 buff/cache**。任何启动数小时的服务器 `used/total` 都会 ≥80%，触发 `mem_medium_pct=85` 误报。

**修复**：

```python
# 1) 命令改为同时输出 meminfo（更稳定的指标来源）
command = "(free 2>/dev/null || true); echo '---MEMINFO---'; (cat /proc/meminfo 2>/dev/null | head -10 || true)"

# 2) analyzer 优先用 MemAvailable 计算
def _analyze_memory(out, err, code, thresholds=None):
    mem_avail_kb = mem_total_kb = swap_total_kb = swap_used_kb = 0
    in_meminfo = False
    for line in out.splitlines():
        s = line.strip()
        if s == "---MEMINFO---":
            in_meminfo = True; continue
        if in_meminfo:
            if s.startswith("MemTotal:"):     mem_total_kb = int(s.split()[1])
            if s.startswith("MemAvailable:"): mem_avail_kb = int(s.split()[1])
            if s.startswith("SwapTotal:"):    swap_total_kb = int(s.split()[1])
            if s.startswith("SwapFree:"):
                swap_used_kb = swap_total_kb - int(s.split()[1])

    if mem_total_kb > 0 and mem_avail_kb > 0:
        mem_pct = int(round((mem_total_kb - mem_avail_kb) / mem_total_kb * 100))
    else:
        # 老内核兜底：用 free 但读 available 列（第 7 列）
        ...
        return ("LOW", "WARNING", "内存指标缺失（meminfo/available 都未读到）",
                "确认 /proc/meminfo 可读，或升级 procps 包以提供 available 列", facts)
```

**验收**：

- 跑过 24h 的服务器 `mem_pct` 应与 `htop` 显示的 used 行一致（含 cache 排除），通常 < 50%。
- 添加测试样本：[tests/services/test_inspection_analyzers.py](../../tests/services/test_inspection_analyzers.py)（新增）的 `test_memory_uses_available_not_used()` 用真实 `/proc/meminfo` 样本断言 < 50%。

---

#### P0-2 · 基础服务 core_services 写死

**位置**：[inspection_center.py:1454-1506](../../app/services/inspection_center.py#L1454-L1506)、[inspection_center.py:803-805](../../app/services/inspection_center.py#L803-L805)

**问题**：`core_services` 列表对所有机器都断言"应该运行 nginx + mysql + redis + ..."，单跑 Web 的机器 `systemctl is-active mysql` = `unknown` → 命中 `inactive` 判断 → HIGH 误报。这是当前"8 台机器全 70 分"的核心根因。

**修复**：

```python
def _analyze_service(out, err, code, thresholds=None):
    cfg = (thresholds or {}).get("SERVICE_STATUS", {}) or {}
    # 不再用作"必须运行"清单，仅用于在 facts 中标注"运维关注"
    watch_services = list(cfg.get("watch_services") or ["nginx","caddy","mysql","redis","postgresql","docker","etcd"])

    failed_units = []
    in_failed = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("UNIT") or s.startswith("●"):
            in_failed = True
        if in_failed and "loaded units listed" in s.lower():
            in_failed = False
        if in_failed and "." in s and "failed" in s.lower():
            failed_units.append(s.split()[0].lstrip("●").strip())

    watched_status = {}
    for svc in watch_services:
        for line in out.splitlines():
            sl = line.strip().lower()
            if sl.startswith(svc + ":"):
                watched_status[svc] = sl.split(":",1)[1].strip()
                break

    facts = {
        "failed_units": failed_units[:10],
        "watched_status": watched_status,
        "criteria": "只在 systemctl --failed 真名单非空时告警；watch_services 仅作为 facts 信息呈现",
    }

    if failed_units:
        return ("MEDIUM" if len(failed_units) < 3 else "HIGH", "RISK",
                f"systemd 失败单元 {len(failed_units)} 个：{', '.join(failed_units[:5])}",
                "查看 journalctl -u <unit> 排查根因",
                facts)
    return ("NONE", "PASS", f"未发现 systemd 失败单元（已观察 {len(watched_status)} 个 watch 服务）", "持续关注关键服务日志", facts)
```

**验收**：

- 单跑 Nginx 的机器：`watched_status={"nginx":"active","mysql":"unknown",...}` 但 status=PASS（之前是 HIGH）。
- 一台机器有 `nginx.service failed` 时，触发 MEDIUM，message 含具体单元名。
- 多机器合并报告评分不再统一 70 分。

---

#### P0-3 · 自定义命令 analyzer 退出码与关键字误判

**位置**：[inspection_center.py:1628-1673](../../app/services/inspection_center.py#L1628-L1673)

**问题**：

1. `grep` 无匹配 exit=1 是 Unix 惯例，当前 1655 行直接 `code not in (0, None) → HIGH`，导致大多数 grep 类自定义巡检都误报。
2. `error_keywords = ["error","fail",...]` 子串匹配会命中 `error_count=0`、`/var/log/nginx/error.log` 路径、`uid=5000` 等。

**修复**：

```python
import re
NOISY_PATHS = re.compile(r"/(var/log|error[._-]?log|access[._-]?log)\b", re.I)
ERROR_WORD = re.compile(r"\b(error|fail|critical|panic|fatal|denied|exception|traceback)\b", re.I)
COUNT_ZERO = re.compile(r"\b(error|fail)[_\-]?(count|num|n|total)?\s*[:=]\s*0\b", re.I)

def _analyze_custom_command(out, err, code, thresholds=None):
    # 退出码：0 与 1 都视为业务正常（grep 习惯），≥2 才视为执行失败
    exit_ok = code in (None, 0, 1)

    out_clean = NOISY_PATHS.sub(" ", out or "")
    # 把"error_count=0 / failed: 0"等正常报告语清掉
    out_clean = COUNT_ZERO.sub(" ", out_clean)
    err_clean = NOISY_PATHS.sub(" ", err or "")

    matched_out = set(m.group(0).lower() for m in ERROR_WORD.finditer(out_clean))
    matched_err = set(m.group(0).lower() for m in ERROR_WORD.finditer(err_clean))
    matched = list(matched_out | matched_err)

    facts = {
        "exit_code": int(code) if code is not None else -1,
        "output_lines": len([l for l in (out or "").splitlines() if l.strip()]),
        "matched_keywords": matched[:10],
        "criteria": "exit ≥2 → HIGH；exit ∈ {0,1} 且无 error 词边界命中 → PASS；命中 error/fatal 词边界 → MEDIUM；仅 stderr 非空 → INFO",
    }

    if not exit_ok:
        return ("HIGH", "RISK", f"自定义命令执行失败（exit={code}）", "确认目标环境兼容性与必要变量替换", facts)
    if matched:
        return ("MEDIUM", "WARNING", f"输出命中错误关键字（词边界）：{', '.join(matched[:5])}",
                "核查输出内容，确认是否为预期告警", facts)
    return ("NONE", "PASS", f"自定义命令执行成功（{facts['output_lines']} 行输出）", "保持定期运行", facts)
```

**验收**：

- 写一个自定义规则 `grep -E '^(ERROR|FATAL)' /var/log/syslog`，无命中时 grep exit=1 → PASS（之前是 HIGH）。
- 写 `echo "error_count=0"` → PASS（之前是 MEDIUM）。
- 写 `false` → HIGH（exit=1 不通过？这里需要权衡，建议保留 exit=1 也 PASS 以容忍 grep，并在文档明示"业务命令不要靠 exit=1 表达失败"）。

---

#### P0-4 · 磁盘判定过滤 overlay/squashfs/NFS

**位置**：[inspection_center.py:1356-1451](../../app/services/inspection_center.py#L1356-L1451)

**问题**：Docker / Snap / CRI-O 节点的 overlay/squashfs 经常显示 100%；NFS 挂死也读到 100% 但 fstype 是远程文件系统，触发 HIGH 误报。

**修复**：

```python
SKIP_FSTYPES = {"overlay", "overlayfs", "squashfs", "tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2", "ramfs", "autofs", "fuse.gvfsd-fuse", "fuse.snapfuse"}

def _analyze_disk(out, err, code, thresholds=None):
    filesystems, inodes, stale_mounts = [], [], []
    ...
    for line in out.splitlines():
        ...
        fstype = parts[1].lower()
        if fstype in SKIP_FSTYPES:
            continue  # 容器/伪文件系统跳过
        if fstype.startswith("nfs") and pct >= 99:
            # NFS 99-100% 大概率是 stale mount，单独标识
            stale_mounts.append({"mount": parts[-1], "type": fstype, "pct": pct})
            continue
        filesystems.append({...})
    facts["stale_mounts"] = stale_mounts
    facts["criteria"] += "；overlay/squashfs/tmpfs 已跳过；NFS≥99% 单独识别为 stale"
    if stale_mounts:
        return ("MEDIUM", "WARNING",
                f"检测到疑似 stale NFS 挂载：{', '.join(m['mount'] for m in stale_mounts)}",
                "umount -f 后重新挂载，确认 NFS server 状态",
                facts)
    ...
```

**验收**：

- 容器宿主机巡检磁盘项不再触发 HIGH。
- 故意造一份 stale NFS（umount 但未清理 mount table）→ 触发"stale NFS"独立判定。

---

### 2.2 P1 项（高频误报场景，建议同 P0 一并修）

#### P1-1 · `/etc/shadow` 不可读时不能静默 PASS

**位置**：[inspection_center.py:580](../../app/services/inspection_center.py#L580)、[inspection_center.py:951-960](../../app/services/inspection_center.py#L951-L960)

**修复要点**：

```python
# 命令侧用 marker 指示是否成功读到
"SHADOW_READ=ok" 或 "SHADOW_READ=denied"

# analyzer:
shadow_readable = "SHADOW_READ=ok" in out
if not shadow_readable:
    facts["shadow_skipped"] = True
    # 不直接返回，但记入 issue 一条 LOW + status=WARNING
    extra_warn = ("LOW", "WARNING",
                  "/etc/shadow 未读取（采集账号权限不足），空密码检测未执行",
                  "若需空密码检测，请提升巡检账号权限或允许 sudo cat /etc/shadow",
                  facts)
    # 若没有其他更严重的 finding，返回 extra_warn；否则附加到 facts.skipped_checks
```

**验收**：非 root 巡检账号在新规则下能看到"空密码检测未执行"的明确提示，而不是默认 PASS。

---

#### P1-2 · 端口高危表与样例报告口径对齐

**位置**：[inspection_center.py:777](../../app/services/inspection_center.py#L777)、[inspection_center.py:1103](../../app/services/inspection_center.py#L1103)、[inspection_center.py:1177-1182](../../app/services/inspection_center.py#L1177-L1182)

**修复**：

```python
DEFAULT_THRESHOLDS["PROCESS_PORT"] = {
    "high_risk_ports_high":   [27017, 6379, 11211, 9200, 5432, 3306],   # 公网暴露直接 HIGH
    "high_risk_ports_medium": [22, 23, 445, 3389, 5900, 8080, 8000, 8888, 9000],  # MEDIUM
    "suspicious_keywords":    ["xmrig","kinsing","minerd","kdevtmpfsi","perfctl","c3pool","tsm","masscan"],
    "cpu_threshold": 80,
    "cpu_sample_count": 2,  # 连续 N 次采样才算高 CPU（见 P2-2）
}

# IPv6 通配修复
def _is_world_bind(bind: str) -> bool:
    b = bind.strip().strip("[").strip("]")
    return b in {"0.0.0.0", "::", "*"} or b == ""

# 触发
if is_world and port in high_risk_set_high:
    high_risk_ports_high.append(entry)
elif is_world and port in high_risk_set_medium:
    high_risk_ports_medium.append(entry)
```

**验收**：

- `[::]:8080` 监听被识别为 world bind（之前漏报）。
- 27017 公网暴露 → HIGH（之前 MEDIUM）。
- 22 公网暴露 → MEDIUM 且 message 与 [改造方案样例](./2026-06-03-inspection-report-restructure.md#L181) 对齐。

---

#### P1-3 · 登录失败阈值改为单 IP + IP 数双指标

**位置**：[inspection_center.py:770-775](../../app/services/inspection_center.py#L770-L775)、[inspection_center.py:897-921](../../app/services/inspection_center.py#L897-L921)

**修复**：

```python
DEFAULT_THRESHOLDS["LOGIN_SECURITY"] = {
    "failed_high_per_ip": 50,       # 单 IP 失败 ≥50 → HIGH
    "failed_high_total":  200,      # 总失败 ≥200 → HIGH（兜底）
    "failed_low_total":   30,       # 总失败 ≥30 → LOW
    "failed_window_hours": 24,
    "root_remote_warn":   True,     # 默认仅 LOW，不强制 MEDIUM
    "root_remote_ip_whitelist": [], # 在白名单内的 root 登录不告警
    "degraded_command_note": True,  # lastb --since 失败时在 facts 标记降级
}
```

判定优先级：单 IP 高频 > 总量高 > root 远程 > 总量低。降级路径（`lastb -n 20` 兜底）必须在 `facts.criteria` 明示"窗口非 24h"，前端可显示。

**验收**：

- 长尾爆破（100 IP × 各 1 次 = 100 总）：触发 LOW；单一爆破源 IP × 80 次：触发 HIGH。
- root 来源在白名单 → PASS。

---

#### P1-4 · 防火墙在云环境的判定

**位置**：[inspection_center.py:1307-1345](../../app/services/inspection_center.py#L1307-L1345)

**修复**：

```python
# 命令改为同时探测多种防火墙后端
"(systemctl is-active firewalld 2>/dev/null || echo 'no-firewalld'); echo '---UFW---'; (ufw status 2>/dev/null || echo 'no-ufw'); echo '---NFT---'; (nft list ruleset 2>/dev/null | head -50 || echo 'no-nft'); echo '---IPT---'; (iptables -S 2>/dev/null | head -100 || echo 'no-iptables')"

# analyzer:
no_local_firewall = all(m in out for m in ["no-firewalld", "no-ufw", "no-nft"]) and "no-iptables" in out
if no_local_firewall:
    return ("LOW", "WARNING",
            "未检测到本地防火墙后端，建议确认云安全组策略已配置",
            "云环境通常依赖安全组；本地无防火墙时务必通过 vendor console 限制入站",
            facts)
```

**验收**：阿里云 EC2 无 firewalld 不再 PASS，而是 LOW + 明确指引安全组。

---

#### P1-5 · 评分模型分类加权 + 上限封顶

**位置**：[inspection_center.py:2169-2186](../../app/services/inspection_center.py#L2169-L2186)

**问题**：当前 `score = 100 - high*15 - medium*8 - low*2`，8 机器各 1 HIGH 直接归零。

**修复（按类别取最高扣分）**：

```python
def _finalize_run(db, run):
    rows = db.query(InspectionItemResult).filter(InspectionItemResult.run_id == run.id).all()

    # 按 (server_id, category) 取最高 risk 扣分
    bucket = {}  # key=(server_id, category) → max_weight
    weight = {"HIGH": 15, "MEDIUM": 8, "LOW": 2, "NONE": 0}
    for r in rows:
        if r.status not in {"RISK", "WARNING", "ERROR"}:
            continue
        key = (r.server_id or "_", r.category)
        bucket[key] = max(bucket.get(key, 0), weight.get(r.risk_level, 0))

    total_deduction = sum(bucket.values())
    # 单机最多扣 60 分（保留 40 分基线），多机合并按平均
    server_count = len({k[0] for k in bucket.keys()}) or 1
    avg_deduction = min(60, total_deduction / server_count)
    score = max(0, int(100 - avg_deduction))

    # 同时输出"最差机器分"作为辅助指标
    per_server_deduction = {}
    for (srv, cat), w in bucket.items():
        per_server_deduction[srv] = per_server_deduction.get(srv, 0) + w
    worst_server_score = max(0, 100 - min(60, max(per_server_deduction.values(), default=0)))

    run.score = score
    run.metadata_json = dict(run.metadata_json or {})
    run.metadata_json["worst_server_score"] = worst_server_score
    ...
```

**验收**：

- 单机一项 HIGH：score=85（同当前）。
- 8 机器各 1 HIGH：avg 15 → score=85（之前是 0）；worst=85。
- 单机 9 项 HIGH：扣 60 封顶 → score=40，避免触底。

---

### 2.3 P2 项（中等优先级，建议下一轮迭代纳入）

#### P2-1 · 命令历史检测策略

**位置**：[inspection_center.py:1017-1040](../../app/services/inspection_center.py#L1017-L1040)

**修复要点**：

- 移除 `/etc/passwd`、`/etc/shadow` 作为危险关键字（自伤）；改用 `cat /etc/shadow` / `cat /etc/passwd` 这种"行首命令 + 路径"的正则。
- 命令侧增加 `stat -c '%s %Y' ~/.bash_history /root/.bash_history` 输出文件大小与 mtime。
- analyzer 检测"bash_history size==0 且 mtime 在最近 1 小时内" → HIGH（疑似刚被清）。

#### P2-2 · CPU 阈值持续两次采样

**位置**：[inspection_center.py:780](../../app/services/inspection_center.py#L780)、[inspection_center.py:1148-1156](../../app/services/inspection_center.py#L1148-L1156)

**修复要点**：

- 命令侧改为 `ps ...; sleep 3; ps ...`，输出两份。
- analyzer 按 pid 关联，两次都 ≥80% 才计入 high_cpu_procs。
- 默认 `cpu_sample_count=2`，可配置。

#### P2-3 · 备份巡检扩展

**位置**：[inspection_center.py:643-651](../../app/services/inspection_center.py#L643-L651)、[inspection_center.py:1509-1603](../../app/services/inspection_center.py#L1509-L1603)

**修复要点**：

1. `DEFAULT_THRESHOLDS["BACKUP"]["backup_paths"]` 默认追加 `/data/db_backup`、`/home/backup`、`/srv/backup`、`/var/lib/mysql/backup`。
2. 命令侧补：`ls /etc/cron.d/ /etc/cron.daily/ /etc/cron.weekly/ /etc/cron.monthly/ 2>/dev/null` + `systemctl list-timers --all 2>/dev/null`。
3. analyzer 区分"该服务器是否承担备份职责"——服务器侧默认 INFO 不告警，备份职责检测下沉到项目巡检（[2026-06-03 改造方案](./2026-06-03-inspection-report-restructure.md) 已暗示分层）。
4. `min_age_days` 支持 `daily/weekly/monthly` 档位。

---

### 2.4 P3 项（拓展性优化，单独迭代）

#### P3-1 · root 远程登录阈值化 + 白名单

详见 P1-3 已涵盖（`root_remote_ip_whitelist`）。

#### P3-2 · 受限 shell 精确匹配 + 移除 200 行截断

**位置**：[inspection_center.py:580](../../app/services/inspection_center.py#L580)、[inspection_center.py:971-973](../../app/services/inspection_center.py#L971-L973)

```python
LOGIN_SHELLS = {"/bin/bash","/bin/sh","/bin/zsh","/bin/dash","/bin/fish","/usr/bin/bash","/usr/bin/zsh","/usr/bin/fish"}
# 命令侧：cat /etc/passwd（移除 head -200）
# analyzer:
if shell.rstrip() in LOGIN_SHELLS:
    login_users.append(...)
```

#### P3-3 · UID=0 白名单基线

利用现有 [InspectionBaseline](../../app/db/models.py#L897) 表（已有 `baseline_type` 字段）：

```python
# baseline_type="ACCOUNT_UID0_WHITELIST"
# content_json = {"users": ["root", "aliyun_assist"]}
```

analyzer 在判定时：

```python
whitelist = _load_baseline(db, server_id, "ACCOUNT_UID0_WHITELIST")
unexpected_uid0 = [a for a in uid0_accounts if a["user"] not in whitelist]
if unexpected_uid0:
    return ("HIGH", "RISK", f"发现未授权 UID=0 账号：...", ...)
```

#### P3-4 · 新增缺口巡检项

新增 5-6 个 analyzer，列入 `SERVER_CATEGORIES`：

| 新增类别 | 关键检测 | 阈值口径 |
|---|---|---|
| `TIME_SYNC` | `chronyc tracking` / `timedatectl` | 时间偏移 >5s → MEDIUM；NTP 服务未启 → LOW |
| `SSH_CONFIG` | `sshd -T` 输出 PermitRootLogin / PasswordAuthentication | `PermitRootLogin yes` → MEDIUM；密码登录开启 → LOW |
| `SUDOERS` | `cat /etc/sudoers` + `ls /etc/sudoers.d/` + `getent group wheel/sudo` | 非预期 sudo 成员 → MEDIUM（依赖白名单） |
| `KERNEL_VERSION` | `uname -r` + `cat /etc/os-release` | 仅记录到 facts，不直接告警（CVE 关联后续做） |
| `SELINUX_APPARMOR` | `getenforce` / `aa-status` | disabled → LOW |
| `MOUNT_OPTIONS` | `mount` 输出 | `/tmp` 无 noexec → LOW |

每个新 analyzer 复用现有 [_remote_check](../../app/services/inspection_center.py#L295) + [_save_result](../../app/services/inspection_center.py#L207) 模式。

---

## 3. 数据/配置改造

### 3.1 DEFAULT_THRESHOLDS 整体迁移

新增字段（P0/P1/P2 涉及）：

```python
DEFAULT_THRESHOLDS = {
    "DISK": {
        ...,
        "skip_fstypes": ["overlay","overlayfs","squashfs","tmpfs","devtmpfs","proc","sysfs","cgroup","cgroup2","ramfs","autofs","fuse.gvfsd-fuse","fuse.snapfuse"],
        "nfs_stale_threshold_pct": 99,
    },
    "BACKUP": {
        "backup_paths": ["/data/backups","/backup","/var/backups","/data/db_backup","/home/backup","/srv/backup","/var/lib/mysql/backup"],
        "min_age_days_daily":   1,
        "min_age_days_weekly":  8,
        "min_age_days_monthly": 32,
        "scan_system_cron":     True,
        "scan_systemd_timer":   True,
    },
    "LOGIN_SECURITY": {
        "failed_high_per_ip":   50,
        "failed_high_total":    200,
        "failed_low_total":     30,
        "failed_window_hours":  24,
        "root_remote_warn":     True,
        "root_remote_ip_whitelist": [],
    },
    "PROCESS_PORT": {
        "high_risk_ports_high":   [27017, 6379, 11211, 9200, 5432, 3306],
        "high_risk_ports_medium": [22, 23, 445, 3389, 5900, 8080, 8000, 8888, 9000],
        "suspicious_keywords":    ["xmrig","kinsing","minerd","kdevtmpfsi","perfctl","c3pool","tsm","masscan"],
        "cpu_threshold":          80,
        "cpu_sample_count":       2,
    },
    "ACCOUNT_SECURITY": {
        ...,
        "uid0_whitelist": ["root"],   # 也可改为从 InspectionBaseline 加载
    },
    "COMMAND_HISTORY": {
        # 移除 "/etc/passwd","/etc/shadow"
        "danger_keywords": ["rm -rf /", "history -c", "chmod 777", "chown root", "> /dev/sd", "dd if=", "iptables -F", "kill -9", "reboot", "shutdown"],
        "pipe_combos":     [["curl","| bash"],["curl","| sh"],["wget","| bash"],["wget","| sh"]],
        "high_keywords":   ["rm -rf /", "chmod 777", "history -c", "> /dev/sd"],
        "sensitive_read_re": r"\b(cat|less|tail|head|more)\s+/etc/(passwd|shadow)\b",
    },
    "FIREWALL": {
        "no_local_firewall_level": "LOW",   # 云环境默认 LOW
    },
    "MEMORY": {
        "mem_high_pct":     95,
        "mem_medium_pct":   85,
        "swap_high_pct":    50,
        "swap_critical_pct": 80,
        "use_meminfo":      True,           # 新：优先 /proc/meminfo
    },
    "SERVICE_STATUS": {
        # core_services 重命名为 watch_services，不再作判定基准
        "watch_services": ["nginx","caddy","mysql","mysqld","mariadb","postgresql","redis","redis-server","docker","etcd"],
        "failed_unit_medium_count": 3,      # 失败单元 <3 → MEDIUM；≥3 → HIGH
    },
}
```

### 3.2 阈值校验

[_load_thresholds](../../app/services/inspection_center.py#L809-L829) 增加宽松校验：

```python
def _coerce_int(v, default):
    try: return int(v)
    except (TypeError, ValueError): return default

def _coerce_bool(v, default):
    if isinstance(v, bool): return v
    if isinstance(v, str):  return v.lower() in {"1","true","yes","on"}
    return default

def _coerce_list(v, default):
    if isinstance(v, list): return v
    if isinstance(v, str):  return [x.strip() for x in v.split(",") if x.strip()]
    return default
```

避免前端误传 `"failed_high": "ten"` 类型错配把整次巡检搞挂。

### 3.3 数据库迁移

无需新增表/列。所有改动都通过 `InspectionItemConfig.config_json` 现有字段承载。

P3-3 的 UID=0 白名单复用 [InspectionBaseline](../../app/db/models.py#L897) 表，添加 `baseline_type="ACCOUNT_UID0_WHITELIST"` 常量定义。

---

## 4. 测试计划

### 4.1 新增单测文件

`tests/services/test_inspection_analyzers.py`：

```python
# 每个 analyzer 至少 3 个样本：典型 PASS / 典型 RISK / 边界
def test_memory_uses_meminfo_available():
    # 样本：MemTotal=8G, MemAvailable=5G → 期望 mem_pct ≈ 37%（不是 used 列的 80%）
def test_service_status_no_false_high_when_mysql_absent():
    # 单跑 nginx 的机器：systemctl is-active mysql=unknown → 不触发 HIGH
def test_custom_command_grep_no_match_is_pass():
    # exit=1 + 空输出 → PASS（不是 HIGH）
def test_disk_skips_overlay_and_nfs_stale():
    # df 输出含 overlay 100% → 跳过；nfs 100% → stale 单独识别
def test_login_failed_high_uses_per_ip_threshold():
    # 100 个 IP × 各 1 次失败 → LOW（不是 HIGH）；1 个 IP × 60 次失败 → HIGH
def test_port_ipv6_wildcard_recognized_as_world():
    # ss 输出 [::]:8080 → is_world=True
def test_account_shadow_unreadable_returns_warning():
    # SHADOW_READ=denied → 不静默 PASS
```

### 4.2 契约测试

`tests/services/test_inspection_scoring.py`：

```python
def test_score_per_category_max_deduction():
    # 同机同类 3 个 HIGH 只扣一次 15 分
def test_score_multi_server_averages():
    # 8 机器各 1 HIGH → 平均扣 15 → score=85（不是 0）
def test_score_caps_at_60_deduction_per_server():
    # 单机 9 项 HIGH → 扣 60 封顶 → score=40
```

### 4.3 回归测试

- 跑一次单机巡检：评分应符合"机器实际状态"，不再统一 70。
- 跑一次 8 机器合并巡检：评分分布应能区分"全绿机器"和"有 HIGH 的机器"。
- 现有 [tests/test_inspection_*.py](../../tests/) 全部通过（不破坏 [改造方案](./2026-06-03-inspection-report-restructure.md) 已规划的 parsed_facts 兼容）。

---

## 5. 与 [2026-06-03 改造方案](./2026-06-03-inspection-report-restructure.md) 的协同

本计划聚焦"**判定标准**"，[2026-06-03 计划] 聚焦"**报告呈现**"。两者数据契约通过 `parsed_facts` 解耦：

| 文件 | 本计划改动 | 2026-06-03 计划改动 | 协同点 |
|---|---|---|---|
| [inspection_center.py](../../app/services/inspection_center.py) | 修阈值 + 修判定逻辑 + 修评分 | 加 parsed_facts schema | 同一个 analyzer 函数，先合并 2026-06-03 的 parsed_facts，再叠加本计划的判定修复 |
| [report_center.py](../../app/services/report_center.py) | 不动 | 重写 markdown 渲染器 | 不冲突 |
| [models.py](../../app/db/models.py) | 不改表结构 | 加 `parsed_facts` 列 | 不冲突 |

**建议执行顺序**：先合并 2026-06-03 的 parsed_facts 框架（T1+T2 ≈ 5h），再做本计划 P0+P1（≈ 5h），共 10h 即可完成"判定准 + 呈现清"的双重升级。

---

## 6. 不做的事

- ❌ 不重构 [InspectionRule](../../app/db/models.py#L844) 表结构（保留现状，仅扩字段）
- ❌ 不引入外部规则引擎（OPA / CEL），所有判定保留在 Python 内
- ❌ 不重写已生成的历史报告（与 [2026-06-03 计划](./2026-06-03-inspection-report-restructure.md#L341) 一致）
- ❌ 不动前端阈值配置页（先让默认值正确，再考虑 UI）
- ❌ 不在本轮做 CVE 关联与 SBOM 采集（P3-4 的 KERNEL_VERSION 仅记录 facts）

---

## 7. 验收标准

### 7.1 P0 完成标志

- [ ] 跑过一段时间的服务器内存项不再统一报 85%+，与 `htop` 一致
- [ ] 单跑 Web 的服务器不再因没装 MySQL 报 HIGH
- [ ] grep 类自定义规则无匹配时返回 PASS
- [ ] 容器宿主机磁盘项不再因 overlay 100% 报 HIGH

### 7.2 P1 完成标志

- [ ] 非 root 巡检账号能看到"空密码未检测"的明确提示
- [ ] 27017 公网暴露 → HIGH；22 公网暴露 → MEDIUM；IPv6 通配监听正确识别
- [ ] 单 IP × 100 次失败登录 → HIGH；100 IP × 各 1 次 → LOW
- [ ] 8 台机器合并巡检评分有梯度（不再统一 70 分）

### 7.3 测试通过

- [ ] `python -m pytest tests/services/test_inspection_analyzers.py -q` 全过
- [ ] `python -m pytest tests/services/test_inspection_scoring.py -q` 全过
- [ ] `python -m pytest tests -q` 全过（不破坏既有测试）

---

## 8. 任务拆分（推荐执行节奏）

### Day 1（上午）· P0 修复 ≈ 2h

| 任务 | 工时 |
|---|---|
| P0-1 内存算法改 MemAvailable | 0.5h |
| P0-2 service_status 不再写死 core_services | 0.5h |
| P0-3 自定义命令 exit=1 容忍 + 词边界 | 0.5h |
| P0-4 磁盘 fstype 黑名单 + NFS stale | 0.5h |

### Day 1（下午）· P1 修复 + 测试 ≈ 4h

| 任务 | 工时 |
|---|---|
| P1-1 shadow 不可读 WARNING | 0.3h |
| P1-2 端口表升级 + IPv6 修复 | 0.5h |
| P1-3 登录失败双指标 | 1h |
| P1-4 防火墙云环境处理 | 0.3h |
| P1-5 评分模型重写 | 1h |
| 单元测试补齐（覆盖 P0+P1） | 1h |

### Day 2 · P2 + 回归 ≈ 3h

| 任务 | 工时 |
|---|---|
| P2-1 history 检测改进 | 0.5h |
| P2-2 CPU 双采样 | 0.5h |
| P2-3 备份扩展 | 1.5h |
| 全量回归 + 文档归档 | 0.5h |

### 后续迭代 · P3 ≈ 5.5h

P3-1 ~ P3-4 单独立项，与本轮解耦。P3-4 的新增 6 个 analyzer 可视产品需求拆给多轮迭代。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 改阈值后历史 issue 大量 OPEN→需要批量关闭 | 不自动关闭历史 issue，保留人工 close 通道 |
| 评分模型变化导致历史报告对比失真 | 在 `InspectionRun.metadata_json` 记录 `scoring_version=v2`，前端展示版本号 |
| 新 watch_services 与运维实际监控对不上 | watch_services 改为"信息呈现"不参与判定，对失败单元才告警 |
| 自定义命令容忍 exit=1 可能让真实失败被吞 | 文档明示"业务命令请用 exit≥2 表达失败"，并保留 stderr 非空 → INFO 路径 |
| /proc/meminfo 在某些极简镜像不存在 | 降级回 `free` 但读 `available` 列；都失败时返回 LOW + WARNING |
| P3-3 baseline 与现有 InspectionBaseline 表语义冲突 | 提前看下表是否已用 → 当前为空表，安全 |

---

## 10. 附录：相关代码与文档速查

| 用途 | 位置 |
|---|---|
| analyzer 主入口 | [inspection_center.py:388](../../app/services/inspection_center.py#L388) |
| DEFAULT_THRESHOLDS | [inspection_center.py:757](../../app/services/inspection_center.py#L757) |
| _load_thresholds | [inspection_center.py:809](../../app/services/inspection_center.py#L809) |
| 评分模型 | [inspection_center.py:2169](../../app/services/inspection_center.py#L2169) |
| InspectionRule 模型 | [models.py:844](../../app/db/models.py#L844) |
| InspectionItemConfig 模型 | [models.py:865](../../app/db/models.py#L865) |
| InspectionBaseline 模型 | [models.py:897](../../app/db/models.py#L897) |
| 报告改造方案 | [2026-06-03-inspection-report-restructure.md](./2026-06-03-inspection-report-restructure.md) |
| oncall runbook | [../runbooks/INSPECTION_TROUBLESHOOTING.md](../runbooks/INSPECTION_TROUBLESHOOTING.md) |

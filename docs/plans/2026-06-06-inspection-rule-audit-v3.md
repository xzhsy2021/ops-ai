# 2026-06-06 巡检规则 v3 整改计划

> 继 [2026-06-05-inspection-rule-audit-p1.md](./2026-06-05-inspection-rule-audit-p1.md) 之后，基于真实服务器输出（ct-test）做第三轮整改。
> 用户输入样本显示服务器同时具备 PM2 管理的 etcd/exchange/monitor/promtail/puller/risk/sender/strategy/supplier/system/trader/transaction + caddy 等。

## 背景

ct-test 是一台典型的「应用服务器 + 反向代理 + 进程管理工具」混合环境：
- **PM2 管理的业务进程**（16 个，含 4 个 stopped：exchange-02/risk-02/strategy-02/transaction-02）
- **pm2-logrotate 模块**（1 个，online）
- **Caddy 反向代理**
- **etcd**（PM2 中是 fork 模式）

当前 SERVICE_STATUS 巡检**只看 systemd**，完全错过：
1. PM2 管理的进程停止/errored（`systemctl --failed` 看不到）
2. etcd 集群健康（应用层 vs 系统层）
3. caddy 等非 systemd 进程（容器/二进制方式部署的服务）

## 整改目标

| 优先级 | ID | 项 | 影响 |
|---|---|---|---|
| P0 | E1 | 重写 SERVICE_STATUS 命令模板 | 必须先有 PM2/etcd/caddy 的数据采集 |
| P0 | E2 | `_analyze_service` 解析 PM2 `pm2 l` / `pm2 jlist` | stopped → MEDIUM，errored → HIGH |
| P0 | E3 | `etcd_health` 解析（`etcdctl endpoint health` / fallback PM2 进程检查） | etcd 不可用 → HIGH |
| P0 | D1 | PROCESS_PORT 高危端口暴露（3306/6379/27017/9200/11211/5432）→ HIGH | 修复 D1 |
| P0 | D2 | `_append_progress_counts_only` 传 `server_count` | 修复 D2 评分震荡 |
| P1 | D4 | LOGIN analyzer 删除 A1 残留 if 块 | 修复 D4 |
| P1 | D5 | ACCOUNT_SECURITY 补 `(10, 20]` LOW 路径 | 修复 D5 |
| P1 | D7 | DISK SPACE msg "系统分区" 提示对齐 | 修复 D7 |
| P1 | D9 | 前端 SERVICE panel 改 `watch_services` | 修复 D9 |
| P1 | D3 | 清理 9 个死字段（部分启用） | 修复 D3 |
| P2 | D6 | 重写 `SERVER_RULE_COMMANDS.PROCESS_PORT/DISK/BACKUP` 静态模板 | 修复 D6 |
| P2 | D8-D13 | 文档/前端细节 | 顺带处理 |

## E1 - SERVICE_STATUS 命令模板重写

新命令（按段划分）：

```bash
echo '---SYSTEMD_FAILED---'
systemctl --failed --no-pager 2>/dev/null || true
echo '---SYSTEMD_ACTIVE---'
(systemctl is-active nginx caddy mysql mysqld mariadb postgresql redis redis-server pm2-node docker 2>/dev/null || true)
echo '---PM2_LIST---'
(pm2 jlist 2>/dev/null || pm2 l 2>/dev/null || echo 'PM2_NOT_FOUND') || true
echo '---PM2_HEALTH---'
(pm2 ping 2>/dev/null || true)
echo '---ETCD_HEALTH---'
(etcdctl endpoint health --cluster 2>/dev/null || etcdctl endpoint health 2>/dev/null || true)
echo '---PROCESS_KEYWORDS---'
(ps -eo pid,user,comm,args --no-headers 2>/dev/null | egrep -i 'nginx|caddy|mysql|postgres|redis|etcd|pm2' | head -50 || true)
```

## E2 - `_analyze_service` 解析新段

判定逻辑（优先级从高到低）：

1. **PM2 探活**：
   - `pm2 ping` 失败且有 PM2 守护 → HIGH（PM2 自身故障）
   - `pm2 jlist` 中存在 `status: errored` → HIGH
   - `pm2 jlist` 中存在 `status: stopped` → MEDIUM（watch_services 中有 stopped → 升级 HIGH）
   - 没有任何 PM2 进程但用户期望（DEFAULT 配 watch_services 含业务名）→ MEDIUM

2. **etcd 健康**：
   - `endpoint health` 失败 → HIGH（`etcd is unhealthy`）
   - 端点不可达数 ≥ 1/集群 → MEDIUM

3. **systemd failed units**（保留原有逻辑）：
   - ≥ `failed_unit_medium_count` → HIGH
   - < 阈值 → MEDIUM
   - 0 → 不告警

4. **watch_services 综合**：
   - watch_services 中有任意"非 active" → LOW（信息性提示）
   - 都没有问题 → PASS

5. **caddy/非 systemd 进程**（PROCESS_KEYWORDS 段）：
   - 期望服务但 ps 看不到进程 → MEDIUM

## E3 - DEFAULT_THRESHOLDS.SERVICE_STATUS 新字段

```python
"SERVICE_STATUS": {
    "watch_services": [...],          # 保留
    "failed_unit_medium_count": 3,    # 保留
    # 新增
    "pm2_stopped_level": "MEDIUM",    # PM2 进程 stopped 时的等级
    "pm2_errored_level": "HIGH",      # PM2 进程 errored 时的等级
    "pm2_expected_processes": [],     # 期望由 PM2 管理的进程名（如 ["etcd", "exchange", ...]）
    "etcd_unhealthy_level": "HIGH",   # etcd 集群不健康时的等级
    "etcd_endpoints": [],             # 自定义 etcd endpoints，缺省 localhost:2379
    "process_keywords": [             # ps 输出中需要检测的关键字
        "nginx", "caddy", "mysql", "postgres", "redis", "etcd", "pm2",
    ],
    # 兼容老字段
    "core_services": [...],           # 保留为 fallback
}
```

## 测试覆盖

至少 12 个新测试：

| 测试 | 覆盖 |
|---|---|
| `test_e1_pm2_healthy_all_online` | pm2 jlist 全 online → PASS |
| `test_e1_pm2_some_stopped_medium` | 1 stopped + 15 online → MEDIUM |
| `test_e1_pm2_errored_high` | 1 errored → HIGH |
| `test_e1_pm2_ping_failed` | pm2 ping 失败 → HIGH |
| `test_e1_etcd_unhealthy_high` | etcd endpoint health 失败 → HIGH |
| `test_e1_etcd_partial_unhealthy_medium` | 1/3 端点 unhealthy → MEDIUM |
| `test_e1_caddy_process_missing` | watch=caddy 但 ps 看不到 → MEDIUM |
| `test_e1_expected_pm2_proc_missing` | expected=etcd 但 pm2 列表无 etcd → MEDIUM |
| `test_e1_systemd_failed_combine_with_pm2` | systemd 失败 + PM2 stopped → 取最高 |
| `test_d1_high_risk_port_3306_public_high` | 3306@0.0.0.0 → HIGH（新增字段） |
| `test_d2_progress_score_uses_server_count` | 8 servers + 1 high → progress=85 |
| `test_d4_login_no_dead_code` | LOGIN 死代码已清理 |
| `test_d5_login_users_11_low` | 11 login users → LOW（补全路径） |
| `test_d7_disk_space_no_system_msg_for_data` | /data HIGH 不显示"系统分区" |

## 预期验证

- `tests/test_inspection_analyzers.py` ≥ 67/67 通过（当前 52 + 12~15 新增）
- `tests/test_inspection_scoring.py` ≥ 20/20 通过
- `npm run build` 无回归
- `_analyze_service` 在 ct-test 真实输出上判定准确

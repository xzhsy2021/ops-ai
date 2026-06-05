# 2026-06-05 巡检规则 P1 整改计划

> 继 [2026-06-04-inspection-rule-audit.md](./2026-06-04-inspection-rule-audit.md) 之后，对审查报告中标记为 🟡 P1 的 8 项问题做收尾整改。

## 背景

- P0 项（A1-A5）已整改并通过 32+20 个测试。
- 本计划覆盖审查报告 §3 中的 8 个 P1 项，按风险/影响排序：
  - 高：B1、B2、B3（判定逻辑偏差，会导致漏报/误报）
  - 中：B4、B5、B7（契约/一致性，前端集成风险）
  - 低：B6、B8（边界场景与死字段清理）

## 项清单

| ID | 模块 | 问题 | 整改 |
|---|---|---|---|
| **B1** | CUSTOM_COMMAND | 脚本不存在（`command not found` / `No such file`）当前可能被吞或降级 | stderr 命中 "command not found" / "No such file or directory" 强制升 HIGH |
| **B2** | DISK INODE | 阈值未应用 `system_pct_offset`，与 DISK 块口径不一致 | 与 DISK 块同公式：`INODE.pct = used_pct + system_pct_offset` |
| **B3** | FIREWALL | `no_local_firewall` 仅识别单一关键字 iptables，多发行版/容器中漏判 | 增加多关键字（ufw/firewalld/nftables/ip6tables）+ 容器/云环境特殊豁免 |
| **B4** | analyzer 契约 | 部分 analyzer 返回 4-tuple，缺 `parsed_facts`，前端 TS 类型不一致 | 所有 analyzer 强制返回 5-tuple，`parsed_facts` 至少 `{}` |
| **B5** | `_load_thresholds` | 多 cfg 合并时字典推导顺序依赖 Python 3.7+ 插入序，结果非确定（DB cfg 与 DEFAULT 同时存在时） | 显式排序：DB 覆盖 DEFAULT，新增审计日志 + 单测断言 |
| **B6** | BACKUP | 应用服务器（无 cron + 无备份）一律 HIGH 偏严 | 引入 `is_application_server` 标志（基于服务清单），应用服务器降为 MEDIUM 并改文案 |
| **B7** | 权重表 | `RISK_WEIGHT` 与 `_risk_weight` 双表并存，存在漂移风险 | 统一为 `RISK_WEIGHT`，删除 `_risk_weight`，所有引用改一处 |
| **B8** | 死字段 | `sensitive_read_re` 在 DEFAULT_THRESHOLS 中定义但无引用 | 删除死字段，单测断言 DEFAULT_THRESHOLDS 全部键被使用 |

## 执行顺序

1. 创建 P1 计划文档（本文）
2. 读 inspection_center.py 定位 B1-B8 实际位置
3. 按 B1→B2→B3→B4→B5→B6→B7→B8 顺序整改
4. 补充单测到 `tests/test_inspection_analyzers.py` / `test_inspection_scoring.py`
5. 跑全量 inspection 测试 + 前端 `npm run build`

## 完成标准

- 全部 8 项 P1 整改落地
- 新增至少 8 个对应单测，全部通过
- 前端构建无回归
- DEFAULT_THRESHOLDS 与 analyzer 引用集合 100% 对齐（死字段清零）
- 5-tuple 契约 100% 覆盖

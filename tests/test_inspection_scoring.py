"""Scoring model contract tests (P1-5) per 2026-06-04 plan section 4.2."""
import sys
sys.path.insert(0, '.')

from types import SimpleNamespace

import app.services.inspection_center as svc


def _row(server_id, category, risk_level, status="RISK"):
    return SimpleNamespace(server_id=server_id, category=category, risk_level=risk_level, status=status)


# ----- _compute_score -----

def test_score_per_category_max_deduction():
    """同机同类 3 个 HIGH 只扣一次 15 分（10 分封顶被 cap 60 包含）。"""
    # 单机同类 3 HIGH → 按 max 取一次 = 15 扣分
    rows = [
        _row("s1", "DISK", "HIGH"),
        _row("s1", "DISK", "LOW"),
        _row("s1", "DISK", "MEDIUM"),
    ]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    # Expected: 1H/0M/0L (max per key)
    assert (high, medium, low) == (1, 0, 0), f"expected 1H/0M/0L, got {high}/{medium}/{low}"
    score = svc._compute_score(high, medium, low)
    assert score == 100 - 15, f"expected 85, got {score}"
    print(f"  PASS: same (server,cat) 3 rows collapsed to 1H → score={score}")


def test_score_multi_server_averages():
    """8 机器各 1 HIGH → 每机器扣 15 → score 仍为 85（封顶内）."""
    rows = [_row(f"s{i}", "DISK", "HIGH") for i in range(8)]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    assert high == 8, f"expected 8 HIGH, got {high}"
    score = svc._compute_score(high, medium, low)
    # 8*15=120，cap 到 60 → score = 40（不归零）
    assert score == 40, f"8 HIGHs should cap at 60 deduction, score=40, got {score}"
    print(f"  PASS: 8 HIGHs → score={score} (not 0, capped at 60 deduction)")


def test_score_caps_at_60_deduction_per_server():
    """单机 9 项 HIGH → 扣 60 封顶 → score=40。"""
    categories = ["DISK", "MEMORY", "SERVICE_STATUS", "LOGIN_SECURITY", "ACCOUNT_SECURITY",
                  "FIREWALL", "PROCESS_PORT", "COMMAND_HISTORY", "BACKUP"]
    rows = [_row("s1", c, "HIGH") for c in categories]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    assert high == 9, f"expected 9 HIGH, got {high}"
    score = svc._compute_score(high, medium, low)
    # 9*15=135，cap 60 → 40
    assert score == 40, f"9 HIGHs should cap at 60 deduction, score=40, got {score}"
    print(f"  PASS: 9 HIGHs (full 9 categories) → score={score}")


def test_score_zero_issues_perfect():
    """0 issues → score=100。"""
    rows = [_row("s1", "DISK", "NONE", status="PASS")]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    assert (high, medium, low) == (0, 0, 0), f"expected 0/0/0, got {high}/{medium}/{low}"
    score = svc._compute_score(high, medium, low)
    assert score == 100, f"expected 100, got {score}"
    print(f"  PASS: 0 issues → score=100")


def test_score_mixed_weights():
    """1H/2M/3L → 100 - 15 - 16 - 6 = 63。"""
    rows = [
        _row("s1", "DISK", "HIGH"),
        _row("s1", "MEMORY", "MEDIUM"),
        _row("s1", "SERVICE_STATUS", "MEDIUM"),
        _row("s1", "LOGIN_SECURITY", "LOW"),
        _row("s1", "ACCOUNT_SECURITY", "LOW"),
        _row("s1", "FIREWALL", "LOW"),
    ]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    assert (high, medium, low) == (1, 2, 3), f"expected 1/2/3, got {high}/{medium}/{low}"
    score = svc._compute_score(high, medium, low)
    assert score == 63, f"expected 63, got {score}"
    print(f"  PASS: 1H/2M/3L → score={score}")


def test_score_huge_input_never_negative():
    """100 HIGHs → 100*15=1500，cap 60 → 40，永不为负。"""
    rows = [_row(f"s{i}", "DISK", "HIGH") for i in range(100)]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    score = svc._compute_score(high, medium, low)
    assert score == 40, f"expected 40 (cap), got {score}"
    assert score >= 0, f"score must be non-negative, got {score}"
    print(f"  PASS: 100 HIGHs → score={score} (non-negative, capped)")


# ----- _aggregate_risk_counts -----

def test_aggregate_uses_max_risk_per_key():
    """同 server 同 category 应取最高风险等级。"""
    rows = [
        _row("s1", "DISK", "HIGH"),
        _row("s1", "DISK", "MEDIUM"),
        _row("s1", "DISK", "LOW"),
        _row("s1", "DISK", "NONE", status="PASS"),
    ]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    assert (high, medium, low) == (1, 0, 0), f"max per key expected 1H/0M/0L, got {high}/{medium}/{low}"
    print(f"  PASS: max per key → 1H/0M/0L")


def test_aggregate_handles_none_risk_level():
    """r.risk_level 为 None 时不崩溃。"""
    rows = [
        _row("s1", "DISK", None, status="PASS"),
        _row("s1", "MEMORY", "MEDIUM"),
    ]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    assert medium == 1, f"expected 1 medium, got {medium}"
    print(f"  PASS: None risk_level → handled gracefully ({high}H/{medium}M/{low}L)")


def test_aggregate_handles_empty_server_id():
    """server_id 为空/None 时也能聚合。"""
    rows = [
        _row(None, "DISK", "HIGH"),
        _row("", "DISK", "MEDIUM"),
        _row("s1", "MEMORY", "LOW"),
    ]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    # (None/'','DISK') 视为同一 key → HIGH 胜；s1/MEMORY → LOW
    assert (high, medium, low) == (1, 0, 1), f"expected 1H/0M/1L, got {high}/{medium}/{low}"
    print(f"  PASS: empty server_id aggregation → {high}H/{medium}M/{low}L")


def test_aggregate_multi_server_independent():
    """不同 server 的同类目互不影响。"""
    rows = [
        _row("s1", "DISK", "HIGH"),
        _row("s2", "DISK", "MEDIUM"),
        _row("s3", "DISK", "LOW"),
    ]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    assert (high, medium, low) == (1, 1, 1), f"expected 1/1/1, got {high}/{medium}/{low}"
    print(f"  PASS: 3 servers × DISK → 1H/1M/1L (independent)")


def test_aggregate_normal_count():
    """normal 计数为 status=PASS 且 risk_level=None/NONE 的行数。"""
    rows = [
        _row("s1", "DISK", "HIGH"),
        _row("s1", "MEMORY", "NONE", status="PASS"),
        _row("s1", "SERVICE_STATUS", None, status="PASS"),
        _row("s1", "FIREWALL", "NONE", status="PASS"),
    ]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    assert normal == 3, f"expected 3 normal, got {normal}"
    print(f"  PASS: normal count = 3 (PASS-status rows)")


# ----- _risk_weight -----

def test_risk_weight_ordering():
    """风险权重：HIGH > MEDIUM > LOW > NONE > unknown。"""
    assert svc._risk_weight("HIGH") > svc._risk_weight("MEDIUM")
    assert svc._risk_weight("MEDIUM") > svc._risk_weight("LOW")
    assert svc._risk_weight("LOW") > svc._risk_weight("NONE")
    assert svc._risk_weight("UNKNOWN") == 0
    print("  PASS: risk weight ordering HIGH>MEDIUM>LOW>NONE>unknown")


# ============================================================
# A5 修复：评分按 server_count 平均
# ============================================================

def test_a5_score_8_servers_1_high_each_85():
    """A5 修复：8 机器各 1 HIGH → avg_deduction=15 → score=85（按文档口径）。"""
    score = svc._compute_score(8, 0, 0, server_count=8)
    assert score == 85, f"8 HIGHs/8 servers should avg → 85, got {score}"
    print(f"  PASS: A5 8 HIGHs / 8 servers → score=85")


def test_a5_score_1_server_1_high_85():
    """A5 修复：单机 1 HIGH → score=85。"""
    score = svc._compute_score(1, 0, 0, server_count=1)
    assert score == 85, f"1 HIGH/1 server should be 85, got {score}"
    print(f"  PASS: A5 1 HIGH / 1 server → score=85")


def test_a5_score_1_server_9_high_cap_40():
    """A5 修复：单机 9 HIGH → 9*15/1=135 → cap 60 → 40。"""
    score = svc._compute_score(9, 0, 0, server_count=1)
    assert score == 40, f"9 HIGHs/1 server should cap → 40, got {score}"
    print(f"  PASS: A5 9 HIGHs / 1 server → cap → score=40")


def test_a5_score_100_servers_100_high_85():
    """A5 修复：100 机器 100 HIGH → avg=15 → 85。"""
    score = svc._compute_score(100, 0, 0, server_count=100)
    assert score == 85, f"100 HIGHs/100 servers should avg → 85, got {score}"
    print(f"  PASS: A5 100 HIGHs / 100 servers → score=85")


def test_a5_score_2_servers_2_medium_avg_8():
    """A5 修复：2 机器各 1 MEDIUM → avg=8 → 92。"""
    score = svc._compute_score(0, 2, 0, server_count=2)
    assert score == 92, f"2 MEDIUMs/2 servers should avg → 92, got {score}"
    print(f"  PASS: A5 2 MEDIUMs / 2 servers → avg=8 → score=92")


def test_a5_score_mixed_3_servers():
    """A5 修复：3 机器混合 (1H+1M+1L) → (15+8+2)/3=8.33 → deduct=8.33 → 92。"""
    score = svc._compute_score(1, 1, 1, server_count=3)
    # total = 15+8+2 = 25; avg = 25/3 = 8.33; deduct = min(60, 8.33) = 8.33; 100-8.33=91.67 → 92
    assert score == 92, f"1H+1M+1L / 3 servers should be 92, got {score}"
    print(f"  PASS: A5 (1H+1M+1L) / 3 servers → score=92")


def test_a5_score_zero_issues_perfect():
    """A5 修复：0/0/0 → score=100。"""
    score = svc._compute_score(0, 0, 0, server_count=5)
    assert score == 100, f"0 issues should be 100, got {score}"
    print(f"  PASS: A5 0 issues / 5 servers → score=100")


# ----- 完整流程（无 DB 模拟） -----

def test_score_full_scenario_typical():
    """典型场景：单机 1 HIGH 1 MEDIUM 2 LOW + 5 PASS。"""
    rows = [
        _row("s1", "DISK", "HIGH"),
        _row("s1", "MEMORY", "MEDIUM"),
        _row("s1", "SERVICE_STATUS", "LOW"),
        _row("s1", "FIREWALL", "LOW"),
        _row("s1", "LOGIN_SECURITY", "NONE", status="PASS"),
        _row("s1", "ACCOUNT_SECURITY", None, status="PASS"),
        _row("s1", "PROCESS_PORT", "NONE", status="PASS"),
        _row("s1", "COMMAND_HISTORY", "NONE", status="PASS"),
        _row("s1", "BACKUP", "NONE", status="PASS"),
    ]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    # 1H/1M/2L + 5 normal
    assert (high, medium, low) == (1, 1, 2), f"expected 1/1/2, got {high}/{medium}/{low}"
    assert normal == 5, f"expected 5 normal, got {normal}"
    score = svc._compute_score(high, medium, low)
    # 15 + 8 + 4 = 27 → 100 - 27 = 73
    assert score == 73, f"expected 73, got {score}"
    print(f"  PASS: full scenario (1H/1M/2L + 5 PASS) → score={score}")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            print(f"RUN: {t.__name__}")
            t()
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ERROR: {e}")
            failed += 1
    print(f"\n=== Total {len(tests)} tests, {failed} failed ===")
    sys.exit(failed)

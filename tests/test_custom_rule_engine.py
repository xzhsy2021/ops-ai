"""End-to-end test for custom rule extractor + threshold engine."""
import sys
sys.path.insert(0, '.')

from app.services.inspection_center import (
    _extract_value, _evaluate_threshold, _max_risk,
    make_custom_rule_analyzer, _analyze_custom_command,
)


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label}: {actual!r}")


# ============ Test 1: regex 提取器 ============
out = "MemTotal:       16384000 kB\nMemFree:         2048000 kB\nMemAvailable:    4194304 kB"
ext = _extract_value(out, "", {"mode": "regex", "pattern": r"MemAvailable:\s+(\d+)"})
print("Test1 regex:", ext)
assert_eq(ext["value"], 4194304.0, "Test1.regex.value")
assert_eq(ext["method"], "regex", "Test1.regex.method")

# ============ Test 2: numeric 提取器 ============
ext2 = _extract_value("CPU 85%", "", {"mode": "numeric"})
print("Test2 numeric:", ext2)
assert_eq(ext2["value"], 85.0, "Test2.numeric.value")

# ============ Test 3: keyword 提取器 ============
ext3 = _extract_value("running kinsing, also kdevtmpfsi here", "", {"mode": "keyword", "keywords": ["kinsing", "kdevtmpfsi"]})
print("Test3 keyword:", ext3)
assert_eq(ext3["value"], 2.0, "Test3.keyword.value")

# ============ Test 4: JSON 提取器（路径） ============
ext4 = _extract_value('{"memory": {"used": 7500}}', "", {"mode": "json", "value_field": "memory.used"})
print("Test4 json path:", ext4)
assert_eq(ext4["value"], 7500.0, "Test4.json.value")

# ============ Test 4b: JSON 提取器（JSONPath） ============
ext4b = _extract_value('{"a":{"b":{"c":42}}}', "", {"mode": "json", "value_field": "$..c"})
print("Test4b json jsonpath:", ext4b)
assert_eq(ext4b["value"], 42.0, "Test4b.jsonpath.value")

# ============ Test 5: 阈值判定 ============
assert_eq(_evaluate_threshold(85, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}), "MEDIUM", "Test5.85")
assert_eq(_evaluate_threshold(95, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}), "HIGH", "Test5.95")
assert_eq(_evaluate_threshold(40, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}), "NONE", "Test5.40")
assert_eq(_evaluate_threshold(60, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}), "LOW", "Test5.60")

# 比较符 <
assert_eq(_evaluate_threshold(5, {"high": 10, "medium": 20, "low": 30, "comparator": "<"}), "HIGH", "Test5.5<10")
assert_eq(_evaluate_threshold(15, {"high": 10, "medium": 20, "low": 30, "comparator": "<"}), "MEDIUM", "Test5.15<20")
assert_eq(_evaluate_threshold(50, {"high": 10, "medium": 20, "low": 30, "comparator": "<"}), "NONE", "Test5.50>30")

# ============ Test 6: 综合 analyzer - 高风险 ============
config = {
    "extractor": {"mode": "regex", "pattern": r"MemAvailable:\s+(\d+)"},
    "threshold": {"high": 1000000, "medium": 5000000, "low": 10000000, "comparator": "<", "unit": "KB"},
}
analyzer = make_custom_rule_analyzer(config, base_risk_level="LOW")
level, status, msg, sug, facts = analyzer(
    "MemTotal:       16384000 kB\nMemFree:         2048000 kB\nMemAvailable:    524288 kB",
    "",
    0,
    None,
)
print(f"Test6 analyzer: level={level} extracted={facts.get('threshold', {}).get('extracted_value')} risk={facts.get('threshold', {}).get('extracted_risk')}")
assert_eq(level, "HIGH", "Test6.level")
assert_eq(facts["threshold"]["extracted_value"], 524288.0, "Test6.extracted")
assert_eq(facts["final_risk"], "HIGH", "Test6.final")
assert "阈值命中" in msg, f"Test6.msg should mention threshold: {msg}"

# ============ Test 7: 综合 analyzer - 低风险 ============
level2, _, _, _, facts2 = analyzer(
    "MemTotal:       16384000 kB\nMemFree:         2048000 kB\nMemAvailable:    8388608 kB",
    "",
    0,
    None,
)
print(f"Test7 analyzer: level={level2} extracted={facts2.get('threshold', {}).get('extracted_value')} risk={facts2.get('threshold', {}).get('extracted_risk')}")
# 8388608 > 10000000? No, 8388608 < 10000000 → LOW
assert_eq(level2, "LOW", "Test7.level")

# ============ Test 8: max_risk ============
assert_eq(_max_risk("LOW", "HIGH", "NONE"), "HIGH", "Test8.max1")
assert_eq(_max_risk("MEDIUM", "LOW"), "MEDIUM", "Test8.max2")
assert_eq(_max_risk("NONE"), "NONE", "Test8.max3")

# ============ Test 9: 旧式 analyzer 仍然兼容 ============
level3, status3, _, _, _ = _analyze_custom_command("normal output", "", 0, None)
print(f"Test9 legacy analyzer: level={level3} status={status3}")
assert_eq(level3, "NONE", "Test9.legacy")

# ============ Test 10: 命令不存在场景 ============
level4, status4, msg4, _, _ = _analyze_custom_command("", "bash: foo: command not found", 127, None)
print(f"Test10 missing command: level={level4} status={status4}")
assert_eq(level4, "HIGH", "Test10.missing")

# ============ Test 11: base_risk 比 extracted_risk 高 ============
# base 风险为 MEDIUM, 提取值在 LOW 范围 → 最终仍是 MEDIUM（base 主导）
config11 = {
    "extractor": {"mode": "regex", "pattern": r"score:\s*(\d+)"},
    "threshold": {"high": 90, "medium": 70, "low": 50, "comparator": ">"},
}
analyzer11 = make_custom_rule_analyzer(config11, base_risk_level="MEDIUM")
level11, _, _, _, facts11 = analyzer11("score: 30", "", 0, None)
print(f"Test11 base dominant: level={level11} extracted={facts11.get('threshold', {}).get('extracted_value')}")
assert_eq(level11, "MEDIUM", "Test11.level")  # base 主导

# ============ Test 12: 提取失败时退回 base ============
level12, _, _, _, facts12 = analyzer11("no score here", "", 0, None)
print(f"Test12 extract fail: level={level12} method={facts12.get('extractor', {}).get('method')}")
assert_eq(level12, "MEDIUM", "Test12.level")
assert "no-match" in facts12["extractor"]["method"], f"method: {facts12['extractor']['method']}"

print()
print("====== ALL TESTS PASSED ======")

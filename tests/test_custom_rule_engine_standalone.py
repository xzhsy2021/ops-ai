"""Standalone end-to-end test for custom rule extractor + threshold engine.

This test copies the implementation directly to avoid pulling in the full
app dependency tree (starlette, sqlalchemy, etc.) which are not installed
in this sandbox.
"""
import re
import json
import sys


# ============ Inline copy of the engine implementation ============

_RISK_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _max_risk(*levels):
    best = "NONE"
    for lv in levels:
        if _RISK_RANK.get(lv, 0) > _RISK_RANK.get(best, 0):
            best = lv
    return best


def _apply_comparator(value, threshold, comparator):
    if value is None:
        return False
    if comparator == ">":
        return value > threshold
    if comparator == ">=":
        return value >= threshold
    if comparator == "<":
        return value < threshold
    if comparator == "<=":
        return value <= threshold
    if comparator == "==":
        return value == threshold
    if comparator == "contains":
        return str(threshold) in str(value)
    return value > threshold


def _extract_value(out, err, extractor):
    if not extractor:
        return {"value": None, "matched": None, "method": "none"}
    mode = (extractor.get("mode") or "regex").lower()
    pattern = extractor.get("pattern") or ""
    text = (out or "") + "\n" + (err or "")

    if mode == "regex":
        if not pattern:
            return {"value": None, "matched": None, "method": "regex-no-pattern"}
        try:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        except re.error:
            return {"value": None, "matched": None, "method": "regex-invalid"}
        if not m:
            return {"value": None, "matched": None, "method": "regex-no-match"}
        raw = m.group(1) if m.lastindex and m.group(1) else m.group(0)
        num_m = re.search(r"-?\d+(?:\.\d+)?", str(raw))
        return {
            "value": float(num_m.group(0)) if num_m else None,
            "matched": m.group(0),
            "method": "regex",
        }
    if mode == "numeric":
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        return {"value": float(m.group(0)) if m else None, "matched": m.group(0) if m else None, "method": "numeric"}
    if mode == "keyword":
        keywords = extractor.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        count = 0
        matched_words = []
        for kw in keywords:
            if not kw:
                continue
            c = text.count(kw)
            if c > 0:
                count += c
                matched_words.append(f"{kw}x{c}")
        return {"value": float(count), "matched": ",".join(matched_words), "method": "keyword"}
    if mode == "json":
        field = extractor.get("value_field") or "value"
        try:
            data = json.loads(out or "{}")
        except (ValueError, TypeError):
            return {"value": None, "matched": None, "method": "json-parse-fail"}
        cur = data
        if field.startswith("$"):
            key = field.lstrip("$.").split("..")[-1]
            found = []
            def _walk(node):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k == key:
                            found.append(v)
                        _walk(v)
                elif isinstance(node, list):
                    for x in node:
                        _walk(x)
            _walk(cur)
            if not found:
                return {"value": None, "matched": None, "method": "json-not-found"}
            num_m = re.search(r"-?\d+(?:\.\d+)?", str(found[0]))
            return {"value": float(num_m.group(0)) if num_m else None, "matched": str(found[0]), "method": "json"}
        else:
            for part in field.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    cur = None
                if cur is None:
                    break
            if cur is None:
                return {"value": None, "matched": None, "method": "json-not-found"}
            num_m = re.search(r"-?\d+(?:\.\d+)?", str(cur))
            return {"value": float(num_m.group(0)) if num_m else None, "matched": str(cur), "method": "json"}
    return {"value": None, "matched": None, "method": f"unknown-{mode}"}


def _evaluate_threshold(extracted, threshold):
    if extracted is None or not threshold:
        return None
    comparator = threshold.get("comparator") or ">"
    high = threshold.get("high")
    medium = threshold.get("medium")
    low = threshold.get("low")
    # contains 比较符不强制 float
    if comparator == "contains":
        if high is not None and _apply_comparator(extracted, high, comparator):
            return "HIGH"
        if medium is not None and _apply_comparator(extracted, medium, comparator):
            return "MEDIUM"
        if low is not None and _apply_comparator(extracted, low, comparator):
            return "LOW"
        return "NONE"
    if high is not None and _apply_comparator(extracted, float(high), comparator):
        return "HIGH"
    if medium is not None and _apply_comparator(extracted, float(medium), comparator):
        return "MEDIUM"
    if low is not None and _apply_comparator(extracted, float(low), comparator):
        return "LOW"
    return "NONE"


# ============ Test cases ============

def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label}: {actual!r}")


def main():
    print("=== Test 1: regex 提取器 (MemAvailable) ===")
    out = "MemTotal:       16384000 kB\nMemFree:         2048000 kB\nMemAvailable:    4194304 kB"
    ext = _extract_value(out, "", {"mode": "regex", "pattern": r"MemAvailable:\s+(\d+)"})
    print(f"  -> {ext}")
    assert_eq(ext["value"], 4194304.0, "1.regex.value")
    assert_eq(ext["method"], "regex", "1.regex.method")
    assert_eq(ext["matched"], "MemAvailable:    4194304", "1.regex.matched")

    print("=== Test 2: numeric 提取器 ===")
    ext2 = _extract_value("CPU 85%", "", {"mode": "numeric"})
    print(f"  -> {ext2}")
    assert_eq(ext2["value"], 85.0, "2.numeric")

    print("=== Test 3: keyword 提取器 ===")
    ext3 = _extract_value("running kinsing, also kdevtmpfsi here", "", {"mode": "keyword", "keywords": ["kinsing", "kdevtmpfsi"]})
    print(f"  -> {ext3}")
    assert_eq(ext3["value"], 2.0, "3.keyword.value")
    assert "kinsingx1" in ext3["matched"], f"3.keyword.matched: {ext3['matched']}"

    print("=== Test 4: JSON 路径提取 ===")
    ext4 = _extract_value('{"memory": {"used": 7500}}', "", {"mode": "json", "value_field": "memory.used"})
    print(f"  -> {ext4}")
    assert_eq(ext4["value"], 7500.0, "4.json.path")

    print("=== Test 4b: JSONPath ($..c) ===")
    ext4b = _extract_value('{"a":{"b":{"c":42}}}', "", {"mode": "json", "value_field": "$..c"})
    print(f"  -> {ext4b}")
    assert_eq(ext4b["value"], 42.0, "4b.jsonpath")

    print("=== Test 5: 阈值判定 >= ===")
    assert_eq(_evaluate_threshold(85, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}), "MEDIUM", "5.85")
    assert_eq(_evaluate_threshold(95, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}), "HIGH", "5.95")
    assert_eq(_evaluate_threshold(60, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}), "LOW", "5.60")
    assert_eq(_evaluate_threshold(40, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}), "NONE", "5.40")

    print("=== Test 5b: 阈值判定 < ===")
    assert_eq(_evaluate_threshold(5, {"high": 10, "medium": 20, "low": 30, "comparator": "<"}), "HIGH", "5b.5")
    assert_eq(_evaluate_threshold(15, {"high": 10, "medium": 20, "low": 30, "comparator": "<"}), "MEDIUM", "5b.15")
    assert_eq(_evaluate_threshold(25, {"high": 10, "medium": 20, "low": 30, "comparator": "<"}), "LOW", "5b.25")
    assert_eq(_evaluate_threshold(50, {"high": 10, "medium": 20, "low": 30, "comparator": "<"}), "NONE", "5b.50")

    print("=== Test 6: 内存可用率场景 - HIGH ===")
    # 内存可用率：MemAvailable 越小越严重，< 1M KB = HIGH
    config = {
        "extractor": {"mode": "regex", "pattern": r"MemAvailable:\s+(\d+)"},
        "threshold": {"high": 1000000, "medium": 5000000, "low": 10000000, "comparator": "<", "unit": "KB"},
    }
    extracted = _extract_value("MemAvailable:    524288 kB", "", config["extractor"])
    risk = _evaluate_threshold(extracted["value"], config["threshold"])
    print(f"  extracted={extracted['value']}, risk={risk}")
    assert_eq(risk, "HIGH", "6.HIGH.risk")

    print("=== Test 6b: 内存可用率场景 - MEDIUM ===")
    extracted2 = _extract_value("MemAvailable:    2097152 kB", "", config["extractor"])
    risk2 = _evaluate_threshold(extracted2["value"], config["threshold"])
    print(f"  extracted={extracted2['value']}, risk={risk2}")
    assert_eq(risk2, "MEDIUM", "6b.MEDIUM.risk")

    print("=== Test 6c: 内存可用率场景 - LOW ===")
    extracted3 = _extract_value("MemAvailable:    8388608 kB", "", config["extractor"])
    risk3 = _evaluate_threshold(extracted3["value"], config["threshold"])
    print(f"  extracted={extracted3['value']}, risk={risk3}")
    assert_eq(risk3, "LOW", "6c.LOW.risk")

    print("=== Test 6d: 内存可用率场景 - NONE ===")
    extracted4 = _extract_value("MemAvailable:    16777216 kB", "", config["extractor"])
    risk4 = _evaluate_threshold(extracted4["value"], config["threshold"])
    print(f"  extracted={extracted4['value']}, risk={risk4}")
    assert_eq(risk4, "NONE", "6d.NONE.risk")

    print("=== Test 7: 磁盘使用率场景 - HIGH ===")
    # 假设 df 输出：/dev/sda1       50G   46G   4G  92% /
    # 我们提取 92
    disk_config = {
        "extractor": {"mode": "regex", "pattern": r"\d+%"},
        "threshold": {"high": 90, "medium": 75, "low": 50, "comparator": ">=", "unit": "%"},
    }
    out = "Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1        50G   46G  4.0G  92% /"
    disk_ext = _extract_value(out, "", disk_config["extractor"])
    print(f"  raw output: {out}\n  extracted: {disk_ext}")
    assert_eq(disk_ext["value"], 92.0, "7.disk.value")
    disk_risk = _evaluate_threshold(disk_ext["value"], disk_config["threshold"])
    assert_eq(disk_risk, "HIGH", "7.disk.risk")

    print("=== Test 8: 关键字计数 ===")
    # 检查可疑挖矿关键字
    sus_config = {
        "extractor": {"mode": "keyword", "keywords": ["kdevtmpfsi", "kinsing", "xmrig"]},
        "threshold": {"high": 1, "medium": None, "low": None, "comparator": ">=", "unit": "个"},
    }
    ps_out = "root  1234  0.0  0.0  12345  1234 ?  Ssl  00:00 /usr/bin/kdevtmpfsi\nuser  5678  0.0  0.0  23456  2345 ?  Ssl  00:00 kinsing"
    sus_ext = _extract_value(ps_out, "", sus_config["extractor"])
    sus_risk = _evaluate_threshold(sus_ext["value"], sus_config["threshold"])
    print(f"  ps_out: {ps_out}\n  extracted: {sus_ext}, risk: {sus_risk}")
    assert_eq(sus_ext["value"], 2.0, "8.sus.value")
    assert_eq(sus_risk, "HIGH", "8.sus.risk")

    print("=== Test 9: max_risk ===")
    assert_eq(_max_risk("LOW", "HIGH", "NONE"), "HIGH", "9.max1")
    assert_eq(_max_risk("MEDIUM", "LOW"), "MEDIUM", "9.max2")
    assert_eq(_max_risk("NONE"), "NONE", "9.max3")

    print("=== Test 10: 提取失败时返回 None（不触发阈值）===")
    fail_ext = _extract_value("nothing here", "", {"mode": "regex", "pattern": r"score:\s*(\d+)"})
    fail_risk = _evaluate_threshold(fail_ext["value"], {"high": 5, "medium": 3, "low": 1, "comparator": ">"})
    print(f"  extracted: {fail_ext}, risk: {fail_risk}")
    assert_eq(fail_ext["value"], None, "10.fail.value")
    assert_eq(fail_risk, None, "10.fail.risk")

    print("=== Test 11: JSON 中找不到字段 ===")
    nf = _extract_value('{"foo": 1}', "", {"mode": "json", "value_field": "bar.baz"})
    print(f"  -> {nf}")
    assert_eq(nf["value"], None, "11.notfound.value")
    assert_eq(nf["method"], "json-not-found", "11.notfound.method")

    print("=== Test 12: contains 比较符 ===")
    assert_eq(_evaluate_threshold("abc", {"high": "b", "comparator": "contains"}), "HIGH", "12.contains")

    print("=== Test 13: 真实业务场景 - CPU 使用率 ===")
    # top -bn1 输出包含 CPU 行：%Cpu(s):  5.0 us,  3.0 sy...
    cpu_config = {
        "extractor": {"mode": "regex", "pattern": r"%Cpu\(s\):\s*([\d.]+)\s*us"},
        "threshold": {"high": 80, "medium": 50, "low": 20, "comparator": ">=", "unit": "%"},
    }
    top_out = """top - 14:30:00 up 100 days,  1 user,  load average: 0.10, 0.20, 0.30
%Cpu(s): 85.0 us,  5.0 sy,  0.0 ni, 10.0 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st
MiB Mem :  16384.0 total,   2048.0 free,   8192.0 used,   6144.0 buff/cache"""
    cpu_ext = _extract_value(top_out, "", cpu_config["extractor"])
    cpu_risk = _evaluate_threshold(cpu_ext["value"], cpu_config["threshold"])
    print(f"  cpu_ext: {cpu_ext}, risk: {cpu_risk}")
    assert_eq(cpu_ext["value"], 85.0, "13.cpu.value")
    assert_eq(cpu_risk, "HIGH", "13.cpu.risk")

    print()
    print("=" * 50)
    print("====== ALL 13 TESTS PASSED ======")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())

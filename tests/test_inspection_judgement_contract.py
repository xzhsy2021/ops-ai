"""巡检判定口径的"静默错误"契约回归（2026-09 复盘第 11 轮）。

本轮修的三类问题都属于"不报错，但结论是错的"：

1) **自定义规则比较符静默退化**
   ``_apply_comparator`` 对未知比较符（``gte`` / ``=<`` / ``!=`` / 拼写错误）直接
   ``return value > threshold``：规则配置写错时不会报错，照样给出风险结论，
   而方向可能与配置意图相反（例如本意"低于阈值告警"被算成"高于阈值告警"）。
   现在未知比较符抛错 → ``_remote_check`` 把 analyzer 异常记成**可见的 ERROR 项**，
   不再产出一个看似正常的结论。

2) **阈值/提取器配置完全没有校验**
   阈值写成 ``"10GB"`` 会在运行期 ``float()`` 抛错；``extractor.mode`` 写错会让
   ``_extract_value`` 返回 ``unknown-xxx``、提取值为 None，阈值判定被整体跳过——
   规则永远不会触发，却显示"已启用/已执行"。现在建/改规则时就 400 拒绝。

3) **台账/报表周期参数静默兜底**
   服务层 ``_period_bounds`` 对未知周期兜底为"今日"（第 5 轮刻意锁定的契约：
   不得因未知 period 抛 500，见 tests/test_inspection_period_window.py）。但 HTTP 边界
   以前把 ``?period=quarterly`` 当成合法输入，返回一份"看起来正常、区间其实是今天"
   的台账/报表。现在 API 层显式 400；服务层兜底契约保持不变（本文件同时锁定两侧）。

另外修掉内存分析里一段**死代码**：``elif source == "free"`` 分支内部判断
``swap_total_kb > 0``，而进入该分支的前提正是 ``swap_total_kb <= 0``，条件恒假 →
"用 free 的 used 列兜底算 swap 使用率"从未生效，swap_pct 会静默停在 0%。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def empty_db(tmp_path):
    from app.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'inspection.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def api_client(empty_db):
    """最小 FastAPI 应用 + 巡检路由，用于断言 HTTP 400（绕过登录中间件）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import inspection as inspection_api
    from app.db import get_db

    app = FastAPI()
    app.include_router(inspection_api.router)
    app.dependency_overrides[get_db] = lambda: empty_db
    inspection_api.require_auth = lambda request, db: {"username": "tester", "id": 1}
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


# ---------------------------------------------------------------------------
# 1) 比较符：6 个白名单语义 + 未知比较符必须失败（而不是退化成 >）
# ---------------------------------------------------------------------------

def test_supported_comparators_semantics():
    from app.services import inspection_center as ic

    # 升序阈值（HIGH 最小）用于 < / <=
    assert ic._evaluate_threshold(5, {"high": 10, "medium": 20, "low": 30, "comparator": "<"}) == "HIGH"
    assert ic._evaluate_threshold(10, {"high": 10, "medium": 20, "low": 30, "comparator": "<="}) == "HIGH"
    # 降序阈值（HIGH 最大）用于 > / >=
    assert ic._evaluate_threshold(95, {"high": 90, "medium": 75, "low": 50, "comparator": ">"}) == "HIGH"
    assert ic._evaluate_threshold(90, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}) == "HIGH"
    assert ic._evaluate_threshold(50, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}) == "LOW"
    assert ic._evaluate_threshold(40, {"high": 90, "medium": 75, "low": 50, "comparator": ">="}) == "NONE"
    # == 只命中完全相等的档位
    assert ic._evaluate_threshold(75, {"high": 90, "medium": 75, "low": 50, "comparator": "=="}) == "MEDIUM"
    # contains 用于文本/关键字命中
    assert ic._evaluate_threshold("running kinsing", {"high": "kinsing", "comparator": "contains"}) == "HIGH"


def test_missing_comparator_defaults_to_greater_than():
    """历史约定：comparator 缺省/为空 → `>`（不能因为本轮加固而改变默认口径）。"""
    from app.services import inspection_center as ic

    assert ic._evaluate_threshold(95, {"high": 90}) == "HIGH"
    assert ic._evaluate_threshold(95, {"high": 90, "comparator": ""}) == "HIGH"
    assert ic._evaluate_threshold(95, {"high": 90, "comparator": "  "}) == "HIGH"


@pytest.mark.parametrize("bad", ["gte", "=<", "!=", "=>", "GREATER", "isin", "包含"])
def test_unknown_comparator_raises_instead_of_silently_using_greater_than(bad):
    """RED：加固前这些值都会静默按 `>` 判定并返回 "HIGH"，不抛错。"""
    from app.services import inspection_center as ic

    with pytest.raises(ValueError) as exc:
        ic._evaluate_threshold(95, {"high": 90, "medium": 75, "low": 50, "comparator": bad})
    assert "不支持的比较符" in str(exc.value)
    assert ">" in str(exc.value), "错误信息必须列出受支持的比较符"


def test_unknown_comparator_turns_rule_into_visible_error_item(monkeypatch):
    """配置写错的规则必须落成可见 ERROR 项，而不是一个正常的（方向可能相反的）结论。"""
    from app.services import inspection_center as ic

    analyzer = ic.make_custom_rule_analyzer(
        {
            "extractor": {"mode": "numeric"},
            "threshold": {"high": 90, "medium": 75, "low": 50, "comparator": "gte", "unit": "%"},
        },
        base_risk_level="NONE",
    )
    monkeypatch.setattr(
        ic,
        "_run_remote",
        lambda server_name, command, timeout=20: {
            "stdout": "CPU 95%",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 5,
        },
    )
    result = ic._remote_check("srv-1", "CPU", "CPU_USAGE", "CPU 使用率", "echo", analyzer)

    assert result.status == "ERROR", f"未知比较符必须落成 ERROR，实际 {result.status}"
    assert "不支持的比较符" in (result.message or "")


# ---------------------------------------------------------------------------
# 2) 阈值必须是数字：错误信息要指出是哪个字段
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["high", "medium", "low"])
def test_non_numeric_threshold_reports_field_name(field):
    from app.services import inspection_center as ic

    with pytest.raises(ValueError) as exc:
        ic._evaluate_threshold(95, {field: "10GB", "comparator": ">"})
    message = str(exc.value)
    assert f"阈值 {field} 不是数字" in message
    assert "10GB" in message


def test_numeric_string_threshold_still_accepted():
    """数字字符串是历史合法输入（前端表单可能传 "90"），不得因为加固而拒绝。"""
    from app.services import inspection_center as ic

    assert ic._evaluate_threshold(95, {"high": "90", "medium": "75", "low": "50", "comparator": ">="}) == "HIGH"


def test_empty_threshold_values_are_treated_as_absent():
    from app.services import inspection_center as ic

    assert ic._evaluate_threshold(95, {"high": "", "medium": None, "comparator": ">="}) == "NONE"


# ---------------------------------------------------------------------------
# 3) 建/改规则的配置校验（HTTP 400，且在写库之前拒绝）
# ---------------------------------------------------------------------------

def test_validate_rule_config_accepts_valid_config():
    from app.services import inspection_center as ic

    cfg = ic.validate_rule_config(
        {
            "extractor": {"mode": "REGEX", "pattern": r"MemAvailable:\s+(\d+)"},
            "threshold": {"high": 10, "medium": 20, "low": 30, "comparator": "<", "unit": "KB"},
        }
    )
    assert cfg["extractor"]["mode"] == "regex", "mode 必须归一化为小写"
    assert cfg["threshold"]["comparator"] == "<"


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"threshold": {"comparator": "gte", "high": 1}}, "comparator"),
        ({"threshold": {"comparator": ">", "high": "10GB"}}, "不是数字"),
        ({"threshold": "high=90"}, "threshold 必须是对象"),
        ({"extractor": {"mode": "regexp", "pattern": "x"}}, "extractor.mode"),
        ({"extractor": {"mode": "regex"}}, "pattern"),
        ({"extractor": {"mode": "regex", "pattern": "(["}}, "合法正则"),
        ({"extractor": {"mode": "keyword", "keywords": []}}, "keywords"),
        ({"extractor": "numeric"}, "extractor 必须是对象"),
    ],
)
def test_validate_rule_config_rejects_bad_config(config, expected):
    from fastapi import HTTPException

    from app.services import inspection_center as ic

    with pytest.raises(HTTPException) as exc:
        ic.validate_rule_config(config)
    assert exc.value.status_code == 400
    assert expected in str(exc.value.detail)


def test_keyword_string_is_normalized_to_list():
    from app.services import inspection_center as ic

    cfg = ic.validate_rule_config({"extractor": {"mode": "keyword", "keywords": "kinsing, kdevtmpfsi ,"}})
    assert cfg["extractor"]["keywords"] == ["kinsing", "kdevtmpfsi"]


def test_update_rule_rejects_unknown_comparator(empty_db):
    """服务层入口：非法比较符在任何写库之前 400，且不留下新规则。"""
    from fastapi import HTTPException

    from app.db.models import InspectionRule
    from app.services import inspection_center as ic

    with pytest.raises(HTTPException) as exc:
        ic.update_rule(
            empty_db,
            "CUSTOM_BAD_CMP",
            {
                "rule_code": "CUSTOM_BAD_CMP",
                "rule_name": "坏比较符",
                "config": {"extractor": {"mode": "numeric"}, "threshold": {"high": 1, "comparator": "gte"}},
            },
        )
    assert exc.value.status_code == 400
    assert empty_db.query(InspectionRule).filter(InspectionRule.rule_code == "CUSTOM_BAD_CMP").first() is None


def test_create_rule_rejects_bad_extractor_mode(empty_db):
    from fastapi import HTTPException

    from app.services import inspection_center as ic

    with pytest.raises(HTTPException) as exc:
        ic.create_rule(empty_db, {"rule_name": "坏提取器", "config": {"extractor": {"mode": "regexp"}}})
    assert exc.value.status_code == 400


def test_create_rule_accepts_valid_threshold_config(empty_db):
    from app.services import inspection_center as ic

    row = ic.create_rule(
        empty_db,
        {
            "rule_code": "CUSTOM_GOOD_CMP",
            "rule_name": "好比较符",
            "config": {"extractor": {"mode": "numeric"}, "threshold": {"high": 90, "comparator": ">=", "unit": "%"}},
        },
    )
    assert row["rule_code"] == "CUSTOM_GOOD_CMP"


# ---------------------------------------------------------------------------
# 4) 台账/报表周期与日期参数：API 层 400，服务层兜底契约保持不变
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "period,expected",
    [("daily", "daily"), ("day", "daily"), ("DAILY ", "daily"), ("", "daily"), ("weekly", "weekly"),
     ("week", "weekly"), ("monthly", "monthly"), ("month", "monthly")],
)
def test_validate_period_query_accepts_known_and_alias(period, expected):
    from app.services import inspection_center as ic

    assert ic.validate_period_query(period) == expected


@pytest.mark.parametrize("period", ["quarterly", "7d", "yearly", "custom", "unknown"])
def test_validate_period_query_rejects_unknown(period):
    from fastapi import HTTPException

    from app.services import inspection_center as ic

    with pytest.raises(HTTPException) as exc:
        ic.validate_period_query(period)
    assert exc.value.status_code == 400
    assert "period 仅支持" in str(exc.value.detail)


@pytest.mark.parametrize("date_from,date_to,expected", [
    ("not-a-date", "", "date_from"),
    ("", "not-a-date", "date_to"),
    ("2026-13-45", "", "date_from"),
    ("09/01/2026", "", "date_from"),
    ("2026-09-10", "2026-09-01", "date_from 不能晚于 date_to"),
])
def test_validate_period_query_rejects_bad_dates(date_from, date_to, expected):
    from fastapi import HTTPException

    from app.services import inspection_center as ic

    with pytest.raises(HTTPException) as exc:
        ic.validate_period_query("daily", date_from, date_to)
    assert exc.value.status_code == 400
    assert expected in str(exc.value.detail)


def test_validate_period_query_accepts_valid_range_and_iso():
    from app.services import inspection_center as ic

    assert ic.validate_period_query("weekly", "2026-09-01", "2026-09-03") == "weekly"
    # 带时区偏移的 ISO 串是合法的（服务层会换算成 UTC）
    assert ic.validate_period_query("monthly", "2026-09-01T08:00:00+08:00", "2026-09-03T00:00:00Z") == "monthly"
    # 同一天允许
    assert ic.validate_period_query("daily", "2026-09-01", "2026-09-01") == "daily"


def test_service_layer_fallback_contract_is_preserved():
    """第 5 轮锁定的契约：服务层对未知 period 仍兜底为 daily（不得改成抛错）。

    API 边界（validate_period_query）负责拒绝垃圾参数；服务层保留兜底是为了保证
    任何内部调用方都不会因为一个未知 period 直接 500。
    """
    from app.services import inspection_center as ic

    start, end, normalized = ic._period_bounds("quarterly")
    assert normalized == "daily"
    assert end >= start


def test_ledger_api_rejects_unknown_period(api_client):
    resp = api_client.get("/api/v2/inspection/ledger", params={"period": "quarterly"})
    assert resp.status_code == 400, resp.text
    assert "period 仅支持" in resp.text


def test_ledger_api_rejects_bad_date_range(api_client):
    resp = api_client.get(
        "/api/v2/inspection/ledger",
        params={"period": "daily", "date_from": "2026-09-10", "date_to": "2026-09-01"},
    )
    assert resp.status_code == 400, resp.text
    assert "date_from 不能晚于 date_to" in resp.text


def test_ledger_api_accepts_known_period(api_client):
    resp = api_client.get("/api/v2/inspection/ledger", params={"period": "weekly"})
    assert resp.status_code == 200, resp.text
    assert resp.json().get("success") is True, resp.text


def test_periodic_preview_api_rejects_unknown_period(api_client):
    resp = api_client.get("/api/v2/inspection/reports/periodic-preview", params={"period": "7d"})
    assert resp.status_code == 400, resp.text


def test_periodic_report_api_rejects_unknown_period(api_client):
    resp = api_client.post("/api/v2/inspection/reports/periodic", json={"period": "yearly"})
    assert resp.status_code == 400, resp.text


def test_ledger_delete_api_rejects_unknown_period(api_client):
    resp = api_client.post("/api/v2/inspection/ledger/delete", json={"period": "custom"})
    assert resp.status_code == 400, resp.text


def test_ledger_delete_api_rejects_malformed_date(api_client):
    resp = api_client.post("/api/v2/inspection/ledger/delete", json={"period": "daily", "date_from": "09/01/2026"})
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# 5) 内存/swap 判定：死代码修正后的行为
# ---------------------------------------------------------------------------

_FREE_3COL = """              total        used        free
Mem:       16384000     8000000     4000000
Swap:      2097148      524287
"""


def test_swap_used_column_is_used_when_free_column_missing():
    """RED：加固前 Swap 行只有 3 列时 swap_pct 静默停在 0%（死代码永不生效）。"""
    from app.services import inspection_center as ic

    level, status, _msg, _sug, facts = ic._analyze_memory(_FREE_3COL, "", 0, None)

    assert facts["swap_pct"] == 25, f"3 列 Swap 行（total+used）也应算出 25%，实际 {facts['swap_pct']}"
    assert status, "判定状态不得为空"


def test_swap_pct_from_standard_free_line():
    from app.services import inspection_center as ic

    out = "              total        used        free\nMem:       16384000     8000000     4000000\nSwap:          2047         512        1535\n"
    _level, _status, _msg, _sug, facts = ic._analyze_memory(out, "", 0, None)

    assert facts["swap_pct"] == 25


def test_swap_pct_uses_meminfo_when_free_section_missing():
    from app.services import inspection_center as ic

    out = "---MEMINFO---\nMemTotal:       16384000 kB\nMemAvailable:    8000000 kB\nSwapTotal:       2097148 kB\nSwapFree:        1572864 kB\n"
    _level, _status, _msg, _sug, facts = ic._analyze_memory(out, "", 0, None)

    assert facts["source"] == "meminfo"
    assert facts["swap_pct"] == 25


def test_swap_pct_is_clamped_when_used_exceeds_total():
    """异常输入（used > total）不得算出 >100% 的使用率。"""
    from app.services import inspection_center as ic

    out = "              total        used        free\nMem:       16384000     8000000     4000000\nSwap:          2047        4096        1535\n"
    _level, _status, _msg, _sug, facts = ic._analyze_memory(out, "", 0, None)

    assert facts["swap_pct"] == 100, f"swap 使用率必须夹紧到 100，实际 {facts['swap_pct']}"


def test_no_swap_reports_zero_pct():
    from app.services import inspection_center as ic

    out = "              total        used        free\nMem:       16384000     8000000     4000000\nSwap:             0           0           0\n"
    _level, _status, _msg, _sug, facts = ic._analyze_memory(out, "", 0, None)

    assert facts["swap_pct"] == 0

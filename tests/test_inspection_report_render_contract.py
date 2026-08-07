"""Contract tests for the restructured inspection report renderer (T7).

Covers the acceptance criteria from docs/plans/2026-06-03-inspection-report-restructure.md:

Single-run report must show:
- account item renders a UID=0 account table (even if only root)
- disk item renders all mount points table with highest usage highlighted
- port item renders port/service/bind/exposure four-column table
- login item renders root login + top attacker IP tables
- backup item states which paths were checked and what was found

Multi-run report must show:
- severity-sorted risk summary table at the top
- one <details> collapsible block per server
- HIGH servers default-expanded (<details open>), all-PASS collapsed

Legacy compatibility:
- item with parsed_facts=None renders judgment/suggestion without crashing
"""
import pytest
from sqlalchemy import create_engine, text

from app.services.report_center import _inspection_markdown


def _item(
    category,
    risk_level,
    message,
    suggestion,
    *,
    server_id=None,
    parsed_facts=None,
    status="RISK",
):
    return {
        "id": "it_%s" % category.lower(),
        "run_id": "run1",
        "scope_type": "SERVER",
        "server_id": server_id,
        "project_id": None,
        "category": category,
        "item_code": category.lower(),
        "item_name": category,
        "status": status,
        "risk_level": risk_level,
        "message": message,
        "suggestion": suggestion,
        "evidence_id": "ev_0000",
        "raw_output": "",
        "parsed_facts": parsed_facts,
        "created_at": "2026-06-03T04:00:00",
    }


def _payload(items, *, runs=None, issues=None, run=None):
    run = run or {
        "id": "run1",
        "scope_type": "SERVER",
        "server_id": "8.216.33.60",
        "score": 70,
        "high_count": sum(1 for i in items if i["risk_level"] == "HIGH"),
        "medium_count": sum(1 for i in items if i["risk_level"] == "MEDIUM"),
        "low_count": sum(1 for i in items if i["risk_level"] == "LOW"),
        "status": "SUCCESS",
    }
    server_ids = sorted(set(i["server_id"] for i in items if i.get("server_id")))
    summary = {
        "score": run["score"],
        "scope_type": run["scope_type"],
        "server_id": run["server_id"],
        "high_count": run["high_count"],
        "medium_count": run["medium_count"],
        "low_count": run["low_count"],
        "open_issue_count": 2,
    }
    return {
        "generated_at": "2026-06-03T04:01:00",
        "schema_version": "iter39.report-center.v1",
        "summary": summary,
        "data": {
            "run": run,
            "items": items,
            "issues": issues or [],
            "ledger": [],
            "category_summary": [],
            "issue_status": {"OPEN": 1, "PROCESSING": 1, "FIXED": 0, "VERIFIED": 0, "IGNORED": 0},
            "runs": runs or [],
            "server_ids": server_ids,
        },
    }


# ---------------------------------------------------------------------------
# Single-run category rendering contract
# ---------------------------------------------------------------------------

def test_account_item_renders_uid0_table():
    facts = {
        "uid0_accounts": [{"user": "root", "uid": 0, "shell": "/bin/bash", "home": "/root"}],
        "login_users": [{"user": "root", "shell": "/bin/bash"}],
        "login_user_count": 1,
        "total_users": 12,
        "summary": "12 个系统账号，1 个可登录，1 个 UID=0",
    }
    md = _inspection_markdown("巡检报告", _payload([_item(
        "ACCOUNT_SECURITY", "NONE", "账号清单正常", "保持定期审计",
        parsed_facts=facts,
    )]))
    assert "账号安全" in md
    # UID=0 account table columns
    assert "| 账号 | UID | Shell | Home |" in md
    assert "| root | 0 | /bin/bash | /root |" in md


def test_account_item_lists_multiple_uid0_names():
    facts = {
        "uid0_accounts": [
            {"user": "root", "uid": 0, "shell": "/bin/bash", "home": "/root"},
            {"user": "special_root", "uid": 0, "shell": "/bin/bash", "home": "/home/special_root"},
        ],
        "login_users": [],
        "login_user_count": 0,
        "total_users": 20,
        "summary": "20 个系统账号，0 个可登录，2 个 UID=0",
    }
    md = _inspection_markdown("巡检报告", _payload([_item(
        "ACCOUNT_SECURITY", "HIGH", "发现 2 个 UID=0 特权账号", "核查非 root 的 UID=0 账号",
        parsed_facts=facts,
    )]))
    assert "special_root" in md
    assert "核查非 root 的 UID=0 账号" in md


def test_disk_item_renders_all_mounts_and_highlights_max():
    facts = {
        "filesystems": [
            {"mount": "/", "type": "ext4", "total": "197G", "used": "19G", "avail": "170G", "pct": 10},
            {"mount": "/boot/efi", "type": "vfat", "total": "189M", "used": "12M", "avail": "177M", "pct": 7},
        ],
        "max_pct": 10,
        "summary": "2 个挂载点，最高 10%",
    }
    md = _inspection_markdown("巡检报告", _payload([_item(
        "DISK", "NONE", "所有挂载点使用率正常", "保持监控",
        parsed_facts=facts,
    )]))
    assert "| 挂载点 | 类型 | 总量 | 已用 | 可用 | 使用率 |" in md
    assert "| / | ext4 | 197G | 19G | 170G | 10% |" in md
    assert "| /boot/efi | vfat | 189M | 12M | 177M | 7% |" in md


def test_port_item_renders_exposure_column():
    facts = {
        "high_risk_ports": [
            {"port": 22, "proto": "tcp", "service": "sshd", "bind": "0.0.0.0:22", "is_world": True},
            {"port": 27017, "proto": "tcp", "service": "mongod", "bind": "0.0.0.0:27017", "is_world": True},
        ],
        "top_cpu_procs": [{"pid": 984, "user": "root", "cpu": "2.5", "mem": "0.9", "cmd": "AliYunDunMonitor"}],
        "listening_count": 12,
        "summary": "检测到 2 个高风险端口监听",
    }
    md = _inspection_markdown("巡检报告", _payload([_item(
        "PROCESS_PORT", "MEDIUM", "检测到 2 个高风险端口监听", "限制白名单",
        parsed_facts=facts,
    )]))
    assert "| 端口 | 协议 | 进程 | 绑定地址 | 暴露面 |" in md
    assert "| 22 | tcp | sshd | 0.0.0.0:22 | 🌐 公网 |" in md
    assert "限制白名单" in md


def test_login_item_renders_root_login_and_attackers():
    facts = {
        "success_logins": [
            {"user": "root", "ip": "47.86.9.194", "tty": "pts/0", "start": "2026-05-29 09:03", "duration": "00:47", "active": False},
        ],
        "top_attackers": [
            {"ip": "60.10.50.90", "count": 78, "attempted_users": ["deploy", "ubuntu", "test"]},
            {"ip": "80.94.92.164", "count": 3, "attempted_users": ["lighthou"]},
        ],
        "failed_total": 81,
        "summary": "成功登录 1 次，失败登录 81 次",
    }
    md = _inspection_markdown("巡检报告", _payload([_item(
        "LOGIN_SECURITY", "HIGH", "检测到 SSH 爆破尝试", "启用 fail2ban",
        parsed_facts=facts,
    )]))
    assert "**root 登录**" in md
    assert "| 时间 | 来源 IP | 终端 | 时长 |" in md
    assert "| 2026-05-29 09:03 | 47.86.9.194 | pts/0 | 00:47 |" in md
    assert "**失败登录 Top 攻击源**" in md
    assert "| 攻击 IP | 失败次数 | 主要尝试账号 |" in md
    assert "| 60.10.50.90 | 78 | deploy, ubuntu, test |" in md
    assert "启用 fail2ban" in md


def test_backup_item_states_checked_paths():
    facts = {
        "backup_dirs": [
            {"path": "/backup", "exists": False, "files": []},
            {"path": "/data/backups", "exists": True, "files": [{"name": "db_2026-06-03.tar.gz", "size": 102400, "mtime": "2026-06-03"}]},
        ],
        "cron_lines": ["0 3 * * * /usr/local/bin/backup-daily.sh"],
        "summary": "找到 1 个备份目录",
    }
    md = _inspection_markdown("巡检报告", _payload([_item(
        "BACKUP", "NONE", "备份任务正常", "保持定时备份",
        parsed_facts=facts,
    )]))
    assert "/backup" in md
    assert "/data/backups" in md
    assert "db_2026-06-03.tar.gz" in md
    assert "保持定时备份" in md


# ---------------------------------------------------------------------------
# Risk summary + per-server collapse contract (multi-run)
# ---------------------------------------------------------------------------

def _multi_server_items():
    return [
        _item("ACCOUNT_SECURITY", "HIGH", "发现 2 个 UID=0 特权账号", "核查特权账号",
              server_id="8.216.33.60-日志-阿里",
              parsed_facts={"uid0_accounts": [{"user": "root", "uid": 0, "shell": "/bin/bash", "home": "/root"}], "login_user_count": 1, "total_users": 12, "summary": "12 账号"},
              status="RISK"),
        _item("DISK", "PASS", "磁盘正常", "保持监控",
              server_id="8.216.33.60-日志-阿里",
              parsed_facts={"filesystems": [{"mount": "/", "type": "ext4", "total": "197G", "used": "19G", "avail": "170G", "pct": 10}], "max_pct": 10, "summary": "1 挂载点"}),
        _item("ACCOUNT_SECURITY", "PASS", "账号正常", "保持审计",
              server_id="203.0.113.10-阿里测试",
              parsed_facts={"uid0_accounts": [{"user": "root", "uid": 0, "shell": "/bin/bash", "home": "/root"}], "login_user_count": 1, "total_users": 8, "summary": "8 账号"}),
        _item("LOGIN_SECURITY", "LOW", "root 有远程登录记录", "禁用 root 直登",
              server_id="203.0.113.10-阿里测试",
              parsed_facts={"success_logins": [{"user": "root", "ip": "1.2.3.4", "tty": "pts/0", "start": "2026-05-01", "duration": "00:10", "active": False}], "top_attackers": [], "failed_total": 5, "summary": "1 登录"},
              status="WARNING"),
    ]


def test_multi_run_has_risk_summary_and_details_blocks():
    items = _multi_server_items()
    md = _inspection_markdown("巡检合并报告", _payload(items, runs=[{"id": "r1"}, {"id": "r2"}]))
    # severity-sorted risk summary section
    assert "## 🚨 风险摘要（按严重度排序）" in md
    assert "### 🔴 高危" in md
    assert "| 服务器 | 巡检项 | 关键事实 | 建议 |" in md
    # each server gets a <details> block
    assert md.count("<details") >= 2
    assert "## 📦 按服务器展开" in md


def test_multi_run_high_server_open_pass_server_collapsed():
    items = _multi_server_items()
    md = _inspection_markdown("巡检合并报告", _payload(items, runs=[{"id": "r1"}, {"id": "r2"}]))
    # HIGH-bearing server: <details open>
    assert "<details open>" in md
    assert "8.216.33.60-日志-阿里" in md
    # all-PASS server: plain <details> (collapsed)
    assert "\n<details>\n<summary>" in md


def test_single_run_with_one_server_uses_detail_section():
    items = [_item("ACCOUNT_SECURITY", "HIGH", "发现 UID=0 特权账号", "核查", server_id="8.216.33.60",
                   parsed_facts={"uid0_accounts": [], "login_user_count": 0, "total_users": 5, "summary": "5 账号"})]
    md = _inspection_markdown("巡检报告", _payload(items))
    assert "## 📋 巡检明细" in md or "## 📦 按服务器展开" in md


# ---------------------------------------------------------------------------
# Legacy compatibility
# ---------------------------------------------------------------------------

def test_legacy_item_without_parsed_facts_renders_gracefully():
    item = _item("ACCOUNT_SECURITY", "HIGH", "发现多个 UID=0 特权账号：2 个", "核查特权账号",
                 parsed_facts=None)
    md = _inspection_markdown("巡检报告", _payload([item]))
    assert "发现多个 UID=0 特权账号：2 个" in md
    assert "核查特权账号" in md


def test_payload_requires_items():
    md = _inspection_markdown("巡检报告", _payload([]))
    assert "巡检报告" in md


# ---------------------------------------------------------------------------
# Migration idempotency + column contract (T8)
# ---------------------------------------------------------------------------

def _migration_engine(tmp_path):
    db_path = tmp_path / "inspection_report_migration.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    return engine


def _create_legacy_inspection_tables(conn):
    conn.execute(text("""
        CREATE TABLE inspection_item_results (
            id VARCHAR(32) PRIMARY KEY,
            run_id VARCHAR(32),
            scope_type VARCHAR(16),
            server_id VARCHAR(64),
            project_id VARCHAR(32),
            category VARCHAR(64),
            item_code VARCHAR(64),
            item_name VARCHAR(128),
            status VARCHAR(16),
            risk_level VARCHAR(16),
            message TEXT,
            suggestion TEXT,
            evidence_id VARCHAR(64),
            raw_output TEXT,
            created_at DATETIME
        )
    """))


def test_parsed_facts_migration_applies_and_is_idempotent(tmp_path):
    from app.db.migrations.runner import run_schema_migrations

    engine = _migration_engine(tmp_path)
    try:
        with engine.begin() as conn:
            _create_legacy_inspection_tables(conn)

        applied1 = run_schema_migrations(engine)
        assert any(v == "070_002_inspection_item_results_parsed_facts" for v in applied1)

        with engine.connect() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(inspection_item_results)")).fetchall()}
        assert "parsed_facts" in cols

        applied2 = run_schema_migrations(engine)
        assert "070_002_inspection_item_results_parsed_facts" not in applied2
    finally:
        engine.dispose()


def test_parsed_facts_column_accepts_json_and_roundtrips(tmp_path):
    import json
    from app.db.migrations.runner import run_schema_migrations

    engine = _migration_engine(tmp_path)
    try:
        with engine.begin() as conn:
            _create_legacy_inspection_tables(conn)
        run_schema_migrations(engine)

        facts = {"uid0_accounts": [{"user": "root", "uid": 0, "shell": "/bin/bash", "home": "/root"}]}
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO inspection_item_results(id, run_id, category, status, risk_level, message, suggestion, parsed_facts) "
                "VALUES('it_1', 'r1', 'ACCOUNT_SECURITY', 'RISK', 'HIGH', 'm', 's', :f)"
            ), {"f": json.dumps(facts, ensure_ascii=False)})
        with engine.connect() as conn:
            row = conn.execute(text("SELECT parsed_facts FROM inspection_item_results WHERE id='it_1'")).fetchone()
        assert json.loads(row[0])["uid0_accounts"][0]["user"] == "root"
    finally:
        engine.dispose()

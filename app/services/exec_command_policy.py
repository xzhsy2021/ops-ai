"""Ad-hoc 远程命令执行（EXEC_REMOTE）护栏：破坏性模式拦截 + 白名单模板。

设计依据：``docs/exec-remote-approval-design.md`` §4.6。

三层防护中的第 1 层（调用前）与第 3 层（执行前重跑）共用本模块，
保证「prepare 时放行」与「execute 时放行」口径一致：

1. 结构校验：空命令 / 超长 / 控制字符 / 未声明的多行；
2. **绝对黑名单**：命中即拒绝，任何 mode、任何环境都没有例外；
3. **破坏性模式**：prod 一律拒绝；test 需显式 ``allow_destructive=true``；
4. 白名单模板：``mode=allowlist`` 时命令必须完整匹配某个模板，且模板声明的
   参数（软件包 / 路径 / 服务名）需在允许取值内；``mode=free`` 跳过模板匹配，
   但 1~3 仍然生效。

``rm`` 递归删除关键目录采用 **shlex 语义解析**而非纯正则：``rm -r -f /``、
``rm -rf /``、``rm --recursive --force /`` 等写法都能可靠识别，同时不会误伤
``docker run --rm`` 或 ``rm -rf /tmp/xxx``。

本模块不依赖 tool_policy / tool_registry，可被策略层与执行器同时导入，
避免循环依赖。模板与黑名单可由 capability_settings 覆盖。
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Dict, Iterable, List, Optional

# ──────────────────────────────────────────────────────────────
# 第 2 层：绝对黑名单（任何 mode / 环境都不放行）
# ──────────────────────────────────────────────────────────────
EXEC_DENY_PATTERNS: List[str] = [
    # 文件系统 / 裸设备毁坏
    r"\bmkfs(\.[a-z0-9]+)?\b",
    r"\bdd\b[^\n]*\bof=/dev/",
    r">\s*/dev/(sd|nvme|vd|hd)[a-z0-9]*",
    r"\btruncate\b[^\n]*(?:^|\s)/dev/",
    # 关机 / 重启
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r"\binit\s+[06]\b",
    r"\bsystemctl\s+(reboot|poweroff|halt|kexec|suspend)\b",
    # 账户与凭据（创建/改密/提权）
    r"\b(useradd|groupadd|passwd|chpasswd|visudo)\b",
    # 防火墙清空
    r"\biptables\s+-F\b",
    r"\bnft\s+flush\s+ruleset\b",
    # 计划任务 / 审计清除
    r"\bcrontab\s+-r\b",
    r"\bhistory\s+-c\b",
    # fork 炸弹
    r":\(\)\s*\{.*\}\s*;",
    # 远端内容直接进 shell
    r"\b(curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|d)?sh\b",
    # 关键路径权限/属主破坏
    r"\bchmod\s+(?:-R\s+)?[0-7]{3,4}\s+/(?:\s|$)",
    r"\bchmod\s+-R\s+[ugoa+=-]+\s+/(?:\s|$)",
    r"\bchown\s+(?:-R\s+)?\S+\s+/(?:\s|$)",
    # 覆盖关键系统文件
    r">\s*/etc/(passwd|shadow|sudoers|fstab)",
    # 绕过包管理器校验 / 内核引导
    r"\b(?:apt|apt-get|dpkg)\b[^\n]*\b(?:--force-yes|--allow-unauthenticated)\b",
    r"\b(update-grub|grub-install|modprobe\s+-r)\b",
]

# 递归删除关键目录（语义解析，见 check_rm_critical_delete）
_CRITICAL_TARGETS = {
    "/", "/*", "/.", "/..", "~", "$HOME",
    "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/lib64",
    "/boot", "/root", "/opt", "/data", "/vol1", "/srv", "/home",
}
_SHELL_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")

# ──────────────────────────────────────────────────────────────
# 第 3 层：破坏性模式（prod 恒拒；test 需 allow_destructive）
# ──────────────────────────────────────────────────────────────
EXEC_DESTRUCTIVE_PATTERNS: List[str] = [
    r"(?<![\w-])rm\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*[rR]",           # 任意递归删除
    r"\bdocker\s+(?:system|volume|image|container|builder)\s+prune\b",
    r"\bdocker\s+rmi\s+-f\b",
    r"\bkill\s+-9\b",
    r"\bpkill\s+-9\b",
    r"\bmv\b[^\n]*\s+/(?:dev/null)\b",
    r"\b(?:userdel|groupdel)\b",
    r"\bsystemctl\s+(?:stop|disable|mask)\b",
    r"\b(?:apt|apt-get)\s+(?:remove|purge|autoremove)\b",
    r"\bswapoff\b",
    r"\bmount\b[^\n]*\s-o\s+remount",
]

# ──────────────────────────────────────────────────────────────
# 白名单模板（mode=allowlist）
# ──────────────────────────────────────────────────────────────
_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/+@-]*$")
_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9.+_-]*$")
_SVC_RE = re.compile(r"^[A-Za-z0-9@._-]+$")

DEFAULT_EXEC_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "apt_update",
        "description": "刷新 APT 索引",
        "pattern": r"apt-get\s+update",
        "env": ["test", "prod"],
    },
    {
        "id": "apt_install_packages",
        "description": "从已配置源安装白名单内的 DEB 包",
        "pattern": r"apt-get\s+install\s+-y\s+(?P<pkgs>[a-z0-9][a-z0-9.+_-]*(?:\s+[a-z0-9][a-z0-9.+_-]*)*)",
        "params": {
            "pkgs": {
                "kind": "package_list",
                "allow": [
                    "docker.io", "docker-ce", "docker-ce-cli", "containerd.io",
                    "docker-buildx-plugin", "docker-compose-plugin", "docker-ce-rootless-extras",
                    "jq", "htop", "curl", "wget", "rsync", "unzip", "sysstat",
                    "net-tools", "telnet", "traceroute", "lsof", "strace",
                ],
            }
        },
        "env": ["test", "prod"],
    },
    {
        "id": "docker_readonly",
        "description": "只读 Docker 查询（状态/镜像/容器/网络/卷/磁盘占用）",
        "pattern": (
            r"docker\s+(?:ps(?:\s+-a)?|images|version|info|network\s+ls|volume\s+ls|"
            r"system\s+df|compose\s+version|compose\s+ls)"
        ),
        "env": ["test", "prod"],
    },
    {
        "id": "systemctl_service_status",
        "description": "查询 systemd 服务状态（只读）",
        "pattern": r"systemctl\s+(?:status|is-active|is-enabled|is-failed|list-units)\s+(?P<svc>[A-Za-z0-9@._-]+)",
        "params": {"svc": {"kind": "service_name", "allow": ["*"]}},
        "env": ["test", "prod"],
    },
    {
        "id": "system_status_probe",
        "description": "主机/磁盘/内存/包清单基础探查（只读）",
        "pattern": (
            r"(?:df\s+-h(?:\s+[^\s;|&]+)?|free\s+-m|uptime|uname\s+-a|hostname|whoami|"
            r"du\s+-sh\s+(?P<path>/[A-Za-z0-9._/+@-]+)|dpkg\s+-l(?:\s+\S+)?|"
            r"cat\s+/etc/os-release|lsb_release\s+-a)"
        ),
        "params": {
            "path": {
                "kind": "path",
                "prefix": ["/opt", "/data", "/var", "/vol1", "/root", "/etc", "/srv", "/home"],
            }
        },
        "env": ["test", "prod"],
    },
    {
        "id": "docker_install_official_repo",
        "description": "按 Docker 官方源配置仓库并安装 docker-ce 全套（与既有量化服务器一致）",
        "pattern": (
            r"install\s+-m\s+0755\s+-d\s+/etc/apt/keyrings\s*&&\s*"
            r"curl\s+-fsSL\s+https://download\.docker\.com/linux/debian/gpg\s+-o\s+/etc/apt/keyrings/docker\.asc\s*&&\s*"
            r"chmod\s+a\+r\s+/etc/apt/keyrings/docker\.asc\s*&&\s*"
            r"(?:printf|tee)[^\n]*>\s*/etc/apt/sources\.list\.d/docker\.sources\s*&&\s*"
            r"apt-get\s+update\s*&&\s*"
            r"apt-get\s+install\s+-y\s+(?P<pkgs>docker-ce(?:\s+docker-ce-cli)?(?:\s+containerd\.io)?"
            r"(?:\s+docker-buildx-plugin)?(?:\s+docker-compose-plugin)?(?:\s+docker-ce-rootless-extras)?)"
        ),
        "params": {
            "pkgs": {
                "kind": "package_list",
                "allow": [
                    "docker-ce", "docker-ce-cli", "containerd.io",
                    "docker-buildx-plugin", "docker-compose-plugin", "docker-ce-rootless-extras",
                ],
            }
        },
        "env": ["test", "prod"],
    },
]

DEFAULT_EXEC_ALLOW_MULTILINE = False
DEFAULT_EXEC_MAX_LENGTH = 4096


class ExecCommandRejected(Exception):
    """命令未通过护栏校验。"""

    def __init__(self, reason: str, *, level: str = "blocked", template_id: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.level = level
        self.template_id = template_id


# ──────────────────────────────────────────────────────────────
# 语义检查：递归删除关键目录
# ──────────────────────────────────────────────────────────────
def _iter_tokens(segment: str) -> List[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def check_rm_critical_delete(command: str) -> Optional[str]:
    """识别递归删除根/关键目录（含 ``rm -r -f /``、``--recursive`` 等写法）。

    返回命中的目标描述，未命中返回 None。
    """
    for segment in _SHELL_SPLIT_RE.split(command or ""):
        tokens = _iter_tokens(segment)
        if not tokens:
            continue
        # 剥掉 sudo / env 前缀
        idx = 0
        while idx < len(tokens) and tokens[idx] in {"sudo", "-E", "env"}:
            idx += 1
        if idx >= len(tokens) or tokens[idx] != "rm":
            continue
        rest = tokens[idx + 1:]
        flags = [t for t in rest if t.startswith("-")]
        recursive = any(
            ("--recursive" in f) or (f.startswith("-") and not f.startswith("--") and "r" in f[1:].lower())
            for f in flags
        )
        if not recursive:
            continue
        for target in (t for t in rest if not t.startswith("-")):
            normalized = target.rstrip("/") or "/"
            if target in _CRITICAL_TARGETS or normalized in _CRITICAL_TARGETS or target.startswith("/*"):
                return target
    return None


def find_denied_pattern(
    command: str,
    deny_patterns: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """返回命中的**绝对黑名单**模式，无命中返回 None。"""
    for raw in (EXEC_DENY_PATTERNS if deny_patterns is None else list(deny_patterns)):
        try:
            if re.search(raw, command, re.IGNORECASE):
                return raw
        except re.error:
            return raw  # 自定义 pattern 写错时宁可拦截
    return None


def find_destructive_pattern(
    command: str,
    destructive_patterns: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """返回命中的**破坏性**模式，无命中返回 None。"""
    for raw in (
        EXEC_DESTRUCTIVE_PATTERNS if destructive_patterns is None else list(destructive_patterns)
    ):
        try:
            if re.search(raw, command, re.IGNORECASE):
                return raw
        except re.error:
            return raw
    return None


def _check_params(template: Dict[str, Any], match: "re.Match[str]") -> Optional[str]:
    """校验模板声明的参数约束，返回错误原因或 None。"""
    for name, spec in (template.get("params") or {}).items():
        try:
            raw_value = match.group(name)
        except (IndexError, KeyError):
            return f"模板 {template.get('id')} 缺少参数 {name} 的捕获组"
        if raw_value is None:
            continue
        kind = (spec or {}).get("kind")
        if kind == "package_list":
            allowed = set((spec or {}).get("allow") or [])
            for pkg in str(raw_value).split():
                if not _PKG_RE.match(pkg):
                    return f"软件包名不合法：{pkg}"
                if pkg not in allowed:
                    return f"软件包不在白名单内：{pkg}（允许：{', '.join(sorted(allowed))}）"
        elif kind == "service_name":
            svc = str(raw_value).strip()
            if not _SVC_RE.match(svc):
                return f"服务名不合法：{svc}"
            allowed = set((spec or {}).get("allow") or [])
            if "*" not in allowed and svc not in allowed:
                return f"服务不在白名单内：{svc}"
        elif kind == "path":
            path = str(raw_value).strip()
            if not _SAFE_PATH_RE.match(path):
                return f"路径含非法字符或不安全：{path}"
            if ".." in path:
                return f"路径不允许包含 ..：{path}"
            prefixes = list((spec or {}).get("prefix") or [])
            if prefixes and not any(
                path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes
            ):
                return f"路径不在允许范围内：{path}（允许前缀：{', '.join(prefixes)}）"
    return None


def match_template(
    command: str,
    templates: Optional[Iterable[Dict[str, Any]]] = None,
    *,
    environment: str = "",
) -> "tuple[Optional[Dict[str, Any]], Optional[str]]":
    """按模板完整匹配命令，返回 (template, error)。"""
    env = (environment or "").strip().lower()
    for template in (DEFAULT_EXEC_TEMPLATES if templates is None else list(templates)):
        pattern = str((template or {}).get("pattern") or "")
        if not pattern:
            continue
        allowed_envs = [str(x).strip().lower() for x in (template.get("env") or [])]
        if allowed_envs and env and env not in allowed_envs:
            continue
        try:
            match = re.fullmatch(pattern, command, re.IGNORECASE)
        except re.error:
            continue
        if not match:
            continue
        err = _check_params(template, match)
        if err:
            return None, err
        return template, None
    return None, None


def is_prod_environment(environment: str) -> bool:
    """宽松的生产环境判定（与 tool_policy._is_prod_env 保持一致口径）。"""
    return (environment or "").strip().lower() in {
        "prod", "production", "online", "live", "生产", "线上",
    }


def validate_exec_command(
    command: str,
    *,
    mode: str = "allowlist",
    templates: Optional[Iterable[Dict[str, Any]]] = None,
    deny_patterns: Optional[Iterable[str]] = None,
    destructive_patterns: Optional[Iterable[str]] = None,
    environment: str = "",
    max_length: int = DEFAULT_EXEC_MAX_LENGTH,
    allow_multi_line: bool = DEFAULT_EXEC_ALLOW_MULTILINE,
    allow_destructive: bool = False,
    target_count: int = 0,
    max_targets: int = 20,
) -> Dict[str, Any]:
    """校验待审批的远程命令。

    返回：``{"ok", "reason", "level", "template_id", "template_description", "notes"}``
    """
    raw = "" if command is None else str(command)
    normalized = raw.strip()
    notes: List[str] = []
    result: Dict[str, Any] = {
        "ok": False, "reason": "", "level": "blocked",
        "template_id": "", "template_description": "", "notes": notes,
    }

    # ── 结构校验 ──
    if not normalized:
        result["reason"] = "命令为空"
        return result
    if len(normalized) > int(max_length):
        result["reason"] = f"命令长度 {len(normalized)} 超出上限 {max_length}"
        return result
    if "\n" in normalized and not allow_multi_line:
        result["reason"] = "命令包含换行；多行命令需管理员开启 exec_allow_multiline"
        return result
    for ch in normalized:
        if ch == "\t" or (ch == "\n" and allow_multi_line):
            continue
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            result["reason"] = f"命令含非法控制字符 U+{ord(ch):04X}"
            return result
    if target_count and int(target_count) > int(max_targets):
        result["reason"] = f"目标数量 {target_count} 超出上限 {max_targets}"
        return result

    # ── 绝对黑名单（含 rm 语义检查）──
    critical = check_rm_critical_delete(normalized)
    if critical:
        result["reason"] = f"命令递归删除关键目录，已拒绝：{critical}"
        result["level"] = "destructive"
        return result
    denied = find_denied_pattern(normalized, deny_patterns)
    if denied:
        result["reason"] = f"命令命中绝对黑名单，已拒绝：{denied}"
        result["level"] = "destructive"
        return result

    # ── 破坏性模式：prod 恒拒；test 需显式放行 ──
    destructive = find_destructive_pattern(normalized, destructive_patterns)
    if destructive:
        if is_prod_environment(environment):
            result["reason"] = f"生产环境不允许破坏性命令（命中：{destructive}）"
            result["level"] = "destructive"
            return result
        if not allow_destructive:
            result["reason"] = (
                f"命令属破坏性操作（命中：{destructive}）；"
                "非生产环境需显式 allow_destructive=true 才能提交审批"
            )
            result["level"] = "destructive"
            return result
        notes.append("已显式声明 allow_destructive=true（非生产环境）")

    # ── 白名单模板 ──
    normalized_mode = (mode or "allowlist").strip().lower()
    if normalized_mode == "allowlist":
        template, err = match_template(normalized, templates, environment=environment)
        if err:
            result["reason"] = err
            return result
        if not template:
            result["reason"] = (
                "命令不匹配任何白名单模板（当前为 allowlist 模式）；"
                "如需自由命令请由管理员开启 exec_remote_mode=free"
            )
            return result
        result["template_id"] = str(template.get("id") or "")
        result["template_description"] = str(template.get("description") or "")
        notes.append(f"匹配白名单模板：{result['template_id']}")
        result["level"] = "safe"
    elif normalized_mode == "free":
        notes.append("自由命令模式（未匹配白名单模板，已通过黑名单与破坏性检查）")
        result["level"] = "high"
    else:
        result["reason"] = f"未知的 exec_remote_mode：{mode}"
        return result

    result["ok"] = True
    return result


def policy_from_settings(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从 capability_settings 提取护栏参数，供 prepare/execute 两侧共用。

    模板与黑名单仅在管理员显式配置时覆盖，否则回落到模块内默认值——
    这样后续升级新增的默认模板不会被旧的设置快照遮蔽。
    """
    settings = settings or {}
    return {
        "mode": str(settings.get("exec_remote_mode") or "allowlist"),
        "templates": settings.get("exec_remote_templates") or None,
        "deny_patterns": settings.get("exec_remote_deny_patterns") or None,
        "destructive_patterns": settings.get("exec_remote_destructive_patterns") or None,
        "max_length": int(settings.get("exec_remote_max_length") or DEFAULT_EXEC_MAX_LENGTH),
        "allow_multi_line": bool(
            settings.get("exec_remote_allow_multiline", DEFAULT_EXEC_ALLOW_MULTILINE)
        ),
        "max_targets": int(settings.get("exec_remote_max_targets") or 20),
    }


def assert_exec_command(command: str, **kwargs: Any) -> Dict[str, Any]:
    """校验并在失败时抛 :class:`ExecCommandRejected`。"""
    verdict = validate_exec_command(command, **kwargs)
    if not verdict.get("ok"):
        raise ExecCommandRejected(
            str(verdict.get("reason") or "命令未通过校验"),
            level=str(verdict.get("level") or "blocked"),
            template_id=str(verdict.get("template_id") or ""),
        )
    return verdict

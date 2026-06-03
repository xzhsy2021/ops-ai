import copy
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from app.core.config import DATABASE_PATH as DB_FILE, LOG_DIR, UPLOAD_DIR

BLUE_GREEN_WAIT_SECONDS = 300
HEALTH_CHECK_TIMEOUT = 30
HEALTH_CHECK_INTERVAL = 5
HEALTH_CHECK_RETRIES = 6

from app.config.repository import (
    get_db_file, get_db_connection, reset_db_connection,
    _init_db, load_config, save_config, CONFIG_FILE,
    _get_db,
)
from app.config.defaults import DEFAULT_CONFIG
from app.config.cache import (
    invalidate_config_cache, load_config_cached,
    _cache_get, _cache_set,
)
from app.config.migration import (
    _ensure_defaults, _migrate_dovo_regions, _ensure_group_field_defaults,
    _ensure_group_servers, _apply_migrations_and_save, _migrate_json_to_db,
)
from app.config.servers import (
    get_all_servers, get_server_by_name, save_server, delete_server,
    get_jump_host_by_name, resolve_jump_host_config, get_server_references,
)
from app.config.systems import (
    get_all_systems, get_system_by_name, get_system_config,
    get_servers_for_system, save_system, delete_system,
    resolve_system_config, get_variable_inheritance,
    get_all_groups, get_group, save_group, delete_group,
    get_all_dovo_regions, get_dovo_region, save_dovo_region, delete_dovo_region,
    _deep_merge, _apply_service_overrides, _apply_group_overrides,
)
from app.config.environments import (
    get_environments, get_environment, save_environment, delete_environment,
)
from app.config.audit import (
    save_audit_log, load_audit_logs, cleanup_audit_logs,
)
from app.config.locks import (
    acquire_db_lock, release_db_lock, LOCK_EXPIRY_SECONDS,
)
from app.config.deploy_logs import (
    save_deploy_log, load_deploy_logs, load_deploy_log_by_id, cleanup_deploy_logs,
)
from app.config.keys import (
    save_key_file, get_key_file_path, delete_key_file, list_key_files, read_key_file, key_file_exists,
)


def test_ssh_connection(server_config: Dict[str, Any]):
    import time as _time
    from ssh_client import create_ssh_client
    details = {
        "host": server_config.get("host", ""),
        "port": server_config.get("port", 22),
        "timestamp": _time.strftime("%Y-%m-%d %H:%M:%S"),
        "connect_ms": 0,
        "command_ms": 0,
        "load": None,
        "memory": None,
        "disk_root": None,
        "warnings": [],
    }
    ssh = None
    try:
        t0 = _time.monotonic()
        ssh = create_ssh_client(server_config)
        ssh.connect(max_retries=1, retry_delay=1)
        details["connect_ms"] = round((_time.monotonic() - t0) * 1000)

        t1 = _time.monotonic()
        exit_code, out, err = ssh.exec("echo OK", timeout=10)
        details["command_ms"] = round((_time.monotonic() - t1) * 1000)

        if exit_code != 0:
            if ssh:
                try:
                    ssh.close()
                except Exception:
                    pass
            return (False, f"命令执行失败 (exit={exit_code}): {err[:200]}", details)

        if out.strip() != "OK":
            if ssh:
                try:
                    ssh.close()
                except Exception:
                    pass
            return (False, f"回显校验失败: 期望 'OK', 实际 '{out.strip()[:50]}'", details)

        try:
            _, load_out, _ = ssh.exec("cat /proc/loadavg 2>/dev/null | awk '{print $1,$2,$3}'", timeout=5)
            load_str = load_out.strip()
            if load_str:
                details["load"] = load_str
                parts = load_str.split()
                if len(parts) >= 1:
                    try:
                        load1 = float(parts[0])
                        _, cpu_out, _ = ssh.exec("nproc 2>/dev/null || echo 1", timeout=3)
                        nproc = int(cpu_out.strip()) if cpu_out.strip().isdigit() else 1
                        if load1 > nproc * 2:
                            details["warnings"].append(f"负载过高: {load1} (CPU核数: {nproc})")
                    except ValueError:
                        pass
        except Exception:
            pass

        try:
            _, mem_out, _ = ssh.exec("free -m 2>/dev/null | awk '/Mem:/{print $2,$3,$4}'", timeout=5)
            mem_str = mem_out.strip()
            if mem_str:
                details["memory"] = mem_str
                mem_parts = mem_str.split()
                if len(mem_parts) >= 3:
                    try:
                        total_mb = int(mem_parts[0])
                        avail_mb = int(mem_parts[2])
                        if total_mb > 0 and avail_mb / total_mb < 0.05:
                            details["warnings"].append(f"内存不足: 可用 {avail_mb}MB / 总计 {total_mb}MB")
                    except ValueError:
                        pass
        except Exception:
            pass

        try:
            _, disk_out, _ = ssh.exec("df -h / 2>/dev/null | tail -1 | awk '{print $5}'", timeout=5)
            disk_pct = disk_out.strip().replace("%", "")
            if disk_pct:
                details["disk_root"] = disk_pct + "%"
                try:
                    if int(disk_pct) >= 95:
                        details["warnings"].append(f"根磁盘使用率 {disk_pct}% >= 95%")
                except ValueError:
                    pass
        except Exception:
            pass

        if ssh:
            try:
                ssh.close()
            except Exception:
                pass

        msg = f"连接成功 - {server_config['host']} ({details['connect_ms']}ms)"
        if details["warnings"]:
            msg += f" [警告: {'; '.join(details['warnings'])}]"
        return (True, msg, details)

    except Exception as e:
        if ssh:
            try:
                ssh.close()
            except Exception:
                pass
        return (False, str(e), details)


PRESET_WORKFLOW_TEMPLATES = {
    "generic_frontend": {
        "name": "通用前端更新",
        "description": "适用于所有前端项目的标准更新流程",
        "category": "frontend",
        "is_generic": True,
        "variables": {
            "service_name": {"type": "string", "required": True, "description": "服务名称"},
            "deploy_path": {"type": "string", "required": True, "description": "前端部署目录"},
            "update_script": {"type": "string", "required": True, "default": "./www.sh", "description": "更新脚本，实际 Web 发布默认为 ./www.sh"}
        },
        "steps": [
            {"type": "upload", "name": "上传前端包", "command": "/tmp/{{service_name}}.tar.gz"},
            {"type": "web_script_update", "name": "Web /data/www 发布", "config": {"deploy_path": "{{deploy_path}}", "file_name": "{{service_name}}.tar.gz", "update_script": "{{update_script}}", "timeout": "600"}}
        ]
    },
    "generic_backend_direct": {
        "name": "通用后端直接更新",
        "description": "适用于后端服务的直接替换更新",
        "category": "backend",
        "is_generic": True,
        "variables": {
            "service_name": {"type": "string", "required": True, "description": "服务名称"},
            "service_dir": {"type": "string", "required": True, "description": "服务部署目录"},
            "update_script": {"type": "string", "required": True, "description": "更新后执行的脚本命令"},
            "pm2_name": {"type": "string", "required": True, "description": "PM2 进程名称"}
        },
        "steps": [
            {"type": "upload", "name": "上传服务包", "command": "/tmp/{{service_name}}.tar.gz"},
            {"type": "command", "name": "进入服务目录", "command": "cd {{service_dir}}", "timeout": 5},
            {"type": "command", "name": "备份旧程序", "command": "cp {{service_name}} {{service_name}}.bak.$(date +%Y%m%d%H%M%S)", "timeout": 10},
            {"type": "command", "name": "解压新版本", "command": "tar -zxvf /tmp/{{service_name}}.tar.gz", "timeout": 60},
            {"type": "command", "name": "执行更新脚本", "command": "{{update_script}}", "timeout": 120},
            {"type": "command", "name": "查看 PM2 进程", "command": "pm2 status | grep {{pm2_name}}", "timeout": 10},
            {"type": "command", "name": "查看日志", "command": "pm2 logs {{pm2_name}} --lines 50 --nostream", "timeout": 10}
        ]
    },
    "generic_backend_bluegreen": {
        "name": "通用后端蓝绿滚动更新",
        "description": "适用于后端服务的蓝绿滚动更新（Caddy切换），含 active/standby 识别和安全检查",
        "category": "backend",
        "is_generic": True,
        "variables": {
            "service_name": {"type": "string", "required": True, "description": "服务名称"},
            "service_dir": {"type": "string", "required": True, "description": "服务部署目录"},
            "pm2_name": {"type": "string", "required": True, "description": "PM2 进程名称"},
            "caddy_config": {"type": "string", "required": True, "description": "Caddy 配置文件路径"},
            "caddy_old_port": {"type": "string", "required": True, "description": "当前活跃端口"},
            "caddy_new_port": {"type": "string", "required": True, "description": "新版本目标端口"}
        },
        "steps": [
            {"type": "command", "name": "识别 active/standby", "command": "(cat {{caddy_config}} | grep -E 'reverse_proxy|port' || echo '(no caddy config)') && pm2 status | grep {{pm2_name}}", "timeout": 10},
            {"type": "command", "name": "安全检查", "command": "grep -q ':{{caddy_new_port}}' {{caddy_config}} && (echo 'FATAL: standby port {{caddy_new_port}} is in caddy config! Aborting.' && exit 1) || echo 'OK: standby port {{caddy_new_port}} not in caddy'", "timeout": 10},
            {"type": "upload", "name": "上传新版本", "command": "/tmp/{{service_name}}.tar.gz"},
            {"type": "command", "name": "进入服务目录", "command": "cd {{service_dir}}", "timeout": 5},
            {"type": "command", "name": "备份旧程序", "command": "cp {{service_name}} {{service_name}}.bak.$(date +%Y%m%d%H%M%S)", "timeout": 10},
            {"type": "command", "name": "解压新版本", "command": "tar -zxvf /tmp/{{service_name}}.tar.gz", "timeout": 60},
            {"type": "command", "name": "启动新程序", "command": "pm2 restart {{pm2_name}}", "timeout": 30},
            {"type": "wait", "name": "等待启动", "wait_seconds": 10},
            {"type": "command", "name": "查看日志", "command": "pm2 logs {{pm2_name}} --lines 50 --nostream", "timeout": 10},
            {"type": "command", "name": "切换 Caddy 端口", "command": "sed -i 's/:{{caddy_old_port}}/:{{caddy_new_port}}/' {{caddy_config}} && caddy reload", "timeout": 30},
            {"type": "wait", "name": "观察 5 分钟", "wait_seconds": 300},
            {"type": "command", "name": "停止旧程序", "command": "pm2 stop {{pm2_name}}-old", "timeout": 10}
        ]
    },
    "generic_binupdate_rolling": {
        "name": "通用 binupdate 滚动发布",
        "description": "使用 binupdate/portupdate 脚本进行滚动发布",
        "category": "backend",
        "is_generic": True,
        "variables": {
            "service_name": {"type": "string", "required": True, "description": "服务名称"},
            "service_dir": {"type": "string", "required": True, "description": "服务部署目录"},
            "health_port": {"type": "integer", "required": True, "description": "健康检查端口"}
        },
        "steps": [
            {"type": "upload", "name": "上传新版本程序", "command": "/tmp/{{service_name}}.tar.gz"},
            {"type": "command", "name": "检查运行状态", "command": "ps aux | grep {{service_name}} | grep -v grep", "timeout": 10},
            {"type": "command", "name": "执行 binupdate.sh", "command": "cd {{service_dir}} && ./binupdate.sh", "timeout": 60},
            {"type": "wait", "name": "等待服务启动", "wait_seconds": 10},
            {"type": "command", "name": "查看服务日志", "command": "tail -n 100 {{service_dir}}/logs/{{service_name}}.log", "timeout": 10},
            {"type": "command", "name": "执行 portupdate.sh", "command": "cd {{service_dir}} && ./portupdate.sh", "timeout": 60},
            {"type": "wait", "name": "等待端口切换", "wait_seconds": 5},
            {"type": "command", "name": "验证服务正常", "command": "curl -s http://localhost:{{health_port}}/health || tail -n 50 {{service_dir}}/logs/{{service_name}}.log", "timeout": 30}
        ]
    },
    "nodejs_standard": {
        "name": "Node.js 标准发布",
        "description": "适用于大多数 Node.js 应用的标准发布流程",
        "category": "language",
        "is_generic": True,
        "variables": {
            "service_name": {"type": "string", "required": True, "description": "服务名称"},
            "deploy_path": {"type": "string", "required": True, "description": "部署目标目录"},
            "restart_command": {"type": "string", "required": False, "default": "pm2 restart all", "description": "重启命令"},
            "update_script": {"type": "string", "required": False, "default": "tar -zxvf /tmp/{{service_name}}.tar.gz", "description": "更新脚本"},
            "health_port": {"type": "integer", "required": False, "default": 8080, "description": "健康检查端口"},
            "health_path": {"type": "string", "required": False, "default": "/health", "description": "健康检查路径"}
        },
        "steps": [
            {"type": "upload", "name": "上传应用包", "command": "/tmp/{{service_name}}.tar.gz"},
            {"type": "command", "name": "进入目录", "command": "cd {{deploy_path}}", "timeout": 5},
            {"type": "command", "name": "解压文件", "command": "{{update_script}}", "timeout": 60},
            {"type": "wait", "name": "等待服务启动", "wait_seconds": 10},
            {"type": "health_check", "name": "健康检查", "health_check": {"port": "{{health_port}}", "path": "{{health_path}}", "retries": 3}},
            {"type": "command", "name": "重启服务", "command": "{{restart_command}}", "timeout": 30}
        ]
    },
    "dovo_bluegreen": {
        "name": "Dovo 蓝绿更新",
        "description": "Dovo 系统专用蓝绿更新流程：识别active/standby → 安全检查 → binupdate → 日志检查 → portupdate → 二次日志检查",
        "category": "dovo",
        "is_generic": True,
        "variables": {
            "base_path": {"type": "string", "required": True, "default": "/data/bin/ata", "description": "基础路径"},
            "global_config_path": {"type": "string", "required": True, "default": "/data/bin/ata/config/global.yml", "description": "全局配置路径"},
            "instances": {"type": "string", "required": True, "default": "", "description": "实例列表(逗号分隔，如 idn1,idn2)"},
        },
        "steps": [
            {"type": "command", "name": "识别 active/standby", "command": "echo '=== Global task_port ===' && (grep task_port {{global_config_path}} || echo '(not found)') && echo '=== Instance ports ===' && for inst in $(echo {{instances}} | tr ',' ' '); do echo \"--- $inst ---\"; (grep port {{base_path}}/$inst/config/server.yml 2>/dev/null || echo '  (not found)'); done", "timeout": 15},
            {"type": "command", "name": "安全检查", "command": "TASK_PORT=$(grep task_port {{global_config_path}} | awk '{print $2}') && for inst in $(echo {{instances}} | tr ',' ' '); do port=$(grep port {{base_path}}/$inst/config/server.yml 2>/dev/null | awk '{print $2}'); if [ \"$port\" != \"$TASK_PORT\" ] && [ -n \"$port\" ]; then if curl -sf -o /dev/null http://localhost:$port --max-time 3; then echo \"FATAL: $inst port $port is active! Aborting.\"; exit 1; else echo \"OK: $inst port $port is standby\"; fi; fi; done", "timeout": 15},
            {"type": "upload", "name": "上传新版本", "command": "/tmp/{{service_name}}.tar.gz"},
            {"type": "command", "name": "复制到 standby 目录", "command": "cp /tmp/{{service_name}}.tar.gz {{base_path}}/{{standby}}/server.new", "timeout": 30},
            {"type": "command", "name": "执行 binupdate.sh", "command": "cd {{base_path}}/{{standby}} && ./binupdate.sh", "timeout": 120},
            {"type": "wait", "name": "等待服务启动", "wait_seconds": 10},
            {"type": "command", "name": "检查日志", "command": "tail -n 20 {{base_path}}/{{standby}}/logs/server.log", "timeout": 10},
            {"type": "command", "name": "执行 portupdate.sh 切换端口", "command": "cd {{base_path}}/{{standby}} && ./portupdate.sh", "timeout": 120},
            {"type": "wait", "name": "等待端口切换", "wait_seconds": 5},
            {"type": "command", "name": "二次日志检查", "command": "tail -n 20 {{base_path}}/{{standby}}/logs/server.log", "timeout": 10},
        ]
    },
}


def resolve_template_variables_with_inheritance(
    system_cfg: Dict[str, Any],
    env_cfg: Optional[Dict[str, Any]],
    service_cfg: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    sys_vars = system_cfg.get("variables", {}) if system_cfg else {}
    if isinstance(sys_vars, dict):
        merged.update(sys_vars)
    if env_cfg:
        env_vars = env_cfg.get("variables", {})
        if isinstance(env_vars, dict):
            merged.update(env_vars)
    if service_cfg:
        svc_vars = service_cfg.get("template_variables", {})
        if isinstance(svc_vars, dict):
            merged.update(svc_vars)
    return merged


def resolve_template_variables(template: dict, variables: dict, shell_safe: bool = True) -> dict:
    import shlex as _shlex
    resolved = copy.deepcopy(template)
    var_pattern = re.compile(r'\{\{(\w+)\}\}')

    def replace_vars(obj):
        if isinstance(obj, str):
            def _replacer(m):
                val = variables.get(m.group(1), m.group(0))
                if shell_safe and isinstance(val, str):
                    return _shlex.quote(val)
                return str(val)
            return var_pattern.sub(_replacer, obj)
        elif isinstance(obj, dict):
            return {k: replace_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [replace_vars(item) for item in obj]
        return obj

    resolved["steps"] = replace_vars(resolved.get("steps", []))
    resolved["is_generic"] = False
    resolved["resolved_from"] = template.get("name", "")
    return resolved


def get_all_workflow_templates() -> Dict[str, Any]:
    config = load_config()
    user_templates = config.get("workflow_templates", {})
    all_templates = {}
    for key, tmpl in PRESET_WORKFLOW_TEMPLATES.items():
        all_templates[key] = {**tmpl, "is_preset": True}
    for name, tmpl in user_templates.items():
        if name in all_templates:
            all_templates[name] = {**all_templates[name], **tmpl, "is_preset": True, "is_modified": True}
        else:
            all_templates[name] = {**tmpl, "is_preset": False}
    return all_templates


def get_workflow_template_by_name(name: str) -> Optional[Dict[str, Any]]:
    return get_all_workflow_templates().get(name)


def save_workflow_template(name: str, template: Dict[str, Any]) -> bool:
    config = load_config()
    templates = config.get("workflow_templates", {})
    template["updated_at"] = datetime.now().isoformat()
    if name not in templates:
        template["created_at"] = datetime.now().isoformat()
    templates[name] = template
    config["workflow_templates"] = templates
    return save_config(config)


def delete_workflow_template(name: str) -> bool:
    config = load_config()
    templates = config.get("workflow_templates", {})
    if name in templates:
        del templates[name]
        config["workflow_templates"] = templates
        return save_config(config)
    return False

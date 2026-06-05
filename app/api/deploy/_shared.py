"""发布管理 API v2 - Pipeline + SQLite 日志 + asyncio 后台任务"""
import os
import json
import hashlib
import re
import shlex
import uuid
import asyncio
import logging
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Request, Depends, UploadFile, File, Query
from app.api.helpers import api_response, audit
from app.pipeline import PipelineEngine
from app.db import get_db, DeployTaskRepository, DeployLogRepository, DeploymentRepository, PipelineRepository, PipelineStepRepository, DeploymentRuntimeRepository
from app.db.models import Pipeline, PipelineStep, DeployTask, Deployment, NotificationEvent
from app.db.base import SessionLocal
from app.core.auth_v2 import require_auth, require_deploy, require_deploy_for_env, normalize_role, ROLE_ORDER
from app.core.rbac import require_confirmed_high_risk, explain_operation_risk
from app.core.deploy_lock import DeployLock
from app.core import config as AppConfig

from sqlalchemy.orm import Session
from sqlalchemy import text
from config_manager import get_server_by_name, get_all_servers
from app.deploy.rollback_health import build_rollback_health_commands
from app.deploy.rollback import RollbackRuntime
from app.deploy.worker import AsyncWorkerHandle
from app.deploy.report import deployment_report_payload, deployment_report_markdown as build_deployment_report_markdown
from app.deploy.history import deployment_list_payload
from app.deploy.logs import deployment_logs_payload, deployment_tasks_payload
from app.deploy.state import task_cancel_requested, normalize_status
from app.deploy.locks import acquire_deployment_locks, release_deployment_locks, list_deployment_locks_payload
from app.deploy.preflight import build_preflight_payload
from app.deploy.schemas import (
    BatchUpdateRequest, DeployRequest, PipelineCreateRequest, PipelineUpdateRequest,
    ResolutionPreviewRequest, StepCreateRequest, StepUpdateRequest,
    SystemEnvironmentPayload, SystemGroupPayload, SystemServicePayload,
)

logger = logging.getLogger(__name__)

__all__ = [
    "logger",
    "_log_db_session",
    "_log_buffer",
    "_deploy_worker",
    "_assert_environment_server_consistency",
    "_assert_strict_deploy_confirmation",
    "_build_confirmation",
    "_build_confirmation_execution_plan",
    "_build_confirmation_impact",
    "_build_confirmation_risk_reasons",
    "_build_operator_checklist",
    "_clean_str_list",
    "_config_service_to_response",
    "_connect_ssh",
    "_db_pipeline_steps",
    "_db_service_to_response",
    "_default_release_steps",
    "_deploy_confirm_text",
    "_DeployLogBuffer",
    "_deployment_to_rollback_candidate",
    "_derive_servers",
    "_deploy_worker_loop",
    "_distributable_step",
    "_distribute_package_to_server",
    "_distribution_target_for_step",
    "_env_alias",
    "_env_value_from_map",
    "_environment_server_conflicts",
    "_extract_change_reason",
    "_extract_script_targets",
    "_filter_servers_by_environment",
    "_filter_servers_by_service",
    "_find_config_service",
    "_find_dovo_group",
    "_find_service_index",
    "_flush_deploy_logs",
    "_get_log_db",
    "_infer_package_metadata",
    "_invalid_worker_payload_reason",
    "_json_safe",
    "_load_system_cfg",
    "_log_rollback_health_commands",
    "_log_to_db",
    "_merge_release_variables",
    "_norm_name",
    "_normalize_service_payload",
    "_notification_defaults",
    "_notification_settings",
    "_package_info",
    "_package_service_match",
    "_remote_checks_for_service",
    "_remote_script_path",
    "_remote_sha256",
    "_render_package_value",
    "_rollback_candidates_for",
    "_rollback_plan_for",
    "_rollback_precheck_for",
    "_rollback_runtime",
    "_run_pipeline_task",
    "_run_rollback_task",
    "_safe_upload_file_path",
    "_select_servers_for_service_env",
    "_send_release_notification",
    "_server_looks_like_test",
    "_server_names_for_group",
    "_service_aliases",
    "_service_matches",
    "_service_servers_for_environment",
    "_service_topology",
    "_sha256_file",
    "_shutdown_log_db",
    "_step_command_preview",
    "_step_display_type",
    "_task_cancel_requested",
    "_upload_dir",
    "ensure_deploy_worker_running",
]

def _clean_str_list(value: Any) -> List[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = value.replace("，", ",").replace("\n", ",").split(",")
    else:
        raw = []
    result = []
    seen = set()
    for item in raw:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _normalize_service_payload(payload: SystemServicePayload) -> Dict[str, Any]:
    service_name = (payload.name or "").strip()
    if not service_name:
        raise HTTPException(status_code=400, detail="Service name is required")
    template_variables = dict(payload.template_variables or {})
    if "server_keywords" in template_variables:
        template_variables["server_keywords"] = _clean_str_list(template_variables.get("server_keywords"))
    if "servers_by_env" in template_variables and not isinstance(template_variables.get("servers_by_env"), dict):
        raise HTTPException(status_code=400, detail="servers_by_env must be a JSON object")
    return {
        "name": service_name,
        "display_name": (payload.display_name or service_name).strip(),
        "template": (payload.template or "generic_backend_direct").strip(),
        "repo": (payload.repo or "").strip(),
        "pipeline_id": (payload.pipeline_id or "").strip(),
        "servers": _clean_str_list(payload.servers),
        "template_variables": template_variables,
    }


def _config_service_to_response(system_name: str, svc: Dict[str, Any], index: int) -> Dict[str, Any]:
    vars = svc.get("template_variables", {}) or {}
    return {
        "id": f"config:{system_name}:{svc.get('name')}",
        "index": index,
        "source": "config",
        "name": svc.get("name", ""),
        "display_name": svc.get("display_name") or svc.get("name", ""),
        "system_name": system_name,
        "repo": svc.get("repo", ""),
        "template": svc.get("template", ""),
        "pipeline_id": svc.get("pipeline_id", ""),
        "servers": svc.get("servers", []) or [],
        "template_variables": vars,
        "service_dir": vars.get("service_dir") or vars.get("deploy_path") or "",
        "update_script": vars.get("update_script") or "",
        "pm2_name": vars.get("pm2_name") or vars.get("service_name") or "",
        "server_keywords": svc.get("server_keywords", vars.get("server_keywords", [])) or [],
        "servers_by_env": svc.get("servers_by_env", vars.get("servers_by_env", {})) or {},
    }


def _db_service_to_response(svc) -> Dict[str, Any]:
    """Phase 3c SSOT: 将 ORM Service row 转成与 _config_service_to_response 同形态的 dict。"""
    vars = svc.template_variables or {}
    return {
        "id": svc.id,
        "source": "db",
        "name": svc.name,
        "display_name": svc.display_name or svc.name,
        "system_name": svc.system_name,
        "repo": svc.repo or "",
        "template": svc.template or "",
        "pipeline_id": svc.pipeline_id or "",
        "servers": svc.servers or [],
        "template_variables": vars,
        "service_dir": vars.get("service_dir") or vars.get("deploy_path") or "",
        "update_script": vars.get("update_script") or "",
        "pm2_name": vars.get("pm2_name") or vars.get("service_name") or "",
        "server_keywords": vars.get("server_keywords", []) or [],
        "servers_by_env": vars.get("servers_by_env", {}) or {},
    }


def _find_service_index(services: List[Dict[str, Any]], service_name: str) -> int:
    target = _norm_name(service_name)
    for idx, svc in enumerate(services):
        if not isinstance(svc, dict):
            continue
        if _norm_name(svc.get("name")) == target:
            return idx
    return -1


def _load_system_cfg(system_name: str) -> Dict[str, Any]:
    from app.domain.inventory import inventory
    return inventory._load_system_raw(system_name)


def _norm_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _service_matches(svc: Dict[str, Any], service_name: str) -> bool:
    target = _norm_name(service_name)
    if not target:
        return False
    candidates = [
        svc.get("name"),
        svc.get("display_name"),
        (svc.get("template_variables") or {}).get("service_name"),
        (svc.get("template_variables") or {}).get("pm2_name"),
    ]
    for candidate in candidates:
        cn = _norm_name(candidate)
        if cn and (cn == target or cn.endswith(f"-{target}") or target.endswith(f"-{cn}")):
            return True
    return False


def _find_config_service(system_name: str, service_name: str, environment: str = "") -> Optional[Dict[str, Any]]:
    from app.domain.inventory import inventory
    return inventory.get_service(system_name, service_name, environment)


def _find_dovo_group(system_name: str, group_code: str) -> Optional[Dict[str, Any]]:
    if system_name != "dovo" or not group_code:
        return None
    sys_cfg = _load_system_cfg(system_name)
    group = (sys_cfg.get("groups") or sys_cfg.get("regions") or {}).get(group_code)
    if not group:
        return None
    return {**group, "group_code": group_code}


def _server_names_for_group(group_id: str, db: Optional[Session] = None) -> List[str]:
    if not group_id:
        return []
    try:
        from app.db import ServerGroupRepository
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True
        try:
            repo = ServerGroupRepository(db)
            group = repo.get_by_id(group_id) or repo.get_by_name(group_id)
            if group and group.server_names:
                return [x for x in group.server_names if x]
        finally:
            if close_db:
                db.close()
    except Exception:
        logger.exception("Failed to resolve DB server group %s", group_id)

    names: List[str] = []
    for srv in get_all_servers():
        if str(srv.get("group") or "") == str(group_id):
            if srv.get("name"):
                names.append(srv.get("name"))
    return names




def _env_alias(value: Any) -> str:
    val = _norm_name(value)
    if val in {"prod", "production", "online", "release", "live", "线上", "生产"}:
        return "prod"
    if val in {"test", "testing", "qa", "stage", "staging", "uat", "dev", "测试"}:
        return "test"
    return val


def _server_looks_like_test(server_name: str) -> bool:
    name = _norm_name(server_name)
    return any(token in name for token in ("测试", "test", "testing", "qa", "stage", "staging", "uat", "dev"))


def _filter_servers_by_environment(server_names: List[str], environment: str) -> List[str]:
    names = [x for x in server_names if x]
    alias = _env_alias(environment)
    if alias == "test":
        matched = [x for x in names if _server_looks_like_test(x)]
        return matched or names
    if alias == "prod":
        matched = [x for x in names if not _server_looks_like_test(x)]
        return matched or names
    return names


def _environment_server_conflicts(environment: str, server_names: List[str]) -> List[Dict[str, str]]:
    alias = _env_alias(environment)
    if alias not in {"prod", "test"}:
        return []
    conflicts: List[Dict[str, str]] = []
    expected = "测试" if alias == "test" else "线上/生产"
    for server_name in [x for x in server_names if x]:
        is_test = _server_looks_like_test(server_name)
        conflict = (alias == "test" and not is_test) or (alias == "prod" and is_test)
        if conflict:
            conflicts.append({
                "name": server_name,
                "expected": expected,
                "actual": "测试" if is_test else "线上/生产",
            })
    return conflicts


def _assert_environment_server_consistency(environment: str, server_names: List[str]):
    conflicts = _environment_server_conflicts(environment, server_names)
    if not conflicts:
        return
    expected = conflicts[0].get("expected", "匹配环境")
    details = ", ".join(f"{item['name']}({item['actual']})" for item in conflicts[:10])
    if len(conflicts) > 10:
        details += f", ... 共{len(conflicts)}台"
    raise HTTPException(
        status_code=400,
        detail=f"环境与服务器不一致：当前环境 {environment or '-'} 只允许选择{expected}服务器；冲突服务器：{details}",
    )


def _env_value_from_map(mapping: Any, environment: str) -> List[str]:
    if not isinstance(mapping, dict):
        return []
    alias = _env_alias(environment)
    candidates = [environment, alias]
    if alias == "prod":
        candidates.extend(["production", "online"])
    if alias == "test":
        candidates.extend(["testing", "qa", "stage", "staging", "uat", "dev"])
    for key in candidates:
        if not key:
            continue
        value = mapping.get(key)
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str) and value.strip():
            return [x.strip() for x in value.split(",") if x.strip()]
    return []


def _environment_variables_for_system(system_name: str, environment: str) -> Dict[str, Any]:
    if not environment:
        return {}
    sys_cfg = _load_system_cfg(system_name)
    envs = sys_cfg.get("environments") or {}
    if not isinstance(envs, dict):
        return {}

    alias = _env_alias(environment)
    candidates = [environment, alias]
    if alias == "prod":
        candidates.extend(["production", "online"])
    if alias == "test":
        candidates.extend(["testing", "qa", "stage", "staging", "uat", "dev"])

    seen = set()
    for key in candidates:
        name = str(key or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        env_cfg = envs.get(name)
        if not isinstance(env_cfg, dict):
            continue
        variables = env_cfg.get("variables") or {}
        if isinstance(variables, dict):
            return dict(variables)
    return {}


def _service_servers_for_environment(svc: Optional[Dict[str, Any]], environment: str) -> List[str]:
    if not svc:
        return []
    tv = svc.get("template_variables") or {}
    for mapping in (
        svc.get("servers_by_env"),
        svc.get("server_names_by_env"),
        svc.get("env_servers"),
        tv.get("servers_by_env"),
        tv.get("server_names_by_env"),
        tv.get("env_servers"),
    ):
        values = _env_value_from_map(mapping, environment)
        if values:
            return values
    return []


def _service_aliases(svc: Optional[Dict[str, Any]], service_name: str = "") -> List[str]:
    tv = (svc or {}).get("template_variables") or {}
    raw: List[Any] = [
        service_name,
        (svc or {}).get("name"),
        (svc or {}).get("display_name"),
        tv.get("service_name"),
        tv.get("pm2_name"),
    ]
    for key in ("server_keywords", "target_aliases", "server_aliases"):
        for source in ((svc or {}).get(key), tv.get(key)):
            if isinstance(source, list):
                raw.extend(source)
            elif isinstance(source, str):
                raw.extend([x.strip() for x in source.split(",")])
    result = set()
    for item in raw:
        val = _norm_name(item)
        if not val:
            continue
        result.add(val)
        if val.startswith("crypto-"):
            result.add(val.replace("crypto-", "", 1))
    if "system" in result:
        result.update({"main", "master", "主节点", "主"})
    if "frontend" in result:
        result.update({"web", "www", "前端"})
    return [x for x in result if len(x) >= 2]


def _filter_servers_by_service(server_names: List[str], svc: Optional[Dict[str, Any]], service_name: str) -> List[str]:
    aliases = _service_aliases(svc, service_name)
    if not aliases:
        return server_names
    matched = []
    for server_name in server_names:
        n = _norm_name(server_name)
        if any(alias in n for alias in aliases):
            matched.append(server_name)
    return matched or server_names


def _select_servers_for_service_env(server_names: List[str], svc: Optional[Dict[str, Any]], service_name: str, environment: str) -> List[str]:
    env_filtered = _filter_servers_by_environment(server_names, environment)
    service_filtered = _filter_servers_by_service(env_filtered, svc, service_name)
    seen = set()
    result = []
    for name in service_filtered:
        if name and name not in seen:
            result.append(name)
            seen.add(name)
    return result

def _upload_dir() -> str:
    upload_dir = AppConfig.get_runtime_path("UPLOAD_DIR", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def _safe_upload_file_path(file_name: str) -> str:
    base = os.path.basename(file_name or "")
    return os.path.join(_upload_dir(), base) if base else ""


def _sha256_file(path: str, max_bytes: int = 0) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        read_bytes = 0
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
            read_bytes += len(chunk)
            if max_bytes and read_bytes >= max_bytes:
                break
    return sha.hexdigest()


def _infer_package_metadata(file_name: str, file_path: str = "") -> Dict[str, Any]:
    base = os.path.basename(file_name or "")
    lower = base.lower()
    tokens = [x for x in re.split(r"[^a-zA-Z0-9]+", lower) if x]
    artifact_type = "archive" if lower.endswith((".tar.gz", ".tgz", ".tar", ".zip")) else "binary"
    service_hint = tokens[0] if tokens else ""
    version_hint = ""
    for token in tokens[1:]:
        if re.search(r"\d", token):
            version_hint = token
            break
    meta = {
        "name": base,
        "artifact_type": artifact_type,
        "service_hint": service_hint,
        "version_hint": version_hint,
        "extension": ".tar.gz" if lower.endswith(".tar.gz") else os.path.splitext(base)[1],
    }
    if file_path and os.path.isfile(file_path):
        stat = os.stat(file_path)
        meta.update({
            "size": stat.st_size,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return meta


def _package_info(file_name: str, with_checksum: bool = False) -> Dict[str, Any]:
    if not file_name:
        return {"exists": False, "name": "", "detail": "未选择发布包"}
    path = _safe_upload_file_path(file_name)
    info = _infer_package_metadata(file_name, path)
    info["exists"] = bool(path and os.path.isfile(path))
    info["path"] = path if info["exists"] else ""
    if info["exists"] and with_checksum:
        info["sha256"] = _sha256_file(path)
    return info


def _package_service_match(file_name: str, svc: Optional[Dict[str, Any]], service_name: str) -> Dict[str, Any]:
    info = _package_info(file_name, with_checksum=False)
    if not file_name:
        return {"status": "warn", "message": "未选择发布包", "package": info}
    if not info.get("exists"):
        return {"status": "error", "message": f"发布包不存在: {file_name}", "package": info}
    aliases = _service_aliases(svc, service_name)
    lower = os.path.basename(file_name).lower()
    if aliases:
        matched = [a for a in aliases if a and a in lower]
        if matched:
            return {"status": "ok", "message": f"发布包名称匹配服务关键字: {', '.join(matched[:5])}", "package": info, "matched": matched}
        return {"status": "warn", "message": f"发布包名称未包含服务关键字，建议确认是否为 {service_name or (svc or {}).get('name') or '-'} 的包", "package": info, "matched": []}
    return {"status": "info", "message": "服务未配置关键字，跳过包名匹配", "package": info}


def _service_topology(system: str, service: str, environment: str, server_group: str = "", db: Optional[Session] = None) -> Dict[str, Any]:
    svc = _find_config_service(system, service, environment) or {}
    tv = svc.get("template_variables") or {}
    env_topology = tv.get("environments") if isinstance(tv.get("environments"), dict) else {}
    alias = _env_alias(environment)
    env_cfg = {}
    for key in (environment, alias, "production" if alias == "prod" else "", "testing" if alias == "test" else ""):
        if key and isinstance(env_topology.get(key), dict):
            env_cfg = env_topology.get(key) or {}
            break
    raw_group_servers = _server_names_for_group(server_group, db) if server_group else []
    explicit_env_servers = _service_servers_for_environment(svc, environment)
    recommended_servers = _select_servers_for_service_env(raw_group_servers or explicit_env_servers or svc.get("servers", []) or [], svc, service, environment)
    return {
        "system": system,
        "service": service,
        "service_name": svc.get("name") or service,
        "display_name": svc.get("display_name") or svc.get("name") or service,
        "template": svc.get("template", ""),
        "environment": environment,
        "environment_alias": alias,
        "service_dir": env_cfg.get("service_dir") or tv.get("service_dir") or tv.get("deploy_path") or "",
        "deploy_path": env_cfg.get("deploy_path") or tv.get("deploy_path") or tv.get("service_dir") or "",
        "update_script": env_cfg.get("update_script") or tv.get("update_script") or "",
        "rollback_script": env_cfg.get("rollback_script") or tv.get("rollback_script") or "",
        "health_url": env_cfg.get("health_url") or tv.get("health_url") or "",
        "health_cmd": env_cfg.get("health_cmd") or env_cfg.get("health_command") or tv.get("health_cmd") or tv.get("health_command") or "",
        "log_path": env_cfg.get("log_path") or tv.get("log_path") or tv.get("log_dir") or "logs",
        "process_keyword": env_cfg.get("process_keyword") or tv.get("process_keyword") or tv.get("pm2_name") or tv.get("service_name") or service,
        "server_keywords": svc.get("server_keywords") or tv.get("server_keywords") or [],
        "servers_by_env": svc.get("servers_by_env") or tv.get("servers_by_env") or {},
        "environment_config": env_cfg,
        "recommended_servers": recommended_servers,
    }




def _extract_script_targets(command: str) -> List[str]:
    """Extract executable script file tokens from a shell command.

    update_script may be a simple path (./updatebin.sh) or a complete shell
    command (echo 2 | ./updatebin.sh, bash ./deploy.sh, sh -c './www.sh').
    Precheck should verify the real script file, not the whole command string.
    """
    raw = str(command or "").strip()
    if not raw:
        return []
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.replace("&&", " ").replace("||", " ").replace(";", " ").replace("|", " ").split()
    candidates: List[str] = []
    wrappers = {"sh", "bash", "zsh", "source", ".", "env", "sudo", "nohup", "time", "timeout", "echo", "cat", "printf", "chmod", "test"}
    operators = {"&&", "||", "|", ";", "(", ")"}
    for token in tokens:
        t = str(token).strip().strip("'\"")
        if not t or t in operators or t in wrappers or t.startswith("-"):
            continue
        if t.startswith(("./", "/")) or t.endswith((".sh", ".bash", ".zsh")):
            candidates.append(t)
    seen = set()
    ordered: List[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _remote_script_path(service_dir: str, script_token: str) -> str:
    token = str(script_token or "").strip()
    if not token:
        return ""
    if token.startswith("/"):
        return token
    return f"{service_dir.rstrip('/')}/{token.lstrip('./')}"

def _remote_checks_for_service(ssh, topology: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    service_dir = topology.get("service_dir") or topology.get("deploy_path") or ""
    update_script = topology.get("update_script") or ""
    log_path = topology.get("log_path") or ""
    proc_kw = topology.get("process_keyword") or ""
    if not ssh:
        return checks
    if service_dir:
        try:
            code, out, _ = ssh.exec(f"test -d {shlex.quote(service_dir)} && echo ok || echo missing", timeout=10)
            ok = "ok" in (out or "")
            checks.append({"name": "目标目录", "status": "ok" if ok else "error", "detail": f"{service_dir} {'存在' if ok else '不存在'}"})
        except Exception as e:
            checks.append({"name": "目标目录", "status": "warn", "detail": f"无法检查 {service_dir}: {e}"})
        if update_script:
            script_targets = _extract_script_targets(update_script)
            if script_targets:
                for script_token in script_targets:
                    script_path = _remote_script_path(service_dir, script_token)
                    try:
                        code, out, _ = ssh.exec(f"test -f {shlex.quote(script_path)} && echo ok || echo missing", timeout=10)
                        ok = "ok" in (out or "")
                        checks.append({"name": "发布脚本", "status": "ok" if ok else "error", "detail": f"{script_path} {'存在' if ok else '不存在'}"})
                    except Exception as e:
                        checks.append({"name": "发布脚本", "status": "warn", "detail": f"无法检查脚本 {script_token}: {e}"})
            else:
                checks.append({"name": "发布脚本", "status": "info", "detail": f"更新命令为内联命令，预检不按文件校验: {update_script}"})
        if log_path:
            remote_log = log_path if log_path.startswith("/") else f"{service_dir.rstrip('/')}/{log_path}"
            try:
                code, out, _ = ssh.exec(f"test -e {shlex.quote(remote_log)} && echo ok || echo missing", timeout=10)
                ok = "ok" in (out or "")
                checks.append({"name": "日志路径", "status": "ok" if ok else "warn", "detail": f"{remote_log} {'存在' if ok else '不存在或首次发布'}"})
            except Exception as e:
                checks.append({"name": "日志路径", "status": "warn", "detail": f"无法检查日志路径: {e}"})
    if proc_kw:
        try:
            cmd = f"ps -ef | grep -v grep | grep -E {shlex.quote(str(proc_kw))} | head -3"
            code, out, _ = ssh.exec(cmd, timeout=10)
            checks.append({"name": "进程状态", "status": "ok" if code == 0 and (out or '').strip() else "warn", "detail": "检测到进程" if code == 0 and (out or '').strip() else f"未检测到关键字: {proc_kw}"})
        except Exception as e:
            checks.append({"name": "进程状态", "status": "warn", "detail": f"无法检查进程: {e}"})
    return checks


def _deploy_confirm_text(req: DeployRequest) -> str:
    service_part = req.service or "-"
    env_part = req.environment or "-"
    return f"确认发布 {req.system}/{service_part} 到 {env_part}"


def _extract_change_reason(payload: Dict[str, Any] | None) -> str:
    payload = payload or {}
    variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
    return str(
        payload.get("reason")
        or payload.get("change_reason")
        or variables.get("reason")
        or variables.get("change_reason")
        or variables.get("release_reason")
        or ""
    ).strip()


def _step_display_type(step: Dict[str, Any]) -> str:
    return str(step.get("type") or step.get("step_type") or "command")


def _step_command_preview(step: Dict[str, Any], limit: int = 180) -> str:
    config = step.get("config") if isinstance(step.get("config"), dict) else {}
    command = str(config.get("command") or config.get("script") or config.get("update_script") or "").strip()
    if not command:
        return ""
    lowered = command.lower()
    if any(token in lowered for token in ["password=", "passwd=", "token=", "secret=", "authorization:"]):
        return "命令包含敏感字段，已隐藏预览"
    return command if len(command) <= limit else command[: limit - 1] + "…"


def _build_confirmation_execution_plan(steps: List[Dict[str, Any]], topology: Dict[str, Any]) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    if topology.get("service_dir") or topology.get("deploy_path"):
        plan.append({
            "name": "确认目标目录",
            "type": "preflight",
            "description": topology.get("service_dir") or topology.get("deploy_path"),
        })
    for idx, step in enumerate(steps or [], start=1):
        step_type = _step_display_type(step)
        name = str(step.get("name") or step_type or f"step-{idx}")
        plan.append({
            "name": name,
            "type": step_type,
            "description": _step_command_preview(step) or f"执行 {step_type} 步骤",
        })
    return plan[:12]


def _build_confirmation_impact(req: DeployRequest, topology: Dict[str, Any], steps: List[Dict[str, Any]], rollback_plan: Dict[str, Any]) -> Dict[str, Any]:
    target_paths = []
    for key in ["service_dir", "deploy_path", "log_path"]:
        value = topology.get(key)
        if value and value not in target_paths:
            target_paths.append(value)
    write_steps = [s for s in (steps or []) if _step_display_type(s) not in {"check", "precheck", "notify"}]
    return {
        "server_count": len(req.servers or []),
        "target_servers": req.servers or [],
        "target_paths": target_paths,
        "step_count": len(steps or []),
        "write_step_count": len(write_steps),
        "package_name": req.file_name or "",
        "version": req.version or req.file_name or "",
        "rollback_safe": bool((rollback_plan or {}).get("safe")),
        "rollback_mode": (rollback_plan or {}).get("mode") or "unknown",
    }


def _build_confirmation_risk_reasons(
    req: DeployRequest,
    blockers: List[str],
    warnings: List[str],
    steps: List[Dict[str, Any]],
    rollback_plan: Dict[str, Any],
    package_match: Dict[str, Any],
) -> List[str]:
    reasons: List[str] = []
    if _env_alias(req.environment) == "prod":
        reasons.append("目标环境为生产环境，需要更严格确认")
    if blockers:
        reasons.append(f"存在 {len(blockers)} 个阻断项")
    if warnings:
        reasons.append(f"存在 {len(warnings)} 个警告项")
    if len(req.servers or []) >= 3:
        reasons.append(f"一次影响 {len(req.servers)} 台服务器")
    if not (rollback_plan or {}).get("safe"):
        reasons.append("未发现自动安全回滚方案")
    if package_match.get("status") == "warn":
        reasons.append("发布包与服务名称不是强匹配")
    if not steps:
        reasons.append("未解析到发布步骤")
    if not reasons:
        reasons.append("低风险：非生产环境、目标明确且未发现阻断项")
    return reasons


def _build_operator_checklist(confirmation: Dict[str, Any]) -> List[Dict[str, Any]]:
    impact = confirmation.get("impact") or {}
    rollback = confirmation.get("rollback_plan") or {}
    checklist = [
        {"label": "确认系统/服务/环境与变更单一致", "required": True},
        {"label": "确认发布包名称、版本或 sha256 无误", "required": True},
        {"label": f"确认目标服务器数量：{impact.get('server_count', 0)}", "required": True},
        {"label": "确认已完成发布预检或 Dry Run", "required": True},
    ]
    if not rollback.get("safe"):
        checklist.append({"label": "当前没有安全自动回滚方案，需准备手工回滚步骤", "required": True})
    if confirmation.get("requires_confirmation"):
        checklist.append({"label": f"高风险发布需输入确认短语：{confirmation.get('required_confirmation') or '-'}", "required": True})
    return checklist


def _assert_strict_deploy_confirmation(req: DeployRequest, payload: Dict[str, Any], confirmation: Dict[str, Any]) -> None:
    if _env_alias(req.environment) != "prod":
        return
    expected = confirmation.get("required_confirmation") or confirmation.get("confirm_text") or _deploy_confirm_text(req)
    supplied = str(payload.get("confirm_text") or payload.get("confirmation") or "").strip()
    legacy_confirmed = bool(payload.get("confirm_production")) or supplied.upper() == "CONFIRM"
    if supplied != expected and not legacy_confirmed:
        raise HTTPException(status_code=400, detail=f"生产环境发布需要输入确认短语：{expected}")
    reason = _extract_change_reason(payload)
    if not reason:
        raise HTTPException(status_code=400, detail="生产环境发布必须填写 reason 或 change_reason")


def _build_confirmation(req: DeployRequest, db: Session, user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    req.servers = _derive_servers(req, db)
    req.variables = _merge_release_variables(req, db)
    svc = _find_config_service(req.system, req.service, req.environment)
    topology = _service_topology(req.system, req.service, req.environment, req.server_group, db)
    package_match = _package_service_match(req.file_name, svc, req.service)
    effective_pipeline_id = req.pipeline_id or (svc or {}).get("pipeline_id", "")
    steps = _db_pipeline_steps(db, effective_pipeline_id) or _default_release_steps(req, db)
    env_conflicts = _environment_server_conflicts(req.environment, req.servers)
    blockers: List[str] = []
    warnings: List[str] = []
    if not req.system:
        blockers.append("未选择系统")
    if not req.service:
        warnings.append("未选择服务，将使用系统默认流程")
    if not req.environment:
        warnings.append("未选择环境")
    if not req.servers:
        blockers.append("未选择或未解析到发布服务器")
    if env_conflicts:
        blockers.append("环境与服务器不一致：" + ", ".join(f"{x['name']}({x['actual']})" for x in env_conflicts[:10]))
    if package_match.get("status") == "error":
        blockers.append(package_match.get("message", "发布包错误"))
    elif package_match.get("status") == "warn":
        warnings.append(package_match.get("message", "发布包需要确认"))
    if not steps:
        blockers.append("未解析到可执行 Pipeline 步骤")
    rollback_plan = _rollback_plan_for(req.system, req.service, req.environment, req.servers, req.variables, db)
    risk_reasons = _build_confirmation_risk_reasons(req, blockers, warnings, steps, rollback_plan, package_match)
    risk_level = "high" if _env_alias(req.environment) == "prod" or blockers else "medium" if warnings or not rollback_plan.get("safe") else "low"
    if req.parallelism > 1 and _env_alias(req.environment) == "prod":
        risk_level = "high"
        risk_reasons.append(f"并发发布生产环境 (parallelism={req.parallelism})，已自动升级为高风险确认")
    confirm_text = _deploy_confirm_text(req)
    requires_confirmation = _env_alias(req.environment) == "prod" or risk_level == "high"
    if req.parallelism > 1 and _env_alias(req.environment) == "prod":
        confirm_text = "CONFIRM PARALLEL DEPLOY"
        requires_confirmation = True
    response = {
        "ready": len(blockers) == 0,
        "risk_level": risk_level,
        "risk_reasons": risk_reasons,
        "blockers": blockers,
        "warnings": warnings,
        "requires_confirmation": requires_confirmation,
        "required_confirmation": confirm_text if requires_confirmation else "",
        "confirm_text": confirm_text if requires_confirmation else "",
        "summary": {
            "system": req.system,
            "service": req.service,
            "environment": req.environment,
            "server_group": req.server_group,
            "servers": req.servers,
            "file_name": req.file_name,
            "version": req.version or req.file_name,
            "pipeline_id": effective_pipeline_id,
            "operator": (user or {}).get("username") if user else "",
        },
        "package": package_match.get("package", {}),
        "package_match": package_match,
        "topology": topology,
        "steps": [{"name": x.get("name"), "type": x.get("type") or x.get("step_type"), "config": x.get("config", {})} for x in steps],
        "execution_plan": _build_confirmation_execution_plan(steps, topology),
        "rollback_plan": rollback_plan,
    }
    response["impact"] = _build_confirmation_impact(req, topology, steps, rollback_plan)
    response["operator_checklist"] = _build_operator_checklist(response)
    return response


def _rollback_plan_for(system: str, service: str, environment: str, servers: List[str], variables: Dict[str, Any], db: Optional[Session] = None) -> Dict[str, Any]:
    svc = _find_config_service(system, service, environment)
    topology = _service_topology(system, service, environment, variables.get("server_group", ""), db)
    tv = (svc or {}).get("template_variables") or {}
    rollback_script = topology.get("rollback_script") or tv.get("rollback_script") or ""
    service_dir = topology.get("service_dir") or variables.get("service_dir") or variables.get("deploy_path") or ""
    update_script = topology.get("update_script") or variables.get("update_script") or ""
    template = (svc or {}).get("template") or variables.get("service_template") or ""
    if rollback_script:
        return {"mode": "script", "safe": True, "command": f"cd {service_dir} && {rollback_script}", "servers": servers, "description": "执行服务配置的 rollback_script"}
    if system == "crypto-trader" or update_script.endswith("updatebin.sh") or template == "generic_backend_direct":
        binary = tv.get("binary_name") or tv.get("service_name") or os.path.basename(service_dir.rstrip("/")) or "server"
        command = (
            f"cd {service_dir} && "
            f"if [ -f server.bak ]; then cp -f server.bak server && chmod +x server; "
            f"elif [ -f {shlex.quote(str(binary))}.bak ]; then cp -f {shlex.quote(str(binary))}.bak {shlex.quote(str(binary))} && chmod +x {shlex.quote(str(binary))}; "
            f"else echo 'missing backup binary'; exit 1; fi; "
            f"{update_script or './updatebin.sh'}"
        )
        return {"mode": "binary_bak", "safe": True, "command": command, "servers": servers, "description": "恢复 .bak 程序文件后重新执行 updatebin.sh"}
    if update_script.endswith("www.sh") or template == "generic_frontend":
        return {"mode": "manual", "safe": False, "command": "", "servers": servers, "description": "Web 发布需配置 rollback_script 或使用历史发布包重新发布"}
    deploy_path = variables.get("deploy_path") or service_dir or "/data/web/app"
    command = f"cd {deploy_path}/releases && prev=$(ls -1t | sed -n '2p') && test -n \"$prev\" && ln -sfn {deploy_path}/releases/$prev {deploy_path}/current"
    return {"mode": "symlink_previous", "safe": True, "command": command, "servers": servers, "description": "切换 current 到上一版 releases"}


def _deployment_to_rollback_candidate(row: Deployment) -> Dict[str, Any]:
    return {
        "id": row.id,
        "version": row.version or "",
        "status": row.status,
        "servers": [x.strip() for x in (row.servers or "").split(",") if x.strip()],
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_by": row.created_by or "",
    }


def _rollback_candidates_for(deployment: Deployment, db: Session, limit: int = 5) -> List[Dict[str, Any]]:
    q = db.query(Deployment).filter(
        Deployment.system == deployment.system,
        Deployment.status == "success",
        Deployment.id != deployment.id,
    )
    if deployment.service:
        q = q.filter(Deployment.service == deployment.service)
    if deployment.environment:
        q = q.filter(Deployment.environment == deployment.environment)
    if deployment.finished_at:
        q = q.filter(Deployment.finished_at <= deployment.finished_at)
    rows = q.order_by(Deployment.finished_at.desc(), Deployment.started_at.desc()).limit(max(1, min(limit, 20))).all()
    return [_deployment_to_rollback_candidate(row) for row in rows]


def _rollback_precheck_for(deployment: Deployment, plan: Dict[str, Any], db: Session) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    recommendations: List[str] = []
    servers = [x.strip() for x in (deployment.servers or "").split(",") if x.strip()]
    lock_key = f"{deployment.system}:{deployment.service or 'default'}:rollback"
    if deployment.status != "success":
        blockers.append("只有成功状态的发布单可以自动回滚")
    if not plan.get("safe") or not plan.get("command"):
        blockers.append("当前发布缺少可自动执行的回滚方案")
    if not servers:
        blockers.append("当前发布记录没有目标服务器")
    missing_servers = [name for name in servers if not get_server_by_name(name)]
    if missing_servers:
        blockers.append("服务器配置不存在：" + ", ".join(missing_servers[:10]))
    if DeployLock.is_locked(lock_key):
        blockers.append("同一服务已有回滚任务正在执行")
    if _env_alias(deployment.environment) == "prod":
        warnings.append("生产环境回滚需要管理员输入 CONFIRM 二次确认")
        recommendations.append("执行前确认最近一次备份、当前故障范围和回滚后健康检查方式")
    topology = _service_topology(deployment.system, deployment.service or "", deployment.environment or "", "", db)
    health_commands = build_rollback_health_commands(topology)
    if not health_commands:
        warnings.append("未配置回滚后健康检查；建议配置 health_url、health_cmd 或 process_keyword")
    if plan.get("mode") == "script":
        recommendations.append("该回滚将执行服务配置中的 rollback_script，请确认脚本幂等")
    elif plan.get("mode") == "symlink_previous":
        recommendations.append("该回滚会把 current 软链切到 releases 中上一版")
    elif plan.get("mode") == "binary_bak":
        recommendations.append("该回滚依赖 server.bak 或服务名.bak 文件存在")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "recommendations": recommendations,
        "risk_level": "high" if _env_alias(deployment.environment) == "prod" else "medium",
        "requires_confirmation": _env_alias(deployment.environment) == "prod",
        "confirm_text": "CONFIRM" if _env_alias(deployment.environment) == "prod" else "",
        "servers": servers,
        "lock_key": lock_key,
        "health_checks": [{"type": label, "command": command} for label, command in health_commands],
    }


def _log_rollback_health_commands(task_id: str, deployment_id: str, server_name: str, ssh: Any, topology: Dict[str, Any]) -> bool:
    """Run best-effort post-rollback health checks and write results to deploy logs."""
    checks = build_rollback_health_commands(topology)
    if not checks:
        _log_to_db(task_id, "warning", "未配置回滚后健康检查，建议为服务配置 health_url、health_cmd 或 process_keyword", f"rollback-health:{server_name}", deployment_id)
        return True
    all_ok = True
    for label, command in checks:
        try:
            _log_to_db(task_id, "info", f"健康检查({label}) > {command}", f"rollback-health:{server_name}", deployment_id)
            exit_code, out, err = ssh.exec(command, timeout=30)
            if exit_code == 0:
                detail = "Rollback health check passed"
                if out:
                    detail += ": " + " | ".join((out or "").splitlines()[:3])
                _log_to_db(task_id, "info", detail, f"rollback-health:{server_name}", deployment_id)
            else:
                all_ok = False
                _log_to_db(task_id, "warning", f"Rollback health check failed({label}): {err or out or exit_code}", f"rollback-health:{server_name}", deployment_id)
        except Exception as exc:
            all_ok = False
            _log_to_db(task_id, "warning", f"Rollback health check error({label}): {exc}", f"rollback-health:{server_name}", deployment_id)
    return all_ok


def _derive_servers(req: DeployRequest, db: Optional[Session] = None) -> List[str]:
    svc = _find_config_service(req.system, req.service, req.environment)

    if req.servers:
        return [x for x in req.servers if x]

    service_env_servers = _service_servers_for_environment(svc, req.environment)
    if service_env_servers:
        return service_env_servers

    if req.server_group:
        group_servers = _server_names_for_group(req.server_group, db)
        if group_servers:
            return _select_servers_for_service_env(group_servers, svc, req.service, req.environment)

    group = _find_dovo_group(req.system, req.service)
    if group and group.get("server"):
        return [group["server"]]

    if svc and svc.get("servers"):
        return _select_servers_for_service_env([x for x in svc.get("servers") or [] if x], svc, req.service, req.environment)
    return []


def _merge_release_variables(req: DeployRequest, db: Optional[Session] = None) -> Dict[str, Any]:
    variables: Dict[str, Any] = {}
    sys_cfg = _load_system_cfg(req.system)
    if isinstance(sys_cfg.get("variables"), dict):
        variables.update(sys_cfg.get("variables") or {})
    variables.update(_environment_variables_for_system(req.system, req.environment))

    group = _find_dovo_group(req.system, req.service)
    if group:
        variables.update({k: v for k, v in group.items() if k not in ("display_name",)})
    svc = _find_config_service(req.system, req.service, req.environment)
    if svc:
        if isinstance(svc.get("template_variables"), dict):
            variables.update(svc.get("template_variables") or {})
        for key in ("template", "display_name"):
            if key in svc:
                variables[f"service_{key}"] = svc.get(key)
    variables.update(req.variables or {})
    if req.server_group:
        variables["server_group"] = req.server_group
    variables["file_name"] = req.file_name
    variables["version"] = req.version or req.file_name
    variables["system"] = req.system
    variables["service"] = req.service
    variables.setdefault("deploy_path", variables.get("base_path") or variables.get("service_dir") or "/data/web/app")
    variables.setdefault("update_script", "./updatebin.sh" if req.system == "crypto-trader" else "./www.sh")
    return variables


def _db_pipeline_steps(db: Session, pipeline_id: str) -> List[Dict[str, Any]]:
    if not pipeline_id:
        return []
    pipeline_obj = PipelineRepository(db).get_by_id(pipeline_id)
    if not pipeline_obj:
        return []
    step_repo = PipelineStepRepository(db)
    return [
        {
            "id": s.id,
            "type": s.step_type,
            "step_type": s.step_type,
            "name": s.name,
            "config": s.config or {},
        }
        for s in sorted(step_repo.list_by_pipeline(pipeline_id), key=lambda x: x.sort_order)
    ]


def _default_release_steps(req: DeployRequest, db: Session) -> List[Dict[str, Any]]:
    """Map real operational runbooks to executable pipeline steps when no pipeline is selected."""
    group = _find_dovo_group(req.system, req.service)
    if group:
        return [{
            "type": "dovo_bluegreen_update",
            "name": f"Dovo {req.service.upper()} 蓝绿发布",
            "config": {
                "group_code": req.service,
                "base_path": group.get("base_path", "/data/bin/ata"),
                "instances": group.get("instances", [f"{req.service}1", f"{req.service}2"]),
                "update_script": group.get("update_script", "./binupdate.sh"),
                "switch_script": group.get("switch_script", "./portupdate.sh"),
                "wait_after_update": group.get("wait_after_update", 10),
                "file_name": req.file_name,
            },
        }]

    svc = _find_config_service(req.system, req.service, req.environment) or {}
    tv = svc.get("template_variables") or {}
    template = svc.get("template", "")
    update_script = tv.get("update_script", "")
    service_dir = tv.get("service_dir", "")
    deploy_path = tv.get("deploy_path", "")

    if template == "generic_frontend" or update_script.endswith("www.sh") or (deploy_path and req.service.endswith("frontend")):
        return [{
            "type": "web_script_update",
            "name": "Web 静态包发布",
            "config": {
                "deploy_path": deploy_path or "/data/www",
                "update_script": update_script or "./www.sh",
                "file_name": req.file_name,
            },
        }]

    if req.system == "crypto-trader" or update_script.endswith("updatebin.sh") or service_dir:
        return [{
            "type": "scripted_service_update",
            "name": "后台服务 updatebin 发布",
            "config": {
                "service_dir": service_dir or f"/data/bin/crypto-trader/{req.service.replace('crypto-', '')}",
                "update_script": update_script or "./updatebin.sh",
                "file_name": req.file_name,
                "log_dir": tv.get("log_dir", "logs"),
            },
        }]

    return [
        {"type": "upload", "name": "上传包", "config": {"file_name": req.file_name, "remote_path": f"/tmp/{req.file_name}"}},
        {"type": "deploy", "name": "部署", "config": {"package_path": f"/tmp/{req.file_name}"}},
        {"type": "health_check", "name": "健康检查", "config": {"url": req.variables.get("health_url", "http://localhost/health")}},
        {"type": "restart", "name": "重启", "config": {"cmd": req.variables.get("restart_cmd", req.variables.get("restart_command", "pm2 restart all"))}},
    ]



def _json_safe(value: Any) -> Any:
    """Return JSON-serializable value for task payloads."""
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _notification_defaults() -> Dict[str, Any]:
    return {
        "enabled": False,
        "webhook_urls": [],
        "events": ["deploy.success", "deploy.failed", "deploy.canceled", "rollback.success", "rollback.failed"],
        "timeout": 5,
    }


def _notification_settings(db: Session) -> Dict[str, Any]:
    """Load notification settings with a stable default schema.

    Older local installations may not have the config key yet. Returning a
    normalized default keeps GET /notifications safe and lets the UI render
    before the first explicit save.
    """
    from app.db import ConfigRepository

    defaults = _notification_defaults()
    raw = ConfigRepository(db).get("notification_settings") or {}
    if not isinstance(raw, dict):
        raw = {}
    timeout = raw.get("timeout", defaults["timeout"])
    try:
        timeout = max(1, min(int(timeout), 60))
    except (TypeError, ValueError):
        timeout = defaults["timeout"]
    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "webhook_urls": _clean_str_list(raw.get("webhook_urls", defaults["webhook_urls"])),
        "events": _clean_str_list(raw.get("events", defaults["events"])) or list(defaults["events"]),
        "timeout": timeout,
    }


def _task_cancel_requested(db: Session, task_id: str) -> bool:
    """Return the freshest cancel state for a running deployment task."""
    try:
        db.expire_all()
        task = DeployTaskRepository(db).get_by_id(task_id)
        if not task:
            return False
        return task_cancel_requested(task)
    except Exception:
        logger.warning("Failed to check deploy task cancel flag: %s", task_id, exc_info=True)
        return False


def _render_package_value(value: Any, variables: Dict[str, Any]) -> str:
    text_value = str(value or "")
    if not text_value:
        return ""
    exact = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text_value)
    if exact:
        replacement = variables.get(exact.group(1))
        return "" if replacement is None else str(replacement)
    return re.sub(
        r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
        lambda m: str(variables.get(m.group(1), m.group(0))),
        text_value,
    )


def _distribution_target_for_step(step: Dict[str, Any], file_name: str, variables: Dict[str, Any]) -> str:
    step_type = step.get("type") or step.get("step_type") or ""
    config = step.get("config") or {}
    remote_path = _render_package_value(config.get("remote_path") or config.get("remote_file") or "", variables)
    if step_type == "scripted_service_update":
        service_dir = _render_package_value(config.get("service_dir") or variables.get("service_dir") or variables.get("deploy_path") or "", variables)
        default_path = f"{service_dir.rstrip('/')}/{file_name}" if service_dir else f"/tmp/{file_name}"
    elif step_type == "web_script_update":
        deploy_path = _render_package_value(config.get("deploy_path") or variables.get("deploy_path") or "/data/www", variables)
        default_path = f"{deploy_path.rstrip('/')}/{file_name}"
    elif step_type == "dovo_bluegreen_update":
        default_path = f"/tmp/{file_name}"
    else:
        default_path = f"/tmp/{file_name}"
    target = remote_path or default_path
    if target.endswith("/"):
        target = f"{target.rstrip('/')}/{file_name}"
    return target


def _distributable_step(steps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for step in steps or []:
        step_type = step.get("type") or step.get("step_type") or ""
        if step_type in {"scripted_service_update", "web_script_update", "dovo_bluegreen_update"}:
            return step
    return None


def _remote_sha256(ssh: Any, remote_path: str) -> str:
    cmd = f"if command -v sha256sum >/dev/null 2>&1 && [ -f {shlex.quote(remote_path)} ]; then sha256sum {shlex.quote(remote_path)} | awk '{{print $1}}'; fi"
    exit_code, out, _ = ssh.exec(cmd, timeout=60)
    if exit_code != 0:
        return ""
    return (out or "").strip().splitlines()[-1].strip() if (out or "").strip() else ""


def _distribute_package_to_server(task_id: str, deployment_id: str, req: DeployRequest, ssh: Any, server_name: str, variables: Dict[str, Any], steps: List[Dict[str, Any]], db: Session) -> None:
    """Upload release artifact once for step types that can reuse a remote package.

    Generic upload/deploy pipelines keep their original UploadStep behavior; this
    helper only handles the specialized one-step release flows that read
    variables['package_distributed'] and variables['remote_package_path'].
    """
    step = _distributable_step(steps)
    if not step:
        return
    file_name = os.path.basename(req.file_name or variables.get("file_name") or req.version or "")
    if not file_name:
        return
    local_path = _safe_upload_file_path(file_name)
    if not os.path.isfile(local_path):
        raise FileNotFoundError(f"Local release package not found: {local_path}")

    remote_path = _distribution_target_for_step(step, file_name, variables)
    local_sha256 = _sha256_file(local_path)
    size_bytes = os.path.getsize(local_path)
    runtime_repo = DeploymentRuntimeRepository(db)
    dist = runtime_repo.create_distribution(
        deployment_id=deployment_id,
        task_id=task_id,
        server_name=server_name,
        package_name=file_name,
        local_path=local_path,
        remote_path=remote_path,
        local_sha256=local_sha256,
        size_bytes=size_bytes,
    )
    started = time.monotonic()
    try:
        existing_sha = _remote_sha256(ssh, remote_path)
        if existing_sha and existing_sha == local_sha256:
            variables["package_distributed"] = True
            variables["remote_package_path"] = remote_path
            variables["artifact_remote_path"] = remote_path
            runtime_repo.update_distribution(
                dist.id,
                status="success",
                reused=True,
                remote_sha256=existing_sha,
                duration_ms=int((time.monotonic() - started) * 1000),
                message="remote package already matches local sha256",
            )
            _log_to_db(task_id, "info", f"复用已存在发布包: {server_name}:{remote_path}", f"package:{server_name}", deployment_id)
            return

        _log_to_db(task_id, "info", f"分发发布包: {local_path} -> {server_name}:{remote_path}", f"package:{server_name}", deployment_id)
        ssh.upload(local_path, remote_path)
        remote_sha = _remote_sha256(ssh, remote_path)
        if remote_sha and remote_sha != local_sha256:
            raise RuntimeError(f"remote sha256 mismatch: local={local_sha256} remote={remote_sha}")
        variables["package_distributed"] = True
        variables["remote_package_path"] = remote_path
        variables["artifact_remote_path"] = remote_path
        runtime_repo.update_distribution(
            dist.id,
            status="success",
            reused=False,
            remote_sha256=remote_sha or "",
            duration_ms=int((time.monotonic() - started) * 1000),
            message="package uploaded",
        )
    except Exception as exc:
        runtime_repo.update_distribution(
            dist.id,
            status="failed",
            duration_ms=int((time.monotonic() - started) * 1000),
            message=str(exc),
        )
        raise




def _send_release_notification(event_type: str, deployment, payload: Optional[Dict[str, Any]], db: Session):
    """Best-effort notification event recorder; actual webhook sender is intentionally optional."""
    try:
        data = dict(payload or {})
        if deployment is not None:
            data.update({
                "deployment_id": getattr(deployment, "id", ""),
                "system": getattr(deployment, "system", ""),
                "service": getattr(deployment, "service", ""),
                "environment": getattr(deployment, "environment", ""),
                "status": getattr(deployment, "status", ""),
            })
        db.add(NotificationEvent(event_type=event_type, target="release", status="recorded", message=event_type, payload=data))
        db.commit()
    except Exception:
        logger.warning("Failed to record release notification event %s", event_type, exc_info=True)

_log_db_session = None

def _get_log_db():
    global _log_db_session
    if _log_db_session is not None:
        try:
            _log_db_session.execute(text("SELECT 1")).scalar()
            return _log_db_session
        except Exception:
            try:
                _log_db_session.close()
            except Exception:
                pass
            _log_db_session = None
    _log_db_session = SessionLocal()
    return _log_db_session


def _shutdown_log_db():
    global _log_db_session
    if _log_db_session is not None:
        try:
            _log_db_session.close()
        except Exception:
            pass
        _log_db_session = None


class _DeployLogBuffer:
    """Small in-process batch writer for deployment logs.

    SQLite handles many short writes, but deployment steps can emit bursts of
    logs.  Batching a handful of rows keeps the local UI responsive without
    introducing Redis/Celery for a small-team install.
    """

    def __init__(self) -> None:
        self.batch_size = max(1, int(os.getenv("DEPLOY_LOG_BATCH_SIZE", "20")))
        self.flush_interval = max(0.0, float(os.getenv("DEPLOY_LOG_FLUSH_INTERVAL_SECONDS", "0.5")))
        self._rows: List[Dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self._lock = threading.RLock()
        self._write_lock = threading.RLock()

    def append(self, row: Dict[str, Any]) -> None:
        rows_to_flush: List[Dict[str, Any]] = []
        with self._lock:
            self._rows.append(row)
            elapsed = time.monotonic() - self._last_flush
            if self.batch_size <= 1 or len(self._rows) >= self.batch_size or elapsed >= self.flush_interval:
                rows_to_flush = self._take_locked()
        if rows_to_flush:
            self._write(rows_to_flush)

    def flush(self) -> None:
        with self._lock:
            rows_to_flush = self._take_locked()
        if rows_to_flush:
            self._write(rows_to_flush)

    def _take_locked(self) -> List[Dict[str, Any]]:
        rows = list(self._rows)
        self._rows.clear()
        self._last_flush = time.monotonic()
        return rows

    def _write(self, rows: List[Dict[str, Any]]) -> None:
        with self._write_lock:
            db = _get_log_db()
            DeployLogRepository(db).create_many(rows)


_log_buffer = _DeployLogBuffer()


def _flush_deploy_logs():
    try:
        _log_buffer.flush()
    except Exception as e:
        logger.error(f"Failed to flush deploy logs: {e}")


def _log_to_db(task_id: str, level: str, message: str, step_name: str = "", deployment_id: str = ""):
    try:
        _log_buffer.append({
            "task_id": task_id,
            "level": level,
            "message": message,
            "step_name": step_name,
            "deployment_id": deployment_id or None,
        })
        if deployment_id:
            try:
                from app.deploy.stream import publish_log
                publish_log(deployment_id, task_id, level, message, step_name)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Failed to write deploy log: {e}")


async def _run_pipeline_task_one_server(
    server_name: str,
    idx: int,
    total_servers: int,
    task_id: str,
    deployment_id: str,
    req: DeployRequest,
    steps: List[Dict[str, Any]],
    db: Session,
    semaphore: Optional[asyncio.Semaphore],
    cancel_event: Optional[asyncio.Event],
) -> tuple:
    from config_manager import get_server_by_name

    if semaphore:
        await semaphore.acquire()
    server_task = None
    ssh = None
    try:
        if cancel_event and cancel_event.is_set():
            _log_to_db(task_id, "warning", f"已取消，跳过服务器: {server_name}", f"server:{server_name}", deployment_id)
            return (server_name, False, "skipped")

        if _task_cancel_requested(db, task_id):
            return (server_name, False, "canceled")

        runtime_repo = DeploymentRuntimeRepository(db)
        server_task = runtime_repo.create_server_task(deployment_id, task_id, server_name)
        runtime_repo.update_server_task(server_task.id, "running", f"开始服务器发布 ({idx}/{total_servers})")
        _log_to_db(task_id, "info", f"开始服务器发布: {server_name} ({idx}/{total_servers})", f"server:{server_name}", deployment_id)

        srv = get_server_by_name(server_name)
        if not srv:
            runtime_repo.update_server_task(server_task.id, "failed", f"服务器未找到: {server_name}")
            _log_to_db(task_id, "error", f"服务器未找到: {server_name}", f"server:{server_name}", deployment_id)
            return (server_name, False, f"服务器未找到: {server_name}")

        loop = asyncio.get_event_loop()
        ssh = await loop.run_in_executor(None, lambda: _connect_ssh(srv))

        if not ssh:
            msg = f"SSH connect failed: {server_name}"
            runtime_repo.update_server_task(server_task.id, "failed", msg)
            _log_to_db(task_id, "error", f"SSH 连接失败: {server_name}", f"server:{server_name}", deployment_id)
            return (server_name, False, msg)

        variables = _merge_release_variables(req, db)
        try:
            _distribute_package_to_server(task_id, deployment_id, req, ssh, server_name, variables, steps, db)
        except Exception as e:
            runtime_repo.update_server_task(server_task.id, "failed", f"发布包分发失败: {e}")
            _log_to_db(task_id, "error", f"发布包分发失败: {server_name}: {e}", f"package:{server_name}", deployment_id)
            return (server_name, False, f"发布包分发失败: {e}")

        def _step_callback(event: str, step_name: str, step_type: str, message: str = "", step_task_id: str = None, config: Dict[str, Any] = None):
            repo = DeploymentRuntimeRepository(db)
            if event == "start":
                captured = None
                if config is not None:
                    try:
                        captured = json.dumps(config, ensure_ascii=False, default=str)
                    except Exception:
                        captured = None
                item = repo.create_step_task(deployment_id, task_id, step_name, step_type, server_task.id, server_name, status="running", captured_config=captured)
                return item.id
            if step_task_id:
                status = "success" if event == "success" else "failed" if event == "failed" else event
                repo.update_step_task(step_task_id, status, message)
            return step_task_id

        engine = PipelineEngine(
            log_callback=lambda tid, lvl, msg, sn="": _log_to_db(tid, lvl, msg, sn, deployment_id),
            step_callback=_step_callback,
            cancel_checker=lambda: _task_cancel_requested(db, task_id),
        )
        ctx_data = {
            "system": req.system,
            "service": req.service,
            "environment": req.environment,
            "server": srv,
            "variables": variables,
            "version": req.version or req.file_name,
            "deploy_path": variables.get("deploy_path", "/data/web/app"),
            "ssh_client": ssh,
        }

        result = await engine.run(task_id, deployment_id, steps, ctx_data)

        if _task_cancel_requested(db, task_id) or result.get("canceled"):
            runtime_repo.update_server_task(server_task.id, "canceled", "Canceled by operator")
            _log_to_db(task_id, "warning", f"服务器发布取消: {server_name}", f"server:{server_name}", deployment_id)
            return (server_name, False, "canceled")

        if not result.get("success"):
            msg = result.get("error", "")
            runtime_repo.update_server_task(server_task.id, "failed", msg)
            _log_to_db(task_id, "error", f"服务器发布失败: {server_name}: {msg}", f"server:{server_name}", deployment_id)
            return (server_name, False, msg)

        runtime_repo.update_server_task(server_task.id, "success", "服务器发布成功")
        _log_to_db(task_id, "info", f"服务器发布成功: {server_name}", f"server:{server_name}", deployment_id)
        return (server_name, True, "")
    except Exception as e:
        logger.exception("Deploy task failed on %s", server_name)
        runtime_repo = DeploymentRuntimeRepository(db)
        if server_task:
            runtime_repo.update_server_task(server_task.id, "failed", str(e))
        _log_to_db(task_id, "error", f"部署异常: {server_name}: {e}", f"server:{server_name}", deployment_id)
        return (server_name, False, str(e))
    finally:
        if ssh:
            try:
                ssh.close()
            except Exception:
                pass
        if semaphore:
            semaphore.release()


async def _run_pipeline_task(task_id: str, deployment_id: str, req: DeployRequest, steps: List[Dict[str, Any]]):
    db = SessionLocal()
    task_repo = DeployTaskRepository(db)
    DeploymentRepository(db).update_status(deployment_id, "running")
    try:
        from app.deploy.stream import publish_status
        publish_status(deployment_id, task_id, "running")
    except Exception:
        pass

    total_servers = len(req.servers)
    parallelism = min(req.parallelism, total_servers, 16)
    fail_fast = req.fail_fast
    wave_size = req.wave_size

    cancel_event = asyncio.Event()

    try:
        if parallelism <= 1:
            serial_results: List[tuple] = []
            for idx, server_name in enumerate(req.servers, start=1):
                if _task_cancel_requested(db, task_id):
                    _log_to_db(task_id, "warning", "发布已取消，停止后续服务器", "cancel", deployment_id)
                    task_repo.update_status(task_id, "canceled", result="Canceled by operator")
                    DeploymentRepository(db).update_status(deployment_id, "canceled", "Canceled by operator")
                    return

                srv_result = await _run_pipeline_task_one_server(
                    server_name, idx, total_servers, task_id, deployment_id, req, steps, db, None, None,
                )
                serial_results.append(srv_result)
                _name, ok, err = srv_result
                if not ok:
                    if err == "canceled":
                        task_repo.update_status(task_id, "canceled", result="Canceled by operator")
                        DeploymentRepository(db).update_status(deployment_id, "canceled", "Canceled by operator")
                        return
                    break

            all_ok = all(r[1] for r in serial_results)
            _finalize_deployment_status(db, task_id, deployment_id, task_repo, all_ok, fail_fast=False, results=serial_results)
            return

        semaphore = asyncio.Semaphore(parallelism)
        servers = req.servers

        if wave_size and wave_size < len(servers):
            waves = [servers[i:i + wave_size] for i in range(0, len(servers), wave_size)]
        else:
            waves = [servers]

        all_results: List[tuple] = []
        wave_aborted = False

        for wave_idx, wave in enumerate(waves):
            if wave_aborted or cancel_event.is_set() or _task_cancel_requested(db, task_id):
                for remaining_srv in wave:
                    runtime_repo = DeploymentRuntimeRepository(db)
                    st = runtime_repo.create_server_task(deployment_id, task_id, remaining_srv)
                    runtime_repo.update_server_task(st.id, "skipped", "前序wave失败，跳过")
                    all_results.append((remaining_srv, False, "skipped"))
                for later_wave in waves[wave_idx + 1:]:
                    for remaining_srv in later_wave:
                        runtime_repo = DeploymentRuntimeRepository(db)
                        st = runtime_repo.create_server_task(deployment_id, task_id, remaining_srv)
                        runtime_repo.update_server_task(st.id, "skipped", "前序wave失败，跳过")
                        all_results.append((remaining_srv, False, "skipped"))
                break

            _log_to_db(task_id, "info", f"Wave {wave_idx + 1}/{len(waves)}: 并发发布 {len(wave)} 台服务器", f"wave:{wave_idx + 1}", deployment_id)

            coros = [
                _run_pipeline_task_one_server(
                    srv, idx + 1, total_servers, task_id, deployment_id, req, steps, db, semaphore, cancel_event,
                )
                for idx, srv in enumerate(wave)
            ]
            wave_results = await asyncio.gather(*coros, return_exceptions=True)

            for r in wave_results:
                if isinstance(r, Exception):
                    all_results.append(("unknown", False, str(r)))
                else:
                    all_results.append(r)

            if fail_fast:
                for _name, ok, err in wave_results if not isinstance(wave_results, Exception) else []:
                    if not ok and err != "skipped":
                        wave_aborted = True
                        break

            if _task_cancel_requested(db, task_id):
                cancel_event.set()
                wave_aborted = True

        _finalize_deployment_status(db, task_id, deployment_id, task_repo, all_success=False, fail_fast=fail_fast, results=all_results)
    finally:
        _flush_deploy_logs()
        task = task_repo.get_by_id(task_id)
        lock_keys = [x for x in str(getattr(task, "lock_key", "") or "").split(",") if x]
        release_deployment_locks(lock_keys, db)
        db.close()


def _finalize_deployment_status(db: Session, task_id: str, deployment_id: str, task_repo, all_success: bool, fail_fast: bool, results: List[tuple]):
    if _task_cancel_requested(db, task_id):
        task_repo.update_status(task_id, "canceled", result="Canceled by operator")
        DeploymentRepository(db).update_status(deployment_id, "canceled", "Canceled by operator")
        deployment = DeploymentRepository(db).get_by_id(deployment_id)
        _send_release_notification("deploy.canceled", deployment, {"task_id": task_id}, db)
        try:
            from app.deploy.stream import publish_done
            publish_done(deployment_id, "canceled")
        except Exception:
            pass
        return

    if all_success:
        task_repo.update_status(task_id, "success")
        DeploymentRepository(db).update_status(deployment_id, "success")
        deployment = DeploymentRepository(db).get_by_id(deployment_id)
        _send_release_notification("deploy.success", deployment, {"task_id": task_id}, db)
        try:
            from app.deploy.stream import publish_done
            publish_done(deployment_id, "success")
        except Exception:
            pass
        return

    success_count = sum(1 for _n, ok, _e in results if ok)
    fail_count = sum(1 for _n, ok, _e in results if not ok and _e != "skipped")
    skipped_count = sum(1 for _n, ok, _e in results if not ok and _e == "skipped")

    if fail_fast and fail_count > 0:
        final_status = "failed"
    elif success_count == 0:
        final_status = "failed"
    elif success_count > 0 and fail_count > 0:
        final_status = "partial_failed"
    else:
        final_status = "success"

    task_repo.update_status(task_id, final_status, result=f"success={success_count} failed={fail_count} skipped={skipped_count}")
    DeploymentRepository(db).update_status(deployment_id, final_status, f"success={success_count} failed={fail_count} skipped={skipped_count}")
    deployment = DeploymentRepository(db).get_by_id(deployment_id)
    notif_type = "deploy.success" if final_status == "success" else "deploy.partial_failed" if final_status == "partial_failed" else "deploy.failed"
    _send_release_notification(notif_type, deployment, {"task_id": task_id}, db)
    try:
        from app.deploy.stream import publish_done
        publish_done(deployment_id, final_status)
    except Exception:
        pass

def _connect_ssh(srv):
    try:
        from ssh_client import create_ssh_client
        ssh = create_ssh_client(srv)
        ssh.connect()
        return ssh
    except Exception as e:
        logger.error(f"SSH connect error: {e}")
        return None


def _invalid_worker_payload_reason(payload: Dict[str, Any]) -> str:
    request = payload.get("request") if isinstance(payload, dict) else {}
    if not isinstance(request, dict):
        return "Invalid deployment task payload: request must be an object"
    for field in ("system",):
        if not str(request.get(field) or "").strip():
            return f"Invalid deployment task payload: missing request.{field}"
    return ""


_deploy_worker = AsyncWorkerHandle("deploy-worker")


def ensure_deploy_worker_running():
    """Start the background deploy worker in the current event loop if needed."""
    _deploy_worker.ensure_running(_deploy_worker_loop)


async def _deploy_worker_loop():
    """Poll pending deployment tasks from DB so browser lifecycle does not control execution.

    Local-team installs usually run on a small machine, so the idle loop uses a
    configurable backoff instead of scanning the database every second forever.
    """
    idle_sleep = max(1.0, float(os.getenv("TASK_IDLE_POLL_SECONDS", "15")))
    active_sleep = max(0.5, float(os.getenv("TASK_ACTIVE_POLL_SECONDS", "2")))
    while True:
        db = SessionLocal()
        try:
            task = DeployTaskRepository(db).next_pending()
            if not task:
                db.close()
                await asyncio.sleep(idle_sleep)
                continue
            payload = json.loads(task.payload_json or "{}")
            _log_to_db(task.id, "info", "后台发布 Worker 已领取任务", "worker", task.deployment_id or "")
            _flush_deploy_logs()
            db.close()
            if payload.get("action") == "rollback":
                await _run_rollback_task(task.id, task.deployment_id or "", payload)
                continue
            invalid_reason = _invalid_worker_payload_reason(payload)
            if invalid_reason:
                err_db = SessionLocal()
                try:
                    DeployTaskRepository(err_db).update_status(task.id, "failed", result=invalid_reason)
                    if task.deployment_id:
                        DeploymentRepository(err_db).update_status(task.deployment_id, "failed", invalid_reason)
                finally:
                    err_db.close()
                continue
            req = DeployRequest(**(payload.get("request") or {}))
            steps = payload.get("steps") or []
            await _run_pipeline_task(task.id, task.deployment_id or "", req, steps)
        except Exception as e:
            logger.exception("Deploy worker error")
            try:
                err_db = SessionLocal()
                if 'task' in locals() and task:
                    DeployTaskRepository(err_db).update_status(task.id, "failed", result=str(e))
                    if task.deployment_id:
                        DeploymentRepository(err_db).update_status(task.deployment_id, "failed", str(e))
                err_db.close()
            except Exception:
                logger.exception("Failed to mark worker task failed")
            try:
                if 'db' in locals():
                    db.close()
            except Exception:
                pass
            _flush_deploy_logs()
            await asyncio.sleep(active_sleep)


def _rollback_runtime() -> RollbackRuntime:
    return RollbackRuntime(
        session_factory=SessionLocal,
        task_repo_cls=DeployTaskRepository,
        deployment_repo_cls=DeploymentRepository,
        deploy_request_cls=DeployRequest,
        get_server_by_name=get_server_by_name,
        connect_ssh=_connect_ssh,
        log_to_db=_log_to_db,
        merge_release_variables=_merge_release_variables,
        rollback_plan_for=_rollback_plan_for,
        service_topology=_service_topology,
        run_health_checks=_log_rollback_health_commands,
        send_release_notification=_send_release_notification,
        release_deployment_locks=release_deployment_locks,
    )


async def _run_rollback_task(task_id: str, deployment_id: str, payload: Dict[str, Any]):
    """Execute rollback work from DeployTask payload.

    Used by Capability Server/MCP tools and the durable deploy worker. Keep this
    wrapper thin so external tool-triggered rollbacks and browser rollbacks share
    the same implementation in app.deploy.rollback.
    """
    lock_keys = [x for x in str(payload.get("lock_key") or "").split(",") if x]
    await _rollback_runtime().run(
        task_id,
        deployment_id,
        lock_keys=lock_keys,
        notification_context={"via": "capability_server"},
        worker_name="deploy-worker",
    )

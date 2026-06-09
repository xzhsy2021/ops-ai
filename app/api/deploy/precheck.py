"""发布管理 - 预检逻辑（增强版：含变量继承链、回滚就绪评估、系统/服务关联拓扑）"""
from __future__ import annotations

import shlex
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Request, Depends
from sqlalchemy.orm import Session

from app.api.helpers import api_response
from app.db import get_db, DeploymentRepository
from app.db.models import Deployment
from app.core.auth_v2 import require_auth
from app.deploy.schemas import DeployRequest
from app.deploy.preflight import build_preflight_payload
from app.core.deploy_lock import DeployLock

from app.api.deploy._shared import (
    _build_confirmation,
    _connect_ssh,
    _derive_servers,
    _env_alias,
    _environment_server_conflicts,
    _find_config_service,
    _load_system_cfg,
    _merge_release_variables,
    _package_info,
    _package_service_match,
    _remote_checks_for_service,
    _rollback_precheck_for,
    _rollback_plan_for,
    _rollback_candidates_for,
    _server_looks_like_test,
    _service_aliases,
    _service_topology,
    logger,
)

precheck_router = APIRouter(tags=["发布管理v2-预检"])


def _resolve_variable_inheritance_chain(
    system_name: str,
    service_name: str,
    environment: str = "",
) -> Dict[str, Any]:
    """解析 系统 → 环境 → 服务 变量继承链，返回完整的继承拓扑。

    三层继承关系：
      系统层 variables        → 系统级默认变量
      环境层 variables         → 环境级覆盖（按DEV/TEST/PROD切换）
      服务层 template_variables → 服务级最终覆盖

    Returns:
        {
            "system": { "name": ..., "variables": {...} },
            "environment": { "name": ..., "variables": {...} },
            "service": { "name": ..., "template_variables": {...} },
            "merged": {...},       # 最终合并后的变量
            "inheritance_trace": [ # 每个 key 的来源
                { "key": "deploy_path", "source": "system.variables" },
                { "key": "health_url", "source": "service.template_variables" },
            ]
        }
    """
    sys_cfg = _load_system_cfg(system_name)
    svc = _find_config_service(system_name, service_name, environment) or {}

    sys_vars = sys_cfg.get("variables", {})
    if not isinstance(sys_vars, dict):
        sys_vars = {}

    env_vars: Dict[str, Any] = {}
    env_name = environment or ""
    if environment and sys_cfg.get("environments"):
        envs = sys_cfg.get("environments", {})
        alias = _env_alias(environment)
        for key in (environment, alias, ""):
            if key and isinstance(envs.get(key), dict):
                ev = envs[key].get("variables") or {}
                if isinstance(ev, dict):
                    env_vars = ev
                    env_name = key
                break

    svc_vars = svc.get("template_variables") or {}
    if not isinstance(svc_vars, dict):
        svc_vars = {}

    merged: Dict[str, Any] = {}
    trace: List[Dict[str, str]] = []
    seen_keys: set = set()

    for key, val in sys_vars.items():
        merged[key] = val
        trace.append({"key": key, "source": "system.variables", "value_preview": str(val)[:80]})
        seen_keys.add(key)
    for key, val in env_vars.items():
        merged[key] = val
        source = f"environment.{env_name}.variables"
        if key in seen_keys:
            trace = [t for t in trace if t["key"] != key]
        trace.append({"key": key, "source": source, "value_preview": str(val)[:80]})
        seen_keys.add(key)
    for key, val in svc_vars.items():
        merged[key] = val
        if key in seen_keys:
            trace = [t for t in trace if t["key"] != key]
        trace.append({"key": key, "source": "service.template_variables", "value_preview": str(val)[:80]})
        seen_keys.add(key)

    return {
        "system": {
            "name": system_name,
            "display_name": sys_cfg.get("display_name") or system_name,
            "variables": sys_vars,
        },
        "environment": {
            "name": env_name,
            "variables": env_vars,
        },
        "service": {
            "name": svc.get("name") or service_name,
            "display_name": svc.get("display_name") or service_name,
            "template": svc.get("template", ""),
            "template_variables": svc_vars,
        },
        "merged": merged,
        "inheritance_trace": trace,
    }


def _collect_precheck_checks(
    system: str,
    service: str,
    environment: str,
    file_name: str,
    servers_raw: List[str],
    variables: Dict[str, Any],
    server_group: str = "",
    db: Optional[Session] = None,
) -> tuple:
    """收集所有预检项，返回 (checks, ssh_fail, remote_disks, ssh_cache, topology, package_match)。"""
    svc = _find_config_service(system, service, environment)
    topology = _service_topology(system, service, environment, server_group, db)

    if not servers_raw:
        try:
            servers_raw = _derive_servers(DeployRequest(
                system=system,
                service=service,
                environment=environment,
                file_name=file_name,
                servers=[],
                server_group=server_group,
                variables=variables,
            ), db)
        except Exception:
            logger.warning("Failed to derive servers for precheck system=%s service=%s", system, service)
            servers_raw = []

    checks: List[Dict[str, Any]] = []

    env_conflicts = _environment_server_conflicts(
        environment,
        [str(x) if isinstance(x, str) else str((x or {}).get("name") or "")
         for x in (servers_raw or [])]
    )
    if env_conflicts:
        expected = env_conflicts[0].get("expected", "匹配环境")
        details = ", ".join(
            f"{item['name']}({item['actual']})" for item in env_conflicts[:10]
        )
        checks.append({
            "name": "环境服务器一致性", "status": "error",
            "detail": f"当前环境 {environment or '-'} 只允许选择{expected}服务器；冲突服务器：{details}"
        })
    else:
        checks.append({
            "name": "环境服务器一致性",
            "status": "ok" if environment else "warn",
            "detail": environment or "未指定环境"
        })

    package_match = _package_service_match(file_name, svc, service)
    pkg = package_match.get("package", {})
    if file_name and pkg.get("exists"):
        checks.append({"name": "部署包", "status": "ok",
                        "detail": f"{file_name} ({pkg.get('size_mb', '-')}MB)"})
    elif file_name:
        checks.append({"name": "部署包", "status": "error",
                        "detail": f"文件不存在: {file_name}"})
    else:
        checks.append({"name": "部署包", "status": "warn", "detail": "未指定文件名"})
    checks.append({
        "name": "发布包匹配",
        "status": package_match.get("status", "info"),
        "detail": package_match.get("message", "")
    })

    lock_key = f"{system}:{service or 'default'}"
    if DeployLock.is_locked(lock_key):
        checks.append({
            "name": "发布锁", "status": "warn",
            "detail": f"服务 '{lock_key}' 正在被部署"
        })
    else:
        checks.append({"name": "发布锁", "status": "ok", "detail": "无冲突"})

    if environment:
        env_label = environment.lower()
        if env_label in ("prod", "production"):
            checks.append({
                "name": "环境", "status": "warn",
                "detail": "生产环境 - 仅 Admin 可发布"
            })
        else:
            checks.append({"name": "环境", "status": "ok", "detail": environment})
    else:
        checks.append({"name": "环境", "status": "warn", "detail": "未指定环境"})

    ssh_ok = 0
    ssh_fail = 0
    remote_disks: List[Dict[str, str]] = []
    ssh_cache: Dict[str, Any] = {}

    from config_manager import get_server_by_name

    for s in (servers_raw or []):
        srv = get_server_by_name(s) if isinstance(s, str) else s
        if not srv:
            ssh_fail += 1
            continue
        srv_name = s if isinstance(s, str) else s.get("name", "")
        try:
            ssh = _connect_ssh(srv)
            if ssh:
                ssh_cache[srv_name] = ssh
                exit_code, out, _ = ssh.exec(
                    "df -h / 2>/dev/null | tail -1", timeout=10
                )
                if exit_code == 0:
                    ssh_ok += 1
                    parts = (out or "").strip().split()
                    if len(parts) >= 5 and len(remote_disks) < 3:
                        remote_disks.append({
                            "server": srv_name,
                            "used": parts[2], "avail": parts[3], "pct": parts[4]
                        })
                    if len([c for c in checks if str(c.get("name", "")).startswith("远程服务")]) < 12:
                        for rc in _remote_checks_for_service(ssh, topology):
                            checks.append({
                                "name": f"远程服务/{srv_name}/{rc['name']}",
                                "status": rc["status"], "detail": rc["detail"]
                            })
                else:
                    ssh_fail += 1
            else:
                ssh_fail += 1
        except Exception:
            ssh_fail += 1

    total = ssh_ok + ssh_fail
    if total == 0:
        checks.append({"name": "SSH连通性", "status": "warn", "detail": "未指定服务器"})
    elif ssh_fail == 0:
        checks.append({
            "name": "SSH连通性", "status": "ok",
            "detail": f"{ssh_ok}/{total} 台可达"
        })
    else:
        checks.append({
            "name": "SSH连通性", "status": "error",
            "detail": f"{ssh_fail}/{total} 台不可达"
        })

    if remote_disks:
        low_disks = [d for d in remote_disks if int(d["pct"].rstrip("%")) > 85]
        if low_disks:
            checks.append({
                "name": "远程磁盘", "status": "error",
                "detail": f"{len(low_disks)}台磁盘使用超85%: " +
                          ", ".join(f"{d['server']}({d['pct']})" for d in low_disks)
            })
        else:
            checks.append({
                "name": "远程磁盘", "status": "ok",
                "detail": f"{len(remote_disks)}台空间充足"
            })

    deploy_path = variables.get("deploy_path", topology.get("deploy_path", ""))
    if deploy_path and ssh_ok > 0:
        first_srv_name = (
            servers_raw[0] if isinstance(servers_raw[0], str)
            else servers_raw[0].get("name", "")
        )
        ssh = ssh_cache.get(first_srv_name)
        if ssh:
            try:
                exit_code, _, _ = ssh.exec(
                    f"test -d {shlex.quote(deploy_path)} && echo ok || echo missing",
                    timeout=10
                )
                checks.append({
                    "name": "目标路径",
                    "status": "ok" if exit_code == 0 else "error",
                    "detail": f"{deploy_path}" + (" 存在" if exit_code == 0 else " 不存在")
                })
            except Exception:
                checks.append({
                    "name": "目标路径", "status": "warn", "detail": "无法检查"
                })
    elif deploy_path:
        checks.append({
            "name": "目标路径", "status": "warn",
            "detail": deploy_path + " (无可用SSH)"
        })

    for ssh in ssh_cache.values():
        try:
            ssh.close()
        except Exception:
            pass

    health_url = variables.get("health_url", "")
    if health_url:
        try:
            import urllib.request
            resp = urllib.request.urlopen(health_url, timeout=5)
            checks.append({
                "name": "健康检查URL", "status": "ok",
                "detail": f"{health_url} → {resp.status}"
            })
        except Exception as e:
            checks.append({
                "name": "健康检查URL", "status": "warn",
                "detail": f"不可达: {e}"
            })
    else:
        checks.append({
            "name": "健康检查URL", "status": "info", "detail": "未配置"
        })

    return checks, ssh_fail, remote_disks, ssh_cache, topology, package_match


@precheck_router.post("/precheck")
@precheck_router.post("/preflight")
async def deploy_precheck(request: Request, db: Session = Depends(get_db)):
    """执行发布前预检（环境一致性、SSH连通性、磁盘空间、健康检查、发布锁等）。"""
    require_auth(request, db)
    data = await request.json()
    system = data.get("system", "")
    servers_raw: List[str] = data.get("servers", [])
    file_name: str = data.get("file_name", "")
    environment: str = data.get("environment", "")
    service: str = data.get("service", "")
    variables: Dict[str, Any] = data.get("variables") or {}

    checks, ssh_fail, remote_disks, _ssh_cache, topology, package_match = \
        _collect_precheck_checks(
            system, service, environment, file_name,
            servers_raw, variables, data.get("server_group", ""), db
        )

    pkg = package_match.get("package", {})
    servers_list = [
        str(x) if isinstance(x, str) else str((x or {}).get("name") or "")
        for x in (servers_raw or [])
    ]

    req_for_confirm = DeployRequest(
        system=system, service=service, environment=environment,
        file_name=file_name, servers=servers_list,
        server_group=data.get("server_group", ""), variables=variables,
        parallelism=int(data.get("parallelism", 1)),
        fail_fast=bool(data.get("fail_fast", True)),
        wave_size=data.get("wave_size"),
    )
    confirmation = _build_confirmation(req_for_confirm, db, {})

    inheritance = _resolve_variable_inheritance_chain(system, service, environment)

    parallelism = req_for_confirm.parallelism
    wave_size = req_for_confirm.wave_size
    effective_parallelism = min(parallelism, len(servers_list), 16)
    expected_waves = 1
    if wave_size and wave_size < len(servers_list):
        expected_waves = (len(servers_list) + wave_size - 1) // wave_size
    elif effective_parallelism < len(servers_list):
        expected_waves = 1
    parallel_summary = {
        "servers": servers_list,
        "parallelism": effective_parallelism,
        "expected_waves": expected_waves,
        "fail_fast": req_for_confirm.fail_fast,
        "wave_size": wave_size,
    }

    return api_response(data={
        **build_preflight_payload(
            checks=checks,
            environment=environment,
            file_name=file_name,
            ssh_fail=ssh_fail,
            servers=servers_list,
            remote_disks=remote_disks,
            topology=topology,
            package=pkg,
            required_confirmation=confirmation.get("required_confirmation") or "",
            requires_confirmation=bool(confirmation.get("requires_confirmation")),
        ),
        "parallel_summary": parallel_summary,
    })


@precheck_router.post("/variables/inheritance")
async def variable_inheritance_chain(
    request: Request,
    db: Session = Depends(get_db),
):
    """查询 系统 → 环境 → 服务 变量继承链。

    返回三层变量合并结果及每个 key 的来源追踪，
    方便前端展示变量从哪里来、被谁覆盖。
    """
    require_auth(request, db)
    data = await request.json()
    system = data.get("system", "")
    service = data.get("service", "")
    environment = data.get("environment", "")
    if not system:
        raise HTTPException(400, "缺少 system 参数")
    result = _resolve_variable_inheritance_chain(system, service, environment)
    return api_response(data=result)


@precheck_router.post("/rollback-readiness")
async def rollback_readiness(
    request: Request,
    db: Session = Depends(get_db),
):
    """评估指定部署的回滚就绪状态。

    检查项包括：部署状态、回滚方案可用性、目标服务器存在性、
    回滚锁冲突、生产环境确认要求等。
    """
    require_auth(request, db)
    data = await request.json()
    deployment_id = data.get("deployment_id")
    if not deployment_id:
        raise HTTPException(400, "缺少 deployment_id")

    repo = DeploymentRepository(db)
    deployment = repo.get_by_id(deployment_id)
    if not deployment:
        raise HTTPException(404, "未找到该发布记录")

    servers = [x.strip() for x in (deployment.servers or "").split(",") if x.strip()]
    req = DeployRequest(
        system=deployment.system,
        service=deployment.service or "",
        environment=deployment.environment or "",
        version=deployment.version or "",
        servers=servers,
        file_name=deployment.version or "",
        variables={},
    )
    variables = _merge_release_variables(req, db)
    plan = _rollback_plan_for(
        deployment.system,
        deployment.service or "",
        deployment.environment or "",
        servers,
        variables,
        db,
    ) or {}
    precheck_result = _rollback_precheck_for(deployment, plan, db)
    candidates = _rollback_candidates_for(deployment, db)
    topology = _service_topology(
        deployment.system,
        deployment.service or "",
        deployment.environment or "",
        "",
        db
    )

    return api_response(data={
        "deployment_id": deployment_id,
        "system": deployment.system,
        "service": deployment.service,
        "environment": deployment.environment,
        "deployment_status": deployment.status,
        "rollback_plan": plan,
        "precheck": precheck_result,
        "candidates": candidates,
        "topology": topology,
        "ready": (
            deployment.status == "success"
            and plan.get("safe")
            and not precheck_result.get("blockers")
        ),
    })


@precheck_router.get("/system/{system_name}/services")
async def system_service_topology(
    system_name: str,
    request: Request,
    environment: str = "",
    db: Session = Depends(get_db),
):
    """获取系统下所有服务的关联拓扑信息。

    返回系统-服务-变量继承的完整视图，包含每个服务的
    template、variables、servers、recommended_servers 等。
    """
    require_auth(request, db)
    sys_cfg = _load_system_cfg(system_name)
    if not sys_cfg:
        raise HTTPException(404, f"系统 '{system_name}' 不存在")

    services = sys_cfg.get("services", []) or []
    result: List[Dict[str, Any]] = []
    for svc in services:
        if not isinstance(svc, dict):
            continue
        svc_name = svc.get("name", "")
        topology = _service_topology(system_name, svc_name, environment, "", db)
        inheritance = _resolve_variable_inheritance_chain(
            system_name, svc_name, environment
        )
        result.append({
            "name": svc_name,
            "display_name": svc.get("display_name") or svc_name,
            "template": svc.get("template", ""),
            "repo": svc.get("repo", ""),
            "servers": svc.get("servers", []),
            "template_variables": svc.get("template_variables", {}),
            "topology": topology,
            "variable_inheritance": inheritance,
        })

    return api_response(data={
        "system": system_name,
        "display_name": sys_cfg.get("display_name") or system_name,
        "strategy": sys_cfg.get("strategy", "DIRECT"),
        "system_variables": sys_cfg.get("variables", {}),
        "services": result,
    })

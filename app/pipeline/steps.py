import os
import re
import shlex
import asyncio
import logging
from typing import Dict, Any, List, Optional
from .step import Step, StepContext

logger = logging.getLogger(__name__)


def _record_package_usage(ctx: StepContext, package_name: str, usage_type: str = "deploy") -> None:
    """Best-effort bookkeeping so that the File Center shows a recent
    `last_used_at` and bumped `used_count` for the package just published.

    Each call uses an independent SQLAlchemy session to avoid colliding with
    the deployment's own session (which lives on a different thread and is
    in the middle of a long-running SSH task). Failures are logged but never
    raised — recording usage is auxiliary and must not break a successful
    deployment.
    """
    if not package_name:
        return
    try:
        from app.db import SessionLocal
        from app.services.package_retention import record_package_reference
    except Exception:
        logger.exception("package retention helpers unavailable, skip record")
        return
    server_name = ""
    server_obj = ctx.server or {}
    if isinstance(server_obj, dict):
        server_name = server_obj.get("name") or server_obj.get("host") or ""
    session = SessionLocal()
    try:
        record_package_reference(
            session,
            package_name=package_name,
            deployment_id=ctx.deployment_id or "",
            system=ctx.system or "",
            service=ctx.service or "",
            environment=ctx.environment or "",
            server_name=server_name,
            usage_type=usage_type,
            commit=True,
        )
    except Exception:
        logger.exception("record_package_reference failed for %s", package_name)
    finally:
        try:
            session.close()
        except Exception:
            pass


_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _first_non_empty(*values: Any, default: str = "") -> str:
    for value in values:
        if value is not None and value != "":
            return str(value)
    return default


def _interpolate(value: Any, ctx: StepContext) -> Any:
    """Resolve ${var} placeholders in step config at execution time."""
    if isinstance(value, str):
        data = {
            **(ctx.variables or {}),
            "system": ctx.system,
            "service": ctx.service,
            "environment": ctx.environment,
            "version": ctx.version,
            "deploy_path": ctx.deploy_path,
            "server_name": ctx.server.get("name", "") if isinstance(ctx.server, dict) else "",
        }
        exact = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
        if exact and exact.group(1) in data:
            return data[exact.group(1)]
        return _VAR_PATTERN.sub(lambda m: str(data.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: _interpolate(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, ctx) for v in value]
    return value


def _shell_quote(value: Any) -> str:
    return shlex.quote(str(value))




def _extract_script_targets(command: str) -> List[str]:
    """Return script file tokens embedded in a shell command.

    Examples:
    - ./updatebin.sh -> [./updatebin.sh]
    - echo 2 | ./updatebin.sh -> [./updatebin.sh]
    - bash ./deploy.sh --flag -> [./deploy.sh]
    """
    raw = str(command or "").strip()
    if not raw:
        return []
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.replace("&&", " ").replace("||", " ").replace(";", " ").replace("|", " ").split()
    wrappers = {"sh", "bash", "zsh", "source", ".", "env", "sudo", "nohup", "time", "timeout", "echo", "cat", "printf", "chmod", "test"}
    operators = {"&&", "||", "|", ";", "(", ")"}
    candidates: List[str] = []
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


def _chmod_scripts_command(script_command: str) -> str:
    targets = _extract_script_targets(script_command)
    if not targets:
        return "true"
    parts = [f"chmod +x {_shell_quote(target)} 2>/dev/null || true" for target in targets]
    return "; ".join(parts)

def _local_upload_path(file_name: str = "", explicit_path: str = "") -> str:
    """Find an uploaded artifact from absolute path, cwd-relative path, or AppConfig.UPLOAD_DIR."""
    candidates: List[str] = []
    if explicit_path:
        candidates.append(explicit_path)
        if not os.path.isabs(explicit_path):
            candidates.append(os.path.abspath(explicit_path))
    if file_name:
        candidates.append(file_name)
        try:
            from app.core.config import get_runtime_path
            candidates.append(os.path.join(get_runtime_path("UPLOAD_DIR", "uploads"), os.path.basename(file_name)))
        except Exception:
            pass
        candidates.append(os.path.join("uploads", os.path.basename(file_name)))
        candidates.append(os.path.join("data", "uploads", os.path.basename(file_name)))
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    # Preserve the best error target for useful logs.
    return explicit_path or (os.path.join("uploads", os.path.basename(file_name)) if file_name else "")


def _remote_artifact_path(config: Dict[str, Any], ctx: StepContext, default_dir: str = "/tmp") -> str:
    file_name = os.path.basename(_first_non_empty(config.get("file_name"), ctx.variables.get("file_name"), ctx.version, default="artifact"))
    remote_path = _first_non_empty(config.get("remote_path"), config.get("remote_file"), default=f"{default_dir.rstrip('/')}/{file_name}")
    if remote_path.endswith("/"):
        remote_path = f"{remote_path.rstrip('/')}/{file_name}"
    return remote_path


def _looks_like_archive(path: str) -> bool:
    lower = path.lower()
    return lower.endswith((".tar.gz", ".tgz", ".tar", ".zip"))


async def _run_required(ctx: StepContext, cmd: str, step_name: str, timeout: int = 120, tail_output: bool = True) -> str:
    ctx.log("info", f"> {cmd}", step_name)
    if not ctx.ssh_client:
        ctx.log("warning", f"无 SSH 连接，跳过: {cmd}", step_name)
        return ""
    exit_code, out, err = await ctx.ssh_exec(cmd, timeout=timeout)
    if out and tail_output:
        lines = out.splitlines()
        preview = "\n".join(lines[-20:])
        if preview:
            ctx.log("info", preview, step_name)
    if err:
        err_preview = "\n".join(err.splitlines()[-20:])
        if err_preview:
            ctx.log("warning", err_preview, step_name)
    if exit_code != 0:
        raise RuntimeError(f"Command failed({exit_code}): {cmd}\n{err or out}")
    return out or ""


def _assert_log_patterns(output: str, must_contain, must_not_contain, where: str) -> None:
    text = output or ""
    for pat in must_contain or []:
        pat_s = str(pat).strip()
        if pat_s and pat_s not in text:
            raise RuntimeError(f"{where} 失败：日志中缺少必要片段 {pat_s!r}")
    for pat in must_not_contain or []:
        pat_s = str(pat).strip()
        if pat_s and pat_s in text:
            raise RuntimeError(f"{where} 失败：日志中出现禁止片段 {pat_s!r}")


class CheckoutStep(Step):
    step_type = "checkout"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        repo = config.get("repo", ctx.variables.get("repo", ""))
        branch = config.get("branch", ctx.variables.get("branch", "main"))
        dest = config.get("dest", f"/tmp/ops-build/{ctx.version}")

        if not repo:
            ctx.log("warning", "未配置 repo，跳过 checkout", "checkout")
            return

        ctx.log("info", f"检出代码: {repo} ({branch})", "checkout")

        if ctx.ssh_client:
            cmds = [
                f"mkdir -p {_shell_quote(dest)}",
                f"git clone --depth 1 --branch {_shell_quote(branch)} {_shell_quote(repo)} {_shell_quote(dest)} 2>&1 || (cd {_shell_quote(dest)} && git fetch origin && git reset --hard origin/{shlex.quote(str(branch))})",
            ]
            for cmd in cmds:
                exit_code, out, err = await ctx.ssh_exec(cmd, timeout=300)
                if exit_code != 0:
                    raise RuntimeError(f"Checkout failed: {err or out}")
        else:
            ctx.log("warning", "无 SSH 连接，跳过 checkout", "checkout")

        ctx.log("info", "代码检出完成", "checkout")


class BuildStep(Step):
    step_type = "build"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        cmd = config.get("cmd", ctx.variables.get("build_cmd", ctx.variables.get("build_command", "npm run build")))
        workdir = config.get("workdir", f"/tmp/ops-build/{ctx.version}")
        timeout = int(config.get("timeout", 300))

        ctx.log("info", f"构建: {cmd}", "build")

        if ctx.ssh_client:
            full_cmd = f"cd {_shell_quote(workdir)} && {cmd}"
            exit_code, out, err = await ctx.ssh_exec(full_cmd, timeout=timeout)
            if exit_code != 0:
                raise RuntimeError(f"Build failed: {err or out}")
            if out:
                for line in out.splitlines()[-5:]:
                    ctx.log("info", line, "build")
        else:
            ctx.log("warning", "无 SSH 连接，跳过 build", "build")

        ctx.log("info", "构建完成", "build")


class UploadStep(Step):
    step_type = "upload"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        file_name = _first_non_empty(config.get("file_name"), ctx.variables.get("file_name"), ctx.version)
        local_path = _local_upload_path(file_name=file_name, explicit_path=config.get("local_path", ""))
        remote_path = _remote_artifact_path(config, ctx, default_dir="/tmp")

        if not local_path or not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        remote_dir = os.path.dirname(remote_path) or "/tmp"
        ctx.log("info", f"上传 {local_path} -> {remote_path}", "upload")

        if ctx.ssh_client:
            await _run_required(ctx, f"mkdir -p {_shell_quote(remote_dir)}", "upload", timeout=30, tail_output=False)
            await ctx.ssh_upload(local_path, remote_path)
        else:
            ctx.log("warning", "无 SSH 连接，跳过上传", "upload")

        ctx.variables["artifact_remote_path"] = remote_path
        ctx.log("info", "上传完成", "upload")


class DeployStep(Step):
    step_type = "deploy"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        deploy_path = config.get("deploy_path", ctx.deploy_path)
        version = ctx.version or "latest"
        release_dir = f"{deploy_path}/releases/{version}"
        package_path = config.get("package_path", ctx.variables.get("artifact_remote_path", f"/tmp/{version}.tar.gz"))

        ctx.log("info", f"部署到 {release_dir}", "deploy")

        cmds = [
            f"mkdir -p {_shell_quote(release_dir)}",
            f"tar -xzf {_shell_quote(package_path)} -C {_shell_quote(release_dir)}",
        ]

        for cmd in cmds:
            await _run_required(ctx, cmd, "deploy", timeout=120)

        ctx.log("info", "部署完成（未切换 current 链接，需 SwitchStep）", "deploy")


class HealthCheckStep(Step):
    step_type = "health_check"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        url = config.get("url", ctx.variables.get("health_url", "http://localhost/health"))
        retries = int(config.get("retries", 3))
        interval = int(config.get("interval", 5))

        ctx.log("info", f"健康检查: {url}", "health_check")

        last_ok = False
        for attempt in range(retries):
            if ctx.ssh_client:
                check_cmd = f"curl -sf -o /dev/null {_shell_quote(url)} --max-time 5"
                exit_code, _, _ = await ctx.ssh_exec(check_cmd, timeout=10)
                if exit_code == 0:
                    last_ok = True
                    break
            else:
                ctx.log("warning", "无 SSH 连接，模拟健康检查通过", "health_check")
                last_ok = True
                break

            ctx.log("info", f"健康检查重试 {attempt + 1}/{retries}", "health_check")
            await asyncio.sleep(interval)

        if not last_ok:
            raise RuntimeError(f"健康检查失败: {url}")

        ctx.log("info", "健康检查通过", "health_check")


class SwitchStep(Step):
    step_type = "switch"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        deploy_path = config.get("deploy_path", ctx.deploy_path)
        version = ctx.version or "latest"
        release_dir = config.get("target", f"{deploy_path}/releases/{version}")
        current_link = config.get("link", f"{deploy_path}/current")

        ctx.log("info", f"切换 current -> {release_dir}", "switch")

        if ctx.ssh_client:
            readlink_cmd = f"readlink -f {_shell_quote(current_link)} 2>/dev/null || echo NONE"
            exit_code, out, _ = await ctx.ssh_exec(readlink_cmd, timeout=10)
            previous_target = out.strip() if exit_code == 0 and out.strip() != "NONE" else ""

            if previous_target and previous_target != release_dir:
                ctx.add_rollback("switch", {
                    "current_link": current_link,
                    "previous_target": previous_target,
                })
                ctx.log("info", f"记录当前指向: {previous_target}", "switch")

            switch_cmd = f"ln -sfn {_shell_quote(release_dir)} {_shell_quote(current_link)}"
            await _run_required(ctx, switch_cmd, "switch", timeout=30)
        else:
            ctx.log("warning", "无 SSH 连接，跳过切换", "switch")

        ctx.log("info", f"切换完成: {current_link} -> {release_dir}", "switch")


class RestartStep(Step):
    step_type = "restart"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        cmd = config.get("cmd", ctx.variables.get("restart_cmd", ctx.variables.get("restart_command", "pm2 restart all")))
        ctx.log("info", f"重启服务: {cmd}", "restart")

        if ctx.ssh_client:
            exit_code, out, err = await ctx.ssh_exec(cmd, timeout=60)
            if exit_code != 0:
                raise RuntimeError(f"Restart failed: {err or out}")
        else:
            ctx.log("warning", "无 SSH 连接，跳过重启", "restart")

        ctx.log("info", "重启完成", "restart")


class RollbackStep(Step):
    step_type = "rollback"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        ctx.log("info", "开始回滚", "rollback")

        if not ctx.rollback_stack:
            ctx.log("warning", "无显式回滚元数据，跳过自动回滚", "rollback")
            return

        for rb in reversed(ctx.rollback_stack):
            action = rb.get("action")
            data = rb.get("data", {})
            if action in ("switch", "deploy") and ctx.ssh_client:
                prev = data.get("previous_target", data.get("previous", ""))
                link = data.get("current_link", "")
                if prev and link:
                    cmd = f"ln -sfn {_shell_quote(prev)} {_shell_quote(link)}"
                    exit_code, out, err = await ctx.ssh_exec(cmd, timeout=60)
                    if exit_code == 0:
                        ctx.log("info", f"回滚链接: {link} -> {prev}", "rollback")
                    else:
                        ctx.log("error", f"回滚失败: {err}", "rollback")
                        raise RuntimeError(f"Rollback switch failed: {err}")

        ctx.log("info", "回滚完成（基于 rollback_stack 显式元数据）", "rollback")


class CommandStep(Step):
    step_type = "command"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        cmd = config.get("cmd", config.get("command", ""))
        timeout = int(config.get("timeout", 120))

        if not cmd:
            ctx.log("warning", "空命令，跳过", "command")
            return

        await _run_required(ctx, cmd, config.get("name", "command"), timeout=timeout)


class WaitStep(Step):
    step_type = "wait"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        seconds = int(config.get("seconds", config.get("wait_seconds", 5)))
        ctx.log("info", f"等待 {seconds} 秒...", "wait")
        await asyncio.sleep(seconds)
        ctx.log("info", "等待完成", "wait")


class ScriptedServiceUpdateStep(Step):
    """Actual backend service update: upload artifact to service_dir, run updatebin.sh, inspect logs."""
    step_type = "scripted_service_update"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        service_dir = _first_non_empty(config.get("service_dir"), ctx.variables.get("service_dir"))
        if not service_dir:
            raise RuntimeError("service_dir is required")
        file_name = os.path.basename(_first_non_empty(config.get("file_name"), ctx.variables.get("file_name"), ctx.version, default="artifact"))
        local_path = _local_upload_path(file_name=file_name, explicit_path=config.get("local_path", ""))
        update_script = _first_non_empty(config.get("update_script"), ctx.variables.get("update_script"), default="./updatebin.sh")
        log_dir = _first_non_empty(config.get("log_dir"), ctx.variables.get("log_dir"), default="logs")
        timeout = int(config.get("timeout", 600))
        remote_artifact = _first_non_empty(config.get("remote_path"), ctx.variables.get("remote_package_path"), default=f"{service_dir.rstrip('/')}/{file_name}")

        if not ctx.variables.get("package_distributed") and not os.path.isfile(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        ctx.log("info", f"量化/脚本服务发布: {service_dir}", "scripted_service_update")
        if ctx.ssh_client:
            await _run_required(ctx, f"test -d {_shell_quote(service_dir)}", "检查服务目录", timeout=15, tail_output=False)
            if ctx.variables.get("package_distributed"):
                ctx.log("info", f"使用已分发制品 {remote_artifact}", "scripted_service_update")
            else:
                await _run_required(ctx, f"if [ -f {_shell_quote(remote_artifact)} ]; then cp -p {_shell_quote(remote_artifact)} {_shell_quote(remote_artifact)}.preops.$(date +%Y%m%d%H%M%S); fi", "备份旧服务包", timeout=30, tail_output=False)
                await ctx.ssh_upload(local_path, remote_artifact)
                ctx.log("info", f"已上传制品到 {remote_artifact}", "scripted_service_update")
            chmod_cmd = _chmod_scripts_command(update_script)
            await _run_required(ctx, f"cd {_shell_quote(service_dir)} && ({chmod_cmd}) && {update_script}", "执行更新脚本", timeout=timeout)
            await _run_required(
                ctx,
                f"cd {_shell_quote(service_dir)} && "
                "echo '=== process ===' && (ps -ef | grep -E './|/server|/system|/trader|/strategy|/exchange|/risk|/sender|/puller|/monitor|/supplier|/transaction' | grep -v grep || true) && "
                f"echo '=== logs ===' && (find {_shell_quote(log_dir)} -maxdepth 1 -type f 2>/dev/null | sort | tail -n 3 | xargs -r -I{{}} sh -c 'echo --- {{}}; tail -n 40 {{}}')",
                "检查程序日志",
                timeout=60,
            )
        else:
            ctx.log("warning", "无 SSH 连接，跳过脚本服务发布", "scripted_service_update")
        ctx.log("info", "脚本服务发布完成", "scripted_service_update")
        _record_package_usage(ctx, file_name, usage_type="script_update")


class WebScriptUpdateStep(Step):
    """Actual web release: upload local package to /data/www and run ./www.sh."""
    step_type = "web_script_update"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        deploy_path = _first_non_empty(config.get("deploy_path"), ctx.variables.get("deploy_path"), ctx.deploy_path, default="/data/www")
        update_script = _first_non_empty(config.get("update_script"), ctx.variables.get("update_script"), default="./www.sh")
        file_name = os.path.basename(_first_non_empty(config.get("file_name"), ctx.variables.get("file_name"), ctx.version, default="artifact"))
        local_path = _local_upload_path(file_name=file_name, explicit_path=config.get("local_path", ""))
        remote_artifact = _first_non_empty(config.get("remote_path"), ctx.variables.get("remote_package_path"), default=f"{deploy_path.rstrip('/')}/{file_name}")
        timeout = int(config.get("timeout", 600))

        if not ctx.variables.get("package_distributed") and not os.path.isfile(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        ctx.log("info", f"Web 发布: 上传到 {deploy_path} 并执行 {update_script}", "web_script_update")
        if ctx.ssh_client:
            await _run_required(ctx, f"mkdir -p {_shell_quote(deploy_path)}", "准备 Web 目录", timeout=30, tail_output=False)
            if ctx.variables.get("package_distributed"):
                ctx.log("info", f"使用已分发制品 {remote_artifact}", "web_script_update")
            else:
                await ctx.ssh_upload(local_path, remote_artifact)
                ctx.log("info", f"已上传制品到 {remote_artifact}", "web_script_update")
            chmod_cmd = _chmod_scripts_command(update_script)
            await _run_required(ctx, f"cd {_shell_quote(deploy_path)} && ({chmod_cmd}) && {update_script}", "执行 Web 更新脚本", timeout=timeout)
            await _run_required(ctx, f"cd {_shell_quote(deploy_path)} && ls -alh | tail -n 30", "检查 Web 目录", timeout=30)
        else:
            ctx.log("warning", "无 SSH 连接，跳过 Web 发布", "web_script_update")
        ctx.log("info", "Web 发布完成", "web_script_update")
        _record_package_usage(ctx, file_name, usage_type="web_update")


class DockerComposeUpdateStep(Step):
    """Docker Compose deployment: pull latest images from registry, restart containers, check status."""
    step_type = "docker_compose_update"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        compose_dir = _first_non_empty(config.get("compose_dir"), ctx.variables.get("compose_dir"), ctx.deploy_path)
        if not compose_dir:
            raise RuntimeError("compose_dir is required")
        compose_file = _first_non_empty(config.get("compose_file"), ctx.variables.get("compose_file"), default="docker-compose.yml")
        env_file = _first_non_empty(config.get("env_file"), ctx.variables.get("env_file"))
        ef_arg = f"--env-file {_shell_quote(env_file)} " if env_file else ""
        timeout = int(config.get("timeout", 600))

        ctx.log("info", f"Docker Compose 发布: {compose_dir}", "docker_compose_update")
        if not ctx.ssh_client:
            ctx.log("warning", "无 SSH 连接，跳过 Docker Compose 发布", "docker_compose_update")
            return

        # 确保 compose 目录存在
        await _run_required(ctx, f"test -d {_shell_quote(compose_dir)}", "检查 compose 目录", timeout=15, tail_output=False)

        # 拉取最新镜像
        await _run_required(
            ctx,
            f"cd {_shell_quote(compose_dir)} && docker compose {ef_arg}-f {_shell_quote(compose_file)} pull 2>&1",
            "拉取最新镜像",
            timeout=timeout,
        )

        # 启动/重启容器
        await _run_required(
            ctx,
            f"cd {_shell_quote(compose_dir)} && docker compose {ef_arg}-f {_shell_quote(compose_file)} up -d --remove-orphans 2>&1",
            "启动容器",
            timeout=timeout,
        )

        # 等待容器稳定
        wait_seconds = int(config.get("wait_after_up", 10))
        if wait_seconds > 0:
            ctx.log("info", f"等待 {wait_seconds} 秒容器稳定...", "docker_compose_update")
            await asyncio.sleep(wait_seconds)

        # 检查容器状态
        await _run_required(
            ctx,
            f"cd {_shell_quote(compose_dir)} && docker compose {ef_arg}-f {_shell_quote(compose_file)} ps 2>&1",
            "容器状态",
            timeout=30,
        )

        # 检查最近日志
        log_lines = int(config.get("log_tail_lines", 30))
        await _run_required(
            ctx,
            f"cd {_shell_quote(compose_dir)} && docker compose {ef_arg}-f {_shell_quote(compose_file)} logs --tail={log_lines} 2>&1",
            "容器日志",
            timeout=60,
        )

        ctx.log("info", "Docker Compose 发布完成", "docker_compose_update")
        compose_label = compose_file or "docker-compose.yml"
        _record_package_usage(ctx, f"docker-compose:{compose_label}", usage_type="docker_compose")


class DovoBlueGreenUpdateStep(Step):
    """Dovo actual operation: detect standby instance, binupdate, log check, portupdate, log check."""
    step_type = "dovo_bluegreen_update"

    async def run(self, ctx: StepContext, config: Dict[str, Any]):
        config = _interpolate(config, ctx)
        group_code = _first_non_empty(config.get("group_code"), ctx.service)
        base_path = _first_non_empty(config.get("base_path"), ctx.variables.get("base_path"), default="/data/bin/ata")
        instances_value = config.get("instances", ctx.variables.get("instances", []))
        if isinstance(instances_value, str):
            instances = [x.strip() for x in instances_value.split(",") if x.strip()]
        else:
            instances = [str(x).strip() for x in instances_value if str(x).strip()]
        if not instances and group_code:
            instances = [f"{group_code}1", f"{group_code}2"]
        if not instances:
            raise RuntimeError("instances is required for dovo_bluegreen_update")

        file_name = os.path.basename(_first_non_empty(config.get("file_name"), ctx.variables.get("file_name"), ctx.version, default="server"))
        local_path = _local_upload_path(file_name=file_name, explicit_path=config.get("local_path", ""))
        if not ctx.variables.get("package_distributed") and not os.path.isfile(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        update_script = _first_non_empty(config.get("update_script"), ctx.variables.get("update_script"), default="./binupdate.sh")
        switch_script = _first_non_empty(config.get("switch_script"), ctx.variables.get("switch_script"), default="./portupdate.sh")
        wait_after_binupdate = int(config.get("wait_after_binupdate", config.get("wait_after_update", ctx.variables.get("wait_after_update", 10))))
        wait_after_portupdate = int(config.get("wait_after_portupdate", 5))
        binupdate_timeout = int(config.get("binupdate_timeout", 180))
        portupdate_timeout = int(config.get("portupdate_timeout", 180))
        log_check_timeout = int(config.get("log_check_timeout", 60))
        detect_timeout = int(config.get("detect_timeout", 30))
        log_must_contain = config.get("log_must_contain") or []
        log_must_not_contain = config.get("log_must_not_contain") or []
        if isinstance(log_must_contain, str):
            log_must_contain = [s.strip() for s in log_must_contain.split(",") if s.strip()]
        if isinstance(log_must_not_contain, str):
            log_must_not_contain = [s.strip() for s in log_must_not_contain.split(",") if s.strip()]
        remote_tmp = _first_non_empty(config.get("remote_path"), ctx.variables.get("remote_package_path"), default=f"/tmp/{file_name}")
        instances_shell = " ".join(_shell_quote(i) for i in instances)

        ctx.log("info", f"Dovo {group_code or '-'} 蓝绿发布，实例: {', '.join(instances)}", "dovo_bluegreen_update")
        if not ctx.ssh_client:
            ctx.log("warning", "无 SSH 连接，跳过 Dovo 发布", "dovo_bluegreen_update")
            return

        if ctx.variables.get("package_distributed"):
            ctx.log("info", f"使用已分发制品 {remote_tmp}", "dovo_bluegreen_update")
        else:
            await ctx.ssh_upload(local_path, remote_tmp)
            ctx.log("info", f"已上传制品到 {remote_tmp}", "dovo_bluegreen_update")

        detect_command_override = config.get("detect_command")
        if detect_command_override:
            detect_cmd = str(detect_command_override)
        else:
            detect_cmd = f"""
set -eu
BASE={_shell_quote(base_path)}
STANDBY=""
ACTIVE=""
for inst in {instances_shell}; do
  dir="$BASE/$inst"
  if pgrep -af "$dir/server" >/dev/null 2>&1 || ps -ef | grep "$dir/server" | grep -v grep >/dev/null 2>&1; then
    echo "ACTIVE=$inst"
    ACTIVE="$inst"
  else
    echo "STANDBY=$inst"
    [ -z "$STANDBY" ] && STANDBY="$inst"
  fi
done
[ -n "$STANDBY" ] || {{ echo 'FATAL: 未找到未运行的旧程序/standby 实例'; exit 1; }}
echo "$STANDBY"
""".strip()
        out = await _run_required(ctx, detect_cmd, "识别旧程序", timeout=detect_timeout)
        standby = out.splitlines()[-1].strip()
        if standby not in instances:
            raise RuntimeError(f"Cannot determine standby instance from output: {out}")
        standby_dir = f"{base_path.rstrip('/')}/{standby}"
        ctx.variables["standby_instance"] = standby
        ctx.variables["standby_dir"] = standby_dir
        ctx.log("info", f"确认更新旧程序目录: {standby_dir}", "dovo_bluegreen_update")

        prep_cmd = f"""
set -eu
cd {_shell_quote(standby_dir)}
if [ -f server ]; then cp -p server server.preops.$(date +%Y%m%d%H%M%S); fi
if [ -d {_shell_quote(remote_tmp)} ]; then echo 'FATAL: remote artifact is a directory'; exit 1; fi
case {_shell_quote(remote_tmp)} in
  *.tar.gz|*.tgz) tar -xzf {_shell_quote(remote_tmp)} -C . ;;
  *.tar) tar -xf {_shell_quote(remote_tmp)} -C . ;;
  *.zip) unzip -o {_shell_quote(remote_tmp)} -d . ;;
  *) cp {_shell_quote(remote_tmp)} ./server ;;
esac
chmod +x ./server 2>/dev/null || true
""".strip()
        await _run_required(ctx, prep_cmd, "更新旧程序文件", timeout=binupdate_timeout)
        await _run_required(ctx, f"cd {_shell_quote(standby_dir)} && chmod +x ./binupdate.sh 2>/dev/null || true && {update_script}", "执行 binupdate.sh", timeout=binupdate_timeout)
        if wait_after_binupdate > 0:
            ctx.log("info", f"等待 {wait_after_binupdate} 秒观察启动", "等待启动")
            await asyncio.sleep(wait_after_binupdate)

        log_check_override = config.get("log_check_command")
        if log_check_override:
            log_cmd = str(log_check_override).replace("${standby_dir}", standby_dir)
        else:
            log_cmd = f"cd {_shell_quote(standby_dir)} && echo '=== process ===' && (ps -ef | grep {_shell_quote(standby_dir)} | grep -v grep || true) && echo '=== logs ===' && (find logs -maxdepth 1 -type f 2>/dev/null | sort | tail -n 3 | xargs -r -I{{}} sh -c 'echo --- {{}}; tail -n 60 {{}}')"

        first_out = await _run_required(ctx, log_cmd, "第一次日志检查", timeout=log_check_timeout)
        _assert_log_patterns(first_out, log_must_contain, log_must_not_contain, where="第一次日志检查")

        await _run_required(ctx, f"cd {_shell_quote(standby_dir)} && chmod +x ./portupdate.sh 2>/dev/null || true && {switch_script}", "执行 portupdate.sh 切换端口", timeout=portupdate_timeout)
        if wait_after_portupdate > 0:
            ctx.log("info", f"等待 {wait_after_portupdate} 秒观察端口切换", "等待切换")
            await asyncio.sleep(wait_after_portupdate)
        second_out = await _run_required(ctx, log_cmd, "第二次日志检查", timeout=log_check_timeout)
        _assert_log_patterns(second_out, log_must_contain, log_must_not_contain, where="第二次日志检查")
        ctx.log("info", f"Dovo {group_code or ''} 发布完成，已切到 {standby}", "dovo_bluegreen_update")
        _record_package_usage(ctx, file_name, usage_type="dovo_bluegreen")

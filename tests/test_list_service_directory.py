"""回归：ops.list_service_directory 的服务目录解析。

背景（2026-09-11 用户报障）：调用 ops.list_service_directory 报
"Service directory is not configured"，无法列出 /data/web——但该服务的
deploy_path 其实配着（前端服务 crypto-trader-web，deploy_path=/data/web）。

原因：服务目录在服务编辑页保存后落在 template_variables 里
（InventoryReadService.get_service 返回的是嵌套结构），而 _service_dir 只读顶层
cfg["deploy_path"]，于是按 UI 配置的服务全部误报未配置。

本测试锁住：
1. template_variables 里的 deploy_path / service_dir 能被解析为基准目录；
2. 顶层字段仍兼容（旧数据）；
3. 越界路径仍被拒绝，未知服务仍报 404——放宽取值不等于放宽边界。
"""
import pytest
from fastapi import HTTPException

from app.services.tool_adapters import server_tools
from app.services.tool_context import ToolContext


class _FakeSSH:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def exec(self, cmd, timeout=None):
        self.calls.append((cmd, timeout))
        return self.results.pop(0) if self.results else (0, "", "")

    def close(self):
        pass


def _ctx(scopes=None):
    return ToolContext(auth_type="tool_token", scopes=scopes or ["ops:read", "server:read"], username="ops-agent")


@pytest.fixture
def fake_ssh(monkeypatch):
    ssh = _FakeSSH([(0, "total 4\ndrwxr-xr-x 2 root root 4096 Sep 11 15:00 .\n", "")])
    monkeypatch.setattr(server_tools, "_connect", lambda name: (ssh, {"name": name}))
    return ssh


def _service_config(**template_variables):
    return {
        "found": True,
        "name": "crypto-trader-web",
        "system_name": "crypto-trader",
        "template_variables": dict(template_variables),
    }


def _patch_config(monkeypatch, cfg):
    monkeypatch.setattr(server_tools, "get_service_config", lambda params, ctx, db: cfg)


# ── 基准目录解析 ──

def test_service_dir_reads_deploy_path_from_template_variables(monkeypatch):
    """报障场景：deploy_path 在 template_variables 里也必须被识别。"""
    _patch_config(monkeypatch, _service_config(deploy_path="/data/web"))
    assert server_tools._service_dir("crypto-trader", "crypto-trader-web", _ctx(), None) == "/data/web"


def test_service_dir_reads_service_dir_from_template_variables(monkeypatch):
    _patch_config(monkeypatch, _service_config(service_dir="/data/svc/"))
    assert server_tools._service_dir("crypto-trader", "crypto-trader-web", _ctx(), None) == "/data/svc"


def test_service_dir_reads_compose_dir_from_template_variables(monkeypatch):
    """docker_compose 模板只用 compose_dir（crypto-docker-compose / puller-kline 等）。"""
    _patch_config(monkeypatch, _service_config(compose_dir="/data/crypto-trader"))
    assert server_tools._service_dir("crypto-trader", "crypto-docker-compose", _ctx(), None) == "/data/crypto-trader"


def test_service_dir_reads_bg_base_dir_from_template_variables(monkeypatch):
    """dovo 蓝绿服务只有 bg_base_dir（其下才是 bg_dirs 子目录）。"""
    _patch_config(monkeypatch, _service_config(bg_base_dir="/data/bin/ata", bg_log_dir="/root/.pm2/logs"))
    assert server_tools._service_dir("dovo", "dovo-pak", _ctx(), None) == "/data/bin/ata"


def test_service_dir_prefers_most_specific_key(monkeypatch):
    """同时配了多个目录键时取更具体的那个（service_dir > deploy_path > compose_dir > bg_base_dir）。"""
    _patch_config(monkeypatch, _service_config(
        service_dir="/data/bin/crypto-trader/puller",
        compose_dir="/data/crypto-trader",
    ))
    assert server_tools._service_dir("crypto-trader", "puller", _ctx(), None) == "/data/bin/crypto-trader/puller"


def test_service_dir_key_priority_is_stable():
    """守卫：解析顺序被改动会导致服务目录悄悄指向别的路径，锁死它。"""
    assert server_tools._SERVICE_DIR_KEYS == (
        "service_dir", "deploy_path", "compose_dir", "bg_base_dir",
    )


def test_service_dir_still_accepts_top_level_fields(monkeypatch):
    """顶层字段（旧数据/其他写入路径）保持兼容，且优先于 template_variables。"""
    cfg = _service_config(deploy_path="/data/web")
    cfg["deploy_path"] = "/data/legacy"
    _patch_config(monkeypatch, cfg)
    assert server_tools._service_dir("crypto-trader", "crypto-trader-web", _ctx(), None) == "/data/legacy"


def test_service_dir_reports_unconfigured_when_truly_missing(monkeypatch):
    _patch_config(monkeypatch, _service_config(update_script="./www.sh"))
    with pytest.raises(HTTPException) as exc:
        server_tools._service_dir("crypto-trader", "crypto-trader-web", _ctx(), None)
    assert exc.value.status_code == 400
    assert "not configured" in exc.value.detail


def test_service_dir_reports_404_for_unknown_service(monkeypatch):
    _patch_config(monkeypatch, {"found": False})
    with pytest.raises(HTTPException) as exc:
        server_tools._service_dir("crypto-trader", "nope", _ctx(), None)
    assert exc.value.status_code == 404


# ── 工具端到端 ──

def test_list_service_directory_lists_configured_directory(monkeypatch, fake_ssh):
    """端到端：配了 template_variables.deploy_path 的服务可正常列目录。"""
    _patch_config(monkeypatch, _service_config(deploy_path="/data/web"))
    result = server_tools.list_service_directory(
        {"server": "prod-master", "system": "crypto-trader", "service": "crypto-trader-web"},
        _ctx(),
        None,
    )

    assert result["path"] == "/data/web"
    assert result["exit_code"] == 0
    assert "www.sh" in result["stdout"] or "total" in result["stdout"]
    assert fake_ssh.calls[0][0].startswith("ls -lah ")


def test_list_service_directory_accepts_subpath_under_base(monkeypatch, fake_ssh):
    _patch_config(monkeypatch, _service_config(deploy_path="/data/web"))
    result = server_tools.list_service_directory(
        {"server": "prod-master", "system": "crypto-trader", "service": "crypto-trader-web", "path": "assets"},
        _ctx(),
        None,
    )
    assert result["path"] == "/data/web/assets"


def test_list_service_directory_rejects_path_outside_base(monkeypatch, fake_ssh):
    """放宽取值不放宽边界：基准目录之外的绝对路径仍被拒。"""
    _patch_config(monkeypatch, _service_config(deploy_path="/data/web"))
    with pytest.raises(HTTPException) as exc:
        server_tools.list_service_directory(
            {"server": "prod-master", "system": "crypto-trader", "service": "crypto-trader-web", "path": "/etc"},
            _ctx(),
            None,
        )
    assert exc.value.status_code == 400
    assert "must stay under" in exc.value.detail

"""Pipeline 标准变量注册表 — 连接服务配置与 Pipeline 步骤字段"""
from typing import Dict, List, Any, Optional


VARIABLE_REGISTRY: List[Dict[str, Any]] = [
    {"name": "deploy_path", "label": "部署路径", "type": "string", "source_field": "deploy_path",
     "required_by": ["deploy"]},
    {"name": "build_command", "label": "构建命令", "type": "string", "source_field": "build_command",
     "required_by": ["build"]},
    {"name": "restart_command", "label": "重启命令", "type": "string", "source_field": "restart_command",
     "required_by": ["restart"]},
    {"name": "health_url", "label": "健康检查 URL", "type": "string", "source_field": "health_url",
     "required_by": ["health_check"]},
    {"name": "repo", "label": "代码仓库", "type": "string", "source_field": "repo",
     "required_by": ["checkout"]},
    {"name": "branch", "label": "分支", "type": "string", "source_field": None,
     "required_by": ["checkout"]},
    {"name": "artifact_type", "label": "制品类型", "type": "string", "source_field": "artifact_type",
     "required_by": []},
    {"name": "version", "label": "版本号", "type": "string", "source_field": None,
     "required_by": []},
    {"name": "artifact_local_path", "label": "制品本地路径", "type": "string", "source_field": None,
     "required_by": ["upload"]},
    {"name": "artifact_remote_path", "label": "制品远程路径", "type": "string", "source_field": None,
     "required_by": ["upload", "deploy"]},
    {"name": "server_group_id", "label": "服务器组", "type": "string", "source_field": "server_group_id",
     "required_by": []},
    {"name": "server_names", "label": "服务器列表", "type": "list", "source_field": None,
     "required_by": []},
]

_INDEX_BY_NAME = {v["name"]: v for v in VARIABLE_REGISTRY}

STEP_FIELD_BINDINGS: Dict[str, List[Dict[str, str]]] = {
    "checkout": [
        {"field": "repo_url", "var": "repo", "label": "仓库地址"},
        {"field": "branch", "var": "branch", "label": "分支"},
    ],
    "build": [
        {"field": "cmd", "var": "build_command", "label": "构建命令"},
    ],
    "upload": [
        {"field": "local_path", "var": "artifact_local_path", "label": "本地路径"},
        {"field": "remote_path", "var": "artifact_remote_path", "label": "远程路径"},
    ],
    "deploy": [
        {"field": "deploy_path", "var": "deploy_path", "label": "部署路径"},
        {"field": "package_path", "var": "artifact_remote_path", "label": "包路径"},
    ],
    "restart": [
        {"field": "cmd", "var": "restart_command", "label": "重启命令"},
    ],
    "health_check": [
        {"field": "url", "var": "health_url", "label": "检查 URL"},
    ],
}


def get_variable(name: str) -> Optional[Dict[str, Any]]:
    return _INDEX_BY_NAME.get(name)


def list_variable_names() -> List[str]:
    return [v["name"] for v in VARIABLE_REGISTRY]


def get_variable_registry() -> List[Dict[str, Any]]:
    return VARIABLE_REGISTRY


def get_bindable_fields(step_type: str) -> List[Dict[str, str]]:
    return STEP_FIELD_BINDINGS.get(step_type, [])

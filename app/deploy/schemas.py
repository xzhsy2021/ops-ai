"""Request/response schemas for deploy and pipeline APIs.

Keeping the Pydantic models outside the route module makes app/api/deploy_v2.py
smaller without changing the public API or the execution logic.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DeployRequest(BaseModel):
    system: str
    service: str = ""
    environment: str = ""
    version: str = ""
    servers: List[str] = Field(default_factory=list)
    file_name: str = ""
    pipeline_id: str = ""
    server_group: str = ""
    variables: Dict[str, Any] = Field(default_factory=dict)
    parallelism: int = Field(default=1, ge=1, le=16)
    fail_fast: bool = True
    wave_size: Optional[int] = Field(default=None, ge=1, le=64)


class SystemServicePayload(BaseModel):
    name: str
    display_name: str = ""
    template: str = "generic_backend_direct"
    repo: str = ""
    pipeline_id: str = ""
    servers: List[str] = Field(default_factory=list)
    template_variables: Dict[str, Any] = Field(default_factory=dict)


class ResolutionPreviewRequest(BaseModel):
    system: str = ""
    service: str = ""
    environment: str = ""
    pipeline_id: str = ""
    runtime_overrides: Dict[str, Any] = Field(default_factory=dict)
    steps: Optional[List[Dict[str, Any]]] = None


class PipelineCreateRequest(BaseModel):
    name: str
    system_name: str
    description: str = ""
    strategy: str = "DIRECT"
    steps: List[Dict[str, Any]] = Field(default_factory=list)


class PipelineUpdateRequest(BaseModel):
    name: Optional[str] = None
    system_name: Optional[str] = None
    description: Optional[str] = None
    strategy: Optional[str] = None


class StepCreateRequest(BaseModel):
    name: str
    step_type: str = "command"
    config: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class StepUpdateRequest(BaseModel):
    name: str = ""
    step_type: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = -1


class BatchUpdateRequest(BaseModel):
    ids: List[str]
    updates: PipelineUpdateRequest


class SystemEnvironmentPayload(BaseModel):
    name: str
    display_name: str = ""
    category: str = "custom"
    description: str = ""
    base_path: str = ""
    servers: List[str] = Field(default_factory=list)
    variables: Dict[str, Any] = Field(default_factory=dict)


class SystemGroupPayload(BaseModel):
    code: str
    display_name: str = ""
    server: str = ""
    servers: List[str] = Field(default_factory=list)
    server_keywords: List[str] = Field(default_factory=list)
    servers_by_env: Dict[str, Any] = Field(default_factory=dict)
    variables: Dict[str, Any] = Field(default_factory=dict)

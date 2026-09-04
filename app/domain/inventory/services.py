import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InventoryReadService:
    """统一配置读取服务 — 第一阶段只收口读取，不迁移写入。"""

    def list_servers(self) -> List[Dict[str, Any]]:
        from app.config.servers import get_all_servers
        return get_all_servers()

    def get_server(self, name: str) -> Optional[Dict[str, Any]]:
        """Resolve a server by name / host / UUID.

        Kept the legacy ``name`` parameter name for API stability, but the
        implementation now accepts any server identifier (full UUID, short
        UUID prefix, or exact host) in addition to a name. This makes
        inventory lookups consistent with the rest of the tool surface and
        lets Path B single-shot probes reach the right SSH target.
        """
        from app.config.servers import resolve_server
        return resolve_server(name)

    def list_systems(self) -> Dict[str, Any]:
        from app.config.systems import get_all_systems
        return get_all_systems()

    def get_system(self, name: str) -> Optional[Dict[str, Any]]:
        from app.config.systems import get_system_by_name
        return get_system_by_name(name)

    def resolve_system_config(self, system_name: str, environment: str = None) -> Dict[str, Any]:
        from app.config.systems import resolve_system_config
        return resolve_system_config(system_name, environment=environment)

    def get_service(self, system_name: str, service_name: str, environment: str = "") -> Optional[Dict[str, Any]]:
        if not service_name:
            return None
        from app.db.base import SessionLocal
        from app.db.repository import ServiceRepository, SystemEnvironmentRepository

        with SessionLocal() as db:
            row = ServiceRepository(db).get_by_name(service_name, system_name)
            if row is None:
                return None
            payload = {
                "id": row.id,
                "name": row.name,
                "display_name": row.display_name or row.name,
                "system_name": row.system_name,
                "repo": row.repo or "",
                "build_cmd": row.build_cmd or "",
                "start_cmd": row.start_cmd or "",
                "template": row.template or "",
                "pipeline_id": row.pipeline_id or "",
                "servers": list(row.servers or []),
                "template_variables": dict(row.template_variables or {}),
                "source": "db",
            }
            if environment:
                env = SystemEnvironmentRepository(db).get_by_name(system_name, environment)
                override = (env.service_overrides or {}).get(row.name, {}) if env else {}
                if isinstance(override, dict):
                    # 环境级覆盖白名单（2026-09-04 收敛）：servers 已从覆盖面
                    # 移除——服务器分配统一走服务编辑页（servers / servers_by_env），
                    # 避免"环境覆盖 servers"与"服务×环境分配"双路径混淆。
                    for key in ("display_name", "repo", "build_cmd", "start_cmd", "template", "pipeline_id"):
                        if key in override:
                            payload[key] = override[key]
                    payload["template_variables"].update(override.get("template_variables") or {})
            return payload

    def list_groups(self, system_name: str, environment: str = None) -> Dict[str, Any]:
        from app.config.systems import get_all_groups
        return get_all_groups(system_name, environment)

    def get_group(self, system_name: str, group_code: str, environment: str = None) -> Optional[Dict[str, Any]]:
        from app.config.systems import get_group
        return get_group(system_name, group_code, environment)

    def get_variable_inheritance(self, system_name: str, service_name: str = None, environment: str = None) -> Dict[str, Any]:
        from app.config.systems import get_variable_inheritance
        return get_variable_inheritance(system_name, service_name, environment)

    def get_servers_for_system(self, system_name: str, environment: str = None) -> List[Dict[str, Any]]:
        from app.config.systems import get_servers_for_system
        return get_servers_for_system(system_name, environment)

    def list_pipelines(self, db, keyword: str = "", limit: int = 100):
        from app.db.repository import PipelineRepository

        items = [
            {
                "id": row.id,
                "name": row.name,
                "system_name": row.system_name,
                "description": row.description or "",
                "strategy": row.strategy or "DIRECT",
            }
            for row in PipelineRepository(db).list_all()
        ]
        if keyword:
            kw = keyword.lower()
            items = [x for x in items if kw in str(x.get("name", "")).lower() or kw in str(x.get("system", "")).lower()]
        return items[:limit]

    def get_pipeline(self, db, pipeline_id: str):
        from app.db.repository import PipelineRepository, PipelineStepRepository

        row = PipelineRepository(db).get_by_id(pipeline_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "system_name": row.system_name,
            "description": row.description or "",
            "strategy": row.strategy or "DIRECT",
            "steps": [
                {
                    "id": step.id,
                    "name": step.name,
                    "type": step.step_type,
                    "config": step.config or {},
                    "sort_order": step.sort_order,
                }
                for step in PipelineStepRepository(db).list_by_pipeline(row.id)
            ],
        }

    @staticmethod
    def _norm_name(value: Any) -> str:
        return str(value or "").strip().lower().replace("_", "-")

    @classmethod
    def _service_matches(cls, svc: Dict[str, Any], service_name: str) -> bool:
        target = cls._norm_name(service_name)
        if not target:
            return False
        candidates = [
            svc.get("name"),
            svc.get("display_name"),
            (svc.get("template_variables") or {}).get("service_name"),
            (svc.get("template_variables") or {}).get("pm2_name"),
        ]
        for candidate in candidates:
            cn = cls._norm_name(candidate)
            if cn and (cn == target or cn.endswith(f"-{target}") or target.endswith(f"-{cn}")):
                return True
        return False


inventory = InventoryReadService()

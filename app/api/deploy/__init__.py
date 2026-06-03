"""发布管理 API v2 - 子模块拆分"""

from app.api.deploy.plans import plans_router
from app.api.deploy.executions import exec_router
from app.api.deploy.history import history_router
from app.api.deploy.precheck import precheck_router

__all__ = ["plans_router", "exec_router", "history_router", "precheck_router"]
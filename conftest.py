"""pytest 全局隔离：让测试套件使用独立临时数据库，避免污染共享开发库 data/ops.db。

必须在导入任何 app 模块之前设置 DATABASE_URL，否则 app/db/base 的 engine 会指向
开发库。pytest 在收集测试模块之前先加载本 conftest，因此顶层设置环境变量即可生效。
"""
import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.gettempdir()) / "opsi_test_dbs"
_TEST_DB_DIR.mkdir(parents=True, exist_ok=True)
_TEST_DB_PATH = _TEST_DB_DIR / f"test_{os.getpid()}.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_db():
    """在独立临时数据库上建表并执行迁移，session 结束后清理临时文件。"""
    from app.db.base import init_db

    init_db()
    _seed_test_environments()
    yield
    for suffix in ("", "-wal", "-shm"):
        try:
            p = Path(str(_TEST_DB_PATH) + suffix)
            if p.exists():
                p.unlink()
        except OSError:
            pass


# ── 环境隔离闸（2026-09-04）的测试种子 ──
# validate_targets_in_environment 要求 targets ⊆ SystemEnvironment.servers，
# 全套审批/executor 测试直接调 ExecutionPlanService.prepare()，未种环境会被
# fail-closed 卡死。这里把测试用到的所有服务器名批量绑定到 test/prod/staging
# 三个环境类别，让既有测试专注于它们各自的面（审批链路/执行器/路由）。
_TEST_SERVER_POOL = [
    # 直调 prepare 的测试所用的全部 targets 形态（16 文件盘点 2026-09-04）
    "s1", "s2", "s3", "cc-test2", "q1", "server-1", "server1", "server2",
    "203.0.113.10",
] + [f"server{i}" for i in range(10)]


def _seed_test_environments():
    """为测试系统批量绑定环境服务器清单（幂等，session 级一次）。"""
    from app.db.base import SessionLocal
    from app.db.models import System, SystemEnvironment

    db = SessionLocal()
    try:
        systems = ["payment", "crypto-trader", "test-system", "quant",
                   "crypto", "dovo", "shop"]
        for name in systems:
            if not db.query(System).filter(System.name == name).first():
                db.add(System(name=name, display_name=name))
        db.flush()
        for sys_name in systems:
            for env_name, category in (("test", "test"), ("prod", "prod"), ("staging", "test")):
                row = db.query(SystemEnvironment).filter(
                    SystemEnvironment.system_name == sys_name,
                    SystemEnvironment.name == env_name,
                ).first()
                if row is None:
                    db.add(SystemEnvironment(
                        system_name=sys_name, name=env_name, category=category,
                        servers=[{"id": sid} for sid in _TEST_SERVER_POOL],
                    ))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
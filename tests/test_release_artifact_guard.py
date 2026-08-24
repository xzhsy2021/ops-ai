"""发版制品格式守卫测试。

设计原则：入库自由、发布受限，两者不冲突——
- Matrix 拉取等路径允许任意普通文件进入文件中心（allow_any_extension）；
- 但发版流程（execute_release / queue_deploy_v2）只接受限定格式的制品包。
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.migrations.runner import run_schema_migrations
from app.services.package_retention import ensure_releasable_artifact


@pytest.fixture(scope="module")
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_empty_package_name_passes(db):
    """未指定制品名（如纯操作型流程）沿用既有行为，不做校验。"""
    assert ensure_releasable_artifact(db, "") is None
    assert ensure_releasable_artifact(db, None) is None


def test_whitelisted_artifact_passes(db):
    for name in ("crypto-frontend.tar.gz", "api-v2.tgz", "app.zip", "server.bin"):
        assert ensure_releasable_artifact(db, name) is None


def test_plain_file_rejected_with_clear_reason(db):
    """普通文件不能作为发版制品：拒绝并给出可执行提示。"""
    for name in ("notes.txt", "report.pdf", "dump.log"):
        with pytest.raises(HTTPException) as exc:
            ensure_releasable_artifact(db, name)
        assert exc.value.status_code == 400
        detail = str(exc.value.detail)
        assert "部署包格式" in detail
        assert name in detail


def test_execute_release_blocks_plain_file(db):
    """RELEASE 执行体（执行计划/旧审批共用）拒绝非制品格式，且不创建部署记录。"""
    from app.db.models import Deployment
    from app.services.approval_executor import execute_release

    before = db.query(Deployment).count()
    with pytest.raises(HTTPException) as exc:
        execute_release(
            db,
            {"system_name": "s", "service_name": "svc", "environment": "test", "targets": []},
            operator="tester",
            package_name="notes.txt",
        )
    assert exc.value.status_code == 400
    assert db.query(Deployment).count() == before


def test_execute_release_accepts_whitelisted_artifact(db):
    """白名单制品正常创建部署记录。"""
    from app.db.models import Deployment
    from app.services.approval_executor import execute_release

    before = db.query(Deployment).count()
    result = execute_release(
        db,
        {"system_name": "guard-sys", "service_name": "svc", "environment": "test", "targets": []},
        operator="tester",
        package_name="artifact.tar.gz",
    )
    assert result["package"] == "artifact.tar.gz"
    assert db.query(Deployment).count() == before + 1

    # 清理，避免污染同模块其他用例
    row = db.query(Deployment).filter(Deployment.id == result["deployment_id"]).first()
    if row is not None:
        db.delete(row)
        db.commit()

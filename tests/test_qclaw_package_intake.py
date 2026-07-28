"""测试 qclaw 包接收服务。"""
import os
import hashlib
import tempfile

import pytest
from fastapi import HTTPException

from app.services.package_intake import (
    compute_sha256,
    intake_package,
    list_staging_packages,
    cleanup_staging,
)
from app.core.config import APPROVAL_STAGING_DIR
from app.db.base import Base, SessionLocal, engine
from app.db.migrations.runner import run_schema_migrations


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(engine)
    run_schema_migrations(engine)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def clean_staging():
    """每个测试前后清理 staging 目录。"""
    cleanup_staging()
    yield
    cleanup_staging()


def _write_staging_file(filename: str, content: bytes) -> str:
    """在 staging 目录写入测试文件。"""
    staging_dir = APPROVAL_STAGING_DIR
    os.makedirs(staging_dir, exist_ok=True)
    path = os.path.join(staging_dir, filename)
    with open(path, "wb") as f:
        f.write(content)
    return path


def test_compute_sha256():
    """测试 SHA-256 计算。"""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"hello world")
        f.flush()
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert compute_sha256(f.name) == expected
    os.unlink(f.name)


def test_intake_package_success(db):
    """成功接收包：验证 SHA-256、移动到上传目录、创建元数据。"""
    import secrets
    content = b"fake package content"
    sha = hashlib.sha256(content).hexdigest()
    fname = f"test-pkg-{secrets.token_hex(4)}.tar.gz"
    _write_staging_file(fname, content)

    result = intake_package(
        db=db,
        staging_filename=fname,
        expected_sha256=sha,
        expected_size=len(content),
        system_name="test-system",
        service_name="test-service",
    )

    assert result.package_name == fname
    assert result.sha256 == sha
    assert result.size_bytes == len(content)
    assert os.path.isfile(result.upload_path)

    # staging 文件已被移走
    staging_path = os.path.join(APPROVAL_STAGING_DIR, fname)
    assert not os.path.exists(staging_path)

    # 清理上传文件
    os.remove(result.upload_path)


def test_intake_package_sha256_mismatch(db):
    """SHA-256 不匹配时拒绝接收。"""
    _write_staging_file("bad-pkg.tar.gz", b"content")

    with pytest.raises(HTTPException) as exc:
        intake_package(
            db=db,
            staging_filename="bad-pkg.tar.gz",
            expected_sha256="0" * 64,
        )
    assert exc.value.status_code == 400
    assert "SHA-256" in exc.value.detail


def test_intake_package_size_mismatch(db):
    """文件大小不匹配时拒绝接收。"""
    _write_staging_file("size-pkg.tar.gz", b"content")

    with pytest.raises(HTTPException) as exc:
        intake_package(
            db=db,
            staging_filename="size-pkg.tar.gz",
            expected_size=999,
        )
    assert exc.value.status_code == 400
    assert "大小" in exc.value.detail


def test_intake_package_not_found(db):
    """staging 文件不存在时返回 404。"""
    with pytest.raises(HTTPException) as exc:
        intake_package(db=db, staging_filename="nonexistent.tar.gz")
    assert exc.value.status_code == 404


def test_list_staging_packages():
    """列出 staging 目录中的包。"""
    _write_staging_file("a.tar.gz", b"aaa")
    _write_staging_file("b.zip", b"bbb")

    items = list_staging_packages()
    names = {item["filename"] for item in items}
    assert "a.tar.gz" in names
    assert "b.zip" in names


def test_cleanup_staging():
    """清理 staging 目录。"""
    _write_staging_file("to-clean.tar.gz", b"content")
    assert len(list_staging_packages()) > 0

    count = cleanup_staging()
    assert count >= 1
    assert len(list_staging_packages()) == 0

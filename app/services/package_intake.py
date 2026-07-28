"""qclaw 包接收与安全路径管理。

qclaw 从 Element 房间接收到用户上传的部署包后，将包放入 staging 目录。
此模块负责：
1. 验证 staging 目录中包的 SHA-256 和大小
2. 将包安全移动到 OPS 上传目录
3. 创建 DeployPackage 元数据
4. 返回包标识供审批工单绑定
"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_runtime_path, QCLAW_STAGING_DIR
from app.db.models import DeployPackage
from app.services.package_retention import (
    _upload_dir,
    safe_package_name,
    upsert_package_metadata,
    get_package_retention_policy,
)


@dataclass
class PackageIntakeResult:
    """包接收结果"""

    package_id: str
    package_name: str
    sha256: str
    size_bytes: int
    upload_path: str


def _staging_dir() -> str:
    """获取 claw staging 目录（通用，兼容 qclaw 旧配置）。"""
    from app.core.config import APPROVAL_STAGING_DIR
    return APPROVAL_STAGING_DIR


def compute_sha256(file_path: str, chunk_size: int = 8192) -> str:
    """计算文件的 SHA-256。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def intake_package(
    db: Session,
    staging_filename: str,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    system_name: str = "",
    service_name: str = "",
) -> PackageIntakeResult:
    """从 staging 目录接收包，验证后移动到上传目录。

    Args:
        db: 数据库会话
        staging_filename: staging 目录中的文件名
        expected_sha256: 期望的 SHA-256（可选，用于验证完整性）
        expected_size: 期望的文件大小（可选）
        system_name: 关联的系统名
        service_name: 关联的服务名

    Returns:
        PackageIntakeResult 包含包 ID、名称、SHA-256、大小和路径

    Raises:
        HTTPException: 文件不存在、SHA-256 不匹配、大小不匹配或移动失败
    """
    staging_path = os.path.join(_staging_dir(), staging_filename)

    # 1. 验证文件存在
    if not os.path.isfile(staging_path):
        raise HTTPException(
            status_code=404,
            detail=f"Staging 文件不存在: {staging_filename}",
        )

    # 2. 验证文件大小
    actual_size = os.path.getsize(staging_path)
    if expected_size is not None and actual_size != expected_size:
        raise HTTPException(
            status_code=400,
            detail=f"文件大小不匹配: 期望 {expected_size}，实际 {actual_size}",
        )

    # 3. 计算并验证 SHA-256
    actual_sha256 = compute_sha256(staging_path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256.lower():
        raise HTTPException(
            status_code=400,
            detail=f"SHA-256 不匹配: 期望 {expected_sha256}，实际 {actual_sha256}",
        )

    # 4. 安全化文件名并移动到上传目录
    safe_name = safe_package_name(staging_filename)
    upload_dir = _upload_dir()
    os.makedirs(upload_dir, exist_ok=True)
    upload_path = os.path.join(upload_dir, safe_name)

    # 如果目标已存在同名文件，追加短随机前缀
    if os.path.exists(upload_path):
        base, ext = os.path.splitext(safe_name)
        import secrets
        safe_name = f"{base}_{secrets.token_hex(2)}{ext}"
        upload_path = os.path.join(upload_dir, safe_name)

    try:
        shutil.move(staging_path, upload_path)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"移动包文件失败: {e}",
        )

    # 5. 创建 DeployPackage 元数据
    policy = get_package_retention_policy(db)
    allowed_exts = policy.get("allowed_extensions", [])
    ext = os.path.splitext(safe_name)[1].lower()
    # .tar.gz 特殊处理
    if safe_name.lower().endswith(".tar.gz"):
        ext = ".tar.gz"
    if allowed_exts and ext not in allowed_exts:
        # 清理已移动的文件
        try:
            os.remove(upload_path)
        except Exception:
            pass
        raise HTTPException(
            status_code=400,
            detail=f"不允许的文件扩展名: {ext}，允许: {allowed_exts}",
        )

    pkg = upsert_package_metadata(
        db,
        name=safe_name,
        path=upload_path,
        system=system_name,
        service=service_name,
        sha256=actual_sha256,
    )

    return PackageIntakeResult(
        package_id=str(pkg.id),
        package_name=safe_name,
        sha256=actual_sha256,
        size_bytes=actual_size,
        upload_path=upload_path,
    )


def list_staging_packages() -> list[dict[str, Any]]:
    """列出 staging 目录中的包文件。"""
    staging = _staging_dir()
    if not os.path.isdir(staging):
        return []

    results = []
    for name in os.listdir(staging):
        path = os.path.join(staging, name)
        if not os.path.isfile(path):
            continue
        stat = os.stat(path)
        results.append({
            "filename": name,
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
        })
    return results


def cleanup_staging(filename: str | None = None) -> int:
    """清理 staging 目录，返回清理的文件数。"""
    staging = _staging_dir()
    if not os.path.isdir(staging):
        return 0

    count = 0
    if filename:
        path = os.path.join(staging, filename)
        if os.path.isfile(path):
            os.remove(path)
            count = 1
    else:
        for name in os.listdir(staging):
            path = os.path.join(staging, name)
            if os.path.isfile(path):
                os.remove(path)
                count += 1
    return count

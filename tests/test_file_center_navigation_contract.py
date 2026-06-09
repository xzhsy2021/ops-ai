from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import DeployPackage


def _sqlite_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'file_center_contract.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_file_center_exposes_manual_package_delete_contract():
    page = open("frontend/src/pages/FileCenterPage.tsx", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    backend = open("app/api/deploy_v2.py", encoding="utf-8").read()
    service = open("app/services/package_retention.py", encoding="utf-8").read()

    assert "deletePackage: (packageName: string)" in api
    assert "api.delete(`/files/packages/${encodeURIComponent(packageName)}`)" in api
    assert "deletePackageCandidate" in page
    assert "confirmDeletePackage" in page
    assert "确认删除文件包" in page
    assert "@resource_v2_router.delete(\"/files/packages/{package_name:path}\")" in backend
    assert "delete_package(db, name, actor=getattr(request.state, \"username\", \"\"))" in backend
    assert "def delete_package(" in service
    assert "manual_delete" in service


def test_sidebar_merges_status_and_diagnostics_nav_entries():
    routes = open("frontend/src/routes.ts", encoding="utf-8").read()

    assert "path: ROUTES.system, label: '状态诊断'" in routes
    assert "path: ROUTES.diagnostics, label: '诊断'" not in routes
    assert "ROUTES.diagnostics" in routes


def test_delete_package_removes_file_and_marks_metadata(tmp_path, monkeypatch):
    from app.services.package_retention import delete_package

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    package_file = upload_dir / "demo-20260609.tar.gz"
    package_file.write_bytes(b"package")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    _, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        db.add(DeployPackage(package_name=package_file.name, file_path=str(package_file), size_bytes=7))
        db.commit()

        result = delete_package(db, package_file.name, actor="tester")

        row = db.query(DeployPackage).filter(DeployPackage.package_name == package_file.name).first()
        assert result["deleted"] is True
        assert result["file_deleted"] is True
        assert not package_file.exists()
        assert row.deleted is True
        assert row.delete_reason == "manual_delete by tester"
    finally:
        db.close()


def test_delete_package_rejects_manually_protected_package(tmp_path, monkeypatch):
    from app.services.package_retention import delete_package

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    package_file = upload_dir / "protected-20260609.tar.gz"
    package_file.write_bytes(b"package")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    _, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        db.add(DeployPackage(package_name=package_file.name, file_path=str(package_file), size_bytes=7, protected=True))
        db.commit()

        with pytest.raises(HTTPException) as excinfo:
            delete_package(db, package_file.name, actor="tester")

        assert excinfo.value.status_code == 409
        assert package_file.exists()
    finally:
        db.close()

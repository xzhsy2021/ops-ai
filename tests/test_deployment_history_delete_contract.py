from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'deployment_history_delete.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_delete_deployment_history_removes_finished_deployment_and_runtime_rows(sqlite_session):
    from app.db.models import (
        Deployment,
        DeployLog,
        DeployTask,
        DeploymentPackageDistribution,
        DeploymentServerTask,
        DeploymentStepTask,
    )
    from app.deploy.history import delete_deployment_history

    db = sqlite_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    deployment = Deployment(id="dep_done", system="ops", service="api", environment="test", status="success", started_at=now, finished_at=now)
    db.add(deployment)
    db.add(DeployTask(id="task_done", deployment_id="dep_done", status="success"))
    db.add(DeployLog(id="log_done", task_id="task_done", deployment_id="dep_done", level="info", message="done"))
    db.add(DeployLog(id="log_task_only", task_id="task_done", deployment_id=None, level="info", message="task-only"))
    db.add(DeploymentServerTask(id="server_done", deployment_id="dep_done", task_id="task_done", server_name="srv-a", status="success"))
    db.add(DeploymentStepTask(id="step_done", deployment_id="dep_done", task_id="task_done", server_name="srv-a", step_name="deploy", status="success"))
    db.add(DeploymentPackageDistribution(id="dist_done", deployment_id="dep_done", task_id="task_done", server_name="srv-a", package_name="ops.tar.gz"))
    db.commit()

    result = delete_deployment_history(db, "dep_done", confirm_text="DELETE DEPLOYMENT dep_done", actor="alice")

    assert result["deployment_id"] == "dep_done"
    assert result["deleted"]["deployments"] == 1
    assert result["deleted"]["deploy_tasks"] == 1
    assert result["deleted"]["deploy_logs"] == 2
    assert result["deleted"]["deployment_server_tasks"] == 1
    assert result["deleted"]["deployment_step_tasks"] == 1
    assert result["deleted"]["deployment_package_distributions"] == 1
    assert db.query(Deployment).filter_by(id="dep_done").count() == 0
    assert db.query(DeployTask).filter_by(deployment_id="dep_done").count() == 0
    assert db.query(DeployLog).filter_by(deployment_id="dep_done").count() == 0
    assert db.query(DeployLog).filter_by(task_id="task_done").count() == 0


def test_delete_deployment_history_rejects_running_without_force(sqlite_session):
    from app.db.models import Deployment
    from app.deploy.history import delete_deployment_history

    db = sqlite_session
    db.add(Deployment(id="dep_running", system="ops", service="api", environment="test", status="running"))
    db.commit()

    with pytest.raises(HTTPException) as excinfo:
        delete_deployment_history(db, "dep_running", confirm_text="DELETE DEPLOYMENT dep_running", actor="alice")

    assert excinfo.value.status_code == 409
    assert db.query(Deployment).filter_by(id="dep_running").count() == 1


def test_delete_deployment_histories_batch_rejects_queued_without_force(sqlite_session):
    from app.db.models import Deployment
    from app.deploy.history import delete_deployment_histories

    db = sqlite_session
    db.add(Deployment(id="dep_queued", system="ops", service="api", environment="test", status="queued"))
    db.commit()

    with pytest.raises(HTTPException) as excinfo:
        delete_deployment_histories(db, ["dep_queued"], confirm_text="DELETE DEPLOYMENTS 1", actor="alice")

    assert excinfo.value.status_code == 409
    assert db.query(Deployment).filter_by(id="dep_queued").count() == 1


def test_delete_deployment_histories_batch_removes_multiple_finished_rows(sqlite_session):
    from app.db.models import (
        Deployment,
        DeployLog,
        DeployTask,
        DeploymentPackageDistribution,
        DeploymentServerTask,
        DeploymentStepTask,
    )
    from app.deploy.history import delete_deployment_histories

    db = sqlite_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for deployment_id in ["dep_a", "dep_b"]:
        task_id = f"task_{deployment_id}"
        db.add(Deployment(id=deployment_id, system="ops", service="api", environment="test", status="success", started_at=now, finished_at=now))
        db.add(DeployTask(id=task_id, deployment_id=deployment_id, status="success"))
        db.add(DeployLog(id=f"log_{deployment_id}", task_id=task_id, deployment_id=deployment_id, level="info", message="done"))
        db.add(DeploymentServerTask(id=f"server_{deployment_id}", deployment_id=deployment_id, task_id=task_id, server_name="srv-a", status="success"))
        db.add(DeploymentStepTask(id=f"step_{deployment_id}", deployment_id=deployment_id, task_id=task_id, server_name="srv-a", step_name="deploy", status="success"))
        db.add(DeploymentPackageDistribution(id=f"dist_{deployment_id}", deployment_id=deployment_id, task_id=task_id, server_name="srv-a", package_name="ops.tar.gz"))
    db.commit()

    result = delete_deployment_histories(
        db,
        ["dep_a", "dep_b"],
        confirm_text="DELETE DEPLOYMENTS 2",
        actor="alice",
    )

    assert result["deployment_ids"] == ["dep_a", "dep_b"]
    assert result["deleted"]["deployments"] == 2
    assert result["deleted"]["deploy_tasks"] == 2
    assert result["deleted"]["deploy_logs"] == 2
    assert result["deleted"]["deployment_server_tasks"] == 2
    assert result["deleted"]["deployment_step_tasks"] == 2
    assert result["deleted"]["deployment_package_distributions"] == 2
    assert db.query(Deployment).count() == 0
    assert db.query(DeployTask).count() == 0
    assert db.query(DeployLog).count() == 0


def test_deployment_history_frontend_exposes_manual_delete_action():
    page = open("frontend/src/pages/deploy/DeploymentHistoryTable.tsx", encoding="utf-8").read()
    api = open("frontend/src/api.ts", encoding="utf-8").read()
    deploy_page = open("frontend/src/pages/DeployPage.tsx", encoding="utf-8").read()

    assert "deleteHistory: (deploymentId: string, data: { confirm_text: string; force?: boolean })" in api
    assert "deleteHistoryBatch" in api
    assert "onDelete" in page
    assert "onDeleteSelected" in page
    assert "selectedIds" in page
    assert "删除" in page
    assert "确认删除部署历史" in deploy_page
    assert "DELETE DEPLOYMENT" in deploy_page
    assert "DELETE DEPLOYMENTS" in deploy_page

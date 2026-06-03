from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'snapshot_isolation.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_build_deployment_snapshot_exposes_captured_and_live_step_config(tmp_path):
    from app.db.models import Deployment, DeployTask, DeploymentServerTask, DeploymentStepTask, Pipeline, PipelineStep, Service
    from app.domain.runtime.snapshots import build_deployment_snapshot

    engine, Session = _session(tmp_path)
    db = Session()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        deployment = Deployment(
            id="dep1",
            system="ops",
            service="web",
            environment="prod",
            status="running",
            servers="s1",
            version="v1",
            created_by="alice",
            started_at=now,
        )
        pipeline = Pipeline(id="pipe1", name="web-prod", system_name="ops")
        step = PipelineStep(
            id="step1",
            pipeline_id="pipe1",
            name="reload",
            step_type="command",
            sort_order=1,
            config='{"command":"pm2 reload web","timeout":30}',
        )
        service = Service(id="svc1", name="web", system_name="ops", pipeline_id="pipe1")
        task = DeployTask(id="task1", deployment_id="dep1", status="running")
        server_task = DeploymentServerTask(
            id="srv1",
            deployment_id="dep1",
            task_id="task1",
            server_name="s1",
            status="running",
            created_at=now,
        )
        step_task = DeploymentStepTask(
            id="dst1",
            deployment_id="dep1",
            task_id="task1",
            server_task_id="srv1",
            server_name="s1",
            step_name="reload",
            step_type="command",
            status="running",
            captured_config='{"command":"pm2 reload web","timeout":15}',
            created_at=now,
        )
        db.add_all([deployment, pipeline, step, service, task, server_task, step_task])
        db.commit()

        snapshot = build_deployment_snapshot(db, "dep1", include_report=False)
        rows = snapshot["task_summary"]["step_tasks"]
        assert len(rows) == 1
        row = rows[0]
        assert row["captured_config"] == {"command": "pm2 reload web", "timeout": 15}
        assert row["live_config"] == {"command": "pm2 reload web", "timeout": 30}
        assert row["config_drift"] is True
    finally:
        db.close()
        engine.dispose()


def test_build_deployment_snapshot_gracefully_handles_missing_captured_config(tmp_path):
    from app.db.models import Deployment, DeployTask, DeploymentServerTask, DeploymentStepTask, Pipeline, PipelineStep, Service
    from app.domain.runtime.snapshots import build_deployment_snapshot

    engine, Session = _session(tmp_path)
    db = Session()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.add(
            Deployment(
                id="dep2",
                system="ops",
                service="web",
                environment="prod",
                status="success",
                servers="s1",
                version="v1",
                created_by="alice",
                started_at=now,
            )
        )
        db.add(Pipeline(id="pipe2", name="web-prod", system_name="ops"))
        db.add(PipelineStep(id="step2", pipeline_id="pipe2", name="reload", step_type="command", sort_order=1, config='{"timeout":30}'))
        db.add(Service(id="svc2", name="web", system_name="ops", pipeline_id="pipe2"))
        db.add(DeployTask(id="task2", deployment_id="dep2", status="success"))
        db.add(DeploymentServerTask(id="srv2", deployment_id="dep2", task_id="task2", server_name="s1", status="success", created_at=now))
        db.add(DeploymentStepTask(id="dst2", deployment_id="dep2", task_id="task2", server_task_id="srv2", server_name="s1", step_name="reload", step_type="command", status="success", captured_config=None, created_at=now))
        db.commit()

        snapshot = build_deployment_snapshot(db, "dep2", include_report=False)
        row = snapshot["task_summary"]["step_tasks"][0]
        assert row["captured_config"] is None
        assert row["live_config"] == {"timeout": 30}
        assert row["config_drift"] is False
    finally:
        db.close()
        engine.dispose()

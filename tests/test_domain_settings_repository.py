from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db import repository as repositories


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_singleton_domain_settings_round_trip():
    capability_cls = getattr(repositories, "CapabilitySettingsRepository", None)
    notification_cls = getattr(repositories, "NotificationSettingsRepository", None)
    deployment_cls = getattr(repositories, "DeploymentDefaultsRepository", None)
    assert capability_cls is not None
    assert notification_cls is not None
    assert deployment_cls is not None

    engine, db = _session()
    try:
        capability_cls(db).set({"enabled": False})
        notification_cls(db).set({"enabled": True, "timeout": 9})
        deployment_cls(db).set({"strategy": "DIRECT"})
        db.commit()

        assert capability_cls(db).get() == {"enabled": False}
        assert notification_cls(db).get() == {"enabled": True, "timeout": 9}
        assert deployment_cls(db).get() == {"strategy": "DIRECT"}
    finally:
        db.close()
        engine.dispose()


def test_retention_policies_are_isolated_by_domain():
    repository_cls = getattr(repositories, "RetentionPolicyRepository", None)
    assert repository_cls is not None

    engine, db = _session()
    try:
        repo = repository_cls(db)
        repo.set("release", {"keep_days": 90})
        repo.set("package", {"keep_days": 30})
        db.commit()

        assert repo.get("release") == {"keep_days": 90}
        assert repo.get("package") == {"keep_days": 30}
        assert repo.get("runtime") is None
    finally:
        db.close()
        engine.dispose()


def test_named_domain_settings_round_trip():
    inspection_cls = getattr(repositories, "InspectionProfileRepository", None)
    workflow_cls = getattr(repositories, "WorkflowTemplateRepository", None)
    variables_cls = getattr(repositories, "GlobalVariableRepository", None)
    assert inspection_cls is not None
    assert workflow_cls is not None
    assert variables_cls is not None

    engine, db = _session()
    try:
        inspection_cls(db).set("daily-lite", {"enabled": False})
        workflow_cls(db).set("custom-release", {"steps": [{"type": "wait"}]})
        variables_cls(db).set("region", "ap-east")
        variables_cls(db).set("replicas", 2)
        db.commit()

        assert inspection_cls(db).get("daily-lite") == {"enabled": False}
        assert workflow_cls(db).get("custom-release") == {"steps": [{"type": "wait"}]}
        assert variables_cls(db).get_all() == {"region": "ap-east", "replicas": 2}
    finally:
        db.close()
        engine.dispose()


def test_system_environments_are_scoped_by_system():
    repository_cls = getattr(repositories, "SystemEnvironmentRepository", None)
    assert repository_cls is not None

    engine, db = _session()
    try:
        repo = repository_cls(db)
        first = repo.create(
            system_name="crypto-trader",
            name="test",
            display_name="Test",
            variables={"TRACE": "true"},
            servers=["test-1"],
        )
        repo.create(system_name="bot-hub", name="test", display_name="Bot Test")
        db.commit()

        assert repo.get_by_name("crypto-trader", "test").id == first.id
        assert [row.name for row in repo.list_by_system("crypto-trader")] == ["test"]
        assert repo.get_by_name("crypto-trader", "test").variables == {"TRACE": "true"}
        assert repo.get_by_name("bot-hub", "test").display_name == "Bot Test"

        updated = repo.upsert(
            system_name="crypto-trader",
            name="test",
            display_name="Test Updated",
            variables={"TRACE": "false"},
            servers=["test-2"],
        )
        assert updated.id == first.id
        assert updated.display_name == "Test Updated"
        assert updated.variables == {"TRACE": "false"}
        assert repo.delete(updated.id) is True
        db.commit()
        assert repo.get_by_name("crypto-trader", "test") is None
    finally:
        db.close()
        engine.dispose()


def test_domain_defaults_are_persisted_for_fresh_database():
    try:
        from app.db.bootstrap import ensure_domain_defaults
    except ModuleNotFoundError:
        ensure_domain_defaults = None
    assert ensure_domain_defaults is not None

    engine, db = _session()
    try:
        ensure_domain_defaults(db)

        assert repositories.CapabilitySettingsRepository(db).get()["enabled"] is True
        assert repositories.RetentionPolicyRepository(db).get("release") is not None
        assert repositories.RetentionPolicyRepository(db).get("package") is not None
        assert repositories.RetentionPolicyRepository(db).get("runtime") is not None
        assert repositories.NotificationSettingsRepository(db).get()["enabled"] is False
    finally:
        db.close()
        engine.dispose()

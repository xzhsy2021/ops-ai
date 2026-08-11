import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deploy_v2 import list_systems_v2
from app.config.servers import get_server_references
from app.config.systems import delete_system, get_all_systems, save_system
from app.db.base import Base
from app.db.models import Service, System, SystemEnvironment


def test_list_systems_reads_database_without_config_kv():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(
            System(
                name="crypto-trader",
                display_name="Crypto Trader",
                strategy="WORKFLOW",
            )
        )
        session.add_all(
            [
                Service(name="system", system_name="crypto-trader"),
                Service(name="risk", system_name="crypto-trader"),
            ]
        )
        session.add(
            SystemEnvironment(
                system_name="crypto-trader",
                name="test",
                display_name="Test",
            )
        )
        session.commit()

        response = asyncio.run(list_systems_v2(request=None, db=session))

        assert response["data"] == [
            {
                "name": "crypto-trader",
                "display_name": "Crypto Trader",
                "strategy": "WORKFLOW",
                "environment_count": 1,
                "service_count": 2,
            }
        ]
    finally:
        session.close()
        engine.dispose()


def test_system_config_composes_services_and_environments_from_domain_tables(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    session = local_session()
    try:
        session.add(System(
            name="crypto-trader",
            display_name="Crypto Trader",
            services=[],
            environments={},
            message_routing={"enabled": True, "keywords": ["quant"]},
        ))
        session.add(Service(
            name="risk",
            display_name="Risk",
            system_name="crypto-trader",
            template="generic_backend",
        ))
        session.add(SystemEnvironment(
            system_name="crypto-trader",
            name="test",
            display_name="Test",
            variables={"TRACE": "true"},
        ))
        session.commit()
        monkeypatch.setattr("app.db.base.SessionLocal", local_session)

        systems = get_all_systems()

        assert [item["name"] for item in systems["crypto-trader"]["services"]] == ["risk"]
        assert systems["crypto-trader"]["environments"]["test"]["variables"] == {"TRACE": "true"}
        assert systems["crypto-trader"]["message_routing"] == {
            "enabled": True,
            "keywords": ["quant"],
        }
    finally:
        session.close()
        engine.dispose()


def test_save_system_writes_child_resources_and_message_routing_to_domain_tables(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.base.SessionLocal", local_session)
    try:
        assert save_system("crypto-trader", {
            "display_name": "Crypto Trader",
            "message_routing": {"enabled": True, "keywords": ["quant"]},
            "services": [{
                "name": "risk",
                "display_name": "Risk",
                "template": "generic_backend",
                "template_variables": {"service_name": "risk"},
            }],
            "environments": {
                "test": {
                    "display_name": "Test",
                    "category": "test",
                    "variables": {"TRACE": "true"},
                }
            },
        }) is True

        with local_session() as db:
            system = db.query(System).filter(System.name == "crypto-trader").one()
            assert system.message_routing == {"enabled": True, "keywords": ["quant"]}
            assert system.services == []
            assert system.environments == {}
            assert db.query(Service).filter_by(system_name="crypto-trader", name="risk").count() == 1
            assert db.query(SystemEnvironment).filter_by(system_name="crypto-trader", name="test").count() == 1
    finally:
        engine.dispose()


def test_delete_system_removes_services_and_environments(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.base.SessionLocal", local_session)
    with local_session() as db:
        db.add(System(name="demo"))
        db.add(Service(name="api", system_name="demo"))
        db.add(SystemEnvironment(system_name="demo", name="test"))
        db.commit()

    try:
        assert delete_system("demo") is True
        with local_session() as db:
            assert db.query(System).filter_by(name="demo").count() == 0
            assert db.query(Service).filter_by(system_name="demo").count() == 0
            assert db.query(SystemEnvironment).filter_by(system_name="demo").count() == 0
    finally:
        engine.dispose()


def test_server_references_include_system_and_environment_domain_rows():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)
    with local_session() as db:
        db.add(System(name="demo", servers=["demo-1"]))
        db.add(SystemEnvironment(
            system_name="demo",
            name="test",
            servers=["demo-1"],
        ))
        db.commit()

        references = get_server_references("demo-1", db=db)

        assert {item["type"] for item in references} == {
            "system",
            "system_environment",
        }
    engine.dispose()

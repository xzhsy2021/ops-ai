"""ops.list_environments 必须回传目标服务器清单。

背景（2026-09-11 审计）：发布计划 parameters.targets 的唯一权威来源是
SystemEnvironment.servers（系统级）与 services.template_variables.servers_by_env
（服务级，例如 system 只在主节点），但 ops.list_environments 拿到了行却把 servers
丢掉了，也没有任何工具暴露服务级映射。后果：AI 客户端做生产发版时无法判断
"该发哪几台"，只能按服务器名里的环境字样猜。

本文件锁定修复后的行为：
1. 系统级 env 回传 servers（名称列表）；
2. 回传 service_server_map（服务 → 环境 → 服务器）；
3. service 参数可只看单个服务。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Service, SystemEnvironment
from app.services.tool_adapters.server_tools import list_environments_tool
from tests.test_multichannel_routing_tools import _identity  # noqa: F401  (保持与其它测试同风格)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(SystemEnvironment(
        system_name="crypto-trader", name="prod", category="custom",
        servers=[{"id": "43.106.12.129-量化-主节点"}, {"id": "43.106.14.247-量化-2节点"}],
    ))
    session.add(SystemEnvironment(
        system_name="crypto-trader", name="test", category="test",
        servers=[{"id": "47.84.58.154-量化测试"}],
    ))
    session.add(Service(
        system_name="crypto-trader", name="system", template="generic_backend_direct",
        template_variables={"compose_dir": "/data/crypto-trader",
                            "servers_by_env": {"prod": ["43.106.12.129-量化-主节点"],
                                               "test": ["47.84.58.154-量化测试"]}},
    ))
    session.add(Service(
        system_name="crypto-trader", name="transaction", template="generic_backend_direct",
        template_variables={"compose_dir": "/data/crypto-trader",
                            "servers_by_env": {"prod": ["43.106.12.129-量化-主节点",
                                                        "43.106.14.247-量化-2节点"]}},
    ))
    session.commit()
    yield session
    session.close()


def test_env_items_carry_target_servers(db):
    out = list_environments_tool({"system": "crypto-trader"}, None, db)
    by_name = {i["name"]: i for i in out["items"] if i.get("system_name") == "crypto-trader"}
    assert by_name["prod"]["servers"] == ["43.106.12.129-量化-主节点", "43.106.14.247-量化-2节点"]
    assert by_name["test"]["servers"] == ["47.84.58.154-量化测试"]
    assert by_name["prod"]["category"] == "custom"


def test_service_server_map_exposes_per_env_targets(db):
    out = list_environments_tool({"system": "crypto-trader"}, None, db)
    mapping = out["service_server_map"]
    # system 只在主节点，transaction 两台都要发——这正是"生产该发哪几台"的答案
    assert mapping["system"]["prod"] == ["43.106.12.129-量化-主节点"]
    assert mapping["transaction"]["prod"] == ["43.106.12.129-量化-主节点", "43.106.14.247-量化-2节点"]


def test_service_filter_narrows_mapping(db):
    out = list_environments_tool({"system": "crypto-trader", "service": "transaction"}, None, db)
    assert set(out["service_server_map"]) == {"transaction"}

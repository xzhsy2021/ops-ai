from __future__ import annotations

import inspect
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _sqlite_session(tmp_path):
    from app.db.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp_contract_sync.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return engine, Session


def test_inspection_execute_tools_accept_confirm_text_in_schema():
    from app.services.tool_registry import register_builtin_tools, registry
    from app.services.tool_schema import validate_schema

    register_builtin_tools()

    samples = {
        "ops.inspection.run_server": {
            "server_id": "server-a",
            "confirm_text": "CONFIRM ops.inspection.run_server",
        },
        "ops.inspection.run_servers_batch": {
            "groups": ["crypto"],
            "confirm_text": "CONFIRM ops.inspection.run_servers_batch",
        },
    }

    for tool_name, arguments in samples.items():
        tool = registry.get(tool_name)
        props = (tool.input_schema or {}).get("properties") or {}
        assert "confirm_text" in props, tool_name
        validate_schema(arguments, tool.input_schema)


def test_agent_tools_are_hidden_when_agent_runtime_is_disabled(tmp_path):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    try:
        register_builtin_tools()
        ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)

        listed = registry.list_tools(db, ctx, include_disabled=False, include_schema=False, limit=1000)
        names = {tool["name"] for tool in listed["tools"]}

        assert not any(name.startswith("ops.agent.") for name in names)
    finally:
        db.close()
        engine.dispose()


def test_large_payload_tools_are_streamable_and_callback_compatible():
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()

    for tool_name in {
        "ops.db.export_query_result",
        "ops.get_deployment_logs",
        "ops.export_diagnostics_report",
    }:
        tool = registry.get(tool_name)
        assert tool.streamable is True, tool_name
        signature = inspect.signature(tool.handler)
        assert (
            "stream_callback" in signature.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
        ), tool_name


def test_inspection_path_a_run_tools_return_followup_metadata(monkeypatch):
    from app.services.tool_adapters import inspection_tools

    def _single_detail(db, *, server_id, categories, created_by):
        return {"run": {"id": "run-server-1", "status": "COMPLETED"}, "summary": "server done"}

    def _batch_detail(db, **kwargs):
        return {
            "runs": [{"id": "run-a", "status": "COMPLETED"}, {"id": "run-b", "status": "FAILED"}],
            "success": 1,
            "failed": 1,
            "summary": "batch done",
        }

    monkeypatch.setattr("app.services.inspection_center.run_server_inspection", _single_detail)
    monkeypatch.setattr("app.services.inspection_center.run_servers_batch_inspection", _batch_detail)

    ctx = SimpleNamespace(username="tester", token_owner="")
    single = inspection_tools.run_server({"server_id": "server-a"}, ctx, object())
    batch_preview = inspection_tools.preview_servers_batch({"server_ids": ["server-a", "server-b"]}, ctx, object())
    batch = inspection_tools.run_servers_batch({
        "server_ids": ["server-a", "server-b"],
        "confirm_text": batch_preview["confirmation"]["confirm_text"],
    }, ctx, object())

    assert single["run_id"] == "run-server-1"
    assert single["status"] == "COMPLETED"
    # 第 13 轮：followup 里此前第一条建议是 ops.inspection.get_run（从未注册的幽灵名）。
    # 现在两条建议都必须指向真实注册工具。
    assert single["next_actions"][0]["tool"] == "ops.inspection.get_run_raw_output"
    assert single["next_actions"][1]["tool"] == "ops.inspection.generate_report"

    assert batch["run_ids"] == ["run-a", "run-b"]
    assert batch["preview"]["confirmation"]["confirm_text"].startswith("确认巡检 ")
    assert batch["status"] == "PARTIAL"
    assert batch["next_actions"][0]["arguments"] == {"run_id": "run-a"}


def test_inspection_resolver_accepts_inventory_uuid_alias(monkeypatch):
    from app.services.inspection_center import resolve_servers_for_inspection

    server_uuid = "e7e021ac170542a3a361ee1d1d55ea0a"

    def _list_servers(self):
        return [
            {
                "id": server_uuid,
                "name": "crypto-test-1",
                "host": "43.106.4.251",
                "group": "crypto",
                "status": "online",
            }
        ]

    monkeypatch.setattr("app.domain.inventory.services.InventoryReadService.list_servers", _list_servers)

    resolved = resolve_servers_for_inspection([server_uuid])

    assert resolved["skipped"] == []
    assert resolved["eligible_ids"] == ["crypto-test-1"]
    assert resolved["eligible"][0]["asset_id"] == server_uuid


def test_mcp_runbook_only_lists_registered_tool_names():
    """能力矩阵只允许列出真实注册的工具名。

    第 13 轮：此前这条测试断言 `ops.inspection.toggle_item_config` **必须**出现在文档里，
    而该工具从来没有注册定义 —— 于是文档里约 30 个幽灵工具名被测试锁住。现在改为
    按注册表动态校验：文档里出现的具体工具名必须真实存在。
    """
    from app.services.tool_registry import register_builtin_tools, registry

    register_builtin_tools()
    known = set(registry._tools)

    text = open("docs/runbooks/mcp-capability-matrix.md", encoding="utf-8").read()

    # 文档必须仍然覆盖当前工具面（防止清理时误删有效内容）。
    assert "ops.inspection.get_run_raw_output" in text
    assert "ops.inspection.run_servers_batch" in text
    assert "ops.inspection.profile.retry_issues" in text
    assert "AI agents must keep using" in text
    assert "confirmation.confirm_text" in text
    assert "UI one-click confirmation" in text

    # 允许出现但不代表"注册工具"的名字：
    #  - 通配写法（`ops.inspection.run_*`）在正则里只会匹配到前缀部分；
    #  - 审批提示名是 tool_policy 的映射键，文档已明确说明它们不是注册入口。
    allowed_non_tool = {
        "ops.inspection.run_",
        "ops.inspection.profile",
        "ops.approval.prepare_",
        "ops.approval.prepare_release",
        "ops.approval.prepare_rollback",
        "ops.approval.prepare_dml",
        "ops.approval.prepare_package_cleanup",
        "ops.tier",
        "ops.notif_route",
        "ops.cascade",
        "ops.agent",
    }
    ghosts = sorted(
        {
            match.group(0)
            for match in re.finditer(r"\bops(?:\.[a-z0-9_]+)+", text)
            if match.group(0) not in known
            and match.group(0) not in allowed_non_tool
            and not match.group(0).endswith((".py", ".md", ".json"))
        }
    )
    assert ghosts == [], f"能力矩阵引用了未注册的工具名：{ghosts}"


def test_runtime_source_of_truth_matches_current_system_service_routes():
    text = open("docs/plans/2026-05-01-runtime-source-of-truth.md", encoding="utf-8").read()

    assert "/systems" in text
    assert "SystemListPage" in text
    assert "ApplicationListPage" not in text
    assert "apps_v2_router" not in text


def test_mcp_capability_service_owns_alias_payload_and_call_contract(tmp_path):
    from app.services.mcp_capability_service import (
        from_mcp_tool_name,
        mcp_tool_payload,
        mcp_tools_list,
        mcp_call_tool,
    )
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()

    @registry.register(
        name="ops.contract.service_call",
        description="contract service call",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        scopes=["ops:read"],
    )
    def _service_call(args, ctx, db, stream_callback=None):
        return {"summary": "service ok", "value": 3}

    ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
    try:
        assert from_mcp_tool_name("ops_contract_service_call") == "ops.contract.service_call"

        payload = mcp_tool_payload({"name": "ops.contract.service_call", "description": "服务调用", "inputSchema": {"type": "object"}})
        assert payload["name"] == "ops_contract_service_call"
        assert payload["annotations"]["ops.originalToolName"] == "ops.contract.service_call"

        listed = mcp_tools_list(db, ctx, {"limit": 1000, "profile": "admin_full"})
        names = {tool["name"] for tool in listed["tools"]}
        assert "ops_contract_service_call" in names

        response = mcp_call_tool(db, ctx, {"name": "ops_contract_service_call", "arguments": {}})
        data = json.loads(response["content"][0]["text"])
        assert data["tool"] == "ops.contract.service_call"
        assert data["result"]["value"] == 3
    finally:
        registry._tools.pop("ops.contract.service_call", None)
        db.close()
        engine.dispose()


def test_mcp_default_profile_is_slim_but_admin_full_keeps_all_tools(tmp_path):
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools, registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
    try:
        register_builtin_tools()

        daily = registry.list_tools(
            db,
            ctx,
            include_disabled=True,
            include_schema=False,
            limit=500,
            profile="daily_ops",
        )
        admin = registry.list_tools(
            db,
            ctx,
            include_disabled=True,
            include_schema=False,
            limit=500,
            profile="admin_full",
        )
        daily_names = {tool["name"] for tool in daily["tools"]}
        admin_names = {tool["name"] for tool in admin["tools"]}

        assert daily["filters"]["profile"] == "daily_ops"
        assert admin["filters"]["profile"] == "admin_full"
        assert len(admin_names) >= 95
        assert 8 <= len(daily_names) <= 80
        assert daily_names < admin_names
        assert "ops.inspection.run_servers_batch" in daily_names
        assert "ops.execute_deploy_plan" not in daily_names
        assert "ops.exec_remote" not in daily_names
        assert "ops.execute_deploy_plan" in admin_names
        assert "ops.exec_remote" in admin_names
    finally:
        db.close()
        engine.dispose()


def test_mcp_tools_list_defaults_to_ai_full_and_can_contract_to_daily_ops(tmp_path):
    from app.services.mcp_capability_service import mcp_tools_list
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
    try:
        register_builtin_tools()

        full = mcp_tools_list(db, ctx, {"limit": 200})
        contracted = mcp_tools_list(db, ctx, {"limit": 200, "profile": "daily_ops"})

        full_names = {
            (tool.get("annotations") or {}).get("ops.originalToolName") or tool.get("name")
            for tool in full["tools"]
        }
        contracted_names = {
            (tool.get("annotations") or {}).get("ops.originalToolName") or tool.get("name")
            for tool in contracted["tools"]
        }

        # Default is the full ai_full exposure so OpenClaw/HTTP MCP discover the
        # whole capability surface; daily_ops is an explicit contract-down subset.
        assert "ops.inspection.run_servers_batch" in full_names
        assert "ops.execute_deploy_plan" in full_names
        assert "ops.exec_remote" in full_names
        assert len(full_names) > len(contracted_names)
        assert "ops.execute_deploy_plan" not in contracted_names
        assert "ops.exec_remote" not in contracted_names
    finally:
        db.close()
        engine.dispose()


def test_remote_mcp_tools_list_exposes_inspection_batch_semantics(tmp_path):
    from app.services.mcp_capability_service import mcp_tools_list
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import register_builtin_tools

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
    try:
        register_builtin_tools()

        listed = mcp_tools_list(db, ctx, {"limit": 200})
        tools = {tool["name"]: tool for tool in listed["tools"]}

        batch = tools["ops_inspection_run_servers_batch"]
        batch_description = batch["description"].lower()
        batch_schema = batch["inputSchema"]
        batch_props = batch_schema["properties"]
        assert "batch" in batch_description
        assert "confirm_text" in batch_description
        assert "ops.inspection.run_servers_batch" == batch["annotations"]["ops.originalToolName"]
        assert {"groups", "confirm_text"} <= set(batch_props)

        merged_report = tools["ops_inspection_generate_report_for_runs"]
        assert "multiple run ids" in merged_report["description"].lower()
        assert merged_report["inputSchema"]["required"] == ["run_ids"]
    finally:
        db.close()
        engine.dispose()


def test_mcp_capability_service_owns_resource_catalog_and_read_wrapper(tmp_path):
    from app.services.mcp_capability_service import mcp_resource_items, mcp_resources_list, mcp_resource_read
    from app.services.tool_context import ToolContext

    engine, Session = _sqlite_session(tmp_path)
    db = Session()
    ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
    try:
        resources = mcp_resource_items()
        uris = {item["uri"] for item in resources}
        assert "ops://capabilities" in uris
        assert "ops://tools" in uris

        listed = mcp_resources_list()
        assert listed["resources"][0]["uri"].startswith("ops://")

        response = mcp_resource_read(db, ctx, {"uri": "ops://tools"})
        content = response["contents"][0]
        assert content["uri"] == "ops://tools"
        assert content["mimeType"] == "application/json"
        payload = json.loads(content["text"])
        assert payload["source"] == "ops-platform"
        assert payload["uri"] == "ops://tools"
    finally:
        db.close()
        engine.dispose()


def test_mcp_capability_service_owns_prompt_catalog_and_get_contract():
    from app.services.mcp_capability_service import mcp_prompt_get, mcp_prompt_items, mcp_prompts_list

    prompts = mcp_prompt_items()
    names = {item["name"] for item in prompts}
    assert "ops_release_plan" in names
    assert "ops_db_export_request" in names
    assert "ops_inspection_workflow" in names

    listed = mcp_prompts_list()
    assert listed["prompts"] == prompts

    prompt = mcp_prompt_get({"name": "ops_db_export_request", "arguments": {"request": "export users as csv"}})
    text = prompt["messages"][0]["content"]["text"]
    assert prompt["description"] == "ops_db_export_request"
    assert "NEVER write standalone Python scripts" in text
    assert "export users as csv" in text

    inspection_prompt = mcp_prompt_get({
        "name": "ops_inspection_workflow",
        "arguments": {"request": "巡检全部服务器，按分组分批巡检并输出报告"},
    })
    inspection_text = inspection_prompt["messages"][0]["content"]["text"]
    assert "ops.inspection.run_servers_batch" in inspection_text
    assert "grouped" in inspection_text
    assert "ops.inspection.generate_report_for_runs" in inspection_text
    assert "确认巡检 <fingerprint>" in inspection_text


def test_fastapi_mcp_endpoint_uses_capability_service_not_stdio_private_helpers():
    api_text = open("app/api/tools.py", encoding="utf-8").read()
    service_text = open("app/services/mcp_capability_service.py", encoding="utf-8").read()

    assert "from app.mcp.server import" not in api_text
    assert "mcp_call_tool" in api_text
    assert "mcp_tools_list" in api_text
    assert "service_mcp_resource_read" in api_text
    assert "service_mcp_prompt_get" in api_text
    assert "service_mcp_prompts_list" in api_text
    assert "def mcp_call_tool" in service_text
    assert "def mcp_resource_read" in service_text
    assert "def mcp_prompt_get" in service_text


def test_mcp_inspection_recommendation_includes_merged_report_tool():
    text = open("app/api/tools.py", encoding="utf-8").read()
    body = text.split('"inspection": [', 1)[1].split("],", 1)[0]

    assert '"ops.inspection.run_servers_batch"' in body
    assert '"ops.list_server_groups"' in body
    assert '"ops.inspection.generate_report_for_runs"' in body


def test_fastapi_mcp_resource_read_has_no_unreachable_legacy_branches():
    api_text = open("app/api/tools.py", encoding="utf-8").read()
    body = api_text.split("def _mcp_http_resource_read", 1)[1].split("def _mcp_http_prompts_list", 1)[0]

    assert "return service_mcp_resource_read(db, ctx, params)" in body
    assert "register_builtin_tools()" not in body
    assert "ops://ai-workflows" not in body


def test_mcp_jsonrpc_tools_call_accepts_alias_and_streams_payload(tmp_path):
    from app.api import tools as tools_api
    from app.services.tool_context import ToolContext
    from app.services.tool_registry import registry

    engine, Session = _sqlite_session(tmp_path)
    db = Session()

    @registry.register(
        name="ops.contract.stream_test",
        description="contract stream test",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        scopes=["ops:read"],
        streamable=True,
    )
    def _stream_test(args, ctx, db, stream_callback=None):
        if stream_callback:
            stream_callback({"event": "contract_chunk", "data": {"value": 1}})
        return {"summary": "stream ok", "value": 2}

    request = SimpleNamespace(
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    ctx = ToolContext(username="tester", auth_type="session", is_admin=True, scopes=["*"], allow_write=True)
    try:
        with patch.object(tools_api, "get_tool_context", return_value=ctx):
            call_response = tools_api._handle_mcp_http_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "ops_describe_capabilities", "arguments": {"include_schema": False, "limit": 1}},
                },
                request,
                db,
            )
            stream_response = tools_api._handle_mcp_http_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call.stream",
                    "params": {"name": "ops_contract_stream_test", "arguments": {}},
                },
                request,
                db,
            )

        assert "result" in call_response
        call_text = call_response["result"]["content"][0]["text"]
        assert json.loads(call_text)["tool"] == "ops.describe_capabilities"

        assert "result" in stream_response
        stream_text = stream_response["result"]["content"][0]["text"]
        stream_payload = json.loads(stream_text)
        assert stream_payload["stream"] == [{"event": "contract_chunk", "data": {"value": 1}}]
        assert stream_payload["result"]["tool"] == "ops.contract.stream_test"
        assert stream_payload["result"]["result"]["value"] == 2
    finally:
        registry._tools.pop("ops.contract.stream_test", None)
        db.close()
        engine.dispose()

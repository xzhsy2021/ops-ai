from __future__ import annotations

import inspect
import json
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
        "ops.inspection.run_project": {
            "project_id": "project-a",
            "confirm_text": "CONFIRM ops.inspection.run_project",
        },
        "ops.inspection.run_combined": {
            "project_id": "project-a",
            "confirm_text": "CONFIRM ops.inspection.run_combined",
        },
        "ops.inspection.delete_runs": {
            "run_ids": ["run-a"],
            "confirm_text": "CONFIRM ops.inspection.delete_runs",
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
    batch = inspection_tools.run_servers_batch({"server_ids": ["server-a", "server-b"]}, ctx, object())

    assert single["run_id"] == "run-server-1"
    assert single["status"] == "COMPLETED"
    assert single["next_actions"][0]["tool"] == "ops.inspection.get_run"
    assert single["next_actions"][1]["tool"] == "ops.inspection.get_run_raw_output"

    assert batch["run_ids"] == ["run-a", "run-b"]
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


def test_mcp_runbook_uses_current_tool_names_and_covers_tier_tools():
    text = open("docs/runbooks/mcp-capability-matrix.md", encoding="utf-8").read()

    assert "ops.inspection.get_run_raw_output" in text
    assert "ops.inspection.toggle_item_config" in text
    assert "ops.inspection.reorder_items" not in text
    assert "ops.inspection.get_raw_output" not in text
    assert "ops.tier.run_now" in text
    assert "ops.notif_route.upsert" in text
    assert "ops.cascade.upsert" in text


def test_runtime_source_of_truth_matches_current_system_service_routes():
    text = open("docs/plans/2026-05-01-runtime-source-of-truth.md", encoding="utf-8").read()

    assert "/systems" in text
    assert "SystemListPage" in text
    assert "ApplicationListPage" not in text
    assert "apps_v2_router" not in text


def test_mcp_aliases_round_trip_without_prior_tools_list_cache():
    from app.mcp.server import _from_mcp_tool_name

    assert _from_mcp_tool_name("ops_describe_capabilities") == "ops.describe_capabilities"
    assert _from_mcp_tool_name("ops_inspection_run_servers_batch") == "ops.inspection.run_servers_batch"
    assert _from_mcp_tool_name("ops_db_export_query_result") == "ops.db.export_query_result"


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

        listed = mcp_tools_list(db, ctx, {"limit": 1000})
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

    listed = mcp_prompts_list()
    assert listed["prompts"] == prompts

    prompt = mcp_prompt_get({"name": "ops_db_export_request", "arguments": {"request": "export users as csv"}})
    text = prompt["messages"][0]["content"]["text"]
    assert prompt["description"] == "ops_db_export_request"
    assert "NEVER write standalone Python scripts" in text
    assert "export users as csv" in text


def test_stdio_mcp_static_resources_and_prompts_use_capability_service_without_http(monkeypatch):
    from app.mcp import server as stdio_server
    from app.services.mcp_capability_service import mcp_prompt_get, mcp_prompts_list, mcp_resources_list

    def _deny_http_request(*args, **kwargs):
        raise AssertionError("stdio static MCP catalogs must not call HTTP")

    monkeypatch.setattr(stdio_server, "_request", _deny_http_request)

    prompt_params = {"name": "ops_db_export_request", "arguments": {"request": "export users as csv"}}
    assert stdio_server._resources_for_mcp() == mcp_resources_list()
    assert stdio_server._prompts_for_mcp() == mcp_prompts_list()
    assert stdio_server._get_prompt_for_mcp(prompt_params) == mcp_prompt_get(prompt_params)


def test_stdio_mcp_static_catalog_source_does_not_depend_on_http_routes():
    server_text = open("app/mcp/server.py", encoding="utf-8").read()
    resources_body = server_text.split("def _resources_for_mcp", 1)[1].split("def _offline_resource_text", 1)[0]
    prompts_body = server_text.split("def _prompts_for_mcp", 1)[1].split("def _get_prompt_for_mcp", 1)[0]
    prompt_get_body = server_text.split("def _get_prompt_for_mcp", 1)[1].split("def handle", 1)[0]

    assert "mcp_resources_list()" in resources_body
    assert "mcp_prompts_list()" in prompts_body
    assert "mcp_prompt_get(params)" in prompt_get_body
    assert '"/api/v2/mcp/resources"' not in resources_body
    assert '"/api/v2/mcp/prompts"' not in prompts_body
    assert '"/api/v2/mcp/prompts/get"' not in prompt_get_body


def test_stdio_mcp_descriptions_are_ascii_safe_for_default_clients():
    from app.mcp import server as stdio_server

    for tool_name, description in stdio_server.ENGLISH_TOOL_DESCRIPTIONS.items():
        cleaned = stdio_server._ascii_only(description, fallback=tool_name)
        assert cleaned
        assert cleaned.encode("ascii", errors="ignore").decode("ascii") == cleaned


def test_stdio_diagnostic_tool_has_clean_utf8_when_ascii_descriptions_disabled(monkeypatch):
    from app.mcp import server as stdio_server

    monkeypatch.setattr(stdio_server, "ASCII_DESCRIPTIONS", False)

    tool = stdio_server._diagnostic_tool("connection failed")
    text = json.dumps(tool, ensure_ascii=False)
    assert "检查" in text
    assert "连接状态" in text
    assert "connection failed" in text
    assert not any(marker in text for marker in ("鈹", "鑳", "鏌", "鎺", "鍙", "绋", "璇", "€", "乷"))


def test_stdio_mcp_offline_tools_list_exposes_fallback_state_and_static_catalogs(monkeypatch):
    from app.mcp import server as stdio_server

    def _offline_request(*args, **kwargs):
        raise RuntimeError("backend offline for contract")

    monkeypatch.setattr(stdio_server, "_request", _offline_request)
    monkeypatch.setattr(stdio_server, "_cached_capability_etag", None)
    monkeypatch.setattr(stdio_server, "_cached_capability_data", None)

    tools_response = stdio_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    resources_response = stdio_server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}})
    prompts_response = stdio_server.handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/list", "params": {}})

    tools_result = tools_response["result"]
    tool_names = {tool["name"] for tool in tools_result["tools"]}
    assert tools_result["offline"] is True
    assert "backend offline for contract" in tools_result["error"]
    assert {"ops_connection_status", "ops_inspect_local_package", "ops_prepare_release_from_local_package"} <= tool_names
    assert "resources" in resources_response["result"]
    assert "prompts" in prompts_response["result"]
    assert resources_response["result"]["resources"]
    assert prompts_response["result"]["prompts"]


def test_stdio_mcp_manifest_has_offline_fallback_with_static_catalogs(monkeypatch):
    from app.mcp import server as stdio_server

    def _offline_request(*args, **kwargs):
        raise RuntimeError("manifest backend offline")

    monkeypatch.setattr(stdio_server, "_request", _offline_request)

    response = stdio_server.handle({"jsonrpc": "2.0", "id": 4, "method": "manifest", "params": {}})

    assert "error" not in response
    result = response["result"]
    tool_names = {tool["name"] for tool in result["tools"]}
    assert result["name"] == "ops-capability-server"
    assert result["transport"] == "stdio-jsonrpc"
    assert result["offline"] is True
    assert "manifest backend offline" in result["error"]
    assert result["resources"]
    assert result["prompts"]
    assert {"ops_connection_status", "ops_inspect_local_package", "ops_prepare_release_from_local_package"} <= tool_names


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

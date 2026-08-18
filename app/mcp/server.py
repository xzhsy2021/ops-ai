"""MCP-compatible stdio bridge for OPS Capability Server.

This server intentionally contains no LLM vendor integration. It bridges MCP
JSON-RPC requests to the OPS HTTP Tool API using OPS_TOOL_TOKEN.

Supported methods:
- initialize
- tools/list
- tools/call
- resources/list
- resources/read
- prompts/list
- prompts/get
- ping

For backward compatibility with the earlier bridge, newline-delimited JSON is
also accepted. MCP clients normally use Content-Length framed JSON-RPC.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Tuple

from app.services.mcp_capability_service import (
    MCP_ALIAS_TO_TOOL,
    MCP_TOOL_DESCRIPTION_OVERRIDES,
    ascii_only,
    from_mcp_tool_name,
    mcp_prompt_get,
    mcp_prompts_list,
    mcp_resources_list,
    mcp_tool_payload,
    to_mcp_tool_name,
)

BASE_URL = os.getenv("OPS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("OPS_TOOL_TOKEN", "")
SERVER_NAME = "ops-capability-server"
SERVER_VERSION = "1.3.0"
try:
    HTTP_TIMEOUT = max(1.0, float(os.getenv("OPS_MCP_HTTP_TIMEOUT", "6")))
except Exception:
    HTTP_TIMEOUT = 6.0

# Some MCP clients convert tool names to LLM function names and only accept
# [A-Za-z0-9_-]. OPS HTTP tools keep dotted names such as
# ops.list_services, but MCP stdio exposes safe aliases by default to avoid
# clients getting stuck in a perpetual "preparing" state.
SAFE_TOOL_NAMES = os.getenv("OPS_MCP_SAFE_TOOL_NAMES", "1").lower() not in {"0", "false", "no", "off"}

# Trace Solo and a few Windows MCP clients currently render non-ASCII tool
# descriptions incorrectly even when the stdio payload is valid UTF-8. Keep MCP
# tool descriptions ASCII by default; the HTTP Tool API and OPS UI still expose
# the original Chinese descriptions. Set OPS_MCP_ASCII_DESCRIPTIONS=0 for clients
# that render UTF-8 descriptions correctly.
ASCII_DESCRIPTIONS = os.getenv("OPS_MCP_ASCII_DESCRIPTIONS", "1").lower() not in {"0", "false", "no", "off"}

# Single source lives in app.services.mcp_capability_service.
# Kept as an alias so existing local references (diagnostic/offline tools, functions) still work.
ENGLISH_TOOL_DESCRIPTIONS: Dict[str, str] = MCP_TOOL_DESCRIPTION_OVERRIDES


def _to_mcp_tool_name(name: str) -> str:
    return to_mcp_tool_name(name)




def _ascii_only(value: Any, fallback: str = "") -> str:
    if not ASCII_DESCRIPTIONS:
        return str(value or fallback or "")
    return ascii_only(value, fallback)


def _english_tool_description(tool: Dict[str, Any], original_name: str, alias: str) -> str:
    if not ASCII_DESCRIPTIONS:
        return str(tool.get("description") or "")
    description = ENGLISH_TOOL_DESCRIPTIONS.get(original_name)
    if not description:
        category = tool.get("category") or (tool.get("annotations") or {}).get("category") or "ops"
        risk = tool.get("risk") or (tool.get("annotations") or {}).get("risk") or "low"
        description = f"OPS capability tool {original_name}. Category: {category}. Risk: {risk}."
    if alias != original_name:
        description += f" Original HTTP tool: {original_name}."
    return _ascii_only(description, fallback=f"OPS tool {alias}")

def _from_mcp_tool_name(name: str) -> str:
    return from_mcp_tool_name(name)


def _mcp_tool_payload(tool: Dict[str, Any]) -> Dict[str, Any]:
    return mcp_tool_payload(tool, ENGLISH_TOOL_DESCRIPTIONS)


def _log(message: str):
    # MCP stdio must keep stdout clean. Diagnostics go to stderr only.
    if os.getenv("OPS_MCP_DEBUG", "").lower() in {"1", "true", "yes", "on"}:
        print(f"[ops-mcp] {message}", file=sys.stderr, flush=True)


_cached_capability_etag: str | None = None
_cached_capability_data: Dict[str, Any] | None = None


def _request(method: str, path: str, data=None, etag: str | None = None):
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=body, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp_etag = resp.headers.get("ETag")
            result = json.loads(resp.read().decode("utf-8"))
            if resp_etag:
                result["_etag"] = resp_etag
            return result
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and etag:
            return {"_not_modified": True, "_etag": etag}
        text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(text).get("detail") or text
        except Exception:
            detail = text
        raise RuntimeError(f"HTTP {exc.code}: {detail}")
    except Exception as exc:
        raise RuntimeError(f"Cannot reach OPS API at {BASE_URL}: {exc}")


def _request_sse(method: str, path: str, data=None) -> Dict[str, Any]:
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=body, method=method)
    req.add_header("Accept", "text/event-stream, application/json")
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(text).get("detail") or text
        except Exception:
            detail = text
        raise RuntimeError(f"HTTP {exc.code}: {detail}")
    except Exception as exc:
        raise RuntimeError(f"Cannot reach OPS API at {BASE_URL}: {exc}")

    stripped = raw.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    events = []
    event_type = "message"
    data_lines = []
    for line in raw.splitlines():
        if not line.strip():
            if data_lines:
                data_text = "\n".join(data_lines)
                try:
                    event_data = json.loads(data_text)
                except Exception:
                    event_data = data_text
                events.append({"event": event_type, "data": event_data})
            event_type = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if data_lines:
        data_text = "\n".join(data_lines)
        try:
            event_data = json.loads(data_text)
        except Exception:
            event_data = data_text
        events.append({"event": event_type, "data": event_data})

    done = next((item.get("data") for item in reversed(events) if item.get("event") == "done"), {})
    return {"events": events, "data": done}


def _diagnostic_tool(error: str = "") -> Dict[str, Any]:
    alias = _to_mcp_tool_name("ops.connection_status")
    if ASCII_DESCRIPTIONS:
        desc = ENGLISH_TOOL_DESCRIPTIONS["ops.connection_status"]
        if error:
            desc += f" Current connection error: {error}"
        title = alias
    else:
        desc = "检查 OPS MCP Server 与 OPS API 的连接状态。"
        if error:
            desc += f" 当前连接异常：{error}"
        title = "OPS 连接状态"
    return {
        "name": alias,
        "description": _ascii_only(desc, fallback="OPS connection status"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {
            "title": _ascii_only(title, fallback=alias),
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _tools_for_mcp(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    global _cached_capability_etag, _cached_capability_data
    params = params or {}
    cursor = params.get("cursor")
    # Default to ai_full: AI agent sees all tools; execution permission is
    # enforced per-call with structured guidance. Override with OPS_MCP_PROFILE.
    default_profile = os.getenv("OPS_MCP_PROFILE", "ai_full")
    profile = str(params.get("profile") or default_profile)
    path = "/api/v2/tools?format=mcp&limit=200&profile=" + urllib.parse.quote(profile)
    if cursor is not None:
        path += "&cursor=" + str(cursor)
    try:
        data = _request("GET", path, etag=_cached_capability_etag if not cursor else None)
        if data.get("_not_modified") and _cached_capability_data:
            data = _cached_capability_data
        else:
            new_etag = data.get("_etag")
            if new_etag and not cursor:
                _cached_capability_etag = new_etag
                _cached_capability_data = data
        data = data.get("data", data)
        tools = [_mcp_tool_payload(t) for t in (data.get("tools") or [])]
        result = {"tools": tools}
        next_cursor = (data.get("pagination") or {}).get("next_cursor")
        if next_cursor is not None:
            result["nextCursor"] = str(next_cursor)
        return result
    except Exception as exc:
        err = str(exc)
        _log(err)
        return {
            "tools": [_diagnostic_tool(err), _local_package_inspect_tool(err), _local_release_prepare_tool(err)],
            "offline": True,
            "error": err,
            "base_url": BASE_URL,
            "token_present": bool(TOKEN),
        }


def _safe_multipart_filename(name: str) -> str:
    base = os.path.basename(str(name or ""))
    base = re.sub(r"[^A-Za-z0-9._@+\-=\u4e00-\u9fff]+", "_", base).strip("._")
    if not base:
        raise RuntimeError("Invalid package filename")
    return base


def _local_package_manifest_for_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    local_path = os.path.abspath(os.path.expanduser(str(args.get("local_path") or "")))
    if not local_path or not os.path.isfile(local_path):
        raise RuntimeError(f"Local package path not found: {local_path or '<empty>'}")
    filename = _safe_multipart_filename(str(args.get("filename") or os.path.basename(local_path)))
    size = os.path.getsize(local_path)
    calculate_sha256 = bool(args.get("calculate_sha256", True))
    sha = ""
    if calculate_sha256:
        h = hashlib.sha256()
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        sha = h.hexdigest()
    allowed_exts = [".tar.gz", ".tgz", ".tar", ".zip", ".jar", ".war", ".gz", ".bin"]
    lower = filename.lower()
    allowed = any(lower.endswith(ext) for ext in allowed_exts)
    blockers = [] if allowed else [f"Unsupported package extension: {filename}"]
    return {
        "ok": not blockers,
        "local_path": local_path,
        "name": filename,
        "package_name": filename,
        "size": size,
        "size_bytes": size,
        "size_mb": round(size / 1024 / 1024, 2),
        "sha256": sha,
        "allowed_extension": allowed,
        "allowed_extensions": allowed_exts,
        "blockers": blockers,
        "summary": "local package ready for upload" if not blockers else "local package cannot be uploaded until blockers are fixed",
    }


def _local_package_inspect_tool(error: str = "") -> Dict[str, Any]:
    alias = _to_mcp_tool_name("ops.inspect_local_package")
    desc = ENGLISH_TOOL_DESCRIPTIONS.get("ops.inspect_local_package", "Inspect local deploy package")
    if error:
        desc += f" Current connection error: {error}"
    return {
        "name": alias,
        "description": _ascii_only(desc, fallback="Inspect local deploy package"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "local_path": {"type": "string"},
                "filename": {"type": "string"},
                "calculate_sha256": {"type": "boolean"},
            },
            "required": ["local_path"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": alias,
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _local_release_prepare_tool(error: str = "") -> Dict[str, Any]:
    alias = _to_mcp_tool_name("ops.prepare_release_from_local_package")
    desc = ENGLISH_TOOL_DESCRIPTIONS.get("ops.prepare_release_from_local_package", "Prepare release from local package")
    if error:
        desc += f" Current connection error: {error}. Offline mode supports dry_run local inspection only."
    return {
        "name": alias,
        "description": _ascii_only(desc, fallback="Prepare release from local package"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "local_path": {"type": "string"},
                "system": {"type": "string"},
                "service": {"type": "string"},
                "environment": {"type": "string"},
                "servers": {"type": "array", "items": {"type": "string"}},
                "filename": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["local_path", "system", "service", "environment"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": alias,
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }


def _multipart_upload_package(args: Dict[str, Any], *, approval_intake: bool = False) -> Dict[str, Any]:
    manifest = _local_package_manifest_for_mcp(args)
    if manifest.get("blockers"):
        raise RuntimeError("; ".join(manifest["blockers"]))
    if args.get("dry_run"):
        return {"data": {"ok": True, "tool": "ops.upload_package", "result": {**manifest, "dry_run": True}, "summary": manifest.get("summary"), "blocked": False}}
    local_path = manifest["local_path"]
    filename = manifest["package_name"]
    boundary = "----OpsMcpUpload" + os.urandom(8).hex()
    fields = {
        "system": str(args.get("system") or ""),
        "service": str(args.get("service") or ""),
        "overwrite": "true" if args.get("overwrite") else "false",
    }
    if approval_intake:
        fields.update({
            "approval_intake": "true",
            "room_id": str(args.get("room_id") or ""),
            "request_event_id": str(args.get("request_event_id") or ""),
            "content_sha256": str(args.get("content_sha256") or ""),
            "package_sha256": str(manifest.get("sha256") or ""),
        })
    preamble_parts = []
    for key, value in fields.items():
        preamble_parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n").encode("utf-8"))
    preamble_parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode("utf-8"))
    preamble = b"".join(preamble_parts)
    epilogue = (f"\r\n--{boundary}--\r\n").encode("utf-8")
    content_length = len(preamble) + int(manifest["size_bytes"]) + len(epilogue)
    parsed = urllib.parse.urlparse(BASE_URL)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    base_path = (parsed.path or "").rstrip("/")
    path = base_path + "/api/v2/tools/packages/upload"
    if parsed.query:
        path += "?" + parsed.query
    conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(host, port, timeout=max(HTTP_TIMEOUT, 30.0))
    try:
        conn.putrequest("POST", path)
        conn.putheader("Accept", "application/json")
        conn.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
        conn.putheader("Content-Length", str(content_length))
        if TOKEN:
            conn.putheader("Authorization", "Bearer " + TOKEN)
        conn.endheaders()
        conn.send(preamble)
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                conn.send(chunk)
        conn.send(epilogue)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw}
        if resp.status >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else raw
            raise RuntimeError(f"HTTP {resp.status}: {detail}")
        return payload
    finally:
        conn.close()


def _prepare_release_from_local_package_for_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    manifest = _local_package_manifest_for_mcp(args)
    if manifest.get("blockers"):
        raise RuntimeError("; ".join(manifest["blockers"]))
    if args.get("dry_run"):
        return {
            "data": {
                "ok": True,
                "tool": "ops.prepare_release_from_local_package",
                "result": {
                    "dry_run": True,
                    "local_inspection": manifest,
                    "summary": "Local package inspected. No package uploaded and no deploy plan created.",
                    "next_actions": [
                        {"tool": "ops_prepare_release_from_local_package", "description": "Run again with dry_run=false to upload, create plan and precheck"}
                    ],
                },
                "summary": "Local package inspected",
                "blocked": False,
            }
        }
    upload_payload = _multipart_upload_package(args).get("data", {})
    upload_result = upload_payload.get("result") if isinstance(upload_payload, dict) else {}
    package_name = (upload_result or {}).get("package_name") or (upload_result or {}).get("name") or manifest.get("package_name")
    prepare_args = dict(args)
    prepare_args.pop("local_path", None)
    prepare_args.pop("content_base64", None)
    prepare_args["package_name"] = package_name
    prepare_args["expected_sha256"] = manifest.get("sha256") or prepare_args.get("expected_sha256") or ""
    data = _request("POST", "/api/v2/tools/call", {"tool": "ops.prepare_release_from_local_package", "arguments": prepare_args}).get("data", {})
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data["result"]["local_inspection"] = manifest
        data["result"]["upload_result"] = upload_result
    return {"data": data}


def _prepare_file_upload_for_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a stdio-local package, then create an approval bound to its File Center name."""
    action_parameters = dict(args.get("action_parameters") or {})
    local_path = str(action_parameters.get("local_path") or "").strip()
    if not local_path:
        return _request("POST", "/api/v2/tools/call", {"tool": "ops.approval.prepare_file_upload", "arguments": args})

    manifest_args = {
        **args,
        **action_parameters,
        "local_path": local_path,
        "filename": action_parameters.get("filename") or "",
    }
    manifest = _local_package_manifest_for_mcp(manifest_args)
    if manifest.get("blockers"):
        raise RuntimeError("; ".join(manifest["blockers"]))

    upload_payload = _multipart_upload_package({
        **manifest_args,
        "system": args.get("system_name") or "",
        "service": args.get("service_name") or "",
    }, approval_intake=True).get("data", {})
    upload_result = upload_payload.get("result") if isinstance(upload_payload, dict) else {}
    package_name = (upload_result or {}).get("package_name") or (upload_result or {}).get("name") or manifest.get("package_name")
    if not package_name:
        raise RuntimeError("Package upload did not return a package name")
    returned_sha256 = str((upload_result or {}).get("sha256") or "").lower()
    if returned_sha256 and returned_sha256 != str(manifest.get("sha256") or "").lower():
        raise RuntimeError("Package upload checksum differs from local package inspection")

    prepare_args = dict(args)
    prepared_parameters = dict(action_parameters)
    prepared_parameters.pop("local_path", None)
    prepared_parameters["package_name"] = package_name
    prepared_parameters["expected_sha256"] = manifest.get("sha256") or ""
    prepared_parameters["expected_size_bytes"] = int(manifest.get("size_bytes") or 0)
    prepare_args["action_parameters"] = prepared_parameters
    data = _request(
        "POST",
        "/api/v2/tools/call",
        {"tool": "ops.approval.prepare_file_upload", "arguments": prepare_args},
    ).get("data", {})
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data["result"]["local_inspection"] = manifest
        data["result"]["upload_result"] = upload_result
    return {"data": data}


def _prepare_plan_for_mcp(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage local FILE_UPLOAD inputs, then create one approval for the frozen plan."""
    prepare_args = dict(args)
    prepared_steps = []
    staged_uploads = []
    for raw_step in args.get("steps") or []:
        step = dict(raw_step)
        parameters = dict(step.get("parameters") or {})
        action_parameters = dict(parameters.get("action_parameters") or {})
        local_path = str(action_parameters.get("local_path") or "").strip()
        if str(step.get("action_type") or "").strip() == "FILE_UPLOAD" and local_path:
            manifest_args = {
                **args,
                **action_parameters,
                "local_path": local_path,
                "filename": action_parameters.get("filename") or "",
                "system": args.get("system_name") or "",
                "service": args.get("service_name") or "",
            }
            manifest = _local_package_manifest_for_mcp(manifest_args)
            if manifest.get("blockers"):
                raise RuntimeError("; ".join(manifest["blockers"]))
            upload_payload = _multipart_upload_package(manifest_args, approval_intake=True).get("data", {})
            upload_result = upload_payload.get("result") if isinstance(upload_payload, dict) else {}
            package_name = (upload_result or {}).get("package_name") or (upload_result or {}).get("name")
            if not package_name:
                raise RuntimeError("Package upload did not return a package name")
            returned_sha256 = str((upload_result or {}).get("sha256") or "").lower()
            if returned_sha256 != str(manifest.get("sha256") or "").lower():
                raise RuntimeError("Package upload checksum differs from local package inspection")
            action_parameters.pop("local_path", None)
            action_parameters["package_name"] = package_name
            action_parameters["expected_sha256"] = manifest.get("sha256") or ""
            action_parameters["expected_size_bytes"] = int(manifest.get("size_bytes") or 0)
            parameters["action_parameters"] = action_parameters
            step["parameters"] = parameters
            staged_uploads.append({"manifest": manifest, "upload_result": upload_result})
        prepared_steps.append(step)
    prepare_args["steps"] = prepared_steps
    data = _request(
        "POST",
        "/api/v2/tools/call",
        {"tool": "ops.approval.prepare_plan", "arguments": prepare_args},
    ).get("data", {})
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data["result"]["staged_uploads"] = staged_uploads
    return {"data": data}


def _call_tool_for_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = _from_mcp_tool_name(params.get("name") or params.get("tool"))
    args = params.get("arguments") or {}
    if tool_name == "ops.inspect_local_package":
        data = _local_package_manifest_for_mcp(args)
        return {"content": [{"type": "text", "text": json.dumps({"ok": bool(data.get("ok")), "tool": tool_name, "result": data, "summary": data.get("summary")}, ensure_ascii=False, indent=2)}], "isError": not bool(data.get("ok"))}
    if tool_name == "ops.connection_status":
        diagnostic = {
            "ok": True,
            "base_url": BASE_URL,
            "token_present": bool(TOKEN),
            "timeout_seconds": HTTP_TIMEOUT,
            "message": "MCP server process is running. If OPS tools are not listed, start OPS backend, verify OPS_BASE_URL, and create/pass OPS_TOOL_TOKEN.",
        }
        return {"content": [{"type": "text", "text": json.dumps(diagnostic, ensure_ascii=False, indent=2)}], "isError": False}
    if tool_name == "ops.approval.prepare_file_upload" and (args.get("action_parameters") or {}).get("local_path"):
        data = _prepare_file_upload_for_mcp(args).get("data", {})
    elif tool_name == "ops.approval.prepare_plan" and any(
        str(step.get("action_type") or "").strip() == "FILE_UPLOAD"
        and ((step.get("parameters") or {}).get("action_parameters") or {}).get("local_path")
        for step in (args.get("steps") or [])
    ):
        data = _prepare_plan_for_mcp(args).get("data", {})
    elif tool_name == "ops.prepare_release_from_local_package" and args.get("local_path") and not args.get("content_base64"):
        data = _prepare_release_from_local_package_for_mcp(args).get("data", {})
    elif tool_name == "ops.upload_package" and args.get("local_path") and not args.get("content_base64"):
        data = _multipart_upload_package(args).get("data", {})
    else:
        data = _request("POST", "/api/v2/tools/call", {"tool": tool_name, "arguments": args}).get("data", {})
    return {
        "content": [
            {"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str, indent=2)}
        ],
        "isError": not bool((data.get("result") or {}).get("ok", True)) if isinstance(data, dict) else False,
    }


def _call_tool_stream_for_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = _from_mcp_tool_name(params.get("name") or params.get("tool"))
    args = params.get("arguments") or {}
    try:
        stream_result = _request_sse("POST", "/api/v2/tools/call/stream", {"tool": tool_name, "arguments": args})
        data = {
            "stream": stream_result.get("events") or [],
            "result": stream_result.get("data") or {},
        }
    except Exception as exc:
        return _call_tool_for_mcp(params)
    return {
        "content": [
            {"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str, indent=2)}
        ],
        "isError": not bool(data.get("ok", True)) if isinstance(data, dict) else False,
    }


def _resources_for_mcp() -> Dict[str, Any]:
    return mcp_resources_list()


def _offline_resource_text(uri: str, error: str) -> str:
    payload = {
        "ok": False,
        "offline": True,
        "uri": uri or "ops://connection-status",
        "base_url": BASE_URL,
        "token_present": bool(TOKEN),
        "timeout_seconds": HTTP_TIMEOUT,
        "message": "OPS API is unavailable. Start the OPS backend, verify OPS_BASE_URL, and pass OPS_TOOL_TOKEN to read live MCP resources.",
        "error": error,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _read_resource_for_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    uri = params.get("uri") or ""
    try:
        data = _request("POST", "/api/v2/mcp/resources/read", {"uri": uri}).get("data", {})
        return {"contents": [{"uri": data.get("uri") or uri, "mimeType": data.get("mimeType") or "application/json", "text": data.get("text") or ""}]}
    except Exception as exc:
        # Keep MCP clients responsive when they try to auto-read resources while
        # the OPS backend is offline. Returning a diagnostic resource is more
        # useful than a JSON-RPC error and avoids client-side preparing loops.
        err = str(exc)
        _log(err)
        return {
            "contents": [
                {
                    "uri": uri or "ops://connection-status",
                    "mimeType": "application/json",
                    "text": _offline_resource_text(uri, err),
                }
            ]
        }


def _prompts_for_mcp() -> Dict[str, Any]:
    return mcp_prompts_list()


def _get_prompt_for_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    return mcp_prompt_get(params)


def _manifest_for_mcp() -> Dict[str, Any]:
    try:
        return _request("GET", "/api/v2/mcp/manifest").get("data", {})
    except Exception as exc:
        err = str(exc)
        _log(err)
        fallback_tools = _tools_for_mcp({})
        return {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "transport": "stdio-jsonrpc",
            "offline": True,
            "error": err,
            "base_url": BASE_URL,
            "token_present": bool(TOKEN),
            "resources": mcp_resources_list().get("resources", []),
            "prompts": mcp_prompts_list().get("prompts", []),
            "tools": fallback_tools.get("tools", []),
        }


def handle(msg: Dict[str, Any]) -> Dict[str, Any] | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    # Notifications do not require a response.
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    try:
        if method == "initialize":
            requested_version = str(params.get("protocolVersion") or "2024-11-05")
            result = {
                # Echo the client protocol version when present. Some clients stay
                # in "preparing" if the server downgrades the version eagerly.
                "protocolVersion": requested_version,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"subscribe": False, "listChanged": True},
                    "prompts": {"listChanged": True},
                },
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = _tools_for_mcp(params)
        elif method == "tools/call":
            result = _call_tool_for_mcp(params)
        elif method == "tools/call.stream":
            result = _call_tool_stream_for_mcp(params)
        elif method == "resources/list":
            result = _resources_for_mcp()
        elif method == "resources/read":
            result = _read_resource_for_mcp(params)
        elif method == "prompts/list":
            result = _prompts_for_mcp()
        elif method == "prompts/get":
            result = _get_prompt_for_mcp(params)
        elif method == "manifest":  # backwards compatible helper
            result = _manifest_for_mcp()
        else:
            raise ValueError(f"Unsupported method: {method}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(exc)}}


def _read_message() -> Tuple[Dict[str, Any] | None, bool]:
    first = sys.stdin.buffer.readline()
    if not first:
        return None, False
    # MCP stdio framing: Content-Length: N\r\n...\r\n\r\n<body>
    if first.lower().startswith(b"content-length:"):
        framed = True
        length = int(first.split(b":", 1)[1].strip())
        while True:
            line = sys.stdin.buffer.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        body = sys.stdin.buffer.read(length)
        return json.loads(body.decode("utf-8")), framed
    # Backwards-compatible line JSON mode.
    text = first.decode("utf-8").strip()
    if not text:
        return {}, False
    return json.loads(text), False


def _write_message(response: Dict[str, Any], framed: bool):
    body = json.dumps(response, ensure_ascii=False, default=str).encode("utf-8")
    if framed:
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        sys.stdout.buffer.flush()
    else:
        print(body.decode("utf-8"), flush=True)


def main():
    while True:
        try:
            msg, framed = _read_message()
            if msg is None:
                break
            if not msg:
                continue
            response = handle(msg)
            if response is not None:
                _write_message(response, framed)
        except Exception as exc:
            _write_message({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}, False)


if __name__ == "__main__":
    main()
